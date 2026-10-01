"""DF2(docs/protocols/df2-2026-10.md) — 두 번째 금고 판정부의 DF2 경로. 합성 자료만(창고·금고 창 자료는 안 읽는다).

지키는 것:
 ① 등록 — DF2 는 앞당김 문서(해시 고정)의 `창` 줄이 아니라 **제 문서·제 해시**로 second 창에 붙는다. 문서가 바뀌면 그 창 전체를 거부.
 ② 발동 규칙 — 그 세션과 바로 앞 세션 모두 p_위기 > 0.5 일 때만(켜기 확인), 한 세션이라도 아니면 끈다(끄기 즉시). 확률 없음은 멈춤.
 ③ 판정 불가 셋(발동 세션 5 · 재조정일 1 · C1′ MDD −5%)이 기준보다 먼저 — 보류는 시행 미소진.
 ④ 기준 넷은 DF 와 같은 값 · 지표 키 누락은 크게 멈춘다.
 ⑤ 얼린 BF1 해시가 없으면 판정 거부 · 금고 HMM 종가는 창 끝 뒤를 읽지 않는다.
 ⑥ 보류 두 번이면 닫는다 · 앞 판정이 보류가 아니면 다시 돌지 않는다.
 ⑦ 합성 스모크 — 채점된 C1·BF1 → 포트 → 판정이 끝까지 돈다. 발동이 없으면 DF2 ≡ C1′.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools import trial_next_four as nf
from tools import vault_judge as vj


@pytest.fixture(autouse=True)
def _restore_window():  # type: ignore[no-untyped-def]
    yield
    vj.use_window(vj.windows()["registered"])


# --------------------------------------------------------------------------- ① 등록


def test_df2_protocol_hash_is_pinned_to_the_file() -> None:
    assert vj.DF2_PROTOCOL_HASH == hashlib.sha256(vj.DF2_PROTOCOL.read_bytes()).hexdigest()[:16]
    assert vj.DF2_PROTOCOL.read_text().startswith("> **고정")


def test_df2_attaches_to_second_window_only() -> None:
    known = vj.windows()
    assert "DF2" in vj.trials_of(known["second"])
    assert "DF2" not in vj.trials_of(known["early"])
    assert "DF2" not in known["second"].trials            # 앞당김 문서의 `창` 줄은 그대로다
    assert vj.protocol_path("DF2") == vj.DF2_PROTOCOL
    assert vj.FAMILY["DF2"] == "selection"


def test_changed_df2_doc_blocks_the_second_window(monkeypatch: pytest.MonkeyPatch) -> None:
    second = vj.windows()["second"]
    assert vj.extra_problems(second) == []
    monkeypatch.setitem(vj.EXTRA_PROTOCOLS, "DF2", (vj.DF2_PROTOCOL, "0" * 16))
    assert vj.extra_problems(second) and "고정 뒤 문서가 바뀌었다" in vj.extra_problems(second)[0]
    assert any("DF2" in p for p in vj.registration_problems(second))
    monkeypatch.setitem(vj.EXTRA_PROTOCOLS, "DF2", (vj.DF2_PROTOCOL, None))
    assert "고정되지 않았다" in vj.extra_problems(second)[0]


# --------------------------------------------------------------------------- ② 발동 규칙


def _attached(p0: list[float], start: date = date(2026, 10, 1)) -> pd.DataFrame:
    days = [start + timedelta(days=i) for i in range(len(p0))]
    return pd.DataFrame({"session": days, "market": "KR", "p0": p0})


def test_fire_needs_two_sessions_and_releases_at_once() -> None:
    p0 = [0.9, 0.2, 0.9, 0.9, 0.9, 0.4, 0.9, 0.51]
    now = _attached(p0)
    prev = _attached([0.1, *p0[:-1]])                       # 창 앞 세션(0.1)은 발동 아님
    fire = vj.df2_fire(now, prev, 0.5)["fire"].tolist()
    assert fire == [False, False, False, True, True, False, False, True]


def test_threshold_is_strict_like_df() -> None:
    now, prev = _attached([0.5, 0.5]), _attached([0.5, 0.5])
    assert not vj.df2_fire(now, prev, 0.5)["fire"].any()   # 0.5 는 발동 아님(DF 와 같다)


def test_missing_probability_stops() -> None:
    now, prev = _attached([0.9, np.nan]), _attached([0.9, 0.9])
    with pytest.raises(ValueError, match="확률이 없는"):
        vj.df2_fire(now, prev, 0.5)


def test_weights_match_df_shape_and_reduce_to_c1_without_fire() -> None:
    fire = pd.DataFrame({"session": [date(2026, 10, 1), date(2026, 10, 2)], "market": "KR", "fire": [True, False]})
    w = vj.df2_weights(fire, 0.5)
    assert w[["C1", "BF2"]].to_numpy().tolist() == [[0.5, 0.5], [1.0, 0.0]]
    # 발동이 없으면 DF2 점수 = C1′ 점수 — 같은 `weighted` 로 합성한다.
    pcts = pd.DataFrame({"entity_id": ["a", "b"], "session": [date(2026, 10, 2)] * 2, "market": "KR",
                         "C1": [0.25, 0.75], "BF2": [0.9, 0.1]})
    off = vj.df2_weights(fire.iloc[[1]], 0.5)
    assert nf.weighted(pcts, off)["pred"].tolist() == nf.weighted(pcts, nf.constant_weights(pcts, C1=1.0))["pred"].tolist()


def test_rebalance_days_follow_portfolio_schedule() -> None:
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(25)]
    pred = pd.DataFrame({"session": days, "entity_id": "a", "pred": 0.0})
    ret = pd.DataFrame(index=days[1:])                      # 첫날은 수익이 없다 → 축에서 빠진다
    assert vj.rebalance_days(pred, ret, every=10) == [days[1], days[11], days[21]]


# --------------------------------------------------------------------------- ③④ 판정


def _row(ann: float, mdd: float, turn: float = 20.0, h1: float | None = None, h2: float | None = None) -> dict[str, float]:
    return {"ann": ann, "h1": ann if h1 is None else h1, "h2": ann if h2 is None else h2, "mdd": mdd, "turn": turn, "beta": 0.7}


def _res(df2: dict[str, float], c1: dict[str, float]) -> dict[str, list[dict[str, float]]]:
    return {"DF2": [df2] * 5, "C1′": [c1] * 5, "DF": [df2] * 5}


def test_undecidable_rules_come_first() -> None:
    good = _res(_row(0.10, -0.08), _row(0.10, -0.10))
    for kw, word in (({"fire_sessions": 4, "fire_rebalances": 1}, "확인 발동 4세션"),
                     ({"fire_sessions": 5, "fire_rebalances": 0}, "재조정일 0")):
        _, verdict = vj.judge_df2(good, **kw)
        assert verdict.startswith("보류") and word in verdict and "미소진" in verdict
    shallow = _res(_row(0.10, -0.02), _row(0.10, -0.04))
    _, verdict = vj.judge_df2(shallow, fire_sessions=9, fire_rebalances=2)
    assert verdict.startswith("보류") and "얕다" in verdict


def test_four_criteria() -> None:
    ok = _res(_row(0.098, -0.08, 22.0), _row(0.10, -0.10, 20.0))
    assert vj.judge_df2(ok, fire_sessions=9, fire_rebalances=2)[1].startswith("채택 후보")
    for bad in (_row(0.10, -0.095),                        # ① MDD 가 0.5%p 만 얕다
                _row(0.09, -0.08),                         # ② 연 −1%p
                _row(0.10, -0.08, h1=0.08, h2=0.12),       # ③ 한 구간 −2%p
                _row(0.10, -0.08, turn=25.0)):             # ④ 회전 ×1.25
        assert vj.judge_df2(_res(bad, _row(0.10, -0.10)), fire_sessions=9, fire_rebalances=2)[1] == "기각"


def test_missing_metric_stops() -> None:
    res = _res(_row(0.1, -0.1), _row(0.1, -0.1))
    res["DF2"] = [{k: v for k, v in _row(0.1, -0.1).items() if k != "h2"}] * 5
    with pytest.raises(ValueError, match="h2"):
        vj.judge_df2(res, fire_sessions=9, fire_rebalances=2)


def test_gates_equal_df_registration() -> None:
    assert (vj.DF2_GATE_MDD, vj.DF2_GATE_ANN, vj.DF2_GATE_HALF, vj.DF2_GATE_TURN) == (
        nf.DF_GATE_MDD, nf.DF_GATE_ANN, nf.DF_GATE_REGIME, nf.DF_GATE_TURN)
    assert (vj.DF2_MIN_FIRE, vj.DF2_MIN_FIRE_REBAL, vj.DF2_MIN_DEPTH) == (5, 1, -0.05)


# --------------------------------------------------------------------------- ⑤ 얼린 모델·HMM


def test_frozen_df2_refuses_without_bf1_hash(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    # BE2 사이드카가 없으면 그 자리에서 멈춘다(BF1 을 보기 전).
    monkeypatch.setattr(vj, "frozen_hashes", lambda trial: {vj.BE2_STEM: "x" * 16})
    with pytest.raises(SystemExit, match="해시가 문서"):
        vj.frozen_df2(folder=tmp_path)
    # BE2(C1) 는 맞고 BF1 해시가 비었으면 — 얼리기가 먼저다.
    monkeypatch.undo()
    if not (vj.BE2_MODELS / f"{vj.BE2_STEM}.json").exists():
        pytest.skip("얼린 BE2 파일이 없는 환경")
    monkeypatch.setattr(vj, "BF1_SIDECAR_HASH", None)
    with pytest.raises(SystemExit, match="BF1_SIDECAR_HASH"):
        vj.frozen_df2()
    monkeypatch.setattr(vj, "BF1_SIDECAR_HASH", "0" * 16)
    with pytest.raises(SystemExit, match="고정값"):
        vj.frozen_df2(folder=vj.BE2_MODELS, bf1_folder=tmp_path, bf1_hash="0" * 16)


def test_vault_index_closes_refuses_after_window_end() -> None:
    vj.use_window(vj.windows()["second"])
    with pytest.raises(ValueError, match="창 끝"):
        vj.vault_index_closes(object(), "KR", vj.VAULT_END + timedelta(days=1))  # type: ignore[arg-type]


def test_vault_index_closes_reads_only_until_end() -> None:
    vj.use_window(vj.windows()["second"])
    from tools import v2_regime_hmm as hmm

    days = [vj.VAULT_END - timedelta(days=2), vj.VAULT_END, vj.VAULT_END + timedelta(days=3)]
    seen: dict[str, object] = {}

    class FakeStore:
        def get(self, table, *, as_of, **kw):  # type: ignore[no-untyped-def]
            seen.update(table=table, as_of=as_of)
            return pd.DataFrame({"entity_id": hmm.INDEX["KR"], "valid_from": pd.to_datetime(days), "close": [1.0, 2.0, 3.0]})

    closes = vj.vault_index_closes(FakeStore(), "KR", vj.VAULT_END)  # type: ignore[arg-type]
    assert list(closes.index) == days[:2] and seen["table"] == "indices"
    assert seen["as_of"].date() == vj.VAULT_END                             # type: ignore[union-attr]


def test_prev_is_one_session_back_on_index_axis() -> None:
    days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]      # 주말을 건넌다 — 달력 하루가 아니라 세션 하나
    probs = pd.DataFrame({"p0": [0.9, 0.8, 0.1], "p1": 0.0, "p2": 0.0}, index=days)
    prev = vj.df2_prev(probs)
    assert np.isnan(prev["p0"].iloc[0]) and prev["p0"].tolist()[1:] == [0.9, 0.8]


# --------------------------------------------------------------------------- ⑥ 보류 두 번


def test_second_hold_closes_df2() -> None:
    shallow = _res(_row(0.10, -0.02), _row(0.10, -0.04))
    _, first = vj.judge_df2(shallow, fire_sessions=9, fire_rebalances=2)
    _, second = vj.judge_df2(shallow, fire_sessions=9, fire_rebalances=2, held_before=True)
    assert first.startswith("보류") and second.startswith("닫음") and "얕다" in second
    # 앞이 보류였어도 이번에 판정 가능하면 기준대로 판정한다.
    ok = _res(_row(0.098, -0.08, 22.0), _row(0.10, -0.10, 20.0))
    assert vj.judge_df2(ok, fire_sessions=9, fire_rebalances=2, held_before=True)[1].startswith("채택 후보")


@pytest.mark.parametrize("prior", ["기각", "채택 후보 — 방어", "닫음 — 보류 두 번째"])
def test_run_df2_refuses_after_a_final_verdict(monkeypatch: pytest.MonkeyPatch, prior: str) -> None:
    monkeypatch.setattr(vj, "prior_verdict", lambda store, trial: prior)
    monkeypatch.setattr(vj, "frozen_df2", lambda **kw: pytest.fail("모델을 싣기 전에 멈춰야 한다"))
    with pytest.raises(SystemExit, match="이미 판정"):
        vj.run_df2(object())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- ⑦ 합성 스모크


def _synthetic(n_days: int = 30, n_names: int = 60, seeds: tuple[int, ...] = (0, 1), *, crisis: bool = True):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(7)
    days = [d.date() for d in pd.bdate_range("2026-10-01", periods=n_days)]
    names = [f"A{i:03d}" for i in range(n_names)]
    preds: dict[str, dict[int, pd.DataFrame]] = {"C1": {}, "BF1": {}}
    for arm in preds:
        for s in seeds:
            preds[arm][s] = pd.DataFrame([(e, d, "KR", float(rng.normal())) for d in days for e in names],
                                         columns=["entity_id", "session", "market", "pred"])
    before = [d.date() for d in pd.bdate_range(end="2026-09-30", periods=5)]
    p0 = np.r_[np.full(5, 0.1), np.where(np.arange(n_days) % 15 < 8, 0.9, 0.1) if crisis else np.full(n_days, 0.1)]
    probs = pd.DataFrame({"p0": p0, "p1": (1 - p0) / 2, "p2": (1 - p0) / 2}, index=[*before, *days])
    ret = pd.DataFrame(rng.normal(-0.002, 0.02, (n_days, n_names)), index=days, columns=names)
    bench = pd.Series(rng.normal(0.0, 0.01, n_days), index=days)
    trad = {d: set(names) for d in days}
    return preds, probs, ret, bench, trad, list(seeds)


def test_smoke_runs_end_to_end_and_judges() -> None:
    lines, out = vj.df2_results(*_synthetic())
    assert out["fire_sessions"] > 0 and out["fire_rebalances"] >= 1
    assert any("확인 발동" in line for line in lines)
    for arm in ("DF2", "C1′", "DF"):
        assert len(out["res"][arm]) == 2 and all(set(vj.DF2_KEYS) <= set(r) for r in out["res"][arm])
    table, verdict = vj.judge_df2(out["res"], fire_sessions=out["fire_sessions"], fire_rebalances=out["fire_rebalances"])
    assert verdict.split(" ")[0] in ("채택", "기각", "보류") and any("판정 가능 여부" in t for t in table)


def test_smoke_without_fire_is_exactly_c1_prime() -> None:
    _, out = vj.df2_results(*_synthetic(crisis=False))
    assert out["fire_sessions"] == 0 and out["fire_rebalances"] == 0 and out["overlap"] == 1.0
    assert out["res"]["DF2"] == out["res"]["C1′"] == out["res"]["DF"]
    assert vj.judge_df2(out["res"], fire_sessions=0, fire_rebalances=0)[1].startswith("보류")
