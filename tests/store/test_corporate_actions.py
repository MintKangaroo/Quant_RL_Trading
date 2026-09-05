"""기업행위 보정 — 검출·저장·읽기 셋이 한 벌로 맞는다.

## 무엇을 지켜야 하나

창고에는 **원주가**가 든다. 액면분할·무상증자·감자·주식병합이 보정되지 않으면
실제 손실이 아닌 가격 급변이 수익률로 계산되고, 모멘텀 창이 250일이면 사건
하나가 그 뒤 250세션을 오염시킨다.

**그래서 이 파일이 지키는 것은 "adj_factor 가 채워졌다" 가 아니라 "분할이
수익률로 안 보인다" 다.** 컬럼이 차 있어도 접기가 틀리면 아무 소용이 없다.

동시에 **원주가가 필요한 자리가 안 바뀌는 것**도 같은 무게로 지킨다. 실제
주문은 원주가로 나가고, 분할 직후에는 주문 게이트(5일)·NAV(30일) 창 안에
사건이 들어와 **주문 수량이 배율만큼 틀어진다.** 돈이 걸린 쪽이다.

## 합성과 실측을 둘 다 둔다

합성만 두면 현실을 말해 주지 않고(상수 피처가 통과하던 전례가 있다), 실측만
두면 창고 없는 곳에서 못 돈다. 구조는 합성으로 못 박고, **실제 창고의 그
종목들**은 창고가 있을 때만 확인한다.

실측 대상은 LS 전수 조회로 확인한 것이다 (2026-08-15):

    KR:001080  2026-03-09  ×0.100000   분할
    KR:025560  2025-07-22  ×17.428571  감자
               2026-07-27  ×0.200000   분할
    KR:484870  2026-06-26  ×0.333333   무상증자
    KR:150840  사건 없음 — 거래정지 후 정리매매다. **실제 -98% 이고 건드리면 안 된다**
"""

from __future__ import annotations

import math
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quant_rl_trading.collectors.corporate_actions import (
    CorporateAction,
    detect_events,
    revision_rows,
)
from quant_rl_trading.session.daily import STATS_WINDOW, market_stats
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import adjust, read_prices

#: config/quant_rl_trading.yaml 의 corporate_action.min_log_factor.
MIN_LOG_FACTOR = 0.02

ENTITY = "KR:000100"
END = datetime(2026, 8, 14, tzinfo=UTC)


def _sessions(count: int, *, end: datetime = END) -> list[datetime]:
    """거래일 흉내. 주말만 건너뛴다 — 여기서 필요한 것은 순서지 달력이 아니다."""
    days: list[datetime] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _seed(
    store: Store,
    *,
    sessions: list[datetime],
    factor: float | None = None,
    split_at: int | None = None,
    entity: str = ENTITY,
    base: float = 10_000.0,
) -> None:
    """가격이 **한 푼도 안 움직이는** 종목 하나.

    진짜 수익률이 0 이어야 "분할이 수익률로 새어 나왔나" 를 잡을 수 있다.
    가격이 흔들리면 분할의 -90% 가 잡음에 섞여 안 보인다.
    """
    store.seed_config_defaults()
    rows = []
    for index, day in enumerate(sessions):
        is_split = split_at is not None and index == split_at
        # 분할 이후에는 실제로 가격이 배율만큼 내려간 값으로 거래된다.
        scale = factor if (split_at is not None and index >= split_at and factor) else 1.0
        close = base * scale
        rows.append({
            "entity_id": entity, "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR",
            "open": close, "high": close, "low": close, "close": close,
            "volume": 100_000.0, "value": 5_000_000_000.0,
            "adj_factor": factor if is_split else None,
        })
    store.append("prices", rows, ingest_run_id=f"seed-{entity}")


# -----------------------------------------------------------------------------
# 검출 — 계단에서 배율을 뽑는다
# -----------------------------------------------------------------------------


