"""EDGAR 공시 수집 계약 (#38).

여기서 고정하는 것은 **분류 규칙과 이중시간**이다. 어느 폼이 어느 doc_type 이
되는지는 설계 결정이고, 그게 흔들리면 미장 Analyst 가 조용히 다른 세계를 본다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from quant_rl_trading.collectors.edgar_filings import (
    EdgarPolicy,
    NotYetKnown,
    classify,
    edgar_run_id,
    filing_rows,
    normalize,
    ticker_map,
)


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


# -- 분류 -----------------------------------------------------------------------


def test_실적발표는_차단이_아니라_사건이다() -> None:
    """**8-K 2.02 로 차단하면 어닝시즌마다 유니버스가 통째로 비운다.**

    실적발표는 사건이지 악재가 아니다. 방향은 실제 수치가 말한다.
    """
    assert classify("8-K", ("2.02",)) == "earnings"


def test_선반등록은_희석이_아니다() -> None:
    """S-3 는 등록일 뿐이다. 몇 년 안 찍는 회사가 많다 — 실제 발행은 424B."""
    assert classify("S-3", ()) == "other"
    assert classify("424B5", ()) == "dilution"


def test_회계_상폐_파산은_전부_distress() -> None:
    for item in ("1.03", "2.06", "3.01", "4.02"):
        assert classify("8-K", (item,)) == "distress", item


def test_보고_지연은_강한_위험_신호다() -> None:
    assert classify("NT 10-K", ()) == "distress"
    assert classify("NT 10-Q", ()) == "distress"


def test_항목이_여럿이면_가장_나쁜_것을_따른다() -> None:
    """실적발표와 상폐통보가 같이 왔으면 **그날 사건은 상폐통보다.**

    순서를 뒤집으면 위험한 공시가 실적으로 분류되어 조용히 통과한다.
    """
    assert classify("8-K", ("2.02", "3.01")) == "distress"
    assert classify("8-K", ("3.01", "2.02")) == "distress"


def test_모르는_항목은_버리지_않는다() -> None:
    """분류를 못 했다고 사실이 사라지는 것은 아니다. 공시가 몰리는 것 자체가
    정보다 (`dart_filings.OTHER` 와 같은 규약)."""
    assert classify("8-K", ("7.01",)) == "other"
    assert classify("무슨폼", ()) == "other"


# -- 매핑·정규화 ----------------------------------------------------------------


def test_CIK_는_10자리로_맞춘다() -> None:
    """EDGAR 응답의 CIK 는 자리수가 제각각이다. 안 맞추면 매핑이 통째로 빈다."""
    mapping = ticker_map({"0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple"}})
    assert mapping == {"0000320193": "AAPL"}


def test_매핑에_없는_CIK_는_세어서_보고한다() -> None:
    """조용히 버리면 수집이 절반만 들어와도 아무도 모른다."""
    hits = [
        {"_source": {"adsh": "1", "form": "8-K", "items": ["4.02"],
                     "ciks": ["0000000001"], "display_names": ["Unknown Co"],
                     "file_date": "2026-08-14"}},
    ]
    batch = normalize(hits, tickers={})
    assert batch.filings == ()
    assert batch.unmapped == 1


def test_공동제출은_양쪽_종목의_사건이다() -> None:
    hits = [
        {"_source": {"adsh": "9", "form": "8-K", "items": ["1.03"],
                     "ciks": ["0000000001", "0000000002"],
                     "display_names": ["A Corp", "B Corp"],
                     "file_date": "2026-08-14"}},
    ]
    batch = normalize(hits, tickers={"0000000001": "AAA", "0000000002": "BBB"})
    assert {f.entity_id for f in batch.filings} == {"US:AAA", "US:BBB"}
    assert all(f.doc_type == "distress" for f in batch.filings)


# -- 이중시간 -------------------------------------------------------------------


def test_관측시각은_그날_18시_ET_다() -> None:
    """**미장 종가는 16:00 ET 다.** 18:00 은 그 뒤라 그날 종가 결정에는 못 쓰고
    다음 날부터 쓴다 — 장중 공시를 그날 종가 피처에 넣으면 미래를 본다.
    """
    policy = EdgarPolicy(clock=FrozenClock(datetime(2026, 8, 20, tzinfo=UTC)))
    moment = policy.for_filing(date(2026, 8, 14))

    assert moment == datetime(2026, 8, 14, 22, 0, tzinfo=UTC)  # 18:00 EDT


def test_아직_알_수_없는_날짜는_거부한다() -> None:
    """불변식 3 — 그 시점에 알 수 없었던 사실은 창고에 들어가면 안 된다."""
    policy = EdgarPolicy(clock=FrozenClock(datetime(2026, 8, 14, 12, tzinfo=UTC)))

    with pytest.raises(NotYetKnown):
        policy.for_filing(date(2026, 8, 14))


def test_같은_공시가_같은_종목에_두_번_오면_한_번만_남는다() -> None:
    """자연키가 (entity_id, valid_from, doc_id) 라 창고가 거부하기 전에 접는다."""
    hits = [
        {"_source": {"adsh": "7", "form": "8-K", "items": ["4.02"],
                     "ciks": ["0000000001", "0000000001"],
                     "display_names": ["A Corp", "A Corp"],
                     "file_date": "2026-08-14"}},
    ]
    batch = normalize(hits, tickers={"0000000001": "AAA"})
    rows = filing_rows(batch.filings, observed_at=datetime(2026, 8, 14, 22, tzinfo=UTC))

    assert len(rows) == 1


def test_이력_키에_폼이_들어간다() -> None:
    """8-K 만 받은 날에 10-K 를 추가로 받아도 건너뛰면 안 된다."""
    assert edgar_run_id(date(2026, 8, 14), "8-K") != edgar_run_id(
        date(2026, 8, 14), "8-K,10-K"
    )
