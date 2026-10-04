"""과거 주문일을 지정해 지난 거래일의 미확정 주문을 대사한다 (국장·조회 TR 만).

설계는 ``docs/design/execution-safety.md`` "2026-09-27 과거 주문일 지정 대사" 절이다.
15:45 대사(``tools/reconcile_fills.py``)는 t0425 를 쓰고 **그 TR 은 당일만 답한다** — 그래서
지난 거래일의 미확정 주문은 매일 같은 줄로 다시 보고될 뿐 영원히 확정되지 않았다(모의계좌
2026-08-28~09-22 사이 199건). ``CSPAQ13700``(주문체결내역조회)은 ``OrdDt`` 를 받는다:
지난 주문일을 지정하면 그날의 주문·누적체결·취소확인 행을 그대로 준다.

## 이 모듈이 지키는 것

1. **주문번호는 날짜마다 다시 1부터 센다.** 8/28 의 6777 과 9/2 의 6777 은 다른 주문이다.
   그래서 번호만으로 신원을 인정하지 않는다 — 번호·종목·방향·**원주문 수량** 네 개가 모두
   맞아야 우리 주문이다(:func:`identify`). 저널(``submitted``)이 있으면 그 주문일만 묻고,
   없는 legacy 행은 후보 날짜를 모두 묻되 **맞는 날이 정확히 하나일 때만** 받는다.
2. **체결 기록은 새로 만들지 않는다.** 차분·자연키·비용·잠금·상태 되적기는 이미
   :func:`~quant_rl_trading.broker.fills.sync_fills` 가 한다. 여기서는 그것에 끼울
   :class:`~quant_rl_trading.broker.fills.FillQuery` 를 만들어 준다.
3. **증거가 없으면 그대로 둔다.** 조회에 행이 없거나, 신원이 어긋나거나, 후보 날짜가 둘
   이상 맞으면 미확정으로 남긴다. 날짜 경과는 취소 증거가 아니다.
4. **조회만 한다.** 주문·정정·취소 TR 을 부르지 않는다.
5. **번호 없는 조각은 사람이 닫는다.** :func:`check_not_received` 가 그날 같은 종목 행이 전부
   우리 다른 주문 번호로 설명될 때만 미도착을 확인하고, :func:`record_not_received` 가 ``rejected``
   revision 을 적는다. 크론이 부르지 않는다(2026-10-04 절).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from quant_rl_trading.broker.fills import FillQuery, PendingFill, index_by_ordno
from quant_rl_trading.collectors.errors import LSAPIError
from quant_rl_trading.collectors.ls_client import PATH_ACCNO
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.executor.action_journal import cancelled_quantities, submission_bindings
from quant_rl_trading.executor.orders import client_order_id
from quant_rl_trading.executor.pipeline import BROKER_ORDER_NO_PREFIX
from quant_rl_trading.replay.events import payload_hash
from quant_rl_trading.risk.account import (
    UNVERIFIED_TERMINAL,
    filled_quantities,
    key,
    order_trading_day,
)
from quant_rl_trading.schemas.order import Side
from quant_rl_trading.store.locking import account_lock

if TYPE_CHECKING:
    from quant_rl_trading.collectors.ls_client import LSClient
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

__all__ = [
    "INQUIRY_TR",
    "Candidate",
    "KnownOrder",
    "Match",
    "NotReceived",
    "candidates",
    "check_not_received",
    "dated_query",
    "explain",
    "fetch_day",
    "identify",
    "known_orders",
    "match_all",
]

#: 국장 주문체결내역조회. ``OrdDt`` 로 **지난 주문일**을 지정할 수 있다.
INQUIRY_TR = "CSPAQ13700"
#: 연속 조회 상한. 한 날의 쪽수가 이걸 넘으면 다 못 읽었다고 터진다 — 조용히 자르지 않는다.
MAX_PAGES = 30
SEOUL = ZoneInfo("Asia/Seoul")
#: "조회내역이 없습니다"(실계좌) · "모의투자 조회할 내역(자료)이 없습니다"(모의).
#: **오류가 아니라 그날 내역이 0 건이다.**
EMPTY_CODES = frozenset({"02679", "00707"})

#: 조회 응답의 누적 체결수량·평균 체결가 필드. t0425·COSAQ00102 와 **또 다른 이름**이다.
#: ``OrdPrc``(지정가)를 가격 후보에 넣지 않는다 — 체결가를 못 읽었을 때 지정가를 체결가로
#: 적어 버린다. 못 읽으면 못 읽은 것으로 남겨야 한다.
QUANTITY_KEYS = ("AllExecQty", "ExecQty")
PRICE_KEYS = ("ExecPrc",)

#: 상태가 무엇이든 수량 원장이 원주문을 채우지 못한 행을 대사 대상으로 본다.
#: ``reconcile_fills.UNRESOLVED_STATUSES`` 와 같은 자리의 목록이다.
UNRESOLVED_STATUSES = frozenset({"sent", "submitting", "cancel_unknown", "modify_unknown"})


@dataclass(frozen=True)
class Candidate:
    """대사해야 할 지난 거래일 주문 한 건."""

    order_id: str
    entity_id: str
    side: Side
    quantity: float
    #: 없으면 전송 응답을 못 받은 것이다 — 조회로 찾을 번호가 없다.
    broker_order_no: str
    status: str
    #: 저널이 증명하는 주문일. 없으면 legacy 행이다.
    bound_day: date | None
    bound_fingerprint: str
    #: 주문일 후보(저널이 있으면 그 하루, 없으면 행이 가리키는 날들).
    days: tuple[date, ...]
    #: 이미 장부에 든 체결 수량.
    filled: float
    #: 이미 증거로 확정된 취소 수량.
    confirmed_cancel: float

    @property
    def accounted(self) -> float:
        return self.filled + self.confirmed_cancel

    @property
    def remaining(self) -> float:
        return self.quantity - self.accounted


@dataclass(frozen=True)
class Match:
    """후보 하나의 판정 결과."""

    candidate: Candidate
    #: 증거가 나온 주문일. 없으면 미확정이다.
    day: date | None = None
    row: dict[str, Any] | None = None
    #: 그날 브로커가 보고한 누적 체결 수량.
    cumulative: float = 0.0
    #: 취소확인 행이 있으면 그 수량.
    cancelled: float = 0.0
    #: 미확정 사유. 비어 있으면 증거를 찾았다.
    reason: str = ""
    #: 번호 없는 건의 사람 확인용 후보 행(적재하지 않는다).
    orphans: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def fetch_day(client: LSClient, day: date) -> list[dict[str, Any]]:
    """그 주문일의 주문체결내역 전부 — 헤더 연속 키로 끝까지.

    ``00704``("조회가 계속 됩니다")는 실패가 아니라 다음 쪽이 있다는 뜻이다. ``ls_client`` 는
    ``with_headers=True`` 로 부른 호출에서만 그걸 성공으로 받는다.

    :data:`EMPTY_CODES` 는 **조회는 됐고 그날 내역이 0 건**이라는 뜻이다(모의계좌 8/27 실측
    ``00707``). 이걸 조회 실패로 다루면 "그날 주문이 없었다" 와 "못 물어봤다" 가 섞인다 —
    어느 쪽이든 증거가 없어 미확정으로 남지만, 보고가 거짓이 된다.
    """
    rows: list[dict[str, Any]] = []
    cont, cont_key = "N", ""
    for _ in range(MAX_PAGES):
        body = {
            f"{INQUIRY_TR}InBlock1": {
                "RecCnt": 1, "OrdMktCode": "00", "BnsTpCode": "0", "IsuNo": "", "ExecYn": "0",
                "OrdDt": day.strftime("%Y%m%d"), "SrtOrdNo2": 0, "BkseqTpCode": "0",
                "OrdPtnCode": "00",
            }
        }
        try:
            data = client.request_tr(
                PATH_ACCNO, INQUIRY_TR, body, with_headers=True, tr_cont=cont, tr_cont_key=cont_key
            )
        except LSAPIError as error:
            if error.rsp_cd in EMPTY_CODES:
                return rows
            raise
        rows += data.get(f"{INQUIRY_TR}OutBlock3") or []
        head = data.get("_cont") or {}
        if head.get("tr_cont") != "Y" or not head.get("tr_cont_key"):
            return rows
        cont, cont_key = "Y", head["tr_cont_key"]
    raise RuntimeError(f"{INQUIRY_TR} 쪽이 {MAX_PAGES} 을 넘는다 — 다 못 읽었다")


def _legacy_days(row: Any) -> tuple[date, ...]:
    """저널 없는 주문 행의 주문일 후보.

    legacy 행의 주문일은 증명할 수 없다. ``session_id`` 의 날짜는 **주문일이 아니다** —
    세션은 전날 데이터로 짜고 다음 아침에 나가므로 348건 전부 하루 어긋난다(실측).
    그래서 행이 가리키는 두 시각(계획·관측)의 KST 날짜를 후보로 둔다.
    ``order_trading_day`` 도 같이 후보로 둔다 — 장 마감 뒤에 적힌 계획은 다음 거래일에
    나갔다(8/27 세션의 주문은 8/28 에 나간다). 후보가 늘어도 ②의 "정확히 하나" 규칙이
    남의 주문을 막는다.
    """
    return tuple(
        sorted(
            {
                row.valid_from.tz_convert(SEOUL).date(),
                row.observed_at.tz_convert(SEOUL).date(),
                order_trading_day(Market.KR, row.valid_from.to_pydatetime()),
                order_trading_day(Market.KR, row.observed_at.to_pydatetime()),
            }
        )
    )


def candidates(store: Store, *, as_of: datetime, market: str, today: date) -> list[Candidate]:
    """수량 원장이 원주문을 채우지 못한 **지난 거래일** 주문들.

    오늘 것은 빼 놓는다 — 15:45 대사의 몫이고, 장중에 아직 살아 있는 주문을 과거 취급하면
    안 된다. 상태로 걸러 내지 않는 이유는 ``abandoned``·``expired`` 로 표기된 행도 실제
    체결이 장부에 없으면 여전히 미확정이기 때문이다(``risk.account.UNVERIFIED_TERMINAL``).
    """
    if market != "KR":
        raise ValueError("dated reconciliation is implemented for KR only")
    frame = store.get("orders", as_of=as_of, market=market)
    if frame.empty:
        return []
    frame = frame[frame["status"].isin(UNRESOLVED_STATUSES | UNVERIFIED_TERMINAL)]
    filled = filled_quantities(store, as_of=as_of)
    cancelled = cancelled_quantities(store, as_of=as_of)
    bindings = submission_bindings(store, as_of=as_of)
    out: list[Candidate] = []
    for row in frame.itertuples(index=False):
        logical = key(str(row.session_id), str(row.entity_id), int(row.slice_seq))
        hashed = client_order_id(
            session=str(row.session_id), entity_id=str(row.entity_id), slice_seq=int(row.slice_seq)
        )
        recorded = filled.get(logical, 0.0) + filled.get(hashed, 0.0)
        confirmed_cancel = cancelled.get(logical, 0.0)
        accounted = recorded + confirmed_cancel
        if accounted > float(row.quantity):
            raise ValueError("fill/cancellation ledger exceeds original order")
        if accounted == float(row.quantity):
            continue
        reason = str(getattr(row, "reason", "") or "")
        broker_no = (
            reason[len(BROKER_ORDER_NO_PREFIX):].strip()
            if reason.startswith(BROKER_ORDER_NO_PREFIX)
            else ""
        )
        binding = bindings.get(logical)
        bound_day = date.fromisoformat(binding["order_day"]) if binding else None
        days = (bound_day,) if bound_day is not None else _legacy_days(row)
        days = tuple(day for day in days if day < today)
        if not days:
            continue
        out.append(
            Candidate(
                order_id=logical,
                entity_id=str(row.entity_id),
                side=Side(str(row.side)),
                quantity=float(row.quantity),
                broker_order_no=broker_no,
                status=str(row.status),
                bound_day=bound_day,
                bound_fingerprint=str(binding["fingerprint"]) if binding else "",
                days=days,
                filled=recorded,
                confirmed_cancel=confirmed_cancel,
            )
        )
    return out


#: 스냅샷 대사가 적는 정정 거래의 출처·주문키(``tools/reconcile_snapshot.py``).
SNAPSHOT_SOURCE = "snapshot_reconcile"
SNAPSHOT_ORDER_PREFIX = "snapshot-recon-"


def snapshot_corrections(store: Store, *, as_of: datetime) -> dict[tuple[date, str], float]:
    """(주문일, 종목) → 스냅샷 대사가 이미 장부에 적어 둔 **부호 있는** 정정 수량.

    **이것을 보지 않으면 같은 체결을 두 번 적는다.** ``reconcile_snapshot`` 은 주문별 대사가
    놓친 체결을 "브로커 보유 − 장부 보유" 차이로 되찾아 ``trades`` 에 정정 거래로 넣는다 —
    주문번호가 아니라 종목·날짜에 붙으므로 ``filled_quantities`` 는 그것을 원래 주문의 체결로
    세지 못한다. 그래서 주문일 대사가 같은 체결을 "장부에 없다" 로 보고, 적으면 포지션과
    현금이 두 배가 된다(2026-09-27 실측: 9/1 정정 6건이 그날 대사 대상 체결과 수량까지 같다).
    """
    frame = store.get("trades", as_of=as_of)
    if frame.empty:
        return {}
    frame = frame[frame["source"] == SNAPSHOT_SOURCE]
    out: dict[tuple[date, str], float] = {}
    for row in frame.itertuples(index=False):
        order_id = str(row.order_id)
        if not order_id.startswith(SNAPSHOT_ORDER_PREFIX) or "|" not in order_id:
            continue
        day_text, entity = order_id[len(SNAPSHOT_ORDER_PREFIX):].split("|", 1)
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        sign = 1.0 if str(row.side) == "buy" else -1.0
        out[(day, entity)] = out.get((day, entity), 0.0) + sign * float(row.quantity)
    return out


def _ordno(value: object) -> str:
    return str(value or "").strip().lstrip("0")


def _exec_time(row: dict[str, Any]) -> str:
    """사슬 한 행의 마지막 체결 시각. 체결이 없으면 빈 문자열(``":  :"`` 가 온다)."""
    for name in ("LastExecTime", "ExecTrxTime"):
        raw = str(row.get(name, "")).replace(":", "").strip()
        if len(raw) == 6 and raw.isdigit():
            return raw
    return ""


def _annotate(members: list[dict[str, Any]]) -> dict[str, Any]:
    """사슬 전체에서 **가장 늦은 체결 시각**. 접힌 행 하나에 하나의 시각을 붙인다.

    마지막 행의 시각을 그냥 쓰면 안 된다 — 사슬의 끝이 취소확인이면 시각이 비어 있어서
    그 앞에서 실제로 채워진 체결을 적을 수 없게 된다.
    """
    times = [t for t in (_exec_time(m) for m in members) if t]
    return {"_fill_time": max(times) if times else ""}


def index_day(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(사슬 접은 행, 원주문번호 행). 신원은 원주문 행에서, 누적 체결은 접은 행에서 읽는다.

    정정 사슬의 자식 행은 ``OrdQty`` 가 줄어 있다(정정한 수량만) — 접은 행으로 수량을 대조하면
    원주문 수량과 안 맞는다. 반대로 체결은 부모·자식에 나뉘어 쌓이므로 합쳐야 한다
    (2026-09-18 실측 A136490: 부모 80 + 정정확인 41 = 원주문 121).
    """
    folded = index_by_ordno(rows, key_pairs=((QUANTITY_KEYS, PRICE_KEYS),), annotate=_annotate)
    raw: dict[str, dict[str, Any]] = {}
    for row in rows:
        number = _ordno(row.get("OrdNo"))
        if number:
            raw[number] = row
    return folded, raw


