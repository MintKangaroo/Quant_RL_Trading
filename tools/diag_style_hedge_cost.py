"""스타일 맞춤 헤지 · 비용 절반 진단 — docs/diag/style-hedge-and-cost.md §1 대로 한 번 잰다.

    nice -n 10 taskset -c 0-3 .venv/bin/python tools/diag_style_hedge_cost.py          # 계산
    .venv/bin/python tools/diag_style_hedge_cost.py report                            # 표

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/style-hedge/` 에 둔다. 새 학습은 없다: 예측·롱 다리·헤지 함수는 앞선 진단
(`tools/diag_hedged_sleeve.py`)을 그대로 부르고, 여기서 더하는 것은 지수 여럿의 헤지(단일 β·바스켓 NNLS)와
롱 다리의 완충 배수·비용 인지 규칙뿐이다(검산 1~3 이 앞선 진단과의 일치를 확인한다).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import nnls  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools import final_round_kit as fkit  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools.diag_bear_2022 import exposure_scales, in_window, realized_end  # noqa: E402
from tools.diag_hedged_sleeve import (  # noqa: E402
    BETA_MIN,
    BETA_WIN,
    KR_LABELS,
    PRICE_START,
    WINDOWS,
    available_gb,
    hedge_ratio,
    ic_series,
    kr_preds,
    labels,
    market_closed,
    mdd,
)
from tools.trial_overlay import (  # noqa: E402
    ANN,
    HEDGE_CARRY,
    HEDGE_TRADE_COST,
    ONE_WAY_COST,
    metrics,
)
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

OUT = Path("data/_diag/style-hedge")
PRIOR = Path("data/_diag/hedged-sleeve")

INDICES = {"K": "KR:IDX:KOSPI200", "Q": "KR:IDX:KOSDAQ150", "M": "KR:IDX:KRX 중형 TMI", "S": "KR:IDX:KRX 소형 TMI"}
#: 꼬리표 → (수단, 방식, 실행 가능). 수단 "U" 는 유니버스 EW 자체(이론 상한, 비용 0).
HEDGES = {
    "HU": (("U",), "single", False),
    "HK": (("K",), "single", True),
    "HQ": (("Q",), "single", True),
    "HM": (("M",), "single", False),
    "HS": (("S",), "single", False),
    "B2": (("K", "Q"), "nnls", True),
    "B4": (("K", "Q", "M", "S"), "nnls", False),
}
EXECUTABLE = [k for k, v in HEDGES.items() if v[2]]
CROSS_HEDGES = ("HU", *EXECUTABLE)

NS = (24, 50, 100)
EVERY = (10, 20)
MULTS = (3, 5)
THETAS = (None, 0.5, 1.0)
CORES = (0.7, 0.9)
CARRY, CARRY_HI, C_H = HEDGE_CARRY, 0.02, HEDGE_TRADE_COST
BASKET_CAP = 1.5
SEEDS = fkit.SEEDS
if os.environ.get("STYLE_SMOKE"):            # 배선 확인용 — 시드 0 · N 24 만, 원본은 smoke/ 아래
    SEEDS, NS, OUT = (0,), (24,), OUT / "smoke"
MIN_AVAILABLE_GB, MAX_RSS_MB = 6.0, 4096.0


def guard(stage: str) -> None:
    rss = fkit.rss_mb()
    print(f"[{stage}] 최대 RSS {rss:.0f}MB · 가용 {available_gb():.1f}GB", flush=True)
    if rss > MAX_RSS_MB:
        print(f"RSS {rss:.0f}MB > {MAX_RSS_MB:.0f}MB — 멈춘다", flush=True)
        raise SystemExit(8)


def tag(n: int, every: int, mult: int, theta: float | None) -> str:
    return f"N{n}-R{every}-K{mult}-" + ("CA0" if theta is None else f"CA{theta:g}")


# --------------------------------------------------------------------------- 지수


def index_returns(store: Store, sessions: list[date]) -> pd.DataFrame:
    """`trial_overlay._index` 와 같은 읽기를 지수 여럿에 — 결정 t 의 값 = 종가 t+1 → t+2(kit 벤치 정렬)."""
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 40
    frame = store.get("indices", as_of=now, lookback=span, market="KR", entity=list(INDICES.values()),
                      columns=["entity_id", "valid_from", "close"])
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.date
    out = {}
    for key, eid in INDICES.items():
        closes = frame[frame["entity_id"] == eid].groupby("day")["close"].last().astype(float).sort_index()
        out[key] = closes.shift(-2) / closes.shift(-1) - 1.0
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- 롱 다리


def cost_aware(held: list[str], target: list[str], row: pd.Series, n: int, theta: float) -> list[str]:
    """문서 §1 ② CA(θ): 강제 퇴출 → 빈 자리는 E 상위로 → 나머지 D·E 를 짝지어 z 차이 ≥ θ 인 짝만 교체."""
    z = (row - row.mean()) / row.std()
    tset, hset = set(target), set(held)
    common = [e for e in held if e in tset]
    rest = sorted([e for e in held if e not in tset and e in z.index], key=lambda e: z[e])
    entrants = sorted([e for e in target if e not in hset], key=lambda e: -z[e])
    free = max(0, n - len(common) - len(rest))
    fill, pool = entrants[:free], entrants[free:]
    swapped = 0
    for d, e in zip(rest, pool, strict=False):
        if z[e] - z[d] < theta:
            break
        swapped += 1
    return common + rest[swapped:] + fill + pool[:swapped]


def long_leg(wide: pd.DataFrame, ret: pd.DataFrame, trad: dict[date, set[str]], n: int, *, every: int, mult: int,
             theta: float | None) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """`diag_hedged_sleeve.long_leg`(EW) 루프 그대로에 완충 배수 `mult` 와 비용 인지 `theta` 만 더했다(검산 3)."""
    held: list[str] = []
    prev = None
    out, turns, rebal = {}, {}, {}
    step = -1
    for day in wide.index:
        if day not in ret.index:
            continue
        step += 1
        if every > 1 and prev is not None and step % every != 0:
            w = prev
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            out[day] = float((w * dr).sum())
            turns[day] = 0.0
            drift = w * (1 + dr)
            prev = drift / drift.sum() if drift.sum() > 0 else w
            continue
        row = wide.loc[day].dropna()
        ok = trad.get(day)
        if ok:
            row = row[row.index.isin(ok)]
        if row.empty:
            continue
        target = pick_mult(held, row.sort_values(ascending=False).index, n, mult)
        held = cost_aware(held, target, row, n, theta) if (theta is not None and held) else target
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns[day] = t
        z = (row - row.mean()) / row.std()
        active = w.reindex(row.index).fillna(0.0) - 1.0 / len(row)
        rebal[day] = {"U": len(row), "tc": float(np.corrcoef(active, z)[0, 1])}
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), pd.Series(turns).sort_index(), pd.DataFrame(rebal).T


# --------------------------------------------------------------------------- 헤지


def nnls_ratios(rl: pd.Series, x: pd.DataFrame) -> pd.DataFrame:
    """결정 t 에 인덱스 ≤ t−1 의 직전 60일(최소 20)로 rl 을 x 에 NNLS(열 평균 제거 = 절편). 합 > 1.5 면 비례 축소.
    20일 전에는 K200 1.0·나머지 0."""
    y = rl.to_numpy()
    X = x.reindex(rl.index).to_numpy()
    out = np.zeros_like(X)
    first = list(x.columns).index("K") if "K" in x.columns else 0
    for i in range(len(y)):
        lo = max(0, i - BETA_WIN)
        yy, XX = y[lo:i], X[lo:i]
        ok = np.isfinite(yy) & np.isfinite(XX).all(axis=1)
        if ok.sum() < BETA_MIN:
            out[i, first] = 1.0
            continue
        yy, XX = yy[ok] - yy[ok].mean(), XX[ok] - XX[ok].mean(axis=0)
        coef, _ = nnls(XX, yy)
        s = coef.sum()
        out[i] = coef * (BASKET_CAP / s) if s > BASKET_CAP else coef
    return pd.DataFrame(out, index=rl.index, columns=x.columns)


def ratios(rl: pd.Series, x: pd.DataFrame, hedge_key: str) -> pd.DataFrame:
    inst, how, _ = HEDGES[hedge_key]
    if how == "single":
        k = inst[0]
        return pd.DataFrame({k: hedge_ratio(rl, x[k], "ROLL")})
    return nnls_ratios(rl, x[list(inst)])


def hedge_multi(rl: pd.Series, x: pd.DataFrame, target: pd.DataFrame, *, carry: float, c_h: float,
                reset_days: set[date] | None = None) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    """`diag_hedged_sleeve.hedge` 를 수단 여럿으로 — 수단마다 h̃_k = h_k·(1 − x_k)/(1 + r_L), 거래는 Σ|h − h̃|."""
    cols = list(target.columns)
    xx = x[cols].reindex(rl.index).fillna(0.0).to_numpy()
    tg = target.to_numpy()
    r_l = rl.fillna(0.0).to_numpy()
    k = len(cols)
    h_prev, x_prev, r_prev = np.zeros(k), np.zeros(k), 0.0
    rs, hs, trades = np.empty(len(r_l)), np.empty((len(r_l), k)), np.empty(len(r_l))
    for i, day in enumerate(rl.index):
        tilde = np.zeros(k) if i == 0 else h_prev * (1 - x_prev) / (1 + r_prev)
        h = tg[i] if (i == 0 or reset_days is None or day in reset_days) else tilde
        trade = float(np.abs(h - tilde).sum())
        rs[i] = r_l[i] - float(h @ xx[i]) - carry * float(h.sum()) / ANN - c_h * trade
        hs[i], trades[i] = h, trade
        h_prev, x_prev, r_prev = h, xx[i], r_l[i]
    return pd.Series(rs, index=rl.index), pd.DataFrame(hs, index=rl.index, columns=cols), pd.Series(trades, index=rl.index)


def run_hedge(rl: pd.Series, x: pd.DataFrame, key: str, *, carry: float = CARRY,
              reset_days: set[date] | None = None) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    cost = HEDGES[key][2] or key != "HU"
    target = ratios(rl, x, key)
    return hedge_multi(rl, x, target, carry=carry if cost else 0.0, c_h=C_H if cost else 0.0, reset_days=reset_days)


# --------------------------------------------------------------------------- 지표


def _ir(r: pd.Series, days: list[date]) -> float:
    v = r.reindex(days).fillna(0.0)
    return float(v.mean() / v.std() * np.sqrt(ANN)) if v.std() > 0 else float("nan")


def _beta(r: pd.Series, f: pd.Series, days: list[date]) -> float:
    a, b = r.reindex(days).fillna(0.0), f.reindex(days).fillna(0.0)
    return float(np.cov(a, b)[0, 1] / b.var()) if b.var() > 0 else float("nan")


def hedge_stats(rs: pd.Series, rl: pd.Series, hret: pd.Series, h: pd.DataFrame, htrade: pd.Series,
                b: pd.Series, uni: pd.Series, days: list[date], *, carry: float) -> dict:
    r = rs.reindex(days).fillna(0.0)
    ann, vol = float(r.mean() * ANN), float(r.std() * np.sqrt(ANN))
    n = len(days)
    turn_h = float(htrade.reindex(days).fillna(0.0).sum() * ANN / n)
    hbar = h.reindex(days).mean()
    return {
        "n": n, "ann": ann, "vol": vol, "ir": ann / vol if vol > 0 else float("nan"),
        "nw4": float(ic_module.newey_west_t(r, lag=4)), "mdd": mdd(r),
        "track": float(np.corrcoef(rl.reindex(days).fillna(0.0), hret.reindex(days).fillna(0.0))[0, 1]),
        "beta_k": _beta(rs, b, days), "beta_u": _beta(rs, uni, days),
        "style_ann": float((uni - hret).reindex(days).fillna(0.0).mean() * ANN),
        "h": {k: float(v) for k, v in hbar.items()}, "turn_hedge": turn_h,
        "hedge_cost": turn_h * C_H + carry * float(hbar.sum()),
    }


def sel_stats(rl: pd.Series, turn: pd.Series, uni: pd.Series, days: list[date]) -> dict:
    sel = rl - uni.reindex(rl.index)
    gross = sel + turn.reindex(rl.index).fillna(0.0) * ONE_WAY_COST
    s, g = sel.reindex(days).fillna(0.0), gross.reindex(days).fillna(0.0)
    n = len(days)
    t = float(turn.reindex(days).fillna(0.0).sum() * ANN / n)
    return {"n": n, "sel_ann": float(s.mean() * ANN), "gross_ann": float(g.mean() * ANN),
            "sel_ir": _ir(sel, days), "gross_ir": _ir(gross, days), "nw4": float(ic_module.newey_west_t(s, lag=4)),
            "long_ann": float(rl.reindex(days).fillna(0.0).mean() * ANN), "turn": t, "cost": t * ONE_WAY_COST,
            "sel_mdd": mdd(s)}


# --------------------------------------------------------------------------- 본 측정


def run(store: Store, now: datetime) -> dict:
    sessions = sorted(set(kr_preds(0)["session"]))
    ret, bench, trad = rkit.market_data(store, [PRICE_START, *sessions])
    calendar = list(ret.index)
    xi = index_returns(store, [PRICE_START, *sessions])
    guard("시장 자료")
    y = labels(KR_LABELS, "KR")

    combos = [(n, e, m, t) for n in NS for e in EVERY for m in MULTS for t in THETAS]
    legs: dict[str, dict[int, pd.Series]] = {}
    turns: dict[str, dict[int, pd.Series]] = {}
    rebs: dict[str, list[pd.DataFrame]] = {}
    ics = {}
    for s in SEEDS:
        pred = kr_preds(s)
        ics[s] = ic_series(pred, y)
        wide = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=rkit.SPAN).mean()
        del pred
        for n, e, m, t in combos:
            key = tag(n, e, m, t)
            daily, turn, rb = long_leg(wide, ret, trad, n, every=e, mult=m, theta=t)
            legs.setdefault(key, {})[s] = daily
            turns.setdefault(key, {})[s] = turn
            rebs.setdefault(key, []).append(rb)
            print(f"  seed{s} {key}: 연 {daily.mean() * ANN:+.1%} · 회전 {turn.mean() * ANN:.1f}", flush=True)
        del wide
        guard(f"seed{s}")
    del y

    base = tag(NS[0], 10, 3, None)
    days = list(legs[base][SEEDS[0]].index)
    spans = realized_end(days, calendar)
    wdays = {k: in_window(pd.Index(days), spans, v) for k, v in WINDOWS.items()}
    b = bench.reindex(days)
    x = xi.reindex(days)
    uni = pd.Series({d: float(ret.loc[d].reindex(list(trad.get(d, ()))).mean()) for d in days if d in ret.index}).reindex(days)
    x["U"] = uni

    prior = pd.read_pickle(PRIOR / "daily-KR.pkl")  # invariant-allow: data-access — 앞선 진단 원본
    prior_res = json.loads((PRIOR / "results-KR.json").read_text())
    res: dict = {"check": {}, "one": {}, "one_seed_ir": {}, "one_sens": {}, "two": {}, "two_seed": {}, "grinold": {},
                 "cross": {}, "three": {}}
    res["check"]["check1_k200"] = float((xi["K"].reindex(days) - b).abs().max())

    rl_avg = {k: pd.DataFrame(v).reindex(days).mean(axis=1) for k, v in legs.items()}
    turn_avg = {k: pd.DataFrame(v).reindex(days).fillna(0.0).mean(axis=1) for k, v in turns.items()}
    res["check"]["check3_leg"] = float((rl_avg[base] - prior["L-24-EW"].reindex(days)).abs().max())

    # ① 스타일 맞춤 헤지 — 기본 롱 다리(R10·3N·끔)
    daily_out: dict[str, pd.Series] = {"b": b, "U": uni, **{f"x-{k}": x[k] for k in INDICES}}
    for n in NS:
        key = tag(n, 10, 3, None)
        rl = rl_avg[key]
        daily_out[f"L-{key}"] = rl
        rebal_days = set(pd.concat(rebs[key]).index)
        for hk in HEDGES:
            rs, h, ht = run_hedge(rl, x, hk)
            hret = (h * x[h.columns].fillna(0.0)).sum(axis=1)
            carry = 0.0 if hk == "HU" else CARRY
            label = f"{n}-{hk}"
            daily_out[f"S-{label}"] = rs
            res["one"][label] = {w: hedge_stats(rs, rl, hret, h, ht, b, uni, wd, carry=carry) for w, wd in wdays.items()}
            seed_ir = []
            for leg in legs[key].values():
                r_s, _, _ = run_hedge(leg.reindex(days).fillna(0.0), x, hk)
                seed_ir.append(_ir(r_s, wdays["전체"]))
            res["one_seed_ir"][label] = [min(seed_ir), max(seed_ir)]
            if hk != "HU":
                r_hi, _, _ = run_hedge(rl, x, hk, carry=CARRY_HI)
                r_10, _, ht10 = run_hedge(rl, x, hk, reset_days=rebal_days)
                res["one_sens"][label] = {"carry2": {w: _ir(r_hi, wd) for w, wd in wdays.items()},
                                          "H10": {w: _ir(r_10, wd) for w, wd in wdays.items()},
                                          "H10_turn": float(ht10.reindex(wdays["전체"]).sum() * ANN / len(wdays["전체"]))}
        print(f"  ① N{n} 끝", flush=True)
    res["check"]["check2_hk24"] = {"now": res["one"][f"{NS[0]}-HK"]["전체"]["ir"],
                                   "prior": prior_res["A"]["24-EW-ROLL"]["전체"]["ir"]}
    guard("①")

    # ② 비용 절반 — 롱 다리만
    ic_mean = pd.DataFrame(ics).mean(axis=1)
    res["ic"] = {w: float(ic_mean.reindex(wd).mean()) for w, wd in wdays.items()}
    for key in legs:
        rl, turn = rl_avg[key], turn_avg[key]
        res["two"][key] = {w: sel_stats(rl, turn, uni, wd) for w, wd in wdays.items()}
        sir = [sel_stats(leg.reindex(days), turns[key][s].reindex(days).fillna(0.0), uni, wdays["전체"])["sel_ir"]
               for s, leg in legs[key].items()]
        res["two_seed"][key] = [min(sir), max(sir)]
        rb = pd.concat(rebs[key])
        every = int(key.split("-R")[1].split("-")[0])
        res["grinold"][key] = {"tc": float(rb["tc"].mean()), "U": float(rb["U"].mean()), "every": every}
        daily_out[f"L-{key}"] = rl
        daily_out[f"T-{key}"] = turn
    guard("②")

    # ①×② 교차 — 36 롱 다리 × {HU, 실행 가능 헤지}
    for key in legs:
        for hk in CROSS_HEDGES:
            rs, _, _ = run_hedge(rl_avg[key], x, hk)
            res["cross"][f"{key}|{hk}"] = {w: _ir(rs, wd) for w, wd in wdays.items()}
            res["cross"][f"{key}|{hk}"]["ann"] = float(rs.reindex(wdays["전체"]).mean() * ANN)
            res["cross"][f"{key}|{hk}"]["mdd"] = mdd(rs.reindex(wdays["전체"]).fillna(0.0))
    guard("교차")

    # ③ 합치기 — 최선 실행 가능 슬리브(표본 안 선택) · GROSS
    best = max((k for k in res["cross"] if k.split("|")[1] in EXECUTABLE), key=lambda k: res["cross"][k]["전체"])
    leg_key, hk = best.split("|")
    scale, _ = exposure_scales(store, days, now)
    scale = scale.reindex(days).fillna(1.0)
    dist = float(store.config("benchmark.kodex200_distribution_yield_annual", as_of=now))
    d2 = b + dist / ANN
    arms = {"I100": prior["B-I100"].reindex(days), "IV6": prior["B-IV6"].reindex(days), "CUR": prior["B-CUR"].reindex(days)}
    for lab, hkey in (("BEST", hk), ("CEIL", "HU")):
        rs, h, _ = run_hedge(rl_avg[leg_key], x, hkey)
        hsum = h.sum(axis=1)
        daily_out[f"S3-{lab}"] = rs
        for w in CORES:
            lo = (1 - w) / (1 + hsum)
            arms[f"{lab}-w{w}"] = lo * rs + scale * w * d2 - C_H * (scale * w).diff().fillna(0.0).abs()
    res["three"]["best"] = best
    res["check"]["iv6_vs_prior_d2"] = float((d2 - prior["D2"].reindex(days)).abs().max())
    for name, r in arms.items():
        out = {}
        for w, wd in wdays.items():
            rr, bb = r.reindex(wd).fillna(0.0), d2.reindex(wd).fillna(0.0)
            m = metrics(rr, bb)
            out[w] = {"ex_cum": float((1 + rr).prod() - (1 + bb).prod()), "ex_ann": float((rr - bb).mean() * ANN),
                      "ir": m["ir"], "mdd": m["mdd"], "beta": m["beta"]}
        res["three"][name] = out
        daily_out[f"B-{name}"] = r
    pd.DataFrame(daily_out).to_pickle(OUT / "daily.pkl")  # invariant-allow: data-access — 진단 원본
    return res


# --------------------------------------------------------------------------- 보고 표(문서 §2 로 옮긴다)


def _f(v, fmt: str) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:+.1%}" if fmt == "%" else (f"{v:.0%}" if fmt == "p" else f"{v:+.2f}" if fmt == "s" else f"{v:.2f}")


def report() -> int:
    r = json.loads((OUT / "results.json").read_text())
    regimes = list(WINDOWS)
    print("### ① 스타일 맞춤 헤지 — 기본 롱 다리(R10·3N·EW), 매일 재조정 · carry 1%\n")
    print("| N · 헤지 | 실행 | " + " | ".join(f"IR {w}" for w in regimes)
          + " | 연 | 변동성 | NW t | MDD | 추적 상관 | 잔여 β K200 / U | 스타일 잔여 연 | h̄ | 헤지 회전·비용 | 시드 IR |")
    print("|---|---|" + "---|" * (len(regimes) + 10))
    for lab, by in r["one"].items():
        a = by["전체"]
        hk = lab.split("-")[1]
        lo, hi = r["one_seed_ir"][lab]
        hb = " ".join(f"{k}{v:.2f}" for k, v in a["h"].items())
        print(f"| {lab} | {'O' if HEDGES[hk][2] else '참고'} | " + " | ".join(_f(by[w]["ir"], "s") for w in regimes)
              + f" | {_f(a['ann'], '%')} | {a['vol']:.1%} | {a['nw4']:+.1f} | {_f(a['mdd'], '%')} | {a['track']:.2f} | "
              f"{a['beta_k']:+.2f} / {a['beta_u']:+.2f} | {_f(a['style_ann'], '%')} | {hb} | {a['turn_hedge']:.1f} · {a['hedge_cost']:.1%} | "
              f"{lo:+.2f}~{hi:+.2f} |")
    print("\n#### ① 국면별 스타일 잔여(연) — 선택 항은 헤지와 무관\n")
    print("| N · 헤지 | " + " | ".join(regimes) + " |")
    print("|---|" + "---|" * len(regimes))
    for lab, by in r["one"].items():
        print(f"| {lab} | " + " | ".join(_f(by[w]["style_ann"], "%") for w in regimes) + " |")
    print("\n#### ① 민감도 — carry 2% · H10(재조정일에만)\n")
    print("| N · 헤지 | IR 기본 | IR carry2 | IR H10 " + " / ".join(regimes) + " | H10 헤지 회전 |")
    print("|---|---|---|---|---|")
    for lab, s in r["one_sens"].items():
        print(f"| {lab} | {_f(r['one'][lab]['전체']['ir'], 's')} | {_f(s['carry2']['전체'], 's')} | "
              + " / ".join(_f(s["H10"][w], "s") for w in regimes) + f" | {s['H10_turn']:.1f} |")

    print("\n### ② 비용 절반 — 선택 알파(상위 N − 유니버스 EW)\n")
    print("| 조합 | 회전(연 편도) | 비용(연) | 선택 연 전 / 후 | IR 전 | IR 후 | 유지율 | NW t | "
          + " | ".join(f"IR후 {w}" for w in regimes[:-1]) + " | 시드 IR 후 |")
    print("|---|" + "---|" * (8 + len(regimes)))
    for key, by in r["two"].items():
        a = by["전체"]
        n = key.split("-")[0]
        base = r["two"][f"{n}-R10-K3-CA0"]["전체"]["gross_ir"]
        lo, hi = r["two_seed"][key]
        print(f"| {key} | {a['turn']:.1f} | {a['cost']:.1%} | {_f(a['gross_ann'], '%')} / {_f(a['sel_ann'], '%')} | "
              f"{_f(a['gross_ir'], 's')} | {_f(a['sel_ir'], 's')} | {a['gross_ir'] / base:.0%} | {a['nw4']:+.1f} | "
              + " | ".join(_f(by[w]["sel_ir"], "s") for w in regimes[:-1]) + f" | {lo:+.2f}~{hi:+.2f} |")

    ic = r["ic"]["전체"]
    print(f"\n#### ② Grinold — IC(h5) {ic:+.4f} · " + " · ".join(f"{w} {r['ic'][w]:+.3f}" for w in regimes[:-1]) + "\n")
    print("| 조합 | U | BR = U·252/R | TC | 이론 TC·IC·√BR | 실측 IR 비용 전 | 비용 후 | 비용 손실 | 실측/이론 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for key, g in r["grinold"].items():
        br = g["U"] * ANN / g["every"]
        theory = g["tc"] * ic * np.sqrt(br)
        a = r["two"][key]["전체"]
        print(f"| {key} | {g['U']:.0f} | {br:,.0f} | {g['tc']:.3f} | {theory:.2f} | {_f(a['gross_ir'], 's')} | "
              f"{_f(a['sel_ir'], 's')} | {a['gross_ir'] - a['sel_ir']:.2f} | {a['gross_ir'] / theory:.0%} |")

    print("\n### ①×② 교차 — 슬리브 IR(전체), N 마다 실행 가능 상위 5 + 기본 + 상한 HU\n")
    print("| 조합 · 헤지 | " + " | ".join(f"IR {w}" for w in regimes) + " | 연 | MDD |")
    print("|---|" + "---|" * (len(regimes) + 2))
    cross = r["cross"]
    for n in sorted({k.split("-")[0] for k in cross}, key=lambda s: int(s[1:])):
        ex = sorted([k for k in cross if k.startswith(n + "-") and k.split("|")[1] in EXECUTABLE],
                    key=lambda k: -cross[k]["전체"])
        show = ex[:5] + [k for k in (f"{n}-R10-K3-CA0|HK", f"{n}-R10-K3-CA0|HQ") if k not in ex[:5]]
        hu = max((k for k in cross if k.startswith(n + "-") and k.endswith("|HU")), key=lambda k: cross[k]["전체"])
        for k in [*show, hu]:
            print(f"| {k} | " + " | ".join(_f(cross[k][w], "s") for w in regimes)
                  + f" | {_f(cross[k]['ann'], '%')} | {_f(cross[k]['mdd'], '%')} |")

    print(f"\n### ③ 합치기(참고) — 최선 실행 가능 슬리브 `{r['three']['best']}`(표본 안 선택) · GROSS · 벤치 D2\n")
    print("| 팔 | " + " | ".join(f"초과 {w}" for w in regimes) + " | 연 초과 | IR | MDD | β |")
    print("|---|" + "---|" * (len(regimes) + 4))
    for name, by in r["three"].items():
        if name == "best":
            continue
        a = by["전체"]
        print(f"| {name} | " + " | ".join(_f(by[w]["ex_cum"], "%") for w in regimes)
              + f" | {_f(a['ex_ann'], '%')} | {_f(a['ir'], 's')} | {_f(a['mdd'], '%')} | {a['beta']:.2f} |")
    print("\n검산:", json.dumps(r["check"], ensure_ascii=False))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        return report()
    now = datetime.now(UTC)  # invariant-allow: wallclock — 실행 관문과 "오늘의 설정"(앞선 진단과 같다)
    if not market_closed(now):
        print("장 중이다 — 돌리지 않는다(rc 9)", flush=True)
        return 9
    if available_gb() < MIN_AVAILABLE_GB:
        print(f"가용 메모리 {available_gb():.1f}GB < {MIN_AVAILABLE_GB}GB — 기다린다(rc 7)", flush=True)
        return 7
    OUT.mkdir(parents=True, exist_ok=True)
    store = Store(root=Path("data"))
    res = run(store, now)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(f"끝 · 최대 RSS {fkit.rss_mb():.0f}MB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
