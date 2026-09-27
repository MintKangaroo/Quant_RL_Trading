"""금고 판정부 — 해시 대조·날짜 잠금·기준 계산·기록 게이트.

## 왜 테스트가 있나

이 도구는 **일생에 한 번** 돈다(2026-11-23 금고 개봉). 그날 처음 실행해 보며 고칠 수는 없다. 그리고 틀렸을 때
조용히 틀린다 — 얼린 모델이 바뀌어도 숫자는 나오고, 기준 부호가 뒤집혀도 표는 인쇄된다.

그래서 여기서는 **금고 창 자료를 전혀 읽지 않고** 합성 자료로 다음 넷만 본다:

(a) 얼린 모델의 해시가 등록 문서와 다르면 판정을 거부한다 — 재학습 금지의 마지막 방어선.
(b) 개봉일(2026-11-23) 전에는 --bake·--judge 가 돌지 않는다(6차 `MEASURE_FROM` 과 같은 잠금).
(c) 시행 넷의 채택 기준이 등록 문서대로 계산된다 — 특히 BD 의 "bear 세션 10 미만이면 보류".
(d) --save 없이는 창고에 아무것도 적지 않는다(시행 미소진).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from tools import vault_judge as vj

# --------------------------------------------------------------------------- 합성 지표


def _m(ann: float, *, mdd: float = -0.10, turn: float = 3.0, h1: float | None = None,
       h2: float | None = None, ir: float = 0.5) -> dict[str, float]:
    """지표 한 줄 — 판정 함수가 읽는 열만."""
    return {"ann": ann, "mdd": mdd, "turn": turn, "ir": ir,
            "h1": ann if h1 is None else h1, "h2": ann if h2 is None else h2}


# --------------------------------------------------------------------------- (a) 해시 대조


def test_frozen_hashes_cover_all_four_trials() -> None:
    """등록 문서 넷 모두에서 모델 해시를 읽는다 — AS 의 대조는 AR 문서에 있다(등록대로)."""
    assert set(vj.frozen_hashes("AQ")) == {f"AQ-loop-s{s}" for s in vj.SEEDS}
    assert set(vj.frozen_hashes("AR")) == {f"AR-{arm}-s{s}" for arm in ("control", "treat") for s in vj.SEEDS}
    assert set(vj.frozen_hashes("AS")) == {f"AS-treat-s{s}" for s in vj.SEEDS}
    assert set(vj.frozen_hashes("BD")) == {f"BD-loop-s{s}" for s in vj.SEEDS}
    assert all(len(h) == 16 for h in vj.frozen_hashes("AR").values())


def test_hash_mismatch_refuses(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """모델 파일이 등록 뒤 바뀌었으면 판정을 거부한다 — 다시 학습하지 않는다."""
    monkeypatch.setattr(vj, "MODELS", tmp_path)
    (tmp_path / "AQ-loop-s0.txt").write_text("이건 얼린 모델이 아니다")
    expected = vj.frozen_hashes("AQ")
    with pytest.raises(SystemExit) as err:
        vj.booster("AQ-loop-s0", expected)
    assert "해시 불일치" in str(err.value)
    assert expected["AQ-loop-s0"] in str(err.value)


def test_missing_model_refuses(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vj, "MODELS", tmp_path)
    with pytest.raises(SystemExit) as err:
        vj.booster("AQ-loop-s0", vj.frozen_hashes("AQ"))
    assert "다시 학습하지 않는다" in str(err.value)


def test_unknown_model_name_refuses(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vj, "MODELS", tmp_path)
    with pytest.raises(SystemExit):
        vj.booster("AQ-loop-s9", vj.frozen_hashes("AQ"))


# --------------------------------------------------------------------------- (b) 날짜 잠금


@pytest.mark.parametrize("argv", [["--judge"], ["--bake"], ["--judge", "--trials", "BD", "--save"]])
def test_locked_before_opening(argv, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """개봉 하루 전이면 rc=2 로 멈춘다. 창고를 열기 전에 멈춰야 한다."""
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 11, 22))
    monkeypatch.setattr(vj, "Store", lambda **_: pytest.fail("잠금 전에 창고를 열었다"))
    assert vj.main(argv) == 2
    assert "금고 개봉(2026-11-23) 이후에만 돈다" in capsys.readouterr().out


def test_plan_runs_before_opening(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """--plan 은 자료를 읽지 않으므로 잠금 전에도 돈다."""
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 9, 27))
    monkeypatch.setattr(vj, "Store", lambda **_: pytest.fail("--plan 이 창고를 열었다"))
    assert vj.main(["--plan"]) == 0
    out = capsys.readouterr().out
    assert "실행 순서" in out and "--judge" in out


def test_unlocked_on_and_after_opening(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 11, 23))
    assert vj.locked("--judge") is False
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 11, 22))
    assert vj.locked("--judge") is True


# --------------------------------------------------------------------------- (c) 시행별 기준


def _aq(v0: list[float], v1: list[float], **kw) -> dict:  # type: ignore[no-untyped-def]
    return {f"s{i}": {"V0": _m(a, **kw.get("v0", {})), "V1": _m(b, **kw.get("v1", {}))}
            for i, (a, b) in enumerate(zip(v0, v1, strict=True))}


def test_aq_adopts_when_all_four_hold() -> None:
    """① 평균 +1%p ② 4중 3 ③ 흩어짐 절반 ④ MDD·회전 — 넷 다면 채택."""
    lines, verdict = vj.judge_aq(_aq([0.10, 0.02, 0.14, 0.06], [0.105, 0.095, 0.12, 0.10]))
    assert verdict.startswith("채택")
    assert all(mark in "".join(lines) for mark in ("①", "②", "③", "④"))


def test_aq_rejects_on_spread_only() -> None:
    """AM 이 기각된 그 모양 — 평균·승수는 좋은데 흩어짐이 V0′ 절반을 못 넘으면 기각."""
    v0 = [0.10, 0.02, 0.14, 0.06]            # 표준편차 약 5.2%p
    v1 = [0.13, 0.05, 0.17, 0.09]            # 평균 +3%p · 4/4 · 그러나 흩어짐이 같다
    lines, verdict = vj.judge_aq(_aq(v0, v1))
    assert verdict.startswith("기각")
    assert "③" in "".join(lines)


def test_aq_rejects_when_only_two_sources_win() -> None:
    v0 = [0.02, 0.02, 0.10, 0.10]
    v1 = [0.09, 0.09, 0.09, 0.09]            # 평균 +0.5%p 도 못 되고 2/4
    assert vj.judge_aq(_aq(v0, v1))[1].startswith("기각")


def test_aq_rejects_when_turnover_grows() -> None:
    """④ — 회전이 V0′ 보다 크면 나머지가 다 좋아도 기각."""
    res = _aq([0.02, 0.02, 0.02, 0.02], [0.05, 0.05, 0.05, 0.05])
    for row in res.values():
        row["V1"]["turn"] = 9.0
    assert vj.judge_aq(res)[1].startswith("기각")


def _ar(market_ann: dict[str, tuple[list[float], list[float]]], ic: dict[str, tuple[float, float]]) -> dict:
    return {m: {"control": {"seeds": [_m(a) for a in ct], "ic": ic[m][0]},
                "treat": {"seeds": [_m(a) for a in tr], "ic": ic[m][1]}}
            for m, (ct, tr) in market_ann.items()}


def test_ar_adopts_when_both_markets_pass() -> None:
    res = _ar({"KR": ([0.05, 0.06, 0.07], [0.07, 0.08, 0.09]),
               "US": ([0.03, 0.04, 0.05], [0.04, 0.05, 0.06])},
              {"KR": (0.09, 0.089), "US": (0.06, 0.061)})
    assert vj.judge_ar(res)[1].startswith("채택 후보")


def test_ar_one_market_only_is_user_decision() -> None:
    res = _ar({"KR": ([0.05, 0.06, 0.07], [0.07, 0.08, 0.09]),
               "US": ([0.03, 0.04, 0.05], [0.04, 0.03, 0.06])},   # 시드 2/3 → 탈락
              {"KR": (0.09, 0.089), "US": (0.06, 0.061)})
    verdict = vj.judge_ar(res)[1]
    assert "한 시장만" in verdict and "KR" in verdict


def test_ar_rejects_when_ic_falls_too_far() -> None:
    """③ ΔIC ≥ −0.01 — G2·G5 가 걸린 자리. 수익이 올라도 IC 를 깎으면 기각."""
    res = _ar({"KR": ([0.05, 0.05, 0.05], [0.09, 0.09, 0.09]),
               "US": ([0.05, 0.05, 0.05], [0.09, 0.09, 0.09])},
              {"KR": (0.09, 0.07), "US": (0.06, 0.04)})
    assert vj.judge_ar(res)[1].startswith("기각")


def _as_kr(ct: list[float], tr: list[float], *, ic=(0.09, 0.09), halves=None, mdd=(-0.10, -0.10),
           turn=(3.0, 3.0)) -> dict:  # type: ignore[no-untyped-def]
    h = halves or {}
    return {"control": {"seeds": [_m(a, mdd=mdd[0], turn=turn[0], **h.get("control", {})) for a in ct], "ic": ic[0]},
            "treat": {"seeds": [_m(a, mdd=mdd[1], turn=turn[1], **h.get("treat", {})) for a in tr], "ic": ic[1]}}


def test_as_adopts_when_all_five_hold() -> None:
    lines, verdict = vj.judge_as(_as_kr([0.05, 0.05, 0.05], [0.08, 0.08, 0.08]), us_dic=-0.001)
    assert verdict.startswith("채택 후보")
    assert "⑤" in "".join(lines)


def test_as_rejects_on_two_point_margin() -> None:
    """① 대조 + 2%p — 랭커 계열 판정 기준 틀(시드 잡음 2.1%p)."""
    assert vj.judge_as(_as_kr([0.05, 0.05, 0.05], [0.065, 0.065, 0.065]), us_dic=0.0)[1].startswith("기각")


def test_as_rejects_when_one_half_collapses() -> None:
    """③ 해 방지 — 창 전반이 대조보다 1%p 넘게 나쁘면, 후반이 아무리 좋아도 기각."""
    res = _as_kr([0.05, 0.05, 0.05], [0.09, 0.09, 0.09],
                 halves={"treat": {"h1": 0.00, "h2": 0.18}, "control": {"h1": 0.05, "h2": 0.05}})
    assert vj.judge_as(res, us_dic=0.0)[1].startswith("기각")


def test_as_rejects_when_other_market_ic_falls() -> None:
    """④ 다른 시장 ΔIC ≥ −0.005 — 국장만 보고 채택하지 않는다."""
    assert vj.judge_as(_as_kr([0.05, 0.05, 0.05], [0.09, 0.09, 0.09]), us_dic=-0.02)[1].startswith("기각")


def test_as_rejects_when_turnover_exceeds_120pct() -> None:
    res = _as_kr([0.05, 0.05, 0.05], [0.09, 0.09, 0.09], turn=(3.0, 4.0))
    assert vj.judge_as(res, us_dic=0.0)[1].startswith("기각")


def _bd(x0: float, seeds: list[float], *, x0_mdd: float = -0.12, mdd: float = -0.12) -> dict:
    return {"X0": _m(x0, mdd=x0_mdd), "switch": {"seeds": [_m(a, mdd=mdd) for a in seeds]}}


def test_bd_holds_when_too_few_bear_sessions() -> None:
    """보류 — bear 세션 10 미만이면 **판정하지 않는다**(시행 미소진). 수치가 좋아도 마찬가지다."""
    lines, verdict = vj.judge_bd(_bd(0.05, [0.30, 0.30, 0.30]), bear_sessions=9)
    assert verdict.startswith("보류")
    assert "9" in verdict
    assert not any("①" in line for line in lines)      # 기준을 아예 계산하지 않는다


def test_bd_judges_at_ten_bear_sessions() -> None:
    lines, verdict = vj.judge_bd(_bd(0.05, [0.08, 0.09, 0.10]), bear_sessions=10)
    assert verdict.startswith("채택")
    assert "①" in "".join(lines)


def test_bd_rejects_when_one_seed_loses_to_index() -> None:
    assert vj.judge_bd(_bd(0.05, [0.08, 0.04, 0.10]), bear_sessions=40)[1].startswith("기각")


def test_bd_rejects_on_deeper_drawdown() -> None:
    res = _bd(0.05, [0.09, 0.09, 0.09], x0_mdd=-0.12, mdd=-0.16)
    assert vj.judge_bd(res, bear_sessions=40)[1].startswith("기각")


# --------------------------------------------------------------------------- BD 전환 장부


def _switch_inputs() -> tuple[pd.Series, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """합성 30종목 · 10세션. 앞 5세션 bull · 뒤 5세션 bear. 점수 최하위 6종목만 수익이 있다.

    → bull(지수 대용, 30종목 전부)은 그 6종목을 들고, bear(상위 24)는 버린다. 부호가 갈리면 전환이 도는 것이다.
    """
    days = [date(2026, 7, d) for d in range(1, 11)]
    names = [f"e{i:02d}" for i in range(30)]
    score = pd.DataFrame([[float(len(names) - i) for i in range(len(names))]] * len(days), index=days, columns=names)
    caps = pd.DataFrame(1.0e9, index=days, columns=names)
    ranks = caps.rank(axis=1, ascending=False)
    ret = pd.DataFrame(0.0, index=days, columns=names)
    ret[names[24:]] = 0.10
    states = pd.Series(["bull"] * 5 + ["bear"] * 5, index=days)
    return states, score, caps, ranks, ret


def test_switch_book_switches_on_regime_change() -> None:
    states, score, caps, ranks, ret = _switch_inputs()
    daily, extra = vj.switch_book(states, score, caps, ranks, ret, cost=0.0025)
    assert extra["switches"] == 1.0 and extra["bear"] == 5.0
    bull, bear = daily.iloc[:5], daily.iloc[5:]
    assert bull.mean() > bear.mean()                    # 지수 쪽만 그 6종목을 들었다
    assert bear.iloc[0] < bear.iloc[-1]                 # 전환일에 비용을 낸다
    assert bull.iloc[1] == pytest.approx(6 / 30 * 0.10, abs=2e-3)


def test_switch_book_never_holds_filtered_names_in_bear() -> None:
    """동전주·증권 종류 필터에서 떨어진 종목(시총 NaN)은 bear 선정 후보에서 빠진다."""
    states, score, caps, ranks, ret = _switch_inputs()
    caps = caps.copy()
    caps[caps.columns[:6]] = np.nan                     # 점수 최상위 6종목이 필터 탈락
    ret = ret.copy()
    ret[ret.columns[:6]] = 1.0                          # 들었다면 수익이 폭발한다
    states = pd.Series(["bear"] * len(states), index=states.index)
    daily, _ = vj.switch_book(states, score, caps, ranks, ret, cost=0.0)
    assert daily.max() < 0.5


# --------------------------------------------------------------------------- (d) 기록 게이트


def _results() -> list[tuple[str, str, list[str]]]:
    return [(t, f"기각 — {t} 합성", [f"{t} 줄"]) for t in vj.TRIALS]


def test_no_save_writes_nothing(store, capsys) -> None:  # type: ignore[no-untyped-def]
    assert vj.record_verdicts(store, _results(), save=False) == 0
    assert "아무것도 적지 않았다" in capsys.readouterr().out
    now = datetime.now(UTC)
    assert store.get("research_trials", as_of=now, lookback=5).empty
    assert store.get("holdout_access", as_of=now, lookback=5).empty


def test_save_records_each_trial_and_the_opening(store) -> None:  # type: ignore[no-untyped-def]
    assert vj.record_verdicts(store, _results(), save=True) == len(vj.TRIALS)
    now = datetime.now(UTC)
    trials = store.get("research_trials", as_of=now, lookback=5)
    assert set(trials["entity_id"]) == set(vj.ENTITY.values())
    assert set(trials["source"]) == {"vault_judge"}
    assert int(trials["n_trials"].sum()) == len(vj.TRIALS)
    opened = store.get("holdout_access", as_of=now, lookback=5)
    assert len(opened) == 1
    assert opened.iloc[0]["reason"] == "promotion-review"
    assert opened.iloc[0]["window_start"] == vj.VAULT_START.isoformat()
    assert opened.iloc[0]["window_end"] == vj.VAULT_END.isoformat()


def test_save_carries_the_protocol_hash(store) -> None:  # type: ignore[no-untyped-def]
    """결과 행에 사전등록 문서의 해시가 같이 저장된다(self-improvement §8②)."""
    import hashlib
    vj.record_verdicts(store, _results(), save=True)
    rows = store.get("research_trials", as_of=datetime.now(UTC), lookback=5)
    for trial in vj.TRIALS:
        want = hashlib.sha256(vj.PROTOCOLS[trial].read_bytes()).hexdigest()[:16]
        row = rows[rows["entity_id"] == vj.ENTITY[trial]].iloc[0]
        assert row["protocol_hash"] == want


# --------------------------------------------------------------------------- 예측·지표


def test_predict_refuses_wrong_feature_count(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """얼린 모델과 패널의 피처 수가 다르면 멈춘다 — numpy 로 학습해 모델 안에 열 이름이 없으므로 이 대조가 유일한 방어다."""
    import hashlib

    from tools.trial_ranker_kit import fit

    rng = np.random.default_rng(0)
    x = rng.normal(size=(600, 3)).astype(np.float32)
    model = fit(x, (x[:, 0] + rng.normal(scale=0.1, size=600)).astype(np.float32), min_data=50)
    text = model.model_to_string()
    monkeypatch.setattr(vj, "MODELS", tmp_path)
    (tmp_path / "synthetic.txt").write_text(text)
    expected = {"synthetic": hashlib.sha256(text.encode()).hexdigest()[:16]}
    loaded = vj.booster("synthetic", expected)

    frame = pd.DataFrame({"entity_id": ["a", "b"], "session": [date(2026, 7, 1)] * 2,
                          "f0": [0.1, 0.2], "f1": [0.3, 0.4], "f2": [0.5, 0.6]})
    out = vj.predict(loaded, frame, ["f0", "f1", "f2"])
    assert list(out.columns) == ["entity_id", "session", "pred"] and len(out) == 2
    with pytest.raises(SystemExit) as err:
        vj.predict(loaded, frame, ["f0", "f1"])
    assert "피처 수 불일치" in str(err.value)


def test_stats_splits_the_window_in_half_by_session_count() -> None:
    """AS 기준 ③ 의 두 구간은 **세션 수 절반**으로 가른다(금고 창은 박스/급등 달력으로 못 가른다)."""
    days = pd.Index([date(2026, 7, 1) + pd.Timedelta(days=i) for i in range(10)])
    daily = pd.Series([0.01] * 5 + [-0.01] * 5, index=days)
    out = vj.stats(daily, pd.Series(0.0, index=days))
    assert out["h1"] == pytest.approx(0.01 * vj.ANN)
    assert out["h2"] == pytest.approx(-0.01 * vj.ANN)
    assert out["ann"] == pytest.approx(0.0)
    assert out["mdd"] < 0
