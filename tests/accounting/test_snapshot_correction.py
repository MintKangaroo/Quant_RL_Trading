"""늦게 도착한 체결은 그날 NAV 를 **정정한다** — 얼려 두지 않는다.

## 이게 가상의 사고가 아니다

shadow 가 여기서 멈췄다. 2026-08-14 16:30 에 세션이 돌아 NAV 를 썼는데 그때는
D+1 체결 단계가 안 돌아 체결이 0 이었다. 같은 날 23:31 에 고친 실행기로 다시
돌렸더니 체결 1건(``KR:000890`` 1,004주 @1,484.12)이 들어왔다 — 그런데
``nav-2026-08-14`` 매니페스트가 이미 있어서 ``snapshot.write`` 가 조용히 0 을
돌려주고 NAV 는 초기 자본 10,000,000 에 얼어붙었다. **창고에는 체결이 있는데
NAV 는 그 전 세계를 가리키는 상태**였고, 재구성한 올바른 값은 9,999,654.76
이었다.

정정은 UPDATE 가 아니다. revision 을 올린 새 행이고(불변식 4), 옛 행은 그대로
남는다 — "그때 알던 것" 이 보존된다.

## 함정 — 정정을 열면 같은 입력이 행을 무한히 쌓는다

그래서 반대쪽도 같이 못 박는다. **값이 그대로면 아무것도 안 쓴다.**
``observed_at`` 처럼 실행할 때마다 달라지는 값을 비교에 넣으면 백테스트를 두
번 돌릴 때마다 nav_daily 가 두 배로 분다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import KRW, snapshot
from quant_rl_trading.replay.clock import ReplayClock

pytestmark = pytest.mark.invariant

DAY1 = datetime(2026, 3, 2, 6, 40, tzinfo=UTC)   # 한국시간 15:40
DAY2 = DAY1 + timedelta(days=1)


@pytest.fixture
def funded(store):  # type: ignore[no-untyped-def]
    """1,000만원 입금 + 원달러 환율. test_ledger.py 의 것과 같은 모양이다."""
    store.seed_config_defaults()
    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day,
            "source": "test", "rate": 1_350.0,
        } for day in (DAY1, DAY2)],
        ingest_run_id="fx-seed",
    )
    store.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "currency": KRW, "amount": 10_000_000.0, "kind": "deposit",
        }],
        ingest_run_id="flow-seed",
    )
    return store


def _buy(store, day: datetime, *, price: float, fee: float, run_id: str) -> None:
    store.append(
        "trades",
        [{
            "entity_id": "KR:005930", "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR", "side": "buy",
            "quantity": 100.0, "price": price, "currency": KRW,
            "fee": fee, "tax": 0.0, "order_id": "o-1",
        }],
        ingest_run_id=run_id,
    )
    store.append(
        "prices",
        [{
            "entity_id": "KR:005930", "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR",
            "open": price, "high": price, "low": price, "close": price,
            "volume": 1_000.0, "value": price * 1_000.0, "adj_factor": None,
        }],
        ingest_run_id=f"p-{run_id}",
    )


def test_늦게_온_체결이_그날_NAV를_정정한다(funded) -> None:
    """체결 전에 쓴 스냅샷 + 체결 도착 + 재실행 → revision 1 이 이긴다."""
    clock = ReplayClock(DAY1)
    assert snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1)) == 1

    frozen = funded.get("nav_daily", as_of=DAY1, entity="FUND")
    assert float(frozen["nav"].iloc[0]) == pytest.approx(10_000_000.0)
    assert float(frozen["equity_kr"].iloc[0]) == 0.0

    # 체결이 뒤늦게 창고에 들어온다. 같은 날, 같은 as_of.
    _buy(funded, DAY1, price=70_000.0, fee=1_050.0, run_id="t-late")

    assert snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1)) == 1

    corrected = funded.get("nav_daily", as_of=DAY1, entity="FUND")
    # 조회는 자연키마다 최신 revision 하나만 준다 — 정정본이 살아남는다.
    assert len(corrected) == 1
    assert int(corrected["revision"].iloc[0]) == 1
    assert float(corrected["equity_kr"].iloc[0]) == pytest.approx(7_000_000.0)
    # 매수 당일 NAV 는 **수수료만큼만** 준다. 현금↓ 과 주식평가↑ 가 상쇄된다.
    assert float(corrected["nav"].iloc[0]) == pytest.approx(10_000_000.0 - 1_050.0)


def test_값이_그대로면_다시_쓰지_않는다(funded) -> None:
    """정정을 열었다고 같은 입력이 행을 쌓으면 안 된다."""
    clock = ReplayClock(DAY1)
    taken = snapshot.take(funded, clock, as_of=DAY1)

    assert snapshot.write(funded, clock, snapshot=taken) == 1
    assert snapshot.write(funded, clock, snapshot=taken) == 0
    # 벽시계가 흘러도(observed_at 이 달라져도) 회계가 같으면 안 쓴다.
    assert snapshot.write(funded, ReplayClock(DAY1 + timedelta(hours=7)), snapshot=taken) == 0

    stored = funded.get("nav_daily", as_of=DAY2, entity="FUND")
    assert len(stored) == 1
    assert int(stored["revision"].iloc[0]) == 0


def test_정정본은_자기_자신을_어제로_보지_않는다(funded) -> None:
    """재계산 시 창고에는 **이미 그날 행이 있다.** 그걸 어제로 잡으면 TWR 이
    "오늘 대 오늘" 이 되고, 누적지수가 그 가짜 수익률만큼 어긋난다. 낙폭은
    지수로 재므로 그 어긋남은 MDD 까지 따라간다.

    첫날이므로 정정 뒤에도 수익률 0 · 지수 100 이어야 한다 — 비교할 어제가
    실제로 없기 때문이다.
    """
    clock = ReplayClock(DAY1)
    snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1))
    _buy(funded, DAY1, price=70_000.0, fee=1_050.0, run_id="t-late")

    corrected = snapshot.take(funded, clock, as_of=DAY1)

    assert corrected.valuation.nav == pytest.approx(10_000_000.0 - 1_050.0)
    assert corrected.twr_return == 0.0
    assert corrected.index_value == pytest.approx(100.0)
    assert corrected.drawdown == pytest.approx(0.0)


def test_정정된_NAV가_다음날_수익률의_기준이_된다(funded) -> None:
    """정정본이 안 이기면 다음 날 TWR 이 얼어붙은 어제로 계산된다."""
    clock = ReplayClock(DAY1)
    snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1))
    _buy(funded, DAY1, price=70_000.0, fee=1_050.0, run_id="t-late")
    snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1))

    # 다음 날 +10%. 보유 700만원이 770만원이 된다.
    funded.append(
        "prices",
        [{
            "entity_id": "KR:005930", "valid_from": DAY2, "observed_at": DAY2,
            "source": "test", "market": "KR",
            "open": 77_000.0, "high": 77_000.0, "low": 77_000.0, "close": 77_000.0,
            "volume": 1_000.0, "value": 77_000_000.0, "adj_factor": None,
        }],
        ingest_run_id="p-day2",
    )
    day2 = snapshot.take(funded, ReplayClock(DAY2), as_of=DAY2)

    # 기준이 정정본(9,998,950)이라 수익률은 +700,000/9,998,950 이다.
    assert day2.twr_return == pytest.approx(700_000.0 / (10_000_000.0 - 1_050.0))
