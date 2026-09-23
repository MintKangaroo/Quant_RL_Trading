"""모의계좌 취소 확인 — 주문체결내역(CSPAQ13700) 조회로. docs/design/execution-safety.md "2026-09-23 모의계좌 취소 확인".

    .venv/bin/python tools/confirm_cancels_inquiry.py [--day 2026-09-23] [--dry-run]

모의투자 서버는 SC3(취소 확인 실시간)를 보내지 않는다 — 추격 도구가 장중에 취소한 주문이 "취소 미확정" 으로 남아 매일 15:45 대사가
rc=1 을 냈다. 이 도구는 그날 **취소 의도가 있고 아직 확인이 없는** 주문마다 증권사 주문체결내역에서 원주문번호가 같은 "취소확인" 행을
찾아, SC3 와 같은 검증(`order_confirmations.accept_inquiry_cancel`)을 통과한 것만 증거로 적는다. **조회만 한다 — 주문을 내지 않는다.**
실계좌 모드면 아무것도 하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.order_confirmations import accept_inquiry_cancel  # noqa: E402
from quant_rl_trading.collectors.ls_client import PATH_ACCNO  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, local_time  # noqa: E402
from quant_rl_trading.dashboard.services.account import _client  # noqa: E402
from quant_rl_trading.executor.action_journal import (  # noqa: E402
    decode,
    events,
    last_intent,
    resolved,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402
from tools.watch_order_events import ACCOUNT_MODE_KEY  # noqa: E402

TR = "CSPAQ13700"
MAX_PAGES = 30


def inquiry_rows(client, day: date) -> list[dict]:
    """그날 주문체결내역 전부 — 헤더 연속 키로 끝까지(00704 는 '다음 쪽 있음')."""
    rows, cont, key = [], "N", ""
    for _ in range(MAX_PAGES):
        body = {f"{TR}InBlock1": {"RecCnt": 1, "OrdMktCode": "00", "BnsTpCode": "0", "IsuNo": "", "ExecYn": "0",
                                  "OrdDt": day.strftime("%Y%m%d"), "SrtOrdNo2": 0, "BkseqTpCode": "0", "OrdPtnCode": "00"}}
        data = client.request_tr(PATH_ACCNO, TR, body, with_headers=True, tr_cont=cont, tr_cont_key=key)
        rows += data.get(f"{TR}OutBlock3") or []
        head = data.get("_cont") or {}
        if head.get("tr_cont") != "Y" or not head.get("tr_cont_key"):
            return rows
        cont, key = "Y", head["tr_cont_key"]
    raise RuntimeError(f"{TR} 쪽이 {MAX_PAGES} 을 넘는다 — 다 못 읽었다")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--day", default="", help="주문일(기본: 오늘, KST)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    now = clock.now()
    day = date.fromisoformat(args.day) if args.day else local_time(Market.KR, now).date()
    store = Store(root=overlay.build(root=Path(args.sandbox), source=build_store(None).root, writable=JOURNAL).root)
    mode = str(store.config(ACCOUNT_MODE_KEY, as_of=now)).strip().lower()
    if mode != "paper":
        print(f"계좌 모드 {mode} — 모의계좌 전용 도구다. 아무것도 하지 않는다.")
        return 0

    history = events(store, as_of=now)
    by_order: dict[str, list[dict]] = {}
    for event in history:
        by_order.setdefault(str(event["order_id"]), []).append(event)
    pending = {}
    for order_id, group in by_order.items():
        submitted = next((e for e in group if e["kind"] == "submitted"), None)
        intent = last_intent(group)
        if submitted is None or intent is None or resolved(group, intent):
            continue
        if intent["payload"].get("action") == "reprice" or submitted["payload"].get("order_day") != day.isoformat():
            continue
        pending[order_id] = (submitted, decode(intent["payload"]["before"]))
    if not pending:
        print(f"{day}: 확인 기다리는 취소 없음")
        return 0

    client = _client(store, as_of=now, market="KR")
    fingerprint = str(getattr(client.credentials, "fingerprint", "") or "")
    rows = inquiry_rows(client, day)
    cancels = {str(int(r["OrgOrdNo"])): r for r in rows
               if str(r.get("MrcTpNm", "")).strip() == "취소확인" and int(r.get("OrgOrdNo") or 0) > 0}
    confirmed = missing = rejected = 0
    for order_id, (_submitted, before) in sorted(pending.items()):
        parent = str(int(before.broker_order_no)) if before.broker_order_no else ""
        row = cancels.get(parent)
        if row is None:
            missing += 1
            print(f"  없음   {order_id} · 원주문 {parent or '?'} — 조회에 취소확인 행이 없다(미확정 유지)")
            continue
        if args.dry_run:
            print(f"  (dry) {order_id} · 원주문 {parent} → 취소확인 {row['OrdQty']}주 {row['OrdTime']}")
            continue
        try:
            added = accept_inquiry_cancel(store, clock, row, fingerprint=fingerprint, mode=mode, day=day)
        except ValueError as exc:
            rejected += 1
            print(f"  거부   {order_id} · 원주문 {parent} — {exc}")
            continue
        confirmed += int(added)
        print(f"  확인   {order_id} · 원주문 {parent} → 취소확인 {row['OrdQty']}주 {row['OrdTime']}" + ("" if added else " (이미 있음)"))
    print(f"{day}: 조회 {len(rows)}행 · 대상 {len(pending)} · 확인 {confirmed} · 조회에 없음 {missing} · 거부 {rejected}")
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
