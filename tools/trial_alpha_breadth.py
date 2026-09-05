"""알파 감쇠(홀딩별 IC) · 폭(N)×상한 격자 — docs/protocols/alpha-decay-breadth-2026-09.md 의 1·2단계.

    .venv/bin/python tools/trial_alpha_breadth.py decay      # 국장·미장 홀딩 1·2·3·5·10·20 IC, NW t, 블록별
    .venv/bin/python tools/trial_alpha_breadth.py breadth    # 5회차 대조(EMA5·완충 3N·EW) 경로 위 N×상한 격자, 판정 100세션

학습 없음 — 창고 `signals.ranker` 점수만 쓴다. 대조 경로는 tools/trial_e2e_final 의 Panel(같은 명단·수익·블록)을 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_e2e_final import ONE_WAY_COST, Panel, START, END  # noqa: E402
from tools.trial_pooled_deep import blocks_for  # noqa: E402
from tools.trial_selection_pair import index_of, prices_of, scores_of, tradable_of  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

HOLDINGS = (1, 2, 3, 5, 10, 20)
ANN = 252
GRID_N = (24, 36, 50, 80)
GRID_CAP = (0.0625, 0.04, 0.025)


def _nw(s: pd.Series, lag: int) -> float:
    s = s.dropna()
    return float(ic_module.newey_west_t(s, lag=lag)) if len(s) > 3 else float("nan")


# ---------------------------------------------------------------- 1. 알파 감쇠

def decay(store: Store) -> str:
    lines = ["## 1. 알파 감쇠 — ranker 점수의 홀딩별 순방향 IC", ""]
    for market, start, end, shift in (("KR", date(2025, 1, 2), date(2026, 6, 30), 1), ("US", date(2025, 9, 1), date(2026, 6, 23), 0)):
        rk = scores_of(store, "ranker", [start, end], market)
        sessions = [d for d in rk.index if start <= d <= end]
        rk = rk.reindex(sessions); trad = tradable_of(store, sessions, market); wide = prices_of(store, sessions, market)
        blocks = [b for b in blocks_for(sessions) if b[1] >= date(2026, 1, 1)]
        judge = set(); [judge.update(d for d in sessions if ps <= d <= pe) for _, ps, pe in blocks]
        lines.append(f"### {market} — 세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}), 판정 블록 {len(blocks)}개 {sum(1 for _ in judge)}세션, 진입 t+{shift+1} 종가")
        lines.append(""); lines.append("| 홀딩 | 전구간 IC | NW t | 판정 100세션 IC | NW t | 블록별 IC |"); lines.append("|---|---|---|---|---|---|")
        summary = {}
        for h in HOLDINGS:
            fwd = wide.shift(-(shift + 1 + h)) / wide.shift(-(shift + 1)) - 1.0
            fwd = fwd.where(fwd.abs() <= 0.5 * max(1, h / 5)).reindex(sessions)
            ics = {}
            for day in sessions:
                if day not in fwd.index:
                    continue
                s_ = rk.loc[day].dropna(); ok = trad.get(day, set()); s_ = s_[s_.index.isin(ok)]
                r = fwd.loc[day].reindex(s_.index)
                m = r.notna()
                if m.sum() < 50:
                    continue
                ics[day] = float(pd.Series(s_[m].to_numpy()).corr(pd.Series(r[m].to_numpy()), method="spearman"))
            ics = pd.Series(ics).sort_index()
            jud = ics[ics.index.isin(judge)]
            per_block = [float(ics[(ics.index >= ps) & (ics.index <= pe)].mean()) for _, ps, pe in blocks]
            summary[h] = (float(ics.mean()), _nw(ics, h), float(jud.mean()), _nw(jud, h))
            lines.append(f"| {h} | {ics.mean():+.4f} | {_nw(ics, h):+.2f} | {jud.mean():+.4f} | {_nw(jud, h):+.2f} | {' '.join(f'{x:+.3f}' for x in per_block)} |")
        # 반감기: IC(h)/h 를 "하루당 알파" 로 보지 않고, 누적 IC 곡선의 절반 도달 홀딩을 보고한다 — 그리고 유의 최대 홀딩.
        ic1 = summary[1][0]
        half = next((h for h in HOLDINGS if summary[h][0] <= ic1 / 2), None)
        sig = [h for h in HOLDINGS if summary[h][1] >= 2.0]
        lines.append("")
        lines.append(f"- 1일 IC {ic1:+.4f} 의 절반 아래로 내려가는 첫 홀딩: **{half if half else '20일 안엔 없음'}** · 전구간 t≥2 를 유지하는 최대 홀딩: **{max(sig) if sig else '없음'}일**")
        lines.append(f"- 홀딩 h 의 IC 는 h 일 누적수익과의 순위상관이라 h 가 길수록 잡음이 커진다(t 의 lag 도 h). 감쇠가 완만하면 리밸런싱을 늦춰도 알파를 안 잃는다.")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 2. 폭 × 상한 격자

def path(panel: Panel, idx: list[int], n: int, cap: float, span: int = 5, exit_mult: int = 3, cadence: int = 1):
    """5회차 대조와 같은 경로(EMA·완충·동일가중)를 N·상한만 바꿔 돈다. 1/N > 상한이면 상한×N 만 투자, 나머지 현금.
    ``cadence`` 세션마다 한 번만 재선정한다(1 = 매일, 현행). 그 사이엔 보유를 그대로 든다 — 1단계(IC 누적) 진단용."""
    scores = panel.ranker_raw.ewm(span=span).mean(); held: list[int] = []; prev = None; rows = []
    Ntot = len(panel.entities); hold_days: dict[int, int] = {}; completed: list[int] = []
    for k, t in enumerate(idx):
        m = panel.mask[t].numpy(); f = scores.iloc[t].to_numpy(); f = np.where(m, f, np.nan)
        order = [int(i) for i in np.argsort(-np.nan_to_num(f, nan=-1e9)) if m[i]]
        if not order:
            continue
        if k % cadence == 0 or not held:
            held = pick_mult(held, pd.Index(order), n, exit_mult)
        else:
            held = [e for e in held if m[e]] or pick_mult([], pd.Index(order), n, exit_mult)
        for e in list(hold_days):
            if e not in held:
                completed.append(hold_days.pop(e))
        for e in held:
            hold_days[e] = hold_days.get(e, 0) + 1
        w_each = min(1.0 / len(held), cap)
        w = np.zeros(Ntot, np.float32); w[held] = w_each
        r = panel.r[t].numpy(); port = float((w * r).sum()); bench = float(r[m].mean())
        turnover = float(np.abs(w - (prev if prev is not None else 0)).sum())
        rows.append({"gross": port, "net": port - ONE_WAY_COST * turnover, "bench": bench, "turn": turnover, "eff_n": 1.0 / float((w * w).sum()) if w.sum() > 0 else 0.0, "invested": float(w.sum())})
        drift = w * (1 + r); prev = drift / drift.sum() if drift.sum() > 0 else w
    completed += list(hold_days.values())
    return pd.DataFrame(rows), (float(np.mean(completed)) if completed else float("nan"))


def breadth(store: Store) -> str:
    panel = Panel(store, trading_days(Market.KR, START, END))
    sessions = panel.sessions; sidx = {s: i for i, s in enumerate(sessions)}
    blocks = [b for b in blocks_for(sessions) if b[1] >= date(2026, 1, 1)]
    idx = [i for _, ps, pe in blocks for i in range(sidx[ps], sidx[pe] + 1)]
    k200 = index_of(store, sessions, "KR", "KR:IDX:KOSPI200"); k_ret = (k200.shift(-2) / k200.shift(-1) - 1.0)
    lines = ["## 2. 폭(N) × 비중 상한 격자 — 5회차 대조 경로(EMA5·완충 3N·동일가중), 판정 100세션", "",
             f"판정 {len(idx)}세션 ({sessions[idx[0]]}~{sessions[idx[-1]]}), 편도 비용 {ONE_WAY_COST:.2%}. IR(명단) = 명단 동일가중 대비, IR(K200) 은 병기.", "",
             "| N | 상한 | 투자비중 | 유효종목 | 연회전 | 비용전 연수익 | 비용후 연수익 | IR(명단) | IR(K200) | MDD | 평균 보유일 | IR/√유효N |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    cells = {}
    for n in GRID_N:
        for cap in GRID_CAP:
            fr, hold = path(panel, idx, n, cap)
            net = fr["net"]; ex = net - fr["bench"]; nav = (1 + net).cumprod(); mdd = float((nav / nav.cummax() - 1).min())
            kr = k_ret.reindex([sessions[i] for i in idx]).fillna(0.0).to_numpy(); exk = net.to_numpy() - kr
            ir = float(ex.mean() / ex.std() * np.sqrt(ANN)) if ex.std() > 0 else float("nan")
            irk = float(exk.mean() / exk.std() * np.sqrt(ANN)) if exk.std() > 0 else float("nan")
            effn = float(fr["eff_n"].mean()); cells[(n, cap)] = dict(ir=ir, effn=effn, net=float(net.mean() * ANN))
            lines.append(f"| {n} | {cap:.2%} | {fr['invested'].mean():.0%} | {effn:.1f} | {fr['turn'].mean() * ANN:.1f} | {fr['gross'].mean() * ANN:+.1%} | {net.mean() * ANN:+.1%} | {ir:+.2f} | {irk:+.2f} | {mdd:.1%} | {hold:.1f} | {ir / np.sqrt(effn) if effn > 0 else float('nan'):+.3f} |")
    base = cells[(24, 0.0625)]
    lines.append("")
    lines.append("### 진단 — 리밸런싱 주기(1단계가 바꾼 가정): 같은 경로, 5세션마다 재선정")
    lines.append("")
    lines.append("| N | 상한 | 주기 | 유효종목 | 연회전 | 비용전 연수익 | 비용후 연수익 | IR(명단) | IR(K200) | MDD | 평균 보유일 |"); lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for n, cap in ((24, 0.0625), (50, 0.04), (80, 0.025)):
        for cadence in (1, 5):
            fr, hold = path(panel, idx, n, cap, cadence=cadence)
            net = fr["net"]; ex = net - fr["bench"]; nav = (1 + net).cumprod(); mdd = float((nav / nav.cummax() - 1).min())
            kr = k_ret.reindex([sessions[i] for i in idx]).fillna(0.0).to_numpy(); exk = net.to_numpy() - kr
            ir = float(ex.mean() / ex.std() * np.sqrt(ANN)) if ex.std() > 0 else float("nan")
            irk = float(exk.mean() / exk.std() * np.sqrt(ANN)) if exk.std() > 0 else float("nan")
            lines.append(f"| {n} | {cap:.2%} | {'매일' if cadence == 1 else '5세션'} | {fr['eff_n'].mean():.1f} | {fr['turn'].mean() * ANN:.1f} | {fr['gross'].mean() * ANN:+.1%} | {net.mean() * ANN:+.1%} | {ir:+.2f} | {irk:+.2f} | {mdd:.1%} | {hold:.1f} |")
    lines.append("")
    lines.append(f"- 대조(24·6.25%) IR(명단) {base['ir']:+.2f}, 유효종목 {base['effn']:.0f}. IR ≈ IC×√N 이면 IR/√유효N 열이 N 에 무관해야 한다 — 그 열이 N 이 클수록 작아지면 폭이 IC 희석에 먹히는 것이다.")
    return "\n".join(lines)


# ---------------------------------------------------------------- 2b. 미장 폭 격자

US_COST = 0.0030


def breadth_us(store: Store) -> str:
    """미장 — 시행 R 과 같은 경로(세션 키 = 신호 관측 KST 날짜, 진입 지연 1일, 편도 0.30%, 위험 하한 config)에 N 격자."""
    start, end = date(2025, 9, 1), date(2026, 6, 23)
    rk_all = scores_of(store, "ranker", [start, end], "US"); sessions = [d for d in rk_all.index if start <= d <= end]
    ranker = rk_all.reindex(sessions); risk = scores_of(store, "risk", [start, end], "US").reindex(sessions)
    trad = tradable_of(store, sessions, "US"); wide = prices_of(store, sessions, "US")
    ret = (wide.shift(-1) / wide - 1.0); ret = ret.where(ret.abs() <= 0.5)
    spx = index_of(store, sessions, "US", "US:IDX:SP500"); spx_ret = (spx.shift(-1) / spx - 1.0)
    floor = float(store.config("selector.risk_floor_percentile", as_of=datetime.combine(end, time(23), tzinfo=UTC)))
    judge = [d for d in sessions if d >= date(2026, 1, 19)]
    smooth = ranker.ewm(span=5).mean()
    lines = ["## 2b. 미장 폭(N) 격자 — 시행 R 경로(EMA5·완충 3N·동일가중·편도 0.30%), 판정 " + f"{len(judge)}세션 ({judge[0]}~{judge[-1]})", "",
             "| N | 주기 | 유효종목 | 연회전 | 비용전 연수익 | 비용후 연수익 | IR(명단) | IR(SP500) | MDD | 평균 보유일 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for n in GRID_N:
        for cadence in (1, 5):
            held: list[str] = []; prev = None; rows = []; hold_days: dict[str, int] = {}; completed: list[int] = []
            for k, day in enumerate(sessions):
                if day not in ret.index:
                    continue
                f = smooth.loc[day].dropna(); f = f[f.index.isin(trad.get(day, set()))]
                r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
                if not r.dropna().empty:
                    f = f[r >= r.quantile(floor)]
                if f.empty:
                    continue
                order = f.sort_values(ascending=False).index
                if k % cadence == 0 or not held:
                    held = pick_mult(held, order, n, 3)
                else:
                    held = [e for e in held if e in f.index] or pick_mult([], order, n, 3)
                for e in list(hold_days):
                    if e not in held:
                        completed.append(hold_days.pop(e))
                for e in held:
                    hold_days[e] = hold_days.get(e, 0) + 1
                w = pd.Series(1.0 / len(held), index=held); dr = ret.loc[day].reindex(w.index).fillna(0.0)
                t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
                bench = float(ret.loc[day].reindex(list(trad.get(day, set()))).dropna().mean()) if trad.get(day) else 0.0
                if day in judge:
                    rows.append({"gross": float((w * dr).sum()), "net": float((w * dr).sum()) - US_COST * t, "bench": bench, "turn": t,
                                 "eff_n": 1.0 / float((w * w).sum()), "spx": float(spx_ret.get(day, 0.0) or 0.0)})
                drift = w * (1 + dr); prev = drift / drift.sum() if drift.sum() > 0 else w
            completed += list(hold_days.values())
            fr = pd.DataFrame(rows); net = fr["net"]; ex = net - fr["bench"]; exs = net - fr["spx"]
            nav = (1 + net).cumprod(); mdd = float((nav / nav.cummax() - 1).min())
            ir = float(ex.mean() / ex.std() * np.sqrt(ANN)) if ex.std() > 0 else float("nan")
            irs = float(exs.mean() / exs.std() * np.sqrt(ANN)) if exs.std() > 0 else float("nan")
            lines.append(f"| {n} | {'매일' if cadence == 1 else '5세션'} | {fr['eff_n'].mean():.1f} | {fr['turn'].mean() * ANN:.1f} | {fr['gross'].mean() * ANN:+.1%} | {net.mean() * ANN:+.1%} | {ir:+.2f} | {irs:+.2f} | {mdd:.1%} | {np.mean(completed):.1f} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["decay", "breadth", "breadth-us"]); parser.add_argument("--root", default="data")
    parser.add_argument("--ns", type=int, nargs="*", help="N 격자 덮어쓰기 (예: --ns 120 200 400)")
    args = parser.parse_args(argv); store = Store(root=Path(args.root))
    if args.ns:
        global GRID_N, GRID_CAP
        GRID_N = tuple(args.ns); GRID_CAP = (0.0625,)
    out = {"decay": decay, "breadth": breadth, "breadth-us": breadth_us}[args.step](store)
    print(out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
