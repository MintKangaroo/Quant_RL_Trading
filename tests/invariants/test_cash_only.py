"""**신용·미수 금지 — 현금으로만 사고판다.**

이 시스템은 빌린 돈으로 매매하지 않는다. 막아야 할 것이 **두 층**이고 서로
다른 것을 막는다 — 하나만 있으면 뚫린다.

1. **신용거래** — 주문 본문의 ``MgntrnCode`` 가 정한다. ``"000"`` 이 보통매매다.
   다른 값이면 증권사가 돈을 빌려준다.
2. **미수** — 신용이 아니어도 **예수금을 넘겨 사면** 미수금이 잡힌다. D+2 에
   못 갚으면 **반대매매**가 나간다 — 우리가 고른 시점·가격이 아니라 증권사가
   정한 대로 강제로 팔린다. 그건 전략이 낸 손실이 아니다.

2번을 막는 것이 ``accounting.ledger.available_cash`` 와 ``executor.sizing`` 의
현금 제약이다. 그게 없던 동안 백테스트가 **레버리지 3.2배**로 돌았고 현금이
-1.96억까지 갔다(2026-08-15 규명). 여기서는 1번을 못 박는다.
"""

from __future__ import annotations

from quant_rl_trading.broker.ls_order import MGNTRN_NONE, _order_body
from quant_rl_trading.broker.ls_order_us import ORDER_BODY_KEYS, us_order_body
from quant_rl_trading.schemas.order import Side

#: 보통매매(신용 아님). 이 값이 바뀌면 빌린 돈으로 사게 된다.
CASH_ONLY = "000"

#: 미장에서 신용을 뜻할 수 있는 필드 이름. **하나도 없어야 한다.**
#: 국장 이름(`MgntrnCode`/`LoanDt`)과 그 미장 변형을 같이 본다 — 나중에
#: 누가 "국장에 있으니 미장에도 넣자" 로 옮겨 붙이는 것을 막는다.
CREDIT_FIELDS = (
    "MgntrnCode", "LoanDt", "OrdCndiTpCode",
    "MgntrnTpCode", "LoanDtlClssCode", "CrdtTpCode", "MgnRatCode",
)


def _bodies():  # type: ignore[no-untyped-def]
    """지정가·시장가 × 매수·매도 네 조합. 어느 경로로도 신용이 안 붙어야 한다."""
    for side in (Side.BUY, Side.SELL):
        for limit in (55_300.0, None):  # None = 시장가(청산·킬스위치)
            yield side, limit, _order_body(
                symbol="005930", side=side, quantity=1, limit_price=limit
            )


def test_모든_주문이_보통매매다() -> None:
    for side, limit, body in _bodies():
        block = body["CSPAT00601InBlock1"]
        assert block["MgntrnCode"] == CASH_ONLY, f"{side} limit={limit} 에 신용이 붙었다"


def test_대출일자를_비워_둔다() -> None:
    """``LoanDt`` 가 채워지면 신용/대주 거래로 읽힌다."""
    for _side, _limit, body in _bodies():
        assert body["CSPAT00601InBlock1"]["LoanDt"] == ""


def test_상수_자체가_보통매매다() -> None:
    """호출부를 다 고쳐도 상수를 바꾸면 뚫린다 — 상수도 못 박는다."""
    assert MGNTRN_NONE == CASH_ONLY


# -----------------------------------------------------------------------------
# 미장 — **없는 것을 없다고 못 박는다**
#
# `COSAT00301InBlock1` 에는 국장의 `MgntrnCode`/`LoanDt` 에 해당하는 필드가
# 아예 없다(LS 포털 공식 예제 8필드). 그래서 미장에서 신용을 막는 방법은
# "값을 000 으로 둔다" 가 아니라 **"그 필드를 만들지 않는다"** 다.
#
# 값이 아니라 부재가 규약이면 테스트도 부재를 봐야 한다 — 필드가 하나 늘어나는
# 순간 규약이 소리 없이 깨지므로 키 집합을 통째로 고정한다.
# -----------------------------------------------------------------------------


def _us_bodies():  # type: ignore[no-untyped-def]
    """지정가·시장가 × 매수·매도 × 뉴욕·나스닥."""
    for side in (Side.BUY, Side.SELL):
        for limit in (8.65, None):  # None = 시장가
            for market_code in ("82", "81"):
                yield side, limit, market_code, us_order_body(
                    symbol="WEN", side=side, quantity=1,
                    limit_price=limit, market_code=market_code,
                )


def test_미장_주문에는_신용_필드가_아예_없다() -> None:
    for side, limit, market_code, body in _us_bodies():
        block = body["COSAT00301InBlock1"]
        for name in CREDIT_FIELDS:
            assert name not in block, (
                f"{side} limit={limit} mkt={market_code} 에 신용 필드 {name} 가 생겼다 — "
                "미장 규약은 '값이 000' 이 아니라 '필드가 없다' 이다"
            )


def test_미장_주문본문의_키집합을_통째로_고정한다() -> None:
    """필드가 늘어나면(줄어도) 여기서 걸린다. 늘어난 필드가 신용인지 아닌지
    사람이 판단해야 하므로, 조용히 통과시키지 않는다."""
    for _side, _limit, _market_code, body in _us_bodies():
        assert set(body["COSAT00301InBlock1"]) == ORDER_BODY_KEYS


def test_미장_상수_자체에_신용이_없다() -> None:
    """호출부를 다 고쳐도 상수 집합을 바꾸면 뚫린다."""
    for name in CREDIT_FIELDS:
        assert name not in ORDER_BODY_KEYS


def test_프로덕션_경로는_언제나_주문가능금액을_넘긴다() -> None:
    """``size_orders(cash=...)`` — 안 넘기면 미수 제약이 통째로 사라진다.

    ``cash`` 의 기본값은 ``None``(제약 없음)이다. 순수 함수 테스트가 한 종목의
    라운딩만 보려고 할 때를 위해 남겨 둔 것이지 운용 경로를 위한 것이 아니다
    (``executor/sizing.py`` 독스트링). 그런데 **기본값이 안전하지 않은 쪽**이라,
    새 호출부가 생기면 아무 소리 없이 레버리지가 열린다 — 실제로 그 길로
    2.83배까지 갔다.

    그래서 호출부를 구조로 검사한다. 테스트는 이 규칙 밖이다.
    """
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for base in ("quant_rl_trading", "tools"):
        for path in (repo / base).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name != "size_orders":
                    continue
                if not any(kw.arg == "cash" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(repo)}:{node.lineno}")

    assert not offenders, (
        "주문가능금액 없이 size_orders 를 부른다 — 신용·미수가 열린다: " + ", ".join(offenders)
    )
