"""ranker 신호 이력 백필 — 워크포워드 모델로 과거 세션을 채점해 `signals` 에 남긴다.

    .venv/bin/python tools/backfill_ranker_signals.py --market KR --start 2024-02-01 --end 2026-09-02

## 왜 필요한가
RL 상태값에는 **과거 이력이 있는 신호만** 넣는다(CLAUDE.md). ranker 는 2026-09-03 에
생겼으니 이력이 없다 — 하지만 모델 열(`ranker-vX-YYYYMMDD`)은 월말마다 그 시점까지의
데이터로만 학습됐고, 세션 S 의 점수는 `usable_from ≤ S` 인 모델이 S 시점의 `signals`
만 읽어 만든다. 그러니 S 시점에 **실제로 낼 수 있었던** 점수다. 백테스트의
`session/signals.produce` 와 같은 규약으로 `observed_at = as_of`(세션 공표 시각)를
찍고, run id 도 같은 규칙(`daily-signals-<시장>-<날짜>-ranker`)이라 라이브가 뒤에
같은 세션을 다시 쓰지 않는다.

## 누수
모델: 학습 종료일 뒤에만 쓴다(analysts/ranker.usable_model). 입력: 그 세션 as_of 의
게이트를 지난 기초 점수뿐. 라벨은 안 본다.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.analysts.ranker import RankerAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.session.signals import SIGNALS, run_id_for  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402
from quant_rl_trading.collectors.market_hours import trading_days  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    store = Store(root=Path(args.root)); market = Market(args.market)
    policy = publication_policy(store, market, clock=LiveClock())
    sessions = list(trading_days(market, args.start, args.end))
    print(f"{market} {len(sessions)}세션 {args.start}~{args.end}", flush=True)
    written = skipped = empty = 0
    for i, session in enumerate(sessions, 1):
        as_of = policy.for_session(session)
        run_id = run_id_for(SIGNALS, market, as_of, "ranker")
        if store.ingest_run_recorded(SIGNALS, run_id):
            skipped += 1; continue
        analyst = RankerAnalyst(store, ReplayClock(as_of), market=market)
        confidence = ic.rolling_confidence(store, analyst="ranker", as_of=as_of, market=str(market))
        signals = analyst.run(as_of, confidence=confidence)
        if not signals:
            empty += 1; continue
        if args.dry_run:
            written += len(signals); continue
        rows = [s.row(observed_at=as_of, source="daily") for s in signals]
        with contextlib.suppress(DuplicateIngestRun):
            written += int(store.append(SIGNALS, rows, ingest_run_id=run_id))
        if i % 25 == 0:
            print(f"  {i}/{len(sessions)} {session} 모델 {analyst._model.trained_through if analyst._model else '-'} · 누적 {written:,}행", flush=True)
    print(f"완료 — 적재 {written:,}행 · 건너뜀 {skipped} · 신호 없음 {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
