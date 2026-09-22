"""시행 AJ — 시장 국면을 랭커의 입력에 넣는다. docs/protocols/ranker-market-state-2026-09.md.

    .venv/bin/python tools/trial_ranker_market_state.py [--save]

J0 = 현행 피처 6개 · J1 = 6개 + 국면 원-핫 4개(regime.classify, 종가 t 까지). 워크포워드·포트는 시행 AA 와 같다.
판정: 두 국면 IC ≥ J0−0.005 · 두 국면 연수익 ≥ J0+1%p · NW t ≥ 2 · MDD.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_ranker_ensemble import FEATS  # noqa: E402
from tools.trial_ranker_kit import (  # noqa: E402
    STATES,
    blocks,
    judge_panel,
    mark,
    market_data,
    market_state,
    nw_t,
    portfolio,
    record,
    session_sets,
    set_return,
    summarize,
    walk,
)

PROTOCOL = Path("docs/protocols/ranker-market-state-2026-09.md")
GATE_IC, GATE_ANN, GATE_T, GATE_MDD = -0.005, 0.01, 2.0, 0.03


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선 확인용")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AJ — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    panel, sessions = judge_panel()
    crisis_floor = float(store.config("exposure.crisis_momentum_floor", as_of=datetime.now(UTC)))  # invariant-allow: wallclock — 등록: 현행값
    state = market_state(store, sessions, crisis_floor)
    for s in STATES:
        panel[f"state_{s}"] = panel["session"].map(state).eq(s).astype(np.float32)
    share = state.value_counts(normalize=True)
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} · 국면 체류 " +
          " · ".join(f"{s} {share.get(s, 0):.0%}" for s in (*STATES, "unknown")), flush=True)
    bl = blocks(sessions)
    if args.smoke:
        bl = bl[: args.smoke]
    feats1 = [*FEATS, *(f"state_{s}" for s in STATES)]
    preds = {"J0": walk(panel, sessions, FEATS, "y5", bl, label="J0"),
             "J1": walk(panel, sessions, feats1, "y5", bl, label="J1")}

    # 기록: 국면 피처 gain 비중 — 마지막 블록 모델 하나로(대표값)
    from tools.trial_ranker_kit import fit
    last_first = bl[-1][0]
    train = panel[(panel["session"] <= sessions[last_first - 6]) & panel["y5"].notna()]
    model = fit(train[feats1].to_numpy(np.float32), train["y5"].to_numpy(np.float32))
    gain = pd.Series(model.feature_importance("gain"), index=feats1)
    state_gain = float(gain[[f"state_{s}" for s in STATES]].sum() / gain.sum())
    del train, model

    ret, bench, trad = market_data(store, sessions)
    rows, series, losers = {}, {}, {}
    for name, pred in preds.items():
        daily, extra = portfolio(pred, ret, trad)
        m = summarize(daily, bench, pred.merge(panel[["entity_id", "session", "y5"]], on=["entity_id", "session"]))
        rows[name], series[name] = {**m, **extra}, daily
        merged = pred.merge(panel[["entity_id", "session", "y5"]], on=["entity_id", "session"])
        losers[name] = set_return(session_sets(merged, "pred", 0.10, top=False), merged)

    j0, j1 = rows["J0"], rows["J1"]
    t = nw_t(series["J1"], series["J0"])
    lines = [f"기록: 국면 피처 gain 비중 {state_gain:.1%} · 국면 체류 " +
             " · ".join(f"{s} {share.get(s, 0):.0%}" for s in STATES),
             "기록: 하위 10% h5 z-수익 (전체/박스/급등) — " +
             " · ".join(f"{n} {v['all']:+.3f}/{v['box']:+.3f}/{v['rally']:+.3f}" for n, v in losers.items()),
             "",
             "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IC 전체 | IC 박스/급등 | 비대칭 | 회전 | IR(K200) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in rows.items():
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                     f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['ic']:+.4f} | {m['box_ic']:+.4f}/{m['rally_ic']:+.4f} | "
                     f"{m['asym']:+.3f} | {m['turn']:.1f} | {m['ir']:+.2f} |")
    c = (j1["box_ic"] >= j0["box_ic"] + GATE_IC and j1["rally_ic"] >= j0["rally_ic"] + GATE_IC,
         j1["box_ann"] >= j0["box_ann"] + GATE_ANN and j1["rally_ann"] >= j0["rally_ann"] + GATE_ANN,
         t >= GATE_T, j1["mdd"] >= j0["mdd"] - GATE_MDD)
    lines += ["", f"J1: ①두 국면 IC ({j1['box_ic'] - j0['box_ic']:+.4f}/{j1['rally_ic'] - j0['rally_ic']:+.4f}) {mark(c[0])} · "
                  f"②두 국면 +1%p ({j1['box_ann'] - j0['box_ann']:+.1%}/{j1['rally_ann'] - j0['rally_ann']:+.1%}) {mark(c[1])} · "
                  f"③NW t {t:+.2f} {mark(c[2])} · ④MDD {j1['mdd']:.1%} {mark(c[3])} → {'통과' if all(c) else '탈락'}"]
    verdict = "채택 J1" if all(c) else "기각 — 현행 피처 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save and not args.smoke:
        record(store, entity="ranker-market-state-2026-09:AJ", source="trial_ranker_market_state", family="ranker",
               digest=digest, verdict=verdict, lines=lines)
        print(f"research_trials 기록: ranker/AJ · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
