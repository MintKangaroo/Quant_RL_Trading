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
from quant_rl_trading.schemas.order import Side

#: 보통매매(신용 아님). 이 값이 바뀌면 빌린 돈으로 사게 된다.
CASH_ONLY = "000"


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
