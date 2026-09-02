"""미장 event = SUE (시행 J). 10-K 배제·같은 분기 전년 비교·오래된 발표 결측을 고정한다."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.analysts.event import EventAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 6, 30, 22, 0, tzinfo=UTC)


def _row(entity, label, end, value, rtype="edgar_10q"):
    observed = end + timedelta(days=35)
    return {"entity_id": entity, "valid_from": end, "observed_at": observed, "source": "edgar", "market": "US",
            "metric": "net_income", "value": value, "fiscal_period": label, "report_type": rtype}


def _quarters(entity, values, start_year=2023):
    rows = []; i = 0
    for y in range(start_year, start_year + 4):
        for q, month in ((1, 3), (2, 6), (3, 9), (4, 12)):
            if i >= len(values): break
            rows.append(_row(entity, f"{y}Q{q}", datetime(y, month, 28, tzinfo=UTC), values[i])); i += 1
    return rows


def test_서프라이즈가_클수록_점수가_높고_10K_는_무시한다(store) -> None:
    # UP: 전년 같은 분기 대비 큰 폭 증가, FLAT: 변화 없음 (이력 분산은 같게)
    # 전년 대비 증가폭이 들쭉날쭉해야 σ 가 0 이 아니다(σ 0 은 결측 처리).
    base = [100, 112, 119, 135, 104, 118, 127, 139, 108, 121, 131, 142]
    up = base[:-1] + [400]      # 마지막 분기(2025Q4 라벨)만 급증
    rows = _quarters("US:UP", up) + _quarters("US:FLAT", base)
    # 10-K 연간값(큰 숫자)이 최신 분기로 끼어들어도 SUE 계산에 안 들어간다
    rows.append(_row("US:FLAT", "2025Q4", datetime(2025, 12, 28, tzinfo=UTC), 9_999, rtype="edgar_10k"))
    store.append("fundamentals", rows, ingest_run_id="sue-test", source="edgar")
    analyst = EventAnalyst(store, ReplayClock(datetime(2026, 3, 1, tzinfo=UTC)), market=Market.US)
    f = analyst.features(datetime(2026, 3, 1, tzinfo=UTC))
    assert list(f.columns) == ["sue"]
    assert f.loc["US:UP", "sue"] > f.loc["US:FLAT", "sue"]


def test_발표가_120일_넘게_지나면_결측이다(store) -> None:
    rows = _quarters("US:OLD", [100, 112, 119, 135, 104, 118, 127, 139, 108, 121, 131, 300])
    store.append("fundamentals", rows, ingest_run_id="sue-old", source="edgar")
    late = datetime(2026, 6, 30, tzinfo=UTC)   # 2025Q4 말(12-28) 에서 184일
    analyst = EventAnalyst(store, ReplayClock(late), market=Market.US)
    assert analyst.features(late).empty
