"""시행 AP — 시간에 걸친 모델 불일치로 종목 수를 정한다. docs/protocols/temporal-uncertainty-breadth-2026-10.md.

    .venv/bin/python tools/trial_temporal_uncertainty.py --precheck   # 규칙이 켜지는 날 비율만(수익 계산 없음)
    .venv/bin/python tools/trial_temporal_uncertainty.py [--save]     # 본 측정 (2026-10-18 이후)

블록 b 의 판정 세션마다 M_b · M_{b−1} · M_{b−2} 로 예측 → 세션 안 순위 백분위 → 종목별 표준편차 = unc. 점수는 M_b.
규칙: gap(24위−72위 백분위, EMA5 점수 기준) < unc(1~72위 중앙값) 이면 72, 아니면 24 (시행 AL 과 같은 꼴, 자만 바꿨다).
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
from tools.trial_ranker_ensemble import BOX_END, FEATS  # noqa: E402
from tools.trial_ranker_kit import (  # noqa: E402
    PURGE,
    SPAN,
    blocks,
    fit,
    judge_panel,
    market_data,
    nw_t,
    portfolio,
    record,
    summarize,
)

PROTOCOL = Path("docs/protocols/temporal-uncertainty-breadth-2026-10.md")
MEASURE_FROM = date(2026, 10, 18)
N, WIDE, LAGS = 24, 72, 3
FIRE_MIN, FIRE_MAX = 0.10, 0.90
GATE_ANN, GATE_T, GATE_MDD = 0.01, 2.0, 0.03


def predictions(panel: pd.DataFrame, sessions: list, bl: list) -> pd.DataFrame:
    """(entity_id, session, pred, unc). 첫 LAGS−1 블록은 모델이 모자라 뺀다."""
    models, parts = [], []
    for first, last in bl:
        train = panel[(panel["session"] <= sessions[first - PURGE - 1]) & panel["y5"].notna()]
        models = (models + [fit(train[FEATS].to_numpy(np.float32), train["y5"].to_numpy(np.float32), seed=0)])[-LAGS:]
        print(f"  블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행 · 모델 {len(models)}", flush=True)
        if len(models) < LAGS:
            continue
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])].copy()
        X = test[FEATS].to_numpy(np.float32)
        for i, m in enumerate(models):
            test[f"p{i}"] = m.predict(X)
        test["pred"] = test[f"p{LAGS - 1}"]
        pct = test.groupby("session")[[f"p{i}" for i in range(LAGS)]].rank(pct=True)
        test["unc"] = pct.std(axis=1, ddof=1)
        parts.append(test[["entity_id", "session", "pred", "unc"]])
    return pd.concat(parts, ignore_index=True)


def rule(pred: pd.DataFrame, trad: dict | None = None):
    """n_of(day, row) 와 결정 기록 dict. row 는 portfolio 가 EMA5 로 평활한 그날 점수."""
    unc = pred.pivot_table(index="session", columns="entity_id", values="unc").sort_index()
    decisions: dict = {}

    def n_of(day, row: pd.Series) -> int:
        order = row.sort_values(ascending=False)
        if len(order) < WIDE or day not in unc.index:
            decisions[day] = N
            return N
        pct = order.rank(pct=True)
        gap = float(pct.iloc[N - 1] - pct.iloc[WIDE - 1])
        u = float(unc.loc[day].reindex(order.index[:WIDE]).median())
        n = WIDE if gap < u else N
        decisions[day] = (n, gap, u)
        return n
    return n_of, decisions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    panel, sessions = judge_panel()
    pred = predictions(panel, sessions, blocks(sessions))

    if args.precheck:
        n_of, dec = rule(pred)
        wide = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
        for day in wide.index:
            n_of(day, wide.loc[day].dropna())
        fired = pd.Series({d: (v[0] if isinstance(v, tuple) else v) > N for d, v in dec.items()})
        ratio = pd.Series({d: v[1] / v[2] for d, v in dec.items() if isinstance(v, tuple) and v[2] > 0})
        ok = FIRE_MIN <= fired.mean() <= FIRE_MAX
        print(f"등록 전 점검: 규칙이 켜진 날 {fired.mean():.0%} (박스 {fired[fired.index <= BOX_END].mean():.0%} · 급등 "
              f"{fired[fired.index > BOX_END].mean():.0%}) · gap/unc 5%/50%/95% {ratio.quantile(.05):.2f}/{ratio.quantile(.5):.2f}/"
              f"{ratio.quantile(.95):.2f} → {'측정 진행' if ok else '측정하지 않는다(10~90% 밖)'}", flush=True)
        return 0
    if LiveClock().now().date() < MEASURE_FROM:
        print(f"본 측정은 {MEASURE_FROM} 이후. 지금은 --precheck 만.", flush=True)
        return 2
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AP — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    ret, bench, trad = market_data(store, sessions)
    y = panel[["entity_id", "session", "y5"]]
    n_of, dec = rule(pred)
    rows, series = {}, {}
    for name, fn in (("L0", None), ("L72", lambda d, r: WIDE), ("L1", n_of)):
        daily, extra = portfolio(pred, ret, trad, n_of=fn)
        rows[name] = {**summarize(daily, bench, pred.merge(y, on=["entity_id", "session"]) if name == "L0" else None), **extra}
        series[name] = daily
    l0, l72, l1 = rows["L0"], rows["L72"], rows["L1"]
    t = nw_t(series["L1"], series["L0"])
    k = (l1["box_ann"] >= l0["box_ann"] + GATE_ANN and l1["rally_ann"] >= l0["rally_ann"] + GATE_ANN,
         l1["ann"] >= l72["ann"] + GATE_ANN, t >= GATE_T, l1["mdd"] >= max(l0["mdd"], l72["mdd"]) - GATE_MDD, l1["turn"] <= l0["turn"])
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    lines = ["| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | 회전 | 평균 N |", "|---|---|---|---|---|---|---|---|"]
    for n, m in rows.items():
        lines.append(f"| {n} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['turn']:.1f} | {m['n_mean']:.0f} |")
    lines += ["", f"기록: 넓게 든 세션 {l1['wide_share']:.0%}",
              f"L1: ①두 국면 +1%p {mark(k[0])} · ②vs L72 {l1['ann'] - l72['ann']:+.1%} {mark(k[1])} · ③NW t {t:+.2f} {mark(k[2])} · "
              f"④MDD {mark(k[3])} · ⑤회전 {mark(k[4])} → {'통과' if all(k) else '탈락'}"]
    verdict = "채택 L1" if all(k) else "기각 — 24 고정 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="temporal-uncertainty-breadth-2026-10:AP", source="trial_temporal_uncertainty",
               family="selection", digest=digest, verdict=verdict, lines=lines[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
