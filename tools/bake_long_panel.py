#!/usr/bin/env python
"""확장 패널 — 시행 Z·AA 가 쓸 긴 구간(박스장 포함) 캐시를 굽는다.

    .venv/bin/python tools/bake_long_panel.py --stage targets   # 달력·타깃(h5·h20·h60)
    .venv/bin/python tools/bake_long_panel.py --stage scores    # Analyst 점수(창고 signals 에서)
    .venv/bin/python tools/bake_long_panel.py --stage tradable  # 세션별 매매 가능 종목

## 왜 다시 채점하지 않나

`diagnose_ic.py cache` 는 Analyst 를 세션마다 돌려 점수를 만든다(300세션·7종에 몇 시간). 그런데
**그 점수는 이미 창고 `signals` 에 있다** — 2021-09/11 이후 전부(2026-09-20 확인). 세션 S 의 점수는
S 의 공표시각 as_of 로만 계산되므로(backfill_ic_history 모듈독스트링의 "항등"), 창고에 남은 그 값과
다시 채점한 값은 같다. 그러니 읽어서 캐시 모양으로 옮긴다.

## 기존 캐시를 덮지 않는다

`data/_diag` 는 지난 시행들의 **입력**이다. 덮으면 그 결과를 다시 만들 수 없다. 기본 출력은
`data/_diag-long` 이고, 시행 도구는 `--cache-dir` 로 이쪽을 가리킨다.

## 누수

- 점수: 그 세션 공표시각 as_of 의 게이트를 이미 지난 값(창고 그대로). 여기서 재계산하지 않는다.
- 타깃: `ic.build_targets` 정의 그대로. 라벨이 미래를 보는 것은 라벨의 정의이고, 피처와의 분리는
  시행 도구의 purged K-fold·embargo 가 한다.
- 매매 유니버스: 세션마다 그 시점 as_of 로 다시 판정한다(오늘 명단으로 과거를 덮으면 상장폐지가 사라진다).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.analysts.regime import RegimeAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

OUT = Path("data/_diag-long")
HORIZONS = (5, 20, 60)
#: 국장 기본 구간 — 2021-11 은 signals 가 여섯 Analyst 모두 차 있는 첫 달이다.
START, END = date(2021, 11, 1), date(2026, 6, 30)
ANALYSTS = ("chart", "event", "flow_kr", "fundamental", "regime", "risk", "volume", "ranker")


def _days(market: Market, start: date, end: date) -> list[date]:
    return list(trading_days(market, start, end))


def bake_targets(store: Store, market: Market, start: date, end: date, out: Path) -> None:
    now = LiveClock().now()
    lookback = (now.date() - start).days + 10
    calendar = _days(market, start, end)
    pd.DataFrame({"session": calendar}).to_pickle(out / f"calendar-{market}.pkl")  # invariant-allow: data-access — 작업 캐시
    print(f"달력 {len(calendar)}세션 {calendar[0]} ~ {calendar[-1]}", flush=True)
    for horizon in HORIZONS:
        path = out / f"targets-{market}-h{horizon}.pkl"
        if path.exists():
            print(f"  타깃 h{horizon}: 이미 있다", flush=True)
            continue
        frame = ic.build_targets(store, as_of=now, lookback=lookback, horizon=horizon, market=str(market))
        frame = frame[(frame["session"] >= start) & (frame["session"] <= end)]
        frame.to_pickle(path)  # invariant-allow: data-access — 작업 캐시
        print(f"  타깃 h{horizon}: {len(frame):,}행", flush=True)


def bake_scores(store: Store, market: Market, start: date, end: date, out: Path, analysts: tuple[str, ...]) -> None:
    """창고 `signals` → `scores-<analyst>-<시장>.pkl` (entity_id, session, score).

    **분기씩 끊어 읽는다.** 5년치를 한 질의로 달라고 하면 죽는다(2026-09-20 실측: 8종 × 300만 행,
    rc=137). `as_of` 는 고정하고 `until`·`lookback` 창만 옮긴다 — as_of 를 옮기면 그 시점에 없던
    정정본 관계가 바뀐다(store.get 독스트링).
    """
    now = LiveClock().now()
    edges = list(pd.date_range(start=start, end=end, freq="QS").date)
    if not edges or edges[0] > start:
        edges = [start, *edges]
    edges.append(end + pd.Timedelta(days=1).to_pytimedelta())
    todo = [name for name in analysts if not (out / f"scores-{name}-{market}.pkl").exists()]
    for name in analysts:
        if name not in todo:
            print(f"=== {name}: 이미 있다", flush=True)
    if not todo:
        return
    parts: dict[str, list[pd.DataFrame]] = {name: [] for name in todo}
    for left, right in zip(edges[:-1], edges[1:], strict=False):
        # **lookback 은 as_of 에서 거슬러 센다**(reader `_valid_from_floor`) — 창의 왼쪽 끝을
        # until 기준으로 계산하면 옛 분기가 통째로 비어 나온다(2026-09-20 실측: 마지막 분기만 남았다).
        window = (now.date() - left).days + 1
        chunk = store.get(
            "signals", as_of=now, until=datetime.combine(right, datetime.min.time(), tzinfo=UTC),
            lookback=window, market=str(market),
            columns=["entity_id", "valid_from", "observed_at", "analyst", "score"],
        )
        if chunk.empty:
            continue
        chunk = chunk.assign(session=chunk["valid_from"].dt.date)
        chunk = chunk[(chunk["session"] >= left) & (chunk["session"] < right)]
        for name in todo:
            part = chunk[chunk["analyst"] == name]
            if not part.empty:
                parts[name].append(part[["entity_id", "session", "observed_at", "score"]])
        print(f"  {left} ~ {right}: {len(chunk):,}행", flush=True)
        del chunk
    for name in todo:
        if not parts[name]:
            print(f"=== {name}: 창고에 0행 — 건너뜀", flush=True)
            continue
        frame = pd.concat(parts[name], ignore_index=True)
        frame = frame.sort_values("observed_at").groupby(["entity_id", "session"], as_index=False).tail(1)
        frame[["entity_id", "session", "score"]].to_pickle(out / f"scores-{name}-{market}.pkl")  # invariant-allow: data-access — 작업 캐시
        print(f"=== {name}: {len(frame):,}행 · {frame['session'].min()} ~ {frame['session'].max()}", flush=True)
        parts[name] = []


def bake_tradable(store: Store, market: Market, start: date, end: date, out: Path) -> None:
    path = out / f"tradable-{market}.pkl"
    if path.exists():
        print("매매 유니버스: 이미 있다", flush=True)
        return
    policy = publication_policy(store, market, clock=LiveClock())
    analyst = RegimeAnalyst(store, LiveClock(), market=market)
    rows: list[dict[str, object]] = []
    calendar = _days(market, start, end)
    for index, session in enumerate(calendar, start=1):
        as_of = policy.for_session(session)
        analyst.clock = ReplayClock(as_of)
        alive = analyst.tradable_entities(as_of, lookback=400)
        if alive:
            rows.extend({"session": session, "entity_id": e} for e in sorted(alive))
        if index % 50 == 0:
            print(f"    … {index}/{len(calendar)} · 누적 {len(rows):,}행", flush=True)
    pd.DataFrame(rows).to_pickle(path)  # invariant-allow: data-access — 작업 캐시
    print(f"매매 유니버스 {len(rows):,}행", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("targets", "scores", "tradable", "all"), default="all")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    parser.add_argument("--start", type=date.fromisoformat, default=START)
    parser.add_argument("--end", type=date.fromisoformat, default=END)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--analyst", nargs="+", default=list(ANALYSTS))
    args = parser.parse_args(argv)
    load_env()
    args.out.mkdir(parents=True, exist_ok=True)
    store = Store(root=Path(args.root))
    market = Market(args.market)
    began = datetime.now()  # invariant-allow: wallclock — 경과 시간 출력
    print(f"확장 패널 · {market} · {args.start} ~ {args.end} · 출력 {args.out}", flush=True)
    if args.stage in ("targets", "all"):
        bake_targets(store, market, args.start, args.end, args.out)
    if args.stage in ("scores", "all"):
        bake_scores(store, market, args.start, args.end, args.out, tuple(args.analyst))
    if args.stage in ("tradable", "all"):
        bake_tradable(store, market, args.start, args.end, args.out)
    print(f"끝 — {(datetime.now() - began).total_seconds() / 60:.1f}분")  # invariant-allow: wallclock
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