def identify(candidate: Candidate, raw: dict[str, dict[str, Any]]) -> str:
    """이 날의 조회에서 후보의 원주문 행을 찾는다. 사유 문자열이면 신원 불일치다."""
    row = raw.get(_ordno(candidate.broker_order_no))
    if row is None:
        return "조회에 그 주문번호가 없다"
    symbol = str(row.get("IsuNo", "")).strip().removeprefix("A")
    if f"KR:{symbol}" != candidate.entity_id:
        return f"종목 불일치(조회 KR:{symbol})"
    side = {"1": "sell", "2": "buy"}.get(str(row.get("BnsTpCode", "")).strip())
    if side != str(candidate.side):
        return f"방향 불일치(조회 {side})"
    try:
        ordered = float(row.get("OrdQty", ""))
    except (TypeError, ValueError):
        return "주문수량을 읽을 수 없다"
    if ordered != candidate.quantity:
        return f"주문수량 불일치(조회 {ordered:g}, 장부 {candidate.quantity:g})"
    return ""


@dataclass(frozen=True)
class KnownOrder:
    """장부가 증권사 주문번호를 아는 우리 주문 한 건 — 조회 행을 "우리 것" 으로 설명하는 근거."""

    order_id: str
    entity_id: str
    side: str
    quantity: float
    #: 이 번호가 나간 주문일 후보. 저널이 있으면 그 하루, legacy 행은 :func:`_legacy_days`.
    days: tuple[date, ...]


