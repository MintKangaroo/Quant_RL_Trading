"""SEC Form 4 분기 벌크(6차 G7) — 파싱·관측 시각·피처 창.

G7 이 무효가 되는 길은 셋이다: 정정본을 두 번 세기, 접수 전에 보기, 계획매매를 정보로 읽기.
"""
from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime

import pandas as pd

from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.ranker_sources import GROUPS, form4_trading
from quant_rl_trading.collectors import sec_form4 as form4
from quant_rl_trading.collectors.edgar_filings import EdgarPolicy
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store.tables import table_names

SUBMISSION = [
    "ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE", "ISSUERCIK",
    "ISSUERNAME", "ISSUERTRADINGSYMBOL", "AFF10B5ONE",
]
TRANS = [
    "ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE",
    "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
]
OWNER = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]


def tsv(columns: list[str], rows: list[dict[str, str]]) -> str:
    lines = ["\t".join(columns)] + ["\t".join(row.get(c, "") for c in columns) for row in rows]
    return "\n".join(lines) + "\n"


def bulk() -> bytes:
    subs = [
        {"ACCESSION_NUMBER": "A1", "FILING_DATE": "02-JUN-2025", "DOCUMENT_TYPE": "4",
         "ISSUERCIK": "320193", "ISSUERTRADINGSYMBOL": "AAPL", "AFF10B5ONE": "0"},
        # 정정본 — 같은 거래를 다시 적는다. 빼야 한다.
        {"ACCESSION_NUMBER": "A2", "FILING_DATE": "05-JUN-2025", "DOCUMENT_TYPE": "4/A",
         "ISSUERCIK": "320193", "ISSUERTRADINGSYMBOL": "AAPL", "AFF10B5ONE": "0"},
        # 티커가 없는 발행사(매핑도 없음) — 버리고 센다.
        {"ACCESSION_NUMBER": "A3", "FILING_DATE": "02-JUN-2025", "DOCUMENT_TYPE": "4",
         "ISSUERCIK": "999", "ISSUERTRADINGSYMBOL": "NA", "AFF10B5ONE": ""},
    ]
    trans = [
        {"ACCESSION_NUMBER": a, "NONDERIV_TRANS_SK": f"{a}-1", "TRANS_DATE": "29-MAY-2025",
         "TRANS_CODE": "S", "TRANS_SHARES": "100", "TRANS_PRICEPERSHARE": "200.5",
         "TRANS_ACQUIRED_DISP_CD": "D", "SHRS_OWND_FOLWNG_TRANS": "900", "DIRECT_INDIRECT_OWNERSHIP": "D"}
        for a in ("A1", "A2", "A3")
    ]
    owners = [
        {"ACCESSION_NUMBER": "A1", "RPTOWNERCIK": "0001", "RPTOWNERNAME": "Cook Tim",
         "RPTOWNER_RELATIONSHIP": "Officer", "RPTOWNER_TITLE": "CEO"},
        {"ACCESSION_NUMBER": "A1", "RPTOWNERCIK": "0002", "RPTOWNERNAME": "Trust",
         "RPTOWNER_RELATIONSHIP": "TenPercentOwner"},
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SUBMISSION.tsv", tsv(SUBMISSION, subs))
        archive.writestr("NONDERIV_TRANS.tsv", tsv(TRANS, trans))
        archive.writestr("REPORTINGOWNER.tsv", tsv(OWNER, owners))
    return buffer.getvalue()


def test_정정본은_빼고_공동보고는_한_행이다() -> None:
    frame = form4.parse(bulk())
    assert sorted(frame["ACCESSION_NUMBER"]) == ["A1", "A3"], "4/A 를 넣으면 같은 거래를 두 번 센다"
    a1 = frame[frame["ACCESSION_NUMBER"] == "A1"].iloc[0]
    assert a1["RPTOWNERCIK"] == "0001"
    assert a1["relationship"] == "Officer,TenPercentOwner"


def test_관측_시각은_접수일_18시_ET_이고_티커_없는_행은_센다() -> None:
    policy = EdgarPolicy(clock=ReplayClock(datetime(2026, 9, 19, tzinfo=UTC)))
    rows, stats = form4.to_rows(form4.parse(bulk()), tickers={"0000320193": "AAPL"}, policy=policy)
    assert stats == {"rows": 1, "no_symbol": 1, "bad_date": 0, "not_yet": 0}
    row = rows[0]
    # 2025-06-02 18:00 EDT = 22:00 UTC. 거래일(5/29)이 아니다.
    assert row["valid_from"] == row["observed_at"] == datetime(2025, 6, 2, 22, tzinfo=UTC)
    assert row["entity_id"] == "US:AAPL" and row["trans_date"] == "2025-05-29"
    assert row["shares"] == 100.0 and row["price"] == 200.5 and row["plan_10b5_1"] == 0.0


def test_아직_접수_시각이_안_된_행은_싣지_않는다() -> None:
    policy = EdgarPolicy(clock=ReplayClock(datetime(2025, 6, 2, 21, tzinfo=UTC)))  # 17:00 ET
    rows, stats = form4.to_rows(form4.parse(bulk()), tickers={"0000320193": "AAPL"}, policy=policy)
    assert rows == [] and stats["not_yet"] == 1


def test_분기_목록() -> None:
    assert form4.quarters("2024q3", "2025q2") == [(2024, 3), (2024, 4), (2025, 1), (2025, 2)]


def test_G7_은_G4_와_다른_표를_읽는다() -> None:
    """같은 표를 쓰면 등록된 G4 의 미장 입력(전부 0)이 몰래 바뀐다."""
    assert "form4_trades" in table_names() and GROUPS["G7"] == ("form4_sell_60", "form4_sellers_20", "form4_buy_60")


# --------------------------------------------------------------------------- 피처


class FakeStore:
    def __init__(self, trades: pd.DataFrame) -> None:
        self.trades = trades

    def get(self, table: str, **_: object) -> pd.DataFrame:
        assert table == "form4_trades"
        return self.trades


class FakeAnalyst:
    """form4_trading 이 쓰는 것만 — store.get · price_panel · wide · market."""

    market = "US"
    wide = staticmethod(Analyst.wide)

    def __init__(self, trades: pd.DataFrame, sessions: list[date]) -> None:
        self.store = FakeStore(trades)
        self.sessions = sessions

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        return pd.DataFrame([
            {"entity_id": e, "session": s, "close": 10.0, "volume": 100.0, "value": 1000.0}
            for s in self.sessions for e in ("US:A", "US:B")
        ])


def test_계획매매는_매도에서_빠지고_보고자는_따로_센다() -> None:
    sessions = list(pd.bdate_range("2025-01-02", periods=70).date)
    late = datetime.combine(sessions[-5], datetime.min.time(), tzinfo=UTC)
    early = datetime.combine(sessions[0], datetime.min.time(), tzinfo=UTC)  # 60세션 창 밖
    trades = pd.DataFrame([
        {"entity_id": "US:A", "valid_from": late, "owner_cik": "1", "trans_code": "S", "shares": 10.0, "price": 10.0, "plan_10b5_1": 0.0},
        {"entity_id": "US:A", "valid_from": late, "owner_cik": "2", "trans_code": "S", "shares": 10.0, "price": 10.0, "plan_10b5_1": None},
        {"entity_id": "US:A", "valid_from": late, "owner_cik": "3", "trans_code": "S", "shares": 99.0, "price": 10.0, "plan_10b5_1": 1.0},
        {"entity_id": "US:A", "valid_from": early, "owner_cik": "4", "trans_code": "S", "shares": 99.0, "price": 10.0, "plan_10b5_1": 0.0},
        {"entity_id": "US:B", "valid_from": late, "owner_cik": "5", "trans_code": "P", "shares": 50.0, "price": 10.0, "plan_10b5_1": 0.0},
        {"entity_id": "US:B", "valid_from": late, "owner_cik": "6", "trans_code": "S", "shares": 5.0, "price": 0.0, "plan_10b5_1": 0.0},
    ])
    raw = form4_trading(FakeAnalyst(trades, sessions), datetime(2025, 4, 30, tzinfo=UTC))  # type: ignore[arg-type]
    # A: 계획 외 매도 두 건 200달러 ÷ ADV20 1,000 — 계획매매(3)와 창 밖(4)은 빠진다.
    assert raw.loc["US:A", "form4_sell_60"] == 0.2
    assert raw.loc["US:A", "form4_sellers_20"] == 2.0
    # B: 매수 500달러, 가격 0 매도는 금액이 없어 빠진다.
    assert raw.loc["US:B", "form4_buy_60"] == 0.5
    assert raw.loc["US:B", "form4_sell_60"] == 0.0


class FakeHttp:
    """경로별 응답. 새 폴더에 없으면(404) 옛 폴더를 봐야 한다."""

    def __init__(self, found: dict[str, bytes]) -> None:
        self.found = found
        self.urls: list[str] = []

    def get(self, url: str) -> object:
        import httpx

        self.urls.append(url)
        body = next((v for k, v in self.found.items() if k in url), None)
        request = httpx.Request("GET", url)
        return httpx.Response(200 if body else 404, content=body or b"", request=request)


def test_SEC_가_폴더를_바꿔도_두_경로를_다_본다() -> None:
    old = FakeHttp({"structureddata": b"PK-old"})
    assert form4.fetch(2025, 2, user_agent="x a@b.c", client=old) == b"PK-old"
    assert len(old.urls) == 2, "새 폴더(404) 다음에 옛 폴더를 봤어야 한다"
    new = FakeHttp({"datastandardsinnovation": b"PK-new"})
    assert form4.fetch(2026, 2, user_agent="x a@b.c", client=new) == b"PK-new"
    import pytest

    with pytest.raises(form4.NotYetKnown):
        form4.fetch(2026, 3, user_agent="x a@b.c", client=FakeHttp({}))
