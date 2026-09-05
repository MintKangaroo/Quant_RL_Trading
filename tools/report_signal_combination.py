"""신호 결합 재설계 — 상관 기반. `tools/diagnose_ic.py cache` 위에서 돈다.

    .venv/bin/python tools/report_signal_combination.py > /tmp/combination.txt

여섯 가지를 잰다.

1. 6종 점수 상관행렬 — 일별 횡단면 상관의 시간 평균 + 롤링 250일 시변성
2. 일별 IC 계열끼리의 시계열 상관 — 같은 시기에 죽는 쌍이 있는가
3. 결합 IC 세 방식 — 현행 · 군집+shrinkage · 순차 직교화
4. 직교화 후 잔차 IC
5. 게이트를 "한계 기여" 로 바꾸는 안
6. chart 내부 피처 군집화

## 가중치는 학습 폴드에서만 만든다

세 방식 중 둘은 **데이터에서 가중치를 정한다**. 전 구간 IC 로 가중치를 만들고
같은 구간에서 채점하면 그 결합 IC 는 미래를 본 값이고, 반드시 현행 방식보다
좋게 나온다 — 자유도가 더 많기 때문이다. 그래서 `ic.purged_folds` 로 갈라
**학습 폴드에서 가중치를 만들고 검증 폴드에서만 채점한다.** 타깃 정의와 폴드
경계는 `analysts/ic.py` 것을 그대로 쓴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.selector.constraints import CONSTRAINT_ANALYSTS  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from tools.diagnose_ic import CACHE_DIR, newey_west_t  # noqa: E402
from tools.report_ic_diagnosis import (  # noqa: E402
    ANALYSTS,
    MARKET,
    score_panel,
    scores,
    section,
    targets,
)

HORIZON = 5

#: 알파 결합에 참여할 수 있는 Analyst. `risk` 는 제약이라 여기 없다
#: (`selector/constraints.py`, 태스크 #32).
ALPHA_ANALYSTS = tuple(name for name in ANALYSTS if name not in CONSTRAINT_ANALYSTS)

#: 군집을 자르는 상관 임계. 이 위로 붙은 것은 한 덩어리로 본다.
CLUSTER_THRESHOLD = 0.6

#: 군집 간 가중을 IC 비례와 균등 사이 어디에 둘지. 1 이면 순수 IC 비례.
SHRINKAGE = 0.5


# -----------------------------------------------------------------------------
# 공통 — 패널
# -----------------------------------------------------------------------------


def merged_panel() -> pd.DataFrame:
    """(session, entity) × 점수 6종 + 타깃."""
    panel = score_panel()
    target = targets(HORIZON)
    return panel.merge(target, on=["entity_id", "session"], how="inner")


def per_session_z(frame: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """세션 안에서 각 점수를 순위 z 로. 결측은 결측으로 둔다."""
    out = frame.copy()
    for name in names:
        grouped = out.groupby("session")[name]
        ranked = grouped.rank(pct=True)
        out[name] = (ranked - 0.5) * np.sqrt(12.0)
    return out


def daily_ic_of(frame: pd.DataFrame, column: str) -> pd.Series:
    sub = frame.loc[:, ["session", column, "target"]].dropna()
    sub = sub.rename(columns={column: "score"})
    if sub.empty:
        return pd.Series(dtype=float)
    return ic.daily_ic(sub)


def summarize(series: pd.Series, label: str) -> dict[str, object]:
    return {
        "방식": label,
        "IC": round(float(series.mean()), 4) if len(series) else np.nan,
        "IC_IR": round(float(series.mean() / series.std()), 4) if len(series) > 1 else np.nan,
        "t(NW)": round(newey_west_t(series, lag=HORIZON - 1), 2),
        "일수": int(len(series)),
    }


# -----------------------------------------------------------------------------
# 1. 상관 — 수준과 시변성
# -----------------------------------------------------------------------------


def daily_correlations(panel: pd.DataFrame, names: list[str]) -> dict[str, pd.Series]:
    """쌍별 일별 횡단면 순위상관 계열."""
    series: dict[str, list[tuple[object, float]]] = {}
    for session, day in panel.groupby("session"):
        sub = day[names]
        if len(sub) < 30:
            continue
        corr = sub.corr(method="spearman")
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                value = corr.loc[left, right]
                if np.isfinite(value):
                    series.setdefault(f"{left}~{right}", []).append((session, float(value)))
    return {
        key: pd.Series([v for _, v in pairs], index=[s for s, _ in pairs]).sort_index()
        for key, pairs in series.items()
    }


def rolling_view(series: dict[str, pd.Series], window: int = 250) -> pd.DataFrame:
    rows = []
    for key, values in series.items():
        rolled = values.rolling(window, min_periods=max(60, window // 4)).mean().dropna()
        if rolled.empty:
            continue
        rows.append(
            {
                "쌍": key,
                "전체평균": round(float(values.mean()), 3),
                f"롤링{window} 최소": round(float(rolled.min()), 3),
                f"롤링{window} 최대": round(float(rolled.max()), 3),
                "진폭": round(float(rolled.max() - rolled.min()), 3),
                "관측일": len(values),
            }
        )
    return pd.DataFrame(rows).sort_values("전체평균", key=abs, ascending=False)


# -----------------------------------------------------------------------------
# 2. IC 계열 상관
# -----------------------------------------------------------------------------


def ic_series_correlation(panel: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    series = {name: daily_ic_of(panel, name) for name in names}
    frame = pd.DataFrame(series).dropna(how="all")
    return frame.corr().round(3)


# -----------------------------------------------------------------------------
# 3. 결합 세 방식 — 가중치는 학습 폴드에서만
# -----------------------------------------------------------------------------


def train_ic(panel: pd.DataFrame, names: list[str], sessions: set) -> dict[str, float]:
    train = panel[panel["session"].isin(sessions)]
    return {name: float(daily_ic_of(train, name).mean()) for name in names}


def train_correlation(panel: pd.DataFrame, names: list[str], sessions: set) -> pd.DataFrame:
    train = panel[panel["session"].isin(sessions)]
    mats = []
    for _, day in train.groupby("session"):
        sub = day[names]
        if len(sub) < 30:
            continue
        mats.append(sub.corr(method="spearman").to_numpy())
    if not mats:
        return pd.DataFrame(np.eye(len(names)), index=names, columns=names)
    return pd.DataFrame(np.nanmean(mats, axis=0), index=names, columns=names).fillna(0.0)


def cluster_by_correlation(corr: pd.DataFrame, threshold: float) -> list[list[str]]:
    """단일 연결 군집. 상관이 임계 위로 이어지면 한 덩어리다.

    scipy 를 새로 들이지 않는다 — 6개짜리 문제라 union-find 로 충분하다.
    """
    names = list(corr.index)
    parent = {name: name for name in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, left in enumerate(names):
        for right in names[i + 1:]:
            if abs(float(corr.loc[left, right])) >= threshold:
                parent[find(left)] = find(right)

    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    return list(groups.values())


def combine_current(panel: pd.DataFrame, names: list[str], train: set, threshold: float) -> pd.Series:
    """(a) 현행 — IC 게이트 통과분만, 신뢰도(롤링 IC) 가중.

    `selector/combine.py` 의 `Σ(w·score·conf)/Σ(w·conf)` 다. w 는 0/1 이고
    conf 는 `ic.rolling_confidence`(= 최근 IC, 음수면 0)라, 결과적으로
    **통과분 안에서 IC 비례 가중**이 된다. 여기서는 학습 폴드 IC 를 conf 로
    쓴다 — 세션마다 다시 재는 것과 값은 다르지만 성질(최근 성적 비례)은 같다.
    """
    ics = train_ic(panel, names, train)
    passing = {name: max(0.0, value) for name, value in ics.items() if value >= threshold}
    if not passing:
        return pd.Series(np.nan, index=panel.index)
    weighted = pd.Series(0.0, index=panel.index)
    share = pd.Series(0.0, index=panel.index)
    for name, conf in passing.items():
        column = panel[name]
        present = column.notna()
        weighted[present] += conf * column[present]
        share[present] += conf
    return (weighted / share.replace(0.0, np.nan))


def combine_clustered(panel: pd.DataFrame, names: list[str], train: set) -> pd.Series:
    """(b) 군집 내 평균 → 군집 간 IC 가중(shrinkage)."""
    corr = train_correlation(panel, names, train)
    clusters = cluster_by_correlation(corr, CLUSTER_THRESHOLD)
    ics = train_ic(panel, names, train)

    cluster_scores: list[pd.Series] = []
    cluster_ic: list[float] = []
    for group in clusters:
        block = panel[group]
        cluster_scores.append(block.mean(axis=1, skipna=True))
        cluster_ic.append(float(np.mean([max(0.0, ics[name]) for name in group])))

    total = sum(cluster_ic)
    count = len(cluster_ic)
    if total <= 0:
        weights = [1.0 / count] * count
    else:
        weights = [
            SHRINKAGE * (value / total) + (1.0 - SHRINKAGE) / count for value in cluster_ic
        ]

    weighted = pd.Series(0.0, index=panel.index)
    share = pd.Series(0.0, index=panel.index)
    for weight, series in zip(weights, cluster_scores, strict=True):
        present = series.notna()
        weighted[present] += weight * series[present]
        share[present] += weight
    return weighted / share.replace(0.0, np.nan)


def orthogonalize(panel: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """순차 직교화. 세션마다 앞선 것들에 대해 회귀하고 잔차를 남긴다."""
    out = panel.copy()
    for index, name in enumerate(order):
        if index == 0:
            continue
        priors = order[:index]
        residuals = pd.Series(np.nan, index=panel.index)
        for _, day in out.groupby("session"):
            block = day[[name, *priors]].dropna()
            if len(block) < 30:
                continue
            y = block[name].to_numpy(dtype=float)
            x = np.column_stack(
                [np.ones(len(block)), block[priors].to_numpy(dtype=float)]
            )
            try:
                beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            except np.linalg.LinAlgError:
                continue
            residuals.loc[block.index] = y - x @ beta
        out[name] = residuals
    return out


def combine_orthogonal(panel: pd.DataFrame, names: list[str], train: set) -> pd.Series:
    """(c) 순차 직교화 → 잔차 IC 기준 가중."""
    ics = train_ic(panel, names, train)
    order = sorted(names, key=lambda name: ics[name], reverse=True)
    residual_panel = orthogonalize(panel, order)
    residual_ic = train_ic(residual_panel, order, train)

    weighted = pd.Series(0.0, index=panel.index)
    share = pd.Series(0.0, index=panel.index)
    for name in order:
        weight = max(0.0, residual_ic[name])
        if weight <= 0:
            continue
        column = residual_panel[name]
        present = column.notna()
        weighted[present] += weight * column[present]
        share[present] += weight
    return weighted / share.replace(0.0, np.nan)


def evaluate_methods(panel: pd.DataFrame, names: list[str], threshold: float) -> pd.DataFrame:
    sessions = sorted(panel["session"].unique())
    folds = list(ic.purged_folds(sessions, n_splits=5, horizon=HORIZON))
    collected: dict[str, list[pd.Series]] = {"현행": [], "군집+shrinkage": [], "순차 직교화": []}

    for train_sessions, test_sessions in folds:
        train, test = set(train_sessions), set(test_sessions)
        block = panel.copy()
        built = {
            "현행": combine_current(block, names, train, threshold),
            "군집+shrinkage": combine_clustered(block, names, train),
            "순차 직교화": combine_orthogonal(block, names, train),
        }
        for label, series in built.items():
            scored = block.assign(combined=series)
            scored = scored[scored["session"].isin(test)]
            daily = daily_ic_of(scored, "combined")
            if not daily.empty:
                collected[label].append(daily)

    rows = []
    for label, parts in collected.items():
        if not parts:
            continue
        series = pd.concat(parts).sort_index()
        rows.append(summarize(series, label))
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# 4. 잔차 IC
# -----------------------------------------------------------------------------


def residual_ic_table(panel: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    rows = []
    for name in names:
        others = [other for other in names if other != name]
        residual = pd.Series(np.nan, index=panel.index)
        for _, day in panel.groupby("session"):
            block = day[[name, *others]].dropna()
            if len(block) < 30:
                continue
            y = block[name].to_numpy(dtype=float)
            x = np.column_stack([np.ones(len(block)), block[others].to_numpy(dtype=float)])
            beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            residual.loc[block.index] = y - x @ beta
        scored = panel.assign(resid=residual)
        alone = daily_ic_of(panel, name)
        left = daily_ic_of(scored, "resid")
        rows.append(
            {
                "analyst": name,
                "단독 IC": round(float(alone.mean()), 4),
                "단독 t": round(newey_west_t(alone, lag=HORIZON - 1), 2),
                "잔차 IC": round(float(left.mean()), 4),
                "잔차 t": round(newey_west_t(left, lag=HORIZON - 1), 2),
                "설명된 비율": round(1.0 - float(left.std() / alone.std()), 3)
                if alone.std() > 0 else np.nan,
            }
        )
    return pd.DataFrame(rows).set_index("analyst")


# -----------------------------------------------------------------------------
# 5. 한계 기여 게이트
# -----------------------------------------------------------------------------


def combine_equal(panel: pd.DataFrame, names: list[str]) -> pd.Series:
    """동일가중 결합. 있는 점수만 평균한다.

    한계 기여를 잴 때 IC 가중을 쓰면 **음수 IC 인 Analyst 는 가중치 0 이 되어
    조합을 바꾸지 못한다** — 실제로 첫 측정에서 chart·flow_kr 의 ΔIC 가 정확히
    0.00000 · t NaN 으로 나왔다. 그건 "기여가 없다" 가 아니라 **못 쟀다** 이다.
    동일가중이면 후보가 반드시 조합을 움직이므로 증분을 실제로 잴 수 있다.

    음수 가중(신호를 뒤집어 쓰기)은 쓰지 않는다. 표본에 맞춰 부호를 고르는
    것이고, 그건 이 프로젝트가 `ic.py` 독스트링에서 이미 금지한 일이다.
    """
    block = panel[names]
    return block.mean(axis=1, skipna=True)


def marginal_contribution(panel: pd.DataFrame, names: list[str], threshold: float) -> pd.DataFrame:
    """각 Analyst 를 기존 조합에 넣었을 때 결합 IC 가 얼마나 오르는가.

    **폴드 밖에서 잰다.** 넣고 뺀 두 조합을 같은 검증 폴드에서 채점하고,
    차이의 계열로 t 를 낸다 — 두 IC 를 따로 낸 뒤 빼면 잡음이 두 배가 된다.

    기준 조합은 **학습 폴드 IC** 로 고른다(전 구간으로 고르면 검증 폴드를 본다).
    결합은 동일가중이다 — 이유는 `combine_equal` 참조.
    """
    sessions = sorted(panel["session"].unique())
    folds = list(ic.purged_folds(sessions, n_splits=5, horizon=HORIZON))

    rows = []
    for candidate in names:
        deltas: list[pd.Series] = []
        in_base = 0
        for train_sessions, test_sessions in folds:
            train, test = set(train_sessions), set(test_sessions)
            ics = train_ic(panel, names, train)
            base_set = [name for name in names if ics[name] >= threshold]
            if candidate in base_set:
                in_base += 1
            with_set = sorted(set(base_set) | {candidate})
            without_set = [name for name in base_set if name != candidate]
            if not without_set:
                continue
            scored = panel.assign(
                with_c=combine_equal(panel, with_set),
                without_c=combine_equal(panel, without_set),
            )
            scored = scored[scored["session"].isin(test)]
            left = daily_ic_of(scored, "with_c")
            right = daily_ic_of(scored, "without_c")
            joined = pd.concat([left.rename("a"), right.rename("b")], axis=1).dropna()
            if not joined.empty:
                deltas.append(joined["a"] - joined["b"])
        if not deltas:
            rows.append({"analyst": candidate, "ΔIC": np.nan, "t(ΔIC)": np.nan, "일수": 0})
            continue
        delta = pd.concat(deltas).sort_index()
        rows.append(
            {
                "analyst": candidate,
                "기준 조합인 폴드": f"{in_base}/{len(folds)}",
                "ΔIC": round(float(delta.mean()), 5),
                "t(ΔIC)": round(newey_west_t(delta, lag=HORIZON - 1), 2),
                "일수": len(delta),
            }
        )
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# 6. chart 내부 피처
# -----------------------------------------------------------------------------


def chart_feature_study() -> None:
    path = CACHE_DIR / f"features-chart-{MARKET}.pkl"
    if not path.exists():
        print("  chart 피처 캐시가 없다 — `diagnose_ic.py cache-extra` 를 먼저 돌릴 것")
        return
    from quant_rl_trading.analysts.chart import WEIGHTS

    features = pd.read_pickle(path)
    names = [name for name in WEIGHTS if name in features.columns]
    target = targets(HORIZON)
    panel = features.merge(target, on=["entity_id", "session"], how="inner")

    mats = []
    for _, day in panel.groupby("session"):
        sub = day[names]
        if len(sub) < 30:
            continue
        mats.append(sub.corr(method="spearman").to_numpy())
    corr = pd.DataFrame(np.nanmean(mats, axis=0), index=names, columns=names).round(3)
    print("[피처 상관행렬 — 일별 횡단면의 시간 평균]")
    print(corr.to_string())

    clusters = cluster_by_correlation(corr, CLUSTER_THRESHOLD)
    print(f"\n[군집 (|상관| ≥ {CLUSTER_THRESHOLD})]")
    for group in clusters:
        print("  ", " + ".join(group))

    print("\n[피처별 단독 IC]")
    feature_ic = {}
    for name in names:
        series = daily_ic_of(panel, name)
        feature_ic[name] = float(series.mean())
        print(f"  {name:16s} IC {series.mean():+.4f} · t {newey_west_t(series, lag=HORIZON - 1):+.2f}")

    # 원본 · 대표 1개 · 군집 PCA 1축
    def score_from(frame: pd.DataFrame, columns: dict[str, float]) -> pd.Series:
        total = sum(abs(w) for w in columns.values())
        out = pd.Series(0.0, index=frame.index)
        for name, weight in columns.items():
            out += weight * frame[name].fillna(0.0)
        return out / total if total else out

    zed = per_session_z(panel, names)
    variants: dict[str, pd.Series] = {"원본(가중합)": score_from(zed, dict(WEIGHTS))}

    representative: dict[str, float] = {}
    for group in clusters:
        best = max(group, key=lambda name: abs(feature_ic[name]))
        representative[best] = sum(WEIGHTS[name] for name in group)
    variants["군집 대표 1개"] = score_from(zed, representative)

    pca_frame = zed.copy()
    pca_columns: dict[str, float] = {}
    for index, group in enumerate(clusters):
        block = pca_frame[group].fillna(0.0).to_numpy(dtype=float)
        if len(group) == 1:
            axis = block[:, 0]
        else:
            centered = block - block.mean(axis=0)
            _, _, vt = np.linalg.svd(centered, full_matrices=False)
            axis = centered @ vt[0]
            # 부호를 원래 가중치 방향에 맞춘다 — PCA 축의 부호는 임의다.
            reference = sum(WEIGHTS[name] for name in group)
            if np.corrcoef(axis, block @ np.array([WEIGHTS[n] for n in group]))[0, 1] < 0:
                axis = -axis
            axis = axis * np.sign(reference if reference else 1.0)
        column = f"pca_{index}"
        pca_frame[column] = axis
        pca_columns[column] = sum(abs(WEIGHTS[name]) for name in group)
    variants["군집 PCA 1축"] = score_from(pca_frame, pca_columns)

    print("\n[축소 방식별 IC]")
    rows = []
    for label, series in variants.items():
        scored = panel.assign(v=series.to_numpy())
        daily = daily_ic_of(scored, "v")
        rows.append(summarize(daily, label))
    print(pd.DataFrame(rows).to_string(index=False))

    # 기여 안정성 — chart 의 결합은 고정 가중 선형합이라 기여가 정확히 계산된다.
    print("\n[기여 안정성 — |가중치 × 피처| 의 몫, 세션별 표준편차]")
    contribution = pd.DataFrame(index=panel.index)
    for name in names:
        contribution[name] = (WEIGHTS[name] * zed[name].fillna(0.0)).abs()
    shares = contribution.div(contribution.sum(axis=1).replace(0.0, np.nan), axis=0)
    shares["session"] = panel["session"].to_numpy()
    per_session = shares.groupby("session").mean()
    print(
        pd.DataFrame(
            {"평균 몫": per_session.mean().round(3), "세션간 표준편차": per_session.std().round(3)}
        ).to_string()
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--section", type=int, nargs="+", default=[1, 2, 3, 4, 5, 6],
        help="다시 돌릴 절만 고른다. 캐시 위에서 도니까 한 절만 재실행이 싸다.",
    )
    args = parser.parse_args(argv)
    wanted = set(args.section)

    load_env()
    store = build_store(None)
    threshold = float(store.config("analyst.ic_threshold", as_of=LiveClock().now()))

    panel = merged_panel()
    names = list(ALPHA_ANALYSTS)
    zed = per_session_z(panel, list(ANALYSTS))

    section("0. 표본")
    print(f"  세션 {panel['session'].nunique()}개 · 행 {len(panel):,} · 게이트 {threshold}")
    print(f"  알파 결합 대상: {names}  (risk 는 제약이라 제외)")

    if 1 in wanted:
        section("1. 점수 상관 — 일별 횡단면의 시간 평균과 롤링 250일")
        series = daily_correlations(zed, list(ANALYSTS))
        print(rolling_view(series).to_string(index=False))

    if 2 in wanted:
        section("2. 일별 IC 계열끼리의 상관 — 같은 시기에 죽는 쌍")
        print(ic_series_correlation(zed, list(ANALYSTS)).to_string())

    if 3 in wanted:
        section("3. 결합 IC 세 방식 (검증 폴드에서만 채점)")
        print(evaluate_methods(zed, names, threshold).to_string(index=False))

    if 4 in wanted:
        section("4. 직교화 후 잔차 IC")
        print(residual_ic_table(zed, list(ANALYSTS)).to_string())

    if 5 in wanted:
        section("5. 한계 기여 — 기존 조합에 넣었을 때의 ΔIC")
        print(marginal_contribution(zed, names, threshold).to_string(index=False))

    if 6 in wanted:
        section("6. chart 내부 피처 군집화")
        chart_feature_study()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
