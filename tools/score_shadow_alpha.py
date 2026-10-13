"""C1·AR shadow 매일 채점 — 얼린 모델로 그 세션 점수를 `signals`(analyst=c1fa | ar_insider, 관찰 모드)에 적는다.

    .venv/bin/python tools/score_shadow_alpha.py --analyst c1fa --market KR          # 마지막으로 공표된 세션 하나(크론)
    .venv/bin/python tools/score_shadow_alpha.py --analyst ar_insider --market KR
    .venv/bin/python tools/score_shadow_alpha.py --analyst c1fa --day 2026-10-14     # 그 세션 다시(실패했던 날)

설계: docs/design/c1-ar-shadow.md. 본보기는 `tools/score_be2.py`(같은 규약 — `observed_at = valid_from = 세션 공표 시각`,
run id `daily-signals-<시장>-<날짜>-<이름>`, 같은 세션을 두 번 안 쓴다). 그래서 신선도 검사(`selector/freshness.py`)의
**일일 규약**으로 읽힌다.

- `c1fa`: 입력은 창고 `fa_features`. 그 세션 FA 가 아직 없으면 `score_be2.score_session(features_only=True)` 로 먼저 적는다
  (be2 와 같은 함수·같은 run id — 22:56 BE2 채점이 이미 적었으면 '이미 있다' 로 통과). 러너는 같은 잠금(score-be2.lock)을 잡는다.
- `ar_insider`: 입력은 창고 `signals`(점수 6) + 내부자 원자료(G4·G7). FA 를 쓰지 않는다.

## 소급하지 않는다 (금고 second 창 봉인)

`SHADOW_START`(2026-10-13) 전 세션은 채점하지 않는다(rc 2). second 창(10/1~11/13)은 11/23 에 한 번 여는 금고다 —
지난 세션을 소급해 채점하면 그 점수의 IC·장부가 창고·화면에 생긴다. 범위 채점(--start/--end)은 일부러 두지 않았다.

## 종료 코드 (score_be2 와 같다)

0 정상(적었거나 이미 있었다) · 1 세션 없음 · 2 인자 오류·시작 전 세션 · 3 입력 미달(점수 6 열이 비었다 — 일일 실행기 전) ·
4 점수를 안 냈다(모델·창 미달 — `skip_reason` 을 찍는다). 러너는 0 이 아니면 그날 shadow 세션을 돌리지 않는다.
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

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import fa_features, ic  # noqa: E402
from quant_rl_trading.analysts.ar_insider import ArInsiderAnalyst  # noqa: E402
from quant_rl_trading.analysts.c1fa import C1FaAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.session.signals import SIGNALS, run_id_for  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402

RC_OK, RC_NO_SESSION, RC_BAD_ARGS, RC_NO_FEATURES, RC_NO_SCORE = 0, 1, 2, 3, 4
#: 이 날 전 세션은 채점하지 않는다 — 사용자 결정(10/13) 날. 그 전 second 창 세션을 소급하지 않는다(모듈 독스트링).
SHADOW_START = date(2026, 10, 13)
ANALYSTS: dict[str, type[C1FaAnalyst] | type[ArInsiderAnalyst]] = {"c1fa": C1FaAnalyst, "ar_insider": ArInsiderAnalyst}


def score_session(store: Store, name: str, market: Market, session: date, as_of, *,  # type: ignore[no-untyped-def]
                  dry_run: bool = False) -> int:
    """세션 하나: (FA) → 신호. rc 를 돌려준다."""
    if session < SHADOW_START:
        print(f"  {session} {name}: shadow 시작({SHADOW_START}) 전 세션 — 소급 채점하지 않는다", flush=True)
        return RC_BAD_ARGS
    run_id = run_id_for(SIGNALS, market, as_of, name)
    if store.ingest_run_recorded(SIGNALS, run_id):
        print(f"  {session} {name}: 이미 있다 ({run_id})", flush=True)
        return RC_OK
    counts = fa_features.score_counts(store, str(market), session, as_of)
    empty = [c for c, n in counts.items() if n == 0]
    if empty:
        print(f"  {session} {name}: 점수 열 {empty} 이 비었다 — 일일 실행기가 아직 안 돌았거나 Analyst 가 죽었다", flush=True)
        return RC_NO_FEATURES
    if name == "c1fa":
        from tools.score_be2 import score_session as write_fa

        rc = write_fa(store, market, session, as_of, features_only=True, write_signals=False, dry_run=dry_run)
        if rc != RC_OK:
            return rc
    analyst = ANALYSTS[name](store, ReplayClock(as_of), market=market)
    confidence = ic.rolling_confidence(store, analyst=name, as_of=as_of, market=str(market))
    signals = analyst.run(as_of, confidence=confidence)
    if not signals:
        print(f"  {session} {name}: 점수 없음 — {analyst.skip_reason or '신호 0건'}", flush=True)
        return RC_NO_SCORE
    scores = pd.Series({s.entity_id: s.score for s in signals})
    print(f"  {session} {name}: {len(signals):,}종목 · 점수 범위 {scores.min():+.2f}~{scores.max():+.2f} · "
          f"confidence {confidence:.2f}", flush=True)
    if not dry_run:
        rows = [s.row(observed_at=as_of, source="daily") for s in signals]
        with contextlib.suppress(DuplicateIngestRun):
            store.append(SIGNALS, rows, ingest_run_id=run_id)
    return RC_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analyst", required=True, choices=sorted(ANALYSTS))
    parser.add_argument("--root", default="data")
    parser.add_argument("--market", default="KR", choices=["KR"])
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.day is not None and args.day < SHADOW_START:
        print(f"--day {args.day}: shadow 시작({SHADOW_START}) 전 — 금고 second 창을 소급 채점하지 않는다", file=sys.stderr)
        return RC_BAD_ARGS
    store = Store(root=Path(args.root))
    market = Market(args.market)
    clock = LiveClock()
    if args.day:
        day, moment = args.day, publication_policy(store, market, clock=clock).for_session(args.day)
    else:
        from tools.run_daily import last_published

        published = last_published(store, market, clock.now())
        if published is None:
            print(f"{market} 공표된 세션을 찾지 못했다", file=sys.stderr)
            return RC_NO_SESSION
        moment = published
        day = fa_features.session_of(str(market), moment)
    print(f"=== {args.analyst} {market} · 세션 {day}{' (dry-run)' if args.dry_run else ''} ===", flush=True)
    return score_session(store, args.analyst, market, day, moment, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
