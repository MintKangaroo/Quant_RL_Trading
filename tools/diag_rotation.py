"""진단 — 자금 회전(시소): 초대형(삼성전자·SK하이닉스)·반도체가 빠지는 날 나머지가 오르나, 그리고 그것이 **다음 날 이후를 예측하나**
(docs/diag/rotation-seesaw-2026-10-11.md, 사용자 아이디어 10/11).

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다. config·크론·장부를 건드리지 않는다.

    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 nice -n 10 .venv/bin/python tools/diag_rotation.py [--posthoc]

**금고 규칙** — 수익·라벨은 END(2026-06-30) 까지만. 모든 읽기는 ``as_of=END``·``until=END``. 결정일 d 의 전방 수익은
c(d+1+h)/c(d+1) 이고 패널이 END 에서 끝나므로 shift 가 그 뒤를 못 만든다. 그래도 마지막 라벨 세션이 END 를 넘으면 멈춘다(``guard``).
data/_past_vault 는 열지 않는다. 예외 하나: 업종 분류(``sectors`` dart_company)는 2026-08 에 처음 관측된 **스냅샷**이라
``as_of=SECTOR_AS_OF`` 로 읽는다(diag_sector_rotation 과 같은 관례 — 수익이 아니라 분류만, 생존 편향은 문서에 적는다).

**매매 타이밍** — 결정 d 종가까지의 정보 → d+1 종가 진입(vault_judge 관례). 같은 날(d) 시소는 매매할 수 없다 — 쓸모는 ② 예측력에 달렸다.

재사용: 시세 로드·유니버스·rowwise_ic·rmean 은 tools/diag_ta_features, 부분 IC(피처·라벨 둘 다 잔차화)·십분위는 tools/diag_n1_issuance.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.selector.ksic import roll_up
from quant_rl_trading.store import Store
from tools import diag_n1_issuance as N1
from tools import diag_ta_features as T

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
END_DAY = date(2026, 6, 30)
PANEL_START = date(2021, 8, 1)
START_DECISION = date(2022, 9, 1)  # 유니버스 '이력 252' 가 2022-08 에야 선다
SECTOR_AS_OF = datetime(2026, 10, 11, tzinfo=UTC)
REGIMES = (("박스", date(2022, 9, 1), date(2024, 12, 31)), ("급등", date(2025, 1, 1), date(2026, 6, 30)))
MEGA = ["KR:005930", "KR:000660"]
LARGE_N = 200  # 대형 = 그날(전일) 유니버스 시총 순위 200 안(초대형 둘 제외), 중소형 = 나머지
HORIZONS = (1, 3, 5)
COST = 0.0041  # 편도
MIN_SECTOR = 10
INVESTORS = ("외인계", "기관", "개인")
IDX = {"k200": "KR:IDX:KOSPI200", "kospi": "KR:IDX:KOSPI", "kosdaq": "KR:IDX:KOSDAQ", "semi": "KR:IDX:KRX 반도체"}
OUT = Path("logs")
TAG = "20261011"


def guard(days) -> None:
    if len(days) and max(days) > END_DAY:
        raise SystemExit(f"금고 침범: {max(days)} > {END_DAY} — 멈춘다")


def seoul_day(s: pd.Series) -> pd.Series:
    t = pd.to_datetime(s)
    if t.dt.tz is not None:
        t = t.dt.tz_convert("Asia/Seoul")
    return t.dt.date


# --------------------------------------------------------------------------- 통계


def nw_ols(y: pd.Series, x: pd.DataFrame, lag: int) -> tuple[np.ndarray, np.ndarray]:
    """OLS + Newey–West(Bartlett) 표준오차. x 에 상수 열을 붙인다. (계수, t)"""
    df = pd.concat([y.rename("_y"), x], axis=1).dropna()
    if len(df) < 30:
        k = x.shape[1] + 1
        return np.full(k, np.nan), np.full(k, np.nan)
    Y = df["_y"].to_numpy()
    X = np.column_stack([np.ones(len(df)), df.drop(columns="_y").to_numpy(dtype=float)])
    xtx_inv = np.linalg.pinv(X.T @ X)
    b = xtx_inv @ X.T @ Y
    u = Y - X @ b
    xu = X * u[:, None]
    S = xu.T @ xu
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1)
        G = xu[L:].T @ xu[:-L]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    return b, b / np.sqrt(np.diag(V))


def nw_t(s: pd.Series, lag: int) -> float:
    s = s.dropna()
    if len(s) < 10:
        return float("nan")
    return float(ic_module.newey_west_t(s, lag=lag))


def p_of(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2)) if np.isfinite(t) else 1.0


def in_regime(idx, a: date, b: date) -> np.ndarray:
    return np.array([(x >= a) and (x <= b) for x in idx])


# --------------------------------------------------------------------------- 읽기


def load_cap(store: Store, idx: pd.Index, cols: pd.Index) -> pd.DataFrame:
    m = store.get("market_stats", as_of=END, until=END, lookback=(END.date() - PANEL_START).days, market="KR",
                  columns=["entity_id", "valid_from", "metric", "value"])
    m = m[(m["metric"] == "market_cap") & m["entity_id"].str.match(r"^KR:\d{6}$")]
    m["day"] = seoul_day(m["valid_from"])
    guard(list(m["day"].unique()))
    return m.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").reindex(index=idx, columns=cols).ffill(limit=3)


def load_sectors(store: Store) -> pd.Series:
    f = store.get("sectors", as_of=SECTOR_AS_OF, market="KR")
    f = f[f["source"] == "dart_company"].sort_values(["valid_from", "revision"])
    return f.groupby("entity_id")["sector"].last().map(roll_up).dropna()


def load_indices(store: Store, idx: pd.Index) -> pd.DataFrame:
    f = store.get("indices", as_of=END, until=END, entity=list(IDX.values()), market="KR",
                  lookback=(END.date() - PANEL_START).days, columns=["entity_id", "valid_from", "close"])
    f["day"] = seoul_day(f["valid_from"])
    guard(list(f["day"].unique()))
    w = f.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last")
    w = w.rename(columns={v: k for k, v in IDX.items()})
    return w.reindex(idx)


def load_flows(store: Store, idx: pd.Index, cols: pd.Index) -> dict[str, pd.DataFrame]:
    """투자자별 순매수 금액(원). 해 단위 창. 행이 없으면 NaN(0 으로 채우지 않는다 — 표 규약)."""
    parts = []
    for year in range(PANEL_START.year, END_DAY.year + 1):
        lo, hi = max(PANEL_START, date(year, 1, 1)), min(date(year, 12, 31), END_DAY)
        until = min(datetime.combine(hi, datetime.max.time(), tzinfo=UTC), END)
        f = store.get("flows", as_of=END, until=until, lookback=(END.date() - lo).days + 1, market="KR",
                      columns=["entity_id", "valid_from", "investor", "net_value"])
        f = f[f["investor"].isin(INVESTORS) & f["entity_id"].str.match(r"^KR:\d{6}$")]
        f["day"] = seoul_day(f["valid_from"])
        f = f[(f["day"] >= lo) & (f["day"] <= hi)]
        parts.append(f[["entity_id", "day", "investor", "net_value"]])
        print(f"  flows {year}: {len(f):,}행", flush=True)
    f = pd.concat(parts)
    guard(list(f["day"].unique()))
    return {inv: g.pivot_table(index="day", columns="entity_id", values="net_value", aggfunc="last").reindex(index=idx, columns=cols)
            for inv, g in f.groupby("investor")}


def load_ranker(store: Store, idx: pd.Index, cols: pd.Index) -> pd.DataFrame:
    parts = []
    lo = date(2022, 7, 1)
    while lo <= END_DAY:
        hi = min(date(lo.year + (lo.month + 2) // 12, (lo.month + 2) % 12 + 1, 1) - timedelta(days=1), END_DAY)
        until = min(datetime.combine(hi, datetime.max.time(), tzinfo=UTC), END)
        s = store.get("signals", as_of=END, until=until, lookback=(END.date() - lo).days + 1, market="KR",
                      columns=["entity_id", "valid_from", "analyst", "score"])
        s = s[s["analyst"] == "ranker"]
        s["day"] = seoul_day(s["valid_from"])
        parts.append(s[(s["day"] >= lo) & (s["day"] <= hi)][["entity_id", "day", "score"]])
        lo = hi + timedelta(days=1)
    s = pd.concat(parts)
    guard(list(s["day"].unique()))
    return s.pivot_table(index="day", columns="entity_id", values="score", aggfunc="last").reindex(index=idx, columns=cols)


# --------------------------------------------------------------------------- 패널


def group_mean(x: pd.DataFrame, sec: pd.Series, min_n: int = MIN_SECTOR) -> pd.DataFrame:
    """종목 열 → 섹터 열 동일가중. 그날 구성 min_n 미만 섹터는 NaN."""
    cols = [c for c in x.columns if c in sec.index]
    g = sec.reindex(cols)
    xx = x[cols]
    m = xx.T.groupby(g).mean().T
    n = xx.notna().T.groupby(g).sum().T
    return m.where(n >= min_n)


def group_wmean(x: pd.DataFrame, w: pd.DataFrame, sec: pd.Series, min_n: int = MIN_SECTOR) -> pd.DataFrame:
    cols = [c for c in x.columns if c in sec.index]
    g = sec.reindex(cols)
    xx, ww = x[cols], w[cols].where(x[cols].notna())
    num = (xx * ww).T.groupby(g).sum(min_count=1).T
    den = ww.T.groupby(g).sum(min_count=1).T
    n = xx.notna().T.groupby(g).sum().T
    return (num / den).where(n >= min_n)


def build(store: Store) -> dict:
    d = T.load(store, END, PANEL_START)
    c, v = d["close"], d["value"]
    guard(list(c.index))
    idx, cols = c.index, c.columns
    r = (c / c.shift(1) - 1.0)
    r = r.mask(r.abs() > 0.35)
    cap = load_cap(store, idx, cols)
    prev_cap = cap.shift(1)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    uni_prev = uni.shift(1, fill_value=False).astype(bool)
    is_mega = pd.Series(cols.isin(MEGA), index=cols)
    sec = load_sectors(store)
    print(f"KSIC 군집 {sec.nunique()} · 분류 {len(sec):,}종목 · 005930 → {sec.get('KR:005930')} · 000660 → {sec.get('KR:000660')}", flush=True)
    ix = load_indices(store, idx)
    return {"c": c, "v": v, "r": r, "cap": cap, "prev_cap": prev_cap, "uni": uni, "uni_prev": uni_prev, "is_mega": is_mega,
            "sec": sec, "ix": ix, "idx": idx, "cols": cols}


def size_masks(uni: pd.DataFrame, capx: pd.DataFrame, is_mega: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(나머지, 대형, 중소형) — 초대형 둘을 뺀 유니버스, 시총 순위로 나눔."""
    rest = uni & ~is_mega.reindex(uni.columns).to_numpy()[None, :]
    rk = capx.where(rest).rank(axis=1, ascending=False)
    large = rest & (rk <= LARGE_N)
    small = rest & ~large & capx.notna()
    return rest, large, small