def _ratios(plateaus: list[tuple[int, float]], *, count: int = 60) -> dict[date, float]:
    """``(시작 인덱스, 그 자리부터의 비율)`` 로 비율 계열을 만든다.

    첫 항은 반드시 인덱스 0 에서 시작한다. 비율은 "그 날 이후에 발효할 사건들의
    누적곱" 이므로, 계열은 뒤로 갈수록 1 에 가까워지는 것이 보통이다.
    """
    days = [d.date() for d in _sessions(count)]
    out: dict[date, float] = {}
    for index, day in enumerate(days):
        value = plateaus[0][1]
        for start, ratio in plateaus:
            if index >= start:
                value = ratio
        out[day] = value
    return out


def _all_close(series: pd.Series, expected: float) -> bool:
    """``Series == pytest.approx(x)`` 는 원소별이 아니다 — 스칼라 하나를 낸다."""
    return bool((series.astype(float) - expected).abs().max() < 1e-6)


def test_a_clean_step_becomes_one_event() -> None:
    ratios = _ratios([(0, 0.1), (30, 1.0)])

    events = detect_events(ratios, entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)

    assert len(events) == 1
    assert events[0].effective_on == sorted(ratios)[30]
    assert events[0].factor == pytest.approx(0.1)


def test_rounding_noise_is_not_an_event() -> None:
    """KRX 가격이 정수라 비율이 ±0.05% 씩 흔들린다.

    실측(KR:006740)으로 2.31520~2.31664 사이를 100번 넘게 오갔다. 이웃 두 점을
    그냥 나누면 그 흔들림이 **전부 사건**이 되고, 없는 배율이 가격에 곱해진다.
    """
    days = [d.date() for d in _sessions(60)]
    ratios = {
        day: 2.3154 + (0.0012 if index % 2 else 0.0)
        for index, day in enumerate(days)
    }

    assert detect_events(ratios, entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR) == []


def test_the_factor_comes_from_plateau_medians_not_neighbours() -> None:
    """계단 양쪽의 **중앙값**끼리 나눈다.

    이웃 두 점만 보면 그 두 점에 실린 반올림 오차가 그대로 계수 오차가 된다.
    아래는 계단 직전 점만 0.4% 튀게 두었다 — 이웃 방식이면 계수가 0.4% 틀리고,
    중앙값 방식이면 흔들림이 지워진다.
    """
    days = [d.date() for d in _sessions(60)]
    ratios: dict[date, float] = {}
    for index, day in enumerate(days):
        if index < 30:
            ratios[day] = 0.1004 if index == 29 else 0.1
        else:
            ratios[day] = 1.0

    events = detect_events(ratios, entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)

    assert len(events) == 1
    assert events[0].factor == pytest.approx(0.1, rel=1e-6)


def test_capital_reduction_gives_a_factor_above_one() -> None:
    """감자·병합은 가격이 **올라가는** 쪽으로 보정된다. 실측 KR:025560 ×17.4."""
    ratios = _ratios([(0, 3.4857), (20, 0.2)])

    events = detect_events(ratios, entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)

    assert len(events) == 1
    assert events[0].factor == pytest.approx(17.4285, rel=1e-3)


def test_two_events_on_one_symbol_are_both_found() -> None:
    """KR:025560 의 실제 모양 — 감자 한 번, 1년 뒤 분할 한 번."""
    ratios = _ratios([(0, 3.4857), (15, 0.2), (35, 1.0)])

    events = detect_events(ratios, entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)

    assert len(events) == 2
    assert events[0].factor > 1.0, "감자가 1보다 큰 배율로 안 나왔다"
    assert events[1].factor == pytest.approx(0.2), "분할 배율이 틀렸다"
    assert events[0].effective_on < events[1].effective_on


# -----------------------------------------------------------------------------
# 정정본 — 행 전체를 다시 쓴다
# -----------------------------------------------------------------------------


