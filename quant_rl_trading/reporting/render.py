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

## 다크 모드 — 색은 언제나 짝으로

배경만 정하고 글자색을 안 정하면(또는 그 반대면) 다크 모드에서 검은 바탕에
검은 글씨가 된다. **글자색을 주는 곳에는 배경색도 함께 준다.** ``_cell``·
``_band`` 가 그 짝을 강제한다.

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

from quant_rl_trading.reporting.briefing import (
    Briefing,
    IndexRow,
    MacroSection,
    MarketBrief,
    NewsSection,
    Ranking,
)

KST = ZoneInfo("Asia/Seoul")

MARKET_LABEL = {"KR": "국장", "US": "미장"}

#: 색은 **배경과 짝으로만** 쓴다 (모듈 독스트링 다크 모드 참고).
UP = "#c0271c"        # 국내 관행 — 상승이 빨강
DOWN = "#12509b"      # 하락이 파랑
INK = "#1a1a1a"       # 본문 글자
SOFT = "#5f6368"      # 보조 글자
FOOTNOTE = "#6b7075"  # 각주
PAPER = "#ffffff"     # 카드 바탕
CANVAS = "#eceff1"    # 바깥 바탕
RULE = "#dcdfe3"
WARN_INK = "#8a4b00"
WARN_BG = "#fff3e0"

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
    pad: str = "9px 4px",
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
        f'line-height:1.5;padding:9px 11px;border-radius:7px;margin:10px 0">{text}</div>'
    )


def _rule() -> str:
    return f'<div style="border-top:1px solid {RULE};margin:16px 0 0"></div>'


def _section(text: str, *, sub: str = "") -> str:
    tail = (
        f'<span style="color:{SOFT};font-size:{SMALL}px;font-weight:400"> {sub}</span>'
        if sub
        else ""
    )
    return (
        f'<div style="background-color:{PAPER};color:{INK};font-size:18px;'
        f'font-weight:700;padding:16px 0 6px">{text}{tail}</div>'
    )


def _grid(rows: str) -> str:
    """표 하나. **폭을 픽셀로 고정하지 않는다** — 390px 화면에서 넘친다."""
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;border-collapse:collapse;background-color:{PAPER}">'
        f"{rows}</table>"
    )


# -- 한 줄 요약 -------------------------------------------------------------------


def headline(briefing: Briefing) -> str:
    """맨 위 한 줄. **이 줄만 읽고 닫아도 그날을 안 것이 되게.**

    문장을 지어내지 않는다 — 대표 지수 둘과 환율을 사실 그대로 잇는다.
    "반도체가 시장을 끌었다" 같은 해석은 근거가 창고에 없다.
    """
    parts: list[str] = []
    for code in ("KR", "US"):
        brief = briefing.markets.get(code)
        if brief is None or not brief.prices:
            continue
        head = brief.prices[0]
        if head.close is None:
            parts.append(f"{head.label} 미수집")
        else:
            parts.append(f"{head.label} {_moved(head.change)}")
    rate = briefing.fx
    if rate.get("rate") is not None:
        parts.append(f"환율 {rate['rate']:,.0f} {_moved(rate['change'])}")
    return " · ".join(parts) if parts else "지수가 들어오지 않았다"


