"""마지막 모델 회차 공통 틀 — 합성 자료로 규칙만 본다(창고·캐시를 읽지 않는다).

보는 것 다섯: ① 금고 행 거부 ② 묶음별 결측 표지 ③ **시장별** rank-gauss ④ 내부 검증이 판정 블록과 안 겹침
⑤ judge 의 ①~⑥. 합성 자료로 통과하는 테스트가 현실을 말해주지 않는다는 것은 이미 배웠다
(memory `constant-feature-eats-weight`) — 그래서 여기서 보는 것은 **수치가 아니라 규칙**이다.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tools import final_round_kit as kit

# --------------------------------------------------------------------------- ① 금고


def test_window_rejects_vault_rows() -> None:
    with pytest.raises(ValueError, match="금고"):
        kit.check_window((date(2022, 7, 1), kit.VAULT_START))
    with pytest.raises(ValueError, match="금고"):
        kit.check_window((date(2022, 7, 1), date(2026, 11, 13)))
    assert kit.check_window((date(2022, 7, 1), date(2026, 6, 30))) == (date(2022, 7, 1), date(2026, 6, 30))


def test_window_rejects_before_panel_and_reversed() -> None:
    with pytest.raises(ValueError, match="확장 패널"):
        kit.check_window((date(2021, 8, 1), date(2026, 6, 30)))
    with pytest.raises(ValueError, match="뒤집"):
        kit.check_window((date(2026, 6, 30), date(2022, 7, 1)))


def test_load_full_panel_refuses_vault_window() -> None:
    """캐시·창고를 건드리기 **전에** 멈춘다 — 인자 검사가 첫 줄이다."""
    with pytest.raises(ValueError, match="금고"):
        kit.load_full_panel(("KR",), (date(2022, 7, 1), date(2026, 7, 31)))


# --------------------------------------------------------------------------- 합성 패널


def _panel() -> pd.DataFrame:
    days = [date(2024, 1, d) for d in (2, 3, 4)]
    rows = []
    for market, names in (("KR", [f"KR:{i:03d}" for i in range(40)]), ("US", [f"US:{i:03d}" for i in range(40)])):
        for day in days:
            for i, name in enumerate(names):
                rows.append({"entity_id": name, "session": day, "market": market,
                             # 미장 원값을 국장보다 100배 크게 둔다 — 시장별 정규화가 아니면 이게 남는다.
                             "chart": float(i) * (100.0 if market == "US" else 1.0),
                             "y5": float(i % 7)})
    return pd.DataFrame(rows)


def _group(name: str, cols: tuple[str, ...], *, markets: tuple[str, ...] = ("KR", "US"),
           zero_first: tuple[str, ...] = ()) -> kit.Group:
    return kit.Group(name, cols, f"miss_{name}", markets, zero_first)


# --------------------------------------------------------------------------- ② 결측 표지


def test_missing_flag_marks_rows_without_source_data() -> None:
    panel = _panel()
    group = _group("g", ("insider_sell_60",))
    # 첫 세션·국장 10종목만 자료가 있다. 값은 0 — "사건이 없었다" 다.
    frame = pd.DataFrame({"entity_id": [f"KR:{i:03d}" for i in range(10)],
                          "session": [date(2024, 1, 2)] * 10, "insider_sell_60": [0.0] * 10})
    out = kit.attach_block(panel, frame, group)
    hit = out[(out["market"] == "KR") & (out["session"] == date(2024, 1, 2))]
    assert hit.loc[hit["entity_id"].isin(frame["entity_id"]), "miss_g"].eq(0.0).all()
    assert hit.loc[~hit["entity_id"].isin(frame["entity_id"]), "miss_g"].eq(1.0).all()
    # 자료가 있던 행의 값은 0 이고 표지는 0 — 개수 0("사건 없음")과 자료 없음이 다른 칸에 있다.
    assert hit.loc[hit["entity_id"] == "KR:000", "insider_sell_60"].eq(0.0).all()
    assert out[out["market"] == "US"]["miss_g"].eq(1.0).all()


def test_missing_flag_is_one_when_group_absent_for_market() -> None:
    panel = _panel()
    out = kit.attach_block(panel, pd.DataFrame(), _group("ba", ("vu_plan",), markets=("KR",)))
    assert out["miss_ba"].eq(1.0).all()
    assert out["vu_plan"].isna().all()


# --------------------------------------------------------------------------- ③ 시장별 rank-gauss


def test_rank_gauss_is_per_market() -> None:
    panel = _panel()
    groups = {"score": kit.Group("score", ("chart",), None, ("KR", "US"))}
    out, feats = kit.finalize(panel, groups)
    assert feats == ["chart", "is_us"]
    kr = out[(out["market"] == "KR") & (out["session"] == date(2024, 1, 2))].sort_values("entity_id")["chart"]
    us = out[(out["market"] == "US") & (out["session"] == date(2024, 1, 2))].sort_values("entity_id")["chart"]
    # 원값 크기가 100배 달라도 시장 안 순위가 같으면 같은 값이 된다.
    np.testing.assert_allclose(kr.to_numpy(), us.to_numpy(), atol=1e-5)
    assert abs(float(kr.mean())) < 1e-5 and float(kr.max()) < 3.0
    assert out.loc[out["market"] == "US", "is_us"].eq(1.0).all()
    assert out.loc[out["market"] == "KR", "is_us"].eq(0.0).all()


def test_finalize_keeps_flags_as_zero_one_and_zero_fills_counts() -> None:
    panel = kit.attach_block(_panel(), pd.DataFrame({
        "entity_id": ["KR:000"], "session": [date(2024, 1, 2)], "vu_plan": [3.0], "vu_plan_age": [10.0],
    }), _group("ba", ("vu_plan", "vu_plan_age"), markets=("KR",), zero_first=("vu_plan",)))
    groups = {"score": kit.Group("score", ("chart",), None, ("KR", "US")),
              "ba": _group("ba", ("vu_plan", "vu_plan_age"), markets=("KR",), zero_first=("vu_plan",))}
    out, feats = kit.finalize(panel, groups)
    assert set(out["miss_ba"].unique()) <= {0.0, 1.0}, "표지를 rank-gauss 하면 표지가 아니다"
    assert "miss_ba" in feats and "is_us" in feats
    # 개수(vu_plan)는 0 으로 먼저 채우므로 공시 있는 한 종목이 세션 안에서 제일 위다.
    day = out[(out["market"] == "KR") & (out["session"] == date(2024, 1, 2))]
    assert day.loc[day["entity_id"] == "KR:000", "vu_plan"].iloc[0] == day["vu_plan"].max()
    # 경과일(vu_plan_age)은 결측 → 순위 중앙(0).
    assert float(day.loc[day["entity_id"] != "KR:000", "vu_plan_age"].abs().max()) == 0.0


# --------------------------------------------------------------------------- ④ 내부 검증·블록


def test_inner_split_never_touches_judge_block() -> None:
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(400)]
    bl = kit.blocks(sessions)
    # 블록 경계는 시행 AA·AM·AN·BA 와 같다(MIN_TRAIN + 퍼지). 엠바고는 학습 끝점에서만 뺀다.
    assert bl and bl[0][0] == kit.MIN_TRAIN + kit.PURGE
    for first, last in bl[:5]:
        end = kit.train_end(sessions, first)
        # 퍼지 5 + 엠바고 5 — 학습 끝과 판정 시작 사이에 GAP 세션이 비어 있다.
        assert sessions.index(sessions[first]) - sessions.index(end) == kit.GAP + 1
        fit_days, val_days = kit.inner_split([d for d in sessions if d <= end])
        assert val_days and fit_days
        judge_days = set(sessions[first: last + 1])
        assert not set(val_days) & judge_days, "내부 검증이 판정 블록과 겹쳤다"
        assert not set(fit_days) & judge_days
        assert max(val_days) <= end and max(fit_days) < min(val_days)
        # 적합과 검증 사이에도 퍼지가 있다 — 라벨이 h5 라 붙여 두면 새는다.
        assert sessions.index(min(val_days)) - sessions.index(max(fit_days)) > kit.PURGE
        assert abs(len(val_days) / len([d for d in sessions if d <= end]) - kit.INNER_VAL_SHARE) < 0.02


def test_inner_split_accepts_frame() -> None:
    frame = pd.DataFrame({"session": [date(2024, 1, 1) + timedelta(days=i) for i in range(50)] * 2})
    fit_days, val_days = kit.inner_split(frame)
    assert len(val_days) == 10 and len(fit_days) == 35


def test_drop_groups_never_drops_scores() -> None:
    groups = {"score": ["chart"], "raw": ["raw_a"], "G1": ["g1a", "miss_g1"]}
    rng = np.random.default_rng(0)
    seen = set()
    for _ in range(50):
        dropped = kit.drop_groups(groups, rng, 0.5)
        assert "chart" not in dropped
        seen |= set(dropped)
    assert seen == {"raw_a", "g1a", "miss_g1"}, "묶음은 통째로 빠져야 한다"
    assert kit.drop_groups(groups, rng, 0.0) == []
    assert set(kit.drop_groups(groups, rng, 1.0)) == {"raw_a", "g1a", "miss_g1"}


# --------------------------------------------------------------------------- evaluate 배선


def _fake_book(market: str, days: list[date], names: list[str], *, with_fund: bool = True) -> kit.MarketBook:
    """창고를 읽지 않는 MarketBook — 두 시장 경로(국장 portfolio · 미장 M1+book)가 같은 지표 형식을 내는지만 본다."""
    rng = np.random.default_rng(7)
    ret = pd.DataFrame(rng.normal(0.0, 0.01, (len(days), len(names))), index=days, columns=names)
    bench = pd.Series(rng.normal(0.0, 0.008, len(days)), index=days)
    trad = {d: set(names) for d in days} if market == "KR" else None
    fund = None
    if market == "US" and with_fund:
        # 절반은 재무가 없다 — 미장 M1 이 다루는 바로 그 모양(시행 AT: 후보 24/24 가 재무 없는 외국 발행사였다).
        fund = pd.DataFrame([{"entity_id": n, "session": d, "fund_raw": float(rng.normal()),
                              "has_fund": i % 2 == 0} for d in days for i, n in enumerate(names)])
    return kit.MarketBook(market, ret, bench, 0.0041 if market == "KR" else 0.0025, trad, fund=fund)


def test_evaluate_gives_the_same_metric_shape_for_both_markets() -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    names = [f"S:{i:03d}" for i in range(60)]
    rng = np.random.default_rng(3)
    pred = pd.DataFrame([{"entity_id": n, "session": d, "pred": float(rng.normal()),
                          "y5": float(rng.normal())} for d in days for n in names])
    keys, y = pred[["entity_id", "session", "pred"]], pred[["entity_id", "session", "y5"]]
    out = {m: kit.evaluate(keys, _fake_book(m, days, names), y=y, control=keys) for m in ("KR", "US")}
    for market, m in out.items():
        assert {"ann", "mdd", "sharpe", "box_ann", "rally_ann", "ic", "turn", "overlap"} <= set(m), market
        assert m["overlap"] == 1.0, "자기 자신과의 상위 24 겹침은 100% 여야 한다"
        assert np.isfinite(m["ann"]) and np.isfinite(m["turn"])
    assert kit.evaluate(keys.head(0), _fake_book("KR", days, names)) == {}
    pooled = kit.pooled_metrics(out)
    assert pooled["ann"] == pytest.approx((out["KR"]["ann"] + out["US"]["ann"]) / 2)


def test_split_markets_needs_the_market_column() -> None:
    frame = pd.DataFrame({"entity_id": ["A", "B"], "session": [date(2024, 1, 1)] * 2, "pred": [1.0, 2.0]})
    with pytest.raises(ValueError, match="market"):
        kit.split_markets(frame)
    frame["market"] = ["KR", "US"]
    parts = kit.split_markets(frame)
    assert set(parts) == {"KR", "US"} and list(parts["KR"].columns) == ["entity_id", "session", "pred"]


def test_evaluate_all_runs_each_market_with_its_own_rule() -> None:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(45)]
    kr, us = [f"KR:{i:03d}" for i in range(40)], [f"US:{i:03d}" for i in range(40)]
    rng = np.random.default_rng(11)
    pred = pd.DataFrame([{"entity_id": n, "session": d, "market": m, "pred": float(rng.normal())}
                         for d in days for m, names in (("KR", kr), ("US", us)) for n in names])
    books = {"KR": _fake_book("KR", days, kr), "US": _fake_book("US", days, us)}
    per, pooled = kit.evaluate_all(pred, books, control=pred)
    assert set(per) == {"KR", "US"}
    assert all(m["overlap"] == 1.0 for m in per.values())
    assert pooled["ann"] == pytest.approx((per["KR"]["ann"] + per["US"]["ann"]) / 2)


# --------------------------------------------------------------------------- ⑤ judge ①~⑥


def _seeds(ann: float, *, box: float | None = None, rally: float | None = None,
           ic: float = 0.10, mdd: float = -0.20, turn: float = 10.0, spread: float = 0.0) -> dict[int, dict]:
    return {s: {"ann": ann + spread * s, "box_ann": box if box is not None else ann,
                "rally_ann": rally if rally is not None else ann, "ic": ic, "mdd": mdd, "turn": turn}
            for s in kit.SEEDS}


def test_judge_model_claim_passes_all_six() -> None:
    lines, verdict = kit.judge(_seeds(0.18), _seeds(0.10), _seeds(0.14))
    assert verdict == "채택 — 모델이 나아서(⑥ 통과)"
    assert "판정:" in lines[-1] and "⑥" in lines[-2]


def test_judge_without_sixth_names_c1_as_candidate() -> None:
    _lines, verdict = kit.judge(_seeds(0.14), _seeds(0.10), _seeds(0.145))
    assert verdict == "채택 후보 C1 — 정보가 늘어서(①~⑤ 통과, ⑥ 미통과)"


def test_judge_first_gate_needs_two_points() -> None:
    _lines, verdict = kit.judge(_seeds(0.119), _seeds(0.10), _seeds(0.10))
    assert verdict == "기각"


def test_judge_second_gate_counts_seed_wins() -> None:
    """평균은 통과하는데 시드 과반이 지는 경우 — 한 시드가 평균을 끌어올린 모양은 떨어진다."""
    treat = {0: {"ann": 0.60, "box_ann": 0.60, "rally_ann": 0.60, "ic": 0.10, "mdd": -0.20, "turn": 10.0},
             **{s: {"ann": 0.05, "box_ann": 0.05, "rally_ann": 0.05, "ic": 0.10, "mdd": -0.20, "turn": 10.0}
                for s in kit.SEEDS[1:]}}
    _lines, verdict = kit.judge(treat, _seeds(0.10), None)
    assert verdict == "기각"


def test_judge_third_gate_needs_both_regimes() -> None:
    _lines, verdict = kit.judge(_seeds(0.20, box=-0.05, rally=0.45), _seeds(0.10, box=0.10, rally=0.10), None)
    assert verdict == "기각", "급등장으로 박스장 손실을 덮을 수 없다"


def test_judge_fourth_gate_is_ic_guard() -> None:
    _lines, verdict = kit.judge(_seeds(0.20, ic=0.05), _seeds(0.10, ic=0.10), None)
    assert verdict == "기각", "ΔIC < 0 은 해 방지 기준에서 떨어진다"


def test_judge_fifth_gate_watches_mdd_and_turnover() -> None:
    _lines, deep = kit.judge(_seeds(0.20, mdd=-0.25), _seeds(0.10, mdd=-0.20), None)
    assert deep == "기각"
    _lines, churn = kit.judge(_seeds(0.20, turn=13.0), _seeds(0.10, turn=10.0), None)
    assert churn == "기각"
    _lines, ok = kit.judge(_seeds(0.20, mdd=-0.215, turn=11.9), _seeds(0.10, mdd=-0.20, turn=10.0), None)
    assert ok.startswith("채택 후보 C1")


def test_judge_without_c1_cannot_claim_model() -> None:
    _lines, verdict = kit.judge(_seeds(0.20), _seeds(0.10), None)
    assert verdict.startswith("채택 후보 C1")


def test_overfit_gap_records_train_minus_judge() -> None:
    gap = kit.overfit_gap(_seeds(0.50, ic=0.30), _seeds(0.10, ic=0.10))
    assert gap["gap_ann"] == pytest.approx(0.40)
    assert gap["gap_ic"] == pytest.approx(0.20)


# --------------------------------------------------------------------------- 묶음 표


def test_group_table_covers_registered_blocks_and_leaves_g10_slot() -> None:
    groups = kit.blocks_of(("KR", "US"))
    assert set(groups) == set(kit.BLOCK_ORDER)
    assert "G8" not in groups, "G8 은 FA 에서 뺐다(리드 결정 2026-09-27 — 월 조각이 없고 6차 판정이 10/4)"
    assert "G8" in kit.blocks_of(("KR", "US"), include=None), "정의는 남아 있어야 나중에 되돌릴 수 있다"
    assert "G10" not in groups, "G10 은 등록 전에 넣을지 정한다 — 자리만 비워 둔다"
    assert groups["score"].flag is None, "점수는 패널을 정의한다 — 표지가 없다"
    assert groups["ba"].markets == ("KR",)
    feats = kit.feature_names(groups)
    assert feats[-1] == "is_us"
    assert len(feats) == len(set(feats)), "이름이 겹치면 한 묶음이 다른 묶음을 덮는다"
    assert sum(1 for f in feats if f.startswith("miss_")) == len(kit.BLOCK_ORDER) - 1


# --------------------------------------------------------------------------- 대조군 C0·C1


def _gbm_panel() -> tuple[pd.DataFrame, list[str], list[date]]:
    """합성 FA 패널 — GBM 이 실제로 학습할 만큼만(블록 둘). 값은 무의미하고, 보는 것은 캐시·꼬리표 규칙이다."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(kit.MIN_TRAIN + kit.PURGE + 2 * kit.BLOCK)]
    # 종목 수는 min_data_in_leaf(2000)보다 학습 행이 넉넉해야 GBM 이 가지를 친다 — 30종목(4,500행)으로는
    # C0·C1 이 같은 나무를 내고 "대조가 대조가 아니다" 를 못 본다.
    names = [f"KR:{i:03d}" for i in range(80)]
    rng = np.random.default_rng(5)
    rows = []
    for d in days:
        for n in names:
            x = rng.normal(size=3)
            extra = float(rng.normal())
            rows.append({"entity_id": n, "session": d, "market": "KR", "is_us": 0.0,
                         **dict(zip(kit.SCORE_FEATS, list(x) + list(-x), strict=True)),
                         # FA 전용 피처(extra)에 진짜 신호를 둔다 — C1 만 쓸 수 있는 정보다.
                         "extra": extra, "y5": float(x[0] + 2.0 * extra + rng.normal(0, 0.3))})
    panel = pd.DataFrame(rows)
    return panel, [*kit.SCORE_FEATS, "is_us", "extra"], days


