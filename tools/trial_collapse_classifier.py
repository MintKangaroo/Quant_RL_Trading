"""시행 AK — "무너질 종목" 분류기를 배제에 쓴다. docs/protocols/collapse-classifier-2026-09.md.

    .venv/bin/python tools/trial_collapse_classifier.py [--save]

랭커(회귀, J0 과 같다)의 상위 24 를 고르기 전에 배제한다: K0 risk 하위 20%(현행) · K1 분류기 상위 20% · K2 둘 다.
분류기: 같은 파라미터에 objective=binary, 타깃 = y5 rank-gauss < Φ⁻¹(0.10). 진짜 질문은 기록 항목(집합별 h5 z-수익)이다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_ranker_ensemble import FEATS  # noqa: E402
from tools.trial_ranker_kit import (  # noqa: E402
    blocks,
    collapse_threshold,
    judge_panel,
    mark,
    market_data,
    nw_t,
    portfolio,
    record,
    session_sets,
    set_return,
    summarize,
    walk,
)

PROTOCOL = Path("docs/protocols/collapse-classifier-2026-09.md")
EXCLUDE_Q = 0.20
GATE_ANN, GATE_T, GATE_MDD, GATE_TURN = 0.01, 2.0, 0.03, 1.20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선 확인용")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AK — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    panel, sessions = judge_panel()
    panel["collapse"] = (panel["y5"] < collapse_threshold(0.10)).astype(np.float32)
    panel.loc[panel["y5"].isna(), "collapse"] = np.nan
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} · 하위 10% 비율 {panel['collapse'].mean():.1%}", flush=True)
    bl = blocks(sessions)
    if args.smoke:
        bl = bl[: args.smoke]
    ranker = walk(panel, sessions, FEATS, "y5", bl, label="랭커")
    clf = walk(panel, sessions, FEATS, "collapse", bl, objective="binary", label="분류기")

    merged = ranker.merge(panel[["entity_id", "session", "y5", "risk"]], on=["entity_id", "session"])
    merged = merged.merge(clf.rename(columns={"pred": "p_collapse"}), on=["entity_id", "session"])
    sets = {
        "랭커 하위10%": session_sets(merged, "pred", 0.10, top=False),
        "risk 하위20%": session_sets(merged, "risk", EXCLUDE_Q, top=False),
        "분류기 상위10%": session_sets(merged, "p_collapse", 0.10, top=True),
        "분류기 상위20%": session_sets(merged, "p_collapse", EXCLUDE_Q, top=True),
    }
    rets = {k: set_return(v, merged) for k, v in sets.items()}
    overlap = np.mean([len(sets["랭커 하위10%"][d] & sets["분류기 상위10%"][d]) / max(1, len(sets["랭커 하위10%"][d]))
                       for d in sets["랭커 하위10%"] if d in sets["분류기 상위10%"]])
    # AUC(세션 평균) — 분류기가 하위 10% 를 가르는가
    from sklearn.metrics import roc_auc_score
    aucs = [roc_auc_score(p["y5"] < collapse_threshold(0.10), p["p_collapse"])
            for _, p in merged.dropna(subset=["y5"]).groupby("session") if p["y5"].lt(collapse_threshold(0.10)).nunique() == 2]

    ret, bench, trad = market_data(store, sessions)
    excludes = {"K0": sets["risk 하위20%"], "K1": sets["분류기 상위20%"],
                "K2": {d: sets["risk 하위20%"].get(d, set()) | sets["분류기 상위20%"].get(d, set()) for d in sessions}}
    rows, series = {}, {}
    for name, ex in excludes.items():
        daily, extra = portfolio(ranker, ret, trad, exclude=ex)
        rows[name], series[name] = {**summarize(daily, bench), **extra}, daily

    lines = ["기록: 집합별 h5 z-수익 (전체/박스/급등, 낮을수록 패자를 잘 가림) — " +
             " · ".join(f"{k} {v['all']:+.3f}/{v['box']:+.3f}/{v['rally']:+.3f}" for k, v in rets.items()),
             f"기록: 랭커 하위10% ∩ 분류기 상위10% 겹침 {overlap:.0%} · 분류기 AUC(세션 평균) {np.mean(aucs):.3f}",
             "",
             "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | 비대칭 | 회전 | IR(K200) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in rows.items():
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                     f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['asym']:+.3f} | {m['turn']:.1f} | {m['ir']:+.2f} |")
    lines.append("")
    k0 = rows["K0"]
    passed = []
    for name in ("K1", "K2"):
        m = rows[name]
        t = nw_t(series[name], series["K0"])
        c = (m["box_ann"] >= k0["box_ann"] + GATE_ANN and m["rally_ann"] >= k0["rally_ann"] + GATE_ANN,
             t >= GATE_T, m["mdd"] >= k0["mdd"] - GATE_MDD, m["turn"] <= k0["turn"] * GATE_TURN)
        lines.append(f"{name}: ①두 국면 +1%p ({m['box_ann'] - k0['box_ann']:+.1%}/{m['rally_ann'] - k0['rally_ann']:+.1%}) {mark(c[0])} · "
                     f"②NW t {t:+.2f} {mark(c[1])} · ③MDD {m['mdd']:.1%} {mark(c[2])} · ④회전 {m['turn']:.1f} vs {k0['turn']:.1f} {mark(c[3])} "
                     f"→ {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["sharpe"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행 위험 하한 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save and not args.smoke:
        record(store, entity="collapse-classifier-2026-09:AK", source="trial_collapse_classifier", family="ranker",
               digest=digest, verdict=verdict, lines=lines)
        print(f"research_trials 기록: ranker/AK · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
