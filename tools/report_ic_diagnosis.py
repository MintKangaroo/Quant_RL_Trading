"""IC 재점검 결과를 낸다 — `tools/diagnose_ic.py cache` 가 구워 둔 것 위에서.

    .venv/bin/python tools/report_ic_diagnosis.py > /tmp/ic-report.txt

여섯 가지를 잰다. **전부 `analysts/ic.py` 의 타깃·검증을 그대로 쓴다** —
바꾸는 것은 무엇을 어느 표본에서 재는가 뿐이다.

1. 신호 종류별 평가 (횡단면 / 이벤트 부분집합 / 레짐 조건부)
2. 점수 상관행렬
3. IC_IR 과 t 값
4. risk 조사 — 자기상관 · 저변동성 팩터와의 거리
5. (flow_kr 구현 점검은 코드 읽기라 여기 없다)
6. chart·flow_kr 스윕 — EWMA × horizon × 유니버스
"""

from __future__ import annotations

import sys
from datetime import date, datetime  # noqa: F401
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from tools.diagnose_ic import (  # noqa: E402
    CACHE_DIR,
    EWMA_SPANS,
    HORIZONS,
    ewma_scores,
    newey_west_t,
    score_ic,
)

MARKET = "KR"
ANALYSTS = ("chart", "event", "flow_kr", "fundamental", "regime", "risk")


def load(name: str) -> pd.DataFrame:
    return pd.read_pickle(CACHE_DIR / f"{name}-{MARKET}.pkl")


def scores(name: str) -> pd.DataFrame:
    return load(f"scores-{name}")


def targets(horizon: int) -> pd.DataFrame:
    return pd.read_pickle(CACHE_DIR / f"targets-{MARKET}-h{horizon}.pkl")


