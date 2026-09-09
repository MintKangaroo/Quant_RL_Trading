"""구형 RL 승격 경로 차단 — rc=2는 승격 미지원이며 성과 판정이 아니다.

    .venv/bin/python tools/promotion_gate.py --run <run_id>

기존 reward/균등가중 기록으로 Champion 대비 순성과나 승격 파일의 동일성을
입증할 수 없다. 새 검증 계약이 구현될 때까지 PASS를 발급하지 않는다.
설계·재개 조건: docs/design/rl-training.md §13, docs/rl-postmortem.md §10.
이 명령은 평가를 실행하거나 창고의 OOS 기록을 읽지 않는다.
"""
from __future__ import annotations

import argparse

BLOCK_REASON = (
    "승격 미지원 — 구형 reward/균등가중 평가는 Champion 대비 비용 후 성과·"
    "여러 학습 seed·체크포인트 동일성을 증명하지 못한다. "
    "재개 조건과 검증 계약은 docs/design/rl-training.md §13을 따른다. "
    "--off(해제)와 --dry-run(설정 변경 없는 점검)은 사용할 수 있다."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--root", default="data", help="구형 호출 호환용; 창고를 열지 않는다")
    parser.add_argument("--window", default="oos", help="구형 호출 호환용; 평가를 실행하지 않는다")
    args = parser.parse_args(argv)

    print(f"{args.run} · {BLOCK_REASON}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
