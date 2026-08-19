"""Selector 계약 테스트.

여기서 고정하는 것은 **순서**와 **빠지는 방식**이다. 점수를 잘 매기는지가
아니라, 관찰 모드 Analyst 가 조용히 섞이지 않는지 · 의견 없음이 반대 의견으로
작동하지 않는지 · 거부 하나가 펀드를 멈추지 않는지를 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.selector import (
    Candidate,
    ConstraintParams,
    SelectionParams,
    SelectionTrace,
    alpha_weights,
    apply_risk_floor,
    combined_scores,
    contributions,
    select,
)

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)

PARAMS = SelectionParams(
    n_candidates=3, corr_threshold=0.7, corr_penalty=0.3, sector_cap=0.5
)


def signals(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"entity_id": entity, "analyst": analyst, "score": score, "confidence": conf}
            for entity, analyst, score, conf in rows
        ]
    )


# -- 합성 --------------------------------------------------------------------------


def test_관찰모드_Analyst는_섞이지_않는다() -> None:
    """가중치 0 은 분자·분모 양쪽에서 빠진다.

    IC 를 통과하지 못한 Analyst 가 조용히 섞이는 경로가 있으면, 우리는 잡음에
    돈을 걸면서 그 사실을 모른다.
    """
    frame = signals([
        ("KR:A", "risk", 1.0, 1.0),
        ("KR:A", "chart", -1.0, 1.0),   # 관찰 모드
    ])

    result = combined_scores(frame, {"risk": 1.0, "chart": 0.0})

    assert result["KR:A"] == pytest.approx(1.0)


def test_의견_없음은_반대_의견이_아니다() -> None:
    """점수를 안 낸 Analyst 는 분모에서도 빠진다.

    중립 0 으로 채워 분모에 남기면 flow_kr 처럼 일부 종목만 보는 Analyst 가
    다른 Analyst 의 강한 의견을 희석한다 — 의견이 없는 것과 중립 의견은 다르다.
    """
    frame = signals([
        ("KR:A", "risk", 1.0, 1.0),
        ("KR:B", "risk", 1.0, 1.0),
        ("KR:B", "flow_kr", 1.0, 1.0),   # B 만 수급 관측이 있다
    ])

    result = combined_scores(frame, {"risk": 1.0, "flow_kr": 1.0})

    # A 는 risk 하나로 1.0. 희석되지 않는다.
    assert result["KR:A"] == pytest.approx(1.0)
    assert result["KR:B"] == pytest.approx(1.0)


def test_신뢰도가_낮으면_덜_반영된다() -> None:
    frame = signals([
        ("KR:A", "risk", 1.0, 1.0),
        ("KR:A", "event", -1.0, 0.1),
    ])

    result = combined_scores(frame, {"risk": 1.0, "event": 1.0})

    assert 0.0 < result["KR:A"] < 1.0


def test_점수_분해를_남긴다() -> None:
    """왜 이 점수인지 못 남기면 결정을 설명할 수 없다."""
    frame = signals([("KR:A", "risk", 1.0, 1.0), ("KR:A", "event", 0.5, 0.5)])

    parts = contributions(frame, {"risk": 0.6, "event": 0.4}, "KR:A")

    assert [item.analyst for item in parts] == ["risk", "event"]
    assert parts[0].share > parts[1].share


# -- 선정 --------------------------------------------------------------------------


def test_상관_감점이_순위를_바꾼다() -> None:
    """감점 후 점수가 낮아지면 다른 종목이 먼저 뽑혀야 한다.

    한 번 정렬해 놓고 훑으면 감점이 순위에 반영되지 않는다. 그러면 상관 페널티는
    이름만 있고 실제로는 아무것도 바꾸지 않는다.
    """
    scores = pd.Series({"KR:A": 1.00, "KR:B": 0.90, "KR:C": 0.80})
    # A 와 B 가 매우 비슷하다.
    corr = pd.DataFrame(
        [[1.0, 0.95, 0.1], [0.95, 1.0, 0.1], [0.1, 0.1, 1.0]],
        index=["KR:A", "KR:B", "KR:C"],
        columns=["KR:A", "KR:B", "KR:C"],
    )

    chosen = select(scores=scores, params=PARAMS, correlations=corr)

    # B 는 0.90 − 0.30 = 0.60 이 되어 C(0.80) 뒤로 밀린다.
    assert [item.entity_id for item in chosen] == ["KR:A", "KR:C", "KR:B"]
    assert chosen[2].penalized


def test_섹터_상한이_걸린다() -> None:
    scores = pd.Series({"KR:A": 1.0, "KR:B": 0.9, "KR:C": 0.8, "KR:D": 0.7})
    sectors = {"KR:A": "반도체", "KR:B": "반도체", "KR:C": "반도체", "KR:D": "은행"}
    trace = SelectionTrace()

    chosen = select(scores=scores, params=PARAMS, sectors=sectors, trace=trace)

    # n_candidates 3 × sector_cap 0.5 → 섹터당 1종목.
    assert [item.entity_id for item in chosen] == ["KR:A", "KR:D"]
    assert "섹터 상한" in trace.dropped["KR:B"]


def test_후보_수_상한을_지킨다() -> None:
    scores = pd.Series({f"KR:{i:03d}": 1.0 - i / 100 for i in range(10)})

    chosen = select(scores=scores, params=PARAMS)

    assert len(chosen) == PARAMS.n_candidates


def test_상관을_모르는_종목은_감점하지_않는다() -> None:
    """모르는 것을 0 으로 채우면 신규 상장주가 항상 분산 효과가 큰 것처럼 보인다."""
    scores = pd.Series({"KR:A": 1.0, "KR:NEW": 0.9})
    corr = pd.DataFrame([[1.0]], index=["KR:A"], columns=["KR:A"])

    chosen = select(scores=scores, params=PARAMS, correlations=corr)

    assert [item.entity_id for item in chosen] == ["KR:A", "KR:NEW"]
    assert not chosen[1].penalized


# -- 제약 Analyst (태스크 #32) -------------------------------------------------------


def test_risk는_알파_가중치에서_빠진다() -> None:
    """측정 가중치에 risk 가 1.0 으로 있어도 알파에는 안 들어간다.

    **0 으로 덮는 것과 키를 지우는 것은 다르다.** 0 으로 두면 화면이 그것을
    관찰 모드("아직 못 미더운 Analyst")로 읽고, 언젠가 누군가 risk 의 IC 를
    보고 가중치를 돌려준다.
    """
    measured = {"fundamental": 1.0, "risk": 1.0, "event": 1.0}

    assert alpha_weights(measured) == {"fundamental": 1.0, "event": 1.0}
    assert "risk" not in alpha_weights(measured)


def test_위험_하한은_꼬리만_자른다() -> None:
    """하위 백분위만 빠지고 나머지 순서는 그대로다.

    제약이 순위를 건드리면 그건 제약이 아니라 알파다 — 자리를 옮긴 의미가 없다.
    """
    frame = signals([
        (f"KR:{i:03d}", "risk", float(i), 1.0) for i in range(10)
    ])
    scores = pd.Series({f"KR:{i:03d}": float(10 - i) for i in range(10)})

    kept = apply_risk_floor(
        scores, signals=frame, params=ConstraintParams(risk_floor_percentile=0.2)
    )

    # risk 가 가장 낮은 둘(000·001)이 빠진다. 남은 것의 순서는 안 바뀐다.
    assert "KR:000" not in kept.index
    assert "KR:001" not in kept.index
    assert list(kept.index) == [f"KR:{i:03d}" for i in range(2, 10)]


def test_위험_점수가_없으면_자르지_않는다() -> None:
    """관측 없음은 위험 판단이 아니다.

    risk 는 60세션 이상 가격이 있는 종목에만 의견을 낸다. 관측이 없는 것을
    "위험하다" 로 읽으면 신규 상장주와 수집 구멍이 같은 처분을 받는다.
    """
    frame = signals([
        ("KR:A", "risk", -1.0, 1.0),
        ("KR:B", "risk", 1.0, 1.0),
    ])
    scores = pd.Series({"KR:A": 1.0, "KR:B": 2.0, "KR:C": 3.0})

    kept = apply_risk_floor(
        scores, signals=frame, params=ConstraintParams(risk_floor_percentile=0.5)
    )

    assert "KR:A" not in kept.index      # 관측됐고 하위다 — 잘린다
    assert "KR:C" in kept.index          # 관측이 없다 — 통과시킨다


def test_위험_신호가_통째로_없으면_흔적에_남긴다() -> None:
    """조용히 통과시키면 "제약이 걸렸다" 와 "걸 게 없었다" 가 같아 보인다."""
    trace = SelectionTrace()
    scores = pd.Series({"KR:A": 1.0, "KR:B": 2.0})

    kept = apply_risk_floor(
        scores,
        signals=signals([("KR:A", "event", 1.0, 1.0)]),
        params=ConstraintParams(risk_floor_percentile=0.2),
        trace=trace,
    )

    assert list(kept.index) == ["KR:A", "KR:B"]
    assert any("안전이 확인된 것이 아니다" in note for note in trace.notes)


def test_하한_0은_제약을_끈다() -> None:
    """끈 것과 걸었는데 아무도 안 걸린 것을 구분한다."""
    trace = SelectionTrace()
    frame = signals([("KR:A", "risk", -9.0, 1.0), ("KR:B", "risk", 9.0, 1.0)])
    scores = pd.Series({"KR:A": 1.0, "KR:B": 2.0})

    kept = apply_risk_floor(
        scores, signals=frame, params=ConstraintParams(risk_floor_percentile=0.0),
        trace=trace,
    )

    assert list(kept.index) == ["KR:A", "KR:B"]
    assert any("적용하지 않았다" in note for note in trace.notes)
