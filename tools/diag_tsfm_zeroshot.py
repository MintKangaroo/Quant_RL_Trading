"""TSFM 제로샷 진단 — 1단계 extract · 3단계 score (docs/diag/tsfm-zeroshot.md §1, 정의 커밋 4417310).

진단이지 시행이 아니다. 창고는 읽기만 한다(`store.get` 경유 `wide_close_and_turnover`). **as_of 를 2026-06-30 장 마감 뒤로 고정해**
금고 구간(2026-07-01~)의 관측을 아예 읽지 않는다. 2단계 추론은 `.venv-bench` 의 `bench/tsfm_zeroshot_infer.py` 가 한다 —
그쪽은 입력 계열 파일만 받는다(라벨은 이 파일의 score 만 읽는다).

    .venv/bin/python tools/diag_tsfm_zeroshot.py extract
    .venv-bench/bin/python bench/tsfm_zeroshot_infer.py
    .venv/bin/python tools/diag_tsfm_zeroshot.py score
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import FACTOR_COLUMN, adjust, read_prices, wide_close_and_turnover  # noqa: E402

OUT = ROOT_DIR / "data" / "_diag" / "tsfm-zeroshot"
KST = timezone(timedelta(hours=9))
AS_OF = datetime.combine(date(2026, 6, 30), time(23, 59), tzinfo=KST)   # 금고 앞 — 7/1 이후 관측은 읽히지 않는다
VAULT_START = date(2026, 7, 1)
HIST_START = date(2023, 10, 2)            # HAR 적합(2024) 의 22일·EWMA 1년 앞
WINDOWS = {"A": (date(2026, 2, 2), date(2026, 6, 30)), "B": (date(2025, 3, 3), date(2026, 1, 29))}
HAR_FIT = (date(2024, 1, 2), date(2024, 12, 30))
STEP = 5
H = 6
UNIVERSE = 300
CTX = 256
MIN_HIST = 240
MAX_GAP = 3
US_MIN_PRICE = 5.0
US_BENCH = "US:IDX:SP500"
EWMA_LAMBDA = 0.94


# ── 1단계 ───────────────────────────────────────────────────────────────

def load_market(store: Store, market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = (AS_OF.date() - HIST_START).days + 40
    close, dv = wide_close_and_turnover(store, as_of=AS_OF, lookback=days, market=market)
    cols = [c for c in close.columns if isinstance(c, str)]
    if market == "KR":
        cols = [c for c in cols if c.startswith("KR:") and not c.startswith(("KR:ETF:", "KR:IDX:"))]
    else:
        kinds = store.get("instrument_types", as_of=AS_OF + timedelta(days=120), lookback=200, market="US",
                          columns=["entity_id", "instrument", "test_issue"])
        ok = set(kinds[kinds["instrument"].isin(["common", "adr", "other"]) & ~kinds["test_issue"].astype(bool)]["entity_id"])
        cols = [c for c in cols if c in ok and c != US_BENCH and ":IDX:" not in c]
    close = close[cols]
    dv = dv.reindex(columns=cols)
    assert max(close.index) < VAULT_START, "금고 구간이 읽혔다"
    return close, dv


def decision_sessions(index: list, start: date, end: date) -> list[int]:
    """start 이후 첫 세션부터 STEP 마다, t+H 가 end 이하인 세션까지(위치 인덱스)."""
    pos = [i for i, d in enumerate(index) if d >= start and i + H < len(index) and index[i + H] <= end]
    return pos[::STEP]


def _context(close: pd.DataFrame, i: int, ents: list[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """보정 종가 CTX 개 · 로그수익 제곱 CTX 개. 3일 넘는 빈칸이 있거나 이력이 MIN_HIST 미만이면 뺀다."""
    win = close.iloc[i - CTX: i + 1][ents]                       # CTX+1 개(수익 CTX 개)
    have = win.notna().sum()
    win = win.ffill(limit=MAX_GAP)
    lead_ok = have >= MIN_HIST + 1
    win = win.bfill(limit=CTX + 1 - MIN_HIST)                     # 이력이 240~256 이면 앞을 첫 값으로
    full = win.notna().all() & lead_ok & (win > 0).all()
    keep = [e for e in ents if bool(full.get(e, False))]
    w = win[keep].to_numpy(np.float64)
    lr = np.diff(np.log(w), axis=0)
    return w[1:].T.astype(np.float32), (lr ** 2).T.astype(np.float32), keep


def build_rows(close: pd.DataFrame, dv: pd.DataFrame, market: str, positions: list[int]):
    idx = list(close.index)
    keys, prices, sqs, labels = [], [], [], []
    logc = np.log(close.where(close > 0))
    r1 = logc.diff()
    for i in positions:
        t = idx[i]
        elig = dv.iloc[i].dropna()
        px = close.iloc[i]
        if market == "US":
            elig = elig[px.reindex(elig.index) >= US_MIN_PRICE]
        elig = elig[px.reindex(elig.index).notna()]
        top = list(elig.sort_values(ascending=False).index[:UNIVERSE])
        p, s, keep = _context(close, i, top)
        if not keep:
            continue
        c = close.iloc[i][keep]
        f = pd.DataFrame({"session": t, "entity_id": keep, "dropped": len(top) - len(keep)})
        f["mom20"] = (c / close.iloc[i - 20][keep] - 1.0).to_numpy()
        f["rev5"] = -(c / close.iloc[i - 5][keep] - 1.0).to_numpy()
        hist = r1.iloc[i - 252: i + 1][keep] ** 2
        wts = EWMA_LAMBDA ** np.arange(len(hist))[::-1]
        f["ewma_var"] = (hist.fillna(0.0).to_numpy() * wts[:, None]).sum(0) / wts.sum()
        f["rv20"] = np.sqrt(5.0 / 20.0 * hist.iloc[-20:].sum().to_numpy())
        f["xd"] = s[:, -1]
        f["xw"] = s[:, -5:].mean(1)
        f["xm"] = s[:, -22:].mean(1)
        fwd = close.iloc[i + H][keep] / close.iloc[i + 1][keep] - 1.0
        rv = (r1.iloc[i + 2: i + H + 1][keep] ** 2).sum(min_count=H - 1)
        lab = pd.DataFrame({"session": t, "entity_id": keep,
                            "r": fwd.where(fwd.abs() <= 1.0).to_numpy(), "sigma": np.sqrt(rv).to_numpy()})
        keys.append(f)
        prices.append(p)
        sqs.append(s)
        labels.append(lab)
    return (pd.concat(keys, ignore_index=True), np.concatenate(prices), np.concatenate(sqs), pd.concat(labels, ignore_index=True))


def kronos_inputs(store: Store, market: str, close: pd.DataFrame, keys: pd.DataFrame) -> dict:
    """§1-보충: Kronos 입력 — 보정 OHLC + 원 거래량 + 거래대금(원종가 × 원거래량), 창 A 행과 같은 순서. 라벨 없음."""
    ents = sorted(keys["entity_id"].unique())
    days = (AS_OF.date() - HIST_START).days + 40
    raw = read_prices(store, as_of=AS_OF, lookback=days, market=market, entity=ents,
                      columns=["open", "high", "low", "close", "volume", FACTOR_COLUMN], adjusted=False)
    raw["day"] = pd.to_datetime(raw["valid_from"]).dt.date
    adj = adjust(raw)

    def wide(frame: pd.DataFrame, col: str) -> pd.DataFrame:
        w = frame.pivot_table(index="day", columns="entity_id", values=col, aggfunc="last")
        return w.reindex(index=close.index, columns=ents)

    o, h, lo, c = (wide(adj, k) for k in ("open", "high", "low", "close"))
    amount = wide(raw, "close") * wide(raw, "volume")
    vol = wide(raw, "volume")
    idx = list(close.index)
    pos = {d: i for i, d in enumerate(idx)}
    x = np.zeros((len(keys), CTX, 6), np.float32)
    stamps = []
    for r, (t, e) in enumerate(zip(keys["session"], keys["entity_id"], strict=True)):
        i = pos[t]
        sl = slice(i - CTX + 1, i + 1)
        cc = c[e].iloc[sl].ffill(limit=MAX_GAP).bfill()
        block = np.column_stack([
            o[e].iloc[sl].fillna(cc), h[e].iloc[sl].fillna(cc), lo[e].iloc[sl].fillna(cc), cc,
            vol[e].iloc[sl].fillna(0.0), amount[e].iloc[sl].fillna(0.0)])
        x[r] = block
    for t in sorted(keys["session"].unique()):
        i = pos[t]
        stamps.append([str(d) for d in idx[i - CTX + 1: i + H + 1]])
    return {"ohlcva": x, "row_session": keys["session"].map({t: k for k, t in enumerate(sorted(keys["session"].unique()))}).to_numpy(),
            "stamps": np.array(stamps)}


def fit_har(close: pd.DataFrame, dv: pd.DataFrame, market: str) -> list[float]:
    idx = list(close.index)
    keys, _, _, lab = build_rows(close, dv, market, decision_sessions(idx, *HAR_FIT))
    y = (lab["sigma"] ** 2).to_numpy()
    X = np.column_stack([np.ones(len(keys)), keys["xd"], keys["xw"], keys["xm"]])
    ok = np.isfinite(y) & np.isfinite(X).all(1)
    beta, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
    return [float(b) for b in beta]


def extract() -> None:
    (OUT / "inputs").mkdir(parents=True, exist_ok=True)
    (OUT / "labels").mkdir(parents=True, exist_ok=True)
    store = Store()
    har = {}
    for market in ("KR", "US"):
        close, dv = load_market(store, market)
        idx = list(close.index)
        har[market] = fit_har(close, dv, market)
        for w, (start, end) in WINDOWS.items():
            pos = decision_sessions(idx, start, min(end, VAULT_START - timedelta(days=1)))
            keys, p, s, lab = build_rows(close, dv, market, pos)
            assert lab["session"].max() < VAULT_START and max(idx[i + H] for i in pos) < VAULT_START
            np.savez(OUT / "inputs" / f"ctx-{market}-{w}.npz", price=p, sq=s)
            if w == "A":                                   # Kronos 는 창 A 만(§1-보충)
                np.savez(OUT / "inputs" / f"kronos-{market}-{w}.npz", **kronos_inputs(store, market, close, keys))
            keys.to_pickle(OUT / "inputs" / f"keys-{market}-{w}.pkl")
            lab.to_pickle(OUT / "labels" / f"labels-{market}-{w}.pkl")
            print(f"{market} 창 {w}: 세션 {keys['session'].nunique()} · 행 {len(keys)} · 세션당 중앙 "
                  f"{int(keys.groupby('session').size().median())} · 뺀 종목(합) {int(keys.groupby('session')['dropped'].first().sum())}",
                  flush=True)
        del close, dv
    (OUT / "labels" / "har.json").write_text(json.dumps(har))
    print("HAR 계수(b0, bd, bw, bm):", har)


# ── 3단계 ───────────────────────────────────────────────────────────────

def _ic_stats(per_session: pd.Series) -> dict:
    v = per_session.dropna().to_numpy()
    n = len(v)
    if n < 3:
        return {"mean": float("nan"), "t": float("nan"), "pos": float("nan"), "n": n}
    m = v.mean()
    d = v - m
    lag1 = (d[1:] * d[:-1]).sum() / n
    var = (d @ d) / n + 2 * 0.5 * lag1                    # 뉴이-웨스트, lag 1(바틀렛)
    se = np.sqrt(max(var, 1e-18) / n)
    return {"mean": float(m), "t": float(m / se), "pos": float((v > 0).mean()), "n": n}


def _spearman_by_session(df: pd.DataFrame, a: str, b: str) -> pd.Series:
    return df.groupby("session").apply(lambda g: g[a].rank().corr(g[b].rank()) if g[[a, b]].dropna().shape[0] >= 30 else np.nan,
                                       include_groups=False)


def _qlike(sig: np.ndarray, hat: np.ndarray) -> float:
    ok = np.isfinite(sig) & np.isfinite(hat) & (sig > 0) & (hat > 0)
    q = (sig[ok] ** 2) / (hat[ok] ** 2)
    return float(np.mean(q - np.log(q) - 1))


def score() -> None:
    har = json.loads((OUT / "labels" / "har.json").read_text())
    rows = []
    for market in ("KR", "US"):
        for w in WINDOWS:
            kp, lp = OUT / "inputs" / f"keys-{market}-{w}.pkl", OUT / "labels" / f"labels-{market}-{w}.pkl"
            if not kp.exists():
                continue
            keys = pd.read_pickle(kp)
            lab = pd.read_pickle(lp)
            base = keys.merge(lab, on=["session", "entity_id"], how="left", validate="1:1")
            assert len(base) == len(keys)
            b0, bd, bw, bm = har[market]
            ctrl = {
                "모멘텀20": (base["mom20"], None),
                "반전5": (base["rev5"], None),
                "EWMA": (None, np.sqrt(5 * base["ewma_var"].clip(lower=0))),
                "HAR": (None, np.sqrt((b0 + bd * base["xd"] + bw * base["xw"] + bm * base["xm"]).clip(lower=1e-12))),
                "RV20": (None, base["rv20"]),
            }
            ewma_q = _qlike(base["sigma"].to_numpy(), ctrl["EWMA"][1].to_numpy())
            for name, (rh, sh) in ctrl.items():
                rows.append(_row(market, w, name, "-", base, rh, sh, ewma_q, None))
            subset_done = False
            for f in sorted((OUT / "preds").glob(f"*-{market}-{w}-L*.npz")):
                model, L = f.stem.split(f"-{market}-")[0], f.stem.rsplit("-L", 1)[1]
                d = np.load(f)
                if len(d["price"]) != len(base):
                    raise SystemExit(f"{f.name}: 행 수 {len(d['price'])} ≠ 입력 {len(base)}")
                p, s = d["price"], d["sq"]
                mask = np.isfinite(p[:, 0])
                r_hat = pd.Series(p[:, H - 1] / p[:, 0] - 1.0)
                s_hat = pd.Series(np.sqrt(np.clip(s[:, 1:H], 0, None).sum(1)))
                use = base
                q_ref = ewma_q
                if not mask.all():                       # §1-보충 2: Kronos 상위 100 — 대조도 같은 부분집합에서
                    use = base[mask].reset_index(drop=True)
                    r_hat, s_hat = r_hat[mask].reset_index(drop=True), s_hat[mask].reset_index(drop=True)
                    sub = {k: (None if a_ is None else a_[mask].reset_index(drop=True),
                               None if b_ is None else b_[mask].reset_index(drop=True)) for k, (a_, b_) in ctrl.items()}
                    q_ref = _qlike(use["sigma"].to_numpy(), sub["EWMA"][1].to_numpy())
                    if not subset_done:
                        for name, (rh, sh) in sub.items():
                            rows.append(_row(market, w, f"{name}@상위100", "-", use, rh, sh, q_ref, None))
                        subset_done = True
                run = json.loads((OUT / "preds" / f"{model}-run.json").read_text()) if (OUT / "preds" / f"{model}-run.json").exists() else {}
                cell = run.get("cells", {}).get(f"{market}-{w}-L{L}", {})
                rows.append(_row(market, w, model, L, use, r_hat, s_hat, q_ref, cell))
    table = pd.DataFrame(rows)
    (OUT / "score.json").write_text(table.to_json(orient="records", force_ascii=False, indent=1))
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(table.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))


def _row(market, w, name, L, base, r_hat, s_hat, ewma_q, cell) -> dict:
    out = {"시장": market, "창": w, "모델": name, "L": L}
    df = base[["session", "r", "sigma"]].copy()
    if r_hat is not None:
        df["r_hat"] = np.asarray(r_hat, dtype=float)
        st = _ic_stats(_spearman_by_session(df, "r_hat", "r"))
        ok = df[["r", "r_hat"]].notna().all(1) & np.isfinite(df["r_hat"])
        r2 = 1 - ((df.loc[ok, "r"] - df.loc[ok, "r_hat"]) ** 2).sum() / (df.loc[ok, "r"] ** 2).sum()
        out.update({"IC": st["mean"], "IC_t": st["t"], "IC>0": st["pos"], "세션": st["n"], "R2oos": float(r2)})
    if s_hat is not None:
        df["s_hat"] = np.asarray(s_hat, dtype=float)
        st = _ic_stats(_spearman_by_session(df, "s_hat", "sigma"))
        q = _qlike(df["sigma"].to_numpy(), df["s_hat"].to_numpy())
        out.update({"변동성ρ": st["mean"], "변동성ρ_t": st["t"], "QLIKE": q, "QLIKE/EWMA": q / ewma_q if ewma_q else np.nan})
    out["종목중앙"] = int(base.groupby("session").size().median())
    if cell:
        out["추론초"] = cell.get("seconds") if isinstance(cell, dict) else cell
        out["ms/계열"] = cell.get("per_series_ms") if isinstance(cell, dict) else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["extract", "score"])
    a = ap.parse_args()
    extract() if a.step == "extract" else score()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
