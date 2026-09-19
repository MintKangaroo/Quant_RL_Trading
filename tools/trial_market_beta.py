"""시행 Y — 베타는 시장만큼, 종목은 랭커가. docs/protocols/market-beta-selection-2026-09.md.

    .venv/bin/python tools/trial_market_beta.py [--save]

틀은 시행 P(`trial_float_cap.py`) 그대로다 — 명단·점수·완충·수익·비용·K200 구성종목이 같아야 P 와 견준다.
여기서 더하는 것은 둘뿐이다: 노출 배수(시행 V 의 국면 축, `trial_regime_rule.scales_for`)와
상승·하락일 추종률. 판정은 Y0(현행 대리) 대비다.
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

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.selector.exposure import ExposureParams  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_bench_construct import _caps  # noqa: E402
from tools.trial_e2e_final import START  # noqa: E402
from tools.trial_exposure_timing import CRISIS_FLOOR, INDEX_ID  # noqa: E402
from tools.trial_float_cap import k200_members  # noqa: E402
from tools.trial_overlay import (  # noqa: E402
    ANN,
    HOLDOUT_START,
    MAX_MOVE,
    ONE_WAY_COST,
    _index,
    _pkl,
    _prices,
    metrics,
)
from tools.trial_regime_rule import scales_for  # noqa: E402
from tools.trial_selection_pair import index_of  # noqa: E402
from tools.trial_selection_ranker import _scores, capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/market-beta-selection-2026-09.md")
N, EXIT_MULT, SPAN, CAP_LIMIT = 24, 3, 5, 0.10
#: 이름: (K200 구성종목만, 유동시총 가중, 노출 V6)
VARIANTS = {
    "Y0": (False, False, True),
    "Y1": (False, False, False),
    "Y2": (True, False, True),
    "Y3": (True, False, False),
    "Y4": (True, True, False),
}
ASYM_GATE, UP_GATE, MDD_SLACK, T_GATE = 0.05, 0.80, 0.02, 2.0


def capture(r: pd.Series, b: pd.Series) -> tuple[float, float]:
    """(상승일 추종, 하락일 추종) — 벤치마크가 오른/내린 날의 Σ포트 / Σ벤치."""
    b = b.reindex(r.index)
    up, down = b > 0, b < 0
    return float(r[up].sum() / b[up].sum()), float(r[down].sum() / b[down].sum())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 Y — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    trad = _pkl("tradable"); sessions = sorted(d for d in trad["session"].unique() if d < HOLDOUT_START)
    end_moment = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    floor = float(store.config("selector.risk_floor_percentile", as_of=end_moment))
    ranker = _scores(store, "ranker", sessions).ewm(span=SPAN).mean(); risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions); ret = (wide.shift(-2) / wide.shift(-1) - 1.0); ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions); idx_ret = (idx.shift(-2) / idx.shift(-1) - 1.0)
    caps = _caps(store, sessions)
    fl = store.get("float_ratio", as_of=datetime.now(UTC), lookback=30)  # invariant-allow: wallclock — 참조 데이터, 최초 관측 소급(시행 P 와 같다)
    fl = fl.sort_values("observed_at").groupby("entity_id").tail(1).set_index("entity_id")["float_ratio"]
    members = k200_members(store, sessions)
    # 노출 배수 — 국면 판정은 400일 분포가 필요해 지수는 START 부터 읽는다(시행 R·V 와 같다).
    params = ExposureParams.from_store(store, as_of=datetime.combine(sessions[-1], time(23), tzinfo=UTC))
    closes = index_of(store, trading_days(Market.KR, START, sessions[-1]), "KR", INDEX_ID)
    regime, _states = scales_for(closes, sessions, CRISIS_FLOOR, params)
    scale = dict(zip(sessions, regime, strict=True))
    print(f"세션 {len(sessions)} · 유동비율 {len(fl)}종목 · 위험 하한 {floor:.2f} · 국면 배수 {params.regime_scale} "
          f"· 배수<1 세션 {sum(1 for v in regime if v < 1.0)}", flush=True)

    rows, series = {}, {}
    for name, (k200_only, by_float, exposed) in VARIANTS.items():
        held: list[str] = []; prev = None; prev_s = 1.0; out, turn, effs, scales = {}, {}, [], []
        for day in sessions:
            if day not in ranker.index or day not in ret.index:
                continue
            f = ranker.loc[day].dropna(); ok = set(trad[trad["session"] == day]["entity_id"])
            if k200_only:
                ok &= members[day]
            f = f[f.index.isin(ok)]
            r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
            if not r.dropna().empty:
                f = f[r >= r.quantile(floor)]
            if f.empty:
                continue
            held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
            if by_float and day in caps.index:
                cap = caps.loc[day].reindex(held).dropna() * fl.reindex(held).fillna(fl.median())
                w = capped_cap_weights(cap.dropna(), CAP_LIMIT) if len(cap.dropna()) >= N // 2 else pd.Series(1.0 / len(held), index=held)
                w = w.reindex(held).fillna(0.0); w = w / w.sum()
            else:
                w = pd.Series(1.0 / len(held), index=held)
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
            s = scale[day] if exposed else 1.0
            out[day] = s * float((w * dr).sum() - ONE_WAY_COST * t) - abs(s - prev_s) * ONE_WAY_COST
            turn[day] = t; effs.append(1.0 / float((w * w).sum())); scales.append(s); prev_s = s
            drift = w * (1 + dr); prev = drift / drift.sum() if drift.sum() > 0 else w
        sr = pd.Series(out).sort_index(); b = idx_ret.reindex(sr.index).fillna(0.0)
        m = metrics(sr, b)
        m["turn"] = float(pd.Series(turn).mean() * ANN); m["effn"] = float(np.mean(effs)); m["avg_scale"] = float(np.mean(scales))
        m["excess"] = float((sr - b).mean() * ANN)
        m["up"], m["down"] = capture(sr, b); m["asym"] = m["up"] - m["down"]
        half = len(sr) // 2
        m["ir_1h"] = metrics(sr.iloc[:half], b.iloc[:half])["ir"]; m["ir_2h"] = metrics(sr.iloc[half:], b.iloc[half:])["ir"]
        rows[name] = m; series[name] = sr

    bench = idx_ret.reindex(series["Y0"].index).fillna(0.0)
    bnav = (1 + bench).cumprod(); bench_mdd = float((bnav / bnav.cummax() - 1).min())
    lines = [
        f"K200 같은 창: 연 {bench.mean() * ANN:+.1%} · MDD {bench_mdd:.1%} · 상승일 {int((bench > 0).sum())} · 하락일 {int((bench < 0).sum())}",
        "",
        "| 변형 | 연수익 | 샤프 | MDD | β | IR(K200) | 대K200 초과 | 상승 추종 | 하락 추종 | 비대칭 | 연회전 | 유효N | 평균 배수 | IR 전/후 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v, m in rows.items():
        lines.append(f"| {v} | {m['ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['beta']:.2f} | {m['ir']:+.2f} | "
                     f"{m['excess']:+.1%} | {m['up']:.2f} | {m['down']:.2f} | {m['asym']:+.3f} | {m['turn']:.1f} | "
                     f"{m['effn']:.1f} | {m['avg_scale']:.2f} | {m['ir_1h']:+.2f}/{m['ir_2h']:+.2f} |")
    lines.append("")
    base = series["Y0"]; passed = []
    for v in VARIANTS:
        if v == "Y0":
            continue
        m = rows[v]; d = (series[v] - base).dropna(); t = float(ic_module.newey_west_t(d, lag=4))
        c = (m["asym"] >= ASYM_GATE, m["up"] >= UP_GATE, m["mdd"] >= bench_mdd - MDD_SLACK, t >= T_GATE)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{v}: ①비대칭 {m['asym']:+.3f} {mark(c[0])} · ②상승 {m['up']:.2f} {mark(c[1])} · "
                     f"③MDD {m['mdd']:.1%} {mark(c[2])} · ④Δ연 {d.mean() * ANN:+.1%} NW t {t:+.2f} {mark(c[3])} "
                     f"→ {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["ir"], v))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "market-beta-selection-2026-09:Y", "valid_from": now, "observed_at": now, "source": "trial_market_beta",
            "market": "KR", "family": "selection", "n_trials": 1, "protocol_hash": digest,
            "detail": (f"{verdict} | " + " | ".join(lines[4:]))[:900],
        }], ingest_run_id=f"trial-market-beta-Y-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/Y · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
