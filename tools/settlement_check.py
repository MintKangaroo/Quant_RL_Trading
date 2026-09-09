"""정산금액 대조 — LS 거래내역(CDPCQ04700) 합계와 장부 trades 를 **정산일(D+2)** 기준으로 맞춘다.

    .venv/bin/python tools/settlement_check.py --market KR [--sandbox data/_paper] [--settle 2026-09-07]

실측(2026-09-07): CDPCQ04700 의 조회일은 **체결일이 아니라 정산일**이다. 9/7 합계가 장부의 9/3 체결과
짝이 맞았다(매도 5,490주 94,355,720 대 장부 94,474,365). 모의계좌는 건별(OutBlock3)이 비고 합계(OutBlock5)
만 온다 — 그래서 TrdNo 대조가 아니라 **합계 대조**다. 차이가 허용치(`execution.settlement_tolerance`,
config)를 넘으면 rc=1 로 밖에 알린다(조용한 실패 금지). 이 대조가 잡은 것: 정정 거래가 평균매입단가로
적혀 매도대금이 +0.12% 과대(→ reconcile_snapshot 수정).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.ls_client import PATH_ACCNO, LSAPIError  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, previous_trading_day  # noqa: E402
from quant_rl_trading.dashboard.services.account import _client  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402

TR = "CDPCQ04700"
NO_ROWS = "00707"  # 모의투자 "조회할 내역이 없습니다" — 건별만 없고 합계(OutBlock5)는 온다
SETTLE_LAG_SESSIONS = 2
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Totals:
    sell_qty: float
    sell_amt: float
    sell_fee: float
    sell_tax: float
    buy_qty: float
    buy_amt: float
    buy_fee: float


def trade_session_for(settle: date, market: Market) -> date:
    """정산일 → 그 정산의 체결 세션(두 거래일 전)."""
    day = settle
    for _ in range(SETTLE_LAG_SESSIONS):
        day = previous_trading_day(market, day)
    return day


def ledger_totals(trades: pd.DataFrame, *, session: date, market: str) -> Totals:
    frame = trades[trades["entity_id"].astype(str).str.startswith(f"{market}:")].copy()
    frame["day"] = pd.to_datetime(frame["valid_from"], utc=True).dt.tz_convert(KST).dt.date
    frame = frame[frame["day"] == session]
    frame["amt"] = frame["price"].astype(float) * frame["quantity"].astype(float)
    sell = frame[frame["side"] == "sell"]
    buy = frame[frame["side"] == "buy"]
    return Totals(
        sell_qty=float(sell["quantity"].sum()), sell_amt=float(sell["amt"].sum()),
        sell_fee=float(sell["fee"].sum()), sell_tax=float(sell["tax"].sum()),
        buy_qty=float(buy["quantity"].sum()), buy_amt=float(buy["amt"].sum()), buy_fee=float(buy["fee"].sum()),
    )


def broker_totals(client, settle: date) -> Totals:
    body = {f"{TR}InBlock1": {
        "RecCnt": 1, "QrySrtDt": settle.strftime("%Y%m%d"), "QryEndDt": settle.strftime("%Y%m%d"),
        "SrtNo": 0, "PdptnCode": "01", "IsuLgclssCode": "00", "IsuNo": "",
    }}
    try:
        data = client.request_tr(PATH_ACCNO, TR, body)
    except LSAPIError as error:
        if getattr(error, "rsp_cd", None) != NO_ROWS or not getattr(error, "payload", None):
            raise
        data = error.payload
    block = data.get(f"{TR}OutBlock5") or {}
    num = lambda key: float(block.get(key) or 0)  # noqa: E731
    return Totals(
        sell_qty=num("SellQty"), sell_amt=num("SellAmt"), sell_fee=num("SellCmsn"), sell_tax=num("EvrTax"),
        buy_qty=num("BuyQty"), buy_amt=num("BuyAmt"), buy_fee=num("BuyCmsn"),
    )


def compare(ledger: Totals, broker: Totals, *, tolerance: float) -> tuple[list[str], bool]:
    """줄 단위 보고와 통과 여부. 통과선은 **금액 상대 차이** — 수량은 0 이어야 한다."""
    lines = []
    ok = True
    for label, a, b in (
        ("매도 수량", ledger.sell_qty, broker.sell_qty), ("매도 금액", ledger.sell_amt, broker.sell_amt),
        ("매도 수수료", ledger.sell_fee, broker.sell_fee), ("매도 세금", ledger.sell_tax, broker.sell_tax),
        ("매수 수량", ledger.buy_qty, broker.buy_qty), ("매수 금액", ledger.buy_amt, broker.buy_amt),
        ("매수 수수료", ledger.buy_fee, broker.buy_fee),
    ):
        diff = a - b
        rel = (diff / b) if b else (0.0 if a == 0 else float("inf"))
        flag = ""
        if "수량" in label and diff != 0:
            flag = " ×"; ok = False
        elif "금액" in label and abs(rel) > tolerance:
            flag = " ×"; ok = False
        lines.append(f"  {label:8s} 장부 {a:>15,.0f} 브로커 {b:>15,.0f} 차이 {diff:>+12,.0f} ({rel:+.4%}){flag}")
    return lines, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR"])
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--settle", default=None, help="정산일 (기본: 오늘)")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    market = Market(args.market)
    settle = date.fromisoformat(args.settle) if args.settle else now.astimezone(KST).date()
    session = trade_session_for(settle, market)
    tolerance = float(store.config("execution.settlement_tolerance", as_of=now))
    trades = store.get("trades", as_of=now, lookback=12)
    ledger = ledger_totals(trades, session=session, market=args.market) if not trades.empty else Totals(0, 0, 0, 0, 0, 0, 0)
    broker = broker_totals(_client(store, as_of=now, market=args.market), settle)
    print(f"정산 대조 — 정산일 {settle} ↔ 체결 세션 {session} · 허용 {tolerance:.2%}")
    lines, ok = compare(ledger, broker, tolerance=tolerance)
    print("\n".join(lines))
    print("정산 대조 통과" if ok else "정산 대조 **불일치** — 체결가 기록 또는 비용 규칙을 의심할 것")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
