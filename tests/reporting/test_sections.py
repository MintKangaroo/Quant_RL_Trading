"""거시지표·뉴스 섹션.

두 섹션 모두 **없는 것을 있는 것처럼 만들기가 쉬운 자리**다.

- 거시: 일정(``scheduled``)과 발표(``released``)가 한 테이블에 산다. 섞으면
  아직 안 나온 지표가 나온 것처럼 나간다
- 뉴스: 하루 수천 건에서 몇 건을 고른다. 기준을 안 밝히면 "왜 이건 있고
  저건 없나" 에 아무도 답할 수 없다
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

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
        "unit": "%",
        "status": status,
    }


def _doc(
    entity: str, doc_id: str, doc_type: str, title: str, *, day: date = FRIDAY
) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": _at(day),
        "observed_at": _at(day),
        "source": "test",
        "doc_id": doc_id,
        "doc_type": doc_type,
        "title": title,
        "filer": "테스트",
        "url": f"https://dart.example/{doc_id}",
        "raw_path": "",
    }


def _price(entity: str, day: date, close: float, value: float) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": _at(day),
        "observed_at": _at(day),
        "source": "test",
        "market": "KR",
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
    나쁜 거짓말이다.
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
    assert [row.label for row in macro.upcoming] == ["지표"]


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
    assert row.label == "소매판매"
    assert row.actual == 763.6
    assert row.previous == 768.0
    # 컨센서스 자리가 아예 없다. 없는 열은 만들지 않는다.
    assert not hasattr(row, "consensus")
    assert not hasattr(row, "surprise")


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


# -- 뉴스·공시 -----------------------------------------------------------------


def test_distress_is_always_selected(store: Store) -> None:
    """거래정지·회생은 **규모와 무관하게** 중요하다.

    작은 회사의 거래정지가 큰 회사의 배당 공시보다 급하다.
    """
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("KR:900001", "d1", "distress", "회생절차종결신청")],
        ingest_run_id="doc-1",
    )
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.rows[0].entity_id == "KR:900001"
    assert news.rows[0].reason == "관리·정지·회생"


def test_ordinary_filing_needs_a_major_stock(store: Store) -> None:
    """실적·증자 같은 사건성 공시는 **거래대금 상위 종목의 것만** 싣는다.

    하루 공시가 수천 건이라 규모 기준이 없으면 아무 회사의 실적 정정이
    거래정지를 밀어낸다.
    """
    store.seed_config_defaults()
    store.append(
        "prices",
        [
            _price("KR:100001", THURSDAY, 50_000.0, 9e11),
            _price("KR:100001", FRIDAY, 51_000.0, 9e11),
        ],
        ingest_run_id="px-1",
    )
    store.append(
        "documents",
        [
            _doc("KR:100001", "big", "earnings", "분기보고서"),
            _doc("KR:900009", "small", "earnings", "분기보고서"),
        ],
        ingest_run_id="doc-2",
    )
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    picked = {row.entity_id for row in news.rows}
    assert "KR:100001" in picked, "거래대금 상위 종목의 실적이 빠졌다"
    assert "KR:900009" not in picked, "무명 종목의 실적이 걸러지지 않았다"
    assert news.rows[0].reason == "거래대금 상위 종목"


def test_news_reports_how_many_were_cut(store: Store) -> None:
    """자르는 것은 피할 수 없다. **자른 사실을 함께 싣는 것**이 요구다."""
    store.seed_config_defaults()
    docs = [_doc("KR:900001", "d1", "distress", "거래정지")]
    docs += [_doc(f"KR:9000{i:02d}", f"x{i}", "ownership", "지분변동") for i in range(2, 40)]
    store.append("documents", docs, ingest_run_id="doc-3")
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.total == 39
    assert len(news.rows) == 1
    assert news.criteria, "선별 기준이 비어 있다"


def test_news_keeps_the_source_url_and_title_verbatim(store: Store) -> None:
    """제목을 "해석" 하지 않는다. 원문과 출처를 그대로 옮긴다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [_doc("KR:900001", "d1", "distress", "주권매매거래정지    (주식의 병합)")],
        ingest_run_id="doc-4",
    )
    row = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news.rows[0]
    # 정렬용 연속 공백만 접는다 — 낱말은 그대로다.
    assert row.title == "주권매매거래정지 (주식의 병합)"
    assert row.url == "https://dart.example/d1"


def test_news_does_not_cross_markets(store: Store) -> None:
    """미장 공시가 국장 칸에 들어가지 않는다."""
    store.seed_config_defaults()
    store.append(
        "documents",
        [
            _doc("KR:900001", "k1", "distress", "국장 거래정지"),
            _doc("US:AAPL", "u1", "distress", "US halt"),
        ],
        ingest_run_id="doc-5",
    )
    markets = briefing_module.build_briefing(store, as_of=NOW).markets
    assert {row.entity_id for row in markets["KR"].news.rows} == {"KR:900001"}
    assert {row.entity_id for row in markets["US"].news.rows} == {"US:AAPL"}


def test_empty_news_says_so(store: Store) -> None:
    store.seed_config_defaults()
    news = briefing_module.build_briefing(store, as_of=NOW).markets["KR"].news
    assert news.rows == []
    assert news.note is not None
