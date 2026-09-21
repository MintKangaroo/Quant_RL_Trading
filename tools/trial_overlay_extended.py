"""시행 AE — 헤지·변동성 오버레이를 두 국면에서 다시 잰다. docs/protocols/beta-overlay-extended-2026-09.md.

    .venv/bin/python tools/trial_overlay_extended.py [--save]

오버레이 규칙은 원 시행(`trial_overlay.overlays`)을 **그대로** 쓴다. 바뀐 것은 창(2022-07~2026-06)과 대리
(지금 실제로 도는 것 — 랭커 EMA5·완충 3N·N=24 동일가중·위험 하한, 노출 축 끔)뿐이다.
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

import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_beta_megacap import BOX_END, CACHE, JUDGE_END, JUDGE_START  # noqa: E402
from tools.trial_overlay import MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics, overlays  # noqa: E402
from tools.trial_selection_ranker import _scores  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/beta-overlay-extended-2026-09.md")
N, EXIT_MULT, SPAN = 24, 3, 5
GATE_SHARPE, GATE_MDD_RATIO, GATE_REGIME = 0.2, 0.8, -0.1


def proxy(store: Store) -> tuple[pd.Series, pd.Series]:
    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad_frame = trad_frame[(trad_frame["session"] >= JUDGE_START) & (trad_frame["session"] <= JUDGE_END)]
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    sessions = sorted(trad)
    floor = float(store.config("selector.risk_floor_percentile",
                               as_of=datetime.combine(sessions[-1], time(16), tzinfo=UTC)))
    ranker = _scores(store, "ranker", sessions).ewm(span=SPAN).mean()
    risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    held: list[str] = []
    prev = None
    out = {}
    for day in sessions:
        if day not in ranker.index or day not in ret.index:
            continue
        f = ranker.loc[day].dropna()
        f = f[f.index.isin(trad[day])]
        r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
        if not r.dropna().empty:
            f = f[r >= r.quantile(floor)]
        if f.empty:
            continue
        held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    s = pd.Series(out).sort_index()
    return s, bench.reindex(s.index)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AE — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    s, b = proxy(store)
    out = overlays(s, b)
    rows = {}
    for name, series in out.items():
        series = series.dropna()
        m = metrics(series, b)
        for label, part in (("box", series[series.index <= BOX_END]), ("rally", series[series.index > BOX_END])):
            pm = metrics(part, b)
            m[f"{label}_sharpe"], m[f"{label}_mdd"], m[f"{label}_ann"] = pm["sharpe"], pm["mdd"], pm["ann"]
        rows[name] = m
    base = rows["BASE"]
    lines = [f"판정 {len(s)}세션 {s.index.min()}~{s.index.max()}", "",
             "| 변형 | 연수익 | 변동성 | 샤프 | MDD | β | IR(K200) | 박스 연/샤프/MDD | 급등 연/샤프/MDD |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, m in rows.items():
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['vol']:.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['beta']:.2f} | "
                     f"{m['ir']:+.2f} | {m['box_ann']:+.1%} / {m['box_sharpe']:+.2f} / {m['box_mdd']:.1%} | "
                     f"{m['rally_ann']:+.1%} / {m['rally_sharpe']:+.2f} / {m['rally_mdd']:.1%} |")
    lines.append("")
    passed = []
    for name, m in rows.items():
        if name == "BASE":
            continue
        c = (m["sharpe"] >= base["sharpe"] + GATE_SHARPE,
             abs(m["mdd"]) <= abs(base["mdd"]) * GATE_MDD_RATIO,
             m["box_sharpe"] >= base["box_sharpe"] + GATE_REGIME and m["rally_sharpe"] >= base["rally_sharpe"] + GATE_REGIME,
             m["box_mdd"] > base["box_mdd"])
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①샤프 +0.2 {mark(c[0])} · ②MDD 20% 얕음 {mark(c[1])} · ③두 국면 샤프 {mark(c[2])} · "
                     f"④박스 MDD {m['box_mdd']:.1%} vs {base['box_mdd']:.1%} {mark(c[3])} → {'파일럿 후보' if all(c) else '기록만'}")
        if all(c):
            passed.append((m["sharpe"], name))
    verdict = f"파일럿 후보 {max(passed)[1]}" if passed else "기록만 — 채택 없음"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "beta-overlay-extended-2026-09:AE", "valid_from": now, "observed_at": now,
            "source": "trial_overlay_extended", "market": "KR", "family": "portfolio", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[4:]))[:900],
        }], ingest_run_id=f"trial-overlay-extended-AE-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: portfolio/AE · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
