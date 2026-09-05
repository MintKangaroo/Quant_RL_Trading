"""탭 렌더 테스트가 쓰는 응답을 **진짜 창고에서** 다시 뜬다.

    uv run python tools/capture_tab_payloads.py

손으로 지어낸 페이로드는 화면이 실제로 못 받는 모양을 테스트만 통과시킨다.
API 응답 모양을 바꿨으면 이걸 다시 돌리고, 그 diff 를 커밋에 같이 남긴다 —
페이로드가 바뀌었다는 사실이 곧 화면 계약이 바뀌었다는 신호다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from quant_rl_trading.dashboard import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "quant_rl_trading" / "dashboard" / "static"
PAYLOADS = REPO_ROOT / "tests" / "dashboard" / "payloads"

TABS = ("market", "headlines", "system", "learning", "ai_review", "calendar_page")
#: 탭 스크립트보다 먼저 실리는 보조 스크립트 — 거기서 부르는 경로도 잡는다
#: (tests/dashboard/test_tab_render.py 의 preload 와 같은 목록).
EXTRA_SCRIPTS = {"market": ("candles.js",), "headlines": ("schedule.js",), "calendar_page": ("calendar.js",)}
#: 변수로 조립돼 정규식이 못 잡는 경로. 손으로 적는다.
EXTRA_PATHS = {"headlines": ("headlines/schedule",)}


def main() -> int:
    PAYLOADS.mkdir(parents=True, exist_ok=True)
    client = create_app().test_client()

    failures = 0
    for tab in TABS:
        source = "\n".join(
            (STATIC / name).read_text(encoding="utf-8")
            for name in (*EXTRA_SCRIPTS.get(tab, ()), f"{tab}.js")
        )
        paths = sorted(set(re.findall(r'fetchJson\(\s*"([^"]+)"', source)) | set(EXTRA_PATHS.get(tab, ())))
        captured: dict[str, object] = {}
        for path in paths:
            response = client.get(f"/api/{path}")
            if response.status_code != 200:
                print(f"  !! {tab} /api/{path} → {response.status_code}")
                failures += 1
                continue
            captured[path] = response.get_json()
        (PAYLOADS / f"{tab}.json").write_text(
            json.dumps(captured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{tab}: {len(captured)}개 경로")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
