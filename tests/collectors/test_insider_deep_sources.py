"""내부자 재료 심화(2026-10-13) — 국장 원문 파서 · 미장 Form 4 일별 파서 · 공통 피처 창.

무효가 되는 길: 보고사유를 잘못 읽어 증여를 매도로 세기, 정정본·4/A 를 두 번 세기, 접수 전 거래를 창에 넣기,
일상 거래를 기회적 거래로 세기.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import insider_deep as deep
from quant_rl_trading.collectors import dart_insider_reports as ir
from quant_rl_trading.collectors import sec_form4_daily as daily

KR_DOC = """
<TABLE><TBODY>
<TR><TD>보고구분</TD><TU AUNIT="RPT_DST" AUNITVALUE="2">변동</TU><TU AUNIT="IFR_TP" AUNITVALUE="D">개인(국내)</TU></TR>
<TR><TE ACODE="IFR_NM">홍길동</TE><TE ACODE="IFR_ID2">700101</TE></TR>
<TR><TU AUNIT="STF_RYN" AUNITVALUE="Y">등기임원</TU><TE ACODE="STF_PSM">대표이사</TE><TU AUNIT="MAIN_SH" AUNITVALUE="4">-</TU></TR>
<TR><TE ACODE="FLT_SUM">27,931,470</TE></TR>
<TR ACOPY="Y">
<TU AUNIT="RPT_RSN" AUNITVALUE="34" ENG="Donation(-)">증여(-)</TU>
<TU AUNIT="MDF_DM" AUNITVALUE="20250319">2025년 03월 19일</TU>
<TU AUNIT="STR_KND" AUNITVALUE="1">보통주</TU>
<TE ACODE="BFR_STK_CNT">1,433,085</TE><TE ACODE="MDF_STK_CNT">-1,153,770</TE><TE ACODE="AFR_STK_CNT"> 279,315 </TE>
<TE ACODE="ACI_AMT2">-</TE><TE ACODE="RMK">수증자 : 윤인호</TE>
</TR>
<TR ACOPY="Y">
<TU AUNIT="RPT_RSN" AUNITVALUE="01">장내매수(+)</TU>
<TU AUNIT="MDF_DM" AUNITVALUE="20250320">2025년 03월 20일</TU>
<TU AUNIT="STR_KND" AUNITVALUE="1">보통주</TU>
<TE ACODE="BFR_STK_CNT">279,315</TE><TE ACODE="MDF_STK_CNT">1,000</TE><TE ACODE="AFR_STK_CNT">280,315</TE>
<TE ACODE="ACI_AMT2">취득&cr;(9,250)</TE><TE ACODE="RMK">-</TE><TU AUNIT="TRD_PLN_RD" AUNITVALUE="-">-</TU>
</TR>
<TR><TD>합계</TD><TE ACODE="MDF_STK_SUM">-1,152,770</TE></TR>
</TBODY></TABLE>
"""


def test_국장_원문_보고사유와_단가를_가른다() -> None:
    parsed = ir.parse(KR_DOC)
    rows = ir.to_rows(parsed, rcept_no="20250324000591", entity_id="KR:000020", moment=datetime(2025, 3, 24, 9, tzinfo=UTC))
    assert [r["category"] for r in rows] == ["transfer", "market"], "증여는 매도가 아니다"
    gift, buy = rows
    assert gift["change"] == -1153770.0 and gift["price"] is None and gift["change_date"] == "2025-03-19"
    assert buy["price"] == 9250.0 and buy["reason_code"] == "01" and buy["plan_report_date"] == ""
    assert buy["registered"] == "Y" and buy["position"] == "대표이사" and buy["reporter_type"] == "D"
    assert buy["shares_outstanding"] == 27931470.0
    assert "700101" not in str(buy.values()), "생년월일 원값을 싣지 않는다"


def test_보고사유_범주() -> None:
    assert ir.category("장내매도(-)") == "market" and ir.category("시간외매매(-)") == "market"
    assert ir.category("장외매수(+)") == "offmarket"
    assert ir.category("주식매수선택권(+)") == "grant" and ir.category("자사주상여금(+)") == "grant"
    assert ir.category("신규선임(+)") == "role" and ir.category("주식매수청구권 행사(-)") == "corporate"
    assert ir.category("기타(+)") == "other"


FORM4_XML = """<SEC-DOCUMENT><XML>
<ownershipDocument>
<documentType>4</documentType>
<issuer><issuerCik>0000320193</issuerCik><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0001</rptOwnerCik><rptOwnerName>Cook Tim</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<aff10b5One>1</aff10b5One>
<nonDerivativeTable><nonDerivativeTransaction>
<transactionDate><value>2026-09-29</value></transactionDate>
<transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>200.5</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>900</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument></XML></SEC-DOCUMENT>"""


def test_미장_일별_원문은_벌크와_같은_열로_읽는다() -> None:
    parsed = daily.parse(FORM4_XML)
    assert parsed is not None
    vf, ob = datetime(2026, 9, 30, 22, tzinfo=UTC), datetime(2026, 10, 13, 12, tzinfo=UTC)
    rows, why = daily.to_rows(parsed, accession="0001-26-1", filed=date(2026, 9, 30), valid_from=vf, observed_at=ob, tickers={})
    assert why == "" and len(rows) == 1
    r = rows[0]
    assert r["entity_id"] == "US:AAPL" and r["trans_code"] == "S" and r["price"] == 200.5 and r["plan_10b5_1"] == 1.0
    assert r["relationship"] == "Officer" and r["title"] == "CEO" and r["observed_at"] == ob and r["valid_from"] == vf
    amended = daily.parse(FORM4_XML.replace("<documentType>4<", "<documentType>4/A<"))
    assert daily.to_rows(amended, accession="x", filed=date(2026, 9, 30), valid_from=vf, observed_at=ob, tickers={})[1] == "not_form4"


def test_색인은_정정본을_빼고_두_번_실린_접수를_하나로() -> None:
    text = "\n".join([
        "4                A CORP     1     20261001    edgar/data/1/0001-26-1.txt",
        "4                OWNER X    2     20261001    edgar/data/1/0001-26-1.txt",
        "4/A              A CORP     1     20261001    edgar/data/1/0001-26-2.txt",
        "8-K              A CORP     1     20261001    edgar/data/1/0001-26-3.txt",
    ])
    assert daily.form4_paths(text) == ["edgar/data/1/0001-26-1.txt"]


def _trades(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["entity_id", "filed", "owner", "side", "value", "exec", "planned"])


def test_일상_거래는_직전_3년_같은_달() -> None:
    t = _trades([
        ("A", date(2021, 3, 5), "o1", -1, 1.0, False, False),
        ("A", date(2022, 3, 5), "o1", -1, 1.0, False, False),
        ("A", date(2023, 3, 5), "o1", -1, 1.0, False, False),
        ("A", date(2024, 3, 5), "o1", -1, 1.0, False, False),   # 3년 연속 3월 → 일상
        ("A", date(2024, 4, 5), "o1", -1, 1.0, False, False),   # 4월은 처음 → 비일상
        ("A", date(2024, 3, 5), "o2", -1, 1.0, False, False),   # 다른 보고자
    ])
    assert deep.routine_flags(t).tolist() == [False, False, False, True, False, False]


def test_창은_접수_다음_세션부터_세고_ADV_로_나눈다() -> None:
    sessions = list(pd.bdate_range("2025-01-01", periods=80).date)
    adv20 = pd.DataFrame(1000.0, index=pd.Index(sessions), columns=["A"])
    t = _trades([
        ("A", sessions[10], "o1", 1, 500.0, True, False),    # 세션 11 부터 보인다
        ("A", sessions[10], "o2", 1, 100.0, False, False),
        ("A", sessions[12], "o3", -1, 300.0, False, True),   # 계획 매도 — 매도 피처에서 빠진다
    ])
    f = deep.features(t, sessions, adv20).set_index("session")
    assert sessions[10] not in f.index, "접수일 당일 개장 전엔 모른다"
    row = f.loc[sessions[11]]
    assert row["opp_buy_60"] == 0.6 and row["buyers_20"] == 2 and row["exec_net_60"] == 0.5
    later = f.loc[sessions[13]]
    assert later["opp_sell_60"] == 0.0 and later["sellers_20"] == 0 and np.isclose(later["mkt_net_60"], 0.3)
    assert f.loc[sessions[30], "buyers_20"] == 2 and f.loc[sessions[31], "buyers_20"] == 0, "20세션 창 = [i-19, i]"
    assert sessions[71] not in f.index or f.loc[sessions[71], "opp_buy_60"] == 0.0, "60세션 창 밖"