def test_revision_row_copies_every_value_column() -> None:
    """값 컬럼을 하나라도 빠뜨리면 **그 세션 시세가 통째로 null 이 된다.**

    게이트가 자연키마다 최신 revision 하나만 고르므로, 정정본이 이긴 뒤에는
    원본 행을 아무도 못 본다.
    """
    day = date(2026, 3, 9)
    existing = {
        (ENTITY, day): {
            "observed_at": datetime(2026, 3, 9, 7, tzinfo=UTC),
            "source": "krx_openapi", "revision": 0, "market": "KR",
            "open": 5100.0, "high": 5200.0, "low": 4900.0, "close": 5010.0,
            "volume": 232_781.0, "value": 1.2e9,
        }
    }
    action = CorporateAction(
        entity_id=ENTITY, effective_on=day, factor=0.1,
        ratio_before=0.1, ratio_after=1.0,
    )

    rows = revision_rows([action], existing)

    assert len(rows) == 1
    row = rows[0]
    assert row["revision"] == 1
    assert row["adj_factor"] == pytest.approx(0.1)
    for name in ("open", "high", "low", "close", "volume", "value", "market"):
        assert row[name] == existing[(ENTITY, day)][name], f"{name} 이 안 옮겨졌다"
    # 관측시각은 원본 그대로다 — 그날 종가를 알 수 있었던 시각이면 계수도 알
    # 수 있었다. 새 정책을 만들지 않는다.
    assert row["observed_at"] == existing[(ENTITY, day)]["observed_at"]


def test_a_session_missing_from_the_warehouse_is_skipped() -> None:
    """창고에 없는 세션에 정정본을 얹으면 시세 없는 행이 생긴다."""
    action = CorporateAction(
        entity_id=ENTITY, effective_on=date(2026, 3, 9), factor=0.1,
        ratio_before=0.1, ratio_after=1.0,
    )

    assert revision_rows([action], {}) == []


# -----------------------------------------------------------------------------
# 접기 — 읽는 쪽
# -----------------------------------------------------------------------------


def test_a_split_stops_looking_like_a_return(store: Store) -> None:
    """**이 파일의 이유다.** 나머지는 이것의 따름이다.

    가격이 한 푼도 안 움직인 종목에 1/10 분할을 하나 넣는다. 보정 전에는
    그 하루가 -90% 로 보이고, 보정 후에는 수익률이 전부 0 이어야 한다.
    """
    sessions = _sessions(40)
    _seed(store, sessions=sessions, factor=0.1, split_at=20)
    as_of = sessions[-1] + timedelta(hours=9)

    raw = read_prices(store, as_of=as_of, market="KR", columns=["close"])
    raw_returns = raw.sort_values("valid_from")["close"].pct_change().dropna()
    assert raw_returns.min() == pytest.approx(-0.9), (
        "합성 데이터가 분할을 재현하지 못했다 — 이 테스트가 아무것도 안 지킨다"
    )

    folded = read_prices(
        store, as_of=as_of, market="KR", columns=["close"], adjusted=True
    )
    returns = folded.sort_values("valid_from")["close"].pct_change().dropna()
    assert returns.abs().max() < 1e-9, (
        f"보정 후에도 최대 {returns.abs().max():.1%} 움직임이 남았다"
    )


def test_the_last_session_is_never_adjusted(store: Store) -> None:
    """마지막 세션은 누적곱이 비어 있어 언제나 원주가와 같다.

    "최신 종가" 를 쓰는 코드가 ``adjusted`` 를 무엇으로 주든 같은 값을 본다는
    뜻이고, 그래서 화면·주문이 이 인자에 흔들리지 않는다.
    """
    sessions = _sessions(40)
    _seed(store, sessions=sessions, factor=0.1, split_at=20)
    as_of = sessions[-1] + timedelta(hours=9)

    for adjusted in (False, True):
        frame = read_prices(
            store, as_of=as_of, market="KR", columns=["close"], adjusted=adjusted
        )
        last = frame.sort_values("valid_from")["close"].iloc[-1]
        assert last == pytest.approx(1_000.0)


def test_a_future_split_is_invisible(store: Store) -> None:
    """**as_of 가 미래 분할을 막는다.**

    발효일 전에 서 있으면 그 사건 행은 게이트에서 아예 안 온다. 읽는 쪽이
    조심할 필요가 없다는 것이 이 설계의 요점이다.
    """
    sessions = _sessions(40)
    _seed(store, sessions=sessions, factor=0.1, split_at=30)
    before = sessions[29] + timedelta(hours=9)

    frame = read_prices(
        store, as_of=before, market="KR", columns=["close"], adjusted=True
    )

    assert not frame.empty
    assert _all_close(frame["close"], 10_000.0), (
        "아직 발효하지 않은 분할이 과거 가격에 반영됐다 — 미래를 봤다"
    )


