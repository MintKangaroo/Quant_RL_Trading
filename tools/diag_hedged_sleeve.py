"""지수 코어 + 시장 헤지 알파 슬리브 진단 — docs/diag/hedged-alpha-sleeve.md §1 대로 한 번 잰다.

    nice -n 10 taskset -c 0-3 .venv/bin/python tools/diag_hedged_sleeve.py

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/hedged-sleeve/` 에 둔다. 새 학습은 없다: 예측은 하락장 검진(`data/_diag/bear-2022`)과
회차 대조군 C0(`data/_diag/final-round`) 캐시를 2023-02-15 에서 잇는다(문서 §1).

규칙은 베끼지 않는다: 수익·벤치·명단은 `trial_ranker_kit.market_data`, 동일가중 롱 다리는 `trial_ranker_kit.portfolio`
와 같은 루프(검산 2 가 일치를 확인한다), 국면 배수는 `diag_bear_2022.exposure_scales`, 현행 전략은 `apply_exposure`,
구간은 `realized_end`·`in_window`, 헤지 비용은 시행 AE 의 `trial_overlay.HEDGE_CARRY`·`HEDGE_TRADE_COST`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools import final_round_kit as fkit  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools import trial_us_kit as ukit  # noqa: E402
from tools.diag_bear_2022 import apply_exposure, exposure_scales, in_window, realized_end  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, HEDGE_CARRY, HEDGE_TRADE_COST, ONE_WAY_COST, metrics  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

OUT = Path("data/_diag/hedged-sleeve")
BEAR_PRED = Path("data/_diag/bear-2022")
C0_TAG = "KR+US-20220701-20260630"
KR_LABELS = fkit.FA2021_DIR / "panel" / "panel-KR-KR+US-20211110-20260630.parquet"  # invariant-allow: data-access — 연구 패널 캐시(라벨만)
US_PANEL = fkit.CACHE / f"panel-US-{C0_TAG}.parquet"  # invariant-allow: data-access — 연구 패널 캐시
#: 회차 C0 의 첫 예측 세션 — 이 날부터 C0, 그 앞은 하락장 검진 예측(문서 §1).
SPLICE = date(2023, 2, 15)
#: market_data 의 가격 창 시작(하락장 검진 패널의 첫 날). σ₆₀ 워밍업이 2021-12-29 전에 찬다.
PRICE_START = date(2021, 11, 10)

NS = (24, 50, 100)
WEIGHTS = ("EW", "IV")
BETAS = ("ROLL", "FIX")
CORES = (0.5, 0.7, 0.9)
CARRY, CARRY_HI, C_H = HEDGE_CARRY, 0.02, HEDGE_TRADE_COST
MARGIN = 0.15
BETA_WIN, BETA_MIN, BETA_CLIP = 60, 20, (0.0, 1.5)
VOL_WIN, VOL_MIN = 60, 20
SEEDS = fkit.SEEDS
if os.environ.get("HEDGED_SMOKE"):           # 배선 확인용 — 시드 0 · N 24 만, 원본은 smoke/ 아래
    SEEDS, NS, OUT = (0,), (24,), OUT / "smoke"
REBAL = fkit.REBALANCE_EVERY

WINDOWS = {
    "하락": (date(2022, 1, 3), date(2022, 9, 30)),
    "반등": (date(2022, 9, 30), date(2023, 1, 31)),
    "박스": (date(2023, 1, 31), date(2024, 12, 31)),
    "급등": (date(2024, 12, 31), date(2026, 6, 30)),
    "전체": (date(2022, 1, 3), date(2026, 6, 30)),
}
US_WINDOWS = {"박스": (date(2023, 2, 15), date(2024, 12, 31)), "급등": (date(2024, 12, 31), date(2026, 6, 30)),
              "전체": (date(2023, 2, 15), date(2026, 6, 30))}
KST = timezone(timedelta(hours=9))
MIN_AVAILABLE_GB, MAX_RSS_MB = 6.0, 4096.0


# --------------------------------------------------------------------------- 자원 관문


def available_gb() -> float:
    with open("/proc/meminfo") as handle:  # invariant-allow: data-access — /proc, 창고 아님
        for line in handle:
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024 / 1024
    return float("nan")


def guard(stage: str) -> None:
    """RSS 4GB 를 넘으면 멈춘다(rc 8). 문서 §1 실행 조건."""
    rss = fkit.rss_mb()
    print(f"[{stage}] 최대 RSS {rss:.0f}MB · 가용 {available_gb():.1f}GB", flush=True)
    if rss > MAX_RSS_MB:
        print(f"RSS {rss:.0f}MB > {MAX_RSS_MB:.0f}MB — 멈춘다", flush=True)
        raise SystemExit(8)


def market_closed(now: datetime) -> bool:
    k = now.astimezone(KST)
    return k.weekday() >= 5 or not (time(8, 30) <= k.time() <= time(15, 45))


# --------------------------------------------------------------------------- 예측


def kr_preds(seed: int) -> pd.DataFrame:
    """하락장 검진 예측(~2023-02-14) + 회차 C0 국장 행(2023-02-15~) — 문서 §1 의 이음."""
    bear = pd.read_pickle(BEAR_PRED / f"pred-seed{seed}.pkl")  # invariant-allow: data-access — 진단 캐시
    c0 = pd.read_pickle(fkit.control_path("C0", seed, C0_TAG))  # invariant-allow: data-access — 대조군 캐시
    c0 = c0[c0["market"] == "KR"]
    out = pd.concat([bear[bear["session"] < SPLICE], c0[c0["session"] >= SPLICE]], ignore_index=True)
    return out[["entity_id", "session", "pred"]]


def labels(path: Path, market: str) -> pd.DataFrame:
    y = pd.read_parquet(path, columns=["entity_id", "session", "market", "y5"])  # invariant-allow: data-access — 연구 패널 캐시
    y = y[y["market"] == market].drop(columns="market")
    y["session"] = pd.to_datetime(y["session"]).dt.date
    return y.dropna()


def ic_series(pred: pd.DataFrame, y: pd.DataFrame) -> pd.Series:
    merged = pred.merge(y, on=["entity_id", "session"]).rename(columns={"pred": "score", "y5": "target"})
    ic = ic_module.daily_ic(merged[["session", "score", "target"]])
    ic.index = pd.to_datetime(pd.Series(ic.index)).dt.date.values
    return ic


# --------------------------------------------------------------------------- 롱 다리


Weigh = Callable[[date, list[str]], pd.Series]


def equal(_day: date, held: list[str]) -> pd.Series:
    return pd.Series(1.0 / len(held), index=held)


def inverse_vol(sigma: pd.DataFrame) -> Weigh:
    """비중 ∝ 1/σ₆₀ — σ 는 결정 t 에 이미 실현된 일수익(정렬상 t−2 까지). 모르면 그날 보유 σ 중앙값."""
    def weigh(day: date, held: list[str]) -> pd.Series:
        s = sigma.loc[day].reindex(held) if day in sigma.index else pd.Series(np.nan, index=held)
        s = s.where(s > 0)
        if s.notna().sum() == 0:
            return equal(day, held)
        inv = 1.0 / s.fillna(s.median())
        return inv / inv.sum()
    return weigh


def long_leg(score: pd.DataFrame, ret: pd.DataFrame, trad: dict[date, set[str]], n: int, weigh: Weigh,
             *, every: int = REBAL) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """`trial_ranker_kit.portfolio` 의 루프 그대로(EMA5·완충 3N·every 세션 재조정·드리프트·비용 0.41%), 비중만 `weigh`.

    반환: 일수익(비용 후) · 일 회전 · 재조정일 기록(U·TC). TC = 활성 비중(w − 1/U)과 그날 EMA 점수 단면 z 의 상관(문서 §1 C).
    """
    wide = score.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=rkit.SPAN).mean()
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
        held = pick_mult(held, row.sort_values(ascending=False).index, n, rkit.EXIT_MULT)
        w = weigh(day, held)
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


def hedge_ratio(rl: pd.Series, b: pd.Series, mode: str) -> pd.Series:
    """ROLL = 직전 60일(인덱스 ≤ t−1, 최소 20) OLS 기울기, 그 전 1.0, [0, 1.5] · FIX = 1.0."""
    if mode == "FIX":
        return pd.Series(1.0, index=rl.index)
    bb = b.reindex(rl.index)
    beta = (rl.rolling(BETA_WIN, min_periods=BETA_MIN).cov(bb) / bb.rolling(BETA_WIN, min_periods=BETA_MIN).var()).shift(1)
    return beta.fillna(1.0).clip(*BETA_CLIP)


def hedge(rl: pd.Series, b: pd.Series, target: pd.Series, *, carry: float = CARRY,
          reset_days: set[date] | None = None) -> tuple[pd.Series, pd.Series, pd.Series]:
    """r_S = r_L − h·b − carry·h/252 − c_h·|h − h̃|, h̃ = 어제 h 가 밤새 움직인 비율(인버스 ETF 를 들고 있을 때).

    ``reset_days`` 를 주면(H10) 그날만 h 를 목표로 맞추고 나머지는 h̃ 그대로 든다. 반환: (r_S, h, |h − h̃|).
    """
    bb = b.reindex(rl.index).fillna(0.0)
    out, hs, trades = {}, {}, {}
    h_prev, b_prev, r_prev = 0.0, 0.0, 0.0
    first = True
    for day in rl.index:
        tilde = 0.0 if first else h_prev * (1 - b_prev) / (1 + r_prev)
        h = float(target[day]) if (first or reset_days is None or day in reset_days) else tilde
        trade = abs(h - tilde)
        r = float(rl[day]) - h * float(bb[day]) - carry * h / ANN - C_H * trade
        out[day], hs[day], trades[day] = r, h, trade
        h_prev, b_prev, r_prev, first = h, float(bb[day]), float(rl[day]), False
    return pd.Series(out), pd.Series(hs), pd.Series(trades)


# --------------------------------------------------------------------------- 지표


def mdd(r: pd.Series) -> float:
    nav = (1 + r.fillna(0.0)).cumprod()
    return float((nav / nav.cummax() - 1).min())


def sleeve_stats(rs: pd.Series, b: pd.Series, days: list[date], *, sel: pd.Series, uni: pd.Series,
                 turn: pd.Series, htrade: pd.Series, h: pd.Series) -> dict[str, float]:
    r = rs.reindex(days).fillna(0.0)
    bb = b.reindex(days).fillna(0.0)
    ann, vol = float(r.mean() * ANN), float(r.std() * np.sqrt(ANN))
    n = len(days)
    out = {
        "n": n, "ann": ann, "vol": vol, "ir": ann / vol if vol > 0 else float("nan"),
        "nw4": float(ic_module.newey_west_t(r, lag=4)), "nw20": float(ic_module.newey_west_t(r, lag=20)),
        "mdd": mdd(r), "cum": float((1 + r).prod() - 1),
        "resid_beta": float(np.cov(r, bb)[0, 1] / bb.var()) if bb.var() > 0 else float("nan"),
        "sel_ann": float(sel.reindex(days).fillna(0.0).mean() * ANN),
        "uni_ann": float(uni.reindex(days).fillna(0.0).mean() * ANN),
        "turn_long": float(turn.reindex(days).fillna(0.0).sum() * ANN / n),
        "turn_hedge": float(htrade.reindex(days).fillna(0.0).sum() * ANN / n),
        "h_mean": float(h.reindex(days).mean()),
    }
    sv = sel.reindex(days).fillna(0.0)
    out["sel_ir"] = float(sv.mean() * ANN / (sv.std() * np.sqrt(ANN))) if sv.std() > 0 else float("nan")
    out["cost"] = out["turn_long"] * ONE_WAY_COST + out["turn_hedge"] * C_H + CARRY * out["h_mean"]
    return out


def structure_stats(r: pd.Series, d2: pd.Series, days: list[date], *, expo: pd.Series | None = None) -> dict[str, float]:
    rr = r.reindex(days).fillna(0.0)
    bb = d2.reindex(days).fillna(0.0)
    m = metrics(rr, bb)
    up, dn = capture(rr, bb)
    cum = float((1 + rr).prod() - 1)
    return {"n": len(days), "cum": cum, "ex_cum": cum - float((1 + bb).prod() - 1), "ex_ann": float((rr - bb).mean() * ANN),
            "ir": m["ir"], "mdd": m["mdd"], "beta": m["beta"], "cap_down": dn, "cap_up": up, "ann": m["ann"],
            "expo": float(expo.reindex(days).mean()) if expo is not None else float("nan")}


def blocks_positive(r: pd.Series, days: list[date]) -> float:
    v = r.reindex(days).fillna(0.0).to_numpy()
    sums = [v[i:i + rkit.BLOCK].sum() for i in range(0, len(v) - rkit.BLOCK + 1, rkit.BLOCK)]
    return float(np.mean([s > 0 for s in sums])) if sums else float("nan")


def half_year_ir(r: pd.Series, days: list[date]) -> dict[str, float]:
    s = r.reindex(days).fillna(0.0)
    s.index = pd.to_datetime(s.index)
    out = {}
    for key, part in s.groupby([s.index.year, (s.index.month - 1) // 6]):
        sd = part.std()
        out[f"{key[0]}H{key[1] + 1}"] = float(part.mean() / sd * np.sqrt(ANN)) if sd > 0 else float("nan")
    return out


def compounding_gap(b: pd.Series, days: list[date], *, lev: float, span: int = 10) -> dict[str, float]:
    """겹치지 않는 span 세션마다 인버스(−lev×) 일일 재조정 복리 − 정적 −lev× 수익 — 문서 §1 D."""
    v = b.reindex(days).fillna(0.0).to_numpy()
    gaps = []
    for i in range(0, len(v) - span + 1, span):
        x = v[i:i + span]
        gaps.append(float(np.prod(1 - lev * x) - 1 - (-lev * (np.prod(1 + x) - 1))))
    g = np.array(gaps)
    return {"mean": float(g.mean()), "p05": float(np.quantile(g, 0.05)), "ann": float(g.mean() * ANN / span), "n": len(g)} if len(g) else {}


# --------------------------------------------------------------------------- 본 측정


def run_kr(store: Store, now: datetime) -> dict:
    sessions = sorted(set(kr_preds(0)["session"]))
    ret, bench, trad = rkit.market_data(store, [PRICE_START, *sessions])
    calendar = list(ret.index)
    sigma = ret.shift(2).rolling(VOL_WIN, min_periods=VOL_MIN).std()
    guard("시장 자료")
    y = labels(KR_LABELS, "KR")

    legs: dict[tuple[int, str], dict[int, pd.Series]] = {}
    turns: dict[tuple[int, str], dict[int, pd.Series]] = {}
    tc: dict[tuple[int, str], list[pd.DataFrame]] = {}
    ics: dict[int, pd.Series] = {}
    rebal_days: dict[tuple[int, str], set[date]] = {}
    check2 = None
    for s in SEEDS:
        pred = kr_preds(s)
        ics[s] = ic_series(pred, y)
        for n in NS:
            for wt in WEIGHTS:
                daily, turn, rb = long_leg(pred, ret, trad, n, equal if wt == "EW" else inverse_vol(sigma))
                legs.setdefault((n, wt), {})[s] = daily
                turns.setdefault((n, wt), {})[s] = turn
                tc.setdefault((n, wt), []).append(rb)
                rebal_days.setdefault((n, wt), set()).update(rb.index)
                print(f"  seed{s} N{n} {wt}: {len(daily)}세션 연 {daily.mean() * ANN:+.1%} · 재조정 {len(rb)}", flush=True)
        if s == SEEDS[0]:
            kit_daily, _ = rkit.portfolio(pred, ret, trad, every=REBAL)
            check2 = float((kit_daily - legs[(24, "EW")][s].reindex(kit_daily.index)).abs().max())
            print(f"검산 2 — EW 루프 대 rkit.portfolio 최대 차이 {check2:.2e}", flush=True)
        del pred
        guard(f"seed{s}")
    del y

    days = list(legs[(24, "EW")][SEEDS[0]].index)
    spans = realized_end(days, calendar)
    wdays = {k: in_window(pd.Index(days), spans, v) for k, v in WINDOWS.items()}
    b = bench.reindex(days)
    dist = float(store.config("benchmark.kodex200_distribution_yield_annual", as_of=now))
    d2 = b + dist / ANN
    uni = pd.Series({d: float(ret.loc[d].reindex(list(trad.get(d, ()))).mean()) for d in days if d in ret.index})
    scale, state = exposure_scales(store, days, now)
    scale = scale.reindex(days).fillna(1.0)

    res: dict = {"A": {}, "A_seed_ir": {}, "A_sens": {}, "B": {}, "C": {}, "D": {}, "check": {"check2": check2},
                 "half_year": {}, "blocks_pos": {}}
    daily_out: dict[str, pd.Series] = {"b": b, "D2": d2, "U": uni, "scale": scale}
    hmap: dict[tuple, pd.Series] = {}
    for (n, wt), seeds in legs.items():
        rl = pd.DataFrame(seeds).reindex(days).mean(axis=1)
        turn = pd.DataFrame(turns[(n, wt)]).reindex(days).fillna(0.0).mean(axis=1)
        sel = rl - uni.reindex(days)
        daily_out[f"L-{n}-{wt}"] = rl
        for mode in BETAS:
            h_target = hedge_ratio(rl, b, mode)
            rs, h, ht = hedge(rl, b, h_target)
            hmap[(n, wt, mode)] = h
            uni_part = uni.reindex(days) - h * b
            key = f"{n}-{wt}-{mode}"
            daily_out[f"S-{key}"] = rs
            res["A"][key] = {w: sleeve_stats(rs, b, wd, sel=sel, uni=uni_part, turn=turn, htrade=ht, h=h)
                             for w, wd in wdays.items()}
            res["half_year"][key] = half_year_ir(rs, wdays["전체"])
            res["blocks_pos"][key] = blocks_positive(rs, wdays["전체"])
            seed_ir = []
            for leg in seeds.values():
                leg = leg.reindex(days).fillna(0.0)
                r_s, _, _ = hedge(leg, b, hedge_ratio(leg, b, mode))
                v = r_s.reindex(wdays["전체"]).fillna(0.0)
                seed_ir.append(float(v.mean() / v.std() * np.sqrt(ANN)))
            res["A_seed_ir"][key] = [min(seed_ir), max(seed_ir)]
            # 민감도: carry 2% · H10(재조정일에만 헤지 맞춤)
            r_hi, _, _ = hedge(rl, b, h_target, carry=CARRY_HI)
            r_10, h10, ht10 = hedge(rl, b, h_target, reset_days=rebal_days[(n, wt)])
            res["A_sens"][key] = {
                "carry2": {w: sleeve_stats(r_hi, b, wd, sel=sel, uni=uni_part, turn=turn, htrade=ht, h=h)["ir"]
                           for w, wd in wdays.items()},
                "H10": {w: sleeve_stats(r_10, b, wd, sel=sel, uni=uni.reindex(days) - h10 * b, turn=turn, htrade=ht10, h=h10)
                        for w, wd in wdays.items()},
            }
        # 선택 알파(비용 전) — 롱 다리 비용을 되돌린다
        sel_gross = sel + turn * ONE_WAY_COST
        res["C"][f"{n}-{wt}"] = {
            "tc": float(pd.concat(tc[(n, wt)])["tc"].mean()),
            "U": float(pd.concat(tc[(n, wt)])["U"].mean()),
            "sel_ir_gross": {w: _ir(sel_gross, wd) for w, wd in wdays.items()},
            "sel_ir_net": {w: _ir(sel, wd) for w, wd in wdays.items()},
        }

    # B — 구조
    off = daily_out["L-24-EW"]
    cur, _ = apply_exposure(off, scale)
    ds = scale.diff().fillna(0.0).abs()
    iv6 = scale * d2 - C_H * ds
    arms = {"I100": (d2, pd.Series(1.0, index=days)), "IV6": (iv6, scale), "CUR": (cur, scale), "CUR_OFF": (off, None)}
    viol: dict[str, int] = {}
    for n, wt in legs:
        rl = daily_out[f"L-{n}-{wt}"]
        for mode in BETAS:
            h = hmap[(n, wt, mode)]
            rs = daily_out[f"S-{n}-{wt}-{mode}"]
            for w in CORES:
                x = scale * w - h * (1 - w)
                net = ((1 - w) * rl + x.clip(lower=0) * d2 - (-x).clip(lower=0) * (b + CARRY / ANN)
                       - C_H * x.diff().fillna(0.0).abs())
                lo = (1 - w) / (1 + h)
                gross = lo * rs + scale * w * d2 - C_H * (scale * w).diff().fillna(0.0).abs()
                f = scale - h * (1 - w)
                port = (1 - w) * rl + f * b - f.abs() * CARRY / ANN - C_H * f.diff().fillna(0.0).abs()
                tag = f"{n}-{wt}-{mode}-w{w}"
                arms[f"NET-{tag}"] = (net, x)
                arms[f"GROSS-{tag}"] = (gross, scale * w)
                arms[f"PORT-{tag}"] = (port, f)
                viol[f"NET-{tag}"] = int(((1 - w) + x.abs() > 1 + 1e-12).reindex(wdays["전체"]).sum())
                viol[f"PORT-{tag}"] = int((MARGIN * f.abs() > w + 1e-12).reindex(wdays["전체"]).sum())
    # 검산 3 — NET w=1 은 IV6 와 같다(정의상). 식 그대로 한 번 돈다.
    h0 = hmap[(24, "EW", "ROLL")]
    x1 = scale * 1.0 - h0 * 0.0
    net1 = x1.clip(lower=0) * d2 - C_H * x1.diff().fillna(0.0).abs()
    res["check"]["check3"] = float((net1 - iv6).abs().max())
    for name, (r, expo) in arms.items():
        res["B"][name] = {w: structure_stats(r, d2, wd, expo=expo) for w, wd in wdays.items()}
        daily_out[f"B-{name}"] = r
    res["B_violations"] = viol

    # 검산 1 — 하락 구간 CUR·CUR_OFF 를 하락장 검진 §3 의 A·B 와
    bear = json.loads((BEAR_PRED / "results.json").read_text())["windows"]["하락장"]
    res["check"]["check1"] = {"CUR": res["B"]["CUR"]["하락"]["cum"], "bear_A": bear["A"]["cum"],
                              "CUR_OFF": res["B"]["CUR_OFF"]["하락"]["cum"], "bear_B": bear["B"]["cum"],
                              "CUR_mdd": res["B"]["CUR"]["하락"]["mdd"], "bear_A_mdd": bear["A"]["mdd"]}

    # C — IC·폭
    ic_frame = pd.DataFrame(ics)
    ic_mean = ic_frame.mean(axis=1)
    res["C"]["ic"] = {w: float(ic_mean.reindex(wd).mean()) for w, wd in wdays.items()}
    seam = [d for d in ic_mean.index if SPLICE - timedelta(days=60) <= d < SPLICE + timedelta(days=60)]
    res["C"]["ic_seam"] = {"before": float(ic_mean[[d for d in seam if d < SPLICE]].mean()),
                           "after": float(ic_mean[[d for d in seam if d >= SPLICE]].mean())}
    daily_out["IC"] = ic_mean

    # D — 인버스 복리 차이(K200 근사)
    res["D"] = {f"{lev:g}x": {w: compounding_gap(b, wd, lev=lev) for w, wd in wdays.items()} for lev in (1.0, 2.0)}
    res["dist_yield"] = dist
    res["state_share"] = {w: state.reindex(wd).value_counts(normalize=True).to_dict() for w, wd in wdays.items()}
    pd.DataFrame(daily_out).to_pickle(OUT / "daily-KR.pkl")  # invariant-allow: data-access — 진단 원본
    return res


def _ir(r: pd.Series, days: list[date]) -> float:
    v = r.reindex(days).fillna(0.0)
    return float(v.mean() / v.std() * np.sqrt(ANN)) if v.std() > 0 else float("nan")


def run_us(store: Store) -> dict:
    """미장 — 회차 C0 미장 행 · M1 합성 · `trial_us_kit.book` · 벤치 = kit 캐시 벤치(US:SPY). EW 만(문서 §1)."""
    panel = pd.read_parquet(US_PANEL, columns=["entity_id", "session", "market", "fund_raw", "has_fund", "y5"])  # invariant-allow: data-access — 연구 패널 캐시
    panel = panel[panel["market"] == "US"]
    panel["session"] = pd.to_datetime(panel["session"]).dt.date
    fund = panel[["entity_id", "session", "fund_raw", "has_fund"]].reset_index(drop=True)
    y = panel[["entity_id", "session", "y5"]].dropna()
    del panel
    ret, bench = ukit.market()
    from tools.trial_us_index_minus_losers import cost_one_way
    cost = cost_one_way(store, datetime.combine(date(2026, 6, 30), time(23), tzinfo=UTC))
    legs: dict[int, dict[int, pd.Series]] = {n: {} for n in NS}
    ics = {}
    for s in SEEDS:
        c0 = pd.read_pickle(fkit.control_path("C0", s, C0_TAG))  # invariant-allow: data-access — 대조군 캐시
        pred = c0[c0["market"] == "US"][["entity_id", "session", "pred"]].reset_index(drop=True)
        del c0
        ics[s] = ic_series(pred, y)
        wide = fkit.us_m1_wide(pred, fund)
        for n in NS:
            legs[n][s], _ = ukit.book(wide, ret, cost, n=n, exit_mult=rkit.EXIT_MULT, every=REBAL)
        uni_names = pred.groupby("session")["entity_id"].apply(set).to_dict()
        del pred, wide
        guard(f"US seed{s}")
    days = list(legs[24][SEEDS[0]].index)
    calendar = list(ret.index)
    spans = realized_end(days, calendar)
    wdays = {k: in_window(pd.Index(days), spans, v) for k, v in US_WINDOWS.items()}
    b = bench.reindex(days)
    uni = pd.Series({d: float(ret.loc[d].reindex(list(uni_names.get(d, ()))).mean()) for d in days if d in ret.index})
    res: dict = {"A": {}, "ic": {w: float(pd.DataFrame(ics).mean(axis=1).reindex(wd).mean()) for w, wd in wdays.items()},
                 "cost_one_way": cost}
    for n in NS:
        rl = pd.DataFrame(legs[n]).reindex(days).mean(axis=1)
        sel = rl - uni.reindex(days)
        for mode in BETAS:
            rs, h, ht = hedge(rl, b, hedge_ratio(rl, b, mode))
            res["A"][f"{n}-EW-{mode}"] = {w: sleeve_stats(rs, b, wd, sel=sel, uni=uni.reindex(days) - h * b,
                                                          turn=pd.Series(0.0, index=days), htrade=ht, h=h)
                                          for w, wd in wdays.items()}
            res["A"][f"{n}-EW-{mode}"]["전체"]["long_ann"] = float(rl.reindex(wdays["전체"]).mean() * ANN)
    return res


# --------------------------------------------------------------------------- 보고 표(문서 §2 로 옮긴다)


def _f(v, fmt: str) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:+.1%}" if fmt == "%" else (f"{v:.0%}" if fmt == "p" else f"{v:+.2f}" if fmt == "s" else f"{v:.2f}")


def report() -> int:
    kr = json.loads((OUT / "results-KR.json").read_text())
    regimes = list(WINDOWS)
    print("### A. 슬리브 IR (국면별) · 전체 지표\n")
    print("| N · 비중 · β | " + " | ".join(f"IR {w}" for w in regimes) + " | 연 | 변동성 | NW t4 / t20 | MDD | 잔여β | 회전 롱+헤지 | h̄ | 시드 IR | 양수 블록 |")
    print("|---|" + "---|" * (len(regimes) + 9))
    for key, by in kr["A"].items():
        a = by["전체"]
        lo, hi = kr["A_seed_ir"][key]
        print(f"| {key} | " + " | ".join(_f(by[w]["ir"], "s") for w in regimes)
              + f" | {_f(a['ann'], '%')} | {a['vol']:.1%} | {a['nw4']:+.1f} / {a['nw20']:+.1f} | {_f(a['mdd'], '%')} | "
              f"{a['resid_beta']:+.2f} | {a['turn_long']:.1f}+{a['turn_hedge']:.1f} | {a['h_mean']:.2f} | "
              f"{lo:+.2f}~{hi:+.2f} | {kr['blocks_pos'][key]:.0%} |")
    print("\n### A. 분해 — 연환산 (선택 = 상위 N − 유니버스 EW · 유니버스 = 유니버스 EW − h·K200)\n")
    print("| N · 비중 · β | " + " | ".join(f"{w} 선택 / 유니버스 / 슬리브" for w in regimes) + " | 선택 IR 전체 |")
    print("|---|" + "---|" * (len(regimes) + 1))
    for key, by in kr["A"].items():
        print(f"| {key} | " + " | ".join(f"{_f(by[w]['sel_ann'], '%')} / {_f(by[w]['uni_ann'], '%')} / {_f(by[w]['ann'], '%')}"
                                         for w in regimes) + f" | {_f(by['전체']['sel_ir'], 's')} |")
    print("\n### A. 민감도 — carry 2% · H10(재조정일에만 헤지)\n")
    print("| N · 비중 · β | IR 기본 전체 | IR carry2 전체 | IR H10 " + " / ".join(regimes) + " | H10 헤지 회전 |")
    print("|---|---|---|---|---|")
    for key, sens in kr["A_sens"].items():
        print(f"| {key} | {_f(kr['A'][key]['전체']['ir'], 's')} | {_f(sens['carry2']['전체'], 's')} | "
              + " / ".join(_f(sens["H10"][w]["ir"], "s") for w in regimes) + f" | {sens['H10']['전체']['turn_hedge']:.1f} |")
    print("\n### A. 반기별 IR\n")
    halves = sorted({h for v in kr["half_year"].values() for h in v})
    print("| N · 비중 · β | " + " | ".join(halves) + " |")
    print("|---|" + "---|" * len(halves))
    for key, v in kr["half_year"].items():
        print(f"| {key} | " + " | ".join(_f(v.get(h), "s") for h in halves) + " |")

    print("\n### B. 구조 — 벤치 KODEX200 TR 대용(D2)\n")
    print("| 팔 | " + " | ".join(f"초과 {w}" for w in regimes) + " | 전체 IR | 연 초과 | MDD | β | 하락·상승 포착 | 평균 지수 노출 |")
    print("|---|" + "---|" * (len(regimes) + 6))
    for name, by in kr["B"].items():
        if name.startswith(("GROSS", "PORT")) or ("-FIX-" in name):
            continue
        a = by["전체"]
        print(f"| {name} | " + " | ".join(_f(by[w]["ex_cum"], "%") for w in regimes)
              + f" | {_f(a['ir'], 's')} | {_f(a['ex_ann'], '%')} | {_f(a['mdd'], '%')} | {a['beta']:.2f} | "
              f"{a['cap_down']:.2f} / {a['cap_up']:.2f} | {_f(a['expo'], 'p')} |")
    print("\n### B. FIX · GROSS · PORT 요약(전체: IR · 연 초과 · MDD · 하락 초과)\n")
    print("| 꼬리표 | NET-FIX | GROSS-ROLL | PORT-ROLL |")
    print("|---|---|---|---|")
    for name in kr["B"]:
        if not name.startswith("NET-") or "-ROLL-" not in name:
            continue
        tag = name[4:]
        cells = []
        for other in (f"NET-{tag.replace('-ROLL-', '-FIX-')}", f"GROSS-{tag}", f"PORT-{tag}"):
            a, d = kr["B"][other]["전체"], kr["B"][other]["하락"]
            cells.append(f"{_f(a['ir'], 's')} · {_f(a['ex_ann'], '%')} · {_f(a['mdd'], '%')} · {_f(d['ex_cum'], '%')}")
        print(f"| {tag} | " + " | ".join(cells) + " |")
    print(f"\n위반(전체 구간 세션 수): { {k: v for k, v in kr['B_violations'].items() if v} or '없음'}")

    print("\n### C. Grinold — IC × √폭\n")
    ic = kr["C"]["ic"]["전체"]
    print(f"IC(h5, 전체) {ic:+.4f} · 국면별 " + " · ".join(f"{w} {kr['C']['ic'][w]:+.3f}" for w in regimes)
          + f" · 이음매 ±60일 {kr['C']['ic_seam']['before']:+.3f} → {kr['C']['ic_seam']['after']:+.3f}")
    print("\n| N · 비중 | U | 이론 IR (BR₅ / BR₁₀) | TC | TC × 이론 (BR₁₀) | 선택 IR 비용 전 | 선택 IR 비용 후 | 슬리브 IR (ROLL) |")
    print("|---|---|---|---|---|---|---|---|")
    for key, c in kr["C"].items():
        if key in ("ic", "ic_seam"):
            continue
        br5, br10 = c["U"] * ANN / 5, c["U"] * ANN / 10
        t5, t10 = ic * np.sqrt(br5), ic * np.sqrt(br10)
        print(f"| {key} | {c['U']:.0f} | {t5:.1f} / {t10:.1f} | {c['tc']:.3f} | {c['tc'] * t10:.2f} | "
              f"{_f(c['sel_ir_gross']['전체'], 's')} | {_f(c['sel_ir_net']['전체'], 's')} | {_f(kr['A'][key + '-ROLL']['전체']['ir'], 's')} |")

    print("\n### D. 인버스 일일 재조정 복리 차이(K200 근사, 10세션 창)\n")
    print("| 배수 | " + " | ".join(regimes) + " |")
    print("|---|" + "---|" * len(regimes))
    for lev, by in kr["D"].items():
        print(f"| −{lev} | " + " | ".join(f"평균 {by[w]['mean']:+.2%} · 5% {by[w]['p05']:+.2%} · 연 {by[w]['ann']:+.1%}"
                                         if by[w] else "—" for w in regimes) + " |")
    print("\n검산:", json.dumps(kr["check"], ensure_ascii=False))
    print("국면 분포:", json.dumps({w: {k: round(v, 2) for k, v in d.items()} for w, d in kr["state_share"].items()}, ensure_ascii=False))

    us_path = OUT / "results-US.json"
    if us_path.exists():
        us = json.loads(us_path.read_text())
        uw = list(US_WINDOWS)
        print("\n### 미장 슬리브 (C0 미장 · M1 · SPY 헤지 · EW)\n")
        print("IC(h5) " + " · ".join(f"{w} {us['ic'][w]:+.3f}" for w in uw) + f" · 편도 비용 {us['cost_one_way']:.2%}")
        print("\n| N · β | " + " | ".join(f"IR {w}" for w in uw) + " | 롱 연 | 선택 / 유니버스 / 슬리브 연 | MDD | 잔여β | h̄ |")
        print("|---|" + "---|" * (len(uw) + 5))
        for key, by in us["A"].items():
            a = by["전체"]
            print(f"| {key} | " + " | ".join(_f(by[w]["ir"], "s") for w in uw)
                  + f" | {_f(a['long_ann'], '%')} | {_f(a['sel_ann'], '%')} / {_f(a['uni_ann'], '%')} / {_f(a['ann'], '%')} | "
                  f"{_f(a['mdd'], '%')} | {a['resid_beta']:+.2f} | {a['h_mean']:.2f} |")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        return report()
    now = datetime.now(UTC)  # invariant-allow: wallclock — 실행 관문과 "오늘의 설정"(하락장 검진과 같다)
    if not market_closed(now):
        print("장 중이다 — 돌리지 않는다(rc 9)", flush=True)
        return 9
    if available_gb() < MIN_AVAILABLE_GB:
        print(f"가용 메모리 {available_gb():.1f}GB < {MIN_AVAILABLE_GB}GB — 기다린다(rc 7)", flush=True)
        return 7
    OUT.mkdir(parents=True, exist_ok=True)
    store = Store(root=Path("data"))
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    results: dict = {}
    if only in ("all", "kr"):
        results["KR"] = run_kr(store, now)
        (OUT / "results-KR.json").write_text(json.dumps(results["KR"], ensure_ascii=False, indent=1, default=str))
        guard("국장 끝")
    if only in ("all", "us"):
        results["US"] = run_us(store)
        (OUT / "results-US.json").write_text(json.dumps(results["US"], ensure_ascii=False, indent=1, default=str))
        guard("미장 끝")
    print(f"끝 · 최대 RSS {fkit.rss_mb():.0f}MB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
