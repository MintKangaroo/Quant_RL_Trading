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


# -----------------------------------------------------------------------------
# 거래정지 중 감자 — LS 계수에 안 잡히는 사건 (2026-10-14 결함 수정 · 같은 날 개정: 계수는 공시 N)
# -----------------------------------------------------------------------------

#: 감자결정 공시 원문을 줄인 고정 자료 — 실제 2026-10-14 대조 사례(`docs/diag/decisions-2026-10-14.md` §2-2·2-3)의 서식 그대로.
#: 정정 공시처럼 14. 참고사항에 "상기 9. … 신주상장예정일은" 이 뒤에 또 나오게 둔다(본문 표의 첫 날짜를 읽어야 한다).
_NOTICE = (
    "감자 결정 1. 감자주식의 종류와 수 보통주식 (주) 1 2. 1주당 액면가액 (원) 500 "
    "4. 감자전후 발행주식수 구 분 감자전 (주) 감자후 (주) 보통주식(주) {before} {after} 기타주식(주) - - "
    "5. 감자비율 보통주식 (%) 90 6. 감자기준일 2025년 01월 01일 7. 감자방법 {method} 8. 감자사유 {reason} "
    "9. 감자일정 주주총회 예정일 - 매매거래정지예정기간 시작일 - 종료일 - 신주권교부예정일 - 신주상장예정일 {listing} "
    "10. 채권자 이의제출기간 시작일 - 14. 기타 투자판단에 참고할 사항 1) 상기 9. 감자일정의 신주상장예정일은 2099년 01월 01일로 바뀔 수 있다."
)
_REHAB = "재무구조개선(「채무자 회생 및 파산에 관한 법률」에 따른 회생계획 인가)"
_PLAIN = "결손금 보전을 통한 재무구조 개선"


def _notice(method: str, listing: str, *, reason: str = _PLAIN, before: str = "100,000,000", after: str = "10,000,000",
            filed: date = date(2025, 2, 10), doc_id: str = "N1"):
    from quant_rl_trading.collectors.corporate_actions import parse_reduction_notice

    text = _NOTICE.format(method=method, listing=listing, reason=reason, before=before, after=after)
    return parse_reduction_notice(text, doc_id=doc_id, filed_on=filed)


def _halt_bars(*, jump_volume: float = 0.0, factor: float = 15.0, start: date = date(2025, 3, 3),
               base: float = 350.0) -> list:
    """KR:352770 모양: 거래하다 정지(거래량 0)되고, 정지 중 기준가가 ×factor 로 바뀐다. 계단 세션 = bars[15]."""
    from quant_rl_trading.collectors.corporate_actions import Bar

    bars = [Bar(day=start + timedelta(days=i), close=base, volume=1_000_000.0) for i in range(5)]
    bars += [Bar(day=start + timedelta(days=5 + i), close=base, volume=0.0) for i in range(10)]
    bars.append(Bar(day=start + timedelta(days=15), close=round(base * factor), volume=jump_volume))
    bars += [Bar(day=start + timedelta(days=16 + i), close=round(base * factor), volume=0.0) for i in range(3)]
    return bars


STEP_DAY = date(2025, 3, 18)          # _halt_bars 기본의 bars[15]
STEP_KO = "2025년 03월 18일"


