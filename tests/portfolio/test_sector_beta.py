"""섹터 상·하방 베타 — 순수 추정부를 창고 없이 검증한다.

측정 함수는 (종목수익률 · 시장수익률 · 시총 · 섹터) 를 받는 순수 함수로
갈라 뒀다. 창고를 안 태우고 **아는 답**을 넣어 베타가 그 값으로 나오는지 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio import sector_beta as sb


def _sessions(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01", periods=n, freq="B")


def test_하방_베타는_하락일만으로_난다() -> None:
    """섹터 = 시장 × 0.5(하락일) · × 1.5(상승일) 이면 하방 0.5·상방 1.5.

    부호로 날을 가르는 것이 핵심이다. 한 계수로 만든 수익률이면 상·하방이
    같게 나오므로, 일부러 두 계수를 다르게 심어 갈라지는지 본다.
    """
    rng = np.random.default_rng(0)
    idx = _sessions(300)
    market = pd.Series(rng.normal(0, 0.01, size=len(idx)), index=idx)
    sector = market.where(market >= 0, market * 0.5)
    sector = sector.where(market <= 0, market * 1.5)

    sector_returns = pd.DataFrame({"제조업": sector})
    betas = sb.betas_from_returns(
        sector_returns, market, sectors={"KR:A": "제조업"}
    )

    beta = betas["제조업"]
    assert abs(beta.down_beta - 0.5) < 1e-6
    assert abs(beta.up_beta - 1.5) < 1e-6
    assert beta.n_down > sb.MIN_SIGN_DAYS
    assert beta.n_up > sb.MIN_SIGN_DAYS


def test_표본이_얇으면_NaN_이다() -> None:
    """하락일이 MIN_SIGN_DAYS 미만이면 하방 베타는 NaN — 잡음을 채택하지 않는다."""
    idx = _sessions(30)
    # 거의 다 상승, 하락일 5개뿐.
    market = pd.Series([0.01] * 25 + [-0.01] * 5, index=idx)
    sector = market * 0.8
    betas = sb.betas_from_returns(
        pd.DataFrame({"제조업": sector}), market, sectors={"KR:A": "제조업"}
    )
    assert np.isnan(betas["제조업"].down_beta)
    assert betas["제조업"].n_down == 5


def test_시총가중은_대형주를_따른다() -> None:
    """섹터 안에서 대형주와 소형주가 반대로 움직이면 합성은 대형주 쪽이다."""
    idx = _sessions(10)
    big = pd.Series(np.linspace(0.01, 0.02, 10), index=idx)
    small = -big
    returns = pd.DataFrame({"KR:BIG": big, "KR:SMALL": small})
    caps = pd.Series({"KR:BIG": 9e12, "KR:SMALL": 1e12})
    sectors = {"KR:BIG": "제조업", "KR:SMALL": "제조업"}

    composite = sb.sector_composite_returns(returns, caps=caps, sectors=sectors)
    # 9:1 가중 → 0.9·big + 0.1·small = 0.8·big
    expected = 0.9 * big + 0.1 * small
    assert np.allclose(composite["제조업"].to_numpy(), expected.to_numpy())


def test_결측_종목은_그날_가중에서_빠진다() -> None:
    """한 종목이 그날 수익률이 없으면 섹터가 통째로 NaN 이 되지 않는다.

    결측을 0 으로 보면 그날 섹터 수익률이 아래로 눌린다. 빼고 남은 종목의
    시총으로 다시 정규화해야 한다.
    """
    idx = _sessions(3)
    a = pd.Series([0.01, 0.02, 0.03], index=idx)
    b = pd.Series([0.01, np.nan, 0.03], index=idx)  # 가운데 결측
    returns = pd.DataFrame({"KR:A": a, "KR:B": b})
    caps = pd.Series({"KR:A": 1e12, "KR:B": 1e12})
    sectors = {"KR:A": "제조업", "KR:B": "제조업"}

    composite = sb.sector_composite_returns(returns, caps=caps, sectors=sectors)
    mid = composite["제조업"].iloc[1]
    # b 가 빠졌으니 그날은 a 혼자 = 0.02, NaN 이 아니다.
    assert not np.isnan(mid)
    assert abs(mid - 0.02) < 1e-9


def test_섹터를_모르는_종목은_섞이지_않는다() -> None:
    """sectors 에 없는 종목은 어느 섹터에도 안 들어간다 (candidates 규칙)."""
    idx = _sessions(5)
    returns = pd.DataFrame({
        "KR:A": pd.Series([0.01] * 5, index=idx),
        "KR:UNKNOWN": pd.Series([0.5] * 5, index=idx),
    })
    caps = pd.Series({"KR:A": 1e12, "KR:UNKNOWN": 1e12})
    composite = sb.sector_composite_returns(
        returns, caps=caps, sectors={"KR:A": "제조업"}
    )
    assert list(composite.columns) == ["제조업"]
    assert np.allclose(composite["제조업"].to_numpy(), 0.01)


def test_방어_판정은_비대칭을_요구한다() -> None:
    """하방<0.7 이어도 상방이 그보다 안 높으면 방어가 아니라 그냥 현금."""
    defensive = sb.SectorBeta(down_beta=0.5, up_beta=0.9, n_down=50, n_up=50, n_members=3)
    cash_like = sb.SectorBeta(down_beta=0.5, up_beta=0.4, n_down=50, n_up=50, n_members=3)
    assert defensive.is_defensive
    assert not cash_like.is_defensive
