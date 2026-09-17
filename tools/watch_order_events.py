"""Collect authenticated KR SC2/SC3 confirmations; never submits/modifies orders.

Start before chase_orders. A disconnect or missing event retains reservations.
This collector requires a pinned account fingerprint, including legacy KR real mode.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.factory import ACCOUNT_MODE_KEY, PROFILES  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, local_time  # noqa: E402
from quant_rl_trading.collectors.order_events import (  # noqa: E402
    WatchAborted,
    WatchResult,
    watch,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402


#: 같은 창 안 재접속 상한. 남의 서버를 우리 편의로 두들기지 않는다.
MAX_RETRIES = 5
RETRY_BACKOFF_SEC = 2.0
MAX_BACKOFF_SEC = 15.0


def _watch_until(store, client, clock, *, seconds: int, connector=None):
    """남은 시간이 있는 한 다시 붙는다. **놓친 이벤트는 다음 회차가 못 되살린다** —
    스트림은 과거를 다시 주지 않으므로, 구독이 안 붙은 채 회차를 버리면 그 창의
    취소·정정 확인은 영영 없다. 2026-09-17 15:19 회차가 그렇게 죽어 15:20 마감 취소
    10건이 미확정으로 남았고 15:45 대사가 rc=1 이 됐다.

    다시 붙어도 되는 사유(``retryable``)만 재시도한다. 계좌 지문·모드가 어긋난 것은
    다시 걸어도 같고 걸어서도 안 된다. 확인 건수는 회차를 넘어 합산한다.
    """
    deadline = clock.now() + timedelta(seconds=seconds)
    confirmed = duplicates = unmatched = 0
    last: WatchAborted | None = None
    attempts = 0
    while True:
        left = int((deadline - clock.now()).total_seconds())
        if left <= 0:
            break
        try:
            extra = {"connector": connector} if connector is not None else {}
            done = watch(store, client, clock, seconds=left, **extra)
        except WatchAborted as exc:
            part = exc.partial
            if part is not None:
                confirmed += part.confirmed; duplicates += part.duplicates; unmatched += part.unmatched
            if not exc.retryable:
                exc.partial = WatchResult(confirmed, duplicates, unmatched)
                raise
            last = exc
            attempts += 1
            if attempts >= MAX_RETRIES:
                print(f"{_stamp(clock)} 구독 재시도 {attempts}회 실패 — 그만둔다: {exc}", file=sys.stderr)
                exc.partial = WatchResult(confirmed, duplicates, unmatched)
                raise
            # **쉬었다 다시 건다.** 구독 거절은 붙자마자 나므로 그냥 `continue` 하면 창이
            # 끝날 때까지 초당 두어 번씩 LS 에 접속·인증을 반복한다(170초면 수백 회).
            print(f"{_stamp(clock)} 구독 재시도 {attempts}/{MAX_RETRIES}: {exc}", file=sys.stderr)
            time.sleep(min(RETRY_BACKOFF_SEC * attempts, MAX_BACKOFF_SEC))
            continue
        return WatchResult(
            confirmed + done.confirmed, duplicates + done.duplicates, unmatched + done.unmatched
        )
    if last is not None:
        # 창이 끝날 때까지 한 번도 못 붙었다. 성공으로 끝내지 않는다.
        last.partial = WatchResult(confirmed, duplicates, unmatched)
        raise last
    return WatchResult(confirmed, duplicates, unmatched)


def _stamp(clock) -> str:
    """로그 줄 앞의 시각. **없으면 어느 회차가 죽었는지 못 찾는다** — 크론이 하루
    스물네 번 도는데 2026-09-17 로그에는 시각이 한 줄도 없어, 15:20 마감 취소를
    놓친 회차를 특정하지 못했다.

    시각은 주입된 Clock 에서만 온다(불변식 2). 로그 한 줄 찍자고 벽시계를 부르면
    그 예외가 다음 사람의 근거가 된다.
    """
    return local_time(Market.KR, clock.now()).strftime("%m-%d %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    client = None
    try:
        mode = str(store.config(ACCOUNT_MODE_KEY, as_of=clock.now()))
        profile = PROFILES[("KR", mode)]
        client = LSClient(
            credentials=LSCredentials.from_env(prefix=profile.env_prefix),
            clock=clock,
            live_trading=False,
        )
        result = _watch_until(store, client, clock, seconds=args.seconds)
    except WatchAborted as exc:
        # **우리가 낸 판정은 문구가 고정이라 그대로 적는다.** 예전에는 이것까지
        # 종류 이름으로 뭉개서, 네 번 죽는 동안 로그에 `ValueError` 만 남았다.
        done = exc.partial
        got = f" · 중단 전 확인 {done.confirmed}건" if done and done.confirmed else ""
        print(f"{_stamp(clock)} 주문 확인 수집 중단: {exc}{got} — 미확정 예약 유지", file=sys.stderr)
        return 1
    except Exception as exc:
        # Authentication/transport exceptions may embed payloads; print no raw details.
        print(f"{_stamp(clock)} 주문 확인 수집 중단 ({type(exc).__name__}) — 미확정 예약 유지", file=sys.stderr)
        return 1
    except BaseException:
        raise
    finally:
        if client is not None:
            client.close()
    print(
        f"{_stamp(clock)} 취소·정정 확인 {result.confirmed}건 · 중복 {result.duplicates}건 · "
        f"로컬 주문과 연결되지 않은 이벤트 {result.unmatched}건"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
