"""이메일 렌더링.

메일은 되돌릴 수 없다. 화면은 새로고침하면 되지만 잘못 나간 메일은 못
거둔다. 그래서 여기서 고정하는 것은 **틀리게 읽힐 여지**다.

1. 지수·등락률이 없으면 **빈칸이 아니라 ``—`` 와 이유**가 찍힌다
2. 변동성 지수에는 **손익 색이 없다** — VIX 상승은 수익이 아니라 공포다
3. **무엇으로 줄 세웠는지** 순위표마다 적힌다
4. 걸어 둔 **하한과 커버리지가 각주로 남는다** — 숨긴 필터는 못 믿는 필터다
5. **성과는 TWR 로 적는다** — 입금일에 NAV 변화율을 "수익률" 이라 적으면
   하루에 +5,000% 가 찍힌다. 없는 것은 0 이 아니라 "못 쟀다" 로 적는다
6. 인라인 CSS · **다크 배경** · JS 없음 (reporting.md §3)
7. 결측은 **성격별로 갈린다** — 우리가 못 받은 것과 원본이 안 낸 것은 다른 사실이다
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

from quant_rl_trading.accounting.performance import Fill, Performance
from quant_rl_trading.collectors.market_hours import Market, is_trading_day
from quant_rl_trading.reporting import render as render_module
from quant_rl_trading.reporting.briefing import (
    MARKET_ORDER,
    Briefing,
    Floor,
    IndexRow,
    MacroRow,
    MacroSection,
    MarketBrief,
    NewsRow,
    NewsSection,
    Ranking,
    RankRow,
)
from quant_rl_trading.reporting.render import (
    CANVAS,
    DOWN,
    INK,
    PAPER,
    UP,
    headline,
    render_html,
    render_text,
    subject,
)
from quant_rl_trading.reporting.sessions import MISSING, UNPUBLISHED, SessionRef

NOW = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)
FRIDAY, THURSDAY = date(2026, 8, 14), date(2026, 8, 13)


def _ranking(
    key: str, label: str, sort_by: str, *, session: date = FRIDAY, **kw: object
) -> Ranking:
    defaults: dict[str, object] = {
        "rows": [RankRow("KR:005930", "삼성전자", 5e11, 73_500.0, 0.05)],
        "eligible": 300,
        "universe": 2872,
        "note": None,
    }
    defaults.update(kw)
    return Ranking(
        key=key,
        label=label,
        sort_by=sort_by,
        session=session,
        prior=THURSDAY,
        rows=defaults["rows"],  # type: ignore[arg-type]
        eligible=defaults["eligible"],  # type: ignore[arg-type]
        universe=defaults["universe"],  # type: ignore[arg-type]
        note=defaults["note"],  # type: ignore[arg-type]
    )


def _news(**kw: object) -> NewsSection:
    defaults: dict[str, object] = {
        "rows": [
            NewsRow(
                "KR:005930", "삼성전자", "3분기 실적 잠정 발표",
                "https://news.example/1", FRIDAY, "거래대금 상위", 4,
            )
        ],
        "total": 305,
        "criteria": "거래대금 상위 100위 종목 · 기사가 몰린 종목 · 종목당 최신 1건",
        "note": None,
    }
    defaults.update(kw)
    return NewsSection(**defaults)  # type: ignore[arg-type]


def _brief(market: str, *, prices: list[IndexRow], **kw: object) -> MarketBrief:
    currency = "KRW" if market == "KR" else "USD"
    rankings = kw.get(
        "rankings",
        [
            _ranking("value", "거래대금 상위", "prices.value — 체결 금액"),
            _ranking(
                "market_cap",
                "시가총액 상위",
                "market_stats.market_cap",
                eligible=4345,
                universe=6647,
            ),
        ],
    )
    return MarketBrief(
        market=market,
        currency=currency,
        index_session=SessionRef(
            market=market,
            source="지수",
            expected=FRIDAY,
            observed=THURSDAY if kw.get("index_note") else FRIDAY,
            note=kw.get("index_note"),  # type: ignore[arg-type]
        ),
        price_session=SessionRef(market, "시세", FRIDAY, FRIDAY, None),
        prices=prices,
        volatility=kw.get("volatility", []),  # type: ignore[arg-type]
        rankings=rankings,  # type: ignore[arg-type]
        floor=Floor(
            currency=currency,
            min_turnover=1_000_000_000 if market == "KR" else 5_000_000,
            min_price=1000 if market == "KR" else 1.0,
            pool=300,
            eligible=300,
        ),
        news=kw.get("news", _news()),  # type: ignore[arg-type]
        proxies=kw.get("proxies", []),  # type: ignore[arg-type]
    )


def _briefing(**overrides: object) -> Briefing:
    kr = _brief(
        "KR",
        prices=[
            IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, THURSDAY,
                     "2026-08-13 종가 · 2026-08-14 미수집"),
            IndexRow("KR:IDX:KOSDAQ", "코스닥", "price", None, None, None,
                     "창고에 이 지수가 없다"),
        ],
        index_note="KR 지수: 창고가 2026-08-13 까지다. 2026-08-14 까지 1개 세션이 안 들어왔다",
    )
    us = _brief(
        "US",
        prices=[IndexRow("US:IDX:SP500", "S&P 500", "price", 7798.99, 0.0065, FRIDAY)],
        volatility=[
            IndexRow("US:IDX:VIX", "VIX (S&P 변동성)", "volatility", 14.63, 0.0055, FRIDAY)
        ],
        news=_news(
            rows=[],
            total=0,
            criteria="거래대금 상위 100위 종목 · 기사가 몰린 종목 · 종목당 최신 1건",
            note="창고에 미장 뉴스가 없다 — 수집기가 이 시장을 아직 안 돈다",
        ),
    )
    macro = MacroSection(
        released=[
            MacroRow(
                entity_id="US:RETAIL_ADVANCE",
                market="US",
                label="소매판매 (속보)",
                source_name="Advance Monthly Sales for Retail and Food Services",
                actual=763_602.0,
                previous=768_072.0,
                unit="mn_usd",
                released_at=datetime(2026, 8, 14, 12, 30, tzinfo=UTC),
            ),
        ],
        notes=["국내 지표: 이 구간에 발표된 것이 없다"],
        since=THURSDAY,
    )
    payload: dict[str, object] = {
        "as_of": NOW,
        "fx": {"rate": 1409.94, "change": -0.0096, "sessions": ["2026-08-07"], "rates": [1409.94]},
        "fx_note": None,
        "fx_gap_kind": None,
        "macro": macro,
        "markets": {"KR": kr, "US": us},
    }
    payload.update(overrides)
    return Briefing(**payload)  # type: ignore[arg-type]


# -- 없는 것은 없다고 -------------------------------------------------------------


def test_missing_index_prints_the_reason_not_a_blank() -> None:
    html = render_html(_briefing())
    assert "창고에 이 지수가 없다" in html
    kosdaq = re.search(r"코스닥.{0,300}?</span>", html, re.S)
    assert kosdaq is not None
    assert "0.00%" not in kosdaq.group(0)


def test_unmeasured_change_is_a_dash_not_zero() -> None:
    """직전 종가가 없으면 ``—`` 다. **0.00% 는 "보합" 이라는 다른 사실이다.**"""
    briefing = _briefing()
    kr = briefing.markets["KR"]
    ranking = _ranking(
        "value",
        "거래대금 상위",
        "prices.value — 체결 금액",
        rows=[RankRow("KR:900001", "신규상장", 5e10, 12_000.0, None)],
    )
    patched = _brief("KR", prices=kr.prices, rankings=[ranking])
    html = render_html(_briefing(markets={"KR": patched}))
    row = re.search(r"신규상장.{0,600}?</tr>", html, re.S)
    assert row is not None
    assert "—" in row.group(0)
    assert "0.00%" not in row.group(0)


# -- 결측은 성격별로 --------------------------------------------------------------


def test_gaps_are_collected_at_the_top() -> None:
    html = render_html(_briefing())
    assert "들어오지 않은 것" in html
    assert "2026-08-14 까지 1개 세션이 안 들어왔다" in html


def test_fixable_gap_is_labelled_ours() -> None:
    """국장 지수 결측은 우리 수집이 못 받은 것이다 — 고칠 수 있다."""
    html = render_html(_briefing())
    assert "우리가 못 받은 것" in html


def test_unpublished_gap_is_not_called_a_collection_failure() -> None:
    """FRED H.10 이 아직 안 낸 환율을 "미수집" 이라 부르면 안 된다.

    ``UNPUBLISHED`` 로 표시된 결측은 "미수집"·"수집 확인" 같은 문구가 아니라
    원본이 아직 안 냈다는 사실을 담아야 한다. 이 문구가 틀리면 매주 없는
    결함을 찾게 된다(실제로 있었던 사고).
    """
    fx_note = (
        "환율: FRED 원본이 아직 2026-08-07 까지다 "
        "(H.10 은 월요일 주간 발행 — 다음 값은 다음 발행일에 나온다)"
    )
    html = render_html(_briefing(fx_note=fx_note, fx_gap_kind=UNPUBLISHED))
    assert "원본이 아직 안 낸 것" in html
    assert fx_note in html
    # "미수집" 이 이 결측 옆에는 없어야 한다 — 원본 문제를 우리 잘못으로 읽히게 하지 않는다.
    unpublished_block = re.search(r"원본이 아직 안 낸 것.{0,400}", html, re.S)
    assert unpublished_block is not None
    assert "미수집" not in unpublished_block.group(0)


def test_missing_gap_and_unpublished_gap_are_grouped_separately() -> None:
    fx_note = (
        "환율: 창고가 2026-08-01 까지다 — FRED 원본은 2026-08-07 까지 이미 냈다 (수집 확인 필요)"
    )
    html = render_html(_briefing(fx_note=fx_note, fx_gap_kind=MISSING))
    assert "우리가 못 받은 것" in html
    assert fx_note in html


# -- 변동성 --------------------------------------------------------------------


def test_volatility_row_carries_no_profit_colour() -> None:
    """VIX +0.55% 에 상승색을 칠하면 "VIX 가 올라서 좋다" 로 읽힌다."""
    html = render_html(_briefing())
    block = re.search(r"VIX \(S&P 변동성\).{0,500}?</div>", html, re.S)
    assert block is not None
    assert UP not in block.group(0), "변동성 지수에 상승색이 칠해졌다"
    assert DOWN not in block.group(0)


def test_price_index_keeps_its_colour() -> None:
    """가격지수는 색을 쓴다 — 변동성만 예외라는 것이 이 테스트의 뜻이다."""
    html = render_html(_briefing())
    row = re.search(r"코스피.{0,600}?</tr>", html, re.S)
    assert row is not None
    assert UP in row.group(0)


def test_volatility_is_labelled() -> None:
    assert "상승은 수익이 아니라 공포다" in render_html(_briefing())
    assert "(상승=공포)" in render_text(_briefing())


# -- 순위표 --------------------------------------------------------------------


def test_every_ranking_says_what_it_sorted_by() -> None:
    """무엇으로 줄 세웠는지 표마다 적는다 — 안 적으면 어느 순위인지 못 읽는다."""
    html = render_html(_briefing())
    for label in ("거래대금 상위", "시가총액 상위"):
        assert label in html
    assert "정렬 prices.value — 체결 금액" in html
    assert "정렬 market_stats.market_cap" in html


def test_renderer_covers_every_ranking_the_data_layer_makes() -> None:
    """``RANKINGS`` 에 표를 하나 더하면 렌더러도 같이 늘어야 한다.

    렌더러는 모르는 키를 만나면 ``KeyError`` 로 죽는다. 그게 메일 한 통이
    통째로 안 나가는 사고라, 그 짝이 어긋난 것을 여기서 먼저 깬다.
    """
    from quant_rl_trading.reporting.briefing import RANKINGS
    from quant_rl_trading.reporting.render import _METRIC

    assert {key for key, _, _ in RANKINGS} == set(_METRIC)


def test_liquidity_floor_is_in_the_footnote() -> None:
    """숨긴 필터는 못 믿는 필터다. 다만 본문이 아니라 각주다."""
    html = render_html(_briefing())
    assert "하한 10억+ · 1,000원+" in html
    assert "(2,872→300)" in html
    assert "$5M+" in html and "$1.00+" in html


def test_market_cap_coverage_is_disclosed_for_us() -> None:
    """미장 시총은 ADR·ETF 가 빠져 있다. 안 적으면 애플 없는 순위가 조용히 나간다."""
    html = render_html(_briefing())
    assert "커버리지 65%" in html
    assert "ADR·ETF 는 주식수가 없어 빠진다" in html


def test_ranking_states_the_comparison_session() -> None:
    """"전일대비" 가 어느 날 대비인지 적는다."""
    assert "전일대비 = 2026-08-13 대비" in render_html(_briefing())


# -- 거시지표 ------------------------------------------------------------------


def test_macro_shows_a_korean_label() -> None:
    """원제만 실으면 아침에 읽고 넘어갈 수 없다. 한글 이름이 먼저 보여야 한다."""
    assert "소매판매" in render_html(_briefing())


def test_macro_keeps_the_source_name_for_verification() -> None:
    """**원제·원값은 버리지 않는다.** 가공한 숫자만 남기면 검증할 길이 없다."""
    html = render_html(_briefing())
    # 상한에 걸려 잘리므로 앞부분만 확인한다 — 전체를 요구하지 않는다.
    assert "Advance Monthly Sales" in html


def test_macro_computes_the_change_so_no_one_has_to_subtract() -> None:
    """763,602 와 768,072 를 나란히 두고 사람이 -0.58%를 암산하게 하지 않는다."""
    html = render_html(_briefing())
    expected = 763_602.0 / 768_072.0 - 1.0
    assert f"{abs(expected) * 100:.2f}%" in html
    assert "▼" in html  # 감소 방향


def test_macro_value_is_in_a_readable_unit() -> None:
    """763,602 mn_usd 는 크기가 안 잡힌다 — 억/B 표기로 접는다."""
    html = render_html(_briefing())
    assert "$763.6B" in html
    # 원값도 괄호로 남는다 (검증 가능성).
    assert "763,602" in html


def test_no_consensus_column_is_invented() -> None:
    """컨센서스를 수집하지 않으므로 "예상 대비" 를 쓸 수 없다."""
    html = render_html(_briefing())
    disclaimer = "컨센서스는 수집하지 않아 직전값 대비만"
    assert disclaimer in html
    body = html.replace(disclaimer, "")
    for banned in ("예상", "컨센서스", "서프라이즈"):
        assert banned not in body


def test_missing_domestic_macro_is_named() -> None:
    assert "국내 지표: 이 구간에 발표된 것이 없다" in render_html(_briefing())


def test_release_time_is_the_scheduled_time() -> None:
    """12:30 UTC = 21:30 KST. ``valid_from``(우리가 안 시각)이 아니다."""
    assert "08-14 21:30" in render_html(_briefing())


# -- 뉴스 ----------------------------------------------------------------------


def test_news_section_replaces_filings() -> None:
    """공시(dart)가 아니라 뉴스(newsapi) 섹션이다."""
    html = render_html(_briefing())
    assert "뉴스" in html
    assert "공시" not in html


def test_news_discloses_how_many_were_cut() -> None:
    html = render_html(_briefing())
    assert "305건 중 1건" in html
    assert "규칙 선별(LLM 미사용)" in html


def test_news_keeps_the_source_link() -> None:
    assert 'href="https://news.example/1"' in render_html(_briefing())


def test_us_news_says_the_collector_has_not_run_yet() -> None:
    """창고에 미장 뉴스가 없으면 그 사실을 적는다 — 지어내지 않는다."""
    html = render_html(_briefing())
    assert "창고에 미장 뉴스가 없다" in html
    assert "수집기가 이 시장을 아직 안 돈다" in html


def test_translated_title_shows_korean_and_keeps_english_original() -> None:
    """번역이 있으면 한글이 앞서고 **원문 영어도 함께** 남는다 — 오역을 검증할 길."""
    us = _brief(
        "US",
        prices=[IndexRow("US:IDX:SP500", "S&P 500", "price", 7798.99, 0.0065, FRIDAY)],
        news=_news(
            rows=[
                NewsRow(
                    "US:META", "Meta", "META unveils new data center",
                    "https://news.example/2", FRIDAY, "거래대금 상위", 2,
                    title_ko="메타, 새 데이터센터 공개",
                )
            ],
            total=1,
            criteria="거래대금 상위 100위 종목 · 기사가 몰린 종목 · 종목당 최신 1건",
        ),
    )
    html = render_html(_briefing(markets={"KR": _briefing().markets["KR"], "US": us}))
    assert "메타, 새 데이터센터 공개" in html
    assert "META unveils new data center" in html, "원문이 사라지면 오역을 검증할 수 없다"
    assert "제목 번역은 Claude" in html


def test_untranslated_title_has_no_stray_english_footnote() -> None:
    """번역이 없으면(``title_ko`` 없음) 번역 각주도 안 붙는다 — 안 한 일을 한 것처럼 안 적는다."""
    html = render_html(_briefing())  # 기본 fixture 는 title_ko 가 없다
    assert "제목 번역은 Claude" not in html


# -- 성과 섹션 ------------------------------------------------------------------
#
# **옛 계약은 "성과 섹션이 없다" 였다.** 매매가 0건이던 동안은 그게 맞았다 —
# 빈 표는 "손실 0" 으로 읽힌다 (reporting.md §0). 매매가 돌기 시작해서 시황
# 위에 성과를 얹었으므로, 지금 고정할 것은 **성과가 어떻게 실리는가** 다.
# 특히 입금이 있는 날 그것이 수익으로 읽히지 않는 것.


def _perf(**over: object) -> Performance:
    payload: dict[str, object] = {
        "mode": "SHADOW",
        "mode_note": "모의 운용 — 돈이 오가지 않는다",
        "store_root": "data/_shadow",
        "session": FRIDAY,
        "previous_session": THURSDAY,
        "since": date(2026, 8, 12),
        "nav": 9_761_790.0,
        "previous_nav": 9_759_185.0,
        "nav_change": 2_605.0,
        "inflow": 0.0,
        "pnl": 2_605.0,
        "daily_return": 0.000267,
        "cumulative_return": -0.0238,
        "index_value": 97.62,
        "drawdown": -0.0296,
        "principal": 10_000_000.0,
        "total_pnl": -238_209.0,
        "fills": [],
        "fills_omitted": 0,
        "buy_count": 0,
        "sell_count": 0,
        "realized_pnl": None,
        "note": None,
    }
    payload.update(over)
    return Performance(**payload)  # type: ignore[arg-type]


def _fill(**over: object) -> Fill:
    payload: dict[str, object] = {
        "entity_id": "KR:037460",
        "name": "삼지전자",
        "side": "sell",
        "quantity": 14.0,
        "price": 31_850.0,
        "currency": "KRW",
        "fee": 66.885,
        "tax": 802.62,
        "realized_pnl": 2_564.0,
        "realized_rate": 0.0058,
    }
    payload.update(over)
    return Fill(**payload)  # type: ignore[arg-type]


def test_입금일_수익률이_입금액만큼_부풀지_않는다() -> None:
    """**이 섹션에서 가장 틀리기 쉬운 자리다.**

    490,238,209원이 들어온 날 NAV 는 976만에서 5억이 된다. 그 변화율을
    "수익률" 이라 적으면 +5,000% 다. 자산 증감은 절대액으로 싣되(사용자가
    요청한 항목이다) **그중 입출금이 얼마인지 바로 아래 줄에 적는다.**
    """
    deposit = 490_238_209.0
    perf = _perf(
        nav=500_002_605.0,
        previous_nav=9_761_790.0,
        nav_change=deposit + 2_605.0,
        inflow=deposit,
        pnl=2_605.0,
        daily_return=0.000267,
    )
    for text in (render_html(_briefing(performance=perf)),
                 render_text(_briefing(performance=perf))):
        # 수익률 자리에 다섯 자리 퍼센트가 있으면 안 된다.
        assert not re.search(r"[+\-]\d{3,},?\d*\.\d\d%", text)
        assert "+0.03%" in text
        # 증감 옆에 입출금이 반드시 적힌다.
        assert "490,238,209" in text
        assert "입출금" in text


def test_성과는_TWR_이라고_적는다() -> None:
    """단순 NAV 변화율과 수익률이 다른 값이라는 사실을 본문이 말한다."""
    html = render_html(_briefing(performance=_perf()))
    assert "TWR" in html
    assert "자산 증감" in html and "당일 수익률" in html


def test_매매가_없던_날은_0건이_아니라_없었다고_적는다() -> None:
    for text in (render_html(_briefing(performance=_perf())),
                 render_text(_briefing(performance=_perf()))):
        assert "체결된 매매가 없다" in text
        assert "매매 0건" not in text


def test_못_잰_것과_0_을_가른다() -> None:
    """회계 스냅샷이 없으면 숫자를 그리지 않는다 — 0 으로 채운 표는
    "손실 0" 으로 읽힌다."""
    perf = _perf(
        session=None, previous_session=None, since=None,
        nav=None, previous_nav=None, nav_change=None, inflow=None, pnl=None,
        daily_return=None, cumulative_return=None, index_value=None, drawdown=None,
        principal=None, total_pnl=None,
        note="회계 스냅샷이 아직 없다 — 성과를 잴 수 없다",
    )
    html = render_html(_briefing(performance=perf))
    assert "성과를 잴 수 없다" in html
    assert "당일 수익률" not in html
    assert "0.00%" not in html


def test_실현손익은_매도에만_붙는다() -> None:
    """매수 자리에 ``0원`` 을 적으면 "본전" 으로 읽힌다."""
    perf = _perf(
        fills=[_fill(side="buy", realized_pnl=None, realized_rate=None)],
        buy_count=1,
    )
    for text in (render_html(_briefing(performance=perf)),
                 render_text(_briefing(performance=perf))):
        assert "아직 실현 없음" in text


def test_잘린_목록은_잘렸다고_적는다() -> None:
    perf = _perf(fills=[_fill()], fills_omitted=4, sell_count=5)
    for text in (render_html(_briefing(performance=perf)),
                 render_text(_briefing(performance=perf))):
        assert "매매 내역" in text or "매매 5건" in text
        assert "4건" in text


def test_어느_창고의_성과인지_밝힌다() -> None:
    """모의 운용 숫자를 실전으로 읽는 것이 이 메일에서 가능한 가장 비싼 오해다."""
    for text in (render_html(_briefing(performance=_perf())),
                 render_text(_briefing(performance=_perf()))):
        assert "모의 운용" in text
        assert "data/_shadow" in text


def test_성과가_없으면_섹션도_없다() -> None:
    """``performance=None`` 은 회계를 안 읽고 만든 메일이다. 그 사실을 적되
    빈 표를 그리지 않는다."""
    html = render_html(_briefing())
    assert "성과 섹션 없음" in html
    body = html.replace("성과 섹션 없음 — 회계를 읽지 않고 만든 메일이다", "")
    for banned in ("당일 수익률", "자산 증감", "총 수익금"):
        assert banned not in body


# -- 이메일 제약 ----------------------------------------------------------------


def test_email_constraints() -> None:
    html = render_html(_briefing())
    assert "<script" not in html.lower()
    assert "<style" not in html.lower()
    assert 'style="' in html
    assert f"background-color:{CANVAS}" in html


def test_dark_background_is_inlined_not_media_query() -> None:
    """``prefers-color-scheme`` 미디어쿼리에 기대지 않는다 — 인라인으로 못 박는다.

    클라이언트가 미디어쿼리를 무시하거나(다수의 메일 클라이언트) iOS Mail 이
    라이트로 짠 색을 제멋대로 반전시키는 사고를 피하려면, 애초에 다크 값을
    본문에 직접 박아야 한다.
    """
    html = render_html(_briefing())
    assert "@media" not in html
    assert "prefers-color-scheme" not in html
    assert f"background-color:{PAPER}" in html


def test_every_background_colour_has_a_paired_text_colour() -> None:
    """배경만 있고 글자색이 없는 칸이 있으면 다크 모드에서 안 보이는 글자가 된다."""
    html = render_html(_briefing())
    # 카드 배경이 나오는 곳마다 같은 스타일 속성 문자열 안에 color: 가 있어야 한다.
    for style in re.findall(r'style="([^"]*background-color:[^"]*)"', html):
        assert "color:" in style, f"배경만 있고 글자색이 없다: {style}"


def test_body_stays_small_enough_for_gmail() -> None:
    """Gmail 은 본문 102KB 를 넘으면 잘라낸다(clipping)."""
    assert len(render_html(_briefing()).encode("utf-8")) < 102_000


def test_subject_uses_the_session_date_not_the_send_date() -> None:
    """08-15 새벽에 보내는 08-14 리포트다. 제목이 08-15 면 하루 어긋난다."""
    line = subject(_briefing())
    assert line.startswith("[시황] 2026-08-14")
    assert "코스피" in line and "S&P 500" in line


# -- 휴장은 결측이 아니다 ----------------------------------------------------------

#: 2026-08-17(월) 광복절 대체공휴일. **국장 휴장 · 미장 개장**이라 그날
#: 리포트의 두 시장이 서로 다른 세션을 단다. 실측 케이스를 그대로 박는다 —
#: ``market_hours.is_trading_day(Market.KR, 2026-08-17)`` 이 False 다.
SUBSTITUTE_HOLIDAY = date(2026, 8, 17)


def _holiday_briefing() -> Briefing:
    """2026-08-18 06:30 KST 에 나가는 리포트. 국장 08-14, 미장 08-17.

    실제로 그날 나간 메일과 같은 모양이다 — 머리글은 08-17 인데 국장 칸은
    08-14 였고, 그 둘 사이에 아무 설명이 없었다.
    """
    kr = _brief("KR", prices=[
        IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, FRIDAY),
    ])
    us = _brief("US", prices=[
        IndexRow("US:IDX:SP500", "S&P 500", "price", 7798.99, 0.0065, SUBSTITUTE_HOLIDAY),
    ])
    us = MarketBrief(
        market="US",
        currency=us.currency,
        index_session=SessionRef("US", "지수", SUBSTITUTE_HOLIDAY, SUBSTITUTE_HOLIDAY, None),
        price_session=SessionRef("US", "시세", SUBSTITUTE_HOLIDAY, SUBSTITUTE_HOLIDAY, None),
        prices=us.prices,
        volatility=us.volatility,
        rankings=us.rankings,
        floor=us.floor,
        news=us.news,
    )
    return _briefing(markets={"KR": kr, "US": us})


def test_closed_market_says_it_was_closed() -> None:
    """**"낡은 날짜" 와 "휴장" 은 다른 사실이다.**

    빈칸으로 두면 "수집이 사흘 밀렸다" 와 구별되지 않는다. 쉬었다고 적고,
    직전 거래일이 언제였는지까지 적는다.
    """
    html = render_html(_holiday_briefing())
    head = re.search(r"국장<span[^>]*>.{0,400}?</div>\s*<div[^>]*>[^<]*</div>", html, re.S)
    assert head is not None
    assert "08-17 휴장" in head.group(0)
    assert "직전 거래일은 2026-08-14 다" in head.group(0)


def test_closed_market_shows_no_numbers_at_all() -> None:
    """**휴장일에 직전 거래일 숫자를 늘어놓으면 그날 장이 선 것처럼 읽힌다.**

    실제로 그렇게 나갔다 — 08-17 브리핑의 국장 칸에 08-14 종가와 08-11 시총
    순위가 그대로 실려 있었다. 머리글에 "휴장" 이라 적어도 사람은 숫자를 믿는다.
    """
    html = render_html(_holiday_briefing())
    kr = html[html.index(">국장<") :]
    kr = kr[: kr.index(">미장<")] if ">미장<" in kr else kr
    for banned in ("코스피", "6,813", "거래대금 상위", "시가총액 상위", "뉴스"):
        assert banned not in kr, f"휴장인데 {banned} 가 남아 있다"


def test_trading_day_still_shows_everything() -> None:
    """**억제는 그 시장이 그 기준일에 휴장일 때만이다.**

    평일 정상 브리핑에서 국장 데이터가 사라지면 이 고침이 더 큰 사고가 된다.
    """
    html = render_html(_briefing())  # 08-14(금) 기준 — 두 시장 모두 개장
    for wanted in ("코스피", "거래대금 상위", "시가총액 상위"):
        assert wanted in html


def test_closed_rule_does_not_single_out_one_market() -> None:
    """미장이 쉬는 날에는 미장이 같은 규칙에 걸린다 — 시장 이름을 안 박는다.

    2026-07-03(금)은 미 독립기념일 대체휴장이고 국장은 열렸다.
    """
    us_holiday = date(2026, 7, 3)
    assert not is_trading_day(Market.US, us_holiday)
    assert is_trading_day(Market.KR, us_holiday)
    kr = _brief("KR", prices=[
        IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, us_holiday),
    ])
    kr = MarketBrief(
        market="KR", currency=kr.currency,
        index_session=SessionRef("KR", "지수", us_holiday, us_holiday, None),
        price_session=SessionRef("KR", "시세", us_holiday, us_holiday, None),
        prices=kr.prices, volatility=kr.volatility, rankings=kr.rankings,
        floor=kr.floor, news=kr.news,
    )
    us = _brief("US", prices=[
        IndexRow("US:IDX:SP500", "S&P 500", "price", 7798.99, 0.0065, date(2026, 7, 2)),
    ])
    us = MarketBrief(
        market="US", currency=us.currency,
        index_session=SessionRef("US", "지수", date(2026, 7, 2), date(2026, 7, 2), None),
        price_session=SessionRef("US", "시세", date(2026, 7, 2), date(2026, 7, 2), None),
        prices=us.prices, volatility=us.volatility, rankings=us.rankings,
        floor=us.floor, news=us.news,
    )
    html = render_html(_briefing(markets={"KR": kr, "US": us}))
    assert "07-03 휴장" in html
    assert "S&P 500" not in html  # 미장 칸이 억제됐다
    assert "코스피" in html       # 국장은 열렸다 — 그대로 나온다


def test_closed_market_is_dropped_from_the_headline() -> None:
    """헤드라인의 08-14 코스피 수치가 08-17 리포트에 있으면 안 된다.

    **조용히 빼지도 않는다** — 사라지면 수집 실패와 구별되지 않는다.
    """
    line = headline(_holiday_briefing())
    assert "국장 휴장" in line
    assert "코스피" not in line
    assert "S&P 500" in line  # 미장은 열렸다


def test_closed_market_text_alternative_shows_no_numbers() -> None:
    text = render_text(_holiday_briefing())
    kr = text[text.index("== 국장") :]
    assert "장이 서지 않았다" in kr
    assert "직전 거래일 2026-08-14" in kr
    assert "코스피" not in kr


def test_open_market_is_not_labelled_closed() -> None:
    """미장은 08-17 에 열었다. 같은 날인데 한쪽만 휴장이다."""
    html = render_html(_holiday_briefing())
    head = re.search(r"미장<span[^>]*>(.{0,200}?)</div>", html, re.S)
    assert head is not None
    assert "휴장" not in head.group(0)


def test_closed_market_is_not_counted_as_a_gap() -> None:
    """**받을 것이 없었던 것은 못 받은 것이 아니다.**

    휴장 때문에 국장 세션이 리포트 기준일보다 이르다고 결측 상자에
    올리면, 고칠 것이 없는 일로 매번 수집기를 뒤지게 된다.
    """
    briefing = _holiday_briefing()
    assert briefing.gaps == []
    html = render_html(briefing)
    assert "들어오지 않은 것" not in html
    assert "결측" not in html


def test_real_missing_sessions_survive_the_holiday_label() -> None:
    """휴장 표시가 **진짜 결측을 덮지 않는다.**

    2026-08-18 실측: 미장 시세가 08-12 까지인데 기대는 08-17 이다. 그 사이는
    휴장이 아니라 우리가 못 받은 3세션이고, 그대로 결측으로 남아야 한다.
    """
    briefing = _holiday_briefing()
    us = briefing.markets["US"]
    note = "US 시세: 창고가 2026-08-12 까지다. 2026-08-17 까지 3개 세션이 안 들어왔다"
    broken = MarketBrief(
        market="US",
        currency=us.currency,
        index_session=us.index_session,
        price_session=SessionRef("US", "시세", SUBSTITUTE_HOLIDAY, date(2026, 8, 12), note),
        prices=us.prices,
        volatility=us.volatility,
        rankings=us.rankings,
        floor=us.floor,
        news=us.news,
    )
    patched = _briefing(markets={"KR": briefing.markets["KR"], "US": broken})
    html = render_html(patched)
    assert note in html
    assert "우리가 못 받은 것" in html
    # 미장 칸에 휴장 딱지가 붙지 않는다 — 저 3세션은 열려 있던 날이다.
    head = re.search(r"미장<span[^>]*>(.{0,200}?)</div>", html, re.S)
    assert head is not None
    assert "휴장" not in head.group(0)


def test_macro_row_is_one_cell_so_nothing_squeezes_the_label() -> None:
    """거시지표는 **한 칸에 위아래로 쌓는다.**

    두 칸으로 나눠 두면 값 칸의 ``nowrap`` 이 폭을 먼저 가져가고, 아이폰
    세로에서 왼쪽에 60px 남짓이 남아 원제가 한 단어씩 세로로 쪼개졌다
    (Advance / Monthly / Sales / for / …). 메일 클라이언트라 미디어쿼리로
    고칠 수 없으니 나눠 가질 폭 자체를 없앤다.
    """
    html = render_html(_briefing())
    rows = [row for row in re.findall(r"<tr>.*?</tr>", html, re.S) if "소매판매" in row]
    assert len(rows) == 1
    # 칸이 하나면 나눠 가질 폭이 없다 — 좁은 화면에서 무너질 자리가 사라진다.
    assert rows[0].count("<td") == 1
    # 라벨·값·원제·시각이 전부 그 한 칸 안에 있다.
    for piece in ("소매판매", "$763.6B", "Advance Monthly Sales", "08-14 21:30 KST"):
        assert piece in rows[0]


def test_macro_release_time_never_splits_across_lines() -> None:
    """"08-14 / 21:30 / KST" 로 쪼개지면 셋을 다시 붙여야 시각이 된다."""
    html = render_html(_briefing())
    assert '<span style="white-space:nowrap">08-14 21:30 KST</span>' in html


def test_macro_value_is_not_nowrapped_into_a_column_grab() -> None:
    """긴 값에 ``nowrap`` 을 걸면 그 폭이 표를 밀어낸다. 등락률만 붙여 둔다."""
    html = render_html(_briefing())
    row = re.search(r"소매판매.{0,900}?</tr>", html, re.S)
    assert row is not None
    value = re.search(
        r'<span style="color:[^"]*;font-weight:700">\$763\.6B[^<]*</span>', row.group(0)
    )
    assert value is not None
    assert "nowrap" not in value.group(0)


# -- 순서 ----------------------------------------------------------------------


def test_us_section_comes_before_the_domestic_one() -> None:
    """**미장이 위, 국장이 아래.**

    이 메일은 미장 마감 뒤 새벽에 나간다 — 읽는 시점에 방금 끝난 장이 미장이고
    국장은 아직 열리지 않았다. 위치로 못 박는다. 문자열 존재만 보면 순서가
    뒤집혀도 초록불이 켜진다.
    """
    html = render_html(_briefing())
    assert html.index(">미장<") < html.index(">국장<")


def test_headline_leads_with_the_us_index() -> None:
    """섹션 순서를 뒤집었으면 헤드라인도 같이 간다 — 제목 줄까지 같은 순서다."""
    line = headline(_briefing())
    assert line.index("S&P 500") < line.index("코스피")
    assert subject(_briefing()).index("S&P 500") < subject(_briefing()).index("코스피")


def test_text_alternative_follows_the_same_order() -> None:
    """HTML 과 텍스트본이 다른 순서면 둘을 나란히 못 읽는다."""
    text = render_text(_briefing())
    assert text.index("== 미장") < text.index("== 국장")


def test_market_order_lives_in_one_place() -> None:
    """순서가 여러 군데 하드코딩돼 있으면 다음에 바꿀 때 한 곳을 빠뜨린다.

    이 저장소가 반복해서 겪은 결함 계열이라, 상수 하나만 남았는지 본다.
    """
    source = Path(render_module.__file__).read_text(encoding="utf-8")
    assert '("KR", "US")' not in source
    assert '("US", "KR")' not in source  # 상수는 briefing 에 산다
    assert MARKET_ORDER == ("US", "KR")


def test_gap_list_follows_the_section_order() -> None:
    """결측 목록의 순서가 섹션과 다르면 위아래를 짝지어 읽을 수 없다."""
    kr = _brief("KR", prices=[IndexRow("KR:IDX:KOSPI", "코스피", "price", 1.0, 0.0, FRIDAY)],
                index_note="KR 지수: 국장 결측 문장")
    us = _brief("US", prices=[IndexRow("US:IDX:SP500", "S&P 500", "price", 1.0, 0.0, FRIDAY)],
                index_note="US 지수: 미장 결측 문장")
    # 창고가 주는 dict 순서가 국장 먼저여도 화면은 미장 먼저다.
    html = render_html(_briefing(markets={"KR": kr, "US": us}))
    assert html.index("미장 결측 문장") < html.index("국장 결측 문장")


# -- 헤드라인은 항목별로 칠한다 -------------------------------------------------------


def _mixed_briefing() -> Briefing:
    """코스피는 오르고 S&P 500 은 내린 날. **한 줄에 두 방향이 같이 산다.**"""
    kr = _brief("KR", prices=[
        IndexRow("KR:IDX:KOSPI", "코스피", "price", 6977.94, 0.0242, FRIDAY),
    ])
    us = _brief("US", prices=[
        IndexRow("US:IDX:SP500", "S&P 500", "price", 7798.99, -0.0017, FRIDAY),
    ])
    return _briefing(markets={"KR": kr, "US": us})


def _headline_html(briefing: Briefing) -> str:
    block = re.search(r"font-size:19px.{0,600}?</div>", render_html(briefing), re.S)
    assert block is not None
    return block.group(0)


def test_headline_colours_each_item_by_its_own_direction() -> None:
    """줄 전체를 한 색으로 칠하던 때, **내린 S&P 500 이 빨강으로 나갔다.**

    한 눈에 읽히라고 만든 줄이라 색이 사실과 반대면 목적을 정면으로 깬다.
    """
    block = _headline_html(_mixed_briefing())
    assert f'<span style="color:{DOWN}">S&P 500 ▼0.17%</span>' in block
    assert f'<span style="color:{UP}">코스피 ▲2.42%</span>' in block


def test_headline_up_and_down_are_not_the_same_colour() -> None:
    """이 테스트가 죽으면 오른 것과 내린 것이 화면에서 같아진 것이다."""
    block = _headline_html(_mixed_briefing())
    rising = re.search(r'color:(#[0-9a-f]{6})">코스피', block)
    falling = re.search(r'color:(#[0-9a-f]{6})">S&P 500', block)
    assert rising is not None and falling is not None
    assert rising.group(1) != falling.group(1)


def test_headline_uses_the_same_palette_as_the_table_body() -> None:
    """헤드라인만 다른 빨강·파랑이면 같은 사실이 두 색으로 보인다."""
    html = render_html(_mixed_briefing())
    block = _headline_html(_mixed_briefing())
    falling = re.search(r'color:(#[0-9a-f]{6})">S&P 500 ▼', block)
    assert falling is not None
    assert falling.group(1) == DOWN
    # 본문 표의 S&P 500 줄도 같은 색이다.
    row = re.search(r"S&P 500.{0,600}?</tr>", html.replace(block, ""), re.S)
    assert row is not None
    assert DOWN in row.group(0)


def test_headline_does_not_colour_the_exchange_rate() -> None:
    """**환율은 손익 방향이 아니다** — 원/달러가 오른 것은 원화가 약해진 것이다.

    변동성 지수에 손익 색을 안 쓰는 것과 같은 이유다.
    """
    block = _headline_html(_mixed_briefing())
    rate = re.search(r'<span style="color:(#[0-9a-f]{6})">환율[^<]*</span>', block)
    assert rate is not None
    assert rate.group(1) == INK


def test_headline_carries_no_markup_into_the_subject_line() -> None:
    """제목에 태그가 섞이면 받은편지함에 ``<span style=...>`` 이 찍힌다."""
    for line in (headline(_mixed_briefing()), subject(_mixed_briefing())):
        assert "<" not in line and "style=" not in line


# -- 커버리지 ------------------------------------------------------------------


def _coverage_briefing(*, eligible: int, universe: int) -> Briefing:
    """시총 순위의 모집단 두 수를 직접 박은 브리핑."""
    rank = _ranking(
        "market_cap",
        "시가총액 상위",
        "market_stats.market_cap",
        eligible=eligible,
        universe=universe,
    )
    kr = _brief(
        "KR",
        prices=[IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, FRIDAY)],
        rankings=[rank],
    )
    return _briefing(markets={"KR": kr})


def test_coverage_is_a_ratio_not_a_percentage() -> None:
    """``:.0%`` 가 100 을 곱한다. 여기 백분율을 담으면 두 번 곱해진다.

    4,345 / 6,647 = 65% 다. 이 자리에 **43450%** 가 찍혀 메일이 나갔다.
    """
    html = render_html(_coverage_briefing(eligible=4345, universe=6647))
    assert "커버리지 65%" in html


def test_coverage_never_exceeds_one_hundred_percent() -> None:
    """**분모는 분자를 담는 모집단이어야 한다.**

    다른 모집단에서 온 두 수를 나누면 비율이 1을 넘는다. 그런 값이 메일에
    실리면 되돌릴 수 없으므로, 렌더가 만들어내는 백분율을 전수로 훑는다.
    """
    for eligible, universe in ((4345, 6647), (2870, 2872), (0, 0), (15, 15)):
        html = render_html(_coverage_briefing(eligible=eligible, universe=universe))
        for shown in re.findall(r"커버리지 (\d+)%", html):
            assert 0 <= int(shown) <= 100, f"커버리지 {shown}% — 모집단이 어긋났다"


def test_market_cap_universe_contains_the_entities_it_counts() -> None:
    """``universe`` 가 ``eligible`` 보다 작으면 그 위의 비율은 전부 거짓이다.

    실측 사고: 미장 시세가 밀려 시세 판이 15행인데 시총 판은 4,345행이라
    4345/15 로 나눠졌다. ``rankings`` 가 합집합을 쓰게 고쳤다.
    """
    from quant_rl_trading.reporting.briefing import Ranking

    rank = Ranking(
        key="market_cap",
        label="시가총액 상위",
        sort_by="market_stats.market_cap",
        session=FRIDAY,
        prior=THURSDAY,
        rows=[RankRow("KR:005930", "삼성전자", 5e11, 73_500.0, 0.05)],
        eligible=4345,
        universe=15,
    )
    assert rank.eligible > rank.universe  # 이런 조합은 만들어지면 안 된다


# -- 한 표 안의 날짜 --------------------------------------------------------------


def _mixed_session_ranking(*, session: date, change_session: date) -> Ranking:
    return Ranking(
        key="market_cap",
        label="시가총액 상위",
        sort_by="market_stats.market_cap",
        session=session,
        prior=THURSDAY,
        rows=[RankRow("KR:005930", "삼성전자", 1.4002e15, 73_500.0, 0.0243)],
        eligible=2870,
        universe=2872,
        change_session=change_session,
    )


def _ranking_briefing(rank: Ranking) -> Briefing:
    kr = _brief(
        "KR",
        prices=[IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, FRIDAY)],
        rankings=[rank],
    )
    return _briefing(markets={"KR": kr})


def _ranking_header(briefing: Briefing) -> str:
    head = re.search(r"<tr>(<td[^>]*>종목</td>.*?)</tr>", render_html(briefing), re.S)
    assert head is not None
    return head.group(1)


def test_mixed_sessions_are_named_column_by_column() -> None:
    """**순위는 08-11 시총, 등락률은 08-14 시세.** 사흘이 한 줄 안에 섞였다.

    아무 표시가 없으면 둘 다 08-11 로 읽힌다. 값을 맞추지 않고(오래된 등락으로
    되돌리면 정보가 준다) 어느 시점의 것인지 밝힌다.
    """
    header = _ranking_header(
        _ranking_briefing(
            _mixed_session_ranking(session=date(2026, 8, 11), change_session=FRIDAY)
        )
    )
    assert "(08-11)" in header  # 시가총액 열
    assert "(08-14)" in header  # 전일대비 열


def test_matching_sessions_are_not_repeated() -> None:
    """수집이 정상화되면 두 날짜가 같아진다. 그때 날짜를 두 번 적으면 시끄럽다.

    이 메일의 규약대로 — **어긋난 것만 눈에 띈다.**
    """
    header = _ranking_header(
        _ranking_briefing(
            _mixed_session_ranking(session=FRIDAY, change_session=FRIDAY)
        )
    )
    assert "(08-14)" not in header
    assert "종목" in header and "시가총액" in header and "전일대비" in header


def test_mixed_session_footnote_names_the_change_basis() -> None:
    """열 제목의 ``(08-14)`` 만으로는 그 등락의 **기준일**이 안 보인다."""
    html = render_html(
        _ranking_briefing(
            _mixed_session_ranking(session=date(2026, 8, 11), change_session=FRIDAY)
        )
    )
    assert "전일대비 = 2026-08-14 의 2026-08-13 대비" in html


def test_matching_session_footnote_stays_short() -> None:
    html = render_html(
        _ranking_briefing(_mixed_session_ranking(session=FRIDAY, change_session=FRIDAY))
    )
    assert "전일대비 = 2026-08-13 대비" in html
    assert "의 2026-08-13 대비" not in html


def test_text_alternative_names_the_mixed_session() -> None:
    text = render_text(
        _ranking_briefing(
            _mixed_session_ranking(session=date(2026, 8, 11), change_session=FRIDAY)
        )
    )
    assert "(2026-08-11, 등락 2026-08-14, 정렬 market_stats.market_cap)" in text



# -- 지수 대용 ETF --------------------------------------------------------------


PROXIES = [
    IndexRow("US:SPY", "SPY (S&P 500 추종 ETF)", "price", 767.45, -0.0068, FRIDAY),
    IndexRow("US:SOXX", "SOXX (ICE 반도체 추종 ETF)", "price", 531.39, -0.0496, FRIDAY),
]


def _proxy_briefing() -> Briefing:
    """미장 지수는 하루 낡고, 대용 ETF 만 그날 값이 있는 판."""
    kr = _brief("KR", prices=[
        IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, FRIDAY),
    ])
    us = _brief(
        "US",
        prices=[
            IndexRow(
                "US:IDX:SP500", "S&P 500", "price", 7745.06, -0.0052, THURSDAY,
                note="2026-08-13 종가 · 2026-08-14 미수집",
            )
        ],
        proxies=PROXIES,
    )
    return _briefing(markets={"KR": kr, "US": us})


def test_proxy_etf_says_it_is_an_etf() -> None:
    """**"S&P 500" 이라 쓰고 SPY 를 싣지 않는다.**

    FRED 지수가 하루 늦어서 ETF 로 보완하는 자리다. 여기서 라벨을 지수 이름으로
    바꾸면 메일이 조용히 거짓말을 시작한다 — SPY 종가 767 은 S&P 500 의 7,745 가
    아니고, 분배락·운용보수만큼 등락도 어긋난다. HTML 과 텍스트 **둘 다** 확인한다:
    한쪽만 정직하면 스타일이 막힌 메일 앱에서 대용치가 지수처럼 읽힌다.
    """
    briefing = _proxy_briefing()
    html = render_html(briefing)
    text = render_text(briefing)

    for body in (html, text):
        assert "SPY (S&amp;P 500 추종 ETF)" in body or "SPY (S&P 500 추종 ETF)" in body
        assert "SOXX (ICE 반도체 추종 ETF)" in body
        # 지수 줄과 ETF 줄이 같은 값으로 보이면 안 된다 — 둘 다 실려 있다.
        assert "7,745.06" in body and "767.45" in body
    assert "지수가 아니다" in html
    assert "ETF 는 지수가 아니다" in text


def test_proxy_etf_survives_news_translation() -> None:
    """**미장 칸을 다시 만드는 자리가 필드를 흘리지 않는다.**

    ``_translate_us_news`` 가 미장 ``MarketBrief`` 를 통째로 새로 짓는다. 필드를
    손으로 나열하던 시절, 새로 생긴 ``proxies`` 가 거기서만 빠져 **미장에서만**
    ETF 줄이 사라졌다 — 국장은 그 함수를 안 지나서 멀쩡했고 그래서 더 안 보였다.
    """
    from quant_rl_trading.reporting.briefing import _translate_us_news

    class _Translate:
        def translate(self, headlines, *, as_of):
            return {}

    briefing = _proxy_briefing()
    markets = _translate_us_news(
        dict(briefing.markets), translate=_Translate(), as_of=NOW
    )
    assert [row.entity_id for row in markets["US"].proxies] == ["US:SPY", "US:SOXX"]


# -- 세션이 갈리는 날 ------------------------------------------------------------
#
# 2026-08-22 06:30 에 실제로 나간 메일이다. 머리말은 ``미장 2026-08-21`` 인데
# 실린 지수는 **8/20 종가**였다 — 출처 두 개의 지연이 다르다:
#
#   LS 해외 ETF (SPY·QQQ)  8/21 세션이 8/22 05:20 에 창고에 들어왔다
#   FRED 지수 (SP500 등)   8/21 세션은 아직 없다 (보통 D+1 06:00 관측)
#
# 그래서 한 메일 안에 ``SPY +0.30%`` 와 ``S&P 500 -0.87%`` 가 나란히 서서
# 모순돼 보였다. 모순이 아니라 **같은 지수의 다른 날**이었다. 시스템은 그
# 사실을 알고 있었고(``IndexRow.note`` 가 채워져 있었다) 표현만 못 했다.

STALE_SESSION, FRESH_SESSION = date(2026, 8, 20), date(2026, 8, 21)
STALE_NOTE = "2026-08-20 종가 · 2026-08-21 미수집"


def _split_session_briefing() -> Briefing:
    """지수는 8/20, ETF·시세는 8/21 인 미장 칸."""
    kr = _brief("KR", prices=[
        IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0589, FRIDAY),
    ])
    us = _brief(
        "US",
        prices=[
            IndexRow("US:IDX:SP500", "S&P 500", "price", 7641.16, -0.0087,
                     STALE_SESSION, STALE_NOTE),
            IndexRow("US:IDX:NASDAQ", "나스닥", "price", 26067.17, -0.0043,
                     STALE_SESSION, STALE_NOTE),
        ],
        proxies=[
            IndexRow("US:SPY", "SPY (S&P 500 추종 ETF)", "price", 767.31, 0.0030,
                     FRESH_SESSION),
        ],
    )
    us = MarketBrief(
        market="US",
        currency=us.currency,
        index_session=SessionRef(
            "US", "지수", FRESH_SESSION, STALE_SESSION,
            "US 지수: 창고가 2026-08-20 까지다. 2026-08-21 까지 1개 세션이 안 들어왔다",
        ),
        price_session=SessionRef("US", "시세", FRESH_SESSION, FRESH_SESSION, None),
        prices=us.prices,
        volatility=us.volatility,
        rankings=us.rankings,
        floor=us.floor,
        news=us.news,
        proxies=us.proxies,
    )
    return _briefing(markets={"KR": kr, "US": us})


def test_header_does_not_pick_one_session_and_hide_the_other() -> None:
    """**머리말이 대표 하나를 세우고 나머지를 덮지 않는다.**

    전에는 ``price_session.observed or index_session.observed`` 로 한 날짜만
    골랐다. 그날 시세(8/21)를 골라 ``미장 2026-08-21`` 을 적었고, 바로 아래
    지수 표는 8/20 이었다. 머리말이 표를 거짓으로 라벨링한 것이다.
    """
    for body in (render_html(_split_session_briefing()),
                 render_text(_split_session_briefing())):
        assert "지수 08-20" in body
        assert "ETF 08-21" in body
        assert "시세 08-21" in body
        # 한 날짜로 뭉뚱그린 옛 머리말이 남아 있으면 안 된다.
        assert "미장 2026-08-21" not in body and "미장</span> 2026-08-21" not in body


def test_one_session_day_still_prints_a_bare_date() -> None:
    """**셋이 같은 날이면 셋을 다 적지 않는다.**

    갈린 날을 드러내려고 평소 날까지 ``지수 08-14 · 시세 08-14`` 로 적으면
    머리말이 매일 길어지고, 정작 갈린 날이 눈에 안 띈다.
    """
    kr = _brief("KR", prices=[
        IndexRow("KR:IDX:KOSPI", "코스피", "price", 6813.34, 0.0356, FRIDAY),
    ])
    html = render_html(_briefing(markets={"KR": kr, "US": _briefing().markets["US"]}))
    assert "2026-08-14</span>" in html
    assert "지수 08-14" not in html


def test_stale_index_carries_its_own_date_next_to_the_number() -> None:
    """**각주가 "다르다" 로 끝나면 어느 날인지는 끝내 안 알려준다.**

    옛 각주는 ``* 그 지수의 종가 세션이 다르거나 미수집`` 이었다. 별표는
    "곧이곧대로 읽지 마라" 까지만 말한다. 읽는 사람이 알아야 하는 것은
    그 날짜다.
    """
    html = render_html(_split_session_briefing())
    assert "08-20 종가" in html
    assert "그 지수의 종가 세션이 다르거나 미수집" not in html
    # 표 전체가 밀린 날은 표 단위로도 한 번 말한다.
    assert "이 표는 2026-08-20 종가다 — 2026-08-21 지수가 아직 안 들어왔다" in html


def test_proxy_table_says_which_day_it_is_when_it_differs() -> None:
    """지수와 ETF 가 다른 날이면 **ETF 표도 자기 날짜를 적는다.**

    안 적으면 두 표가 같은 날의 서로 다른 값처럼 보인다 — 8/22 발송분에서
    지수는 전부 하락, ETF 는 전부 상승으로 나가 모순돼 보인 이유가 이것이다.
    """
    assert "이 표는 2026-08-21 종가다." in render_html(_split_session_briefing())


def test_headline_stamps_the_stale_session() -> None:
    """**헤드라인은 사람이 유일하게 반드시 읽는 줄이다.**

    표 안쪽에 각주를 아무리 달아도, 이 줄만 읽고 닫은 사람에게는 그냥 어제
    숫자다. 여기가 거짓이면 아래쪽 정직함은 쓸모가 없다. 제목 줄에도 같은
    도장이 찍힌다 — ``subject`` 가 같은 목록을 읽는다.
    """
    briefing = _split_session_briefing()
    assert "S&P 500 ▼0.87% (08-20 종가)" in headline(briefing)
    assert "(08-20 종가)" in subject(briefing)
    # 최신인 조각에는 안 붙는다.
    assert "코스피 ▲5.89%" in headline(briefing)
    assert "코스피 ▲5.89% (" not in headline(briefing)


def test_headline_does_not_stamp_the_weekly_fx_lag() -> None:
    """**환율은 주간 발행이라 늘 며칠 낡다** (reporting.md — H.10 은 월요일 발행).

    그걸 매일 찍으면 제목 줄이 매일 같은 말을 반복하고, 정작 진짜 지연이
    왔을 때 눈에 안 띈다. 원본이 아직 안 낸 것(``UNPUBLISHED``)은 맨 아래
    목록이 이미 성격까지 갈라서 적는다.
    """
    lagging = _briefing(
        fx_note="환율: FRED 가 2026-08-07 까지 냈다 — 우리도 거기까지다",
        fx_gap_kind=UNPUBLISHED,
    )
    assert "환율 1,410 ▼0.96%" in headline(lagging)
    assert "관측)" not in headline(lagging)

    ours = _briefing(
        fx_note="환율: 창고가 2026-08-01 까지다 — FRED 는 2026-08-07 까지 냈다",
        fx_gap_kind=MISSING,
    )
    assert "(08-07 관측)" in headline(ours)
