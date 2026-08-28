#!/usr/bin/env python
"""계좌를 비운다 — **보유 전량을 시장가로 판다.**

    uv run python tools/liquidate.py --market KR              # 드라이런
    uv run python tools/liquidate.py --market KR --live       # 실제 매도
    uv run python tools/liquidate.py --market US --live --assume-yes

## 보유의 정본은 브로커다

우리 장부(``trades`` 를 접은 ``Book``)가 아니라 **증권사 잔고**를 읽어서 판다.
장부가 실계좌보다 적으면 못 파는 주식이 남고, 많으면 없는 주식을 팔려 든다.
2026-08-18 실측으로 그 둘이 실제로 갈라져 있었다 — 체결이 스키마 위반으로
``trades`` 에 못 들어가는 동안 실계좌에는 주식이 쌓였다.

## 매도가능수량만 판다

``janqty``(보유)가 아니라 ``mdposqt``(매도가능)다. 미결제 매수분은 아직 못
판다 — 그걸 팔려 들면 거부되거나, 받아들여지면 미수가 된다.

## 시장가다

청산은 "얼마에 파느냐" 가 아니라 "확실히 나가느냐" 가 목적이다. 지정가로
걸면 안 붙은 채 장이 끝나고, 그건 청산이 아니다. ``executor/pipeline.py`` 도
청산·킬스위치에만 시장가를 쓴다 — 같은 규약이다.

## 판 뒤에는 장부에 적는다

``sync_fills`` 로 ``trades`` 에 남긴다. 안 적으면 다음 세션의 회계가 아직
들고 있다고 믿는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker import BrokerError, RejectedOrder  # noqa: E402
from quant_rl_trading.broker.fills import PendingFill, sync_fills  # noqa: E402
from quant_rl_trading.broker.ls_order import LSBroker  # noqa: E402
from quant_rl_trading.broker.ls_order_us import LSUSBroker  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.verify_live_order import (  # noqa: E402
    PROFILES,
    resolve_profile,
    canonical_entity,
    fetch_balance,
    fetch_holdings_us,
    fetch_quote_us,
    make_planned_order,
)


def _kr_lots(client: LSClient) -> list[tuple[str, int, str]]:
    """(종목코드, 매도가능수량, 이름). t0424 의 ``mdposqt`` 를 쓴다."""
    out = []
    for row in fetch_balance(client).positions:
        code = str(row.get("expcode", "")).strip()
        qty = int(row.get("mdposqt") or 0)
        if code and qty > 0:
            out.append((code, qty, str(row.get("hname", "")).strip()))
    return out


def _us_lots(client: LSClient) -> list[tuple[str, int, str]]:
    """(심볼, 매도가능수량, 이름). ``AstkSellAbleQty`` 를 쓴다."""
    out = []
    for row in fetch_holdings_us(client):
        code = str(row.get("ShtnIsuNo", "")).strip()
        qty = int(float(row.get("AstkSellAbleQty") or 0))
        if code and qty > 0:
            out.append((code, qty, str(row.get("JpnMktHanglIsuNm", "")).strip()))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=sorted(PROFILES))
    parser.add_argument("--live", action="store_true", help="실제로 판다")
    parser.add_argument("--assume-yes", action="store_true", help="무인 실행")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    # **모드에 맞는 키를 집는다.** 실전 프로파일을 그대로 쓰면 "모의를
    # 정리한다" 고 생각하며 실전 계좌를 청산한다 (`resolve_profile` 독스트링).
    profile = resolve_profile(store, market=args.market, as_of=clock.now())
    # **모의 계좌의 체결은 모의 장부(data/_paper)에 적는다** (backtest.md §9). 2026-08-28
    # 에 이 분기가 없어서 모의계좌 청산 4건이 실전 창고에 들어갔고, 정정본으로 지웠다.
    if args.data_root is None and profile.env_prefix == "LS_PAPER_":
        from quant_rl_trading.store import overlay
        from tools.run_backtest import JOURNAL

        layer = overlay.build(root=REPO_ROOT / "data" / "_paper", source=store.root, writable=JOURNAL)
        store = Store(root=layer.root)
        print(f"장부 → {store.root} (모의 계좌)")
    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    print(
        f"계좌 — 모드 키 {profile.env_prefix} · 지문 {credentials.fingerprint or '(없음)'} "
        f"· 선언 {credentials.kind or '(미선언)'}"
    )
    # **조회는 언제나 실전으로 연다.** ``live_trading=False`` 면 t0424·COSOQ00201
    # 이 아예 안 나가고(PAPER_ALLOWED_TR 밖), 빈 응답이 "보유 없음" 으로 보인다 —
    # "안 물어봤다" 와 "없다" 가 같은 문구가 된다. ``--live`` 는 **전송**을
    # 가르는 스위치이지 조회를 가르는 스위치가 아니다(preflight_live_order 와
    # 같은 규약). 실제 전송은 아래에서 ``--live`` 없이는 도달하지 않는다.
    client = LSClient(
        credentials=credentials,
        live_trading=True,
        min_interval_sec=profile.min_interval_sec,
    )
    is_us = args.market == "US"

    try:
        lots = _us_lots(client) if is_us else _kr_lots(client)
        if not lots:
            # 여기까지 왔으면 조회는 실제로 나갔다(위 live_trading=True).
            # 그러니 이건 "없다" 가 맞다.
            print(f"{args.market} 보유 없음 — 조회는 됐고 팔 것이 없다.")
            return 0

        print(f"{args.market} 청산 대상 {len(lots)}종목 (매도가능수량 기준)")
        for code, qty, name in lots:
            print(f"  {code} {name} · {qty}주")
        if not args.live:
            print("\n드라이런 — 전송하지 않는다. 실제로 팔려면 --live 를 준다.")
            return 0
        if not args.assume_yes:
            answer = input("위 전량을 시장가로 판다. 계속할까? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("중단.")
                return 1

        failures = 0
        for code, qty, name in lots:
            entity = canonical_entity(args.market, code)
            broker: LSBroker | LSUSBroker
            if is_us:
                quote = fetch_quote_us(client, code)
                if quote is None:
                    print(f"  {code}: 시세를 못 받아 주문시장을 모른다 — 건너뛴다.")
                    failures += 1
                    continue
                broker = LSUSBroker(
                    client=client, store=store, market_code=quote.market_code
                )
            else:
                broker = LSBroker(client=client, store=store)

            planned = make_planned_order(
                symbol=code, side=Side.SELL, quantity=qty,
                price=None, clock=clock, market=args.market,  # price=None = 시장가
            )
            try:
                ack = broker.submit(planned, as_of=clock.now())
            except (RejectedOrder, BrokerError) as error:
                print(f"  {code} {name}: 매도 전송 실패 — {error}")
                failures += 1
                continue
            print(
                f"  {code} {name} {qty}주 매도 전송 — sent={ack.sent} "
                f"주문번호={ack.broker_order_no} {ack.rsp_msg or ''}"
            )
            if not ack.broker_order_no:
                print("    주문번호를 못 받았다 — 체결 조회로 확인할 것.")
                failures += 1
                continue

            result = sync_fills(
                store, client, clock, as_of=clock.now(),
                pending=[PendingFill(
                    order_id=planned.order_id, entity_id=entity, side=Side.SELL,
                    market=args.market, broker_order_no=str(ack.broker_order_no),
                    requested_quantity=qty,
                )],
            )
            outcome = result.outcomes[0]
            print(
                f"    체결 {outcome.cumulative_quantity}/{qty} · {outcome.state.name}"
                f" · trades {result.rows_written}행"
            )
        return 1 if failures else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
