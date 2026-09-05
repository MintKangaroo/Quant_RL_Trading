"""성과 요약 — 화면과 메일이 같이 읽는 숫자.

여기서 고정하는 것은 계산이 되는가가 아니라 **틀리는 방식으로 틀리지
않는가** 다. 이 모듈에서 제일 틀리기 쉬운 자리는 하나다:

    입금이 들어온 날 "수익률" 을 NAV 변화율로 재는 것.

2026-08-24 유효로 모의계좌에 490,238,209원이 들어온다. 그날 NAV 는 976만에서
5억으로 뛴다 — 단순 변화율이면 하루에 +5,000% 다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import KRW, USD, performance

DAY1 = datetime(2026, 3, 2, 6, 40, tzinfo=UTC)  # 한국시간 15:40
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)
LATER = DAY3 + timedelta(hours=6)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day,
                "source": "test", "rate": 1_400.0,
            }
            for day in (DAY1, DAY2, DAY3)
        ],
        ingest_run_id="fx-seed",
    )
    return store


def nav_row(day: datetime, **over) -> dict:
    row = {
        "entity_id": "FUND", "valid_from": day, "observed_at": day,
        "source": "accounting",
        "nav": 10_000_000.0, "inflow": 0.0, "twr_return": 0.0,
        "index_value": 100.0, "drawdown": 0.0,
        "cash_krw": 0.0, "cash_usd": 0.0, "equity_kr": 0.0, "equity_us": 0.0,
        "accrued_dividend": 0.0, "payable": 0.0, "fx_rate": 1_400.0,
        "tax_provision": 0.0, "nav_after_tax": 10_000_000.0,
        "benchmark_index": None, "benchmark_note": None,
    }
    row.update(over)
    return row


def flow(day: datetime, amount: float, currency: str = KRW) -> dict:
    return {
        "entity_id": "FUND", "valid_from": day, "observed_at": day,
        "source": "test", "currency": currency, "amount": amount, "kind": "deposit",
    }


def trade(day: datetime, entity: str, side: str, quantity: float, price: float,
          order_id: str) -> dict:
    return {
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": "KR", "side": side,
        "quantity": quantity, "price": price, "currency": KRW,
        "fee": 0.0, "tax": 0.0, "order_id": order_id,
    }


# -- 입금일 --------------------------------------------------------------------


def test_입금일에_수익률이_입금액만큼_부풀지_않는다(warehouse) -> None:
    """**이 작업에서 가장 틀리기 쉬운 자리다.**

    490,238,209원이 들어온 날 NAV 는 976만에서 5억이 된다. 그것을 "수익률"
    이라 적으면 +5,000% 가 찍힌다. 성과 요약은 TWR 을 읽어야 하고, 자산
    증감은 절대액으로 보여주되 **그중 입출금이 얼마인지 함께 들어야 한다.**
    """
    deposit = 490_238_209.0
    before = 9_761_790.67
    after = before + deposit + 2_605.55  # 입금 + 그날 진짜 손익

    warehouse.append("capital_flows", [flow(DAY1, before), flow(DAY2, deposit)],
                     ingest_run_id="flows")
    warehouse.append(
        "nav_daily",
        [
            nav_row(DAY1, nav=before, inflow=before, index_value=100.0),
            nav_row(DAY2, nav=after, inflow=deposit,
                    twr_return=2_605.55 / before, index_value=100.0267),
        ],
        ingest_run_id="nav",
    )

    perf = performance.daily(warehouse, as_of=LATER)

    # 자산 증감은 입금을 포함한 절대액이다 — 사용자가 요청한 항목이다.
    assert perf.nav_change == pytest.approx(deposit + 2_605.55)
    # 그런데 그중 입출금이 얼마인지 같이 들고 있어야 한다. 이게 없으면
    # 위 숫자가 아래 수익률과 서로를 거짓말쟁이로 만든다.
    assert perf.inflow == pytest.approx(deposit)
    # 진짜 번 것은 2,605원이다.
    assert perf.pnl == pytest.approx(2_605.55)
    # 수익률은 0.03% 언저리다. 5,000% 가 아니다.
    assert perf.daily_return == pytest.approx(2_605.55 / before)
    assert abs(perf.daily_return) < 0.01


def test_입금은_누적수익률도_안_흔든다(warehouse) -> None:
    """누적은 NAV 비율이 아니라 TWR 누적지수에서 온다."""
    warehouse.append("capital_flows", [flow(DAY1, 10_000_000.0), flow(DAY2, 490_000_000.0)],
                     ingest_run_id="flows")
    warehouse.append(
        "nav_daily",
        [
            nav_row(DAY1, nav=10_000_000.0, inflow=10_000_000.0),
            nav_row(DAY2, nav=500_000_000.0, inflow=490_000_000.0, index_value=100.0),
        ],
        ingest_run_id="nav",
    )
    perf = performance.daily(warehouse, as_of=LATER)
    assert perf.cumulative_return == pytest.approx(0.0)
    # 원금은 입출금 누계다. 첫날 NAV 가 아니다.
    assert perf.principal == pytest.approx(500_000_000.0)
    assert perf.total_pnl == pytest.approx(0.0)


def test_원금은_통화를_환산해_더한다(warehouse) -> None:
    """달러 입금을 1원으로 세면 원금이 조용히 줄고 총 수익금이 부푼다.

    실전 창고가 그랬다(2026-08-22): 5,000원 + 9.49달러가 5,009원으로 세어져
    총 수익금이 204원 대신 13,616원(+272%)으로 나갔다.
    """
    warehouse.append(
        "capital_flows",
        [flow(DAY1, 5_000.0, KRW), flow(DAY2, 9.49, USD)],
        ingest_run_id="flows",
    )
    warehouse.append("nav_daily", [nav_row(DAY1, nav=18_625.4)], ingest_run_id="nav")
    perf = performance.daily(warehouse, as_of=LATER)
    assert perf.principal == pytest.approx(5_000.0 + 9.49 * 1_400.0)


# -- 없는 것과 0 을 가른다 ------------------------------------------------------


def test_회계_스냅샷이_없으면_숫자를_지어내지_않는다(warehouse) -> None:
    perf = performance.daily(warehouse, as_of=LATER)
    assert perf.measured is False
    assert perf.nav is None and perf.daily_return is None
    assert perf.note  # 이유를 적는다
    # 모드는 여전히 말한다 — 어느 창고를 봤는지가 못 잰 것과 무관하다.
    assert perf.mode == "LIVE"


def test_매매가_없던_날은_0건이_아니라_없었던_것이다(warehouse) -> None:
    warehouse.append("capital_flows", [flow(DAY1, 10_000_000.0)], ingest_run_id="flows")
    warehouse.append("nav_daily", [nav_row(DAY1), nav_row(DAY2)], ingest_run_id="nav")
    perf = performance.daily(warehouse, as_of=LATER)
    # 잰 것이다 — note 는 비어 있어야 한다. 못 잰 것과 다른 사실이다.
    assert perf.note is None
    assert perf.fills == [] and perf.fill_count == 0
    # 매도가 없으면 실현손익은 0 이 아니라 None 이다.
    assert perf.realized_pnl is None


def test_첫_세션은_비교할_어제가_없다(warehouse) -> None:
    warehouse.append("capital_flows", [flow(DAY1, 10_000_000.0)], ingest_run_id="flows")
    warehouse.append("nav_daily", [nav_row(DAY1)], ingest_run_id="nav")
    perf = performance.daily(warehouse, as_of=LATER)
    assert perf.previous_nav is None
    assert perf.nav_change is None and perf.pnl is None
    assert perf.note  # 왜 비었는지 적는다


# -- 체결 ----------------------------------------------------------------------


def test_목록이_잘려도_건수와_실현손익은_전수다(warehouse) -> None:
    """상한은 메일 길이 때문이지 사실을 줄이는 것이 아니다.

    목록 길이로 건수를 세면 "매매 16건 (매수 0 · 매도 12)" 이 나간다 —
    실제로 그렇게 한 번 나왔다.
    """
    warehouse.append("capital_flows", [flow(DAY1, 10_000_000.0)], ingest_run_id="flows")
    warehouse.append("nav_daily", [nav_row(DAY1), nav_row(DAY2)], ingest_run_id="nav")
    buys = [
        trade(DAY1, f"KR:{i:06d}", "buy", 10.0, 1_000.0 + i, f"o-buy-{i}")
        for i in range(5)
    ]
    sells = [
        trade(DAY2, f"KR:{i:06d}", "sell", 10.0, 1_100.0 + i, f"o-sell-{i}")
        for i in range(5)
    ]
    warehouse.append("trades", buys + sells, ingest_run_id="trades")

    perf = performance.daily(warehouse, as_of=LATER, fill_limit=2)
    assert len(perf.fills) == 2
    assert perf.fills_omitted == 3
    assert (perf.buy_count, perf.sell_count) == (0, 5)
    assert perf.fill_count == 5
    # 실현손익 합은 다섯 건 전부를 센 것이다. 매도가 100원씩 5건.
    assert perf.realized_pnl == pytest.approx(5 * 10 * 100.0)


def test_실현손익은_매도에만_붙는다(warehouse) -> None:
    """매수에 0 을 넣으면 "본전" 으로 읽힌다."""
    warehouse.append("capital_flows", [flow(DAY1, 10_000_000.0)], ingest_run_id="flows")
    warehouse.append("nav_daily", [nav_row(DAY1)], ingest_run_id="nav")
    warehouse.append(
        "trades", [trade(DAY1, "KR:005930", "buy", 10.0, 1_000.0, "o-1")],
        ingest_run_id="trades",
    )
    perf = performance.daily(warehouse, as_of=LATER)
    assert [f.realized_pnl for f in perf.fills] == [None]
    assert perf.buy_count == 1 and perf.sell_count == 0


def test_체결은_한국시간_역일로_가른다(warehouse) -> None:
    """UTC 로 가르면 새벽에 도는 미장 체결이 하루씩 밀린다."""
    warehouse.append("capital_flows", [flow(DAY1, 10_000_000.0)], ingest_run_id="flows")
    warehouse.append("nav_daily", [nav_row(DAY1)], ingest_run_id="nav")
    # DAY1 은 UTC 06:40 = KST 15:40. 같은 KST 날짜의 UTC 16:00(=KST 익일 01:00)
    # 체결은 **다른 세션**이다.
    next_kst_day = DAY1.replace(hour=16, minute=0)
    warehouse.append(
        "trades",
        [
            trade(DAY1, "KR:005930", "buy", 1.0, 1_000.0, "o-1"),
            trade(next_kst_day, "KR:000660", "buy", 1.0, 1_000.0, "o-2"),
        ],
        ingest_run_id="trades",
    )
    perf = performance.daily(warehouse, as_of=LATER)
    assert [f.entity_id for f in perf.fills] == ["KR:005930"]


# -- 창고를 섞지 않는다 ---------------------------------------------------------


def test_모드는_창고_경로에서_나온다(tmp_path) -> None:
    """모의 운용 숫자를 실전으로 읽는 것이 여기서 가능한 가장 비싼 오해다."""
    from quant_rl_trading.store import Store

    shadow = Store(root=tmp_path / "data" / "_shadow")
    shadow.seed_config_defaults()
    perf = performance.daily(shadow, as_of=LATER)
    assert perf.mode == "SHADOW"
    assert perf.store_root.endswith("_shadow")
