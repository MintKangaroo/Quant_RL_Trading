"""시행 P1-a′ 러너 — 합성 자료만(창고·캐시를 읽지 않는다).

지키는 것: 구간은 거래가능 명단 안 시총 순위 · 구간 밖 점수는 지운다 · 대조는 같은 재조정 세기 ·
시총 상한 30% · 판정 기준 값 · 해시 고정 전에는 판정 거부.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools import trial_p1a_prime as p1a


def _days(n: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def test_zone_rank_is_within_tradable_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(p1a, "ZONES", {"A1": (1, 2), "A2": (3, 4)})
    d = date(2026, 1, 2)
    caps = pd.DataFrame({"session": d, "entity_id": list("abcde"), "cap": [50.0, 40, 30, 20, 10]})
    members = p1a.zone_members(caps, {d: {"b", "c", "d", "e"}})       # a 는 거래 불가 → 순위에서 빠진다
    assert members["A1"][d] == {"b", "c"} and members["A2"][d] == {"d", "e"}


def test_mask_zone_drops_outside_and_empty_sessions() -> None:
    days = _days(3)
    wide = pd.DataFrame(1.0, index=days, columns=list("abc"))
    out = p1a.mask_zone(wide, {days[0]: {"a"}, days[1]: {"b", "c"}})
    assert out.loc[days[0]].notna().tolist() == [True, False, False]
    assert out.loc[days[1]].notna().tolist() == [False, True, True]
    assert out.loc[days[2]].isna().all()                              # 구간 정보 없음 → 전 유니버스로 새지 않는다


def test_cap_limit_redistributes() -> None:
    three = p1a.capped(pd.Series([90.0, 5, 5]), 0.30)                # 3 × 0.3 < 1 → 상한을 못 지키니 동일가중
    assert three.tolist() == pytest.approx([1 / 3] * 3)
    few = p1a.capped(pd.Series([90.0, 10.0]), 0.30)                   # 2 × 0.3 < 1 → 동일가중
    assert few.tolist() == [0.5, 0.5]
    many = p1a.capped(pd.Series([60.0, 10, 10, 10, 10]), 0.30)
    assert many.iloc[0] == pytest.approx(0.30) and many.sum() == pytest.approx(1.0)


def test_static_book_rebalances_on_long_leg_schedule() -> None:
    days = _days(25)
    ret = pd.DataFrame(0.01, index=days, columns=list("ab"))
    calls: list[date] = []

    def target(day: date) -> pd.Series:
        calls.append(day)
        return pd.Series([0.5, 0.5], index=list("ab"))

    daily, turn = p1a.static_book(days, ret, target, every=10)
    assert calls == [days[0], days[10], days[20]]
    assert turn.iloc[0] == 1.0 and daily.iloc[0] == pytest.approx(0.01 - p1a.ONE_WAY_COST)
    assert daily.iloc[1] == pytest.approx(0.01)                      # 그 사이는 비용 없이 드리프트


def test_gates_match_registration() -> None:
    assert p1a.ZONES == {"A1": (1, 200), "A2": (201, 700)}
    assert (p1a.N, p1a.MULT, p1a.EVERY, p1a.CAP_LIMIT) == (24, 3, 10, 0.30)
    assert (p1a.GATE_ANN, p1a.GATE_T, p1a.GATE_SHARE, p1a.GATE_REGIME, p1a.GATE_MDD, p1a.GATE_TURN) == (
        0.01, 2.0, 4, -0.01, 0.02, 1.2)
    assert (p1a.MIN_ACTIVE_SHARE, p1a.MIN_IC) == (0.05, 0.02)


def _row(ann: float, mdd: float = -0.2, turn: float = 20.0, reg: float | None = None) -> dict[str, float]:
    return {"ann": ann, "mdd": mdd, "turn": turn, **{g: ann if reg is None else reg for g in p1a.REGIMES}}


def _case(a1: dict[str, float], edge: float):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(0)
    kew = pd.Series(rng.normal(0, 0.01, 500))
    res = {"A1": [a1] * 5}
    ctrl = {"A1": {"K-EW": _row(0.10), "K-CAP": _row(0.12)}}
    return res, ctrl, {"A1": kew + edge + rng.normal(0, 0.001, 500)}, {"A1": kew}


def test_judge_four_criteria() -> None:
    ok = _case(_row(0.13), 0.002)
    assert vjudge(*ok).startswith("채택 후보 A1")
    assert vjudge(*_case(_row(0.105), 0.002)) == "기각"                  # ① +0.5%p
    assert vjudge(*_case(_row(0.13, reg=0.08), 0.002)) == "기각"         # ③ 국면 −2%p
    assert vjudge(*_case(_row(0.13, mdd=-0.25), 0.002)) == "기각"        # ④ MDD 5%p 깊다
    assert vjudge(*_case(_row(0.13, turn=30.0), 0.002)) == "기각"        # ④ 회전 × 1.5
    assert vjudge(*_case(_row(0.13), 0.0)) == "기각"                     # ① NW t 미달


def vjudge(res, ctrl, daily, kew):  # type: ignore[no-untyped-def]
    return p1a.judge(res, ctrl, daily, kew, cur_turn=20.0)[1]


def test_run_refuses_before_hash_is_fixed(capsys: pytest.CaptureFixture[str]) -> None:
    assert p1a.PROTOCOL_HASH is None
    assert p1a.cmd_run(object(), save=False) == 2  # type: ignore[arg-type]
    assert "판정 거부" in capsys.readouterr().out