def known_orders(store: Store, *, as_of: datetime, market: str = "KR") -> dict[str, list[KnownOrder]]:
    """주문번호 → 그 번호를 가진 우리 주문들. ``orders.reason`` 과 전송 저널(``submitted``) 둘 다 읽는다.

    **번호는 날짜마다 다시 센다** — 그래서 번호 하나에 여러 주문이 매달릴 수 있고, 조회 행을
    설명할 때 날짜까지 맞춰 본다(:func:`explain`).
    """
    out: dict[str, dict[str, KnownOrder]] = {}
    bindings = submission_bindings(store, as_of=as_of)
    frame = store.get("orders", as_of=as_of, market=market)
    for row in frame.itertuples(index=False) if not frame.empty else ():
        reason = str(getattr(row, "reason", "") or "")
        logical = key(str(row.session_id), str(row.entity_id), int(row.slice_seq))
        binding = bindings.get(logical)
        number = _ordno(binding["broker_order_no"]) if binding else ""
        if not number and reason.startswith(BROKER_ORDER_NO_PREFIX):
            number = _ordno(reason[len(BROKER_ORDER_NO_PREFIX):])
        if not number:
            continue
        days = (date.fromisoformat(binding["order_day"]),) if binding else _legacy_days(row)
        out.setdefault(number, {})[logical] = KnownOrder(
            order_id=logical,
            entity_id=str(row.entity_id),
            side=str(row.side),
            quantity=float(row.quantity),
            days=days,
        )
    return {number: list(group.values()) for number, group in out.items()}


