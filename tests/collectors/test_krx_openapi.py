"""KRX Open API 정규화 — 섹터.

``SECT_TP_NM`` 은 업종이 아니라 소속부다(store/tables.py 의 ``sectors`` 스펙
참조). 그래도 필드 자체의 정규화 규칙은 지켜야 한다 — 없는 것을 채우지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from quant_rl_trading.collectors.krx_openapi import normalize_sectors

VALID_FROM = datetime(2026, 8, 13, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 8, 13, 9, tzinfo=UTC)


def test_섹터가_있는_행만_남는다() -> None:
    rows = [
        {"code": "005930", "sector": "우량기업부"},
        {"code": "000660", "sector": ""},  # KOSPI 는 소속부가 빈 문자열이다
        {"code": "", "sector": "벤처기업부"},  # 코드가 없으면 종목을 특정 못 한다
    ]

    out = normalize_sectors(
        rows, market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT
    )

    assert len(out) == 1
    assert out[0]["entity_id"] == "KR:005930"
    assert out[0]["sector"] == "우량기업부"
    assert out[0]["valid_from"] == VALID_FROM
    assert out[0]["observed_at"] == OBSERVED_AT
    assert out[0]["market"] == "KR"


def test_모르는_섹터를_빈_문자열이나_기타로_채우지_않는다() -> None:
    """빈 섹터는 행 자체가 없어야 한다 — 값이 있는 척하면 selector 가
    모르는 종목들을 한 섹터로 묶어 상한을 엉뚱하게 적용한다."""
    rows = [{"code": "005930", "sector": None}]

    out = normalize_sectors(
        rows, market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT
    )

    assert out == []
