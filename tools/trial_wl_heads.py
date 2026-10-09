"""시행 WL — 오를 종목·망할 종목을 따로 맞히는 두 갈래 GBM(docs/protocols/wl-heads-2026-10.md).

    nice -n 10 taskset -c 0-5 .venv/bin/python tools/trial_wl_heads.py bake [--arm C1|W|L]   # 워크포워드 예측 굽기(시드 5)
    .venv/bin/python tools/trial_wl_heads.py check                                       # 등록 전 점검 — 라벨 비율·행 수, 수익 없음
    .venv/bin/python tools/trial_wl_heads.py run [--save]                                # 판정(등록 해시 고정 뒤에만)

같은 국장 FA2021 패널 · 같은 C1 피처 · 같은 블록 · 같은 GBM(`trial_ranker_kit.fit`, 시행 L 하이퍼파라미터)에서 **목표만** 바꾼다:
C1 = y5(그날 횡단면 rank-gauss 회귀, 회차 C1 과 같은 목표) · W = y5 가 그날 상위 10% 인가(이진) · L = 하위 10% 인가(이진).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools.diag_bear_2022 import in_window, realized_end  # noqa: E402
from tools.diag_hedged_sleeve import PRICE_START, WINDOWS  # noqa: E402
from tools.trial_overlay import ANN  # noqa: E402
from tools.trial_p1a_prime import static_book  # noqa: E402

PROTOCOL = Path("docs/protocols/wl-heads-2026-10.md")
#: 등록 문서 sha256 앞 16자 — 사용자 확인 뒤 적는다. None 이면 `run` 은 거부한다(굽기·점검만 허용).
PROTOCOL_HASH: str | None = "36517f65cb98dc3f"  # 2026-10-09 고정
PANEL = Path("data/_diag/fa2021/panel/panel-KR-KR+US-20211110-20260630.parquet")  # invariant-allow: data-access — 연구 패널 캐시
OUT = Path("data/_diag/wl-heads")
ARMS = ("C1", "W", "L")
SEEDS = (0, 1, 2, 3, 4)
TAIL = 0.10
EVERY = 10
#: W 기준(대 C1 상위 24, 회차 kit 와 같은 꼴): ① 연 ≥ +1%p · NW t ≥ 2 ② 시드 4/5 ③ 국면 넷 ≥ −1%p ④ MDD 2%p · 회전 ×1.2
W_GATE_ANN, W_GATE_T, W_GATE_SHARE, W_GATE_REGIME, W_GATE_MDD, W_GATE_TURN = 0.01, 2.0, 4, -0.01, 0.02, 1.2
#: L 기준(유니버스 동일가중 − 하위 10%, 대 C1 로 뺀 것): ① 연 ≥ +0.5%p · NW t ≥ 2 ② 시드 4/5 ③ 국면 넷 ≥ −0.5%p
L_GATE_ANN, L_GATE_T, L_GATE_SHARE, L_GATE_REGIME = 0.005, 2.0, 4, -0.005
REGIMES = ("하락", "반등", "박스", "급등")


def feats() -> list[str]:
    from tools import final_round_kit as fkit
    return fkit.feature_names(fkit.blocks_of(("KR", "US"), raw_dirs=fkit.FA2021["raw_dirs"]))


def label(panel: pd.DataFrame, arm: str) -> pd.Series:
    """목표 열. C1 = y5 그대로 · W/L = 그날 y5 횡단면 백분위가 상위/하위 TAIL 인가(1/0). y5 결측은 결측."""
    if arm == "C1":
        return panel["y5"]
    pct = panel.groupby("session")["y5"].rank(pct=True)
    hit = (pct > 1 - TAIL) if arm == "W" else (pct <= TAIL)
    return hit.astype(np.float32).where(panel["y5"].notna())


def pred_path(arm: str, seed: int) -> Path:
    return OUT / f"pred-{arm}-seed{seed}.pkl"


def walk(panel: pd.DataFrame, cols: list[str], arm: str, seeds: list[int]) -> dict[int, pd.DataFrame]:
    """`final_round_kit.walk_gbm` 과 같은 블록·학습 끝점·판정 행 — 목표 열과 objective 만 다르다."""
    from tools import final_round_kit as fkit
    sessions = sorted(pd.unique(panel["session"]))
    bl = fkit.blocks(sessions)
    target = label(panel, arm)
    objective = "regression" if arm == "C1" else "binary"
    parts: dict[int, list[pd.DataFrame]] = {s: [] for s in seeds}
    for first, last in bl:
        end = fkit.train_end(sessions, first)
        mask = (panel["session"] <= end) & target.notna()
        test = fkit.block_rows(panel, sessions, first, last)
        if not mask.any() or test.empty:
            continue
        X, y = panel.loc[mask, cols].to_numpy(np.float32), target[mask].to_numpy(np.float32)
        Xt = test[cols].to_numpy(np.float32)
        base = test[["entity_id", "session"]].reset_index(drop=True)
        for s in seeds:
            out = base.copy()
            out["pred"] = rkit.fit(X, y, objective=objective, seed=s).predict(Xt)
            parts[s].append(out)
        print(f"  {arm} 블록 {sessions[first]}~{sessions[last]} · 학습 ~{end} ({int(mask.sum()):,}행)", flush=True)
        del X, y, Xt, test, base
    return {s: pd.concat(v, ignore_index=True) for s, v in parts.items() if v}


def load_panel(cols: list[str]) -> pd.DataFrame:
    from tools import final_round_kit as fkit
    p = pd.read_parquet(PANEL, columns=["entity_id", "session", "market", "y5", *cols])  # invariant-allow: data-access — 연구 패널 캐시
    p = p[p["market"] == "KR"].drop(columns="market").reset_index(drop=True)
    p["session"] = fkit._as_dates(p["session"])
    return p


def cmd_bake(arms: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cols = feats()
    panel = load_panel(cols)
    print(f"[bake] 국장 {len(panel):,}행 · 세션 {panel['session'].nunique()} · 피처 {len(cols)}", flush=True)
    for arm in arms:
        todo = [s for s in SEEDS if not pred_path(arm, s).exists()]
        if not todo:
            print(f"  {arm}: 예측 있음 — 건너뛴다", flush=True)
            continue
        for s, frame in walk(panel, cols, arm, todo).items():
            frame.to_pickle(pred_path(arm, s))  # invariant-allow: data-access — 시행 작업 캐시
        print(f"  {arm} 끝 · 시드 {todo}", flush=True)
    return 0


def cmd_check() -> int:
    """등록 전 점검 — 라벨 비율과 세션당 행 수만(수익·IC 를 보지 않는다)."""
    cols = feats()
    panel = load_panel(cols)
    per = panel.groupby("session").size()
    out = {"rows": len(panel), "sessions": int(per.size), "per_session_min": int(per.min()),
           "per_session_med": float(per.median()), "features": len(cols),
           "w_share": float(label(panel, "W").mean()), "l_share": float(label(panel, "L").mean()),
           "y5_missing": float(panel["y5"].isna().mean())}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


# --------------------------------------------------------------------------- 판정


def _stats(daily: pd.Series, turn: float, wd: dict[str, list[date]]) -> dict[str, float]:
    r = daily.reindex(wd["전체"]).fillna(0.0)
    nav = (1 + r).cumprod()
    out = {"ann": float(r.mean() * ANN), "mdd": float((nav / nav.cummax() - 1).min()), "turn": turn}
    for g in REGIMES:
        out[g] = float(daily.reindex(wd[g]).fillna(0.0).mean() * ANN)
    return out


def excl_book(pred: pd.DataFrame, sessions: list[date], ret: pd.DataFrame, trad: dict[date, set[str]],
              *, worst_is_high: bool) -> tuple[pd.Series, pd.Series]:
    """유니버스 동일가중에서 그날 점수로 본 하위 TAIL 을 뺀 포트(재조정 10세션, 비용 후). L 은 확률이 높을수록 패자."""
    wide = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=rkit.SPAN).mean()

    def target(day: date) -> pd.Series | None:
        names = sorted(trad.get(day, ()))
        if not names:
            return None
        if day in wide.index:
            sc = wide.loc[day].reindex(names).dropna()
            n = int(len(sc) * TAIL)
            worst = set((sc.nlargest(n) if worst_is_high else sc.nsmallest(n)).index)
            names = [e for e in names if e not in worst]
        return pd.Series(1.0 / len(names), index=names)
    return static_book(sessions, ret, target, every=EVERY)


def judge_w(w: list[dict], c: list[dict], dw: pd.Series, dc: pd.Series) -> tuple[str, bool]:
    m = lambda rows, k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    d = m(w, "ann") - m(c, "ann")
    t = float(ic_module.newey_west_t(dw - dc, lag=4))
    wins = sum(a["ann"] > b["ann"] for a, b in zip(w, c, strict=True))
    reg = {g: m(w, g) - m(c, g) for g in REGIMES}
    ok = (d >= W_GATE_ANN and t >= W_GATE_T, wins >= W_GATE_SHARE, all(v >= W_GATE_REGIME for v in reg.values()),
          m(c, "mdd") - m(w, "mdd") <= W_GATE_MDD and m(w, "turn") <= m(c, "turn") * W_GATE_TURN)
    line = (f"W(오를 종목) 상위 24: 연 {m(w, 'ann'):+.1%} 대 C1 {m(c, 'ann'):+.1%} ({d:+.1%}p, NW t {t:+.2f}) {rkit.mark(ok[0])} · "
            f"시드 {wins}/5 {rkit.mark(ok[1])} · 국면 " + " / ".join(f"{g} {v:+.1%}p" for g, v in reg.items())
            + f" {rkit.mark(ok[2])} · MDD {m(w, 'mdd'):.1%} 대 {m(c, 'mdd'):.1%} · 회전 {m(w, 'turn'):.1f} 대 {m(c, 'turn'):.1f} {rkit.mark(ok[3])}")
    return line, all(ok)


def judge_l(lx: list[dict], cx: list[dict], dl: pd.Series, dc: pd.Series) -> tuple[str, bool]:
    m = lambda rows, k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    d = m(lx, "ann") - m(cx, "ann")
    t = float(ic_module.newey_west_t(dl - dc, lag=4))
    wins = sum(a["ann"] > b["ann"] for a, b in zip(lx, cx, strict=True))
    reg = {g: m(lx, g) - m(cx, g) for g in REGIMES}
    ok = (d >= L_GATE_ANN and t >= L_GATE_T, wins >= L_GATE_SHARE, all(v >= L_GATE_REGIME for v in reg.values()))
    line = (f"L(망할 종목) 유니버스 − 하위 10%: 연 {m(lx, 'ann'):+.1%} 대 C1 로 뺀 것 {m(cx, 'ann'):+.1%} ({d:+.1%}p, NW t {t:+.2f}) "
            f"{rkit.mark(ok[0])} · 시드 {wins}/5 {rkit.mark(ok[1])} · 국면 " + " / ".join(f"{g} {v:+.1%}p" for g, v in reg.items())
            + f" {rkit.mark(ok[2])}")
    return line, all(ok)


def cmd_run(store: Store, *, save: bool) -> int:
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else None
    if PROTOCOL_HASH is None or digest != PROTOCOL_HASH:
        print(f"판정 거부 — 등록 해시가 고정되지 않았거나 문서가 바뀌었다(고정 {PROTOCOL_HASH} · 지금 {digest}). "
              "허용되는 것은 bake·check 뿐이다.", flush=True)
        return 2
    preds = {a: {s: pd.read_pickle(pred_path(a, s)) for s in SEEDS} for a in ARMS}  # invariant-allow: data-access — 시행 작업 캐시
    sessions = sorted(pd.unique(preds["C1"][0]["session"]))
    ret, _, trad = rkit.market_data(store, [PRICE_START, *sessions])
    spans = realized_end(sessions, list(ret.index))
    wd = {g: in_window(pd.Index(sessions), spans, w) for g, w in WINDOWS.items()}
    full = wd["전체"]
    res: dict[str, list[dict]] = {k: [] for k in ("W", "C1top", "L", "C1excl", "U")}
    daily: dict[str, list[pd.Series]] = {k: [] for k in res}
    uni_d, uni_t = static_book(sessions, ret, lambda d: (pd.Series(1.0 / len(trad[d]), index=sorted(trad[d]))
                                                        if trad.get(d) else None), every=EVERY)
    for s in SEEDS:
        for key, arm in (("W", "W"), ("C1top", "C1")):
            dd, extra = rkit.portfolio(preds[arm][s], ret, trad, every=EVERY)
            res[key].append(_stats(dd, extra["turn"], wd))
            daily[key].append(dd.reindex(full).fillna(0.0))
        for key, arm, high in (("L", "L", True), ("C1excl", "C1", False)):
            dd, tt = excl_book(preds[arm][s], sessions, ret, trad, worst_is_high=high)
            res[key].append(_stats(dd, float(tt.reindex(full).fillna(0).sum() * ANN / len(full)), wd))
            daily[key].append(dd.reindex(full).fillna(0.0))
        print(f"  seed{s} 끝", flush=True)
    mean = {k: pd.concat(v, axis=1).mean(axis=1) for k, v in daily.items() if v}
    lw, ok_w = judge_w(res["W"], res["C1top"], mean["W"], mean["C1top"])
    ll, ok_l = judge_l(res["L"], res["C1excl"], mean["L"], mean["C1excl"])
    u = _stats(uni_d, float(uni_t.reindex(full).fillna(0).sum() * ANN / len(full)), wd)
    lines = [lw, ll, f"기록: 유니버스 동일가중 연 {u['ann']:+.1%} · 판정 세션 {len(full)}"]
    verdict = ("채택 후보 " + "·".join(k for k, ok in (("W", ok_w), ("L", ok_l)) if ok)) if (ok_w or ok_l) else "기각"
    print("\n".join(lines) + f"\n판정: {verdict}", flush=True)
    if save:
        rkit.record(store, entity="wl-heads-2026-10:WL", source="trial_wl_heads", family="ranker", digest=digest,
                    verdict=verdict, lines=["WL · 두 갈래(W·L) · W-fa2021 국장", *lines])
    else:
        print("--save 없음 — 창고에 적지 않았다", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bake")
    b.add_argument("--arm", choices=ARMS, default=None)
    sub.add_parser("check")
    r = sub.add_parser("run")
    r.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "bake":
        return cmd_bake([args.arm] if args.arm else list(ARMS))
    if args.cmd == "check":
        return cmd_check()
    return cmd_run(Store(root=Path(args.root)), save=args.save)


if __name__ == "__main__":
    raise SystemExit(main())
