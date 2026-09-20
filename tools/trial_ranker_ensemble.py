"""시행 AB — 랭커 앙상블, 지평 3종의 순위 평균. docs/protocols/ranker-ensemble-horizons-2026-09.md.

    .venv/bin/python tools/trial_ranker_ensemble.py [--save] [--cache-dir data/_diag-long]

등록대로: 모델·하이퍼파라미터·워크포워드는 시행 L 과 같고 **바꾸는 것은 타깃 지평과 합치는 방식뿐**이다.
퍼지는 지평에 맞춰 키운다(h5=5 · h20=20 · h60=60) — 안 그러면 라벨이 겹쳐 긴 지평이 누수로 좋아 보인다.
판정 블록은 **세 지평이 똑같다**(가장 긴 퍼지 기준으로 잡는다). 학습 끝점만 지평마다 다르다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics  # noqa: E402
from tools.trial_pooled import fit_gbm  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/ranker-ensemble-horizons-2026-09.md")
FEATS = ["chart", "event", "flow", "fundamental", "regime", "risk"]
SOURCES = [("chart", "chart"), ("event", "event"), ("flow_kr", "flow"),
           ("fundamental", "fundamental"), ("regime", "regime"), ("risk", "risk")]
HORIZONS = (5, 20, 60)
#: 이름: 섞을 지평들. 순위 평균, 가중치는 적합하지 않는다(등록 문서).
VARIANTS: dict[str, tuple[int, ...]] = {
    "B0": (5,), "B1": (5, 20), "B2": (5, 20, 60), "B3": (20,), "B4": (60,),
}
JUDGE_START, JUDGE_END = date(2022, 7, 1), date(2026, 6, 30)
BOX_END = date(2024, 12, 31)
MIN_TRAIN, BLOCK = 150, 20
N, EXIT_MULT, SPAN = 24, 3, 5
GATE_ANN, GATE_T, GATE_MDD, GATE_ASYM = 0.01, 2.0, 0.03, -0.02


def load_panel(cache: Path) -> pd.DataFrame:
    """긴 패널을 **인덱스 정렬 concat** 으로 합친다.

    6종을 outer merge 로 잇던 첫 판은 3백만 행 × 6회에서 메모리를 다 먹고 멈췄다(2026-09-20).
    (entity_id, session) 을 인덱스로 세우고 한 번에 concat 하면 같은 결과를 훨씬 싸게 얻는다.
    값은 float32 로 내린다 — 정밀도는 rank-gauss 뒤에 어차피 순위만 남는다.
    """
    parts = []
    for name, col in SOURCES:
        f = pd.read_pickle(cache / f"scores-{name}-KR.pkl")  # invariant-allow: data-access — 작업 캐시
        f["session"] = pd.to_datetime(f["session"]).dt.date
        parts.append(f.set_index(["entity_id", "session"])["score"].astype(np.float32).rename(col))
    for horizon in HORIZONS:
        t = pd.read_pickle(cache / f"targets-KR-h{horizon}.pkl")  # invariant-allow: data-access — 작업 캐시
        t["session"] = pd.to_datetime(t["session"]).dt.date
        parts.append(t.set_index(["entity_id", "session"])["target"].astype(np.float32).rename(f"y{horizon}"))
    panel = pd.concat(parts, axis=1, join="outer").reset_index()
    panel["market"] = "KR"
    return panel


def blocks_for(sessions: list[date], purge_max: int) -> list[tuple[int, int]]:
    """판정 블록(시작·끝 인덱스). **세 지평이 같은 블록을 본다** — 가장 긴 퍼지 뒤에서 시작한다."""
    out: list[tuple[int, int]] = []
    start = MIN_TRAIN + purge_max
    while start + BLOCK <= len(sessions):
        out.append((start, start + BLOCK - 1))
        start += BLOCK
    return out


def walk_forward(panel: pd.DataFrame, sessions: list[date], horizon: int,
                 blocks: list[tuple[int, int]]) -> pd.DataFrame:
    """지평 하나의 워크포워드 예측. 학습은 블록 시작 − 퍼지(=지평)까지만 본다."""
    target = f"y{horizon}"
    out = []
    for first, last in blocks:
        train_end = sessions[first - horizon - 1]
        train = panel[(panel["session"] <= train_end) & panel[target].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])].copy()
        if train.empty or test.empty:
            continue
        model = fit_gbm(train[FEATS].to_numpy(np.float32), train[target].to_numpy(np.float32))
        test["pred"] = model.predict(test[FEATS].to_numpy(np.float32))
        out.append(test[["entity_id", "session", "pred"]])
        print(f"  h{horizon} 블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행", flush=True)
    return pd.concat(out, ignore_index=True)


def portfolio(score: pd.DataFrame, ret: pd.DataFrame, trad: dict[date, set[str]]) -> pd.Series:
    """점수 → 비용 후 일수익. EMA5 평활·완충 3N·동일가중 24 (시행 P·Y·Z 와 같다)."""
    wide = score.pivot_table(index="session", columns="entity_id", values="pred").sort_index()
    wide = wide.ewm(span=SPAN).mean()
    held: list[str] = []
    prev = None
    out = {}
    for day in wide.index:
        if day not in ret.index:
            continue
        row = wide.loc[day].dropna()
        ok = trad.get(day)
        if ok:
            row = row[row.index.isin(ok)]
        if row.empty:
            continue
        held = pick_mult(held, row.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        turn = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * turn)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index()


def summarize(r: pd.Series, b: pd.Series) -> dict[str, float]:
    m = metrics(r, b)
    m["up"], m["down"] = capture(r, b.reindex(r.index).fillna(0.0))
    m["asym"] = m["up"] - m["down"]
    m["excess"] = float((r - b.reindex(r.index).fillna(0.0)).mean() * ANN)
    return m


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/_diag-long"))
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — **배선 확인용, 수치는 안 찍는다**")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AB — {PROTOCOL} (해시 {digest}) ===", flush=True)

    panel = load_panel(args.cache_dir)
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    panel = rank_gauss(panel, FEATS + [f"y{h}" for h in HORIZONS])
    sessions = sorted(panel["session"].unique())
    blocks = blocks_for(sessions, max(HORIZONS))
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 판정 블록 {len(blocks)}", flush=True)

    if args.smoke:
        blocks = blocks[: args.smoke]
        preds = {h: walk_forward(panel, sessions, h, blocks) for h in HORIZONS}
        for h, f in preds.items():
            print(f"  h{h}: 예측 {len(f):,}행 · 세션 {f['session'].nunique()}", flush=True)
        print("배선 확인만 했다 — 판정 수치는 안 찍는다(--smoke)", flush=True)
        return 0
    preds = {h: walk_forward(panel, sessions, h, blocks) for h in HORIZONS}
    # 순위 평균 — 세션마다 백분위로 바꿔 더한다(스케일이 다른 모델을 그대로 더하지 않는다).
    ranked = {h: f.assign(pct=f.groupby("session")["pred"].rank(pct=True)) for h, f in preds.items()}

    store = Store(root=Path(args.root))
    wide_px = _prices(store, sessions)
    ret = (wide_px.shift(-2) / wide_px.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    trad_frame = pd.read_pickle(args.cache_dir / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    del trad_frame

    series: dict[str, pd.Series] = {}
    rows: dict[str, dict[str, float]] = {}
    for name, members in VARIANTS.items():
        merged = ranked[members[0]][["entity_id", "session"]].copy()
        merged["pred"] = np.mean([ranked[h].set_index(["entity_id", "session"]).loc[
            pd.MultiIndex.from_frame(merged[["entity_id", "session"]]), "pct"].to_numpy() for h in members], axis=0)
        daily = portfolio(merged, ret, trad)
        b = bench.reindex(daily.index).fillna(0.0)
        rows[name] = summarize(daily, b)
        box = daily[daily.index <= BOX_END]
        rally = daily[daily.index > BOX_END]
        rows[name]["box_ann"] = float(box.mean() * ANN)
        rows[name]["rally_ann"] = float(rally.mean() * ANN)
        rows[name]["box_excess"] = float((box - b.reindex(box.index).fillna(0.0)).mean() * ANN)
        rows[name]["rally_excess"] = float((rally - b.reindex(rally.index).fillna(0.0)).mean() * ANN)
        series[name] = daily
        print(f"{name}: 연 {rows[name]['ann']:+.1%} · 박스 {rows[name]['box_ann']:+.1%} · 급등 "
              f"{rows[name]['rally_ann']:+.1%} · MDD {rows[name]['mdd']:.1%} · 비대칭 {rows[name]['asym']:+.3f}", flush=True)

    base = series["B0"]
    lines = ["| 변형 | 지평 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR(K200) | 상승 | 하락 | 비대칭 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, members in VARIANTS.items():
        m = rows[name]
        lines.append(f"| {name} | {'+'.join(f'h{h}' for h in members)} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | "
                     f"{m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['beta']:.2f} | "
                     f"{m['ir']:+.2f} | {m['up']:.2f} | {m['down']:.2f} | {m['asym']:+.3f} |")
    lines.append("")
    passed = []
    for name in VARIANTS:
        if name == "B0":
            continue
        m, b0 = rows[name], rows["B0"]
        d = (series[name] - base).dropna()
        t = float(ic_module.newey_west_t(d, lag=4))
        c = (m["box_ann"] >= b0["box_ann"] + GATE_ANN and m["rally_ann"] >= b0["rally_ann"] + GATE_ANN,
             t >= GATE_T, m["mdd"] >= b0["mdd"] - GATE_MDD, m["asym"] >= b0["asym"] + GATE_ASYM)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①두 국면 +1%p {mark(c[0])} · ②NW t {t:+.2f} {mark(c[1])} · "
                     f"③MDD {m['mdd']:.1%} {mark(c[2])} · ④비대칭 {m['asym']:+.3f} {mark(c[3])} "
                     f"→ {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["sharpe"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행 h5 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)

    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "ranker-ensemble-horizons-2026-09:AB", "valid_from": now, "observed_at": now,
            "source": "trial_ranker_ensemble", "market": "KR", "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[2:]))[:900],
        }], ingest_run_id=f"trial-ranker-ensemble-AB-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/AB · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