@pytest.mark.parametrize(
    ("method", "ratio"),
    [
        ("기명식 보통주 15주를 동일한 액면주식 1주로 무상병합", 15.0),                                   # KR:352770
        ("3대1 무상감자&cr;(기명식 보통주 3주를 동일한 액면주식 1주로 무상병합)", 3.0),                    # KR:009730
        ("보통주식 3주를 병합하여 같은액면주식 1주로 무상감자", 3.0),                                      # KR:041590
        ("액면가 500원의 보통주 1.37주를 동일 액면금액 보통주 1주로 무상병합", 1.37),                      # KR:001840
        ("액면가 100원의 보통주 6주를 액면가 100원의 보통주 1주로 병합. 단, 1주 미만의 단주는 무상소각.", 6.0),  # KR:245620
        ("제1차 자본감소-보통주에 대하여 27:1 병합-주식 병합시 발행하는 1주 미만의 단주는 무상 소각", 27.0),    # KR:001470
        ("액면가액500원의 보통주4.7500주를 1주로 병합. 단, 이때 발생하는1주 미만의 단주는 무상 소각.", 4.75),   # KR:182400
        ("- 최대주주와 기타주주간에 차등감자- 최대주주 소유주식 26,948,235 주 전부에 대하여는 무상 소각하여 감자- "
         "기타주주 보유주식 37,266,094주에 대하여 액면가 500 원의 기명식 보통주 2주를 액면가 500 원의 기명식 보통주 1주로 "
         "병합하여 감자 (2:1)", 2.0),                                                                       # KR:119650 차등
        ("액면가 500원의 보통주식에 대하여1. 최대주주가 감자전 보유하는 10,590,000주는 감자 후 353,000주로(감자비율 30:1)"
         "2. 그외 주주들이 보유하는 보통주식 48,581,967주는 감자 후 2,429,098주로(감자비율 20:1) 병합하는 차등무상감자.", 20.0),  # KR:241820 차등
        ("1) 액면금액 1,000원의 보통주식 1주를 액면금액 500원 보통주식 2주로 주식분할함.2) 주식분할과 동시에 1주당 액면금액 "
         "500원의 보통주 10주를 동일한 액면금액 보통주 1주로 무상병합함.", 5.0),                           # KR:106080 분할 뒤 병합
        ("- 기존 주식의 소각(제1차 감자) 후 잔여 주식 및 회생채권에서 출자전환된 신주는 액면가 500원의 보통주 9주를 "
         "액면가 500원의 보통주 1주로 병합합니다.- 회생담보권에서 출자전환 된 신주는 액면가 500원의 보통주 7주를 "
         "액면가 500원의 보통주 1주로 병합 합니다.", 9.0),                                                   # KR:377460 2차
        ("구 특수관계인 보유 주식 150,000주 전체를 무상소각합니다.", None),                                # KR:377460 1차(소각뿐)
    ],
)
def test_reduction_ratio_reads_the_minority_holder_n(method: str, ratio: float | None) -> None:
    notice = _notice(method, STEP_KO)
    assert notice.ratio == (pytest.approx(ratio) if ratio is not None else None)
    assert notice.listing_on == STEP_DAY                # 14. 참고사항의 2099 년이 아니라 본문 표의 날짜


def test_share_count_ratio_is_not_the_factor() -> None:
    """감자전후 발행주식수 비율은 차등감자·분할에서 틀린다 — 계수는 감자방법에서만 읽는다(KR:119650 64.2M/18.6M = 3.45 ≠ 2)."""
    notice = _notice("기타주주 보유주식에 대하여 보통주 2주를 보통주 1주로 병합하여 감자 (2:1)", STEP_KO,
                     before="64,214,329", after="18,633,047")
    assert notice.ratio == pytest.approx(2.0)


def test_merge_without_capital_reduction_form_is_read() -> None:
    """주식병합결정(감자 아닌 액면 병합) 서식 — 1주당 가액 전후 비율과 신주권상장예정일(실측 KR:000040)."""
    from quant_rl_trading.collectors.corporate_actions import parse_reduction_notice

    text = ("주식병합 결정 1. 주식병합의 내용 구분 병합 전 병합 후 1주당 가액(원) 500 2,500 발행주식총수 보통주식(주) "
            "86,375,184 17,275,036 종류주식(주) 0 0 2. 주식병합의 일정 주주총회예정일 2026-06-19 신주의 효력발생일 2026-07-07 "
            "매매거래정지기간 시작일 2026-07-03 종료일 2026-07-27 신주권상장예정일 2026-07-28 3. 주식병합목적 적정 유통주식수 유지")
    notice = parse_reduction_notice(text, doc_id="M1", filed_on=date(2026, 5, 11))
    assert notice.ratio == pytest.approx(5.0)
    assert notice.listing_on == date(2026, 7, 28)


