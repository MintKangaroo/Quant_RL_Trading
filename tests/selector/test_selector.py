"""Selector 계약 테스트.

여기서 고정하는 것은 **순서**와 **빠지는 방식**이다. 점수를 잘 매기는지가
아니라, 관찰 모드 Analyst 가 조용히 섞이지 않는지 · 의견 없음이 반대 의견으로
작동하지 않는지 · 거부 하나가 펀드를 멈추지 않는지를 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from lattice.selector import (
    Candidate,
    SelectionParams,
    SelectionTrace,
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
