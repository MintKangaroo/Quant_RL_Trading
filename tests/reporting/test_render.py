"""이메일 렌더링.

메일은 되돌릴 수 없다. 화면은 새로고침하면 되지만 잘못 나간 메일은 못
거둔다. 그래서 여기서 고정하는 것은 **틀리게 읽힐 여지**다.

1. 지수·등락률이 없으면 **빈칸이 아니라 ``—`` 와 이유**가 찍힌다
2. 변동성 지수에는 **손익 색이 없다** — VIX 상승은 수익이 아니라 공포다
3. **무엇으로 줄 세웠는지** 순위표마다 적힌다
4. 걸어 둔 **하한과 커버리지가 각주로 남는다** — 숨긴 필터는 못 믿는 필터다
5. **성과·수익률·보유 섹션이 없다** — 빈 표는 "손실 0" 으로 읽힌다
6. 인라인 CSS · 라이트 배경 · JS 없음 (reporting.md §3)
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from quant_rl_trading.reporting.briefing import (
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
from quant_rl_trading.reporting.render import DOWN, UP, render_html, render_text, subject
from quant_rl_trading.reporting.sessions import SessionRef

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
        news=kw.get(  # type: ignore[arg-type]
            "news",
            NewsSection(
                rows=[
                    NewsRow(
                        "KR:080720", "한국종합", "distress", "회생절차종결신청",
                        "https://dart.example/1", FRIDAY, "관리·정지",
                    )
                ],
                total=2990,
                criteria="distress 는 전부 · 그 밖은 거래대금 상위 100위 종목만",
            ),
        ),
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
    )
    macro = MacroSection(
        released=[
            MacroRow("US", "Advance Monthly Sales", 763_602.0, 768_072.0, "mn_usd",
                     datetime(2026, 8, 14, 12, 30, tzinfo=UTC)),
        ],
        upcoming=[
            MacroRow("US", "CPI", 0.0, None, "%", datetime(2026, 8, 20, 12, 30, tzinfo=UTC))
        ],
        notes=["국내 지표: 이 구간에 발표된 것이 없다"],
        since=THURSDAY,
    )
    payload: dict[str, object] = {
        "as_of": NOW,
        "fx": {"rate": 1409.94, "change": -0.0096, "sessions": ["2026-08-07"], "rates": [1409.94]},
        "fx_note": None,
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


def test_gaps_are_collected_at_the_top() -> None:
    html = render_html(_briefing())
    assert "들어오지 않은 것" in html
    assert "2026-08-14 까지 1개 세션이 안 들어왔다" in html


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


def test_upcoming_releases_are_not_shown_at_all() -> None:
    """**예정 목록은 화면에서 뺐다** (아이폰 분량). 데이터는 그대로 있다.

    빼는 쪽을 고른 이유: 섞이면 안 나온 지표가 나온 것처럼 읽히는데, 좁은
    화면에서 "발표됨" 과 "예정" 을 시각적으로 확실히 가르려면 자리가 든다.
    자리를 못 낼 바에는 **안 싣는 편이 안전하다** — 틀리게 읽힐 여지가 0 이다.
    """
    html = render_html(_briefing())
    assert "Advance Monthly Sales" in html, "발표된 것은 실린다"
    assert "CPI" not in html, "예정된 지표가 새어 나왔다"


def test_no_consensus_column_is_invented() -> None:
    """컨센서스를 수집하지 않으므로 "예상 대비" 를 쓸 수 없다."""
    html = render_html(_briefing())
    # 각주가 "컨센서스는 수집하지 않아 직전값 대비만" 이라고 스스로 말한다.
    # 그 문장까지 금칙어로 잡으면 면책 문구가 자기 검사에 걸린다 — 빼고 본다.
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


def test_news_discloses_how_many_were_cut() -> None:
    """하루 2,990건에서 1건을 골랐다는 사실이 보여야 한다."""
    html = render_html(_briefing())
    assert "2,990건 중 1건" in html
    assert "distress 는 전부" in html
    assert "규칙 선별(LLM 미사용)" in html


def test_news_keeps_the_source_link() -> None:
    assert 'href="https://dart.example/1"' in render_html(_briefing())


# -- 매매 리포트가 아니다 ----------------------------------------------------------


def test_no_performance_section() -> None:
    html = render_html(_briefing())
    disclaimer = "성과·보유는 넣지 않는다"
    assert disclaimer in html
    body = html.replace(disclaimer, "")
    for banned in ("수익률", "일간 손익", "보유 종목", "체결 내역", "액션 반영률", "NAV"):
        assert banned not in body, f"{banned} 섹션이 들어갔다 — 빈 표는 '손실 0' 으로 읽힌다"


# -- 이메일 제약 ----------------------------------------------------------------


def test_email_constraints() -> None:
    html = render_html(_briefing())
    assert "<script" not in html.lower()
    assert "<style" not in html.lower()
    assert 'style="' in html
    assert "background-color:#eceff1" in html


def test_body_stays_small_enough_for_gmail() -> None:
    """Gmail 은 본문 102KB 를 넘으면 잘라낸다(clipping)."""
    assert len(render_html(_briefing()).encode("utf-8")) < 102_000


def test_subject_uses_the_session_date_not_the_send_date() -> None:
    """08-15 새벽에 보내는 08-14 리포트다. 제목이 08-15 면 하루 어긋난다."""
    line = subject(_briefing())
    assert line.startswith("[시황] 2026-08-14")
    assert "코스피" in line and "S&P 500" in line
