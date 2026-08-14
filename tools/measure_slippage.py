"""슬리피지 실측 — M3 완료 기준 5번.

    uv run python tools/measure_slippage.py --market KR --start 2026-08-01

`docs/milestones.md` 의 M3 완료 기준: **"슬리피지 실측이 모델 예측의 ±30%
이내"**. 모델은 `replay/fills.py` 의 충격비용

    충격비용(bp) = impact_k × 변동성 × √(체결수량 / ADV) × 10,000

이고, 실측은 실제 체결가가 **주문 시점 기준가**에서 얼마나 벗어났는지다.

    실현(bp) = 방향 × (체결가 - 기준가) / 기준가 × 10,000
    방향: 매수 +1, 매도 -1  (비싸게 사고 싸게 판 만큼이 비용이다)

기준가는 결정일 종가다 — `executor/pipeline.py:177` 이 `item.price`(그날
`prices.close`)를 `reference_price` 로 넘기고, 지정가가 거기서 파생된다.
`orders.limit_price` 를 거꾸로 풀지 않는 이유는 호가단위 반올림(`ticks.py`)이
이미 정보를 지웠기 때문이다 — 되돌리면 반 틱만큼 항상 틀린다.

## 시뮬레이션 체결로는 이 기준을 통과할 수 없다 (통과해도 의미가 없다)

`trades.source` 가 둘이다. `backtest` 는 `replay/fills.py` 가 **모델 그 자체로**
만든 체결이라, 실측을 모델과 비교하면 정의상 오차 0 이 나온다. 자기 자신과
비교해 놓고 "모델이 맞았다" 고 적는 것이다. `broker` 만 실제 체결이다.

그래서 기본값이 `--source broker` 이고, 표본이 시뮬레이션뿐이면 이 도구는
**PASS 를 내지 않는다.** 종료코드 2 로 "아직 잴 수 없다" 를 구분해서 낸다
(1 은 기준 미달, 0 은 통과).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.replay.fills import FillParams, MarketState, impact_bps  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

#: 완료 기준의 허용 오차. 실측이 예측의 (1±TOLERANCE) 안에 들어와야 한다.
TOLERANCE = 0.30

#: ADV·변동성을 재는 창. 모델이 쓰는 것과 같은 길이여야 비교가 성립한다.
ADV_WINDOW = 20

#: 실제 체결. 이것 말고는 모델과 비교할 자격이 없다.
REAL_SOURCE = "broker"


@dataclass(frozen=True)
class Measurement:
    entity_id: str
    session: datetime
    side: str
    quantity: float
    reference: float
    fill_price: float
    realized_bps: float
    predicted_bps: float

    @property
    def ratio(self) -> float:
        """실측 / 예측. 예측이 0 이면 비교가 성립하지 않는다."""
        if self.predicted_bps == 0:
            return float("nan")
        return self.realized_bps / self.predicted_bps

    @property
    def within(self) -> bool:
        ratio = self.ratio
        if ratio != ratio:  # NaN
            return False
        return abs(ratio - 1.0) <= TOLERANCE


def _market_state(
    prices: pd.DataFrame, entity_id: str, session: pd.Timestamp
) -> MarketState | None:
    """체결 시점의 시장 상태. 모델이 보는 것과 같은 것만 넣는다.

    ADV 와 변동성은 **체결일 이전** 창에서만 잰다. 체결일을 포함하면 그날의
    거래량으로 그날의 충격을 예측하는 셈이라, 예측이 실측을 훔쳐본다.
    """
    rows = prices[prices["entity_id"] == entity_id].sort_values("valid_from")
    past = rows[rows["valid_from"] < session]
    if past.empty:
        return None
    window = past.tail(ADV_WINDOW)
    adv = float(window["volume"].mean())
    # **종가 0 세션을 먼저 걷어낸다.** 창고에 close=0 인 세션이 실재하고,
    # 그대로 pct_change 를 하면 inf 가 하나 섞여 std 가 통째로 NaN 이 된다.
    # NaN 은 예외를 내지 않고 조용히 퍼져서, 예측값이 사라진 줄도 모르고
    # "비교 불가" 를 "기준 밖" 으로 읽게 만든다.
    closes = window["close"]
    closes = closes[closes > 0]
    returns = closes.pct_change().replace([float("inf"), float("-inf")], pd.NA).dropna()
    volatility = float(returns.std()) if len(returns) > 1 else 0.0
    if volatility != volatility:
        volatility = 0.0
    fill_day = rows[rows["valid_from"] == session]
    close = float(fill_day.iloc[-1]["close"]) if not fill_day.empty else float(
        past.iloc[-1]["close"]
    )
    return MarketState(
        entity_id=entity_id,
        close=close,
        volume=float(window.iloc[-1]["volume"]),
        adv=adv,
        volatility=volatility,
    )


def _reference_price(
    prices: pd.DataFrame, entity_id: str, session: pd.Timestamp
) -> float | None:
    """기준가 — 결정일 종가.

    결정은 D, 체결은 D+1 이다. 체결일 종가를 기준가로 쓰면 슬리피지가
    구조적으로 0 에 붙어 버린다 — 모델이 예측하는 것은 결정 시점 대비
    이탈이지 체결 당일 종가 대비 이탈이 아니다.
    """
    rows = prices[(prices["entity_id"] == entity_id) & (prices["valid_from"] < session)]
    if rows.empty:
        return None
    close = float(rows.sort_values("valid_from").iloc[-1]["close"])
    return close if close > 0 else None


def measure(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    lookback: int,
    source: str,
) -> tuple[list[Measurement], list[str]]:
    notes: list[str] = []
    trades = store.get("trades", as_of=as_of, lookback=lookback, market=market)
    if trades.empty:
        return [], [f"trades 0행 (market={market}, lookback={lookback}일)"]

    by_source = trades["source"].value_counts().to_dict()
    notes.append("체결 출처: " + ", ".join(f"{k} {v}행" for k, v in sorted(by_source.items())))

    real = trades[trades["source"] == source]
    if real.empty:
        notes.append(
            f"source={source} 체결이 0행이다. "
            f"{REAL_SOURCE} 체결이 없으면 모델을 검증할 수 없다 — "
            "시뮬레이션 체결은 모델 그 자체라 비교가 자기순환이다."
        )
        return [], notes

    prices = store.get("prices", as_of=as_of, lookback=lookback + ADV_WINDOW * 2, market=market)
    if prices.empty:
        notes.append("prices 0행 — 기준가를 알 수 없다")
        return [], notes

    params = FillParams.from_store(store, as_of=as_of)
    measurements: list[Measurement] = []
    for row in real.sort_values(["valid_from", "entity_id"]).to_dict(orient="records"):
        entity = str(row["entity_id"])
        session = row["valid_from"]
        reference = _reference_price(prices, entity, session)
        if reference is None:
            notes.append(f"{entity} {session:%Y-%m-%d}: 기준가 없음 — 제외")
            continue
        state = _market_state(prices, entity, session)
        if state is None or state.adv <= 0:
            notes.append(f"{entity} {session:%Y-%m-%d}: ADV 없음 — 제외")
            continue
        if state.volatility <= 0:
            # 예측이 0 이면 비율이 정의되지 않는다. 0 을 예측값으로 세워 두면
            # "예측 0bp 인데 실측 0.8bp" 가 무한대 오차로 찍혀 표를 오염시킨다.
            notes.append(f"{entity} {session:%Y-%m-%d}: 변동성 계산 불가 — 제외")
            continue

        quantity = float(row["quantity"])
        fill_price = float(row["price"])
        direction = 1.0 if str(row["side"]) == "buy" else -1.0
        realized = direction * (fill_price - reference) / reference * 10_000
        predicted = impact_bps(int(quantity), state, params)
        measurements.append(
            Measurement(
                entity_id=entity,
                session=session,
                side=str(row["side"]),
                quantity=quantity,
                reference=reference,
                fill_price=fill_price,
                realized_bps=realized,
                predicted_bps=predicted,
            )
        )
    return measurements, notes


def render(measurements: list[Measurement], notes: list[str], *, source: str) -> tuple[str, int]:
    lines: list[str] = ["슬리피지 실측 — M3 완료 기준 5번", ""]
    lines += [f"  {note}" for note in notes]
    lines.append("")

    if not measurements:
        lines.append("[미측정] 비교할 실제 체결이 없다.")
        lines.append("")
        lines.append("  M3 완료 기준 5번은 shadow 가 아니라 **실계좌 체결**을 요구한다.")
        lines.append("  시뮬레이션 체결(source=backtest)은 replay/fills.py 가 모델로")
        lines.append("  만든 것이라, 그것으로 모델을 검증하면 언제나 오차 0 이 나온다.")
        lines.append("  실계좌 소액 투입(tools/verify_live_order.py) 뒤에 다시 돌릴 것.")
        return "\n".join(lines), 2

    header = (
        f"{'종목':<14}{'세션':<12}{'방향':<6}{'수량':>8}"
        f"{'실측bp':>10}{'예측bp':>10}{'비율':>8}"
    )
    lines.append(header)
    lines.append("-" * 72)
    for m in measurements:
        mark = "" if m.within else "  ←기준밖"
        lines.append(
            f"{m.entity_id:<14}{m.session:%Y-%m-%d}  {m.side:<6}"
            f"{m.quantity:>8.0f}{m.realized_bps:>10.1f}{m.predicted_bps:>10.1f}"
            f"{m.ratio:>8.2f}{mark}"
        )

    inside = [m for m in measurements if m.within]
    ratio = len(inside) / len(measurements)
    realized_mean = sum(m.realized_bps for m in measurements) / len(measurements)
    predicted_mean = sum(m.predicted_bps for m in measurements) / len(measurements)

    lines.append("")
    lines.append(f"  표본 {len(measurements)}건 (source={source})")
    lines.append(f"  평균 실측 {realized_mean:.1f}bp · 평균 예측 {predicted_mean:.1f}bp")
    lines.append(f"  ±{TOLERANCE:.0%} 안 {len(inside)}/{len(measurements)} = {ratio:.0%}")

    if len(measurements) < 30:
        lines.append("")
        lines.append(
            f"  [경고] 표본이 {len(measurements)}건뿐이다. 통과로 읽지 마라 — "
            "체결 몇 건의 우연을 모델 검증이라 부르는 것이다."
        )
        return "\n".join(lines), 2

    passed = ratio >= 0.7
    lines.append("")
    mark = "PASS" if passed else "FAIL"
    lines.append(f"[{mark}] 슬리피지 실측이 모델 예측의 ±{TOLERANCE:.0%} 이내")
    return "\n".join(lines), 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="슬리피지 실측 (M3 완료 기준 5번)")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--lookback", type=int, default=60, help="거슬러 볼 일수")
    parser.add_argument(
        "--source",
        default=REAL_SOURCE,
        help=f"체결 출처. 기본 {REAL_SOURCE}(실계좌). backtest 는 모델 자기순환이라 검증이 아니다",
    )
    parser.add_argument("--as-of", default=None, help="기준 시각 (기본: 현재)")
    args = parser.parse_args()

    load_env()
    store = build_store()
    as_of = (
        datetime.fromisoformat(args.as_of).replace(tzinfo=UTC)
        if args.as_of
        else LiveClock().now()
    )

    measurements, notes = measure(
        store,
        as_of=as_of,
        market=args.market,
        lookback=args.lookback,
        source=args.source,
    )
    text, code = render(measurements, notes, source=args.source)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
