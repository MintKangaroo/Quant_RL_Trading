#!/usr/bin/env python
"""자본 증액 게이트 — 지금 자본을 늘려도 되는지 판정한다.

    uv run python tools/verify_capital_gate.py
    uv run python tools/verify_capital_gate.py --market US

``config.capital`` 이 다섯 값을 못 박아 두고도 **읽는 코드가 한 곳도
없었다**(2026-08-17 발견). 설정만 있고 호출부가 0건이면 그 게이트는 존재하지
않는 것과 같다 — 이 저장소에서 제일 자주 나는 결함이다.

    gate_min_trading_days: 60        무사고로 굴린 거래일
    gate_max_order_fail_rate: 0.01   주문 실패율 상한
    gate_max_missing_rate: 0.005     데이터 결측률 상한
    gate_slippage_tolerance: 0.30    슬리피지 실측 vs 모델 오차
    step_multiplier: 2.5             통과하면 자본을 이 배수로 올린다

## 왜 M3 가 아니라 여기서 슬리피지를 재나

M3 완료기준 5번도 슬리피지 ±30% 를 요구한다. 그런데 **표본을 쌓으려면
실전을 돌려야 하고, 실전을 돌리려면 M3 를 닫아야 한다** — 순환이다. 게다가
배선 검증용 1주 주문은 시장에 충격을 안 줘서, 그 체결로 잰 오차는 전략의
체결 비용을 말해주지 않는다.

그래서 M3 는 "배선이 도는가" 까지만 보고, **"모델이 현실과 맞는가" 는 실제
물량이 60거래일 쌓인 이 자리에서 판정한다**(사용자 결정 2026-08-17). 검증
강도는 안 떨어진다 — 오히려 표본이 30건 미만이면 통과를 안 주므로 더 세다.

## 판정은 셋이다

``verify_m1.py``·``verify_m3.py`` 와 같은 규약이다 — ``PASS`` · ``FAIL`` ·
``미측정``. 아직 못 재는 것을 FAIL 로 적으면 고장으로 읽히고 PASS 로 적으면
거짓이 된다. 종료코드도 같다: 0 전부 통과 · 1 FAIL 있음 · 2 미측정만.

## 함정 — 아무것도 안 하면 전부 통과한다

주문을 한 번도 안 냈으면 실패율은 0/0 이고, 체결이 없으면 슬리피지 오차도
없다. 그 상태를 통과로 적으면 **거래를 안 한 계좌가 증액 자격을 얻는다.**
그래서 각 검사는 **표본이 없으면 PASS 가 아니라 미측정**을 낸다.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.measure_slippage import measure as measure_slippage  # noqa: E402
from tools.measure_slippage import render as render_slippage  # noqa: E402

#: 실계좌 체결만 센다. 시뮬레이션 체결로 모델을 검증하면 정의상 오차 0 이다.
BROKER_SOURCE = "broker"

#: 슬리피지 판정에 필요한 최소 표본. ``measure_slippage`` 가 30건 미만이면
#: "통과로 읽지 마라" 경고를 찍는데, 여기서는 아예 미측정으로 둔다 —
#: 증액 판정은 경고를 읽고 넘어갈 수 있는 자리가 아니다.
MIN_SLIPPAGE_SAMPLES = 30

_STATUSES = ("PASS", "FAIL", "미측정")


@dataclass
class Check:
    name: str
    status: str
    evidence: list[str]

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"알 수 없는 상태: {self.status!r} (허용: {_STATUSES})")

    def render(self) -> str:
        return "\n".join([f"[{self.status}] {self.name}", *(f"       {e}" for e in self.evidence)])


def _config(store: Store, name: str, as_of: datetime, default: float) -> float:
    try:
        return float(store.config(name, as_of=as_of))
    except Exception:
        return default


# -----------------------------------------------------------------------------


def check_trading_days(store: Store, as_of: datetime, market: str) -> Check:
    """무사고로 굴린 거래일. **체결이 있었던 날만 센다.**

    NAV 스냅샷이 있다고 굴린 것이 아니다 — 주문이 하나도 안 나간 날을 세면
    "쉬는 계좌" 가 증액 자격을 쌓는다.
    """
    required = int(_config(store, "capital.gate_min_trading_days", as_of, 60.0))
    name = f"무사고 {required}거래일"
    trades = store.get("trades", as_of=as_of, lookback=required * 3 + 30)
    if trades.empty:
        return Check(name, "미측정", ["실계좌 체결이 0건 — 아직 셀 날이 없다"])

    live = trades[trades["source"] != "backtest"]
    if live.empty:
        return Check(
            name, "미측정",
            [f"체결 {len(trades)}건이 전부 시뮬레이션(source=backtest) — 실계좌 체결이 없다"],
        )
    days = sorted({pd.Timestamp(v).date() for v in live["valid_from"]})
    evidence = [
        f"실계좌 체결일 {len(days)}일 (필요 {required}일)",
        f"구간 {days[0]} ~ {days[-1]}",
    ]
    if len(days) < required:
        return Check(name, "미측정", [*evidence, f"→ {len(days)}/{required}"])
    return Check(name, "PASS", [*evidence, f"→ {len(days)}/{required} 충족"])


def check_order_fail_rate(store: Store, as_of: datetime, market: str) -> Check:
    """주문 실패율. ``orders.status`` 에서 센다."""
    cap = _config(store, "capital.gate_max_order_fail_rate", as_of, 0.01)
    name = f"주문 실패율 {cap:.1%} 이하"
    orders = store.get("orders", as_of=as_of, lookback=200)
    if orders.empty:
        return Check(name, "미측정", ["주문이 0건 — 실패율을 셀 표본이 없다"])
    live = orders[orders["status"] != "paper"]
    if live.empty:
        return Check(
            name, "미측정",
            [f"주문 {len(orders)}건이 전부 paper — 실계좌 주문이 없다"],
        )
    failed = live[live["status"].isin(["rejected", "failed", "error"])]
    rate = len(failed) / len(live)
    evidence = [f"실계좌 주문 {len(live)}건 · 실패 {len(failed)}건 = {rate:.2%} (상한 {cap:.1%})"]
    return Check(name, "PASS" if rate <= cap else "FAIL", evidence)


def check_missing_rate(store: Store, as_of: datetime, market: str) -> Check:
    """데이터 결측률. 최근 구간에서 세션당 시세가 빠진 비율이다."""
    cap = _config(store, "capital.gate_max_missing_rate", as_of, 0.005)
    required = int(_config(store, "capital.gate_min_trading_days", as_of, 60.0))
    name = f"데이터 결측률 {cap:.2%} 이하"
    from quant_rl_trading.store.prices import read_prices

    frame = read_prices(
        store, as_of=as_of, lookback=required * 2, market=market,
        columns=["entity_id", "valid_from", "close"],
    )
    if frame.empty:
        return Check(name, "미측정", ["시세가 0행 — 결측률을 셀 표본이 없다"])
    sessions = frame["valid_from"].nunique()
    entities = frame["entity_id"].nunique()
    expected = sessions * entities
    missing = max(0, expected - len(frame))
    rate = missing / expected if expected else 0.0
    evidence = [
        f"세션 {sessions}일 × 종목 {entities} = {expected:,} · 실제 {len(frame):,}",
        f"결측 {missing:,} = {rate:.3%} (상한 {cap:.2%})",
        "상장·폐지로 종목 수가 변하면 이 값이 부풀 수 있다 — 추세로 볼 것",
    ]
    return Check(name, "PASS" if rate <= cap else "FAIL", evidence)


def check_slippage(store: Store, as_of: datetime, market: str) -> Check:
    """슬리피지 실측 vs 모델. **M3 에서 이월된 기준이다.**

    ``tools/measure_slippage.py`` 를 그대로 불러 쓴다 — 다시 구현하지 않는다.
    다만 표본이 ``MIN_SLIPPAGE_SAMPLES`` 미만이면 그 도구의 판정과 무관하게
    미측정으로 둔다. 증액은 경고를 읽고 넘어갈 자리가 아니다.
    """
    tolerance = _config(store, "capital.gate_slippage_tolerance", as_of, 0.30)
    required = int(_config(store, "capital.gate_min_trading_days", as_of, 60.0))
    name = f"슬리피지 실측이 모델 예측의 ±{tolerance:.0%} 이내"
    measurements, notes = measure_slippage(
        store, as_of=as_of, market=market, lookback=required * 2, source=BROKER_SOURCE
    )
    text, code = render_slippage(measurements, notes, source=BROKER_SOURCE)
    lines = text.splitlines()
    if len(measurements) < MIN_SLIPPAGE_SAMPLES:
        return Check(
            name, "미측정",
            [
                *lines,
                f"표본 {len(measurements)}건 < {MIN_SLIPPAGE_SAMPLES}건 — 판정하지 않는다.",
                "1주짜리 배선 검증 체결은 시장에 충격을 안 줘서, 그것으로 잰",
                "오차는 전략의 체결 비용을 말해주지 않는다.",
            ],
        )
    return Check(name, {0: "PASS", 1: "FAIL", 2: "미측정"}[code], lines)


# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    as_of = LiveClock().now()

    print(f"자본 증액 게이트 — as_of {as_of.isoformat()}, 창고 {store.root}\n")

    checks = [
        check_trading_days(store, as_of, args.market),
        check_order_fail_rate(store, as_of, args.market),
        check_missing_rate(store, as_of, args.market),
        check_slippage(store, as_of, args.market),
    ]
    for check in checks:
        print(check.render())
        print()

    failed = [c for c in checks if c.status == "FAIL"]
    unmeasured = [c for c in checks if c.status == "미측정"]
    passed = [c for c in checks if c.status == "PASS"]
    print(f"PASS {len(passed)} · FAIL {len(failed)} · 미측정 {len(unmeasured)} / {len(checks)}")

    if failed:
        print("\n증액하지 않는다 — 못 넘은 기준이 있다:")
        for check in failed:
            print(f"  - {check.name}")
        return 1
    if unmeasured:
        print("\n아직 판정할 수 없다 — 표본이 모자란 기준이 있다:")
        for check in unmeasured:
            print(f"  - {check.name}")
        return 2

    multiplier = _config(store, "capital.step_multiplier", as_of, 2.5)
    print(f"\n전부 통과 — 자본을 {multiplier}배로 올릴 수 있다. **증액은 사람이 한다.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