def test_controls_bakes_once_then_reads_the_cache(tmp_path) -> None:
    panel, feats, days = _gbm_panel()
    bl = kit.blocks(days)[:2]
    tag = kit.control_tag(panel)
    assert tag.startswith("KR-") and tag.endswith(f"{days[-1]:%Y%m%d}")
    out = kit.controls(panel, feats, days, bl, seeds=(0, 1), cache_dir=tmp_path)
    assert set(out) == {"C0", "C1"} and set(out["C0"]) == {0, 1}
    for arm, per_seed in out.items():
        for seed, frame in per_seed.items():
            assert kit.control_path(arm, seed, tag, cache_dir=tmp_path).exists()
            assert list(frame.columns) == ["entity_id", "session", "market", "pred"]
            assert frame["session"].nunique() == 2 * kit.BLOCK
    # C0(6점수)과 C1(FA)은 다른 예측이어야 한다 — 같은 값이면 대조가 대조가 아니다.
    # (합성 자료에서만 보장된다: extra 에 진짜 신호를 넣었다)
    assert not np.allclose(out["C0"][0]["pred"].to_numpy(), out["C1"][0]["pred"].to_numpy())
    # 두 번째 호출은 굽지 않는다 — 블록·세션을 빈 것으로 줘도 캐시에서 같은 것이 나온다.
    again = kit.controls(panel, [], [], [], seeds=(0, 1), cache_dir=tmp_path)
    np.testing.assert_allclose(again["C1"][1]["pred"].to_numpy(), out["C1"][1]["pred"].to_numpy())