def test_halt_capital_reduction_is_found_with_a_filing() -> None:
    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    bars = _halt_bars()
    events = detect_halt_steps(bars, [_notice("보통주 15주를 동일한 액면주식 1주로 무상병합", STEP_KO)],
                               entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)
    assert len(events) == 1
    assert events[0].effective_on == STEP_DAY
    assert events[0].factor == pytest.approx(15.0)       # 감자 = 1 보다 큰 배율(과거 가격 × 15)


def test_halt_step_needs_notice_no_trade_and_no_existing_factor() -> None:
    from dataclasses import replace

    from quant_rl_trading.collectors.corporate_actions import ReductionNotice, detect_halt_steps

    kwargs = dict(entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)
    good = _notice("보통주 15주를 동일한 액면주식 1주로 무상병합", STEP_KO)
    assert detect_halt_steps(_halt_bars(), [], **kwargs) == []                                    # 공시 없음
    assert detect_halt_steps(_halt_bars(), [replace(good, filed_on=date(2024, 1, 2))], **kwargs) == []   # 공시가 250일 밖
    assert detect_halt_steps(_halt_bars(jump_volume=5_000.0), [good], **kwargs) == []             # 거래 있는 날
    assert detect_halt_steps(_halt_bars(factor=0.2), [good], **kwargs) == []                      # 내려간 계단(감자 아님)
    bars = _halt_bars()
    bars[15] = replace(bars[15], adj_factor=15.0)                                                 # LS 가 이미 잡았다
    assert detect_halt_steps(bars, [good], **kwargs) == []
    assert len(detect_halt_steps(_halt_bars(factor=10.01),
                                 [_notice("보통주 10주를 1주로 병합", STEP_KO)], **kwargs)) == 1  # 호가 올림은 통과(가격 배율)
    odd: list = []
    blind = ReductionNotice(doc_id="X", filed_on=date(2025, 2, 10), ratio=None, listing_on=None, has_text=False)
    assert detect_halt_steps(_halt_bars(), [blind], rejected=odd, **kwargs) == []                 # 원문 없음 → 안 쓴다
    assert len(odd) == 1 and "원문" in odd[0].reason


def test_rehab_step_uses_notice_n_and_leaves_the_dilution_as_return() -> None:
    """KR:001470 2026-09-01 — 1차 27:1 × 2차 20:1 = 540, 가격은 ×16.77(347 → 5,820). 정정본이 같은 차수를 두 번 세지 않는다."""
    from dataclasses import replace

    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    listing = "2026년 09월 01일"
    first = _notice("제1차 자본감소-보통주에 대하여 27:1 병합", listing, reason=_REHAB,
                    before="229,681,824", after="8,455,571", filed=date(2026, 6, 29), doc_id="A")
    second = _notice("제2차 자본감소-보통주에 대하여 20:1 병합", listing, reason=_REHAB,
                     before="123,465,987", after="6,139,903", filed=date(2026, 6, 29), doc_id="B")
    first_fix = replace(first, doc_id="A2", filed_on=date(2026, 8, 7))           # [기재정정] — 같은 감자
    bars = _halt_bars(start=date(2026, 8, 17), base=347.0, factor=5820 / 347)
    odd: list = []
    events = detect_halt_steps(bars, [first, second, first_fix], entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR,
                               rejected=odd)
    assert odd == []
    assert len(events) == 1 and events[0].effective_on == date(2026, 9, 1)
    assert events[0].factor == pytest.approx(540.0)
    residual = events[0].ratio_after / (events[0].ratio_before * events[0].factor) - 1.0
    assert residual == pytest.approx(-0.969, abs=1e-3)                          # 옛 주주의 실제 손실은 수익으로 남는다


def test_rehab_with_exact_base_price_keeps_the_price_factor() -> None:
    """KR:033340·099520 — 회생이어도 기준가가 정확히 직전가 × 2 면 가격 배율 그대로(이미 적재한 19건과 같은 값)."""
    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    events = detect_halt_steps(_halt_bars(base=1055.0, factor=2.0),
                               [_notice("보통주 2주를 액면가 500원의 기명식 보통주 1주로 병합", STEP_KO, reason=_REHAB)],
                               entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)
    assert len(events) == 1 and events[0].factor == pytest.approx(2.0)


