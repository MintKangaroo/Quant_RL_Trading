"""Authenticated KR SC2/SC3 evidence; REST receipts never release reservations.

Only the account-pinned websocket collector calls ``accept`` in production. Raw
messages (which include account details) are never persisted. Missing or ambiguous
submission/action identity leaves the order unresolved.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from quant_rl_trading.collectors.market_hours import Market, local_time
from quant_rl_trading.executor.action_journal import (
    ActionJournal,
    decode,
    events,
    last_intent,
    record,
    refresh_order_states,
    resolved,
)
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store
from quant_rl_trading.store.locking import account_lock


class UnmatchedConfirmation(ValueError):
    """No unique local submission/action can authenticate this notification."""


def number(value: object) -> str:
    raw = str(value).strip()
    if not raw.isascii() or not raw.isdigit() or int(raw) <= 0:
        raise ValueError("invalid broker order number/quantity")
    return str(int(raw))


def accept(
    store: Store, clock: Clock, message: dict, *, fingerprint: str, connected_at: datetime,
    evidence: str = "SC3", require_full: bool = False, order_day: date | None = None,
) -> bool:
    """Return True only for newly persisted, verified evidence. No broker calls.

    ``evidence`` — 확인의 출처. 기본은 실시간 SC2/SC3. **모의계좌에서만** 주문체결내역 조회(CSPAQ13700)의 "취소확인" 행을
    같은 규칙으로 받는다(`accept_inquiry_cancel`, docs/design/execution-safety.md 2026-09-23 절). 그때는 ``require_full`` —
    취소 수량이 남은 수량과 **정확히 같을 때만**(주문이 완전히 닫힐 때만) 받는다.
    """
    now = clock.now()
    # 실시간 SC3 는 "오늘" 수신만 받는다. 조회 증거(evidence != SC3)는 주문일을 명시해 지난 날도 확인할 수 있다.
    day = order_day if (order_day is not None and evidence != "SC3") else local_time(Market.KR, now).date()
    if not fingerprint or local_time(Market.KR, connected_at).date() != day:
        raise ValueError("unpinned account or stream crossed trading date")
    header, body = message.get("header", {}), message.get("body", {})
    code = header.get("tr_cd")
    if code not in {"SC2", "SC3"} or not isinstance(body, dict):
        raise ValueError("not a KR modification/cancellation confirmation")
    parent, broker_no = number(body.get("orgordno")), number(body.get("ordno"))
    if parent == broker_no:
        raise ValueError("confirmation number must differ from original order")
    symbol = str(body.get("shtnIsuno", "")).strip().removeprefix("A")
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
        raise ValueError("invalid KR instrument")
    entity = f"KR:{symbol}"
    side = {"1": "sell", "2": "buy"}.get(str(body.get("bnstp", "")).strip())
    if side is None:
        raise ValueError("invalid confirmation side")
    quantity = int(number(body.get("mdfycnfqty" if code == "SC2" else "canccnfqty")))
    raw_time = str(body.get("exectime", "")).strip()
    if len(raw_time) != 9 or not raw_time.isascii() or not raw_time.isdigit():
        raise ValueError("invalid confirmation time")
    at = datetime.combine(
        day, datetime.strptime(raw_time, "%H%M%S%f").time(), tzinfo=ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    if at > now:
        raise ValueError("confirmation outside authenticated stream interval")
    price = float(body.get("mdfycnfprc", 0)) if code == "SC2" else None
    if price is not None and (not math.isfinite(price) or price <= 0):
        raise ValueError("invalid confirmed price")
    kind = "modify_confirmed" if code == "SC2" else "cancel_confirmed"
    proof = {
        "broker_order_no": broker_no,
        "parent_order_no": parent,
        "quantity": quantity,
        "price": price,
        "side": side,
        "order_day": day.isoformat(),
        "fingerprint": fingerprint,
        "time": raw_time,
    }
    if evidence != "SC3":
        proof["evidence"] = evidence
    store = store.execution_view()
    with account_lock(store.root):
        all_events = events(store, as_of=now)
        # Deduplicate before selecting the current intent: a replay may arrive after
        # another action on the same chain. Conflicting evidence is never additive.
        for event in all_events:
            p = event["payload"]
            if (
                event["kind"] == kind
                and p.get("broker_order_no") == broker_no
                and p.get("order_day") == day.isoformat()
                and p.get("fingerprint") == fingerprint
            ):
                if event["entity_id"] != entity or any(p.get(k) != v for k, v in proof.items()):
                    raise ValueError("conflicting confirmation")
                refresh_order_states(store, clock, order_ids={event["order_id"]})
                return False
        if at < connected_at.replace(microsecond=connected_at.microsecond // 1000 * 1000):
            raise ValueError("new confirmation predates authenticated stream")
        matches = []
        for binding in (e for e in all_events if e["kind"] == "submitted"):
            bound = binding["payload"]
            if (
                binding["entity_id"] != entity
                or bound["order_day"] != day.isoformat()
                or bound["fingerprint"] != fingerprint
                or bound["side"] != side
            ):
                continue
            history = [e for e in all_events if e["order_id"] == binding["order_id"]]
            intent = last_intent(history)
            if intent is None or resolved(history, intent):
                continue
            before = decode(intent["payload"]["before"])
            if number(before.broker_order_no) != parent:
                continue
            chain_no = bound["broker_order_no"]
            for previous in sorted(history, key=lambda e: e["valid_from"]):
                if previous["kind"] == "modify_confirmed":
                    chain_no = previous["payload"]["broker_order_no"]
            if number(chain_no) != parent or int(bound["quantity"]) != before.original_quantity:
                continue
            matches.append((intent, before, history))
        if len(matches) != 1:
            raise UnmatchedConfirmation("no unique dated account submission/action")
        intent, before, history = matches[0]
        proposed = decode(intent["payload"]["proposed"])
        if at < intent["valid_from"].floor("ms").to_pydatetime():
            raise ValueError("confirmation predates action intent")
        if (code == "SC2") != (intent["payload"]["action"] == "reprice"):
            raise ValueError("confirmation action does not match intent")
        receipt = next(
            (
                e["payload"]
                for e in history
                if e["kind"] == "receipt" and e["payload"]["intent_id"] == intent["event_id"]
            ),
            None,
        )
        # Legacy adapters sometimes returned the parent number; it isn't a child ID.
        if receipt and receipt["broker_order_no"] not in {"", parent, broker_no}:
            raise ValueError("confirmation number differs from receipt")
        if quantity > proposed.remaining_quantity:
            raise ValueError("confirmed quantity exceeds requested action")
        if code == "SC2" and (
            quantity != proposed.remaining_quantity or price != proposed.limit_price
        ):
            raise ValueError("partial or mismatched modification requires reconciliation")
        if code == "SC3":
            remaining = ActionJournal(store, clock, None).restore(before).remaining_quantity
            if quantity > remaining:
                raise ValueError("cancellation and recorded fills exceed original quantity")
            if require_full and quantity != remaining:
                raise ValueError("inquiry evidence must close the order exactly (cancel == remaining)")
        proof["intent_id"] = intent["event_id"]
        added = record(
            store,
            clock,
            order_id=before.order_id,
            entity=entity,
            kind=kind,
            identity=f"{day.isoformat()}|{fingerprint}|{broker_no}",
            payload=proof,
            at=at,
        )
        refresh_order_states(store, clock, order_ids={before.order_id})
        return added


INQUIRY_EVIDENCE = "CSPAQ13700"


def accept_inquiry_cancel(
    store: Store, clock: Clock, row: dict, *, fingerprint: str, mode: str, day: date,
) -> bool:
    """주문체결내역(CSPAQ13700) 한 행 → 취소 확인 증거. **모의계좌 전용.**

    모의투자 서버는 SC3 를 보내지 않는다(2026-09-23 실측: 주문감시 하루 종일 확인 0건, 조회로는 매번 "취소확인·체결 0").
    그래서 매일 15:45 대사가 rc=1 을 냈다. 실계좌는 SC3 만 인정한다 — 조회 상태 코드의 최종성을 실계좌에서 확인하기 전까지.
    검증은 SC3 와 **같은 함수**(`accept`)가 한다: 계좌 지문·주문일·종목·방향·원주문번호가 우리 전송 기록과 맞고, 취소 의도가 있고,
    취소 수량이 남은 수량과 정확히 같아야 한다.
    """
    if mode != "paper":
        raise ValueError("inquiry cancellation evidence is accepted for the paper account only")
    if str(row.get("MrcTpNm", "")).strip() != "취소확인":
        raise ValueError("not a cancellation confirmation row")
    raw_time = str(row.get("OrdTime", "")).replace(":", "").strip()
    if len(raw_time) != 6 or not raw_time.isdigit():
        raise ValueError("invalid inquiry time")
    message = {
        "header": {"tr_cd": "SC3"},
        "body": {
            "orgordno": row.get("OrgOrdNo"), "ordno": row.get("OrdNo"), "shtnIsuno": str(row.get("IsuNo", "")),
            # 조회 시각은 초까지만 온다 — 그 초의 **끝**으로 본다. 끝으로 안 보면 같은 초 안(밀리초 뒤)에 적힌 취소 의도보다
            # 확인이 앞선 것으로 읽혀 거부된다(2026-09-23 실측 세 건). 확인 시각이 지금보다 늦을 수는 없다(accept 가 지킨다).
            "bnstp": str(row.get("BnsTpCode", "")), "canccnfqty": row.get("OrdQty"), "exectime": raw_time + "999",
        },
    }
    start = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Seoul")).astimezone(UTC)
    return accept(store, clock, message, fingerprint=fingerprint, connected_at=start,
                  evidence=INQUIRY_EVIDENCE, require_full=True, order_day=day)
