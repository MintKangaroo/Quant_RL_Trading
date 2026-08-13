"""여섯 단계를 진짜 창고 위에서. 목으로 통과하는 파이프라인 테스트는
파이프라인을 검증하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.selector import pipeline

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)
SESSIONS = [NOW - timedelta(days=offset) for offset in range(400, -1, -1)]


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """세 종목이 400세션 동안 상장·거래된 창고."""
    store.seed_config_defaults()
    entities = ["KR:000100", "KR:000200", "KR:000300"]

    universe_rows = []
    price_rows = []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(entities):
            universe_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 10_000.0 + index * 10 + offset * 100
            price_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 100_000.0,
                # 거래대금 하한(5억)을 넉넉히 넘긴다.
                "value": 5_000_000_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    store.append(
        "analyst_weights",
        [{
            "entity_id": "risk", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0,
        }],
        ingest_run_id="w-seed",
    )
    store.append(
        "signals",
        [{
            "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
            "source": "test", "analyst": "risk", "analyst_version": "risk-v0.1.0",
            "score": score, "confidence": 1.0, "horizon_days": 5,
            "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
        } for entity, score in zip(entities, [0.9, 0.5, 0.1], strict=True)],
        ingest_run_id="s-seed",
    )
    return store


def test_후보가_점수순으로_나온다(seeded) -> None:
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    assert [item.entity_id for item in result.candidates][:2] == ["KR:000100", "KR:000200"]
    assert result.weights == {"risk": 1.0}


def test_측정_결과가_없으면_후보도_없다(store) -> None:
    """**동일가중으로 때우지 않는다.** 그건 관찰 모드 Analyst 에게 실제
    가중치를 주는 것과 같다."""
    store.seed_config_defaults()

    result = pipeline.run(store, as_of=NOW, market="KR", equity=100_000_000.0)

    assert result.candidates == ()
    assert any("동일가중으로 때우지 않는다" in note for note in result.trace.notes)


def test_1주_가격이_자본의_상한을_넘으면_뺀다(seeded) -> None:
    """한 주도 제대로 못 담는 종목을 후보에 두면 목표 비중이 라운딩에서
    사라지고 그 자리는 현금으로 남는다."""
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=50_000.0)

    assert result.candidates == ()
    assert "자본의 상한 초과" in " ".join(result.trace.dropped.values())


def test_부실_공시_종목은_배제된다(seeded) -> None:
    """감점이 아니라 배제다. 감점으로만 두면 다른 장점이 상쇄해서 관리종목이
    후보에 남는다."""
    seeded.append(
        "documents",
        [{
            "entity_id": "KR:000100", "valid_from": NOW - timedelta(days=5),
            "observed_at": NOW - timedelta(days=5), "source": "dart",
            "doc_id": "2026080100001", "doc_type": "distress",
            "title": "불성실공시법인지정", "filer": "거래소", "url": "", "raw_path": None,
        }],
        ingest_run_id="doc-1",
    )

    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    assert "KR:000100" not in [item.entity_id for item in result.candidates]
    assert "부실 공시" in result.trace.dropped["KR:000100"]


def test_거부_상한이_펀드를_멈추지_않는다(seeded) -> None:
    """News·SNS 가 후보 전부를 막아도 상한까지만 반영한다.

    LLM 판정 하나로 그날 포트폴리오가 통째로 현금이 되게 두지 않는다.
    """
    seeded.append(
        "verdicts",
        [{
            "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
            "source": "test", "analyst": "news", "analyst_version": "news-v0.1.0",
            "decision": "reject", "severity": 0.9, "category": "fraud",
            "reason": "테스트", "expires_at": NOW + timedelta(days=3),
        } for entity in ("KR:000100", "KR:000200", "KR:000300")],
        ingest_run_id="v-seed",
    )

    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    # 3종목 × 30% → 상한 1건. 나머지 둘은 살아남는다.
    assert len(result.candidates) == 2
    assert any("상한" in note for note in result.trace.notes)


def test_만료된_거부는_효력이_없다(seeded) -> None:
    """영구 차단은 존재할 수 없다는 것이 verdicts 스키마의 규칙이다."""
    seeded.append(
        "verdicts",
        [{
            "entity_id": "KR:000100", "valid_from": NOW - timedelta(days=10),
            "observed_at": NOW - timedelta(days=10), "source": "test",
            "analyst": "news", "analyst_version": "news-v0.1.0",
            "decision": "reject", "severity": 0.9, "category": "fraud",
            "reason": "테스트", "expires_at": NOW - timedelta(days=1),
        }],
        ingest_run_id="v-old",
    )

    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    assert "KR:000100" in [item.entity_id for item in result.candidates]


def test_섹터_데이터가_없으면_조용히_넘어가지_않는다(seeded) -> None:
    """건너뛴 사실을 남긴다. 안 남기면 나중에 "섹터 상한이 왜 안 걸렸지" 를
    아무도 묻지 않게 된다."""
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    assert any("섹터" in note for note in result.trace.notes)
