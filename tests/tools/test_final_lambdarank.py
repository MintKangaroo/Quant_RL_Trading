"""시행 BF(LambdaRank v2) — 쿼리 그룹·10분위 라벨·결정론·미래 누설·조기 종료의 출처.

합성 자료다. 그래서 "이긴다" 는 여기서 재지 않는다 — 여기서 재는 것은 **배관이 등록한 규칙대로인지**다.
지난 AN 이 기각된 이유(맨 위 24자리만 배움)는 여기 테스트로 못 잡는다. 대신 그 수리(NDCG@100·선형 이득·
시장별 쿼리)가 코드에 실제로 들어갔는지를 잡는다.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tools.trial_final_lambdarank import (
    MISMATCH_EXIT,
    N_BUCKETS,
    NDCG_AT,
    PARAMS,
    _block_rows_fallback,
    _blocks_fallback,
    _inner_split_fallback,
    _synthetic,
    bucket_labels,
    fit_rank,
    gap_lines,
    ndcg_at,
    query_groups,
    query_keys,
    rank_average,
    session_sets_match,
    split_frames,
    walk_rank,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture(scope="module")
def panel() -> tuple[pd.DataFrame, list[str], list[date]]:
    return _synthetic(seed=0, n_sessions=60, n_names=50)


# --------------------------------------------------------------------------- 쿼리 그룹


def test_쿼리는_세션과_시장으로_갈린다(panel) -> None:
    """국장 행과 미장 행이 한 쿼리에 섞이면 시장 사이 수익 차를 종목 신호로 착각한다."""
    frame, _, days = panel
    keys = query_keys(frame)
    assert keys == ["session", "market"]
    _, sizes = query_groups(frame, keys)
    assert len(sizes) == len(days) * 2
    assert set(sizes) == {50}


def test_그룹_크기_합이_행수와_같고_그룹이_연속한다(panel) -> None:
    """LightGBM 은 group 을 행 순서로만 읽는다 — 정렬이 깨지면 조용히 엉뚱한 쿼리를 배운다."""
    frame, _, _ = panel
    keys = query_keys(frame)
    ordered, sizes = query_groups(frame, keys)
    assert sizes.sum() == len(ordered)
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    for start, size in zip(starts, sizes, strict=True):
        block = ordered.iloc[start : start + size]
        assert block["session"].nunique() == 1
        assert block["market"].nunique() == 1


def test_그룹_열을_직접_주면_그것을_쓴다(panel) -> None:
    frame, _, _ = panel
    assert query_keys(frame, "session") == ["session"]
    assert query_keys(frame, ["session", "market"]) == ["session", "market"]
    with pytest.raises(KeyError):
        query_keys(frame, "없는열")


# --------------------------------------------------------------------------- 라벨


def test_라벨은_쿼리_안_10분위_0에서_9(panel) -> None:
    frame, _, _ = panel
    keys = query_keys(frame)
    label = bucket_labels(frame, keys)
    assert label.min() == 0
    assert label.max() == N_BUCKETS - 1
    counts = pd.concat([frame[keys], label], axis=1).groupby([*keys, "label"]).size()
    assert counts.min() == counts.max() == 5  # 50종목 / 10분위


def test_라벨은_그룹_밖과_비교하지_않는다() -> None:
    """한 시장이 통째로 좋은 날에도 그 시장의 라벨은 0~9 를 다 쓴다 — 시장 효과가 라벨로 새지 않는다."""
    rng = np.random.default_rng(1)
    rows = []
    for market, shift in (("KR", 0.0), ("US", 10.0)):
        rows.append(pd.DataFrame({"entity_id": [f"{market}{i}" for i in range(40)],
                                  "session": date(2024, 1, 2), "market": market,
                                  "y5": rng.normal(size=40) + shift}))
    frame = pd.concat(rows, ignore_index=True)
    keys = query_keys(frame)
    label = bucket_labels(frame, keys)
    for market in ("KR", "US"):
        part = label[frame["market"] == market]
        assert part.min() == 0
        assert part.max() == N_BUCKETS - 1


def test_라벨은_행_순서에_흔들리지_않는다(panel) -> None:
    """동점을 입력 순서로 가르면 같은 자료에서 다른 모델이 나온다."""
    frame, _, _ = panel
    keys = query_keys(frame)
    a = bucket_labels(frame, keys)
    shuffled = frame.sample(frac=1.0, random_state=7)
    b = bucket_labels(shuffled, keys).reindex(frame.index)
    pd.testing.assert_series_equal(a, b)


# --------------------------------------------------------------------------- 등록 규격


def test_등록한_하이퍼파라미터가_고정되어_있다() -> None:
    """AN 기각의 원인 셋(맨 위만·비선형 이득·피처 전량)이 실제로 고쳐졌는지."""
    assert NDCG_AT == 100
    assert PARAMS["eval_at"] == [100]
    assert PARAMS["lambdarank_truncation_level"] == 100
    assert PARAMS["label_gain"] == list(range(10))  # 선형 — 2^label-1 아니다
    assert PARAMS["min_data_in_leaf"] == 2000
    assert PARAMS["feature_fraction"] == 0.7
    assert PARAMS["bagging_fraction"] == 0.8
    assert PARAMS["lambda_l2"] > 0
    assert PARAMS["num_leaves"] <= 15


def test_ndcg_는_학습과_같은_선형_이득을_쓴다() -> None:
    """완벽한 순서면 1.0, 뒤집으면 1보다 작다 — 자가 학습 목적과 같아야 격차가 뜻을 가진다."""
    frame = pd.DataFrame({"session": date(2024, 1, 2), "market": "KR",
                          "entity_id": [f"a{i}" for i in range(20)],
                          "label": list(range(20)), "pred": list(range(20))})
    keys = ["session", "market"]
    assert ndcg_at(frame, keys, k=10) == pytest.approx(1.0)
    flipped = frame.assign(pred=frame["pred"].to_numpy()[::-1])
    assert ndcg_at(flipped, keys, k=10) < 1.0


# --------------------------------------------------------------------------- 내부 분할·조기 종료


def test_내부_검증은_학습창_마지막_20퍼센트다(panel) -> None:
    """kit.inner_split 과 같은 계약: (적합 세션, 검증 세션) 목록이고 사이에 퍼지가 있다."""
    frame, _, days = panel
    train = frame[frame["session"] <= days[49]]
    fit_days, val_days = _inner_split_fallback(train)
    assert max(fit_days) < min(val_days)
    assert len(val_days) == pytest.approx(50 * 0.2, abs=1)
    assert max(val_days) <= days[49]          # 판정 창(뒤쪽 세션)은 어느 쪽에도 없다
    assert set(fit_days) & set(val_days) == set()


def test_분할_세션으로_학습창을_자른다(panel) -> None:
    frame, _, days = panel
    train = frame[frame["session"] <= days[49]]
    fit_days, val_days = _inner_split_fallback(train)
    inner_train, inner_valid = split_frames(train, _inner_split_fallback)
    assert set(inner_train["session"]) == set(fit_days)
    assert set(inner_valid["session"]) == set(val_days)
    assert len(inner_train) + len(inner_valid) < len(train)   # 퍼지 세션은 어디에도 안 들어간다


def test_검증이_학습창_밖을_보면_거부한다(panel) -> None:
    """이 사고가 조용히 지나가면 조기 종료가 판정 창을 보고 멈춘다 — 결과 전체가 거짓이 된다."""
    frame, _, days = panel
    train = frame[frame["session"] <= days[39]]

    def leaky(t: pd.DataFrame):
        fit_days, _ = _inner_split_fallback(t)
        return fit_days, [days[45]]           # 학습창 밖

    with pytest.raises(ValueError, match="판정 창 누설"):
        split_frames(train, leaky)


def test_조기_종료는_내부_검증만_본다(panel) -> None:
    """판정 창 행이 valid_sets 에 들어가면 조기 종료가 답을 보고 멈춘다 — 그 길을 막는다."""
    frame, feats, days = panel
    train = frame[frame["session"] <= days[39]]
    keys = query_keys(frame)
    seen: list[list[date]] = []

    def spy(t: pd.DataFrame):
        a, b = _inner_split_fallback(t)
        seen.append(b)
        return a, b

    booster, diag = fit_rank(train, feats, keys, 0, spy, rounds=40, min_data=50)
    assert len(seen) == 1
    assert max(seen[0]) <= days[39]
    assert booster.best_iteration <= 40
    assert 0.0 < diag["inner_valid_ndcg"] <= 1.0
    assert 0.0 < diag["train_ndcg"] <= 1.0


def test_시간_역순_분할은_거부한다(panel) -> None:
    frame, feats, days = panel
    train = frame[frame["session"] <= days[39]]
    keys = query_keys(frame)

    def backwards(t: pd.DataFrame):
        a, b = _inner_split_fallback(t)
        return b, a  # 뒤집는다

    with pytest.raises(ValueError, match="시간 순"):
        fit_rank(train, feats, keys, 0, backwards, rounds=10, min_data=50)


# --------------------------------------------------------------------------- 워크포워드


def test_학습은_퍼지_이전까지만_본다(panel) -> None:
    """미래 누설 — 블록 시작에서 퍼지 5세션을 더 뺀 지점까지만 학습에 들어가야 한다."""
    frame, feats, days = panel
    keys = query_keys(frame)
    bl = _blocks_fallback(days, min_train=30, block=10)
    ends: list[date] = []

    def spy(t: pd.DataFrame):
        ends.append(t["session"].max())
        return _inner_split_fallback(t)

    pred, diags = walk_rank(frame, feats, keys, days, bl[:2], 0, spy, gap=5, label="t", min_data=50, rounds=30)
    for (first, _), end in zip(bl[:2], ends, strict=True):
        assert end == days[first - 5 - 1]
    assert pred["session"].min() == days[bl[0][0]]
    assert len(diags) == 2


def test_국장_휴장일의_미장_행이_사라지지_않는다(panel) -> None:
    """kit 의 `sessions` 는 **국장** 세션이다. 블록 끝을 `sessions[last]` 로 닫으면 국장 휴장일의 미장
    행이 어느 블록에도 안 들어가 조용히 사라진다 — 미장 수익·IC 가 며칠만큼 빠진 채 판정된다.

    잃는 자리는 블록 **이음매**다: 휴장일이 블록 안쪽이면 닫힌 구간에도 들어가지만, 한 블록의 끝과
    다음 블록의 시작 사이에 놓이면 어느 구간에도 안 든다. 그래서 휴장일을 이음매에 놓고 잰다
    (min_train 25 · gap 5 · block 5 → 블록 끝이 인덱스 34·39·44 이고 날짜 구멍이 그 뒤에 온다).
    """
    frame, feats, days = panel
    keys = query_keys(frame)
    holiday = days[45]                      # 국장만 쉬고 미장은 도는 날
    kr_gone = frame[(frame["market"] == "KR") & (frame["session"] == holiday)].index
    frame = frame.drop(index=kr_gone)
    kr_sessions = sorted(frame.loc[frame["market"] == "KR", "session"].unique())
    bl = _blocks_fallback(kr_sessions, min_train=25, block=5, purge=5)
    # 휴장일이 정말 이음매에 있는지 먼저 확인한다 — 아니면 이 테스트는 헛돈다
    seams = {kr_sessions[last] for _, last in bl if last + 1 < len(kr_sessions)}
    assert any(s < holiday < kr_sessions[kr_sessions.index(s) + 1] for s in seams)

    pred, _ = walk_rank(frame, feats, keys, kr_sessions, bl, 0, _inner_split_fallback,
                        gap=5, label="t", min_data=50, rounds=20)

    lo = kr_sessions[bl[0][0]]
    hi = kr_sessions[bl[-1][1] + 1] if bl[-1][1] + 1 < len(kr_sessions) else frame["session"].max()
    for market in ("KR", "US"):
        want = {d for d in frame.loc[frame["market"] == market, "session"].unique() if lo <= d < hi}
        got = set(pred.loc[pred["market"] == market, "session"].unique())
        assert want - got == set(), f"{market}: 덮이지 않은 세션 {sorted(want - got)}"
    assert lo <= holiday < hi
    assert holiday in set(pred.loc[pred["market"] == "US", "session"].unique())
    assert holiday not in set(pred.loc[pred["market"] == "KR", "session"].unique())
    # 한 세션이 두 블록에 들어가지도 않는다 — 겹치면 그 날이 두 번 채점된다
    assert not pred.duplicated(["entity_id", "session"]).any()


def test_판정_행_선택이_kit_과_한_글자도_다르지_않다(panel) -> None:
    """규칙이 두 곳에 있으면 대조군과 처리군이 다른 날을 채점한다 — 이음매 결함이 그렇게 생겼다.
    실측은 `kit.block_rows` 를 쓰고, 여기 폴백은 사본이다. 사본이 어긋나면 이 테스트가 잡는다."""
    from tools.final_round_kit import block_rows, block_span

    frame, _, days = panel
    holiday = days[45]
    frame = frame.drop(index=frame[(frame["market"] == "KR") & (frame["session"] == holiday)].index)
    kr_sessions = sorted(frame.loc[frame["market"] == "KR", "session"].unique())
    bl = _blocks_fallback(kr_sessions, min_train=25, block=5, purge=5)
    for first, last in bl:
        mine = _block_rows_fallback(frame, kr_sessions, first, last)
        theirs = block_rows(frame, kr_sessions, first, last)
        pd.testing.assert_frame_equal(mine, theirs)
    # kit 의 구간이 실제로 반열림인지도 같이 본다(닫힌 구간으로 되돌아가면 여기서 걸린다)
    first, last = bl[0]
    lo, hi = block_span(kr_sessions, first, last)
    assert lo == kr_sessions[first]
    assert hi == kr_sessions[last + 1]


def test_블록_시작은_엠바고만큼_밀리지_않는다(panel) -> None:
    """채점 첫 세션은 `MIN_TRAIN + PURGE`(155)다. 엠바고는 블록을 늦추지 않고 **학습 끝점만** 당긴다 —
    블록 시작을 GAP 만큼 밀면 BF 만 판정 블록이 달라져 C0·C1 과 견줄 수 없다."""
    from tools import final_round_kit as kit

    assert kit.FIRST_JUDGED_OFFSET == kit.MIN_TRAIN + kit.PURGE
    assert kit.FIRST_JUDGED_OFFSET != kit.MIN_TRAIN + kit.GAP
    _, _, days = panel
    # 폴백도 같은 규격이다 — 시작 = min_train + purge, 끝점만 GAP 만큼 앞
    bl = _blocks_fallback(days, min_train=30, block=10, purge=5)
    assert bl[0][0] == 35


def test_관문_종료_코드가_kit_것과_겹치지_않는다() -> None:
    """셸이 "무엇이 없어서 안 돌았나" 를 로그에 가려 적으려면 번호가 겹치면 안 된다."""
    from tools import final_round_kit as kit

    assert MISMATCH_EXIT not in {kit.CONTROLS_EXIT, kit.COVERAGE_EXIT, kit.WINDOW_EXIT}


def test_라운드_소진과_조기_종료를_구분해_적는다(panel) -> None:
    """둘을 섞어 세면 "조기 종료가 잘 걸린다" 는 기록이 실은 "매번 예산을 다 썼다" 를 포함한다 —
    그러면 과적합 진단을 반대로 읽는다. 라운드를 2로 조여 **반드시 소진**되게 만들어 잰다."""
    frame, feats, days = panel
    keys = query_keys(frame)
    bl = _blocks_fallback(days, min_train=30, block=10)[:1]
    _, diags = walk_rank(frame, feats, keys, days, bl, 0, _inner_split_fallback,
                         gap=5, label="t", min_data=50, rounds=30)
    assert diags["rounds"].iloc[0] == 30                  # 예산을 진단에 같이 적는다
    assert diags["stopped_early"].iloc[0] == float(diags["best_iter"].iloc[0] < 30)

    # 기록 줄이 둘을 섞지 않는지 — 예산을 다 쓴 회차와 걸린 회차를 손으로 만들어 넣는다
    made = pd.DataFrame([
        {"train_ndcg": 0.9, "inner_valid_ndcg": 0.8, "judge_ndcg": 0.8, "best_iter": 800.0,
         "rounds": 800.0, "stopped_early": 0.0},          # 다 썼다
        {"train_ndcg": 0.9, "inner_valid_ndcg": 0.8, "judge_ndcg": 0.8, "best_iter": 120.0,
         "rounds": 800.0, "stopped_early": 1.0},          # 걸렸다
    ])
    g, line = gap_lines(made)
    assert g["early_share"] == 0.5
    assert "조기 종료 50%" in line
    assert "라운드 800 소진 1/2" in line


def test_채점_세션이_다르면_짚어낸다() -> None:
    """kit 의 `block_rows` 는 블록을 닫힌 구간으로 고른다 — 국장 휴장일이 이음매에 놓이면 대조군에는
    그날 미장 행이 없다. 처리가 며칠 더 넓으면 이긴 건지 넓은 건지 가를 수 없으므로 판정을 멈춘다."""
    base = {"entity_id": "US:A:1", "pred": 0.0}
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    pred = pd.DataFrame([{**base, "session": d, "market": "US"} for d in days])
    same = pred.copy()
    assert session_sets_match(pred, same) == []
    short = pred[pred["session"] == days[0]]
    drift = session_sets_match(pred, short)
    assert len(drift) == 1
    assert "미장" in drift[0]
    assert str(days[1]) in drift[0]


def test_같은_시드는_같은_예측을_낸다(panel) -> None:
    frame, feats, days = panel
    keys = query_keys(frame)
    bl = _blocks_fallback(days, min_train=30, block=10)[:1]
    a, _ = walk_rank(frame, feats, keys, days, bl, 3, _inner_split_fallback, label="a", min_data=50, rounds=30)
    b, _ = walk_rank(frame, feats, keys, days, bl, 3, _inner_split_fallback, label="b", min_data=50, rounds=30)
    pd.testing.assert_frame_equal(a, b)


def test_시드가_다르면_다른_예측이_난다(panel) -> None:
    frame, feats, days = panel
    keys = query_keys(frame)
    bl = _blocks_fallback(days, min_train=30, block=10)[:1]
    a, _ = walk_rank(frame, feats, keys, days, bl, 0, _inner_split_fallback, label="a", min_data=50, rounds=30)
    b, _ = walk_rank(frame, feats, keys, days, bl, 4, _inner_split_fallback, label="b", min_data=50, rounds=30)
    assert not np.allclose(a["pred"].to_numpy(), b["pred"].to_numpy())


def test_블록이_하나도_없으면_조용히_넘어가지_않는다(panel) -> None:
    frame, feats, days = panel
    keys = query_keys(frame)
    with pytest.raises(ValueError, match="블록"):
        walk_rank(frame.iloc[:0], feats, keys, days, [(35, 44)], 0, _inner_split_fallback, min_data=50)


# --------------------------------------------------------------------------- 변형 BF2


def test_순위_평균은_척도가_아니라_순위로_섞는다() -> None:
    """원점수를 그대로 더하면 척도 큰 쪽이 먹는다 — AT 의 교훈(단위를 먼저 맞춘다)."""
    keys = ["session", "market"]
    base = {"session": date(2024, 1, 2), "market": "KR"}
    a = pd.DataFrame({**base, "entity_id": ["x", "y", "z"], "pred": [1.0, 2.0, 3.0]})
    b = pd.DataFrame({**base, "entity_id": ["x", "y", "z"], "pred": [3000.0, 2000.0, 1000.0]})
    out = rank_average(a, b, keys).set_index("entity_id")["pred"]
    assert out["x"] == pytest.approx(out["z"])  # 한쪽 1위·다른쪽 3위 → 같은 자리
    assert out["y"] == pytest.approx(out["x"])
    assert set(rank_average(a, b.iloc[:2], keys)["entity_id"]) == {"x", "y"}