def test_require_controls_refuses_instead_of_baking_its_own(tmp_path) -> None:
    """시행 도구는 대조군이 없으면 **조용히 자기 GBM 을 짜지 않고** rc=3 으로 멈춘다."""
    panel, feats, days = _gbm_panel()
    with pytest.raises(SystemExit) as caught:
        kit.require_controls(panel, seeds=(0,), cache_dir=tmp_path)
    assert caught.value.code == 3
    assert not list(tmp_path.glob("*.pkl")), "멈출 때 파일을 남기면 다음 판정이 그걸 읽는다"
    kit.controls(panel, feats, days, kit.blocks(days)[:2], seeds=(0,), cache_dir=tmp_path)
    got = kit.require_controls(panel, seeds=(0,), cache_dir=tmp_path)
    assert set(got) == {"C0", "C1"}


def test_control_tag_separates_markets_and_smoke(tmp_path) -> None:
    panel, _feats, _days = _gbm_panel()
    pooled = pd.concat([panel, panel.assign(market="US", is_us=1.0)], ignore_index=True)
    assert kit.control_tag(panel) != kit.control_tag(pooled), "KR 단독 예측이 합동 판정에 섞이면 미장이 사라진다"
    assert kit.control_tag(pooled).startswith("KR+US-")
    assert kit.control_tag(panel, smoke=2).endswith("-smoke2"), "배선 확인 예측이 본 판정 이름을 차지하면 안 된다"


