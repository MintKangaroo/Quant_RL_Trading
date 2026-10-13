"""AR(현행 6점수 + 내부자 G4·G7) 대 현행 랭커 — 평가 사다리 첫 시험(2026-10-13 밤, 사용자 "통과면 11/23 전이라도 모의계좌 편입을 다시 묻는다").

    .venv/bin/python tools/diag_ar_vs_current.py context-us    # 미장 공통 자료(가격·SPY·명단·비용) — eval_ladder 형식
    .venv/bin/python tools/diag_ar_vs_current.py style-us      # 미장 스타일 대리(원 종가 × 원 거래량 ADV20 · 원 종가)
    .venv/bin/python tools/diag_ar_vs_current.py walk          # AR·대조 연 단위 워크포워드(시드 5) → 점수 파일
    .venv/bin/python tools/diag_ar_vs_current.py report        # 사다리 · 손실표 · 연도별 ΔIC · 미장 스타일 잔차 IC

**진단이지 시행이 아니다**(10/13 AR 금고 판정은 그대로 — 같은 창으로 다시 판정하지 않는다). 창고·실전·모델 폴더에 쓰지 않는다.
**날짜 상한 2026-09-30**(`eval_ladder.DATE_CAP`).

AR 의 등록 규격(`docs/protocols/insider-forward-2026-09.md`, `trial_insider_forward.freeze_ar`)을 그대로 따른다:
국장+미장 한 모델(is_us), 대조 = 6점수 + is_us, 처리 = 대조 + G4(insider_sell_60·insider_net_60) + G7(form4_sell_60·form4_sellers_20·form4_buy_60),
피처·정답은 (시장, 세션) 안 rank-gauss, 내부자 결측은 0(순위 중앙), 시행 L 하이퍼파라미터. 다른 것은 **학습 방식** 하나 —
얼린 한 번이 아니라 **연 단위 워크포워드**(그해 첫 세션 − 퍼지 5 − 엠바고 5 까지로 배워 그해를 채점), 시드 0~4.

가장 긴 구간(자료 확인 2026-10-13 23시):
- 미장 가격(창고)이 **2021-08-16 부터**다 — form4 는 2006 부터 있지만 그 앞 가격·6점수가 없어 2006~ 워크포워드는 **불가**(보고에 적는다).
  미장 패널 = `data/_diag/insider-deep/panel-US`(2021-08-16~2026-09-21, 6점수·h5 정답·G7, 같은 날 내부자 심화 진단이 as_of 규칙으로 구운 것).
- 국장 `insider_trades` 는 as_of 9/30 기준 **2024-08-30 부터**다(그 앞 G4 는 전부 0). 국장 점수는 확장 패널(2021-11-10~) + 금고 early 캐시,
  G4 는 확장 패널 열(≤ 6/30) + 월 조각 `data/_diag/ranker-sources/G4-KR-2026{07,08,09}`(판정 때와 같은 as_of 입력, '채움' 반사실 아님).
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

from tools import eval_ladder as el  # noqa: E402

OUT = el.OUT / "ar"
CTX_US = el.OUT / "context-US"
US_PANEL = Path("data/_diag/insider-deep/panel-US.parquet")  # invariant-allow: data-access — 진단 패널(내부자 심화)
G4_MONTHS = [Path(f"data/_diag/ranker-sources/G4-KR-2026{m:02d}.parquet") for m in (7, 8, 9)]  # invariant-allow: data-access — 월 조각
FEATS = list(el.SCORE_FEATS)
G4 = ["insider_sell_60", "insider_net_60"]
G7 = ["form4_sell_60", "form4_sellers_20", "form4_buy_60"]
CONTROL = [*FEATS, "is_us"]
TREAT = [*CONTROL, *G4, *G7]
YEARS = (2022, 2023, 2024, 2025, 2026)
GAP = 10                      # 퍼지 5 + 엠바고 5
KR_INSIDER_FROM = date(2024, 9, 2)
US_INDEX = "US:SPY"


def log(msg: str) -> None:
    print(msg, flush=True)


def rank_gauss_inplace(df: pd.DataFrame, cols: list[str], *, keep_nan: bool = False, key: str = "session") -> None:
    """`trial_pooled_rank.rank_gauss` 와 같은 식(세션×시장 안 순위 → 정규분위, 결측 0)을 사본 없이."""
    g = df.groupby(["market", key], sort=False, observed=True)
    for c in cols:
        r = g[c].rank(pct=True)
        cnt = g[c].transform("count")
        v = pd.Series(norm.ppf((r * cnt - 0.5) / cnt), index=df.index)
        df[c] = (v if keep_nan else v.fillna(0.0)).astype(np.float32)
        del r, cnt, v
        gc.collect()


# --------------------------------------------------------------------------- 미장 공통 자료


def us_tradable() -> dict[date, set[str]]:
    """미장 명단 = 내부자 심화 패널에 그 세션 행이 있는 종목. 종목 이름은 범주로 읽어 **같은 문자열 객체를 나눠 쓴다**
    (행마다 문자열 660만 개를 만들면 4GB 상한에 걸린다 — 2026-10-14 00:45 MemoryError)."""
    import pyarrow.parquet as pq  # invariant-allow: data-access — 진단 패널

    t = pq.read_table(US_PANEL, columns=["entity_id", "session"], read_dictionary=["entity_id"])  # invariant-allow: data-access — 진단 패널
    keys = t.to_pandas()
    del t
    keys["session"] = pd.to_datetime(keys["session"]).dt.date
    cats = keys["entity_id"].cat.categories
    codes = keys["entity_id"].cat.codes.to_numpy()
    out: dict[date, set[str]] = {}
    for d, idx in keys.groupby("session").indices.items():
        out[d] = set(cats[codes[idx]])
    del keys
    gc.collect()
    return out


def read_wide(store: Any, *, columns: list[str], adjusted: bool, keep: set[str], start: date,
              months: int = 6) -> dict[str, pd.DataFrame]:
    """미장 가격을 반년 조각으로 읽어 열마다 넓은 표로(종목은 keep 만) — 한 번에 읽으면 1천만 행이 넘는다."""
    from quant_rl_trading.store.prices import read_prices

    parts: dict[str, list[pd.DataFrame]] = {c: [] for c in columns}
    lo = start
    while lo <= el.DATE_CAP:
        hi = date(lo.year + (lo.month - 1 + months) // 12, (lo.month - 1 + months) % 12 + 1, 1)
        until = min(datetime.combine(hi, time(0), tzinfo=UTC), el.CAP_AS_OF)
        f = read_prices(store, as_of=el.CAP_AS_OF, lookback=(el.DATE_CAP - lo).days + 2, until=until, columns=columns,
                        adjusted=adjusted, market="US")
        f = f[f["entity_id"].isin(keep)]
        f["day"] = pd.to_datetime(f["valid_from"]).dt.date
        f = f[(f["day"] >= lo) & (f["day"] < hi) & (f["day"] <= el.DATE_CAP)]
        for c in columns:
            parts[c].append(f.pivot_table(index="day", columns="entity_id", values=c, aggfunc="last").astype(np.float32))
        log(f"  가격 조각 {lo}~{hi} {len(f):,}행")
        del f
        gc.collect()
        lo = hi
    return {c: pd.concat(v).sort_index() for c, v in parts.items()}


def cmd_context_us(root: str, force: bool) -> int:
    el.require_heavy(force)
    from quant_rl_trading.store import Store

    store = Store(root=Path(root))
    trad = us_tradable()
    keep = set().union(*trad.values()) | {US_INDEX}
    log(f"미장 명단 {len(trad)}세션 · 종목 {len(keep):,}")
    close = read_wide(store, columns=["close"], adjusted=True, keep=keep, start=date(2021, 8, 1))["close"].astype("float64")
    el.guard_cap(close.index, "미장 가격")
    if US_INDEX not in close.columns:
        raise SystemExit("US:SPY 가격이 없다 — 벤치 없이 진행하지 않는다")
    spy = close[US_INDEX].copy()
    ret = close.shift(-2) / close.shift(-1) - 1.0
    ret = ret.where(ret.abs() <= el.MAX_MOVE)
    bench = spy.shift(-2) / spy.shift(-1) - 1.0
    days = [d for d in ret.index if d in trad]
    uni = pd.Series({d: float(ret.loc[d].reindex(list(trad[d])).mean()) for d in days})
    cost = float(store.config("accounting.fee_us", as_of=el.CAP_AS_OF))
    ctx = el.Context(close=close, ret=ret, bench=bench, k200_close=spy, trad=trad, uni=uni,
                     scale=pd.Series(1.0, index=days), caps=None, sectors={}, cost=cost, market="US")
    el.save_context(ctx, CTX_US)
    log(f"미장 공통 자료 {CTX_US} · 가격 {close.index[0]}~{close.index[-1]} · 종목 {close.shape[1]} · 명단 {len(trad)}세션 · 편도 {cost:.4%}")
    return 0


def cmd_style_us(root: str, force: bool) -> int:
    """스타일 대리 — 시총 원천이 없어(시점별 발행주식수 없음, 내부자 심화 §3) **원 종가 × 원 거래량의 20일 평균(ADV20)** 과
    **원 종가 수준**을 쓴다. 분할 보정 종가 × 원 거래량(10/7 미장 명단 오염의 원인)은 쓰지 않는다."""
    el.require_heavy(force)
    from quant_rl_trading.store import Store

    store = Store(root=Path(root))
    keep = set().union(*us_tradable().values())
    w = read_wide(store, columns=["close", "volume"], adjusted=False, keep=keep, start=date(2021, 7, 1))
    px = w["close"]
    dv = (w["close"].astype(np.float64) * w["volume"].astype(np.float64)).astype(np.float32)
    del w
    gc.collect()
    adv = dv.rolling(20, min_periods=10).mean().astype(np.float32)
    OUT.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(adv, OUT / "us-adv20.pkl")  # invariant-allow: data-access — 진단 캐시
    pd.to_pickle(px, OUT / "us-rawclose.pkl")  # invariant-allow: data-access — 진단 캐시
    log(f"미장 스타일 대리 ADV20·원 종가 {adv.shape}")
    return 0


# --------------------------------------------------------------------------- 패널·워크포워드


NUM = [*FEATS, *G4, *G7, "is_us", "target"]


def compact(frame: pd.DataFrame, market_code: int) -> pd.DataFrame:
    """행마다 든 문자열·date 객체를 버린다 — 종목은 범주, 세션은 날짜 서수(int32), 숫자는 float32
    (2026-10-14 03:20 합동 패널 이어 붙이기 MemoryError)."""
    out = pd.DataFrame({"entity_id": frame["entity_id"].astype("category"),
                        "ord": (pd.to_datetime(frame["session"]).to_numpy().astype("datetime64[D]").astype(np.int64) + 719163
                                ).astype(np.int32)
                        if not np.issubdtype(frame["session"].dtype, np.integer) else frame["session"].astype(np.int32),
                        "market": np.int8(market_code)})
    for c in NUM:
        out[c] = frame[c].to_numpy(np.float32) if c in frame.columns else np.float32(np.nan)
    return out


def kr_panel() -> pd.DataFrame:
    ctx = el.load_context()
    panel, _ = el.score_panel(ctx)
    panel = panel[["entity_id", "session", "market", *FEATS]]
    long = pd.read_parquet(el.PANEL_LONG, columns=["entity_id", "session", "market", *G4])  # invariant-allow: data-access — 연구 패널
    long = long[long["market"] == "KR"].drop(columns="market")
    long["session"] = pd.to_datetime(long["session"]).dt.date
    early = pd.concat([pd.read_parquet(p) for p in G4_MONTHS], ignore_index=True)  # invariant-allow: data-access — 월 조각
    early["session"] = pd.to_datetime(early["session"]).dt.date
    early = early[early["session"] > max(long["session"])]
    g4 = pd.concat([long, early[["entity_id", "session", *G4]]], ignore_index=True)
    panel = panel.merge(g4, on=["entity_id", "session"], how="left")
    del long, early, g4
    gc.collect()
    y = ctx.y5()
    panel = panel.merge(y, on=["entity_id", "session"], how="left")
    del ctx, y
    gc.collect()
    panel["is_us"] = 0.0
    out = compact(panel, 0)
    del panel
    gc.collect()
    return out


def us_panel() -> pd.DataFrame:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq  # invariant-allow: data-access — 진단 패널

    t = pq.read_table(US_PANEL, columns=["entity_id", "session", *FEATS, "target", *G7],  # invariant-allow: data-access — 진단 패널
                      read_dictionary=["entity_id"])
    days = pc.cast(pc.cast(t.column("session"), "date32"), "int32").to_numpy() + 719163   # 1970-01-01 의 서수
    t = t.drop(["session"])
    f = t.to_pandas()
    del t
    f["session"] = days.astype(np.int32)
    f = f[f["session"] <= el.DATE_CAP.toordinal()]
    if len(f) and int(f["session"].max()) > el.DATE_CAP.toordinal():
        raise el.CapViolation("미장 패널에 봉인선 뒤 세션")
    f["is_us"] = 1.0
    out = compact(f, 1)
    del f
    gc.collect()
    return out


def build_pooled() -> tuple[pd.DataFrame, list[date], pd.Index]:
    kr = kr_panel()
    log(f"국장 패널 {len(kr):,}행")
    us = us_panel()
    log(f"미장 패널 {len(us):,}행 {date.fromordinal(int(us['ord'].min()))}~{date.fromordinal(int(us['ord'].max()))}")
    from pandas.api.types import union_categoricals

    ents = union_categoricals([kr["entity_id"], us["entity_id"]]).categories
    for f in (kr, us):
        f["ent"] = pd.Categorical(f["entity_id"], categories=ents).codes.astype(np.int32)
        f.drop(columns="entity_id", inplace=True)
    panel = pd.concat([kr, us], ignore_index=True, copy=False)
    del kr, us
    gc.collect()
    ords = np.sort(panel["ord"].unique())
    axis = [date.fromordinal(int(o)) for o in ords]
    panel["sidx"] = np.searchsorted(ords, panel["ord"].to_numpy()).astype(np.int32)
    panel.drop(columns="ord", inplace=True)
    have = panel["target"].notna().to_numpy()
    rank_gauss_inplace(panel, [*FEATS, *G4, *G7, "target"], key="sidx")
    panel.loc[~have, "target"] = np.nan
    log(f"합동 패널 {len(panel):,}행 · 세션 {len(axis)} · 종목 {len(ents):,} · 정답 {int(have.sum()):,}")
    return panel, axis, ents


def fit(ds: Any, seed: int):  # type: ignore[no-untyped-def]
    return el.fit(None, None, seed=seed, dataset=ds)


def cmd_walk(force: bool, seeds: tuple[int, ...]) -> int:
    el.require_heavy(force)
    import lightgbm as lgb

    panel, axis, ents = build_pooled()
    sidx = panel["sidx"].to_numpy()
    OUT.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {"folds": []}
    for arm, cols in (("ARc", CONTROL), ("AR", TREAT)):
        preds = {s: np.full(len(panel), np.nan, dtype=np.float32) for s in seeds}
        xall = panel[cols].to_numpy(np.float32)
        yall = panel["target"].to_numpy(np.float32)
        for year in YEARS:
            lo = next(i for i, d in enumerate(axis) if d >= date(year, 1, 1))
            hi = max(i for i, d in enumerate(axis) if d <= min(date(year, 12, 31), el.DATE_CAP))
            te = lo - GAP - 1
            idx_tr = np.flatnonzero((sidx <= te) & ~np.isnan(yall))
            idx_te = np.flatnonzero((sidx >= lo) & (sidx <= hi))
            ds = lgb.Dataset(xall[idx_tr], yall[idx_tr], free_raw_data=True)
            ds.construct()
            gc.collect()
            xt = xall[idx_te]
            for s in seeds:
                preds[s][idx_te] = fit(ds, s).predict(xt).astype(np.float32)
            meta["folds"].append({"arm": arm, "year": year, "train_end": str(axis[te]), "n_train": int(len(idx_tr)),
                                  "n_test": int(len(idx_te))})
            log(f"  {arm} {year}: 학습 ~{axis[te]} {len(idx_tr):,}행 · 채점 {axis[lo]}~{axis[hi]} {len(idx_te):,}행")
            del ds, xt
            gc.collect()
        del xall, yall
        ax = np.array(axis, dtype=object)
        ent_names = np.asarray(ents, dtype=object)
        for market, code in (("KR", 0), ("US", 1)):
            mask = (panel["market"] == code).to_numpy()

            def gen(mask: np.ndarray = mask, preds: dict = preds, ax: np.ndarray = ax,
                    ent_names: np.ndarray = ent_names):  # type: ignore[no-untyped-def]
                for s in seeds:
                    ok = mask & ~np.isnan(preds[s])
                    yield s, pd.DataFrame({"entity_id": ent_names[panel["ent"].to_numpy()[ok]],
                                           "session": ax[sidx[ok]], "pred": preds[s][ok].astype(float)})
            el.write_scores(gen(), el.score_path(f"{arm}-{market}", OUT))
            log(f"  {arm}-{market} 저장")
        del preds
        gc.collect()
    (OUT / "walk-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


# --------------------------------------------------------------------------- 보고


def yearly_dic(a: Any, b: Any, ctx: el.Context) -> dict[str, Any]:
    ia = el.ic_frame(a, ctx.y5(), ctx.trad).mean(axis=1)
    ib = el.ic_frame(b, ctx.y5(), ctx.trad).mean(axis=1)
    d = (ia - ib).dropna()
    by = d.groupby([x.year for x in d.index])
    return {"all": el.describe(d, ctx, scale=1),
            "years": {int(y): {"dic": float(v.mean()), "t": el.nw_t(v), "n": int(len(v))} for y, v in by}}


def style_residual_ic(ctx: el.Context) -> dict[str, Any]:
    """미장 G7 원 열 · AR−대조 점수 차의 IC — 원래 / 스타일(log ADV20 · log 원 종가 · 60일 변동성) 잔차.
    세션마다 피처 rank-gauss 를 세 스타일 z 에 최소제곱으로 회귀한 잔차의 순위 IC(대 h5 라벨)."""
    adv = pd.read_pickle(OUT / "us-adv20.pkl")  # invariant-allow: data-access — 진단 캐시
    px = pd.read_pickle(OUT / "us-rawclose.pkl")  # invariant-allow: data-access — 진단 캐시
    vol = ctx.close.pct_change(fill_method=None).rolling(60, min_periods=30).std()
    raw = pd.read_parquet(US_PANEL, columns=["entity_id", "session", *G7])  # invariant-allow: data-access — 진단 패널
    raw["session"] = pd.to_datetime(raw["session"]).dt.date
    ar = el.bundle(el.read_scores(el.score_path("AR-US", OUT))).rename(columns={"pred": "ar"})
    arc = el.bundle(el.read_scores(el.score_path("ARc-US", OUT))).rename(columns={"pred": "arc"})
    m = raw.merge(ar, on=["entity_id", "session"], how="left").merge(arc, on=["entity_id", "session"], how="left")
    del raw, ar, arc
    m["diff"] = m["ar"].rank(pct=True) - m["arc"].rank(pct=True)
    y = ctx.y5()
    m = m.merge(y, on=["entity_id", "session"], how="inner")

    def stack(w: pd.DataFrame, name: str) -> pd.DataFrame:
        s = w.stack().rename(name).reset_index()
        s.columns = ["session", "entity_id", name]
        return s
    for w, name in ((np.log(adv.where(adv > 0)), "ladv"), (np.log(px.where(px > 0)), "lpx"), (vol, "vol")):
        m = m.merge(stack(w, name), on=["session", "entity_id"], how="left")
    del adv, px, vol
    gc.collect()
    m = m.dropna(subset=["ladv", "lpx", "vol", "target"])
    feats = [*G7, "ar", "arc", "diff"]
    rows: dict[str, dict[str, list[float]]] = {f: {"raw": [], "res": [], "res2": []} for f in feats}
    days = []
    for day, part in m.groupby("session"):
        if len(part) < 200:
            continue
        Z = part[["ladv", "lpx", "vol"]].to_numpy(float)
        Z = (Z - Z.mean(0)) / np.where(Z.std(0) > 0, Z.std(0), 1)
        Z = np.column_stack([np.ones(len(Z)), Z])
        ty = part["target"].rank(pct=True).to_numpy(float)
        ty_res = ty - Z @ np.linalg.lstsq(Z, ty, rcond=None)[0]
        days.append(day)
        for f in feats:
            x = part[f].rank(pct=True).to_numpy(float)
            ok = ~np.isnan(x)
            if ok.sum() < 200:
                for k in ("raw", "res", "res2"):
                    rows[f][k].append(np.nan)
                continue
            xr = np.full(len(x), np.nan)
            xr[ok] = x[ok] - Z[ok] @ np.linalg.lstsq(Z[ok], x[ok], rcond=None)[0]
            rows[f]["raw"].append(float(np.corrcoef(x[ok], ty[ok])[0, 1]))
            rows[f]["res"].append(float(pd.Series(xr[ok]).corr(pd.Series(ty[ok]), method="spearman")))
            rows[f]["res2"].append(float(pd.Series(xr[ok]).corr(pd.Series(ty_res[ok]), method="spearman")))
    out = {}
    for f in feats:
        out[f] = {k: el.describe(pd.Series(v, index=days), ctx, scale=1) for k, v in rows[f].items()}
    return out


def _ladder_block(name: str, a: Any, b: Any, ctx: el.Context, *, n_tests: int, md: list[str], out: dict[str, Any]) -> None:
    res = el.ladder(a, ctx, control=b, n_tests=n_tests)
    out[name] = res
    md += [el.render(name, res), ""]
    from tools.diag_translation_loss import _seed_lines
    md += _seed_lines(res)


def cmd_report(force: bool) -> int:
    el.require_heavy(force)
    out: dict[str, Any] = {"cap": str(el.DATE_CAP)}
    md = [f"# AR 대 현행 — 진단 원본(상한 {el.DATE_CAP})", ""]
    # 국장
    ctx = el.load_context()
    ar, arc = el.read_scores(el.score_path("AR-KR", OUT)), el.read_scores(el.score_path("ARc-KR", OUT))
    c0 = el.read_scores(el.score_path("C0"))
    for label, lo in (("국장 전체", None), ("국장 내부자 창", KR_INSIDER_FROM)):
        a, b = el.restrict(ar, lo, None), el.restrict(arc, lo, None)
        _ladder_block(f"{label} — AR 대 AR 대조(같은 규격, 내부자만 다름)", a, b, ctx, n_tests=4, md=md, out=out)
        lo2 = lo or date(2022, 1, 1)
        _ladder_block(f"{label} — AR 대 C0(현행 랭커 예측)", a, el.restrict(c0, lo2, None), ctx, n_tests=4, md=md, out=out)
        out[f"{label} 연도별 ΔIC"] = yearly_dic(a, b, ctx)
        lt = el.loss_table(a, ctx)
        out[f"{label} 손실표 AR"] = lt
        md += [el.render_loss(f"{label} AR", lt), ""]
        lt = el.loss_table(b, ctx)
        out[f"{label} 손실표 AR 대조"] = lt
        md += [el.render_loss(f"{label} AR 대조", lt), ""]
    del ctx
    gc.collect()
    # 미장
    ctx = el.load_context(CTX_US)
    ar, arc = el.read_scores(el.score_path("AR-US", OUT)), el.read_scores(el.score_path("ARc-US", OUT))
    _ladder_block("미장 — AR 대 AR 대조", ar, arc, ctx, n_tests=4, md=md, out=out)
    out["미장 연도별 ΔIC"] = yearly_dic(ar, arc, ctx)
    for name, sc in (("AR", ar), ("AR 대조", arc)):
        lt = el.loss_table(sc, ctx)
        out[f"미장 손실표 {name}"] = lt
        md += [el.render_loss(f"미장 {name}(벤치 SPY · 편도 {ctx.cost:.2%} · V6 없음)", lt), ""]
    sr = style_residual_ic(ctx)
    out["미장 스타일 잔차 IC"] = sr
    md += ["### 미장 스타일 잔차 IC (log ADV20 · log 원 종가 · 60일 변동성)", "",
           "| 열 | 원 IC (t) | 피처 잔차 IC (t) | 둘 다 잔차 (t) | 잔차 특수 뺀 쪽 (t) |", "|---|---|---|---|---|"]
    for f, v in sr.items():
        md.append(f"| {f} | {v['raw']['ann']:+.4f} ({v['raw']['t']:+.2f}) | {v['res']['ann']:+.4f} ({v['res']['t']:+.2f}) | "
                  f"{v['res2']['ann']:+.4f} ({v['res2']['t']:+.2f}) | {v['res']['ex_special']['ann']:+.4f} ({v['res']['ex_special']['t']:+.2f}) |")
    for k in ("국장 전체 연도별 ΔIC", "국장 내부자 창 연도별 ΔIC", "미장 연도별 ΔIC"):
        v = out[k]
        md += ["", f"{k}: 전체 {v['all']['ann']:+.4f}(t {v['all']['t']:+.2f}) · 특수 뺀 쪽 {v['all']['ex_special']['ann']:+.4f}"
               f"(t {v['all']['ex_special']['t']:+.2f}) · " + " · ".join(f"{y} {d['dic']:+.4f}(t {d['t']:+.2f})" for y, d in v["years"].items())]
    (OUT / "report.json").write_text(json.dumps(el._jsonable(out), ensure_ascii=False, indent=1))
    (OUT / "report.md").write_text("\n".join(md))
    log("\n".join(md))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("context-us", "style-us", "walk", "report"))
    ap.add_argument("--root", default="data")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="*", default=list(el.SEEDS))
    args = ap.parse_args(argv)
    if args.cmd == "context-us":
        return cmd_context_us(args.root, args.force)
    if args.cmd == "style-us":
        return cmd_style_us(args.root, args.force)
    if args.cmd == "walk":
        return cmd_walk(args.force, tuple(args.seeds))
    return cmd_report(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
