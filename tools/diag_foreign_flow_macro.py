"""외국인 수급 · 환율 · 금리 진단 — docs/diag/foreign-flow-macro.md §1 대로 한 번 잰다.

    nice -n 10 .venv/bin/python tools/diag_foreign_flow_macro.py --fetch   # KRX 국채 지표·미국달러선물 → 연구 캐시(세션당 2콜)
    nice -n 10 .venv/bin/python tools/diag_foreign_flow_macro.py --panel   # 창고(flows·market_stats·prices·indices) + 캐시 → 패널
    nice -n 10 .venv/bin/python tools/diag_foreign_flow_macro.py --run     # (a)(b)(c) → result.json · 표

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다(KRX 응답은 `data/_diag/foreign-flow/krx/`
연구 캐시에만). 노출 기준선은 베끼지 않는다: V6·HMM 경로는 시행 BB 의 `v2_hmm_exposure.v6_path`·`hmm_path`, 노출 식은
`diag_bear_2022.apply_exposure` 와 같은 식, 지표는 `trial_overlay.metrics`, NW t 는 이 도구의 `hac_t`(z 회귀 기울기, 통제변수 지원).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd

from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

OUT = Path("data/_diag/foreign-flow")
KRX_CACHE = OUT / "krx"
INDEX = "KR:IDX:KOSPI200"
#: 문서 §1 — 신호 첫날(F_20 이 차는 날) · 마지막 날 · 금고 시작 앞.
SIGNAL_START, LAST_DAY = date(2021, 9, 10), date(2026, 6, 30)
FETCH_START, FETCH_END = date(2021, 6, 1), date(2026, 7, 10)
PERIODS = {"하락": (date(2021, 9, 10), date(2022, 9, 30)), "박스": (date(2022, 10, 1), date(2024, 12, 31)),
           "급등": (date(2025, 1, 1), date(2026, 6, 30))}
INVESTORS = {"외국인": "foreign", "외인계": "foreign", "기관합계": "inst", "기관": "inst", "개인": "retail",
             "연기금": "pension", "기금": "pension"}
LARGE_N, SMALL_RANKS = 50, (201, 1000)
MAX_MOVE = 0.5
ONE_WAY_COST = 0.0041
MIN_TRAIN, MIN_Z, STEP, DEADBAND = 120, 60, 0.05, 0.10
MACRO = ["X1", "X2", "X3", "X4", "X5"]


# -- 내려받기 ---------------------------------------------------------------------------------------------------------

def sessions_from_index(store: Store, start: date, end: date) -> list[date]:
    as_of = datetime.combine(end + timedelta(days=3), time(0), tzinfo=UTC)
    frame = store.get("indices", as_of=as_of, entity=[INDEX], market="KR", lookback=(as_of.date() - start).days + 1,
                      columns=["entity_id", "valid_from", "close"])
    days = sorted({pd.Timestamp(v).date() for v in frame["valid_from"]})
    return [d for d in days if start <= d <= end]


def fetch(store: Store) -> int:
    import httpx

    from quant_rl_trading.collectors.krx_openapi import AUTH_HEADER, BASE_URL
    from quant_rl_trading.settings import load_env

    load_env()
    key = os.environ.get("KRX_OPENAPI_KEY", "").strip()
    if not key:
        print("KRX_OPENAPI_KEY 없음", flush=True)
        return 2
    KRX_CACHE.mkdir(parents=True, exist_ok=True)
    days = sessions_from_index(store, FETCH_START, FETCH_END)
    todo = [d for d in days if not (KRX_CACHE / f"{d:%Y%m%d}.json").exists()]
    print(f"세션 {len(days)} · 받을 것 {len(todo)}", flush=True)
    client = httpx.Client(timeout=30)
    for i, day in enumerate(todo):
        out = {}
        for name, path in (("kts", "/bon/kts_bydd_trd"), ("fut", "/drv/fut_bydd_trd")):
            for attempt in range(4):
                try:
                    r = client.get(f"{BASE_URL}{path}", headers={AUTH_HEADER: key}, params={"basDd": f"{day:%Y%m%d}"})
                    if r.status_code != 200:
                        raise RuntimeError(f"{path} HTTP {r.status_code}")
                    rows = list(r.json().get("OutBlock_1") or [])
                    break
                except Exception as error:
                    if attempt == 3:
                        print(f"{day} {path} 실패: {error}", flush=True)
                        return 1
                    _time.sleep(3 * (attempt + 1))
            if name == "kts":
                rows = [x for x in rows if x.get("GOVBND_ISU_TP_NM") == "지표"]
            else:
                rows = [x for x in rows if x.get("PROD_NM") == "미국달러 선물" and x.get("MKT_NM") == "정규"
                        and " F " in str(x.get("ISU_NM"))]
            out[name] = rows
            _time.sleep(0.3)
        (KRX_CACHE / f"{day:%Y%m%d}.json").write_text(json.dumps(out, ensure_ascii=False))
        if i % 100 == 0:
            print(f"{i}/{len(todo)} {day} kts {len(out['kts'])} fut {len(out['fut'])}", flush=True)
    print("끝", flush=True)
    return 0


# -- 패널 -------------------------------------------------------------------------------------------------------------

def third_monday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 14)


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return float("nan")


def krx_panel() -> pd.DataFrame:
    """세션 × [KR 국고 지표 수익률, 원달러 최근월 정산가·같은 월물 일변화, 내재 금리차]."""
    rows, prev = [], None
    for path in sorted(KRX_CACHE.glob("*.json")):
        day = datetime.strptime(path.stem, "%Y%m%d").date()
        data = json.loads(path.read_text())
        rec: dict = {"day": day}
        for x in data["kts"]:
            tenor = str(x.get("BND_EXP_TP_NM"))
            name = str(x.get("ISU_NM"))
            if name.startswith("국고"):          # 물가채 제외
                rec[f"kr{tenor}y"] = _num(x.get("CLSPRC_YD"))
        fut = []
        for x in data["fut"]:
            label = str(x["ISU_NM"]).split(" F ")[1].split()[0]   # 202610
            settle = _num(x.get("SETL_PRC"))
            if np.isfinite(settle) and settle > 0:
                fut.append((label, settle, _num(x.get("SPOT_PRC"))))
        fut.sort()
        # 만기가 지난 월물은 응답에 없다. 최근월 = 첫째. 내재 금리차는 최근월과 **만기가 12개월 뒤에 가장 가까운 월물** 사이로 읽는다
        # (§1 정정 — 차근월과는 만기 차가 한 달뿐이라 호가 단위 0.1원이 연 0.1%p 잡음이 된다).
        if len(fut) >= 2:
            l1, f1, spot = fut[0]
            months = lambda lab: int(lab[:4]) * 12 + int(lab[4:])
            l2, f2, _ = min(fut[1:], key=lambda x: abs(months(x[0]) - months(l1) - 12))
            t1 = third_monday(int(l1[:4]), int(l1[4:]))
            t2 = third_monday(int(l2[:4]), int(l2[4:]))
            rec.update(fx=f1, fx_label=l1, spot=spot, implied=(f2 / f1 - 1.0) * 365.0 / max(1, (t2 - t1).days))
            # 같은 월물끼리의 일변화 — 롤 날짜에 월물 간 차이를 수익으로 섞지 않는다.
            if prev is not None and l1 in prev:
                rec["fx_ret"] = np.log(f1 / prev[l1])
            elif prev is not None and l1 not in prev:
                rec["fx_ret"] = float("nan")
            prev = {lab: s for lab, s, _ in fut}
        rows.append(rec)
    frame = pd.DataFrame(rows).set_index("day").sort_index()
    return frame


def us_rates(store: Store, days: list[date]) -> pd.DataFrame:
    as_of = datetime.combine(days[-1] + timedelta(days=5), time(0), tzinfo=UTC)
    frame = store.get("indices", as_of=as_of, entity=["US:RATE:UST2Y", "US:RATE:UST10Y"], market="US",
                      lookback=(as_of.date() - days[0]).days + 30, columns=["entity_id", "valid_from", "close"])
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.tz_convert("America/New_York").dt.date
    wide = frame.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    wide.columns = [c.split(":")[-1].lower() for c in wide.columns]
    # 결정 d(서울)에 아는 값 = 뉴욕 d−1 까지(H.15 는 뉴욕 d 16:15 = KST d+1 05:15 공표, 문서 §0).
    out = {}
    for d in days:
        known = wide[wide.index <= d - timedelta(days=1)]
        out[d] = known.iloc[-1] if not known.empty else pd.Series(dtype=float)
    return pd.DataFrame(out).T


def flows_and_caps(store: Store, days: list[date]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """일별 주체별 순매수 합·종목 수 / 시총 합, 그리고 종목×일 시총(순위용). 한 달씩 읽는다(메모리)."""
    flows, caps = [], []
    months = sorted({(d.year, d.month) for d in days})
    for y, m in months:
        lo = date(y, m, 1)
        hi = (lo + timedelta(days=32)).replace(day=1)
        as_of = datetime.combine(min(hi + timedelta(days=10), date(2026, 10, 1)), time(0), tzinfo=UTC)
        until = datetime.combine(hi, time(0), tzinfo=UTC)
        look = (as_of.date() - lo).days + 1
        f = store.get("flows", as_of=as_of, lookback=look, until=until, market="KR",
                      columns=["entity_id", "valid_from", "investor", "net_value", "source"])
        f = f[f["investor"].isin(INVESTORS)]
        f["day"] = pd.to_datetime(f["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
        f = f[(f["day"] >= lo) & (f["day"] < hi)]
        f["who"] = f["investor"].map(INVESTORS)
        flows.append(f[["day", "entity_id", "who", "net_value", "source"]])
        c = store.get("market_stats", as_of=as_of, lookback=look, until=until, market="KR",
                      columns=["entity_id", "valid_from", "metric", "value"])
        c = c[c["metric"] == "market_cap"]
        c["day"] = pd.to_datetime(c["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
        c = c[(c["day"] >= lo) & (c["day"] < hi)]
        caps.append(c[["day", "entity_id", "value"]])
        print(f"{y}-{m:02d} flows {len(f):,} caps {len(c):,}", flush=True)
    return pd.concat(flows, ignore_index=True), pd.concat(caps, ignore_index=True)


def panel(store: Store) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    days = sessions_from_index(store, date(2021, 7, 1), FETCH_END)
    flows, caps = flows_and_caps(store, days)
    capw = caps.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index()
    total_cap = capw.sum(axis=1)
    agg = flows.groupby(["day", "who"])["net_value"].sum().unstack()
    n_ent = flows[flows["who"] == "foreign"].groupby("day")["entity_id"].nunique().rename("n_foreign")
    src = flows.groupby(["day", "source"]).size().unstack().fillna(0)
    print("출처별 행 수(월 합):\n" + src.groupby(pd.to_datetime(src.index).to_period("Q")).sum().to_string(), flush=True)
    # 대형(시총 상위 200) 과 나머지의 외국인 순매수 — 기록만.
    rank = capw.rank(axis=1, ascending=False)
    fw = flows[flows["who"] == "foreign"].pivot_table(index="day", columns="entity_id", values="net_value", aggfunc="sum")
    fw = fw.reindex(index=capw.index, columns=capw.columns)
    top = (fw.where(rank <= 200)).sum(axis=1)
    # 수익
    now = datetime.combine(days[-1] + timedelta(days=3), time(0), tzinfo=UTC)
    prices = read_prices(store, as_of=now, lookback=(now.date() - days[0]).days + 1, columns=["close"],
                         adjusted=True, market="KR")
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    wide = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    del prices
    wide = wide.reindex(days)
    ret = wide / wide.shift(1) - 1.0
    ret = ret.where(ret.abs() <= MAX_MOVE)
    prev_rank = rank.reindex(days).shift(1)                       # d 수익의 바구니 = d−1 종가 시총 순위
    ret = ret.reindex(columns=prev_rank.columns)
    large = ret.where(prev_rank <= LARGE_N).mean(axis=1)
    small = ret.where((prev_rank >= SMALL_RANKS[0]) & (prev_rank <= SMALL_RANKS[1])).mean(axis=1)
    as_of = datetime.combine(days[-1] + timedelta(days=3), time(0), tzinfo=UTC)
    idx = store.get("indices", as_of=as_of, entity=[INDEX], market="KR", lookback=(as_of.date() - days[0]).days + 1,
                    columns=["entity_id", "valid_from", "close"])
    idx["day"] = pd.to_datetime(idx["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    k200 = idx.groupby("day")["close"].last().reindex(days)
    out = pd.DataFrame(index=pd.Index(days, name="day"))
    out["total_cap"] = total_cap.reindex(days)
    for who in ("foreign", "inst", "retail", "pension"):
        out[f"net_{who}"] = agg[who].reindex(days) if who in agg else np.nan
    out["n_foreign"] = n_ent.reindex(days)
    out["net_foreign_top200"] = top.reindex(days)
    out["k200"] = k200
    out["r_k200"] = k200 / k200.shift(1) - 1.0
    out["r_large"], out["r_small"] = large, small
    out = out.join(us_rates(store, days), how="left")   # KRX 캐시는 --run 에서 붙인다(내려받기와 따로 돌게)
    out.to_pickle(OUT / "panel.pkl")
    print(out.describe().T.to_string(), flush=True)
    print(f"패널 {OUT / 'panel.pkl'} · {len(out)} 세션 · 결측: " +
          ", ".join(f"{c} {int(out[c].isna().sum())}" for c in out.columns if out[c].isna().any()), flush=True)
    return 0


# -- 분석 -------------------------------------------------------------------------------------------------------------

def fwd(r: pd.Series, h: int) -> pd.Series:
    """결정 d → d+1 종가 ~ d+1+h 종가 누적수익(로그 합 아님, 복리)."""
    g = np.log1p(r)
    # d+2 .. d+1+h 의 일수익 = d+1 종가 → d+1+h 종가
    s = g[::-1].rolling(h, min_periods=h).sum()[::-1]          # s[t] = g[t..t+h-1]
    return np.expm1(s.shift(-2))


def fwd_sum(x: pd.Series, h: int) -> pd.Series:
    """d+1 .. d+h 합(미래 외국인 순매수)."""
    s = x[::-1].rolling(h, min_periods=h).sum()[::-1]
    return s.shift(-1)


def hac_t(y: pd.Series, x: pd.Series, lag: int, controls: pd.DataFrame | None = None) -> float:
    """z 회귀 기울기의 NW t. controls 가 있으면 부분 기울기."""
    frame = pd.concat([y.rename("y"), x.rename("x")] + ([controls] if controls is not None else []), axis=1)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 30 or (frame.std() <= 0).any():
        return float("nan")
    z = (frame - frame.mean()) / frame.std()
    X = np.column_stack([np.ones(len(z)), z.drop(columns="y").to_numpy()])
    yv = z["y"].to_numpy()
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    e = yv - X @ beta
    xtx_inv = np.linalg.inv(X.T @ X)
    u = X * e[:, None]
    S = u.T @ u
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1)
        G = u[L:].T @ u[:-L]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    return float(beta[1] / np.sqrt(V[1, 1]))


def ic_row(sig: pd.Series, tgt: pd.Series, h: int, controls: pd.DataFrame | None = None) -> dict:
    out = {}
    tgt = tgt.reindex(sig.index)
    controls = None if controls is None else controls.reindex(sig.index)
    for name, (lo, hi) in {"전체": (SIGNAL_START, LAST_DAY), **PERIODS}.items():
        m = (sig.index >= lo) & (sig.index <= hi)
        s, t = sig[m], tgt[m]
        c = None if controls is None else controls[m]
        ok = pd.concat([s, t], axis=1).dropna()
        out[name] = {"ic": float(ok.iloc[:, 0].corr(ok.iloc[:, 1], method="spearman")) if len(ok) > 10 else float("nan"),
                     "t": hac_t(t, s, h, c), "n": len(ok), "n_indep": int(len(ok) // h)}
    return out


def build_signals(p: pd.DataFrame) -> pd.DataFrame:
    s = pd.DataFrame(index=p.index)
    daily = p["net_foreign"] / p["total_cap"]
    s["f_daily"] = daily
    for k in (1, 5, 20):
        s[f"F{k}"] = daily.rolling(k, min_periods=k).sum()
    for who in ("inst", "retail"):
        s[f"{who}20"] = (p[f"net_{who}"] / p["total_cap"]).rolling(20, min_periods=20).sum()
    fx_lvl = np.exp(p["fx_ret"].fillna(0.0).cumsum())   # 같은 월물 일변화를 이은 수준(롤 날은 0)
    s["fx_ret"] = p["fx_ret"]
    s["X1"] = np.log(fx_lvl).diff(20)
    s["X2"] = np.log(fx_lvl).diff(5)
    s["X3"] = p["implied"].diff(20)
    s["X4"] = (p["kr10y"] - p["ust10y"]).diff(20)
    s["X5"] = (p["ust10y"] - p["ust2y"]).diff(20)
    return s


def predicted_inflow(s: pd.DataFrame, target: pd.Series, days: list[date], cols: list[str] = MACRO) -> pd.Series:
    """§1(c) — 확장창 OLS, 학습 = 목표가 d 전에 실현된 관측(신호일 ≤ d−20), 매월 첫 세션 재적합, 최소 120관측."""
    pos = {d: i for i, d in enumerate(days)}
    X = s[cols]
    out, coef, month = {}, None, None
    for d in days:
        i = pos[d]
        if (d.year, d.month) != month:
            month = (d.year, d.month)
            cutoff = days[i - 20] if i >= 20 else None
            if cutoff is not None:
                train = pd.concat([X, target.rename("y")], axis=1).loc[:cutoff].dropna()
                if len(train) >= MIN_TRAIN:
                    A = np.column_stack([np.ones(len(train)), train[cols].to_numpy()])
                    coef, *_ = np.linalg.lstsq(A, train["y"].to_numpy(), rcond=None)
        if coef is None or X.loc[d].isna().any():
            continue
        out[d] = float(coef[0] + X.loc[d].to_numpy() @ coef[1:])
    return pd.Series(out)


def expanding_z(x: pd.Series) -> pd.Series:
    x = x.dropna()
    mu = x.expanding(MIN_Z).mean()
    sd = x.expanding(MIN_Z).std()
    return ((x - mu) / sd).dropna()


def stepped(raw: pd.Series) -> pd.Series:
    """0.05 반올림 · 데드밴드 0.10 — HMM 경로와 같은 규칙."""
    out, cur = {}, None
    for d, v in raw.dropna().items():
        k = round(float(v) / STEP) * STEP
        if cur is None or abs(k - cur) >= DEADBAND:
            cur = k
        out[d] = cur
    return pd.Series(out)


def run(store: Store) -> int:
    from tools.trial_overlay import metrics
    from tools.v2_hmm_exposure import hmm_path, v6_path

    p = pd.read_pickle(OUT / "panel.pkl")  # invariant-allow: data-access — 이 진단의 연구 캐시
    p = p.join(krx_panel(), how="left")
    days = list(p.index)
    s = build_signals(p)
    res: dict = {"a": {}, "b": {}, "lag": {}, "c": {}}
    # 성과 구간 끝이 6/30 을 넘는 관측은 버린다 — 신호일 d 의 끝 = d+1+h.
    pos = {d: i for i, d in enumerate(days)}

    def clip_end(series: pd.Series, h: int, extra: int) -> pd.Series:
        keep = [d for d in series.index if pos[d] + extra + h < len(days) and days[pos[d] + extra + h] <= LAST_DAY]
        return series.reindex(keep)

    targets = {"K200": p["r_k200"], "대형EW": p["r_large"], "중소형EW": p["r_small"],
               "대형−중소형": p["r_large"] - p["r_small"]}
    for h in (5, 20):
        for tname, r in targets.items():
            y = clip_end(fwd(r, h), h, 1)
            for k in (1, 5, 20):
                res["a"][f"F{k}→{tname} h{h}"] = ic_row(s[f"F{k}"], y, h)
    for h in (5, 20):
        y = clip_end(fwd_sum(s["f_daily"], h), h, 0)
        for x in MACRO:
            res["b"][f"{x}→F h{h}"] = ic_row(s[x], y, h)
            res["b"][f"{x}→F h{h} |F20"] = ic_row(s[x], y, h, controls=s[["F20"]])
    # 시차 상관: corr(F1_t, fx_ret_{t+L}) — L<0 이면 환율이 먼저.
    win = s.loc[SIGNAL_START:LAST_DAY]
    res["lag"]["daily"] = {L: float(win["f_daily"].corr(win["fx_ret"].shift(-L))) for L in range(-10, 11)}
    blk = win[["f_daily", "fx_ret"]].groupby(np.arange(len(win)) // 5).sum()
    res["lag"]["block5"] = {L: float(blk["f_daily"].corr(blk["fx_ret"].shift(-L))) for L in range(-4, 5)}

    # (c)
    target20 = fwd_sum(s["f_daily"], 20)
    P = predicted_inflow(s, target20, days)
    zP = expanding_z(P)
    # 표본 밖 예측력 — 예측 유입 P 가 실제 다음 20세션 외국인 순매수·K200 수익을 맞히나(기록, 변형 선택에 안 쓴다).
    res["b"]["P(표본밖)→F h20"] = ic_row(P, clip_end(target20, 20, 0), 20)
    res["a"]["P(표본밖)→K200 h20"] = ic_row(P, clip_end(fwd(p["r_k200"], 20), 20, 1), 20)
    zF = expanding_z(s["F20"])
    k_c1 = stepped((1 + 0.25 * zP).clip(0.5, 1.0))
    k_c2 = stepped((1 + 0.25 * zF).clip(0.5, 1.0))
    a_t1 = stepped((0.5 + 0.25 * zP).clip(0.0, 1.0))
    # 결정 d → d+1→d+2 수익. 경로(V6·HMM)는 세션 e=d+1 의 값(= e 전날까지 정보)을 d 에 붙인다.
    nxt = {d: days[pos[d] + 1] for d in days if pos[d] + 1 < len(days)}
    e_days = list(nxt.values())
    v6 = v6_path(store, e_days)
    hm = hmm_path(e_days)
    k_v6 = pd.Series({d: v6.get(e, np.nan) for d, e in nxt.items()})
    k_hmm = pd.Series({d: hm.get(e, np.nan) for d, e in nxt.items()})
    r2 = lambda r: r.shift(-2)
    rk, rs = r2(p["r_k200"]), r2(p["r_small"])
    start = max(k_c1.index.min(), SIGNAL_START)
    eval_days = [d for d in days if start <= d and d in nxt and pos[d] + 2 < len(days) and days[pos[d] + 2] <= LAST_DAY]
    res["c"]["eval_start"], res["c"]["eval_end"] = str(eval_days[0]), str(eval_days[-1])

    def expo(k: pd.Series) -> tuple[pd.Series, pd.Series]:
        k = k.reindex(eval_days).ffill().fillna(1.0)
        prev = k.shift(1).fillna(k.iloc[0])
        return k * rk.reindex(eval_days) - (k - prev).abs() * ONE_WAY_COST, k

    def tilt(a: pd.Series) -> tuple[pd.Series, pd.Series]:
        a = a.reindex(eval_days).ffill().fillna(0.5)
        prev = a.shift(1).fillna(a.iloc[0])
        return a * rk.reindex(eval_days) + (1 - a) * rs.reindex(eval_days) - 2 * (a - prev).abs() * ONE_WAY_COST, a

    variants = {
        "N0": expo(pd.Series(1.0, index=eval_days)), "V6": expo(k_v6), "HMM": expo(k_hmm),
        "C1": expo(k_c1), "C2": expo(k_c2), "C3": expo(pd.concat([k_hmm, k_c1.reindex(k_hmm.index).ffill()], axis=1).min(axis=1)),
        "T0": tilt(pd.Series(0.5, index=eval_days)), "T1": tilt(a_t1),
    }
    bench = rk.reindex(eval_days)
    for name, (r, k) in variants.items():
        row = {}
        for pname, (lo, hi) in {"전체": (date(2000, 1, 1), LAST_DAY), **PERIODS}.items():
            m = [d for d in eval_days if lo <= d <= hi]
            if len(m) < 20:
                continue
            met = metrics(r.reindex(m), bench.reindex(m))
            met["avg_k"] = float(k.reindex(m).mean())
            met["switches"] = int((k.reindex(m).diff().abs() > 1e-9).sum())
            row[pname] = met
        res["c"][name] = row
    res["c"]["k_first"] = {"C1": str(k_c1.index.min()), "zP_first": str(zP.index.min())}
    pd.DataFrame({"P": P, "zP": zP, "k_c1": k_c1, "k_c2": k_c2, "a_t1": a_t1, "k_v6": k_v6, "k_hmm": k_hmm}).to_pickle(OUT / "paths.pkl")
    (OUT / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    report(res)
    return 0


def report(res: dict, *, ext: bool = False) -> None:
    def cell(c: dict) -> str:
        return f"{c['ic']:+.3f}({c['t']:+.1f})" if np.isfinite(c["ic"]) else "—"

    cols = ["전체", *PERIODS]
    for part in ("a", "b"):
        print(f"\n## ({part}) IC(NW t) — 열: {' · '.join(cols)}")
        for key, row in res[part].items():
            n = row["전체"]
            print(f"{key:28s} " + "  ".join(cell(row[c]) for c in cols) + f"   n={n['n']} 독립≈{n['n_indep']}")
    if not ext:
        print("\n## 시차 상관 corr(F1_t, Δfx_{t+L}) — L<0: 환율이 먼저")
        print(" ".join(f"{L}:{v:+.2f}" for L, v in res["lag"]["daily"].items()))
        print("5세션 블록: " + " ".join(f"{L}:{v:+.2f}" for L, v in res["lag"]["block5"].items()))
        print(f"\n## (c) {res['c']['eval_start']} ~ {res['c']['eval_end']}")
    else:
        print(f"\n## (c′) {res['c']['eval']}")
    for name, row in res["c"].items():
        if not isinstance(row, dict) or "전체" not in row:
            continue
        parts = []
        for pn in cols:
            if pn in row:
                m = row[pn]
                parts.append(f"{pn} 연{m['ann']:+.1%} 샤프{m['sharpe']:.2f} MDD{m['mdd']:.1%} IR{m['ir']:+.2f} k{m['avg_k']:.2f}/{m['switches']}")
        print(f"{name:4s} " + " | ".join(parts))


# -- §3 확장: ECOS · FRED(최초 공표일) --------------------------------------------------------------------------------

#: §3.0 — 목록 API(StatisticItemList)로 이름을 대조한 코드. 짐작하지 않는다.
ECOS_SERIES = {
    "cd91": ("817Y002", "010502000"), "call": ("817Y002", "010101000"),
    "ktb3y": ("817Y002", "010200000"), "ktb10y": ("817Y002", "010210000"),
    "frg_kospi": ("802Y001", "0030000"), "frg_kosdaq": ("802Y001", "0113000"), "cap_kospi": ("802Y001", "0183000"),
    "usdkrw_1530": ("731Y003", "0000003"),
}
FRED_SERIES = ["DGS3MO", "DGS2", "DGS5", "DGS10", "DGS30", "T10Y2Y", "T10Y3M", "SOFR", "DFII10", "DTWEXBGS", "BAA10Y"]
EXT = ["Y1", "Y2", "Y3", "Y4", "Y5", "Y6"]


def fetch_macro() -> int:
    """ECOS 일별 계열 · FRED 최초 공표본(ALFRED output_type=4) → 연구 캐시 JSON. 키는 출력하지 않는다."""
    import httpx

    from quant_rl_trading.collectors.macro_source import ECOS_BASE, FRED_BASE
    from quant_rl_trading.settings import load_env

    load_env()
    ecos_key = os.environ.get("ECOS_API_KEY", "").strip()
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if not ecos_key or not fred_key:
        print("ECOS_API_KEY / FRED_API_KEY 없음", flush=True)
        return 2
    out: dict = {"ecos": {}, "fred": {}}
    with httpx.Client(timeout=60) as client:
        for name, (table, item) in ECOS_SERIES.items():
            url = "/".join([f"{ECOS_BASE}/StatisticSearch", ecos_key, "json", "kr", "1", "100000",
                            table, "D", "20210501", "20260731", item])
            body = client.get(url)
            if body.text.lstrip().startswith("<"):
                print(f"ECOS {name}: HTML 응답(키 문제)", flush=True)
                return 1
            rows = body.json().get("StatisticSearch", {}).get("row", [])
            out["ecos"][name] = {r["TIME"]: r["DATA_VALUE"] for r in rows}
            print(f"ECOS {name} {len(rows)}행", flush=True)
            _time.sleep(0.5)
        for sid in FRED_SERIES:
            r = client.get(f"{FRED_BASE}/series/observations", params={
                "series_id": sid, "api_key": fred_key, "file_type": "json", "output_type": 4,
                "observation_start": "2021-04-01", "observation_end": "2026-07-31",
                "realtime_start": "2021-05-01", "realtime_end": "9999-12-31"})
            if r.status_code != 200:
                print(f"FRED {sid} HTTP {r.status_code}", flush=True)
                return 1
            obs = r.json().get("observations", [])
            out["fred"][sid] = [(o["date"], o["realtime_start"], o["value"]) for o in obs]
            print(f"FRED {sid} {len(obs)}행", flush=True)
            _time.sleep(0.5)
    (OUT / "macro.json").write_text(json.dumps(out))
    return 0


def fred_known(rows: list, days: list[date]) -> tuple[pd.Series, pd.Series]:
    """결정 d 에 아는 값(최초 공표일 R ≤ d−1)과 그 시점까지 알려진 일변화의 20개 표준편차."""
    frame = pd.DataFrame(rows, columns=["obs", "rel", "v"])
    frame["v"] = pd.to_numeric(frame["v"], errors="coerce")
    frame = frame.dropna().assign(obs=lambda f: pd.to_datetime(f["obs"]).dt.date,
                                  rel=lambda f: pd.to_datetime(f["rel"]).dt.date).sort_values("obs")
    level, vol = {}, {}
    for d in days:
        known = frame[frame["rel"] <= d - timedelta(days=1)]
        if known.empty:
            continue
        level[d] = float(known["v"].iloc[-1])
        tail = known["v"].iloc[-21:]
        if len(tail) == 21:
            vol[d] = float(tail.diff().std())
    return pd.Series(level), pd.Series(vol)


def ext_signals(p: pd.DataFrame, s: pd.DataFrame, days: list[date]) -> tuple[pd.DataFrame, pd.Series]:
    raw = json.loads((OUT / "macro.json").read_text())
    pos = {d: i for i, d in enumerate(days)}

    def ecos(name: str) -> pd.Series:
        ser = pd.Series({datetime.strptime(k, "%Y%m%d").date(): _num(v) for k, v in raw["ecos"][name].items()})
        return ser.sort_index()

    def ecos_prev(name: str) -> pd.Series:
        """결정 d 는 ECOS ≤ d−1 세션만(§3.0)."""
        ser = ecos(name)
        return pd.Series({d: ser[ser.index <= days[pos[d] - 1]].iloc[-1] for d in days
                          if pos[d] > 0 and (ser.index <= days[pos[d] - 1]).any()})

    fred = {sid: fred_known(rows, days) for sid, rows in raw["fred"].items()}
    e = pd.DataFrame(index=pd.Index(days))
    e["Y1"] = (ecos_prev("cd91") - fred["DGS3MO"][0]).diff(20)
    e["Y2"] = fred["T10Y3M"][0].diff(20)
    e["Y3"] = np.log(fred["DTWEXBGS"][0]).diff(20)
    e["Y4"] = fred["BAA10Y"][0].diff(20)
    e["Y5"] = fred["DFII10"][0].diff(20)
    e["Y6"] = fred["DGS10"][1]
    e = e.reindex(days)
    for c in ("X4", "F20"):
        e[c] = s[c]
    fe = ecos("frg_kospi") / ecos("cap_kospi")
    return e, fe.reindex(days)


def run_ext(store: Store) -> int:
    from tools.trial_overlay import metrics
    from tools.v2_hmm_exposure import hmm_path, v6_path

    p = pd.read_pickle(OUT / "panel.pkl")  # invariant-allow: data-access — 이 진단의 연구 캐시
    p = p.join(krx_panel(), how="left")
    days = list(p.index)
    pos = {d: i for i, d in enumerate(days)}
    s = build_signals(p)
    e, fe = ext_signals(p, s, days)
    res: dict = {"b2": {}, "b2_ecos": {}, "corr_F_FE": None, "c2": {}}

    def clip_end(series: pd.Series, h: int, extra: int) -> pd.Series:
        keep = [d for d in series.index if pos[d] + extra + h < len(days) and days[pos[d] + extra + h] <= LAST_DAY]
        return series.reindex(keep)

    win = [d for d in days if SIGNAL_START <= d <= LAST_DAY]
    res["corr_F_FE"] = float(s["f_daily"].reindex(win).corr(fe.reindex(win)))
    for h in (5, 20):
        y = clip_end(fwd_sum(s["f_daily"], h), h, 0)
        ye = clip_end(fwd_sum(fe, h), h, 0)
        for x in EXT:
            res["b2"][f"{x}→F h{h}"] = ic_row(e[x], y, h)
            res["b2"][f"{x}→F h{h} |F20"] = ic_row(e[x], y, h, controls=s[["F20"]])
        for x in [*MACRO, *EXT]:
            sig = s[x] if x in MACRO else e[x]
            res["b2_ecos"][f"{x}→F^E h{h} |F20"] = ic_row(sig, ye, h, controls=s[["F20"]])

    target20 = fwd_sum(s["f_daily"], 20)
    P2 = predicted_inflow(e, target20, days, cols=["X4", *EXT])
    zP2 = expanding_z(P2)
    res["b2"]["P2(표본밖)→F h20"] = ic_row(P2, clip_end(target20, 20, 0), 20)
    z_risk = -pd.concat([expanding_z(e[c]) for c in ("Y3", "Y4", "Y5", "Y6")], axis=1).mean(axis=1, skipna=False)
    k_c4 = stepped((1 + 0.25 * zP2).clip(0.5, 1.0))
    k_d1 = stepped((1 + 0.25 * z_risk).clip(0.5, 1.0))
    a_t2 = stepped((0.5 + 0.25 * zP2).clip(0.0, 1.0))
    nxt = {d: days[pos[d] + 1] for d in days if pos[d] + 1 < len(days)}
    e_days = list(nxt.values())
    v6, hm = v6_path(store, e_days), hmm_path(e_days)
    k_v6 = pd.Series({d: v6.get(x, np.nan) for d, x in nxt.items()})
    k_hmm = pd.Series({d: hm.get(x, np.nan) for d, x in nxt.items()})
    rk, rs = p["r_k200"].shift(-2), p["r_small"].shift(-2)
    eval_days = [d for d in days if date(2022, 6, 28) <= d and d in nxt and pos[d] + 2 < len(days)
                 and days[pos[d] + 2] <= LAST_DAY]
    res["c2"]["eval"] = f"{eval_days[0]} ~ {eval_days[-1]} ({len(eval_days)})"
    res["c2"]["first"] = {"C4": str(k_c4.index.min()), "D1": str(k_d1.index.min())}

    def expo(k: pd.Series) -> tuple[pd.Series, pd.Series]:
        k = k.reindex(eval_days).ffill().fillna(1.0)
        prev = k.shift(1).fillna(k.iloc[0])
        return k * rk.reindex(eval_days) - (k - prev).abs() * ONE_WAY_COST, k

    def tilt(a: pd.Series) -> tuple[pd.Series, pd.Series]:
        a = a.reindex(eval_days).ffill().fillna(0.5)
        prev = a.shift(1).fillna(a.iloc[0])
        return a * rk.reindex(eval_days) + (1 - a) * rs.reindex(eval_days) - 2 * (a - prev).abs() * ONE_WAY_COST, a

    def both(a: pd.Series, b: pd.Series) -> pd.Series:
        return pd.concat([a, b.reindex(a.index).ffill()], axis=1).min(axis=1)

    variants = {"N0": expo(pd.Series(1.0, index=eval_days)), "V6": expo(k_v6), "HMM": expo(k_hmm),
                "C4": expo(k_c4), "C5": expo(both(k_hmm, k_c4)), "D1": expo(k_d1), "D2": expo(both(k_hmm, k_d1)),
                "T0": tilt(pd.Series(0.5, index=eval_days)), "T2": tilt(a_t2)}
    bench = rk.reindex(eval_days)
    for name, (r, k) in variants.items():
        row = {}
        for pname, (lo, hi) in {"전체": (date(2000, 1, 1), LAST_DAY), **PERIODS}.items():
            m = [d for d in eval_days if lo <= d <= hi]
            met = metrics(r.reindex(m), bench.reindex(m))
            met["avg_k"] = float(k.reindex(m).mean())
            met["switches"] = int((k.reindex(m).diff().abs() > 1e-9).sum())
            row[pname] = met
        res["c2"][name] = row
    pd.DataFrame({"P2": P2, "zP2": zP2, "z_risk": z_risk, "k_c4": k_c4, "k_d1": k_d1, "a_t2": a_t2}).to_pickle(OUT / "paths-ext.pkl")
    (OUT / "result-ext.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    report({"a": {}, "b": {**res["b2"], **res["b2_ecos"]}, "lag": None, "c": res["c2"]}, ext=True)
    print(f"창고 F 와 ECOS F^E 일별 상관 {res['corr_F_FE']:+.3f} · (c′) {res['c2']['eval']} · 첫 정의 {res['c2']['first']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--panel", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--fetch-macro", action="store_true", help="§3 ECOS·FRED → 연구 캐시")
    parser.add_argument("--run-ext", action="store_true", help="§3 (b′)(c′)")
    args = parser.parse_args(argv)
    store = Store(root=Path("data"))
    if args.fetch:
        return fetch(store)
    if args.panel:
        return panel(store)
    if args.run:
        return run(store)
    if args.fetch_macro:
        return fetch_macro()
    if args.run_ext:
        return run_ext(store)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
