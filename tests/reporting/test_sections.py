"""거시지표·뉴스 섹션.

두 섹션 모두 **없는 것을 있는 것처럼 만들기가 쉬운 자리**다.

- 거시: 일정(``scheduled``)과 발표(``released``)가 한 테이블에 산다. 섞으면
  아직 안 나온 지표가 나온 것처럼 나간다
- 뉴스: 하루 수백 건에서 몇 건을 고른다. 기준을 안 밝히면 "왜 이건 있고
  저건 없나" 에 아무도 답할 수 없다. **공시(dart)가 아니라 기사(newsapi)다**
  — 공시는 뺐다(``briefing.NEWS_SOURCE``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from quant_rl_trading.reporting import briefing as briefing_module
from quant_rl_trading.store import Store

NOW = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)
FRIDAY, THURSDAY = date(2026, 8, 14), date(2026, 8, 13)


def _at(day: date, hour: int = 7) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _macro(
    entity: str,
    *,
    market: str,
    scheduled: datetime,
    status: str,
    actual: float | None,
    previous: float | None = None,
    name: str = "지표",
    unit: str = "%",
) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": _at(THURSDAY),
        "observed_at": _at(THURSDAY),
        "source": "test",
        "market": market,
        "indicator": entity,
        "release_name": name,
        "scheduled_at": scheduled,
        "actual": actual,
        "previous": previous,
        "unit": unit,
        "status": status,
    }


def _doc(
    entity: str, doc_id: str, title: str, *, day: date = FRIDAY, source: str = "newsapi"
) -> dict[str, Any]:
    """기사 한 건. **source 는 newsapi 다** — dart 공시는 이 섹션에서 뺐다."""
    return {
        "entity_id": entity,
        "valid_from": _at(day),
        "observed_at": _at(day),
        "source": source,
        "doc_id": doc_id,
        "doc_type": "article",
        "title": title,
        "filer": "",
        "url": f"https://news.example/{doc_id}",
        "raw_path": "",
    }


def _price(
    entity: str, day: date, close: float, value: float, *, market: str = "KR"
) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": _at(day),
        "observed_at": _at(day),
        "source": "test",
        "market": market,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": value / close,
        "value": value,
        "adj_factor": 1.0,
    }


# -- 거시지표 ------------------------------------------------------------------


def test_scheduled_without_actual_is_not_a_release(store: Store) -> None:
    """**``actual`` 이 없는 것은 발표된 것이 아니다.**

    아직 안 나온 지표를 나온 것처럼 적는 것이 이 섹션이 할 수 있는 가장
    나쁜 거짓말이다. **예정 목록도 싣지 않는다** — 좁은 화면에서 "발표됨"
    과 "예정" 을 확실히 가를 자리가 없으면 안 싣는 편이 안전하다.
    """
    store.seed_config_defaults()
    store.append(
        "macro_releases",
        [
            # status 는 released 인데 실측이 없다 — 수집 중간 상태다.
            _macro("KR:CPI", market="KR", scheduled=_at(FRIDAY, 8),
                   status="released", actual=None),
            # 아직 예정.
            _macro("US:CPI", market="US", scheduled=_at(date(2026, 8, 20), 12),
                   status="scheduled", actual=None),
        ],
        ingest_run_id="macro-1",
    )
    macro = briefing_module.build_briefing(store, as_of=NOW).macro
    assert macro.released == [], "actual 없는 행이 발표로 잡혔다"


def test_released_carries_previous_but_no_consensus(store: Store) -> None:
    """직전값은 있고 컨센서스는 없다 — 우리는 예상치를 수집하지 않는다."""
    store.seed_config_defaults()
    store.append(
        "macro_releases",
        [
            _macro("US:RETAIL", market="US", scheduled=_at(FRIDAY, 12),
                   status="released", actual=763.6, previous=768.0, name="소매판매"),
        ],
        ingest_run_id="macro-2",
    )
    macro = briefing_module.build_briefing(store, as_of=NOW).macro
    assert len(macro.released) == 1
    row = macro.released[0]
    assert row.source_name == "소매판매"
    assert row.actual == 763.6
    assert row.previous == 768.0
    # 컨센서스 자리가 아예 없다. 없는 열은 만들지 않는다.
    assert not hasattr(row, "consensus")
    assert not hasattr(row, "surprise")


def test_change_is_percentage_points_for_percent_units(store: Store) -> None:
    """금리·실업률처럼 값 자체가 %인 지표는 변화를 %p 로 잰다.

    3.50 → 3.63 을 "+3.7%" 라고 쓰면 금리가 3.7% 오른 것으로 읽힌다.
    실제로는 0.13%p 다.
    """
    store.seed_config_defaults()
    store.append(
        "macro_releases",
        [
            _macro("US:FED_FUNDS", market="US", scheduled=_at(FRIDAY, 14),
                   status="released", actual=3.63, previous=3.50, name="Federal Funds Rate"),
        ],
        ingest_run_id="macro-fed",
    )
    row = briefing_module.build_briefing(store, as_of=NOW).macro.released[0]
    assert row.is_percent
    assert row.change == pytest.approx(0.13)
    assert row.change_unit == "%p"


def test_change_is_a_ratio_for_non_percent_units(store: Store) -> None:
    """소매판매처럼 값 자체가 금액인 지표는 변화를 비율(%)로 잰다."""
    store.seed_config_defaults()
    store.append(
        "macro_releases",
        [
            _macro("US:RETAIL_ADVANCE", market="US", scheduled=_at(FRIDAY, 12),
                   status="released", actual=763_602.0, previous=768_072.0,
                   name="Advance Retail", unit="mn_usd"),
        ],
        ingest_run_id="macro-retail",
    )
    row = briefing_module.build_briefing(store, as_of=NOW).macro.released[0]
    assert not row.is_percent
    assert row.change == pytest.approx(763_602.0 / 768_072.0 - 1.0)
    assert row.change_unit == "%"


def test_release_time_is_scheduled_at_not_valid_from(store: Store) -> None:
    """``valid_from`` 은 우리가 안 시각이고 발표 시각은 ``scheduled_at`` 이다."""
    store.seed_config_defaults()
    when = datetime(2026, 8, 14, 21, 30, tzinfo=UTC)
    store.append(
        "macro_releases",
        [
            _macro("US:PPI", market="US", scheduled=when, status="released",
                   actual=1.0, previous=0.9),
        ],
        ingest_run_id="macro-3",
    )
    macro = briefing_module.build_briefing(store, as_of=NOW).macro
    assert macro.released[0].released_at == when
    assert macro.released[0].released_at != _at(THURSDAY)


def test_future_release_never_counts_as_released(store: Store) -> None:
    """미래에 발표될 지표에 실측값이 들어 있어도 발표로 세지 않는다."""
    store.seed_config_defaults()
    store.append(
        "macro_releases",
        [
            _macro("US:NFP", market="US", scheduled=_at(date(2026, 8, 21), 12),
                   status="released", actual=5.0, previous=4.0),
        ],
        ingest_run_id="macro-4",
    )
    macro = briefing_module.build_briefing(store, as_of=NOW).macro
    assert macro.released == []


def test_missing_domestic_macro_is_named_not_hidden(store: Store) -> None:
    """국내 지표가 비면 그 사실을 적는다 — ECOS 키가 막혔던 이력이 있다."""
    store.seed_config_defaults()
    store.append(
        "macro_releases",
        [
            _macro("US:PPI", market="US", scheduled=_at(FRIDAY, 12),
                   status="released", actual=1.0, previous=0.9),
        ],
        ingest_run_id="macro-5",
    )
    macro = briefing_module.build_briefing(store, as_of=NOW).macro
    assert any("국내" in note for note in macro.notes)
    assert not any("미국" in note for note in macro.notes)


def test_empty_macro_says_so(store: Store) -> None:
    """섹션이 사라지면 읽는 사람이 "원래 없는 항목" 으로 안다."""
    store.seed_config_defaults()
    macro = briefing_module.build_briefing(store, as_of=NOW).macro
    assert macro.released == []
    assert macro.notes, "발표가 없으면 이유가 남아야 한다"


# -- 뉴스 ------------------------------------------------------------------
#
# **공시(dart)를 뺐다.** ``NEWS_SOURCE`` 가 ``newsapi`` 하나뿐이다. 아래는
# ``briefing.news_section`` 의 실제 선정 규칙을 그대로 검증한다:
# 그날 거래대금 상위 종목을 먼저 고르고, 그 다음 기사가 몰린 종목을 고른다.


def test_major_stock_gets_the_turnover_reason(store: Store) -> None:
    """그날 거래대금 상위 종목의 기사는 "거래대금 상위" 로 뽑힌다."""
    store.seed_config_defaults()
    store.append(
        "prices",
        [
            _price("KR:005930", THURSDAY, 70_000.0, 9e11),
            _price("KR:005930", FRIDAY, 73_500.0, 9e11),
        ],
        ingest_run_id="px-1",
    )
    store.append(
        "documents",
        [_doc("KR:005930", "d1", "삼성전자 실적 발표")],
        ingest_run_id="doc-1",
    )
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.rows[0].entity_id == "KR:005930"
    assert news.rows[0].reason == "거래대금 상위"


def test_non_major_stock_gets_the_article_count_reason(store: Store) -> None:
    """거래대금 상위가 아니어도 기사가 몰리면 뽑힌다 — 화제성으로 뽑힌 것임을 밝힌다."""
    store.seed_config_defaults()
    docs = [_doc("KR:900001", f"d{i}", f"화제 기사 {i}", day=FRIDAY) for i in range(3)]
    store.append("documents", docs, ingest_run_id="doc-2")
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    picked = {row.entity_id: row for row in news.rows}
    assert "KR:900001" in picked
    assert picked["KR:900001"].reason == "기사 3건"


def test_one_row_per_entity(store: Store) -> None:
    """한 종목에 한 줄만 준다 — 안 그러면 기사 많은 종목이 목록을 다 먹는다."""
    store.seed_config_defaults()
    docs = [_doc("KR:900001", f"d{i}", f"기사 {i}", day=FRIDAY) for i in range(5)]
    store.append("documents", docs, ingest_run_id="doc-3")
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert sum(1 for row in news.rows if row.entity_id == "KR:900001") == 1


def test_news_reports_how_many_were_cut(store: Store) -> None:
    """자르는 것은 피할 수 없다. **자른 사실을 함께 싣는 것**이 요구다."""
    store.seed_config_defaults()
    docs = [_doc(f"KR:9000{i:02d}", f"x{i}", "화제") for i in range(0, 39)]
    store.append("documents", docs, ingest_run_id="doc-4")
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.total == 39
    assert 0 < len(news.rows) <= 3, "config.reporting.news_rows 상한을 넘었다"
    assert news.criteria, "선별 기준이 비어 있다"


def test_news_keeps_the_source_url_and_title_verbatim(store: Store) -> None:
    """제목을 "해석" 하지 않는다. 원문과 출처를 그대로 옮긴다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("KR:900001", "d1", "삼성전자    3분기 실적 (잠정)")],
        ingest_run_id="doc-5",
    )
    row = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news.rows[0]
    # 정렬용 연속 공백만 접는다 — 낱말은 그대로다.
    assert row.title == "삼성전자 3분기 실적 (잠정)"
    assert row.url == "https://news.example/d1"


