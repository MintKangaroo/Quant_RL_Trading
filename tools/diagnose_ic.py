"""IC 재점검 — 신호 종류별로 평가 방법을 가른다.

    .venv/bin/python tools/diagnose_ic.py cache --sessions 300
    .venv/bin/python tools/diagnose_ic.py report

`measure_ic.py` 는 **모든 Analyst 를 같은 자로 잰다**: 일별 횡단면 순위상관.
그 자가 맞는 신호가 있고 안 맞는 신호가 있다.

- **횡단면**(chart·flow_kr·fundamental·risk) — 지금 자가 맞다
- **이벤트성**(event) — 대부분의 종목·세션에는 이벤트가 없다. 전종목에서 재면
  이벤트가 없는 종목의 점수(maturity 뿐)가 표본의 99%를 채우고, 이벤트의
  예측력은 그 잡음에 희석된다. **이벤트가 있는 부분집합에서만 잰다**
- **시장수준**(regime) — 상태 판정이 본체이고 종목 축 점수는 그 상태를 옮긴
  것이다. 레짐별 조건부 수익으로 평가한다

**이 도구는 IC 를 올리려고 만든 것이 아니다.** 타깃 정의(전방 5일 초과수익의
횡단면 z)와 검증(purged K-fold + embargo)은 `analysts/ic.py` 것을 그대로
쓴다. 바꾸는 것은 **무엇을 어느 표본에서 재는가** 뿐이다.

두 단계로 나눈 이유는 비용이다. 점수를 만드는 데 Analyst 당 300세션 13분이
드는데, 스윕은 같은 점수를 수십 번 다시 채점한다. **한 번 만들어 캐시에 두고
채점만 반복한다.**
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.analysts.base import to_scores_frame  # noqa: E402
from quant_rl_trading.analysts.regime import RegimeAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from tools.measure_ic import ANALYSTS, score_sessions, target_span  # noqa: E402

#: 캐시 위치. ``data/`` 는 .gitignore 대상이라 커밋되지 않는다.
CACHE_DIR = REPO_ROOT / "data" / "_diag"

#: 스윕에서 볼 보유 기간(거래일). 5일이 운영값이다 (agents.md §10).
HORIZONS = (1, 5, 10, 20)

#: 스윕에서 볼 점수 평활(EWMA span, 거래일). None 은 평활 없음.
EWMA_SPANS = (None, 3, 5)


# -----------------------------------------------------------------------------
# 캐시
# -----------------------------------------------------------------------------


def calendar_for(store: Store, *, market: Market, sessions: int, as_of: datetime) -> list[date]:
    """측정에 쓸 세션 목록. ``measure_ic.measure`` 와 **같은 방식으로 고른다.**

    다르게 고르면 여기서 나온 숫자를 운영 IC 와 나란히 놓을 수 없다.
    """
    targets = ic.build_targets(
        store, as_of=as_of, lookback=target_span(sessions), market=str(market)
    )
    if targets.empty:
        raise SystemExit("타깃이 비었다. prices 백필 확인.")
    available = sorted(targets["session"].unique())
    window = available[-sessions:]
    chosen = set(window)
    return [day for day in trading_days(market, window[0], window[-1]) if day in chosen]


def cache_targets(
    store: Store, *, market: Market, sessions: int, as_of: datetime, out: Path
) -> None:
    """horizon 별 타깃. **정의는 손대지 않는다** — ``horizon`` 인자만 바꾼다."""
    for horizon in HORIZONS:
        path = out / f"targets-{market}-h{horizon}.pkl"
        if path.exists():
            print(f"  타깃 h={horizon}: 이미 있다 — 건너뛴다")
            continue
        frame = ic.build_targets(
            store,
            as_of=as_of,
            lookback=target_span(sessions),
            horizon=horizon,
            market=str(market),
        )
        frame.to_pickle(path)
        print(f"  타깃 h={horizon}: {len(frame):,}행 → {path.name}", flush=True)


def cache_scores(
    name: str,
    store: Store,
    *,
    market: Market,
    calendar: list[date],
    out: Path,
) -> None:
    """Analyst 를 세션마다 돌려 점수를 굽는다. 있으면 건너뛴다."""
    path = out / f"scores-{name}-{market}.pkl"
    if path.exists():
        print(f"=== {name}: 캐시가 이미 있다 — 건너뛴다 ({path.name})", flush=True)
        return
    print(f"=== {name}: {len(calendar)}세션", flush=True)
    analyst = ANALYSTS[name](store, LiveClock(), market=market)
    frame = score_sessions(analyst, store, calendar, market, verbose=True)
    frame.to_pickle(path)
    print(f"    {len(frame):,}행 → {path.name}", flush=True)


def cache_context(
    store: Store, *, market: Market, calendar: list[date], out: Path
) -> None:
    """세션별 부가 정보 — 레짐 상태와 매매 유니버스.

    둘 다 그 시점 ``as_of`` 로 다시 판정한다. 오늘 값으로 과거를 채우면 레짐은
    미래를 보고, 유니버스는 상장폐지 종목을 통째로 지운다.
    """
    state_path = out / f"regime-state-{market}.pkl"
    universe_path = out / f"tradable-{market}.pkl"
    if state_path.exists() and universe_path.exists():
        print("=== 맥락(레짐 상태·매매 유니버스): 캐시가 이미 있다", flush=True)
        return

    policy = publication_policy(store, market, clock=LiveClock())
    analyst = RegimeAnalyst(store, LiveClock(), market=market)
    states: list[dict[str, object]] = []
    tradable: list[dict[str, object]] = []
    for index, session in enumerate(calendar, start=1):
        as_of = policy.for_session(session)
        analyst.clock = ReplayClock(as_of)
        states.append({"session": session, "state": analyst.state(as_of)})
        alive = analyst.tradable_entities(as_of, lookback=400)
        if alive:
            tradable.extend({"session": session, "entity_id": e} for e in sorted(alive))
        if index % 25 == 0:
            print(f"       … 맥락 {index}/{len(calendar)}", flush=True)

    pd.DataFrame(states).to_pickle(state_path)
    pd.DataFrame(tradable).to_pickle(universe_path)
    print(f"    레짐 {len(states)}세션 · 매매 유니버스 {len(tradable):,}행", flush=True)


def run_cache(args: argparse.Namespace) -> int:
    load_env()
    store = build_store(args.data_root)
    market = Market(args.market)
    out = Path(args.cache_dir)
    out.mkdir(parents=True, exist_ok=True)

    as_of = LiveClock().now()
    print(f"캐시 굽기 · {market} · 최근 {args.sessions}세션 · as_of {as_of:%F %T}")
    cache_targets(store, market=market, sessions=args.sessions, as_of=as_of, out=out)
    calendar = calendar_for(store, market=market, sessions=args.sessions, as_of=as_of)
    print(f"세션 {len(calendar)}개 ({calendar[0]} ~ {calendar[-1]})", flush=True)
    pd.DataFrame({"session": calendar}).to_pickle(out / f"calendar-{market}.pkl")

    for name in args.analyst:
        cache_scores(name, store, market=market, calendar=calendar, out=out)
    cache_context(store, market=market, calendar=calendar, out=out)
    print("캐시 완료")
    return 0


# -----------------------------------------------------------------------------
# 채점 도구 — 전부 `analysts/ic.py` 의 정의를 쓴다
# -----------------------------------------------------------------------------


def t_stat(series: pd.Series) -> tuple[float, float]:
    """일별 IC 계열의 t 값과 IC_IR.

    IC_IR = mean/std (연율화 없음). t = IC_IR × √n.

    **겹침 보정을 한다.** horizon 이 5일이면 이웃한 날의 타깃 구간이 4일 겹쳐서
    일별 IC 가 서로 독립이 아니다. 그대로 √n 을 곱하면 t 가 부풀고, 그 부풀린
    값으로 게이트를 세우면 게이트가 헐거워진다. Newey-West(lag = horizon-1)로
    분산을 키운다.
    """
    values = series.dropna().to_numpy(dtype=float)
    n = values.size
    if n < 3:
        return float("nan"), float("nan")
    mean = float(values.mean())
    std = float(values.std(ddof=1))
    return (mean / std if std > 0 else float("nan")), mean


def newey_west_t(series: pd.Series, *, lag: int) -> float:
    """겹치는 타깃을 감안한 t 값. lag=0 이면 보통 t 와 같다."""
    values = series.dropna().to_numpy(dtype=float)
    n = values.size
    if n < 3:
        return float("nan")
    mean = float(values.mean())
    dev = values - mean
    gamma0 = float((dev * dev).sum() / n)
    variance = gamma0
    for k in range(1, min(lag, n - 1) + 1):
        gamma = float((dev[k:] * dev[:-k]).sum() / n)
        variance += 2.0 * (1.0 - k / (lag + 1)) * gamma
    if variance <= 0:
        return float("nan")
    return mean / np.sqrt(variance / n)


def score_ic(
    scores: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    horizon: int,
    n_splits: int = 5,
) -> dict[str, float]:
    """OOS IC + t 값. IC 자체는 ``ic.evaluate`` 가 낸 값을 쓴다."""
    result = ic.evaluate(
        scores,
        targets,
        analyst="-",
        analyst_version="-",
        market="-",
        threshold=0.03,
        min_sample_days=200,
        horizon=horizon,
        n_splits=n_splits,
    )
    merged = scores.merge(targets, on=["entity_id", "session"], how="inner")
    daily = ic.daily_ic(merged) if not merged.empty else pd.Series(dtype=float)
    ic_ir, _ = t_stat(daily)
    return {
        "ic": result.ic,
        "ic_ir": ic_ir,
        "t": newey_west_t(daily, lag=max(horizon - 1, 0)),
        "t_naive": ic_ir * np.sqrt(daily.dropna().size) if daily.size else float("nan"),
        "days": float(daily.dropna().size),
        "rows": float(len(merged)),
    }


def ewma_scores(scores: pd.DataFrame, span: int | None) -> pd.DataFrame:
    """종목별 점수 EWMA. **과거만 본다** — pandas ewm 은 인과적이다."""
    if span is None:
        return scores
    frame = scores.sort_values(["entity_id", "session"]).copy()
    frame["score"] = (
        frame.groupby("entity_id")["score"].transform(lambda s: s.ewm(span=span).mean())
    )
    return frame



# -----------------------------------------------------------------------------
# 추가 캐시 — 유동성 패널 · chart 내부 피처
# -----------------------------------------------------------------------------


def cache_liquidity(
    store: Store, *, market: Market, calendar: list[date], as_of: datetime, out: Path
) -> None:
    """세션×종목 20일 평균 거래대금. 스윕의 '매매 유니버스' 축이 이걸 쓴다.

    **거래대금(``value``)을 쓴다. ``close × volume`` 이 아니다** — 보정가와
    원거래량을 곱하면 기업행위가 있었던 종목에서 배율만큼 어긋난다
    (`store/prices.py`: 보정은 가격에만 곱한다).
    """
    path = out / f"liquidity-{market}.pkl"
    if path.exists():
        print("=== 유동성 패널: 캐시가 이미 있다", flush=True)
        return
    from quant_rl_trading.store.prices import read_prices

    span = target_span(len(calendar))
    prices = read_prices(
        store, as_of=as_of, lookback=span, market=str(market),
        columns=["market", "value"], adjusted=False,
    )
    prices = prices.copy()
    prices["session"] = prices["valid_from"].dt.date
    prices = prices.sort_values(["entity_id", "session"])
    prices["turnover_20d"] = (
        prices.groupby("entity_id")["value"]
        .transform(lambda s: s.rolling(20, min_periods=10).mean())
    )
    keep = set(calendar)
    frame = prices.loc[
        prices["session"].isin(keep), ["entity_id", "session", "turnover_20d"]
    ].dropna()
    frame.to_pickle(path)
    print(f"    유동성 {len(frame):,}행 → {path.name}", flush=True)


def cache_chart_features(
    store: Store, *, market: Market, calendar: list[date], out: Path, name: str = "chart"
) -> None:
    """Analyst 의 **내부 피처**를 세션마다. 피처 단위 IC 측정이 이걸 쓴다.

    합성 점수가 아니라 피처를 굽는 이유: 게이트가 재는 것은 Analyst 인데
    **알파의 단위는 피처**일 수 있다. chart 에서 실제로 그랬다 — 유일하게
    유의한 `volume_surge` 가 안 되는 다섯과 평균당해 사라졌다
    (`docs/signal-combination.md` §6).
    """
    path = out / f"features-{name}-{market}.pkl"
    if path.exists():
        print(f"=== {name} 피처: 캐시가 이미 있다", flush=True)
        return
    print(f"=== {name} 피처: {len(calendar)}세션", flush=True)

    policy = publication_policy(store, market, clock=LiveClock())
    analyst = ANALYSTS[name](store, LiveClock(), market=market)
    frames: list[pd.DataFrame] = []
    for index, session in enumerate(calendar, start=1):
        moment = policy.for_session(session)
        analyst.clock = ReplayClock(moment)
        frame = analyst.features(moment)
        if not frame.empty:
            frames.append(frame.assign(session=session).reset_index(names="entity_id"))
        if index % 50 == 0:
            print(f"       … {name} 피처 {index}/{len(calendar)}", flush=True)
    if not frames:
        print(f"    {name} 피처가 비었다 — 캐시를 안 만든다", flush=True)
        return
    out_frame = pd.concat(frames, ignore_index=True)
    out_frame.to_pickle(path)
    print(f"    {name} 피처 {len(out_frame):,}행 → {path.name}", flush=True)


def run_cache_extra(args: argparse.Namespace) -> int:
    load_env()
    store = build_store(args.data_root)
    market = Market(args.market)
    out = Path(args.cache_dir)
    out.mkdir(parents=True, exist_ok=True)
    as_of = LiveClock().now()
    calendar = [
        day.date() if hasattr(day, "date") else day
        for day in pd.read_pickle(out / f"calendar-{market}.pkl")["session"]
    ]
    print(f"추가 캐시 · 세션 {len(calendar)}개", flush=True)
    cache_liquidity(store, market=market, calendar=calendar, as_of=as_of, out=out)
    for name in args.analyst:
        cache_chart_features(store, market=market, calendar=calendar, out=out, name=name)
    print("추가 캐시 완료")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cache = sub.add_parser("cache", help="점수·타깃·맥락을 구워 둔다")
    cache.add_argument("--analyst", nargs="+", default=sorted(ANALYSTS), choices=sorted(ANALYSTS))
    cache.add_argument("--sessions", type=int, default=300)
    cache.add_argument("--market", default="KR", choices=[m.value for m in Market])
    cache.add_argument("--data-root", type=Path)
    cache.add_argument("--cache-dir", default=str(CACHE_DIR))
    cache.set_defaults(func=run_cache)

    extra_parser = sub.add_parser("cache-extra", help="유동성 패널 · Analyst 내부 피처")
    extra_parser.add_argument(
        "--analyst", nargs="+", default=["chart"], choices=sorted(ANALYSTS),
        help="내부 피처를 구울 Analyst. 피처 단위 IC 측정은 전부 필요하다",
    )
    extra_parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    extra_parser.add_argument("--data-root", type=Path)
    extra_parser.add_argument("--cache-dir", default=str(CACHE_DIR))
    extra_parser.set_defaults(func=run_cache_extra)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
