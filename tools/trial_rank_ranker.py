"""시행 L — 순위 목적 GBM 랭커. docs/protocols/rank-objective-ranker-2026-09.md 대로 한 번 잰다.

    .venv/bin/python tools/trial_rank_ranker.py [--save]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_pooled import FEATS, TOP_N, _nw, daily_ic, fit_gbm, load_kr, load_us, top_excess  # noqa: E402
from tools.trial_pooled_deep import blocks_for  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402

PROTOCOL = Path("docs/protocols/rank-objective-ranker-2026-09.md")
T_GATE = 2.0; WORST_GATE = -0.03


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true", help="research_trials 에 기록(시행 소진)")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 L — {PROTOCOL} (해시 {digest}) ===", flush=True)
    kr, _ = load_kr(); us = load_us()
    kr = rank_gauss(kr, FEATS + ["target"]); us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"] = 0.0; us["is_us"] = 1.0
    X = FEATS + ["is_us"]
    sessions = sorted(kr["session"].unique()); blocks = blocks_for(sessions)
    print(f"국장 {len(kr):,}행 · 미장 {len(us):,}행 · 판정 블록 {len(blocks)}", flush=True)
    parts_kr, parts_us, gains = [], [], []
    for i, (train_end, ps, pe) in enumerate(blocks, 1):
        train = pd.concat([kr[kr["session"] <= train_end], us[us["session"] <= train_end]], ignore_index=True)
        m = fit_gbm(train[X].to_numpy(np.float32), train["target"].to_numpy(np.float32))
        gains.append(dict(zip(X, m.feature_importance(importance_type="gain"))))
        for src, sink in ((kr, parts_kr), (us, parts_us)):
            te = src[(src["session"] >= ps) & (src["session"] <= pe)].copy()
            te["pred"] = m.predict(te[X].to_numpy(np.float32))
            sink.append(te[["entity_id", "session", "target", "fundamental", "pred"]])
        print(f"블록 {i}/{len(blocks)} 학습 ~{train_end} ({len(train):,}행) → 판정 {ps}~{pe}", flush=True)
    d = pd.concat(parts_kr, ignore_index=True); du = pd.concat(parts_us, ignore_index=True)
    ic_p, ic_f = daily_ic(d, "pred"), daily_ic(d, "fundamental"); c = ic_p.index.intersection(ic_f.index)
    delta = ic_p.loc[c] - ic_f.loc[c]
    bd = []
    for (_e, ps, pe) in blocks:
        days = [x for x in delta.index if ps <= x <= pe]; bd.append(float(delta.loc[days].mean()) if days else np.nan)
    tp, tf = float(top_excess(d, "pred").mean()), float(top_excess(d, "fundamental").mean())
    icu_p, icu_f = daily_ic(du, "pred"), daily_ic(du, "fundamental"); cu = icu_p.index.intersection(icu_f.index)
    delta_us = icu_p.loc[cu] - icu_f.loc[cu]
    gain = pd.DataFrame(gains).mean().sort_values(ascending=False)
    c1 = _nw(delta, 4) >= T_GATE; c2 = tp >= tf; c3 = float(np.nanmin(bd)) >= WORST_GATE; c4 = float(delta_us.mean()) >= 0
    verdict = "채택" if (c1 and c2 and c3 and c4) else "기각"
    lines = [
        f"국장 판정 {len(c)}세션 · 랭커 IC {ic_p.mean():+.4f} (t {_nw(ic_p,4):+.2f}) 대 fundamental {ic_f.mean():+.4f} (t {_nw(ic_f,4):+.2f})",
        f"① ΔIC {delta.mean():+.4f} · NW t {_nw(delta,4):+.2f} {'○' if c1 else '×'}",
        f"② 상위{TOP_N} h5 z-수익 랭커 {tp:+.4f} 대 대조 {tf:+.4f} {'○' if c2 else '×'}",
        f"③ 블록별 ΔIC {' '.join(f'{x:+.3f}' for x in bd)} · 최악 {np.nanmin(bd):+.3f} {'○' if c3 else '×'}",
        f"④ 미장 판정 {len(cu)}세션 · 랭커 {icu_p.mean():+.4f} 대 fund {icu_f.mean():+.4f} · ΔIC {delta_us.mean():+.4f} (NW t {_nw(delta_us,4):+.2f}) {'○' if c4 else '×'}",
        "피처 gain: " + " · ".join(f"{k} {v/gain.sum():.0%}" for k, v in gain.items()),
        f"판정: {verdict}",
    ]
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        store = Store(root=Path(args.root)); now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "rank-objective-ranker-2026-09:L", "valid_from": now, "observed_at": now,
            "source": "trial_rank_ranker", "market": "KR", "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": " | ".join(lines)[:900],
        }], ingest_run_id=f"trial-ranker-L-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/L · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
