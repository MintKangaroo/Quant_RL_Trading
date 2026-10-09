"""자기개선 ① 채점지 — 라벨이 닫힌 결정 세션마다 Analyst 별 적중을 `signal_scorecard` 에 적는다.

    .venv/bin/python tools/scorecard.py                     # 크론: 아직 안 적은, 라벨 닫힌 세션 전부(START 부터)
    .venv/bin/python tools/scorecard.py --dry               # 적지 않고 표만
    .venv/bin/python tools/scorecard.py --summary           # 지금까지 적힌 것 요약(② 회고가 읽는 표)

START = 2026-10-15(새 측정 첫날, 사용자 결정 10/9). 그 전 세션은 적지 않는다 — 채점지는 **전방 기록**이다.
같은 세션을 두 번 적지 않는다(이미 있는 세션은 건너뛴다). 종료코드: 0 정상(적을 것 없음 포함) · 1 실패(조용히 0 으로 끝내지 않는다).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.modelops import scorecard as sc
from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

START = date(2026, 10, 15)
HORIZON = 5
TABLE = "signal_scorecard"


def done_sessions(store: Store, now) -> set[str]:  # type: ignore[no-untyped-def]
    try:
        f = store.get(TABLE, as_of=now)
    except Exception:  # noqa: BLE001 — 표가 아직 없으면 빈 것
        return set()
    return set(f["session"].astype(str)) if not f.empty else set()


def k200_members(store: Store, as_of) -> set[str]:  # type: ignore[no-untyped-def]
    im = store.get("index_members", as_of=as_of, lookback=14, market="KR")
    if im.empty:
        return set()
    im = im[im["index_id"].astype(str).str.contains("KOSPI200")]
    last = pd.to_datetime(im["valid_from"]).max()
    return set(im[pd.to_datetime(im["valid_from"]) == last]["entity_id"].astype(str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="data")
    ap.add_argument("--start", type=date.fromisoformat, default=START)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    if args.start < START and not args.summary:
        # 2026-10-09 사고: 드라이런을 9월로 돌려 금고 early 창(7/1~9/30, 10/13 판정 전)의 신호 적중을 리드가 봤다(trials-postmortem 기록).
        # 채점지는 전방 기록이다 — START 이전은 열지 않는다. 검산은 합성 자료로(tests/modelops/test_scorecard.py).
        print(f"--start {args.start} < {START}: 채점지는 {START} 부터의 전방 기록이다 — 그 전 세션은 금고 창일 수 있어 열지 않는다", file=sys.stderr)
        return 2
    store = Store(root=Path(args.root))
    now = LiveClock().now()
    if args.summary:
        f = store.get(TABLE, as_of=now)
        print(sc.summarize(f).to_string() if not f.empty else "채점지 0행 — 아직 라벨이 닫힌 세션이 없다")
        return 0
    days = list(trading_days(Market.KR, args.start, now.date()))
    if len(days) <= HORIZON + 1:
        print(f"{args.start} 뒤 거래일 {len(days)} — 라벨이 닫힌 세션이 아직 없다(지평 {HORIZON}+1)")
        return 0
    prices = read_prices(store, as_of=now, lookback=(now.date() - args.start).days + 20, columns=["close"], adjusted=True, market="KR")
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.tz_convert(sc.KST).dt.date
    close = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index().reindex(days)
    labels = sc.forward_labels(close, HORIZON)
    have = done_sessions(store, now)
    todo = [d for d in days if d.isoformat() not in have and labels.loc[d].notna().sum() >= sc.MIN_N]
    records = []
    for d in todo:
        as_of = sc.decision_as_of(d)
        sig = store.get("signals", as_of=as_of, lookback=5, market="KR")
        if sig.empty:
            print(f"{d}: 신호 0행 — 건너뜀(그날 세션이 무엇을 봤는지 확인할 것)")
            continue
        cards = sc.score_session(sig, labels.loc[d], k200_members(store, as_of))
        if not sc.is_finite(cards):
            print(f"{d}: 채점 값에 NaN/inf — 적지 않는다", file=sys.stderr)
            return 1
        versions = {}
        if "analyst_version" in sig.columns:
            versions = sig.sort_values("observed_at").groupby("analyst")["analyst_version"].last().astype(str).to_dict()
        records += sc.as_records(cards, day=d, horizon=HORIZON, market="KR", versions=versions)
        allrow = {c.analyst: c for c in cards if c.universe == "all"}
        print(f"{d}: " + " · ".join(f"{a} IC {c.ic:+.3f} 하위 {c.bottom_excess:+.2%}" for a, c in sorted(allrow.items())))
    if not records:
        print("적을 세션 없음")
        return 0
    if args.dry:
        print(f"--dry: {len(records)}행 안 적음")
        return 0
    for r in records:
        r["observed_at"] = now
    n = store.append(TABLE, records, ingest_run_id=f"scorecard-{now:%Y%m%dT%H%M%S}", source="tools/scorecard.py")
    print(f"{TABLE} {n}행 적재 · 세션 {len(todo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
