"""시행 BE(세트 트랜스포머) 단위 테스트 — 합성 자료만. 창고·실자료는 건드리지 않는다.

지키는 것 일곱:
 ① 모양 — 큐브·배치·예측 열(`market` 열 포함).
 ② 결정론 — 같은 시드는 같은 예측.
 ③ 미래 누설 없음 — 창은 과거만, 학습은 퍼지+엠바고 앞 세션만.
 ④ 조기 종료는 내부 검증만 본다 — 판정 블록 세션이 학습 로그에 한 번도 안 나온다.
 ⑤ 드롭아웃 — 묶음 단위 피처 드롭아웃이 열을 0 으로, 결측 표지를 1 로 만든다.
 ⑥ 이음매 — 블록 경계의 미장 단독 세션이 새지 않는다(옛 닫힌 구간 규칙이면 빠지는지 **역검증**).
 ⑦ 관문 — 창·종료 코드 규칙이 kit 과 하나다.

**규칙은 베끼지 않는다.** `inner_split`·`block_span`·`require_full_window`·`drop_groups` 는 진짜 kit 에서 가져온다 —
대역을 손으로 써 두면 kit 이 바뀌는 날 그 대역만 옛 규칙으로 남아 테스트가 거짓으로 통과한다.
자료만 합성이다(창고는 읽지 않는다).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from tools import final_round_kit as _kit
from tools import trial_final_transformer as be

torch = pytest.importorskip("torch")
# 테스트는 **스레드 2개**만 쓴다 — 기본값(코어 수)이면 스위트가 머신을 독차지해 같이 도는 연구 작업을 굶긴다.
torch.set_num_threads(2)


#: 묶음 드롭아웃은 진짜 kit 것을 쓴다 — 베껴 두면 kit 이 바뀌는 날 이 대역만 옛 규칙으로 남는다.
_drop_groups = _kit.drop_groups


@pytest.fixture(scope="module")
def small():
    panel, feats, groups, sessions = be.synthetic(n_sessions=90, n_entities=160, seed=7)
    panel = panel.assign(is_us=(panel["market"] == "US").astype(np.float32))
    entities = sorted(panel["entity_id"].unique())
    columns, index_of = be._feature_layout(list(feats))
    aug = be.Aug(_drop_groups, dict(groups), index_of)
    cube = be.build_cube(panel, columns, sessions, entities)
    observed = be.observed_mask(panel, sessions, entities)
    market_id = panel.groupby("entity_id")["is_us"].max().reindex(entities).to_numpy(np.int8)
    targets = {}
    row_of = {s: i for i, s in enumerate(sessions)}
    col_of = {e: i for i, e in enumerate(entities)}
    for s, part in panel.groupby("session"):
        column = np.full(len(entities), np.nan, np.float32)
        column[part["entity_id"].map(col_of).to_numpy()] = part["y5"].to_numpy(np.float32)
        targets[row_of[s]] = column
    return dict(panel=panel, feats=feats, groups=groups, sessions=sessions, entities=entities,
                columns=columns, index_of=index_of, aug=aug, cube=cube,
                observed=observed, market_id=market_id, targets=targets)


# ① 모양 -----------------------------------------------------------------------

def test_cube_shape_and_dtype(small):
    cube = small["cube"]
    assert cube.shape == (len(small["sessions"]), len(small["entities"]), len(small["columns"]))
    assert cube.dtype == np.float16
    assert np.isfinite(cube.astype(np.float32)).all(), "큐브에 NaN/inf 가 남으면 손실이 조용히 NaN 이 된다"


def test_is_us_column_present(small):
    assert "is_us" in small["columns"], "합동 패널이면 시장 표지가 입력에 있어야 한다"


def test_window_batch_shape(small):
    rows = np.arange(30)
    x = be.window_batch(small["cube"], 80, rows)
    assert x.shape == (30, be.N_STEPS, len(small["columns"]))
    assert x.dtype == np.float32


def test_time_grid_covers_window_and_is_past_only():
    assert max(be.TIME_OFFSETS) < be.WINDOW
    assert min(be.TIME_OFFSETS) == 0
    assert all(o >= 0 for o in be.TIME_OFFSETS), "음수 오프셋 = 미래를 보는 것"
    assert len(set(be.TIME_OFFSETS)) == len(be.TIME_OFFSETS)


def test_predict_columns(small):
    model = be.make_model(0, small["cube"].shape[2])
    out = be.predict_days(model, small["cube"], small["observed"], small["market_id"],
                          small["entities"], small["sessions"], [70, 71])
    assert list(out.columns) == ["entity_id", "session", "market", "pred"]
    assert set(out["session"]) == {small["sessions"][70], small["sessions"][71]}
    assert set(out["market"]) == {"KR", "US"}, "kit.split_markets 가 market 열을 요구한다"
    assert out["pred"].notna().all()


def test_model_is_small(small):
    model = be.make_model(0, small["cube"].shape[2])
    n = sum(p.numel() for p in model.parameters())
    assert 10_000 <= n <= 120_000, f"작은 모델이어야 한다(공통 틀 4) — {n:,}"


def test_rank_average_is_mean_of_percentiles():
    a = pd.DataFrame({"entity_id": ["A", "B", "C"], "session": [date(2024, 1, 2)] * 3,
                      "market": "KR", "pred": [3.0, 2.0, 1.0]})
    b = a.assign(pred=[1.0, 2.0, 3.0])
    out = be.rank_average(a, b).set_index("entity_id")["pred"]
    # 세션 안 백분위는 1/3·2/3·1 — 두 방향을 섞으면 셋이 모두 2/3 로 모인다(반대 순위의 평균).
    assert out.loc["B"] == pytest.approx(2 / 3)
    assert out.loc["A"] == pytest.approx(out.loc["C"]) == pytest.approx(2 / 3)


def test_rank_average_ranks_within_market_not_across(small):
    """백분위는 (세션, 시장) 안에서 — 두 시장을 한 줄로 세우면 시장 수준 차이가 순위에 섞인다."""
    day = date(2024, 1, 2)
    frame = pd.DataFrame({"entity_id": ["K1", "K2", "U1", "U2"], "session": day,
                          "market": ["KR", "KR", "US", "US"], "pred": [1.0, 2.0, 100.0, 200.0]})
    out = be.rank_average(frame).set_index("entity_id")["pred"]
    assert out.loc["K1"] == pytest.approx(out.loc["U1"]), "미장 원값이 커도 시장 안 순위는 같아야 한다"
    assert out.loc["K2"] == pytest.approx(out.loc["U2"])
    assert list(be.rank_average(frame).columns) == ["entity_id", "session", "market", "pred"]


# ② 결정론 ---------------------------------------------------------------------

def test_same_seed_same_model(small):
    def flat(seed):  # pos 는 0 으로 시작하므로 전체를 이어 붙여 본다
        return np.concatenate([p.detach().numpy().ravel().copy()
                               for p in be.make_model(seed, small["cube"].shape[2]).parameters()])

    assert np.array_equal(flat(3), flat(3))
    assert not np.array_equal(flat(3), flat(4)), "시드가 다르면 초기값이 달라야 한다(시드 앙상블의 전제)"


def test_prediction_is_deterministic(small):
    model = be.make_model(1, small["cube"].shape[2])
    a = be.predict_days(model, small["cube"], small["observed"], small["market_id"],
                        small["entities"], small["sessions"], [75])
    b = be.predict_days(model, small["cube"], small["observed"], small["market_id"],
                        small["entities"], small["sessions"], [75])
    pd.testing.assert_frame_equal(a, b)
    assert model.training is False, "예측 경로는 eval 모드여야 한다 — dropout 이 켜지면 같은 입력이 다른 점수를 낸다"


def test_training_is_deterministic(small):
    out = []
    for _ in range(2):
        model, _ = be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                                  fit_days=list(range(60, 72)), val_days=list(range(72, 78)), seed=5,
                                  aug=small["aug"], max_epochs=1)
        out.append(be.predict_days(model, small["cube"], small["observed"], small["market_id"],
                                   small["entities"], small["sessions"], [80])["pred"].to_numpy())
    np.testing.assert_allclose(out[0], out[1], rtol=0, atol=0)


# ③ 미래 누설 없음 --------------------------------------------------------------

def test_window_batch_reads_only_past(small):
    """뒤쪽 세션을 전부 망가뜨려도 배치가 바뀌지 않아야 한다."""
    cube = small["cube"].copy()
    day = 60
    before = be.window_batch(cube, day, np.arange(20))
    cube[day + 1:] = np.float16(999.0)
    after = be.window_batch(cube, day, np.arange(20))
    np.testing.assert_array_equal(before, after)


def test_window_batch_uses_the_day_itself(small):
    cube = small["cube"].copy()
    cube[60] = np.float16(7.0)
    x = be.window_batch(cube, 60, np.arange(5))
    assert (x[:, 0, :] == 7.0).all(), "오프셋 0 = 그 날. 공통 틀은 t 를 포함하는 창이다"


def test_window_batch_rejects_out_of_range(small):
    with pytest.raises(IndexError):
        be.window_batch(small["cube"], small["cube"].shape[0], np.arange(3))


def test_run_seed_never_trains_on_judge_sessions(small):
    """④ 와 겹치는 핵심 — 판정 블록의 세션은 학습·조기 종료 어느 쪽에도 나오면 안 된다."""
    blocks = [(62, 71), (72, 81)]
    purge = 5
    pred, train_pred, logs, _ = be.run_seed(
        _Kit, small["cube"], small["observed"], small["market_id"], small["entities"],
        small["sessions"], small["sessions"], blocks, small["targets"], small["aug"],
        seed=0, purge=purge, max_epochs=1)
    judge_days = {d for first, last in blocks for d in range(first, last + 1)}
    seen = {d for log in logs for d in log.seen_days} | {d for log in logs for d in log.val_days}
    assert not (seen & judge_days), f"판정 세션이 학습에 새어 들어왔다: {sorted(seen & judge_days)}"
    assert max(seen) <= blocks[0][0] - purge - 1, "퍼지 구간까지 학습에 쓰였다"
    assert len(pred) > 0
    # 학습창 표본은 과적합 격차 기록용 — 판정 세션이 여기에도 들어오면 안 된다
    assert set(train_pred["session"]).isdisjoint({small["sessions"][d] for d in judge_days})


class _Kit:
    """실제 kit 과 **같은 시그니처**의 최소 대역. 경계·관문 규칙은 진짜 kit 에서 가져온다.

    베껴 쓰면 진짜 kit 이 바뀌는 날 이 대역만 옛 규칙으로 남아 테스트가 거짓으로 통과한다.
    """

    calls: ClassVar[list[tuple]] = []

    # `inner_split` 도 진짜 kit 것을 쓴다 — 단순화해 두면 **적합·검증 사이 퍼지 5** 를 안 보게 되고,
    # 라벨이 h5 라 그 퍼지가 없으면 내부 검증이 새어 조기 종료가 낙관적으로 걸린다(kit 담당 지적).
    inner_split = staticmethod(_kit.inner_split)
    block_span = staticmethod(_kit.block_span)
    # 진행 기록도 진짜 kit 것 — store=None 이면 아무것도 안 한다(2026-09-28, 진행 칸 배선 뒤 빠져 있던 것).
    record_progress = staticmethod(_kit.record_progress)

    @classmethod
    def require_full_window(cls, axis, first_judged, length, *, label="창"):
        cls.calls.append((len(axis), first_judged, length))
        return _kit.require_full_window(axis, first_judged, length, label=label)


def test_block_indices_is_half_open_and_covers_the_seam(small):
    """이음매 — 블록 끝과 다음 블록 시작 **사이**의 미장 단독 세션도 그 블록이 받아야 한다.

    닫힌 구간 `[sessions[first], sessions[last]]` 이던 첫 판은 그날을 어느 블록에도 안 넣었다
    (BF 담당이 찾았다). 여기서는 국장 축에서 세션 하나를 빼 '미장만 열린 날' 로 만들어 본다.
    """
    cube_sessions = small["sessions"]
    seam = cube_sessions[80]                      # 블록 끝(79) 과 다음 블록 시작(81) 사이
    axis = [s for s in cube_sessions if s != seam]
    first, last = axis.index(cube_sessions[60]), axis.index(cube_sessions[79])
    got = be.block_indices(_Kit, cube_sessions, axis, first, last)
    assert 80 in got, "이음매의 미장 단독 세션이 새 나갔다"
    # 역검증 — 옛 닫힌 구간 규칙이면 빠진다. 빠지지 않으면 이 테스트가 결함을 못 잡는다.
    closed = [i for i, d in enumerate(cube_sessions) if axis[first] <= d <= axis[last]]
    assert 80 not in closed, "역검증 실패 — 옛 규칙으로도 안 빠지면 이 테스트는 아무 것도 지키지 않는다"


def test_block_indices_assigns_every_session_exactly_once(small):
    """연속한 블록들이 세션을 **빠짐없이 한 번씩** 덮는다 — 겹치지도, 새지도 않는다."""
    cube_sessions = small["sessions"]
    axis = [s for s in cube_sessions if s != cube_sessions[80]]
    blocks = [(20, 39), (40, 59), (60, 79)]
    seen: list[int] = []
    for first, last in blocks:
        seen += be.block_indices(_Kit, cube_sessions, axis, first, last)
    assert len(seen) == len(set(seen)), "같은 세션이 두 블록에 들었다"
    lo = cube_sessions.index(axis[20])
    hi = cube_sessions.index(axis[80])            # 마지막 블록의 배타 상한
    assert set(seen) == set(range(lo, hi)), "구간 안에 빠진 세션이 있다"


def test_block_indices_last_block_has_no_upper_bound(small):
    """마지막 블록은 위쪽 한계가 없다 — 마지막 국장 세션 뒤의 미장 단독 세션까지 받는다."""
    cube_sessions = small["sessions"]
    axis = cube_sessions[:-3]                     # 뒤 3세션은 '미장만 열린 날'
    first = len(axis) - 20
    got = be.block_indices(_Kit, cube_sessions, axis, first, len(axis) - 1)
    assert max(got) == len(cube_sessions) - 1, "마지막 블록이 꼬리 세션을 버렸다"


def test_run_seed_scores_sessions_the_block_axis_does_not_have(small):
    """축이 둘이다 — 큐브 축(국장 ∪ 미장)에만 있는 세션도 그 블록 구간이면 채점한다.

    국장 세션 축으로만 찍으면 국장이 쉬는 날의 미장 세션이 조용히 빠진다.
    """
    cube_sessions = small["sessions"]
    hidden = cube_sessions[66]                               # 블록 축에서 빼 둘 '미장만 열린 날'
    axis = [s for s in cube_sessions if s != hidden]
    first = axis.index(cube_sessions[62])
    last = axis.index(cube_sessions[71])
    pred, _train, _logs, _ = be.run_seed(
        _Kit, small["cube"], small["observed"], small["market_id"], small["entities"],
        cube_sessions, axis, [(first, last)], small["targets"], small["aug"],
        seed=0, purge=5, max_epochs=1)
    assert hidden in set(pred["session"]), "블록 축에 없는 세션이 채점에서 빠졌다 — 미장이 새 난다"


def test_run_seed_calls_the_kit_window_gate(small):
    """창 관문은 **kit 한 곳**에 있고 `run_seed` 가 시드마다 부른다 — 함수만 있고 안 부르면 못 지킨다."""
    _Kit.calls.clear()
    blocks = [(62, 71)]
    be.run_seed(_Kit, small["cube"], small["observed"], small["market_id"], small["entities"],
                small["sessions"], small["sessions"], blocks, small["targets"],
                small["aug"], seed=0, purge=5, max_epochs=1)
    assert _Kit.calls, "kit.require_full_window 을 부르지 않았다"
    axis_len, first_judged, length = _Kit.calls[0]
    assert first_judged == small["sessions"][62], "채점 첫 세션을 잘못 넘겼다"
    assert length == be.WINDOW, "창 길이는 WINDOW(60)여야 한다"
    assert axis_len == len(small["sessions"]), "축은 큐브 축(국장 ∪ 미장)이어야 한다"


def test_run_seed_refuses_a_truncated_judge_window(small):
    """창이 모자라면 학습 전에 rc=5 로 멈춘다 — 0 으로 채운 창으로 9시간을 태우지 않는다."""
    _Kit.calls.clear()
    with pytest.raises(SystemExit) as caught:
        be.run_seed(_Kit, small["cube"], small["observed"], small["market_id"], small["entities"],
                    small["sessions"], small["sessions"], [(10, 29)], small["targets"],
                    small["aug"], seed=0, max_epochs=1)
    assert caught.value.code == _kit.WINDOW_EXIT == 5


def test_exit_codes_are_distinct_and_not_one():
    """3 대조군 · 4 굽기 · 5 창. 서로 달라야 러너의 case 문이 이유를 가릴 수 있고, 1(일반 오류)과도 달라야 한다."""
    codes = (_kit.CONTROLS_EXIT, _kit.COVERAGE_EXIT, _kit.WINDOW_EXIT)
    assert codes == (3, 4, 5)
    assert len(set(codes)) == 3
    assert 1 not in codes


def test_sixty_session_window_is_already_full_at_the_first_judged_session():
    """워밍업이 필요 없다는 판단을 **맞는 양으로** 못 박는다.

    첫 판은 `MIN_TRAIN + GAP`(160)을 봤다 — 통과하지만 엉뚱한 양이다. `blocks()` 는
    `MIN_TRAIN + PURGE`(155)에서 시작하고 **엠바고는 블록을 늦추지 않는다**(학습 끝점만 당긴다).
    그래서 `PURGE` 를 키우면 실제 워밍업이 커지는데 옛 테스트는 반응하지 않고, `EMBARGO` 를 키우면
    워밍업과 무관한데 테스트만 반응했다. 지켜야 할 양은 `FIRST_JUDGED_OFFSET` 하나다.
    """
    assert _kit.FIRST_JUDGED_OFFSET >= be.WINDOW, (
        f"채점 첫 세션 앞 {_kit.FIRST_JUDGED_OFFSET}세션이 창 {be.WINDOW} 보다 짧다 — 워밍업을 따로 받아야 한다")


def test_first_judged_offset_is_what_blocks_actually_does():
    """상수가 `blocks()` 의 실제 동작과 같은지 — 갈라지면 위 테스트가 엉뚱한 것을 지킨다."""
    axis = [date(2022, 1, 1) + timedelta(days=i) for i in range(400)]
    assert _kit.blocks(axis)[0][0] == _kit.FIRST_JUDGED_OFFSET == _kit.MIN_TRAIN + _kit.PURGE
    assert _kit.FIRST_JUDGED_OFFSET != _kit.MIN_TRAIN + _kit.GAP, "엠바고는 블록 시작을 밀지 않는다"
    # 엠바고는 학습 끝점에만 나타난다 — 채점 첫 세션에서 GAP+1 앞.
    first = _kit.blocks(axis)[0][0]
    assert axis.index(_kit.train_end(axis, first)) == first - _kit.GAP - 1


# ④ 조기 종료는 내부 검증만 본다 ------------------------------------------------

def test_early_stopping_watches_only_inner_validation(small):
    log = be.TrainLog()
    fit, val = list(range(50, 70)), list(range(70, 76))
    be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                   fit_days=fit, val_days=val, seed=2, aug=small["aug"], log=log, max_epochs=2)
    assert log.val_days == set(val), f"검증에 val 밖 세션이 들어왔다: {sorted(log.val_days - set(val))}"
    assert log.seen_days <= set(fit), "그래디언트가 fit 밖 세션을 봤다"
    assert len(log.val_scores) == log.epochs, "에포크마다 내부 검증 점수를 남겨야 한다(격차 기록)"


def test_early_stopping_flag_means_it_really_stopped_early(small):
    """`stopped_early` 는 **예산을 남기고 멈췄을 때만** True.

    처음 판은 마지막 허용 에포크에서 걸린 break 도 True 로 적었다 — 그러면 기록 줄의 "조기 종료 N회" 가
    예산을 다 쓴 회차까지 세어 부풀려진다. 검증 점수가 계속 오르지 않게 max_epochs 를 넉넉히 줘서 본다.
    """
    log = be.TrainLog()
    be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                   fit_days=list(range(40, 46)), val_days=list(range(46, 50)), seed=11,
                   aug=small["aug"], log=log, max_epochs=8)
    assert log.epochs <= 8
    assert len(log.val_scores) == log.epochs
    if log.stopped_early:
        assert log.epochs < 8, "예산을 다 쓴 회차를 조기 종료로 적으면 안 된다"


def test_early_stopping_flag_is_false_when_budget_is_exhausted(small):
    """max_epochs=1 이면 멈출 여지가 없다 — 그런데도 True 가 뜨면 그 지표는 못 믿는다."""
    log = be.TrainLog()
    be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                   fit_days=list(range(40, 46)), val_days=list(range(46, 50)), seed=11,
                   aug=small["aug"], log=log, max_epochs=1)
    assert log.epochs == 1
    assert log.stopped_early is False


def test_early_stopping_keeps_the_best_epoch_weights(small):
    """멈출 때 최고 검증 시점의 가중치로 되돌린다 — 마지막(나빠진) 에포크를 들고 나오면 안 된다."""
    log = be.TrainLog()
    model, _ = be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                              fit_days=list(range(30, 50)), val_days=list(range(50, 56)), seed=3,
                              aug=small["aug"], log=log, max_epochs=4)
    best_at = int(np.argmax(log.val_scores))
    got = be._val_score(model, small["cube"], small["observed"], small["market_id"],
                        small["targets"], list(range(50, 56)), be.TrainLog())
    assert got == pytest.approx(log.val_scores[best_at], abs=1e-6), (
        f"들고 나온 가중치의 검증 점수 {got:+.6f} 가 최고 시점 {log.val_scores[best_at]:+.6f} 와 다르다")


def test_step_budget_caps_sessions_per_epoch(small):
    """에포크 = 고정 스텝 예산. 학습 세션이 늘어도 한 에포크 비용은 같아야 한다(확장창 비용 폭발 방지)."""
    log = be.TrainLog()
    fit, val = list(range(20, 60)), list(range(60, 66))
    be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                   fit_days=fit, val_days=val, seed=1, aug=small["aug"], log=log,
                   max_epochs=1, steps_per_epoch=7)
    assert len(log.seen_days) <= 7, f"예산 7 인데 {len(log.seen_days)} 세션을 봤다"
    assert log.seen_days <= set(fit)


def test_warm_start_continues_from_previous_weights(small):
    """웜스타트는 앞 모델에서 이어 학습한다 — 주어진 가중치가 출발점이어야 한다."""
    donor = be.make_model(9, small["cube"].shape[2])
    state = {k: v.detach().clone() for k, v in donor.state_dict().items()}
    model, _ = be.train_model(small["cube"], small["observed"], small["market_id"], small["targets"],
                              fit_days=[], val_days=list(range(60, 64)), seed=0,
                              aug=small["aug"], max_epochs=1, init_state=state)
    # 학습 스텝이 0 이므로(fit 이 비었다) 가중치가 그대로 남아야 한다
    for k, v in model.state_dict().items():
        np.testing.assert_allclose(v.numpy(), state[k].numpy(), atol=0)
    fresh = be.make_model(0, small["cube"].shape[2]).state_dict()
    assert not np.allclose(model.state_dict()["inp.weight"].numpy(), fresh["inp.weight"].numpy())


def test_hyperparameters_are_module_constants():
    """공통 틀 2 — 하이퍼는 등록 때 고정. 판정 창을 인자로 받아 고르는 경로가 없어야 한다."""
    assert (be.D_MODEL, be.TIME_LAYERS, be.SET_LAYERS) == (32, 2, 2)
    assert pytest.approx(0.2) == be.DROPOUT
    assert pytest.approx(1e-4) == be.WEIGHT_DECAY
    assert len(be.SEEDS) == 5
    assert be.RETRAIN_EVERY == 5
    assert be.STEPS_PER_EPOCH > 0 and be.PATIENCE == 2
    assert be.GROUP_DROP_P > 0 and be.INPUT_NOISE > 0 and be.ENTITY_SUBSAMPLE < 1.0


# ⑤ 드롭아웃 -------------------------------------------------------------------

def test_group_dropout_zeros_group_and_raises_missing_flag():
    """묶음을 떨어뜨리면 값 열은 0, 결측 표지는 1 — 실제 결측과 같은 모양이어야 한다."""
    feats = ["a1", "a2", "b1", "miss_gA"]
    groups = {"gA": ["a1", "a2", "miss_gA"], "gB": ["b1"]}   # kit 은 표지도 묶음 열에 넣어 준다
    columns, index_of = be._feature_layout(feats)
    zero, flags = be.drop_indices(_drop_groups, groups, index_of, np.random.default_rng(0), p=1.0)
    assert set(zero) == {index_of["a1"], index_of["a2"], index_of["b1"]}
    assert set(flags) == {index_of["miss_gA"]}, "표지는 0 이 아니라 1 로 가야 한다"
    x = np.ones((4, be.N_STEPS, len(columns)), np.float32)
    out = be.augment(x.copy(), np.random.default_rng(0), zero, flags, noise=0.0)
    assert (out[:, :, index_of["a1"]] == 0.0).all()
    assert (out[:, :, index_of["miss_gA"]] == 1.0).all()


def test_drop_groups_never_drops_the_score_block():
    """`keep=("score",)` — 현행 6점수는 절대 빠지지 않는다(kit.drop_groups 의 규칙)."""
    groups = {"score": ["chart", "risk"], "G1": ["g1a"], "raw": ["raw_x"]}
    _columns, index_of = be._feature_layout([c for cols in groups.values() for c in cols])
    for seed in range(20):
        zero, _ = be.drop_indices(_drop_groups, groups, index_of, np.random.default_rng(seed), p=1.0)
        assert index_of["chart"] not in set(zero)
        assert index_of["risk"] not in set(zero)


def test_no_dropout_no_noise_is_identity():
    columns, index_of = be._feature_layout(["a1", "a2"])
    x = np.random.default_rng(1).normal(size=(3, be.N_STEPS, len(columns))).astype(np.float32)
    zero, flags = be.drop_indices(_drop_groups, {"gA": ["a1", "a2"]}, index_of,
                                  np.random.default_rng(0), p=0.0)
    assert len(zero) == 0 and len(flags) == 0
    np.testing.assert_array_equal(x, be.augment(x.copy(), np.random.default_rng(0), zero, flags, noise=0.0))


def test_input_noise_changes_inputs_but_keeps_scale():
    columns, _ = be._feature_layout(["a1", "a2", "a3"])
    x = np.zeros((200, be.N_STEPS, len(columns)), np.float32)
    out = be.augment(x.copy(), np.random.default_rng(0), np.empty(0, np.int64),
                     np.empty(0, np.int64), noise=be.INPUT_NOISE)
    assert out.std() == pytest.approx(be.INPUT_NOISE, rel=0.15)


def test_aug_apply_is_reproducible_for_a_given_rng(small):
    """같은 rng 상태면 같은 증강 — 학습 결정론의 전제."""
    x = be.window_batch(small["cube"], 70, np.arange(20))
    a = small["aug"].apply(x.copy(), np.random.default_rng(4))
    b = small["aug"].apply(x.copy(), np.random.default_rng(4))
    np.testing.assert_array_equal(a, b)


def test_model_dropout_is_active_in_train_mode(small):
    model = be.make_model(0, small["cube"].shape[2])
    x = torch.from_numpy(be.window_batch(small["cube"], 70, np.arange(60)))
    model.train()
    torch.manual_seed(0)
    a = model(x).detach().numpy()
    torch.manual_seed(1)
    b = model(x).detach().numpy()
    assert not np.allclose(a, b), "학습 모드에서 dropout 이 안 걸리면 억제 장치가 없는 것이다"


# 횡단면 attention 이 실제로 이웃을 본다 ----------------------------------------

def test_cross_sectional_attention_couples_entities(small):
    """다른 종목의 입력을 바꾸면 내 점수가 바뀌어야 한다 — 그게 횡단면 attention 의 정의다."""
    model = be.make_model(0, small["cube"].shape[2])
    model.eval()
    x = be.window_batch(small["cube"], 70, np.arange(60))
    with torch.inference_mode():
        base = model(torch.from_numpy(x)).numpy()
    y = x.copy()
    y[1:] += 3.0
    with torch.inference_mode():
        moved = model(torch.from_numpy(y)).numpy()
    assert abs(float(base[0] - moved[0])) > 1e-6


def test_markets_are_separate_cross_sections(small):
    """같은 세션이라도 국장·미장은 따로 attention — 한쪽만 바꿔도 다른 쪽 점수는 그대로."""
    cube = small["cube"].copy()
    model = be.make_model(0, cube.shape[2])
    before = be.predict_days(model, cube, small["observed"], small["market_id"],
                             small["entities"], small["sessions"], [70]).set_index("entity_id")["pred"]
    us_rows = np.flatnonzero(small["market_id"] == 1)
    cube[:, us_rows, :] = (cube[:, us_rows, :].astype(np.float32) + 5.0).astype(np.float16)
    after = be.predict_days(model, cube, small["observed"], small["market_id"],
                            small["entities"], small["sessions"], [70]).set_index("entity_id")["pred"]
    kr = [small["entities"][i] for i in np.flatnonzero(small["market_id"] == 0)]
    np.testing.assert_allclose(before.loc[kr].to_numpy(), after.loc[kr].to_numpy(), atol=1e-5)


def test_min_set_skips_thin_cross_sections(small):
    """종목이 MIN_SET 미만인 (세션, 시장) 집합은 건너뛴다 — 횡단면 attention 이 뜻을 잃는다."""
    observed = np.zeros_like(small["observed"])
    observed[70, :10] = True
    model = be.make_model(0, small["cube"].shape[2])
    out = be.predict_days(model, small["cube"], observed, small["market_id"],
                          small["entities"], small["sessions"], [70])
    assert out.empty


# 합성 스모크 -------------------------------------------------------------------

def test_synthetic_smoke_runs_end_to_end():
    assert be.main(["--synthetic", "--smoke", "1", "--seeds", "1", "--max-epochs", "1"]) == 0


def test_save_is_refused_with_smoke_or_synthetic():
    assert be.main(["--synthetic", "--save"]) == 2
    assert be.main(["--smoke", "1", "--save"]) == 2
