"""N6 금융위 대차·보호예수 반환 수집기 (collectors/fsc_lending.py).

증명하는 것: 응답 파싱·오류 봉투(한도는 QuotaExceeded) · KR7 만 남김 · observed_at = 다음 거래일 13:00 KST ·
페이지 넘김 · 창고 적재 뒤 기준일 16:00 결정에는 안 보이고 다음 날 16:00 에 보인다 · 피처 하루 늦춤.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.collectors import fsc_lending as F
from quant_rl_trading.collectors.errors import CollectorError


def _body(items, total=None) -> str:
    return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL"},
                                    "body": {"items": {"item": items}, "totalCount": total if total is not None else len(items),
                                             "pageNo": 1, "numOfRows": 1000}}})


LEND = [{"basDt": "20250102", "isinCd": "KR7005930003", "isinCdNm": "삼성전자", "lnbCclStckCnt": "100",
         "lnbRdptStckCnt": "20", "lnbRmanStckCnt": "5,000"},
        {"basDt": "20250102", "isinCd": "HK0000057197", "isinCdNm": "외국", "lnbCclStckCnt": "0",
         "lnbRdptStckCnt": "0", "lnbRmanStckCnt": "10"}]


def test_파싱과_오류() -> None:
    items, total = F.parse(_body(LEND))
    assert total == 2 and items[0]["isinCd"] == "KR7005930003"
    env = json.dumps({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnReasonCode": "22"}}})
    with pytest.raises(F.QuotaExceeded):
        F.parse(env)
    with pytest.raises(CollectorError, match="활용 미승인"):
        F.parse(json.dumps({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnReasonCode": "20"}}}))


def test_행_변환_KR7만_다음_거래일_13시() -> None:
    rows = F.lending_rows(LEND)
    assert [r["entity_id"] for r in rows] == ["KR:005930"]
    r = rows[0]
    assert r["lend_balance"] == 5000.0 and r["lend_new"] == 100.0
    assert r["valid_from"] == datetime(2025, 1, 2, tzinfo=UTC)
    assert r["observed_at"] == datetime(2025, 1, 3, 4, 0, tzinfo=UTC)          # 1/3(금) 13:00 KST
    assert F.observed_at_for(date(2025, 1, 3)) == datetime(2025, 1, 6, 4, 0, tzinfo=UTC)   # 금 → 다음 월요일
    lock = F.lockup_rows([{"basDt": "20250102", "isinCd": "KR7217330000", "lkupRegDt": "20240102", "stckLblHoldRcd": "31",
                           "stckLblHoldRcdNm": "최대주주", "rsrnStckCnt": "1,000", "afrsRsqtCnt": "0"}])
    assert lock[0]["entity_id"] == "KR:217330" and lock[0]["returned_shares"] == 1000.0
    assert lock[0]["lockup_reg_date"] == "2024-01-02"


def test_페이지를_넘긴다() -> None:
    pages = {1: _body(LEND[:1], total=2), 2: _body([{**LEND[0], "isinCd": "KR7000660001"}], total=2)}
    seen = []

    def fetch(url: str) -> str:
        assert "serviceKey=" in url
        page = int(url.split("pageNo=")[1].split("&")[0])
        seen.append(page)
        return pages[page]

    client = F.DataGoKrClient(service_key="x", fetch=fetch)
    items = client.day(F.PATH_LENDING, date(2025, 1, 2))
    assert len(items) == 2 and seen == [1, 2] and client.calls == 2


def test_창고_가시성(store) -> None:  # type: ignore[no-untyped-def]
    store.append(F.TABLE_LENDING, F.lending_rows(LEND), ingest_run_id="t")
    same_day = store.get(F.TABLE_LENDING, as_of=datetime(2025, 1, 2, 7, 0, tzinfo=UTC), lookback=5)
    next_day = store.get(F.TABLE_LENDING, as_of=datetime(2025, 1, 3, 7, 0, tzinfo=UTC), lookback=5)
    assert same_day.empty and len(next_day) == 1


def test_피처는_하루_늦춘다() -> None:
    idx = pd.Index([date(2025, 1, d) for d in (2, 3, 6)])
    bal = pd.DataFrame({"KR:005930": [100.0, 200.0, 400.0]}, index=idx)
    sh = pd.DataFrame({"KR:005930": [1000.0, 1000.0, 1000.0]}, index=idx)
    f = F.lending_features(bal, sh)
    assert np.isnan(f["lend_ratio"].iloc[0, 0]) and f["lend_ratio"].iloc[1, 0] == pytest.approx(0.1)
    ret = pd.DataFrame({"KR:005930": [0.0, 50.0, np.nan]}, index=idx)
    lk = F.lockup_features(ret, sh, window=2)
    assert lk.iloc[2, 0] == pytest.approx(0.05)