def daily_series(P: dict) -> pd.DataFrame:
    """같은 날(t) 수익 계열 — 구성은 전일(t−1) 정보."""
    r, pc, up, mega = P["r"], P["prev_cap"], P["uni_prev"], P["is_mega"]
    rest, large, small = size_masks(up, pc, mega)
    out = pd.DataFrame(index=P["idx"])
    w = pc[MEGA].where(r[MEGA].notna())
    out["mega"] = (r[MEGA] * w).sum(axis=1, min_count=1) / w.sum(axis=1, min_count=1)
    out["samsung"], out["hynix"] = r["KR:005930"], r["KR:000660"]
    out["rest_ew"] = r.where(rest).mean(axis=1)
    wl = pc.where(large & r.notna())
    out["large_cw"] = (r * wl).sum(axis=1, min_count=1) / wl.sum(axis=1, min_count=1)
    out["large_ew"] = r.where(large).mean(axis=1)
    out["small_ew"] = r.where(small).mean(axis=1)
    out["rest_up_frac"] = (r.where(rest) > 0).sum(axis=1) / r.where(rest).notna().sum(axis=1)
    for k in IDX:
        out[k] = P["ix"][k] / P["ix"][k].shift(1) - 1.0
    # 거래대금 점유 — 유니버스(전일) + 초대형 안에서
    v = P["v"]
    tot = v.where(up | mega.to_numpy()[None, :]).sum(axis=1)
    for name, m in (("mega", None), ("large", large), ("small", small)):
        s = (v[MEGA].sum(axis=1) if m is None else v.where(m).sum(axis=1)) / tot
        out[f"share_{name}"] = s
        out[f"dshare_{name}"] = s - s.shift(1).rolling(20, min_periods=15).mean()
    return out


