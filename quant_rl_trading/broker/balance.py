"""증권사 계좌를 **실제로 조회한다** — 화면이 장부와 현실을 나란히 놓게.

## 왜 장부가 아니라 계좌를 따로 읽나

매매 결정(주문 가능 금액)은 **장부에서** 읽는다. 그래야 백테스트와 라이브가
같은 숫자를 본다(불변식 5) — `session/daily.py` 가 그 이유를 적어 뒀고,
그 규칙은 이 모듈이 생겨도 바뀌지 않는다.

**화면은 다른 문제다.** 장부가 현실과 어긋났을 때 그것을 아무도 못 보면
어긋난 채로 며칠이 간다. 2026-08-23 에 실제로 그랬다 — shadow 장부가 아직
안 들어온 입금 4.9억을 이미 세어 총 수익률이 4900% 로 찍혔는데, 화면에는
장부값만 있어서 대조할 것이 없었다.

그래서 이 모듈은 **결정에 쓰이지 않는다.** 화면과 점검에만 쓰고, 장부와
차이가 나면 그 차이 자체를 보여준다.

## 모의투자도 실제로 조회된다

LS 는 모의/실전을 endpoint 가 아니라 appkey 로 가른다. 모의 appkey 로도
``t0424`` 가 정상 응답한다(2026-08-15 실측). 어느 계좌를 보는지는
`broker/factory.py` 의 ``execution.account_mode`` 가 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 잔고 조회 TR 과 경로. `tools/verify_live_order.py` 와 같은 값이다.
TR_BALANCE = "t0424"
PATH_ACCNO = "/stock/accno"


@dataclass(frozen=True)
class AccountBalance:
    """계좌가 말하는 현실. **장부가 아니다.**

    장부의 결제완료 현금과 다를 수 있고, 그 차이가 곧 미결제 대금이다.
    """

    #: 예수금(현금). t0424 ``sunamt1`` — **당일 예수금**이다(LS 앱 "예수금"과 같다).
    #: 미결제 매도대금은 안 들어 있다. 결제 후 현금은 ``net_asset − equity`` (2026-08-28 실측:
    #: sunamt1 422,792,281 · 앱 D+2 예수금 442,188,462 = sunamt 508,822,722 − tappamt 66,634,225).
    cash: float
    #: 주식 평가금액. t0424 ``tappamt``.
    equity: float
    #: 추정순자산(총자산). t0424 ``sunamt`` — **예수금 + 평가금액이 아니라
    #: 계좌가 직접 주는 값이다.** 둘을 더해 만들면 이중계산이 난다.
    net_asset: float
    #: 매입금액(원가). t0424 ``mamt``.
    cost: float
    #: 평가손익. t0424 ``tdtsunik`` = 평가금액 − 매입금액.
    unrealized: float
    #: 보유 종목 수.
    n_positions: int
    #: 계좌가 안 열렸거나 조회가 막힌 경우 True — 값은 전부 0 이다.
    unavailable: bool = False
    #: 당일 실현손익. t0424 ``dtsunik`` — 그날 판 것 전부(장부 밖 청산 포함).
    realized_today: float = 0.0
    #: 종목별 보유 — 장부와 종목·수량을 1:1 대조하는 자리 (대시보드 계좌 패널).
    holdings: tuple[dict[str, object], ...] = ()


def _num(block: dict[str, Any], key: str) -> float:
    try:
        return float(str(block.get(key, "0") or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def fetch(client: Any) -> AccountBalance:
    """``t0424`` 로 계좌를 조회한다. 못 읽으면 ``unavailable=True``.

    **예외를 밖으로 내보내지 않는다.** 화면이 이 값 하나 때문에 통째로
    죽으면 안 된다 — 못 읽었다는 사실을 값으로 돌려주고, 부르는 쪽이
    "조회 불가" 로 그린다.
    """
    body = {
        "t0424InBlock": {
            "prcgb": "1", "chegb": "2", "dangb": "0",
            "charge": "1", "cts_expcode": "",
        }
    }
    try:
        data = client.request_tr(PATH_ACCNO, TR_BALANCE, body)
    except Exception:  # 네트워크·토큰·권한 — 화면을 죽이지 않는다
        return _blank()

    # paper 모드(전송 차단)면 요청 자체가 안 나간다 — 그것도 "못 읽음" 이다.
    if data.get("paper"):
        return _blank()

    summary = data.get("t0424OutBlock") or {}
    positions = data.get("t0424OutBlock1") or []
    # **필드 이름을 짐작하지 않는다.** 2026-08-23 실측으로 산수가 맞는 것을
    # 확인했다: sunamt1(422,792,281) + tappamt(77,936,422) = sunamt(500,728,703),
    # tappamt - mamt(77,425,226) = tdtsunik(511,196). 처음에 sunamt 를 예수금으로
    # 읽고 평가금액을 더했다가 총자산을 578,665,125 로 **이중계산**할 뻔했다.
    return AccountBalance(
        cash=_num(summary, "sunamt1"),
        equity=_num(summary, "tappamt"),
        net_asset=_num(summary, "sunamt"),
        cost=_num(summary, "mamt"),
        unrealized=_num(summary, "tdtsunik"),
        n_positions=len(positions),
        realized_today=_num(summary, "dtsunik"),
        holdings=tuple(
            {
                "entity_id": f"KR:{str(row.get('expcode', '')).strip()}",
                "name": str(row.get("hname", "")).strip(),
                "quantity": _num(row, "janqty"),
                "avg_price": _num(row, "pamt"),
                "value": _num(row, "appamt"),
                "unrealized": _num(row, "dtsunik"),
            }
            for row in positions
            if str(row.get("expcode", "")).strip()
        ),
    )


def _blank() -> AccountBalance:
    return AccountBalance(0.0, 0.0, 0.0, 0.0, 0.0, 0, unavailable=True)


def reconcile(balance: AccountBalance, *, ledger_nav: float) -> float | None:
    """계좌와 장부의 차이(계좌 − 장부). 조회 불가면 None.

    **이 값이 0 이 아니면 둘 중 하나가 틀렸다.** 어느 쪽인지는 이 함수가
    말하지 않는다 — 그건 사람이 볼 일이고, 화면은 차이를 숨기지만 않으면 된다.
    """
    if balance.unavailable:
        return None
    return balance.net_asset - ledger_nav
