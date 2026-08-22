"""제약 투영 — RC 상한·하방 베타·현금 하한을 아는 답으로 검증한다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio import constraints as C
from quant_rl_trading.portfolio.risk_parity import (
    risk_contributions,
    sector_risk_contributions,
)


def _diag_cov(names, variances) -> pd.DataFrame:
    return pd.DataFrame(np.diag(variances), index=names, columns=names)


def test_종목_RC_상한이_지켜진다() -> None:
    """한 종목이 위험을 독식하면 그 RC 를 상한까지 깎는다.

    상한은 실현 가능해야 한다 — RC 합은 1 이라 n종목·상한 ≥ 1 이어야 한다.
    여기선 4종목·0.4 = 1.6 이라 A 를 0.4 까지 깎을 수 있다.
    """
    names = ["KR:A", "KR:B", "KR:C", "KR:D"]
    # A 가 변동성이 훨씬 커서 RC 를 독식한다.
    cov = _diag_cov(names, [0.20, 0.01, 0.01, 0.01])
    sectors = {n: f"s{i}" for i, n in enumerate(names)}
    w = pd.Series({"KR:A": 0.5, "KR:B": 0.2, "KR:C": 0.2, "KR:D": 0.1})
    capped = C.cap_risk_contributions(w, cov, sectors, name_cap=0.4, sector_cap=1.0)
    rc = risk_contributions(capped, cov)
    assert rc.max() <= 0.4 + 2e-3
    assert abs(capped.sum() - 1.0) < 1e-9


def test_섹터_RC_상한이_지켜진다() -> None:
    """한 섹터에 위험이 몰리면 그 섹터를 상한까지 깎는다.

    섹터 상한 0.35 는 섹터가 셋 이상이라야 실현 가능하다(3·0.35=1.05≥1).
    반도체 2 + 통신 + 금융 으로 둔다.
    """
    names = ["KR:A", "KR:B", "KR:C", "KR:D"]
    cov = _diag_cov(names, [0.04, 0.04, 0.04, 0.04])
    sectors = {"KR:A": "반도체", "KR:B": "반도체", "KR:C": "통신", "KR:D": "금융"}
    w = pd.Series({"KR:A": 0.4, "KR:B": 0.4, "KR:C": 0.1, "KR:D": 0.1})
    capped = C.cap_risk_contributions(w, cov, sectors, name_cap=1.0, sector_cap=0.35)
    rc = risk_contributions(capped, cov)
    sec_rc = sector_risk_contributions(rc, sectors)
    assert sec_rc["반도체"] <= 0.35 + 2e-3


def test_하방_베타_가중평균이_상한을_지킨다() -> None:
    names = ["KR:LOW", "KR:HIGH"]
    beta = pd.Series({"KR:LOW": 0.4, "KR:HIGH": 1.6})
    w = pd.Series({"KR:LOW": 0.5, "KR:HIGH": 0.5})  # 평균 1.0... 상한 0.8 로 압박
    capped = C.cap_downside_beta(w, beta, cap=0.8)
    avg = (capped * beta.reindex(capped.index)).sum() / capped.sum()
    assert avg <= 0.8 + 1e-6
    # 저베타 쪽으로 옮겨졌다.
    assert capped["KR:LOW"] > capped["KR:HIGH"]


def test_하방_베타_이미_만족하면_그대로() -> None:
    names = ["KR:A", "KR:B"]
    beta = pd.Series({"KR:A": 0.5, "KR:B": 0.6})
    w = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    capped = C.cap_downside_beta(w, beta, cap=1.0)
    assert np.allclose(capped.to_numpy(), w.to_numpy())


def test_모두_고베타면_최선을_다하고_멈춘다() -> None:
    """후보가 죄다 상한 위 베타면 만족 못 한다 — 터지지 않고 최선을 낸다."""
    beta = pd.Series({"KR:A": 1.5, "KR:B": 1.6})
    w = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    capped = C.cap_downside_beta(w, beta, cap=1.0)
    assert abs(capped.sum() - 1.0) < 1e-9
    # 그래도 더 낮은 쪽으로 최대한 옮긴다.
    assert capped["KR:A"] > capped["KR:B"]


def test_NaN_베타는_안_누른다() -> None:
    beta = pd.Series({"KR:A": np.nan, "KR:HIGH": 1.8})
    w = pd.Series({"KR:A": 0.5, "KR:HIGH": 0.5})
    capped = C.cap_downside_beta(w, beta, cap=1.0)
    # 아는 종목(HIGH)만 눌려 평균이 상한 이하. A 는 모르므로 상대적으로 남는다.
    assert capped["KR:A"] >= capped["KR:HIGH"]


def test_현금_하한이_투자비중을_줄인다() -> None:
    names = ["KR:A", "KR:B"]
    cov = _diag_cov(names, [0.04, 0.04])
    sectors = {"KR:A": "s1", "KR:B": "s2"}
    beta = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    w = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    out = C.project(
        w, cov=cov, sectors=sectors, downside_beta=beta,
        name_rc_cap=0.5, sector_rc_cap=0.6, downside_beta_cap=1.0, cash_floor=0.2,
    )
    assert abs(out.sum() - 0.8) < 1e-9  # 20% 현금


def test_project_는_모든_제약을_동시에_건다() -> None:
    names = ["KR:A", "KR:B", "KR:C", "KR:D"]
    cov = _diag_cov(names, [0.25, 0.02, 0.02, 0.02])
    sectors = {"KR:A": "반도체", "KR:B": "반도체", "KR:C": "통신", "KR:D": "금융"}
    beta = pd.Series({"KR:A": 1.4, "KR:B": 1.3, "KR:C": 0.5, "KR:D": 0.6})
    w = pd.Series({"KR:A": 0.4, "KR:B": 0.3, "KR:C": 0.2, "KR:D": 0.1})
    out = C.project(
        w, cov=cov, sectors=sectors, downside_beta=beta,
        name_rc_cap=0.4, sector_rc_cap=0.5, downside_beta_cap=1.0, cash_floor=0.05,
    )
    invested = out / out.sum()
    rc = risk_contributions(invested, cov)
    sec_rc = sector_risk_contributions(rc, sectors)
    avg_beta = (invested * beta.reindex(invested.index)).sum()
    assert rc.max() <= 0.4 + 2e-3
    assert sec_rc.max() <= 0.5 + 2e-3
    assert avg_beta <= 1.0 + 1e-3
    assert abs(out.sum() - 0.95) < 1e-9
