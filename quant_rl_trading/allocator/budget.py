"""본 학습의 크기 — `train_config()` 와 대시보드가 **같은 숫자**를 읽는 자리.

torch 를 안 부른다. 대시보드가 "1,220 업데이트 중 몇 번째인가" 를 보이려면
총량을 알아야 하는데, 그걸 화면에 따로 적으면 학습 설정과 어긋나는 날이 온다
(CLAUDE.md 불변식 10 의 정신). 그렇다고 대시보드 4개가 torch 를 import 하면
프로세스마다 300MB 가 든다. 그래서 숫자만 여기 둔다 — `rl-training.md §4`.
"""

from __future__ import annotations

TOTAL_TIMESTEPS = 20_000_000
NUM_ENVS = 32
N_STEPS = 512


def total_updates() -> int:
    """업데이트 수 = 총 스텝 // (환경 수 × 롤아웃 길이). `tools/train_rl.py` 와 같은 식."""
    return max(1, TOTAL_TIMESTEPS // (NUM_ENVS * N_STEPS))
