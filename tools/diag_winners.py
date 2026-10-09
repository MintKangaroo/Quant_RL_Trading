"""오를 종목 탐색 진단 (2026-10-09 밤, 탐색 · 시행 아님) — 유동주 상위 300, 매일 TSFM 예측(tools/diag_tsfm_daily) 위에서.

    .venv/bin/python tools/diag_winners.py band|large|tail

band  : TTM 예측 분위 띠(D6~D9 등) 포트 — 최상위 10% 가 '떨어지는 칼날' 이면 띠가 낫다.
large : 대형(시총 1~200) 안 — TTM 하위 20% 를 뺀 뒤 외국인 60세션 순매수/시총 상위 N.
tail  : Chronos-Bolt 분위수(10·50·90%) — 상단 꼬리·비대칭이 오를 종목을 가리나.
창 둘: 최근(2025-03~2026-06, D₀ 뒤) · 장기(2022-08~2025-02, 공개 전 — 참고). 모든 신호 **하루 늦춤**, 5세션 재조정, 비용 0.41%×|Δw|.
창고에는 쓰지 않는다.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOTS = {"최근": Path("data/_diag/tsfm-daily"), "장기": Path("data/_diag/tsfm-daily-long")}
CAPS = Path("data/_diag/p1a-prime/caps-rank.parquet")  # invariant-allow: data-access — 시행 작업 캐시
COST, K = 0.0041, 245 / 5


def load(root: Path) -> pd.DataFrame:
    keys = pd.read_pickle(root / "inputs" / "keys-KR-D.pkl")
    lab = pd.read_pickle(root / "labels-KR-D.pkl")
    df = keys[["session", "entity_id"]].copy()
    df["r"] = lab["r"].to_numpy()
    pr = np.load(root / "preds" / "ttm.npz")["price"]
    df["ttm"] = pr[:, -1] / pr[:, 0] - 1.0
    qf = root / "preds" / "chronos_bolt_q.npz"
    if qf.exists():
        q = np.load(qf)["q"]                          # (N, 6, 3)
        base = q[:, 0, 1]
        df["q10"], df["q50"], df["q90"] = q[:, -1, 0] / base - 1, q[:, -1, 1] / base - 1, q[:, -1, 2] / base - 1
        df["up"] = df["q90"] - df["q50"]
        df["skew"] = (df["q90"] - df["q50"]) - (df["q50"] - df["q10"])
    caps = pd.read_parquet(CAPS).rename(columns={"cap": "c"})  # invariant-allow: data-access — 시행 작업 캐시
    caps["rank"] = caps.groupby("session")["c"].rank(ascending=False)
    df = df.merge(caps, on=["session", "entity_id"], how="left")
    return df


def lag(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    sessions = sorted(df["session"].unique())
    nxt = dict(zip(sessions[:-1], sessions[1:], strict=True))
    for c in cols:
        sh = df[["session", "entity_id", c]].copy()
        sh["session"] = sh["session"].map(nxt)
        df = df.drop(columns=[c]).merge(sh.dropna(subset=["session"]), on=["session", "entity_id"], how="left")
    return df


def book(df: pd.DataFrame, pick, *, cap: bool) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    sessions = sorted(df["session"].unique())[1::5]
    rows, prev = [], None
    for s in sessions:
        g = df[df["session"] == s].dropna(subset=["r"]).set_index("entity_id")
        uni = g
        g = pick(g)
        if g is None or len(g) == 0:
            continue
        w = (g["c"] / g["c"].sum()) if cap else pd.Series(1 / len(g), index=g.index)
        w = w.dropna()
        turn = 1.0 if prev is None else w.subtract(prev, fill_value=0).abs().sum()
        rows.append({"s": s, "r": float((w * g["r"].reindex(w.index)).sum()) - COST * turn, "uni": float(uni["r"].mean()),
                     "t": turn})
        prev = w
    return pd.DataFrame(rows).set_index("s")


def show(name: str, f: pd.DataFrame, ref: pd.Series) -> None:
    x = f["r"] - ref.reindex(f.index)
    print(f"  {name}: 연 {f['r'].mean() * K:+.1%} · 대 기준 {x.mean() * K:+.2%}p · IR {x.mean() / x.std() * np.sqrt(K):+.2f} · "
          f"이긴 창 {(x > 0).mean():.0%} · 회전/회 {f['t'].iloc[1:].mean():.0%}", flush=True)


def band() -> None:
    for w, root in ROOTS.items():
        df = lag(load(root), ["ttm"])
        df["dec"] = df.groupby("session")["ttm"].rank(pct=True)
        ew = book(df, lambda g: g, cap=False)["r"]
        print(f"== {w} · 기준 = 유동주 300 동일가중(연 {ew.mean() * K:+.1%})", flush=True)
        for lo, hi in ((0.5, 0.9), (0.6, 0.9), (0.7, 0.9), (0.8, 0.9), (0.9, 1.01), (0.2, 0.9)):
            show(f"TTM 분위 {lo:.0%}~{min(hi, 1):.0%}", book(df, lambda g, lo=lo, hi=hi: g[(g["dec"] > lo) & (g["dec"] <= hi)], cap=False), ew)


def large() -> None:
    from quant_rl_trading.store import Store
    store = Store(root=Path("data"))
    fl = store.get("flows", as_of=datetime(2026, 7, 1, tzinfo=UTC), lookback=1900, market="KR",
                   columns=["entity_id", "valid_from", "investor", "net_value"])
    fl = fl[fl["investor"] == "외인계"]
    fl["day"] = pd.to_datetime(fl["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    net = fl.pivot_table(index="day", columns="entity_id", values="net_value", aggfunc="last").sort_index()
    roll = net.rolling(60, min_periods=48).sum().stack().rename("f60").reset_index().rename(columns={"day": "session"})
    for w, root in ROOTS.items():
        df = load(root).merge(roll, on=["session", "entity_id"], how="left")
        df["f60"] = df["f60"] / df["c"]
        df = lag(df, ["ttm", "f60"])
        big = lambda g: g[g["rank"] <= 200]  # noqa: E731
        kew = book(df, big, cap=False)["r"]
        kcap = book(df, big, cap=True)["r"]
        print(f"== {w} · 대형(시총 1~200 ∩ 유동주 300) · 동일가중 연 {kew.mean() * K:+.1%} · 시총가중 연 {kcap.mean() * K:+.1%}", flush=True)
        for n in (20, 40):
            def pick(g, n=n, ttm_cut=False):  # type: ignore[no-untyped-def]
                g = big(g)
                if ttm_cut:
                    t = g["ttm"].dropna()
                    g = g[~g.index.isin(t[t <= t.quantile(0.2)].index)]
                return g.dropna(subset=["f60"]).nlargest(n, "f60")
            for cut in (False, True):
                lbl = f"외국인 상위 {n}" + (" (TTM 하위 20% 뺀 뒤)" if cut else "")
                show(lbl + " · 동일가중 대 대형 EW", book(df, lambda g, n=n, cut=cut: pick(g, n, cut), cap=False), kew)
                show(lbl + " · 시총가중 대 대형 시총", book(df, lambda g, n=n, cut=cut: pick(g, n, cut), cap=True), kcap)


def tail() -> None:
    for w, root in ROOTS.items():
        df = load(root)
        if "up" not in df:
            print(f"{w}: 분위수 예측 없음 — bench/tsfm_quantile_infer.py 먼저", flush=True)
            continue
        df = lag(df, ["ttm", "q10", "q50", "q90", "up", "skew"])
        print(f"== {w}", flush=True)
        for c in ("q50", "q90", "up", "skew", "q10"):
            sub = df.dropna(subset=[c, "r"])
            sub = sub[sub.groupby("session")[c].transform("size") >= 50]
            exc = sub["r"] - sub.groupby("session")["r"].transform("mean")
            q = sub.groupby("session")[c].transform(lambda x: pd.qcut(x.rank(method="first"), 10, labels=False))
            dec = exc.groupby(q).mean()
            ic = sub.groupby("session")[[c, "r"]].corr(method="spearman").xs(c, level=1)["r"]
            print(f"  {c:5s}: IC {ic.mean():+.4f} · 10분위 5일 " + " ".join(f"D{int(k) + 1} {v:+.2%}" for k, v in dec.items()), flush=True)
        ew = book(df, lambda g: g, cap=False)["r"]
        for c in ("q90", "up"):
            show(f"{c} 상위 30 (TTM 하위 20% 뺀 뒤)",
                 book(df, lambda g, c=c: g[g["ttm"] > g["ttm"].quantile(0.2)].nlargest(30, c), cap=False), ew)


if __name__ == "__main__":
    {"band": band, "large": large, "tail": tail}[sys.argv[1]]()
