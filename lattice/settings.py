"""``.env`` 로딩 한 곳.

이 함수는 원래 ``tools/backfill.py`` 에 있었다. 그런데 대시보드가 API 키를
읽어야 하는 순간 문제가 됐다 — 라이브러리(``lattice/``)가 도구(``tools/``)를
import 하는 모양이 되기 때문이다. 그래서 여기로 옮기고 양쪽이 같은 것을 쓴다.

**대시보드가 이걸 안 부르면 조용히 망가진다.** 키가 없어도 화면은 200 을 내고
숫자만 나온다 — 해설이 빠진 것을 아무도 눈치채지 못한다. 실제로 그렇게
한 번 놓쳤다.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """``.env`` 를 환경변수로. 이미 설정된 값은 덮지 않는다.

    의존성을 하나 더 늘리지 않으려고 직접 읽는다. 셸에서 export 한 값이
    파일보다 우선한다 — 일회성 덮어쓰기가 가능해야 한다.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