def explain(row: dict[str, Any], day: date, known: dict[str, list[KnownOrder]]) -> str:
    """이 조회 행이 우리 어느 주문의 것인지 — 주문 ID, 설명 못 하면 빈 문자열.

    원주문 행은 번호·종목·방향·원주문 수량·주문일이 모두 맞아야 한다(:func:`identify` 와 같은
    네 개 + 날짜). 정정·취소 사슬의 자식 행(``OrgOrdNo`` ≠ 0)은 수량이 줄어 있으므로
    원주문번호로 찾고 수량은 보지 않는다.
    """
    parent = _ordno(row.get("OrgOrdNo"))
    number = parent or _ordno(row.get("OrdNo"))
    symbol = str(row.get("IsuNo", "")).strip().removeprefix("A")
    side = {"1": "sell", "2": "buy"}.get(str(row.get("BnsTpCode", "")).strip())
    try:
        ordered = float(row.get("OrdQty", ""))
    except (TypeError, ValueError):
        ordered = None
    for order in known.get(number, ()):
        if order.entity_id != f"KR:{symbol}" or order.side != side or day not in order.days:
            continue
        if not parent and ordered != order.quantity:
            continue
        return order.order_id
    return ""


def match_all(
    items: list[Candidate],
    days: dict[date, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]],
    known: dict[str, list[KnownOrder]] | None = None,
) -> list[Match]:
    """후보마다 증거를 찾는다. **맞는 날이 정확히 하나일 때만** 받는다.

    ``known`` (:func:`known_orders`) 을 주면 번호 없는 건의 후보에서 이미 우리 다른 주문으로
    설명되는 행을 뺀다 — 안 빼면 같은 세션 조각 0 의 체결이 조각 1 의 후보로 보인다(2026-09-27 오해).
    """
    out: list[Match] = []
    for candidate in items:
        if not candidate.broker_order_no:
            out.append(
                Match(candidate, reason="주문번호 없음 — 전송 응답 미수신, 조회로 찾을 번호가 없다",
                      orphans=_orphans(candidate, days, known or {}))
            )
            continue
        found: list[tuple[date, dict[str, Any], dict[str, Any]]] = []
        reasons: list[str] = []
        for day in candidate.days:
            indexed = days.get(day)
            if indexed is None:
                reasons.append(f"{day} 조회 없음")
                continue
            folded, raw = indexed
            problem = identify(candidate, raw)
            if problem:
                reasons.append(f"{day} {problem}")
                continue
            found.append((day, folded[_ordno(candidate.broker_order_no)], raw))
        if len(found) > 1:
            out.append(Match(candidate, reason="후보 날짜 둘 이상이 맞는다 — 사람 확인"))
            continue
        if not found:
            out.append(Match(candidate, reason="; ".join(reasons) or "증거 없음"))
            continue
        day, folded_row, raw = found[0]
        cumulative = float(folded_row.get(QUANTITY_KEYS[0]) or 0.0)
        cancel_row = next(
            (
                r
                for r in raw.values()
                if str(r.get("MrcTpNm", "")).strip() == "취소확인"
                and _ordno(r.get("OrgOrdNo")) == _ordno(candidate.broker_order_no)
            ),
            None,
        )
        out.append(
            Match(
                candidate,
                day=day,
                row=folded_row,
                cumulative=cumulative,
                cancelled=float(cancel_row.get("OrdQty") or 0.0) if cancel_row else 0.0,
            )
        )
    return out


