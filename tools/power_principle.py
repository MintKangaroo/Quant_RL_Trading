"""원리 시험 검정력 — 짝 ΔIC(처리 − 대조, 거래가능 전 종목 횡단면 h5 순위 IC)의 30세션 블록 분포로 문턱·검정력을 잰다.

    .venv/bin/python tools/power_principle.py --case c1g             # C1(GBM·FA) 대 C0(6점수 GBM), 국장
    .venv/bin/python tools/power_principle.py --case c1g --block 30 --deltas 0.005 0.011 0.022

새 기준 틀(docs/protocols/_template-principle-test-2026-10.md)의 등록 전 계산. 수익·IC 를 **금고 밖 자료로만** 본다:

- **판정 창(2022-07~2026-06)**: 마지막 모델 회차의 워크포워드 예측 `data/_diag/final-round/pred-{C0,C1}-seed{0..4}-…pkl`(OOS) ×
  연구 타깃 `data/_diag-long/targets-KR-h5.pkl` × 연구 거래가능 명단 `tradable-KR.pkl`.
- **이제 탐색 자료가 된 금고 early(2026-07-01~)**: 얼린 C1·C0(등록 해시 대조) × 창고 `fa_features`(실전 경로) × 라벨 `ic.build_targets(as_of=9/30 장 마감 뒤)`.
  **h5 라벨이 9/30 종가 안에서 닫히는 세션만**(t+6 ≤ 9/30) — 10/1 이후 가격(금고 second 창)을 한 칸도 쓰지 않는다.
  거래가능 명단 = 실전 함수(`Analyst.tradable_entities`, 그 세션 공표 as_of).

출력: ① 일별 ΔIC 의 평균·NW t(전 구간) ② 30세션 블록의 평균·표준편차, **귀무(평균을 뺀 계열)에서 t ≥ 문턱의 비율(1종 오류)**과
**효과 δ 를 더했을 때 t ≥ 문턱의 비율(검정력)** ③ 시드별 부호 일관(5 중 k) 의 귀무·대립 비율 ④ 보조 — 십분위 스프레드(D10−D1) 짝 차이
⑤ 해 방지 여유 — 상위 24 포트(EMA5·완충 3N·10세션·편도 0.41%)의 30세션 연수익 차·MDD 차·회전 비의 블록 표준편차(판정 창).

블록은 겹치지 않게 잘라 시작점을 5세션씩 옮긴다(겹침 블록 — 분포 모양을 촘촘히, 독립 표본 수는 세션/30 이다).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402

PRED = Path("data/_diag/final-round")  # invariant-allow: data-access — 연구 캐시(OOS 예측)
LONG = Path("data/_diag-long")  # invariant-allow: data-access — 연구 캐시(타깃·명단)
SEEDS = (0, 1, 2, 3, 4)
LAG = 4
EARLY_START, EARLY_LAST_CLOSE = date(2026, 7, 1), date(2026, 9, 30)


def nw_t(x: np.ndarray, lag: int = LAG) -> float:
    s = pd.Series(x).dropna()
    return float(ic_module.newey_west_t(s, lag=lag)) if len(s) > lag + 2 else float("nan")


def session_stats(pred: pd.DataFrame, target: pd.DataFrame, tradable: dict[date, set[str]]) -> pd.DataFrame:
    """세션별 Spearman IC 와 십분위 스프레드(타깃 백분위의 상위 10% 평균 − 하위 10% 평균), 거래가능 종목만."""
    m = pred.merge(target, on=["entity_id", "session"], how="inner")
    keep = np.array([e in tradable.get(s, ()) for e, s in zip(m["entity_id"], m["session"], strict=True)])
    m = m[keep]
    g = m.groupby("session")
    m = m.assign(rp=g["pred"].rank(pct=True), rt=g["target"].rank(pct=True))
    ic = m.groupby("session")[["rp", "rt"]].corr().xs("rp", level=1)["rt"]
    top = m[m["rp"] > 0.9].groupby("session")["rt"].mean()
    bot = m[m["rp"] <= 0.1].groupby("session")["rt"].mean()
    return pd.DataFrame({"ic": ic, "spread": top - bot, "n": g.size()})


def blocks(n: int, length: int, step: int = 5) -> list[tuple[int, int]]:
    return [(a, a + length) for a in range(0, n - length + 1, step)]


def block_power(daily: pd.Series, *, length: int, deltas: list[float], thresholds: list[float]) -> pd.DataFrame:
    """귀무 = 전 구간 평균을 뺀 계열, 대립 = 거기에 δ 를 더한 계열. 블록마다 NW t ≥ 문턱의 비율."""
    x = daily.dropna().to_numpy(float)
    x0 = x - x.mean()
    rows = []
    for delta in [0.0, *deltas]:
        ts = np.array([nw_t(x0[a:b] + delta) for a, b in blocks(len(x0), length)])
        rows.append({"δ": delta, **{f"t≥{t:g}": float(np.mean(ts >= t)) for t in thresholds},
                     "평균>0": float(np.mean([np.mean(x0[a:b] + delta) > 0 for a, b in blocks(len(x0), length)]))})
    return pd.DataFrame(rows)


def seed_sign_power(per_seed: pd.DataFrame, *, length: int, deltas: list[float], need: int) -> dict[float, float]:
    """시드별 ΔIC 블록 평균이 양수인 시드 수 ≥ need 인 블록 비율(귀무 = 시드마다 평균을 뺀 것)."""
    x = per_seed.dropna().to_numpy(float)
    x0 = x - x.mean(axis=0)
    out = {}
    for delta in [0.0, *deltas]:
        hits = [int(((x0[a:b] + delta).mean(axis=0) > 0).sum()) >= need for a, b in blocks(len(x0), length)]
        out[delta] = float(np.mean(hits))
    return out


# --------------------------------------------------------------------------- 자료


def research_ic(seeds: tuple[int, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """판정 창 KR — 시드별 (ΔIC, Δspread) 세션 표."""
    target = pd.read_pickle(LONG / "targets-KR-h5.pkl")  # invariant-allow: data-access — 연구 캐시
    target["session"] = pd.to_datetime(target["session"]).dt.date
    trad_frame = pd.read_pickle(LONG / "tradable-KR.pkl")  # invariant-allow: data-access — 연구 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    tradable = {d: set(p["entity_id"]) for d, p in trad_frame.groupby("session")}
    del trad_frame
    dic, dsp = {}, {}
    for s in seeds:
        stats = {}
        for arm in ("C1", "C0"):
            p = pd.read_pickle(PRED / f"pred-{arm}-seed{s}-KR+US-20220701-20260630.pkl")  # invariant-allow: data-access — 연구 캐시
            p = p[p["market"] == "KR"][["entity_id", "session", "pred"]]
            p["session"] = pd.to_datetime(p["session"]).dt.date
            stats[arm] = session_stats(p, target, tradable)
            del p
        dic[s] = stats["C1"]["ic"] - stats["C0"]["ic"]
        dsp[s] = stats["C1"]["spread"] - stats["C0"]["spread"]
        print(f"  판정 창 시드 {s}: 세션 {dic[s].notna().sum()} · ΔIC 평균 {dic[s].mean():+.4f} · 종목 중앙값 {int(stats['C1']['n'].median())}",
              flush=True)
    return pd.DataFrame(dic).sort_index(), pd.DataFrame(dsp).sort_index()


def early_ic(store, seeds: tuple[int, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """금고 early(이제 탐색 자료) — 얼린 C1·C0 × 실전 fa_features. 라벨은 9/30 종가 안에서 닫히는 세션만."""
    from quant_rl_trading.analysts import fa_features
    from quant_rl_trading.analysts.regime import RegimeAnalyst
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import LiveClock, ReplayClock
    from tools import vault_judge as vj

    model, c0 = vj.frozen_c1(vj.c1g_hashes(), folder=Path("data/models/be2"))
    days = list(trading_days(Market.KR, EARLY_START, EARLY_LAST_CLOSE))
    sessions = days[:-6]                                   # t+1 진입 · t+6 청산이 9/30 안에 든다
    label_as_of = datetime.combine(EARLY_LAST_CLOSE, time(23, 0), tzinfo=UTC) - timedelta(hours=9)   # 9/30 23:00 KST
    target = ic_module.build_targets(store, as_of=label_as_of, lookback=(label_as_of.date() - EARLY_START).days + 40,
                                     horizon=5, market="KR")
    target = target[(target["session"] >= EARLY_START) & (target["session"] <= sessions[-1])][["entity_id", "session", "target"]]
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    analyst = RegimeAnalyst(store, LiveClock(), market=Market.KR)
    rows: dict[str, list[pd.DataFrame]] = {f"{arm}{s}": [] for arm in ("C1", "C0") for s in seeds}
    tradable: dict[date, set[str]] = {}
    for day in sessions:
        as_of = policy.for_session(day)
        analyst.clock = ReplayClock(as_of)
        tradable[day] = set(analyst.tradable_entities(as_of, lookback=400) or ())
        hist = fa_features.read_window(store, as_of=as_of, market="KR", lookback_days=3)
        today = hist[hist["session"] == day]
        if today.empty:
            raise SystemExit(f"{day} FA 행이 없다 — 창을 줄이지 않는다")
        ents = today["entity_id"].astype(str).to_numpy()
        flat = today[list(model.features)].to_numpy(np.float32)
        x0 = today[list(vj.C0_FEATURES)].to_numpy(np.float32)
        for s in seeds:
            rows[f"C1{s}"].append(pd.DataFrame({"entity_id": ents, "session": day, "pred": model.predict_gbm(s, flat)}))
            rows[f"C0{s}"].append(pd.DataFrame({"entity_id": ents, "session": day, "pred": c0[s].predict(x0)}))
    dic, dsp = {}, {}
    for s in seeds:
        a = session_stats(pd.concat(rows[f"C1{s}"]), target, tradable)
        b = session_stats(pd.concat(rows[f"C0{s}"]), target, tradable)
        dic[s], dsp[s] = a["ic"] - b["ic"], a["spread"] - b["spread"]
    print(f"  early(탐색) 세션 {len(sessions)}({sessions[0]}~{sessions[-1]}) · ΔIC 시드 평균 {pd.DataFrame(dic).mean(axis=1).mean():+.4f}",
          flush=True)
    return pd.DataFrame(dic).sort_index(), pd.DataFrame(dsp).sort_index()


def _mdd(r: pd.Series) -> float:
    nav = (1 + r).cumprod()
    return float((nav / nav.cummax() - 1).min())


def portfolio_margins(store, seeds: tuple[int, ...], length: int) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """판정 창 상위 24 포트 — 블록별 (연수익 차, MDD 차, 회전 비), 시드 평균."""
    from tools.trial_ranker_kit import market_data, portfolio

    sessions = None
    by_seed = []
    for s in seeds:
        legs = {}
        for arm in ("C1", "C0"):
            p = pd.read_pickle(PRED / f"pred-{arm}-seed{s}-KR+US-20220701-20260630.pkl")  # invariant-allow: data-access — 연구 캐시
            p = p[p["market"] == "KR"][["entity_id", "session", "pred"]]
            p["session"] = pd.to_datetime(p["session"]).dt.date
            if sessions is None:
                sessions = sorted(p["session"].unique())
                ret, _, trad = market_data(store, sessions, cache=LONG)
            tl: dict[date, float] = {}
            daily, extra = portfolio(p, ret, trad, every=10, turn_log=tl)
            legs[arm] = (daily.dropna(), extra, pd.Series(tl).sort_index())
            del p
        a, b = legs["C1"][0], legs["C0"][0]
        idx = a.index.intersection(b.index)
        a, b = a.loc[idx], b.loc[idx]
        ta, tb = legs["C1"][2].reindex(idx).fillna(0.0), legs["C0"][2].reindex(idx).fillna(0.0)
        rows = []
        for i, j in blocks(len(idx), length):
            ra, rb = a.iloc[i:j], b.iloc[i:j]
            tb_sum = float(tb.iloc[i:j].sum())
            rows.append({"ann": (ra.mean() - rb.mean()) * 252, "mdd": _mdd(ra) - _mdd(rb),
                         "turn": float(ta.iloc[i:j].sum()) / tb_sum if tb_sum > 0 else float("nan")})
        frame = pd.DataFrame(rows)
        by_seed.append(frame)
        print(f"  포트 시드 {s}: 판정 창 연 C1 {a.mean() * 252:+.1%} · C0 {b.mean() * 252:+.1%} · 회전 "
              f"{legs['C1'][1].get('turn', float('nan')):.1f} 대 {legs['C0'][1].get('turn', float('nan')):.1f}", flush=True)
    return sum(by_seed) / len(by_seed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default="c1g", choices=["c1g"])
    ap.add_argument("--root", default="data")
    ap.add_argument("--block", type=int, default=30)
    ap.add_argument("--deltas", type=float, nargs="*", default=[0.005, 0.0075, 0.011, 0.015])
    ap.add_argument("--thresholds", type=float, nargs="*", default=[1.0, 1.28, 1.645, 2.0])
    ap.add_argument("--no-early", action="store_true")
    ap.add_argument("--no-portfolio", action="store_true")
    ap.add_argument("--portfolio-only", action="store_true", help="⑤ 해 방지 여유만")
    ap.add_argument("--cwd", default=None, help="상대 경로(data/·docs/)의 기준 디렉터리 — worktree 에서 실전 창고를 읽을 때")
    args = ap.parse_args(argv)
    if args.cwd:
        import os

        os.chdir(args.cwd)
    from quant_rl_trading.store import Store

    store = Store(root=Path(args.root))
    print(f"=== 원리 시험 검정력 · {args.case} · 블록 {args.block}세션 · NW lag {LAG} ===", flush=True)
    if args.portfolio_only:
        return _print_margins(store, args.block)
    dic, dsp = research_ic(SEEDS)
    if not args.no_early:
        e_dic, e_dsp = early_ic(store, SEEDS)
        dic, dsp = pd.concat([dic, e_dic]), pd.concat([dsp, e_dsp])
    mean = dic.mean(axis=1).dropna()
    print(f"\n일별 ΔIC(시드 평균) — 세션 {len(mean)} · 평균 {mean.mean():+.4f} · 표준편차 {mean.std():.4f} · NW t {nw_t(mean.to_numpy()):+.2f}",
          flush=True)
    sd_block = np.std([mean.to_numpy()[a:b].mean() for a, b in blocks(len(mean), args.block)])
    print(f"{args.block}세션 블록 평균 ΔIC 의 표준편차 {sd_block:.4f} → 80%·양측 5% 최소 탐지 ≈ {2.80 * sd_block:.4f}", flush=True)
    print("\n검정력 표(블록 NW t ≥ 문턱인 비율 — δ=0 줄이 1종 오류):")
    print(block_power(mean, length=args.block, deltas=args.deltas, thresholds=args.thresholds).to_string(index=False,
                                                                                                         float_format=lambda v: f"{v:.3f}"))
    print("\n시드 일관(블록 시드별 ΔIC 평균 > 0 인 시드 ≥ k):")
    for need in (4, 5):
        print(f"  k={need}: " + " · ".join(f"δ {d:g} → {p:.2f}" for d, p in
                                         seed_sign_power(dic, length=args.block, deltas=args.deltas, need=need).items()))
    sp = dsp.mean(axis=1).dropna()
    print(f"\n보조 Δ십분위 스프레드(백분위 단위) — 평균 {sp.mean():+.4f} · NW t {nw_t(sp.to_numpy()):+.2f} · "
          f"{args.block}세션 블록 표준편차 {np.std([sp.to_numpy()[a:b].mean() for a, b in blocks(len(sp), args.block)]):.4f}", flush=True)
    if not args.no_portfolio:
        _print_margins(store, args.block)
    return 0


def _print_margins(store, block: int) -> int:  # type: ignore[no-untyped-def]
    print(f"\n해 방지 여유 — 상위 24 포트 {block}세션 블록(판정 창, 시드 평균):", flush=True)
    m = portfolio_margins(store, SEEDS, block)
    for k, label in (("ann", "연수익 차"), ("mdd", "MDD 차")):
        v = m[k].to_numpy()
        print(f"  {label}: 평균 {v.mean():+.3f} · 표준편차 {v.std():.3f} · 귀무 하위 5%(평균 뺀 뒤) {np.quantile(v - v.mean(), 0.05):+.3f}",
              flush=True)
    v = m["turn"].dropna().to_numpy()
    print(f"  회전 비(C1/C0, 블록 합): 평균 {v.mean():.2f} · 중앙값 {np.median(v):.2f} · 95% {np.quantile(v, 0.95):.2f} · "
          f"5% {np.quantile(v, 0.05):.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
