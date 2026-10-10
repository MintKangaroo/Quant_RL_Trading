"""금고 early 추가 시행 TF·TB·TC·P1B2 — 합성 자료만. 하루 늦춤 · 제외 규칙 · 원리 시험 · 기준 · 미고정 문서는 창에 안 붙는다."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools import vault_judge as vj


@pytest.fixture(autouse=True)
def _restore_window():  # type: ignore[no-untyped-def]
    yield
    vj.use_window(vj.windows()["registered"])


def _days(n: int) -> list[date]:
    return [date(2026, 7, 1) + timedelta(days=i) for i in range(n)]


def test_unpinned_document_does_not_attach_to_early() -> None:
    if vj.ADD_PROTOCOL_HASH is None:
        assert not set(vj.ADD_TRIALS) & set(vj.trials_of(vj.windows()["early"]))
        assert vj.registration_problems(vj.windows()["early"]) == []    # 기존 다섯은 그대로 열린다
    assert all(vj.protocol_path(t) == vj.ADD_PROTOCOL for t in vj.ADD_TRIALS)
    assert {k: vj.TF_RULES[k] for k in ("TF", "TB", "TC")} == {"TF": (0.20, 0.0, 0.0), "TB": (0.20, 0.10, 0.0), "TC": (0.0, 0.0, 0.10)}
    assert (vj.TF_GATE_T, vj.TF_GATE_SHARE, vj.TF_GATE_HALF, vj.TF_GATE_TURN, vj.TF_GATE_MDD) == (-2.0, 4, -0.01, 1.3, 0.02)
    assert (vj.P1B2_GATE_TURN, vj.P1B2_GATE_SHARE, vj.P1B2_GATE_MDD) == (0.6, 3, 0.02)


def test_lag_uses_previous_session_only() -> None:
    d = _days(3)
    tsfm = pd.DataFrame({"session": [d[0], d[0], d[1], d[1]], "entity_id": ["a", "b", "a", "b"], "r_hat": [1.0, 2.0, 3.0, 4.0]})
    lagged = vj.tsfm_lagged(tsfm, [d[1], d[2]])
    assert lagged[d[1]].to_dict() == {"a": 1.0, "b": 2.0}          # d1 결정은 d0 밤의 예측
    assert lagged[d[2]].to_dict() == {"a": 3.0, "b": 4.0}
    assert d[0] not in vj.tsfm_lagged(tsfm, [d[0]])                  # 앞 세션이 없으면 제외 목록도 없다


def test_exclusion_rules() -> None:
    d = _days(1)[0]
    r = pd.Series(np.arange(10, dtype=float), index=[f"e{i}" for i in range(10)])
    tf = vj.tf_exclusions("TF", {d: r})[d]
    tb = vj.tf_exclusions("TB", {d: r})[d]
    assert tf == {"e0", "e1"} and tb == {"e0", "e1", "e9"}
    c0 = pd.DataFrame({"session": d, "entity_id": r.index, "pred": r.to_numpy()[::-1]})   # C0 는 반대로 — 결합은 가운데가 남는다
    tc = vj.tf_exclusions("TC", {d: r}, c0)[d]
    assert len(tc) == 1


def test_excluded_excess_sign() -> None:
    days = _days(12)
    names = [f"e{i}" for i in range(60)]
    ret = pd.DataFrame(0.001, index=days, columns=names)
    ret[["e0", "e1", "e2", "e3", "e4"]] = -0.01                      # 뺀 종목이 매일 진다
    trad = {d: set(names) for d in days}
    excl = {d: {"e0", "e1", "e2", "e3", "e4"} for d in days}
    m = vj.excluded_excess(excl, ret, trad)
    assert len(m) == len(days) - 4 and (m < 0).all()                 # 끝 4세션은 5세션 앞이 없어 빠진다


def _row(ann: float, turn: float = 20.0, mdd: float = -0.10) -> dict[str, float]:
    return {"ann": ann, "h1": ann, "h2": ann, "mdd": mdd, "turn": turn}


def test_judge_tf() -> None:
    rng = np.random.default_rng(0)
    strong = pd.Series(-0.01 + rng.normal(0, 0.002, 60))
    ok = vj.judge_tf("TF", [_row(0.12)] * 5, [_row(0.10)] * 5, strong)
    assert ok[1].startswith("채택 후보")
    assert vj.judge_tf("TF", [_row(0.12)] * 5, [_row(0.10)] * 5, pd.Series(rng.normal(0, 0.01, 60)))[1] == "기각"   # 원리 없음
    assert vj.judge_tf("TF", [_row(0.09)] * 5, [_row(0.10)] * 5, strong)[1] == "기각"                           # 방향
    assert vj.judge_tf("TF", [_row(0.12, turn=30.0)] * 5, [_row(0.10)] * 5, strong)[1] == "기각"                # 회전 ×1.5
    with pytest.raises(ValueError):
        vj.judge_tf("TF", [_row(0.12)] * 5, [_row(0.10)] * 5, strong.iloc[:5])


def test_judge_p1b2() -> None:
    row = lambda ann, turn, ir=1.0: {"ann": ann, "turn": turn, "mdd": -0.1, "sel_ir": ir, "sel_ann": ann}  # noqa: E731
    res = {"B0": [row(0.10, 20.0)] * 5, "B1′": [row(0.11, 5.0, 1.5)] * 5, "B2′": [row(0.11, 15.0)] * 5}
    lines, verdict = vj.judge_p1b2(res)
    assert verdict.startswith("채택 후보 B1′")                       # B2′ 는 회전비 0.75 로 ① 미달
    res["B1′"] = [row(0.09, 5.0)] * 5
    assert vj.judge_p1b2(res)[1] == "기각"


def test_tg_wiring() -> None:
    """TG — TF 규칙(하위 20%)을 합성 점수 파일에. 고정 전엔 second 창에 붙지 않는다."""
    assert vj.TF_RULES["TG"] == (0.20, 0.0, 0.0)
    assert vj.EXTRA_PROTOCOLS["TG"][0] == vj.TG_PROTOCOL and "TG" in vj.RUNNERS
    if vj.TG_PROTOCOL_HASH is None:
        assert "TG" not in vj.EXTRA_TRIALS["second"]