# --------------------------------------------------------------------------- 미장 M1 합성 (리드 결정 ③)


def test_us_evaluate_refuses_without_the_fund_table() -> None:
    """미장은 M1 합성이 등록 규칙이다 — 재무 표가 없으면 **조용히 다른 규칙으로 돌지 않고** 멈춘다."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)]
    names = [f"US:{i:03d}" for i in range(40)]
    pred = pd.DataFrame([{"entity_id": n, "session": d, "pred": float(i)} for d in days for i, n in enumerate(names)])
    with pytest.raises(ValueError, match="M1"):
        kit.evaluate(pred, _fake_book("US", days, names, with_fund=False))


def test_us_m1_wide_uses_fund_where_present_and_zero_where_missing() -> None:
    """M1 = 재무가 있으면 섞고, 없으면 그 자리를 0 으로 두고 **분모에는 남긴다**(AT 채택 규칙)."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(6)]
    names = [f"US:{i:03d}" for i in range(20)]
    # 예측 순위는 모든 종목이 같게 두고 재무만 갈라 놓는다 — 그러면 차이는 전부 재무에서 온다.
    pred = pd.DataFrame([{"entity_id": n, "session": d, "pred": 0.0} for d in days for n in names])
    fund = pd.DataFrame([{"entity_id": n, "session": d, "fund_raw": 1.0 if i < 10 else -1.0,
                          "has_fund": i < 15} for d in days for i, n in enumerate(names)])
    wide = kit.us_m1_wide(pred, fund)
    last = wide.iloc[-1]
    assert last["US:000"] > last["US:010"], "좋은 재무가 나쁜 재무보다 위여야 한다"
    # 재무 없는 종목(15~19)은 fund 몫이 0 — 나쁜 재무(−1)보다는 위, 좋은 재무(+1)보다는 아래다.
    assert last["US:010"] < last["US:015"] < last["US:000"]
    assert wide.index.tolist() == days and not wide.isna().all().any()


