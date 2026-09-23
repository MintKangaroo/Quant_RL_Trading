"""시행 AN — 맨 위 24자리를 직접 맞히는 학습(LambdaRank, NDCG@24). docs/protocols/lambdarank-top24-2026-10.md.

    .venv/bin/python tools/trial_lambdarank.py --precheck     # seed 0 · 첫 5블록 · 상위 24 겹침만(수익 계산 없음)
    .venv/bin/python tools/trial_lambdarank.py [--save]       # 본 측정 (2026-10-17 이후)
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_ranker_ensemble import FEATS  # noqa: E402
from tools.trial_ranker_kit import (  # noqa: E402
    PURGE,
    blocks,
    judge_panel,
    market_data,
    nw_t,
    portfolio,
    record,
    summarize,
    walk,
)

PROTOCOL = Path("docs/protocols/lambdarank-top24-2026-10.md")
MEASURE_FROM = date(2026, 10, 17)
SEEDS = (0, 1, 2)
TOP = 24
OVERLAP_STOP = 0.90
GATE_MEAN, GATE_REGIME, GATE_MDD, GATE_TURN = 0.02, -0.01, 0.02, 1.2


def fit_rank(train: pd.DataFrame, seed: int):
    """쿼리 = 세션, 라벨 = 세션 안 y5 순위 십분위(0~9). 나머지는 시행 L 하이퍼파라미터."""
    import lightgbm as lgb
    train = train.sort_values("session")
    label = train.groupby("session")["y5"].rank(pct=True, method="first").mul(10).clip(upper=9.999).astype(int)
    groups = train.groupby("session", sort=True).size().to_numpy()
    params = dict(objective="lambdarank", lambdarank_truncation_level=TOP, eval_at=[TOP], label_gain=list(range(10)),
                  num_leaves=7, min_data_in_leaf=2000, learning_rate=0.03, bagging_fraction=0.8, bagging_freq=1,
                  feature_fraction=1.0, lambda_l2=1.0, verbose=-1, seed=seed, num_threads=6)
    ds = lgb.Dataset(train[FEATS].to_numpy(np.float32), label.to_numpy(), group=groups)
    return lgb.train(params, ds, num_boost_round=300)


def walk_rank(panel: pd.DataFrame, sessions: list, bl: list, seed: int) -> pd.DataFrame:
    parts = []
    for first, last in bl:
        train = panel[(panel["session"] <= sessions[first - PURGE - 1]) & panel["y5"].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])].copy()
        if train.empty or test.empty:
            continue
        test["pred"] = fit_rank(train, seed).predict(test[FEATS].to_numpy(np.float32))
        parts.append(test[["entity_id", "session", "pred"]])
        print(f"  lambdarank seed{seed} 블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행", flush=True)
    return pd.concat(parts, ignore_index=True)


def top_overlap(a: pd.DataFrame, b: pd.DataFrame) -> float:
    m = a.merge(b, on=["entity_id", "session"], suffixes=("_a", "_b"))
    vals = []
    for _, g in m.groupby("session"):
        sa, sb = set(g.nlargest(TOP, "pred_a")["entity_id"]), set(g.nlargest(TOP, "pred_b")["entity_id"])
        vals.append(len(sa & sb) / TOP)
    return float(np.mean(vals))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    panel, sessions = judge_panel()
    bl = blocks(sessions)
    if args.precheck:
        bl = bl[:5]
        ctrl = walk(panel, sessions, FEATS, "y5", bl, seeds=(0,), label="대조 seed0")
        treat = walk_rank(panel, sessions, bl, 0)
        ov = top_overlap(treat, ctrl)
        print(f"등록 전 점검: 상위 {TOP} 겹침 평균 {ov:.0%} (첫 5블록, seed 0) → "
              f"{'측정하지 않는다(≥90%)' if ov > OVERLAP_STOP else '측정 진행'}", flush=True)
        return 0
    if LiveClock().now().date() < MEASURE_FROM:
        print(f"본 측정은 {MEASURE_FROM} 이후. 지금은 --precheck 만.", flush=True)
        return 2
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AN — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    ret, bench, trad = market_data(store, sessions)
    y = panel[["entity_id", "session", "y5"]]
    rows, series, overlaps = {}, {}, []
    for s in SEEDS:
        c = walk(panel, sessions, FEATS, "y5", bl, seeds=(s,), label=f"대조 seed{s}")
        t = walk_rank(panel, sessions, bl, s)
        overlaps.append(top_overlap(t, c))
        for name, pred in ((f"ctrl{s}", c), (f"rank{s}", t)):
            daily, extra = portfolio(pred, ret, trad)
            rows[name] = {**summarize(daily, bench, pred.merge(y, on=["entity_id", "session"])), **extra}
            series[name] = daily

    def mean(prefix: str, key: str) -> float:
        return float(np.mean([rows[f"{prefix}{s}"][key] for s in SEEDS]))

    lines = ["| 모델 | 연수익 | 박스 | 급등 | 샤프 | MDD | IC | 회전 |", "|---|---|---|---|---|---|---|---|"]
    for n, m in rows.items():
        lines.append(f"| {n} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['ic']:+.4f} | {m['turn']:.1f} |")
    wins = sum(rows[f"rank{s}"]["ann"] > rows[f"ctrl{s}"]["ann"] for s in SEEDS)
    t_avg = nw_t(sum(series[f"rank{s}"] for s in SEEDS) / 3, sum(series[f"ctrl{s}"] for s in SEEDS) / 3)
    k = (mean("rank", "ann") >= mean("ctrl", "ann") + GATE_MEAN, wins == len(SEEDS),
         mean("rank", "box_ann") >= mean("ctrl", "box_ann") + GATE_REGIME and mean("rank", "rally_ann") >= mean("ctrl", "rally_ann") + GATE_REGIME,
         mean("rank", "mdd") >= mean("ctrl", "mdd") - GATE_MDD, mean("rank", "turn") <= mean("ctrl", "turn") * GATE_TURN)
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    lines += ["", f"기록: 상위 {TOP} 겹침(시드별) {' / '.join(f'{o:.0%}' for o in overlaps)} · 시드 평균 일별 차 NW t {t_avg:+.2f}",
              f"AN: ①평균 {mean('rank', 'ann') - mean('ctrl', 'ann'):+.1%}p {mark(k[0])} · ②{wins}/3 {mark(k[1])} · ③국면 {mark(k[2])} · "
              f"④MDD {mark(k[3])} · ⑤회전 {mark(k[4])} → {'통과' if all(k) else '탈락'}"]
    verdict = "채택" if all(k) else "기각 — 회귀 목적 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="lambdarank-top24-2026-10:AN", source="trial_lambdarank", family="ranker",
               digest=digest, verdict=verdict, lines=lines[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
