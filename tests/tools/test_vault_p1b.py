"""P1-b′(docs/protocols/p1b-prime-2026-10.md) — 두 번째 금고 판정부의 P1B 경로. 합성 자료만(창고·금고 창 자료는 안 읽는다).

지키는 것:
 ① 등록 — P1B 는 제 문서·제 해시로 second 창에 붙는다(DF2 와 같은 자리). 변형 셋은 등록 표 그대로.
 ② 롱 다리 — 진단 ② 의 `long_leg` 를 그대로 부른다. 데운 세션은 장부에 안 든다(창 첫 세션에 빈 손으로 시작).
 ③ 기준 넷은 등록 값 그대로 · 둘 다 통과면 선택 IR 높은 쪽 · 지표 누락은 크게 멈춘다.
 ④ 1회 시행 — 앞 판정이 있으면 다시 돌지 않는다 · 채점 구멍은 창을 줄이지 않고 멈춘다.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools import vault_judge as vj
from tools.diag_style_hedge_cost import long_leg


@pytest.fixture(autouse=True)
def _restore_window():  # type: ignore[no-untyped-def]
    yield
    vj.use_window(vj.windows()["registered"])


# --------------------------------------------------------------------------- ① 등록


def test_p1b_protocol_hash_is_pinned_to_the_file() -> None:
    assert vj.P1B_PROTOCOL_HASH == hashlib.sha256(vj.P1B_PROTOCOL.read_bytes()).hexdigest()[:16] == "12f053518c05ae3c"
    assert vj.P1B_PROTOCOL.read_text().startswith("> **고정")


def test_p1b_attaches_to_second_window_only() -> None:
    known = vj.windows()
    assert "P1B" in vj.trials_of(known["second"]) and "DF2" in vj.trials_of(known["second"])
    assert "P1B" not in vj.trials_of(known["early"])
    assert vj.protocol_path("P1B") == vj.P1B_PROTOCOL
    assert vj.FAMILY["P1B"] == "selection" and vj.RUNNERS["P1B"] is vj.run_p1b
    assert vj.extra_problems(known["second"]) == []


def test_arms_and_gates_match_registration() -> None:
    assert vj.P1B_ARMS == {"B0": (24, 10, 3, None), "B1′": (100, 20, 5, 1.0), "B2′": (24, 20, 3, None)}
    assert (vj.P1B_GATE_ANN, vj.P1B_GATE_T, vj.P1B_GATE_SHARE, vj.P1B_GATE_REGIME) == (0.01, 2.0, 4, -0.01)
    assert (vj.P1B_GATE_TURN, vj.P1B_GATE_MDD) == (0.6, 0.02)


def test_changed_p1b_doc_blocks_the_second_window(monkeypatch: pytest.MonkeyPatch) -> None:
    second = vj.windows()["second"]
    monkeypatch.setitem(vj.EXTRA_PROTOCOLS, "P1B", (vj.P1B_PROTOCOL, "0" * 16))
    assert any("P1B" in p for p in vj.registration_problems(second))


# --------------------------------------------------------------------------- ② 롱 다리 (합성)


def _market(n_names: int = 150, n_warm: int = 5, n_days: int = 45, seed: int = 0):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    start = date(2026, 10, 1)
    warm = [start - timedelta(days=n_warm - i) for i in range(n_warm)]
    days = [start + timedelta(days=i) for i in range(n_days)]
    names = [f"KR:{i:06d}" for i in range(n_names)]
    alpha = rng.normal(0, 1, n_names)
    ret = pd.DataFrame(rng.normal(0, 0.02, (n_days, n_names)) + 0.001 * alpha, index=days, columns=names)
    bench = pd.Series(rng.normal(0, 0.01, n_days), index=days)
    trad = {d: set(names) for d in days}
    preds = {s: pd.DataFrame([{"entity_id": e, "session": d, "pred": a + rng.normal(0, 0.5)}
                              for d in [*warm, *days] for e, a in zip(names, alpha, strict=True)]) for s in range(5)}
    return preds, ret, bench, trad, days, warm


def test_results_start_empty_at_window_and_use_diag_leg() -> None:
    preds, ret, bench, trad, days, warm = _market()
    out = vj.p1b_results(preds, ret, bench, trad, days, list(range(5)))
    assert out["days"][0] == days[0] and not set(warm) & set(out["days"])
    assert set(out["res"]) == set(vj.P1B_ARMS) and all(len(v) == 5 for v in out["res"].values())
    # 진단 함수 그대로 — 시드 0 B1′ 을 손으로 다시 돌린 값과 같다(데운 세션이 EMA 에만 든다).
    wide = preds[0].pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=5).mean()
    daily, turn, _ = long_leg(wide[wide.index >= days[0]], ret, trad, 100, every=20, mult=5, theta=1.0)
    assert out["res"]["B1′"][0]["ann"] == pytest.approx(float(daily.reindex(out["days"]).fillna(0.0).mean() * vj.ANN))
    assert turn.iloc[0] == 1.0                                       # 첫 구성 회전은 빈 장부에서
    # R20 변형은 R10 대조보다 회전이 적다.
    assert vj._mean(out["res"]["B2′"], "turn") <= vj._mean(out["res"]["B0"], "turn")


def test_smoke_judges_to_a_verdict() -> None:
    preds, ret, bench, trad, days, _ = _market()
    out = vj.p1b_results(preds, ret, bench, trad, days, list(range(5)))
    lines, verdict = vj.judge_p1b(out["res"], out["daily"])
    assert verdict == "기각" or verdict.startswith("채택 후보")
    assert any(line.startswith("B1′:") for line in lines) and any(line.startswith("B2′:") for line in lines)


# --------------------------------------------------------------------------- ③ 판정


def _row(ann: float, turn: float = 20.0, mdd: float = -0.05, sel_ir: float = 1.0) -> dict[str, float]:
    return {"ann": ann, "turn": turn, "mdd": mdd, "sel_ir": sel_ir, "sel_ann": ann}


def _daily(edge: float, n: int = 200) -> pd.Series:
    rng = np.random.default_rng(1)
    return pd.Series(edge + rng.normal(0, 0.001, n), index=range(n))


def _case(b1: dict[str, float], b2: dict[str, float], *, edge: float = 0.002):  # type: ignore[no-untyped-def]
    res = {"B0": [_row(0.10)] * 5, "B1′": [b1] * 5, "B2′": [b2] * 5}
    zero = pd.Series(0.0, index=range(200))
    return res, {"B0": zero, "B1′": zero + _daily(edge), "B2′": zero + _daily(edge)}


def test_four_criteria_and_best_of_two() -> None:
    res, daily = _case(_row(0.13, turn=5.0, sel_ir=1.4), _row(0.12, turn=11.0, sel_ir=1.6))
    _, verdict = vj.judge_p1b(res, daily)
    assert verdict.startswith("채택 후보 B2′")                       # 둘 다 통과 → 선택 IR 높은 쪽
    for bad in (_row(0.105, turn=5.0),                              # ① 연 +0.5%p
                _row(0.13, turn=13.0),                              # ④ 회전비 0.65
                _row(0.13, turn=5.0, mdd=-0.08)):                   # ④ MDD 3%p 깊다
        assert vj.judge_p1b(*_case(bad, bad))[1] == "기각"
    weak_t = _case(_row(0.13, turn=5.0), _row(0.13, turn=5.0), edge=0.0)
    assert vj.judge_p1b(*weak_t)[1] == "기각"                        # ① NW t 미달


def test_seed_share_needs_four_of_five() -> None:
    res, daily = _case(_row(0.13, turn=5.0), _row(0.13, turn=5.0))
    res["B1′"] = [_row(0.13, turn=5.0)] * 3 + [_row(0.05, turn=5.0)] * 2
    res["B2′"] = res["B1′"]
    assert vj.judge_p1b(res, daily)[1] == "기각"


def test_missing_metric_stops() -> None:
    res, daily = _case(_row(0.13), _row(0.13))
    res["B1′"] = [{k: v for k, v in _row(0.13).items() if k != "sel_ir"}] * 5
    with pytest.raises(ValueError, match="sel_ir"):
        vj.judge_p1b(res, daily)


# --------------------------------------------------------------------------- ④ 1회 시행 · 구멍


def test_run_refuses_after_a_prior_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vj, "prior_verdict", lambda store, trial: "기각")
    with pytest.raises(SystemExit, match="이미 판정"):
        vj.run_p1b(object())  # type: ignore[arg-type]


def test_predictions_refuse_a_hole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vj.be2_module, "session_batch", lambda store, market, as_of: "fa_features 없음")
    with pytest.raises(SystemExit, match="창을 조용히 줄이지 않는다"):
        vj.p1b_predictions(object(), [date(2026, 10, 1)], {0: object()}, as_of_of=lambda d: d)  # type: ignore[arg-type]
