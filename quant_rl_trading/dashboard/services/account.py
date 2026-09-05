"""증권사 계좌 실조회 — **장부와 나란히 놓는다.**

## 왜 KPI 에 안 섞고 따로 두나

화면의 KPI(총자산·수익률)는 **장부**에서 온다. 그것이 맞다 — 백테스트와
라이브가 같은 숫자를 봐야 하고(불변식 5), 수익률은 입금을 걸러낸 TWR 이라
계좌 잔고에서 바로 나오지 않는다.

**그렇다고 계좌를 안 보면 장부가 틀려도 모른다.** 2026-08-23 에 실제로
그랬다 — shadow 장부가 아직 안 들어온 입금 4.9억을 이미 세어 총 수익률이
4900% 로 찍혔는데, 화면에 대조할 것이 없어 아무도 못 알아챘다.

그래서 이 서비스는 **계좌가 말하는 값을 그대로** 내고, 장부와의 차이를
같이 낸다. 섞지 않는다 — 섞으면 어느 쪽이 틀렸는지 말할 수 없게 된다.

## 실패해도 화면을 죽이지 않는다

네트워크·토큰·권한 어디서 막혀도 ``available=False`` 로 돌려준다. 계좌
조회 하나 때문에 트레이딩 탭 전체가 500 이 되면 안 된다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quant_rl_trading.broker import balance as balance_module
from quant_rl_trading.store import Store

#: 어느 계좌를 보는지 가르는 설정. `broker/factory.py` 와 같은 키다.
ACCOUNT_MODE_KEY = "execution.account_mode"


def _client(store: Store, *, as_of: datetime, market: str):
    """조회 전용 클라이언트. **주문은 이 경로로 안 나간다** — TR 이 t0424 뿐이다."""
    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
    from tools.verify_live_order import resolve_profile

    profile = resolve_profile(store, market=market, as_of=as_of)
    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    # **live_trading=True 는 "조회를 실제로 보낸다" 는 뜻이다.** 주문 TR 은
    # 이 모듈이 아예 부르지 않으므로 이 값으로 주문이 나갈 길은 없다.
    return LSClient(
        credentials=credentials,
        live_trading=True,
        min_interval_sec=profile.min_interval_sec,
    )


def broker_account(
    store: Store, *, as_of: datetime, market: str = "KR", ledger_nav: float | None = None
) -> dict[str, Any]:
    """계좌가 말하는 자산·예수금·평가금액. 장부와의 차이도 같이 낸다.

    ``ledger_nav`` 를 주면 ``gap`` 에 (계좌 − 장부)를 넣는다. **0 이 아니면
    둘 중 하나가 틀렸다** — 어느 쪽인지는 이 함수가 판정하지 않는다.
    """
    try:
        mode = str(store.config(ACCOUNT_MODE_KEY, as_of=as_of))
    except Exception:
        mode = "unknown"

    try:
        found = balance_module.fetch(_client(store, as_of=as_of, market=market))
    except Exception:
        # 자격증명이 없는 배포도 있다. 화면은 "조회 불가" 로 그린다.
        found = None

    if found is None or found.unavailable:
        return {
            "available": False, "mode": mode, "market": market,
            "net_asset": None, "cash": None, "equity": None,
            "cost": None, "unrealized": None, "positions": None, "gap": None,
        }

    gap = (
        balance_module.reconcile(found, ledger_nav=ledger_nav)
        if ledger_nav is not None
        else None
    )
    return {
        "available": True,
        "mode": mode,
        "market": market,
        "net_asset": found.net_asset,
        "cash": found.cash,
        "equity": found.equity,
        "cost": found.cost,
        "unrealized": found.unrealized,
        "positions": found.n_positions,
        "realized_today": found.realized_today,
        "holdings": list(found.holdings),
        "gap": gap,
    }
