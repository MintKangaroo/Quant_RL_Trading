"""시행 AL — 예측 불확실성으로 종목 수를 조절한다. docs/protocols/uncertainty-breadth-2026-09.md.

    .venv/bin/python tools/trial_uncertainty_breadth.py [--save]

5시드 배깅 평균 점수 · 표준편차. 그날 gap(24위−72위 평활 점수) < unc(1~72위 se 중앙값) 이면 72, 아니면 24.
L0 24 고정 · L72 72 고정 · L1 적응. 다섯 기준(두 국면 +1%p · vs L72 +1%p · NW t · MDD · 회전 ≤ L0).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END, FEATS  # noqa: E402
from tools.trial_ranker_kit import (  # noqa: E402
    N,
    blocks,
    judge_panel,
    mark,
    market_data,
    nw_t,
    portfolio,
    record,
    summarize,
    walk,
)

PROTOCOL = Path("docs/protocols/uncertainty-breadth-2026-09.md")
SEEDS = (0, 1, 2, 3, 4)
WIDE = 72
GATE_ANN, GATE_T, GATE_MDD = 0.01, 2.0, 0.03


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선 확인용")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AL — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    panel, sessions = judge_panel()
    bl = blocks(sessions)
    if args.smoke:
        bl = bl[: args.smoke]
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} · 블록 {len(bl)} · 시드 {len(SEEDS)}", flush=True)
    pred = walk(panel, sessions, FEATS, "y5", bl, seeds=SEEDS, label="5시드")
    single = walk(panel, sessions, FEATS, "y5", bl, seeds=(0,), label="seed0")

    # 적응 규칙 — n_of 가 받는 row 는 portfolio 가 EMA5 로 평활한 그날 점수다. se 는 평활하지 않는다(등록).
    se = pred.pivot_table(index="session", columns="entity_id", values="pred_se").sort_index()
    decisions: dict[date, int] = {}
    gaps: dict[date, tuple[float, float]] = {}  # 기록: 날마다 (gap, unc) — 규칙이 한 번도 안 켜져도 왜인지 읽을 수 있게

    def n_of(day: date, row: pd.Series) -> int:
        order = row.sort_values(ascending=False)
        if len(order) < WIDE:
            decisions[day] = N
            return N
        gap = float(order.iloc[N - 1] - order.iloc[WIDE - 1])
        unc = float(se.loc[day].reindex(order.index[:WIDE]).median()) if day in se.index else float("inf")
        gaps[day] = (gap, unc)
        n = WIDE if gap < unc else N
        decisions[day] = n
        return n

    ret, bench, trad = market_data(store, sessions)
    y = panel[["entity_id", "session", "y5"]]
    rows, series = {}, {}
    daily, extra = portfolio(pred, ret, trad)
    rows["L0"], series["L0"] = {**summarize(daily, bench, pred.merge(y, on=["entity_id", "session"])), **extra}, daily
    daily, extra = portfolio(pred, ret, trad, n_of=lambda d, r: WIDE)
    rows["L72"], series["L72"] = {**summarize(daily, bench), **extra}, daily
    daily, extra = portfolio(pred, ret, trad, n_of=n_of)
    rows["L1"], series["L1"] = {**summarize(daily, bench), **extra}, daily
    daily, extra = portfolio(single, ret, trad)
    rows["T0(seed0)"] = {**summarize(daily, bench, single.merge(y, on=["entity_id", "session"])), **extra}

    dec = pd.Series(decisions).sort_index()
    flips = dec[dec != dec.shift()].index[1:]
    saw = sum(1 for a, b in zip(flips, flips[1:], strict=False) if sessions.index(b) - sessions.index(a) <= 20)
    l0, l72, l1 = rows["L0"], rows["L72"], rows["L1"]
    t = nw_t(series["L1"], series["L0"])
    g = pd.DataFrame(gaps, index=["gap", "unc"]).T
    lines = [f"기록: gap(24위−72위) 중앙값 {g['gap'].median():.4f} · unc(se 중앙값) 중앙값 {g['unc'].median():.4f} · "
             f"gap/unc 5%~50%~95% {g['gap'].div(g['unc']).quantile(0.05):.2f}/{g['gap'].div(g['unc']).quantile(0.5):.2f}/"
             f"{g['gap'].div(g['unc']).quantile(0.95):.2f}",
             f"기록: 넓게 든 세션 {l1['wide_share']:.0%} (박스 {(dec[dec.index <= BOX_END] > N).mean():.0%} · "
             f"급등 {(dec[dec.index > BOX_END] > N).mean():.0%}) · 전환 {len(flips)}회(톱니 {saw}) · "
             f"5시드 IC {l0['ic']:+.4f} vs seed0 {rows['T0(seed0)']['ic']:+.4f} · L0 vs seed0 연수익 {l0['ann'] - rows['T0(seed0)']['ann']:+.1%}p",
             "",
             "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | 비대칭 | 회전 | 평균 N | IR(K200) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in rows.items():
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                     f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['asym']:+.3f} | {m['turn']:.1f} | {m['n_mean']:.0f} | {m['ir']:+.2f} |")
    c = (l1["box_ann"] >= l0["box_ann"] + GATE_ANN and l1["rally_ann"] >= l0["rally_ann"] + GATE_ANN,
         l1["ann"] >= l72["ann"] + GATE_ANN, t >= GATE_T,
         l1["mdd"] >= max(l0["mdd"], l72["mdd"]) - GATE_MDD, l1["turn"] <= l0["turn"])
    lines += ["", f"L1: ①두 국면 +1%p ({l1['box_ann'] - l0['box_ann']:+.1%}/{l1['rally_ann'] - l0['rally_ann']:+.1%}) {mark(c[0])} · "
                  f"②vs L72 {l1['ann'] - l72['ann']:+.1%} {mark(c[1])} · ③NW t {t:+.2f} {mark(c[2])} · ④MDD {l1['mdd']:.1%} {mark(c[3])} · "
                  f"⑤회전 {l1['turn']:.1f} vs {l0['turn']:.1f} {mark(c[4])} → {'통과' if all(c) else '탈락'}"]
    verdict = "채택 L1" if all(c) else "기각 — 24 고정 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save and not args.smoke:
        record(store, entity="uncertainty-breadth-2026-09:AL", source="trial_uncertainty_breadth", family="selection",
               digest=digest, verdict=verdict, lines=lines)
        print(f"research_trials 기록: selection/AL · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
