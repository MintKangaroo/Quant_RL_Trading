"""시행 BB — HMM 국면 확률로 노출 정하기. docs/protocols/v2-hmm-exposure-2026-10.md.

    .venv/bin/python tools/v2_hmm_exposure.py [--save]

기본 포트 = 시행 AZ 채택 구성(K1/K2), 기각이면 K0. 비중 경로는 tools/v2_sim.simulate(실전 보유일 규칙), 10세션 재조정.
E0 노출 끔 · E1 규칙 V6(regime.classify, 2세션 확인) · E2 HMM 확률 매핑(0.5/0.8/1.0, 0.05 반올림, 데드밴드 0.10).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

from quant_rl_trading.allocator.float_cap_baseline import capped  # noqa: E402
from quant_rl_trading.analysts.regime import LOOKBACK_DAYS, classify  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_kr_index_tilt import CAP_LIMIT, LAMBDAS, caps_panel, float_ratios, members  # noqa: E402
from tools.trial_overlay import ANN, ONE_WAY_COST, metrics  # noqa: E402
from tools.trial_portfolio_variance import CACHE, SEEDS  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END  # noqa: E402
from tools.trial_ranker_kit import INDEX, SPAN, market_data, record, scores_chunked  # noqa: E402
from tools.v2_sim import State, simulate  # noqa: E402

PROTOCOL = Path("docs/protocols/v2-hmm-exposure-2026-10.md")
AZ_LOG = Path("logs/trial-kr-index-tilt-AZ.log")
HMM = Path("data/_diag/v2/hmm-KR.pkl")
EVERY = 10
V6 = {"bull": 1.0, "volatile": 1.0, "bear": 0.7, "crisis": 0.5, "unknown": 1.0}
CONFIRM, CRISIS_FLOOR, DEADBAND = 2, -0.03, 0.10
HMM_MAP = (0.5, 0.8, 1.0)


def base_variant() -> str:
    text = AZ_LOG.read_text() if AZ_LOG.exists() else ""
    if "판정:" not in text:
        raise SystemExit("AZ 판정 전 — 기다린다(등록: 기본 포트는 AZ 채택 구성)")
    m = re.findall(r"^판정: 채택 (K\d)", text, flags=re.M)
    return m[-1] if m else "K0"


def targets(variant: str, score: pd.DataFrame, mem: dict, caps: pd.DataFrame, fr: pd.Series, trad: dict) -> pd.DataFrame:
    """세션별 목표 비중(주식만, 합 1) — AZ 와 같은 공식."""
    rows = {}
    for day in score.index:
        if day not in mem or day not in caps.index:
            continue
        names = [e for e in mem[day] if e in caps.columns and (not trad.get(day) or e in trad[day])]
        cap = (caps.loc[day].reindex(names) * fr.reindex(names)).dropna()
        cap = cap[cap > 0]
        if cap.empty:
            continue
        if variant == "K0":
            w = capped(cap, CAP_LIMIT)
        else:
            s = score.loc[day].reindex(cap.index).dropna()
            z = pd.Series(norm.ppf((s.rank() - 0.5) / len(s)), index=s.index).reindex(cap.index).fillna(0.0)
            w = capped(cap * np.exp(LAMBDAS[variant] * z), CAP_LIMIT)
        rows[day] = w
    return pd.DataFrame(rows).T.sort_index()


def v6_path(store: Store, days: list) -> pd.Series:
    end = datetime.combine(days[-1], time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(days[-1] - days[0]).days + LOOKBACK_DAYS + 10, market="KR",
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    out, cur, streak, last = {}, 1.0, 0, None
    for d in days:
        prior = closes[(closes.index > d - timedelta(days=LOOKBACK_DAYS)) & (closes.index < d)]   # 개장 전 = 전날 종가까지
        state = classify(prior, crisis_floor=CRISIS_FLOOR)
        streak = streak + 1 if state == last else 1
        last = state
        if streak >= CONFIRM:
            cur = V6.get(state, 1.0)
        out[d] = cur
    return pd.Series(out)


def hmm_path(days: list) -> pd.Series:
    h = pd.read_pickle(HMM)  # invariant-allow: data-access — v2 연구 캐시
    h.index = pd.to_datetime(pd.Series(h.index)).dt.date.values
    out, cur = {}, 1.0
    for d in days:
        prev = h[h.index < d]                     # 개장 전 = 전날까지 거른 확률
        if prev.empty:
            out[d] = cur
            continue
        p = prev.iloc[-1][["p0", "p1", "p2"]].to_numpy(dtype=float)
        k = round(float(np.dot(p, HMM_MAP)) / 0.05) * 0.05
        if abs(k - cur) >= DEADBAND:
            cur = k
        out[d] = cur
    return pd.Series(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    base = base_variant()
    print(f"=== 시행 BB — {PROTOCOL} (해시 {digest}) · 기본 포트 {base} ===", flush=True)
    store = Store(root=Path("data"))
    sessions = list(pd.read_pickle(CACHE / "sessions.pkl"))  # invariant-allow: data-access — AM loop 캐시
    sources = {f"loop{s}": pd.read_pickle(CACHE / f"loop-seed{s}.pkl") for s in SEEDS}  # invariant-allow: data-access — AM loop 캐시
    start = min(p["session"].min() for p in sources.values())
    sessions = [s for s in sessions if s >= start]
    live = scores_chunked(store, "ranker", sessions).stack().rename("pred").reset_index()
    live.columns = ["session", "entity_id", "pred"]
    sources = {"live": live[["entity_id", "session", "pred"]], **sources}
    ret, bench, trad = market_data(store, sessions)
    mem = members(store, sessions)
    names = sorted(set().union(*mem.values()))
    caps, fr = caps_panel(store, sessions, names), float_ratios(store, names)
    days = [d for d in sessions if d in mem and d in ret.index]
    paths = {"E0": pd.Series(1.0, index=days), "E1": v6_path(store, days), "E2": hmm_path(days)}
    for v, p in paths.items():
        print(f"  {v}: 평균 노출 {p.mean():.2f} · 전환 {int((p.diff().abs() > 1e-9).sum())}회", flush=True)
    res: dict[str, dict[str, dict]] = {v: {} for v in paths}
    for src, frame in sources.items():
        score = frame.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
        tgt = targets(base, score.reindex([d for d in days if d in score.index]), mem, caps, fr, trad)
        for v, path in paths.items():
            policy = (lambda pth: (lambda s: (float(pth.get(s.day, 1.0)), s.step % EVERY == 0)))(path)
            r = simulate(tgt, ret, bench, ONE_WAY_COST, policy, k_min=0.30)
            b = bench.reindex(r.daily.index).fillna(0.0)
            m = metrics(r.daily, b)
            for label, part in (("box", r.daily.index <= BOX_END), ("rally", r.daily.index > BOX_END)):
                m[f"{label}_ann"] = float(r.daily[part].mean() * ANN)
            res[v][src] = {**m, "turn": r.turnover}

    def avg(v: str, k: str) -> float:
        return float(np.mean([res[v][s][k] for s in sources]))

    lines = ["| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR(K200) | 회전 |", "|---|---|---|---|---|---|---|---|---|"]
    for v in paths:
        lines.append(f"| {v} | {avg(v, 'ann'):+.1%} | {avg(v, 'box_ann'):+.1%} | {avg(v, 'rally_ann'):+.1%} | {avg(v, 'sharpe'):+.2f} | "
                     f"{avg(v, 'mdd'):.1%} | {avg(v, 'beta'):.2f} | {avg(v, 'ir'):+.2f} | {avg(v, 'turn'):.1f} |")
    wins = sum(res["E2"][s]["sharpe"] > res["E1"][s]["sharpe"] for s in sources)
    c = (avg("E2", "sharpe") >= avg("E1", "sharpe") + 0.10, avg("E2", "mdd") >= avg("E1", "mdd") - 0.01,
         avg("E2", "ann") >= avg("E1", "ann") - 0.01, wins == len(sources), avg("E2", "sharpe") >= avg("E0", "sharpe"))
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    lines += ["", f"E2: ①샤프 {avg('E2', 'sharpe'):+.2f} vs {avg('E1', 'sharpe'):+.2f} (+0.10) {mark(c[0])} · ②MDD {avg('E2', 'mdd'):.1%} vs {avg('E1', 'mdd'):.1%} {mark(c[1])} · "
              f"③연수익 {avg('E2', 'ann') - avg('E1', 'ann'):+.1%}p {mark(c[2])} · ④{wins}/4 {mark(c[3])} · ⑤샤프 vs 노출 끔 {avg('E0', 'sharpe'):+.2f} {mark(c[4])}"]
    shadow = wins == len(sources) and avg("E2", "sharpe") >= avg("E1", "sharpe") + 0.05
    verdict = "채택 E2" if all(c) else ("기각 — shadow 승격 후보(4/4·샤프 +0.05)" if shadow else "기각 — 규칙 V6 유지")
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="v2-hmm-exposure-2026-10:BB", source="v2_hmm_exposure", family="selection",
               digest=digest, verdict=verdict, lines=lines[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
