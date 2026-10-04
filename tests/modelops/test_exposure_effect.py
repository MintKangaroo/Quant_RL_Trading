"""regime(노출 지표)의 노출 기여 평가 — 장부 기록 우선·비용·as_of·한 줄 (modelops-ranker.md ①)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.modelops import exposure_effect as ee

DAYS = list(trading_days(Market.KR, date(2024, 3, 4), date(2024, 3, 15)))   # 10 세션
CLOSES = [100.0, 101.0, 99.0, 98.0, 100.0, 97.0, 95.0, 96.0, 99.0, 100.0]


def _at(day: date, hour: int = 7) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=hour)


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    index_id = str(store.config("benchmark.kr_index", as_of=_at(DAYS[-1])))
    store.append("indices", [
        {"entity_id": index_id, "valid_from": _at(d, 0), "observed_at": _at(d), "source": "test", "market": "KR",
         "board": None, "open": None, "high": None, "low": None, "close": c, "volume": None, "value": None}
        for d, c in zip(DAYS, CLOSES, strict=True)
    ], ingest_run_id="t-index")
    # 장부 기록: 세션마다 배수. 셋째 세션부터 0.5 로 줄였다가 여덟째에 되돌린다.
    scales = [1.0, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5, 1.0, 1.0, 1.0]
    store.append("events", [
        {"entity_id": f"session-KR-{d}", "seq": 3, "valid_from": _at(d), "observed_at": _at(d), "source": "test",
         "stage": "exposure", "actor": "regime", "payload_hash": "x", "payload": json.dumps({"scale": s})}
        for d, s in zip(DAYS, scales, strict=True)
    ], ingest_run_id="t-events")
    return store, dict(zip(DAYS, scales, strict=True))


def test_uses_recorded_scales_and_costs(seeded):  # type: ignore[no-untyped-def]
    store, scales = seeded
    result = ee.exposure_effect(store, as_of=_at(DAYS[-1], 12), sessions=8)
    assert result["recorded"] == 8 and result["recomputed"] == 0
    # 세션 d 의 배수는 d 종가 → d+1 종가 수익에 붙는다. 첫 세션(DAYS[0])은 직전 종가가 없어 빠지고 마지막은 d+1 이 없어 빠진다.
    decisions = DAYS[1:-1]
    fee = float(store.config("accounting.fee_kr", as_of=_at(DAYS[-1])))
    tax = float(store.config("accounting.transaction_tax_kr", as_of=_at(DAYS[-1])))
    applied, index, prev = 1.0, 1.0, scales[decisions[0]]
    for d in decisions:
        i = DAYS.index(d)
        r = CLOSES[i + 1] / CLOSES[i] - 1.0
        s = scales[d]
        cost = abs(s - prev) * fee + max(0.0, prev - s) * tax
        applied *= 1 + s * r - cost
        index *= 1 + r
        prev = s
    assert result["cum_applied"] == pytest.approx(applied - 1)
    assert result["cum_index"] == pytest.approx(index - 1)
    assert result["mdd_applied"] > result["mdd_index"]   # 하락 구간에 절반만 들었다
    assert "대상 아님" in ee.effect_line(result)


def test_as_of_rewinds(seeded):  # type: ignore[no-untyped-def]
    store, _ = seeded
    early = ee.exposure_effect(store, as_of=_at(DAYS[5], 12), sessions=8)
    assert early["end"] < DAYS[5].isoformat()


def test_window_comes_from_config(seeded):  # type: ignore[no-untyped-def]
    store, _ = seeded
    assert int(store.config(ee.SESSIONS_KEY, as_of=_at(DAYS[-1]))) == 120
    result = ee.exposure_effect(store, as_of=_at(DAYS[-1], 12))
    assert result["sessions"] == len(DAYS) - 2          # 창(120)보다 자료가 짧으면 있는 만큼
