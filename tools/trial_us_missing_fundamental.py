"""시행 AT — 미장 합성 점수에서 빠진 fundamental 을 어떻게 다루나. docs/protocols/us-missing-fundamental-2026-09.md.

    .venv/bin/python tools/trial_us_missing_fundamental.py [--save] [--precheck] [--smoke 2]

M0 = 현행(결측은 분모에서 뺀다) · M1 = 결측을 0 으로 분모에 남긴다 · M2 = 랭커(루프 GBM) 점수 하나. 세 시드.
유니버스는 **거래대금 상위 1,000**(세션마다) — 시총 상위로 자르면 시총이 없는 ADR(20-F)이 빠져 문제가 난 자리를 못 잰다(복기 규칙 4).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, metrics  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_kit import fit, record  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402
from tools.trial_us_index_minus_losers import FEATS, load_scores  # noqa: E402

PROTOCOL = Path("docs/protocols/us-missing-fundamental-2026-09.md")
JUDGE_START, JUDGE_END, BOX_END = date(2022, 7, 1), date(2026, 6, 30), date(2024, 12, 31)
UNIVERSE, H, MIN_TRAIN, BLOCK, PURGE = 1000, 5, 150, 20, 5
N, EXIT_MULT = 24, 2                 # 완충 48 = exit_rank_us
W_FUND, W_RANK = 0.84, 1.0           # 2026-09-25 실전 비(fundamental 0.836 : ranker 1.0)
#: 실전 신뢰도(2026-09-25 신호에서 읽은 상수) — 합성 몫은 가중치 × 신뢰도다(combine.combined_scores).
CONF_FUND, CONF_RANK = 0.072, 0.094
SEEDS = (0, 1, 2)
BENCH = "US:SPY"
GATE_MEAN, GATE_REGIME, GATE_MDD, GATE_TURN = 0.02, -0.01, 0.02, 1.2
VARIANTS = ("M0", "M1", "M2")


def combine(variant: str, fund: pd.Series, has_fund: pd.Series, rank: pd.Series) -> pd.Series:
    if variant == "M2":
        return rank
    a, b = W_FUND * CONF_FUND, W_RANK * CONF_RANK
    if variant == "M1":
        return (a * fund.where(has_fund, 0.0) + b * rank) / (a + b)
    # M0 — 결측이면 분모에서도 빠진다 → 랭커 점수 그대로
    both = (a * fund + b * rank) / (a + b)
    return both.where(has_fund, rank)


def book(score: pd.DataFrame, fund_mask: pd.DataFrame, ret: pd.DataFrame, cost: float) -> tuple[pd.Series, dict]:
    held: list[str] = []
    prev = None
    out, turns, miss = {}, [], []
    for day in score.index:
        if day not in ret.index:
            continue
        row = score.loc[day].dropna()
        if row.empty:
            continue
        held = pick_mult(held, row.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - cost * t)
        turns.append(t)
        miss.append(float((~fund_mask.loc[day].reindex(held).fillna(False).astype(bool)).mean()))
        d = w * (1 + dr)
        prev = d / d.sum() if d.sum() > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "miss": float(np.mean(miss))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--precheck", action="store_true", help="커버리지·상위 24 겹침만(수익 계산 없음)")
    parser.add_argument("--smoke", type=int, default=0)
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AT — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    now = datetime.combine(JUDGE_END, time(23), tzinfo=UTC)
    span = (JUDGE_END - JUDGE_START).days + 60
    cost = float(store.config("accounting.fee_us", as_of=now))

    prices = read_prices(store, as_of=now, lookback=span + 40, columns=["close", "volume"], adjusted=True, market="US")
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    close = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    volume = prices.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last").sort_index()
    del prices
    bench_close = close.pop(BENCH) if BENCH in close.columns else None
    volume = volume.drop(columns=[BENCH], errors="ignore")
    dv = (close * volume).rolling(20, min_periods=10).mean()
    dv = dv[(dv.index >= JUDGE_START) & (dv.index <= JUDGE_END)]
    in_universe = dv.rank(axis=1, ascending=False) <= UNIVERSE
    keep = in_universe.stack()
    keep = keep[keep].reset_index()
    keep.columns = ["session", "entity_id", "_"]
    keep = keep[["entity_id", "session"]]
    print(f"거래대금 상위 {UNIVERSE} · 세션 {in_universe.shape[0]} · 종목 {keep['entity_id'].nunique():,} · 비용 편도 {cost:.2%}", flush=True)

    panel = load_scores(keep)
    panel["has_fund"] = panel["fundamental"].notna()
    # **실전 척도 그대로 합성한다**(정정 1). fundamental 은 Analyst 원점수(표준편차 ~0.19), 랭커는 실전 변환
    # tanh(순위점수/2)(±0.70). 둘을 rank-gauss 로 맞추면 실전의 쏠림(척도 차 + 분모 제외)이 재현되지 않는다 — 점검에서 겹침 90%.
    panel["fund_raw"] = panel["fundamental"].astype(float)
    fwd = close.shift(-H) / close - 1.0
    fwd = fwd.where(fwd.abs() <= 1.0)
    y = fwd.stack().rename("y5").reset_index()
    y.columns = ["session", "entity_id", "y5"]
    panel = panel.merge(y, on=["entity_id", "session"], how="left")
    panel["market"] = "US"
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    print(f"커버리지: fundamental 있는 행 {panel['has_fund'].mean():.1%}", flush=True)
    panel = rank_gauss(panel, [*FEATS, "y5"])
    sessions = sorted(panel["session"].unique())
    bl, start = [], MIN_TRAIN + PURGE
    while start + BLOCK <= len(sessions):
        bl.append((start, start + BLOCK - 1))
        start += BLOCK
    if args.smoke or args.precheck:
        bl = bl[: (args.smoke or 5)]
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} · 블록 {len(bl)}", flush=True)

    preds = {s: [] for s in (SEEDS if not args.precheck else (0,))}
    for first, last in bl:
        train_end = sessions[first - PURGE - 1]
        train = panel[(panel["session"] <= train_end) & panel["y5"].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])]
        X, yy = train[FEATS].to_numpy(np.float32), train["y5"].to_numpy(np.float32)
        for s in preds:
            t = test[["entity_id", "session", "fund_raw", "has_fund"]].copy()
            t["pred"] = fit(X, yy, seed=s).predict(test[FEATS].to_numpy(np.float32))
            preds[s].append(t)
        print(f"  블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행", flush=True)
    if args.smoke:
        print("smoke 끝 — 수치 안 찍음", flush=True)
        return 0

    ret = (close.shift(-2) / close.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    bench = (bench_close.shift(-2) / bench_close.shift(-1) - 1.0)

    res: dict[str, dict[int, dict]] = {v: {} for v in VARIANTS}
    for s, parts in preds.items():
        p = pd.concat(parts, ignore_index=True)
        # 실전 랭커 점수 변환 — 순위점수(균등, 표준편차 1: ±√3) → tanh(x/2). analysts/ranker.py·base.rank_score 와 같다.
        p["rank"] = p.groupby("session")["pred"].transform(
            lambda x: np.tanh(((x.rank() - 0.5) / x.count() - 0.5) * 2.0 * np.sqrt(3.0) / 2.0))
        mask = p.pivot_table(index="session", columns="entity_id", values="has_fund", aggfunc="last").fillna(False)
        for v in VARIANTS:
            p["c"] = combine(v, p["fund_raw"], p["has_fund"], p["rank"])
            score = p.pivot_table(index="session", columns="entity_id", values="c").sort_index()
            if args.precheck:
                res[v][s] = {"top": {d: set(score.loc[d].dropna().nlargest(N).index) for d in score.index}}
                continue
            daily, extra = book(score, mask, ret, cost)
            m = metrics(daily, bench.reindex(daily.index).fillna(0.0))
            m["box_ann"] = float(daily[daily.index <= BOX_END].mean() * ANN)
            m["rally_ann"] = float(daily[daily.index > BOX_END].mean() * ANN)
            res[v][s] = {**m, **extra}
    if args.precheck:
        tops = {v: res[v][0]["top"] for v in VARIANTS}
        for v in ("M1", "M2"):
            ov = np.mean([len(tops[v][d] & tops["M0"][d]) / N for d in tops["M0"] if d in tops[v]])
            print(f"등록 전 점검: {v} 대 M0 상위 24 겹침 평균 {ov:.0%} (첫 5블록, seed 0)", flush=True)
        return 0

    def avg(v: str, k: str) -> float:
        return float(np.mean([res[v][s][k] for s in SEEDS]))

    lines = ["| 변형 | 시드 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | 회전 | 상위24 중 fundamental 결측 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        for s in SEEDS:
            m = res[v][s]
            lines.append(f"| {v} | {s} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                         f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['turn']:.1f} | {m['miss']:.0%} |")
    lines += ["", f"SPY 같은 창: 연 {bench.reindex(pd.Index(sessions)).mean() * ANN:+.1%}", ""]
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    passed = []
    for v in ("M1", "M2"):
        wins = sum(res[v][s]["ann"] > res["M0"][s]["ann"] for s in SEEDS)
        c = (avg(v, "ann") >= avg("M0", "ann") + GATE_MEAN, wins == len(SEEDS),
             avg(v, "box_ann") >= avg("M0", "box_ann") + GATE_REGIME and avg(v, "rally_ann") >= avg("M0", "rally_ann") + GATE_REGIME,
             avg(v, "mdd") >= avg("M0", "mdd") - GATE_MDD, avg(v, "turn") <= avg("M0", "turn") * GATE_TURN)
        lines.append(f"{v}: ①평균 {avg(v, 'ann') - avg('M0', 'ann'):+.1%}p {mark(c[0])} · ②{wins}/3 {mark(c[1])} · "
                     f"③국면 {avg(v, 'box_ann') - avg('M0', 'box_ann'):+.1%}p/{avg(v, 'rally_ann') - avg('M0', 'rally_ann'):+.1%}p {mark(c[2])} · "
                     f"④MDD {avg(v, 'mdd'):.1%} vs {avg('M0', 'mdd'):.1%} {mark(c[3])} · ⑤회전 {avg(v, 'turn'):.1f} vs {avg('M0', 'turn'):.1f} {mark(c[4])}"
                     f" → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((avg(v, "sharpe"), v))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행(M0) 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="us-missing-fundamental-2026-09:AT", source="trial_us_missing_fundamental", family="selection",
               digest=digest, verdict=verdict, lines=lines[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
