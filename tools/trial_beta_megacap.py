"""시행 Z — 베타를 직접 겨냥한다, 그리고 대형주를 든다. docs/protocols/beta-target-megacap-2026-09.md.

    .venv/bin/python tools/trial_beta_megacap.py [--save]

틀은 시행 P·Y 와 같다(EMA5·완충 3N·N=24·위험 하한·t+1→t+2·편도 0.41%). 확장 패널(2022-07~2026-06)에서
**두 국면을 갈라** 판정한다. 노출 축은 끈다(등록).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_bench_construct import _caps  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics  # noqa: E402
from tools.trial_selection_ranker import _scores, capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/beta-target-megacap-2026-09.md")
CACHE = Path("data/_diag-long")
JUDGE_START, JUDGE_END, BOX_END = date(2022, 7, 1), date(2026, 6, 30), date(2024, 12, 31)
N, EXIT_MULT, SPAN = 24, 3, 5
CAP_LIMIT, MEGA_LIMIT, BETA_BAND, BETA_STEP = 0.10, 0.15, (0.95, 1.05), 0.005
BETA_WINDOW, BETA_MIN = 120, 60
ETF_FEE = 0.0015
VARIANTS = ("Z0", "Z1", "Z2", "Z3", "Z4", "Z5")
GATE_EXCESS, GATE_ASYM, GATE_MDD, GATE_T = -0.01, 0.05, 0.03, 2.0


def k200_members(sessions: list[date], first_quarter: str = "2022Q2") -> dict[date, set[str]]:
    """분기말 스냅샷으로 잇는다(시행 P 와 같은 방식, 구간만 길다). 세션 S 는 S 이전 마지막 스냅샷을 쓴다."""
    from pykrx import stock

    from tools.backfill import load_env
    load_env()
    quarters = pd.period_range(first_quarter, "2026Q2", freq="Q")
    snaps: dict[date, set[str]] = {}
    for q in quarters:
        end = q.end_time.date()
        day = max((s for s in sessions if s <= end), default=None) or end
        for back in range(0, 7):  # 분기말이 휴장이면 며칠 당긴다
            probe = day - pd.Timedelta(days=back).to_pytimedelta()
            codes = stock.get_index_portfolio_deposit_file("1028", probe.strftime("%Y%m%d"))
            if len(codes) > 0:
                snaps[probe] = {"KR:" + c for c in codes}
                break
    keys = sorted(snaps)
    print(f"K200 스냅샷 {len(keys)}개 {keys[0]}~{keys[-1]} · 평균 {np.mean([len(v) for v in snaps.values()]):.0f}종목", flush=True)
    return {s: snaps[max((k for k in keys if k <= s), default=keys[0])] for s in sessions}


def beta_target_weights(betas: pd.Series) -> pd.Series:
    """동일가중에서 출발해 포트 β 가 밴드에 들 때까지 저β→고β 로 조금씩 옮긴다(상한 10%). 등록 규칙 그대로."""
    w = pd.Series(1.0 / len(betas), index=betas.index)
    known = betas.dropna()
    if len(known) < 2:
        return w
    fill = betas.fillna(known.median())
    for _ in range(2000):
        port = float((w * fill).sum())
        if BETA_BAND[0] <= port <= BETA_BAND[1]:
            break
        raise_beta = port < BETA_BAND[0]
        order = known.sort_values(ascending=raise_beta).index  # 올릴 때: 저β 에서 뺀다
        donors = [e for e in order if w[e] > BETA_STEP]
        takers = [e for e in reversed(order) if w[e] < CAP_LIMIT - 1e-12]
        if not donors or not takers or donors[0] == takers[0]:
            break
        if (known[takers[0]] > known[donors[0]]) != raise_beta:
            break  # 더 옮겨도 방향이 안 나온다
        step = min(BETA_STEP, w[donors[0]], CAP_LIMIT - w[takers[0]])
        w[donors[0]] -= step
        w[takers[0]] += step
    return w / w.sum()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 Z — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad_frame = trad_frame[(trad_frame["session"] >= JUDGE_START) & (trad_frame["session"] <= JUDGE_END)]
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    sessions = sorted(trad)
    del trad_frame
    end_moment = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    floor = float(store.config("selector.risk_floor_percentile", as_of=end_moment))
    ranker = _scores(store, "ranker", sessions).ewm(span=SPAN).mean()
    risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    # β: 종가→종가 일수익의 직전 120세션 OLS, **t−1 까지**(shift 1).
    daily = wide.pct_change(fill_method=None)
    daily = daily.where(daily.abs() <= MAX_MOVE)
    mkt = idx.pct_change().reindex(daily.index)
    beta = (daily.rolling(BETA_WINDOW, min_periods=BETA_MIN).cov(mkt)
            .div(mkt.rolling(BETA_WINDOW, min_periods=BETA_MIN).var(), axis=0)).shift(1)
    caps = _caps(store, sessions)
    fl = store.get("float_ratio", as_of=datetime.now(UTC), lookback=30)  # invariant-allow: wallclock — 참조 데이터, 최초 관측 소급(시행 P 와 같다)
    fl = fl.sort_values("observed_at").groupby("entity_id").tail(1).set_index("entity_id")["float_ratio"]
    members = k200_members(sessions)
    print(f"세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 랭커 점수 {ranker.index.min()}~ · 위험 하한 {floor:.2f}", flush=True)

    rows: dict[str, dict[str, float]] = {}
    series: dict[str, pd.Series] = {}
    for name in VARIANTS:
        if name == "Z5":
            s = (bench - ETF_FEE / ANN).reindex(sessions).dropna()
            series[name] = s
            continue
        held: list[str] = []
        prev = None
        out, turn, effs, maxw, mega_in = {}, [], [], [], 0
        for day in sessions:
            if day not in ranker.index or day not in ret.index:
                continue
            f = ranker.loc[day].dropna()
            ok = set(trad[day])
            if name in ("Z1", "Z2", "Z3"):
                ok &= members[day]
            f = f[f.index.isin(ok)]
            r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
            if not r.dropna().empty:
                f = f[r >= r.quantile(floor)]
            if f.empty:
                continue
            held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
            fcap = (caps.loc[day] * fl.reindex(caps.columns).fillna(fl.median())).dropna() if day in caps.index else pd.Series(dtype=float)
            top2 = list(fcap[fcap.index.isin(trad[day])].sort_values(ascending=False).index[:2])
            if name == "Z2" and len(fcap.reindex(held).dropna()) >= N // 2:
                w = capped_cap_weights(fcap.reindex(held).dropna(), CAP_LIMIT).reindex(held).fillna(0.0)
            elif name == "Z3":
                w = beta_target_weights(beta.loc[day].reindex(held) if day in beta.index else pd.Series(np.nan, index=held))
            elif name == "Z4" and top2:
                k200_total = float(fcap.reindex(list(members[day])).dropna().sum())
                mega = (fcap.reindex(top2) / k200_total).clip(upper=MEGA_LIMIT) if k200_total > 0 else pd.Series(0.0, index=top2)
                rest = [e for e in held if e not in top2]
                w = pd.concat([mega, pd.Series((1.0 - float(mega.sum())) / len(rest), index=rest)])
            else:
                w = pd.Series(1.0 / len(held), index=held)
            w = w / w.sum()
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
            out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
            turn.append(t); effs.append(1.0 / float((w * w).sum())); maxw.append(float(w.max()))
            mega_in += int(any(e in w.index and w[e] > 0 for e in top2))
            drift = w * (1 + dr)
            prev = drift / drift.sum() if drift.sum() > 0 else w
        s = pd.Series(out).sort_index()
        series[name] = s
        rows[name] = {"turn": float(np.mean(turn) * ANN), "effn": float(np.mean(effs)),
                      "maxw": float(np.mean(maxw)), "mega": mega_in}

    for name, s in series.items():
        b = bench.reindex(s.index).fillna(0.0)
        m = metrics(s, b)
        m["up"], m["down"] = capture(s, b)
        m["asym"] = m["up"] - m["down"]
        for label, part in (("box", s[s.index <= BOX_END]), ("rally", s[s.index > BOX_END])):
            pb = b.reindex(part.index)
            m[f"{label}_ann"] = float(part.mean() * ANN)
            m[f"{label}_excess"] = float((part - pb).mean() * ANN)
        rows[name] = {**rows.get(name, {}), **m}

    bnav = (1 + bench.reindex(series["Z0"].index).fillna(0.0)).cumprod()
    bench_mdd = float((bnav / bnav.cummax() - 1).min())
    lines = [f"K200 같은 창 MDD {bench_mdd:.1%} · 세션 {len(series['Z0'])}", "",
             "| 변형 | 연수익 | 박스 | 급등 | 박스 초과 | 급등 초과 | 샤프 | MDD | β | IR | 상승 | 하락 | 비대칭 | 회전 | 유효N | 최대비중 | 시총2 보유세션 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in VARIANTS:
        m = rows[name]
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['box_excess']:+.1%} | "
                     f"{m['rally_excess']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['beta']:.2f} | {m['ir']:+.2f} | "
                     f"{m['up']:.2f} | {m['down']:.2f} | {m['asym']:+.3f} | {m.get('turn', 0):.1f} | {m.get('effn', 0):.1f} | "
                     f"{m.get('maxw', 0):.1%} | {m.get('mega', '-')} |")
    lines.append("")
    base, b0 = series["Z0"], rows["Z0"]
    passed = []
    for name in ("Z1", "Z2", "Z3", "Z4"):
        m = rows[name]
        d = (series[name] - base).dropna()
        t = float(ic_module.newey_west_t(d, lag=4))
        c = (m["box_excess"] >= b0["box_excess"] + GATE_EXCESS and m["rally_excess"] >= b0["rally_excess"] + GATE_EXCESS,
             m["asym"] >= b0["asym"] + GATE_ASYM, m["mdd"] >= bench_mdd - GATE_MDD, t >= GATE_T)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①두 국면 초과 {mark(c[0])} · ②비대칭 {m['asym']:+.3f} {mark(c[1])} · ③MDD {m['mdd']:.1%} {mark(c[2])} "
                     f"· ④Δ연 {d.mean() * ANN:+.1%} NW t {t:+.2f} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["ir"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "beta-target-megacap-2026-09:Z", "valid_from": now, "observed_at": now,
            "source": "trial_beta_megacap", "market": "KR", "family": "selection", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[4:]))[:900],
        }], ingest_run_id=f"trial-beta-megacap-Z-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/Z · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
