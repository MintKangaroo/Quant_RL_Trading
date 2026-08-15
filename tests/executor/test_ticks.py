"""호가단위(tick) 계약 테스트.

핵심 오라클: **실제 종가는 전부 유효 호가 위에 있다.** 그러니 창고의 실제
종가에 ``round_to_tick`` 을 먹이면 자기 자신이 나와야 한다. 표를 손으로
지어낸 픽스처가 아니라 실제 창고에서 뜬 값으로 검증한다 — 표가 현실과
어긋나면 이 테스트가 바로 잡는다.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from quant_rl_trading.executor.ticks import round_to_tick, tick_size, us_tick_size
from quant_rl_trading.schemas.order import Side

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICES_GLOB = str(REPO_ROOT / "data/curated/prices/observed_date=2026-08-1[234]/*.parquet")
UNIVERSE_GLOB = str(REPO_ROOT / "data/curated/universe/observed_date=2026-08-14/*.parquet")


def _tradable_kr_closes() -> list[float]:
    """실제 종가만 — 호가단위 규칙이 적용될 대상(체결 가능 종목)만 남긴다.

    청약기 스팩(``is_tradable=False``)은 뺀다 — 체결이 없는 지표성 가격이라
    호가단위를 지킬 이유가 없다는 걸 실제로 추적해서 확인했다(ticks.py
    모듈 docstring). 집행기도 애초에 이런 종목엔 주문을 내지 않는다.
    """
    con = duckdb.connect()
    con.execute("SET memory_limit='700MB'")
    query = f"""
        SELECT p.close
        FROM read_parquet('{PRICES_GLOB}') p
        JOIN read_parquet('{UNIVERSE_GLOB}') u ON p.entity_id = u.entity_id
        WHERE p.close > 0 AND p.entity_id LIKE 'KR:%' AND u.is_tradable = true
    """
    return [float(row[0]) for row in con.execute(query).fetchall()]


def test_warehouse_has_kr_closes_to_check() -> None:
    """오라클 자체가 비어 있으면 통과가 아니라 거짓 안심이다."""
    assert len(_tradable_kr_closes()) > 1000


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_real_closes_are_already_on_a_valid_tick(side: Side) -> None:
    """전수 검증: 실제 체결 가능 종목의 종가는 반올림해도 그대로다."""
    closes = _tradable_kr_closes()
    bad = [c for c in closes if round_to_tick(c, side=side) != c]
    assert not bad, f"호가단위를 벗어난 실제 종가 {len(bad)}건: {sorted(set(bad))[:20]}"


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (1_999, 1),
        (2_000, 5),
        (4_999, 5),
        (5_000, 10),
        (19_999, 10),
        (20_000, 50),
        (49_999, 50),
        (50_000, 100),
        (199_999, 100),
        (200_000, 500),
        (499_999, 500),
        (500_000, 1_000),
        (10_000_000, 1_000),
    ],
)
def test_tick_size_bands(price: float, expected: int) -> None:
    """구간 경계 — '이상'은 다음 칸, '미만'은 지금 칸."""
    assert tick_size(price) == expected


def test_tick_size_rejects_nonpositive_price() -> None:
    with pytest.raises(ValueError):
        tick_size(0)
    with pytest.raises(ValueError):
        tick_size(-100)


def test_buy_rounds_down() -> None:
    """매수는 내림 — 상한을 넘으면 상한보다 비싸게 사버린다."""
    # 55,576.5 원, tick=100 (50,000~200,000) → 내려서 55,500.
    assert round_to_tick(55_576.5, side=Side.BUY) == 55_500.0


def test_sell_rounds_up() -> None:
    """매도는 올림 — 하한을 내리면 하한보다 싸게 팔아버린다."""
    # 55,023.5 원, tick=100 → 올려서 55,100.
    assert round_to_tick(55_023.5, side=Side.SELL) == 55_100.0


def test_round_to_tick_is_stable_on_exact_multiples() -> None:
    """이미 유효 호가인 값은 방향에 상관없이 그대로 나온다."""
    for price in (1_500, 3_005, 12_340, 45_050, 123_400, 456_500, 1_234_000):
        assert round_to_tick(float(price), side=Side.BUY) == price
        assert round_to_tick(float(price), side=Side.SELL) == price


def test_round_to_tick_never_crosses_reference_direction() -> None:
    """매수 결과 ≤ 원값, 매도 결과 ≥ 원값 — 슬리피지 상한을 넘지 않는다."""
    for price in (2_001.3, 5_002.7, 20_010.9, 200_300.4, 500_700.1):
        assert round_to_tick(price, side=Side.BUY) <= price
        assert round_to_tick(price, side=Side.SELL) >= price


# -- 미장(US) 호가단위 -----------------------------------------------------------
#
# `docs/design/ls-api.md` §0-10 실측(g3104.untprc) 근거: AAPL($305.26)·
# WEN($8.65)·BRK.A($762,000) 전부 0.01, LTRYW($0.0070)만 0.0001.


@pytest.mark.parametrize(
    ("price", "expected"),
    [(0.0070, 0.0001), (0.99, 0.0001), (1.0, 0.01), (8.65, 0.01), (305.26, 0.01), (762_000.0, 0.01)],
)
def test_us_tick_size_matches_real_observations(price: float, expected: float) -> None:
    assert us_tick_size(price) == pytest.approx(expected)


def test_round_to_tick_market_kr_is_default() -> None:
    """market 인자를 안 주면 지금까지와 같은 국장 표를 쓴다 — 기존 호출부 계약."""
    assert round_to_tick(55_576.5, side=Side.BUY) == round_to_tick(
        55_576.5, side=Side.BUY, market="KR"
    )


def test_round_to_tick_us_does_not_use_kr_table() -> None:
    """회귀 재현: KR 표를 달러 가격에 먹이면 $50 가 $1 tick 으로 반올림된다
    (2026-08-15 발견). market='US' 를 넘기면 실제 미장 호가단위($0.01)를 쓴다.

    KR 표로 잘못 계산했을 때와 값이 달라야 이 수정이 실제로 걸린 것이다.
    """
    price = 50.3719
    kr_wrong = round_to_tick(price, side=Side.BUY, market="KR")
    us_correct = round_to_tick(price, side=Side.BUY, market="US")
    assert kr_wrong == 50.0  # KR 표: 50.3719 < 2,000 대역 → tick=1
    assert us_correct == pytest.approx(50.37)  # 실제 미장: tick=$0.01
    assert kr_wrong != us_correct


def test_round_to_tick_us_sub_penny() -> None:
    """$1 미만은 $0.0001 tick — LTRYW 급 저가주."""
    assert round_to_tick(0.00731, side=Side.BUY, market="US") == pytest.approx(0.0073)
    assert round_to_tick(0.00731, side=Side.SELL, market="US") == pytest.approx(0.0074)
