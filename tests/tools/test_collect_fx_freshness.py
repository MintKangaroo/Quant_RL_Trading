"""**환율 신선도 판정 — 세 갈래를 가른다.**

날짜 수로 재던 것이 틀렸다. FRED H.10 은 월요일 주간 발행이고 그 발행분이
담는 마지막 관측은 직전 금요일이라, **금요일에는 창고 최신값이 정상적으로
7일 전**이 된다. 6일 임계로는 매주 금요일마다 경보가 떴다(실측 2026-08-21).

매주 우는 경보는 곧 아무도 안 보는 경보가 된다. 그러면 진짜 사고가 왔을 때
그 화면은 이미 빨간 채로 방치돼 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting.ledger import FX_USDKRW
from quant_rl_trading.replay.clock import ReplayClock
from tools import collect_fx


def _seed(store, day: datetime) -> None:
    store.append(
        "fx",
        [{
            "entity_id": FX_USDKRW, "valid_from": day, "observed_at": day,
            "source": "test", "rate": 1_380.0,
        }],
        ingest_run_id=f"fx-{day.date()}",
    )


def test_금요일_7일_지연은_정상이다(store) -> None:  # type: ignore[no-untyped-def]
    """H.10 이 낸 마지막 값을 우리가 갖고 있으면 며칠이 지났든 정상이다."""
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)  # 금요일
    from quant_rl_trading.reporting.sessions import fx_source_latest

    latest = fx_source_latest(now)
    _seed(store, datetime.combine(latest, datetime.min.time(), tzinfo=UTC))
    assert collect_fx._fresh_enough(store, ReplayClock(now)) is True


def test_원본보다_밀리면_실패다(store) -> None:  # type: ignore[no-untyped-def]
    """**이것만 우리 잘못이다.** 원본은 냈는데 우리가 못 받은 경우."""
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    from quant_rl_trading.reporting.sessions import fx_source_latest

    latest = fx_source_latest(now)
    behind = datetime.combine(latest, datetime.min.time(), tzinfo=UTC) - timedelta(days=7)
    _seed(store, behind)
    assert collect_fx._fresh_enough(store, ReplayClock(now)) is False


def test_회계가_죽는_선은_원본_사정과_무관하다(store) -> None:  # type: ignore[no-untyped-def]
    """원본이 늦더라도 NAV 가 멈출 지경이면 비상이다.

    `ledger.fx_rate` 의 조회 창이 10일이라 그것을 넘기면 해외분 평가가
    거부되고 NAV 가 통째로 멈춘다. 그 사고는 원본 탓이어도 사고다.
    """
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    _seed(store, now - timedelta(days=collect_fx.NAV_BREAKS_AFTER_DAYS + 1))
    assert collect_fx._fresh_enough(store, ReplayClock(now)) is False


def test_한_행도_없으면_실패다(store) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    assert collect_fx._fresh_enough(store, ReplayClock(now)) is False
