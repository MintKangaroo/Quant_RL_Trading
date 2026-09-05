"""리스크 기여 균등 + 스코어 틸트 (§3).

스코어 비례 배분은 금액을 나눈다. 변동성 큰 반도체와 작은 통신에 같은 금액을
넣으면 **위험은 반도체가 3배**다. 그래서 금액이 아니라 위험을 나눈다.

두 단계로 간다:

    1단계  섹터별 리스크 기여를 균등하게 만드는 비중  (기준선)
    2단계  w ∝ 기준선 × exp(score_tilt · score)        (스코어로 틸트)

제약 투영(섹터·종목 리스크 기여 상한, 하방 베타, 현금 하한)은 다음 자리다
(`constraints` 로 뺀다). 여기까지는 **투영 전** 목표 비중이다.

## 리스크 기여란

    RC_i = w_i · (Σw)_i / (wᵀΣw)          (종목 i 가 포트폴리오 위험에 기여한 몫)

RC 의 합은 1 이다 — 위험을 100% 로 두고 누가 얼마씩 지는지 본다. 섹터 RC 는
그 섹터 종목들의 RC 합이다.

## 왜 섹터 균등인가

종목 균등(각 종목 RC 동일)은 한 섹터에 종목이 몰리면 그 섹터가 위험을
독식한다 — 반도체 10종목이 각자 균등해도 반도체 섹터가 10몫이다. §3 이
경계한 "상위 20종목이 전부 반도체" 가 정확히 이 그림이다. 그래서 **섹터에
먼저 균등한 위험 예산**을 주고, 그 안에서 종목으로 나눈다.

    b_i = 1 / (섹터 수 · 그 섹터의 종목 수)

이 예산으로 리스크 버짓을 풀면 섹터 RC 가 전부 1/(섹터 수) 로 같아진다.

## 스코어 틸트

``score_tilt`` 이 공격성이다. 0 이면 순수 리스크 패리티, 크면 스코어 비례에
가까워진다. **진화 탐색 대상**이다(Selector 유전자). RL 할인율 ``gamma`` 와
헷갈리지 않게 이름을 나눴다.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

#: 리스크 버짓 좌표하강의 최대 순회. 25종목 규모면 수십 회에 수렴한다.
MAX_ITERATIONS = 1000

#: 수렴 판정 — 순회 사이 비중 변화의 최대 절댓값.
TOLERANCE = 1e-9


def risk_contributions(weights: pd.Series, cov: pd.DataFrame) -> pd.Series:
    """종목별 분수 리스크 기여. 합은 1(총위험 대비).

    ``weights`` 와 ``cov`` 의 축을 맞춰 정렬한다. 총위험이 0 이면(비중이 다
    0) 균등으로 돌려준다 — 0 으로 나누지 않는다.
    """
    entities = list(weights.index)
    w = weights.to_numpy(dtype=float)
    sigma = cov.reindex(index=entities, columns=entities).to_numpy(dtype=float)
    portfolio_var = float(w @ sigma @ w)
    if portfolio_var <= 0:
        return pd.Series(np.full(len(entities), 1.0 / len(entities)), index=entities)
    marginal = sigma @ w
    contrib = w * marginal / portfolio_var
    return pd.Series(contrib, index=entities)


def risk_budget_weights(
    cov: pd.DataFrame, budgets: Mapping[str, float]
) -> pd.Series:
    """리스크 버짓 비중 — RC_i ∝ budgets_i 가 되게 만드는 롱온리 비중.

    Spinu(2013) 볼록화를 좌표하강으로 푼다: 다른 비중을 고정하면 i 의 1차
    조건이 w_i 의 2차식이라 양의 근이 유일하다.

        σ_ii w_i² + (Σ_{j≠i} σ_ij w_j) w_i − b_i = 0
        w_i = (−c + √(c² + 4 σ_ii b_i)) / (2 σ_ii),   c = Σ_{j≠i} σ_ij w_j

    순회하며 갱신하고 마지막에 합 1 로 정규화한다. 정규화는 비율을 안 바꾸므로
    RC 비율도 그대로다.
    """
    entities = [e for e in cov.index if e in budgets and budgets[e] > 0]
    if not entities:
        return pd.Series(dtype=float)
    sigma = cov.reindex(index=entities, columns=entities).to_numpy(dtype=float)
    b = np.array([budgets[e] for e in entities], dtype=float)
    b = b / b.sum()
    n = len(entities)
    # 분산 역가중으로 시작하면 좌표하강이 빨리 수렴한다.
    diag = np.clip(np.diag(sigma), 1e-18, None)
    w = np.sqrt(b) / np.sqrt(diag)
    w = w / w.sum()

    for _ in range(MAX_ITERATIONS):
        w_prev = w.copy()
        for i in range(n):
            c = float(sigma[i] @ w) - sigma[i, i] * w[i]
            disc = c * c + 4.0 * sigma[i, i] * b[i]
            w[i] = (-c + np.sqrt(disc)) / (2.0 * sigma[i, i])
        if np.max(np.abs(w - w_prev)) < TOLERANCE:
            break
    w = w / w.sum()
    return pd.Series(w, index=entities)


def sector_budgets(
    entities: list[str], sectors: Mapping[str, str]
) -> dict[str, float]:
    """섹터 RC 를 균등화하는 종목별 위험 예산.

    b_i = 1/(섹터 수 · 그 섹터의 종목 수). 섹터를 모르는 종목은 **자기 혼자
    한 섹터**로 본다 — "기타" 로 뭉치면 미상 종목들이 한 섹터의 예산을 나눠
    갖게 되고, 그건 우리가 모르는 것을 아는 것처럼 다루는 일이다.
    """
    labels = {e: sectors.get(e, f"__단독:{e}") for e in entities}
    size: dict[str, int] = {}
    for label in labels.values():
        size[label] = size.get(label, 0) + 1
    n_sectors = len(size)
    return {
        e: 1.0 / (n_sectors * size[labels[e]]) for e in entities
    }


def score_tilt(
    base_weights: pd.Series, scores: Mapping[str, float], tilt: float
) -> pd.Series:
    """기준선을 스코어로 틸트한다. w ∝ base × exp(tilt · score), 합 1.

    ``tilt`` 0 이면 기준선 그대로다. 스코어는 그대로(부호 포함) 쓴다 — 여기
    들어오는 종목은 이미 양수 스코어만 남은 후보다(construct 가 거른다).
    지수라 스코어 차가 클수록 비중 차가 벌어진다.
    """
    entities = list(base_weights.index)
    s = np.array([float(scores.get(e, 0.0)) for e in entities])
    raw = base_weights.to_numpy() * np.exp(tilt * s)
    total = raw.sum()
    if total <= 0:
        return base_weights
    return pd.Series(raw / total, index=entities)


def construct_baseline(
    *,
    scores: Mapping[str, float],
    cov: pd.DataFrame,
    sectors: Mapping[str, str],
    tilt: float,
) -> pd.Series:
    """투영 전 목표 비중 — 섹터 리스크 패리티 기준선에 스코어 틸트.

    **음수 스코어는 사지 않는다**(baseline.py 와 같은 원칙) — 후보에서 뺀다.
    공분산에 없는 종목도 뺀다(위험을 모르는 종목에 위험 예산을 못 준다).
    """
    candidates = [
        e for e in scores if float(scores[e]) > 0.0 and e in cov.index
    ]
    if not candidates:
        return pd.Series(dtype=float)
    cov_sub = cov.reindex(index=candidates, columns=candidates)
    budgets = sector_budgets(candidates, sectors)
    base = risk_budget_weights(cov_sub, budgets)
    if base.empty:
        return base
    return score_tilt(base, scores, tilt)


def sector_risk_contributions(
    rc: pd.Series, sectors: Mapping[str, str]
) -> pd.Series:
    """종목 RC 를 섹터로 접는다. 섹터 상한(투영)이 읽는 값이다."""
    labels = pd.Series({e: sectors.get(e, f"__단독:{e}") for e in rc.index})
    return rc.groupby(labels).sum()
