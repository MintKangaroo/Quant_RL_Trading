"""실주문 사전점검 — **주문 경로가 이 파일에 없다.**

    uv run python tools/preflight_live_order.py --market KR --symbol 027360
    uv run python tools/preflight_live_order.py --market US --symbol WEN

개장 시각에 크론이 돌려서 "지금 주문을 내면 어떻게 되는가" 를 미리 찍어 둔다.
사람은 그 로그를 보고 ``verify_live_order.py`` 를 한 번 실행하면 된다.

## 왜 ``verify_live_order.py --dry-run`` 을 크론에 걸지 않는가

그 도구는 잔고를 보려면 ``--live`` 가 필요하고, ``--live`` 는
``execution.live_trading`` 설정을 켜라고 요구한다. **무인 실행을 위해 그
설정을 켜 두면 그 시각 이후 모든 경로에서 실주문이 열린다.** 사전점검 하나
때문에 게이트를 여는 것은 앞뒤가 바뀐 것이다.

그래서 이 파일은 **조회 TR 만** 부른다. 주문·정정·취소 TR 을 부르는 코드가
아예 없어서, 실수로 나갈 경로가 존재하지 않는다. 확인하는 것은 넷이다:

1. **토큰이 발급되는가** — appkey 가 살아 있는지
2. **예수금이 얼마인가** — 신용·미수 금지라 이 금액이 상한이다
3. **시세와 호가단위** — 미장은 LS 가 종목별 ``untprc`` 를 준다
4. **주문 본문이 어떻게 만들어지는가** — 만들기만 하고 보내지 않는다

## 이 결과를 그대로 믿지 마라

**정규장이 아니면 시세가 낡았다.** 개장 직후에 돌려야 의미가 있고, 그때도
호가는 미장에서 안 온다(``verify_live_order`` 모듈 docstring §미장). 이 파일은
"주문이 나갈 수 있는 상태인가" 를 보는 것이지 체결을 예측하는 것이 아니다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.ls_order import _order_body  # noqa: E402
from quant_rl_trading.broker.ls_order_us import us_order_body  # noqa: E402
from quant_rl_trading.collectors.errors import LSAPIError, MissingCredentials  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, is_regular_session  # noqa: E402
from quant_rl_trading.executor.ticks import round_to_tick  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.verify_live_order import (  # noqa: E402
    fetch_balance,
    fetch_balance_us,
    fetch_quote,
    fetch_quote_us,
    reference_price,
    round_to_tick_us,
)


def preflight(market: str, symbol: str, quantity: int, out) -> int:  # type: ignore[no-untyped-def]
    load_env()
    us = market == "US"
    creds = LSCredentials.from_env(prefix="LS_US_" if us else "LS_")

    out(f"사전점검 — {market} {symbol} × {quantity}주")
    out(f"  계좌 선언 kind={creds.declared_kind or '(미선언)'} · 지문={creds.fingerprint}")
    if creds.declared_kind != "real":
        out("  ⚠️ 실계좌 선언이 없다. `.env` 의 LS_ACCOUNT_KIND 를 확인하라.")

    market_enum = Market.US if us else Market.KR
    now = LiveClock().now()
    if not is_regular_session(market_enum, now):
        out(f"  ⚠️ 지금은 {market} 정규장이 아니다 — 아래 시세는 낡았을 수 있다.")

    # **조회 전용 클라이언트.** 주문 TR 을 부르는 코드가 이 파일에 없다.
    client = LSClient(credentials=creds, live_trading=True)
    try:
        client.ensure_token()
        out("  토큰 발급 OK")

        balance = fetch_balance_us(client) if us else fetch_balance(client)
        unit = "USD" if us else "KRW"
        out(f"  주문가능금액 {balance.net_asset:,.2f} {unit} · 보유 {len(balance.positions)}종목")

        quote = fetch_quote_us(client, symbol) if us else fetch_quote(client, symbol)
        if quote is None:
            out("  시세를 못 받았다 — 종목코드·시장코드를 확인하라.")
            return 1
        out(f"  현재가 {quote.price:,.4f} · 호가 {quote.bid:,.4f}/{quote.ask:,.4f}")

        raw = reference_price(quote, Side.BUY)
        price = (
            round_to_tick_us(raw, side=Side.BUY, tick=quote.tick)
            if us
            else float(round_to_tick(raw, side=Side.BUY))
        )
        amount = price * quantity
        out(
            f"  매수 기준가 {raw:,.4f} → 호가단위 반올림 {price:,.4f} · "
            f"예상금액 {amount:,.2f} {unit}"
        )

        # **신용·미수 금지.** 예수금을 넘으면 그 자체가 결론이다.
        if amount > balance.net_asset:
            out(
                f"  ✗ 예수금 부족 — {amount:,.2f} > {balance.net_asset:,.2f} {unit}. "
                "이대로면 미수다."
            )
            return 1
        out(f"  ✓ 현금 범위 안 (여유 {balance.net_asset - amount:,.2f} {unit})")

        body = (
            us_order_body(
                symbol=symbol, side=Side.BUY, quantity=quantity,
                limit_price=price, market_code=quote.market_code or "82",
            )
            if us
            else _order_body(symbol=symbol, side=Side.BUY, quantity=quantity, limit_price=price)
        )
        out("  보낼 본문(전송하지 않는다):")
        for key, value in next(iter(body.values())).items():
            out(f"    {key:15s} {value!r}")
        out("→ 준비됨. 실주문은 verify_live_order.py 로 사람이 확인하며 낸다.")
        return 0
    except (LSAPIError, MissingCredentials) as error:
        out(f"  ✗ {type(error).__name__}: {error}")
        return 1
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="실주문 사전점검 (조회 전용)")
    parser.add_argument("--market", choices=("KR", "US"), default="KR")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--quantity", type=int, default=1)
    args = parser.parse_args()
    return preflight(args.market, args.symbol, args.quantity, print)


if __name__ == "__main__":
    raise SystemExit(main())