def fwd_panels(P: dict) -> dict[int, pd.DataFrame]:
    c = P["c"]
    out = {}
    for h in HORIZONS:
        f = c.shift(-(1 + h)) / c.shift(-1) - 1.0
        out[h] = f.where(f.abs() <= 0.5)
    return out


def fwd_series(P: dict, F: dict[int, pd.DataFrame]) -> dict[int, pd.DataFrame]:
    """결정일 d(구성 = d 정보) 의 d+1 종가 → d+1+h 종가 누적 수익."""
    up, cap, mega = P["uni"], P["cap"], P["is_mega"]
    rest, large, small = size_masks(up, cap, mega)
    out = {}
    for h, f in F.items():
        s = pd.DataFrame(index=P["idx"])
        s["rest_ew"] = f.where(rest).mean(axis=1)
        s["large_ew"] = f.where(large).mean(axis=1)
        wl = cap.where(large & f.notna())
        s["large_cw"] = (f * wl).sum(axis=1, min_count=1) / wl.sum(axis=1, min_count=1)
        s["small_ew"] = f.where(small).mean(axis=1)
        w = cap[MEGA].where(f[MEGA].notna())
        s["mega"] = (f[MEGA] * w).sum(axis=1, min_count=1) / w.sum(axis=1, min_count=1)
        for k in IDX:
            lv = P["ix"][k]
            s[k] = lv.shift(-(1 + h)) / lv.shift(-1) - 1.0
        out[h] = s
    return out


# --------------------------------------------------------------------------- ① 같은 날


def events(D: pd.DataFrame, days: list) -> dict[str, pd.Series]:
    dd = D.loc[days]
    q10 = dd["mega"].quantile(0.10)
    ev = {
        "초대형 하위10%": D["mega"] <= q10,
        "초대형 ≤−2%": D["mega"] <= -0.02,
        "반도체지수 ≤−2%": D["semi"] <= -0.02,
        "초대형 ≤−2% & 지수200 >−0.5%": (D["mega"] <= -0.02) & (D["k200"] > -0.005),
    }
    print(f"초대형 하위10% 문턱(사후, 결정 구간 전체 분포) = {q10:.2%}")
    return {k: v.reindex(days).fillna(False).astype(bool) for k, v in ev.items()}


def same_day(D: pd.DataFrame, E: dict[str, pd.Series], days: list) -> pd.DataFrame:
    dd = D.loc[days]
    b, _ = nw_ols(dd["rest_ew"], dd[["mega"]], 4)
    beta = float(b[1])
    resid = dd["rest_ew"] - b[0] - beta * dd["mega"]
    print(f"나머지 EW 일수익 ~ 초대형 수익 β = {beta:.3f}, 상관 {dd['rest_ew'].corr(dd['mega']):.3f}"
          f" · 소형 β {nw_ols(dd['small_ew'], dd[['mega']], 4)[0][1]:.3f} · 대형시총가중 β {nw_ols(dd['large_cw'], dd[['mega']], 4)[0][1]:.3f}")
    rows = []
    for name, e in {**E, "평일(비이벤트, 초대형≤−2% 아님)": ~E["초대형 ≤−2%"]}.items():
        x = dd[e.to_numpy()]
        row = {"이벤트": name, "n": int(len(x))}
        for rn, a, bb in REGIMES:
            row[f"n_{rn}"] = int(in_regime(x.index, a, bb).sum())
        row.update({
            "초대형": x["mega"].mean(), "반도체지수": x["semi"].mean(), "K200": x["k200"].mean(),
            "나머지EW": x["rest_ew"].mean(), "나머지EW>0 비율": (x["rest_ew"] > 0).mean(),
            "상승종목비율": x["rest_up_frac"].mean(),
            "나머지EW−K200": (x["rest_ew"] - x["k200"]).mean(),
            "대형CW": x["large_cw"].mean(), "대형EW": x["large_ew"].mean(), "중소형EW": x["small_ew"].mean(),
            "중소형−대형EW": (x["small_ew"] - x["large_ew"]).mean(),
            "β잔차(나머지)": resid[e.to_numpy()].mean(), "β잔차t": nw_t(resid[e.to_numpy()], 0) if len(x) > 10 else np.nan,
            "Δ점유 초대형": x["dshare_mega"].mean(), "Δ점유 대형": x["dshare_large"].mean(), "Δ점유 중소형": x["dshare_small"].mean(),
        })
        for rn, a, bb in REGIMES:
            m = in_regime(x.index, a, bb)
            row[f"나머지EW_{rn}"] = x["rest_ew"][m].mean()
            row[f"β잔차_{rn}"] = resid[e.to_numpy()][m].mean()
        rows.append(row)
    return pd.DataFrame(rows).set_index("이벤트")