def test_raw_is_the_default(store: Store) -> None:
    """기본값은 원주가다. **뒤집으면 주문 수량이 틀어진다.**"""
    sessions = _sessions(40)
    _seed(store, sessions=sessions, factor=0.1, split_at=20)
    as_of = sessions[-1] + timedelta(hours=9)

    default = read_prices(store, as_of=as_of, market="KR", columns=["close"])
    explicit = read_prices(
        store, as_of=as_of, market="KR", columns=["close"], adjusted=False
    )

    assert default["close"].tolist() == explicit["close"].tolist()
    assert default["close"].max() == pytest.approx(10_000.0), "기본값이 보정가가 됐다"


def test_helper_columns_do_not_leak(store: Store) -> None:
    """보정하려고 얹은 컬럼은 돌려줄 때 다시 뺀다."""
    sessions = _sessions(40)
    _seed(store, sessions=sessions, factor=0.1, split_at=20)
    as_of = sessions[-1] + timedelta(hours=9)

    frame = read_prices(
        store, as_of=as_of, market="KR", columns=["value"], adjusted=True
    )

    assert "value" in frame.columns
    for name in ("close", "adj_factor", "open", "high", "low"):
        assert name not in frame.columns, f"{name} 이 새어 나왔다"


def test_a_frame_without_the_factor_column_is_untouched() -> None:
    """계수를 아직 안 채운 구간에서 값이 조용히 바뀌면 안 된다.

    보정이 **안 된 것이 눈에 보이는** 편이, 반쯤 보정된 값이 맞는 척하는 것보다
    낫다.
    """
    frame = pd.DataFrame({
        "entity_id": [ENTITY] * 3,
        "valid_from": _sessions(3),
        "close": [1.0, 2.0, 3.0],
    })

    assert adjust(frame)["close"].tolist() == [1.0, 2.0, 3.0]


def test_two_events_compound_backwards() -> None:
    """사건이 둘이면 그 앞 구간에는 **둘 다** 곱해진다.

    감자(×5)와 분할(×0.2)을 나란히 두면 맨 앞 구간의 누적은 1.0 이다 — 방향이
    반대인 두 사건이 서로를 상쇄한다. 한쪽만 곱하면 여기서 틀어진다.
    """
    days = _sessions(6)
    frame = pd.DataFrame({
        "entity_id": [ENTITY] * 6,
        "valid_from": days,
        "close": [100.0] * 6,
        "adj_factor": [None, None, 5.0, None, 0.2, None],
    })

    folded = adjust(frame)["close"].tolist()

    #  0,1 : 뒤에 5.0 과 0.2 가 둘 다 있다  → 100 × 1.0
    #  2,3 : 뒤에 0.2 만 있다               → 100 × 0.2
    #  4,5 : 뒤에 아무것도 없다             → 100
    assert folded == pytest.approx([100.0, 100.0, 20.0, 20.0, 100.0, 100.0])


def test_shuffled_input_does_not_cross_entities() -> None:
    """행 순서가 섞여 들어와도 배율이 종목을 넘나들지 않는다.

    접기는 정렬 → 그룹별 누적곱 → **원래 순서로 되돌리기**로 돈다. 그
    되돌리기가 어긋나면 A 의 배율이 B 의 가격에 곱해지는데, 값이 여전히
    그럴듯해서 눈으로는 안 보인다. 게이트가 주는 순서는 보장된 것이 아니므로
    (파티션 나열 순서를 탄다) 여기서 못 박는다.
    """
    days = _sessions(3)
    frame = pd.DataFrame({
        "entity_id": ["KR:000200", ENTITY, "KR:000200", ENTITY, "KR:000200", ENTITY],
        "valid_from": [days[0], days[0], days[1], days[1], days[2], days[2]],
        "close": [7_000.0, 100.0, 7_000.0, 100.0, 7_000.0, 100.0],
        # 배율은 ENTITY 의 가운데 세션에만 있다.
        "adj_factor": [None, None, None, 0.5, None, None],
    })

    folded = adjust(frame)

    assert folded.index.tolist() == frame.index.tolist(), "행 순서가 바뀌었다"
    mine = folded[folded["entity_id"] == ENTITY]["close"].tolist()
    other = folded[folded["entity_id"] == "KR:000200"]["close"].tolist()
    assert mine == pytest.approx([50.0, 100.0, 100.0])
    assert other == pytest.approx([7_000.0] * 3), "배율이 옆 종목으로 샜다"


