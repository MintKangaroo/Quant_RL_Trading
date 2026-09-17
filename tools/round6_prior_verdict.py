"""6차 묶음 하나가 **이미 측정됐는지** 창고에 묻는다. 채택/기각을 찍거나, 없으면 아무것도 안 찍는다.

    .venv/bin/python tools/round6_prior_verdict.py G1

사전등록은 묶음당 1회다. 다시 돌리면 시행 예산을 두 번 쓰면서 "여러 번 재고 좋은 것을
골랐다" 가 된다 — 그 판정은 자기 자신을 못 믿는다. 판단 근거는 로그가 아니라 창고 기록이다.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_ranker_sources import TRIAL_PREFIX  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("묶음 이름 하나를 달라 (예: G1)", file=sys.stderr)
        return 2
    now = datetime.now(UTC)  # invariant-allow: wallclock — 기록 조회 시점
    frame = Store(root=Path("data")).get("research_trials", as_of=now, lookback=400)
    if frame.empty:
        return 0
    rows = frame[frame["entity_id"].astype(str) == f"{TRIAL_PREFIX}:{args[0]}"]
    if rows.empty:
        return 0
    detail = str(rows.sort_values("valid_from").iloc[-1]["detail"])
    print("채택" if "판정: 채택" in detail else "기각")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
