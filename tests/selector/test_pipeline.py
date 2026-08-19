"""여섯 단계를 진짜 창고 위에서. 목으로 통과하는 파이프라인 테스트는
파이프라인을 검증하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.selector import candidates as candidates_module
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
            "entity_id": "fundamental", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0,
        }],
        ingest_run_id="w-seed",
    )
    store.append(
        "signals",
        [{
            "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
            "source": "test", "analyst": "fundamental", "analyst_version": "fundamental-v0.1.0",
            "score": score, "confidence": 1.0, "horizon_days": 5,
            "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
        } for entity, score in zip(entities, [0.9, 0.5, 0.1], strict=True)],
        ingest_run_id="s-seed",
    )
    return store


def test_후보가_점수순으로_나온다(seeded) -> None:
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    assert [item.entity_id for item in result.candidates][:2] == ["KR:000100", "KR:000200"]
    assert result.weights == {"fundamental": 1.0}


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


def _seed_entities(store, entities: list[str], *, tag: str) -> None:
    """seeded 픽스처와 같은 모양으로 종목을 더 추가한다 (섹터 상한 시험용).

    universe·prices 를 400세션 통째로 채우는 이유는 seeded 픽스처와 같다 —
    상장 6개월 경과 등 유니버스 필터를 통과해야 후보 단계까지 온다.
    """
    universe_rows, price_rows, signal_rows = [], [], []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(entities):
            universe_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 20_000.0 + index * 5 + offset * 50
            price_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None,
            })
    for offset, entity in enumerate(entities):
        signal_rows.append({
            "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
            "source": "test", "analyst": "fundamental", "analyst_version": "fundamental-v0.1.0",
            # 기존 3종목(0.9~0.1)보다 낮게 둬 순위를 방해하지 않는다.
            "score": 0.05 - offset * 0.001, "confidence": 1.0, "horizon_days": 5,
            "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
        })
    store.append("universe", universe_rows, ingest_run_id=f"u-{tag}")
    store.append("prices", price_rows, ingest_run_id=f"p-{tag}")
    store.append("signals", signal_rows, ingest_run_id=f"s-{tag}")


def test_섹터를_주입하면_상한이_걸린다(seeded) -> None:
    """상한 **기계 자체는 멀쩡하다.** 지금 안 걸리는 건 쓸 만한 업종 분류가
    없어서지 로직이 없어서가 아니다 — 진짜 업종을 받는 날 이 테스트가 그대로
    통과해야 한다.

    n_candidates=24, sector_cap=0.35 → 섹터당 8종목. 한 섹터에 9종목을
    넣으면 하나는 반드시 잘려야 한다.
    """
    entities = [f"KR:9{i:05d}" for i in range(9)]
    _seed_entities(seeded, entities, tag="sect-cap")

    result = pipeline.run(
        seeded,
        as_of=NOW,
        market="KR",
        equity=100_000_000.0,
        sectors=dict.fromkeys(entities, "반도체"),
    )

    chosen = [item.entity_id for item in result.candidates if item.entity_id in entities]
    assert len(chosen) == 8
    dropped = {
        entity: reason for entity, reason in result.trace.dropped.items() if entity in entities
    }
    assert any("섹터 상한" in reason for reason in dropped.values())


def test_창고의_소속부로는_섹터_상한을_걸지_않는다(seeded) -> None:
    """**창고에 sectors 가 있어도 자동으로 쓰지 않는다.**

    KRX 일별매매의 ``SECT_TP_NM`` 은 업종이 아니라 KOSDAQ 소속부다
    (우량기업부·벤처기업부…). KOSPI 는 전 종목이 빈 문자열이라 아예 안 걸리고,
    KOSDAQ 만 시장 등급으로 나뉜다. 그걸로 건 상한은 상관 분산이 아닌데
    화면에는 "섹터 상한 적용됨" 이 뜬다 — 분산되고 있다는 착시가 생긴다.

    그래서 자동 연결을 끊었고, **끊었다는 사실이 흔적에 남아야 한다.**
    """
    entities = [f"KR:9{i:05d}" for i in range(9)]
    _seed_entities(seeded, entities, tag="sect-auto")
    seeded.append(
        "sectors",
        [
            {
                "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
                "source": "test", "market": "KR", "sector": "우량기업부",
            }
            for entity in entities
        ],
        ingest_run_id="sec-auto",
    )

    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=100_000_000.0)

    # 9종목이 한 "섹터" 인데도 아무도 안 잘린다 — 상한을 안 걸었기 때문이다.
    chosen = [item.entity_id for item in result.candidates if item.entity_id in entities]
    assert len(chosen) == 9
    assert not any("섹터 상한" in reason for reason in result.trace.dropped.values())
    assert any("소속부" in note for note in result.trace.notes)


def test_섹터는_이중시간이다(seeded) -> None:
    """종목이 업종을 옮기면, 과거 시점 조회는 옛 섹터를 봐야 한다."""
    entity = "KR:000100"
    old_day = SESSIONS[100]
    new_day = SESSIONS[395]  # sector_map 의 기본 lookback(30일) 안에 들어야 한다
    seeded.append(
        "sectors",
        [
            {
                "entity_id": entity, "valid_from": old_day, "observed_at": old_day,
                "source": "test", "market": "KR", "sector": "구업종",
            },
            {
                "entity_id": entity, "valid_from": new_day, "observed_at": new_day,
                "source": "test", "market": "KR", "sector": "신업종",
            },
        ],
        ingest_run_id="sec-move",
    )

    past = candidates_module.sector_map(
        seeded, as_of=old_day + timedelta(hours=1), entities=[entity], market="KR", source="test"
    )
    present = candidates_module.sector_map(
        seeded, as_of=NOW, entities=[entity], market="KR", source="test"
    )

    assert past[entity] == "구업종"
    assert present[entity] == "신업종"


def test_분류체계가_둘이면_고른_쪽만_나온다(seeded) -> None:
    """같은 날 같은 종목에 KRX 소속부와 DART 업종이 함께 있어도 섞이지 않는다.

    둘은 경쟁하는 정정본이 아니라 **다른 사실**이다. 자연키에 source 가 없으면
    게이트가 하나를 정정본으로 보고 읽기에서 지우는데, 파일에는 둘 다 남아
    있어서 창고를 봐도 사라진 줄 모른다. 그렇게 만들어진 dict 으로 건 섹터
    상한은 무엇을 분산시킨 것인지 아무도 말할 수 없다.
    """
    entity = "KR:000100"
    day = SESSIONS[395]
    seeded.append(
        "sectors",
        [
            {
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "krx_openapi", "market": "KR", "sector": "우량기업부",
            },
            {
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "dart_company", "market": "KR", "sector": "KSIC:264",
            },
        ],
        ingest_run_id="sec-two-sources",
    )

    krx = candidates_module.sector_map(
        seeded, as_of=NOW, entities=[entity], market="KR", source="krx_openapi"
    )
    dart = candidates_module.sector_map(
        seeded, as_of=NOW, entities=[entity], market="KR", source="dart_company"
    )

    # 한쪽이 다른 쪽을 지웠다면 둘 중 하나가 비어 있을 것이다.
    assert krx[entity] == "우량기업부"
    assert dart[entity] == "KSIC:264"


def test_섹터_미상_종목은_한_바구니로_묶이지_않는다(seeded) -> None:
    """섹터를 아는 종목 하나, 모르는 종목 둘. 모르는 둘이 같은 바구니로
    묶이면 (예: None 대신 "" 나 "기타") 상한이 둘을 서로 경쟁시킨다.

    KOSPI 는 소속부가 전부 빈 문자열이라 이 경우가 **기본값**이다 — 진짜 업종을
    붙이는 날에도 커버리지는 100% 가 아니다.
    """
    result = pipeline.run(
        seeded,
        as_of=NOW,
        market="KR",
        equity=100_000_000.0,
        sectors={"KR:000100": "반도체"},
    )

    by_id = {item.entity_id: item for item in result.candidates}
    assert by_id["KR:000100"].sector == "반도체"
    assert by_id["KR:000200"].sector is None
    assert by_id["KR:000300"].sector is None


class Test침묵의_이유:
    """합성 점수 0건은 셋 중 하나다. 셋은 서로 완전히 다른 사건이다.

    "확인할 것" 으로 뭉뚱그리면 매번 같은 조사를 처음부터 다시 한다 —
    2026-08-12 세션이 실제로 그렇게 시간을 먹었다.
    """

    @staticmethod
    def _signals(analysts, confidence):
        import pandas as pd

        return pd.DataFrame(
            [
                {"entity_id": f"KR:00{index}", "analyst": name,
                 "score": 0.5, "confidence": confidence}
                for index, name in enumerate(analysts)
            ]
        )

    def test_confidence가_전원_0이면_그렇게_말한다(self) -> None:
        note = pipeline._silent_score_reason(
            self._signals(["risk", "event"], 0.0), {"risk": 1.0, "event": 1.0}
        )
        assert "confidence 가 전원 0" in note
        # 기다리면 풀린다는 것까지 말해야 판단이 선다.
        assert "이력이 쌓이면 풀린다" in note

    def test_가중치가_없으면_기다려도_안_풀린다고_말한다(self) -> None:
        note = pipeline._silent_score_reason(
            self._signals(["risk"], 1.0), {"risk": 0.0}
        )
        assert "가중치를 받은 Analyst 가 없다" in note
        assert "기다려서 풀리지 않는다" in note

    def test_점수와_가중치가_안_겹치면_배선_사고다(self) -> None:
        """가장 조용하고 가장 나쁜 경우 — 양쪽 다 0 이 아닌데 곱이 0 이다."""
        note = pipeline._silent_score_reason(
            self._signals(["chart"], 1.0), {"risk": 1.0}
        )
        assert "안 겹친다" in note
        assert "배선 사고" in note
        # 어느 쪽이 무엇인지 이름까지 나와야 바로 고칠 수 있다.
        assert "chart" in note and "risk" in note