def test_folding_is_per_entity(store: Store) -> None:
    """한 종목의 분할이 다른 종목 가격을 건드리지 않는다."""
    sessions = _sessions(40)
    _seed(store, sessions=sessions, factor=0.1, split_at=20, entity="KR:000100")
    _seed(store, sessions=sessions, entity="KR:000200", base=7_000.0)
    as_of = sessions[-1] + timedelta(hours=9)

    frame = read_prices(
        store, as_of=as_of, market="KR", columns=["close"], adjusted=True
    )
    other = frame[frame["entity_id"] == "KR:000200"]["close"]

    assert _all_close(other, 7_000.0), "옆 종목이 같이 접혔다"


# -----------------------------------------------------------------------------
# 원주가가 필요한 자리 — 돈이 걸린 쪽
# -----------------------------------------------------------------------------


def test_order_prices_stay_raw_but_volatility_does_not(store: Store) -> None:
    """``market_stats`` 는 한 프레임에서 **가격은 원주가, 변동성은 보정가**를 낸다.

    가격이 보정가면 분할 직후 주문 수량이 배율만큼 틀어진다. 변동성이 원주가면
    그 종목의 변동성이 통째로 부풀어 역변동성 가중이 비중을 0 으로 누른다.
    한 함수 안에서 둘이 갈려야 한다.
    """
    sessions = _sessions(STATS_WINDOW * 3)
    _seed(store, sessions=sessions, factor=0.1, split_at=len(sessions) - 5)
    as_of = sessions[-1] + timedelta(hours=9)

    prices, _, volatility = market_stats(
        store, as_of=as_of, entities=[ENTITY], market="KR"
    )

    assert prices[ENTITY] == pytest.approx(1_000.0), (
        "주문에 쓰는 가격이 보정가가 됐다 — 수량이 배율만큼 틀어진다"
    )
    # 가격이 한 푼도 안 움직인 종목이므로 보정 후 변동성은 0 이고, 0 은 사전에
    # 안 들어간다. 원주가로 쟀다면 -90% 하루 때문에 큰 값이 들어왔을 것이다.
    assert ENTITY not in volatility, (
        f"변동성 {volatility.get(ENTITY)} — 분할을 진짜 움직임으로 읽었다"
    )


# -----------------------------------------------------------------------------
# 실측 — 진짜 창고가 있을 때만
# -----------------------------------------------------------------------------

#: LS 전수 조회로 확인한 실제 사건 (2026-08-15).
REAL_ACTIONS = (
    ("KR:001080", date(2026, 3, 9), 0.100000),
    ("KR:025560", date(2025, 7, 22), 17.428571),
    ("KR:025560", date(2026, 7, 27), 0.200000),
    ("KR:484870", date(2026, 6, 26), 0.333333),
)

#: 급락했지만 **기업행위가 아닌** 종목. 거래정지 후 정리매매다 —
#: 직전일 거래량이 0 이었고 LS 수정주가도 이 하루를 보정하지 않는다.
REAL_NON_ACTIONS = (("KR:150840", date(2026, 1, 15)),)


def _real_store() -> Store | None:
    root = Path(os.environ.get("QUANT_RL_DATA_ROOT", "data")) / "curated" / "prices"
    return Store() if root.exists() else None


def test_real_corporate_actions_are_stored() -> None:
    """실제 창고에 그 배율이 그 발효일에 들어 있다."""
    store = _real_store()
    if store is None:
        pytest.skip("창고가 없다")

    as_of = datetime(2026, 8, 15, tzinfo=UTC)
    frame = read_prices(
        store,
        as_of=as_of,
        entity=sorted({entity for entity, _, _ in REAL_ACTIONS}),
        lookback=500,
        market="KR",
        columns=["entity_id", "valid_from", "adj_factor"],
    )
    if frame.empty or frame["adj_factor"].notna().sum() == 0:
        pytest.skip("adj_factor 가 아직 안 채워졌다")

    stored = {
        (str(row["entity_id"]), row["valid_from"].date()): row["adj_factor"]
        for row in frame.to_dict(orient="records")
    }
    for entity, day, factor in REAL_ACTIONS:
        assert (entity, day) in stored, f"{entity} {day} 사건이 없다"
        assert stored[(entity, day)] == pytest.approx(factor, rel=1e-4)