def section(title: str) -> None:
    print(f"\n\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


# -----------------------------------------------------------------------------
# 1. 횡단면 분산 — regime 이 정말 시장수준 신호인가
# -----------------------------------------------------------------------------


def dispersion_table() -> pd.DataFrame:
    rows = []
    for name in ANALYSTS:
        frame = scores(name)
        grouped = frame.groupby("session")["score"]
        rows.append(
            {
                "analyst": name,
                "세션당 종목수": grouped.size().mean(),
                "횡단면 표준편차": grouped.std().mean(),
                "고유값 수": grouped.nunique().mean(),
                "동점 비율": 1.0 - (grouped.nunique().mean() / grouped.size().mean()),
                "전체 표준편차": frame["score"].std(),
            }
        )
    return pd.DataFrame(rows).set_index("analyst").round(4)


# -----------------------------------------------------------------------------
# 2. IC · IC_IR · t
# -----------------------------------------------------------------------------


def ic_table(horizon: int = 5) -> pd.DataFrame:
    target = targets(horizon)
    rows = []
    for name in ANALYSTS:
        stats = score_ic(scores(name), target, horizon=horizon)
        rows.append({"analyst": name, **stats})
    return pd.DataFrame(rows).set_index("analyst").round(4)


# -----------------------------------------------------------------------------
# 3. 이벤트 부분집합
# -----------------------------------------------------------------------------


def event_subset_ic(horizon: int = 5) -> dict[str, object]:
    """공시가 있는 종목·세션에서만 event IC 를 잰다.

    부분집합의 정의도 **그 시점에 알 수 있었던 것**으로 한다 — 공시의
    ``observed_at`` 이 그 세션 공표 시각보다 늦으면 그 종목은 그날 이벤트가
    없었던 것이다. 오늘 시점으로 고르면 정정본까지 보고 표본을 고르게 된다.
    """
    from quant_rl_trading.analysts.event import (
        DISTRESS_WINDOW_DAYS,
        FILING_SIGNS,
        FILING_WINDOW_DAYS,
    )

    load_env()
    store = build_store(None)
    now = LiveClock().now()
    market = Market(MARKET)
    policy = publication_policy(store, market, clock=LiveClock())

    frame = scores("event")
    sessions = sorted(frame["session"].unique())
    span = (max(sessions) - min(sessions)).days + DISTRESS_WINDOW_DAYS + 10

    documents = store.get(
        "documents", as_of=now, lookback=span,
        columns=["doc_type", "doc_id", "observed_at"],
    )
    documents = documents[
        documents["entity_id"].astype(str).str.startswith(f"{MARKET}:")
        & documents["doc_type"].isin(FILING_SIGNS)
    ].copy()
    documents["observed_at"] = pd.to_datetime(documents["observed_at"], utc=True)
    documents["valid_from"] = pd.to_datetime(documents["valid_from"], utc=True)

    pairs: list[dict[str, object]] = []
    for session in sessions:
        as_of = policy.for_session(session)
        window = FILING_WINDOW_DAYS
        visible = documents[documents["observed_at"] <= as_of]
        recent = visible[
            (visible["doc_type"] == "distress")
            & (visible["valid_from"] >= as_of - pd.Timedelta(days=DISTRESS_WINDOW_DAYS))
        ]
        others = visible[
            (visible["doc_type"] != "distress")
            & (visible["valid_from"] >= as_of - pd.Timedelta(days=window))
        ]
        entities = set(recent["entity_id"]) | set(others["entity_id"])
        pairs.extend({"session": session, "entity_id": e} for e in entities)

    subset = pd.DataFrame(pairs)
    target = targets(horizon)
    full = score_ic(frame, target, horizon=horizon)
    if subset.empty:
        return {"subset_rows": 0, "full": full, "subset": None}

    narrowed = frame.merge(subset, on=["entity_id", "session"], how="inner")
    on_subset = score_ic(narrowed, target, horizon=horizon)
    complement = frame.merge(
        subset.assign(_flag=1), on=["entity_id", "session"], how="left"
    )
    complement = complement[complement["_flag"].isna()].drop(columns=["_flag"])
    off_subset = score_ic(complement, target, horizon=horizon)
    coverage = subset.groupby("session").size()
    return {
        "subset_rows": len(subset),
        "coverage_mean": float(coverage.mean()),
        "full": full,
        "subset": on_subset,
        "complement": off_subset,
    }


# -----------------------------------------------------------------------------
# 4. 레짐 — IC 가 아니라 조건부 수익
# -----------------------------------------------------------------------------


def forward_return_panel(horizon: int = 5) -> pd.DataFrame:
    """실제 전방수익률(%). 타깃의 횡단면 z 와 달리 단위가 있다."""
    from quant_rl_trading.store.prices import PRICES, adjust, drop_dead_sessions
    from quant_rl_trading.dashboard.services import data_quality as dq

    load_env()
    store = build_store(None)
    now = LiveClock().now()
    calendar = pd.read_pickle(CACHE_DIR / f"calendar-{MARKET}.pkl")["session"]
    span = (max(calendar) - min(calendar)).days + 120

    windows = dq.iter_windows(
        store, PRICES, as_of=now, lookback=span, window=32,
        columns=["close", "adj_factor"], market=MARKET,
    )
    frames = [
        drop_dead_sessions(chunk).loc[:, ["entity_id", "valid_from", "close", "adj_factor"]]
        for chunk in windows
        if not chunk.empty
    ]
    prices = pd.concat(frames, ignore_index=True)
    prices = prices.drop_duplicates(subset=["entity_id", "valid_from"], keep="last")
    prices = adjust(prices)
    prices["session"] = prices["valid_from"].dt.date
    forward = ic.forward_returns(prices, horizon=horizon)
    return forward.loc[:, ["entity_id", "session", "forward_return"]]


def regime_conditional(horizon: int = 5, quantile: float = 0.2) -> pd.DataFrame:
    """레짐별 조건부 수익. 상위분위 − 유니버스 평균, 단위는 %.

    IC 로 재지 않는 이유: regime 은 상태에 따라 **점수의 부호 자체를 뒤집는다**
    (`REGIME_WEIGHTS`). 전 구간 IC 하나로 재면 서로 다른 방향의 구간이 상쇄돼
    0 근처로 수렴하고, 그것은 "예측력이 없다" 가 아니라 "물음이 틀렸다" 다.
    """
    state = pd.read_pickle(CACHE_DIR / f"regime-state-{MARKET}.pkl")
    frame = scores("regime").merge(forward_return_panel(horizon), on=["entity_id", "session"])
    frame = frame.merge(state, on="session", how="left")

    rows = []
    for name, group in frame.groupby("state"):
        per_session = []
        for session, day in group.groupby("session"):
            if len(day) < 20:
                continue
            top = day[day["score"] >= day["score"].quantile(1 - quantile)]
            bottom = day[day["score"] <= day["score"].quantile(quantile)]
            per_session.append(
                {
                    "top": top["forward_return"].mean(),
                    "bottom": bottom["forward_return"].mean(),
                    "universe": day["forward_return"].mean(),
                }
            )
        if not per_session:
            continue
        panel = pd.DataFrame(per_session)
        excess = panel["top"] - panel["universe"]
        rows.append(
            {
                "state": name,
                "세션수": len(panel),
                "상위20% 수익%": panel["top"].mean() * 100,
                "하위20% 수익%": panel["bottom"].mean() * 100,
                "유니버스 수익%": panel["universe"].mean() * 100,
                "초과(상위-유니버스)%": excess.mean() * 100,
                "t(초과)": newey_west_t(excess, lag=horizon - 1),
            }
        )
    return pd.DataFrame(rows).set_index("state").round(4)


# -----------------------------------------------------------------------------
# 5. 상관행렬
# -----------------------------------------------------------------------------


def score_panel() -> pd.DataFrame:
    """(entity_id, session) × analyst 점수 행렬."""
    merged: pd.DataFrame | None = None
    for name in ANALYSTS:
        frame = scores(name).rename(columns={"score": name})
        merged = frame if merged is None else merged.merge(
            frame, on=["entity_id", "session"], how="outer"
        )
    assert merged is not None
    return merged


def cross_sectional_correlation(panel: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """일별 횡단면 순위상관의 시간 평균."""
    mats = []
    for _, day in panel.groupby("session"):
        sub = day[names].dropna(how="all")
        if len(sub) < 30:
            continue
        mats.append(sub.corr(method="spearman").to_numpy())
    if not mats:
        return pd.DataFrame()
    return pd.DataFrame(np.nanmean(mats, axis=0), index=names, columns=names).round(3)


# -----------------------------------------------------------------------------
# 6. risk 조사
# -----------------------------------------------------------------------------


def risk_autocorrelation(name: str = "risk", lags: tuple[int, ...] = (1, 5, 20)) -> dict[int, float]:
    frame = scores(name).sort_values(["entity_id", "session"])
    out = {}
    for lag in lags:
        shifted = frame.groupby("entity_id")["score"].shift(lag)
        pair = pd.DataFrame({"now": frame["score"], "past": shifted}).dropna()
        out[lag] = float(pair["now"].corr(pair["past"], method="spearman"))
    return out


# -----------------------------------------------------------------------------
# 7. 스윕
# -----------------------------------------------------------------------------


def sweep(name: str) -> pd.DataFrame:
    base = scores(name)
    liquidity = pd.read_pickle(CACHE_DIR / f"liquidity-{MARKET}.pkl")
    load_env()
    store = build_store(None)
    floor = float(store.config("universe.min_turnover_20d_kr", as_of=LiveClock().now()))
    tradable = liquidity[liquidity["turnover_20d"] >= floor][["entity_id", "session"]]

    rows = []
    for span in EWMA_SPANS:
        smoothed = ewma_scores(base, span)
        for universe_name, frame in (
            ("전종목", smoothed),
            ("매매유니버스", smoothed.merge(tradable, on=["entity_id", "session"])),
        ):
            for horizon in HORIZONS:
                stats = score_ic(frame, targets(horizon), horizon=horizon)
                rows.append(
                    {
                        "EWMA": "없음" if span is None else f"{span}일",
                        "유니버스": universe_name,
                        "horizon": horizon,
                        "IC": round(stats["ic"], 4),
                        "IC_IR": round(stats["ic_ir"], 4),
                        "t(NW)": round(stats["t"], 2),
                        "일수": int(stats["days"]),
                    }
                )
    return pd.DataFrame(rows)


def main() -> int:
    section("1. 횡단면 분산 — 어떤 신호가 종목을 실제로 가르는가")
    print(dispersion_table().to_string())

    section("2. IC · IC_IR · t (horizon=5, 운영값)")
    print(ic_table().to_string())
    print("\n  t(NW): Newey-West lag=horizon-1. t_naive: 겹침 무시.")

    section("3. event — 이벤트가 있는 부분집합에서만")
    result = event_subset_ic()
    print(f"  부분집합 {result['subset_rows']:,}쌍 · 세션당 평균 {result.get('coverage_mean', 0):.1f}종목")
    for label in ("full", "subset", "complement"):
        stats = result.get(label)
        if stats:
            print(
                f"  {label:11s} IC {stats['ic']:+.4f} · t {stats['t']:+.2f} "
                f"· {int(stats['rows']):,}행"
            )

    section("4. regime — IC 가 아니라 레짐별 조건부 수익 (5일 보유, %)")
    print(regime_conditional().to_string())

    section("5. 점수 상관행렬 — 일별 횡단면 순위상관의 시간 평균")
    panel = score_panel()
    print("[3종: event · fundamental · risk]")
    print(cross_sectional_correlation(panel, ["event", "fundamental", "risk"]).to_string())
    print("\n[6종 전체]")
    print(cross_sectional_correlation(panel, list(ANALYSTS)).to_string())

    section("6. risk — 자기상관과 저변동성 거리")
    print("  점수 자기상관(Spearman):", risk_autocorrelation())

    section("7. 스윕 — chart")
    print(sweep("chart").to_string(index=False))

    section("8. 스윕 — flow_kr")
    print(sweep("flow_kr").to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