def _headline_block(briefing: Briefing) -> str:
    """요약 한 줄 + 색. 대표 지수의 방향으로 칠한다."""
    kr = briefing.markets.get("KR")
    lead = kr.prices[0].change if kr and kr.prices else None
    return (
        f'<div style="background-color:{PAPER};color:{_color(lead)};font-size:19px;'
        f'font-weight:800;line-height:1.35;padding:2px 0 0">{headline(briefing)}</div>'
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


def _ranking_rows(rank: Ranking, currency: str) -> str:
    """순위 세 줄. 종목명은 접히고 숫자는 안 접힌다."""
    label, fmt = _METRIC[rank.key]
    header = (
        "<tr>"
        + _cell("종목", color=FOOTNOTE, size=SMALL, pad="0 4px 4px")
        + _cell(label, color=FOOTNOTE, size=SMALL, align="right", pad="0 4px 4px")
        + _cell("전일대비", color=FOOTNOTE, size=SMALL, align="right", pad="0 4px 4px")
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
        covered = rank.eligible / rank.universe if rank.universe else 0.0
        notes.append(
            f"시총 아는 {rank.eligible:,}종목 중 (커버리지 {covered:.0%}"
            + (" · ADR·ETF 는 주식수가 없어 빠진다)" if brief.market == "US" else ")")
        )
    else:
        notes.append(
            f"하한 {_money(floor.min_turnover, floor.currency)}+ · "
            f"{_price(floor.min_price, floor.currency)}+ "
            f"({rank.universe:,}→{rank.eligible:,})"
        )
    if rank.prior:
        notes.append(f"전일대비 = {rank.prior.isoformat()} 대비")
    if rank.note:
        notes.append(f'<span style="color:{WARN_INK}">{rank.note}</span>')
    return _section(rank.label, sub=when) + _ranking_rows(rank, brief.currency) + _foot(
        " · ".join(notes)
    )


# -- 뉴스 ----------------------------------------------------------------------

_DOC_LABEL = {
    "distress": "관리·정지",
    "earnings": "실적",
    "dilution": "증자",
    "buyback": "자사주",
    "split": "분할",
    "contract": "계약",
}


def _news_block(news: NewsSection) -> str:
    if not news.rows:
        return _section("공시") + _foot(f"{news.note or '해당 없음'} · {news.criteria}")
    body = ""
    for row in news.rows:
        kind = _DOC_LABEL.get(row.doc_type, row.doc_type)
        title = (
            f'<a href="{row.url}" style="color:{INK};text-decoration:none">{row.title}</a>'
            if row.url
            else row.title
        )
        body += (
            "<tr>"
            + _cell(
                f'<span style="color:{SOFT};font-size:{SMALL}px">{kind}</span><br>'
                f'<span style="color:{INK};font-weight:600">{row.name}</span> {title}',
                extra=f"border-top:1px solid {RULE}",
            )
            + "</tr>"
        )
    return (
        _section("공시")
        + _grid(body)
        + _foot(f"{news.total:,}건 중 {len(news.rows)}건 · {news.criteria} · 규칙 선별(LLM 미사용)")
    )


# -- 거시 ----------------------------------------------------------------------


#: 거시 지표명 최대 길이. FRED 이름은 "Advance Monthly Sales for Retail and
#: Food Services" 처럼 길어서 390px 에서 세 줄로 접힌다 — 이름 한 개가 표를
#: 밀어내면 옆의 숫자를 못 읽는다. 자른 것은 말줄임표가 말한다.
MACRO_LABEL_MAX = 26


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _macro_actual(value: float | None) -> str:
    """큰 값의 소수점 두 자리는 화면만 먹는다 — 763,602.00 → 763,602."""
    if value is None:
        return "—"
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}"


def _macro_block(macro: MacroSection) -> str:
    if not macro.released:
        note = " · ".join(macro.notes) if macro.notes else "이 구간에 발표된 지표가 없다"
        return _section("거시지표") + _foot(note)
    body = ""
    for row in macro.released:
        unit = _unit(row.unit)
        when = row.released_at.astimezone(KST).strftime("%m-%d %H:%M")
        body += (
            "<tr>"
            + _cell(
                f'<span style="color:{INK};font-weight:600">'
                f"{_clip(row.label, MACRO_LABEL_MAX)}</span><br>"
                f'<span style="color:{FOOTNOTE};font-size:{SMALL}px">{when} KST</span>',
                extra=f"border-top:1px solid {RULE}",
            )
            + _cell(
                f'<span style="color:{INK};font-weight:700">'
                f"{_macro_actual(row.actual)}{unit}</span><br>"
                f'<span style="color:{FOOTNOTE};font-size:{SMALL}px">직전 '
                f"{_macro_actual(row.previous)}{unit}</span>",
                align="right",
                wrap=False,
                extra=f"border-top:1px solid {RULE}",
            )
            + "</tr>"
        )
    tail = ["컨센서스는 수집하지 않아 직전값 대비만", *macro.notes]
    return _section("거시지표") + _grid(body) + _foot(" · ".join(tail))


# -- 시장 한 칸 -------------------------------------------------------------------


def _market_block(brief: MarketBrief) -> str:
    label = MARKET_LABEL.get(brief.market, brief.market)
    session = brief.price_session.observed or brief.index_session.observed
    when = session.isoformat() if session else "세션 미확인"
    head = (
        f'<div style="background-color:{PAPER};color:{INK};font-size:20px;'
        f'font-weight:800;padding:18px 0 8px">{label}'
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
    count = len(briefing.notes)
    if not count:
        return ""
    return _band(f"<b>결측 {count}건</b> — 맨 아래 목록", ink=WARN_INK, bg=WARN_BG)


def _gap_detail(briefing: Briefing) -> str:
    if not briefing.notes:
        return ""
    lines = "".join(f"<div>· {note}</div>" for note in briefing.notes)
    return _band(
        f'<b>들어오지 않은 것 {len(briefing.notes)}건</b>{lines}',
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
    blocks = "".join(
        _market_block(briefing.markets[code])
        for code in ("KR", "US")
        if code in briefing.markets
    )
    # 거시는 두 시장 뒤에 한 번. 좌우로 가르지 않는 이유는 Briefing.macro 주석 참고.
    blocks += _rule() + _macro_block(briefing.macro)
    stamp = briefing.as_of.astimezone(KST).strftime("%m-%d %H:%M")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"></head>
<body style="margin:0;padding:0;background-color:{CANVAS};color:{INK};\
-webkit-text-size-adjust:100%;\
font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" \
style="width:100%;background-color:{CANVAS};border-collapse:collapse">
<tr><td style="padding:10px">
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
    if briefing.notes:
        lines.append(f"[결측 {len(briefing.notes)}건 — 맨 아래 목록]")
        lines.append("")

    for code in ("KR", "US"):
        brief = briefing.markets.get(code)
        if brief is None:
            continue
        lines.append(f"== {MARKET_LABEL.get(code, code)} ==")
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
            lines.append(f"  -- {rank.label} ({when}, 정렬 {rank.sort_by})")
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
        lines.append(f"  -- 공시 ({news.total:,}건 중 {len(news.rows)}건)")
        if news.note:
            lines.append(f"     {news.note}")
        lines.append(f"     기준: {news.criteria}")
        for doc in news.rows:
            lines.append(
                f"     [{_DOC_LABEL.get(doc.doc_type, doc.doc_type)}] {doc.name} {doc.title}"
            )
        lines.append("")

    macro = briefing.macro
    lines.append("== 거시지표 ==")
    if not macro.released:
        lines.append("  " + (" · ".join(macro.notes) or "이 구간에 발표된 지표가 없다"))
    for item in macro.released:
        when = item.released_at.astimezone(KST).strftime("%m-%d %H:%M")
        unit = _unit(item.unit)
        lines.append(
            f"  [{MARKET_LABEL.get(item.market, item.market)}] {item.label} "
            f"{_num(item.actual)}{unit} (직전 {_num(item.previous)}{unit}) · {when} KST"
        )
    if macro.released:
        for note in macro.notes:
            lines.append(f"  * {note}")

    if briefing.notes:
        lines += ["", f"== 들어오지 않은 것 {len(briefing.notes)}건 =="]
        lines += [f"  - {note}" for note in briefing.notes]
    lines += ["", "실매매 기록이 없어 성과·보유 섹션 없음"]
    return "\n".join(lines)


def render(briefing: Briefing) -> dict[str, Any]:
    return {
        "subject": subject(briefing),
        "html": render_html(briefing),
        "text": render_text(briefing),
    }