def test_step_without_a_listing_that_day_is_not_a_reduction() -> None:
    """KR:083470 2022-03-14(감자 2:1 은 넉 달 전 2021-11-15 상장) · KR:182400 2025-08-06(4.75:1 은 7/22 상장) — 기준가 재산정."""
    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    odd: list = []
    events = detect_halt_steps(_halt_bars(base=2775.0, factor=3220 / 2775),
                               [_notice("기명식 보통주식 2주를 동일한 액면주식 1주로 병합", "2025년 01월 06일")],
                               entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR, rejected=odd)
    assert events == [] and len(odd) == 1 and "신주상장" in odd[0].reason


def test_price_mismatch_without_rehab_goes_to_a_human() -> None:
    """KR:000300 2026-01-14 꼴 — 공시 2:1 인데 가격 ×2.117, 원문에 회생·출자전환 없음 → 쓰지 않는다."""
    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    odd: list = []
    events = detect_halt_steps(_halt_bars(base=1984.0, factor=4200 / 1984),
                               [_notice("보통주식 2주를 동일한 액면주가의 보통주식 1주로 무상병합 (2:1 무상감자)", STEP_KO)],
                               entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR, rejected=odd)
    assert events == [] and len(odd) == 1 and "회생" in odd[0].reason


def test_retirement_only_notice_does_not_block_the_merge_stage() -> None:
    """KR:377460 — 1차(특수관계인 주식만 소각, N 없음)와 2차 9:1 이 같은 날 상장 → 9."""
    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    listing = STEP_KO
    events = detect_halt_steps(
        _halt_bars(base=1691.0, factor=4080 / 1691),
        [_notice("구 특수관계인 보유 주식 150,000주 전체를 무상소각합니다.", listing, reason=_REHAB, doc_id="R1"),
         _notice("잔여 주식 및 출자전환된 신주는 보통주 9주를 보통주 1주로 병합합니다.", listing, reason=_REHAB, doc_id="R2",
                 before="135,543,592", after="14,055,687")],
        entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)
    assert len(events) == 1 and events[0].factor == pytest.approx(9.0)


def test_halt_reduction_stops_looking_like_a_return(store: Store) -> None:
    """검출 → 정정본 → 접기 한 벌: 정지 중 ×15 계단이 보정가에서 수익 0 이 된다."""
    from quant_rl_trading.collectors.corporate_actions import detect_halt_steps

    bars = _halt_bars()
    rows = []
    for bar in bars:
        moment = datetime(bar.day.year, bar.day.month, bar.day.day, tzinfo=UTC)
        rows.append({"entity_id": ENTITY, "valid_from": moment, "observed_at": moment + timedelta(hours=8),
                     "source": "test", "market": "KR", "open": bar.close, "high": bar.close, "low": bar.close,
                     "close": bar.close, "volume": bar.volume, "value": bar.close * bar.volume, "adj_factor": None})
    store.append("prices", rows, ingest_run_id="halt-seed")
    events = detect_halt_steps(bars, [_notice("보통주 15주를 동일한 액면주식 1주로 무상병합", STEP_KO)],
                               entity_id=ENTITY, min_log_factor=MIN_LOG_FACTOR)
    as_of = datetime(2025, 4, 1, tzinfo=UTC)
    frame = read_prices(store, as_of=as_of, market="KR",
                        columns=["entity_id", "valid_from", "observed_at", "source", "revision", "adj_factor",
                                 "market", "open", "high", "low", "close", "volume", "value"])
    existing = {(str(r["entity_id"]), r["valid_from"].date()): r for r in frame.to_dict(orient="records")}
    store.append("prices", revision_rows(events, existing), ingest_run_id="halt-fix")
    folded = read_prices(store, as_of=as_of, market="KR", columns=["close"], adjusted=True)
    returns = folded.sort_values("valid_from")["close"].pct_change().dropna()
    assert returns.abs().max() < 1e-9
