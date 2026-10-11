"""진단 — 새 정보원 후보 N1 "순주식발행·자산성장" 의 등록 전 점검 + 탐색 구간 IC(docs/diag/n1-net-issuance-2026-10-11.md).

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
배경: docs/diag/source-scan-2026-10-10.md §1·§3①.

    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 nice -n 10 .venv/bin/python tools/diag_n1_issuance.py

**금고 규칙** — 수익·라벨은 END(2026-06-30) 까지만 본다. 모든 읽기는 ``as_of=END``·``until=END`` 이고, 결정일 t 의 라벨
c(t+6)/c(t+1) 은 t+6 이 END 안의 세션일 때만 생긴다(패널이 END 에서 끝나므로 shift 가 그 뒤를 못 만든다). 그래도 라벨
마지막 세션이 END 를 넘으면 멈춘다(``guard``). data/_past_vault 는 열지 않는다.

재료(1번):
- KR 상장주식수는 ``prices`` 에 열이 없다 — ``market_stats`` 의 ``metric='shares'``(KRX Open API ``LIST_SHRS``,
  collectors/krx_openapi.normalize_shares) 하나뿐이다. 관측은 그날 16:00 KST.
- 분할·무상 보정은 ``prices.adj_factor``(그 세션의 **가격** 배율 f(D), 2:1 분할이면 0.5)로 한다:
      nsi(t, n) = log S(t) − log S(t−n) + Σ_{t−n<D≤t} log f(D)
  분할이면 log S 가 +log 2, log f 가 log 0.5 라 상쇄된다. 창 안의 사건만 쓰므로 미래 분할이 섞이지 않는다.
- 총자산은 ``fundamentals`` 의 ``total_assets``. 관측 시각(observed_at = 공시 접수 18:00) **다음 세션**부터 쓴다 —
  fundamental Analyst 가 기대는 store 게이트(공시 전 재무를 안 보여줌)를 날짜별로 되감은 것.

피처(2번, 모두 하루 늦춤 — 결정일 t 에는 t−1 까지 관측된 값): nsi_12m(252세션) · nsi_1m(21세션) · asset_growth(총자산 YoY).
유니버스·라벨·통제·잔차화·NW t 는 tools/diag_ta_features.py 를 그대로 부른다.
"""
from __future__ import annotations

import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices
from tools import diag_ta_features as T

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
END_DAY = date(2026, 6, 30)
PANEL_START = date(2020, 9, 1)
START_DECISION = date(2022, 9, 1)
REGIMES = (("박스", date(2022, 9, 1), date(2024, 12, 31)), ("급등", date(2025, 1, 1), date(2026, 6, 30)))
#: 문헌 부호 — 순발행·자산성장 높을수록 부진(Pontiff·Woodgate, Cooper·Gulen·Schill).
SIGN = {"nsi_12m": -1, "nsi_1m": -1, "asset_growth": -1}
#: 같은 피처의 견고성 변형(새 피처가 아니다) — 창 안에 기업행위 사건(|log f|>3%, 발효 뒤 40세션 여유)이나 하루 2배 넘는
#: 원 주식수 급변(보정 안 된 감자·병합·스팩 합병 등)이 있는 종목-날을 뺀다. 1번 점검에서 보정 누락이 드러나 붙였다.
CLEAN = {"nsi_12m_c": ("nsi_12m", 252), "nsi_1m_c": ("nsi_1m", 21)}
OUT_CSV = Path("logs/diag-n1-issuance-20261011.csv")
KEYWORDS = "무상|분할|병합|감자|유상|전환|신주|합병|소각|교환|행사"


def guard(days: list[date]) -> None:
    if days and max(days) > END_DAY:
        raise SystemExit(f"금고 침범: {max(days)} > {END_DAY} — 멈춘다")


# --------------------------------------------------------------------------- 읽기


def load_shares(store: Store, idx: pd.Index, cols: pd.Index) -> pd.DataFrame:
    m = store.get("market_stats", as_of=END, until=END, lookback=(END.date() - PANEL_START).days, market="KR",
                  columns=["entity_id", "valid_from", "metric", "value"])
    m = m[(m["metric"] == "shares") & m["entity_id"].str.match(r"^KR:\d{6}$")]
    m["day"] = pd.to_datetime(m["valid_from"]).dt.date
    guard(list(m["day"].unique()))
    return m.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").reindex(index=idx, columns=cols)