def sector_same_day(P: dict, E: pd.Series, days: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """섹터(KSIC, 초대형 제외 동일가중) 같은 날 수익 − 나머지 EW. 이벤트일 평균 vs 평일 평균."""
    rest, _, _ = size_masks(P["uni_prev"], P["prev_cap"], P["is_mega"])
    sr = group_mean(P["r"].where(rest), P["sec"]).loc[days]
    rew = P["r"].where(rest).mean(axis=1).loc[days]
    ex = sr.sub(rew, axis=0)
    e = E.reindex(days).to_numpy()
    rows = []
    for s in ex.columns:
        a, b = ex[s][e].dropna(), ex[s][~e].dropna()
        if len(a) < 10:
            continue
        t = float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a))))
        rows.append({"섹터": s, "n": len(a), "이벤트일 초과": a.mean(), "t": t, "평일 초과": b.mean(), "p": p_of(t)})
    out = pd.DataFrame(rows).set_index("섹터").sort_values("이벤트일 초과", ascending=False)
    return out, ex


# --------------------------------------------------------------------------- ② 예측력


def event_forward(FS: dict[int, pd.DataFrame], E: dict[str, pd.Series], days: list, extra: dict[int, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for h, s in FS.items():
        s = s.loc[days]
        q = {
            "(a) 나머지EW−K200": s["rest_ew"] - s["k200"],
            "(a) 나머지EW−KOSPI": s["rest_ew"] - s["kospi"],
            "(a) 중소형EW−K200": s["small_ew"] - s["k200"],
            "(a) 중소형EW−대형CW": s["small_ew"] - s["large_cw"],
            "(c) 초대형−K200": s["mega"] - s["k200"],
            "(c) 초대형 원수익": s["mega"],
            "K200 원수익": s["k200"],
            **{k: v.loc[days] for k, v in extra[h].items()},
        }
        for en, e in E.items():
            dummy = e.astype(float).rename("ev")
            for qn, y in q.items():
                b, t = nw_ols(y, dummy.to_frame(), h)
                ye = y[e.to_numpy()].dropna()
                row = {"이벤트": en, "h": h, "수익": qn, "n_ev": len(ye), "이벤트 평균": ye.mean(), "평일 평균": b[0],
                       "차이": b[1], "차이t": t[1], "p": p_of(t[1])}
                for rn, a, bb in REGIMES:
                    m = in_regime(y.index, a, bb)
                    bb_, tt = nw_ols(y[m], dummy[m].to_frame(), h)
                    row[f"차이_{rn}"], row[f"t_{rn}"] = bb_[1], tt[1]
                    row[f"n_{rn}"] = int((e.to_numpy() & m).sum())
                rows.append(row)
    return pd.DataFrame(rows)


def beneficiary_extra(P: dict, F: dict[int, pd.DataFrame], same_ex: pd.DataFrame) -> dict[int, dict[str, pd.Series]]:
    """(b) 같은 날 많이 오른 섹터(상위 k) − 많이 빠진 섹터(하위 k) 의 전방 수익, 그리고 종목 같은 날 수익 상위10% − 나머지EW.
    매일 계산 — 이벤트일 vs 평일 차이를 event_forward 가 잰다."""
    rest, _, _ = size_masks(P["uni"], P["cap"], P["is_mega"])
    r_today = P["r"].where(rest)
    pr = r_today.rank(axis=1, pct=True)
    out = {}
    sig = same_ex.reindex(P["idx"])  # 같은 날(d) 섹터 초과 — rest 구성은 d−1 이지만 섹터 순위만 쓴다
    k = (sig.notna().sum(axis=1) // 4).clip(lower=2)
    srk = sig.rank(axis=1, ascending=False)
    top = srk.le(k, axis=0)
    bot = srk.gt(sig.notna().sum(axis=1) - k, axis=0) & sig.notna()
    for h, f in F.items():
        fr = f.where(rest)
        sf = group_mean(fr, P["sec"])
        sf = sf.reindex(columns=sig.columns)
        rew = fr.mean(axis=1)
        out[h] = {
            "(b) 수혜섹터(상위k)−하락섹터(하위k)": sf.where(top).mean(axis=1) - sf.where(bot).mean(axis=1),
            "(b) 수혜섹터(상위k)−나머지EW": sf.where(top).mean(axis=1) - rew,
            "(b) 종목 같은날 상위10%−나머지EW": fr.where(pr > 0.9).mean(axis=1) - rew,
            "(b) 종목 같은날 하위10%−나머지EW": fr.where(pr <= 0.1).mean(axis=1) - rew,
        }
    return out


def sector_cs_ic(P: dict, F: dict[int, pd.DataFrame], days: list) -> pd.DataFrame:
    """일반화: 섹터 수익 횡단면(1·5일) → 다음 섹터 수익(d+1 종가 진입 1·5세션) 순위 IC."""
    rest, _, _ = size_masks(P["uni"], P["cap"], P["is_mega"])
    r = P["r"].where(rest)
    c = P["c"]
    r5 = (c / c.shift(5) - 1.0).where(rest)
    w = P["cap"]
    sig = {("KSIC-EW", 1): group_mean(r, P["sec"]), ("KSIC-EW", 5): group_mean(r5, P["sec"]),
           ("KSIC-CW", 1): group_wmean(r, w, P["sec"]), ("KSIC-CW", 5): group_wmean(r5, w, P["sec"])}
    rows = []
    for h in (1, 5):
        f = F[h].where(rest) if h in F else None
        fwd = {"KSIC-EW": group_mean(f, P["sec"]), "KSIC-CW": group_wmean(f, w, P["sec"])}
        for (bn, L), s in sig.items():
            ic = s.loc[days].T.corrwith(fwd[bn].loc[days].T, method="spearman").dropna()
            row = {"묶음": bn, "신호L": L, "h": h, "n": len(ic), "IC": ic.mean(), "t": nw_t(ic, max(h - 1, 0) + 1)}
            for rn, a, b in REGIMES:
                part = ic[in_regime(ic.index, a, b)]
                row[f"IC_{rn}"], row[f"t_{rn}"] = part.mean(), nw_t(part, max(h - 1, 0) + 1)
            row["p"] = p_of(row["t"])
            rows.append(row)
    return pd.DataFrame(rows)


def size_spread_ac(D: pd.DataFrame, FS: dict[int, pd.DataFrame], days: list) -> pd.DataFrame:
    """대형−소형 스프레드 자기상관: 오늘(1·5일) 중소형EW−대형CW → d+1 진입 h 세션 같은 스프레드."""
    sp1 = D["small_ew"] - D["large_cw"]
    sp5 = sp1.rolling(5).sum()
    rows = []
    for h in (1, 5):
        y = (FS[h]["small_ew"] - FS[h]["large_cw"]).loc[days]
        for L, x in ((1, sp1), (5, sp5)):
            xx = x.loc[days].rename("x")
            b, t = nw_ols(y, xx.to_frame(), h)
            row = {"신호": f"스프레드 {L}일", "h": h, "기울기": b[1], "t": t[1], "상관": y.corr(xx), "p": p_of(t[1])}
            for rn, a, bb in REGIMES:
                m = in_regime(y.index, a, bb)
                bb_, tt = nw_ols(y[m], xx[m].to_frame(), h)
                row[f"기울기_{rn}"], row[f"t_{rn}"] = bb_[1], tt[1]
            rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- ③ 자금 경로


def flow_paths(P: dict, FL: dict[str, pd.DataFrame], F: dict[int, pd.DataFrame], days: list) -> tuple[pd.DataFrame, dict]:
    """섹터별 투자자 순매수 / 섹터 전일 시총(순매수 행이 있는 종목만, bp). 원천 = 초대형 둘의 같은 비율.
    오늘 원천 순매수(d) → (i) 같은 날 섹터 순매수 상관 (ii) 다음 날 섹터 순매수 (iii) d+1 진입 1·5세션 섹터 수익."""
    up, pc, mega = P["uni"], P["prev_cap"], P["is_mega"]
    rest, _, _ = size_masks(up, P["cap"], mega)
    rows, cover = [], {}
    sec_ret = {h: group_mean(F[h].where(rest), P["sec"], 5) for h in (1, 5)}
    for inv, x in FL.items():
        m = rest & x.notna()
        num = x.where(m)
        den = pc.where(m)
        g = P["sec"].reindex([c for c in x.columns if c in P["sec"].index])
        cols = list(g.index)
        n = m[cols].T.groupby(g).sum().T
        sflow = (num[cols].T.groupby(g).sum(min_count=1).T / den[cols].T.groupby(g).sum(min_count=1).T * 1e4).where(n >= 5)
        src = (x[MEGA].sum(axis=1, min_count=2) / pc[MEGA].sum(axis=1, min_count=2) * 1e4).rename("src")
        cover[inv] = {"원천 결측일": int(src.loc[days].isna().sum()), "섹터당 평균 종목": float(n.loc[days].where(n.loc[days] >= 5).mean().mean())}
        s = src.loc[days]
        for sector in sflow.columns:
            fl = sflow[sector]
            row = {"투자자": inv, "섹터": sector, "같은날 상관": s.corr(fl.loc[days])}
            b, t = nw_ols(fl.shift(-1).loc[days], s.to_frame(), 1)
            row["다음날 순매수 β"], row["다음날 순매수 t"] = b[1], t[1]
            # 섹터 자기 순매수 통제(지속성) — 원천의 고유 기여
            b2, t2 = nw_ols(fl.shift(-1).loc[days], pd.concat([s, fl.loc[days].rename("own")], axis=1), 1)
            row["다음날 β|자기"], row["다음날 t|자기"] = b2[1], t2[1]
            for h in (1, 5):
                if sector not in sec_ret[h].columns:
                    continue
                b, t = nw_ols(sec_ret[h][sector].loc[days], s.to_frame(), h)
                row[f"수익{h} β"], row[f"수익{h} t"] = b[1], t[1]
                for rn, a, bb in REGIMES:
                    mm = in_regime(days, a, bb)
                    bb_, tt = nw_ols(sec_ret[h][sector].loc[days][mm], s[mm].to_frame(), h)
                    row[f"수익{h} t_{rn}"] = tt[1]
            rows.append(row)
    return pd.DataFrame(rows), cover


# --------------------------------------------------------------------------- ④ 종목 피처


def stock_features(P: dict, FL: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """사전 정의(결과 보기 전 고정) — d 종가·d 16:00 관측 순매수까지.
    rot_flow: (외인계+기관 순매수_d / 전일 시총) − 같은 섹터(유니버스) 평균. '오늘 자기 섹터 대비 받은 자금'.
    rot_share: log(거래대금_d / 직전 20일 평균) − 같은 섹터 평균. '오늘 섹터 안에서 거래대금 점유가 늘어난 정도'.
    """
    up, pc, sec = P["uni"], P["prev_cap"], P["sec"]
    cols = [c for c in P["cols"] if c in sec.index]
    g = sec.reindex(cols)

    def demean(x: pd.DataFrame) -> pd.DataFrame:
        x = x.where(up)[cols]
        mu = x.T.groupby(g).transform("mean").T
        n = x.notna().T.groupby(g).transform("sum").T
        return (x - mu).where(n >= MIN_SECTOR).reindex(columns=P["cols"])

    inst = FL["외인계"] + FL["기관"]  # 둘 다 있을 때만(한쪽 결측은 NaN)
    flow = inst / pc
    v = P["v"]
    surge = np.log(v.where(v > 0) / v.shift(1).rolling(20, min_periods=15).mean())
    return {"rot_flow": demean(flow.replace([np.inf, -np.inf], np.nan)), "rot_share": demean(surge.replace([np.inf, -np.inf], np.nan))}


def controls(P: dict) -> dict[str, pd.DataFrame]:
    c, v, r = P["c"], P["v"], P["r"]
    f = {"_rev5": c / c.shift(5) - 1.0, "_mom60": c / c.shift(60) - 1.0, "_lowvol": -r.ewm(span=60, min_periods=60).std(),
         "_liq": np.log(T.rmean(v, 20)), "_logcap": np.log(P["cap"].where(P["cap"] > 0))}
    mkt = r.clip(-0.5, 1.0).mean(axis=1)
    f["_beta"] = r.rolling(120, min_periods=100).cov(mkt).div(mkt.rolling(120, min_periods=100).var(), axis=0)
    return {k: x.replace([np.inf, -np.inf], np.nan) for k, x in f.items()}


def feature_ic(P: dict, feats: dict[str, pd.DataFrame], ranker: pd.DataFrame, E: pd.Series) -> pd.DataFrame:
    c, idx = P["c"], P["idx"]
    label = c.shift(-6) / c.shift(-1) - 1.0
    label = label.where(label.abs() <= 0.5).where(P["uni"])
    days = [x for x in idx if x >= START_DECISION]
    label = label.loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    sess = list(idx)
    guard([sess[sess.index(days[-1]) + 6]])
    ctl = {k: x.where(P["uni"]).loc[days] for k, x in controls(P).items()}
    cl = [ctl[k] for k in ("_rev5", "_mom60", "_lowvol", "_liq", "_logcap", "_beta")]
    rk = ranker.where(P["uni"]).loc[days]
    rows = []
    for name, x in feats.items():
        X = x.loc[days]
        ic = T.rowwise_ic(X, label)
        row = {"피처": name, "n일": len(ic), "커버리지": float((X.notna().sum(axis=1) / label.notna().sum(axis=1)).mean()),
               "IC": ic.mean(), "t": nw_t(ic, 4)}
        for rn, a, b in REGIMES:
            part = ic[in_regime(ic.index, a, b)]
            row[f"IC_{rn}"], row[f"t_{rn}"] = part.mean(), nw_t(part, 4)
        e = E.reindex(ic.index).fillna(False).to_numpy()
        row["IC 이벤트일"], row["n 이벤트일"] = ic[e].mean(), int(e.sum())
        row["IC 평일"] = ic[~e].mean()
        pic = N1.partial_ic(X, label, cl)
        row["편IC6"], row["편t6"] = pic.mean(), nw_t(pic, 4)
        for rn, a, b in REGIMES:
            part = pic[in_regime(pic.index, a, b)]
            row[f"편IC6_{rn}"] = part.mean()
        ric = N1.partial_ic(X, label, [rk])
        row["랭커잔차IC"], row["랭커잔차t"], row["랭커잔차 n일"] = ric.mean(), nw_t(ric, 4), len(ric)
        row["ρ랭커"] = T.rowwise_ic(X, rk).mean()
        for k_, cv in zip(("rev5", "logcap", "liq"), (ctl["_rev5"], ctl["_logcap"], ctl["_liq"]), strict=True):
            row[f"ρ_{k_}"] = T.rowwise_ic(X, cv).mean()
        dec = N1.deciles(X, label)
        row.update({f"D{i + 1}": x_ for i, x_ in enumerate(dec)})
        row["p"] = p_of(row["t"])
        rows.append(row)
    rk_ic = T.rowwise_ic(rk, label)
    print(f"랭커(signals ranker) IC 같은 우주·라벨 {rk_ic.mean():.4f} t {nw_t(rk_ic, 4):.2f} · {len(rk_ic)}일")
    return pd.DataFrame(rows).set_index("피처")


# --------------------------------------------------------------------------- ⑤ 노출 규칙


def tilt_rule(D: pd.DataFrame, E: dict[str, pd.Series], days: list) -> pd.DataFrame:
    """초대형 급락일 d → d+1 종가에 '중소형EW 매수·K200 매도' 덮어쓰기(단위 1) → h 세션 보유 → 청산.
    일수익 = 그날 중소형EW(전일 구성) − K200. 진입·청산마다 두 다리 편도 = 2×0.41%(한 번 갈아타기 = 매도+매수)."""
    spread = (D["small_ew"] - D["k200"])
    pos_days = list(D.index)
    loc = {x: i for i, x in enumerate(pos_days)}
    rows = []
    for en in ("초대형 하위10%", "초대형 ≤−2%", "반도체지수 ≤−2%"):
        e = E[en]
        for h in HORIZONS:
            pos = pd.Series(0.0, index=D.index)
            for d in e[e].index:
                i = loc[d]
                pos.iloc[i + 2: i + 2 + h] = 1.0  # d+1 종가 진입 → d+2 일수익부터 h 일
            pos = pos.loc[days[0]:END_DAY]
            sp = spread.reindex(pos.index).fillna(0.0)
            gross = pos * sp
            chg = pos.diff().abs().fillna(pos.iloc[0])
            net = gross - chg * 2 * COST
            for rn, a, b in (("전체", days[0], END_DAY), *REGIMES):
                m = in_regime(pos.index, a, b)
                g_, n_ = gross[m], net[m]
                yrs = m.sum() / 252
                rows.append({"이벤트": en, "h": h, "구간": rn, "보유일 비율": pos[m].mean(), "진입 횟수": int((pos[m].diff() > 0).sum()),
                             "총 연환산": g_.sum() / yrs, "비용후 연환산": n_.sum() / yrs,
                             "비용후 IR": n_.mean() / n_.std() * math.sqrt(252) if n_.std() > 0 else np.nan,
                             "보유일 평균(bp)": g_[pos[m] > 0].mean() * 1e4, "보유일 t": nw_t(g_[pos[m] > 0], h)})
    # 대조: 늘 들고 있기(무조건 중소형−K200)
    for rn, a, b in (("전체", days[0], END_DAY), *REGIMES):
        sp = spread.loc[days]
        m = in_regime(sp.index, a, b)
        rows.append({"이벤트": "대조: 늘 보유(비용 없음)", "h": 0, "구간": rn, "보유일 비율": 1.0, "진입 횟수": 0,
                     "총 연환산": sp[m].mean() * 252, "비용후 연환산": sp[m].mean() * 252,
                     "비용후 IR": sp[m].mean() / sp[m].std() * math.sqrt(252), "보유일 평균(bp)": sp[m].mean() * 1e4, "보유일 t": nw_t(sp[m], 4)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- 다중검정


def multi(pvals: dict[str, float], q: float = 0.10) -> dict:
    passed = T.bh(pvals, q)
    m = len(pvals)
    bonf = {k for k, p in pvals.items() if p <= 0.05 / m}
    return {"m": m, "BH10": sorted(passed), "본페로니5": sorted(bonf)}


def main_posthoc() -> int:
    """사후 점검(결과를 본 뒤 추가 — 다중검정 가족 밖, 탐색으로만 읽는다): ② (b) 의 유일한 BH 생존 줄 '반도체 급락일 같은 날
    상위10% 가 3~5세션 되돌린다' 가 (i) 평소 1일 반전의 증폭인지 (ii) 랭커가 이미 아는지. 피처 = −r_d(같은 날 수익 부호 반전)."""
    store = Store(root=Path("data"))
    P = build(store)
    idx, c = P["idx"], P["c"]
    D = daily_series(P)
    label = c.shift(-6) / c.shift(-1) - 1.0
    label = label.where(label.abs() <= 0.5).where(P["uni"])
    days = [x for x in idx if x >= START_DECISION]
    label = label.loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    sess = list(idx)
    guard([sess[sess.index(days[-1]) + 6]])
    rest, _, _ = size_masks(P["uni"], P["cap"], P["is_mega"])
    x = (-P["r"]).where(rest).loc[days]
    ranker = load_ranker(store, idx, P["cols"]).where(P["uni"]).loc[days]
    ic = T.rowwise_ic(x, label)
    ric = N1.partial_ic(x, label, [ranker])
    ctl = {k: v.where(P["uni"]).loc[days] for k, v in controls(P).items()}
    pic = N1.partial_ic(x, label, [ctl[k] for k in ("_mom60", "_lowvol", "_liq", "_logcap", "_beta")])
    rows = []
    for en, e in (("반도체지수 ≤−2%", D["semi"] <= -0.02), ("초대형 ≤−2%", D["mega"] <= -0.02), ("평일(반도체 >−2%)", D["semi"] > -0.02)):
        m = e.reindex(ic.index).fillna(False).to_numpy()
        row = {"날": en, "n": int(m.sum()), "IC(−r_d)": ic[m].mean(), "t": nw_t(ic[m], 4),
               "편IC(통제5, rev5 제외)": pic.reindex(ic.index)[m].mean(), "편t": nw_t(pic.reindex(ic.index)[m], 4),
               "랭커잔차IC": ric.reindex(ic.index)[m].mean(), "랭커잔차t": nw_t(ric.reindex(ic.index)[m], 4)}
        for rn, a, b in REGIMES:
            mm = m & in_regime(ic.index, a, b)
            row[f"랭커잔차IC_{rn}"], row[f"n_{rn}"] = ric.reindex(ic.index)[mm].mean(), int(mm.sum())
        dd = [x_ for x_ in ic.index[m]]
        dec = N1.deciles(x.loc[dd], label.loc[dd])
        row["D1(같은날 최고 상승)"], row["D10(같은날 최대 하락)"] = dec[0], dec[9]
        # 랭커 상위 24 안에서 같은 날 상위 10% 가 얼마나 들어 있나(실전에 닿는지)
        top24 = ranker.loc[dd].rank(axis=1, ascending=False) <= 24
        hot = x.loc[dd].rank(axis=1, pct=True) <= 0.1
        row["랭커상위24 중 같은날상위10% 비율"] = float((top24 & hot).sum(axis=1).mean() / 24)
        rows.append(row)
    out = pd.DataFrame(rows).set_index("날")
    pd.set_option("display.width", 260)
    print(out.round(4).T.to_string())
    out.to_csv(OUT / f"diag-rotation-posthoc-{TAG}.csv")
    return 0


def main() -> int:
    if "--posthoc" in sys.argv:
        return main_posthoc()
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 60)
    pd.set_option("display.max_rows", 400)
    store = Store(root=Path("data"))
    P = build(store)
    idx = P["idx"]
    D = daily_series(P)
    F = fwd_panels(P)
    FS = fwd_series(P, F)
    days = [x for x in idx if START_DECISION <= x <= END_DAY]
    sess = list(idx)
    # 결정일은 h=5 전방 수익이 서는 날까지만(라벨 끝 ≤ END)
    days = [x for x in days if sess.index(x) + 1 + max(HORIZONS) < len(sess)]
    guard([sess[sess.index(days[-1]) + 1 + max(HORIZONS)]])
    print(f"패널 {idx[0]}~{idx[-1]} · 결정일 {days[0]}~{days[-1]} {len(days)}일 · 평균 유니버스 {P['uni'].loc[days].sum(axis=1).mean():.0f}"
          f" · 대형(초대형 제외) {LARGE_N}")
    res: dict = {}

    print("\n=== ① 같은 날 시소 ===")
    E = events(D, days)
    sd = same_day(D, E, days)
    print((sd * 1).round(4).T.to_string())
    sec_tab, sec_ex = sector_same_day(P, E["초대형 ≤−2%"], days)
    sec_tab["BH10"] = sec_tab.index.isin(T.bh(sec_tab["p"].to_dict()))
    print("\n섹터 같은 날 초과(나머지 EW 대비) — 초대형 ≤−2% 날")
    print(sec_tab.round(4).to_string())

    print("\n=== ② 예측력 ===")
    extra = beneficiary_extra(P, F, sec_ex)
    ef = event_forward(FS, E, days, {h: {k: v for k, v in extra[h].items()} for h in HORIZONS})
    print(ef.round(4).to_string())
    cs = sector_cs_ic(P, F, days)
    print("\n섹터 횡단면 다음 수익 IC")
    print(cs.round(4).to_string())
    ac = size_spread_ac(D, FS, days)
    print("\n중소형−대형 스프레드 자기상관")
    print(ac.round(4).to_string())

    print("\n=== ③ 자금 경로 ===")
    FL = load_flows(store, idx, P["cols"])
    fp, cover = flow_paths(P, FL, F, days)
    print(cover)
    print(fp.round(3).to_string())

    print("\n=== ④ 종목 피처 ===")
    ranker = load_ranker(store, idx, P["cols"])
    feats = stock_features(P, FL)
    fi = feature_ic(P, feats, ranker, E["초대형 ≤−2%"])
    print(fi.round(4).T.to_string())

    print("\n=== ⑤ 노출 규칙 ===")
    tr = tilt_rule(D, E, days)
    print(tr.round(4).to_string())

    # 다중검정 — 가족별·전체
    fam = {
        "②이벤트 전방": {f"{r.이벤트}|{r.수익}|h{r.h}": r.p for r in ef.itertuples()},
        "②섹터 횡단면": {f"{r.묶음}|L{r.신호L}|h{r.h}": r.p for r in cs.itertuples()},
        "②스프레드": {f"{r.신호}|h{r.h}": r.p for r in ac.itertuples()},
        "③자금경로": {f"{r['투자자']}|{r['섹터']}|{col}": p_of(r[col]) for _, r in fp.iterrows()
                   for col in ("다음날 t|자기", "수익1 t", "수익5 t") if col in fp.columns and np.isfinite(r[col])},
        "④피처": {k: fi.loc[k, "p"] for k in fi.index},
    }
    allp = {f"{fn}|{k}": p for fn, d_ in fam.items() for k, p in d_.items()}
    mt = {fn: multi(d_) for fn, d_ in fam.items()} | {"전체": multi(allp)}
    print("\n=== 다중검정 ===")
    for fn, m in mt.items():
        print(f"{fn}: m={m['m']} · BH10% {len(m['BH10'])} · 본페로니5% {len(m['본페로니5'])}")
        if fn == "전체":
            print("  BH10:", m["BH10"][:60])
            print("  본페로니:", m["본페로니5"])

    OUT.mkdir(exist_ok=True)
    sd.to_csv(OUT / f"diag-rotation-sameday-{TAG}.csv")
    sec_tab.to_csv(OUT / f"diag-rotation-sector-sameday-{TAG}.csv")
    ef.to_csv(OUT / f"diag-rotation-forward-{TAG}.csv", index=False)
    cs.to_csv(OUT / f"diag-rotation-sector-ic-{TAG}.csv", index=False)
    ac.to_csv(OUT / f"diag-rotation-spread-ac-{TAG}.csv", index=False)
    fp.to_csv(OUT / f"diag-rotation-flows-{TAG}.csv", index=False)
    fi.to_csv(OUT / f"diag-rotation-features-{TAG}.csv")
    tr.to_csv(OUT / f"diag-rotation-tilt-{TAG}.csv", index=False)
    (OUT / f"diag-rotation-multi-{TAG}.json").write_text(json.dumps(mt, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
