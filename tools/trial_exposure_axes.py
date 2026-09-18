"""시행 V — 노출 축 셋 중 무엇이 값을 하나. docs/protocols/exposure-axes-2026-09.md 대로 한 번 잰다.

    .venv/bin/python tools/trial_exposure_axes.py [--save]

시행 U 는 노출 축을 **통째로** 끄면 연 +7.7%p 가 남고 MDD 는 같다는 것을 보였다. 그런데 축이
셋이다(추세 77 · 국면 53 · 압축 35 세션). 어느 축이 비용을 내는지 모른 채 전부 끄거나 전부
두는 것은 거칠다. 여기서는 **하나 뺀 것**(한계기여)과 **하나만**(단독 값)을 같이 잰다.

대리 경로·판정 창·비용 규칙은 시행 U 와 **완전히 같다** — 경로를 바꾸면 U 와 못 견준다.
데드밴드는 끈다(U 에서 무용으로 판정).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.selector.exposure import (  # noqa: E402
    FLOOR,
    INDEX_LOOKBACK_DAYS,
    ExposureParams,
    regime_scale,
    squeeze_scale,
    trend_scale,
)
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_alpha_breadth import path  # noqa: E402
from tools.trial_e2e_final import ANN, END, ONE_WAY_COST, START, Panel  # noqa: E402
from tools.trial_exposure_timing import INDEX_ID, CRISIS_FLOOR, _IndexView  # noqa: E402
from tools.trial_regime_rule import JUDGE_START, PANEL_START, state_at  # noqa: E402

PROTOCOL = Path("docs/protocols/exposure-axes-2026-09.md")
AXES = ("trend", "regime", "squeeze")
#: 등록된 여덟 조합. 순서가 표의 순서다.
VARIANTS: dict[str, tuple[str, ...]] = {
    "V0": AXES,
    "V1": (),
    "V2": ("regime", "squeeze"),
    "V3": ("trend", "squeeze"),
    "V4": ("trend", "regime"),
    "V5": ("trend",),
    "V6": ("regime",),
    "V7": ("squeeze",),
}
WORST_BLOCK = 20
MIN_JUDGE_SESSIONS = 300


def axis_scales(view, closes, days, params: ExposureParams) -> dict[str, list[float]]:
    """세션마다 **축별** 배수. 조합은 호출부가 최솟값으로 만든다 (생산 `decide` 와 같은 규칙)."""
    out: dict[str, list[float]] = {name: [] for name in AXES}
    recent: list[str] = []
    for day in days:
        as_of = datetime.combine(day, time.min, tzinfo=UTC)
        trend, _ = trend_scale(view, as_of=as_of, index_id=INDEX_ID, params=params)
        squeeze, _ = squeeze_scale(view, as_of=as_of, index_id=INDEX_ID, params=params)
        state = state_at(closes, day, CRISIS_FLOOR)
        regime, _ = regime_scale(state, params, recent_states=tuple(recent))
        recent = (recent + [state])[-max(1, params.regime_confirm_sessions):]
        out["trend"].append(float(trend))
        out["regime"].append(float(regime))
        out["squeeze"].append(float(squeeze))
    return out


def combine(per_axis: dict[str, list[float]], keep: tuple[str, ...], n: int) -> list[float]:
    """고른 축들의 **최솟값**. 바닥(FLOOR)은 생산과 같이 여기서 건다."""
    if not keep:
        return [1.0] * n
    return [max(FLOOR, min(per_axis[name][i] for name in keep)) for i in range(n)]


def evaluate(net: np.ndarray, scales: list[float]) -> dict[str, float]:
    """시행 U 의 `evaluate` 와 같은 식 + **최악 20세션 누적수익**."""
    s = np.asarray(scales, dtype=float)
    prev = np.concatenate([[1.0], s[:-1]])
    r = s * net - np.abs(s - prev) * ONE_WAY_COST
    nav = np.cumprod(1 + r)
    block = pd.Series(r).rolling(WORST_BLOCK).apply(lambda x: float(np.prod(1 + x) - 1), raw=True)
    half = len(r) // 2
    return {
        "ann": float(r.mean() * ANN),
        "mdd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "switches": int((np.abs(s - prev) > 1e-12).sum()),
        "worst": float(block.min()),
        "avg_scale": float(s.mean()),
        "ann_1h": float(r[:half].mean() * ANN),
        "ann_2h": float(r[half:].mean() * ANN),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true", help="research_trials 에 기록(시행 소진)")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 V — {PROTOCOL} (해시 {digest}) ===", flush=True)

    store = Store(root=Path(args.root))
    sessions = trading_days(Market.KR, PANEL_START, END)
    panel = Panel(store, sessions)
    sidx = {s: i for i, s in enumerate(panel.sessions)}
    days = [s for s in panel.sessions if JUDGE_START <= s <= END]
    frame, _ = path(panel, [sidx[s] for s in days], 24, 0.0625)
    net = frame["net"].to_numpy(dtype=float)
    days = days[: len(net)]

    as_of_end = datetime.combine(END, time(23, 0), tzinfo=UTC)
    params = ExposureParams.from_store(store, as_of=as_of_end)
    index_frame = store.get(
        "indices", as_of=as_of_end, entity=INDEX_ID,
        lookback=(END - START).days + INDEX_LOOKBACK_DAYS + 40,
        columns=["entity_id", "valid_from", "observed_at", "close"],
    )
    view = _IndexView(index_frame)
    closes = index_frame.assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = closes.groupby("day")["close"].last().sort_index()
    print(f"판정 {len(days)}세션 {days[0]}~{days[-1]} · 배수 {params.regime_scale} "
          f"· 추세 {params.below_trend} · 압축 {params.squeezed} "
          f"· 확인 {params.regime_confirm_sessions}세션 (데드밴드는 끈다)", flush=True)
    if len(days) < MIN_JUDGE_SESSIONS:
        print(f"판정 세션 {len(days)} < {MIN_JUDGE_SESSIONS} — 보류(기각 아님)", flush=True)
        return 2

    per_axis = axis_scales(view, closes, days, params)
    fired = {name: sum(1 for v in per_axis[name] if v < 1.0) for name in AXES}
    results = {n: evaluate(net, combine(per_axis, keep, len(days))) for n, keep in VARIANTS.items()}
    base = results["V0"]
    passed = {
        n: (r["ann"] >= base["ann"] and r["mdd"] >= base["mdd"]
            and r["switches"] <= base["switches"] and r["worst"] >= base["worst"])
        for n, r in results.items() if n != "V0"
    }
    winners = [n for n, ok in passed.items() if ok]
    if winners:
        chosen = min(winners, key=lambda n: (len(VARIANTS[n]), results[n]["switches"], n))
        verdict = f"채택 {chosen} — 켠 축 {VARIANTS[chosen] or '없음'}"
    else:
        verdict = "기각 (현행 V0 유지)"

    lines = [
        f"축별 발동 세션 {fired} / {len(days)}",
        "",
        "| 변형 | 켠 축 | 비용 후 연수익 | MDD | 최악20 | 전환 | 평균 배수 | 전반 | 후반 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, keep in VARIANTS.items():
        m = results[name]
        lines.append(
            # **소수점 셋까지 찍는다.** 한 자리로는 게이트가 왜 떨어뜨렸는지 안 보인다 —
            # 2026-09-18 실행에서 여덟 변형의 MDD 가 전부 "−17.7%" 로 같아 보였는데
            # 전체 정밀도에서는 갈렸다. 반올림이 판정 근거를 가리면 안 된다.
            f"| {name} | {'·'.join(keep) or '없음'} | {m['ann']:+.2%} | {m['mdd']:+.3%} | "
            f"{m['worst']:+.3%} | {m['switches']} | {m['avg_scale']:.2f} | "
            f"{m['ann_1h']:+.1%} | {m['ann_2h']:+.1%} |"
        )
    lines.append("")
    lines.append("통과(①수익 ≥ ②MDD ≤ ③전환 ≤ ④최악20 ≥, V0 대비): "
                 + " · ".join(f"{n} {'○' if ok else '×'}" for n, ok in passed.items()))
    lines.append(f"판정: {verdict}")
    text = "\n".join(lines)
    print("\n" + text, flush=True)

    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "exposure-axes-2026-09:V", "valid_from": now, "observed_at": now,
            "source": "trial_exposure_axes", "market": "KR", "family": "exposure", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-exposure-V-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: exposure/V · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
