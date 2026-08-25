"""사전등록 2차 후보를 잰다 — 처녀 표본(2021~24)이 1차 판정이다.

    .venv/bin/python tools/report_registry2.py

원본은 `docs/feature-registry-2.md` 다. **그 문서가 백필 데이터를 보기 전에
커밋됐고**(4b45701), 아래 EXPECTED 는 그 표를 코드로 옮긴 것이다. 규칙:

    부호 일치 AND NW t ≥ 2 AND BH FDR 10%
    1차 판정 = 처녀 표본(2021~2024, 어떤 결정에도 쓰인 적 없는 구간)
    2차 확인 = 2025~2026 — 1차 통과가 여기서 부호가 뒤집히면 기각

C군(chart 5일축 8종)은 가격만 필요해 처녀 판정이 가능하다. A군(공매도)은
데이터가 2025-07부터라 확인 창에서만 재고 **관찰 상한**을 못 벗어난다.
B군(장중)은 커버리지를 먼저 재고, 횡단면이 안 되면(종목 수 부족) 측정하지
않는다 — 몇 종목짜리 IC 는 숫자가 나와도 뜻이 없다.

전부 벡터화다: 세션 루프가 아니라 가격 패널 전체에서 rolling 으로 만든다 —
850세션 × 2,800종목을 Analyst 루프로 돌면 몇 시간이지만 패널로는 몇 분이다.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.report_feature_ic import benjamini_hochberg, two_sided_p  # noqa: E402

MARKET = "KR"
HORIZON = 5
FDR = 0.10
T_GATE = 2.0

#: 처녀 표본(1차 판정)과 확인 표본(2차). 처녀 창의 시작은 가격(2021-08-11)
#: + 롤링 워밍업 120세션이 정한다.
VIRGIN = (datetime(2022, 2, 1, tzinfo=UTC), datetime(2024, 12, 30, tzinfo=UTC))
CONFIRM = (datetime(2025, 1, 2, tzinfo=UTC), datetime(2026, 6, 30, tzinfo=UTC))

#: `docs/feature-registry-2.md` 의 부호. 문서가 원본이다.
EXPECTED_C = {
    "trend_persist_60": +1, "ma_stack_20": +1, "adx_14": +1,
    "bb_squeeze": +1, "range_compression": +1, "efficiency_20": +1,
    "up_day_share_20": +1, "volume_confirm_20": +1,
}
EXPECTED_A = {
    "short_intensity_5": -1, "short_intensity_20": -1,
    "short_balance_delta_5": -1, "days_to_cover": -1,
}
EXPECTED_B = {"close_strength_5": +1, "overnight_gap_5": +1, "intraday_vol_ratio": +1}


def _panel(store: Store, *, until: datetime, lookback_days: int) -> pd.DataFrame:
    """보정 종가·거래량 와이드 패널. 인덱스=세션, 컬럼=종목."""
    prices = read_prices(
        store, as_of=until, lookback=lookback_days, market=MARKET,
        columns=["entity_id", "valid_from", "close", "volume"], adjusted=True,
    )
    prices["session"] = prices["valid_from"].dt.date
    close = prices.pivot_table(index="session", columns="entity_id",
                               values="close", aggfunc="last").sort_index()
    volume = prices.pivot_table(index="session", columns="entity_id",
                                values="volume", aggfunc="last").sort_index()
    return close, volume


def chart_state_features(close: pd.DataFrame, volume: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """C군 8종 — `docs/ic-2026-08-18-chart-trend.md` 의 정의 그대로.

    압축 계열은 **자기 과거 분포의 분위수**다. 절대 폭이면 저변동 종목이 늘
    "압축" 으로 잡혀 low_volatility 의 복제가 된다 — 그 함정은 이미 밟았다.
    """
    returns = close.pct_change(fill_method=None)
    ma5, ma20, ma60 = close.rolling(5).mean(), close.rolling(20).mean(), close.rolling(60).mean()
    out: dict[str, pd.DataFrame] = {}

    out["trend_persist_60"] = (close > ma20).rolling(60).mean()
    out["ma_stack_20"] = ((ma5 > ma20) & (ma20 > ma60)).rolling(20).mean()

    # ADX(14) — Wilder. 종가만 있으므로 고저 대신 |수익률| 기반 근사 대신,
    # 등록 정의가 "추세 강도" 이므로 방향성 지수를 종가 차분으로 만든다.
    delta = close.diff()
    up = delta.clip(lower=0.0).rolling(14).mean()
    down = (-delta.clip(upper=0.0)).rolling(14).mean()
    out["adx_14"] = ((up - down).abs() / (up + down)).rolling(14).mean()

    # 볼린저 폭 = 20일 std / 20일 mean → 자기 과거 120세션 분위수, 부호 반전.
    bb_width = close.rolling(20).std() / close.rolling(20).mean()
    out["bb_squeeze"] = 1.0 - bb_width.rolling(120).rank(pct=True)

    range20 = close.rolling(20).max() - close.rolling(20).min()
    range120 = close.rolling(120).max() - close.rolling(120).min()
    out["range_compression"] = 1.0 - (range20 / range120)

    net_move = (close - close.shift(20)).abs()
    path_len = close.diff().abs().rolling(20).sum()
    out["efficiency_20"] = net_move / path_len

    out["up_day_share_20"] = (returns > 0).rolling(20).mean()

    up_vol = volume.where(returns > 0).rolling(20).mean()
    down_vol = volume.where(returns < 0).rolling(20).mean()
    out["volume_confirm_20"] = up_vol / down_vol - 1.0
    return out


def short_features(store: Store, *, until: datetime, lookback_days: int) -> dict[str, pd.DataFrame]:
    """A군 4종 — short_flow 창고 컬럼에서."""
    frame = store.get("short_flow", as_of=until, lookback=lookback_days)
    if frame.empty:
        return {}
    frame["session"] = frame["valid_from"].dt.date

    def wide(col: str) -> pd.DataFrame:
        return frame.pivot_table(index="session", columns="entity_id",
                                 values=col, aggfunc="last").sort_index()

    short_v, total_v = wide("short_volume"), wide("total_volume")
    ratio = (short_v / total_v.replace(0.0, np.nan))
    position, prev_position = wide("short_position"), wide("previous_short_position")
    adv = wide("average_daily_volume")
    out = {
        "short_intensity_5": -(ratio.rolling(5).mean()),
        "short_intensity_20": -(ratio.rolling(20).mean()),
        "short_balance_delta_5": -((position - prev_position).rolling(5).sum()
                                   / adv.replace(0.0, np.nan)),
        "days_to_cover": -wide("days_to_cover"),
    }
    # 등록 부호가 − 이므로 위에서 이미 반전했다 → EXPECTED 비교는 +1 로 본다.
    return out


def measure_window(
    features: dict[str, pd.DataFrame], targets: pd.DataFrame,
    expected: dict[str, int], *, label: str, sign_prenegated: bool = False,
) -> pd.DataFrame:
    """피처 패널 → 일별 Spearman IC → NW t. 등록 부호와 대조."""
    target_wide = targets.pivot_table(index="session", columns="entity_id",
                                      values="target", aggfunc="last")
    rows = []
    for name, sign in expected.items():
        panel = features.get(name)
        if panel is None:
            rows.append({"창": label, "feature": name, "일수": 0})
            continue
        idx = panel.index.intersection(target_wide.index)
        daily = pd.Series({
            s: panel.loc[s].corr(target_wide.loc[s], method="spearman")
            for s in idx
        }).dropna()
        if daily.size < 30:
            rows.append({"창": label, "feature": name, "일수": int(daily.size)})
            continue
        want = +1 if sign_prenegated else sign
        t_value = ic.newey_west_t(daily, lag=HORIZON - 1)
        mean_ic = float(daily.mean())
        rows.append({
            "창": label, "feature": name, "IC": round(mean_ic, 4),
            "t(NW)": round(t_value, 2),
            "부호일치": "○" if np.sign(mean_ic) == want else "✗",
            "일수": int(daily.size),
            "_p": two_sided_p(t_value, int(daily.size)),
            "_t": t_value, "_ic": mean_ic,
        })
    return pd.DataFrame(rows)


def main() -> int:
    store = Store(root=Path("data"))

    for label, (start, until) in (("처녀 2022-02~2024-12", VIRGIN),
                                  ("확인 2025-01~2026-06", CONFIRM)):
        lookback = (until - start).days + 260   # 워밍업 120세션 여유
        close, volume = _panel(store, until=until, lookback_days=lookback)
        window_mask = pd.Series(close.index) >= start.date()
        targets = ic.build_targets(
            store, as_of=until, lookback=(until - start).days + 40, market=MARKET,
        )
        targets = targets[targets["session"] >= start.date()]

        feats = chart_state_features(close, volume)
        feats = {k: v[v.index >= start.date()] for k, v in feats.items()}
        table = measure_window(feats, targets, EXPECTED_C, label=label)

        if label.startswith("확인"):
            sfeats = short_features(store, until=until, lookback_days=lookback)
            sfeats = {k: v[v.index >= start.date()] for k, v in sfeats.items()}
            if sfeats:
                table = pd.concat([
                    table,
                    measure_window(sfeats, targets, EXPECTED_A, label=label,
                                   sign_prenegated=True),
                ], ignore_index=True)

        # B군 커버리지 검사 — 횡단면이 안 되면 재지 않는다.
        intraday = store.get("prices_intraday", as_of=until, lookback=30)
        n_intraday = intraday["entity_id"].nunique() if not intraday.empty else 0
        good = table.dropna(subset=["_p"]) if "_p" in table.columns else pd.DataFrame()
        if not good.empty:
            bh = benjamini_hochberg(good["_p"], fdr=FDR)
            table.loc[good.index, f"BH{FDR:.0%}"] = np.where(bh, "통과", "")

        print(f"\n================ {label} ================")
        cols = [c for c in ["feature", "IC", "t(NW)", "부호일치", "일수", f"BH{FDR:.0%}"]
                if c in table.columns]
        print(table[cols].to_string(index=False))
        if label.startswith("확인"):
            print(f"\n  B군(장중): 최근 30일 커버리지 {n_intraday}종목 — "
                  + ("횡단면 불가, 측정하지 않는다(등록 문서 예고대로)"
                     if n_intraday < 100 else "측정 가능"))
    print("\n채택 = 처녀에서 [부호○ · t≥2 · BH통과] AND 확인에서 부호 유지 (registry-2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
