"""모의·실계좌로 나간 주문의 체결을 계좌에서 읽어 ``trades`` 에 적는다 (backtest.md §9).

    .venv/bin/python tools/reconcile_fills.py --market KR --sandbox data/_paper
    .venv/bin/python tools/reconcile_fills.py --market KR --sandbox data/_paper --day 2026-08-31

아침 세션(``run_session --live-broker``)이 보낸 주문은 ``orders.status = sent`` 로
남고 ``reason`` 에 ``broker_order_no=<번호>`` 가 있다. 이 도구는 그 번호로 t0425 를
조회해 실제 체결을 장부에 적는다. **시뮬레이션하지 않는다** — 체결가는 계좌가
말해 준다. ``execution.pending`` 은 ``sent`` 를 봉으로 체결시키지 않으므로, 이
도구가 안 돌면 그 주문은 장부에 영원히 없다. 그래서 종료코드가 말한다:

    0  전부 확인(체결·미체결·취소·만료 중 하나로 확정)
    1  하나라도 "모른다"(조회 실패) — 다음 실행이 다시 본다
    2  대사할 주문이 없다 (오늘 세션이 안 돌았거나 sent 가 0건)

STALE_DAYS 를 넘긴 "모른다" 는 ``expired`` 로 확정한다 — 브로커가 며칠 지난 주문을
안 돌려주므로 영원히 모름으로 남아 매일 rc=1 을 만들기 때문이다. 체결을 0 으로 적는
것이 아니라 주문만 종결시키며, 포지션 진실은 ``reconcile_snapshot`` 이 계좌 잔고와
대조해 따로 맞춘다.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.fills import FillState, PendingFill, sync_fills  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.executor.pipeline import BROKER_ORDER_NO_PREFIX  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import last_settled_day  # noqa: E402
from tools.verify_live_order import resolve_profile  # noqa: E402

ORDERS = "orders"
STATUS_SENT = "sent"
SOURCE_EXPIRE = "reconcile_expire"


def pending_from_orders(store: Store, *, as_of: datetime, market: str, session_id: str) -> list[PendingFill]:
    """``sent`` 주문 → PendingFill. 주문번호가 없는 ``sent`` 는 대사할 수 없다 — 그 사실을 남긴다.

    **현재 세션만 보지 않는다.** 예전에는 ``session_id`` 로 걸러 그날 세션 주문만
    대사했는데, 대사가 코드 버그·네트워크로 한 번 실패하면(2026-08-28 실측) 그 세션의
    ``sent`` 주문은 영영 고아가 됐다 — 다음 날 대사는 새 세션만 보기 때문이다. 그렇게
    8/27 주문 70건이 미체결로 남아 계좌가 87% 현금으로 굳었다.
    브로커 매칭은 세션이 아니라 행별 ``broker_order_no`` 로 하므로, **미기록 sent 를
    세션 불문 전부** 대상으로 삼는다. 이미 장부에 든 체결은 sync_fills 의
    ``_recorded_quantities`` 가 중복을 막는다.
    """
    frame = store.get(ORDERS, as_of=as_of, lookback=14)
    if frame.empty:
        return []
    # 시장을 섞지 않는다 — KR 대사에 US 주문이 들어오면 계좌·조회 경로가 어긋난다.
    # entity_id 접두사(``KR:``/``US:``)로 이 시장 것만 남긴다.
    frame = frame[
        (frame["status"] == STATUS_SENT)
        & frame["entity_id"].astype(str).str.startswith(f"{market}:")
    ]
    out: list[PendingFill] = []
    for row in frame.itertuples(index=False):
        reason = str(getattr(row, "reason", "") or "")
        # 행이 자기 세션을 들고 있으면 그걸 쓴다(옛 세션 고아도 정확히 식별). 없으면
        # 넘겨받은 현재 세션으로 메운다.
        row_session = str(getattr(row, "session_id", "") or session_id)
        if not reason.startswith(BROKER_ORDER_NO_PREFIX):
            print(f"  ⚠️  {row.entity_id} slice {row.slice_seq}: sent 인데 주문번호가 없다 — 대사 불가", file=sys.stderr)
            continue
        out.append(
            PendingFill(
                order_id=f"{row_session}|{row.entity_id}|{row.slice_seq}",
                entity_id=str(row.entity_id),
                side=Side(str(row.side)),
                market=market,
                broker_order_no=reason[len(BROKER_ORDER_NO_PREFIX):],
                requested_quantity=float(row.quantity),
            )
        )
    return out



#: 이 일수를 넘긴 sent 주문이 계속 "모른다" 면 만료로 확정한다. 브로커의 체결
#: 조회 창(며칠)보다 넉넉히 잡되, 늦게 오는 체결을 놓치지 않을 만큼은 기다린다.
STALE_DAYS = 3


def _expire_stale(
    store: Store, clock, *, now: datetime, market: str, result
) -> int:
    """오래된 UNKNOWN 주문을 ``expired`` revision 으로 되적는다. 적은 건수를 돌려준다."""
    unknown_ids = {
        o.order_id for o in result.outcomes if o.state is FillState.UNKNOWN
    }
    if not unknown_ids:
        return 0
    frame = store.get(ORDERS, as_of=now, lookback=30)
    frame = frame[frame["status"] == STATUS_SENT]
    cutoff = pd.Timestamp(now) - pd.Timedelta(days=STALE_DAYS)
    rows = []
    for row in frame.itertuples(index=False):
        oid = f"{getattr(row, 'session_id', '')}|{row.entity_id}|{row.slice_seq}"
        if oid not in unknown_ids:
            continue
        if pd.Timestamp(row.observed_at) > cutoff:
            continue  # 아직 늦게 올 수 있다
        record = {c: getattr(row, c) for c in frame.columns}
        record["status"] = "expired"
        record["revision"] = int(getattr(row, "revision", 2) or 2) + 1
        record["observed_at"] = now
        rows.append(record)
    if not rows:
        return 0
    run_id = f"expire-stale-{market}-{now:%Y%m%dT%H%M%S}"
    if store.ingest_run_recorded(ORDERS, run_id):
        return 0
    store.append(ORDERS, rows, ingest_run_id=run_id, source=SOURCE_EXPIRE)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--day", help="세션 날짜 (기본: 마지막 거래일 = 아침 세션이 결정한 날)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    market = Market(args.market)
    day = date.fromisoformat(args.day) if args.day else last_settled_day(store, market, clock.now())
    if day is None:
        print("거래일을 찾지 못했다.", file=sys.stderr)
        return 2
    session_id = f"{args.market}-{day.isoformat()}"
    now = clock.now()
    pending = pending_from_orders(store, as_of=now, market=args.market, session_id=session_id)
    print(f"{args.market} 세션 {session_id} · 창고 {store.root} · sent {len(pending)}건")
    if not pending:
        print("대사할 주문이 없다.")
        return 2

    profile = resolve_profile(store, market=args.market, as_of=now)
    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    print(f"계좌 — 모드 키 {profile.env_prefix} · 지문 {credentials.fingerprint or '(없음)'} · 선언 {credentials.kind or '(미선언)'}")
    client = LSClient(credentials=credentials, live_trading=True, min_interval_sec=profile.min_interval_sec)

    result = sync_fills(store, client, clock, as_of=now, pending=pending)
    unknown = 0
    for outcome in result.outcomes:
        if outcome.state is FillState.UNKNOWN:
            unknown += 1
            mark = "모른다"
        elif outcome.state is FillState.RECORDED:
            mark = "체결"
        else:
            mark = "변동없음"
        qty = outcome.fill.quantity if outcome.fill else outcome.cumulative_quantity
        price = f" @ {outcome.fill.price:,.0f}" if outcome.fill else ""
        print(f"  {mark:<4} {outcome.order_id} · {qty if qty is not None else '-'}주{price} {outcome.detail}")
    print(f"trades {result.rows_written}행 적재 · 모름 {unknown}건")

    # **오래된 '모름' 은 만료로 확정한다.** 브로커는 며칠 지난 주문의 체결을 안
    # 돌려주므로(4일이면 "주문 없음"), 그 주문들은 영원히 UNKNOWN 으로 남아 매
    # 대사마다 조회되고 rc=1 을 만든다 — 2026-08-27 주문 70건이 그랬다. 정상
    # 상태를 매일 실패로 보고하면 감시가 무뎌진다.
    #
    # **0 체결로 적는 것이 아니다** — trades 는 손대지 않는다. 주문만 종결로
    # 옮겨 더 쫓지 않게 한다. 그 사이 실제로 체결됐더라도 포지션 진실은
    # `reconcile_snapshot` 이 계좌 잔고와 대조해 따로 맞춘다. 그 안전망이 있어야
    # 이 만료 처리가 안전하다.
    stale = _expire_stale(store, clock, now=now, market=args.market, result=result)
    if stale:
        print(f"만료 확정 {stale}건 — {STALE_DAYS}일 넘게 브로커가 모른다고 답한 주문")
        unknown -= stale
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
