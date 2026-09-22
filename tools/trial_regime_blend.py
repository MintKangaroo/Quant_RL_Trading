"""시행 AI — 국면에 따라 배합한다(오르는 장엔 넓게, 내리는 장엔 선택). docs/protocols/regime-blend-2026-09.md.

    .venv/bin/python tools/trial_regime_blend.py [--save]

두 구성(H0 선택 · D1 넓게)을 **매일 각자** 돌리고, 배합 포트는 그날 국면 신호가 가리키는 구성의 비중을 든다. 회전은 배합 포트
자신의 (오늘 비중 − 어제 드리프트 비중)이라 전환일의 전량 교체 비용이 그대로 들어간다. 구성 규칙은 AD·AH 와 같다.
창 둘: 하락 창(2021-11-10~2022-06-30, AH 의 역방향 GBM) · 급등 포함 창(2022-07-01~2026-06-30, 백필 실전 랭커).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts.regime import LOOKBACK_DAYS, classify  # noqa: E402
from quant_rl_trading.selector.exposure import TREND_WINDOW  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_bench_construct import _caps  # noqa: E402
from tools.trial_beta_megacap import CACHE, ETF_FEE, k200_members  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _prices, metrics  # noqa: E402
from tools.trial_reverse_window import reverse_scores  # noqa: E402
from tools.trial_selection_ranker import _scores, capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/regime-blend-2026-09.md")
INDEX = "KR:IDX:KOSPI200"
FALL = (date(2021, 11, 10), date(2022, 6, 30))
RISE = (date(2022, 7, 1), date(2026, 6, 30))
SPAN, N, EXIT_MULT = 5, 24, 3
CUT, CAP_LIMIT, REENTRY = 0.10, 0.10, 0.10
GATE_KEEP, GATE_BEAT, GATE_MDD = 0.02, 0.01, 0.01
SAWTOOTH = 20

Books = dict[date, pd.Series]


def book_h0(score: pd.DataFrame, sessions: list[date], trad: dict, risk: pd.DataFrame, floor: float) -> Books:
    """선택 구성 — AD 의 D0 · AH 의 H0 와 같은 규칙(상위 24 동일가중, 완충 3N, 위험 하한). 날마다 동일가중으로 맞춘다."""
    held: list[str] = []
    out: Books = {}
    for day in sessions:
        if day not in score.index:
            continue
        f = score.loc[day].dropna()
        f = f[f.index.isin(trad[day])]
        r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
        if not r.dropna().empty:
            f = f[r >= r.quantile(floor)]
        if f.empty:
            continue
        held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
        out[day] = pd.Series(1.0 / len(held), index=held)
    return out


def book_d1(score: pd.DataFrame, sessions: list[date], trad: dict, members: dict, caps: pd.DataFrame,
            fl: pd.Series, ret: pd.DataFrame) -> Books:
    """넓게 들기 — AD-D1 그대로. 편입·제외가 바뀔 때만 재조정하고, 아니면 자기 드리프트 비중을 이어 간다."""
    excluded: set[str] = set()
    prev: pd.Series | None = None
    prev_keep: frozenset[str] = frozenset()
    out: Books = {}
    for day in sessions:
        if day not in score.index or day not in ret.index:
            continue
        pool = score.loc[day].dropna()
        pool = pool[pool.index.isin(trad[day] & members[day])]
        if len(pool) < 50:
            continue
        pct = pool.rank(pct=True)
        excluded = {e for e in excluded if e in pct.index and pct[e] < CUT + REENTRY} | set(pct[pct < CUT].index)
        keep = [e for e in pool.index if e not in excluded]
        if not keep:
            continue
        same = prev is not None and frozenset(keep) == prev_keep and float(prev.max()) <= CAP_LIMIT + 0.02
        if same:
            w = prev
        elif day in caps.index:
            fcap = (caps.loc[day].reindex(keep) * fl.reindex(keep).fillna(fl.median())).dropna()
            w = capped_cap_weights(fcap, CAP_LIMIT) if len(fcap) >= 30 else pd.Series(1.0 / len(keep), index=keep)
        else:
            w = pd.Series(1.0 / len(keep), index=keep)
        prev_keep = frozenset(keep)
        out[day] = w
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return out


def hold(pick: Callable[[date], Books | None], sessions: list[date], ret: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """그날 고른 구성의 비중을 들고 비용 후 일수익과 일 회전을 돌려준다."""
    prev: pd.Series | None = None
    out, turns = {}, {}
    for day in sessions:
        books = pick(day)
        if books is None or day not in books or day not in ret.index:
            continue
        w = books[day]
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns[day] = t
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), pd.Series(turns).sort_index()


def signals(closes: pd.Series, sessions: list[date], crisis_floor: float) -> pd.DataFrame:
    """세션별 하락 국면 여부(True=하락). 둘 다 종가 t 까지만 본다."""
    rows = {}
    for day in sessions:
        hist = closes[closes.index <= day]
        trend = len(hist) >= TREND_WINDOW and float(hist.iloc[-1]) < float(hist.tail(TREND_WINDOW).mean())
        window = hist[hist.index > day - timedelta(days=LOOKBACK_DAYS)]
        state = classify(window, crisis_floor=crisis_floor)
        rows[day] = {"R1": trend, "R2": state in ("bear", "crisis")}
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def summary(s: pd.Series, bench: pd.Series) -> dict[str, float]:
    b = bench.reindex(s.index).fillna(0.0)
    m = metrics(s, b)
    m["up"], m["down"] = capture(s, b)
    return m


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AI — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad_frame = trad_frame[(trad_frame["session"] >= FALL[0]) & (trad_frame["session"] <= RISE[1])]
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    sessions = sorted(trad)
    del trad_frame
    end_moment = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    floor = float(store.config("selector.risk_floor_percentile", as_of=end_moment))
    # 등록: crisis 문턱은 **현행값**(시행 R, 2026-09-07 발효). 판정 창 끝(2026-06-30)엔 아직 발효 전이라 지금 시점으로 읽는다.
    crisis_floor = float(store.config("exposure.crisis_momentum_floor", as_of=datetime.now(UTC)))  # invariant-allow: wallclock — 등록이 정한 현행값

    fall_days = [d for d in sessions if d <= FALL[1]]
    rise_days = [d for d in sessions if d >= RISE[0]]
    pred, _ = reverse_scores()
    fall_score = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    del pred
    rise_score = _scores(store, "ranker", rise_days).ewm(span=SPAN).mean()

    risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    del wide
    idx = store.get("indices", as_of=end_moment, lookback=(sessions[-1] - date(2020, 1, 1)).days, market="KR",
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    print(f"K200 종가 {len(closes)}개 {closes.index[0]}~{closes.index[-1]}", flush=True)
    level = closes.reindex(sessions).ffill()
    bench = (level.shift(-2) / level.shift(-1) - 1.0)
    caps = _caps(store, sessions)
    fl = store.get("float_ratio", as_of=datetime.now(UTC), lookback=30)  # invariant-allow: wallclock — 참조 데이터, 최초 관측 소급(시행 P·AD 와 같다)
    fl = fl.sort_values("observed_at").groupby("entity_id").tail(1).set_index("entity_id")["float_ratio"]
    members = k200_members(sessions, first_quarter="2021Q3")
    sig = signals(closes, sessions, crisis_floor)
    print(f"세션 {len(sessions)} · 하락 창 {len(fall_days)} · 급등 포함 창 {len(rise_days)} · 위험 하한 {floor:.0%}", flush=True)

    # 창별로 두 구성을 각자 돌린다(점수 원천이 창마다 다르다).
    books = {"H0": {}, "D1": {}}
    for days, score in ((fall_days, fall_score), (rise_days, rise_score)):
        books["H0"].update(book_h0(score, days, trad, risk, floor))
        books["D1"].update(book_d1(score, days, trad, members, caps, fl, ret))

    series, turns = {}, {}
    series["H0"], turns["H0"] = hold(lambda d: books["H0"], sessions, ret)
    series["D1"], turns["D1"] = hold(lambda d: books["D1"], sessions, ret)
    for name in ("R1", "R2"):
        down = sig[name]
        series[name], turns[name] = hold(lambda d, down=down: books["H0"] if bool(down.get(d, False)) else books["D1"],
                                         sessions, ret)
    series["K200"] = (bench - ETF_FEE / ANN).reindex(sessions).dropna()

    def part(s: pd.Series, lo: date, hi: date) -> pd.Series:
        return s[(s.index >= lo) & (s.index <= hi)]

    rows = {}
    for name, s in series.items():
        rows[name] = {w: summary(part(s, *span), bench) for w, span in
                      (("fall", FALL), ("rise", RISE), ("all", (FALL[0], RISE[1])))}
        if name in turns:
            rows[name]["turn"] = float(turns[name].mean() * ANN)

    lines = ["| 변형 | 하락 창 | 급등 포함 창 | 전체 | 전체 샤프 | 하락 MDD | 급등 포함 MDD | 전체 MDD | 전체 β | 연 회전 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name in ("H0", "D1", "R1", "R2", "K200"):
        r = rows[name]
        lines.append(f"| {name} | {r['fall']['ann']:+.1%} | {r['rise']['ann']:+.1%} | {r['all']['ann']:+.1%} | "
                     f"{r['all']['sharpe']:+.2f} | {r['fall']['mdd']:.1%} | {r['rise']['mdd']:.1%} | {r['all']['mdd']:.1%} | "
                     f"{r['all']['beta']:.2f} | {r.get('turn', float('nan')):.1f} |")
    lines.append("")

    # 기록: 판정 지연·전환
    fall_close = closes[(closes.index >= FALL[0]) & (closes.index <= FALL[1])]
    peak, trough = fall_close.idxmax(), fall_close.idxmin()
    for name in ("R1", "R2"):
        down = sig[name].astype(bool)
        flips = down[down != down.shift()].index[1:]
        saw = sum(1 for a, b in zip(flips, flips[1:], strict=False) if sessions.index(b) - sessions.index(a) <= SAWTOOTH)
        first_down = next((d for d in down.index if d >= peak and down[d]), None)
        first_up = next((d for d in down.index if d >= trough and not down[d]), None)
        lag_down = sessions.index(first_down) - sessions.index(peak) if first_down else float("nan")
        lag_up = sessions.index(first_up) - sessions.index(trough) if first_up else float("nan")
        switch_cost = float(turns[name][turns[name].index.isin(flips)].sum() * ONE_WAY_COST / (len(sessions) / ANN))
        best = "H0" if rows["H0"]["all"]["ann"] >= rows["D1"]["all"]["ann"] else "D1"
        t_best = float(ic_module.newey_west_t((series[name] - series[best]).dropna(), lag=4))
        lines.append(f"기록 {name}: 하락 체류 {down.mean():.0%} · 전환 {len(flips)}회(톱니 {saw}) · 전환 비용 연 {switch_cost:.1%}p · "
                     f"지연 고점 {peak}→하락 신호 {first_down} ({lag_down} 세션) · 저점 {trough}→상승 신호 {first_up} ({lag_up} 세션) · "
                     f"대 {best} NW t {t_best:+.2f}")
    lines.append("")

    passed = []
    h0, d1 = rows["H0"], rows["D1"]
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    for name in ("R1", "R2"):
        r = rows[name]
        c = (r["rise"]["ann"] >= d1["rise"]["ann"] - GATE_KEEP,
             r["fall"]["ann"] >= h0["fall"]["ann"] - GATE_KEEP,
             r["all"]["ann"] >= max(h0["all"]["ann"], d1["all"]["ann"]) + GATE_BEAT,
             r["all"]["mdd"] >= max(h0["all"]["mdd"], d1["all"]["mdd"]) - GATE_MDD)
        lines.append(f"{name}: ①급등 포함 {r['rise']['ann']:+.1%} vs D1 {d1['rise']['ann']:+.1%}−2%p {mark(c[0])} · "
                     f"②하락 {r['fall']['ann']:+.1%} vs H0 {h0['fall']['ann']:+.1%}−2%p {mark(c[1])} · "
                     f"③전체 {r['all']['ann']:+.1%} vs 둘 중 나은 쪽+1%p {mark(c[2])} · "
                     f"④전체 MDD {r['all']['mdd']:.1%} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((r["all"]["sharpe"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "regime-blend-2026-09:AI", "valid_from": now, "observed_at": now,
            "source": "trial_regime_blend", "market": "KR", "family": "selection", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[2:]))[:900],
        }], ingest_run_id=f"trial-regime-blend-AI-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/AI · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