def load_factor(store: Store, idx: pd.Index, cols: pd.Index) -> pd.DataFrame:
    p = read_prices(store, as_of=END, until=END, lookback=(END.date() - PANEL_START).days,
                    columns=["close", "adj_factor"], adjusted=False, market="KR")
    p = p[p["entity_id"].str.match(r"^KR:\d{6}$") & p["adj_factor"].notna()]
    p["day"] = pd.to_datetime(p["valid_from"]).dt.date
    return p.pivot_table(index="day", columns="entity_id", values="adj_factor", aggfunc="last").reindex(index=idx, columns=cols)


def asof_fundamental(store: Store, metric: str, idx: pd.Index, cols: pd.Index, *, yoy: bool) -> tuple[pd.DataFrame, dict]:
    """날짜별 '그날 이미 공시된 최신 분기' 값(yoy 면 전년 동기 대비 성장). 관측 다음 세션부터 쓴다."""
    f = store.get("fundamentals", as_of=END, until=END, lookback=(END.date() - date(2019, 1, 1)).days, market="KR",
                  columns=["entity_id", "valid_from", "observed_at", "metric", "value", "fiscal_period"])
    f = f[(f["metric"] == metric) & f["entity_id"].str.match(r"^KR:\d{6}$") & f["fiscal_period"].str.match(r"^\d{4}Q[1-4]$")].copy()
    vf, ob = pd.to_datetime(f["valid_from"]), pd.to_datetime(f["observed_at"])
    stats = {"rows": len(f), "entities": f["entity_id"].nunique(), "first_period_end": str(vf.min().date()),
             "obs_before_period_end": int((ob < vf).sum())}
    f = f[ob >= vf]  # 회계기간 끝보다 먼저 관측됐다는 행은 기간 표기가 틀린 것 — 버린다
    f["obs_day"] = pd.to_datetime(f["observed_at"]).dt.tz_convert("Asia/Seoul").dt.date
    f["y"] = f["fiscal_period"].str[:4].astype(int)
    f["q"] = f["fiscal_period"].str[-1].astype(int)
    f["pidx"] = f["y"] * 4 + f["q"]
    f = f.sort_values(["entity_id", "pidx", "obs_day"]).drop_duplicates(["entity_id", "pidx"], keep="last")
    if yoy:
        prev = f[["entity_id", "pidx", "value", "obs_day"]].rename(columns={"value": "v0", "obs_day": "o0"})
        prev["pidx"] = prev["pidx"] + 4
        f = f.merge(prev, on=["entity_id", "pidx"], how="inner")
        f = f[f["v0"] > 0]
        f["value"] = f["value"] / f["v0"] - 1.0
        f["obs_day"] = np.maximum(f["obs_day"], f["o0"])
    # 날짜별 최신 '분기'(늦게 나온 옛 분기가 새 분기를 덮지 않게 pidx 누적 최대만 남긴다)
    f = f.sort_values(["entity_id", "obs_day", "pidx"])
    f = f[f["pidx"] >= f.groupby("entity_id")["pidx"].cummax()]
    sessions = np.array(list(idx))
    pos = np.searchsorted(sessions, f["obs_day"].to_numpy(), side="right")  # 관측일 다음 세션
    f = f[pos < len(sessions)].copy()
    f["day"] = sessions[pos[pos < len(sessions)]]
    w = f.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").reindex(index=idx, columns=cols)
    return w.ffill(limit=300), stats  # 다음 공시까지 유지(300세션 넘게 묵은 값은 버린다)