def _orphans(
    candidate: Candidate,
    days: dict[date, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]],
    known: dict[str, list[KnownOrder]],
) -> tuple[dict[str, Any], ...]:
    """번호 없는 건의 사람 확인용 — 같은 날·종목·방향·수량이고 **우리 다른 주문으로 설명되지
    않는** 행. **적재하지 않는다.**"""
    out: list[dict[str, Any]] = []
    for day in candidate.days:
        indexed = days.get(day)
        if indexed is None:
            continue
        for row in indexed[1].values():
            if explain(row, day, known):
                continue
            symbol = str(row.get("IsuNo", "")).strip().removeprefix("A")
            side = {"1": "sell", "2": "buy"}.get(str(row.get("BnsTpCode", "")).strip())
            try:
                ordered = float(row.get("OrdQty", ""))
            except (TypeError, ValueError):
                continue
            if (
                f"KR:{symbol}" == candidate.entity_id
                and side == str(candidate.side)
                and ordered == candidate.quantity
            ):
                out.append({**row, "_day": day.isoformat()})
    return tuple(out)


#: 미도착 확인으로 닫을 수 있는 상태 — 전송 응답을 못 받은 것들뿐이다.
NOT_RECEIVED_STATUSES = frozenset({"submitting", "sent"})


@dataclass(frozen=True)
class NotReceived:
    """번호 없는 조각의 미도착 판정 (``docs/design/execution-safety.md`` 2026-10-04 절)."""

    candidate: Candidate
    #: 비어 있으면 미도착이 확인됐다. 아니면 닫으면 안 되는 이유다.
    refusal: str
    #: 조회한 날 → 그날 행 수.
    queried: dict[date, int]
    #: 같은 종목·방향 행과 그것을 설명하는 우리 주문 ID(설명 못 하면 빈 문자열).
    same_symbol: tuple[tuple[date, str, str], ...]

    @property
    def confirmed(self) -> bool:
        return not self.refusal

    def reason(self) -> str:
        """장부 ``reason`` 에 남길 근거 — 한 달 뒤에도 왜 닫았는지 말할 수 있게."""
        days = ", ".join(f"{day} {n}행" for day, n in sorted(self.queried.items()))
        ours = ", ".join(f"{day} {no}={owner}" for day, no, owner in self.same_symbol) or "없음"
        return f"미도착 확인 — {INQUIRY_TR} {days}에 이 조각 없음 · 같은 종목·방향 행: {ours}"


