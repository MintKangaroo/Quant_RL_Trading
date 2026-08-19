"""노출 제어 계약 테스트 (태스크 #39).

여기서 고정하는 것은 **얼마나 줄이냐**가 아니라 **어떻게 합치느냐**다.
배수 값은 config 이고 바뀔 수 있지만, 세 축을 곱하지 않는다는 것과 줄인
몫을 다시 정규화하지 않는다는 것은 설계다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.selector.exposure import (
    FLOOR,
    ExposureDecision,
    ExposureParams,
    apply,
    decide,
    regime_scale,
)

NOW = datetime(2026, 8, 19, 7, 0, tzinfo=UTC)

PARAMS = ExposureParams(
    below_trend=0.6,
    regime_scale={"bull": 1.0, "volatile": 1.0, "bear": 0.7, "crisis": 0.5, "unknown": 1.0},
    squeezed=0.8,
    squeeze_quantile=0.20,
)


class FakeStore:
    """지수 계열 하나만 돌려주는 창고. 노출 제어가 읽는 것이 그것뿐이다."""

    def __init__(self, closes: list[float]) -> None:
        self._closes = closes

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:  # noqa: ARG002
        if not self._closes:
            return pd.DataFrame()
        start = NOW - timedelta(days=len(self._closes))
        return pd.DataFrame({
            "close": self._closes,
            "valid_from": [start + timedelta(days=i) for i in range(len(self._closes))],
        })


def _rising(n: int = 400) -> list[float]:
    """꾸준히 오르는 지수. 마지막 값이 이평 위다."""
    return [1000.0 + i * 2.0 for i in range(n)]


def _falling(n: int = 400) -> list[float]:
    """올랐다가 꺾인 지수. 마지막 값이 이평 아래다."""
    up = [1000.0 + i * 2.0 for i in range(n - 60)]
    return [*up, *[up[-1] - i * 12.0 for i in range(1, 61)]]


# -- 축 하나씩 ---------------------------------------------------------------------


def test_지수가_이평_위면_노출을_안_줄인다() -> None:
    decision = decide(
        FakeStore(_rising()), as_of=NOW, index_id="KR:IDX:KOSPI",
        regime_state="bull", params=PARAMS,
    )
    assert decision.scale == 1.0
    assert decision.driver == "full"


def test_지수가_이평_아래면_줄인다() -> None:
    """**이것이 chart 가 원래 잘하는 일이다** — 종목 줄세우기가 아니라 시점."""
    decision = decide(
        FakeStore(_falling()), as_of=NOW, index_id="KR:IDX:KOSPI",
        regime_state="bull", params=PARAMS,
    )
    assert decision.scale == pytest.approx(0.6)
    assert decision.driver == "trend"
    assert any("이평 아래" in note for note in decision.notes)


def test_모르는_국면은_줄이지_않는다() -> None:
    """`unknown` 에서 줄이면 지수 이력이 짧은 구간마다 까닭 없이 절반만 산다.

    모른다는 것은 위험하다는 뜻이 아니다.
    """
    assert regime_scale("unknown", PARAMS)[0] == 1.0
    assert regime_scale("처음 보는 상태", PARAMS)[0] == 1.0


def test_고변동_국면_자체로는_줄이지_않는다() -> None:
    """`volatile` 은 1.0 이다 — 변동성이 높다고 무조건 줄이면 상승 변동까지 버린다.

    실제로 이 전략은 2026-06 이후 하락장에서 코스피를 +24.97%p 이겼다.
    """
    assert regime_scale("volatile", PARAMS)[0] == 1.0
    assert regime_scale("crisis", PARAMS)[0] == pytest.approx(0.5)


# -- 합치는 방식 -------------------------------------------------------------------


def test_세_축을_곱하지_않는다() -> None:
    """곱하면 셋이 조금씩 낮을 때 **아무도 위험하다고 말하지 않았는데 반토막**
    이 난다 (0.6 × 0.7 × 0.8 = 0.336). 가장 낮은 것을 따른다.
    """
    decision = decide(
        FakeStore(_falling()), as_of=NOW, index_id="KR:IDX:KOSPI",
        regime_state="bear", params=PARAMS,
    )
    # 추세 0.6 · 국면 0.7 → 최솟값 0.6. 곱이면 0.42 가 됐을 것이다.
    assert decision.scale == pytest.approx(0.6)
    assert decision.scale > 0.42


def test_가장_낮은_축의_이름이_남는다() -> None:
    """노출이 줄어든 날 이유를 못 대면 다음에 누가 이 장치를 꺼 버린다."""
    decision = decide(
        FakeStore(_rising()), as_of=NOW, index_id="KR:IDX:KOSPI",
        regime_state="crisis", params=PARAMS,
    )
    assert decision.driver == "regime"
    assert decision.scale == pytest.approx(0.5)


def test_바닥_아래로는_안_내려간다() -> None:
    """0 까지 내리면 되돌아오는 구간을 통째로 놓친다. 나가는 판단은
    킬스위치가 따로 한다 — 노출 제어는 참여를 줄이는 것이다."""
    params = ExposureParams(
        below_trend=0.05, regime_scale={"bull": 1.0}, squeezed=1.0, squeeze_quantile=0.2
    )
    decision = decide(
        FakeStore(_falling()), as_of=NOW, index_id="KR:IDX:KOSPI",
        regime_state="bull", params=params,
    )
    assert decision.scale == pytest.approx(FLOOR)
    assert any("바닥" in note for note in decision.notes)


# -- 적용 -------------------------------------------------------------------------


def test_줄인_몫은_현금이다_정규화하지_않는다() -> None:
    """**정규화하면 합이 도로 1 이 되어 노출을 줄인 것이 사라진다.**

    `apply` 가 따로 있는 이유가 그 실수를 막는 것이다.
    """
    weights = {"KR:A": 0.4, "KR:B": 0.4, "KR:C": 0.15}
    out = apply(weights, ExposureDecision(scale=0.6, driver="trend"))

    assert sum(out.values()) == pytest.approx(0.57)  # 0.95 × 0.6
    # 종목 간 상대 비중은 그대로다 — 노출만 줄었지 선택은 안 바뀌었다.
    assert out["KR:A"] / out["KR:C"] == pytest.approx(weights["KR:A"] / weights["KR:C"])


def test_배수가_1이면_원본을_그대로_돌려준다() -> None:
    weights = {"KR:A": 0.5}
    assert apply(weights, ExposureDecision()) == weights


# -- 데이터가 없을 때 ---------------------------------------------------------------


def test_지수가_없으면_줄이지_않고_이유를_남긴다() -> None:
    """**없는 데이터를 위험 신호로 읽지 않는다.** 지수를 못 받은 날 노출을
    줄이면 수집 사고가 조용히 매매 결정이 된다."""
    decision = decide(
        FakeStore([]), as_of=NOW, index_id="KR:IDX:KOSPI",
        regime_state="bull", params=PARAMS,
    )
    assert decision.scale == 1.0
    assert any("미적용" in note for note in decision.notes)


# -- 배선 (태스크 #39 의 진짜 관문) ---------------------------------------------


def test_노출_제어가_주문까지_도달한다() -> None:
    """**로그에만 남고 주문은 원래대로 나가는 것**을 막는다.

    이 저장소에서 제일 자주 나는 결함이 "코드는 있는데 아무도 안 부른다" 이고,
    이 모듈을 배선하면서 실제로 한 번 그랬다 — `exposure.apply` 결과를 새
    변수에 담아 놓고 집행 단계는 줄이기 전 `weights` 를 계속 읽었다.
    테스트는 통과하고 로그에도 "exposure" 가 찍히는데 주문만 안 줄어든다.

    그래서 `session/daily.py` 가 **줄인 비중을 집행에 넘기는지**를 소스에서
    직접 확인한다. 함수를 부르는 것만으로는 이 결함이 안 잡힌다.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "quant_rl_trading" / "session" / "daily.py"
    ).read_text(encoding="utf-8")

    assert "exposure.apply(" in source, "노출 제어를 아예 안 부른다"
    # apply 결과가 집행에 쓰이는 이름으로 되돌아와야 한다.
    applied = source.index("exposure.apply(")
    execute = source.index("# 3. 집행")
    between = source[applied:execute]
    assert "weights = scaled" in between, (
        "exposure.apply 결과가 집행 단계로 안 넘어간다 — 노출 제어가 "
        "로그에만 남고 주문은 원래대로 나간다"
    )
