"""뒤늦게 관측된 입금은 이번 스냅샷의 입금이다 (2026-09-02 미장 shadow 사고)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting import snapshot
from quant_rl_trading.replay.clock import ReplayClock

DAY1 = datetime(2026, 3, 2, 6, 40, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
FX = 1_300.0


def _fx(day):
    return {"entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day, "source": "test", "rate": FX}


def _flow(valid_from, observed_at, amount, currency="KRW", kind="deposit"):
    return {"entity_id": ledger_module.ACCOUNT, "valid_from": valid_from, "observed_at": observed_at,
            "source": "test", "currency": currency, "amount": amount, "kind": kind}


def _roll(store, day):
    clock = ReplayClock(day)
    taken = snapshot.take(store, clock, as_of=day)
    snapshot.write(store, clock, snapshot=taken)
    return taken


def test_직전_스냅샷_뒤에_관측된_입금은_수익이_아니다(store) -> None:
    store.seed_config_defaults()
    store.append("fx", [_fx(DAY1 - timedelta(days=2)), _fx(DAY1 - timedelta(days=1)), _fx(DAY1), _fx(DAY2)], ingest_run_id="fx", source="test")
    store.append("capital_flows", [_flow(DAY1 - timedelta(days=1), DAY1 - timedelta(days=1), 10_000_000.0)],
                 ingest_run_id="seed", source="test")
    first = _roll(store, DAY1)
    assert first.valuation.nav == 10_000_000.0

    # 달러 입금을 DAY1 **이전**으로 발효시켰는데 관측은 DAY1 스냅샷 **뒤**다.
    late_valid = DAY1 - timedelta(hours=1)
    store.append("capital_flows", [_flow(late_valid, DAY1 + timedelta(hours=3), 1_000.0, currency="USD")],
                 ingest_run_id="late", source="test")
    second = _roll(store, DAY2)
    assert second.valuation.nav == 10_000_000.0 + 1_000.0 * FX
    # 발효 창으로 세면 0 이 되어 130만원이 통째로 수익(+13%)이 된다. 지식 차분이면 입금이다.
    assert second.inflow == 1_000.0 * FX
    assert abs(second.twr_return) < 1e-9
