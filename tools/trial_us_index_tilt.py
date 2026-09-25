"""미장 지수 이기기 — AX 신호로 기울인 지수 · AY 지수 + 국면 노출. docs/protocols/us-index-tilt-2026-09.md.

    .venv/bin/python tools/trial_us_index_tilt.py --trial AX --precheck   # 액티브 셰어만(수익 없음)
    .venv/bin/python tools/trial_us_index_tilt.py --trial AX|AY [--save]

점수는 `tools/trial_us_kit` 캐시(루프 GBM 3시드 → 실전 척도 → AT M1 합성). 시총은 창고 market_stats(시행 AG 와 같은 경로).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import UTC, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import ANN, metrics  # noqa: E402
from tools.trial_ranker_kit import record  # noqa: E402
from tools.trial_selection_ranker import capped_cap_weights  # noqa: E402
from tools.trial_us_index_minus_losers import top_caps  # noqa: E402
from tools.trial_us_kit import build, market, scores, spx_regime  # noqa: E402
from tools.trial_us_missing_fundamental import BOX_END, JUDGE_END, JUDGE_START, SEEDS  # noqa: E402
from tools.trial_us_selection import CRISIS_FLOOR, exposure_path  # noqa: E402

PROTOCOL = Path("docs/protocols/us-index-tilt-2026-09.md")
LOG_AX = Path("logs/trial-us-index-tilt-AX.log")
WIDE, CUT, REENTRY, CAP_LIMIT, EVERY = 500, 0.10, 0.10, 0.10, 10
LAMBDAS = {"X2": 0.25, "X3": 0.50}
PRECHECK_MIN_ACTIVE = 0.05
IR_GATE, MDD_SLACK = 0.30, 0.02
AY_SHARPE, AY_MDD, AY_ANN = 0.10, 0.03, -0.02


def zscore(row: pd.Series) -> pd.Series:
    """시총 상위 500 안 점수 → rank-gauss. 점수 없는 종목(거래대금 1,000 밖)은 0(중립) — 기울이지 않는다."""
    r = row.dropna()
    z = pd.Series(norm.ppf((r.rank() - 0.5) / len(r)), index=r.index) if len(r) > 1 else pd.Series(dtype=float)
    return z.reindex(row.index).fillna(0.0)


def targets(variant: str, cap: pd.Series, score: pd.Series, excluded: set[str]) -> tuple[pd.Series, set[str]]:
    """한 재조정일의 목표 비중과 (G1 의) 제외 집합."""
    if variant == "X0":
        return capped_cap_weights(cap, CAP_LIMIT), excluded
    s = score.reindex(cap.index)
    if variant == "X1":
        pct = s.dropna().rank(pct=True)
        excluded = {e for e in excluded if e in pct.index and pct[e] < CUT + REENTRY} | set(pct[pct < CUT].index)
        keep = cap[[e for e in cap.index if e not in excluded]]
        return capped_cap_weights(keep, CAP_LIMIT), excluded
    lam = LAMBDAS[variant]
    tilted = cap * np.exp(lam * zscore(s))
    return capped_cap_weights(tilted, CAP_LIMIT), excluded


def run(variant: str, caps: pd.DataFrame, ranks: pd.DataFrame, score: pd.DataFrame, ret: pd.DataFrame, cost: float,
        scale: pd.Series | None = None) -> tuple[pd.Series, dict]:
    prev, excluded, last_k = None, set(), None
    out, turns, active = {}, [], []
    days = [d for d in score.index if d in ret.index and d in caps.index]
    for i, day in enumerate(days):
        k = float(scale.get(day, 1.0)) if scale is not None else 1.0
        if prev is None or i % EVERY == 0:
            wide = ranks.loc[day][ranks.loc[day] <= WIDE].index
            cap = caps.loc[day].reindex(wide).dropna()
            w, excluded = targets(variant, cap, score.loc[day], excluded)
            base = capped_cap_weights(cap, CAP_LIMIT)
            active.append(0.5 * float(w.subtract(base, fill_value=0.0).abs().sum()))
            w = w * k
        elif k != last_k:
            w = prev / prev.sum() * k if prev.sum() > 0 else prev
        else:
            w = prev
        last_k = k
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else float(w.sum())
        out[day] = float((w * dr).sum() - cost * t)
        turns.append(t)
        d = w * (1 + dr)
        total = 1.0 - float(w.sum()) + float(d.sum())
        prev = d / total if total > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "active": float(np.mean(active))}


def summarize(daily: pd.Series, bench: pd.Series, extra: dict) -> dict:
    b = bench.reindex(daily.index).fillna(0.0)
    m = metrics(daily, b)
    for label, part in (("box", daily.index <= BOX_END), ("rally", daily.index > BOX_END)):
        m[f"{label}_ann"] = float(daily[part].mean() * ANN)
        m[f"{label}_excess"] = float((daily[part] - b[part]).mean() * ANN)
    return {**m, **extra}


def ax_adopted() -> str:
    text = LOG_AX.read_text() if LOG_AX.exists() else ""
    if "판정:" not in text:
        raise SystemExit("AX 판정 전 — 기다린다")
    m = re.findall(r"^판정: 채택 (\S+)", text, flags=re.M)
    return m[-1] if m else "X1"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", required=True, choices=("AX", "AY"))
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    store = Store(root=Path("data"))
    cost = float(store.config("accounting.fee_us", as_of=datetime.now(UTC)))  # invariant-allow: wallclock — 설정 현행값
    build(store)
    ret, bench = market()
    now = datetime.combine(JUDGE_END, time(23), tzinfo=UTC)
    caps = top_caps(store, now, (JUDGE_END - JUDGE_START).days + 60)
    caps = caps[(caps.index >= JUDGE_START) & (caps.index <= JUDGE_END)]
    ranks = caps.rank(axis=1, ascending=False)

    if args.trial == "AX":
        variants: dict[str, dict] = {v: {} for v in ("X0", "X1", "X2", "X3")}
        control = "X1"
    else:
        base = ax_adopted()
        sessions = sorted(scores(SEEDS[0]).index)
        scale = exposure_path(spx_regime(store, sessions, CRISIS_FLOOR))
        variants = {"Y0": {"variant": base}, "Y1": {"variant": base, "scale": scale}}
        control = "Y0"
    print(f"=== 시행 {args.trial} — {PROTOCOL} (해시 {digest}) · 비용 편도 {cost:.2%} ===", flush=True)

    res: dict[str, dict[int, dict]] = {v: {} for v in variants}
    seeds = SEEDS[:1] if args.precheck else SEEDS
    for s in seeds:
        sc = scores(s)
        if args.precheck:
            sc = sc.iloc[:200]
        for v, kw in variants.items():
            name = kw.get("variant", v)
            daily, extra = run(name, caps, ranks, sc, ret, cost, kw.get("scale"))
            res[v][s] = summarize(daily, bench, extra) if not args.precheck else extra
    if args.precheck:
        for v in ("X2", "X3"):
            a = res[v][SEEDS[0]]["active"]
            print(f"등록 전 점검: {v} 액티브 셰어(시총가중 대비) 평균 {a:.1%} · 첫 200세션 seed 0 → "
                  f"{'측정 진행' if a >= PRECHECK_MIN_ACTIVE else '측정하지 않는다(5% 미만)'}", flush=True)
        return 0

    def avg(v: str, k: str) -> float:
        return float(np.mean([res[v][s][k] for s in SEEDS]))

    lines = ["| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR(SPY) | 박스 초과 | 급등 초과 | 액티브 셰어 | 회전 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in variants:
        lines.append(f"| {v} | {avg(v, 'ann'):+.1%} | {avg(v, 'box_ann'):+.1%} | {avg(v, 'rally_ann'):+.1%} | {avg(v, 'sharpe'):+.2f} | "
                     f"{avg(v, 'mdd'):.1%} | {avg(v, 'beta'):.2f} | {avg(v, 'ir'):+.2f} | {avg(v, 'box_excess'):+.1%} | "
                     f"{avg(v, 'rally_excess'):+.1%} | {avg(v, 'active'):.0%} | {avg(v, 'turn'):.1f} |")
    days = [d for d in sorted(scores(SEEDS[0]).index) if d in caps.index]
    spy = summarize(bench.reindex(pd.Index(days)).dropna(), bench, {})
    lines += ["", f"SPY 같은 창: 연 {spy['ann']:+.1%} · 샤프 {spy['sharpe']:+.2f} · MDD {spy['mdd']:.1%}", ""]
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    passed = []
    for v in variants:
        if v == control or (args.trial == "AX" and v == "X0"):
            continue
        if args.trial == "AX":
            wins = sum(res[v][s]["ann"] > res[control][s]["ann"] for s in SEEDS)
            c = (avg(v, "ir") >= IR_GATE, avg(v, "box_excess") >= 0 and avg(v, "rally_excess") >= 0,
                 wins == len(SEEDS), avg(v, "mdd") >= avg(control, "mdd") - MDD_SLACK)
            lines.append(f"{v}: ①IR(SPY) {avg(v, 'ir'):+.2f} (≥0.30) {mark(c[0])} · ②두 국면 초과 {avg(v, 'box_excess'):+.1%}/{avg(v, 'rally_excess'):+.1%} {mark(c[1])} · "
                         f"③X1 대비 {wins}/3 {mark(c[2])} · ④MDD {avg(v, 'mdd'):.1%} vs {avg(control, 'mdd'):.1%} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
            score_key = avg(v, "ir")
        else:
            wins = sum(res[v][s]["sharpe"] > res[control][s]["sharpe"] for s in SEEDS)
            c = (avg(v, "sharpe") >= avg(control, "sharpe") + AY_SHARPE, avg(v, "mdd") >= avg(control, "mdd") + AY_MDD,
                 avg(v, "ann") >= avg(control, "ann") + AY_ANN, wins == len(SEEDS))
            lines.append(f"{v}: ①샤프 {avg(v, 'sharpe'):+.2f} vs {avg(control, 'sharpe'):+.2f} (+0.10) {mark(c[0])} · ②MDD {avg(v, 'mdd'):.1%} vs {avg(control, 'mdd'):.1%} (+3%p) {mark(c[1])} · "
                         f"③연수익 {avg(v, 'ann') - avg(control, 'ann'):+.1%}p (≥ −2%p) {mark(c[2])} · ④샤프 {wins}/3 {mark(c[3])} → {'통과' if all(c) else '탈락'}")
            score_key = avg(v, "sharpe")
        if all(c):
            passed.append((score_key, v))
    verdict = f"채택 {max(passed)[1]}" if passed else f"기각 — {control} 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity=f"us-index-tilt-2026-09:{args.trial}", source="trial_us_index_tilt", family="selection",
               digest=digest, verdict=verdict, lines=lines[-4:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
