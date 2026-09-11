"""모의·실계좌로 나간 주문의 체결을 계좌에서 읽어 ``trades`` 에 적는다 (backtest.md §9).

    .venv/bin/python tools/reconcile_fills.py --market KR --sandbox data/_paper
    .venv/bin/python tools/reconcile_fills.py --market KR --sandbox data/_paper --day 2026-08-31

아침 세션(``run_session --live-broker``)이 보낸 주문은 ``orders.status = sent`` 로
남고 ``reason`` 에 ``broker_order_no=<번호>`` 가 있다. 이 도구는 그 번호로 t0425 를
조회해 실제 체결을 장부에 적는다. **시뮬레이션하지 않는다** — 체결가는 계좌가
말해 준다. ``execution.pending`` 은 ``sent`` 를 봉으로 체결시키지 않으므로, 이
도구가 안 돌면 그 주문은 장부에 영원히 없다. 그래서 종료코드가 말한다:

    0  조회 대상의 체결량 확인, 미확정 전송·종결 상태 없음
    1  하나라도 "모른다"(조회 실패) — 다음 실행이 다시 본다
    2  대사할 주문이 없다 (오늘 세션이 안 돌았거나 sent 가 0건)

오래된 미확정 주문도 자동 만료시키지 않는다. 날짜 경과는 취소·미체결 증거가 아니다.
주문번호 없는 전송 시도도 실패로 보고하며 예약은 장부에서 유지한다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.fills import FillState, PendingFill, sync_fills  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.executor.action_journal import (  # noqa: E402
    cancelled_quantities,
    refresh_order_states,
    submission_bindings,
)
from quant_rl_trading.executor.orders import client_order_id  # noqa: E402
from quant_rl_trading.executor.pipeline import BROKER_ORDER_NO_PREFIX  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.risk.account import UNVERIFIED_TERMINAL, filled_quantities, key  # noqa: E402
from quant_rl_trading.schemas.order import Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import last_settled_day  # noqa: E402
from tools.verify_live_order import resolve_profile  # noqa: E402

ORDERS = "orders"
STATUS_SENT = "sent"
UNRESOLVED_STATUSES = frozenset({STATUS_SENT, "submitting", "cancel_unknown", "modify_unknown"})


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
    # unknown은 날짜가 오래됐다는 이유만으로 대사 대상에서 사라지면 안 된다.
    frame = store.get(ORDERS, as_of=as_of)
    if frame.empty:
        return []
    # 시장을 섞지 않는다 — KR 대사에 US 주문이 들어오면 계좌·조회 경로가 어긋난다.
    # entity_id 접두사(``KR:``/``US:``)로 이 시장 것만 남긴다.
    frame = frame[
        (frame["status"].isin(UNRESOLVED_STATUSES | UNVERIFIED_TERMINAL))
        & frame["entity_id"].astype(str).str.startswith(f"{market}:")
    ]
    filled = filled_quantities(store, as_of=as_of)
    cancelled = cancelled_quantities(store, as_of=as_of)
    bindings = submission_bindings(store, as_of=as_of)
    out: list[PendingFill] = []
    for row in frame.itertuples(index=False):
        reason = str(getattr(row, "reason", "") or "")
        # 행이 자기 세션을 들고 있으면 그걸 쓴다(옛 세션 고아도 정확히 식별). 없으면
        # 넘겨받은 현재 세션으로 메운다.
        row_session = str(getattr(row, "session_id", "") or session_id)
        logical = key(row_session, str(row.entity_id), int(row.slice_seq))
        hashed = client_order_id(
            session=row_session, entity_id=str(row.entity_id), slice_seq=int(row.slice_seq)
        )
        accounted = filled.get(logical, 0.0) + filled.get(hashed, 0.0) + cancelled.get(logical, 0.0)
        if accounted > float(row.quantity):
            raise ValueError("fill/cancellation ledger exceeds original order")
        if accounted == float(row.quantity):
            continue
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
                observed_day=(date.fromisoformat(bindings[logical]["order_day"]) if logical in bindings
                              else row.observed_at.tz_convert(
                    ZoneInfo("America/New_York" if market == "US" else "Asia/Seoul")
                ).date()),
            )
        )
    return out


def missing_broker_ids(store: Store, *, as_of: datetime, market: str) -> int:
    frame = store.get(ORDERS, as_of=as_of, market=market)
    if frame.empty:
        return 0
    return int((frame["status"].isin(UNRESOLVED_STATUSES)
                & ~frame["reason"].fillna("").str.startswith(BROKER_ORDER_NO_PREFIX)).sum())


def unverified_remainders(
    store: Store, *, as_of: datetime, market: str, venue_day: date | None = None
) -> int:
    """잔량이 최종 확정되지 않은 주문 수.

    ``venue_day`` 를 주면 **그 거래소 날짜에 관측된 주문만** 센다. 지난 거래일 것은
    t0425(당일 주문만 조회)로는 구조적으로 답이 나오지 않는다 — 그것까지 매일 세면
    rc 가 영영 1 로 굳어 진짜 실패를 덮는다(2026-09-11 15:45 실측 178건). 지난 날
    잔량은 거래소가 소멸시켰고(day order), 체결 누락 여부는 D+2 정산 대조가 답한다.
    """
    frame = store.get(ORDERS, as_of=as_of, market=market)
    if frame.empty:
        return 0
    frame = frame[frame["status"].isin(UNVERIFIED_TERMINAL | {"cancel_unknown", "modify_unknown"})]
    if venue_day is not None and not frame.empty:
        zone = ZoneInfo("America/New_York" if market == "US" else "Asia/Seoul")
        frame = frame[frame["observed_at"].dt.tz_convert(zone).dt.date == venue_day]
    filled = filled_quantities(store, as_of=as_of)
    cancelled = cancelled_quantities(store, as_of=as_of)
    count = 0
    for row in frame.itertuples(index=False):
        if not str(row.reason).startswith(BROKER_ORDER_NO_PREFIX):
            continue
        logical = key(str(row.session_id), str(row.entity_id), int(row.slice_seq))
        hashed = client_order_id(
            session=str(row.session_id), entity_id=str(row.entity_id), slice_seq=int(row.slice_seq)
        )
        accounted = filled.get(logical, 0.0) + filled.get(hashed, 0.0) + cancelled.get(logical, 0.0)
        if accounted > float(row.quantity):
            raise ValueError("fill/cancellation ledger exceeds original order")
        count += accounted < float(row.quantity)
    return int(count)


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
    refresh_order_states(store, clock)
    market = Market(args.market)
    day = date.fromisoformat(args.day) if args.day else last_settled_day(store, market, clock.now())
    if day is None:
        print("거래일을 찾지 못했다.", file=sys.stderr)
        return 2
    session_id = f"{args.market}-{day.isoformat()}"
    now = clock.now()
    pending = pending_from_orders(store, as_of=now, market=args.market, session_id=session_id)
    print(f"{args.market} 세션 {session_id} · 창고 {store.root} · sent {len(pending)}건")
    missing = missing_broker_ids(store, as_of=now, market=args.market)
    if missing:
        print(f"주문번호 미확정 {missing}건 — 자동 재전송·예약 해제 금지", file=sys.stderr)
    if not pending:
        if missing:
            return 1
        # TWAP 전환(2026-09-02) 뒤로는 조각 배포·재호가가 장중에 체결·포기를 다 확정하므로 15:45 에
        # `sent` 가 남지 않는 것이 정상이다. 그 세션의 주문이 창고에 있으면 "이미 끝났다"(0), 주문
        # 자체가 없으면 "세션이 안 돌았다"(2) — 둘을 같은 rc 로 내보내면 크론이 매일 경보를 낸다.
        frame = store.get("orders", as_of=now, lookback=7, market=args.market)
        n = int((frame["session_id"] == session_id).sum()) if not frame.empty else 0
        if n:
            print(f"세션 기록 {n}건 중 현재 브로커 체결 대사 대상이 없다.")
            return 0
        print("대사할 주문이 없다 — 이 세션의 주문이 창고에 없다.")
        return 2

    profile = resolve_profile(store, market=args.market, as_of=now)
    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    print(f"계좌 — 모드 키 {profile.env_prefix} · 지문 {credentials.fingerprint or '(없음)'} · 선언 {credentials.kind or '(미선언)'}")
    client = LSClient(credentials=credentials, live_trading=True, min_interval_sec=profile.min_interval_sec)

    result = sync_fills(store, client, clock, as_of=now, pending=pending)
    # **지난 거래일 주문은 t0425 로 답할 수 없다** — 그 TR 은 당일 주문만 준다. 이것을
    # 오늘의 실패와 같은 rc 로 내보내면 매일 1 이라 경보가 죽는다. 밀린 건수는 따로 센다.
    venue = ZoneInfo("America/New_York" if args.market == "US" else "Asia/Seoul")
    today = now.astimezone(venue).date()
    backlog = {
        p.order_id for p in pending if p.observed_day is not None and p.observed_day != today
    }
    unknown = 0
    unknown_backlog = 0
    for outcome in result.outcomes:
        if outcome.state is FillState.UNKNOWN:
            if outcome.order_id in backlog:
                unknown_backlog += 1
            else:
                unknown += 1
            mark = "모른다"
        elif outcome.state is FillState.RECORDED:
            mark = "체결"
        else:
            mark = "변동없음"
        qty = outcome.fill.quantity if outcome.fill else outcome.cumulative_quantity
        price = f" @ {outcome.fill.price:,.0f}" if outcome.fill else ""
        print(f"  {mark:<4} {outcome.order_id} · {qty if qty is not None else '-'}주{price} {outcome.detail}")
    print(
        f"trades {result.rows_written}행 적재 · "
        f"모름 {unknown}건(오늘) + {unknown_backlog}건(지난 거래일)"
    )

    unverified = unverified_remainders(store, as_of=now, market=args.market, venue_day=today)
    carried = unverified_remainders(store, as_of=now, market=args.market) - unverified
    if unverified:
        print(f"잔량 최종 상태 미확정 {unverified}건 — 체결량 확인은 취소 확정이 아니다")
    if unknown_backlog or carried:
        print(
            f"지난 거래일 미확정 {max(unknown_backlog, carried)}건 — t0425 는 당일만 답한다. "
            "거래소가 잔량을 소멸시켰고 체결 누락은 정산 대조(D+2)가 잡는다. "
            "주문일 지정 대사는 후속 작업이다 (docs/design/execution-safety.md)"
        )
    return 1 if unknown or missing or unverified else 0



if __name__ == "__main__":
    raise SystemExit(main())
