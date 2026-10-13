#!/usr/bin/env python
"""내부자 재료 심화 탐색 진단 (AR2 계열 후보, 2026-10-13) — **등록 전 탐색이다. 시행이 아니다.**

    .venv/bin/python tools/diag_insider_deep.py --market US --stage features   # 피처 패널을 굽는다
    .venv/bin/python tools/diag_insider_deep.py --market US --stage single     # 단독 IC·구간·국면
    .venv/bin/python tools/diag_insider_deep.py --market US --stage marginal   # C0·AR 위 한계기여(워크포워드 GBM)
    .venv/bin/python tools/diag_insider_deep.py --market KR --stage coverage   # 국장 원문 파싱 커버리지·elestock 대조

## 날짜 상한 — 11/23 금고 봉인

탐색 자료는 **2026-09-30 까지**다(7/1~9/30 은 10/13 금고 early 로 소진돼 탐색 자료가 됐다). 2026-10-01 이후는
11/23 second 금고다. 이 도구는:

- 창고를 ``as_of = 2026-09-30 23:59 KST`` 로만 읽는다(그 뒤에 관측된 행은 게이트가 막는다).
- 가격은 ``until = 2026-10-01 00:00 KST``(배타)로 자른다.
- 라벨(h5 = t+1 종가 → t+6 종가)이 9/30 을 넘는 세션은 **버린다** — 9/30 까지의 마지막 거래일에서 6세션 앞까지만 쓴다.
- 패널·피처·라벨의 날짜가 상한을 넘으면 `SealBreach` 로 **멈춘다**(경고가 아니다).

## 무엇을 재나

- 단독: 피처별 일별 순위 IC(h5)·NW t(lag 4), 0 이 아닌 행의 z-수익 구간(값 오분위 / 개수 0·1·2·3+), 국면(박스 ≤2024-12-31 · 급등 그 뒤) 부호.
- 한계기여: 워크포워드 GBM(시행 L 규격, 잎 7·min_data 2,000·lr 0.03·300 라운드, 학습 = 블록 시작 − 퍼지 5 까지)으로
  C0(점수 6) · AR(C0 + G7 또는 G4) · AR + 새 피처 묶음. ΔIC(h5)·NW t·상위 24 z-수익·국면별.
  **진단용으로 스레드 2 · 시험 블록 60세션**이다(등록 시행의 20세션 블록·스레드 6 과 다르다 — 등록 때 시행 규격으로 다시 잰다).

산출물은 ``data/_diag/insider-deep/``(git 밖). 결과 해석은 docs/diag/insider-deep-2026-10.md.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts import insider_deep as deep  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
#: **탐색 자료 상한.** 2026-10-01 이후는 11/23 second 금고 — 수익·IC 를 계산하지 않는다.
SEAL_END = date(2026, 9, 30)
AS_OF = datetime.combine(SEAL_END, time(23, 59), tzinfo=KST)
PRICE_UNTIL = datetime.combine(SEAL_END + timedelta(days=1), time(0, 0), tzinfo=KST)
ENTRY_LAG, HORIZON = 1, 5
BOX_END = date(2024, 12, 31)
FEATS = ["chart", "event", "flow", "fundamental", "regime", "risk"]
G4 = ["insider_sell_60", "insider_net_60"]
G7 = ["form4_sell_60", "form4_sellers_20", "form4_buy_60"]
TOP_N = 24
MIN_TRAIN, BLOCK, PURGE = 250, 60, 5
WARMUP_DAYS = 120  # 60세션 창 + ADV20 워밍업(달력일)
#: 일상 판정(직전 3년 같은 달)에 쓸 이력의 시작 연도 — 패널 시작(2021-08) − 워밍업 − 3년보다 앞.
ROUTINE_FROM = 2017


class SealBreach(RuntimeError):
    """금고(2026-10-01~) 자료가 섞였다 — 즉시 멈춘다."""


def seal(frame: pd.DataFrame, col: str = "session", *, what: str = "") -> pd.DataFrame:
    if frame.empty:
        return frame
    last = pd.to_datetime(frame[col]).max().date()
    if last > SEAL_END:
        raise SealBreach(f"{what or col} 에 {last} 가 있다 — 상한 {SEAL_END}(11/23 금고 봉인)")
    return frame


def label_cutoff(calendar: list[date]) -> date:
    """h5 라벨(t+1 → t+6 종가)이 상한 안에 끝나는 마지막 신호 세션."""
    cal = [d for d in calendar if d <= SEAL_END]
    return cal[-1 - (ENTRY_LAG + HORIZON)]


# --------------------------------------------------------------------------- 패널


def _scores_us(work: Path) -> pd.DataFrame:
    parts = []
    for name, col in (("chart", "chart"), ("event", "event"), ("flow_us", "flow"), ("fundamental", "fundamental"),
                      ("regime", "regime"), ("risk", "risk")):
        fs = sorted(glob.glob(str(work / f"scores-{name}-0*.parquet")))  # invariant-allow: data-access — 작업 캐시
        f = pd.concat([pd.read_parquet(x, columns=["entity_id", "session", "score"]) for x in fs], ignore_index=True)  # invariant-allow: data-access — 작업 캐시
        f["session"] = pd.to_datetime(f["session"]).dt.date
        parts.append(f.drop_duplicates(["entity_id", "session"], keep="last").set_index(["entity_id", "session"])["score"]
                     .astype(np.float32).rename(col))
    scores = pd.concat(parts, axis=1, join="outer")
    # 타깃 파일들은 세션 창이 겹친다(월마다 다시 구움, 합치면 3천만 행 — 한 번에 묶으면 메모리가 터진다, 2026-10-13).
    # 세션마다 **가장 늦게 구운 파일** 하나에서만 읽는다(load_us 는 겹친 값의 평균 — 같은 가격이면 값이 같다).
    files = sorted(glob.glob(str(work / "targets-*.parquet")))  # invariant-allow: data-access — 작업 캐시
    owner: dict[date, int] = {}
    for i, x in enumerate(files):
        s = pd.read_parquet(x, columns=["session"])["session"]  # invariant-allow: data-access — 작업 캐시
        for d in pd.to_datetime(s.unique()).date:
            owner[d] = i
    tparts = []
    for i, x in enumerate(files):
        mine = {d for d, k in owner.items() if k == i}
        if not mine:
            continue
        t = pd.read_parquet(x, columns=["entity_id", "session", "target"])  # invariant-allow: data-access — 작업 캐시
        t["session"] = pd.to_datetime(t["session"]).dt.date
        t = t[t["session"].isin(mine)].drop_duplicates(["entity_id", "session"], keep="last")
        t["target"] = t["target"].astype(np.float32)
        tparts.append(t)
    t = pd.concat(tparts, ignore_index=True).set_index(["entity_id", "session"])["target"]
    return scores.join(t, how="inner").reset_index()


def load_panel(market: str, data: Path) -> pd.DataFrame:
    """점수 6 + 타깃(h5). 국장 = 확장 패널(_diag-long) + 금고 early(7/1~9/30), 미장 = ic-history-us-early + ic-history-us + 금고 early."""
    if market == "KR":
        parts = []
        for cache in (data / "_diag-long", data / "_diag" / "vault-early"):
            cols = []
            for name, col in (("chart", "chart"), ("event", "event"), ("flow_kr", "flow"), ("fundamental", "fundamental"),
                              ("regime", "regime"), ("risk", "risk")):
                f = pd.read_pickle(cache / f"scores-{name}-KR.pkl")  # invariant-allow: data-access — 작업 캐시
                f["session"] = pd.to_datetime(f["session"]).dt.date
                cols.append(f.set_index(["entity_id", "session"])["score"].astype(np.float32).rename(col))
            t = pd.read_pickle(cache / "targets-KR-h5.pkl")  # invariant-allow: data-access — 작업 캐시
            t["session"] = pd.to_datetime(t["session"]).dt.date
            cols.append(t.set_index(["entity_id", "session"])["target"].astype(np.float32))
            parts.append(pd.concat(cols, axis=1, join="outer").dropna(subset=["target"]).reset_index())
        panel = pd.concat(parts, ignore_index=True).drop_duplicates(["entity_id", "session"], keep="last")
    else:
        parts = [_scores_us(data / "ic-history-us-early"), _scores_us(data / "ic-history-us"),
                 _scores_us(data / "_diag" / "vault-early" / "ic-history-us")]
        panel = pd.concat(parts, ignore_index=True).drop_duplicates(["entity_id", "session"], keep="last")
    return panel.sort_values(["session", "entity_id"]).reset_index(drop=True)


def rank_gauss(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    from scipy.stats import norm
    out = frame.copy()
    for c in cols:
        r = out.groupby("session")[c].rank(method="average")
        n = out.groupby("session")[c].transform("count")
        out[c] = norm.ppf((r - 0.5) / n).astype(np.float32)
    return out


# --------------------------------------------------------------------------- 원자료


def price_values(store: Store, market: str, start: date) -> tuple[pd.DataFrame, pd.DataFrame, list[date]]:
    """(거래대금 세션×종목, 종가 세션×종목, 거래일 달력). 해마다 끊어 읽는다(메모리)."""
    vals, closes = [], []
    y0 = start
    while y0 <= SEAL_END:
        y1 = min(date(y0.year + 1, 1, 1), SEAL_END + timedelta(days=1))
        until = min(datetime.combine(y1, time(0), tzinfo=KST), PRICE_UNTIL)
        f = read_prices(store, as_of=AS_OF, lookback=(AS_OF.date() - y0).days + 1, until=until, market=market,
                        columns=["entity_id", "valid_from", "close", "volume", "value"], adjusted=True)
        f = f[f["valid_from"] >= pd.Timestamp(datetime.combine(y0, time(0), tzinfo=KST))]
        f["session"] = f["valid_from"].dt.date
        v = f["value"].where(f["value"].notna(), f["close"] * f["volume"])
        vals.append(f.assign(v=v).pivot_table(index="session", columns="entity_id", values="v", aggfunc="last").astype(np.float32))
        closes.append(f.pivot_table(index="session", columns="entity_id", values="close", aggfunc="last").astype(np.float32))
        print(f"  가격 {market} {y0}~{y1}: {len(f):,}행", flush=True)
        del f
        y0 = y1
    value = pd.concat(vals).sort_index()
    close = pd.concat(closes).sort_index()
    value = value[~value.index.duplicated(keep="last")]
    close = close[~close.index.duplicated(keep="last")]
    seal(pd.DataFrame({"session": value.index}), what="가격")
    return value, close, list(value.index)


def trades_us(store: Store) -> pd.DataFrame:
    """form4_trades 전 이력(일상 판정에 3년 이력이 필요하다). 해마다 끊어 읽는다."""
    parts = []
    for year in range(ROUTINE_FROM, SEAL_END.year + 1):
        until = min(datetime(year + 1, 1, 1, tzinfo=UTC), PRICE_UNTIL)
        f = store.get("form4_trades", as_of=AS_OF, lookback=(AS_OF.date() - date(year, 1, 1)).days + 1, until=until,
                      market="US", columns=["entity_id", "valid_from", "owner_cik", "title", "trans_code", "shares",
                                            "price", "plan_10b5_1"])
        f = f[f["trans_code"].isin(["P", "S"])]
        parts.append(f)
    t = pd.concat(parts, ignore_index=True)
    seal(t.assign(d=t["valid_from"].dt.tz_convert(KST).dt.date), "d", what="form4")
    return t


def trades_kr(store: Store) -> pd.DataFrame:
    t = store.get("insider_reports", as_of=AS_OF, until=PRICE_UNTIL, market="KR")
    if not t.empty:
        seal(t.assign(d=t["valid_from"].dt.tz_convert(KST).dt.date), "d", what="insider_reports")
    return t


# --------------------------------------------------------------------------- 대조 피처(G7·G4, 같은 정의의 벡터판)


def g7_vector(form4: pd.DataFrame, sessions: list[date], adv20: pd.DataFrame) -> pd.DataFrame:
    """`ranker_sources.form4_trading` 과 같은 정의 — 창 계산만 벡터로(검증: --stage validate)."""
    t = form4.copy()
    t["usd"] = t["shares"].astype(float) * t["price"].astype(float)
    t = t[t["usd"] > 0]
    t["filed"] = t["valid_from"].dt.tz_convert("America/New_York").dt.date
    sess = np.array(sessions, dtype="datetime64[D]")
    t["k"] = deep._known_index(t["filed"], sess)
    t = t[t["k"] >= 1]
    ents = pd.Index(sorted(t["entity_id"].unique()))
    t["e"] = ents.get_indexer(t["entity_id"])
    n = len(sessions)
    sells = t[(t["trans_code"] == "S") & (t["plan_10b5_1"].fillna(0.0) != 1.0)]
    buys = t[t["trans_code"] == "P"]
    a = adv20.reindex(index=pd.Index(sessions), columns=ents).to_numpy().T
    s60 = deep._window_sum(ents, n, sells["e"].to_numpy(), sells["k"].to_numpy(), sells["usd"].to_numpy(), 60)
    b60 = deep._window_sum(ents, n, buys["e"].to_numpy(), buys["k"].to_numpy(), buys["usd"].to_numpy(), 60)
    so = sells[sells["owner_cik"].astype(str) != ""]
    owners = pd.Index(sorted(so["owner_cik"].astype(str).unique()))
    c20 = deep._window_distinct(ents, n, so["e"].to_numpy(), owners.get_indexer(so["owner_cik"].astype(str)), so["k"].to_numpy(), 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        mats = {"form4_sell_60": s60 / a, "form4_sellers_20": c20, "form4_buy_60": b60 / a}
    nz = np.zeros_like(s60, dtype=bool)
    for k in mats:
        mats[k] = np.where(np.isfinite(mats[k]), mats[k], 0.0)
        nz |= mats[k] != 0
    ei, si = np.nonzero(nz)
    out = pd.DataFrame({k: m[ei, si] for k, m in mats.items()})
    out.insert(0, "session", np.array(sessions, dtype=object)[si])
    out.insert(0, "entity_id", ents.to_numpy()[ei])
    return out


# --------------------------------------------------------------------------- 단계


def stage_features(market: str, store: Store, data: Path, out: Path) -> None:
    panel = load_panel(market, data)
    cal_start = min(panel["session"]) - timedelta(days=WARMUP_DAYS)
    value, close, calendar = price_values(store, market, cal_start)
    cut = label_cutoff(calendar)
    panel = seal(panel[panel["session"] <= cut], what="패널")
    print(f"패널 {market} {len(panel):,}행 · {panel['session'].min()}~{panel['session'].max()} (라벨 상한 세션 {cut})", flush=True)
    adv20 = deep.adv(value)
    sessions = [d for d in calendar if d <= cut]
    if market == "US":
        raw = trades_us(store)
        norm_t = deep.normalize_us(raw)
        base = g7_vector(raw[raw["valid_from"] >= pd.Timestamp(datetime.combine(cal_start, time(0), tzinfo=UTC))], sessions, adv20)
        base_cols = G7
    else:
        raw = trades_kr(store)
        close_on = close.stack()
        close_on.index = close_on.index.swaplevel()
        norm_t = deep.normalize_kr(raw, close_on=close_on)
        base = pd.DataFrame(columns=["entity_id", "session"])
        base_cols = []
    norm_t = norm_t[norm_t["entity_id"].isin(set(panel["entity_id"]))].reset_index(drop=True)  # 행렬 크기(메모리)
    feats = deep.features(norm_t, sessions, adv20)
    panel = panel.merge(feats, on=["entity_id", "session"], how="left")
    if base_cols:
        panel = panel.merge(base, on=["entity_id", "session"], how="left")
    for c in [*deep.FEATURES, *base_cols]:
        panel[c] = panel[c].fillna(0.0).astype(np.float32)
    seal(panel, what="피처 패널")
    out.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out / f"panel-{market}.parquet", index=False)  # invariant-allow: data-access — 진단 산출물(창고 아님)
    stats = {
        "market": market, "rows": len(panel), "sessions": int(panel["session"].nunique()),
        "first": str(panel["session"].min()), "last": str(panel["session"].max()), "label_cut": str(cut),
        "trades_market": len(norm_t), "routine_share": float(deep.routine_flags(norm_t).mean()) if len(norm_t) else None,
        "nonzero": {c: float((panel[c] != 0).mean()) for c in [*deep.FEATURES, *base_cols]},
    }
    (out / f"features-{market}.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    print(json.dumps(stats, ensure_ascii=False, indent=1), flush=True)


def _daily_ic(frame: pd.DataFrame, col: str) -> pd.Series:
    s = ic_module.daily_ic(frame[["entity_id", "session", col, "target"]].rename(columns={col: "score"}).dropna())
    s.index = pd.to_datetime(pd.Series(s.index)).dt.date.values
    return s


def _nw(s: pd.Series) -> float:
    return float(ic_module.newey_west_t(s.dropna(), lag=4)) if s.notna().sum() > 3 else float("nan")


def stage_single(market: str, out: Path) -> None:
    panel = pd.read_parquet(out / f"panel-{market}.parquet")  # invariant-allow: data-access — 진단 산출물
    seal(panel, what="패널")
    panel["target"] = rank_gauss(panel[["session", "target"]], ["target"])["target"]
    rows = []
    base_cols = G7 if market == "US" else []
    for col in [*deep.FEATURES, *base_cols]:
        ic = _daily_ic(panel, col)
        box, rally = ic[[d <= BOX_END for d in ic.index]], ic[[d > BOX_END for d in ic.index]]
        nz = panel[panel[col] != 0]
        ev = nz.groupby("session")["target"].mean()  # 사건 종목의 z-수익(세션 평균)
        row = {"feature": col, "nonzero": float((panel[col] != 0).mean()), "ic": float(ic.mean()), "ic_t": _nw(ic),
               "ic_box": float(box.mean()), "ic_box_t": _nw(box), "ic_rally": float(rally.mean()), "ic_rally_t": _nw(rally),
               "event_z": float(ev.mean()), "event_t": _nw(ev),
               "event_z_box": float(ev[[d <= BOX_END for d in ev.index]].mean()),
               "event_z_rally": float(ev[[d > BOX_END for d in ev.index]].mean())}
        if col in ("buyers_20", "sellers_20", "form4_sellers_20"):
            for k, lo, hi in (("n1", 1, 1), ("n2", 2, 2), ("n3p", 3, 10**6)):
                g = panel[(panel[col] >= lo) & (panel[col] <= hi)].groupby("session")["target"].mean()
                row[f"bucket_{k}"] = float(g.mean())
                row[f"bucket_{k}_t"] = _nw(g)
                row[f"bucket_{k}_rows"] = int(((panel[col] >= lo) & (panel[col] <= hi)).sum())
        else:
            for sign, part in (("pos", nz[nz[col] > 0]), ("neg", nz[nz[col] < 0])):
                if len(part) < 500:
                    continue
                q = pd.qcut(part[col].abs().rank(method="first"), 5, labels=False)
                for b in range(5):
                    g = part[q == b].groupby("session")["target"].mean()
                    row[f"{sign}_q{b + 1}"] = float(g.mean())
                row[f"{sign}_rows"] = len(part)
        rows.append(row)
        print(f"{col:18s} 비0 {row['nonzero']:.2%} · IC {row['ic']:+.4f} (t {row['ic_t']:+.2f}) · 박스 {row['ic_box']:+.4f} "
              f"(t {row['ic_box_t']:+.2f}) · 급등 {row['ic_rally']:+.4f} (t {row['ic_rally_t']:+.2f}) · 사건 z {row['event_z']:+.4f} "
              f"(t {row['event_t']:+.2f}) 박스 {row['event_z_box']:+.4f} 급등 {row['event_z_rally']:+.4f}", flush=True)
    pd.DataFrame(rows).to_csv(out / f"single-{market}.csv", index=False)
    by_year = []
    for col in [*deep.FEATURES, *base_cols]:
        ic = _daily_ic(panel, col)
        for y, part in ic.groupby([d.year for d in ic.index]):
            by_year.append({"feature": col, "year": y, "ic": float(part.mean()), "t": _nw(part), "n": len(part)})
    pd.DataFrame(by_year).to_csv(out / f"single-{market}-by-year.csv", index=False)
    corr = panel[[*deep.FEATURES, *base_cols]].corr(method="spearman")
    corr.to_csv(out / f"corr-{market}.csv")
    print(corr.round(2).to_string(), flush=True)


def _fit(X: np.ndarray, y: np.ndarray, seed: int):
    import lightgbm as lgb
    params = dict(objective="regression", num_leaves=7, min_data_in_leaf=2000, learning_rate=0.03, bagging_fraction=0.8,
                  bagging_freq=1, feature_fraction=1.0, lambda_l2=1.0, verbose=-1, seed=seed, num_threads=2)
    return lgb.train(params, lgb.Dataset(X, y), num_boost_round=300)


def stage_marginal(market: str, out: Path, arms: dict[str, list[str]], seeds: tuple[int, ...], train_every: int) -> None:
    panel = pd.read_parquet(out / f"panel-{market}.parquet")  # invariant-allow: data-access — 진단 산출물
    seal(panel, what="패널")
    cols = sorted({c for v in arms.values() for c in v})
    panel = rank_gauss(panel, [*cols, "target"])
    for c in cols:
        panel[c] = panel[c].fillna(0.0)
    sessions = sorted(panel["session"].unique())
    blocks, start = [], MIN_TRAIN + PURGE
    while start < len(sessions):
        blocks.append((start, min(start + BLOCK, len(sessions)) - 1))
        start += BLOCK
    keep = set(sessions[::train_every])
    preds = []
    for first, last in blocks:
        train_end = sessions[first - PURGE - 1]
        train = panel[(panel["session"] <= train_end) & panel["session"].isin(keep) & panel["target"].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])][["entity_id", "session", "target", *cols]].copy()
        y = train["target"].to_numpy(np.float32)
        for arm, feats in arms.items():
            X = train[feats].to_numpy(np.float32)
            p = np.mean([_fit(X, y, s).predict(test[feats].to_numpy(np.float32)) for s in seeds], axis=0)
            test[arm] = p
        preds.append(test[["entity_id", "session", "target", *arms]])
        print(f"  블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행 ({len(arms)}팔 × 시드 {len(seeds)})", flush=True)
    pred = pd.concat(preds, ignore_index=True)
    seal(pred, what="예측")
    pred.to_parquet(out / f"marginal-{market}.parquet", index=False)  # invariant-allow: data-access — 진단 산출물
    summary = []
    ics = {arm: _daily_ic(pred, arm) for arm in arms}
    tops = {arm: pred.sort_values(arm, ascending=False).groupby("session").head(TOP_N).groupby("session")["target"].mean() for arm in arms}
    for arm in arms:
        ic, top = ics[arm], tops[arm]
        row = {"arm": arm, "ic": float(ic.mean()), "ic_t": _nw(ic), "top24_z": float(top.mean())}
        for ref in arms:
            if ref == arm:
                continue
            d = (ic - ics[ref]).dropna()
            dt = (top - tops[ref]).dropna()
            row[f"dIC_vs_{ref}"] = float(d.mean())
            row[f"dIC_vs_{ref}_t"] = _nw(d)
            row[f"dTop_vs_{ref}"] = float(dt.mean())
            row[f"dIC_vs_{ref}_box"] = float(d[[x <= BOX_END for x in d.index]].mean())
            row[f"dIC_vs_{ref}_rally"] = float(d[[x > BOX_END for x in d.index]].mean())
            row[f"dTop_vs_{ref}_box"] = float(dt[[x <= BOX_END for x in dt.index]].mean())
            row[f"dTop_vs_{ref}_rally"] = float(dt[[x > BOX_END for x in dt.index]].mean())
            yearly = {}
            for y, part in d.groupby([x.year for x in d.index]):
                yearly[str(y)] = round(float(part.mean()), 4)
            row[f"dIC_vs_{ref}_by_year"] = json.dumps(yearly)
        summary.append(row)
    pd.DataFrame(summary).to_csv(out / f"marginal-{market}.csv", index=False)
    for row in summary:
        print(json.dumps(row, ensure_ascii=False), flush=True)


def stage_validate(store: Store, data: Path, out: Path) -> None:
    """벡터판 G7 이 `ranker_sources.form4_trading`(세션별 구운 월 조각)과 같은지 — 창 계산 검증."""
    cached = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(str(data / "_diag" / "ranker-sources" / "G7-US-2025*.parquet")))],  # invariant-allow: data-access — 작업 캐시
                       ignore_index=True)
    cached["session"] = pd.to_datetime(cached["session"]).dt.date
    panel = pd.read_parquet(out / "panel-US.parquet", columns=["entity_id", "session", *G7])  # invariant-allow: data-access — 진단 산출물
    m = cached.merge(panel, on=["entity_id", "session"], how="inner", suffixes=("_c", "_v"))
    res = {}
    for c in G7:
        a, b = m[f"{c}_c"].fillna(0.0), m[f"{c}_v"].fillna(0.0)
        res[c] = {"rows": len(m), "exact_share": float((np.isclose(a, b, rtol=1e-3, atol=1e-9)).mean()),
                  "spearman": float(a.corr(b, method="spearman"))}
    print(json.dumps(res, indent=1), flush=True)
    (out / "validate-g7.json").write_text(json.dumps(res, indent=1))


def stage_coverage(store: Store, out: Path) -> None:
    """국장 원문 파싱 — 접수 월별 커버리지, elestock(insider_trades) 증감과의 대조(같은 접수번호)."""
    reports = store.get("insider_reports", as_of=AS_OF, until=PRICE_UNTIL, market="KR")
    docs = store.get("documents", as_of=AS_OF, market="KR", columns=["doc_id", "doc_type", "title"])
    docs = docs[(docs["doc_type"] == "ownership")]
    compact = docs["title"].fillna("").astype(str).str.replace(" ", "", regex=False)
    docs = docs[compact.str.startswith("임원ㆍ주요주주특정증권등소유상황보고서")].drop_duplicates("doc_id")
    docs["month"] = docs["doc_id"].astype(str).str[:6]
    done = set(reports["rcept_no"].astype(str))
    cov = docs.assign(done=docs["doc_id"].astype(str).isin(done)).groupby("month")["done"].agg(["size", "sum"])
    cov.to_csv(out / "coverage-KR.csv")
    print(cov.tail(30).to_string(), flush=True)
    ele = store.get("insider_trades", as_of=AS_OF, market="KR", columns=["rcept_no", "change"])
    ele = ele.groupby("rcept_no")["change"].sum()
    parsed = reports.groupby("rcept_no")["change"].sum()
    both = pd.concat([parsed.rename("parsed"), ele.rename("elestock")], axis=1, join="inner").dropna()
    match = np.isclose(both["parsed"], both["elestock"], atol=0.5)
    res = {"reports": int(reports["rcept_no"].nunique()), "lines": len(reports), "overlap_with_elestock": len(both),
           "change_sum_match": float(match.mean()) if len(both) else None,
           "category_share": reports["category"].value_counts(normalize=True).round(4).to_dict(),
           "reason_top": reports["reason"].value_counts().head(25).to_dict(),
           "reporter_type": reports.drop_duplicates("rcept_no")["reporter_type"].value_counts().to_dict(),
           "price_missing_market": float(reports.loc[reports["category"] == "market", "price"].isna().mean())}
    mism = both[~match].head(20)
    mism.to_csv(out / "coverage-KR-mismatch.csv")
    (out / "coverage-KR.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="data")
    parser.add_argument("--market", choices=("KR", "US"), default="US")
    parser.add_argument("--stage", choices=("features", "single", "marginal", "validate", "coverage"), required=True)
    parser.add_argument("--arms", default="", help="JSON {이름: [피처…]} — 비우면 C0·AR·AR+전부")
    parser.add_argument("--seeds", default="0")
    parser.add_argument("--train-every", type=int, default=2, help="학습 세션 솎기(진단 비용)")
    args = parser.parse_args(argv)
    data = Path(args.data)
    out = data / "_diag" / "insider-deep"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root=data)
    if args.stage == "features":
        stage_features(args.market, store, data, out)
    elif args.stage == "single":
        stage_single(args.market, out)
    elif args.stage == "marginal":
        base = G7 if args.market == "US" else G4
        arms = json.loads(args.arms) if args.arms else {"C0": FEATS, "AR": FEATS + base, "AR_new": FEATS + base + list(deep.FEATURES)}
        stage_marginal(args.market, out, arms, tuple(int(s) for s in args.seeds.split(",")), args.train_every)
    elif args.stage == "validate":
        stage_validate(store, data, out)
    else:
        stage_coverage(store, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
