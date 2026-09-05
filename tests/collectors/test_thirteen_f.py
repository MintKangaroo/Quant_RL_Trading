"""13F 수집 — **틀린 값이 아니라 그럴듯한 값**을 만드는 함정 둘.

둘 다 아무 데서도 에러가 안 난다. 형식은 멀쩡하고 숫자도 나온다. 화면에 뜬
뒤에야 이상함을 눈치채는 종류라 테스트로 고정한다.
"""

from __future__ import annotations

import pytest

from quant_rl_trading.collectors.thirteen_f import (
    Filing,
    Holding,
    ThirteenFError,
    information_table_name,
    ingest_run_id,
    lag_days,
    observed_at_for,
    parse_information_table,
    recent_filings,
    to_rows,
)

NS = 'xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"'


def table(*rows: str) -> str:
    return f"<informationTable {NS}>{''.join(rows)}</informationTable>"


def row(issuer: str, cusip: str, value: int, shares: int, manager: str = "4") -> str:
    return f"""<infoTable>
      <nameOfIssuer>{issuer}</nameOfIssuer><cusip>{cusip}</cusip>
      <value>{value}</value>
      <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
      <otherManager>{manager}</otherManager>
    </infoTable>"""


# -- 함정 1: 한 종목이 여러 줄로 온다 -------------------------------------------


def test_같은_cusip_은_한_종목으로_접는다() -> None:
    """13F 는 **자회사 운용역별로 줄을 나눠** 낸다.

    버크셔 2026 Q2 실측: infoTable 89줄인데 실제 보유는 29종목이고 애플만
    12줄이었다. 접지 않고 정렬하면 "애플 7.8% · 애플 6.0% · 애플 3.4%" 가
    따로 등수에 오르고, **합쳐서 22.0% 1위인 사실이 사라진다.**
    """
    xml = table(
        row("APPLE INC", "037833100", 200_237_120, 691_000, manager="4"),
        row("APPLE INC", "037833100", 23_341_172_315, 80_664_820, manager="4,11"),
        row("APPLE INC", "037833100", 17_808_079_008, 61_542_988, manager="4,8,11"),
        row("COCA COLA CO", "191216100", 22_976_876_186, 282_722_729),
    )
    holdings = parse_information_table(xml)

    assert len(holdings) == 2, "CUSIP 이 같은데 따로 세고 있다"
    apple = next(h for h in holdings if h.cusip == "037833100")
    assert apple.value_usd == 200_237_120 + 23_341_172_315 + 17_808_079_008
    assert apple.shares == 691_000 + 80_664_820 + 61_542_988
    assert apple.rows == 3, "몇 줄을 접었는지 안 남기면 합산 사실이 사라진다"
    # 접고 나면 애플이 1위다. 접기 전에는 코카콜라가 1위로 보인다.
    assert holdings[0].cusip == "037833100"


def test_접은_줄_수가_행에_남는다() -> None:
    """합산은 가공이고, **가공한 숫자는 가공했다고 말해야 한다.**"""
    xml = table(
        row("APPLE INC", "037833100", 10, 1, manager="4"),
        row("APPLE INC", "037833100", 90, 9, manager="5"),
    )
    filing = Filing("1", "테스트", "2026-06-30", "2026-08-14", "acc")
    filing.holdings = parse_information_table(xml)
    rows = to_rows(filing)

    assert len(rows) == 1
    assert rows[0]["folded_rows"] == 2.0
    assert rows[0]["weight"] == pytest.approx(1.0), "혼자면 비중은 100% 다"


# -- 함정 2: value 는 달러다 ----------------------------------------------------


def test_value_를_천달러로_읽지_않는다() -> None:
    """2023년까지 13F 의 ``value`` 는 천 달러였고 그 시절 코드가 아직 많다.

    옛 규칙대로 1,000 을 곱하면 버크셔 포트폴리오가 **$299조**가 된다 —
    실제는 $299십억이다. 실측한 총액을 그대로 못 박는다.
    """
    xml = table(row("BERKSHIRE-ISH", "000000000", 299_253_556_246, 1))
    (holding,) = parse_information_table(xml)

    assert holding.value_usd == 299_253_556_246
    assert 250e9 < holding.value_usd < 350e9, "십억 자릿수가 아니다 — 단위를 잘못 읽었다"


# -- 지연 --------------------------------------------------------------------


