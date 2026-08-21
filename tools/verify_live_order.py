"""LS 증권 실계좌 배선 검증 — **최소 수량 1주를 사고 즉시 판다.**

    uv run python tools/verify_live_order.py --symbol 005930                  # 국장 드라이런(기본)
    uv run python tools/verify_live_order.py --symbol 005930 --live           # 국장 실전
    uv run python tools/verify_live_order.py --market US --symbol WEN         # 미장 드라이런
    uv run python tools/verify_live_order.py --market US --symbol WEN --live  # 미장 실전

## 두 시장

``--market`` 이 갈리는 지점을 ``MarketProfile`` 한 곳에 모았다. **국장 경로는
미장이 붙기 전과 같은 함수·같은 값을 쓴다** — 미장 쪽을 고치다 국장이 흔들리면
검증 도구가 검증 대상이 되어 버린다.

| | KR | US |
|---|---|---|
| 자격증명 | ``LS_*`` | ``LS_US_*`` (별도 appkey — 토큰 충돌 없음) |
| 잔고 | ``t0424`` ``sunamt`` (KRW) | ``COSOQ02701`` ``FcurrOrdAbleAmt`` (USD) |
| 시세 | ``t1102`` | ``g3104`` |
| 주문 | ``CSPAT00601`` | ``COSAT00301`` |
| 체결조회 | ``t0425`` | ``COSAQ00102`` — **아직 안 붙었다**(§미장 한계) |
| 호가단위 | ``executor/ticks.py`` 표 | **LS 가 준다** (``g3104.untprc``) |
| 수량 | 정수 | 정수 — 소수점 주문 불가, 깎지 않고 거부 |
| 지문 고정 | ``execution.live_account_fingerprint`` | ``…_us`` — **미선언도 거부** |

## 미장에서 다른 것 셋 — 겉보기로는 안 드러난다

1. **미장 시세 TR 은 호가를 주지 않는다.** ``g3104`` 의 ``bidlotsize``/
   ``asklotsize`` 는 호가 **수량 단위**이지 호가 가격이 아니다(전부 ``1``).
   ``g3101`` 에도 없다 — 2026-08-15 실측. 그래서 미장 기준가는 항상 현재가로
   물러선다(``reference_price`` 가 ``bid``/``ask`` 가 0 이면 그렇게 한다).
   **매수 즉시체결을 기대하지 마라** — 최우선 매도호가를 모르니 지정가가
   현재가에 걸리고, 체결까지 시간이 걸릴 수 있다. 호가는
   ``/websocket/overseas-stock`` 의 ``GSH`` 에나 있고 이 도구 범위 밖이다.
2. **호가단위를 짐작하지 않는다.** ``g3104`` 가 종목별 ``untprc`` 를 준다
   (실측: WEN·AAPL·SNAP·BRK.A = ``0.01``, LTRYW($0.007) = ``0.0001``).
   ``executor/ticks.py`` 는 원화 KRX 표 전용이고 ``int`` 를 돌려주므로 미장에
   쓰면 안 된다 — 그 표에 $305.77 을 먹이면 500원 단위로 반올림된다.
3. **주문시장코드(81 뉴욕 / 82 나스닥)를 확인해서 넣는다.** 틀리면 g3104 가
   "해당종목이 없습니다" 로 답한다(실측). 그래서 시세 조회를 82→81 순으로
   시도해 **성공한 쪽을 그대로 주문에 쓴다** — 짐작하지 않는다.

## 미장 한계 — 이 도구가 아직 못 하는 것

``broker/fills.py`` 는 국장 ``t0425`` 전용이다(블록 이름·필드명·시장별 분할
호출이 전부 다르다 — ``docs/design/ls-api.md`` §0-6). 그래서 **미장은
체결조회(5단계) 이후를 자동으로 잇지 못한다.** 매수 주문 전송 결과까지
확인하고 멈추며, 체결·매도 정리는 사람이 LS 화면에서 해야 한다.
그 상태를 화면에 크게 찍는다 — 조용히 0건으로 끝나면 "안 샀다" 로 오해한다.

## 왜 이 도구가 필요한가

모의투자 appkey 로는 주문 TR(CSPAT*)·잔고(t0424) 자체가 막힌다
(``collectors/ls_client.py`` ``PAPER_ALLOWED_TR``). ``broker/ls_order.py``·
``broker/fills.py``·``executor/pipeline.py`` 의 배선은 전부 단위 테스트로
검증됐지만, **LS 가 실제로 그 TR 을 어떻게 받아주는지**는 실계좌 없이는
알 수 없다. 이 도구는 그 마지막 구간 하나를 사람이 지켜보는 자리에서
확인하기 위한 것이다.

## 안전장치

1. **매 단계마다 사람 확인.** ``input()`` 으로 y 를 눌러야 다음 단계로
   간다. 자동으로 쭉 진행되지 않는다.
2. **두 게이트를 이 도구도 그대로 지킨다.** ``--live`` 없이 실행하면
   ``LSClient.live_trading=False`` 로 만들어서, 브로커 계층이 이미
   막아준다(``broker/ls_order.py`` 모듈 docstring). ``--live`` 를 줘도
   ``execution.live_trading`` store 설정이 꺼져 있으면 여기서 멈추고
   무엇을 켜야 하는지 알려준다.
3. **드라이런은 별도 스위치다.** ``--dry-run`` 을 주면(``--live`` 여부와
   무관하게) 주문·정정·취소 TR 은 **전송하지 않고 본문만 출력**한다.
   ``--live`` 없이 돌리면 자동으로 드라이런이 켜진다 — 실수로 뭔가
   나가는 경로가 없다.
4. **주문 금액 상한.** ``--max-order-value`` (기본 10만원)를 넘으면
   주문 직전에 거부한다.
5. **호가단위.** ``executor/ticks.py`` 의 ``round_to_tick`` 을 그대로
   쓴다 — 안 쓰면 거래소가 거부한다.

## 이 도구가 확인하려는 것 둘 (docs/live-order-checklist.md 참고)

- CSPAT00801(취소)의 ``OrdQty`` 가 "이번에 취소할 수량"인지 "잔량 전체"
  인지 — 5단계에서 부분체결 상태를 만들고, 취소 수량을 사람이 직접
  입력하게 해서 관찰한다.
- 재호가(``lifecycle.decide`` 의 ``market_price``)에 어떤 가격을 먹여야
  하는지 — 6단계에서 미체결 상태의 새 호가를 조회해 정정을 내고 결과를
  본다.

## .env

키를 읽어 쓰지만(``LSCredentials.from_env``) **출력하지 않는다.** 존재
여부만 화면에 남긴다.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker import BrokerError, RejectedOrder  # noqa: E402
from quant_rl_trading.broker import factory as broker_factory  # noqa: E402
from quant_rl_trading.broker.fills import NO_ROWS, PendingFill, sync_fills  # noqa: E402
from quant_rl_trading.broker.ls_order import (  # noqa: E402
    ORD_CNDI_NONE,
    ORD_PTN_LIMIT,
    LSBroker,
    _order_body,
)
from quant_rl_trading.broker.ls_order_us import (  # noqa: E402
    MARKET_CODES,
    PATH_ACCNO_US,
    LSUSBroker,
    us_cancel_body,
    us_modify_body,
    us_order_body,
    us_symbol,
)
from quant_rl_trading.collectors.errors import LSAPIError, MissingCredentials  # noqa: E402
from quant_rl_trading.collectors.ls_client import (  # noqa: E402
    MIN_INTERVAL_SEC_KR,
    MIN_INTERVAL_SEC_US,
    PATH_ACCNO,
    PATH_MARKET,
    LSClient,
    LSCredentials,
    isu_code,
)
from quant_rl_trading.collectors.market_hours import Market, is_regular_session  # noqa: E402
from quant_rl_trading.executor import guards  # noqa: E402
from quant_rl_trading.executor.orders import PlannedOrder, client_order_id  # noqa: E402
from quant_rl_trading.executor.ticks import round_to_tick  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Order, Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TR_BALANCE = "t0424"
TR_QUOTE = "t1102"
#: 미장 예수금·현재가. 잔고 평가(``COSOQ00201``)가 아니라 **예수금**을 본다 —
#: 미수를 막으려면 "지금 쓸 수 있는 현금" 이 필요하고 그게 ``FcurrOrdAbleAmt`` 다.
TR_BALANCE_US = "COSOQ02701"
#: 미장 **잔고평가**. 예수금 TR(``COSOQ02701``)은 보유 종목을 아예 안 준다 —
#: 그래서 "보유 0종목" 이 "안 물어봤다" 의 다른 말이었다 (2026-08-17 실주문에서
#: SNAP 1주가 체결됐는데 화면은 0종목이라고 했다).
TR_HOLDINGS_US = "COSOQ00201"

#: 이 본문이어야 응답이 온다. ``{"QryTp","BalCreTp"}`` 로 부르면
#: ``02679 조회내역이 없습니다`` 가 돌아온다 — **인자가 틀리면 "없다" 고
#: 답한다.** 그 응답을 그대로 믿으면 보유가 없는 것으로 읽힌다 (2026-08-18 실측).
HOLDINGS_BODY_US = {"RecCnt": 1, "BaseDt": "", "CrcyCode": "USD", "AstkBalTpCode": "00"}
TR_QUOTE_US = "g3104"

#: 미장 시세 경로. 국장(``/stock/market-data``)과 다르다.
PATH_MARKET_US = "/overseas-stock/market-data"

#: 기본 주문 금액 상한. **아주 작게** — 실수로 큰 금액이 나가는 문을 좁힌다.
DEFAULT_MAX_ORDER_VALUE = 100_000.0
#: 미장 기본 상한. 계좌에 USD 9.49 뿐이라(2026-08-15 실측) 그보다 조금 위에
#: 둔다 — 원화 기본값 100,000 을 달러로 읽으면 상한이 사실상 없는 것과 같다.
DEFAULT_MAX_ORDER_VALUE_US = 20.0


# -----------------------------------------------------------------------------
# 사람 확인 — 기본 구현은 input(). 테스트는 이 자리에 가짜를 넣는다.
# -----------------------------------------------------------------------------


def default_confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


#: 무인 실행에서 **자동 승인하지 않는** 확인의 표지.
#:
#: 이 도구의 확인은 두 종류다. "1단계 — 토큰을 발급받는다. 계속할까?" 는
#: 절차를 넘기는 물음이고, "킬스위치가 걸려 있다. **그래도** 계속할까?" 는
#: **위험을 알면서 밀고 갈까** 라는 물음이다. 둘을 한 스위치로 자동 승인하면
#: 무인 실행이 킬스위치를 스스로 무시한다 — 안전장치를 켜 둔 의미가 없다.
OVERRIDE_MARKER = "그래도 계속할까"


def auto_confirm(prompt: str) -> bool:
    """``--assume-yes`` 의 확인 구현. **위험 확인은 거부한다.**

    거부하면 그 자리에서 절차가 멈추고 종료코드가 0 이 아니게 된다. 무인
    실행에서 그게 옳다 — 킬스위치가 걸렸거나 정규장이 아니면 사람이 봐야 한다.
    """
    if OVERRIDE_MARKER in prompt:
        print(f"  [무인] 거부 — 사람이 봐야 한다: {prompt}")
        return False
    print(f"  [무인] 승인: {prompt}")
    return True


def auto_prompt(prompt: str) -> str:
    """``--assume-yes`` 의 입력 구현. 빈 문자열 = 그 자리의 기본값."""
    print(f"  [무인] 기본값: {prompt}")
    return ""


def default_prompt(prompt: str) -> str:
    try:
        return input(f"{prompt}: ").strip()
    except EOFError:
        return ""


# -----------------------------------------------------------------------------
# 순수 계산 — 확인/시세와 무관하게 테스트할 수 있는 부분
# -----------------------------------------------------------------------------


def _shcode(symbol: str) -> str:
    """t1102/t0424 가 쓰는 순수 6자리 코드. 주문용 ``isu_code`` 와 다르게
    "A" 접두어가 없다 (LS_KR ls_client.py get_current_price/get_account_balance).

    시장 접두어(``KR:``)도 뗀다 — 창고 정본이 그 모양이다."""
    stripped = symbol.strip()
    _, _, bare = stripped.rpartition(":")
    return (bare or stripped).lstrip("A")


def canonical_entity(market: str, symbol: str) -> str:
    """창고 정본 ``KR:067290`` · ``US:SNAP``.

    **``trades`` 는 시장 접두어를 요구한다**(TableSpec.market_prefixed_entity).
    안 붙이면 체결을 적는 순간 SchemaViolation 으로 튕긴다 — 주문은 이미
    나간 뒤라 실계좌와 장부가 갈라진다(2026-08-18 실측).
    """
    bare = symbol.strip()
    _, _, bare = bare.rpartition(":")
    return f"{market}:{bare or symbol.strip()}"


def _num(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Quote:
    price: float
    bid: float
    ask: float
    raw: dict[str, Any] = field(default_factory=dict)
    #: 호가단위. 미장은 LS 가 종목별로 준다(``g3104.untprc``). 국장은 0 —
    #: ``executor/ticks.py`` 의 표를 쓴다.
    tick: float = 0.0
    #: 미장 주문시장코드(81/82). 시세가 나온 쪽을 그대로 주문에 쓴다.
    market_code: str = ""
    #: 거래정지·매수금지. 비어 있으면 정상.
    halt: str = ""


def reference_price(quote: Quote, side: Side) -> float:
    """주문에 쓸 기준가. 매수는 매도호가1(즉시 체결 가능한 쪽), 매도는
    매수호가1을 우선한다 — 이 검증은 배선 확인이 목적이라 체결이 빨리
    나야 다음 단계(체결조회)를 바로 밟을 수 있다. 호가가 비어 있으면
    현재가로 물러선다."""
    if side is Side.BUY:
        return quote.ask if quote.ask > 0 else quote.price
    return quote.bid if quote.bid > 0 else quote.price


def round_to_tick_us(price: float, *, side: Side, tick: float) -> float:
    """미장 호가단위 반올림. **표를 만들지 않는다** — ``tick`` 은 LS 가 준
    종목별 ``untprc`` 다(``fetch_quote_us``).

    방향 규칙은 국장과 같다: **매수는 내림, 매도는 올림.** 슬리피지 상한을
    넘지 않는 방향으로만 옮긴다(``executor/ticks.py`` 참고).

    ``executor/ticks.py`` 를 못 쓰는 이유는 표가 원화 전용이라서만이 아니라
    ``tick_size()`` 가 ``int`` 를 돌려주기 때문이다 — $0.01 은 거기서 표현이
    안 된다.
    """
    if tick <= 0:
        raise ValueError(f"호가단위는 양수여야 한다: {tick}")
    scaled = price / tick
    steps = math.floor(scaled + _TICK_EPS) if side is Side.BUY else math.ceil(scaled - _TICK_EPS)
    # tick 이 0.0001 까지 내려가므로 8자리에서 끊는다 — 부동소수점 찌꺼기가
    # 그대로 주문가로 나가면 거래소가 거부한다.
    return round(steps * tick, 8)


#: ``executor/ticks.py`` 의 ``_EPS`` 와 같은 목적 — 정확히 tick 배수인 가격이
#: 부동소수점 오차로 한 칸 밀리는 것을 막는다.
_TICK_EPS = 1e-9


@dataclass(frozen=True)
class OrderSummary:
    symbol: str
    side: Side
    quantity: int
    price: float
    amount: float
    ok: bool
    reason: str = ""
    currency: str = "KRW"

    def render(self) -> str:
        if self.currency == "KRW":
            return (
                f"  종목 {self.symbol} · {self.side.value} · {self.quantity}주 · "
                f"지정가 {self.price:,.0f} · 예상금액 {self.amount:,.0f}원"
            )
        return (
            f"  종목 {self.symbol} · {self.side.value} · {self.quantity}주 · "
            f"지정가 ${self.price:,.4f} · 예상금액 ${self.amount:,.2f} {self.currency}"
        )


def build_order_summary(
    *,
    symbol: str,
    side: Side,
    quantity: float,
    raw_reference_price: float,
    max_order_value: float,
    tick: float | None = None,
    currency: str = "KRW",
) -> OrderSummary:
    """주문 직전 요약. **호가단위 반올림을 여기서 강제한다** — 안 거치면
    거래소가 거부한다.

    ``tick`` 이 ``None`` 이면 국장이다 — ``executor/ticks.py`` 의 표를 쓴다.
    값이 있으면 미장이고 그 값이 호가단위다(LS 가 종목별로 준다).
    """
    unit = "원" if currency == "KRW" else f" {currency}"
    if int(quantity) != quantity:
        return OrderSummary(
            symbol, side, int(quantity), 0.0, 0.0, False,
            f"소수점 수량 {quantity} — LS 는 정수주만 받는다. "
            "반올림하면 예수금 검사를 통과한 금액과 어긋나므로 깎지 않고 거부한다.",
            currency,
        )
    quantity = int(quantity)
    price = (
        round_to_tick(raw_reference_price, side=side)
        if tick is None
        else round_to_tick_us(raw_reference_price, side=side, tick=tick)
    )
    amount = price * quantity
    if amount > max_order_value:
        return OrderSummary(
            symbol, side, quantity, price, amount, False,
            f"예상금액 {amount:,.4f}{unit} 이 상한 {max_order_value:,.4f}{unit} 을 넘는다",
            currency,
        )
    return OrderSummary(symbol, side, quantity, price, amount, True, currency=currency)


# -----------------------------------------------------------------------------
# 네트워크 — 읽기 (잔고 · 시세)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BalanceSummary:
    net_asset: float
    positions: tuple[dict[str, Any], ...]
    paper: bool


def fetch_balance(client: LSClient) -> BalanceSummary:
    """t0424. paper 모드(``LSClient.live_trading=False``)면 애초에 나가지
    않는다 — ``PAPER_ALLOWED_TR`` 에 없다(``collectors/ls_client.py``)."""
    body = {
        "t0424InBlock": {
            "prcgb": "1",
            "chegb": "2",
            "dangb": "0",
            "charge": "1",
            "cts_expcode": "",
        }
    }
    data = client.request_tr(PATH_ACCNO, TR_BALANCE, body)
    if data.get("paper"):
        return BalanceSummary(net_asset=0.0, positions=(), paper=True)
    summary = data.get("t0424OutBlock") or {}
    positions = tuple(data.get("t0424OutBlock1") or [])
    return BalanceSummary(net_asset=_num(summary, "sunamt"), positions=positions, paper=False)


def fetch_quote(client: LSClient, symbol: str) -> Quote | None:
    """t1102. paper 모드에서도 허용되는 TR 이라 ``--live`` 없이도 실제로
    나간다(``PAPER_ALLOWED_TR``) — 키가 있으면."""
    body = {"t1102InBlock": {"shcode": _shcode(symbol)}}
    data = client.request_tr(PATH_MARKET, TR_QUOTE, body)
    if data.get("paper"):
        return None
    block = data.get("t1102OutBlock") or {}
    return Quote(
        price=_num(block, "price"),
        bid=_num(block, "bidho1"),
        ask=_num(block, "offerho1"),
        raw=block,
    )


def fetch_balance_us(client: LSClient) -> BalanceSummary:
    """COSOQ02701 — **USD 주문가능금액.** 국장 ``t0424`` 의 ``sunamt`` 자리다.

    ``net_asset`` 에 ``FcurrOrdAbleAmt``(주문가능금액)를 넣는다. 예수금
    (``FcurrDps``)이 아니라 주문가능금액인 이유는, 미수를 막으려면 "지금 이
    주문에 쓸 수 있는 현금" 과 비교해야 하기 때문이다.

    같은 응답의 ``WonDpsBalAmt``(원화 예수금)를 잘못 읽으면 5원이 나온다 —
    국장 ``t0424`` 를 미장 계좌로 불렀을 때 나오던 그 ``5`` 다.
    **통화가 다른 두 숫자를 한 필드에 섞지 마라.**
    """
    body = {f"{TR_BALANCE_US}InBlock1": {"RecCnt": 1, "CrcyCode": "USD"}}
    try:
        data = client.request_tr(PATH_ACCNO_US, TR_BALANCE_US, body)
    except LSAPIError as error:
        if error.rsp_cd == NO_ROWS:
            # 조회는 됐고 내역이 0 건이다. **0 으로 진행한다** — 안전한 방향이다
            # (주문금액 > 0 이므로 예수금 검사에서 반드시 걸린다).
            return BalanceSummary(net_asset=0.0, positions=(), paper=False)
        raise
    if data.get("paper"):
        return BalanceSummary(net_asset=0.0, positions=(), paper=True)
    rows = data.get(f"{TR_BALANCE_US}OutBlock3") or []
    usd = next((row for row in rows if str(row.get("CrcyCode", "")).upper() == "USD"), {})
    # **``positions`` 는 비워 둔다.** 이 TR 은 예수금이지 잔고평가가 아니다 —
    # OutBlock3 의 행은 **통화**(USD·KRW…)이지 종목이 아니다. 그걸 그대로
    # positions 에 담으면 화면이 **"보유 1종목"** 이라고 말한다. 실제로 그랬고,
    # 계좌에는 아무것도 없었다(``COSOQ00201`` 이 "조회내역이 없습니다").
    # 실주문 직전에 잔고를 오독하게 만드는 자리다. 보유 종목이 필요하면
    # 잔고평가(``COSOQ00201``)를 따로 불러야 한다.
    return BalanceSummary(
        net_asset=_num(usd, "FcurrOrdAbleAmt"),
        positions=fetch_holdings_us(client),
        paper=False,
    )


def fetch_holdings_us(client: LSClient) -> tuple[dict[str, Any], ...]:
    """COSOQ00201 OutBlock4 — **실제 보유 종목.**

    예수금 TR 은 종목을 안 준다. 그걸 모르고 "보유 0종목" 을 찍고 있었고,
    2026-08-17 실주문에서 SNAP 1주가 체결됐는데도 화면은 0 이라고 말했다.
    숫자는 이미 답을 갖고 있었다 — 같은 예수금 응답의 ``FcurrPldgAmt`` 가
    5.17 로 매수분을 가리키고 있었다.

    **조회 실패를 "보유 없음" 으로 읽지 않는다.** 인자가 틀리면 LS 는
    ``02679 조회내역이 없습니다`` 로 답한다 — 그 코드는 "정말 없다" 와
    "잘못 물었다" 를 구분해 주지 않는다. 그래서 예외를 삼키되 그 사실이
    호출부에 보이도록 빈 튜플이 아니라 **경고 행**을 섞지는 않고, 대신
    여기서 로그를 남길 수 없으므로 예외를 그대로 올린다. 호출부가 잔고를
    못 읽으면 주문을 내면 안 된다.
    """
    data = client.request_tr(
        PATH_ACCNO_US, TR_HOLDINGS_US, {f"{TR_HOLDINGS_US}InBlock1": HOLDINGS_BODY_US}
    )
    rows = data.get(f"{TR_HOLDINGS_US}OutBlock4") or []
    return tuple(
        row for row in rows if _num(row, "AstkBalQty") > 0.0
    )


def fetch_quote_us(client: LSClient, symbol: str) -> Quote | None:
    """g3104 — 미장 현재가. **주문시장코드를 여기서 확정한다.**

    82(나스닥)→81(뉴욕) 순으로 시도해 **응답이 온 쪽**을 ``market_code`` 로
    돌려준다. 틀린 코드로 부르면 "해당종목이 없습니다" 가 오므로(2026-08-15
    실측) 이 시도 자체가 확인 절차다 — 주문시장을 짐작하지 않는다.

    **호가(bid/ask)는 0 으로 둔다.** ``g3104`` 의 ``bidlotsize``/``asklotsize``
    는 호가 **수량 단위**이지 가격이 아니다(실측: 전부 ``1``). 미장 REST 시세에
    호가 가격은 없다 — ``reference_price`` 가 현재가로 물러선다.
    """
    for code in MARKET_CODES:
        ticker = us_symbol(symbol)
        body = {
            f"{TR_QUOTE_US}InBlock": {
                "keysymbol": f"{code}{ticker}",
                "exchcd": code,
                "symbol": ticker,
            }
        }
        data = client.request_tr(PATH_MARKET_US, TR_QUOTE_US, body)
        if data.get("paper"):
            return None
        block = data.get(f"{TR_QUOTE_US}OutBlock") or {}
        if not block:
            # 이 거래소에는 없는 종목이다 — 다음 코드로 넘어간다.
            continue
        halt = []
        if str(block.get("suspend", "N")).upper() not in ("N", ""):
            halt.append(f"거래정지({block.get('suspend')})")
        if str(block.get("sellonly", "0")) not in ("0", ""):
            halt.append(f"매수금지({block.get('sellonly')})")
        return Quote(
            price=_num(block, "clos"),
            bid=0.0,
            ask=0.0,
            raw=block,
            tick=_num(block, "untprc"),
            market_code=code,
            halt=" · ".join(halt),
        )
    return None


# -----------------------------------------------------------------------------
# 네트워크 — 쓰기 (주문 · 정정 · 취소) 와 그 드라이런 미리보기
# -----------------------------------------------------------------------------


def preview_cancel_body(*, symbol: str, order_no: str, quantity: int) -> dict[str, Any]:
    """CSPAT00801 본문 미리보기. ``ls_order.LSBroker.cancel`` 과 같은 모양 —
    거기는 메서드 안에 인라인돼 있어 미리보기용으로 여기서 다시 짠다."""
    return {
        "CSPAT00801InBlock1": {
            "OrgOrdNo": int(order_no),
            "IsuNo": isu_code(symbol),
            "OrdQty": int(quantity),
        }
    }


def preview_modify_body(
    *, symbol: str, order_no: str, quantity: int, price: float
) -> dict[str, Any]:
    """CSPAT00701 본문 미리보기. ``ls_order.LSBroker.modify`` 와 같은 모양."""
    return {
        "CSPAT00701InBlock1": {
            "OrgOrdNo": int(order_no),
            "IsuNo": isu_code(symbol),
            "OrdQty": int(quantity),
            "OrdprcPtnCode": ORD_PTN_LIMIT,
            "OrdCndiTpCode": ORD_CNDI_NONE,
            "OrdPrc": int(price),
        }
    }


def make_planned_order(
    *, symbol: str, side: Side, quantity: int, price: float | None, clock: Clock,
    market: str = "KR",
) -> PlannedOrder:
    """검증 전용 주문 하나. 세션에 실행 시각과 방향을 같이 넣어 매 실행·매
    방향마다 새 ``order_id`` 가 나오게 한다. 시각만 쓰면 매수·매도가 같은
    초에 나갈 때(리플레이 시계는 아예 안 흐른다) ``client_order_id`` 가
    entity_id·slice_seq 까지 같아 **같은 order_id** 가 되고, ``LSBroker`` 의
    멱등 캐시(§멱등성, ``broker/ls_order.py``)가 매도를 매수 결과로
    덮어써 버린다 — 재전송이 아니라 두 번째 전송 자체가 씹힌다."""
    session = f"verify-{clock.now():%Y%m%dT%H%M%S}-{side.value}"
    # **창고 정본으로 만든다.** trades 는 시장 접두어를 요구하고, 주문 본문은
    # isu_code()/us_symbol() 이 그 접두어를 뗀다. 순수 코드로 두면 주문은
    # 나가는데 체결을 적을 때 SchemaViolation 으로 튕긴다 — 그러면 실계좌와
    # 장부가 갈라지고, append-only 창고에서 그건 되돌릴 수 없다.
    entity = canonical_entity(market, symbol)
    order = Order(
        entity_id=entity, side=side, quantity=quantity, limit_price=price,
        reason="verify_live_order",
    )
    return PlannedOrder(
        order=order,
        order_id=client_order_id(session=session, entity_id=entity, slice_seq=0),
        session_id=session,
        slice_seq=0,
        target_weight=0.0,
    )


# -----------------------------------------------------------------------------
# 실행 — 8단계
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketProfile:
    """시장 하나가 갈리는 지점 전부. **분기를 여기 한 곳에 모은다** —
    ``run()`` 안에 ``if market == "US"`` 가 흩어지면 국장 경로가 미장 변경에
    흔들린다.
    """

    market: Market
    #: ``.env`` 접두어. 국장·미장은 **별도 appkey** 다 — 같은 키를 공유하면
    #: 한쪽 재발급이 다른 쪽 토큰을 IGW00121 로 죽인다.
    env_prefix: str
    currency: str
    default_max_order_value: float
    #: 지문 고정 설정 키.
    fingerprint_key: str
    #: 지문이 **비어 있어도** 진행할지. 국장은 기존 규약(빈 값 = 고정 안 함)을
    #: 그대로 둔다 — 여기서 바꾸면 8/18 국장 검증이 깨진다. 미장은 새로 만드는
    #: 경로라 처음부터 조인다: **미선언이면 진행하지 않는다.**
    allow_unpinned: bool
    balance_tr: str
    quote_tr: str
    order_tr: str
    min_interval_sec: float
    #: 체결조회 이후(부분취소·정정·매도 정리)까지 자동으로 이을 수 있는가.
    supports_lifecycle: bool

    def amount(self, value: float) -> str:
        return f"{value:,.0f}원" if self.currency == "KRW" else f"${value:,.4f} {self.currency}"


PROFILES: dict[str, MarketProfile] = {
    "KR": MarketProfile(
        market=Market.KR,
        env_prefix="LS_",
        currency="KRW",
        default_max_order_value=DEFAULT_MAX_ORDER_VALUE,
        fingerprint_key=broker_factory.FINGERPRINT_KEY_KR,
        allow_unpinned=True,
        balance_tr=TR_BALANCE,
        quote_tr=TR_QUOTE,
        order_tr="CSPAT00601",
        min_interval_sec=MIN_INTERVAL_SEC_KR,
        supports_lifecycle=True,
    ),
    "US": MarketProfile(
        market=Market.US,
        env_prefix="LS_US_",
        currency="USD",
        default_max_order_value=DEFAULT_MAX_ORDER_VALUE_US,
        fingerprint_key=broker_factory.FINGERPRINT_KEY_US,
        allow_unpinned=False,
        balance_tr=TR_BALANCE_US,
        quote_tr=TR_QUOTE_US,
        order_tr="COSAT00301",
        min_interval_sec=MIN_INTERVAL_SEC_US,
        # 체결조회까지는 붙었지만(``broker/fills.py`` 미장 분기), 부분취소·정정
        # 본문은 **한 번도 보낸 적이 없다.** 첫 실행에서 사람이 관찰해야 한다.
        supports_lifecycle=False,
    ),
}


def resolve_profile(store: Any, *, market: str, as_of: datetime) -> MarketProfile:
    """``execution.account_mode`` 에 맞는 프로파일. **모의면 모의 키를 집는다.**

    이 함수가 없으면 도구들이 언제나 ``LS_``(실전)를 집는다. 2026-08-22 에
    모의투자 배선을 넣으면서 ``broker/factory.py`` 는 모드를 보게 됐는데
    도구 쪽은 그대로였다 — 그러면 "모의로 돌린다" 고 생각하며 **실전 계좌를
    청산하는** 경로가 남는다. 그게 이 분야에서 가장 흔한 사고다
    (``docs/design/ls-api.md`` §모의/실전 구분).

    모드별 (env_prefix, fingerprint_key) 는 ``broker_factory.PROFILES`` 하나가
    쥔다. 여기서 따로 적으면 한쪽만 고쳐지는 날이 온다.
    """
    base = PROFILES[market]
    try:
        raw = store.config(broker_factory.ACCOUNT_MODE_KEY, as_of=as_of)
        mode = str(raw or broker_factory.MODE_PAPER).strip().lower()
    except Exception:  # ConfigNotFound 포함 — 모르면 모의다(factory 와 같은 규약)
        mode = broker_factory.MODE_PAPER
    live = broker_factory.PROFILES.get((market.upper(), mode))
    if live is None:
        # 그 시장에 그 모드의 배선이 없다. 실전 프로파일로 **떨어뜨리지 않는다** —
        # 키를 못 찾아 아래에서 멈추는 편이 낫다.
        return replace(base, env_prefix=f"__NO_WIRING_{mode.upper()}_", allow_unpinned=False)
    return replace(
        base,
        env_prefix=live.env_prefix,
        fingerprint_key=live.fingerprint_key,
        allow_unpinned=live.allow_unpinned,
    )


@dataclass
class RunConfig:
    symbol: str
    quantity: int
    max_order_value: float
    live: bool
    dry_run: bool
    market: str = "KR"
    #: 지정가 대신 시장가로 낸다. **체결을 보는 것이 목적일 때만.**
    market_order: bool = False

    @property
    def profile(self) -> MarketProfile:
        return PROFILES[self.market]


def run(
    config: RunConfig,
    *,
    store: Store,
    client: LSClient,
    clock: Clock,
    confirm: Callable[[str], bool] = default_confirm,
    prompt: Callable[[str], str] = default_prompt,
    out: Callable[[str], None] = print,
) -> int:
    """전체 절차. 반환값은 종료코드 — 0 이 아니면 사람이 봐야 한다.

    **여기서 만드는 ``LSClient`` 는 호출자가 넘긴다.** 이 함수 자체는
    실전 여부를 판단만 하지 스스로 파일·환경을 읽지 않는다 — 그래야
    테스트에서 진짜 네트워크·자격증명 없이 이 함수를 그대로 돌릴 수 있다.
    """
    profile = config.profile
    dry_run = config.dry_run or not config.live
    out(f"시장: {config.market} ({profile.currency}) · 자격증명 {profile.env_prefix}*")
    out(
        f"검증 대상: {config.symbol} · {config.quantity}주 · "
        f"상한 {profile.amount(config.max_order_value)}"
    )
    out(f"모드: {'실전' if config.live else '페이퍼(--live 없음)'}"
        f" · {'드라이런(전송 안 함)' if dry_run else '실제 전송'}")

    # -- 사전 점검 --------------------------------------------------------
    if not client.credentials.usable():
        out(f"자격증명 없음 — {profile.env_prefix}APPKEY/{profile.env_prefix}APPSECRET 이 "
            ".env 에 없거나 템플릿 값이다.")
        out("(값 자체는 여기서 읽지도 출력하지도 않는다 — 존재 여부만 본다.)")
        return 1
    out(f"자격증명 존재 확인 — .env 에 {profile.env_prefix}APPKEY/"
        f"{profile.env_prefix}APPSECRET 있음")

    # **계좌 선언 게이트.** 코드는 모의·실전을 판별할 수 없다 — 같은 호스트를
    # 쓰고, 모의 키로도 t0424 가 응답한다(2026-08-15 실측). 그래서 사람이
    # 선언하게 하고, 그 선언을 **지문에 묶어** 확인한다. .env 를 바꿔치기하면
    # 지문이 달라져 여기서 걸린다.
    kind = client.credentials.declared_kind
    fingerprint = client.credentials.fingerprint
    out(f"계좌 선언: kind={kind or '(미선언)'} · appkey 지문={fingerprint or '(없음)'}")
    if kind not in ("real", "paper"):
        out(f"{profile.env_prefix}ACCOUNT_KIND 가 선언되지 않았다 — "
            ".env 에 real 또는 paper 로 적어야 한다.")
        out("**모르는 것을 모의로 가정하지 않는다.** 그 가정이 실전 주문을 낸다.")
        return 1
    if config.live:
        # 지문 고정은 **진짜 주문이 나갈 때만** 본다. 드라이런은 어차피 안 나간다.
        pinned = str(store.config(profile.fingerprint_key, as_of=clock.now()) or "")
        if not pinned and not profile.allow_unpinned:
            out(f"{profile.fingerprint_key} 가 비어 있다 — 어느 계좌에 주문할지 "
                "고정되지 않았다. 진행하지 않는다.")
            out(f"이 키의 appkey 지문은 {fingerprint} 다. 맞는 계좌가 확실하면 "
                "설정에 그 값을 적어라.")
            return 1
        if pinned and pinned != fingerprint:
            out(f"지문 불일치 — 설정에 고정된 계좌는 {pinned} 인데 지금 키는 {fingerprint} 다.")
            out(".env 가 바뀌었거나 다른 계좌를 보고 있다. 진행하지 않는다.")
            return 1
        if kind != "real":
            out(f"--live 인데 선언이 kind={kind} 다. 실전 주문은 real 계좌에만 낸다.")
            return 1

        live_cfg = bool(store.config("execution.live_trading", as_of=clock.now()))
        if not live_cfg:
            out("execution.live_trading 이 꺼져 있다 — 이 상태로는 진행하지 않는다.")
            out("config/quant_rl_trading.yaml 의 execution.live_trading 을 true 로 켜거나,")
            out("store.config 오버라이드로 as_of 시점 값을 켜야 한다.")
            return 1
        if not client.live_trading:
            out(
                "LSClient.live_trading 이 꺼져 있다 — 이 도구를 만든 쪽이 "
                "client=LSClient(..., live_trading=True) 로 넘겨야 한다."
            )
            return 1
        out("게이트 둘 다 열림 — execution.live_trading=True · LSClient.live_trading=True")
    else:
        out(
            "--live 없음 — execution.live_trading 확인을 건너뛴다. "
            "주문 TR 은 client 단에서부터 막혀 있다."
        )

    switch = guards.check_killswitch(store, as_of=clock.now())
    if not switch:
        out(f"⚠️  킬스위치 발동 중 — {switch.reason}. 진행해도 되는지 사람이 먼저 판단해야 한다.")
        if not confirm("킬스위치가 걸려 있다. 그래도 계속할까?"):
            out("중단.")
            return 1

    if not is_regular_session(profile.market, clock.now()):
        out(f"⚠️  지금은 {config.market} 정규장 시간이 아니다 — 시세·체결이 기대와 다를 수 있다.")
        if not confirm("정규장 시간이 아니다. 그래도 계속할까?"):
            out("중단.")
            return 1

    symbol = config.symbol.strip()
    quantity = config.quantity

    # -- 1. 토큰 발급 ------------------------------------------------------
    if not confirm("1단계 — 토큰을 발급받는다. 계속할까?"):
        out("중단.")
        return 1
    try:
        token = client.ensure_token(allow_paper=True)
    except (LSAPIError, MissingCredentials) as error:
        out(f"토큰 발급 실패: {error}")
        return 1
    out(f"  토큰 확보 ({'PAPER' if token.access_token == 'PAPER_PLACEHOLDER' else '실전'})")

    is_us = profile.market is Market.US

    # -- 2. 잔고 조회 ------------------------------------------------------
    if not confirm(f"2단계 — 잔고를 조회한다 ({profile.balance_tr}). 계속할까?"):
        out("중단.")
        return 1
    try:
        balance = fetch_balance_us(client) if is_us else fetch_balance(client)
    except (LSAPIError, MissingCredentials) as error:
        out(f"잔고 조회 실패: {error}")
        return 1
    if balance.paper:
        out(f"  잔고 조회 생략 — paper 모드({profile.balance_tr} 는 PAPER_ALLOWED_TR 밖이다)")
    elif is_us:
        out(f"  주문가능금액 {profile.amount(balance.net_asset)} (FcurrOrdAbleAmt)")
    else:
        out(f"  추정순자산 {balance.net_asset:,.0f}원 · 보유종목 {len(balance.positions)}개")

    # -- 3. 시세 조회 ------------------------------------------------------
    if not confirm(f"3단계 — {symbol} 현재가를 조회한다 ({profile.quote_tr}). 계속할까?"):
        out("중단.")
        return 1
    try:
        quote = fetch_quote_us(client, symbol) if is_us else fetch_quote(client, symbol)
    except (LSAPIError, MissingCredentials) as error:
        out(f"시세 조회 실패: {error}")
        return 1
    if quote is None:
        out("  시세를 못 받았다 (paper 응답이거나 빈 응답) — 중단한다.")
        if is_us:
            out(f"  {MARKET_CODES} 둘 다 '해당종목이 없습니다' 였다면 심볼을 다시 확인해라.")
        return 1
    if is_us:
        out(f"  현재가 ${quote.price:,.4f} · 호가단위 ${quote.tick} · "
            f"주문시장 {quote.market_code}({'나스닥' if quote.market_code == '82' else '뉴욕'})")
        out("  호가(bid/ask)는 미장 REST 시세에 없다 — 기준가는 현재가로 물러선다.")
        if quote.halt:
            out(f"  ⚠️  {quote.halt} — 주문이 거부될 수 있다.")
            if not confirm("거래 제한이 걸려 있다. 그래도 계속할까?"):
                out("중단.")
                return 1
        if quote.tick <= 0:
            out("  호가단위(untprc)를 못 읽었다 — 반올림 기준이 없으므로 중단한다.")
            return 1
    else:
        out(f"  현재가 {quote.price:,.0f} · 매도호가1 {quote.ask:,.0f} · "
            f"매수호가1 {quote.bid:,.0f}")

    # -- 4. 주문 직전 요약 · 확인 · 매수 ------------------------------------
    buy_price = reference_price(quote, Side.BUY)
    buy_summary = build_order_summary(
        symbol=symbol, side=Side.BUY, quantity=quantity,
        raw_reference_price=buy_price, max_order_value=config.max_order_value,
        tick=quote.tick if is_us else None, currency=profile.currency,
    )
    out("4단계 — 매수 주문 요약")
    out(buy_summary.render())
    if not buy_summary.ok:
        out(f"  {buy_summary.reason} — 중단한다.")
        return 1

    # **신용·미수 금지 — 예수금 안에서만 산다.**
    # 주문 본문의 ``MgntrnCode="000"``(보통매매)은 **신용거래**만 막는다.
    # 신용이 아니어도 예수금을 넘겨 사면 **미수금**이 잡히고 D+2 에 못 갚으면
    # 반대매매가 나간다. 그건 우리가 고른 포지션이 아니라 증권사가 정한
    # 시점·가격에 강제로 팔리는 것이다 — 잔고를 읽어 놓고 검사하지 않으면
    # 그 숫자는 화면 장식일 뿐이다.
    # 미장은 통화가 다르다 — ``FcurrOrdAbleAmt`` 도 주문금액도 USD 라 그대로
    # 비교된다. **원화 값과 섞으면 안 된다**(같은 응답의 WonDpsBalAmt 는 5원이다).
    if not balance.paper:
        need = buy_summary.amount
        if need > balance.net_asset:
            out(
                f"  예수금 부족 — 필요 {profile.amount(need)} > "
                f"주문가능 {profile.amount(balance.net_asset)}. 미수가 되므로 중단한다."
            )
            return 1
        out(f"  예수금 확인 {profile.amount(balance.net_asset)} ≥ "
            f"주문금액 {profile.amount(need)} — 현금 범위 안")

    if dry_run:
        if is_us:
            body = us_order_body(
                symbol=symbol, side=Side.BUY, quantity=quantity,
                limit_price=buy_summary.price, market_code=quote.market_code,
            )
        else:
            body = _order_body(
                symbol=symbol, side=Side.BUY, quantity=quantity, limit_price=buy_summary.price
            )
        out(f"  드라이런 — 전송하지 않는다. 보낼 본문: {body}")
        if is_us:
            cancel = us_cancel_body(
                order_no="0", quantity=quantity, market_code=quote.market_code
            )
            out(f"  참고 — 취소 본문: {cancel}")
            modify = us_modify_body(
                order_no="0", price=buy_summary.price, market_code=quote.market_code
            )
            out(f"  참고 — 정정 본문: {modify}")
            out("  (원주문번호 0 은 자리표시다. 취소·정정 본문은 아직 한 번도 보낸 적이 없다.)")
        out(
            "드라이런이라 이후 단계(체결조회·정정·취소·매도)도 실물 없이는 "
            "진행할 수 없다. 여기서 끝낸다."
        )
        return 0

    if not confirm("위 내용으로 매수 주문을 실제로 낸다. 계속할까?"):
        out("중단.")
        return 1

    # **시장가는 limit_price=None 으로 표현한다** (broker/ls_order.py §_order_body).
    # 예수금 검사·요약은 그대로 현재가 기준으로 한다 — 시장가라도 얼마쯤 나갈지
    # 모르고 보내면 안 되고, 국장 상하한이 ±30% 라 그 안에서는 현재가 기준
    # 여유로 판단할 수 있다.
    planned = make_planned_order(
        symbol=symbol, side=Side.BUY, quantity=quantity,
        price=None if config.market_order else buy_summary.price, clock=clock,
        market=config.market,
    )
    if config.market_order:
        out("  **시장가로 낸다** — 체결가는 현재가와 다를 수 있다(배선 검증 목적).")
    broker: LSBroker | LSUSBroker = (
        LSUSBroker(client=client, store=store, market_code=quote.market_code)
        if is_us
        else LSBroker(client=client, store=store)
    )
    try:
        buy_ack = broker.submit(planned, as_of=clock.now())
    except RejectedOrder as error:
        out(f"  거부됨 — {error}")
        return 1
    except BrokerError as error:
        out(f"  전송 결과를 모른다(BrokerError) — {error}")
        out("  재전송하지 말고 잔고·체결 화면을 직접 확인해야 한다.")
        return 1

    out(f"  전송 결과: sent={buy_ack.sent} broker_order_no={buy_ack.broker_order_no} "
        f"rsp_cd={buy_ack.rsp_cd} msg={buy_ack.rsp_msg}")
    if not buy_ack.sent or buy_ack.broker_order_no is None:
        out("  주문번호가 없다 — 이후 단계(체결조회)를 이 도구가 자동으로 이어갈 수 없다.")
        out("  LS 화면에서 직접 확인해야 한다.")
        return 1

    # -- 5. 체결 조회 · (필요하면) 부분취소 테스트 --------------------------
    fills_tr = "COSAQ00102" if is_us else "t0425"
    if not confirm(f"5단계 — 체결을 확인한다 ({fills_tr}). 계속할까?"):
        out("중단.")
        return 0

    filled = _poll_fill(
        store=store, client=client, clock=clock, order_id=planned.order_id,
        symbol=symbol, side=Side.BUY, broker_order_no=buy_ack.broker_order_no,
        requested_quantity=quantity, market=config.market, out=out,
    )
    remaining = quantity - filled

    if not profile.supports_lifecycle:
        # 여기서 멈추는 것을 **크게** 알린다. 조용히 0 으로 끝나면
        # "안 샀다" 로 오해하고 다시 주문하게 된다.
        out("")
        out("=" * 68)
        out(f"⚠️  {config.market} 는 여기까지다 — 부분취소·정정·매도 정리를 자동으로 잇지 않는다.")
        out("    미장 취소·정정 본문은 아직 한 번도 보낸 적이 없어(문서 등급 '샘플코드'),")
        out("    이 도구가 사람 확인 없이 내보내면 안 된다.")
        out(f"    지금 상태: 주문번호 {buy_ack.broker_order_no} · 누적체결 {filled}/{quantity}주")
        if remaining > 0:
            out(f"    미체결 {remaining}주가 남아 있다 — LS 화면에서 직접 취소해라.")
        if filled > 0:
            out(f"    체결 {filled}주가 계좌에 남아 있다 — LS 화면에서 직접 매도해라.")
        out("    관찰한 것을 docs/design/ls-api.md §0-5 에 적어 등급을 올려라.")
        out("=" * 68)
        return 0

    if remaining > 0 and confirm(
        f"미체결 잔량이 {remaining}주 남았다. 부분취소 테스트를 해볼까?"
        " (CSPAT00801 의 OrdQty 의미를 여기서 직접 관찰한다)"
    ):
        answer = prompt(f"취소할 수량 (1~{remaining}, 그냥 Enter 면 {remaining} 전체)")
        try:
            cancel_qty = int(answer) if answer else remaining
        except ValueError:
            cancel_qty = remaining
        cancel_qty = max(1, min(cancel_qty, remaining))
        body = preview_cancel_body(
            symbol=symbol, order_no=buy_ack.broker_order_no, quantity=cancel_qty
        )
        out(f"  보낼 취소 본문: {body}")
        if confirm(f"{cancel_qty}주 취소를 실제로 보낸다. 계속할까?"):
            cancel_ack = broker.cancel(
                broker_order_no=buy_ack.broker_order_no, entity_id=symbol, quantity=cancel_qty
            )
            out(
                f"  취소 결과: sent={cancel_ack.sent} rsp_cd={cancel_ack.rsp_cd} "
                f"msg={cancel_ack.rsp_msg}"
            )
            out(
                "  → docs/live-order-checklist.md 에 관찰 결과(정확히 cancel_qty 만 줄었는지,"
                " 초과 요청 시 01443 거부가 뜨는지)를 적어 둔다."
            )

    # -- 6. 정정(재호가) 테스트 — 선택 --------------------------------------
    # 취소 성공 여부와 무관하게 최신 조회로 다시 확인하는 것이 정확하지만,
    # 여기서는 간단히 남은 목표수량 - 체결량으로 근사한다.
    remaining_after_cancel = quantity - filled
    if remaining_after_cancel > 0 and confirm(
        "미체결이 남아 있다. 재호가(정정) 테스트를 해볼까?"
        " (lifecycle.decide 의 market_price 로 무엇을 먹여야 하는지 여기서 관찰한다)"
    ):
        try:
            fresh = fetch_quote(client, symbol)
        except (LSAPIError, MissingCredentials) as error:
            out(f"  재호가용 시세 조회 실패: {error}")
            fresh = None
        if fresh is not None:
            new_price = round_to_tick(reference_price(fresh, Side.BUY), side=Side.BUY)
            out(
                f"  최우선 매도호가 {fresh.ask:,.0f} · 현재가 {fresh.price:,.0f} "
                f"→ 새 지정가 후보 {new_price:,.0f}"
            )
            body = preview_modify_body(
                symbol=symbol, order_no=buy_ack.broker_order_no,
                quantity=remaining_after_cancel, price=new_price,
            )
            out(f"  보낼 정정 본문: {body}")
            if confirm("이 가격으로 정정을 실제로 보낸다. 계속할까?"):
                modify_ack = broker.modify(
                    broker_order_no=buy_ack.broker_order_no, entity_id=symbol,
                    quantity=remaining_after_cancel, price=new_price,
                )
                out(
                    f"  정정 결과: sent={modify_ack.sent} rsp_cd={modify_ack.rsp_cd} "
                    f"msg={modify_ack.rsp_msg}"
                )
                out(
                    "  → docs/live-order-checklist.md 에 어떤 가격(최우선호가/현재가)이 "
                    "받아들여졌는지 적어 둔다."
                )

    # -- 7. 매도로 되돌리기 --------------------------------------------------
    if filled <= 0:
        out("체결된 수량이 없어 되팔 것이 없다. 남은 미체결은 취소해서 정리해야 한다.")
        return 0

    if not confirm(f"7단계 — 체결된 {filled}주를 되판다. 계속할까?"):
        out(f"중단 — {filled}주가 계좌에 남아 있다. 직접 정리해야 한다.")
        return 1

    try:
        sell_quote = fetch_quote(client, symbol)
    except (LSAPIError, MissingCredentials) as error:
        out(f"매도 시세 조회 실패: {error}")
        return 1
    if sell_quote is None:
        out("매도 시세를 못 받았다 — 중단한다. 포지션이 남아 있다.")
        return 1

    sell_summary = build_order_summary(
        symbol=symbol, side=Side.SELL, quantity=filled,
        raw_reference_price=reference_price(sell_quote, Side.SELL),
        # 매도는 이미 산 것을 되파는 것 — 상한을 막을 이유가 없다.
        max_order_value=config.max_order_value * 2,
        tick=sell_quote.tick if is_us else None, currency=profile.currency,
    )
    out(sell_summary.render())
    if not confirm("위 내용으로 매도 주문을 실제로 낸다. 계속할까?"):
        out(f"중단 — {filled}주가 계좌에 남아 있다.")
        return 1

    sell_planned = make_planned_order(
        symbol=symbol, side=Side.SELL, quantity=filled,
        price=None if config.market_order else sell_summary.price, clock=clock,
        market=config.market,
    )
    try:
        sell_ack = broker.submit(sell_planned, as_of=clock.now())
    except (RejectedOrder, BrokerError) as error:
        out(f"매도 전송 실패 — {error}. {filled}주가 계좌에 남아 있을 수 있다.")
        return 1

    out(f"  매도 전송 결과: sent={sell_ack.sent} broker_order_no={sell_ack.broker_order_no}")
    if sell_ack.sent and sell_ack.broker_order_no:
        _poll_fill(
            store=store, client=client, clock=clock, order_id=sell_planned.order_id,
            symbol=symbol, side=Side.SELL, broker_order_no=sell_ack.broker_order_no,
            requested_quantity=filled, market=config.market, out=out,
        )

    out("8단계 — 결과. 위 로그와 LS 화면(잔고·체결내역)을 대조해 배선이 맞는지 확인한다.")
    return 0


def _poll_fill(
    *,
    store: Store,
    client: LSClient,
    clock: Clock,
    order_id: str,
    symbol: str,
    side: Side,
    broker_order_no: str,
    requested_quantity: int,
    out: Callable[[str], None],
    market: str = "KR",
    attempts: int = 5,
) -> int:
    """체결조회 TR 을 몇 번 불러 누적 체결수량을 본다. 어느 TR 인지는
    ``market`` 이 정한다(``broker/fills.py``). 새 체결은 ``trades`` 에
    적힌다 — 여기서 지어내지 않는다."""
    filled = 0
    for attempt in range(1, attempts + 1):
        pending = [
            PendingFill(
                order_id=order_id, entity_id=canonical_entity(market, symbol),
                side=side, market=market,
                broker_order_no=broker_order_no, requested_quantity=requested_quantity,
            )
        ]
        result = sync_fills(store, client, clock, as_of=clock.now(), pending=pending)
        outcome = result.outcomes[0]
        cumulative = outcome.cumulative_quantity
        if cumulative is not None:
            filled = int(cumulative)
        detail = f" · {outcome.detail}" if outcome.detail else ""
        out(
            f"  [{attempt}/{attempts}] {outcome.state.value} · "
            f"누적체결 {filled}/{requested_quantity}{detail}"
        )
        if filled >= requested_quantity:
            break
    return filled


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--market", choices=sorted(PROFILES), default="KR",
        help="검증할 시장 (기본 KR). US 는 LS_US_* 자격증명을 쓴다.",
    )
    parser.add_argument(
        "--symbol", required=True, help="국장 6자리 코드(005930) 또는 미장 심볼(WEN)"
    )
    parser.add_argument("--quantity", type=int, default=1, help="검증 수량 (기본 1주)")
    parser.add_argument(
        "--max-order-value", type=float, default=None,
        help=(
            "주문 금액 상한. **시장의 통화 단위다** — 기본값은 "
            f"KR {DEFAULT_MAX_ORDER_VALUE:,.0f}원 · US ${DEFAULT_MAX_ORDER_VALUE_US:,.2f}. "
            "원화 기본값을 달러로 읽으면 상한이 사실상 없는 것과 같다."
        ),
    )
    parser.add_argument(
        "--live", action="store_true",
        help="실전 게이트를 켠다 (execution.live_trading 확인 포함)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="--live 여부와 무관하게 주문·정정·취소는 전송하지 않고 본문만 출력",
    )
    parser.add_argument("--data-root", type=Path, help="창고 루트 (기본: 표준 위치)")
    parser.add_argument(
        "--market-order", action="store_true",
        help="지정가 대신 시장가로 낸다. **체결을 보는 것이 목적일 때만** — "
        "2026-08-17 미장 검증이 지정가 $5.41 로 나가 미체결로 끝났다",
    )
    parser.add_argument(
        "--assume-yes", action="store_true",
        help="사람 확인을 자동 승인한다(무인 실행). **위험 확인은 자동 거부다** — "
        "킬스위치·장외시간·거래제한이면 그 자리에서 멈춘다",
    )
    args = parser.parse_args(argv)

    load_env()
    profile = PROFILES[args.market]
    store = build_store(args.data_root)
    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    client = LSClient(
        credentials=credentials,
        live_trading=args.live,
        min_interval_sec=profile.min_interval_sec,
    )
    clock = LiveClock()

    config = RunConfig(
        symbol=args.symbol,
        quantity=args.quantity,
        max_order_value=(
            args.max_order_value
            if args.max_order_value is not None
            else profile.default_max_order_value
        ),
        live=args.live,
        dry_run=args.dry_run,
        market=args.market,
        market_order=args.market_order,
    )
    hooks = (
        {"confirm": auto_confirm, "prompt": auto_prompt} if args.assume_yes else {}
    )
    try:
        return run(config, store=store, client=client, clock=clock, **hooks)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
