"""번역 손실표 · BE2 재평가 · 정답 재정의 대조 — docs/diag/translation-loss-2026-10.md 의 원본을 만든다.

    # (무거움 — 연구 슬롯 잠금 안, 금지 창 밖)
    .venv/bin/python tools/eval_ladder.py context                 # 공통 자료(가격·명단·벤치·V6·시총·업종, ≤ 2026-09-30)
    .venv/bin/python tools/diag_translation_loss.py early         # 7/1~9/30 얼린 BE2·C1·C0 채점(실전 fa_features 경로)
    .venv/bin/python tools/diag_translation_loss.py scores        # 점수 파일 C0·C1·BE2 잇기(가벼움)
    .venv/bin/python tools/diag_translation_loss.py retarget      # 정답 재정의 GBM T0·T1·T2 × 시드 5 워크포워드(무거움)
    .venv/bin/python tools/diag_translation_loss.py report        # 손실표·사다리·레버 → data/_diag/eval-ladder/report.md

**진단이지 시행이 아니다** — 합격선 없음, `research_trials` 에 적지 않는다, 창고·실전 설정·모델 폴더에 쓰지 않는다(읽기만).
**날짜 상한 2026-09-30** 은 `eval_ladder.DATE_CAP` 이 막는다 — 10/1 뒤 가격·라벨은 계산하지 않는다(금고 second 봉인).

점수 원천(새 학습은 retarget 만):
- C0 이음: 2021-12-29~2023-02-14 하락장 검진 예측(국장 단독) · 2023-02-15~2026-06-29 마지막 모델 회차 C0(국장+미장 합동, 국장 행) ·
  2026-07-01~ 얼린 C0(`data/models/be2` 사이드카 c0, 6/30 까지 학습) × 실전 `fa_features`. 이음매 둘.
- C1: 회차 C1(2023-02-15~) · 얼린 C1(7/1~). BE2: 회차 BE1 판정 예측과 C1 의 세션 안 백분위 평균(회차 규칙 `rank_average`) · 얼린 BE2(7/1~).
- T0·T1·T2(정답 재정의): 국장 6점수(C0 와 같은 피처) GBM, 시행 L 하이퍼파라미터, C0 블록·간격 — 정답만 다르다.
  T0 = h5 수익의 세션 안 rank-gauss(현행 정답, 국장 단독 재현) · T1 = (r5 − β·K200 r5) 의 rank-gauss ·
  T2 = 세션마다 r5 를 [β, log 시총, 업종(KSIC 중분류)] 에 회귀한 잔차의 rank-gauss. 세 정답은 **같은 행**(β·시총이 있는 행)에서 배운다.
  ※ 'K200 대비 초과수익의 세션 안 순위' 는 현행 정답과 **수학적으로 같다**(그날 K200 수익은 모든 종목에 같은 상수) — 그래서 β 조정판을 쓴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import eval_ladder as el  # noqa: E402

OUT = el.OUT
SCORES = el.SCORES
EARLY_START = date(2026, 7, 1)
SPLICE = date(2023, 2, 15)
BEAR_PRED = Path("data/_diag/bear-2022")  # invariant-allow: data-access — 진단 캐시
FINAL = Path("data/_diag/final-round")  # invariant-allow: data-access — 회차 캐시
TAG = "KR+US-20220701-20260630"
BETA_WIN, BETA_MIN = 120, 60
CLIP = (-0.5, 1.0)


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- early — 7/1~9/30 얼린 모델 채점


def cmd_early(root: str, force: bool) -> int:
    el.require_heavy(force)
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import LiveClock
    from quant_rl_trading.store import Store
    from tools import vault_judge as vj

    store = Store(root=Path(root))
    model, c0 = vj.frozen_c1(vj.c1g_hashes(), folder=Path("data/models/be2"))
    sessions = [d for d in trading_days(Market.KR, EARLY_START, el.DATE_CAP) if d <= el.DATE_CAP]
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    for d in sessions:
        if policy.for_session(d) > el.CAP_AS_OF + timedelta(hours=12):
            raise SystemExit(f"{d} as_of {policy.for_session(d)} 가 봉인선 뒤 — 멈춘다")
    log(f"얼린 BE2·C1·C0 채점 — {len(sessions)}세션 {sessions[0]}~{sessions[-1]}")
    preds = vj.be2_predictions(store, sessions, model, c0, as_of_of=policy.for_session)
    for arm, by_seed in preds.items():
        el.write_scores(by_seed, el.score_path(f"early-{arm}", OUT))
        log(f"  {arm}: {sum(len(v) for v in by_seed.values()):,}행")
    return 0


# --------------------------------------------------------------------------- scores — 점수 파일 잇기


def _kr(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame[frame["market"] == "KR"] if "market" in frame.columns else frame
    f = f[["entity_id", "session", "pred"]].copy()
    f["session"] = pd.to_datetime(f["session"]).dt.date
    return f


def _early(arm: str) -> dict[int, pd.DataFrame]:
    return el.read_scores(el.score_path(f"early-{arm}", OUT))


def cmd_scores() -> int:
    from tools.trial_final_transformer import rank_average

    e = {arm: _early(arm) for arm in ("C0", "C1", "BE2")}
    c0, c1, be2 = {}, {}, {}
    for s in el.SEEDS:
        bear = _kr(pd.read_pickle(BEAR_PRED / f"pred-seed{s}.pkl"))  # invariant-allow: data-access — 진단 캐시
        f0 = pd.read_pickle(FINAL / f"pred-C0-seed{s}-{TAG}.pkl")  # invariant-allow: data-access — 회차 캐시
        f1 = pd.read_pickle(FINAL / f"pred-C1-seed{s}-{TAG}.pkl")  # invariant-allow: data-access — 회차 캐시
        be = pd.read_parquet(FINAL / "BE" / f"seed{s}-judge.parquet")  # invariant-allow: data-access — 회차 캐시
        f1k = f1[f1["market"] == "KR"]
        b2 = _kr(rank_average(be[be["market"] == "KR"], f1k))
        c0[s] = pd.concat([bear[bear["session"] < SPLICE], _kr(f0)[lambda f: f["session"] >= SPLICE], e["C0"][s]], ignore_index=True)
        c1[s] = pd.concat([_kr(f1k), e["C1"][s]], ignore_index=True)
        be2[s] = pd.concat([b2, e["BE2"][s]], ignore_index=True)
        log(f"  시드 {s}: C0 {len(c0[s]):,} · C1 {len(c1[s]):,} · BE2 {len(be2[s]):,}행")
    for name, preds in (("C0", c0), ("C1", c1), ("BE2", be2)):
        el.write_scores(preds, el.score_path(name))
    return 0


# --------------------------------------------------------------------------- retarget — 정답 재정의 GBM


def betas(ctx: el.Context) -> pd.DataFrame:
    """종목별 K200 β — 결정 t 종가까지의 일수익 120일(최소 60) 창."""
    d = ctx.close.pct_change(fill_method=None)
    dk = ctx.k200_close.pct_change(fill_method=None).reindex(d.index)
    mask = d.notna()
    k = mask.mul(dk, axis=0).where(mask)
    mxy = (d * k).rolling(BETA_WIN, min_periods=BETA_MIN).mean()
    mx = d.rolling(BETA_WIN, min_periods=BETA_MIN).mean()
    my = k.rolling(BETA_WIN, min_periods=BETA_MIN).mean()
    vy = (k * k).rolling(BETA_WIN, min_periods=BETA_MIN).mean() - my * my
    return ((mxy - mx * my) / vy.where(vy > 0)).clip(-1.0, 3.0)


def targets(ctx: el.Context, sessions: list[date]) -> pd.DataFrame:
    """T0·T1·T2 의 원값(rank-gauss 전) — entity_id·session·r5·t1·t2. 세 정답이 모두 있는 행만."""
    y = ctx.y5().rename(columns={"target": "r5"})
    y = y[y["session"].isin(set(sessions))]
    b = betas(ctx).stack().rename("beta").reset_index()
    b.columns = ["session", "entity_id", "beta"]
    k5 = ctx.k5().rename("k5")
    caps = ctx.caps.assign(lcap=lambda f: np.log(f["cap"].astype(float)))[["session", "entity_id", "lcap"]]
    m = y.merge(b, on=["session", "entity_id"]).merge(caps, on=["session", "entity_id"])
    m = m.merge(k5, left_on="session", right_index=True)
    m = m.dropna(subset=["r5", "beta", "lcap", "k5"])
    m["r5"] = m["r5"].clip(*CLIP)
    m["t1"] = m["r5"] - m["beta"] * m["k5"]
    m["sector"] = m["entity_id"].map(ctx.sectors).fillna("NA")
    resid = []
    for _day, part in m.groupby("session"):
        X = pd.get_dummies(part["sector"], drop_first=False, dtype=float)
        X["beta"] = part["beta"].to_numpy()
        X["lcap"] = part["lcap"].to_numpy() - part["lcap"].mean()
        coef, *_ = np.linalg.lstsq(X.to_numpy(float), part["r5"].to_numpy(float), rcond=None)
        resid.append(pd.Series(part["r5"].to_numpy(float) - X.to_numpy(float) @ coef, index=part.index))
    m["t2"] = pd.concat(resid)
    log(f"정답 행 {len(m):,} · 세션 {m['session'].nunique()} · 업종 {m['sector'].nunique()}종(모름 {float((m['sector'] == 'NA').mean()):.0%})")
    return m[["entity_id", "session", "r5", "t1", "t2"]]


def cmd_retarget(force: bool, seeds: tuple[int, ...], redo: bool = False) -> int:
    el.require_heavy(force)
    from tools.trial_pooled_rank import rank_gauss

    ctx = el.load_context()
    panel, sessions = el.score_panel(ctx)
    tg = targets(ctx, sessions)
    panel = panel.merge(tg, on=["entity_id", "session"], how="left")
    have = panel["r5"].notna()
    for col in ("r5", "t1", "t2"):
        panel[f"y_{col}"] = panel[col]
    panel = rank_gauss(panel, ["y_r5", "y_t1", "y_t2"])
    for col in ("r5", "t1", "t2"):
        panel[f"y_{col}"] = panel[f"y_{col}"].where(have)       # rank_gauss 는 결측을 0 으로 채운다 — 정답 결측은 그대로
    panel = panel.drop(columns=["r5", "t1", "t2"])
    log(f"패널 {len(panel):,}행 · 세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 정답 있는 행 {int(have.sum()):,}")
    for name, col in (("T0", "y_r5"), ("T1", "y_t1"), ("T2", "y_t2")):
        path = el.score_path(name)
        if path.exists() and not redo:
            log(f"{name} 있음 — 건너뜀")
            continue
        preds = el.walk_kr(panel, sessions, el.SCORE_FEATS, col, seeds, label=name, progress=log)
        el.write_scores(preds, path)
        log(f"{name} 저장 {path}")
    return 0


# --------------------------------------------------------------------------- report


def _ann_pair(a: pd.Series, b: pd.Series, ctx: el.Context) -> dict[str, Any]:
    idx = a.index.intersection(b.index)
    return el.describe(a.reindex(idx) - b.reindex(idx), ctx)


def levers(c0: dict[int, pd.DataFrame], ctx: el.Context) -> dict[str, Any]:
    """레버 후보 — 같은 C0 시드 5, 같은 창. 기준(현행) 대비 '비용 후 − K200' 과 '비용 후 − U' 의 개선(짝 차)."""
    def seed_mean(n: int, mult: int, every: int, theta: float | None) -> tuple[pd.Series, pd.Series]:
        nets, turns = [], []
        for p in c0.values():
            net, turn = el.book(p, ctx, n=n, mult=mult, every=every, theta=theta)
            nets.append(net)
            turns.append(turn)
        return pd.concat(nets, axis=1).mean(axis=1), pd.concat(turns, axis=1).mean(axis=1)

    configs = {
        "현행 N24·R10·3N": (24, 3, 10, None),
        "회전 N24·R20·3N": (24, 3, 20, None),
        "비용 인지 N100·R20·5N·θ1": (100, 5, 20, 1.0),
        "넓게 N50·R10·3N": (50, 3, 10, None),
        "넓게 N100·R10·3N": (100, 3, 10, None),
        "넓게 N200·R10·3N": (200, 3, 10, None),
    }
    books = {k: seed_mean(*v) for k, v in configs.items()}
    bn, bt = el.book(el.bundle(c0), ctx)
    books["묶음(5시드 점수 평균) N24·R10·3N"] = (bn, bt)
    base_net = books["현행 N24·R10·3N"][0]
    out: dict[str, Any] = {}
    for name, (net, turn) in books.items():
        days = list(net.index)
        b = ctx.bench.reindex(days).fillna(0.0)
        out[name] = {"vs_k200": el.describe(net - b, ctx), "vs_uni": el.describe(net - ctx.uni.reindex(days), ctx),
                     "v6_vs_k200": el.describe(el.v6(net, ctx) - b, ctx),
                     "gain_vs_base": _ann_pair(net, base_net, ctx), "turn": float(turn.sum() * el.ANN / len(days)),
                     "mdd": el.mdd(net)}
    out.update(index_minus_losers(c0, ctx))
    return out


def index_minus_losers(c0: dict[int, pd.DataFrame], ctx: el.Context, *, top: int = 200, q: float = 0.10,
                       cap_limit: float = 0.30) -> dict[str, Any]:
    """지수 − 패자 — 대용 지수(그날 거래가능 시총 상위 200, 시총가중, 종목 상한 30%) 와 그 안 C0(EMA5, 시드 평균 점수) 하위 10% 를 뺀 것.
    둘 다 R10 재조정·드리프트·편도 0.41%. 실제 K200 대비와 대용 대비를 같이 적는다."""
    from tools.trial_p1a_prime import capped, static_book

    caps = {d: p.set_index("entity_id")["cap"] for d, p in ctx.caps.groupby("session")}
    score = el.bundle(c0).pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=el.SPAN).mean()
    days = [d for d in score.index if d in ctx.ret.index and d in caps]

    def members(day: date) -> pd.Series:
        c = caps[day]
        ok = ctx.trad.get(day)
        if ok:
            c = c[c.index.isin(ok)]
        return c.sort_values(ascending=False).iloc[:top].astype(float)

    def proxy(day: date) -> pd.Series | None:
        m = members(day)
        return capped(m, cap_limit) if len(m) else None

    def minus(day: date) -> pd.Series | None:
        m = members(day)
        s = score.loc[day].reindex(m.index).dropna()
        if len(s) < 20:
            return capped(m, cap_limit)
        drop = set(s[s <= s.quantile(q)].index)
        keep = m[~m.index.isin(drop)]
        return capped(keep, cap_limit) if len(keep) else None

    px, pt = static_book(days, ctx.ret, proxy, every=el.EVERY)
    mn, mt = static_book(days, ctx.ret, minus, every=el.EVERY)
    b = ctx.bench.reindex(px.index).fillna(0.0)
    return {
        "대용 지수(시총 상위 200·상한 30%)": {"vs_k200": el.describe(px - b, ctx), "turn": float(pt.sum() * el.ANN / len(px)),
                                       "te": float((px - b).std() * np.sqrt(el.ANN)), "mdd": el.mdd(px)},
        "지수−패자(대용 − C0 하위 10%)": {"vs_k200": el.describe(mn - b, ctx), "vs_proxy": _ann_pair(mn, px, ctx),
                                    "v6_vs_k200": el.describe(el.v6(mn, ctx) - b, ctx),
                                    "turn": float(mt.sum() * el.ANN / len(mn)), "mdd": el.mdd(mn)},
    }


def cmd_report(force: bool) -> int:
    el.require_heavy(force)
    ctx = el.load_context()
    out: dict[str, Any] = {"cap": str(el.DATE_CAP)}
    md: list[str] = [f"# 번역 손실 진단 원본 — 날짜 상한 {el.DATE_CAP}", ""]
    c0 = el.read_scores(el.score_path("C0"))
    # A — C0 최장 구간
    lt = el.loss_table(c0, ctx)
    out["A_C0_long"] = lt
    md += [el.render_loss("C0 이음(최장)", lt), ""]
    lv = levers(c0, ctx)
    out["levers"] = lv
    md += ["### 레버 — C0 시드 5, 같은 창", "", "| 레버 | 대 K200 (t) | 특수 뺀 쪽 | 대 U (t) | +V6 대 K200 | 현행 대비 짝 차 (t) | 회전 | MDD |",
           "|---|---|---|---|---|---|---|---|"]
    for name, v in lv.items():
        k = v["vs_k200"]
        u = v.get("vs_uni")
        g = v.get("gain_vs_base") or v.get("vs_proxy")
        v6 = v.get("v6_vs_k200")
        md.append(f"| {name} | {k['ann']:+.1%} ({k['t']:+.2f}) | {k['ex_special']['ann']:+.1%} ({k['ex_special']['t']:+.2f}) | "
                  f"{(f'{u['ann']:+.1%} ({u['t']:+.2f})') if u else '—'} | {(f'{v6['ann']:+.1%}') if v6 else '—'} | "
                  f"{(f'{g['ann']:+.1%} ({g['t']:+.2f})') if g else '—'} | {v.get('turn', float('nan')):.1f} | {v.get('mdd', float('nan')):+.1%} |")
    md.append("")
    # BE2 재평가 — 공통 창
    arms = {"C0": c0, "C1": el.read_scores(el.score_path("C1")), "BE2": el.read_scores(el.score_path("BE2"))}
    common = {k: el.restrict(v, SPLICE, None) for k, v in arms.items()}
    out["BE2"] = {}
    for name, preds in common.items():
        lt = el.loss_table(preds, ctx)
        out["BE2"][f"loss_{name}"] = lt
        md += [el.render_loss(f"{name} (공통 창)", lt), ""]
    for name, ctrl in (("C1", "C0"), ("BE2", "C0"), ("BE2", "C1")):
        res = el.ladder(common[name], ctx, control=common[ctrl], n_tests=3)
        out["BE2"][f"ladder_{name}_vs_{ctrl}"] = res
        md += [el.render(f"{name} 대 {ctrl} (공통 창 {SPLICE}~)", res), ""]
        md += _seed_lines(res)
    # C — 정답 재정의
    tpaths = {k: el.score_path(k) for k in ("T0", "T1", "T2")}
    if all(p.exists() for p in tpaths.values()):
        ts = {k: el.read_scores(p) for k, p in tpaths.items()}
        lo = max(min(min(p["session"]) for p in v.values()) for v in ts.values())
        ts = {k: el.restrict(v, lo, None) for k, v in ts.items()}
        out["C"] = {}
        for name, preds in ts.items():
            lt = el.loss_table(preds, ctx)
            out["C"][f"loss_{name}"] = lt
            md += [el.render_loss(f"{name} 정답 재정의", lt), ""]
        for name in ("T1", "T2"):
            res = el.ladder(ts[name], ctx, control=ts["T0"], n_tests=2)
            out["C"][f"ladder_{name}"] = res
            md += [el.render(f"{name} 대 T0", res), ""]
            md += _seed_lines(res)
        c0w = el.restrict(c0, lo, None)
        res = el.ladder(ts["T0"], ctx, control=c0w, n_tests=1)
        out["C"]["ladder_T0_vs_C0"] = res
        md += [el.render("T0(국장 단독 재현) 대 C0 이음 — 재현 검산", res), ""]
    else:
        md.append("(정답 재정의 점수 없음 — retarget 를 먼저)")
    (OUT / "report.json").write_text(json.dumps(el._jsonable(out), ensure_ascii=False, indent=1))
    (OUT / "report.md").write_text("\n".join(md))
    log("\n".join(md))
    return 0


def _seed_lines(res: dict[str, Any]) -> list[str]:
    seeds = res["port"]["seeds"]
    ic = res["principle"]["ic_seed"]
    dic = res.get("marginal", {}).get("dic_seed", {})
    lines = ["| 시드 | IC | ΔIC | 비용 후 대 K200 | 대 U | MDD | 회전 |", "|---|---|---|---|---|---|---|"]
    for s, v in seeds.items():
        lines.append(f"| {s} | {ic.get(s, float('nan')):+.4f} | {dic.get(s, float('nan')):+.4f} | {v['vs_k200']['ann']:+.1%} | "
                     f"{v['vs_uni']['ann']:+.1%} | {v['mdd']:+.1%} | {v['turn']:.1f} |")
    bp = res["port"]["bundle"]
    lines.append(f"| 묶음 | — | — | {bp['vs_k200']['ann']:+.1%} | {bp['vs_uni']['ann']:+.1%} | {bp['mdd']:+.1%} | {bp['turn']:.1f} |")
    return [*lines, ""]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("early", "scores", "retarget", "report"))
    ap.add_argument("--root", default="data")
    ap.add_argument("--force", action="store_true", help="실행 창·메모리 관문 무시(사람이 판단했을 때만)")
    ap.add_argument("--seeds", type=int, nargs="*", default=list(el.SEEDS))
    ap.add_argument("--redo", action="store_true", help="retarget: 이미 있는 점수 파일도 다시 굽는다")
    args = ap.parse_args(argv)
    if args.cmd == "early":
        return cmd_early(args.root, args.force)
    if args.cmd == "scores":
        return cmd_scores()
    if args.cmd == "retarget":
        return cmd_retarget(args.force, tuple(args.seeds), args.redo)
    return cmd_report(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