def check_not_received(
    candidate: Candidate,
    days: dict[date, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]],
    known: dict[str, list[KnownOrder]],
) -> NotReceived:
    """번호 없는 조각이 증권사에 **안 들어갔다**고 말할 수 있는가.

    전부 맞아야 한다: 번호 없음 · 미확정 상태 · 장부 체결·취소 0 · 주문일 후보 **전부** 조회 성공 ·
    그 날들의 같은 종목·방향 행이 **전부** 우리 다른 주문 번호로 설명됨. 설명 안 되는 행이 하나라도
    있으면 그게 우리 조각일 수 있다(응답만 못 받은 경우) — 거부하고 사람이 본다.
    """
    queried = {day: len(days[day][1]) for day in candidate.days if day in days}
    same: list[tuple[date, str, str]] = []

    def refuse(text: str) -> NotReceived:
        return NotReceived(candidate, text, queried, tuple(same))

    if candidate.broker_order_no:
        return refuse(f"주문번호 {candidate.broker_order_no} 가 있다 — 미도착이 아니라 주문일 대사 대상")
    if candidate.status not in NOT_RECEIVED_STATUSES:
        return refuse(f"상태 {candidate.status} — 전송 응답 미수신 건이 아니다")
    if candidate.accounted:
        return refuse(f"장부에 체결·취소 {candidate.accounted:g}주가 있다 — 도착했다는 뜻")
    if not candidate.days:
        return refuse("주문일 후보가 없다")
    missing = [day for day in candidate.days if day not in days]
    if missing:
        return refuse(f"조회 못 한 주문일 {', '.join(str(d) for d in missing)} — 다 못 봤다")
    for day in candidate.days:
        for row in days[day][1].values():
            symbol = str(row.get("IsuNo", "")).strip().removeprefix("A")
            side = {"1": "sell", "2": "buy"}.get(str(row.get("BnsTpCode", "")).strip())
            if f"KR:{symbol}" != candidate.entity_id or side != str(candidate.side):
                continue
            same.append((day, _ordno(row.get("OrdNo")), explain(row, day, known)))
    strangers = [(day, no) for day, no, owner in same if not owner]
    if strangers:
        listed = ", ".join(f"{day} {no}" for day, no in strangers)
        return refuse(f"우리 번호로 설명 안 되는 같은 종목 행 {listed} — 이 조각일 수 있다, LS 화면 확인")
    return NotReceived(candidate, "", queried, tuple(same))