def test_us_m1_missing_fund_rows_default_to_no_fund() -> None:
    """재무 표에 그 (종목, 세션) 이 없으면 has_fund=False 로 본다 — NaN 이 합성을 통째로 NaN 으로 만들지 않게."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(4)]
    names = [f"US:{i:03d}" for i in range(10)]
    pred = pd.DataFrame([{"entity_id": n, "session": d, "pred": float(i)} for d in days for i, n in enumerate(names)])
    fund = pd.DataFrame([{"entity_id": "US:000", "session": days[0], "fund_raw": 1.0, "has_fund": True}])
    wide = kit.us_m1_wide(pred, fund)
    assert wide.notna().to_numpy().sum() > 0
    assert wide.shape == (len(days), len(names))


def test_market_books_needs_the_panel_for_us(monkeypatch) -> None:
    """`market_books` 는 패널에서 fund_raw·has_fund 를 떼어 온다 — 패널을 안 주면 fund 가 없어 evaluate 가 멈춘다."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(5)]
    ret = pd.DataFrame(0.0, index=days, columns=["US:000"])
    monkeypatch.setattr(kit.ukit, "build", lambda store: None)
    monkeypatch.setattr(kit.ukit, "market", lambda: (ret, pd.Series(0.0, index=days)))
    monkeypatch.setattr("tools.trial_us_index_minus_losers.cost_one_way", lambda store, as_of: 0.0025)
    panel = pd.DataFrame({"entity_id": ["US:000"], "session": [days[0]], "market": ["US"],
                          "fund_raw": [1.0], "has_fund": [True]})
    assert kit.market_books(None, days, ("US",))["US"].fund is None
    assert kit.market_books(None, days, ("US",), panel=panel)["US"].fund is not None


# --------------------------------------------------------------------------- 세션 축 (BE 담당이 찾은 함정)


def test_block_rows_keeps_us_sessions_on_kr_holidays() -> None:
    """블록 **안쪽**의 국장 휴장일 — 미장 행이 빠지면 안 된다."""
    kr_days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 4)]     # 1/3 은 국장 휴장
    rows = [{"entity_id": "KR:000", "session": d, "market": "KR"} for d in kr_days]
    rows += [{"entity_id": "US:000", "session": date(2024, 1, d), "market": "US"} for d in (1, 2, 3, 4)]
    panel = pd.DataFrame(rows)
    got = kit.block_rows(panel, kr_days, 0, 2)
    # 마지막 블록이라 위쪽 한계가 없다 — 국장 세션 뒤의 미장 단독 세션까지 받는다.
    assert kit.block_span(kr_days, 0, 2) == (date(2024, 1, 1), None)
    assert len(got) == 7, "국장 3 + 미장 4 — 1/3 의 미장 행까지 들어와야 한다"
    assert date(2024, 1, 3) in set(got.loc[got["market"] == "US", "session"])
    # 인덱스를 날짜 집합으로 바꿔 isin 으로 고르면 그 행이 사라진다 — 이것이 막으려는 실수 하나다.
    naive = panel[panel["session"].isin(set(kr_days))]
    assert len(naive) == 6 and date(2024, 1, 3) not in set(naive["session"])