def test_공개까지_걸린_날을_센다() -> None:
    """분기말 → 접수. 이 값을 화면이 안 보여주면 낡은 보유를 지금으로 읽는다."""
    filing = Filing("1067983", "Berkshire", "2026-06-30", "2026-08-14", "acc")
    assert lag_days(filing) == 45


def test_observed_at_은_접수_마감_뒤다() -> None:
    """그날 낸 공시를 그날 장중에 알았다고 하면 미래를 보는 것이 된다."""
    moment = observed_at_for("2026-08-14")
    assert moment.date().isoformat() == "2026-08-14"
    assert moment.hour >= 21, "접수 마감(17:30 ET) 전으로 잡혔다"


def test_valid_from_과_observed_at_이_벌어진다() -> None:
    """**이 간격이 이 표의 성격이다.** 좁히면 데이터가 거짓이 된다."""
    filing = Filing("1", "테스트", "2026-06-30", "2026-08-14", "acc")
    filing.holdings = [Holding("A", "000000000", 100.0, 1.0)]
    (record,) = to_rows(filing)
    assert (record["observed_at"] - record["valid_from"]).days == 45


# -- "없다" 와 "못 받았다" -------------------------------------------------------


def test_빈_신고와_형식_오류를_가른다() -> None:
    """보유를 다 정리한 분기는 실제로 있을 수 있다. 그건 0행이지 오류가 아니다."""
    assert parse_information_table(table()) == []
    with pytest.raises(ThirteenFError):
        parse_information_table("<이건 xml 이 아니다")


def test_표지만_있으면_보유_명세가_없다고_말한다() -> None:
    """``primary_doc.xml`` 은 표지다. 그걸 보유 목록으로 읽으면 0종목이 된다."""
    only_cover = {"directory": {"item": [{"name": "primary_doc.xml", "size": "5555"}]}}
    assert information_table_name(only_cover) is None


def test_보유_명세는_이름이_아니라_크기로_고른다() -> None:
    """파일 이름이 신고마다 다르다(예: ``56757.xml``) — 짐작하지 않는다."""
    index = {"directory": {"item": [
        {"name": "primary_doc.xml", "size": "5555"},
        {"name": "56757.xml", "size": "44724"},
        {"name": "0001-index.html", "size": "900"},
    ]}}
    assert information_table_name(index) == "56757.xml"


def test_13F_HR_만_고른다() -> None:
    """같은 기관이 13F-NT(보유 없음 통지) 등 다른 서식도 낸다."""
    payload = {"filings": {"recent": {
        "form": ["10-K", "13F-HR", "13F-NT", "13F-HR"],
        "accessionNumber": ["a", "b", "c", "d"],
        "reportDate": ["2026-06-30"] * 4,
        "filingDate": ["2026-08-14"] * 4,
    }}}
    got = recent_filings(payload, limit=5)
    assert [f["accession"] for f in got] == ["b", "d"]


def test_run_id_는_분기마다_바뀐다() -> None:
    """분기가 바뀌면 새로 받아야 한다 — adjfactor 처럼 고정이면 영영 막힌다."""
    q2 = ingest_run_id("1067983", "2026-06-30")
    q1 = ingest_run_id("1067983", "2026-03-31")
    assert q2 != q1
    assert "20260630" in q2


def test_비중은_수집기가_한_번만_계산한다() -> None:
    """화면에서 다시 나누면 분모를 무엇으로 잡았는지가 갈린다."""
    filing = Filing("1", "테스트", "2026-06-30", "2026-08-14", "acc")
    filing.holdings = [
        Holding("A", "000000001", 750.0, 10.0),
        Holding("B", "000000002", 250.0, 20.0),
    ]
    rows = to_rows(filing)
    assert [r["weight"] for r in rows] == [0.75, 0.25]
    assert sum(r["weight"] for r in rows) == pytest.approx(1.0)


def test_티커를_못_풀면_cusip_을_그대로_쓴다() -> None:
    """**모르는 것을 지어내지 않는다.** 매핑이 생기면 그때 정정본이 들어온다."""
    filing = Filing("1", "테스트", "2026-06-30", "2026-08-14", "acc")
    filing.holdings = [Holding("APPLE INC", "037833100", 100.0, 1.0)]
    (bare,) = to_rows(filing)
    assert bare["entity_id"] == "CUSIP:037833100"
    (mapped,) = to_rows(filing, cusip_to_entity={"037833100": "US:AAPL"})
    assert mapped["entity_id"] == "US:AAPL"
