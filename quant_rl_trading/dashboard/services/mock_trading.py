"""트레이딩 화면 목업 — **레이아웃을 먼저 세우기 위한 것이다.**

## 왜 목업이 따로 있는가

트레이딩 탭이 그릴 것 중 대부분은 아직 데이터가 없다. M2 시점에는
``quant_rl_trading/accounting/`` 모듈 자체가 없고, ``positions``·``orders``·``fills``
테이블도 없다. RL(Allocator)은 M4 다.

그래도 레이아웃을 먼저 잡는 것은 합리적이다 — M3 에서 데이터가 생겼을 때
붙일 자리가 정해져 있어야 하고, 그 전에 배치가 맞는지 눈으로 봐야 한다.

## 다만 오인되면 안 된다

자동매매 화면에서 가짜 손익이 진짜처럼 보이면 사고가 난다. 그래서 셋을
강제한다.

1. **엔드포인트가 다르다.** ``/api/trading/*`` 가 아니라 ``/api/mock/*`` 다.
   실제 트레이딩 API 가 생겨도 이 경로와 겹치지 않는다
2. **모든 응답에 ``mock: true`` 가 붙는다.** 화면이 이걸 보고 배너를 띄운다
3. **숫자가 눈에 띄게 비현실적이다.** 계좌는 ``MOCK-0000`` 이고 종목명 앞에
   ``[목업]`` 이 붙는다. 실수로 스크린샷이 돌아다녀도 구분된다

## 값은 고정이다 — 난수를 쓰지 않는다

새로고침마다 숫자가 흔들리면 "살아 있는 화면" 처럼 보여서 오인 위험이 커지고,
레이아웃 검토에도 방해가 된다. 시드 고정도 아니고 **그냥 상수**다.

M3 에서 이 모듈은 통째로 지운다. 지울 것을 전제로 짜여 있다 —
서비스 계층에만 있고 어떤 실제 코드도 여기에 의존하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

#: 목업이 그리는 자본 규모. 실제 자본 단계(reward-and-risk.md)와 무관하다.
NAV = 12_540_000.0

BANNER = (
    "목업 데이터입니다 — 실제 잔고·손익이 아닙니다. "
    "accounting·positions·orders 는 M3 에서 붙습니다."
)


def _series(start: float, steps: list[float]) -> list[float]:
    """누적 시계열. 상수 배열이라 새로고침해도 같은 그림이 나온다."""
    out, value = [], start
    for step in steps:
        value *= 1.0 + step
        out.append(round(value, 1))
    return out


#: 30세션치 포트폴리오·벤치마크. 손으로 고른 값이다.
#: **마지막 두 세션을 하락으로 둔다** — 최고점에서 끝나면 MDD 게이지가
#: 0.00 을 가리켜 시그니처 위젯이 아무것도 보여주지 못한다. 레이아웃 검토가
#: 목적이므로 게이지가 실제로 움직이는 상태를 보여야 한다.
_PORT_STEPS = [
    0.004, -0.002, 0.006, 0.003, -0.005, 0.008, 0.002, -0.001, 0.005, 0.004,
    -0.007, 0.003, 0.006, -0.002, 0.004, 0.007, -0.003, 0.002, 0.005, -0.004,
    0.006, 0.003, -0.002, 0.008, 0.001, -0.005, 0.004, 0.006, -0.011, -0.006,
]
_BENCH_STEPS = [
    0.003, -0.004, 0.004, 0.001, -0.006, 0.005, 0.001, -0.003, 0.003, 0.002,
    -0.009, 0.002, 0.004, -0.004, 0.002, 0.005, -0.005, 0.001, 0.003, -0.006,
    0.004, 0.001, -0.004, 0.006, -0.001, -0.007, 0.002, 0.004, 0.001, 0.002,
]


def curves(as_of: datetime) -> dict[str, Any]:
    """NAV · 벤치마크 · 언더워터. 언더워터는 NAV 에서 계산한다 — 두 벌로 두면
    화면의 두 그림이 서로 다른 이야기를 하게 된다."""
    port = _series(NAV * 0.92, _PORT_STEPS)
    bench = _series(NAV * 0.92, _BENCH_STEPS)
    days = [(as_of - timedelta(days=len(port) - 1 - i)).date().isoformat()
            for i in range(len(port))]

    peak, underwater, bench_peak, bench_uw = port[0], [], bench[0], []
    for value in port:
        peak = max(peak, value)
        underwater.append(round((value / peak - 1.0) * 100, 3))
    for value in bench:
        bench_peak = max(bench_peak, value)
        bench_uw.append(round((value / bench_peak - 1.0) * 100, 3))

    return {
        "sessions": days,
        "nav": port,
        "benchmark": bench,
        "underwater": underwater,
        "benchmark_underwater": bench_uw,
        "excess_pct": round((port[-1] / port[0] - bench[-1] / bench[0]) * 100, 2),
    }


def summary(as_of: datetime) -> dict[str, Any]:
    """상단 KPI. 실제로는 quant_rl_trading/accounting 이 낼 값이다."""
    data = curves(as_of)
    drawdown = data["underwater"][-1]
    return {
        "account": "MOCK-0000",
        "nav": NAV,
        "cash": 3_180_000.0,
        "day_pnl": 184_200.0,
        "day_pnl_pct": 1.49,
        "total_return_pct": round((data["nav"][-1] / data["nav"][0] - 1.0) * 100, 2),
        "excess_pct": data["excess_pct"],
        "drawdown_pct": drawdown,
        "max_drawdown_pct": round(min(data["underwater"]), 2),
        # 밴드는 reward-and-risk.md 의 12/22/30 이다. 여기서 지어내지 않는다.
        "mdd_bands": [12.0, 22.0, 30.0],
        "exposure_pct": 43.0,
        "positions": 3,
        "orders_today": 5,
        # M2 에서 실제로 있는 값과 같은 이름을 쓴다 — M3 에서 갈아 끼울 때
        # 화면을 안 고치기 위함이다.
        "action_rate_pct": None,
        "ai_state": "PAUSED",
        "ai_note": "Allocator 는 M4 다. 지금은 규칙도 RL 도 돌지 않는다",
    }


def positions(as_of: datetime) -> list[dict[str, Any]]:
    """보유 포지션. 목업임이 드러나도록 종목명에 표식을 단다."""
    rows = [
        ("KR:005930", "[목업] 삼성전자", 20, 84_200, 84_800, 4, "chart · fundamental"),
        ("KR:000660", "[목업] SK하이닉스", 5, 241_000, 243_500, 7, "event"),
        ("KR:035420", "[목업] NAVER", 12, 174_300, 172_900, 2, "fundamental"),
    ]
    out = []
    for entity, name, qty, avg, last, held, contributors in rows:
        pnl_pct = round((last / avg - 1.0) * 100, 2)
        out.append(
            {
                "entity_id": entity,
                "name": name,
                "quantity": qty,
                "avg_price": avg,
                "last_price": last,
                "value": qty * last,
                "weight_target_pct": round(qty * last / NAV * 100, 2),
                "weight_actual_pct": round(qty * last / NAV * 100, 2),
                "pnl_pct": pnl_pct,
                "held_days": held,
                "contributors": contributors,
            }
        )
    return out


def orders(as_of: datetime) -> list[dict[str, Any]]:
    """주문·체결. 상태 흐름은 실제 설계와 같은 이름을 쓴다."""
    base = as_of.replace(second=0, microsecond=0)
    return [
        {
            "at": (base - timedelta(minutes=m)).isoformat(),
            "entity_id": e, "name": n, "side": s, "quantity": q,
            "price": p, "status": st,
        }
        for m, e, n, s, q, p, st in [
            (6, "KR:005930", "[목업] 삼성전자", "BUY", 20, 84_200, "FILLED"),
            (4, "KR:000660", "[목업] SK하이닉스", "SELL", 5, 243_500, "FILLED"),
            (2, "KR:035420", "[목업] NAVER", "BUY", 12, 174_300, "PARTIAL"),
            (1, "KR:051910", "[목업] LG화학", "BUY", 3, 402_000, "REJECTED"),
        ]
    ]


def allocation(as_of: datetime) -> list[dict[str, Any]]:
    """자본 배분 — 파이차트용. 현금을 반드시 포함한다.

    현금을 빼면 합이 100%가 되어 **레버리지가 없다는 사실이 안 보인다.**
    """
    held = positions(as_of)
    out = [{"label": row["name"], "value": row["value"]} for row in held]
    out.append({"label": "현금", "value": NAV - sum(row["value"] for row in held)})
    return out


def risk(as_of: datetime) -> dict[str, Any]:
    """리스크 모니터. 한도는 M3 에서 store.config 로 옮긴다 (불변식 10)."""
    data = summary(as_of)
    return {
        "daily_loss_pct": -0.42,
        "max_daily_loss_pct": -2.0,
        "exposure_pct": data["exposure_pct"],
        "max_exposure_pct": 70.0,
        "open_positions": data["positions"],
        "max_positions": 5,
        "api_errors": 0,
        "order_rejects": 1,
        "kill_switch": "READY",
    }


def payload(as_of: datetime) -> dict[str, Any]:
    return {
        "mock": True,
        "banner": BANNER,
        "summary": summary(as_of),
        "curves": curves(as_of),
        "positions": positions(as_of),
        "orders": orders(as_of),
        "allocation": allocation(as_of),
        "risk": risk(as_of),
    }