def _seam_panel() -> tuple[pd.DataFrame, list[date], list[date], date]:
    """이음매에 국장 휴장일을 **일부러** 놓은 패널. (panel, kr_sessions, all_days, seam_day)

    블록 경계가 (155,174)·(175,194)… 이므로, 국장 인덱스 174 와 175 **사이**에 미장만 열린 날을 끼운다.
    거기가 닫힌 구간 규칙이 구멍을 내던 자리다.
    """
    all_days = [date(2024, 1, 1) + timedelta(days=i) for i in range(220)]
    seam = all_days[175]
    kr_sessions = [d for d in all_days if d != seam]
    rows = [{"entity_id": "KR:000", "session": d, "market": "KR"} for d in kr_sessions]
    rows += [{"entity_id": "US:000", "session": d, "market": "US"} for d in all_days]
    return pd.DataFrame(rows), kr_sessions, all_days, seam


def test_seam_day_is_really_between_two_blocks() -> None:
    """배치 확인이 먼저다 — 이 날이 정말 두 블록 사이에 있는가. 아니면 아래 테스트는 아무것도 안 본다."""
    _panel_, kr, _all, seam = _seam_panel()
    bl = kit.blocks(kr)
    assert len(bl) >= 2
    (_first_a, last_a), (first_b, _last_b) = bl[0], bl[1]
    assert last_a + 1 == first_b, "블록은 국장 축에서 맞붙어 있다"
    assert kr[last_a] < seam < kr[first_b], "이음매 날짜가 두 블록 사이에 있어야 한다"
    assert seam not in set(kr), "그날은 국장 휴장일이다"


def test_blocks_cover_every_judged_session_in_both_markets_exactly_once() -> None:
    """반열림 구간이면 두 시장의 판정 세션이 **빠짐없이·한 번씩** 덮인다."""
    panel, kr, all_days, seam = _seam_panel()
    bl = kit.blocks(kr)
    parts = [kit.block_rows(panel, kr, f, last) for f, last in bl]
    covered = pd.concat(parts, ignore_index=True)
    assert len(covered) == len(covered.drop_duplicates(["entity_id", "session"])), "블록이 겹쳐 같은 행을 두 번 채점했다"
    # 이음매 날은 어느 한 블록에 정확히 한 번 든다.
    hits = [i for i, part in enumerate(parts) if seam in set(part["session"])]
    assert hits == [0], f"이음매 날이 블록 {hits} 에 들었다 — 정확히 첫 블록 하나여야 한다"
    # 판정 구간(첫 블록 시작 ~ 마지막 블록의 위쪽 한계) 안의 **모든** 미장 세션이 덮인다.
    lo = kr[bl[0][0]]
    _lo_last, hi = kit.block_span(kr, *bl[-1])
    want = {d for d in all_days if d >= lo and (hi is None or d < hi)}
    assert set(covered.loc[covered["market"] == "US", "session"]) == want
    # 블록에 못 든 국장 나머지 세션은 채점되지 않는다.
    if hi is not None:
        assert not any(d >= hi for d in covered["session"]), "버려진 국장 나머지 구간까지 채점했다"


def test_closed_span_would_drop_the_seam_day() -> None:
    """역검증 — 옛 닫힌 구간 규칙이면 이음매 날이 어느 블록에도 안 든다."""
    panel, kr, _all, seam = _seam_panel()
    old = [panel[(panel["session"] >= kr[f]) & (panel["session"] <= kr[last])] for f, last in kit.blocks(kr)]
    assert not any(seam in set(part["session"]) for part in old), "이 테스트가 재현하려는 결함이 재현되지 않았다"
    new = pd.concat([kit.block_rows(panel, kr, f, last) for f, last in kit.blocks(kr)], ignore_index=True)
    assert seam in set(new["session"]), "고친 규칙이 이음매 날을 받아야 한다"
    assert len(new) > sum(len(p) for p in old), "새 규칙이 옛 규칙보다 더 많은 행을 채점해야 한다"


def test_walk_gbm_scores_the_seam_day_too() -> None:
    """대조군 C0·C1 도 `block_rows` 를 쓴다 — 대조군만 미장 행을 잃으면 비교가 어긋난다."""
    panel, kr, _all, seam = _seam_panel()
    rng = np.random.default_rng(2)
    panel = panel.assign(is_us=(panel["market"] == "US").astype(float),
                         f0=rng.normal(size=len(panel)), y5=rng.normal(size=len(panel)))
    preds = kit.walk_gbm(panel, kr, ["f0", "is_us"], kit.blocks(kr)[:2], (0,), label="C0")
    assert seam in set(preds[0]["session"]), "대조군이 이음매 날을 채점하지 않았다"


def test_all_sessions_is_the_union_of_markets() -> None:
    panel = pd.DataFrame({"entity_id": ["a", "b"], "market": ["KR", "US"],
                          "session": [date(2024, 1, 2), date(2024, 1, 3)]})
    assert kit.all_sessions(panel) == [date(2024, 1, 2), date(2024, 1, 3)]


def test_coverage_ready_flags_a_short_kr_cache(tmp_path, monkeypatch) -> None:
    """국장 캐시가 판정 창을 못 덮으면 관문이 떨어진다 — 2026-09-27 의 28% 구멍이 이것이다."""
    short = pd.DataFrame({"session": [date(2025, 5, 21), date(2026, 6, 30)]})
    short.to_pickle(tmp_path / "calendar-KR.pkl")
    monkeypatch.setitem(kit.RAW_DIRS, "KR", tmp_path)
    ok, lines = kit.coverage_ready(markets=("KR",))
    assert not ok and "모자란다" in lines[0]
    pd.DataFrame({"session": [date(2022, 4, 1), date(2026, 6, 30)]}).to_pickle(tmp_path / "calendar-KR.pkl")
    ok, lines = kit.coverage_ready(markets=("KR",))
    assert ok and "덮는다" in lines[0]


