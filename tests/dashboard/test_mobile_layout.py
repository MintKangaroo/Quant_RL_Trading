"""모바일 폭 — **아이폰 13 Pro 는 CSS 폭 390px 이다.**

이 저장소에는 헤드리스 브라우저가 없어서 실제 렌더 픽셀을 못 잰다. 대신 CSS 에
**선언된 값**을 읽어 "390px 화면에서 본문이 가로로 밀리는가" 를 산술로 판정한다.
브라우저가 아니라 산술이라 놓치는 것이 있지만, 반대로 **놓치지 않는 것이 하나
있다** — 누가 나중에 고정폭을 다시 넣으면 여기서 걸린다.

## 폭 계산

    뷰포트 390 − main 좌우 패딩 − .panel 자식 좌우 마진 = 패널 안 가용폭

`@media (max-width: 640px)` 에서 패딩·마진을 줄였으므로 모바일 값으로 잰다.

## 무엇을 막는가

1. **본문이 가로로 스크롤되면 안 된다.** 넓은 것은 자기 컨테이너 안에서만
   밀려야 한다 — 페이지가 통째로 흐르면 좌우 배치가 무너진다.
2. **표를 카드로 흩지 않는다.** 이 대시보드의 표는 열끼리 견주는 것이
   요점이라 카드로 바꾸면 못 쓴다. 가로 스크롤 + 첫 열 고정이 처방이다.
3. **데스크톱을 깨뜨리지 않는다.** 좁은 화면 규칙은 미디어쿼리 안에 있거나,
   밖에 있다면 넓은 화면에서 값이 안 바뀌는 형태(`min()`)여야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "quant_rl_trading" / "dashboard" / "static"

#: 아이폰 13 Pro. 이보다 좁은 흔한 기기는 지금 대상이 아니다.
VIEWPORT = 390
#: `@media (max-width: 640px)` 의 값이다 — main 8+8, .panel 자식 8+8.
CONTENT = VIEWPORT - 16
INNER = CONTENT - 16


def _no_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _blocks(text: str) -> list[tuple[str | None, str, str]]:
    """(@media 조건 | None, 선택자, 선언) 목록."""
    text = _no_comments(text)
    out: list[tuple[str | None, str, str]] = []
    media: str | None = None
    depth = 0
    pos = 0
    while True:
        match = re.search(r"([^{}]+)\{", text[pos:])
        if not match:
            return out
        selector = match.group(1).strip()
        start = pos + match.end()
        if selector.startswith("@media"):
            media, depth = selector, 1
            pos = start
            continue
        close = text.find("}", start)
        if close == -1:
            return out
        out.append((media, selector, text[start:close]))
        pos = close + 1
        if depth:
            tail = text[pos:]
            if re.match(r"\s*\}", tail):
                media, depth = None, 0
                pos += tail.index("}") + 1


def _narrow(media: str | None) -> bool:
    """그 규칙이 390px 에서 실제로 걸리는가."""
    if media is None:
        return True
    caps = [int(v) for v in re.findall(r"max-width:\s*(\d+)px", media)]
    mins = [int(v) for v in re.findall(r"min-width:\s*(\d+)px", media)]
    if any(VIEWPORT > cap for cap in caps):
        return False
    return not any(VIEWPORT < floor for floor in mins)


def _sheets() -> list[Path]:
    return sorted(STATIC.glob("*.css"))


@pytest.mark.parametrize("sheet", _sheets(), ids=lambda p: p.name)
def test_390px_에서_본문을_밀어내는_고정폭이_없다(sheet: Path) -> None:
    """고정 ``width``/``min-width`` 가 패널 안 가용폭을 넘으면 본문이 흐른다.

    자기 컨테이너 안에서 미는 것(``overflow-x`` 를 든 요소)은 예외다 —
    그건 의도된 가로 스크롤이다.
    """
    offenders = []
    for media, selector, body in _blocks(sheet.read_text(encoding="utf-8")):
        if not _narrow(media):
            continue
        if re.search(r"overflow-x:\s*(auto|scroll)", body):
            continue
        for prop in ("min-width", "width"):
            for match in re.finditer(rf"(?<![-\w]){prop}:\s*(\d+)px", body):
                if int(match.group(1)) > INNER:
                    offenders.append(f"{sheet.name} {selector} {prop}:{match.group(1)}px")
    assert not offenders, f"390px 에서 {INNER}px 을 넘는다: {offenders}"


@pytest.mark.parametrize("sheet", _sheets(), ids=lambda p: p.name)
def test_390px_에서_그리드가_한_칸으로_접힌다(sheet: Path) -> None:
    """``minmax(420px, 1fr)`` 처럼 최소폭이 고정된 그리드는 화면이 좁아도 그
    폭을 고집해 본문을 밀어낸다. ``min(420px, 100%)`` 로 적거나 미디어쿼리로
    접어야 한다 — 전자는 넓은 화면에서 값이 그대로라 데스크톱을 안 건드린다.
    """
    offenders = []
    for media, selector, body in _blocks(sheet.read_text(encoding="utf-8")):
        if not _narrow(media):
            continue
        match = re.search(r"grid-template-columns:\s*([^;]+)", body)
        if not match:
            continue
        value = match.group(1)
        # min(...) 안의 px 은 상한이지 최소폭이 아니다.
        outside = re.sub(r"min\([^)]*\)", "", value)
        pixels = [int(v) for v in re.findall(r"(\d+)px", outside)]
        if not pixels:
            continue
        repeat = re.search(r"repeat\((\d+)\s*,", value)
        need = min(pixels) * int(repeat.group(1)) if repeat else min(pixels)
        if need > INNER:
            offenders.append(f"{sheet.name} {selector} → 최소 {need}px ({value.strip()[:40]})")
    assert not offenders, f"390px 에서 안 접힌다: {offenders}"


def test_넓은_표는_자기_컨테이너_안에서_민다() -> None:
    """표를 카드로 흩지 않는 대신 스크롤 영역이 가로도 열어야 한다.
    안 그러면 열이 많은 표가 페이지 본문을 통째로 가로로 민다."""
    app = (STATIC / "app.css").read_text(encoding="utf-8")
    # 선택자가 **정확히** ``.scroll`` 인 블록이다. 정규식으로 훑으면
    # ``.panel > .scroll`` 같은 파생 규칙을 잘못 문다.
    bodies = [body for _, selector, body in _blocks(app) if selector == ".scroll"]
    assert bodies, ".scroll 규칙이 없다"
    assert any(re.search(r"overflow-x:\s*(auto|scroll)", body) for body in bodies), (
        ".scroll 이 가로를 안 연다 — 넓은 표가 본문을 밀어낸다"
    )


def test_모바일에서_첫_열이_붙어_있다() -> None:
    """가로로 밀면 종목명이 화면 밖으로 나간다 — 무엇에 대한 줄인지 모르는
    숫자만 남는다. 첫 열을 고정해 그걸 막는다."""
    app = _no_comments((STATIC / "app.css").read_text(encoding="utf-8"))
    narrow = [body for media, _, body in _blocks(app) if media and _narrow(media)]
    joined = "\n".join(narrow)
    assert "position: sticky" in joined and "left: 0" in joined, (
        "모바일에서 표 첫 열이 안 붙어 있다"
    )


def test_모바일_글씨가_바닥_아래로_안_내려간다() -> None:
    """데스크톱에서 10px 로 잡은 라벨이 손에서는 안 읽힌다. **숫자를 줄여
    자리를 만들지 않는다** — 자릿수나 소수점을 자르면 화면이 다른 값을 말한다.
    """
    floor = 10.5
    app = (STATIC / "app.css").read_text(encoding="utf-8")
    mobile = [
        (selector, body)
        for media, selector, body in _blocks(app)
        if media and _narrow(media)
    ]
    sizes = [
        float(m.group(1))
        for _, body in mobile
        for m in re.finditer(r"font-size:\s*([\d.]+)px", body)
    ]
    assert sizes, "모바일 글씨 규칙이 아예 없다"
    assert min(sizes) >= floor, f"모바일 글씨가 {min(sizes)}px 까지 내려간다"


def test_데스크톱_배치는_그대로다() -> None:
    """**추가이지 교체가 아니다.** 미디어쿼리 밖에서 바꾼 것은 넓은 화면에서
    값이 안 변하는 형태여야 한다 — `.grid2` 는 `min(420px, 100%)` 라 1920px
    에서 여전히 420px 이고, `.scroll` 의 `overflow-x` 는 넘칠 때만 뜬다.
    """
    app = (STATIC / "app.css").read_text(encoding="utf-8")
    bodies = [body for _, selector, body in _blocks(app) if selector == ".grid2"]
    assert bodies, ".grid2 규칙이 없다"
    assert any("min(420px, 100%)" in body for body in bodies), (
        ".grid2 의 데스크톱 최소폭 420px 이 사라졌다"
    )
