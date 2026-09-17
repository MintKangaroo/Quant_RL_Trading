"""시행 U — 노출 타이밍이 비용을 감당하는가. docs/protocols/exposure-timing-2026-09.md 대로 한 번 잰다.

    .venv/bin/python tools/trial_exposure_timing.py [--save]

모의계좌 13세션에서 **거래의 76%가 종목 선택이 아니라 노출 변경**이었다(연 27회전). 그 타이밍이
비용을 감당하는지 대리 경로에서 잰다. 뼈대는 시행 R(`trial_regime_rule.py`)과 같고, 다른 것은
국면 축 하나가 아니라 **노출 결정 전체**(추세·국면·압축 최솟값)를 다룬다는 점이다.

지수 계열은 한 번 읽어 두고, **생산 코드의 축 함수를 그대로 호출**한다 — 규칙을 여기 다시 쓰면
재는 대상이 생산과 갈라진다. 그래서 `store.get(indices, …)` 만 흉내 내는 얇은 어댑터를 끼운다.
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

from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.selector.exposure import (  # noqa: E402
    BAND_EPSILON,
    INDEX_LOOKBACK_DAYS,
    ExposureParams,
    regime_scale,
    squeeze_scale,
    trend_scale,
)
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_alpha_breadth import path  # noqa: E402
from tools.trial_e2e_final import ANN, END, ONE_WAY_COST, START, Panel  # noqa: E402
from tools.trial_regime_rule import JUDGE_START, MIN_HISTORY, PANEL_START, state_at  # noqa: E402

PROTOCOL = Path("docs/protocols/exposure-timing-2026-09.md")
INDEX_ID = "KR:IDX:KRX 300"
#: 시행 R 의 R0 과 같은 crisis 하한 — 대용 지수도 같다. 경로를 바꾸면 R 과 못 견준다.
CRISIS_FLOOR = 0.0
MIN_JUDGE_SESSIONS = 300


class _IndexView:
    """``indices`` 만 답하는 얇은 창고. 미리 읽은 프레임에 창고와 같은 필터를 건다.

    창고 규칙 그대로다 — ``observed_at <= as_of`` 와 ``valid_from >= as_of − lookback일``
    (`reader._valid_from_floor` 의 int 의미: N 달력일 전 UTC 자정).
    """

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def get(self, table: str, *, as_of: datetime, entity=None, lookback=None,
            columns=None, **_kwargs) -> pd.DataFrame:
        if table != "indices":
            raise AssertionError(f"이 어댑터는 indices 만 답한다: {table}")
        out = self._frame
        out = out[out["observed_at"] <= as_of]
        if entity is not None:
            wanted = {entity} if isinstance(entity, str) else set(entity)
            out = out[out["entity_id"].isin(wanted)]
        if lookback is not None:
            floor_date = as_of.astimezone(UTC).date() - timedelta(days=int(lookback))
            floor = datetime.combine(floor_date, time.min, tzinfo=UTC)
            out = out[out["valid_from"] >= floor]
        return out if columns is None else out[[c for c in columns if c in out.columns]]


def raw_scales(view: _IndexView, closes: pd.Series, days: list[date],
               params: ExposureParams) -> tuple[list[float], list[str]]:
    """세션마다 세 축의 **최솟값**. 생산 `decide()` 와 같은 규칙, 같은 함수들."""
    recent: list[str] = []
    out: list[float] = []
    drivers: list[str] = []
    for day in days:
        # t−1 종가까지로 정한다 — 라이브와 같은 지연.
        as_of = datetime.combine(day, time.min, tzinfo=UTC)
        trend, _ = trend_scale(view, as_of=as_of, index_id=INDEX_ID, params=params)
        squeeze, _ = squeeze_scale(view, as_of=as_of, index_id=INDEX_ID, params=params)
        state = state_at(closes, day, CRISIS_FLOOR)
        regime, _ = regime_scale(state, params, recent_states=tuple(recent))
        recent = (recent + [state])[-max(1, params.regime_confirm_sessions):]
        axes = {"trend": trend, "regime": regime, "squeeze": squeeze}
        driver = min(axes, key=lambda name: axes[name])
        out.append(float(axes[driver]))
        drivers.append(driver if axes[driver] < 1.0 else "none")
    return out, drivers


def deadband(raw: list[float], width: float, *, asymmetric: bool = False) -> list[float]:
    """유지 중인 배수와 차이가 ``width`` 미만이면 그대로 둔다.

    ``asymmetric`` 이면 **내릴 때는 즉시** 따르고 올릴 때만 밴드를 건다 —
    현행 `regime_confirm_sessions` 의 정신("낮추기는 즉시, 올리기는 확인")과 같다.
    """
    held = 1.0
    out: list[float] = []
    for value in raw:
        if asymmetric and value < held:
            held = value
        # 생산과 **같은 여유**를 쓴다 — 안 맞추면 여기서 잰 것과 실전이 다르게 움직인다.
        # `1.0 - 0.8 = 0.19999999999999996` 이라 여유가 없으면 0.8 이 흡수 상태가 된다.
        elif abs(value - held) >= width - BAND_EPSILON:
            held = value
        out.append(held)
    return out


def evaluate(net: np.ndarray, scales: list[float]) -> dict[str, float]:
    """시행 R 의 `evaluate` 와 같은 식 — 배수를 곱하고 변화분에 편도 비용을 문다."""
    s = np.asarray(scales, dtype=float)
    prev = np.concatenate([[1.0], s[:-1]])
    cost = np.abs(s - prev) * ONE_WAY_COST
    r = s * net - cost
    nav = np.cumprod(1 + r)
    half = len(r) // 2
    return {
        "ann": float(r.mean() * ANN),
        "mdd": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "switches": int((np.abs(s - prev) > 1e-12).sum()),
        "turnover": float(np.abs(s - prev).sum()),
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
    print(f"=== 시행 U — {PROTOCOL} (해시 {digest}) ===", flush=True)

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
    print(f"{INDEX_ID}: {len(closes)}세션 {closes.index.min()}~{closes.index.max()}")
    print(f"판정 {len(days)}세션 {days[0]}~{days[-1]} · 배수 {params.regime_scale} "
          f"· 추세 {params.below_trend} · 압축 {params.squeezed} "
          f"· 확인 {params.regime_confirm_sessions}세션", flush=True)
    if len(days) < MIN_JUDGE_SESSIONS:
        print(f"판정 세션 {len(days)} < {MIN_JUDGE_SESSIONS} — 보류(기각 아님)", flush=True)
        return 2
    if len(closes) < MIN_HISTORY:
        print("지수 이력이 모자라다 — 보류", file=sys.stderr)
        return 2

    raw, drivers = raw_scales(view, closes, days, params)
    variants = {
        "U0": raw,
        "U1": [1.0] * len(raw),
        "U2": deadband(raw, 0.10),
        "U3": deadband(raw, 0.20),
        "U4": deadband(raw, 0.20, asymmetric=True),
    }
    results = {name: evaluate(net, s) for name, s in variants.items()}
    driven = sum(1 for d in drivers if d != "none")
    counts = {d: drivers.count(d) for d in ("trend", "regime", "squeeze")}
    lines = [
        f"노출이 1.0 아닌 세션 {driven}/{len(drivers)} · 축별 {counts}",
        "",
        "| 변형 | 비용 후 연수익 | MDD | 전환 | 배수이동합 | 평균 배수 | 전반 | 후반 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, m in results.items():
        lines.append(
            f"| {name} | {m['ann']:+.1%} | {m['mdd']:.1%} | {m['switches']} | "
            f"{m['turnover']:.1f} | {m['avg_scale']:.2f} | {m['ann_1h']:+.1%} | {m['ann_2h']:+.1%} |"
        )
    base = results["U0"]
    # 등록 기준: 셋 다 충족. U1(축 끔)은 채택 후보가 아니다 — 보고만 한다.
    passed = {
        n: (r["ann"] >= base["ann"] and r["mdd"] >= base["mdd"] and r["switches"] <= base["switches"])
        for n, r in results.items() if n not in ("U0", "U1")
    }
    winners = [n for n, ok in passed.items() if ok]
    if winners:
        chosen = min(winners, key=lambda n: (results[n]["switches"], n))
        verdict = f"채택 {chosen}"
    else:
        verdict = "기각 (현행 유지)"
    lines.append("")
    lines.append("통과(①수익 ≥ ②MDD ≤ ③전환 ≤ U0): "
                 + " · ".join(f"{n} {'○' if ok else '×'}" for n, ok in passed.items()))
    lines.append(f"판정: {verdict}")
    u1 = results["U1"]
    lines.append(
        f"참고(채택 후보 아님) · U1 노출 끔: 연수익 {u1['ann']:+.1%} vs U0 {base['ann']:+.1%} · "
        f"MDD {u1['mdd']:.1%} vs {base['mdd']:.1%} — 노출 축이 산 것과 치른 것"
    )
    text = "\n".join(lines)
    print("\n" + text, flush=True)

    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "exposure-timing-2026-09:U", "valid_from": now, "observed_at": now,
            "source": "trial_exposure_timing", "market": "KR", "family": "exposure", "n_trials": 1,
            "protocol_hash": digest, "detail": (text.replace("\n", " | "))[:900],
        }], ingest_run_id=f"trial-exposure-U-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: exposure/U · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
