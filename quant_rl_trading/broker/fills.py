"""주문을 낸 뒤 **체결을 확인해서 창고에 적는다**.

`broker/__init__.py` 는 주문을 내보내는 계약(``Broker.submit``)만 정의한다.
LS 는 그 결과를 즉시 알려주지 않는다 — 신규 주문(CSPAT00601)은 접수 여부만
돌려주고, 실제로 얼마나 채워졌는지는 별도 TR(t0425, 미체결/체결 조회)로
따로 물어봐야 한다. 이 모듈이 그 질문을 던지고 답을 ``trades`` 에 적는다.

## 지키는 것 셋

1. **부분체결이 정상 경로다.** t0425 는 주문 하나당 "지금까지 누적 몇 주
   채워졌는지" 를 돌려준다. 매 폴링마다 그 누적치와 창고에 이미 적힌
   누적치의 **차이만** 새 체결로 적는다 — 그래야 두 번째 폴링이 첫 폴링
   분량을 또 세지 않는다.
2. **체결을 지어내지 않는다.** 조회 자체가 실패했거나, 브로커 응답에 그
   주문이 없거나, 필드를 못 읽으면 체결 0 이 아니라 :class:`FillState.UNKNOWN`
   이다. 회계는 "모른다" 와 "0 이다" 를 다르게 다뤄야 한다 — 0 으로 적으면
   장부가 "안 샀다" 로 확정해 버린다.
3. **멱등성은 자연키로 지킨다.** 창고는 ``(entity_id, valid_from, order_id)``
   가 같으면 최신 revision 하나만 남긴다 (``store/schema.py``). 그래서 이
   모듈이 쓰는 ``order_id`` 컬럼값은 ``f"{client_order_id}#{그때까지_누적수량}"``
   이다 — 같은 누적 상태를 두 번 관측해서 두 번 적어도(재시작·재시도) 같은
   키가 나와 창고가 알아서 하나만 남긴다. ``ingest_run_id`` 도 이번 배치의
   내용으로 만든 지문이라 똑같은 재시도는 :class:`DuplicateIngestRun` 으로
   한 번 더 막힌다.

## 이 모듈이 모르는 것

``orders`` 테이블에는 LS 가 돌려준 주문번호(``broker_order_no``)가 없다
(``store/tables.py`` 참고 — 스키마 변경은 이 작업 범위 밖이라 고치지 않았다).
그래서 이 모듈은 "무엇을 조회할지" 를 스스로 찾지 않는다 — 호출자가
``Broker.submit`` 이 돌려준 :class:`~quant_rl_trading.broker.Ack` 에서
``broker_order_no`` 를 들고 :class:`PendingFill` 로 넘겨야 한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from quant_rl_trading.accounting.book import Side as BookSide
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.backtest.execution import currency_of
from quant_rl_trading.broker import Fill
from quant_rl_trading.broker.ls_order_us import PATH_ACCNO_US
from quant_rl_trading.collectors.errors import LSAPIError, MissingCredentials
from quant_rl_trading.collectors.ls_client import PATH_ACCNO
from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.collectors.ls_client import LSClient
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

__all__ = [
    "TR_FILLS",
    "FillOutcome",
    "FillState",
    "PendingFill",
    "SyncResult",
    "sync_fills",
]

TRADES = "trades"
SOURCE = "broker"
#: 국장 체결조회.
TR_FILLS = "t0425"
#: 미장 체결조회. 국장과 **블록 이름·필드명·호출 횟수가 전부 다르다**
#: (``docs/design/ls-api.md`` §0-6). 조회 결과를 ``trades`` 에 적는 규약
#: (부분체결 차분·자연키·비용 계산)은 시장과 무관해서 그대로 공유한다 —
#: 갈리는 것은 "행을 어떻게 얻어오는가" 하나뿐이라 :class:`FillQuery` 로 뺐다.
TR_FILLS_US = "COSAQ00102"

#: 미장 주문일 기준 시간대. LS 는 **거래소 날짜**로 주문을 가른다.
NEW_YORK = ZoneInfo("America/New_York")

#: 미장 주문시장코드. **한 번에 전종목을 못 받는다** — ``OrdMktCode`` 가 필수라
#: 뉴욕·나스닥을 각각 부르고 합쳐야 한다. 초당 1건 한도라 한 사이클에 2초 든다.
US_MARKET_CODES = ("82", "81")

#: 계좌 조회 경로. 국장과 미장이 다르다.
PATH_ACCNO_BY_MARKET: dict[str, str] = {"KR": PATH_ACCNO, "US": PATH_ACCNO_US}

#: "조회내역이 없습니다". **오류가 아니라 빈 결과다.** ``OK_CODES`` 에 없어서
#: ``LSClient`` 가 예외로 올리지만, 보유·주문이 0 건인 계좌의 정상 응답이다
#: (2026-08-15 실측 — 미장 빈 계좌에서 잔고·주문내역 모두 이 코드가 왔다).
#: 이걸 실패로 다루면 "조회 못 했다" 와 "아직 안 잡혔다" 가 구분되지 않는다.
NO_ROWS = "02679"

#: t0425 조회 창. 주문은 세션을 이월하지 않는다(executor/lifecycle.py
#: close_session) — 오늘 세션 주문만 대상이면 되지만, 자정 근처 지연 접수
#: 여지를 두고 며칠 여유를 둔다. backtest/execution.py 의 pending() 이 쓰는
#: lookback=7 과 같은 자리의 값이다(질의 창이지 임계치가 아니라 store.config
#: 대상이 아니다).
ORDER_LOOKBACK_DAYS = 7


class FillState(Enum):
    """이 조회 한 건이 어떻게 끝났는지."""

    #: 새 체결 수량을 창고에 적었다.
    RECORDED = "recorded"
    #: 조회는 됐지만 지난번 이후로 새로 채워진 게 없다.
    UNCHANGED = "unchanged"
    #: **모른다.** 조회 실패 / 응답에 없음 / 필드를 못 읽음 — 0 이 아니다.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PendingFill:
    """체결을 확인해야 할 주문 한 건.

    호출자(브로커 어댑터)가 ``Broker.submit`` 의 ``Ack.broker_order_no`` 를
    들고 있다가 만든다. 이 모듈은 주문을 내지도, ``orders`` 테이블을 읽지도
    않는다 — broker_order_no 를 찾는 것은 호출자 책임이다.
    """

    order_id: str
    entity_id: str
    side: Side
    market: str
    broker_order_no: str
    requested_quantity: float


@dataclass(frozen=True)
class FillOutcome:
    """주문 한 건에 대한 조회 결과."""

    order_id: str
    state: FillState
    #: RECORDED 일 때만 채워진다. broker.Fill — 팀 계약 그대로.
    fill: Fill | None = None
    #: 그때까지 브로커가 보고한 누적 체결 수량. 모르면 None.
    cumulative_quantity: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class SyncResult:
    outcomes: tuple[FillOutcome, ...]
    rows_written: int

    @property
    def recorded(self) -> list[FillOutcome]:
        return [item for item in self.outcomes if item.state is FillState.RECORDED]

    @property
    def unknown(self) -> list[FillOutcome]:
        """**"모른다" 는 호출자가 여기서 받는다.** 0 체결로 조용히 넘어가지 않는다."""
        return [item for item in self.outcomes if item.state is FillState.UNKNOWN]


def sync_fills(
    store: Store,
    client: LSClient,
    clock: Clock,
    *,
    as_of: datetime,
    pending: list[PendingFill],
) -> SyncResult:
    """대기 중인 주문들의 체결을 확인해 ``trades`` 에 적는다.

    조회 TR 은 ``PendingFill.market`` 이 정한다 — 국장 ``t0425``, 미장
    ``COSAQ00102``. **시장별로 따로 조회하고, 한쪽 실패가 다른 쪽을 오염시키지
    않는다.** 한 번에 다 실패로 접으면 국장 주문이 미장 장애 때문에 "모른다"
    가 된다.
    """
    if not pending:
        return SyncResult((), 0)

    # 시장별 조회. 결과는 (주문번호 → 행) 또는 실패 사유 문자열.
    fetched: dict[str, dict[str, dict[str, Any]] | str] = {}
    for market in sorted({item.market for item in pending}):
        fetched[market] = _fetch_fill_rows(client, market, as_of=as_of)

    recorded_so_far = _recorded_quantities(store, as_of=as_of, pending=pending)

    observed_at = clock.now()
    rates = Rates.from_store(store, as_of=as_of)

    outcomes: list[FillOutcome] = []
    rows: list[dict[str, object]] = []

    for item in pending:
        found = fetched.get(item.market)
        if isinstance(found, str):
            # 조회 자체가 실패했다 — 모른다. 0건으로 적으면 "안 샀다" 로
            # 확정돼 버린다.
            outcomes.append(FillOutcome(item.order_id, FillState.UNKNOWN, detail=found))
            continue
        by_ordno = found or {}
        row = by_ordno.get(_normalize_ordno(item.broker_order_no))
        if row is None:
            outcomes.append(
                FillOutcome(item.order_id, FillState.UNKNOWN, detail="브로커 응답에 주문 없음")
            )
            continue

        cumulative = _first_numeric(row, _QUANTITY_KEYS.get(item.market, _QUANTITY_KEYS["KR"]))
        price = _first_numeric(row, _PRICE_KEYS.get(item.market, _PRICE_KEYS["KR"]))
        if cumulative is None or price is None:
            outcomes.append(
                FillOutcome(item.order_id, FillState.UNKNOWN, detail=f"체결 필드 파싱 실패: {row!r}")
            )
            continue

        already = recorded_so_far.get(item.order_id, 0.0)
        delta = cumulative - already
        if delta <= 0:
            outcomes.append(
                FillOutcome(
                    item.order_id, FillState.UNCHANGED, cumulative_quantity=cumulative
                )
            )
            continue
        if price <= 0:
            # 뭔가는 채워졌는데 체결가를 못 읽었다 — 지어낼 수 없으니 모른다.
            outcomes.append(
                FillOutcome(
                    item.order_id,
                    FillState.UNKNOWN,
                    cumulative_quantity=cumulative,
                    detail=f"체결가 0/누락 — 신규체결 {delta} 를 적을 수 없다",
                )
            )
            continue

        currency = currency_of(item.market)
        gross = delta * price
        fee, tax = rates.costs(side=BookSide(str(item.side)), gross=gross, currency=currency)

        fill = Fill(
            order_id=item.order_id,
            entity_id=item.entity_id,
            side=str(item.side),
            quantity=delta,
            price=price,
            filled_at=observed_at,
            fee=fee,
            tax=tax,
            broker_order_no=item.broker_order_no,
        )
        rows.append(
            {
                "entity_id": item.entity_id,
                "valid_from": as_of,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": item.market,
                "side": str(item.side),
                "quantity": float(delta),
                "price": float(price),
                "currency": currency,
                "fee": fee,
                "tax": tax,
                # 자연키의 핵심 — 같은 누적 상태를 두 번 관측해도 같은 문자열이
                # 나온다. 그래야 재시도가 창고에서 자동으로 한 번만 남는다.
                "order_id": _trade_order_id(item.order_id, cumulative),
            }
        )
        outcomes.append(
            FillOutcome(
                item.order_id,
                FillState.RECORDED,
                fill=fill,
                cumulative_quantity=cumulative,
            )
        )

    written = 0
    if rows:
        run_id = _run_id(rows)
        if not store.ingest_run_recorded(TRADES, run_id):
            written = int(store.append(TRADES, rows, ingest_run_id=run_id, source=SOURCE))

    return SyncResult(tuple(outcomes), written)


# -- 시장별 조회 ----------------------------------------------------------------
#
# 갈리는 것은 이 아래뿐이다. 위쪽(부분체결 차분·자연키·비용)은 시장과 무관하다.

#: 누적 체결수량 후보 필드. 순서대로 본다.
#: 미장 ``AllExecQty`` 가 "전체 체결수량", ``ExecQty`` 가 "이번 체결수량" 으로
#: 알려져 있어 **누적 쪽을 먼저** 본다 — 이번치를 누적으로 읽으면 차분이
#: 매번 새 체결로 잡혀 같은 체결을 여러 번 적는다.
_QUANTITY_KEYS: dict[str, tuple[str, ...]] = {
    "KR": ("cheqty", "chevol", "execqty", "ordcheqty"),
    "US": ("AllExecQty", "ExecQty"),
}

#: 체결가 후보 필드. 미장은 **달러 소수**다 — 어디에도 정수 캐스팅을 넣지 마라.
_PRICE_KEYS: dict[str, tuple[str, ...]] = {
    "KR": ("cheprice", "cheprc", "chegprc", "execprc"),
    "US": ("OvrsExecPrc", "OvrsOrdPrc"),
}


def _fetch_fill_rows(
    client: LSClient, market: str, *, as_of: datetime
) -> dict[str, dict[str, Any]] | str:
    """그 시장의 체결 조회. 성공하면 주문번호→행, 실패하면 사유 문자열.

    반환형이 둘인 이유는 **"조회 못 했다"와 "조회했는데 그 주문이 없다"를
    호출부가 구분해야** 하기 때문이다. 빈 dict 로 뭉개면 조회 실패가
    "아직 안 잡혔다" 로 둔갑한다.
    """
    calls: list[tuple[str, dict[str, Any], str]]
    if market == "US":
        # 뉴욕·나스닥을 각각 부른다 — ``OrdMktCode`` 가 필수라 한 번에 못 받는다.
        calls = [
            (
                TR_FILLS_US,
                {
                    f"{TR_FILLS_US}InBlock1": {
                        "RecCnt": 1,
                        "QryTpCode": "1",  # 계좌별
                        "BkseqTpCode": "2",  # 정순
                        "OrdMktCode": code,
                        "BnsTpCode": "0",  # 전체
                        "IsuNo": "",
                        "SrtOrdNo": 0,  # 정순이면 0 부터
                        # **주문일을 반드시 준다.** 비워 두면 LS 가 오늘로
                        # 해석하고, 미장 주문은 한국 날짜가 넘어간 뒤에 조회되는
                        # 일이 흔해서 그대로 "조회내역이 없습니다"(02679)가 온다.
                        # 그 코드는 **"없다" 와 "잘못 물었다" 를 구분해 주지
                        # 않는다** — 2026-08-17 SNAP 주문이 0.4초 만에 체결됐는데
                        # 5회 조회가 전부 "브로커 응답에 주문 없음" 으로 돌아왔다.
                        # 미장 주문일은 **뉴욕 날짜**다. 한국시간 22:40 은 뉴욕
                        # 09:40 이라 같은 날이지만, 새벽 03:00(뉴욕 13:00)에 낸
                        # 주문은 한국 날짜가 하루 앞선다.
                        "OrdDt": as_of.astimezone(NEW_YORK).strftime("%Y%m%d"),
                        "ExecYn": "0",  # 0:전체 — 미체결 잔량과 체결을 한 번에
                        "CrcyCode": "USD",
                        "ThdayBnsAppYn": "0",
                        "LoanBalHldYn": "0",
                    }
                },
                f"{TR_FILLS_US}OutBlock3",
            )
            for code in US_MARKET_CODES
        ]
    else:
        calls = [
            (
                TR_FILLS,
                {
                    "t0425InBlock": {
                        "expcode": "",
                        "chegb": "0",  # 0:전체 — 미체결 잔량과 누적 체결을 한 번에.
                        "medosu": "0",
                        "sortgb": "1",
                        "cts_ordno": "",
                    }
                },
                "t0425OutBlock1",
            )
        ]

    rows: list[dict[str, Any]] = []
    for tr, body, out_block in calls:
        try:
            data = client.request_tr(PATH_ACCNO_BY_MARKET.get(market, PATH_ACCNO), tr, body)
        except LSAPIError as error:
            if error.rsp_cd == NO_ROWS:
                # 조회는 됐고 내역이 0 건이다 — 실패가 아니다.
                continue
            return f"조회 실패: {error}"
        except MissingCredentials as error:
            return f"조회 실패: {error}"

        if data.get("paper"):
            # live_trading 이 꺼져 있으면 체결조회 TR 도 paper 응답으로 막힌다
            # (계좌/주문 TR 은 PAPER_ALLOWED_TR 밖이다). 이 모드에서는 애초에
            # 실주문이 나가지 않았으므로 확인할 체결도 없다 — "0 건" 이 아니라
            # "조회하지 않았다" 로 남긴다.
            return "paper 모드 — 조회하지 않았다"

        rows.extend(data.get(out_block) or [])

    return _index_by_ordno(rows)


# -- 내부 -----------------------------------------------------------------------


def _trade_order_id(order_id: str, cumulative_quantity: float) -> str:
    return f"{order_id}#{int(round(cumulative_quantity))}"


def _run_id(rows: list[dict[str, object]]) -> str:
    """이번 배치 내용의 지문. 같은 배치를 다시 시도해도 같은 값이 나온다."""
    fingerprint = "|".join(sorted(str(row["order_id"]) for row in rows))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"fills-{digest}"


def _normalize_ordno(value: str | None) -> str:
    """LS 주문번호는 앞자리 0 유무가 응답마다 다르다. 비교 전에 지운다."""
    return (value or "").strip().lstrip("0")


def _index_by_ordno(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ordno = _normalize_ordno(
            str(row.get("ordno") or row.get("OrdNo") or row.get("orgordno") or "")
        )
        if ordno:
            out[ordno] = row
    return out


def _first_numeric(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """후보 필드 이름을 순서대로 본다.

    참고 구현(ls_kr_rl_trader/broker/order.py ``_first``)과 다르게 **"0" 을
    결측으로 보지 않는다.** 0주 체결은 유효한 사실이다(아직 안 채워짐) —
    0 을 결측으로 보고 다음 후보로 넘어가면, 다른 이름의 필드에 우연히 값이
    있을 때 엉뚱한 숫자를 체결가로 읽는다.
    """
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _recorded_quantities(
    store: Store, *, as_of: datetime, pending: list[PendingFill]
) -> dict[str, float]:
    """이 주문들에 대해 창고에 이미 적힌 체결 수량 합계.

    ``trades.order_id`` 는 ``f"{client_order_id}#{누적수량}"`` 꼴로 적힌다
    (``_trade_order_id``). ``#`` 앞부분으로 원래 주문을 되찾아 이미 적힌
    조각들을 더한다 — 장부와 같은 원칙이다: 캐시를 믿지 않고 기록에서
    다시 접는다(``accounting/ledger.py``).
    """
    entities = sorted({item.entity_id for item in pending})
    if not entities:
        return {}
    frame = store.get(TRADES, as_of=as_of, entity=entities, lookback=ORDER_LOOKBACK_DAYS)
    if frame.empty:
        return {}
    wanted = {item.order_id for item in pending}
    totals: dict[str, float] = {}
    for raw_order_id, quantity in zip(frame["order_id"], frame["quantity"], strict=True):
        base = str(raw_order_id).split("#", 1)[0]
        if base in wanted:
            totals[base] = totals.get(base, 0.0) + float(quantity)
    return totals
