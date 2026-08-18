"""브리핑 → 이메일 HTML · 텍스트.

## 이 메일은 아이폰에서 읽힌다

아이폰 13 Pro, **CSS 폭 390px**. 그게 전부다. 데스크톱에서 잘 보이는 표는
여기서 가로로 넘치거나 글씨가 찌그러진다. 그래서 설계 규칙이 셋이다.

1. **고정 픽셀 폭을 쓰지 않는다.** 폭은 언제나 ``100%`` 이거나 지정하지
   않는다. ``width:600px`` 한 줄이면 390px 화면에서 가로 스크롤이 생긴다
2. **본문 16px 이상.** 그 아래는 아이폰 Mail 이 자동 확대하면서 레이아웃을
   제멋대로 바꾼다. 각주만 13px 로 내린다
3. **한 줄 요약이 맨 위에 있다.** 그 한 줄만 읽고 창을 닫아도 그날 시장을
   안 것이 되어야 한다. 나머지는 그 한 줄의 근거다

## 메일 클라이언트는 브라우저가 아니다

- ``<style>`` 블록·flex·grid 는 통째로 무시되거나 잘린다. **인라인 스타일과
  ``<table>`` 레이아웃만 쓴다.** 이 파일에 flex 도 grid 도 없는 이유다
- 외부 이미지·웹폰트는 차단된다. 색·굵기·여백으로만 만든다
- Gmail 은 본문이 크면 잘라낸다(clipping)

## 다크 배경 — prefers-color-scheme 을 믿지 않는다

사용자가 아이폰 Mail 을 다크 모드로 읽는다. **``prefers-color-scheme`` 만
믿으면 안 된다** — 클라이언트가 무시하거나, iOS Mail 이 라이트로 짠 메일의
색을 제멋대로 반전시켜 예측 못 할 조합을 만든다(실측 사고 사례). 그래서
미디어쿼리에 기대지 않고 **배경·글자를 처음부터 인라인 다크 값으로 못 박는다.**
배경만 정하고 글자색을 안 정하면(또는 그 반대면) 검은 바탕에 검은 글씨가
된다 — **글자색을 주는 곳에는 배경색도 함께 준다.** ``_cell``·``_band`` 가
그 짝을 강제한다.

## 이 메일은 아이폰 기본 메일 기준으로 설계한다

배경이 흰색으로 보인다는 신고가 있었다 — 처음엔 "배경 선언이 어딘가
빠졌다" 로 의심하고 모든 ``<table>``/``<td>`` 에 ``bgcolor`` 속성을
덧대는 시도를 했지만 **틀린 진단이었다.** 실측(2026-08-15, 실기기 스크린샷
비교)으로 밝혀진 사실:

- **아이폰 기본 메일 앱** — 이 파일이 만드는 그대로 나온다(``#0e0f11``
  배경·``#1c1e21`` 카드·밝은 글자·앰버 결측 배지·빨강 상승색). **의도대로다**
- **Gmail 앱** — 명도만 뒤집히고 색상(hue)은 보존된다(``#0e0f11`` 배경이
  밝은 회색으로, 밝은 글자가 검정으로. 상승색 빨강은 빨강 그대로). 이건
  배경 선언이 벗겨진 게 아니라 **Gmail 이 "라이트로 디자인됐다" 고 가정하고
  자기 다크모드 변환을 적용한 것**이다 — 이미 어둡게 만들어 놓은 색을
  다시 뒤집으니 밝아진다

**Gmail 의 이 변환은 인라인 스타일로 못 막는다.** ``bgcolor`` 속성을 아무리
두껍게 깔아도 소용없다 — 벗겨내는 게 아니라 값을 계산해서 바꾸는 것이라,
더 확실히 선언할수록 더 확실하게 뒤집힌다. 그래서 ``bgcolor`` 하드닝과
``color-scheme`` 메타 조정은 시도했다가 **되돌렸다** — Gmail 을 이기려는
시도는 이 메일의 문제가 아닌 것을 고치려는 시도였다.

``<html>`` 배경(아래 ``render_html`` 의 ``<html style="background-color:
{CANVAS}">``)은 **되돌리지 않고 남겼다** — 이건 성격이 다른 방어다. Gmail
의 명도 반전과는 무관하고, 일부 클라이언트가 ``<body>`` 태그 자체를 벗기는
것(색을 바꾸는 게 아니라 태그를 들어내는 것)에 대한 방어선이다. 이미 덮인
자리 뒤에 같은 색을 한 겹 더 까는 것뿐이라 **아이폰 메일 렌더가 한 픽셀도
안 바뀐다** — 그래서 되돌릴 이유가 없다. ``tools/check_email_dark.py`` 의
body-제거 시뮬레이션이 정확히 이 방어선을 검증한다.

**사용자가 Gmail 대신 아이폰 기본 메일로 읽기로 했다.** 그래서 이 파일은
아이폰 기본 메일 기준으로 설계하고 검증한다. Gmail 에서 다시 이상하다는
얘기가 나오면 "왜 이상하지" 부터 반나절 쓰지 말고 이 절부터 읽을 것 —
원인은 이미 안다.

## 손익 색 — 어두운 바탕에서 특히 조심

**어두운 바탕에서 빨강이 탁해진다.** 라이트용 빨강(``#c0271c``)을 그대로
쓰면 카드 배경(``#1c1e21``)과 명도차가 부족해 상승·하락이 눈에 잘 안
갈린다. 그래서 ``UP``·``DOWN`` 은 다크 배경 전용으로 밝힌 값을 쓴다 —
라이트용 팔레트에서 그대로 가져오지 않는다.

## 구분선 — 다크 배경에서는 더 잘 묻는다

라이트 배경의 옅은 회색 선(``#dcdfe3``)을 다크에 그대로 쓰면 카드 배경과
거의 안 갈린다. ``RULE`` 은 다크 카드 배경 위에서도 보이도록 명도를 올린 값이다.

## 색을 칠하지 않는 자리

**변동성 지수에는 손익 색을 쓰지 않는다.** VIX 가 오른 것은 수익이 아니라
공포다. **빈칸도 칠하지 않는다** — 값이 없으면 0 이 아니라 ``—`` 다.
``0.00%`` 는 "보합" 이라는 다른 사실이다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from quant_rl_trading.collectors.market_hours import Market, is_trading_day
from quant_rl_trading.reporting.briefing import (
    MARKET_ORDER,
    Briefing,
    IndexRow,
    MacroRow,
    MacroSection,
    MarketBrief,
    NewsRow,
    NewsSection,
    Ranking,
)
from quant_rl_trading.reporting.sessions import MISSING, UNPUBLISHED, Gap

KST = ZoneInfo("Asia/Seoul")

MARKET_LABEL = {"KR": "국장", "US": "미장"}

#: 색은 **배경과 짝으로만** 쓴다 (모듈 독스트링 다크 모드 참고).
#: 아래는 전부 다크 카드 배경(``PAPER``) 위에서 실측으로 고른 값이다 —
#: 라이트 팔레트를 어둡게 "보정" 한 것이 아니라 다크 전용으로 다시 골랐다.
UP = "#ff6659"         # 국내 관행 — 상승이 빨강. 탁해지지 않게 밝은 코랄레드
DOWN = "#5b9dff"       # 하락이 파랑. 밝은 하늘색 — UP 과 명도가 비슷하게
INK = "#e8eaed"        # 본문 글자
SOFT = "#9aa0a6"       # 보조 글자
FOOTNOTE = "#8a8f96"   # 각주
PAPER = "#1c1e21"      # 카드 바탕
CANVAS = "#0e0f11"     # 바깥 바탕 — 카드보다 한 단 더 어둡게, 카드 경계가 보이게
RULE = "#4a4e54"       # 구분선 — 카드 배경과 명도차를 충분히 둔다
WARN_INK = "#ffca7a"
WARN_BG = "#3d2e10"

#: 본문 최소 크기. 아이폰 Mail 은 이보다 작은 글씨를 만나면 자동 확대하면서
#: 레이아웃을 다시 짠다 — 표가 그때 무너진다.
BODY = 16
#: 각주·표 머리글. **이건 곁다리가 아니다** — 위의 숫자를 어떻게 읽어야
#: 하는지 알려주는 글이라(무엇으로 줄 세웠나, 어떤 하한이 걸렸나, 어느 날
#: 대비인가), 안 읽히면 숫자를 잘못 읽는다. 본문(16px)보다 한 단계 작을 뿐
#: 그 아래로는 안 내린다.
SMALL = 15


def _pct(value: float | None) -> str:
    """등락률. **None 은 ``—`` 다** — 0.00% 로 채우면 "보합" 이라는 다른 사실이 된다."""
    return "—" if value is None else f"{value * 100:+.2f}%"


def _moved(value: float | None) -> str:
    """화살표를 쓰는 자리의 등락률. **부호를 겹쳐 쓰지 않는다** — 화살표가
    이미 방향을 말하는데 ``+`` 까지 붙이면 "▲+3.56%" 가 된다."""
    if value is None:
        return "—"
    return f"{_arrow(value)}{abs(value) * 100:.2f}%"


def _num(value: float | None, *, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _money(value: float | None, currency: str) -> str:
    """금액. 좁은 화면이라 자릿수를 접는다 — 원은 억/조, 달러는 M/B."""
    if value is None:
        return "—"
    if currency == "KRW":
        if abs(value) >= 1e12:
            return f"{value / 1e12:,.1f}조"
        return f"{value / 1e8:,.0f}억"
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.1f}B"
    return f"${value / 1e6:,.0f}M"


def _price(value: float, currency: str) -> str:
    """원과 달러를 같은 숫자로 적으면 "$1" 이 "1원" 으로 읽힌다."""
    return f"{value:,.0f}원" if currency == "KRW" else f"${value:,.2f}"


def _unit(raw: str) -> str:
    """값과 단위 사이 간격. 붙여 쓰면 "119.772020=100" 같은 줄이 나온다.

    **HTML 과 텍스트가 같은 함수를 쓴다** — 두 벌로 두면 언젠가 갈린다.
    """
    return raw if raw in ("%", "") else f" {raw}"


def _color(change: float | None, kind: str = "price") -> str:
    if kind == "volatility" or change is None or change == 0:
        return INK
    return UP if change > 0 else DOWN


def _arrow(change: float | None) -> str:
    if change is None or change == 0:
        return ""
    return "▲" if change > 0 else "▼"


# -- 조각 ----------------------------------------------------------------------


def _cell(
    content: str,
    *,
    color: str = INK,
    align: str = "left",
    size: int = BODY,
    weight: int = 400,
    pad: str = "7px 4px",
    wrap: bool = True,
    extra: str = "",
) -> str:
    """표 칸 하나. **글자색과 배경색을 언제나 함께 준다** (다크 모드)."""
    nowrap = "" if wrap else "white-space:nowrap;"
    return (
        f'<td style="padding:{pad};background-color:{PAPER};color:{color};'
        f"font-size:{size}px;font-weight:{weight};text-align:{align};"
        f'line-height:1.35;{nowrap}{extra}">{content}</td>'
    )


def _foot(text: str) -> str:
    """각주 한 줄. 밝혀야 할 것들이 사는 자리다 — 본문에서 뺀 게 아니라 내린 것."""
    return (
        f'<div style="background-color:{PAPER};color:{FOOTNOTE};font-size:{SMALL}px;'
        f'line-height:1.5;padding:5px 4px 0">{text}</div>'
    )


def _band(text: str, *, ink: str, bg: str, size: int = SMALL) -> str:
    """색 배경 위의 한 줄. 배경과 글자를 짝으로 준다."""
    return (
        f'<div style="background-color:{bg};color:{ink};font-size:{size}px;'
        f'line-height:1.4;padding:7px 10px;border-radius:7px;margin:8px 0">{text}</div>'
    )


def _rule() -> str:
    return f'<div style="border-top:1px solid {RULE};margin:12px 0 0"></div>'


def _section(text: str, *, sub: str = "") -> str:
    tail = (
        f'<span style="color:{SOFT};font-size:{SMALL}px;font-weight:400"> {sub}</span>'
        if sub
        else ""
    )
    return (
        f'<div style="background-color:{PAPER};color:{INK};font-size:18px;'
        f'font-weight:700;padding:12px 0 5px">{text}{tail}</div>'
    )


def _grid(rows: str) -> str:
    """표 하나. **폭을 픽셀로 고정하지 않는다** — 390px 화면에서 넘친다."""
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;border-collapse:collapse;background-color:{PAPER}">'
        f"{rows}</table>"
    )


# -- 한 줄 요약 -------------------------------------------------------------------


def _is_closed(market: str, report_day: date | None) -> bool:
    """이 시장이 **브리핑 기준일에** 휴장이었나.

    휴장이면 그 시장 칸도 헤드라인도 숫자를 싣지 않는다 (``_market_block``).
    직전 거래일 값을 늘어놓으면 그날 장이 선 것처럼 읽히기 때문이다.

    시장을 가리지 않는다 — 미장이 쉬는 날(독립기념일·추수감사절)에는 미장이
    이 규칙에 걸린다. ``MARKET_ORDER`` 와 같은 이유로, 시장 이름이 박힌
    분기를 만들지 않는다.

    한때 머리글에 ``국장 2026-08-14 · 08-17 휴장`` 처럼 **날짜와 휴장 표시를
    나란히** 두고 숫자는 그대로 실었다. 그 화면은 "쉬었다" 를 말하면서 동시에
    쉰 날의 시세를 보여줘서, 읽는 사람이 숫자 쪽을 믿었다.
    """
    if report_day is None:
        return False
    return not is_trading_day(Market(market), report_day)


def _headline_parts(briefing: Briefing) -> list[tuple[str, float | None]]:
    """헤드라인 조각들. ``(문장, 색을 정하는 등락)``.

    **텍스트본과 HTML 본이 같은 목록을 읽는다.** 두 벌로 두면 언젠가 갈리고,
    그때 제목 줄과 본문 첫 줄이 서로 다른 말을 한다.

    등락이 ``None`` 인 조각은 색을 안 입힌다:

    - ``"코스피 미수집"`` — 방향이 없다. 없는 것을 칠하면 있는 것처럼 보인다
    - ``"환율 1,410 ▼0.09%"`` — **환율은 손익 방향이 아니다.** 원/달러가
      오른 것은 이익도 손실도 아니라 원화가 약해진 것이다. 변동성 지수에
      손익 색을 안 쓰는 것과 같은 이유다 (모듈 독스트링)
    """
    parts: list[tuple[str, float | None]] = []
    report_day = report_date(briefing)
    for code in MARKET_ORDER:
        brief = briefing.markets.get(code)
        if brief is None or not brief.prices:
            continue
        if _is_closed(code, report_day):
            # 직전 거래일 등락을 여기 실으면 그날 장이 선 것처럼 읽힌다.
            # **빼지도 않는다** — 조용히 사라지면 수집 실패와 구별되지 않는다.
            parts.append((f"{MARKET_LABEL.get(code, code)} 휴장", None))
            continue
        head = brief.prices[0]
        if head.close is None:
            parts.append((f"{head.label} 미수집", None))
        else:
            parts.append((f"{head.label} {_moved(head.change)}", head.change))
    rate = briefing.fx
    if rate.get("rate") is not None:
        parts.append((f"환율 {rate['rate']:,.0f} {_moved(rate['change'])}", None))
    return parts


def headline(briefing: Briefing) -> str:
    """맨 위 한 줄. **이 줄만 읽고 닫아도 그날을 안 것이 되게.**

    문장을 지어내지 않는다 — 대표 지수 둘과 환율을 사실 그대로 잇는다.
    "반도체가 시장을 끌었다" 같은 해석은 근거가 창고에 없다.

    **LLM 요약을 이 위에 얹어 본 적이 있다(2026-08-18, 되돌림).** 실제로
    붙여 보니 문장이 자료를 넘어섰다 — 거래대금 상위 목록을 근거로
    "반도체주가 강세를 이끌었다" 를 썼는데, 종목 등락과 지수 등락은 창고에
    있어도 **그 둘을 잇는 인과는 창고에 없다.** 프롬프트를 조여 인과를
    막고 나면 남는 것은 이 줄이 이미 하는 일(숫자를 나란히 적는 것)이라,
    돈과 실패 경로를 더한 값이 아니었다. 다시 제안이 나오면 여기서부터
    시작하면 된다.

    여기는 **글자만**이다. 메일 제목에도 그대로 들어가서, 태그를 섞으면
    받은편지함에 ``<span style=...>`` 이 찍힌다.
    """
    parts = _headline_parts(briefing)
    return " · ".join(text for text, _ in parts) if parts else "지수가 들어오지 않았다"


def _headline_block(briefing: Briefing) -> str:
    """요약 한 줄. **조각마다 자기 방향으로 칠한다.**

    줄 전체를 대표 지수 하나의 색으로 칠하던 때가 있었다. 그때 나간 메일이
    이랬다 — ``코스피 ▲2.42% · S&P 500 ▼0.17% · 환율 1,410`` 이 통째로
    빨강. **내린 S&P 500 까지 상승색이었다.** 이 줄은 한 눈에 읽히라고 만든
    자리라, 색이 사실과 반대면 만든 목적을 정면으로 깬다.

    색은 본문 표와 **같은 ``_color``** 를 쓴다. 헤드라인만 다른 빨강·파랑이면
    같은 사실이 두 색으로 보여서 더 헷갈린다.
    """
    parts = _headline_parts(briefing)
    body = (
        " · ".join(
            f'<span style="color:{_color(change)}">{text}</span>' for text, change in parts
        )
        if parts
        else "지수가 들어오지 않았다"
    )
    # 바탕 글자색은 남긴다 — 색 없는 조각과 조각 사이 가운뎃점이 여기서 색을 받는다.
    return (
        f'<div style="background-color:{PAPER};color:{INK};font-size:19px;'
        f'font-weight:800;line-height:1.35;padding:2px 0 0">{body}</div>'
    )


# -- 지수 ----------------------------------------------------------------------


def _index_rows(rows: list[IndexRow], *, volatility: bool) -> str:
    """지수 표. 좌우 두 칸이라 **어떤 폭에서도 안 넘친다.**

    이름은 접히게 두고(``wrap``) 숫자만 안 접는다. 반대로 하면 긴 이름이
    숫자를 화면 밖으로 밀어낸다.
    """
    body = ""
    for row in rows:
        kind = "volatility" if volatility else row.kind
        if row.close is None:
            body += (
                "<tr>"
                + _cell(row.label, color=SOFT, size=BODY)
                + _cell(
                    f'<span style="color:{WARN_INK}">{row.note or "값 없음"}</span>',
                    align="right",
                    size=SMALL,
                )
                + "</tr>"
            )
            continue
        mark = (
            f'<span style="color:{WARN_INK};font-size:{SMALL}px"> *</span>'
            if row.note
            else ""
        )
        value = (
            f'<span style="color:{INK};font-weight:700">{_num(row.close)}</span>'
            f'<span style="color:{_color(row.change, kind)};font-weight:700"> '
            f"{_moved(row.change)}</span>"
        )
        body += (
            "<tr>"
            + _cell(f"{row.label}{mark}", color=SOFT)
            + _cell(value, align="right", size=17, wrap=False)
            + "</tr>"
        )
    return _grid(body)


# -- 순위 ----------------------------------------------------------------------

#: 순위표 값의 제목과 서식. **무엇으로 줄 세웠는지가 열 제목에 그대로 나온다.**
_METRIC: dict[str, tuple[str, Callable[[float, str], str]]] = {
    "value": ("거래대금", _money),
    "market_cap": ("시가총액", _money),
}


def _when(day: date | None) -> str:
    """열 제목에 붙는 세션 표시. 줄을 갈아 끼워 넓이를 안 먹는다."""
    return f'<br><span style="white-space:nowrap">({day.strftime("%m-%d")})</span>'


def _ranking_rows(rank: Ranking, currency: str) -> str:
    """순위 세 줄. 종목명은 접히고 숫자는 안 접힌다.

    **두 열의 세션이 다르면 열마다 날짜를 밝힌다.** 순위는 ``market_stats``
    의 시총으로 매기는데 그 테이블이 ``prices`` 보다 늦게 들어와서, 실측
    2026-08-18 기준으로 순위는 08-11 시총이고 등락률은 08-14 시세였다.
    사흘이 한 줄 안에 섞여 있고 아무 표시가 없으면 둘 다 08-11 로 읽힌다.

    값을 맞추지 않는다 — 등락률을 08-11 기준으로 되돌리면 표는 일관되지만
    "어제 시장이 어땠나" 를 못 보게 되어 **정보가 준다.** 모르는 것을
    지어내지 않고, 아는 것이 정확히 어느 시점의 것인지 말한다.

    **같으면 반복하지 않는다.** 수집이 정상화되면 두 날짜가 같아지는데,
    그때도 ``(08-14)`` 를 두 번 적으면 시끄럽다. 이 메일의 규약대로 —
    어긋난 것만 눈에 띈다.
    """
    label, fmt = _METRIC[rank.key]
    split = (
        rank.change_session is not None
        and rank.session is not None
        and rank.change_session != rank.session
    )
    metric_when = _when(rank.session) if split else ""
    change_when = _when(rank.change_session) if split else ""
    header = (
        "<tr>"
        + _cell("종목", color=FOOTNOTE, size=SMALL, pad="0 4px 4px")
        + _cell(
            f"{label}{metric_when}",
            color=FOOTNOTE, size=SMALL, align="right", pad="0 4px 4px",
        )
        + _cell(
            f"전일대비{change_when}",
            color=FOOTNOTE, size=SMALL, align="right", pad="0 4px 4px",
        )
        + "</tr>"
    )
    body = ""
    for index, row in enumerate(rank.rows, start=1):
        name = (
            f'<span style="color:{FOOTNOTE};font-weight:400">{index}</span> '
            f'<span style="color:{INK};font-weight:600">{row.name}</span>'
        )
        body += (
            "<tr>"
            + _cell(name, extra=f"border-top:1px solid {RULE}")
            + _cell(
                fmt(row.metric, currency),
                align="right",
                weight=700,
                wrap=False,
                extra=f"border-top:1px solid {RULE}",
            )
            + _cell(
                _pct(row.change),
                color=_color(row.change),
                align="right",
                weight=700,
                wrap=False,
                extra=f"border-top:1px solid {RULE}",
            )
            + "</tr>"
        )
    return _grid(header + body)


def _ranking_block(rank: Ranking, brief: MarketBrief) -> str:
    when = rank.session.isoformat()[5:] if rank.session else "세션 미확인"
    if not rank.rows:
        return _section(rank.label, sub=when) + _foot(rank.note or "해당 없음")

    floor = brief.floor
    notes = [f"정렬 {rank.sort_by}"]
    if rank.key == "market_cap":
        # **0~1 의 비율이다** — 백분율이 아니다. ``:.0%`` 가 100 을 곱하므로
        # 여기 백분율을 담으면 두 번 곱해진다. 이름을 ``covered`` 라고만 둬서
        # 어느 쪽인지 알 수 없던 때 실제로 사고가 났다 (커버리지 43450%).
        # 1 을 넘지 않는 것은 ``Ranking.universe`` 가 구조적으로 보장한다 —
        # 여기서 clamp 하지 않는다. 잘라내면 다음 사고가 100% 로 위장한다.
        coverage_ratio = rank.eligible / rank.universe if rank.universe else 0.0
        notes.append(
            f"시총 아는 {rank.eligible:,}종목 중 (커버리지 {coverage_ratio:.0%}"
            + (" · ADR·ETF 는 주식수가 없어 빠진다)" if brief.market == "US" else ")")
        )
    else:
        notes.append(
            f"하한 {_money(floor.min_turnover, floor.currency)}+ · "
            f"{_price(floor.min_price, floor.currency)}+ "
            f"({rank.universe:,}→{rank.eligible:,})"
        )
    if rank.prior:
        # 두 세션이 다르면 "무엇이 무엇 대비인지" 를 각주가 마저 말한다 —
        # 열 제목의 (08-14) 만으로는 그 등락의 기준일이 안 보인다.
        base = rank.change_session or rank.session
        if base is not None and base != rank.session:
            notes.append(f"전일대비 = {base.isoformat()} 의 {rank.prior.isoformat()} 대비")
        else:
            notes.append(f"전일대비 = {rank.prior.isoformat()} 대비")
    if rank.note:
        notes.append(f'<span style="color:{WARN_INK}">{rank.note}</span>')
    return _section(rank.label, sub=when) + _ranking_rows(rank, brief.currency) + _foot(
        " · ".join(notes)
    )


# -- 뉴스 ----------------------------------------------------------------------
#
# **공시(dart)가 아니라 기사(newsapi)다.** 공시는 "무슨 일이 처리됐나" 고
# 기사는 "무슨 일이 벌어지고 있나" 라, 아침에 읽고 싶은 쪽은 후자다 —
# ``briefing.NEWS_SOURCE`` 참고. 국장·미장 각각 한 칸이고, 미장은 창고에
# 수집된 기사가 아직 없어(``briefing.news_section`` docstring) 없으면
# 없다고 적을 뿐 코드를 더 손댈 자리는 아니다 — 수집기가 돌기 시작하면
# ``news.rows`` 가 채워지고 이 함수는 그대로 그린다.


def _news_row(row: NewsRow) -> str:
    """뉴스 한 줄. ``title_ko`` 가 있으면(미장) 번역을 앞세우고 **원문을
    작게 함께 보여준다** — 번역만 남기면 오역을 검증할 길이 없다."""
    when = row.published_on.isoformat()[5:] if row.published_on else ""
    shown = row.title_ko or row.title
    title = (
        f'<a href="{row.url}" style="color:{INK};text-decoration:none">{shown}</a>'
        if row.url
        else shown
    )
    original = (
        f'<br><span style="color:{FOOTNOTE};font-size:{SMALL}px">{row.title}</span>'
        if row.title_ko
        else ""
    )
    return (
        "<tr>"
        + _cell(
            f'<span style="color:{SOFT};font-size:{SMALL}px">{row.reason}'
            + (f" · {when}" if when else "")
            + f'</span><br><span style="color:{INK};font-weight:600">{row.name}</span> '
            + f"{title}{original}",
            extra=f"border-top:1px solid {RULE}",
        )
        + "</tr>"
    )


def _news_block(news: NewsSection) -> str:
    if not news.rows:
        return _section("뉴스") + _foot(f"{news.note or '해당 없음'} · {news.criteria}")
    body = "".join(_news_row(row) for row in news.rows)
    translated = any(row.title_ko for row in news.rows)
    selection = (
        "규칙 선별(LLM 미사용) · 제목 번역은 Claude" if translated else "규칙 선별(LLM 미사용)"
    )
    return (
        _section("뉴스")
        + _grid(body)
        + _foot(f"{news.total:,}건 중 {len(news.rows)}건 · {news.criteria} · {selection}")
    )


# -- 거시 ----------------------------------------------------------------------


#: 거시 지표명 최대 길이. 우리가 붙인 한글 이름이라 원래도 짧지만, 상한이
#: 없으면 언젠가 긴 이름 하나가 칸을 밀어낸다. 자른 것은 말줄임표가 말한다.
#:
#: 예전 값은 26 이었다. 그때는 지표명이 좁은 왼쪽 칸에 갇혀 있었는데
#: (``_macro_block`` 주석), 지금은 한 줄을 통째로 쓴다.
MACRO_LABEL_MAX = 34

#: 원제(``source_name``) 최대 길이. 한글 이름 아래 부제로 싣는다 — 가공한
#: 숫자를 검증할 수 있게 원제를 버리지 않는다는 것이 이 부제의 존재 이유다.
#: "Advance Monthly Sales for Retail and Food Services" 가 여기 걸린다.
MACRO_SOURCE_MAX = 46

#: 값 자체가 "백만 달러" 인 단위. 763,602 를 그대로 찍으면 크기가 안 잡힌다
#: — ``_money`` 로 억/조·B/M 표기로 접는다. ``briefing.PERCENT_UNITS`` 처럼
#: 여기도 명단은 하나뿐이다.
MILLION_USD_UNITS = frozenset({"mn_usd"})


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _macro_actual(value: float | None) -> str:
    """큰 값의 소수점 두 자리는 화면만 먹는다 — 763,602.00 → 763,602."""
    if value is None:
        return "—"
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}"


def _macro_value(value: float | None, unit: str) -> str:
    """읽을 수 있는 단위로. 763,602 mn_usd 는 "얼마나 큰지 감이 안 온다" —
    억·B 표기로 접는다. 원값은 버리지 않는다 — 괄호로 남긴다."""
    if value is None:
        return "—"
    if unit in MILLION_USD_UNITS:
        return f"{_money(value * 1e6, 'USD')} ({_macro_actual(value)}{_unit(unit)})"
    return f"{_macro_actual(value)}{_unit(unit)}"


def _macro_change(row: MacroRow) -> str:
    """직전 대비. **방향(화살표)과 크기를 사람이 직접 빼지 않게 미리 계산한다.**

    퍼센트 단위 지표는 ``row.change`` 가 이미 %p 차이라 100 을 또 곱하지
    않는다 — 곱하면 금리 +0.13%p 가 "+13.00%p" 가 된다.
    """
    if row.change is None:
        return "—"
    magnitude = abs(row.change) if row.is_percent else abs(row.change) * 100
    return f"{_arrow(row.change)}{magnitude:.2f}{row.change_unit}"


def _macro_block(macro: MacroSection) -> str:
    """거시지표. **좌우 두 칸으로 나누지 않는다** — 390px 에서 무너진다.

    무너지는 방식이 눈에 안 띄어서 오래 나갔다. 값 칸에 ``white-space:nowrap``
    이 걸려 있는데 그 안의 문자열이 ``$763.6B (763,602 mn_usd)`` 처럼 길다.
    표는 그 칸에 필요한 폭을 먼저 주고 남은 것을 왼쪽에 준다 — 아이폰 세로에서
    왼쪽에 남는 것이 60px 남짓이라 원제가 한 단어씩 세로로 쪼개졌다:

        Advance / Monthly / Sales / for / Retail... / 08-14 / 21:30 / KST

    메일 클라이언트라 미디어쿼리·flex 로 고칠 수 없다. 그래서 **한 칸에
    위아래로 쌓는다** — 칸이 하나면 나눠 가질 폭이 없어서 좁은 화면에서
    무너질 자리 자체가 없다. 넓은 화면에서는 지표 하나가 네 줄을 쓰는데,
    그 대가로 어느 폭에서도 같은 모양이 나온다.
    """
    if not macro.released:
        note = " · ".join(macro.notes) if macro.notes else "이 구간에 발표된 지표가 없다"
        return _section("거시지표") + _foot(note)
    body = ""
    for row in macro.released:
        when = row.released_at.astimezone(KST).strftime("%m-%d %H:%M")
        change_color = _color(row.change)
        body += (
            "<tr>"
            + _cell(
                f'<span style="color:{INK};font-weight:600">'
                f"{_clip(row.label, MACRO_LABEL_MAX)}</span><br>"
                # 값과 등락은 한 줄에 붙여 둔다 — 그 둘이 갈리면 "직전 대비" 가
                # 어느 숫자에 걸린 말인지 한 박자 늦게 보인다.
                f'<span style="color:{INK};font-weight:700">'
                f"{_macro_value(row.actual, row.unit)}</span> "
                f'<span style="color:{change_color};font-weight:700;white-space:nowrap">'
                f"{_macro_change(row)}</span><br>"
                f'<span style="color:{FOOTNOTE};font-size:{SMALL}px">'
                f"{_clip(row.source_name, MACRO_SOURCE_MAX)}<br>"
                # 시각은 한 덩어리다. "08-14 / 21:30 / KST" 로 쪼개지면
                # 셋 다 읽어서 다시 붙여야 시각이 된다.
                f'<span style="white-space:nowrap">{when} KST</span> · 직전 '
                f"{_macro_value(row.previous, row.unit)}</span>",
                extra=f"border-top:1px solid {RULE}",
            )
            + "</tr>"
        )
    tail = ["컨센서스는 수집하지 않아 직전값 대비만", *macro.notes]
    return _section("거시지표") + _grid(body) + _foot(" · ".join(tail))


# -- 시장 한 칸 -------------------------------------------------------------------


def _market_block(brief: MarketBrief, report_day: date | None = None) -> str:
    """시장 한 칸. **휴장이면 표를 그리지 않는다.**

    휴장일 브리핑에 직전 거래일 숫자를 늘어놓으면 **그날 장이 선 것처럼
    읽힌다.** 2026-08-18 에 나간 메일이 그랬다 — 08-17(광복절 대체공휴일)
    브리핑의 국장 칸에 08-14 종가와 08-11 시총 순위가 그대로 실려 있었다.
    머리글에 "휴장" 이라 적어도, 아래에 숫자가 깔려 있으면 사람은 숫자를
    믿는다.

    빈칸으로 두지도 않는다 — 그러면 "휴장" 과 "수집 실패" 가 다시 같은
    모양이 된다. **쉬었다고 적고, 직전 거래일이 언제였는지까지 적는다.**
    """
    label = MARKET_LABEL.get(brief.market, brief.market)
    if _is_closed(brief.market, report_day):
        assert report_day is not None
        shut = report_day.strftime("%m-%d")
        head = (
            f'<div style="background-color:{PAPER};color:{INK};font-size:20px;'
            f'font-weight:800;padding:14px 0 6px">{label}'
            f'<span style="color:{SOFT};font-size:{SMALL}px;font-weight:400"> '
            f'<span style="white-space:nowrap">{shut} 휴장</span></span></div>'
        )
        prior = brief.price_session.expected or brief.index_session.expected
        since = f" 직전 거래일은 {prior.isoformat()} 다." if prior else ""
        return _rule() + head + _foot(f"이 날은 장이 서지 않았다 — 실을 세션이 없다.{since}")

    session = brief.price_session.observed or brief.index_session.observed
    when = session.isoformat() if session else "세션 미확인"
    head = (
        f'<div style="background-color:{PAPER};color:{INK};font-size:20px;'
        f'font-weight:800;padding:14px 0 6px">{label}'
        f'<span style="color:{SOFT};font-size:{SMALL}px;font-weight:400"> {when}</span></div>'
    )
    body = _index_rows(brief.prices, volatility=False)
    if any(row.note for row in brief.prices):
        body += _foot("* 그 지수의 종가 세션이 다르거나 미수집")
    if brief.volatility:
        body += _index_rows(brief.volatility, volatility=True)
        body += _foot("변동성 지수 — 상승은 수익이 아니라 공포다. 손익 색 없음")
    for rank in brief.rankings:
        body += _ranking_block(rank, brief)
    body += _news_block(brief.news)
    return _rule() + head + body


# -- 한 판 ---------------------------------------------------------------------


def _gap_line(briefing: Briefing) -> str:
    """결측을 **한 줄로 접는다.**

    숨기지 않는다 — 결측을 숨기면 낡은 값을 오늘 값으로 읽는다. 다만 맨 위
    네 줄을 다 차지하면 정작 시황이 안 보인다. 여기는 건수만 알리고, 무엇이
    빠졌는지는 맨 아래 상세가 받는다.
    """
    count = len(briefing.gaps)
    if not count:
        return ""
    return _band(f"<b>결측 {count}건</b> — 맨 아래 목록", ink=WARN_INK, bg=WARN_BG)


def _gap_group(gaps: list[Gap], kind: str, heading: str) -> str:
    items = [gap.text for gap in gaps if gap.kind == kind]
    if not items:
        return ""
    lines = "".join(f"<div>· {text}</div>" for text in items)
    return f'<div style="margin-top:8px"><b>{heading} {len(items)}건</b>{lines}</div>'


def _gap_detail(briefing: Briefing) -> str:
    """결측을 **성격별로 가른다.**

    우리가 못 받은 것(``MISSING``)은 수집기를 고치면 채워진다. 원본이 아직
    안 낸 것(``UNPUBLISHED``, 지금은 환율뿐 — FRED H.10 이 월요일 주간
    발행이다)은 고칠 것이 없다. 한 문구로 뭉치면 매주 없는 결함을 찾게 된다.
    """
    gaps = briefing.gaps
    if not gaps:
        return ""
    body = _gap_group(gaps, MISSING, "우리가 못 받은 것") + _gap_group(
        gaps, UNPUBLISHED, "원본이 아직 안 낸 것 — 우리 잘못이 아니다"
    )
    return _band(
        f"<b>들어오지 않은 것 {len(gaps)}건</b>{body}",
        ink=WARN_INK,
        bg=WARN_BG,
    )


def _report_date(briefing: Briefing) -> str:
    """리포트가 다루는 날. **as_of 의 날짜가 아니다** — 세션 날짜다."""
    day = report_date(briefing)
    return day.isoformat() if day else briefing.as_of.astimezone(KST).date().isoformat()


def report_date(briefing: Briefing) -> date | None:
    days = [
        brief.price_session.expected or brief.index_session.expected
        for brief in briefing.markets.values()
    ]
    real = [day for day in days if day is not None]
    return max(real) if real else None


def subject(briefing: Briefing) -> str:
    return f"[시황] {_report_date(briefing)} · {headline(briefing)}"


def render_html(briefing: Briefing) -> str:
    report_day = report_date(briefing)
    blocks = "".join(
        _market_block(briefing.markets[code], report_day)
        for code in MARKET_ORDER
        if code in briefing.markets
    )
    # 거시는 두 시장 뒤에 한 번. 좌우로 가르지 않는 이유는 Briefing.macro 주석 참고.
    blocks += _rule() + _macro_block(briefing.macro)
    stamp = briefing.as_of.astimezone(KST).strftime("%m-%d %H:%M")
    return f"""<!DOCTYPE html><html style="background-color:{CANVAS}">\
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark"></head>
<body style="margin:0;padding:0;background-color:{CANVAS};color:{INK};\
-webkit-text-size-adjust:100%;\
font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
style="width:100%;background-color:{CANVAS};border-collapse:collapse">
<tr><td style="padding:10px;background-color:{CANVAS}">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
style="width:100%;max-width:520px;margin:0 auto;background-color:{PAPER};\
border-collapse:collapse;border-radius:10px">
<tr><td style="padding:16px 14px;background-color:{PAPER}">
<div style="background-color:{PAPER};color:{SOFT};font-size:{SMALL}px">\
시황 브리핑 · {_report_date(briefing)}</div>
{_headline_block(briefing)}
{_gap_line(briefing)}
{blocks}
{_gap_detail(briefing)}
<div style="background-color:{PAPER};color:{FOOTNOTE};font-size:{SMALL}px;\
line-height:1.5;padding:14px 0 0;border-top:1px solid {RULE};margin-top:16px">
실매매 기록이 없어 성과·보유는 넣지 않는다 · 전 수치 store.get(as_of) 경유 · 생성 {stamp} KST
</div>
</td></tr></table>
</td></tr></table>
</body></html>"""


def render_text(briefing: Briefing) -> str:
    """텍스트 대체본. HTML 을 막는 클라이언트와 로그 확인용."""
    lines: list[str] = [subject(briefing), ""]
    if briefing.gaps:
        lines.append(f"[결측 {len(briefing.gaps)}건 — 맨 아래 목록]")
        lines.append("")

    for code in MARKET_ORDER:
        brief = briefing.markets.get(code)
        if brief is None:
            continue
        report_day = report_date(briefing)
        label = MARKET_LABEL.get(code, code)
        if _is_closed(code, report_day):
            # HTML 과 같은 규칙이다 — 한쪽만 숫자를 실으면 두 벌이 다른 말을 한다.
            assert report_day is not None
            prior = brief.price_session.expected or brief.index_session.expected
            since = f" 직전 거래일 {prior.isoformat()}." if prior else ""
            lines.append(f"== {label} · {report_day.strftime('%m-%d')} 휴장 ==")
            lines.append(f"  장이 서지 않았다 — 실을 세션이 없다.{since}")
            lines.append("")
            continue
        lines.append(f"== {label} ==")
        for index_row in brief.prices:
            mark = f"  — {index_row.note}" if index_row.note else ""
            lines.append(
                f"  {index_row.label}: {index_row.note or '값 없음'}"
                if index_row.close is None
                else f"  {index_row.label} {_num(index_row.close)} "
                f"{_pct(index_row.change)}{mark}"
            )
        for vol_row in brief.volatility:
            lines.append(
                f"  [변동성] {vol_row.label}: {vol_row.note or '값 없음'}"
                if vol_row.close is None
                else f"  [변동성] {vol_row.label} {_num(vol_row.close)} "
                f"{_pct(vol_row.change)} (상승=공포)"
            )
        for rank in brief.rankings:
            when = rank.session.isoformat() if rank.session else "세션 미확인"
            _, fmt = _METRIC[rank.key]
            mixed = (
                f", 등락 {rank.change_session.isoformat()}"
                if rank.change_session and rank.change_session != rank.session
                else ""
            )
            lines.append(f"  -- {rank.label} ({when}{mixed}, 정렬 {rank.sort_by})")
            if not rank.rows:
                lines.append(f"     {rank.note or '해당 없음'}")
            for index, entry in enumerate(rank.rows, start=1):
                lines.append(
                    f"     {index}. {entry.name} {fmt(entry.metric, brief.currency)} "
                    f"{_pct(entry.change)}"
                )
            if rank.note:
                lines.append(f"     ! {rank.note}")
        news = brief.news
        lines.append(f"  -- 뉴스 ({news.total:,}건 중 {len(news.rows)}건)")
        if news.note:
            lines.append(f"     {news.note}")
        lines.append(f"     기준: {news.criteria}")
        for item in news.rows:
            headline = item.title_ko or item.title
            lines.append(f"     [{item.reason}] {item.name} {headline}")
            if item.title_ko:
                lines.append(f"       원문: {item.title}")
        lines.append("")

    macro = briefing.macro
    lines.append("== 거시지표 ==")
    if not macro.released:
        lines.append("  " + (" · ".join(macro.notes) or "이 구간에 발표된 지표가 없다"))
    for item in macro.released:
        when = item.released_at.astimezone(KST).strftime("%m-%d %H:%M")
        lines.append(
            f"  [{MARKET_LABEL.get(item.market, item.market)}] {item.label} "
            f"({item.source_name}) {_macro_value(item.actual, item.unit)} "
            f"{_macro_change(item)} (직전 {_macro_value(item.previous, item.unit)}) · {when} KST"
        )
    if macro.released:
        for note in macro.notes:
            lines.append(f"  * {note}")

    if briefing.gaps:
        lines += ["", f"== 들어오지 않은 것 {len(briefing.gaps)}건 =="]
        groups = ((MISSING, "우리가 못 받은 것"), (UNPUBLISHED, "원본이 아직 안 낸 것"))
        for kind, heading in groups:
            items = [gap.text for gap in briefing.gaps if gap.kind == kind]
            if items:
                lines.append(f"  -- {heading} --")
                lines += [f"  - {text}" for text in items]
    lines += ["", "실매매 기록이 없어 성과·보유 섹션 없음"]
    return "\n".join(lines)


def render(briefing: Briefing) -> dict[str, Any]:
    return {
        "subject": subject(briefing),
        "html": render_html(briefing),
        "text": render_text(briefing),
    }