def test_real_liquidation_selloffs_are_left_alone() -> None:
    """정리매매는 **실제 가격**이다. 보정하면 -98% 를 지워 거짓을 만든다."""
    store = _real_store()
    if store is None:
        pytest.skip("창고가 없다")

    as_of = datetime(2026, 8, 15, tzinfo=UTC)
    for entity, day in REAL_NON_ACTIONS:
        frame = read_prices(
            store, as_of=as_of, entity=entity, lookback=500, market="KR",
            columns=["entity_id", "valid_from", "adj_factor"],
        )
        if frame.empty:
            pytest.skip(f"{entity} 가 창고에 없다")
        hit = frame[frame["valid_from"].dt.date == day]["adj_factor"]
        assert hit.isna().all(), (
            f"{entity} {day} 에 배율이 붙었다 — 거래정지 후 정리매매를 "
            "기업행위로 오인했다"
        )


def test_real_split_no_longer_reads_as_a_crash() -> None:
    """실제 종목의 분할 하루가 보정 후에는 급락이 아니다."""
    store = _real_store()
    if store is None:
        pytest.skip("창고가 없다")

    as_of = datetime(2026, 8, 15, tzinfo=UTC)
    entity, day, factor = REAL_ACTIONS[0]
    frame = read_prices(
        store, as_of=as_of, entity=entity, lookback=500, market="KR",
        columns=["entity_id", "valid_from", "close", "adj_factor"],
    )
    if frame.empty or frame["adj_factor"].notna().sum() == 0:
        pytest.skip("adj_factor 가 아직 안 채워졌다")

    folded = adjust(frame).sort_values("valid_from")
    folded["ret"] = folded["close"].pct_change()
    hit = folded[folded["valid_from"].dt.date == day]["ret"]
    assert not hit.empty
    # 배율(×0.1)을 걷어내고 남는 것은 그날의 진짜 움직임뿐이다.
    residual = float(hit.iloc[0])
    assert abs(residual) < 0.35, (
        f"보정 후에도 {residual:+.1%} — 배율이 안 걷혔다 (기대: 1/{1 / factor:.0f})"
    )


def test_real_warehouse_has_no_absurd_factors() -> None:
    """배율이 상식 범위 안에 있다.

    반올림 잡음을 사건으로 오인하면 1 에 아주 가까운 배율이 무더기로 생긴다.
    반대로 계수를 뒤집어 저장하면 천문학적인 값이 나온다. 둘 다 여기서 걸린다.

    **창을 좁게 잡는다.** 5년 전 구간까지 훑으면 이 테스트 하나가 340만 행을
    올린다 — 단위 테스트에 둘 비용이 아니고, 램을 다른 작업과 나눠 쓰는
    기계에서는 그 봉우리가 머신을 멈춘다. 전 구간 검사는 쓰기 시점에
    ``tools/backfill_adj_factor.py`` 의 ``_sane`` 이 이미 한다.
    """
    store = _real_store()
    if store is None:
        pytest.skip("창고가 없다")

    frame = read_prices(
        store, as_of=datetime(2026, 8, 15, tzinfo=UTC), lookback=400, market="KR",
        columns=["entity_id", "valid_from", "adj_factor"],
    )
    factors = frame["adj_factor"].dropna()
    if factors.empty:
        pytest.skip("adj_factor 가 아직 안 채워졌다")

    logs = factors.map(math.log).abs()
    assert logs.min() > MIN_LOG_FACTOR, (
        f"|log| {logs.min():.4f} 인 배율이 있다 — 반올림 잡음을 사건으로 읽었다"
    )
    assert logs.max() < math.log(1_000.0), (
        f"배율 {factors.abs().max():,.0f} — 계수가 뒤집혔거나 누적이 저장됐다"
    )
