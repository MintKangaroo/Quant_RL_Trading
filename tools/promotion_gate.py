"""승격 판정 — `rl_evaluations` 의 최신 평가가 §13 조건을 만족하나. rc 0 통과 / 2 불합격.

    .venv/bin/python tools/promotion_gate.py --run <run_id> [--reflection-floor 0.30]

체인이 사람 없이 모의계좌에 정책을 끼우기 전에 마지막으로 서는 자리다. 조건은 문서(§13)에서
그대로 온다: **OOS 에서 균등가중 대비 보상 우위**(verdict generalizes) + **액션 반영률 하한**.
숫자를 지어내지 않는다 — 평가 기록이 없으면 불합격이다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import Store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--root", default="data")
    parser.add_argument("--window", default="oos", help="eval_window 값 (oos | valid)")
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    now = datetime.now(UTC)  # invariant-allow: wallclock
    frame = store.get("rl_evaluations", as_of=now, lookback=30, entity=args.run)
    if frame.empty:
        print(f"불합격 — {args.run} 의 평가 기록이 없다")
        return 2
    frame = frame[(frame["eval_window"] == args.window) & (frame["arm"] == "policy")]
    if frame.empty:
        print(f"불합격 — {args.run} 의 {args.window} 정책 평가가 없다")
        return 2
    row = frame.sort_values(["valid_from", "observed_at"]).iloc[-1]
    floor = float(store.config("allocator.action_reflection_floor", as_of=now))
    verdict = str(row["verdict"])
    gap = float(row["gap_vs_equal"])
    reflection = float(row["action_reflection"])
    ok = verdict == "generalizes" and reflection >= floor
    print(
        f"{args.run} · {args.window} 우위 {gap:+.5f} · 판정 {verdict} · "
        f"반영률 {reflection:.3f}(하한 {floor:.2f}) · 체크포인트 {row['checkpoint']}"
    )
    print("통과 — 모의계좌에 끼울 자격이 있다" if ok else "불합격 — 끼우지 않는다")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
