"""지수 간 일수익 괴리 점검(data-contract §3-1) — 경고만, 모름은 경고 아님, as_of 로 되감긴다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.dashboard.services import data_quality as dq

#: 2024-03-04(월) ~ 03-08(금) 다섯 세션. 관측은 16:00 KST = 07:00 UTC.
SESSIONS = [datetime(2024, 3, day, tzinfo=UTC) for day in (4, 5, 6, 7, 8)]
AS_OF = datetime(2024, 3, 9, tzinfo=UTC)


def _index(store, entity: str, closes: list[float | None], *, run: str) -> None:  # type: ignore[no-untyped-def]
    rows = [
        {"entity_id": entity, "valid_from": s, "observed_at": s + timedelta(hours=7), "source": "test",
         "market": "KR", "board": None, "open": None, "high": None, "low": None, "close": c,
         "volume": None, "value": None}
        for s, c in zip(SESSIONS, closes, strict=True) if c is not None
    ]
    store.append("indices", rows, ingest_run_id=f"t-{run}")


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    # K200 과 KRX 300 은 03-07 에 갈라진다(+0.5% 대 +5.5% → 차 5%p). 나머지는 같이 움직인다.
    _index(store, "KR:IDX:KOSPI200", [100, 101, 102, 102.51, 103], run="k200")
    _index(store, "KR:IDX:KRX 300", [100, 101, 102, 107.61, 108.1], run="k300")
    # KRX 100 은 03-06 이 비어 있다 → 03-06·03-07 수익을 못 잰다(모름, 경고 아님).
    _index(store, "KR:IDX:KRX 100", [100, 101, None, 102.51, 103], run="k100")
    # TMI 의 짝인 코스피는 아예 없다 → 전부 모름.
    _index(store, "KR:IDX:KRX TMI", [100, 101, 102, 103, 104], run="tmi")
    return store


def _pair(result, a):  # type: ignore[no-untyped-def]
    return next(p for p in result["pairs"] if p["a"] == a)


def test_alert_names_day_and_both_returns(seeded):  # type: ignore[no-untyped-def]
    result = dq.index_divergence(seeded, as_of=AS_OF, lookback=7)
    pair = _pair(result, "KR:IDX:KRX 300")
    assert [a["day"] for a in pair["alerts"]] == ["2024-03-07"]
    alert = pair["alerts"][0]
    assert alert["return_a"] == pytest.approx(0.055, abs=1e-3)
    assert alert["return_b"] == pytest.approx(0.005, abs=1e-3)
    lines = dq.divergence_lines(result)
    assert len(lines) == 1 and "2024-03-07" in lines[0] and "KRX 300" in lines[0] and "KOSPI200" in lines[0]


def test_missing_side_is_unknown_not_alert(seeded):  # type: ignore[no-untyped-def]
    result = dq.index_divergence(seeded, as_of=AS_OF, lookback=7)
    k100 = _pair(result, "KR:IDX:KRX 100")
    assert k100["alerts"] == []
    assert k100["unknown"] >= 2            # 03-06(값 없음)·03-07(전날 없음)
    tmi = _pair(result, "KR:IDX:KRX TMI")
    assert tmi["measured"] == 0 and tmi["alerts"] == []
    assert result["alert_count"] == 1


def test_as_of_rewinds(seeded):  # type: ignore[no-untyped-def]
    # 03-07 16:00 KST 공표 전의 as_of 에서는 그날 값을 모른다 → 경고도 없다.
    before = dq.index_divergence(seeded, as_of=datetime(2024, 3, 7, 6, tzinfo=UTC), lookback=7)
    assert before["alert_count"] == 0


def test_threshold_comes_from_config(seeded):  # type: ignore[no-untyped-def]
    result = dq.index_divergence(seeded, as_of=AS_OF, lookback=7)
    assert result["threshold"] == pytest.approx(float(seeded.config(dq.DIVERGENCE_KEY, as_of=AS_OF)))


def test_summary_carries_divergence_warning(seeded):  # type: ignore[no-untyped-def]
    thresholds = {"coverage_warn": 0.98, "missing_warn": 0.01, "latency_p90_warn_ms": 300000, "failure_rows": 50}
    out = dq.summary(seeded, as_of=AS_OF, lookback=7, thresholds=thresholds)
    assert out["index_divergence_alerts"] == 1
    assert any("지수 괴리 2024-03-07" in w for w in out["warnings"])
