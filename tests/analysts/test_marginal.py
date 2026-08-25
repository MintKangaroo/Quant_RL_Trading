"""한계기여 가중 (ic.marginal_shares) — 합성 신호로 규칙을 검증한다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic


def _frame(entities, sessions, values) -> pd.DataFrame:
    rows = []
    for si, s in enumerate(sessions):
        for ei, e in enumerate(entities):
            rows.append({"entity_id": e, "session": s, "score": float(values[si, ei])})
    return pd.DataFrame(rows)


def _setup(seed=0, n_sessions=120, n_entities=30):
    rng = np.random.default_rng(seed)
    sessions = pd.date_range("2025-01-01", periods=n_sessions, freq="B").date
    entities = [f"KR:{i:06d}" for i in range(n_entities)]
    signal = rng.normal(size=(n_sessions, n_entities))          # 진짜 알파 축
    target = signal + rng.normal(scale=1.0, size=signal.shape)   # 타깃 = 신호 + 잡음
    targets = _frame(entities, sessions, target).rename(columns={"score": "target"})
    return rng, sessions, entities, signal, targets


def test_중복_신호는_가중치가_0_이_된다() -> None:
    """A = 강한 신호, B = A 의 잡음 낀 복사본.

    A 를 빼면 B 만 남아 크게 나빠지고(ΔIC_A > 0), B 를 빼면 A 가 다
    설명하므로 ΔIC_B ≈ 0 — event↔fundamental 의 실측 구조와 같다.
    """
    rng, sessions, entities, signal, targets = _setup()
    A = _frame(entities, sessions, signal)
    B = _frame(entities, sessions, 0.4 * signal + rng.normal(scale=2.0, size=signal.shape))
    shares, details = ic.marginal_shares({"A": A, "B": B}, targets, t_min=2.0)
    assert shares["A"] == 1.0
    assert shares["B"] == 0.0
    assert details["A"][0] > details["B"][0]


def test_상보적_신호는_둘_다_산다() -> None:
    """서로 다른 두 알파 축이면 둘 다 양의 한계기여를 받는다."""
    rng, sessions, entities, sig1, _ = _setup()
    sig2 = rng.normal(size=sig1.shape)
    target = sig1 + sig2 + rng.normal(scale=1.0, size=sig1.shape)
    targets = _frame(entities, sessions, target).rename(columns={"score": "target"})
    A = _frame(entities, sessions, sig1)
    B = _frame(entities, sessions, sig2)
    shares, _ = ic.marginal_shares({"A": A, "B": B}, targets, t_min=2.0)
    assert shares["A"] > 0.0 and shares["B"] > 0.0


def test_혼자면_1_이다() -> None:
    _, sessions, entities, signal, targets = _setup()
    A = _frame(entities, sessions, signal)
    shares, _ = ic.marginal_shares({"A": A}, targets)
    assert shares == {"A": 1.0}


def test_쌍둥이는_동등가중으로_물러선다() -> None:
    """완전한 쌍둥이는 LOO 로 안 갈린다 — 전부 0 이면 동등 가중(오늘과 동일)."""
    _, sessions, entities, signal, targets = _setup()
    A = _frame(entities, sessions, signal)
    B = _frame(entities, sessions, signal.copy())
    shares, details = ic.marginal_shares({"A": A, "B": B}, targets, t_min=2.0)
    assert shares == {"A": 1.0, "B": 1.0}
