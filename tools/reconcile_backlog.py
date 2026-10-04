"""지난 거래일의 미확정 주문을 **주문일을 지정해** 대사한다. 기본은 미리보기.

    .venv/bin/python tools/reconcile_backlog.py                    # 미리보기(적재 안 함)
    .venv/bin/python tools/reconcile_backlog.py --apply            # 확정된 것만 적재
    .venv/bin/python tools/reconcile_backlog.py --day 2026-09-18   # 그 주문일만
    .venv/bin/python tools/reconcile_backlog.py --confirm-not-received 'KR-2026-09-21|KR:005945|1'          # 미리보기
    .venv/bin/python tools/reconcile_backlog.py --confirm-not-received 'KR-2026-09-21|KR:005945|1' --apply  # 닫기

15:45 대사(``reconcile_fills.py``)는 t0425 를 쓰고 **그 TR 은 당일만 답한다** — 그래서 지난
거래일의 미확정 주문은 매일 같은 줄로 다시 보고될 뿐 확정되지 않았다. ``CSPAQ13700`` 은
``OrdDt`` 를 받는다. 설계와 신원 규칙은 ``docs/design/execution-safety.md`` 2026-09-27 절.

**조회만 한다.** 주문·정정·취소 TR 을 부르지 않는다. 증거가 없는 건은 그대로 미확정으로
남긴다 — 날짜가 지났다는 것은 취소 증거가 아니다.

``--confirm-not-received`` 는 주문번호 없이 남은 조각(전송 응답 미수신)을 **사람이** 닫는 길이다.
주문일 후보를 전부 조회해 같은 종목·방향 행이 모두 우리 다른 조각 번호로 설명될 때만 미도착을
확인하고, ``--apply`` 면 ``orders`` 에 ``rejected`` revision 을 적는다(``execution-safety.md``
2026-10-04 절). 설명 안 되는 행이 있으면 거부한다.

종료코드: 0 미확정 없음(미도착 확인됨) · 1 사람 확인이 필요한 건이 있다(미도착 거부) · 2 대사할 주문이 없다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from typing import Any
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker import backlog  # noqa: E402
from quant_rl_trading.broker.factory import ACCOUNT_MODE_KEY  # noqa: E402
from quant_rl_trading.broker.fills import FillState, sync_fills  # noqa: E402
from quant_rl_trading.broker.order_confirmations import accept_inquiry_cancel  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, local_time  # noqa: E402
from quant_rl_trading.dashboard.services.account import _client  # noqa: E402
from quant_rl_trading.executor.action_journal import refresh_order_states  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402


def classify(
    match: backlog.Match, corrections: dict[tuple[date, str], float] | None = None
) -> tuple[str, str]:
    """(분류, 설명). 분류는 보고와 적재 판단에 같이 쓴다.

    ``corrections`` — 스냅샷 대사가 이미 장부에 넣어 둔 정정 수량. **그 종목·그날에 정정이
    있으면 체결을 적지 않는다.** 같은 체결을 두 번 세는 유일한 경로다
    (``backlog.snapshot_corrections`` 참고).
    """
    item = match.candidate
    if match.reason:
        return "증거없음", match.reason
    assert match.day is not None
    delta = match.cumulative - item.filled
    if delta > 0:
        covered = (corrections or {}).get((match.day, item.entity_id))
        if covered:
            return "체결·스냅샷중복", (
                f"{match.day} 신규 {delta:g}주지만 그날 스냅샷 정정 {covered:+g}주가 이미 장부에 있다 "
                "— 적으면 이중계상. 정정을 무효화하는 절차가 먼저다"
            )
        return "체결", f"{match.day} 누적 {match.cumulative:g}주 · 장부 {item.filled:g}주 → 신규 {delta:g}주"
    if match.cumulative < item.filled:
        return "역행", f"{match.day} 조회 누적 {match.cumulative:g} < 장부 {item.filled:g} — 사람 확인"
    if match.cancelled > 0:
        if match.cancelled != item.remaining:
            return "부분취소", f"{match.day} 취소확인 {match.cancelled:g}주 ≠ 잔량 {item.remaining:g}주 — 미확정 유지"
        if item.bound_day is None:
            return "취소·저널없음", f"{match.day} 취소확인 {match.cancelled:g}주 — 전송 저널이 없어 표기 불가"
        return "취소", f"{match.day} 취소확인 {match.cancelled:g}주 = 잔량"
    return "체결0", f"{match.day} 그날 체결 0 · 잔량 {item.remaining:g}주는 당일 소멸(표기는 후속 범위)"


def confirm_not_received(store: Store, clock: LiveClock, order_id: str, *, market: str, apply: bool) -> int:
    """번호 없는 조각 하나의 미도착을 확인하고, ``apply`` 면 닫는다. 조회 TR 만 부른다."""
    now = clock.now()
    today = local_time(Market.KR, now).date()
    found = [c for c in backlog.candidates(store, as_of=now, market=market, today=today) if c.order_id == order_id]
    if not found:
        print(f"{order_id}: 지난 거래일 미확정 주문이 아니다(이미 닫혔거나 오늘 것이거나 ID 가 틀렸다).")
        return 2
    (item,) = found
    print(f"{item.order_id} · {item.side.value} {item.quantity:g}주 · 상태 {item.status} · "
          f"주문일 후보 {', '.join(str(d) for d in item.days)}")
    client = _client(store, as_of=now, market=market)
    print(f"계좌 지문 {getattr(client.credentials, 'fingerprint', '') or '(없음)'} · "
          f"선언 {getattr(client.credentials, 'kind', '') or '(미선언)'}")
    days: dict[date, tuple[dict[str, Any], dict[str, Any]]] = {}
    for day in item.days:
        try:
            rows = backlog.fetch_day(client, day)
        except Exception as error:  # 못 물어본 날은 "그날 주문 없음" 이 아니다 — 비워 두면 거부된다.
            print(f"  ⚠️  {day} 조회 실패: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        days[day] = backlog.index_day(rows)
        print(f"  {day} 조회 {len(rows)}행")
    result = backlog.check_not_received(item, days, backlog.known_orders(store, as_of=now, market=market))
    for day, number, owner in result.same_symbol:
        print(f"  같은 종목·방향 {day} 주문번호 {number} → {owner or '설명 안 됨'}")
    if not result.confirmed:
        print(f"미도착 확인 거부 — {result.refusal}")
        return 1
    print(result.reason())
    if not apply:
        print("미리보기다 — 아무것도 적지 않았다. 닫으려면 --apply.")
        return 0
    written = backlog.record_not_received(store, clock, result)
    print(f"orders {written}행 적재 — {item.order_id} → {backlog.NOT_RECEIVED_STATUS}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="KR", choices=["KR"],
                        help="국장만. 모의계좌는 해외주식을 지원하지 않는다(rsp_cd 01900)")
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--day", help="이 주문일만 대사한다 (기본: 오늘 뺀 전부)")
    parser.add_argument("--apply", action="store_true", help="확정된 것을 창고에 적재한다")
    parser.add_argument("--confirm-not-received", metavar="ORDER_ID",
                        help="주문번호 없는 조각의 미도착 확인 ('세션|종목|조각'). --apply 면 rejected 로 닫는다")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    if args.confirm_not_received:
        return confirm_not_received(store, clock, args.confirm_not_received, market=args.market, apply=args.apply)
    if args.apply:
        # 미리보기는 창고를 건드리지 않는다. 상태 되적기는 15:45 대사가 매일 이미 한다.
        refresh_order_states(store, clock)
    mode = str(store.config(ACCOUNT_MODE_KEY, as_of=now)).strip().lower()
    today = local_time(Market.KR, now).date()

    items = backlog.candidates(store, as_of=now, market=args.market, today=today)
    if args.day:
        only = date.fromisoformat(args.day)
        items = [i for i in items if only in i.days]
    print(f"{args.market} · 창고 {store.root} · 계좌 모드 {mode} · 오늘 {today}")
    if not items:
        print("지난 거래일 미확정 주문이 없다.")
        return 2

    wanted = sorted({day for item in items for day in item.days} - {today})
    if args.day:
        wanted = [date.fromisoformat(args.day)]
    print(f"미확정 {len(items)}건 · 조회할 주문일 {len(wanted)}일: {', '.join(d.isoformat() for d in wanted)}")

    client = _client(store, as_of=now, market=args.market)
    fingerprint = str(getattr(client.credentials, "fingerprint", "") or "")
    print(f"계좌 지문 {fingerprint or '(없음)'} · 선언 {getattr(client.credentials, 'kind', '') or '(미선언)'}")

    days: dict[date, tuple[dict[str, Any], dict[str, Any]]] = {}
    for day in wanted:
        try:
            rows = backlog.fetch_day(client, day)
        except Exception as error:  # 조회 실패는 "체결 0" 이 아니다 — 그 날을 비워 둔다.
            print(f"  ⚠️  {day} 조회 실패: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        days[day] = backlog.index_day(rows)
        print(f"  {day} 조회 {len(rows)}행")

    matches = backlog.match_all(items, days, backlog.known_orders(store, as_of=now, market=args.market))
    # **스냅샷 정정을 먼저 읽는다.** 그게 이미 장부에 넣은 체결을 또 적으면 포지션이 두 배가 된다.
    corrections = backlog.snapshot_corrections(store, as_of=now)
    tally: Counter[str] = Counter()
    labelled = []
    for match in sorted(matches, key=lambda m: (m.day or date.min, m.candidate.order_id)):
        kind, detail = classify(match, corrections)
        # 지금 조회하는 계좌가 그 주문을 낸 계좌가 아니면 취소 증거를 받을 수 없다(`accept` 규칙).
        if kind == "취소" and match.candidate.bound_fingerprint != fingerprint:
            kind = "취소·지문불일치"
            detail += f" — 전송 계좌 지문 {match.candidate.bound_fingerprint or '(없음)'} ≠ 지금 {fingerprint}"
        tally[kind] += 1
        labelled.append((kind, detail, match))

    print("\n분류      건수")
    for kind, count in tally.most_common():
        print(f"  {kind:<10} {count}")
    print()
    for kind, detail, match in labelled:
        item = match.candidate
        print(f"  {kind:<12} {item.order_id:<34} {item.status:<14} {item.quantity:g}주 · {detail}")
        for orphan in match.orphans:
            print(f"      후보(적재 안 함) {orphan['_day']} 주문번호 {orphan['OrdNo']} "
                  f"{orphan['MrcTpNm']} 체결 {orphan['AllExecQty']} @ {orphan['ExecPrc']}")

    if not args.apply:
        print("\n미리보기다 — 아무것도 적지 않았다. 적재는 --apply.")
        needs_human = sum(
            tally[kind]
            for kind in (
                "증거없음", "역행", "부분취소", "취소·저널없음", "취소·지문불일치",
                "체결·스냅샷중복",
            )
        )
        return 1 if needs_human else 0

    # -- 적재 -------------------------------------------------------------------
    # 체결: 주문일별로 sync_fills. 차분·자연키·비용·잠금·상태 되적기는 그쪽 규약 그대로다.
    written = recorded = 0
    by_day: dict[date, list[backlog.Match]] = {}
    for kind, _detail, match in labelled:
        if kind == "체결" and match.day is not None:
            by_day.setdefault(match.day, []).append(match)
    for day, group in sorted(by_day.items()):
        folded, _raw = days[day]
        result = sync_fills(
            store, client, clock, as_of=now,
            pending=[backlog.pending_for(m) for m in group],
            queries={"KR": backlog.dated_query(day, folded)},
        )
        written += result.rows_written
        for outcome in result.outcomes:
            mark = {FillState.RECORDED: "체결", FillState.UNCHANGED: "변동없음"}.get(outcome.state, "모른다")
            recorded += outcome.state is FillState.RECORDED
            quantity = outcome.fill.quantity if outcome.fill else outcome.cumulative_quantity
            print(f"  적재 {mark:<6} {outcome.order_id} · {quantity if quantity is not None else '-'}주 {outcome.detail}")

    # 취소: 2026-09-23 절의 같은 함수·같은 규칙(모의 전용, 주문을 정확히 닫는 증거만).
    confirmed = refused = 0
    for kind, _detail, match in labelled:
        if kind != "취소" or match.day is None:
            continue
        row = next(
            r for r in days[match.day][1].values()
            if str(r.get("MrcTpNm", "")).strip() == "취소확인"
            and str(r.get("OrgOrdNo") or "").strip().lstrip("0")
            == match.candidate.broker_order_no.strip().lstrip("0")
        )
        try:
            added = accept_inquiry_cancel(
                store, clock, row, fingerprint=fingerprint, mode=mode, day=match.day
            )
        except ValueError as error:  # UnmatchedConfirmation 도 ValueError 다
            refused += 1
            print(f"  적재 거부   {match.candidate.order_id} — {error}")
            continue
        confirmed += int(added)
        print(f"  적재 취소   {match.candidate.order_id} · 취소확인 {match.cancelled:g}주"
              + ("" if added else " (이미 있음)"))

    print(f"\ntrades {written}행 · 체결 확정 {recorded}건 · 취소 확정 {confirmed}건 · 거부 {refused}건")
    if tally["체결·스냅샷중복"]:
        print(f"체결 {tally['체결·스냅샷중복']}건은 적지 않았다 — 스냅샷 정정과 이중계상된다.")
    return 1 if refused or tally["증거없음"] or tally["역행"] or tally["체결·스냅샷중복"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
