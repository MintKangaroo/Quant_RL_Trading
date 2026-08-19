"""과거 ``signals`` 백필 — M4 학습 구간을 실제로 여는 도구.

    uv run python tools/backfill_signals.py --work <ic-history 디렉터리> --limit 3
    uv run python tools/backfill_signals.py --work <...> --start 2024-09-01 --save

## 왜 필요한가

``analyst_weights`` 를 과거 시점으로 백필해도(``tools/backfill_ic_history.py``)
RL 환경은 여전히 후보 0건으로 굴렀다. **층이 둘이었기 때문이다.** 가중치는
"어느 Analyst 를 얼마나 믿나" 이고, 점수는 "그 Analyst 가 그날 무엇을 어떻게
봤나" 다. Selector 는 둘을 곱해서 후보를 만든다 — 한쪽이 0행이면 곱은 0이다.

``signals`` 를 만드는 것은 ``tools/run_daily.py`` 뿐이고, 그건 매일 한 번
**오늘**만 돈다. 2024~2026 구간은 한 번도 돈 적이 없다. 실측으로 2025-09-12 ·
2026-03-13 둘 다 0행이었다.

## 왜 다시 채점하지 않나

``tools/backfill_ic_history.py`` 2단계가 이미 채점해서 작업 디렉터리에 남겨
뒀다 — 합집합 구간 773세션 × Analyst 6종, 2023-06-05 ~ 2026-08-06. 그게 정확히
``Signal.score`` 다(``analysts/base.to_scores_frame`` 는 ``signal.score`` 를
그대로 옮긴다).

**세션 점수는 측정 시점에 의존하지 않는다.** Analyst 는 그 세션의 공표 시각으로
돌고 ``store.get(as_of=...)`` 만 거치므로, 언제 채점하든 같은 값이 나온다.
근사가 아니라 항등이고, 실측으로 확인했다 — chart·risk × (2025-09-12,
2026-03-13) 재채점 결과가 저장된 값과 **최대 오차 0.0** 으로 일치했다.
그래서 4시간짜리 재채점을 통째로 건너뛴다.

## 복원하지 못하는 것

작업 파일은 ``(entity_id, session, score)`` 세 열뿐이라, 아래 셋은 재채점
없이는 되살릴 수 없다. **지어내지 않고 비워 둔다.**

- ``evidence_json`` — Decision Trace 화면이 읽는 근거. 과거 신호는 빈 목록이다
- ``features_hash`` — ``agent_cache`` 의 키. 빈 문자열이다
- ``latency_ms`` — 그때 실제로 걸린 시간이 아니므로 0 이다

셋 다 Selector 가 후보를 고르는 데는 쓰이지 않는다(``selector/pipeline.py`` 는
``analyst`` · ``score`` · ``confidence`` 만 본다). 그래서 ``source`` 를
``daily`` 가 아니라 ``signals-backfill`` 로 찍는다 — 나중에 이 행들이 근거를
안 들고 있는 이유를 창고 안에서 알 수 있어야 한다.

## confidence 는 지어내지 않는다

``ic.rolling_confidence`` 를 그대로 부른다(``session/signals.produce`` 와 같은
경로). 이 값은 **이미 창고에 있는 과거 signals** 를 읽어 계산하므로, 세션을
**오름차순으로** 처리해야 한다. 뒤에서부터 넣으면 앞 세션이 자기 미래를
근거로 confidence 를 받는다.

표본이 없는 초반 구간은 ``NO_EVIDENCE_CONFIDENCE`` (=1.0) 가 된다. 0 이 아니다
— 0 으로 두면 합성의 분모가 0 이 되어 후보가 통째로 빈다 (``analysts/ic.py``).
2024-09 시점에 실제로 돌렸어도 같은 값이었을 것이다.

## observed_at

``publication_policy`` 가 정한다. 그 세션의 공표 시각(국장 16:00 KST =
07:00 UTC)이고, ``valid_from`` 과 같다 — 신호는 계산한 그 순간부터 유효하고,
소급 적용되는 사실이 아니다. 오늘 날짜로 찍으면 이 도구가 고치려는 문제가
그대로 재발한다.

## 멱등

``run_id`` 는 ``session/signals.run_id_for`` 를 그대로 쓴다. 이미 들어간
세션·Analyst 는 창고가 알고 있으므로 건너뛴다. 중단은 예외가 아니라 기본값이다
— 언제 죽여도 마지막으로 끝난 세션까지는 무결하게 남는다.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import (  # noqa: E402
    NotYetPublished,
    publication_policy,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.signal import Signal  # noqa: E402
from quant_rl_trading.session.signals import SCORERS, run_id_for  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402
from quant_rl_trading.store.memo import MemoStore  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

# 작업 파일 읽기는 저쪽에서 이미 면제를 받아 뒀다. 여기서 다시 열면 면제가
# 두 곳으로 늘어나고, 늘어난 면제는 언젠가 진짜 창고 우회를 가린다.
from tools.backfill_ic_history import read_work, work_blocks  # noqa: E402

SIGNALS = "signals"

#: ``daily`` 와 구분한다. 이 행들은 근거(evidence)를 안 들고 있고, 그 이유가
#: 창고 안에서 읽혀야 한다. 모듈 독스트링 "복원하지 못하는 것" 참조.
BACKFILL_SOURCE = "signals-backfill"


# -----------------------------------------------------------------------------
# 작업 파일 읽기 — 블록 하나씩만 램에 둔다
# -----------------------------------------------------------------------------


class BlockCursor:
    """Analyst 하나의 세션별 점수를, **블록 하나씩만 들고** 훑는다.

    작업 파일은 150세션 단위로 끊겨 있고 세션 순서대로 쓰여 있다. 6종을
    통째로 올리면 700만 행이라 램이 버티지 못하고, 반대로 세션마다 여섯 파일을
    다시 여는 것은 480세션 × 6종 = 2,880번의 재열기다.

    세션을 **오름차순으로만** 묻는다는 전제로, 지금 필요한 블록 하나만 들고
    있다가 창을 넘어가면 다음 블록으로 넘긴다. 되돌아가지 않는다.
    """

    def __init__(self, work: Path, name: str) -> None:
        self.name = name
        self._paths = work_blocks(work, f"scores-{name}")
        self._index = -1
        self._sessions: dict[date, pd.DataFrame] = {}
        self._max: date | None = None

    def _advance(self) -> bool:
        self._index += 1
        if self._index >= len(self._paths):
            self._sessions, self._max = {}, None
            return False
        frame = read_work(self._paths[self._index])
        # 미리 세션별로 쪼개 둔다. 세션마다 다시 거르면 블록 전체를 150번
        # 훑게 되고, 그게 이 도구에서 제일 자주 도는 코드가 된다.
        self._sessions = {
            session: chunk for session, chunk in frame.groupby("session", sort=False)
        }
        self._max = max(self._sessions) if self._sessions else None
        return True

    def scores(self, session: date) -> pd.DataFrame | None:
        """그 세션의 ``(entity_id, score)``. 없으면 ``None``."""
        while self._max is None or session > self._max:
            if not self._advance():
                return None
        return self._sessions.get(session)


def analyst_version(work: Path, name: str) -> str | None:
    """채점에 쓴 Analyst 버전. 작업 파일이 남긴 것을 그대로 쓴다.

    **지금 버전으로 바꿔 적지 않는다.** ``analyst_version`` 은 자연키에 들어
    있어서, 여기 적힌 값이 "이 점수를 낸 모델"의 유일한 기록이다. 과거를 지금
    버전으로 채우는 것 자체는 정직하다 — 그때 그 버전이 없었다는 사실은
    ``observed_at`` 이 아니라 이 도구의 존재로 남는다.
    """
    path = work / f"scores-{name}.version"
    if not path.exists():
        return None
    return path.read_text().strip()


# -----------------------------------------------------------------------------
# 세션 하나
# -----------------------------------------------------------------------------


@dataclass
class SessionResult:
    written: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def backfill_session(
    store: Store,
    *,
    market: Market,
    as_of: datetime,
    session: date,
    cursors: dict[str, BlockCursor],
    versions: dict[str, str],
    dry_run: bool,
) -> SessionResult:
    """세션 하나의 신호를 창고에 남긴다.

    ``session/signals.produce`` 와 **같은 run_id · 같은 confidence 경로**를 쓴다.
    다른 것은 점수를 다시 계산하지 않고 작업 파일에서 읽는다는 것뿐이다.
    """
    result = SessionResult()
    # 여섯이 같은 as_of 로 같은 signals 창을 읽는다. 캐시가 없으면 그 조회가
    # 여섯 번 돈다 — 백필 후반에는 창이 1.6M 행이라 그게 세션 비용의 대부분이
    # 된다 (store/memo.py).
    cached = MemoStore(store)

    for name in SCORERS[market]:
        version = versions.get(name)
        if version is None:
            result.skipped.append(f"{name}: 채점 결과 없음")
            continue

        run_id = run_id_for(SIGNALS, market, as_of, name)
        if store.ingest_run_recorded(SIGNALS, run_id):
            result.skipped.append(f"{name}: 이미 적재됨")
            continue

        scores = cursors[name].scores(session)
        if scores is None or scores.empty:
            result.warnings.append(f"{name}: 점수 0건")
            continue

        confidence = ic.rolling_confidence(
            cached, analyst=name, as_of=as_of, market=str(market)
        )
        signals = [
            Signal(
                analyst=name,
                analyst_version=version,
                entity_id=str(entity_id),
                as_of=as_of,
                score=float(score),
                confidence=confidence,
                # evidence·features_hash·latency_ms 는 비운다. 작업 파일이
                # 안 들고 있고, 지어내면 Decision Trace 가 거짓을 보여준다.
            )
            for entity_id, score in zip(
                scores["entity_id"], scores["score"], strict=True
            )
        ]
        result.counts[name] = len(signals)
        result.confidence[name] = confidence
        if dry_run:
            continue

        rows = [
            signal.row(observed_at=as_of, source=BACKFILL_SOURCE) for signal in signals
        ]
        with contextlib.suppress(DuplicateIngestRun):
            result.written += int(store.append(SIGNALS, rows, ingest_run_id=run_id))
    return result


# -----------------------------------------------------------------------------
# 구간
# -----------------------------------------------------------------------------


def sessions_in(
    store: Store, market: Market, *, start: date, end: date
) -> list[tuple[date, datetime]]:
    """``(세션, 공표 시각)`` 오름차순.

    아직 공표되지 않은 세션은 뺀다 — 거부가 옳다. 미래에 공표될 사실을 지금
    관측했다고 적을 수는 없다.
    """
    policy = publication_policy(store, market, clock=LiveClock())
    now = LiveClock().now()
    out: list[tuple[date, datetime]] = []
    for day in trading_days(market, start, end):
        try:
            moment = policy.for_session(day)
        except NotYetPublished:
            continue
        if moment <= now:
            out.append((day, moment))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--start", default="2024-09-01", help="YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-31", help="YYYY-MM-DD")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument(
        "--work", type=Path, required=True, help="backfill_ic_history 의 작업 디렉터리"
    )
    parser.add_argument("--limit", type=int, help="앞에서부터 이만큼 세션만 (예행용)")
    parser.add_argument(
        "--dry-run", action="store_true", help="계산만 하고 저장하지 않는다"
    )
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    market = Market(args.market)
    work: Path = args.work

    versions = {
        name: version
        for name in SCORERS[market]
        if (version := analyst_version(work, name)) is not None
    }
    if not versions:
        print(f"{work} 에 채점 결과가 없다.", file=sys.stderr)
        return 2
    print("Analyst 버전: " + ", ".join(f"{k}={v}" for k, v in sorted(versions.items())))

    calendar = sessions_in(
        store,
        market,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
    if args.limit:
        calendar = calendar[: args.limit]
    if not calendar:
        print("세션이 없다.", file=sys.stderr)
        return 2
    print(
        f"{market} {len(calendar)}세션: {calendar[0][0]} ~ {calendar[-1][0]}"
        f"{' (dry-run)' if args.dry_run else ''}",
        flush=True,
    )

    cursors = {name: BlockCursor(work, name) for name in versions}
    started = time.monotonic()  # invariant-allow: wallclock
    total = 0
    for index, (session, as_of) in enumerate(calendar, start=1):
        result = backfill_session(
            store,
            market=market,
            as_of=as_of,
            session=session,
            cursors=cursors,
            versions=versions,
            dry_run=args.dry_run,
        )
        total += result.written
        if result.counts:
            detail = "  ".join(
                f"{name} {count}건/conf {result.confidence[name]:.3f}"
                for name, count in sorted(result.counts.items())
            )
        else:
            detail = "·".join(result.skipped) or "없음"
        elapsed = time.monotonic() - started  # invariant-allow: wallclock
        remaining = timedelta(seconds=int(elapsed / index * (len(calendar) - index)))
        print(
            f"[{index}/{len(calendar)}] {session} observed_at={as_of.isoformat()} "
            f"{result.written}행  {detail}  (남은시간 ~{remaining})",
            flush=True,
        )
        for message in result.warnings:
            print(f"    ⚠️  {message}", flush=True)

    print(f"\nsignals {total}행 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