def test_coverage_ready_reports_a_missing_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(kit.RAW_DIRS, "KR", tmp_path / "없는곳")
    ok, lines = kit.coverage_ready(markets=("KR",))
    assert not ok and "캐시가 없다" in lines[0]
    assert kit.raw_span("KR") is None


# --------------------------------------------------------------------------- 관문 셋의 종료 코드


def test_gate_exit_codes_are_distinct() -> None:
    """셸이 "무엇이 없어서 안 돌았나" 를 구분해 적어야 한다 — 셋이 같으면 로그가 거짓말을 한다."""
    codes = (kit.CONTROLS_EXIT, kit.COVERAGE_EXIT, kit.WINDOW_EXIT)
    assert len(set(codes)) == 3 and all(c > 1 for c in codes), "1 은 일반 오류와 겹친다"


def test_require_full_coverage_stops_with_its_own_code(tmp_path, monkeypatch) -> None:
    short = pd.DataFrame({"session": [date(2025, 5, 21), date(2026, 6, 30)]})
    short.to_pickle(tmp_path / "calendar-KR.pkl")
    monkeypatch.setitem(kit.RAW_DIRS, "KR", tmp_path)
    with pytest.raises(SystemExit) as caught:
        kit.require_full_coverage(markets=("KR",))
    assert caught.value.code == kit.COVERAGE_EXIT
    pd.DataFrame({"session": [date(2022, 4, 1), date(2026, 6, 30)]}).to_pickle(tmp_path / "calendar-KR.pkl")
    kit.require_full_coverage(markets=("KR",))          # 덮으면 조용히 지나간다


def test_require_full_window_counts_sessions_before_the_first_judged_day() -> None:
    """0 으로 채워진 창은 아무 경고도 내지 않는다(rank-gauss 뒤 0 은 "순위 중앙" 처럼 보인다) — 그래서 rc 로 낸다."""
    axis = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
    kit.require_full_window(axis, axis[60], 60)          # 앞에 60세션 — 딱 찬다
    with pytest.raises(SystemExit) as caught:
        kit.require_full_window(axis, axis[59], 60)      # 59세션 — 한 칸 모자라다
    assert caught.value.code == kit.WINDOW_EXIT


def test_require_full_window_uses_the_axis_it_is_given() -> None:
    """축이 국장이면 미장 단독 세션이 안 세어진다 — 창 검사는 도구가 쓰는 축(보통 all_sessions)으로 해야 한다."""
    all_days = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
    kr_only = [d for i, d in enumerate(all_days) if i % 5]      # 5일마다 국장 휴장
    first_judged = all_days[60]
    kit.require_full_window(all_days, first_judged, 60)         # 합집합 축이면 찬다
    with pytest.raises(SystemExit):
        kit.require_full_window(kr_only, first_judged, 60)      # 국장 축이면 모자라다


def test_first_judged_offset_is_min_train_plus_purge_not_gap() -> None:
    """채점 첫 세션은 `MIN_TRAIN + PURGE`(155) 다 — **GAP(160)이 아니다.**

    GAP 은 `train_end` 로 학습 끝점만 당기고 블록 시작은 밀지 않는다. 2026-09-27 에 이 둘을 헷갈려 155 를
    160 으로 적은 자리가 셋 있었다(문서·docstring·시행 쪽 상수 테스트). 워밍업이 차는지 따질 때 쓰는 수라
    틀리면 "창이 찬다" 는 판단의 근거가 엉뚱한 수가 된다.
    """
    axis = [date(2022, 7, 1) + timedelta(days=i) for i in range(400)]
    bl = kit.blocks(axis)
    assert bl[0][0] == kit.FIRST_JUDGED_OFFSET == kit.MIN_TRAIN + kit.PURGE == 155
    assert kit.FIRST_JUDGED_OFFSET != kit.MIN_TRAIN + kit.GAP, "GAP 으로 블록 시작을 재면 5세션 틀린다"
    # 학습 끝점만 GAP 만큼 앞이다.
    assert axis.index(kit.train_end(axis, bl[0][0])) == bl[0][0] - kit.GAP - 1


def test_current_spec_leaves_the_transformer_window_full() -> None:
    """지금 규격에서 60세션 창이 채점 첫 세션에 이미 찬다 — BE 의 판단을 상수로 못 박는다."""
    axis = [date(2022, 7, 1) + timedelta(days=i) for i in range(400)]
    bl = kit.blocks(axis)
    kit.require_full_window(axis, axis[bl[0][0]], 60)
    assert kit.FIRST_JUDGED_OFFSET >= 60, "이 부등식이 깨지면 워밍업이 필요 없다는 판단이 무너진다"


# --------------------------------------------------------------------------- 패널 로드 메모리(9/29) — 값은 그대로
#
# 조립이 최대 RSS 4.8GB 를 찍어 BF 가 OOM 으로 죽은 뒤 로드 경로의 사본을 줄였다. BE·BF·BG·D1·대조군이 모두 이
# 패널을 받으므로 **표가 한 비트도 달라지면 안 된다** — 옛 경로(pd.read_parquet → to_datetime().dt.date →
# pd.concat → isna 채우기)를 여기 얼려 두고 정확 비교한다.


