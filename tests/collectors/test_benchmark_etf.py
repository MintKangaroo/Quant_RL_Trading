"""종료 판정 대조군 — **없으면 판정 자체가 불가능하다.**

2026-09-18 점검에서 창고의 KODEX200 이 0행이었다. ETF 는 유니버스에 없어 일상 수집
어디에도 안 걸렸고, 신선도 띠에도 없어 그 공백이 아무 데도 안 보였다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from quant_rl_trading.collectors import benchmark_etf as bench

OBSERVED = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)


def _frame(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"시가": p, "고가": p, "저가": p, "종가": p, "거래량": 1.0, "거래대금": 2.0,
          "기초지수": idx} for _d, p, idx in rows],
        index=pd.to_datetime([d for d, _p, _i in rows]),
    )


def test_ETF_와_기초지수를_함께_적는다() -> None:
    """같은 응답이 둘을 들고 온다. KRX 지수 경로는 하루 늦어 K200 에 구멍이 생긴다."""
    rows = bench.rows_from_frame(_frame([("2026-09-17", 106280.0, 1058.27)]), observed_at=OBSERVED)

    by_id = {r["entity_id"]: r for r in rows}
    assert set(by_id) == {bench.BENCHMARK_ETF, bench.UNDERLYING_INDEX}
    assert by_id[bench.BENCHMARK_ETF]["close"] == 106280.0
    assert by_id[bench.UNDERLYING_INDEX]["close"] == 1058.27
    assert by_id[bench.UNDERLYING_INDEX]["open"] is None, "지수는 종가뿐 — OHLC 를 지어내지 않는다"
    assert all(r["market"] == "KR" for r in rows)


def test_종가가_0인_날은_버린다() -> None:
    """KRX 는 휴장일에도 0 으로 채운 표를 준다. 그대로 적으면 '지수가 0이 됐다' 가 남는다."""
    rows = bench.rows_from_frame(
        _frame([("2026-09-16", 0.0, 0.0), ("2026-09-17", 106280.0, 1058.27)]),
        observed_at=OBSERVED,
    )
    days = {r["valid_from"].date().isoformat() for r in rows}
    assert days == {"2026-09-17"}


def test_기초지수가_없어도_ETF_는_남는다() -> None:
    rows = bench.rows_from_frame(_frame([("2026-09-17", 106280.0, 0.0)]), observed_at=OBSERVED)
    assert [r["entity_id"] for r in rows] == [bench.BENCHMARK_ETF]


def test_판정_벤치마크는_신선도_띠에_있다() -> None:
    """안 보이는 공백이 판정을 무효로 만든다 — 판정일에 발견하면 늦는다."""
    from quant_rl_trading.dashboard.services.freshness import DATASETS

    entities = {item[5] for item in DATASETS}
    assert bench.BENCHMARK_ETF in entities
