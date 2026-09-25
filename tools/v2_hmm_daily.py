"""AI v2 — HMM 노출 행동을 매일 계산해 창고(`exposure_actions`)에 적는다. 시행 BB(shadow 승격 후보)의 전방 기록용.

    .venv/bin/python tools/v2_hmm_daily.py --market KR [--day YYYY-MM-DD] [--dry-run]

규칙은 시행 BB 와 같다(docs/protocols/v2-hmm-exposure-2026-10.md): 지수 [일수익·20일 수익·20일 변동성] 3상태 가우시안 HMM, **그 달 첫 세션 전까지로
적합**하고 그날까지 전방 필터 → k = 0.5·p0 + 0.8·p1 + 1.0·p2, 0.05 반올림, 직전 행동과 0.10 미만 차이면 유지.
valid_from = observed_at = 그 세션 기준 시각(`backtest.loop.snapshot_moment` — 신호와 같은 규약; 쓰는 정보는 그날 종가까지).
`session/daily.run` 은 샌드박스 설정 `exposure.source: hmm-v1` 일 때 이 행을 **읽기만** 한다(불변식 6).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.backtest import loop  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, is_trading_day, previous_trading_day  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.selector.exposure import ACTIONS  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.v2_regime_hmm import INDEX, latest  # noqa: E402

SOURCE = "hmm-v1"
HMM_MAP = (0.5, 0.8, 1.0)
DEADBAND, STEP = 0.10, 0.05


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="KR", choices=sorted(INDEX))
    parser.add_argument("--day", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    market = Market(args.market)
    now = LiveClock().now()
    day = date.fromisoformat(args.day) if args.day else now.astimezone(market_tz(market)).date()
    if not is_trading_day(market, day):
        day = previous_trading_day(market, day)
    store = Store(root=Path("data"))
    moment = loop.snapshot_moment(store, day, as_of=now, market=market)
    end = datetime.combine(day, time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=max(end, now), lookback=(day - date(2020, 1, 1)).days, market=args.market,
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX[args.market]].assign(d=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("d")["close"].last().sort_index()
    closes = closes[(closes > 0) & (closes.index <= day)]
    if closes.empty or closes.index[-1] != day:
        print(f"{args.market} {day}: 지수 종가가 아직 없다(마지막 {closes.index[-1] if len(closes) else '없음'}) — 적지 않는다", file=sys.stderr)
        return 1
    p = latest(closes, 3)
    target = round(float(np.dot(p, HMM_MAP)) / STEP) * STEP
    prev = store.get(ACTIONS, as_of=now, lookback=20, market=args.market, columns=["entity_id", "valid_from", "source", "scale"])
    prev = prev[(prev["source"] == SOURCE) & (prev["valid_from"] < moment)] if not prev.empty else prev
    held = float(prev.sort_values("valid_from").iloc[-1]["scale"]) if len(prev) else None
    scale = target if held is None or abs(target - held) >= DEADBAND else held
    row = {"entity_id": args.market, "valid_from": moment, "observed_at": moment, "source": SOURCE, "market": args.market,
           "scale": float(scale), "detail": json.dumps({"p": [round(x, 4) for x in p], "target": target, "held": held})}
    print(f"{args.market} {day}: p={np.round(p, 3).tolist()} → 목표 {target:.2f} · 직전 {held} → 행동 {scale:.2f}", flush=True)
    if not args.dry_run:
        store.append(ACTIONS, [row], ingest_run_id=f"{SOURCE}-{args.market}-{day:%Y%m%d}", source=SOURCE)
    return 0


def market_tz(market: Market):  # type: ignore[no-untyped-def]
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Seoul" if market is Market.KR else "America/New_York")


if __name__ == "__main__":
    raise SystemExit(main())