def test_news_does_not_cross_markets(store: Store) -> None:
    """미장 기사가 국장 칸에 들어가지 않는다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [
            _doc("KR:900001", "k1", "국장 기사"),
            _doc("US:AAPL", "u1", "US news"),
        ],
        ingest_run_id="doc-6",
    )
    markets = briefing_module.build_briefing(store, as_of=NOW).markets
    assert {row.entity_id for row in markets["KR"].news.rows} == {"KR:900001"}
    assert {row.entity_id for row in markets["US"].news.rows} == {"US:AAPL"}


def test_dart_source_is_excluded(store: Store) -> None:
    """공시(dart)는 뺐다 — newsapi 가 아닌 source 는 안 실린다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("KR:900001", "d1", "공시", source="dart")],
        ingest_run_id="doc-7",
    )
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.rows == []


def test_empty_news_says_so(store: Store) -> None:
    store.seed_config_defaults()
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.rows == []
    assert news.note is not None


def test_missing_us_news_names_the_collector_gap(store: Store) -> None:
    """미장 뉴스가 아예 없으면(수집기가 아직 안 돈다) 그 사실을 적는다.

    지어내지 않는다 — 국장 뉴스만 있고 미장은 창고에 한 건도 없는 것이
    2026-08 현재 실제 상태다. 수집기가 돌기 시작하면 이 테스트는 그대로
    두고 데이터만 채우면 된다(코드 변경 없음).
    """
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("KR:900001", "k1", "국장만 있다")],
        ingest_run_id="doc-8",
    )
    us_news = briefing_module.build_briefing(store, as_of=NOW).markets["US"].news
    assert us_news.rows == []
    assert "미장" in (us_news.note or "")


