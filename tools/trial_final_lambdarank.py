"""시행 BF — LambdaRank v2(NDCG@100 · 10분위 라벨). docs/protocols/final-model-round-2026-10.md 의 BF 절.

    .venv/bin/python tools/trial_final_lambdarank.py --smoke        # 합성 자료 스모크 (언제든)
    .venv/bin/python tools/trial_final_lambdarank.py --precheck     # 실자료 seed 0 · 첫 5블록 · 겹침·격차만
    .venv/bin/python tools/trial_final_lambdarank.py [--save]       # 본 측정 (2026-10-05 이후, 예산 1회)

지난 AN(기각, 9/24)이 왜 죽었는지에서 출발한다: **NDCG@24** 는 세션마다 맨 위 24자리의 라벨에서만 배웠고,
h5 수익 라벨의 잡음을 외웠다(시드 겹침 23%). 그래서 이번에 바꾸는 것은 셋뿐이다.

1. **NDCG@100** — 배우는 자리를 네 배로 늘려 표본을 되찾는다.
2. **라벨 = 세션×시장 안 y5 의 10분위 버킷**(label_gain 0..9 **선형**). 2^label−1 이면 맨 윗칸 하나가
   이득의 절반을 먹는다 — 가장 잡음이 큰 자리를 가장 믿는 셈이다. 선형 이득은 그 편향을 뺀다.
3. 입력이 FA(전 피처)다 — AN 은 6점수였다. 그래서 `feature_fraction 0.7` 로 피처 쪽도 가지를 친다.

과적합 억제는 공통 틀과 같다: 하이퍼파라미터는 이 파일에 **고정**(판정 창에서 고르지 않는다),
조기 종료는 학습창 안쪽 검증(`final_round_kit.inner_split`)의 NDCG@100 으로만, 시드 5,
그리고 "학습창 대비 판정창 격차" 를 과적합 지표로 남긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import TYPE_CHECKING, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover
    from lightgbm import Booster

#: 학습창을 (적합 세션, 검증 세션) 으로 가르는 함수 — `final_round_kit.inner_split` 의 계약이다.
InnerSplit = Callable[[pd.DataFrame], tuple[list[date], list[date]]]
#: 블록 하나의 판정 행을 고르는 함수 — `final_round_kit.block_rows` 의 계약이다.
BlockRows = Callable[[pd.DataFrame, "list[date]", int, int], pd.DataFrame]

PROTOCOL = Path("docs/protocols/final-model-round-2026-10.md")
#: 본 측정 가능 시각 — BC(10/3)·6차 G8(10/4) 뒤. 사전등록 머리글.
# 원래 10/5(일정 잠금). 사용자 9/27 허용으로 앞당김 — 진짜 잠금은 스크립트의 '초안' 검사·해시 고정이다.
MEASURE_FROM = date(2026, 9, 28)
MARKETS = ("KR", "US")
WINDOW = (date(2022, 7, 1), date(2026, 6, 30))
SEEDS = (0, 1, 2, 3, 4)
TOP = 24
#: 배우는 자리. AN 은 24 였다 — 그것이 기각의 원인으로 적혀 있다.
NDCG_AT = 100
N_BUCKETS = 10
#: 퍼지 5 + 엠바고 5 (공통 틀 1). kit 의 GAP 이 있으면 그것을 쓴다.
GAP = 10
MAX_ROUNDS, EARLY_STOP = 800, 50
#: 관문 종료 코드. 3·4·5 는 kit 것이다(`CONTROLS_EXIT`·`COVERAGE_EXIT`·`WINDOW_EXIT`) — 겹치면 셸 로그에서
#: "무엇이 없어서 안 돌았나" 를 못 가린다. BF 만의 관문은 그 뒤 번호를 쓴다.
MISMATCH_EXIT = 6

#: **등록 때 고정한다.** 판정 창에서 고르지 않는다(공통 틀 2).
PARAMS: dict[str, object] = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "eval_at": [NDCG_AT],
    "lambdarank_truncation_level": NDCG_AT,
    "label_gain": list(range(N_BUCKETS)),  # 선형 — 맨 위를 덜 믿는다
    "num_leaves": 7,
    "min_data_in_leaf": 2000,
    "learning_rate": 0.03,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "num_threads": 6,
    "deterministic": True,
    "force_row_wise": True,
}

GROUP_KEYS = ("session", "market")


# --------------------------------------------------------------------------- 쿼리 그룹·라벨


def query_keys(panel: pd.DataFrame, override: object = None) -> list[str]:
    """쿼리 그룹의 열 이름. 시장별 쿼리(**세션×시장**)가 규칙이다 — 국장 행과 미장 행을 한 쿼리에 섞으면
    시장 사이 수익 차이를 종목 신호로 착각한다.

    주의: `final_round_kit.load_full_panel` 이 주는 ``groups`` 는 **묶음 피처**(피처 드롭아웃용)이지
    쿼리 그룹이 아니다 — 그것을 여기에 넘기지 않는다. 열 이름을 직접 주고 싶을 때만 ``override`` 를 쓴다.
    """
    if isinstance(override, str):
        keys = [override]
    elif isinstance(override, list | tuple) and override and all(isinstance(g, str) for g in override):
        keys = list(override)
    else:
        keys = [k for k in GROUP_KEYS if k in panel.columns] or ["session"]
    missing = [k for k in keys if k not in panel.columns]
    if missing:
        raise KeyError(f"쿼리 그룹 열이 패널에 없다: {missing}")
    return keys


def bucket_labels(frame: pd.DataFrame, keys: list[str], target: str = "y5") -> pd.Series:
    """쿼리(세션×시장) 안에서 target 의 10분위 버킷 0..9. 순위는 그룹 안에서만 — 그룹 밖과 비교하지 않는다.

    두 군데를 조심한다.
    - `method="first"` 는 동점을 **입력 순서**로 가른다 — 같은 자료가 행 순서에 따라 다른 라벨을 낸다.
      `method="average"` 로 묶는다.
    - `rank(pct=True)` 는 r/n 이라 맨 아래 칸이 한 칸 모자라고 맨 위 칸이 한 칸 많아진다(50종목에서 4 대 6).
      `(r − 0.5)/n` 으로 칸 중심을 쓰면 열 칸이 고르게 찬다 — 분위가 기울면 이득도 같이 기운다.
    """
    grouped = frame.groupby(keys, sort=False)[target]
    rank = grouped.rank(method="average")
    size = grouped.transform("count")
    pct = (rank - 0.5) / size
    label = (pct * N_BUCKETS).clip(lower=0.0, upper=N_BUCKETS - 0.001).fillna(0.0)
    return label.astype(int).rename("label")


def query_groups(frame: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, np.ndarray]:
    """그룹이 **연속**하도록 정렬한 표와 그룹 크기 배열. LightGBM 은 group 을 행 순서로만 읽는다 —
    정렬을 빼먹으면 조용히 엉뚱한 쿼리를 학습한다."""
    ordered = frame.sort_values([*keys, "entity_id"], kind="stable")
    sizes = ordered.groupby(keys, sort=False).size().to_numpy()
    return ordered, sizes


# --------------------------------------------------------------------------- 지표


def ndcg_at(frame: pd.DataFrame, keys: list[str], *, k: int = NDCG_AT,
            score: str = "pred", label: str = "label") -> float:
    """쿼리 평균 NDCG@k. 이득은 학습과 **같은 선형**(label_gain=0..9)이다 — 학습 목적과 다른 자로 재면
    "학습창 대비 판정창 격차" 가 무엇의 격차인지 알 수 없게 된다."""
    disc = 1.0 / np.log2(np.arange(2, k + 2))
    vals = []
    for _, part in frame.groupby(keys, sort=False):
        if part.empty:
            continue
        gain = part[label].to_numpy(np.float64)
        order = np.argsort(-part[score].to_numpy(np.float64), kind="stable")
        top = gain[order][:k]
        ideal = np.sort(gain)[::-1][:k]
        dcg = float((top * disc[: len(top)]).sum())
        idcg = float((ideal * disc[: len(ideal)]).sum())
        if idcg > 0:
            vals.append(dcg / idcg)
    return float(np.mean(vals)) if vals else float("nan")


def top_overlap(a: pd.DataFrame, b: pd.DataFrame, *, top: int = TOP) -> float:
    """세션 평균 상위 N 겹침 — 등록 전 점검 ③. AN 은 여기서 23% 였다."""
    m = a.merge(b, on=["entity_id", "session"], suffixes=("_a", "_b"))
    vals = []
    for _, g in m.groupby("session", sort=False):
        sa = set(g.nlargest(top, "pred_a")["entity_id"])
        sb = set(g.nlargest(top, "pred_b")["entity_id"])
        vals.append(len(sa & sb) / top)
    return float(np.mean(vals)) if vals else float("nan")


def rank_average(a: pd.DataFrame, b: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """변형 BF2 — 두 예측의 **쿼리 안 백분위 순위 평균**. 원점수는 척도가 달라 그대로 더하면 한쪽이 먹는다."""
    m = a.merge(b, on=["entity_id", *keys], suffixes=("_a", "_b"), how="inner")
    ra = m.groupby(keys, sort=False)["pred_a"].rank(pct=True, method="average")
    rb = m.groupby(keys, sort=False)["pred_b"].rank(pct=True, method="average")
    m["pred"] = (ra + rb) / 2.0
    return m[["entity_id", *keys, "pred"]]


# --------------------------------------------------------------------------- 학습


def split_frames(train: pd.DataFrame,
                 inner_split: InnerSplit) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`inner_split` 이 준 세션 목록으로 학습창을 자른다. 잘못된 분할은 여기서 **멈춘다** —
    조기 종료가 판정 창을 보는 사고는 조용히 지나가면 결과 전체가 거짓이 된다."""
    fit_days, val_days = inner_split(train)
    if not len(fit_days) or not len(val_days):
        raise ValueError("내부 학습·검증 중 한쪽이 비었다 — inner_split 을 볼 것")
    if min(val_days) <= max(fit_days):
        raise ValueError("내부 검증이 내부 학습보다 앞선다 — 시간 순 분할이 아니다")
    if max(val_days) > train["session"].max():
        raise ValueError("내부 검증이 학습창 밖을 본다 — 판정 창 누설")
    fit_set, val_set = set(fit_days), set(val_days)
    return (train[train["session"].isin(fit_set)], train[train["session"].isin(val_set)])


