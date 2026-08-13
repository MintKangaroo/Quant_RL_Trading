"""News·SNS 필터 배관.

수집기는 아직 없다. 그래도 지금 만드는 이유는, **판정 규칙과 안전장치를 먼저
검증해 두면 데이터가 붙는 날 소스만 갈아 끼우면 되기 때문**이다. 빈 판정을
내는 것과 판정 로직이 없는 것은 다르다.

여기서 고정하는 것 넷:

1. 매수 금지만. 매도 권한 없음
2. 영구 차단 불가 (expires_at)
3. 하루 거부 상한 — 전부 차단하면 필터가 아니라 정지 버튼이다
4. 성적표 — IC 를 못 쓰는 이 둘을 검증하는 유일한 수단
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.analysts.scorecard import evaluate_blocks
from quant_rl_trading.analysts.verdicts import NewsAnalyst, SnsAnalyst, VerdictAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.verdict import Category, Decision

NOW = datetime(2026, 8, 12, tzinfo=UTC)
ENTITIES = [f"KR:{index:06d}" for index in range(20)]


class Greedy(VerdictAnalyst):
    """전부 차단하려 드는 필터. 상한이 실제로 막는지 보려고 만든다."""

    name = "greedy"
    version = "greedy-v0"

    def candidates_to_block(self, entities, as_of):  # type: ignore[no-untyped-def]
        return [
            (entity, Category.DILUTION, 0.5 + index * 0.01, "전부 차단 시도")
            for index, entity in enumerate(entities)
        ]


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return store


# -- 안전장치 ---------------------------------------------------------------------


def test_block_cap_is_enforced_by_the_base_class(seeded) -> None:
    """하위 클래스가 상한을 어길 수 없어야 한다.

    각 필터가 알아서 지키게 두면 언젠가 하나가 안 지킨다. 전부 차단하면
    살 종목이 남지 않고, 그건 필터가 아니라 정지 버튼이다.
    """
    cap = float(seeded.config("analyst.block_ratio_cap", as_of=NOW))
    verdicts = Greedy(seeded, ReplayClock(NOW)).run(ENTITIES, NOW)

    assert len(verdicts) == int(len(ENTITIES) * cap)
    assert len(verdicts) < len(ENTITIES)


def test_cap_keeps_the_most_severe(seeded) -> None:
    """상한에 걸리면 덜 심각한 것이 살아남는다."""
    verdicts = Greedy(seeded, ReplayClock(NOW)).run(ENTITIES, NOW)
    severities = [verdict.severity for verdict in verdicts]

    assert severities == sorted(severities, reverse=True)
    assert min(severities) > 0.5   # 가장 덜 심각한 것들은 잘려 나갔다


def test_every_block_expires(seeded) -> None:
    """영구 차단이 있으면 그 종목에서 났을 수익을 아무도 모른 채 사라진다."""
    ttl = int(seeded.config("analyst.verdict_ttl_days", as_of=NOW))
    verdicts = Greedy(seeded, ReplayClock(NOW)).run(ENTITIES, NOW)

    assert verdicts
    for verdict in verdicts:
        assert verdict.expires_at == NOW + timedelta(days=ttl)
        assert verdict.active_at(NOW)
        assert not verdict.active_at(verdict.expires_at)


def test_filters_never_sell(seeded) -> None:
    """매수 금지만. 오작동해도 기회를 놓칠 뿐 손실이 확정되지 않는다."""
    verdicts = Greedy(seeded, ReplayClock(NOW)).run(ENTITIES, NOW)

    assert all(verdict.decision is Decision.BLOCK for verdict in verdicts)
    assert Decision.__members__.keys() == {"BLOCK", "PASS"}


def test_no_candidates_no_verdicts(seeded) -> None:
    assert Greedy(seeded, ReplayClock(NOW)).run([], NOW) == []


# -- 수집기 없음 -------------------------------------------------------------------


def test_news_blocks_nothing_without_documents(seeded) -> None:
    """수집기가 없으면 지어내지 않는다."""
    assert NewsAnalyst(seeded, ReplayClock(NOW), market=Market.KR).run(ENTITIES, NOW) == []


def test_sns_blocks_nothing_yet(seeded) -> None:
    assert SnsAnalyst(seeded, ReplayClock(NOW), market=Market.KR).run(ENTITIES, NOW) == []


def test_news_matches_structural_bad_news(seeded) -> None:
    """공시 제목이 들어오면 사유별로 잡아낸다.

    단순 주가 하락 기사는 잡지 않는다 — 전부 차단하면 살 종목이 안 남는다.
    """
    seeded.append(
        "documents",
        [
            {
                "entity_id": "KR:000001", "valid_from": NOW - timedelta(days=1),
                "observed_at": NOW - timedelta(days=1), "source": "dart",
                "doc_id": "d1", "doc_type": "공시", "title": "감사의견 거절",
                "filer": "x", "url": "", "raw_path": "",
            },
            {
                "entity_id": "KR:000002", "valid_from": NOW - timedelta(days=1),
                "observed_at": NOW - timedelta(days=1), "source": "news",
                "doc_id": "d2", "doc_type": "뉴스", "title": "주가 3% 하락 마감",
                "filer": "x", "url": "", "raw_path": "",
            },
        ],
        ingest_run_id="docs",
    )

    verdicts = NewsAnalyst(seeded, ReplayClock(NOW), market=Market.KR).run(ENTITIES, NOW)
    blocked = {verdict.entity_id: verdict for verdict in verdicts}

    assert "KR:000001" in blocked
    assert blocked["KR:000001"].category is Category.ACCOUNTING
    assert "KR:000002" not in blocked, "단순 주가 하락은 차단 사유가 아니다"


# -- 성적표 -----------------------------------------------------------------------


def price_row(entity: str, day: datetime, close: float) -> dict[str, Any]:
    return {
        "entity_id": entity, "valid_from": day, "observed_at": day + timedelta(hours=7),
        "source": "test", "market": "KR",
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "value": 1e7, "adj_factor": None,
    }


def verdict_row(entity: str, blocked_at: datetime, expires: datetime) -> dict[str, Any]:
    return {
        "entity_id": entity, "valid_from": blocked_at, "observed_at": blocked_at,
        "source": "news", "analyst": "news", "analyst_version": "news-v0.1.0",
        "decision": "block", "severity": 1.0, "category": "accounting",
        "reason": "감사의견 거절", "expires_at": expires,
    }


def test_scorecard_credits_avoided_losses(seeded) -> None:
    """차단한 종목이 시장보다 더 떨어졌으면 필터가 일한 것이다."""
    start = NOW - timedelta(days=10)
    end = NOW - timedelta(days=5)

    prices = []
    for day_offset in range(6):
        day = start + timedelta(days=day_offset)
        prices.append(price_row("KR:000001", day, 1000.0 - day_offset * 50))  # 급락
        prices.append(price_row("KR:000002", day, 1000.0))                    # 보합
        prices.append(price_row("KR:000003", day, 1000.0))
    seeded.append("prices", prices, ingest_run_id="p")
    seeded.append("verdicts", [verdict_row("KR:000001", start, end)], ingest_run_id="v")

    card = evaluate_blocks(seeded, as_of=NOW, lookback=60)

    assert card["settled"] == 1
    assert card["hit_rate"] == 1.0
    assert card["mean_excess"] < 0, "차단 종목이 시장보다 더 떨어졌다"


def test_scorecard_flags_missed_opportunities(seeded) -> None:
    """차단한 종목이 올랐으면 기회를 버린 것이다. 그것도 성적이다."""
    start = NOW - timedelta(days=10)
    end = NOW - timedelta(days=5)

    prices = []
    for day_offset in range(6):
        day = start + timedelta(days=day_offset)
        prices.append(price_row("KR:000001", day, 1000.0 + day_offset * 50))  # 급등
        prices.append(price_row("KR:000002", day, 1000.0))
        prices.append(price_row("KR:000003", day, 1000.0))
    seeded.append("prices", prices, ingest_run_id="p")
    seeded.append("verdicts", [verdict_row("KR:000001", start, end)], ingest_run_id="v")

    card = evaluate_blocks(seeded, as_of=NOW, lookback=60)

    assert card["hit_rate"] == 0.0
    assert card["mean_excess"] > 0
    assert card["worst_calls"][0]["entity_id"] == "KR:000001"


def test_active_blocks_are_not_scored_yet(seeded) -> None:
    """진행 중인 차단을 성적에 넣으면 성적이 매일 흔들린다."""
    seeded.append(
        "verdicts",
        [verdict_row("KR:000001", NOW - timedelta(days=1), NOW + timedelta(days=4))],
        ingest_run_id="v-active",
    )

    card = evaluate_blocks(seeded, as_of=NOW, lookback=60)

    assert card["settled"] == 0
    assert card["pending"] == 1
    # 채점할 것이 없으면 0 이 아니라 '모름' 이다.
    assert card["hit_rate"] is None


def test_empty_scorecard_is_unknown_not_zero(seeded) -> None:
    card = evaluate_blocks(seeded, as_of=NOW, lookback=60)

    assert card["settled"] == 0
    assert card["hit_rate"] is None
    assert card["mean_excess"] is None