def load_documents(store: Store, entities: list[str] | None = None, title: bool = False) -> pd.DataFrame:
    cols = ["entity_id", "valid_from", "doc_type"] + (["title"] if title else [])
    d = store.get("documents", as_of=END, until=END, lookback=(END.date() - PANEL_START).days, market="KR",
                  entity=entities, columns=cols)
    d = d[d["entity_id"].astype(str).str.match(r"^KR:\d{6}$")]
    d["day"] = pd.to_datetime(d["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    return d


# --------------------------------------------------------------------------- 1. 재료 점검


def adjusted_log_shares(s: pd.DataFrame, fac: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """L(t) = log S(t) + Σ_{D≤t} log f(D). 차분이 곧 보정 주식수 변화. 시세 없는 날의 주식수는 직전 값(5세션까지)."""
    logs = np.log(s.where(s > 0)).ffill(limit=5)
    logf = np.log(fac.where(fac > 0)).fillna(0.0).cumsum()
    return logs + logf, logf


def material_check(store: Store, d: dict[str, pd.DataFrame], s: pd.DataFrame, fac: pd.DataFrame, uni: pd.DataFrame) -> dict:
    c = d["close"]
    out: dict = {}
    first = s.notna().any(axis=1)
    out["shares_first_day"] = str(first[first].index[0])
    out["shares_last_day"] = str(first[first].index[-1])
    has_px = c.notna()
    cov_all = (s.notna() & has_px).sum(axis=1) / has_px.sum(axis=1)
    cov_uni = (s.notna() & uni).sum(axis=1) / uni.sum(axis=1).replace(0, np.nan)
    rng = cov_all.index >= first[first].index[0]
    out["missing_all_px"] = float(1 - cov_all[rng].mean())
    out["missing_uni"] = float(1 - cov_uni[cov_uni.index >= START_DECISION].mean())
    out["missing_all_px_min_day"] = float(cov_all[rng].min())
    L, _ = adjusted_log_shares(s, fac)
    raw = np.log(s.where(s > 0)).ffill(limit=5)
    jump_adj = L.diff()
    jump_raw = raw.diff()
    # 사건 정렬: |log f|>3% 인 사건마다 ±40 세션 안에서 원 주식수가 가장 크게 뛴 날까지의 거리
    sessions = list(c.index)
    ev = np.log(fac.where(fac > 0)).stack()
    ev = ev[ev.abs() > 0.03]
    offs = []
    for (day, ent), lf in ev.items():
        i = sessions.index(day)
        lo, hi = max(0, i - 40), min(len(sessions), i + 41)
        win = jump_raw[ent].iloc[lo:hi].abs()
        if win.notna().sum() == 0 or win.max() < 0.02:
            offs.append((day, ent, lf, None, np.nan))
            continue
        k = int(np.nanargmax(win.to_numpy())) + lo - i
        offs.append((day, ent, lf, k, float(jump_raw[ent].iloc[i + k])))
    o = pd.DataFrame(offs, columns=["day", "entity_id", "log_f", "offset", "raw_jump"])
    out["events"] = len(o)
    out["events_no_share_jump"] = int(o["offset"].isna().sum())
    oo = o.dropna(subset=["offset"])
    out["events_same_day"] = int((oo["offset"] == 0).sum())
    out["events_offset_pos"] = int((oo["offset"] > 0).sum())
    out["events_offset_neg"] = int((oo["offset"] < 0).sum())
    out["offset_pos_median"] = float(oo.loc[oo["offset"] > 0, "offset"].median()) if (oo["offset"] > 0).any() else float("nan")
    out["events_table"] = o
    # 상위 이상치 20건 — 보정 하루 변화 |Δ| 최대
    st = jump_adj.stack()
    st = st[st.index.get_level_values(0) >= date(2021, 8, 14)]
    top = st.abs().sort_values(ascending=False).head(20)
    rows = []
    for (day, ent), _ in top.items():
        i = sessions.index(day)
        near = ev.xs(ent, level=1) if ent in ev.index.get_level_values(1) else pd.Series(dtype=float)
        near = near[[abs(sessions.index(x) - i) <= 40 for x in near.index]]
        rows.append({"day": day, "entity_id": ent, "adj_jump": float(jump_adj.at[day, ent]), "raw_jump": float(jump_raw.at[day, ent]),
                     "S_before": float(s[ent].iloc[:i].dropna().iloc[-1]) if s[ent].iloc[:i].notna().any() else np.nan,
                     "S_after": float(s.at[day, ent]),
                     "f_near": "; ".join(f"{x}:{math.exp(v):.3g}({sessions.index(x) - i:+d})" for x, v in near.items()) or "-"})
    top_df = pd.DataFrame(rows)
    docs = load_documents(store, sorted(top_df["entity_id"].unique()), title=True)
    docs = docs[docs["title"].astype(str).str.contains(KEYWORDS)]
    def titles(r: pd.Series) -> str:
        x = docs[(docs["entity_id"] == r["entity_id"]) & (docs["day"] >= r["day"] - timedelta(days=75)) & (docs["day"] <= r["day"] + timedelta(days=10))]
        return " / ".join([f"{a.strftime('%y-%m-%d')} {t[:28]}" for a, t in zip(x["day"], x["title"].astype(str), strict=True)][-4:]) or "-"
    top_df["titles"] = top_df.apply(titles, axis=1)
    out["top20"] = top_df
    # 사건이 창에 걸렸는데 원 주식수 반영이 어긋난 경우 nsi_12m 이 얼마나 오염되나 — 큰 값(|nsi_12m|>0.5) 중 창 안에 사건이 있는 비율
    return out


# --------------------------------------------------------------------------- 2. 피처·통제


def build(store: Store, d: dict[str, pd.DataFrame], s: pd.DataFrame, fac: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict]:
    c, idx, cols = d["close"], d["close"].index, d["close"].columns
    L, _ = adjusted_log_shares(s, fac)
    f: dict[str, pd.DataFrame] = {}
    f["nsi_12m"] = L - L.shift(252)
    f["nsi_1m"] = L - L.shift(21)
    raw = np.log(s.where(s > 0)).ffill(limit=5)
    ev = (np.log(fac.where(fac > 0)).abs() > 0.03).astype(float)
    big = (raw.diff().abs() > math.log(2)).astype(float)
    for name, (src, n) in CLEAN.items():
        dirty = (ev.rolling(n + 40, min_periods=1).sum() > 0) | (big.rolling(n, min_periods=1).sum() > 0)
        f[name] = f[src].where(~dirty)
    ag, ag_stats = asof_fundamental(store, "total_assets", idx, cols, yoy=True)
    f["asset_growth"] = ag
    eq, _ = asof_fundamental(store, "total_equity", idx, cols, yoy=False)
    # 스타일 — 시총은 원주가×원 주식수(수준)
    r = c / c.shift(1) - 1.0
    mcap = d["close_raw"] * s.ffill(limit=5)
    f["_logcap"] = np.log(mcap.where(mcap > 0))
    f["_vol60"] = r.rolling(60, min_periods=60).std()
    mkt = r.clip(-0.5, 1.0).mean(axis=1)
    cov = r.rolling(120, min_periods=100).cov(mkt)
    f["_beta"] = cov.div(mkt.rolling(120, min_periods=100).var(), axis=0)
    f["_bm"] = eq / mcap.where(mcap > 0)
    # 통제 — diag_ta_features.features 의 _rev5·_mom60·_lowvol·_liq 와 같은 정의(18종 전부를 만들지 않으려고 옮겨 적음)
    f["_rev5"] = c / c.shift(5) - 1.0
    f["_mom60"] = c / c.shift(60) - 1.0
    f["_lowvol"] = -r.ewm(span=60, min_periods=60).std()
    f["_liq"] = np.log(T.rmean(d["value"], 20))
    # 하루 늦춤 — 결정일 t 에는 t−1 까지 관측된 값
    f = {k: (x.shift(1) if not k.startswith("_") else x).replace([np.inf, -np.inf], np.nan) for k, x in f.items()}
    return f, ag_stats


def dilution_panels(store: Store, idx: pd.Index, cols: pd.Index) -> dict[str, pd.DataFrame]:
    out = {}
    docs = load_documents(store)
    dil = docs[docs["doc_type"] == "dilution"]
    sessions = np.array(list(idx))
    pos = np.searchsorted(sessions, dil["day"].to_numpy(), side="left")
    dil = dil[pos < len(sessions)].copy()
    dil["s"] = sessions[pos[pos < len(sessions)]]
    cnt = dil.groupby(["s", "entity_id"]).size().unstack().reindex(index=idx, columns=cols).fillna(0.0)
    out["dilution_60"] = cnt.rolling(60, min_periods=60).sum().shift(1)  # G2 정의(최근 60세션 dilution 공시 수)
    ev = pd.read_pickle("data/_diag/kr-long/features-event-KR.pkl")  # invariant-allow: data-access — 진단 캐시(event 원피처, ~2026-06-30)
    ev = ev[pd.to_datetime(ev["session"]).dt.date <= END_DAY]
    ev["session"] = pd.to_datetime(ev["session"]).dt.date
    out["raw_event_dilution"] = ev.pivot_table(index="session", columns="entity_id", values="dilution", aggfunc="last").reindex(index=idx, columns=cols)
    return out


# --------------------------------------------------------------------------- 4. 측정


def deciles(x: pd.DataFrame, y: pd.DataFrame) -> list[float]:
    m = x.notna() & y.notna()
    pr = x.where(m).rank(axis=1, pct=True, method="first")  # 0 이 몰려 있어 평균 순위면 십분위가 비어 버린다
    yy = y.where(m)
    mean = yy.mean(axis=1)
    out = []
    for k in range(10):
        sel = (pr > k / 10) & (pr <= (k + 1) / 10)
        out.append(float((yy.where(sel).mean(axis=1) - mean).mean()))
    return out


def partial_ic(x: pd.DataFrame, y: pd.DataFrame, controls: list[pd.DataFrame]) -> pd.Series:
    """일별 부분 순위상관 — 피처·라벨 **둘 다** 통제(순위 백분위)에 잔차화한 뒤 상관(Frisch–Waugh).

    diag_ta_features.residualize 는 피처만 잔차화한다. 이 피처들은 값 0(주식수 변화 없음)이 몰려 있어, 동점 무리의 잔차
    순서가 통제 변수만으로 정해지고 그 순서가 다시 라벨과 상관한다 — 통제의 IC 가 부분 IC 로 새어 부호까지 뒤집혔다
    (1차 실행: nsi_12m 원 IC −0.043 → 피처만 잔차화 +0.023). 라벨도 같이 잔차화하면 그 누출이 사라진다.
    """
    out = {}
    for day in x.index:
        frame = pd.concat([x.loc[day], y.loc[day]] + [c.loc[day] for c in controls], axis=1).dropna()
        if len(frame) < 100:
            continue
        r = frame.rank(pct=True).to_numpy()
        a = np.column_stack([np.ones(len(r)), r[:, 2:]])
        res = r[:, :2] - a @ np.linalg.lstsq(a, r[:, :2], rcond=None)[0]
        den = np.sqrt((res[:, 0] ** 2).sum() * (res[:, 1] ** 2).sum())
        if den > 0:
            out[day] = float((res[:, 0] * res[:, 1]).sum() / den)
    return pd.Series(out)


def ranker_panel(idx: pd.Index, cols: pd.Index) -> pd.DataFrame:
    """앞선 진단의 걷기식 랭커 예측(하락장 검진 + 회차 C0, 시드 5개 평균) — 재학습 없음."""
    from tools.diag_hedged_sleeve import kr_preds
    import tools.final_round_kit as fkit
    acc = None
    for seed in fkit.SEEDS:
        p = kr_preds(seed)
        w = p.pivot_table(index="session", columns="entity_id", values="pred", aggfunc="last").rank(axis=1, pct=True)
        acc = w if acc is None else acc.add(w, fill_value=np.nan)
    acc = acc / len(fkit.SEEDS)
    acc.index = pd.to_datetime(pd.Series(acc.index)).dt.date.values
    return acc.reindex(index=idx, columns=cols)


def delays(S: pd.DataFrame, F: pd.DataFrame, max_off: int = 150) -> pd.DataFrame:
    """기업행위 사건(|log f|>3%)마다 짝이 되는 원 주식수 변화까지의 세션 거리(±max_off). **날짜·주식수·배율만** 본다(수익 없음).

    짝 = 창 안에서 Δlog S 의 부호가 −log f 와 같고 크기가 |log f| 의 절반 이상인 날 중 |Δlog S + log f| 가 가장 작은 날(동점은 가까운 날).
    """
    logS = np.log(S.where(S > 0)).ffill(limit=5)
    dS = logS.diff()
    logf = np.log(F.where(F > 0))
    ev = logf.stack()
    ev = ev[ev.abs() > 0.03]
    pos = {d: i for i, d in enumerate(S.index)}
    rows = []
    for (day, ent), lf in ev.items():
        i = pos[day]
        lo, hi = max(1, i - max_off), min(len(S), i + max_off + 1)
        x = dS[ent].iloc[lo:hi].to_numpy()
        offs = np.arange(lo, hi) - i
        ok = np.isfinite(x) & (np.sign(x) == -np.sign(lf)) & (np.abs(x) >= 0.5 * abs(lf))
        if not ok.any():
            rows.append((day, ent, lf, np.nan))
            continue
        err = np.abs(x + lf) + 1e-6 * np.abs(offs)
        err[~ok] = np.inf
        rows.append((day, ent, lf, int(offs[int(np.argmin(err))])))
    return pd.DataFrame(rows, columns=["day", "entity_id", "log_f", "offset"])


def main_delays() -> int:
    from tools import past_vault_n1_bake as nb
    store = Store(root=Path("data"))
    S, F = nb.read_inputs(store, PANEL_START, END_DAY)
    guard(list(S.index))
    o = delays(S, F)
    o.to_csv("logs/diag-n1-delays-20261011.csv", index=False)
    m = o.dropna(subset=["offset"])
    print(f"사건 {len(o)} · 짝 찾음 {len(m)} · 못 찾음 {len(o) - len(m)} (±150)")
    print(f"같은 날 {int((m.offset == 0).sum())} · 뒤(+) {int((m.offset > 0).sum())} · 앞(−) {int((m.offset < 0).sum())}")
    p = m.loc[m.offset > 0, "offset"]
    print("뒤(+) 분위: " + " · ".join(f"{q:.0%} {p.quantile(q):.0f}" for q in (0.5, 0.9, 0.95, 0.99)) + f" · 최대 {p.max():.0f}")
    print("뒤(+) 구간 개수: " + " · ".join(f"{a}~{b} {int(((p >= a) & (p <= b)).sum())}" for a, b in ((1, 20), (21, 40), (41, 60), (61, 90), (91, 120), (121, 150))))
    n = m.loc[m.offset < 0, "offset"]
    print("앞(−) 분위: " + " · ".join(f"{q:.0%} {n.quantile(1 - q):.0f}" for q in (0.5, 0.9, 0.99)) + f" · 최소 {n.min():.0f}" if len(n) else "앞(−) 없음")
    allabs = m["offset"].abs()
    print("|거리| 99% 분위 " + f"{allabs.quantile(0.99):.0f}")
    big = o[(o.log_f.abs() > math.log(1.5))]
    print(f"큰 사건(|log f|>log1.5) {len(big)} · 그중 못 찾음 {int(big.offset.isna().sum())}")
    return 0


def main() -> int:
    if "--delays" in sys.argv:
        return main_delays()
    store = Store(root=Path("data"))
    d = T.load(store, END, PANEL_START)
    c, v = d["close"], d["value"]
    guard(list(c.index))
    idx, cols = c.index, c.columns
    s = load_shares(store, idx, cols)
    fac = load_factor(store, idx, cols)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)

    print("=== 1. 재료 점검 ===")
    mc = material_check(store, d, s, fac, uni)
    for k, val in mc.items():
        if not isinstance(val, pd.DataFrame):
            print(f"{k}: {val}")
    pd.set_option("display.width", 260)
    pd.set_option("display.max_colwidth", 140)
    print(mc["top20"].round(4).to_string())
    mc["events_table"].to_csv("logs/diag-n1-events-20261011.csv", index=False)

    f, ag_stats = build(store, d, s, fac)
    print("total_assets:", ag_stats)
    dil = dilution_panels(store, idx, cols)

    label = c.shift(-6) / c.shift(-1) - 1.0
    label = label.where(label.abs() <= 0.5)
    days = [x for x in idx if x >= START_DECISION]
    label = label.where(uni).loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    sess = list(idx)
    last_label_day = sess[sess.index(days[-1]) + 6]
    guard([last_label_day])
    print(f"\n결정일 {days[0]}~{days[-1]} {len(days)}일 · 라벨 마지막 세션 {last_label_day} · 평균 우주 {label.notna().sum(axis=1).mean():.0f}")

    F = {k: x.where(uni).loc[days] for k, x in {**f, **dil}.items()}
    # 커버리지(우주 안)
    for k in SIGN:
        print(f"값 0 비율 {k}: {float((F[k] == 0).sum().sum() / F[k].notna().sum().sum()):.3f}")
    for k in [*SIGN, *CLEAN]:
        cov = (F[k].notna().sum(axis=1) / label.notna().sum(axis=1)).mean()
        print(f"커버리지 {k}: {cov:.3f}")

    print("\n=== 3. 스타일 겹침(일별 횡단면 순위상관 평균) ===")
    style = ["_logcap", "_vol60", "_beta", "_bm", "_rev5", "_mom60", "nsi_12m", "nsi_1m", "asset_growth", "dilution_60", "raw_event_dilution"]
    corr = pd.DataFrame(index=list(SIGN), columns=style, dtype=float)
    for a in SIGN:
        for b in style:
            corr.loc[a, b] = np.nan if a == b else float(T.rowwise_ic(F[a], F[b]).mean())
    print(corr.round(3).to_string())

    print("\n=== 4. 탐색 IC ===")
    controls = [F["_rev5"], F["_mom60"], F["_lowvol"], F["_liq"]]
    rank = ranker_panel(idx, cols).where(uni).loc[days]
    rows = []
    rk_ic = T.rowwise_ic(rank, label)
    for name, sign in {**SIGN, **{k: SIGN[v[0]] for k, v in CLEAN.items()}}.items():
        ic = T.rowwise_ic(F[name], label)
        t = float(ic_module.newey_west_t(ic, lag=4))
        reg = {}
        for rn, a, b in REGIMES:
            part = ic[(ic.index >= a) & (ic.index <= b)]
            reg[f"IC_{rn}"], reg[f"t_{rn}"] = float(part.mean()), float(ic_module.newey_west_t(part, lag=4))
        old = T.rowwise_ic(T.residualize(F[name], controls), label)  # 기존 도구 방식(피처만 잔차화) — 비교용
        pic = partial_ic(F[name], label, controls)
        for rn, a, b in REGIMES:
            part = pic[(pic.index >= a) & (pic.index <= b)]
            reg[f"편IC4_{rn}"], reg[f"편t4_{rn}"] = float(part.mean()), float(ic_module.newey_west_t(part, lag=4))
        pic5 = partial_ic(F[name], label, controls + [F["_logcap"], F["_beta"], F["_vol60"]])
        ric = partial_ic(F[name], label, [rank])
        dec = deciles(F[name], label)
        # 랭커 위 단순 결합(재학습 아님): 순위 백분위 + w·부호×피처 순위
        # 부호는 문헌 부호가 아니라 이 구간의 원 IC 부호(asset_growth 는 문헌과 반대로 나왔다 — 탐색 진단이라 허용, 시행이면 미리 박는다)
        combo = rank + 0.25 * (np.sign(ic.mean()) * F[name]).rank(axis=1, pct=True).reindex_like(rank)
        combo = combo.where(F[name].notna(), rank)
        cic = T.rowwise_ic(combo, label)
        dd = (cic - rk_ic).dropna()
        rows.append({"name": name, "n_days": len(ic), "IC": float(ic.mean()), "t": t, **reg,
                     "옛편IC4": float(old.mean()), "옛편t4": float(ic_module.newey_west_t(old, lag=4)),
                     "편IC4": float(pic.mean()), "편t4": float(ic_module.newey_west_t(pic, lag=4)),
                     "편IC7": float(pic5.mean()), "편t7": float(ic_module.newey_west_t(pic5, lag=4)),
                     "랭커잔차IC": float(ric.mean()), "랭커잔차t": float(ic_module.newey_west_t(ric, lag=4)),
                     "ΔIC결합": float(dd.mean()), "ΔIC결합t": float(ic_module.newey_west_t(dd, lag=4)),
                     **{f"D{k + 1}": x for k, x in enumerate(dec)}})
    out = pd.DataFrame(rows).set_index("name")
    print(f"랭커 IC(같은 우주·라벨) {rk_ic.mean():.4f} t {ic_module.newey_west_t(rk_ic, lag=4):.2f}")
    print(out.drop(columns=[f"D{k}" for k in range(1, 11)]).round(4).T.to_string())
    print("\n십분위별 5세션 초과(동일가중 대비, %) — D1 = 피처 최저, D10 = 최고")
    print((out[[f"D{k}" for k in range(1, 11)]] * 100).round(3).to_string())
    out.to_csv(OUT_CSV)
    corr.to_csv(OUT_CSV.with_name("diag-n1-issuance-style-20261011.csv"))
    mc["top20"].to_csv(OUT_CSV.with_name("diag-n1-issuance-top20-20261011.csv"), index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