def _part(market: str, n_days: int, n_names: int, seed: int, *, extra: bool) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = [date(2024, 1, 2) + timedelta(days=i) for i in range(n_days)]
    n = n_days * n_names
    frame = pd.DataFrame({
        "entity_id": [f"{market}:A:{i:04d}" for _ in days for i in range(n_names)],
        "session": [d for d in days for _ in range(n_names)],
        "s1": rng.normal(size=n).astype(np.float32),
        "y5": rng.normal(size=n).astype(np.float32),
        "r1": rng.normal(size=n).astype(np.float32),
        "miss_raw": (rng.random(n) < 0.3).astype(np.float32),
        "s2": rng.normal(size=n).astype(np.float32),
        "market": market,
        "is_us": np.float32(market == "US"),
    })
    frame.loc[rng.random(n) < 0.05, "s2"] = np.nan           # 조각 사이 빈 칸 → 등록 규칙으로 메운다
    frame.loc[rng.random(n) < 0.05, "y5"] = np.nan
    if extra:                                                # 미장에만 있는 열 — concat 이 열을 맞추는 자리
        frame["has_fund"] = rng.random(n) < 0.5
        frame["fund_raw"] = rng.normal(size=n)
        frame["r2"] = rng.normal(size=n).astype(np.float32)  # 한 시장에만 있는 float32 피처
        frame = frame[["y5", *[c for c in frame.columns if c != "y5"]]]   # 열 순서도 다르게
    return frame


def _old_load(paths: list) -> pd.DataFrame:
    frames = []
    for path in paths:
        cached = pd.read_parquet(path)
        cached["session"] = pd.to_datetime(cached["session"]).dt.date
        frames.append(cached)
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


def test_lean_concat_is_exactly_pd_concat() -> None:
    kr = _part("KR", 6, 7, 1, extra=False)
    us = _part("US", 5, 9, 2, extra=True)
    us["s1"] = us["s1"].astype(np.float64)                   # 한 조각에서만 float64 — 빠른 길에서 빠져야 한다
    want = pd.concat([kr, us], ignore_index=True)
    got = kit.lean_concat([kr.copy(), us.copy()])
    pd.testing.assert_frame_equal(got, want, check_exact=True)
    assert list(got.columns) == list(want.columns)
    assert got["has_fund"].dtype == want["has_fund"].dtype == object   # 옛 경로의 dtype 그대로(fillna 경고까지)
    single = kit.lean_concat([kr.copy()])
    pd.testing.assert_frame_equal(single, kr, check_exact=True)


def test_as_dates_matches_to_datetime_dt_date() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2)]
    for col in (pd.Series(days, dtype=object, name="session"),
                pd.Series(pd.to_datetime(days), name="session"),
                pd.Series([days[0], None, days[1]], dtype=object, name="session")):
        want = pd.to_datetime(col).dt.date
        got = kit._as_dates(col)
        pd.testing.assert_series_equal(got, want, check_exact=True)
    got = kit._as_dates(pd.Series(days, dtype=object))
    assert got.iloc[0] is got.iloc[2]                        # 같은 날은 같은 객체


def test_read_part_matches_read_parquet(tmp_path) -> None:
    frame = _part("US", 4, 5, 3, extra=True)
    path = tmp_path / "part.parquet"
    frame.to_parquet(path, index=False)
    pd.testing.assert_frame_equal(kit._read_part(path), pd.read_parquet(path), check_exact=True)


def test_share_objects_keeps_values(tmp_path) -> None:
    frame = _part("KR", 3, 4, 4, extra=False)
    frame["session"] = [date(d.year, d.month, d.day) for d in frame["session"]]   # 행마다 새 객체(pickle 을 읽은 꼴)
    want = frame.copy()
    got = kit.share_objects(frame)
    pd.testing.assert_frame_equal(got, want, check_exact=True)
    assert got["session"].iloc[0] is got["session"].iloc[1]


def test_load_full_panel_table_is_unchanged(tmp_path, monkeypatch) -> None:
    """옛 로드 경로와 **같은 표**(열 순서·dtype·값·인덱스) — 모든 시행 도구의 입력이 여기서 나온다."""
    window = (date(2022, 7, 1), date(2026, 6, 30))
    groups = {"score": kit.Group("score", ("s1", "s2"), None, ("KR", "US")),
              "raw": kit.Group("raw", ("r1", "r2", "r3"), "miss_raw", ("KR", "US"))}
    monkeypatch.setattr(kit, "blocks_of", lambda markets, include=None: groups)
    parts = {"KR": _part("KR", 6, 7, 5, extra=False), "US": _part("US", 5, 9, 6, extra=True)}
    tag = f"KR+US-{window[0]:%Y%m%d}-{window[1]:%Y%m%d}"
    paths = []
    for market, frame in parts.items():
        path = tmp_path / f"panel-{market}-{tag}.parquet"
        frame.to_parquet(path, index=False)
        paths.append(path)

    panel, feats, _bundles, sessions = kit.load_full_panel(("KR", "US"), window, cache_dir=tmp_path, store=object())

    want = _old_load(paths)                                  # 옛 경로 그대로 — 빈 열 채우기까지
    for group in groups.values():
        for c in group.cols:
            if c not in want.columns:
                want[c] = np.float32(0.0)
        if group.flag and group.flag not in want.columns:
            want[group.flag] = np.float32(1.0)
    missing = want[feats].isna().sum()
    for c in missing[missing > 0].index:
        want[c] = want[c].fillna(1.0 if c.startswith("miss_") else 0.0).astype(np.float32)
    pd.testing.assert_frame_equal(panel, want, check_exact=True)
    assert list(panel.columns) == list(want.columns)
    assert panel[feats].to_numpy(np.float32).tobytes() == want[feats].to_numpy(np.float32).tobytes()
    assert sessions == sorted(want.loc[want["market"] == "KR", "session"].unique())
    assert "r3" in panel.columns and (panel["r3"] == 0.0).all()   # 조각에 없던 열은 0(순위 중앙)
