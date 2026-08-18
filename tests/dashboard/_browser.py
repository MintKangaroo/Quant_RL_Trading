"""JS 하네스가 공유하는 브라우저 API 조각.

## 왜 따로 두나

`test_market_render` · `test_trading_render` · `test_tab_render` 이 각자
하네스를 갖고 있다. DOM 스텁은 화면마다 필요한 것이 달라서 갈라져 있는 게
맞지만, **`getComputedStyle` 은 셋 다 똑같이 필요하다.**

실제로 그래서 깨졌다(2026-08-18). `scope.js` 의 `COLOR` 를 CSS 변수에서
읽도록 바꾸자 — JS 에 리터럴 hex 를 안 두려고 — 모듈 최상단에서
`getComputedStyle` 을 부르게 됐고, 그게 없는 하네스에서 `ReferenceError` 로
파일 전체가 죽었다. 한 곳을 고쳐도 나머지 둘이 같은 이유로 계속 죽는다.

## 값을 지어내지 않는다

`app.css` 의 `:root` 를 실제로 파싱한다. 가짜 색을 돌려주면 **JS 가 CSS 에
없는 토큰을 부르는 결함**을 테스트가 못 잡는다 — 브라우저에서 그건 빈
문자열이 되어 색이 조용히 사라진다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "quant_rl_trading" / "dashboard" / "static"


def css_tokens() -> dict[str, str]:
    """``app.css`` 의 ``:root`` 에서 CSS 변수를 그대로 읽는다."""
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    body = re.search(r":root\s*\{(.*?)\}", css, re.S)
    if not body:
        return {}
    return {
        f"--{name}": value.strip()
        for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body.group(1))
    }


def style_shim() -> str:
    """하네스에 붙일 JS 조각. ``document`` 선언 **뒤에** 이어 붙여야 한다."""
    return (
        "\nconst __TOKENS = " + json.dumps(css_tokens()) + ";\n"
        "global.getComputedStyle = () => ({\n"
        "  getPropertyValue: (name) => __TOKENS[name] || \"\",\n"
        "});\n"
    )
