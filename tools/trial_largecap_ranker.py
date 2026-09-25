"""시행 BC — 대형주 전용 랭커: 지수 구성 안에서만 학습해 지수 비중을 기울인다. docs/protocols/largecap-ranker-2026-10.md.

    .venv/bin/python tools/trial_largecap_ranker.py --precheck [--market KR|US|both]   # 커버리지 · 현행 랭커와의 순위상관
    .venv/bin/python tools/trial_largecap_ranker.py --market both [--save]            # 판정(10/3 크론)

학습 우주만 바뀐다 — 모델·워크포워드는 `trial_ranker_kit.walk`(시행 L 규격, min_data 500), 포트는 시행 AZ(국장)·AX(미장) 의
`run` 을 그대로 부른다. 대조는 같은 우주의 지수 대용(K0/X0). 기록용으로 현행 랭커로 기울인 것(AZ·AX 캐시)을 같은 시드 짝으로 잰다.
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

from quant_rl_trading.allocator.float_cap_baseline import _one_per_company  # noqa: E402
from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_beta_megacap import CACHE as LONG_CACHE  # noqa: E402
from tools.trial_kr_index_tilt import caps_panel, float_ratios, members  # noqa: E402
from tools.trial_kr_index_tilt import run as run_kr  # noqa: E402
from tools.trial_overlay import ANN, metrics  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_portfolio_variance import CACHE as KR_LOOP  # noqa: E402
from tools.trial_ranker_ensemble import FEATS as KR_FEATS  # noqa: E402
from tools.trial_ranker_ensemble import load_panel  # noqa: E402
from tools.trial_ranker_kit import SPAN, blocks, market_data, record, walk  # noqa: E402
from tools.trial_us_index_minus_losers import FEATS as US_FEATS  # noqa: E402
from tools.trial_us_index_minus_losers import load_scores, top_caps  # noqa: E402
from tools.trial_us_index_tilt import run as run_us  # noqa: E402
from tools.trial_us_kit import build as build_us  # noqa: E402
from tools.trial_us_kit import market as us_market  # noqa: E402
from tools.trial_us_kit import scores as us_scores  # noqa: E402

PROTOCOL = Path("docs/protocols/largecap-ranker-2026-10.md")
JUDGE_START, JUDGE_END, BOX_END = date(2022, 7, 1), date(2026, 6, 30), date(2024, 12, 31)
SEEDS = (0, 1, 2)
MIN_DATA = 500
BETA_WIN, BETA_MIN = 60, 40
US_WIDE = 500
US_BENCH = "US:SPY"
EXTRA = ["beta60", "caprank"]
GATE_MEAN, GATE_REGIME, GATE_MDD, PROMOTE_MEAN = 0.01, -0.01, 0.02, 0.005
PRECHECK_BLOCKS, PRECHECK_MAX_CORR = 5, 0.90
#: 판정 창 첫날이 이보다 늦으면(학습창 150 뒤) 등록한 창을 못 덮는다.
LATEST_START = {"KR": date(2023, 7, 31), "US": date(2023, 3, 31)}
EARLIEST_END = date(2026, 6, 1)


def rolling_beta(r: pd.DataFrame, m: pd.Series) -> pd.DataFrame:
    """종목별 60일 β(지수 대비). 그날 종가까지의 수익만 쓴다."""
    m = m.reindex(r.index)
    cov = r.mul(m, axis=0).rolling(BETA_WIN, min_periods=BETA_MIN).mean() - \
        r.rolling(BETA_WIN, min_periods=BETA_MIN).mean().mul(m.rolling(BETA_WIN, min_periods=BETA_MIN).mean(), axis=0)
    var = m.rolling(BETA_WIN, min_periods=BETA_MIN).var(ddof=0)
    return cov.div(var, axis=0).astype(np.float32)


def stack(wide: pd.DataFrame, name: str) -> pd.DataFrame:
    s = wide.stack().rename(name).reset_index()
    s.columns = ["session", "entity_id", name]
    return s


def ic_in(pred: pd.DataFrame, panel: pd.DataFrame) -> float:
    """우주 안 h5 IC(y5 rank-gauss 와의 순위상관)."""
    f = pred.merge(panel[["entity_id", "session", "y5"]], on=["entity_id", "session"])
    return float(ic_module.daily_ic(f.rename(columns={"pred": "score", "y5": "target"}).dropna()).mean())


def rank_corr(a: pd.DataFrame, b: pd.DataFrame) -> float:
    """같은 종목·세션에서 두 예측의 세션별 스피어만 평균."""
    f = a.merge(b, on=["entity_id", "session"], suffixes=("_a", "_b")).dropna()
    return float(ic_module.daily_ic(f, score="pred_a", target="pred_b").mean())


# ---------------------------------------------------------------- 국장


def kr_universe(store: Store) -> dict:
    panel = load_panel(LONG_CACHE)
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    panel = panel.drop(columns=[c for c in ("y20", "y60") if c in panel.columns])
    all_sessions = sorted(panel["session"].unique())
    mem = members(store, all_sessions)
    keep = pd.DataFrame([(e, d) for d, names in mem.items() for e in names], columns=["entity_id", "session"])
    panel = panel.merge(keep, on=["entity_id", "session"])
    sessions = sorted(panel["session"].unique())
    ret, bench, trad = market_data(store, all_sessions)
    names = sorted(set(keep["entity_id"]))
    r = ret.shift(2).reindex(columns=[c for c in names if c in ret.columns])   # r_t = close_t / close_{t-1} − 1
    beta = rolling_beta(r, bench.shift(2))
    caps = caps_panel(store, all_sessions, names)
    panel = panel.merge(stack(beta, "beta60"), on=["session", "entity_id"], how="left")
    panel = panel.merge(stack(caps.astype(np.float32), "caprank"), on=["session", "entity_id"], how="left")
    panel = rank_gauss(panel, [*KR_FEATS, *EXTRA, "y5"])
    return {"panel": panel, "sessions": sessions, "mem": mem, "ret": ret, "bench": bench, "trad": trad, "caps": caps,
            "fr": float_ratios(store, names), "feats": [*KR_FEATS, *EXTRA], "n_all": len(all_sessions)}


def kr_control(seed: int) -> pd.DataFrame:
    return pd.read_pickle(KR_LOOP / f"loop-seed{seed}.pkl")  # invariant-allow: data-access — AM loop 캐시(현행 랭커)


def kr_book(u: dict, pred: pd.DataFrame, variant: str) -> tuple[pd.Series, dict]:
    score = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    return run_kr(variant, score, u["mem"], u["caps"], u["fr"], u["ret"], u["trad"])


# ---------------------------------------------------------------- 미장


def us_universe(store: Store) -> dict:
    build_us(store)
    ret, bench = us_market()
    now = datetime.combine(JUDGE_END, time(23), tzinfo=UTC)
    span = (JUDGE_END - JUDGE_START).days + 60
    caps = top_caps(store, now, span)
    cfg_at = datetime.now(UTC)  # invariant-allow: wallclock — 설정 현행값(필터가 9/25~26 에 심겼다)·최신 증권 분류(AT 와 같은 한계)
    kinds = store.get("instrument_types", as_of=cfg_at, lookback=10, market="US",
                      columns=["entity_id", "instrument", "test_issue"])
    types = list(store.config("universe.instrument_types_us", as_of=cfg_at))
    ok = set(kinds[kinds["instrument"].isin(types) & ~kinds["test_issue"].astype(bool)]["entity_id"])
    caps = caps[[c for c in caps.columns if c in ok]]
    min_price = float(store.config("universe.min_price_us", as_of=cfg_at))
    min_cap = float(store.config("universe.min_market_cap_us", as_of=cfg_at))
    prices = read_prices(store, as_of=now, lookback=span + 120, columns=["close"], adjusted=False, market="US",
                         entity=[*caps.columns, US_BENCH])
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    raw = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index().ffill(limit=5)
    del prices
    caps = caps[(caps.index >= JUDGE_START) & (caps.index <= JUDGE_END)]
    px = raw.reindex(index=caps.index, columns=caps.columns)
    caps = caps.where((px >= min_price) & (caps >= min_cap))
    uni = {}
    for day, row in caps.iterrows():
        top = row.dropna().sort_values(ascending=False).iloc[: US_WIDE + 50]
        top = _one_per_company(top.sort_index()).sort_values(ascending=False).iloc[:US_WIDE]
        uni[day] = top
    masked = pd.DataFrame(uni).T.sort_index()
    ranks = masked.rank(axis=1, ascending=False)
    keep = stack(masked, "cap")[["entity_id", "session"]]
    panel = load_scores(keep)
    panel = keep.merge(panel, on=["entity_id", "session"], how="left")
    r = ret.shift(2)                                                  # r_t = close_t / close_{t-1} − 1 (배당 조정 종가)
    names = sorted(set(keep["entity_id"]))
    r = r.reindex(columns=[c for c in names if c in r.columns])
    beta = rolling_beta(r, bench.shift(2))
    y5 = (np.log1p(r.clip(lower=-0.99)).rolling(5).sum().shift(-5)).pipe(np.expm1)
    y5 = y5.where(y5.abs() <= 1.0)
    for name, wide in (("beta60", beta), ("caprank", masked.astype(np.float32)), ("y5", y5.astype(np.float32))):
        panel = panel.merge(stack(wide, name), on=["session", "entity_id"], how="left")
    panel["market"] = "US"
    panel = rank_gauss(panel, [*US_FEATS, *EXTRA, "y5"])
    sessions = sorted(panel["session"].unique())
    cost = float(store.config("accounting.fee_us", as_of=cfg_at))
    return {"panel": panel, "sessions": sessions, "caps": masked, "ranks": ranks, "ret": ret, "bench": bench,
            "cost": cost, "feats": [*US_FEATS, *EXTRA], "n_all": len(sessions)}


def us_control(seed: int) -> pd.DataFrame:
    p = pd.read_pickle(Path("data/_diag/us-selection") / f"pred-seed{seed}.pkl")  # invariant-allow: data-access — AT 루프 캐시
    return p[["entity_id", "session", "pred"]]


def us_book(u: dict, score: pd.DataFrame, variant: str) -> tuple[pd.Series, dict]:
    return run_us(variant, u["caps"], u["ranks"], score, u["ret"], u["cost"])


# ---------------------------------------------------------------- 공통

MARKETS = {
    "KR": {"universe": kr_universe, "variants": ("K1", "K2"), "base": "K0", "index": "K200"},
    "US": {"universe": us_universe, "variants": ("X2", "X3"), "base": "X0", "index": "SPY"},
}


def book(market: str, u: dict, pred: pd.DataFrame, variant: str, *, control: bool = False,
         seed: int = 0) -> tuple[pd.Series, dict]:
    if market == "KR":
        return kr_book(u, pred, variant)
    if control:
        score = us_scores(seed)            # AX 와 같은 점수(M1 합성)
    else:
        score = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index()
    return us_book(u, score, variant)


def summarize(daily: pd.Series, bench: pd.Series, extra: dict) -> dict:
    b = bench.reindex(daily.index).fillna(0.0)
    m = metrics(daily, b)
    for label, part in (("box", daily.index <= BOX_END), ("rally", daily.index > BOX_END)):
        m[f"{label}_ann"] = float(daily[part].mean() * ANN)
    return {**m, **extra}


def precheck(market: str, u: dict) -> int:
    counts = u["panel"].groupby("session").size()
    bl = blocks(u["sessions"])
    first, last = u["sessions"][bl[0][0]], u["sessions"][bl[-1][1]]
    cover_ok = first <= LATEST_START[market] and last >= EARLIEST_END
    print(f"[{market}] ① 우주 세션 {len(u['sessions'])}/{u['n_all']} · 종목 수 최소 {counts.min()} · 중앙 {int(counts.median())} · 최대 {counts.max()} · "
          f"판정 {first}~{last} ({len(bl)}블록) → {'덮음' if cover_ok else '못 덮음'}", flush=True)
    if not cover_ok:
        print(f"[{market}] 판정 창을 못 덮는다 — 측정하지 않는다", flush=True)
        return 2
    pred = walk(u["panel"], u["sessions"], u["feats"], "y5", bl[:PRECHECK_BLOCKS], seeds=(0,), label=f"{market} seed0",
                min_data=MIN_DATA)
    ctrl = (kr_control if market == "KR" else us_control)(0)
    corr = rank_corr(pred, ctrl)
    print(f"[{market}] ② 첫 {PRECHECK_BLOCKS}블록 seed 0 · 대형주 랭커 대 현행 랭커 순위상관 {corr:+.3f} · IC(우주 안) 대형주 {ic_in(pred, u['panel']):+.4f} "
          f"대 현행 {ic_in(ctrl[ctrl['session'].isin(pred['session'].unique())], u['panel']):+.4f} → "
          f"{'측정하지 않는다(같은 신호)' if corr > PRECHECK_MAX_CORR else '측정 진행'}", flush=True)
    return 3 if corr > PRECHECK_MAX_CORR else 0


def judge(market: str, u: dict, *, smoke: bool = False) -> tuple[list[str], str]:
    """smoke 면 2블록·seed 0 으로 배선만 돈다(수치는 버린다 — 등록 전 결과 엿보기 금지)."""
    spec = MARKETS[market]
    bl = blocks(u["sessions"])[:2] if smoke else blocks(u["sessions"])
    res: dict[str, dict[int, dict]] = {}
    ics: dict[str, list[float]] = {"BC": [], "현행": []}
    seeds = SEEDS[:1] if smoke else SEEDS
    for s in seeds:
        pred = walk(u["panel"], u["sessions"], u["feats"], "y5", bl, seeds=(s,), label=f"{market} seed{s}", min_data=MIN_DATA)
        days = set(pred["session"])
        ctrl = (kr_control if market == "KR" else us_control)(s)
        ics["BC"].append(ic_in(pred, u["panel"]))
        ics["현행"].append(ic_in(ctrl[ctrl["session"].isin(days)], u["panel"]))
        for v in (spec["base"], *spec["variants"]):
            daily, extra = book(market, u, pred, v)
            res.setdefault(v, {})[s] = summarize(daily, u["bench"], extra)
        for v in spec["variants"]:
            daily, extra = book(market, u, ctrl, v, control=True, seed=s)
            daily = daily[daily.index >= min(days)]
            res.setdefault(f"{v}·현행", {})[s] = summarize(daily, u["bench"], extra)

    def avg(v: str, k: str) -> float:
        return float(np.mean([res[v][s][k] for s in seeds]))

    idx = spec["index"]
    lines = [f"### {market} — 판정 {u['sessions'][bl[0][0]]}~{u['sessions'][bl[-1][1]]} · IC(우주 안 h5) BC {np.mean(ics['BC']):+.4f} · 현행 {np.mean(ics['현행']):+.4f}",
             f"| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR({idx}) | 액티브 셰어 | 회전 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for v in res:
        lines.append(f"| {v} | {avg(v, 'ann'):+.1%} | {avg(v, 'box_ann'):+.1%} | {avg(v, 'rally_ann'):+.1%} | {avg(v, 'sharpe'):+.2f} | "
                     f"{avg(v, 'mdd'):.1%} | {avg(v, 'beta'):.2f} | {avg(v, 'ir'):+.2f} | {avg(v, 'active'):.0%} | {avg(v, 'turn'):.1f} |")
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    base = spec["base"]
    passed, promote = [], []
    for v in spec["variants"]:
        wins = sum(res[v][s]["ann"] > res[base][s]["ann"] for s in seeds)
        gap = avg(v, "ann") - avg(base, "ann")
        c = (gap >= GATE_MEAN, wins == len(seeds),
             avg(v, "box_ann") >= avg(base, "box_ann") + GATE_REGIME and avg(v, "rally_ann") >= avg(base, "rally_ann") + GATE_REGIME,
             avg(v, "mdd") >= avg(base, "mdd") - GATE_MDD)
        vs_cur = avg(v, "ann") - avg(f"{v}·현행", "ann")
        lines.append(f"{market} {v}: ①평균 {gap:+.1%}p (≥+1%p) {mark(c[0])} · ②{wins}/{len(seeds)} {mark(c[1])} · "
                     f"③박스 {avg(v, 'box_ann') - avg(base, 'box_ann'):+.1%}p·급등 {avg(v, 'rally_ann') - avg(base, 'rally_ann'):+.1%}p (≥−1%p) {mark(c[2])} · "
                     f"④MDD {avg(v, 'mdd'):.1%} vs {avg(base, 'mdd'):.1%} {mark(c[3])} · 현행 랭커 기울기 대비 {vs_cur:+.1%}p → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((gap, v))
        if wins == len(seeds) and gap >= PROMOTE_MEAN:
            promote.append(v)
    if smoke:
        return [f"[{market}] smoke 통과 — 표 {len(lines)}줄 · 수치는 찍지 않는다"], "smoke"
    verdict = f"{market} 채택 {max(passed)[1]}" if passed else f"{market} 기각"
    if not passed and promote:
        verdict += f"(shadow 승격 후보 {'·'.join(promote)})"
    return lines, verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", choices=("KR", "US", "both"), default="both")
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="판정 경로 배선 확인(2블록·seed 0, 수치 없음)")
    args = parser.parse_args(argv)
    if args.save and (args.precheck or args.smoke or args.market != "both"):
        parser.error("--save 는 --market both 판정에서만(등록 1회)")
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 BC — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    markets = ("KR", "US") if args.market == "both" else (args.market,)
    lines, verdicts = [], []
    for market in markets:
        u = MARKETS[market]["universe"](store)
        if args.precheck:
            rc = precheck(market, u)
            if rc:
                return rc
            continue
        part, verdict = judge(market, u, smoke=args.smoke)
        del u
        lines += [*part, ""]
        verdicts.append(verdict)
        print("\n" + "\n".join(part), flush=True)
    if args.precheck or args.smoke:
        return 0
    verdict = " · ".join(verdicts)
    print(f"\n판정: {verdict}", flush=True)
    if args.save:
        summary = [ln for ln in lines if ln.startswith(("KR ", "US "))]
        record(store, entity="largecap-ranker-2026-10:BC", source="trial_largecap_ranker", family="ranker",
               digest=digest, verdict=verdict, lines=summary)
        print(f"research_trials 기록: ranker/BC · protocol {digest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
