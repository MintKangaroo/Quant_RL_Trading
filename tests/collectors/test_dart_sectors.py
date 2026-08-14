"""DART 업종 분류 — 진짜 업종(KSIC) 대 KRX 소속부.

여기서 고정하는 사실은 셋이다.

1. **표준산업분류 코드(``induty_code``)가 진짜 업종이다.** KRX 일별매매의
   ``SECT_TP_NM`` 은 소속부일 뿐이다(krx_openapi.py 모듈 docstring).
2. **모르는 종목은 행을 안 만든다.** "기타" 로 채우면 그 종목들이 한 섹터로
   묶여 상한이 엉뚱한 종목을 걸러낸다.
3. **파티션 폭발을 피한다.** ``store.append()`` 는 배치 전체를 한 번에
   쓴다 — 회사 수만큼 호출하지 않는다.

샘플은 실호출 응답 그대로다(2026-08-15, 삼성전자·큐캐피탈파트너스).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from quant_rl_trading.collectors.dart_sectors import (
    SECTOR_PREFIX,
    SOURCE,
    SectorCollector,
    normalize_sectors,
)
from quant_rl_trading.collectors.errors import CollectorError

VALID_FROM = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 8, 15, 3, 0, tzinfo=UTC)

#: 실호출 응답(2026-08-15). 유가증권.
SAMSUNG = {
    "status": "000", "message": "정상", "corp_code": "00126380",
    "corp_name": "삼성전자(주)", "stock_code": "005930", "corp_cls": "Y",
    "induty_code": "264", "est_dt": "19690113", "acc_mt": "12",
}
#: 실호출 응답(2026-08-15). 코스닥 — KOSPI 와 다른 업종 코드가 나온다는 것을 보인다.
QCAPITAL = {
    "status": "000", "message": "정상", "corp_code": "00267942",
    "corp_name": "큐캐피탈파트너스(주)", "stock_code": "016600", "corp_cls": "K",
    "induty_code": "649", "est_dt": "19821217", "acc_mt": "12",
}


# -- 정규화 ----------------------------------------------------------------------


def test_실호출_응답에서_업종코드를_뽑는다() -> None:
    rows = normalize_sectors(
        [("KR:005930", SAMSUNG), ("KR:016600", QCAPITAL)],
        market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT,
    )
    by_entity = {row["entity_id"]: row for row in rows}
    assert by_entity["KR:005930"]["sector"] == f"{SECTOR_PREFIX}264"
    assert by_entity["KR:016600"]["sector"] == f"{SECTOR_PREFIX}649"
    # 두 시장 구분(코스피·코스닥)이 아니라 서로 다른 업종 코드다 — 이게 KRX
    # 소속부와 다른 점이다.
    assert by_entity["KR:005930"]["sector"] != by_entity["KR:016600"]["sector"]


def test_소속부와_source가_다르다() -> None:
    """natural_key 가 넓어지기 전까지 이 컬럼이 두 스킴을 구분하는 유일한
    단서다(dart_sectors.py 모듈 docstring)."""
    row = normalize_sectors(
        [("KR:005930", SAMSUNG)], market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT
    )[0]
    assert row["source"] == SOURCE
    assert row["source"] != "krx_openapi"


def test_데이터_없음은_행을_안_만든다() -> None:
    """013(조회된 데이터 없음)은 company_info() 가 None 으로 돌려준다."""
    rows = normalize_sectors(
        [("KR:999999", None)], market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT
    )
    assert rows == []


def test_업종코드가_빈_응답도_행을_안_만든다() -> None:
    """모르는 종목을 '기타' 로 채우지 않는다."""
    blank = {**SAMSUNG, "induty_code": ""}
    rows = normalize_sectors(
        [("KR:005930", blank)], market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT
    )
    assert rows == []


# -- 수집 ------------------------------------------------------------------------


class _StubSource:
    """DartSource 를 흉내낸다. 네트워크를 안 탄다."""

    def __init__(self, answers: dict[str, dict[str, Any] | Exception | None]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def company_info(self, corp_code: str) -> dict[str, Any] | None:
        self.calls.append(corp_code)
        outcome = self.answers.get(corp_code)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_corp_code가_없으면_호출하지_않는다() -> None:
    """우선주 등 DART 미매핑 종목. 실패로 세지 않는다 — 시도할 수 없는
    것과 실패한 것은 다르다."""
    source = _StubSource({})
    collector = SectorCollector(source=source, pause_sec=0.0)

    report = collector.fetch({"005930": "00126380", "004255": ""})

    assert source.calls == ["00126380"]
    assert report.requested == 2
    assert report.fetched == 1
    assert report.no_corp_code == 1
    assert report.failures == []


def test_실패한_콜은_기록하되_나머지는_계속한다() -> None:
    source = _StubSource(
        {"00126380": SAMSUNG, "00267942": CollectorError("일시 장애")}
    )
    collector = SectorCollector(source=source, pause_sec=0.0)

    report = collector.fetch({"005930": "00126380", "016600": "00267942"})

    assert report.fetched == 1
    assert len(report.failures) == 1
    assert report.failures[0][0] == "KR:016600"
    assert [entity for entity, _ in report.rows] == ["KR:005930"]


def test_induty_없는_회사는_no_induty로_센다() -> None:
    blank = {**SAMSUNG, "induty_code": ""}
    source = _StubSource({"00126380": blank})
    collector = SectorCollector(source=source, pause_sec=0.0)

    report = collector.fetch({"005930": "00126380"})

    assert report.fetched == 1
    assert report.no_induty == 1


# -- 창고 통합: 파티션 폭발을 피한다 ------------------------------------------------


def test_배치_전체가_한_번의_append로_한_파티션에_쓰인다(store) -> None:
    """과거에 종목 축으로 store.append() 를 개별 호출해 파일 247만 개를
    만든 전례가 있다(모듈 docstring). 여기서는 한 번만 쓴다."""
    store.seed_config_defaults()
    rows = normalize_sectors(
        [(f"KR:{code:06d}", SAMSUNG) for code in range(200)],
        market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT,
    )
    written = store.append("sectors", rows, ingest_run_id="test-dart-sectors")

    assert written == 200
    files = list((store.root / "curated" / "sectors").rglob("*.parquet"))
    assert len(files) == 1, f"파티션이 하나여야 한다: {files}"


def test_기존_소속부와_같은_날이어도_둘_다_남는다(store) -> None:
    """**한때는 이게 반대로 실패했다.** ``sectors`` 의 natural_key 가
    ``(entity_id, valid_from)`` 뿐이던 시절엔 같은 종목·같은 날에 소속부
    (krx_openapi)와 업종(dart_company)을 같이 넣으면 게이트가 최신 관측
    하나만 돌려줬다 — 파일은 둘 다 남아도 ``store.get()`` 은 하나만 봤다.

    team-lead 가 `store/tables.py` 의 natural_key 를
    ``(entity_id, valid_from, source)`` 로 넓히면서 고쳤다(2026-08-15).
    이제는 이 컬렉터가 만든 행을 안심하고 프로덕션 창고에 그대로
    ``store.append()`` 할 수 있다 — 이 테스트가 그걸 증명한다. selector 쪽
    회귀는 `tests/selector/test_pipeline.py::test_분류체계가_둘이면_고른_쪽만_나온다`
    가 별도로 잡는다.
    """
    store.seed_config_defaults()
    kospi_row = {
        "entity_id": "KR:005930", "valid_from": VALID_FROM, "observed_at": OBSERVED_AT,
        "source": "krx_openapi", "market": "KR", "sector": "",  # KOSPI 는 빈 문자열
    }
    dart_row = normalize_sectors(
        [("KR:005930", SAMSUNG)], market="KR", valid_from=VALID_FROM,
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    )[0]

    store.append("sectors", [kospi_row], ingest_run_id="krx-sectors")
    store.append("sectors", [dart_row], ingest_run_id="dart-sectors")

    frame = store.get("sectors", as_of=OBSERVED_AT + timedelta(hours=1))
    by_source = {row["source"]: row["sector"] for row in frame.to_dict(orient="records")}
    assert by_source == {"krx_openapi": "", SOURCE: "KSIC:264"}
