"""일일 실행 — Analyst 를 돌려 신호와 판정을 창고에 남긴다.

    uv run python tools/run_daily.py                    # 오늘(마지막 거래일)
    uv run python tools/run_daily.py --as-of 2026-08-11T16:00:00+09:00

## 왜 이게 필요한가

``measure_ic`` 는 점수를 **계산만** 하고 저장하지 않는다. 그래서 IC 를 몇 번
재도 ``signals`` 는 0행이었고, M2 완료 기준의 "Signal 기록 중" 이 충족되지
않았다. ``verdicts`` 도 마찬가지로 비어 있어 거부 성적표가 집계할 것이 없었다.

둘 다 **일일 실행기가 없어서 생긴 같은 구멍**이다. 그리고 M3 의 Selector 가
읽을 것이 정확히 이 두 테이블이라, 어차피 M3 전에 반드시 필요하다.

## as_of 는 그 세션의 공표 시각이다

자정으로 두면 그날 종가를 못 보고, 다음날로 두면 하루를 버린다. ``measure_ic``
와 같은 규칙(``publication_policy``)을 쓴다 — 두 벌로 두면 측정과 운영이
서로 다른 시점을 보게 되고, 그 차이는 IC 로 안 드러난다.

## 관찰 모드도 기록한다

IC 를 통과하지 못한 Analyst 의 신호도 저장한다. 가중치가 0이라 매매에는 안
쓰이지만, **기록이 없으면 나중에 좋아졌는지 알 수 없다.** 통과 여부는
``analyst_weights`` 가 들고 있으므로 여기서 거를 이유가 없다.

## confidence 는 스스로 매기지 않는다

최근 60일 롤링 IC 로 계산해 넣는다 (agents.md §1). 스스로 매기면 과신한다.
**잴 표본이 없으면 감쇠하지 않는다** — 0 으로 두면 합성의 분모가 0 이 되어
후보가 통째로 비고, 그 침묵이 "오늘 살 게 없다" 와 구분되지 않는다
(`analysts/ic.py` NO_EVIDENCE_CONFIDENCE).
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts.news_screen import NewsScreen  # noqa: E402
from quant_rl_trading.analysts.verdicts import NewsAnalyst, SnsAnalyst, VerdictAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import (  # noqa: E402
    NotYetPublished,
    publication_policy,
)
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.session import signals as signals_module  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402

SIGNALS = "signals"
VERDICTS = "verdicts"

#: 점수를 내는 Analyst 는 ``session/signals.py`` 가 들고 있다. 백테스트 루프가
#: 같은 목록을 봐야 하기 때문이다 — 여기 한 벌 더 두면 언젠가 갈라진다.

#: 판정만 내는 Analyst. 매수 금지만 가능하다.
FILTERS: dict[str, type[VerdictAnalyst]] = {"news": NewsAnalyst, "sns": SnsAnalyst}


def run_id_for(table: str, market: Market, moment: datetime, name: str) -> str:
    """결정론적 run id. 같은 세션을 두 번 넣으려 하면 창고가 거부한다."""
    return f"daily-{table}-{market}-{moment:%Y%m%d}-{name}"


def last_published(store: Store, market: Market, now: datetime) -> datetime | None:
    """마지막으로 **알 수 있는** 세션의 공표 시각.

    마지막 거래일이 아니다. 장 마감 전에 돌리면 오늘 세션은 아직 공표되지
    않았고, 그때 as_of 를 오늘로 잡으면 게이트가 거부한다(그게 옳다).
    공표된 세션을 만날 때까지 거슬러 올라간다.

    휴장일도 같은 방식으로 처리된다 — 조용히 아무것도 안 하는 대신 직전
    거래일을 잡는다. 크론이 공휴일에 한 번 건너뛰면 그날 신호가 영영 안 생긴다.
    """
    policy = publication_policy(store, market, clock=LiveClock())
    day = now.astimezone(UTC).date()
    for session in reversed(trading_days(market, day - timedelta(days=14), day)):
        try:
            moment = policy.for_session(session)
        except NotYetPublished:
            continue
        if moment <= now:
            return moment
    return None


def run_scorers(
    store: Store, *, market: Market, as_of: datetime, dry_run: bool, revision: int = 0
) -> tuple[int, list[str], list[str]]:
    """Analyst 점수 → signals. (적재 행수, 경고, **실패**)

    계산은 ``session.signals`` 가 한다. 여기는 화면에 찍는 일만 한다 —
    백테스트 루프도 같은 함수를 부르므로, 로직이 이 파일에 남으면 백테스트는
    그것을 못 본다.
    """
    result = signals_module.produce(
        store, market=market, as_of=as_of, dry_run=dry_run, revision=revision
    )
    for name, count in result.counts.items():
        print(f"    {name:12s} {count:>5}건  confidence {result.confidence[name]:.4f}")
    return result.written, result.warnings, result.failures


def run_filters(
    store: Store, *, market: Market, as_of: datetime, dry_run: bool
) -> tuple[int, list[str]]:
    """News·SNS 판정 → verdicts.

    후보는 **그날 신호가 나온 종목**이다. 전 종목을 넣으면 상한(30%)이
    수천 종목 기준이 되어 사실상 무한대가 된다 — 상한의 뜻이 사라진다.
    """
    written, warnings = 0, []
    clock = ReplayClock(as_of)

    signals = store.get(SIGNALS, as_of=as_of, lookback=3, market=None)
    if signals.empty:
        return 0, ["후보 없음 — 신호가 먼저 있어야 판정할 수 있다"]
    prefix = f"{market}:"
    entities = sorted(
        {str(value) for value in signals["entity_id"] if str(value).startswith(prefix)}
    )
    if not entities:
        return 0, [f"{market} 후보 없음"]

    screen = NewsScreen.from_env(store, clock)
    for name, factory in FILTERS.items():
        run_id = run_id_for(VERDICTS, market, as_of, name)
        if store.ingest_run_recorded(VERDICTS, run_id):
            continue

        analyst = factory(store, clock, market=market)
        if name == "news" and screen.usable():
            # 키워드 1단계의 오탐을 Claude 가 기각한다 (실측 오탐률 4/4).
            analyst.screen = screen
        try:
            verdicts = analyst.run(entities, as_of)
        except Exception as error:
            warnings.append(f"{name}: {type(error).__name__}: {error}")
            continue

        print(f"    {name:12s} 후보 {len(entities)}종목 → 차단 {len(verdicts)}건")
        if not verdicts or dry_run:
            continue
        rows = analyst.rows(verdicts, observed_at=as_of)
        with contextlib.suppress(DuplicateIngestRun):
            written += int(store.append(VERDICTS, rows, ingest_run_id=run_id))
    return written, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--as-of", help="기준 시각 (ISO8601). 생략하면 마지막 거래일")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 저장하지 않는다")
    parser.add_argument(
        "--revision",
        type=int,
        default=0,
        help="같은 세션의 정정본을 넣는다. 계산 규칙이 바뀌었을 때만 쓴다 (불변식 4)",
    )
    args = parser.parse_args(argv)

    load_env()
    store = build_store(None)
    clock = LiveClock()
    market = Market(args.market)

    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            print("as_of 에 타임존이 없다. 오프셋을 명시할 것.", file=sys.stderr)
            return 2
    else:
        as_of = last_published(store, market, clock.now())
        if as_of is None:
            print(f"{market} 공표된 세션을 찾지 못했다.", file=sys.stderr)
            return 1

    print(f"{market} as_of={as_of.isoformat()}{' (dry-run)' if args.dry_run else ''}")

    print("  [1/2] Analyst 점수 → signals")
    signals_written, signal_warnings, signal_failures = run_scorers(
        store, market=market, as_of=as_of, dry_run=args.dry_run, revision=args.revision
    )
    print("  [2/2] News · SNS 판정 → verdicts")
    verdicts_written, filter_warnings = run_filters(
        store, market=market, as_of=as_of, dry_run=args.dry_run
    )

    print(f"\nsignals {signals_written}행 · verdicts {verdicts_written}행")
    for message in signal_warnings + filter_warnings:
        print(f"  ⚠️  {message}")

    # **Analyst 가 죽으면 0 으로 끝내지 않는다.**
    #
    # 예전에는 무조건 0 이었다. 그래서 2026-08-18~20 세 세션 동안 6종 중
    # 3종이 MemoryError 로 죽는 내내 크론은 성공으로 봤고, 아무 데도 안
    # 나타났다. 사람이 로그를 열어 봐야만 알 수 있는 고장은 **없는 고장과
    # 같다.**
    #
    # "신호 0건"(``warnings``)은 여기서 안 센다. 그건 데이터가 아직 없는
    # 정상 상태일 수 있고, 그것까지 실패로 치면 경보가 매일 울려서 아무도
    # 안 보게 된다.
    if signal_failures:
        print(f"\nAnalyst {len(signal_failures)}종이 죽었다 — 남은 신호로 후보를 골랐다.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
