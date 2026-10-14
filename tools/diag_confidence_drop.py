"""랭커·risk 운영 신뢰도 하락 원인 — 금고 early(7/1~9/30) 개봉 뒤 진단 (2026-10-14, 사용자 10/11 "둘 다").

    flock data/_locks/research-slot-1.lock nice -n 10 .venv/bin/python tools/diag_confidence_drop.py --part live
    ... --part split · --part retrain · --part peers

선행: docs/diag/confidence-regime-2026-10-11.md(금고 전 진단, ≤ 2026-06-30). 결과: docs/diag/confidence-drop-2026-10.md.

**상한 2026-09-30.** 모든 라벨·수익은 9/30 장 마감까지의 가격으로만 만든다(as_of = 9/30 16:00 UTC). 결정 t 의 h5 라벨은 c(t+6)/c(t+1) 이라
t+6 > 9/30 인 결정 세션은 라벨이 없다(계산 안 함). 10/1 이후(금고 second) IC·수익은 어디서도 계산하지 않는다. 실전 롤링 값(10/8·10/12)은
메모에 적힌 숫자로만 인용한다.

부분:
- live   : 실전 정의(`ic.rolling_confidence` = signals × build_targets 일별 Spearman, 60 신호 세션 롤링)로 7~9월 일별 IC·롤링 경로.
           ① 극단일(07-28·07-31)을 라벨 창에 품은 세션 · 그날 장세 회귀(2022-07~2026-06 계수로 7~9월 예측) · 창에서 빠져나갈 세션의 몫.
- split  : 연구 공통 자료(eval_ladder context, 거래가능 전 종목)로 ② 대형/중소형 IC · 패자(D1)/승자(D10) 5일 초과.
- retrain: ③ C0 규격(6점수 rank-gauss GBM, 시드 5)으로 6월 말 고정 모델 vs 20세션 블록 재학습 — 7~9월 같은 날 IC.
- peers  : ④ 같은 기간 TTM(제로샷)·TA 합성·BE2·C1 IC.
진단 도구다 — 시행 아님, `research_trials`·창고 쓰기 없음.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402

CAP_DAY = date(2026, 9, 30)
CAP = datetime(2026, 9, 30, 16, 0, tzinfo=UTC)
VAULT_LO = date(2026, 7, 1)
HIST_LO = date(2022, 7, 1)
EXTREME = (date(2026, 7, 28), date(2026, 7, 31))
OUT = Path("logs")
DIAG = Path("data/_diag")  # invariant-allow: data-access — 연구 캐시
H, LAG = ic_module.HORIZON_DAYS, ic_module.ENTRY_LAG_DAYS


def _cap(days, what: str) -> None:
    days = [d for d in days if d is not None]
    if days and max(days) > CAP_DAY:
        raise SystemExit(f"상한 침범({what}): {max(days)} > {CAP_DAY}")


def _nwt(x: pd.Series, lag: int = H - 1) -> float:
    x = x.dropna()
    return float(ic_module.newey_west_t(x, lag=lag)) if len(x) > lag + 2 else float("nan")


def _summ(x: pd.Series) -> dict:
    x = x.dropna()
    return {"n": int(len(x)), "mean": float(x.mean()) if len(x) else None, "nw_t": _nwt(x) if len(x) > 6 else None}


def _write(name: str, obj: dict) -> Path:
    path = OUT / f"diag-confidence-drop-{name}-20261014.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
    print(f"→ {path}", flush=True)
    return path


# --------------------------------------------------------------------------- live


def part_live() -> None:
    from quant_rl_trading.store import Store
    from tools import diag_confidence_regime as R

    R.END, R.END_DAY = CAP, CAP_DAY          # 앞선 도구의 읽기·가드를 상한 9/30 으로
    store = Store(root=Path("data"))  # invariant-allow: data-access — 창고 루트(읽기는 store.get)
    lo = date(2026, 1, 2)
    print("signals 읽기", flush=True)
    sig = R.load_signals(store, {"ranker", "risk"}, lo)
    print("라벨(build_targets as_of 9/30)", flush=True)
    targets = ic_module.build_targets(store, as_of=CAP, lookback=(CAP_DAY - lo).days + 30, market="KR")
    targets["entity_id"] = targets["entity_id"].astype(str)
    idx = R.load_indices(store)
    cal = sorted(idx.index)
    pos = {d: i for i, d in enumerate(cal)}
    lab_max = max(targets["session"])
    _cap([cal[min(pos[lab_max] + LAG + H, len(cal) - 1)]], "라벨 끝")
    state = R.market_state(idx, cal)
    hist = pd.read_csv(OUT / "diag-confidence-regime-20261011.csv", parse_dates=["session"])
    hist["session"] = hist["session"].dt.date
    hist = hist.set_index("session")
    out: dict = {"cap": str(CAP_DAY)}
    for a in ("ranker", "risk"):
        s = sig[sig["analyst"] == a].copy()
        s["entity_id"] = s["entity_id"].astype(str)
        daily = ic_module.daily_ic(s[["entity_id", "session", "score"]].merge(targets, on=["entity_id", "session"], how="inner"))
        sessions = sorted(s["session"].unique())
        path = R.rolling_path(daily, sessions, cal)
        stored = s.groupby("session")["confidence"].median()
        # 앞선 진단(≤6/30)과 겹치는 3~6월 일치 확인
        ov = pd.concat([daily.rename("new"), hist[f"{a}_daily"].rename("old")], axis=1).dropna()
        hd = hist[f"{a}_daily"].dropna()
        hd = hd[hd.index >= HIST_LO]
        v = daily[daily.index >= VAULT_LO]
        months = {m: _summ(v[[d.month == m for d in v.index]]) for m in (7, 8, 9)}
        # ① 극단일을 라벨 창(t+1..t+6)에 품은 결정 세션
        def has_ext(t: date) -> bool:
            i = pos.get(t)
            return i is not None and any(pos.get(e) is not None and i + 1 <= pos[e] <= i + LAG + H for e in EXTREME)
        ext = [t for t in v.index if has_ext(t)]
        # 실전 경로(재계산·저장)와 9/29·9/30 창 구성
        win_rows = {}
        for T in (date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29), date(2026, 9, 30)):
            if T not in pos:
                continue
            i_s = [t for t in sessions if t <= T][-60:]
            ok = [t for t in i_s if t in pos and pos[t] + LAG + H <= pos[T] and t in daily.index]
            okx = [t for t in ok if t not in ext]
            win_rows[str(T)] = {"recomputed": float(daily.loc[ok].mean()) if ok else None, "n": len(ok),
                                "stored": float(stored.get(T, np.nan)), "ex_extreme": float(daily.loc[okx].mean()) if okx else None,
                                "n_extreme_in_window": len(ok) - len(okx), "first": str(ok[0]) if ok else None, "last": str(ok[-1]) if ok else None}
        # 9/29 창에서 다음 7 신호 세션 동안 빠져나갈 가장 오래된 7개(라벨은 다 9/30 전) — 들어올 7개는 라벨이 10월이라 안 잰다
        T = date(2026, 9, 29)
        i_s = [t for t in sessions if t <= T][-60:]
        ok = [t for t in i_s if t in pos and pos[t] + LAG + H <= pos[T] and t in daily.index]
        leaving = ok[:7]
        lv = float(daily.loc[leaving].mean()) if leaving else None
        # 장세 회귀: 2022-07~2026-06 일별 IC ~ 라벨 창 K200 5일·대형−소형 5일 → 7~9월 예측
        X = state[["k200_f5", "ls_f5", "absk_f5"]]
        reg = R.ols_nw(hd, X.reindex(hd.index), lag=H - 1)
        Xv = X.reindex(v.index)
        pred = reg.loc["const", "coef"] + sum(reg.loc[c, "coef"] * Xv[c] for c in X.columns)
        resid = (v - pred).dropna()
        worst = v.sort_values().head(10)
        out[a] = {
            "overlap_check_mar_jun": {"n": len(ov), "corr": float(ov.corr().iloc[0, 1]) if len(ov) > 2 else None,
                                       "mad": float((ov["new"] - ov["old"]).abs().median()) if len(ov) else None},
            "hist_daily": _summ(hd), "hist_by_phase": {
                "2023~24 박스": _summ(hd[(hd.index >= date(2023, 1, 1)) & (hd.index <= date(2024, 12, 31))]),
                "2025~26H1 급등": _summ(hd[hd.index >= date(2025, 1, 1)])},
            "vault_daily": _summ(v), "vault_months": months,
            "extreme_sessions": [str(t) for t in ext], "vault_daily_extreme": _summ(v.loc[ext]),
            "vault_daily_ex_extreme": _summ(v.drop(index=ext)),
            "window": win_rows, "leaving_7_from_0929": {"sessions": [str(t) for t in leaving], "mean_ic": lv},
            "path_vault": {str(k): float(x) for k, x in path[path.index >= date(2026, 6, 1)].items()},
            "stored_vault": {str(k): float(x) for k, x in stored[stored.index >= date(2026, 6, 1)].items()},
            "regression_hist": {"coef": reg["coef"].to_dict(), "t": reg["t"].to_dict(), "r2": reg.attrs["r2"], "n": reg.attrs["n"]},
            "vault_state_mean": {c: float(Xv[c].mean()) for c in X.columns},
            "hist_state_mean": {c: float(X.reindex(hd.index)[c].mean()) for c in X.columns},
            "vault_pred_mean": float(pred.dropna().mean()), "vault_resid": _summ(resid),
            "worst10": [{"session": str(t), "ic": float(x), "k200_f5": float(X.at[t, "k200_f5"]), "ls_f5": float(X.at[t, "ls_f5"])}
                        for t, x in worst.items() if t in X.index],
            "daily_vault": {str(k): float(x) for k, x in v.items()},
        }
        print(a, json.dumps({k: out[a][k] for k in ("vault_daily", "vault_daily_extreme", "vault_daily_ex_extreme", "vault_pred_mean",
                                                    "vault_resid", "leaving_7_from_0929")}, ensure_ascii=False, default=str), flush=True)
    _write("live", out)


# --------------------------------------------------------------------------- split (공통 자료)


def _ctx():
    from tools import eval_ladder as L

    return L, L.load_context()


def _wide_ranker(L, ctx, analyst: str) -> pd.DataFrame:
    """창고 signals 의 실전 점수 — ≤6/30 은 앞선 진단의 signals 읽기, 7~9월은 금고 early 캐시(같은 signals 를 store.get 으로 굽은 것)."""
    from quant_rl_trading.store import Store
    from tools import diag_confidence_regime as R

    R.END, R.END_DAY = CAP, CAP_DAY
    store = Store(root=Path("data"))  # invariant-allow: data-access — 창고 루트(읽기는 store.get)
    s = R.load_signals(store, {analyst}, HIST_LO, columns=("entity_id", "valid_from", "analyst", "score"))
    s = s.rename(columns={"score": "pred"})[["entity_id", "session", "pred"]]
    s["entity_id"] = s["entity_id"].astype(str)
    return L.as_wide(s, ctx)


def _split_stats(L, ctx, w: pd.DataFrame) -> pd.DataFrame:
    """세션별: 전체 IC · 대형(시총 상위 200) IC · 중소형 IC · D1/D10 의 5일 초과(거래가능 동일가중 대비)."""
    caps = ctx.caps.copy()
    caps["session"] = pd.to_datetime(caps["session"]).dt.date
    capw = caps.pivot_table(index="session", columns="entity_id", values="cap", aggfunc="last").reindex(columns=ctx.close.columns)
    idx = w.index.intersection(ctx.close.index).intersection(capw.index)
    P = w.loc[idx].to_numpy(np.float64)
    Y = ctx.y5w().loc[idx].to_numpy(np.float64)
    M = ctx.trad_mask().loc[idx].to_numpy() & ~np.isnan(P) & ~np.isnan(Y)
    C = capw.loc[idx].to_numpy(np.float64)
    Cm = np.where(M, C, np.nan)
    crank = pd.DataFrame(Cm).rank(axis=1, ascending=False).to_numpy()
    big = M & (crank <= 200)
    rows = []
    for i, d in enumerate(idx):
        r = {}
        for name, m in (("all", M[i]), ("big", big[i]), ("small", M[i] & ~big[i])):
            if m.sum() >= 30:
                r[f"ic_{name}"] = pd.Series(P[i, m]).rank().corr(pd.Series(Y[i, m]).rank())
        m = M[i]
        if m.sum() >= 50:
            p, y = P[i, m], Y[i, m]
            ew = y.mean()
            q = pd.Series(p).rank(pct=True).to_numpy()
            r["d1_ex"] = y[q <= 0.1].mean() - ew
            r["d10_ex"] = y[q > 0.9].mean() - ew
            r["n"] = int(m.sum())
        rows.append(pd.Series(r, name=d))
    return pd.DataFrame(rows)


PHASES = {"2022H2~2024 (하락·박스)": (HIST_LO, date(2024, 12, 31)), "2025~26H1 (급등)": (date(2025, 1, 1), date(2026, 6, 30)),
          "7~9월 (금고 early)": (VAULT_LO, CAP_DAY)}


def _by_phase(df: pd.DataFrame) -> dict:
    out = {}
    for k, (lo, hi) in PHASES.items():
        sub = df[(df.index >= lo) & (df.index <= hi)]
        out[k] = {c: _summ(sub[c]) for c in df.columns if c != "n"}
    return out


def part_split() -> None:
    L, ctx = _ctx()
    _cap(ctx.close.index, "공통 자료")
    out: dict = {}
    for a in ("ranker", "risk"):
        print(a, flush=True)
        w = _wide_ranker(L, ctx, a)
        st = _split_stats(L, ctx, w)
        out[a] = _by_phase(st)
        v = st[st.index >= VAULT_LO]
        out[a]["vault_months"] = {m: {c: _summ(v[[d.month == m for d in v.index]][c]) for c in v.columns if c != "n"} for m in (7, 8, 9)}
        print(json.dumps(out[a]["7~9월 (금고 early)"], ensure_ascii=False, default=str), flush=True)
    _write("split", out)


# --------------------------------------------------------------------------- retrain (③)


def part_retrain() -> None:
    import lightgbm as lgb

    from tools import final_round_kit as fkit
    from tools.trial_pooled_rank import rank_gauss

    L, ctx = _ctx()
    panel, sessions = L.score_panel(ctx)
    y5 = ctx.y5()
    panel = panel.merge(y5.rename(columns={"target": "r5"}), on=["entity_id", "session"], how="left")
    panel["y"] = panel["r5"]
    panel = rank_gauss(panel, ["y"])
    panel["y"] = panel["y"].where(panel["r5"].notna())
    feats = list(L.SCORE_FEATS)
    bl = [(f, l) for f, l in fkit.blocks(sessions) if sessions[l] >= VAULT_LO]
    first0 = bl[0][0]
    frozen_end = fkit.train_end(sessions, first0)
    test = panel[panel["session"] >= sessions[first0]]
    Xt = test[feats].to_numpy(np.float32)
    base = test[["entity_id", "session"]].reset_index(drop=True)
    # 6월 말 고정: 7월 첫 블록 학습 끝으로 한 번 배워 7~9월 전부를 채점
    tr = panel[(panel["session"] <= frozen_end) & panel["y"].notna()]
    ds = lgb.Dataset(tr[feats].to_numpy(np.float32), tr["y"].to_numpy(np.float32), free_raw_data=False)
    frozen = {s: base.assign(pred=L.fit(None, None, seed=s, dataset=ds).predict(Xt)) for s in L.SEEDS}
    del ds, tr
    # 재학습: 블록마다 그 블록 학습 끝으로
    retr: dict[int, list[pd.DataFrame]] = {s: [] for s in L.SEEDS}
    ends = []
    for first, last in bl:
        end = fkit.train_end(sessions, first)
        ends.append((str(sessions[first]), str(sessions[last]), str(end)))
        tr = panel[(panel["session"] <= end) & panel["y"].notna()]
        hi = sessions[last + 1] if last + 1 < len(sessions) else None
        te = panel[(panel["session"] >= sessions[first]) & ((panel["session"] < hi) if hi else True)]
        ds = lgb.Dataset(tr[feats].to_numpy(np.float32), tr["y"].to_numpy(np.float32), free_raw_data=False)
        Xb = te[feats].to_numpy(np.float32)
        for s in L.SEEDS:
            retr[s].append(te[["entity_id", "session"]].reset_index(drop=True).assign(pred=L.fit(None, None, seed=s, dataset=ds).predict(Xb)))
        print("블록", ends[-1], len(tr), flush=True)
        del ds, tr
    retrained = {s: pd.concat(v, ignore_index=True) for s, v in retr.items()}
    icf = L.ic_frame(frozen, ctx).mean(axis=1)
    icr = L.ic_frame(retrained, ctx).mean(axis=1)
    icf, icr = icf[icf.index >= VAULT_LO], icr[icr.index >= VAULT_LO]
    # 실전 저장 점수(6/30 모델) 같은 날
    w = _wide_ranker(L, ctx, "ranker")
    icl = L.wide_ic(w.loc[w.index >= VAULT_LO], ctx)
    d = (icr - icf).dropna()
    out = {"frozen_train_end": str(frozen_end), "blocks": ends, "frozen": _summ(icf), "retrained": _summ(icr), "live_stored": _summ(icl),
           "retrained_minus_frozen": _summ(d), "seed_pos": None,
           "by_month": {m: {"frozen": _summ(icf[[x.month == m for x in icf.index]]), "retrained": _summ(icr[[x.month == m for x in icr.index]]),
                            "live": _summ(icl[[x.month == m for x in icl.index]])} for m in (7, 8, 9)},
           "rho_frozen_vs_live": float(pd.concat([icf, icl], axis=1).dropna().corr().iloc[0, 1])}
    per_seed = [(L.ic_frame({0: retrained[s]}, ctx)[0] - L.ic_frame({0: frozen[s]}, ctx)[0]).mean() for s in L.SEEDS]
    out["seed_pos"] = f"{sum(1 for x in per_seed if x > 0)}/{len(per_seed)}"
    print(json.dumps(out, ensure_ascii=False, default=str)[:1500], flush=True)
    _write("retrain", out)


# --------------------------------------------------------------------------- peers (④)


def part_peers() -> None:
    from quant_rl_trading.store import Store
    from tools.ta_composite import composite_panel

    L, ctx = _ctx()
    out: dict = {}

    def ic_of(name: str, w: pd.DataFrame) -> None:
        w = w[w.index <= CAP_DAY]
        _cap(w.index, name)
        ic = L.wide_ic(w, ctx)
        out[name] = {"hist_2025_26H1": _summ(ic[(ic.index >= date(2025, 1, 1)) & (ic.index <= date(2026, 6, 30))]),
                     "hist_2023_24": _summ(ic[(ic.index >= date(2023, 1, 1)) & (ic.index <= date(2024, 12, 31))]),
                     "vault": _summ(ic[ic.index >= VAULT_LO]),
                     "vault_months": {m: _summ(ic[[d.month == m and d >= VAULT_LO for d in ic.index]]) for m in (7, 8, 9)},
                     "daily_vault": {str(k): float(x) for k, x in ic[ic.index >= VAULT_LO].items()}}
        print(name, out[name]["vault"], flush=True)

    for name in ("C0", "C1", "BE2"):
        sc = L.read_scores(name)
        ics = []
        for s in sc:
            ics.append(L.wide_ic(L.as_wide(sc[s], ctx), ctx))
        ic = pd.concat(ics, axis=1).mean(axis=1)
        out[name] = {"hist_2025_26H1": _summ(ic[(ic.index >= date(2025, 1, 1)) & (ic.index <= date(2026, 6, 30))]),
                     "hist_2023_24": _summ(ic[(ic.index >= date(2023, 1, 1)) & (ic.index <= date(2024, 12, 31))]),
                     "vault": _summ(ic[ic.index >= VAULT_LO]),
                     "vault_months": {m: _summ(ic[[d.month == m and d >= VAULT_LO for d in ic.index]]) for m in (7, 8, 9)},
                     "daily_vault": {str(k): float(x) for k, x in ic[ic.index >= VAULT_LO].items()}}
        print(name, out[name]["vault"], flush=True)
        del sc
    t = pd.read_pickle(DIAG / "vault-early" / "tsfm-KR.pkl")  # invariant-allow: data-access — 금고 early TTM 굽기
    t["session"] = pd.to_datetime(t["session"]).dt.date
    ic_of("TTM", L.as_wide(t.rename(columns={"r_hat": "pred"})[["entity_id", "session", "pred"]], ctx))
    store = Store(root=Path("data"))  # invariant-allow: data-access — 창고 루트(읽기는 store.get)
    comp, _ = composite_panel(store, end=CAP, start=date(2024, 9, 1))
    comp = comp[comp.index >= date(2025, 9, 1)]
    comp = comp.reindex(columns=ctx.close.columns)
    comp.index = pd.Index([d if isinstance(d, date) else pd.Timestamp(d).date() for d in comp.index])
    ic_of("TA", comp.astype(np.float32))
    _write("peers", out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=("live", "split", "retrain", "peers"), required=True)
    a = ap.parse_args()
    {"live": part_live, "split": part_split, "retrain": part_retrain, "peers": part_peers}[a.part]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