# -- 미장 뉴스 번역 --------------------------------------------------------------


class _StubTranslate:
    """``NewsTitleTranslate`` 흉내 — 실제 Claude 를 안 부른다."""

    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.calls: list[list[Any]] = []

    def translate(self, headlines: list[Any], *, as_of: datetime) -> dict[str, str]:
        self.calls.append(headlines)
        out = {}
        for headline in headlines:
            ko = self.table.get(headline.title)
            if ko:
                out[headline.fingerprint] = ko
        return out


def test_us_news_titles_get_translated_when_translator_is_given(store: Store) -> None:
    """``translate`` 를 넘기면 미장 제목에 ``title_ko`` 가 채워진다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("US:META", "u1", "META unveils new data center")],
        ingest_run_id="doc-9",
    )
    stub = _StubTranslate({"META unveils new data center": "메타, 새 데이터센터 공개"})
    row = briefing_module.build_briefing(
        store, as_of=NOW, translate=stub
    ).markets["US"].news.rows[0]
    assert row.title_ko == "메타, 새 데이터센터 공개"
    assert row.title == "META unveils new data center", "원문이 지워지면 안 된다"


def test_kr_news_is_never_sent_to_the_translator(store: Store) -> None:
    """국장은 이미 한국어다 — 번역기를 아예 안 부른다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("KR:900001", "k1", "국장 기사")],
        ingest_run_id="doc-10",
    )
    stub = _StubTranslate({})
    briefing = briefing_module.build_briefing(store, as_of=NOW, translate=stub)
    assert briefing.markets["KR"].news.rows[0].title_ko is None
    assert stub.calls == [], "국장 뉴스가 번역기로 넘어갔다"


def test_translator_none_leaves_titles_in_english(store: Store) -> None:
    """``translate=None``(기본값)이면 미장 제목은 그대로 영어다 — 결정론을 지킨다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("US:META", "u1", "META unveils new data center")],
        ingest_run_id="doc-11",
    )
    row = briefing_module.build_briefing(store, as_of=NOW).markets["US"].news.rows[0]
    assert row.title_ko is None
    assert row.title == "META unveils new data center"