#: 미도착 확인 정정 행의 출처 — 사람이 명령을 쳐서 닫았다는 표시.
NOT_RECEIVED_SOURCE = "not-received-confirmed"
NOT_RECEIVED_STATUS = "rejected"


def record_not_received(store: Store, clock: Clock, result: NotReceived) -> int:
    """확인된 미도착을 ``orders`` 의 새 revision 으로 적는다(append-only). 적은 행 수.

    잠금 안에서 그 조각을 다시 읽어, 판정 뒤에 상태나 번호가 바뀌었으면 적지 않는다.
    ``trades``·``execution_events`` 는 건드리지 않는다 — 체결이 없었다는 사실만 상태로 남긴다.
    """
    if not result.confirmed:
        raise ValueError(f"미도착이 확인되지 않았다: {result.refusal}")
    item = result.candidate
    session, entity, seq = item.order_id.rsplit("|", 2)
    with account_lock(store.root):
        now = clock.now()
        frame = store.get("orders", as_of=now, market="KR")
        rows = frame[
            (frame["session_id"] == session)
            & (frame["entity_id"] == entity)
            & (frame["slice_seq"] == int(seq))
        ] if not frame.empty else frame
        if len(rows) != 1:
            raise ValueError(f"{item.order_id}: 주문 행이 {len(rows)}개다")
        current = rows.iloc[0].to_dict()
        if str(current["status"]) != item.status or str(current.get("reason") or "").startswith(
            BROKER_ORDER_NO_PREFIX
        ):
            raise ValueError(f"{item.order_id}: 판정 뒤 상태가 바뀌었다({current['status']}) — 다시 판정한다")
        revision = int(current["revision"]) + 1
        row = {
            **current,
            "status": NOT_RECEIVED_STATUS,
            "reason": result.reason()[:300],
            "revision": revision,
            "observed_at": now,
            "source": NOT_RECEIVED_SOURCE,
        }
        identity = payload_hash([item.order_id, revision, NOT_RECEIVED_STATUS])
        return int(
            store.append("orders", [row], ingest_run_id=f"not-received-{identity}", source=NOT_RECEIVED_SOURCE)
        )


