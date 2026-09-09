"""시행 R — 국면 crisis 조건. docs/protocols/regime-crisis-rule-2026-09.md 대로 한 번 잰다.

    .venv/bin/python tools/trial_regime_rule.py [--save]

대리 = 5회차 대조 경로(trial_alpha_breadth.path, N 24·상한 6.25%)의 비용 후 일수익 r_t.
노출 = 세션 t−1 종가까지의 대용 지수로 정한 국면 배수(확인 2세션, 창고 exposure 설정)를 r_t 에 곱하고,
배수 변화분에 편도 비용을 문다. 변형은 등록 표의 R0~R3, 판정 창 2025-01-02~2026-06-30.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.regime import HIGH_VOL_QUANTILE, LOOKBACK_DAYS  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.selector.exposure import ExposureParams, regime_scale  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_alpha_breadth import path  # noqa: E402
from tools.trial_e2e_final import ANN, END, ONE_WAY_COST, START, Panel  # noqa: E402
from tools.trial_selection_pair import index_of  # noqa: E402

PROTOCOL = Path("docs/protocols/regime-crisis-rule-2026-09.md")
JUDGE_START = date(2025, 1, 2)
#: 대리 경로 구성 시작 — START(2024-02)부터 올리면 시세 패널이 5.6GB 로 커널 OOM 에 죽는다(2026-09-07 두 번).
#: 판정 창 앞 EMA5·완충 워밍업에 한 달이면 충분하다. 결과는 판정 창 안의 수익만 쓴다.
PANEL_START = date(2024, 12, 1)
VARIANTS = {  # 이름: (대용 지수, crisis 모멘텀 하한)
    "R0": ("KR:IDX:KRX 300", 0.0),
    "R1": ("KR:IDX:KRX 300", -0.03),
    "R2": ("KR:IDX:KOSPI", 0.0),
    "R3": ("KR:IDX:KOSPI", -0.03),
}
MIN_HISTORY = 120  # regime.state 와 같다


def state_at(closes: pd.Series, day: date, floor: float) -> str:
    """세션 ``day`` 의 판단에 쓰는 국면 — ``day`` **전날까지**의 종가만 본다(라이브와 같은 지연)."""
    hist = closes[closes.index < day]
    hist = hist[hist.index >= day - timedelta(days=LOOKBACK_DAYS)]
    if len(hist) < MIN_HISTORY:
        return "unknown"
    returns = hist.pct_change()
    realized = returns.rolling(60).std()
    current_vol = float(realized.iloc[-1])
    threshold = float(realized.dropna().quantile(HIGH_VOL_QUANTILE))
    momentum = float(hist.iloc[-1] / hist.iloc[-21] - 1.0)
    if not np.isfinite(current_vol) or not np.isfinite(momentum):
        return "unknown"
    if current_vol > threshold:
        return "crisis" if momentum < floor else "volatile"
    return "bull" if momentum >= 0.0 else "bear"


def scales_for(closes: pd.Series, days: list[date], floor: float, params: ExposureParams) -> tuple[list[float], list[str]]:
    recent: list[str] = []
    out: list[float] = []
    states: list[str] = []
    for day in days:
        state = state_at(closes, day, floor)
        scale, _ = regime_scale(state, params, recent_states=tuple(recent))
        recent = (recent + [state])[-max(1, params.regime_confirm_sessions):]
        out.append(scale)
        states.append(state)
    return out, states


def evaluate(net: np.ndarray, scales: list[float]) -> dict[str, float]:
    s = np.asarray(scales, dtype=float)
    prev = np.concatenate([[1.0], s[:-1]])
    cost = np.abs(s - prev) * ONE_WAY_COST
    r = s * net - cost
    nav = np.cumprod(1 + r)
    mdd = float((nav / np.maximum.accumulate(nav) - 1).min())
    half = len(r) // 2
    return {
        "ann": float(r.mean() * ANN), "mdd": mdd, "switches": int((s != prev).sum()),
        "ann_1h": float(r[:half].mean() * ANN), "ann_2h": float(r[half:].mean() * ANN),
        "avg_scale": float(s.mean()),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true", help="research_trials 에 기록(시행 소진)")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 R — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    sessions = trading_days(Market.KR, PANEL_START, END)
    panel = Panel(store, sessions)
    sidx = {s: i for i, s in enumerate(panel.sessions)}
    days = [s for s in panel.sessions if JUDGE_START <= s <= END]
    idx = [sidx[s] for s in days]
    frame, _ = path(panel, idx, 24, 0.0625)
    net = frame["net"].to_numpy(dtype=float)
    days = days[: len(net)]
    params = ExposureParams.from_store(store, as_of=datetime.combine(END, time(23, 0), tzinfo=UTC))
    # 지수는 판정 창 앞 400일 분포가 필요하다 — 패널과 달리 START(2024-02)부터 읽는다(작다).
    index_sessions = trading_days(Market.KR, START, END)
    closes = {e: index_of(store, index_sessions, "KR", e) for e in {v[0] for v in VARIANTS.values()}}
    for e, c in closes.items():
        print(f"{e}: {len(c)}세션 {c.index.min()}~{c.index.max()}")
    print(f"판정 {len(days)}세션 {days[0]}~{days[-1]} · 배수 {params.regime_scale} · 확인 {params.regime_confirm_sessions}세션", flush=True)
    results = {}
    lines = ["| 변형 | 비용 후 연수익 | MDD | 전환 | crisis 세션 | 평균 배수 | 전반 | 후반 |", "|---|---|---|---|---|---|---|---|"]
    for name, (entity, floor) in VARIANTS.items():
        scales, states = scales_for(closes[entity], days, floor, params)
        m = evaluate(net, scales)
        m["crisis_days"] = sum(1 for s in states if s == "crisis")
        results[name] = m
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['mdd']:.1%} | {m['switches']} | {m['crisis_days']} | {m['avg_scale']:.2f} | {m['ann_1h']:+.1%} | {m['ann_2h']:+.1%} |")
    base = results["R0"]
    passed = {
        n: (r["ann"] >= base["ann"] and r["mdd"] >= base["mdd"] and r["switches"] <= base["switches"])
        for n, r in results.items() if n != "R0"
    }
    if passed.get("R1") or passed.get("R2"):
        chosen = "R1" if passed.get("R1") else "R2"
        if passed.get("R1") and passed.get("R2") and passed.get("R3"):
            chosen = "R3"
        verdict = f"채택 {chosen}"
    else:
        verdict = "기각 (현행 유지)"
    lines.append("")
    lines.append("통과(①수익 ≥ ②MDD ≤ ③전환 ≤ 현행): " + " · ".join(f"{n} {'○' if ok else '×'}" for n, ok in passed.items()))
    lines.append(f"판정: {verdict}")
    text = "\n".join(lines)
    print("\n" + text, flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "regime-crisis-rule-2026-09:R", "valid_from": now, "observed_at": now,
            "source": "trial_regime_rule", "market": "KR", "family": "exposure", "n_trials": 1,
            "protocol_hash": digest, "detail": (text.replace("\n", " | "))[:900],
        }], ingest_run_id=f"trial-exposure-R-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: exposure/R · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
