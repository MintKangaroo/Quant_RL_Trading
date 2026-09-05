"""제약 투영 (§3 3단계) — 기준선을 **위반한 만큼만** 민다.

`risk_parity.construct_baseline` 이 낸 투영 전 비중을 받아 네 제약에 맞춘다:

    단일 종목 리스크 기여   ≤ name_rc_cap     (기본 15%)
    섹터 리스크 기여        ≤ sector_rc_cap   (기본 35%)
    하방 베타 가중평균      ≤ downside_beta_cap (기본 1.0)
    현금 하한               ≥ cash_floor      (레짐에 따라, 호출자가 준다)

## 왜 투영인가 — 덮어쓰기가 아니다

기준선이 스코어와 리스크 구조를 이미 담고 있다. 제약은 그것을 **버리는 게
아니라 경계 안으로 당긴다** — 위반한 종목·섹터만 줄이고 남는 몫은 나머지로
다시 나눈다. §5 에서 이 자리가 RL 출력에 대한 "최소 거리 투영" 이 되는데,
그때도 원칙은 같다: 위반하지 않은 부분은 건드리지 않는다.

## 리스크 기여 상한은 비선형이라 반복한다

RC_i = w_i·(Σw)_i / σ_p². 한 종목의 비중을 줄이면 **모든** 종목의 RC 가
바뀐다. 그래서 닫힌 해가 없다 — 위반한 것을 감쇠해서 줄이고 다시 재는 것을
수렴할 때까지 반복한다(water-filling). 비중을 줄이면 그 RC 는 1차보다 빠르게
주므로 √ 로 감쇠해 넘치지 않게 한다.

## 하방 베타는 이분 탐색으로 민다

가중평균 하방 베타가 상한을 넘으면 exp(−λ·베타) 로 비중을 눌러 저베타 쪽으로
옮긴다. λ 를 키울수록 저베타로 쏠리므로, 상한을 만족하는 최소 λ 를 이분
탐색한다. 모든 후보가 상한 위 베타면 만족할 수 없고 — 그때는 최선을 다한
뒤 남는 위험은 현금이 흡수한다(현금 하한과 같은 방향).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio.risk_parity import (
    risk_contributions,
    sector_risk_contributions,
)

#: RC 상한 반복 횟수. 25종목 규모면 수 회에 든다.
MAX_RC_ITERATIONS = 200

#: RC 상한 수렴 여유 — 상한을 이만큼 넘지 않으면 만족으로 본다.
RC_TOLERANCE = 1e-4

#: 하방 베타 이분 탐색의 최대 λ 와 반복.
MAX_LAMBDA = 50.0
BISECT_ITERATIONS = 60


def _renormalize(weights: pd.Series) -> pd.Series:
    total = weights.sum()
    if total <= 0:
        return weights
    return weights / total


def cap_risk_contributions(
    weights: pd.Series,
    cov: pd.DataFrame,
    sectors: dict[str, str],
    *,
    name_cap: float,
    sector_cap: float,
) -> pd.Series:
    """종목·섹터 RC 상한을 반복 감쇠로 건다. 합은 1 로 유지한다.

    위반한 종목은 √(name_cap/RC) 로, 위반한 섹터의 종목은 √(sector_cap/섹터RC)
    로 줄인다. 둘 중 더 센 감쇠를 그 종목에 먹인다 — 종목이 섹터·종목 상한을
    동시에 어길 수 있다. 줄인 뒤 다시 정규화하면 남는 몫이 나머지로 간다.
    """
    w = _renormalize(weights.copy())
    labels = {e: sectors.get(e, f"__단독:{e}") for e in w.index}
    for _ in range(MAX_RC_ITERATIONS):
        rc = risk_contributions(w, cov)
        sec_rc = sector_risk_contributions(rc, sectors)
        name_over = rc[rc > name_cap + RC_TOLERANCE]
        sec_over = sec_rc[sec_rc > sector_cap + RC_TOLERANCE]
        if name_over.empty and sec_over.empty:
            break
        factor = pd.Series(1.0, index=w.index)
        for entity in name_over.index:
            factor[entity] = min(factor[entity], np.sqrt(name_cap / rc[entity]))
        for sector in sec_over.index:
            members = [e for e in w.index if labels[e] == sector]
            damp = np.sqrt(sector_cap / sec_rc[sector])
            for entity in members:
                factor[entity] = min(factor[entity], damp)
        w = _renormalize(w * factor)
    return w


def cap_downside_beta(
    weights: pd.Series, downside_beta: pd.Series, *, cap: float
) -> pd.Series:
    """가중평균 하방 베타를 상한 이하로 민다. 합은 1 로 유지한다.

    ``downside_beta`` 는 종목별 하방 베타(대개 그 종목 섹터의 값). NaN 인
    종목은 베타를 모르므로 **누르지 않는다**(중립, λ 항에서 0 취급) — 모르는
    값으로 비중을 옮기지 않는다.
    """
    beta = downside_beta.reindex(weights.index).astype(float)
    filled = beta.fillna(0.0).to_numpy()
    valid = beta.notna().to_numpy()

    def avg_for(lmbda: float) -> tuple[pd.Series, float]:
        tilted = _renormalize(weights * np.exp(-lmbda * filled))
        # 가중평균은 베타를 아는 종목만으로 낸다.
        w_valid = tilted.to_numpy()[valid]
        b_valid = beta.to_numpy()[valid]
        if w_valid.sum() <= 0:
            return tilted, 0.0
        return tilted, float((w_valid * b_valid).sum() / w_valid.sum())

    base, base_avg = avg_for(0.0)
    if not np.isfinite(base_avg) or base_avg <= cap:
        return base
    # λ 를 키우면 저베타로 쏠려 평균이 내려간다. 최소 λ 를 이분 탐색.
    lo, hi = 0.0, MAX_LAMBDA
    _, hi_avg = avg_for(hi)
    if hi_avg > cap:
        # 최대 λ 로도 못 맞춘다 — 후보가 죄다 고베타다. 최선(hi)을 쓴다.
        return avg_for(hi)[0]
    for _ in range(BISECT_ITERATIONS):
        mid = (lo + hi) / 2
        _, mid_avg = avg_for(mid)
        if mid_avg > cap:
            lo = mid
        else:
            hi = mid
    return avg_for(hi)[0]


def project(
    weights: pd.Series,
    *,
    cov: pd.DataFrame,
    sectors: dict[str, str],
    downside_beta: pd.Series,
    name_rc_cap: float,
    sector_rc_cap: float,
    downside_beta_cap: float,
    cash_floor: float,
) -> pd.Series:
    """네 제약을 모두 건 최종 목표 비중. 합은 ``1 − cash_floor`` 이하.

    **상한은 실현 가능해야 한다.** RC 합은 언제나 1 이라, 종목 상한이 ``c`` 면
    종목이 ``1/c`` 개 이상, 섹터 상한이 ``s`` 면 섹터가 ``1/s`` 개 이상이라야
    만족할 수 있다(0.15 → 7종목, 0.35 → 3섹터). 실전 후보는 24종목이라
    넉넉하지만, 후보가 그보다 적으면 이 투영은 상한에 **최대한 가깝게** 줄일
    뿐 넘길 수밖에 없다 — 없는 종목으로 위험을 나눌 수는 없다.

    순서: 하방 베타 → RC 상한 → 현금 하한. 하방 베타 틸트가 비중을 옮기므로
    RC 상한을 그 뒤에 다시 걸어, 저베타로 쏠리며 생긴 집중을 잡는다. 현금
    하한은 마지막에 전체를 스케일 다운한다 — RC 는 분수라 스케일에 안 변하니
    현금을 마지막에 빼도 상한은 유지된다.
    """
    if weights.empty:
        return weights
    w = _renormalize(weights.copy())
    w = cap_downside_beta(w, downside_beta, cap=downside_beta_cap)
    w = cap_risk_contributions(
        w, cov, sectors, name_cap=name_rc_cap, sector_cap=sector_rc_cap
    )
    invested = max(0.0, 1.0 - cash_floor)
    return w * invested