def fit_rank(train: pd.DataFrame, feats: list[str], keys: list[str], seed: int,
             inner_split: InnerSplit, *, rounds: int = MAX_ROUNDS,
             min_data: int | None = None) -> tuple[Booster, dict[str, float]]:
    """학습창 하나로 랭커 하나. 조기 종료는 **학습창 내부 검증**의 NDCG@100 으로만 한다.

    `inner_split(train)` 은 (적합 세션, 검증 세션) 을 준다 — `final_round_kit.inner_split` 의 계약이다
    (마지막 20%, 사이에 퍼지 5). 판정 블록은 학습창 뒤에 있으므로 검증은 판정 블록과 겹칠 수 없다.
    반환: (booster, 진단 dict).

    ``min_data``·``rounds`` 는 **합성 스모크·테스트만** 줄인다. 실측(main)은 주지 않는다 —
    min_data 2000 은 등록한 값이고, 3천 행짜리 합성 자료에서는 가지가 하나도 안 갈려 예측이 전부 0 이 된다.
    """
    import lightgbm as lgb  # 무거운 라이브러리는 실제로 쓸 때만 든다

    inner_train, inner_valid = split_frames(train, inner_split)

    params = {**PARAMS, "seed": seed, "bagging_seed": seed + 1, "feature_fraction_seed": seed + 2,
              "data_random_seed": seed + 3}
    if min_data is not None:
        params["min_data_in_leaf"] = min_data
    #: 스레드 수는 등록 대상이 아니다 — 다른 작업과 머신을 나눠 쓸 때(테스트·스모크) 밖에서 줄인다.
    #: `deterministic=True` 라 스레드를 줄여도 같은 자료·같은 시드면 같은 모델이 나온다.
    params["num_threads"] = int(os.environ.get("QUANT_RL_LGB_THREADS", "") or 6)
    # ``free_raw_data`` 는 기본(True)으로 둔다 — 원배열을 Dataset 이 붙잡으면 학습창이 1GB 를 넘는 마지막
    # 블록에서 같은 자료를 두 번 들게 된다. 예측용 배열은 학습이 끝난 뒤 다시 만든다.
    frames: dict[str, pd.DataFrame] = {}
    sets: dict[str, Any] = {}
    for name, part in (("tr", inner_train), ("va", inner_valid)):
        ordered, sizes = query_groups(part, keys)
        frames[name] = ordered
        sets[name] = lgb.Dataset(ordered[feats].to_numpy(np.float32),
                                 bucket_labels(ordered, keys).to_numpy(), group=sizes)
    booster = lgb.train(params, sets["tr"], num_boost_round=rounds,
                        valid_sets=[sets["va"]], valid_names=["inner_valid"],
                        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
    del sets
    diag: dict[str, float] = {"best_iter": float(booster.best_iteration or rounds)}
    for name, key in (("tr", "train_ndcg"), ("va", "inner_valid_ndcg")):
        ordered = frames.pop(name)
        ordered["pred"] = booster.predict(ordered[feats].to_numpy(np.float32),
                                          num_iteration=booster.best_iteration)
        ordered["label"] = bucket_labels(ordered, keys)
        diag[key] = ndcg_at(ordered, keys)
    return booster, diag


def walk_rank(panel: pd.DataFrame, feats: list[str], keys: list[str], sessions: list[date],
              bl: list[tuple[int, int]], seed: int, inner_split: InnerSplit, *, gap: int = GAP,
              label: str = "BF1", min_data: int | None = None, rounds: int = MAX_ROUNDS,
              train_end: Callable[[list[date], int], date] | None = None,
              rows_of: BlockRows | None = None,
              store: Any = None, clock: Any = None, n_seeds: int = 0,
              record: Callable[..., bool] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """확장창 워크포워드. 학습은 블록 시작 − (퍼지+엠바고)까지만. 반환: (예측, 블록별 진단).

    판정 행은 **`kit.block_rows` 로만 고른다**(`rows_of`). 규칙이 두 곳에 있으면 대조군과 처리군이 서로
    다른 날을 채점한다 — 실제로 그런 적이 있다(이음매 결함, 9/27). 아래 `_block_rows_fallback` 은
    스모크·테스트용이고 kit 과 **같은 반열림 규칙**이며, 둘이 어긋나지 않는지 테스트가 지킨다.

    ``train_end`` 는 kit 것을 쓴다(`kit.train_end` — 퍼지 5 + 엠바고 5). 안 주면 ``gap`` 으로 센다(테스트용).
    ``min_data``·``rounds`` 는 스모크·테스트만 줄인다(fit_rank 를 볼 것).

    ``store``·``clock``·``record``(=`kit.record_progress`) 를 주면 블록마다 진행을 적는다. 적는 것은
    **내부 검증 NDCG(부호 뒤집음)·학습 NDCG·부스팅 횟수·조기 종료·경과**뿐이다 — 같은 diag 에 있는
    `judge_ndcg` 는 **판정 창 지표라서 적지 않는다**(사전등록: 학습 중에 판정 창을 보지 않는다)."""
    preds: list[pd.DataFrame] = []
    diags: list[dict[str, float]] = []
    rows = rows_of or _block_rows_fallback
    mark = monotonic()  # invariant-allow: wallclock — 블록 하나에 걸린 시간
    for number, (first, last) in enumerate(bl):
        end = train_end(sessions, first) if train_end else sessions[first - gap - 1]
        train = panel[(panel["session"] <= end) & panel["y5"].notna()]
        test = rows(panel, sessions, first, last).copy()
        if train.empty or test.empty:
            continue
        booster, diag = fit_rank(train, feats, keys, seed, inner_split,
                                 rounds=rounds, min_data=min_data)
        test["pred"] = booster.predict(test[feats].to_numpy(np.float32),
                                       num_iteration=booster.best_iteration)
        test["label"] = bucket_labels(test, keys)
        #: **조기 종료가 걸린 것과 라운드를 다 쓴 것을 구분해 적는다.** 둘을 섞으면 "조기 종료 N회" 가
        #: 예산을 다 쓴 회차까지 세서, 과적합 진단이 반대로 읽힌다(BE 가 자기 코드에서 찾은 결함).
        diag.update({"seed": float(seed), "first": float(first), "judge_ndcg": ndcg_at(test, keys),
                     "train_rows": float(len(train)), "rounds": float(rounds),
                     "stopped_early": float(diag["best_iter"] < rounds)})
        preds.append(test[["entity_id", *keys, "pred", "label"]])
        diags.append(diag)
        print(f"  {label} seed{seed} 블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행 · "
              f"iter {diag['best_iter']:.0f} · 내부검증 NDCG {diag['inner_valid_ndcg']:.4f} · "
              f"판정 {diag['judge_ndcg']:.4f}", flush=True)
        if record is not None:
            record(store, clock, "BF", source="trial_final_lambdarank",
                   market="+".join(sorted(pd.unique(test["market"]))) if "market" in test.columns else "",
                   seed=int(seed), n_seeds=n_seeds or None, block=number, n_blocks=len(bl),
                   step=int(diag["best_iter"]), rounds=int(rounds),
                   # NDCG 는 **높을수록** 좋다 — 표 규약대로 부호를 뒤집어 넣는다.
                   train_loss=-float(diag["train_ndcg"]), val_loss=-float(diag["inner_valid_ndcg"]),
                   metric=f"ndcg@{NDCG_AT}(−)", stopped_early=bool(diag["stopped_early"]),
                   elapsed_s=monotonic() - mark,  # invariant-allow: wallclock
                   note=f"{label} · 학습 {len(train):,}행 ~{end}")
        mark = monotonic()  # invariant-allow: wallclock
    if not preds:
        raise ValueError("블록이 하나도 돌지 않았다 — 세션·퍼지·블록 설정을 볼 것")
    return pd.concat(preds, ignore_index=True), pd.DataFrame(diags)


def gap_lines(diags: pd.DataFrame) -> tuple[dict[str, float], str]:
    """학습창 대비 판정창 격차(공통 틀 5) — 판정에 쓰지 않고 **기록만** 한다."""
    g = {"train_ndcg": float(diags["train_ndcg"].mean()),
         "inner_valid_ndcg": float(diags["inner_valid_ndcg"].mean()),
         "judge_ndcg": float(diags["judge_ndcg"].mean()),
         "best_iter": float(diags["best_iter"].mean())}
    g["gap"] = g["train_ndcg"] - g["judge_ndcg"]
    g["inner_gap"] = g["train_ndcg"] - g["inner_valid_ndcg"]
    #: 조기 종료가 **걸린** 회차와 라운드를 **다 쓴** 회차는 다른 것이다. 둘을 섞어 세면
    #: "조기 종료가 잘 걸린다" 는 기록이 실제로는 "매번 예산을 다 썼다" 를 포함한다.
    early = diags["stopped_early"] if "stopped_early" in diags.columns else pd.Series(dtype=float)
    g["early_share"] = float(early.mean()) if len(early) else float("nan")
    n_used = int((1 - early).sum()) if len(early) else 0
    budget = int(diags["rounds"].max()) if "rounds" in diags.columns else MAX_ROUNDS
    line = (f"과적합 지표: 학습 NDCG@{NDCG_AT} {g['train_ndcg']:.4f} · 내부검증 {g['inner_valid_ndcg']:.4f} · "
            f"판정 {g['judge_ndcg']:.4f} → 격차 {g['gap']:+.4f}(내부 {g['inner_gap']:+.4f}) · "
            f"평균 부스팅 {g['best_iter']:.0f}회 · 조기 종료 {g['early_share']:.0%}"
            f"(라운드 {budget} 소진 {n_used}/{len(diags)})")
    return g, line


# --------------------------------------------------------------------------- 합성 스모크


def _synthetic(*, seed: int = 0, n_sessions: int = 90, n_names: int = 60,
               markets: tuple[str, ...] = MARKETS) -> tuple[pd.DataFrame, list[str], list[date]]:
    """합성 패널 — 배관 검증용. 신호가 심겨 있지만 **"이긴다"는 여기서 재지 않는다**."""
    rng = np.random.default_rng(seed)
    feats = [f"f{i}" for i in range(8)]
    rows = []
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(n_sessions)]
    for market in markets:
        for day in days:
            x = rng.normal(size=(n_names, len(feats)))
            y = x[:, 0] * 0.6 + x[:, 1] * 0.3 + rng.normal(scale=1.0, size=n_names)
            frame = pd.DataFrame(x, columns=feats)
            frame["entity_id"] = [f"{market}:A:{i:04d}" for i in range(n_names)]
            frame["session"] = day
            frame["market"] = market
            frame["y5"] = y
            rows.append(frame)
    panel = pd.concat(rows, ignore_index=True)
    return panel, feats, days


def _inner_split_fallback(train: pd.DataFrame, share: float = 0.2,
                          purge: int = 5) -> tuple[list[date], list[date]]:
    """스모크·테스트용 내부 분할 — `final_round_kit.inner_split` 과 **같은 규칙**(마지막 share, 사이 퍼지).
    실측은 kit 것을 쓴다. 두 구현이 갈리면 여기 테스트가 실측을 대변하지 못한다."""
    days = sorted(pd.unique(train["session"]))
    cut = int(len(days) * (1.0 - share))
    return list(days[: max(1, cut - purge)]), list(days[cut:])


def _block_rows_fallback(panel: pd.DataFrame, sessions: list[date], first: int, last: int) -> pd.DataFrame:
    """스모크·테스트용 판정 행 선택 — `final_round_kit.block_rows` 와 **같은 반열림 규칙**이다.

    ``[sessions[first], sessions[last+1])``. 닫힌 구간으로 고르면 블록 이음매에 놓인 날(국장 휴장일에만
    열린 미장 세션)이 어느 블록에도 안 든다. 실측은 kit 것을 쓰고, 여기 것은 둘이 어긋나지 않는지
    테스트가 지키는 사본이다.
    """
    lo = sessions[first]
    hi = sessions[last + 1] if last + 1 < len(sessions) else None
    selected = panel["session"] >= lo
    if hi is not None:
        selected &= panel["session"] < hi
    return panel[selected]


def _blocks_fallback(sessions: list[date], *, min_train: int = 150, block: int = 20,
                     purge: int = 5) -> list[tuple[int, int]]:
    """스모크·테스트용 블록. **시작은 `min_train + purge`** 다 — 엠바고는 블록을 늦추지 않고
    학습 끝점만 당긴다(`kit.train_end`). 실측은 `kit.blocks` 를 쓰고, 채점 첫 세션은
    `kit.FIRST_JUDGED_OFFSET`(= MIN_TRAIN + PURGE = 155) 이다. MIN_TRAIN + GAP 이 아니다.
    """
    out: list[tuple[int, int]] = []
    start = min_train + purge
    while start + block <= len(sessions):
        out.append((start, start + block - 1))
        start += block
    return out


# --------------------------------------------------------------------------- 실행


def _kit() -> ModuleType:
    """공통 틀. 없으면 무엇이 없는지 밝히고 멈춘다 — 조용히 자체 규칙으로 판정하지 않는다."""
    try:
        from tools import final_round_kit as kit
    except ImportError as exc:  # pragma: no cover - 실자료 경로
        raise SystemExit(
            f"tools/final_round_kit.py 가 필요하다({exc}). 스모크는 --smoke 로만 돈다.") from None
    return kit


def seed_preds(kit: ModuleType, panel: pd.DataFrame, feats: list[str], keys: list[str],
               sessions: list[date], bl: list[tuple[int, int]], seed: int, *,
               tag: str = "", store: Any = None, clock: Any = None, n_seeds: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """시드 하나의 BF1 예측 — **캐시한다**. 메모리 가드가 내려도 다음 회차가 남은 시드만 돈다.

    한 시드가 몇 시간이고 시드가 다섯이다. 캐시가 없으면 가드에 한 번 내려갈 때마다 처음부터 다시 돈다.
    꼬리표는 대조군과 **같은 규칙**(`kit.control_tag`)을 쓴다 — 창·시장이 다른 캐시를 섞어 읽지 않게.
    """
    cache = Path(getattr(kit, "CACHE", None) or "data/_diag/final-round")
    cache.mkdir(parents=True, exist_ok=True)
    stem = f"pred-BF1-seed{seed}-{tag or kit.control_tag(panel)}"
    path, diag_path = cache / f"{stem}.pkl", cache / f"{stem}-diag.pkl"
    if path.exists() and diag_path.exists():
        print(f"  BF1 seed{seed}: 캐시 사용 {path}", flush=True)
        return (pd.read_pickle(path),       # invariant-allow: data-access — 작업 캐시
                pd.read_pickle(diag_path))  # invariant-allow: data-access — 작업 캐시
    pred, diags = walk_rank(panel, feats, keys, sessions, bl, seed, kit.inner_split,
                            train_end=kit.train_end, rows_of=kit.block_rows,
                            store=store, clock=clock, record=kit.record_progress, n_seeds=n_seeds)
    pred.to_pickle(path)        # invariant-allow: data-access — 작업 캐시
    diags.to_pickle(diag_path)  # invariant-allow: data-access — 작업 캐시
    return pred, diags


def session_sets_match(pred: pd.DataFrame, control: pd.DataFrame) -> list[str]:
    """처리와 대조가 **같은 (세션 × 시장)** 을 채점하는지. 어긋난 자리를 사람 말로 돌려준다(없으면 빈 목록).

    kit 의 `block_rows`·`walk_gbm` 은 블록을 `sessions[last]` 로 **닫힌** 구간으로 고른다. 국장 휴장일이
    블록 이음매(한 블록의 끝과 다음 블록의 시작 사이)에 놓이면 그날 미장 행이 어느 블록에도 안 든다 —
    BF 는 반열림으로 그 날을 덮으므로, kit 이 고쳐지기 전에는 처리가 대조보다 며칠 더 넓다.
    **며칠 넓은 쪽이 이겼는지 진 건지 아무도 모른다.** 그래서 어긋나면 판정하지 않는다.
    """
    out = []
    for name, market in (("국장", "KR"), ("미장", "US")):
        a = {d for d in pred.loc[pred["market"] == market, "session"].unique()}
        b = {d for d in control.loc[control["market"] == market, "session"].unique()}
        if a != b:
            only_a = [str(d) for d in sorted(a - b)[:5]]
            only_b = [str(d) for d in sorted(b - a)[:5]]
            out.append(f"{name}: 처리에만 {only_a}({len(a - b)}일) · "
                       f"대조에만 {only_b}({len(b - a)}일)")
    return out


def evaluate_pooled(kit: ModuleType, pred: pd.DataFrame, books: dict[str, Any],
                    y: pd.DataFrame, *, control: pd.DataFrame | None = None) -> dict[str, float]:
    """`kit.evaluate_all`(시장별 + 합동) 을 판정용 한 겹으로 접는다. 시장별 값도 접두어를 붙여 남긴다 —
    합동 평균만 보면 한 시장이 다른 시장을 가리는 것을 못 본다(9/2 벤치마크 분해의 교훈)."""
    by, pooled = kit.evaluate_all(pred, books, y=y, control=control)
    out = dict(pooled)
    for market, m in by.items():
        out.update({f"{market}_{k}": v for k, v in m.items()})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--smoke", action="store_true", help="합성 자료로 배관만 확인")
    parser.add_argument("--precheck", action="store_true", help="실자료 seed 0 · 첫 5블록 · 겹침·격차만")
    parser.add_argument("--save", action="store_true", help="판정 1행을 창고에 적는다(예산 소진)")
    parser.add_argument("--seeds", type=int, nargs="*", default=list(SEEDS))
    parser.add_argument("--no-progress", action="store_true", help="trial_progress 기록을 끈다(기본은 적는다)")
    args = parser.parse_args(argv)

    if args.smoke:
        panel, feats, days = _synthetic()
        keys = query_keys(panel)
        bl = _blocks_fallback(days, min_train=40, block=10)
        pred, diags = walk_rank(panel, feats, keys, days, bl[:2], 0, _inner_split_fallback,
                                label="스모크", min_data=50, rounds=60)
        _, line = gap_lines(diags)
        print(f"스모크: 예측 {len(pred):,}행 · 쿼리 {pred.groupby(keys).ngroups}개\n{line}", flush=True)
        return 0

    kit = _kit()
    from quant_rl_trading.replay.clock import LiveClock
    from quant_rl_trading.store import Store

    panel, feats, _bundles, sessions = kit.load_full_panel(MARKETS, WINDOW)
    keys = query_keys(panel)          # ``_bundles`` 는 묶음 피처다 — 쿼리 그룹이 아니다
    bl = kit.blocks(sessions)
    print(f"FA {len(feats)}피처 · 판정 블록 {len(bl)} · 국장 세션 {len(sessions)} · "
          f"전 시장 세션 {len(kit.all_sessions(panel))}", flush=True)

    if args.precheck:
        head = bl[:5]
        ctrl = kit.require_controls(panel, seeds=(0,), smoke=len(head))
        pred, diags = walk_rank(panel, feats, keys, sessions, head, 0, kit.inner_split,
                                train_end=kit.train_end, rows_of=kit.block_rows)
        _, line = gap_lines(diags)
        print(f"등록 전 점검: 상위 {TOP} 겹침(C0 대비) {top_overlap(pred, ctrl['C0'][0]):.0%}\n{line}",
              flush=True)
        return 0

    if LiveClock().now().date() < MEASURE_FROM:
        print(f"본 측정은 {MEASURE_FROM} 이후. 지금은 --smoke / --precheck 만.", flush=True)
        return 2

    #: **굽기 관문**(kit, rc=4) — 원피처 캐시가 판정 창을 못 덮으면 FA 가 굶고, C1 도 같이 굶어 C0 과
    #: 거의 같아진다. 그 상태의 숫자는 기준 ①~⑤(대 C0)를 재는 것이 아니다. 수익을 안 보는 싼 검사다.
    #: `require_full_window`(rc=5)는 BF 에 해당 없다 — GBM 은 횡단면만 보고 시계열 창을 쓰지 않는다.
    kit.require_full_coverage(WINDOW, markets=MARKETS)

    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 BF — {PROTOCOL} (해시 {digest}) ===", flush=True)
    seeds = [int(s) for s in args.seeds]
    #: **대조군이 먼저다.** 없으면 rc=3 으로 멈춘다 — 대조 없이 판정 예산을 쓰지 않고,
    #: 시행 도구가 자기 GBM 을 새로 짜서 시행마다 다른 C0 을 만드는 길도 막는다.
    ctrl = kit.require_controls(panel, seeds=tuple(seeds))
    store = Store(root=Path("data"))
    #: ``panel`` 을 반드시 준다 — 미장 포트가 시행 AT 채택 M1 합성을 쓴다(빼면 evaluate 가 멈춘다).
    books = kit.market_books(store, sessions, MARKETS, panel=panel)
    y = panel[["entity_id", "session", "y5"]]
    res: dict[str, dict[int, dict[str, float]]] = {"BF1": {}, "BF2": {}, "C0": {}, "C1": {}}
    all_diags: list[pd.DataFrame] = []
    overlaps: list[float] = []
    for seed in seeds:
        c0p, c1p = ctrl["C0"][seed], ctrl["C1"][seed]
        res["C0"][seed] = evaluate_pooled(kit, c0p, books, y)
        res["C1"][seed] = evaluate_pooled(kit, c1p, books, y, control=c0p)
        bf1, diags = seed_preds(kit, panel, feats, keys, sessions, bl, seed,
                                store=None if args.no_progress else store, clock=LiveClock(),
                                n_seeds=len(seeds))
        #: 처리와 대조가 같은 (세션 × 시장)을 채점하는지 — 어긋나면 판정하지 않는다(rc=5).
        if (drift := session_sets_match(bf1, c0p)):
            print("처리와 대조의 채점 세션이 다르다 — 판정을 멈춘다:\n  " + "\n  ".join(drift),
                  flush=True)
            return MISMATCH_EXIT
        all_diags.append(diags)
        overlaps.append(top_overlap(bf1, c0p))
        res["BF1"][seed] = evaluate_pooled(kit, bf1, books, y, control=c0p)
        res["BF2"][seed] = evaluate_pooled(kit, rank_average(bf1, c1p, keys), books, y, control=c0p)
        del bf1

    _, line = gap_lines(pd.concat(all_diags, ignore_index=True))
    lines = [line,
             f"기록: 상위 {TOP} 겹침(시드별, C0 대비) {' / '.join(f'{o:.0%}' for o in overlaps)}"]
    verdicts = {}
    for variant in ("BF1", "BF2"):
        vlines, verdict = kit.judge(res[variant], res["C0"], res["C1"], label=f"{variant}: ")
        #: 변형이 둘이라 `판정:` 줄도 둘이다 — 어느 변형의 판정인지 줄에 적는다(로그 grep 이 구분하도록).
        lines += [ln.replace("판정:", f"판정({variant}):", 1) if ln.startswith("판정:") else ln
                  for ln in vlines]
        verdicts[variant] = verdict
    verdict_all = " / ".join(f"{k} {v}" for k, v in verdicts.items())
    lines.append(f"판정: {verdict_all}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        from tools.trial_ranker_kit import record
        record(store, entity="final-model-round-2026-10:BF", source="trial_final_lambdarank",
               family="ranker", digest=digest, verdict=verdict_all, lines=lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