def dated_query(day: date, folded: dict[str, dict[str, Any]]) -> FillQuery:
    """이미 받아 둔 그날의 행으로 :func:`sync_fills` 를 돌리는 조회 계획.

    행을 미리 받는 이유는 신원 대조(:func:`identify`)를 적재 **전에** 해야 하고, 같은 날을
    두 번 조회하지 않기 위해서다. ``fetch`` 는 그래서 브로커를 부르지 않는다.
    """
    at: dict[str, datetime] = {}

    def filled_at(row: dict[str, Any]) -> datetime | None:
        raw = str(row.get("_fill_time", ""))
        if len(raw) != 6 or not raw.isdigit():
            return None
        return at.setdefault(
            raw,
            datetime.combine(
                day, datetime.strptime(raw, "%H%M%S").time(), tzinfo=SEOUL
            ).astimezone(ZoneInfo("UTC")),
        )

    return FillQuery(
        fetch=lambda _client: folded,
        quantity_keys=QUANTITY_KEYS,
        price_keys=PRICE_KEYS,
        order_day=day,
        filled_at=filled_at,
    )


def pending_for(match: Match) -> PendingFill:
    """확정된 증거 한 건 → ``sync_fills`` 가 받는 :class:`PendingFill`."""
    if match.day is None:
        raise ValueError("no dated evidence for this order")
    return PendingFill(
        order_id=match.candidate.order_id,
        entity_id=match.candidate.entity_id,
        side=match.candidate.side,
        market="KR",
        broker_order_no=match.candidate.broker_order_no,
        requested_quantity=match.candidate.quantity,
        observed_day=match.day,
    )
