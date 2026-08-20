#!/usr/bin/env python
"""재부팅·정전 뒤 **무엇이 비었는지** 묻고 할 일을 적어 낸다.

    uv run python tools/plan_recovery.py --market KR

## 왜 "돌았나" 가 아니라 "비었나" 를 묻는가

놓친 크론을 찾는 방법은 둘이다. 하나는 **로그에 오늘 기록이 있나** 를 보는
것이고, 하나는 **창고에 오늘 세션이 있나** 를 보는 것이다.

앞의 방법은 틀린다. `collect_daily.sh KR` 은 하루 두 번(15:55·22:40) 같은
로그에 쓴다. 15:55 것만 돌고 22:40 것이 빠진 날에도 "오늘 기록이 있다" 가
되어 통과한다. 게다가 로그는 rc=0 으로 끝나면서 0행을 받아 올 수 있다 —
그건 [[fresh-is-not-fresh]] 가 이미 적어 둔 함정이다.

**창고를 본다.** 기대 세션(`sessions.expected_session`, 공표가 끝난 마지막
거래일)과 창고에 실제로 들어온 마지막 세션을 견준다. 그러면 크론이 돌았든
안 돌았든, 성공했든 조용히 0행이었든 **같은 질문 하나로 답이 나온다.**

## 판정을 여기 한 곳에만 둔다

기대 세션 계산은 브리핑이 쓰는 `reporting.sessions` 를 그대로 부른다.
복구가 자기 달력을 따로 들면 브리핑은 "안 들어왔다" 는데 복구는 "다 됐다"
고 말하는 날이 온다.

## 나가는 형식

셸이 읽는다. 한 줄에 하나:

    NEED collect   KR 시세: 창고가 2026-08-18 까지다 (2개 세션)
    OK   session   KR 세션: 2026-08-20 이 있다
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.reporting.sessions import expected_session  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

#: 창고를 거꾸로 훑는 창. 연휴가 끼어도 마지막 거래일이 들어오도록 넉넉히.
LOOKBACK_DAYS = 14


def latest_session(
    store: Store, table: str, *, as_of: datetime, market: str | None, column: str
) -> "pd.Timestamp | None":
    """그 표에 들어온 **마지막 세션 날짜**. 없으면 ``None``.

    ``lookback`` 을 반드시 준다 — 안 주면 파티션 프루닝이 꺼져 5년치를
    통째로 훑는다 ([[partition-pruning-was-off]]).

    ``market`` 이 ``None`` 인 표가 있다. `nav_daily` 는 포트폴리오 전체가
    한 줄이라 시장 축이 없다 — 거기에 시장을 넘기면 조회가 통째로 실패한다.
    """
    try:
        frame = store.get(
            table,
            as_of=as_of,
            lookback=LOOKBACK_DAYS,
            columns=[column],
            **({"market": market} if market else {}),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN {table} 조회 실패: {exc}", file=sys.stderr)
        return None
    if frame.empty:
        return None
    return pd.to_datetime(frame[column]).max().date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)

    # **여기서는 벽시계가 맞다.** "지금 무엇이 비었나" 가 질문이라 고정된
    # as_of 로 물으면 언제 돌려도 같은 답이 나와 복구가 안 된다.
    now = datetime.now(UTC)  # invariant-allow: wallclock
    market = args.market
    store = Store(root=Path(args.root))

    expected = expected_session(store, Market(market), as_of=now)
    if expected is None:
        print(f"OK   -         {market}: 최근 {LOOKBACK_DAYS}일에 공표된 세션이 없다 (휴장)")
        return 0

    print(f"기대 세션 {market} {expected.isoformat()}")

    # -- 시세 ----------------------------------------------------------------
    observed = latest_session(
        store, "prices", as_of=now, market=market, column="valid_from"
    )
    if observed is None:
        print(f"NEED collect   {market} 시세: 최근 {LOOKBACK_DAYS}일에 한 행도 없다 (기대 {expected})")
    elif observed < expected:
        print(f"NEED collect   {market} 시세: 창고가 {observed.isoformat()} 까지다 (기대 {expected})")
    else:
        print(f"OK   collect   {market} 시세: {observed.isoformat()}")

    # -- 세션(신호·후보) -----------------------------------------------------
    #
    # 세션이 돌았는지는 `nav_daily` 로 본다. 세션의 마지막 산출물이라
    # 여기까지 왔으면 앞 단계는 다 지난 것이다.
    for table, job, label, axis in (("nav_daily", "session", "세션", None),):
        observed = latest_session(
            store, table, as_of=now, market=axis, column="valid_from"
        )
        if observed is None:
            print(f"NEED {job:9s} {market} {label}: 최근 {LOOKBACK_DAYS}일에 한 행도 없다 (기대 {expected})")
        elif observed < expected:
            print(f"NEED {job:9s} {market} {label}: 창고가 {observed.isoformat()} 까지다 (기대 {expected})")
        else:
            print(f"OK   {job:9s} {market} {label}: {observed.isoformat()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
