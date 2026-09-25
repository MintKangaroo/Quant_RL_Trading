"""미장 선정 시행 셋 — AU 재조정 주기 → AV 폭 → AW 노출 국면. docs/protocols/us-selection-chain-2026-09.md.

    .venv/bin/python tools/trial_us_selection.py --trial AU|AV|AW [--save]

셋 다 시행 AT 가 채택한 M1 합성(실전 척도) 위에서, 같은 루프 GBM 3시드 캐시(`tools/trial_us_kit`)로 잰다. 앞 시행의 채택 구성이
뒤 시행의 대조가 된다 — 앞 판정은 로그의 "판정:" 줄에서 읽는다(등록 문서가 정한 대로).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_ranker_kit import record  # noqa: E402
from tools.trial_us_kit import book, build, market, scores, spx_regime, summarize  # noqa: E402
from tools.trial_us_missing_fundamental import SEEDS  # noqa: E402

PROTOCOL = Path("docs/protocols/us-selection-chain-2026-09.md")
LOGS = {t: Path(f"logs/trial-us-selection-{t}.log") for t in ("AU", "AV", "AW")}
GATE_MEAN, GATE_REGIME, GATE_MDD, GATE_TURN = 0.02, -0.01, 0.02, 1.2
EXPO_MDD_GAIN, EXPO_ANN_FLOOR = 0.03, -0.01
REGIME_SCALE = {"bull": 1.0, "volatile": 1.0, "bear": 0.7, "crisis": 0.5, "unknown": 1.0}   # 국장 V6 와 같은 값(config exposure.regime_scale)
CONFIRM, CRISIS_FLOOR = 2, -0.03                                                           # exposure.regime_confirm_sessions · crisis_momentum_floor


def adopted(trial: str) -> str | None:
    """앞 시행의 채택 변형 이름. 기각이면 None(대조 유지)."""
    text = LOGS[trial].read_text() if LOGS[trial].exists() else ""
    m = re.findall(r"^판정: 채택 (\S+)", text, flags=re.M)
    if not m and "판정:" not in text:
        raise SystemExit(f"{trial} 판정 전 — 기다린다")
    return m[-1] if m else None


def exposure_path(regimes: pd.Series) -> pd.Series:
    """국면 배수 — 같은 국면이 CONFIRM 세션 이어져야 바꾼다(실전 exposure.regime_scale 확인 기간)."""
    out, cur, streak, last = {}, 1.0, 0, None
    for day, state in regimes.items():
        streak = streak + 1 if state == last else 1
        last = state
        if streak >= CONFIRM:
            cur = REGIME_SCALE.get(state, 1.0)
        out[day] = cur
    return pd.Series(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", required=True, choices=("AU", "AV", "AW"))
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    store = Store(root=Path("data"))
    cost = float(store.config("accounting.fee_us", as_of=datetime.now(UTC)))  # invariant-allow: wallclock — 설정 현행값
    build(store)
    ret, bench = market()
    every, n = 1, 24
    if args.trial in ("AV", "AW"):
        au = adopted("AU")
        every = {"C5": 5, "C10": 10}.get(au or "", 1)
    if args.trial == "AW":
        av = adopted("AV")
        n = 72 if av == "N72" else 24
    print(f"=== 시행 {args.trial} — {PROTOCOL} (해시 {digest}) · 대조 구성: 주기 {every} · 폭 {n} ===", flush=True)

    if args.trial == "AU":
        variants = {"C1": dict(every=1), "C5": dict(every=5), "C10": dict(every=10)}
        control = "C1"
    elif args.trial == "AV":
        variants = {"N24": dict(every=every, n=24, exit_mult=2), "N72": dict(every=every, n=72, exit_mult=2)}
        control = "N24"
    else:
        sessions = sorted(scores(SEEDS[0]).index)
        scale = exposure_path(spx_regime(store, sessions, CRISIS_FLOOR))
        print(f"국면 배수 분포: {scale.value_counts(normalize=True).round(3).to_dict()} · 전환 {int((scale.diff() != 0).sum() - 1)}회", flush=True)
        variants = {"E0": dict(every=every, n=n), "E1": dict(every=every, n=n, scale=scale)}
        control = "E0"

    res: dict[str, dict[int, dict]] = {v: {} for v in variants}
    for s in SEEDS:
        sc = scores(s)
        for v, kw in variants.items():
            daily, extra = book(sc, ret, cost, **kw)
            res[v][s] = summarize(daily, bench, extra)

    def avg(v: str, k: str) -> float:
        return float(np.mean([res[v][s][k] for s in SEEDS]))

    lines = ["| 변형 | 시드 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | 회전 |", "|---|---|---|---|---|---|---|---|---|"]
    for v in variants:
        for s in SEEDS:
            m = res[v][s]
            lines.append(f"| {v} | {s} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                         f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['turn']:.1f} |")
    lines += ["", "| 변형 | 시드 평균 연수익 | 박스 | 급등 | 샤프 | MDD | 회전 |", "|---|---|---|---|---|---|---|"]
    for v in variants:
        lines.append(f"| {v} | {avg(v, 'ann'):+.1%} | {avg(v, 'box_ann'):+.1%} | {avg(v, 'rally_ann'):+.1%} | {avg(v, 'sharpe'):+.2f} | "
                     f"{avg(v, 'mdd'):.1%} | {avg(v, 'turn'):.1f} |")
    lines.append("")
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    passed = []
    for v in variants:
        if v == control:
            continue
        if args.trial == "AW":
            wins = sum(res[v][s]["sharpe"] > res[control][s]["sharpe"] for s in SEEDS)
            c = (avg(v, "mdd") >= avg(control, "mdd") + EXPO_MDD_GAIN, avg(v, "ann") >= avg(control, "ann") + EXPO_ANN_FLOOR,
                 wins == len(SEEDS))
            lines.append(f"{v}: ①MDD {avg(v, 'mdd'):.1%} vs {avg(control, 'mdd'):.1%} (+3%p 이상) {mark(c[0])} · "
                         f"②연수익 {avg(v, 'ann') - avg(control, 'ann'):+.1%}p (≥ −1%p) {mark(c[1])} · ③샤프 {wins}/3 {mark(c[2])}"
                         f" → {'통과' if all(c) else '탈락'}")
        else:
            wins = sum(res[v][s]["ann"] > res[control][s]["ann"] for s in SEEDS)
            c = (avg(v, "ann") >= avg(control, "ann") + GATE_MEAN, wins == len(SEEDS),
                 avg(v, "box_ann") >= avg(control, "box_ann") + GATE_REGIME and avg(v, "rally_ann") >= avg(control, "rally_ann") + GATE_REGIME,
                 avg(v, "mdd") >= avg(control, "mdd") - GATE_MDD, avg(v, "turn") <= avg(control, "turn") * GATE_TURN)
            lines.append(f"{v}: ①평균 {avg(v, 'ann') - avg(control, 'ann'):+.1%}p {mark(c[0])} · ②{wins}/3 {mark(c[1])} · "
                         f"③국면 {avg(v, 'box_ann') - avg(control, 'box_ann'):+.1%}p/{avg(v, 'rally_ann') - avg(control, 'rally_ann'):+.1%}p {mark(c[2])} · "
                         f"④MDD {avg(v, 'mdd'):.1%} vs {avg(control, 'mdd'):.1%} {mark(c[3])} · ⑤회전 {avg(v, 'turn'):.1f} vs {avg(control, 'turn'):.1f} {mark(c[4])}"
                         f" → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((avg(v, "sharpe"), v))
    spy = float(bench.reindex(pd.Index(sorted(scores(SEEDS[0]).index))).mean() * 252)
    lines.append(f"SPY 같은 창 연 {spy:+.1%}")
    verdict = f"채택 {max(passed)[1]}" if passed else f"기각 — {control} 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity=f"us-selection-chain-2026-09:{args.trial}", source="trial_us_selection", family="selection",
               digest=digest, verdict=verdict, lines=lines[-4:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
