"""미장 알파 진단 — docs/diag/us-alpha.md §1 대로 한 번 잰다.

    nice -n 10 .venv/bin/python tools/diag_us_alpha.py fetch-etf      # 스타일 ETF 일봉 → 연구 캐시 (LS g3204 조회 TR, sujung=Y)
    nice -n 10 .venv/bin/python tools/diag_us_alpha.py check          # 검산 A — 두 미장 패널의 키·점수·y5 일치(가볍다)
    nice -n 10 taskset -c 0-3 .venv/bin/python tools/diag_us_alpha.py bake --arm old   # 미장 단독 C0·C1 예측(old · new · cut)
    nice -n 10 taskset -c 0-3 .venv/bin/python tools/diag_us_alpha.py run              # ①~④ 계산
    .venv/bin/python tools/diag_us_alpha.py shadow                    # ⑤ shadow 장부 점검(읽기만)
    .venv/bin/python tools/diag_us_alpha.py report                    # 표

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/us-alpha/` 에 둔다. 예측 굽기는 `final_round_kit.controls` 를, 포트는 `trial_us_kit.book` 의 루프를,
헤지는 `diag_hedged_sleeve.hedge_ratio`·`diag_style_hedge_cost.hedge_multi` 를 그대로 쓴다(검산 C·D 가 일치를 본다).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = Path("data/_diag/us-alpha")
#: 금고(research.holdout.start = 2026-07-01) 앞날까지만 받는다.
ETF_END = date(2026, 6, 30)
#: 스타일 ETF — SPY·MDY 는 창고에도 있다(검산용으로 같이 받는다). 섹터 11 은 `data/_diag/sector-rotation/us-spdr.parquet` 를 읽는다.
STYLE_ETFS = ("SPY", "RSP", "MDY", "IJR", "IWM", "QQQ")
SPDR_PATH = Path("data/_diag/sector-rotation/us-spdr.parquet")  # invariant-allow: data-access — 진단 원본(LS g3204 연구 캐시)

ARMS = ("old", "new", "cut", "blank")
CUT_START = date(2022, 7, 1)
#: U-blank — U-cut 에서 회차 패널이 비어 있던 세션(미장 FA 첫 채움 전)의 FA 열을 회차 패널처럼 비운다(값 0 · 표지 1).
#: 같은 (고친) 명단 위에서 채움 효과만 떼어 보는 군이다(§1 정정, 2026-10-07 검산 A).
PRED_DIRS = {a: OUT / f"pred-{a}" for a in ARMS}
#: ETF 원본은 입력이다 — 스모크가 OUT 을 smoke/ 로 바꿔도 같은 파일을 읽는다(10/8 스모크가 smoke/etf.parquet 를 찾다 멈췄다).
ETF_PATH = OUT / "etf.parquet"  # invariant-allow: data-access — 진단 원본(LS g3204 연구 캐시)
J_TAG = "KR+US-20220701-20260630"

NS = (24, 50, 100)
EVERY = (10, 20)
MULTS = (3, 5)
BASE_EVERY, BASE_MULT = 10, 3
HEDGES = {  # 꼬리표 → (수단, 방식, 실행 가능)
    "HU": (("U",), "single", False),
    "HSPY": (("SPY",), "single", True),
    "HRSP": (("RSP",), "single", True),
    "HMDY": (("MDY",), "single", True),
    "HIJR": (("IJR",), "single", True),
    "HIWM": (("IWM",), "single", True),
    "BSTY": (("SPY", "MDY", "IJR", "IWM", "RSP"), "nnls", True),
    "BSEC": (("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"), "nnls", True),
}
CROSS_HEDGES = ("HU", "HSPY", "HRSP", "HIWM", "BSTY", "BSEC")
CARRY, CARRY_HI = 0.01, 0.02
BASKET_CAP = 1.5
#: 실현 구간 (시작, 끝] — `diag_bear_2022.in_window`. 2022H2 는 U-new 만 예측이 있다.
WINDOWS = {"2022H2": (date(2022, 6, 30), date(2023, 2, 15)), "박스": (date(2023, 2, 15), date(2024, 12, 31)),
           "급등": (date(2024, 12, 31), date(2026, 6, 30)), "공통": (date(2023, 2, 15), date(2026, 6, 30))}
SOURCES_24 = ("J-C0", "U-new-C0", "U-new-C1")      # ②~④ 의 예측 원천(J-C0 이 기본)
ALL_SOURCES = ("J-C0", "J-C1", "U-old-C0", "U-old-C1", "U-cut-C0", "U-cut-C1", "U-blank-C1", "U-new-C0", "U-new-C1")
SCORES = ("chart", "event", "flow", "fundamental", "regime", "risk")
SEEDS = (0, 1, 2, 3, 4)
if os.environ.get("US_ALPHA_SMOKE"):          # 배선 확인용 — 시드 0 · N 24 만, 원본은 smoke/ 아래
    SEEDS, NS, OUT = (0,), (24,), OUT / "smoke"
MIN_AVAILABLE_GB, MAX_RSS_MB = 6.0, 4096.0


# --------------------------------------------------------------------------- 자원·시간


def available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 / 1024
    return float("nan")


def gate(now: datetime) -> str | None:
    """무거운 단계를 시작하면 안 되는 사유 — 평일 장 중 08:30~15:45 · DART 00:45~07:30 · 22:20~00:40 (KST)."""
    kst = now.astimezone(pd.Timestamp(0).tz_localize("Asia/Seoul").tzinfo)
    hm = kst.hour * 60 + kst.minute
    if kst.weekday() < 5 and 8 * 60 + 30 <= hm < 15 * 60 + 45:
        return "평일 장 중"
    if 45 <= hm < 7 * 60 + 30:
        return "DART 수집 시간"
    if hm >= 22 * 60 + 20 or hm < 40:
        return "22:20~00:40"
    if available_gb() < MIN_AVAILABLE_GB:
        return f"가용 메모리 {available_gb():.1f}GB < {MIN_AVAILABLE_GB}GB"
    return None


def guard(stage: str) -> None:
    from tools import final_round_kit as fkit
    rss = fkit.rss_mb()
    print(f"[{stage}] 최대 RSS {rss:.0f}MB · 가용 {available_gb():.1f}GB", flush=True)
    if rss > MAX_RSS_MB:
        print(f"RSS {rss:.0f}MB > {MAX_RSS_MB:.0f}MB — 멈춘다", flush=True)
        raise SystemExit(8)


def _wall() -> datetime:
    return datetime.now(UTC)  # invariant-allow: wallclock — 진단 실행 시간 관문(값에는 안 들어간다)


# --------------------------------------------------------------------------- 자료


def fetch_closes(symbols: tuple[str, ...], *, start: date = date(2021, 1, 1), end: date = ETF_END) -> pd.DataFrame:
    """g3204 일봉 종가(수정주가 sujung=Y) — `diag_sector_rotation.fetch_us` 와 같은 호출을 종목 목록만 바꿔 쓴다."""
    from quant_rl_trading.collectors import ls_us_source as ls
    from quant_rl_trading.settings import load_env

    load_env()
    src = ls.LsUsSource.from_env()
    rows = []
    for symbol in symbols:
        exchange = src.resolve_exchange(symbol)
        if exchange is None:
            raise SystemExit(f"{symbol}: 거래소 못 찾음")
        got: dict[str, float] = {}
        cursor = end
        while cursor >= start:
            payload = src.client.request_tr(ls.PATH_CHART, ls.TR_CHART, {f"{ls.TR_CHART}InBlock": {
                "sujung": "Y", "delaygb": "R", "comp_yn": "N", "keysymbol": f"{exchange}{symbol}", "exchcd": exchange,
                "symbol": symbol, "gubun": "2", "qrycnt": ls.MAX_ROWS_PER_CALL, "sdate": start.strftime("%Y%m%d"),
                "edate": cursor.strftime("%Y%m%d"), "cts_date": "", "cts_info": ""}})
            days = [r for r in payload.get(f"{ls.TR_CHART}OutBlock1") or [] if r.get("date")]
            if not days:
                break
            for r in days:
                got[r["date"]] = float(r["close"])
            oldest = datetime.strptime(min(r["date"] for r in days), "%Y%m%d").date()
            nxt = oldest - timedelta(days=1)
            if nxt >= cursor:
                break
            cursor = nxt
        rows += [{"day": datetime.strptime(k, "%Y%m%d").date(), "symbol": symbol, "close": v} for k, v in got.items()]
        print(f"{symbol}({exchange}) {len(got)}행 {min(got)}~{max(got)}", flush=True)
    return pd.DataFrame(rows)


def fetch_etf(_args: argparse.Namespace) -> int:
    frame = fetch_closes(STYLE_ETFS)
    frame = frame[frame["day"] <= ETF_END]
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(ETF_PATH)  # invariant-allow: data-access — 진단 원본(LS g3204 연구 캐시)
    levels = frame.pivot(index="day", columns="symbol", values="close").sort_index()
    jumps = levels.pct_change().abs().stack()
    print(f"|일수익| > 15% 칸: {int((jumps > 0.15).sum())} · 빈 칸: {int(levels.isna().sum().sum())}", flush=True)
    return 0


def etf_returns() -> pd.DataFrame:
    """ETF 여섯 + 섹터 11 의 결정 t 값 = 종가 t+1 → t+2(kit 벤치 정렬)."""
    e = pd.read_parquet(ETF_PATH).pivot(index="day", columns="symbol", values="close")  # invariant-allow: data-access — 진단 원본
    s = pd.read_parquet(SPDR_PATH).pivot(index="day", columns="sector", values="close")  # invariant-allow: data-access — 진단 원본
    lv = pd.concat([e, s], axis=1).sort_index()
    lv = lv[lv.index <= ETF_END]
    return lv.shift(-2) / lv.shift(-1) - 1.0


def panel_path(arm: str) -> Path:
    from tools import final_round_kit as fkit
    if arm == "old":
        return fkit.CACHE / f"panel-US-{J_TAG}.parquet"  # invariant-allow: data-access — 연구 패널 캐시
    return fkit.FA2021_DIR / "panel" / "panel-US-KR+US-20211110-20260630.parquet"  # invariant-allow: data-access — 연구 패널 캐시


#: §1 정정 5 — 이 진단이 읽는 패널 캐시(크기 B, 수정 시각). 명단 수정(619ec39) 뒤 누가 같은 자리에 새로 구웠으면 멈춘다.
PANEL_STAMPS = {"old": (56443946, "2026-09-28 04:27"), "new": (97454387, "2026-10-05 07:53")}


def check_stamps() -> None:
    for arm, (size, when) in PANEL_STAMPS.items():
        path = panel_path(arm)
        st = path.stat()
        got = (st.st_size, datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"))  # invariant-allow: wallclock — 파일 시각
        if got != (size, when):
            print(f"{path}: {got} ≠ 고정 {(size, when)} — 패널이 바뀌었다, 멈춘다", flush=True)
            raise SystemExit(6)


def read_panel(arm: str, columns: list[str] | None = None) -> pd.DataFrame:
    from tools import final_round_kit as fkit
    p = pd.read_parquet(panel_path(arm), columns=columns)  # invariant-allow: data-access — 연구 패널 캐시
    p = p[p["market"] == "US"].reset_index(drop=True)
    p["session"] = fkit._as_dates(p["session"])
    if arm in ("cut", "blank"):
        p = p[p["session"] >= CUT_START].reset_index(drop=True)
    return p


def blank_fa(p: pd.DataFrame, feats: list[str]) -> tuple[pd.DataFrame, date]:
    """U-blank — 회차 패널의 미장 FA 가 처음 찬 세션(miss_raw = 0 의 첫 세션) 전의 FA 열을 0 · 표지 1 로 비운다."""
    from tools import final_round_kit as fkit
    old = read_panel("old", ["entity_id", "session", "market", "miss_raw"])
    first = min(old.loc[old["miss_raw"] == 0, "session"])
    del old
    early = (p["session"] < first).to_numpy()
    fa = [c for c in feats if c not in (*fkit.SCORE_FEATS, "is_us")]
    for c in fa:
        col = p[c].to_numpy(copy=True)
        col[early] = 1.0 if c.startswith("miss_") else 0.0
        p[c] = col
    return p, first


def c1_feats(arm: str) -> list[str]:
    from tools import final_round_kit as fkit
    raw = fkit.RAW_DIRS if arm == "old" else fkit.FA2021["raw_dirs"]
    return fkit.feature_names(fkit.blocks_of(("KR", "US"), raw_dirs=raw))


# --------------------------------------------------------------------------- 검산 A · 굽기


def check(_args: argparse.Namespace) -> int:
    """검산 A — U-old 와 U-cut 의 키·점수 6·y5·fund_raw·has_fund 가 같은가. 수익은 보지 않는다."""
    cols = ["entity_id", "session", "market", *SCORES, "y5", "fund_raw", "has_fund"]
    a = read_panel("old", cols).drop(columns="market").set_index(["entity_id", "session"]).sort_index()
    b = read_panel("cut", cols).drop(columns="market").set_index(["entity_id", "session"]).sort_index()
    out: dict = {"old_rows": len(a), "cut_rows": len(b), "only_old": int((~a.index.isin(b.index)).sum()),
                 "only_cut": int((~b.index.isin(a.index)).sum())}
    common = a.index.intersection(b.index)
    a, b = a.loc[common], b.loc[common]
    for c in [*SCORES, "y5", "fund_raw"]:
        x, y = a[c].astype(float).to_numpy(), b[c].astype(float).to_numpy()
        same = np.isclose(x, y, atol=1e-6, equal_nan=True)
        out[c] = {"diff_rows": int((~same).sum()), "max_abs": float(np.nanmax(np.abs(x - y))) if (~same).any() else 0.0}
    out["has_fund"] = int((a["has_fund"].astype(bool) != b["has_fund"].astype(bool)).sum())
    del a, b
    guard("check")
    # 패널 채움(표지) — 굽기 전에 '후' 가 실제로 찼는지 본다(값·라벨은 안 본다).
    flags = ["miss_raw", "miss_g1", "miss_g2", "miss_g3", "miss_g5", "miss_g6", "miss_g7"]
    for arm in ("old", "new"):
        p = read_panel(arm, ["entity_id", "session", "market", *flags])
        p["month"] = pd.to_datetime(p["session"]).dt.to_period("M").astype(str)
        fill = (1.0 - p.groupby("month")[flags].mean()).round(3)
        out[f"fill_{arm}"] = fill.to_dict(orient="index")
        del p
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "check-A.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if not k.startswith("fill_")}, ensure_ascii=False, indent=1))
    for arm in ("old", "new"):
        f = pd.DataFrame(out[f"fill_{arm}"]).T
        print(f"\n채움(표지 0 비율) — {arm}\n" + f.iloc[::3].to_string())
    return 0


def bake(args: argparse.Namespace) -> int:
    from tools import final_round_kit as fkit
    why = None if args.force else gate(_wall())
    if why:
        print(f"시작하지 않는다 — {why}", flush=True)
        return 7
    arm = args.arm
    check_stamps()
    feats = c1_feats(arm)
    panel = read_panel(arm)
    if arm == "blank":
        panel, first = blank_fa(panel, feats)
        print(f"[bake blank] {first} 전 FA 열을 비웠다", flush=True)
    missing = [c for c in feats if c not in panel.columns]
    if missing:
        print(f"패널에 없는 C1 열 {missing[:8]} — 멈춘다", flush=True)
        return 2
    keep = ["entity_id", "session", "market", "y5", *dict.fromkeys([*fkit.SCORE_FEATS, *feats])]
    panel = panel[keep]
    sessions = sorted(pd.unique(panel["session"]))
    bl = fkit.blocks(sessions)
    print(f"[bake {arm}] {len(panel):,}행 · 세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 블록 {len(bl)} "
          f"(첫 판정 {sessions[bl[0][0]]}) · C1 피처 {len(feats)}", flush=True)
    guard(f"bake {arm} 적재")
    # U-blank 의 C0 은 U-cut 의 C0 과 같다(점수 6 열은 안 비운다) — 굽지 않는다.
    fkit.controls(panel, feats, sessions, bl, seeds=SEEDS, cache_dir=PRED_DIRS[arm],
                  arms=("C1",) if arm == "blank" else ("C0", "C1"))
    guard(f"bake {arm} 끝")
    return 0


# --------------------------------------------------------------------------- 예측·명단


def preds(source: str, seed: int) -> pd.DataFrame:
    """예측 원천 → (entity_id, session, pred) 미장 행."""
    from tools import final_round_kit as fkit
    fam, *rest = source.split("-")
    arm = rest[-1]
    if rest[0] == "blank" and arm == "C0":
        rest[0] = "cut"
    if fam == "J":
        p = pd.read_pickle(fkit.control_path(arm, seed, J_TAG))  # invariant-allow: data-access — 대조군 캐시
    else:
        found = sorted(PRED_DIRS[rest[0]].glob(f"pred-{arm}-seed{seed}-*.pkl"))
        if len(found) != 1:
            raise SystemExit(f"{source} seed{seed}: 예측 파일 {len(found)}개 — bake 가 먼저다")
        p = pd.read_pickle(found[0])  # invariant-allow: data-access — 진단 예측 캐시
    p = p[p["market"] == "US"][["entity_id", "session", "pred"]].reset_index(drop=True)
    p["session"] = fkit._as_dates(p["session"])
    return p


def clean_mask(store, names: list[str], days: list[date]) -> tuple[pd.DataFrame, dict]:
    """config 명단 규칙(§1 ②) — 세션 × 종목 불리언. 그날 알 수 있던 원주가·거래대금·시총으로."""
    from quant_rl_trading.store.prices import read_prices
    now = datetime.combine(days[-1], time(23), tzinfo=UTC)
    cfg_at = _wall()      # 설정 현행값(규칙이 9/25~26 에 심겼다)·최신 증권 분류 — trial_largecap_ranker.us_universe 와 같은 한계
    span = (days[-1] - days[0]).days + 60
    kinds = store.get("instrument_types", as_of=cfg_at, lookback=10, market="US",
                      columns=["entity_id", "instrument", "test_issue"])
    types = list(store.config("universe.instrument_types_us", as_of=cfg_at))
    ok = set(kinds[kinds["instrument"].isin(types) & ~kinds["test_issue"].astype(bool)]["entity_id"])
    min_price = float(store.config("universe.min_price_us", as_of=cfg_at))
    min_cap = float(store.config("universe.min_market_cap_us", as_of=cfg_at))
    min_nocap = float(store.config("universe.min_turnover_no_cap_us", as_of=cfg_at))
    min_dv = float(store.config("universe.min_turnover_20d_us", as_of=cfg_at))
    px = read_prices(store, as_of=now, lookback=span, columns=["close", "volume"], adjusted=False, market="US", entity=names)
    px["day"] = pd.to_datetime(px["valid_from"]).dt.date
    close = px.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    vol = px.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last").sort_index()
    del px
    dv = (close * vol).rolling(20, min_periods=10).mean()
    caps = store.get("market_stats", as_of=now, lookback=span, market="US", entity=names,
                     columns=["entity_id", "valid_from", "metric", "value"])
    caps = caps[caps["metric"] == "market_cap"]
    caps["day"] = pd.to_datetime(caps["valid_from"]).dt.date
    cap = caps.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index()
    del caps
    idx = pd.Index(days)
    close, dv = close.reindex(index=idx, columns=names), dv.reindex(index=idx, columns=names)
    cap = cap.reindex(columns=names).reindex(index=cap.index.union(idx)).sort_index().ffill(limit=5).reindex(idx)
    has_cap = cap.notna()
    m = (close >= min_price) & (dv >= min_dv) & ((has_cap & (cap >= min_cap)) | (~has_cap & (dv >= min_nocap)))
    m = m & pd.DataFrame(np.broadcast_to(np.array([n in ok for n in names]), m.shape), index=idx, columns=names)
    rules = {"types": types, "min_price": min_price, "min_cap": min_cap, "min_nocap": min_nocap, "min_dv": min_dv,
             "cap_known_share": float(has_cap.to_numpy().mean())}
    return m, rules


def book(score: pd.DataFrame, ret: pd.DataFrame, cost: float, *, n: int, exit_mult: int,
         every: int) -> tuple[pd.Series, pd.Series, dict[date, list[str]]]:
    """`trial_us_kit.book`(scale 없음) 루프 그대로 + 재조정일 보유 명단·일 회전을 같이 돌려준다(검산 D 가 일치를 본다)."""
    from tools.trial_selection_smoothing import pick_mult
    held: list[str] = []
    prev = None
    out, turns, picks = {}, {}, {}
    for i, day in enumerate(d for d in score.index if d in ret.index):
        if prev is None or i % every == 0:
            row = score.loc[day].dropna()
            if row.empty:
                continue
            held = pick_mult(held, row.sort_values(ascending=False).index, n, exit_mult)
            w = pd.Series(1.0 / len(held), index=held)
            picks[day] = list(held)
        else:
            w = prev
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else float(w.sum())
        out[day] = float((w * dr).sum() - cost * t)
        turns[day] = t
        d = w * (1 + dr)
        total = 1.0 - float(w.sum()) + float(d.sum())
        prev = d / total if total > 0 else w
    return pd.Series(out).sort_index(), pd.Series(turns).sort_index(), picks


# --------------------------------------------------------------------------- 헤지·지표


def nnls_us(rl: pd.Series, x: pd.DataFrame, spy: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """`diag_style_hedge_cost.nnls_ratios` 와 같은 식. 20일 전에는 SPY 1.0(바스켓 밖이면 따로 드는 비율로 돌려준다)."""
    from scipy.optimize import nnls

    from tools.diag_hedged_sleeve import BETA_MIN, BETA_WIN
    y = rl.to_numpy()
    X = x.reindex(rl.index).to_numpy()
    out = np.zeros_like(X)
    fallback = pd.Series(0.0, index=rl.index)
    for i in range(len(y)):
        lo = max(0, i - BETA_WIN)
        yy, XX = y[lo:i], X[lo:i]
        ok = np.isfinite(yy) & np.isfinite(XX).all(axis=1)
        if ok.sum() < BETA_MIN:
            fallback.iloc[i] = 1.0
            continue
        yy, XX = yy[ok] - yy[ok].mean(), XX[ok] - XX[ok].mean(axis=0)
        coef, _ = nnls(XX, yy)
        s = coef.sum()
        out[i] = coef * (BASKET_CAP / s) if s > BASKET_CAP else coef
    del spy
    return pd.DataFrame(out, index=rl.index, columns=x.columns), fallback


def run_hedge(rl: pd.Series, inst: pd.DataFrame, key: str, *, c_h: float, carry: float = CARRY,
              reset_days: set[date] | None = None) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    from tools.diag_hedged_sleeve import hedge_ratio
    from tools.diag_style_hedge_cost import hedge_multi
    cols, how, _ = HEDGES[key]
    if how == "single":
        target = pd.DataFrame({cols[0]: hedge_ratio(rl, inst[cols[0]], "ROLL")})
    else:
        target, fb = nnls_us(rl, inst[list(cols)], inst["SPY"])
        if "SPY" in target.columns:
            target["SPY"] = target["SPY"] + fb
        else:
            target["SPY"] = fb
    free = key == "HU"
    return hedge_multi(rl, inst, target, carry=0.0 if free else carry, c_h=0.0 if free else c_h, reset_days=reset_days)


def ann(r: pd.Series, days: list[date]) -> float:
    return float(r.reindex(days).fillna(0.0).mean() * 252)


def ir(r: pd.Series, days: list[date]) -> float:
    v = r.reindex(days).fillna(0.0)
    return float(v.mean() / v.std() * np.sqrt(252)) if v.std() > 0 else float("nan")


def beta(r: pd.Series, f: pd.Series, days: list[date]) -> float:
    a, b = r.reindex(days).fillna(0.0), f.reindex(days).fillna(0.0)
    return float(np.cov(a, b)[0, 1] / b.var()) if b.var() > 0 else float("nan")


def nw(r: pd.Series, days: list[date]) -> float:
    from quant_rl_trading.analysts import ic as ic_module
    return float(ic_module.newey_west_t(r.reindex(days).fillna(0.0), lag=4))


def stats(r: pd.Series, days: list[date]) -> dict[str, float]:
    from tools.diag_hedged_sleeve import mdd
    v = r.reindex(days).fillna(0.0)
    return {"n": len(days), "ann": ann(v, days), "vol": float(v.std() * np.sqrt(252)), "ir": ir(v, days),
            "nw": nw(v, days), "mdd": mdd(v)}


def sleeve_row(rs, h, ht, rl, inst, uni, spy, days, *, c_h, carry, free) -> dict[str, float]:
    hx = (h * inst[h.columns].reindex(h.index).fillna(0.0)).sum(axis=1)
    out = stats(rs, days)
    out.update({
        "track_corr": float(rl.reindex(days).corr(hx.reindex(days))),
        "beta_spy": beta(rs, spy, days), "beta_u": beta(rs, uni, days),
        "style_resid_ann": ann(uni - hx, days),
        "h_mean": {c: float(h[c].reindex(days).mean()) for c in h.columns if float(h[c].reindex(days).abs().mean()) > 1e-4},
        "h_sum": float(h.sum(axis=1).reindex(days).mean()),
        "hedge_turn": float(ht.reindex(days).sum() * 252 / max(len(days), 1)),
        "hedge_cost": 0.0 if free else float(ht.reindex(days).sum() * 252 / max(len(days), 1) * c_h
                                               + carry * h.sum(axis=1).reindex(days).mean()),
    })
    return out


# --------------------------------------------------------------------------- 본 측정


def run(args: argparse.Namespace) -> int:
    from quant_rl_trading.store import Store
    from tools import final_round_kit as fkit
    from tools import trial_us_kit as ukit
    from tools.diag_bear_2022 import in_window, realized_end
    from tools.diag_hedged_sleeve import hedge, hedge_ratio, ic_series
    from tools.trial_overlay import metrics
    from tools.trial_us_index_minus_losers import cost_one_way
    from tools.trial_us_selection import CRISIS_FLOOR

    why = None if args.force else gate(_wall())
    if why:
        print(f"시작하지 않는다 — {why}", flush=True)
        return 7
    check_stamps()
    store = Store(root=Path("data"))
    ret, bench = ukit.market()
    cost = cost_one_way(store, datetime.combine(date(2026, 6, 30), time(23), tzinfo=UTC))
    inst_all = etf_returns()
    # 패널: 라벨·점수·M1 재료. J·U-old 는 회차 패널의 재무 점수, U-new·U-cut 은 FA2021 의 것(§1).
    cols = ["entity_id", "session", "market", *SCORES, "y5", "fund_raw", "has_fund"]
    pnew = read_panel("new", cols)
    pold = read_panel("old", ["entity_id", "session", "market", "fund_raw", "has_fund"])
    fund = {"new": pnew[["entity_id", "session", "fund_raw", "has_fund"]], "old": pold[["entity_id", "session", "fund_raw", "has_fund"]]}
    y = pnew[["entity_id", "session", "y5"]].dropna()
    calendar = list(ret.index)
    all_days = sorted(d for d in pd.unique(pnew["session"]) if d in ret.index)
    # 명단 규칙은 두 패널 종목의 합집합에 건다 — J·U-old 의 L-clean = 회차 명단 ∩ 규칙(§1 정정).
    names = sorted(set(pd.unique(pnew["entity_id"])) | set(pd.unique(pold["entity_id"])))
    panel_names = pnew.groupby("session")["entity_id"].apply(list).to_dict()
    del pold
    guard("적재")
    mask, rules = clean_mask(store, names, all_days)
    guard("명단")
    uni_panel = pd.Series({d: float(ret.loc[d].reindex(panel_names.get(d, [])).mean()) for d in all_days})
    clean_names = {d: [e for e in panel_names.get(d, []) if bool(mask.at[d, e])] for d in all_days}
    uni_clean = pd.Series({d: float(ret.loc[d].reindex(clean_names[d]).mean()) for d in all_days})
    n_clean = pd.Series({d: len(clean_names[d]) for d in all_days})
    n_panel = pd.Series({d: len(panel_names.get(d, [])) for d in all_days})
    spans = realized_end(all_days, calendar)
    win = {k: in_window(pd.Index(all_days), spans, v) for k, v in WINDOWS.items()}
    spy = bench
    inst = inst_all.reindex(all_days)
    inst["U"] = uni_clean
    regime = ukit.spx_regime(store, all_days, CRISIS_FLOOR, before=True)
    res: dict = {"cost": cost, "rules": rules, "n_clean": {k: float(n_clean.reindex(v).mean()) for k, v in win.items() if v},
                 "n_panel": {k: float(n_panel.reindex(v).mean()) for k, v in win.items() if v},
                 "regime_share": regime.reindex(win["공통"]).value_counts(normalize=True).round(3).to_dict()}
    # ② 기준선 대 ETF
    base_rows = {}
    for k, v in win.items():
        if not v:
            continue
        row = {"U-clean": ann(uni_clean, v), "U-panel": ann(uni_panel, v)}
        for e in STYLE_ETFS:
            x = inst_all[e]
            row[e] = ann(x, v)
            row[f"{e}_corr"] = float(uni_clean.reindex(v).corr(x.reindex(v)))
            row[f"{e}_te"] = float((uni_clean - x).reindex(v).std() * np.sqrt(252))
        base_rows[k] = row
    res["baseline"] = base_rows
    ics: dict[str, pd.Series] = {}
    legs: dict = {}
    for source in ALL_SOURCES:
        primary = source in SOURCES_24
        fam = source.split("-")
        f = fund["old"] if fam[0] == "J" or fam[1] == "old" else fund["new"]
        per_seed_ic, per = [], {}
        for s in SEEDS:
            try:
                p = preds(source, s)
            except SystemExit as exc:
                print(f"{source}: 건너뛴다 — {exc}", flush=True)
                per_seed_ic = []
                break
            per_seed_ic.append(ic_series(p, y))
            wide = fkit.us_m1_wide(p, f)
            clean_wide = wide.where(mask.reindex(index=wide.index, columns=wide.columns).fillna(False).astype(bool))
            combos = [(n, e, m) for n in NS for e in EVERY for m in MULTS] if primary else [(n, BASE_EVERY, BASE_MULT) for n in (24, 100) if n in NS]
            for n, e, m in combos:
                per.setdefault(("clean", n, e, m), {})[s] = book(clean_wide, ret, cost, n=n, exit_mult=m, every=e)
            for n in (24, 100):
                if n in NS:
                    per.setdefault(("panel", n, BASE_EVERY, BASE_MULT), {})[s] = book(wide, ret, cost, n=n, exit_mult=BASE_MULT, every=BASE_EVERY)
            if s == SEEDS[0] and source == "J-C0":
                ref, _ = ukit.book(wide, ret, cost, n=24, exit_mult=BASE_MULT, every=BASE_EVERY)
                mine = per[("panel", 24, BASE_EVERY, BASE_MULT)][s][0]
                res["check_D"] = float((ref - mine.reindex(ref.index)).abs().max())
            if s == SEEDS[0] and primary:
                legs.setdefault("scores", {})[source] = wide          # TC 용(시드 0 점수)
            del p, wide, clean_wide
        if not per_seed_ic:
            continue
        ics[source] = pd.concat(per_seed_ic, axis=1).mean(axis=1)
        legs[source] = per
        guard(source)
    # 점수 IC(패널 그대로) — fundamental 은 has_fund 행만 한 번 더
    score_ic = {}
    for c in SCORES:
        score_ic[c] = ic_series(pnew[["entity_id", "session", c]].rename(columns={c: "pred"}), y)
    hf = pnew[pnew["has_fund"].astype(bool)]
    score_ic["fundamental_has"] = ic_series(hf[["entity_id", "session", "fundamental"]].rename(columns={"fundamental": "pred"}), y)
    del pnew, hf
    res["ic"] = {k: {w: {"ic": float(v.reindex(d).mean()), "nw": nw(v, d)} for w, d in win.items() if d and v.reindex(d).notna().any()}
                 for k, v in {**ics, **score_ic}.items()}
    guard("IC")

    def mean_leg(per_seed: dict) -> tuple[pd.Series, pd.Series]:
        r = pd.DataFrame({s: v[0] for s, v in per_seed.items()}).mean(axis=1)
        t = pd.DataFrame({s: v[1] for s, v in per_seed.items()}).mean(axis=1)
        return r, t

    # ① 상위 N 성과(두 명단, 기본 조합) — 6~8 군
    res["topn"] = {}
    for source, per in legs.items():
        if source == "scores":
            continue
        for (uni_name, n, e, m), seeds in per.items():
            if (e, m) != (BASE_EVERY, BASE_MULT) or n not in (24, 100):
                continue
            rl, turn = mean_leg(seeds)
            days_all = list(rl.index)
            row = {}
            for w, d in win.items():
                d = [x for x in d if x in set(days_all)]
                if len(d) < 40:
                    continue
                mm = metrics(rl.reindex(d), spy.reindex(d).fillna(0.0))
                sel = rl - uni_clean
                row[w] = {**mm, "turn": float(turn.reindex(d).sum() * 252 / len(d)), "sel_ann": ann(sel, d), "sel_ir": ir(sel, d),
                          "seed_ir": [metrics(v[0].reindex(d), spy.reindex(d).fillna(0.0))["ir"] for v in seeds.values()]}
            res["topn"][f"{source}|{uni_name}|{n}"] = row
    guard("①")

    # 검산 C — 앞선 진단 24-EW-ROLL(c_h 0.10%)
    rl, _ = mean_leg(legs["J-C0"][("panel", 24, BASE_EVERY, BASE_MULT)])
    rs, _, _ = hedge(rl, spy, hedge_ratio(rl, spy, "ROLL"))
    prior = json.loads(Path("data/_diag/hedged-sleeve/results-US.json").read_text())
    res["check_C"] = {"mine": ir(rs, win["공통"]), "prior": prior["A"]["24-EW-ROLL"]["전체"]["ir"]}
    print(f"검산 C {res['check_C']} · 검산 D {res.get('check_D')}", flush=True)

    # ②~④ — 원천마다
    res["src"] = {}
    for source in SOURCES_24:
        if source not in legs:
            continue
        per = legs[source]
        out: dict = {"decomp": {}, "hedge": {}, "sens": {}, "cost": {}, "cross": {}, "regime": {}}
        score = legs["scores"][source]
        ic_d = ics[source]
        for n in NS:
            rl, turn = mean_leg(per[("clean", n, BASE_EVERY, BASE_MULT)])
            rlp, _ = mean_leg(per[("panel", n, BASE_EVERY, BASE_MULT)]) if ("panel", n, BASE_EVERY, BASE_MULT) in per else (None, None)
            have = set(rl.index)
            wd = {w: [x for x in d if x in have] for w, d in win.items()}
            wd = {w: d for w, d in wd.items() if len(d) >= 40}
            # ② 분해
            gross = rl + turn * cost
            dec = {}
            for w, d in wd.items():
                bb = hedge_ratio(uni_clean.reindex(rl.index), spy.reindex(rl.index), "ROLL")
                dec[w] = {"long_minus_spy": ann(rl - spy, d), "sel_net": ann(rl - uni_clean, d), "sel_net_ir": ir(rl - uni_clean, d),
                          "sel_gross": ann(gross - uni_clean, d), "sel_gross_ir": ir(gross - uni_clean, d),
                          "style": ann(uni_clean - spy, d), "style_ir": ir(uni_clean - spy, d),
                          "style_beta": ann(uni_clean - bb * spy, d), "beta_u": float(bb.reindex(d).mean())}
                if rlp is not None:
                    dec[w].update({"panel_sel": ann(rlp - uni_panel, d), "panel_style": ann(uni_panel - spy, d),
                                   "panel_long_minus_spy": ann(rlp - spy, d)})
            out["decomp"][n] = dec
            # ③ 헤지
            hrow = {}
            for key in HEDGES:
                rs, h, ht = run_hedge(rl, inst.reindex(rl.index), key, c_h=cost)
                free = key == "HU"
                hrow[key] = {w: sleeve_row(rs, h, ht, rl, inst.reindex(rl.index), uni_clean, spy, d, c_h=cost, carry=CARRY, free=free)
                             for w, d in wd.items()}
                hrow[key]["seed_ir"] = []
                if n == 24 and key != "HU":
                    for v in per[("clean", n, BASE_EVERY, BASE_MULT)].values():
                        r1, _, _ = run_hedge(v[0], inst.reindex(v[0].index), key, c_h=cost)
                        hrow[key]["seed_ir"].append(ir(r1, wd["공통"]))
                reg = {}
                for g in ("bull", "bear", "volatile", "crisis"):
                    d = [x for x in wd.get("공통", []) if regime.get(x) == g]
                    if len(d) >= 20:
                        reg[g] = {"n": len(d), "ann": ann(rs, d), "ir": ir(rs, d)}
                out["regime"].setdefault(n, {})[key] = reg
                if key != "HU":
                    s_hi, _, _ = run_hedge(rl, inst.reindex(rl.index), key, c_h=cost, carry=CARRY_HI)
                    rebal = set(list(rl.index)[::BASE_EVERY])
                    s_h10, _, _ = run_hedge(rl, inst.reindex(rl.index), key, c_h=cost, reset_days=rebal)
                    out["sens"].setdefault(n, {})[key] = {w: {"carry2": ir(s_hi, d), "H10": ir(s_h10, d)} for w, d in wd.items()}
            out["hedge"][n] = hrow
            guard(f"{source} N{n} ③")
            # ④ 비용 격자 + 교차
            base_gross_ir = None
            for e in EVERY:
                for m in MULTS:
                    rl2, turn2 = mean_leg(per[("clean", n, e, m)])
                    gross2 = rl2 + turn2 * cost
                    picks = per[("clean", n, e, m)][SEEDS[0]][2]
                    tc = []
                    for day, held in picks.items():
                        row = score.loc[day].dropna() if day in score.index else pd.Series(dtype=float)
                        row = row[row.index.isin(clean_names.get(day, []))]
                        if len(row) < n + 5:
                            continue
                        z = (row - row.mean()) / row.std()
                        a = pd.Series(-1.0 / len(row), index=row.index)
                        a[a.index.isin(held)] += 1.0 / len(held)
                        tc.append(float(np.corrcoef(a.to_numpy(), z.to_numpy())[0, 1]))
                    cell = {}
                    for w, d in wd.items():
                        dd = [x for x in d if x in set(rl2.index)]
                        u = float(n_clean.reindex(dd).mean())
                        icw = float(ic_d.reindex(dd).mean())
                        cell[w] = {"sel_gross": ann(gross2 - uni_clean, dd), "sel_gross_ir": ir(gross2 - uni_clean, dd),
                                   "sel_net": ann(rl2 - uni_clean, dd), "sel_net_ir": ir(rl2 - uni_clean, dd),
                                   "sel_nw": nw(rl2 - uni_clean, dd), "long": ann(rl2, dd),
                                   "turn": float(turn2.reindex(dd).sum() * 252 / max(len(dd), 1)),
                                   "cost": float(turn2.reindex(dd).sum() * 252 / max(len(dd), 1) * cost),
                                   "ic": icw, "U": u, "BR": u * 252 / e, "TC": float(np.mean(tc)) if tc else float("nan"),
                                   "theory": (float(np.mean(tc)) if tc else float("nan")) * icw * np.sqrt(u * 252 / e)}
                    if (e, m) == (BASE_EVERY, BASE_MULT):
                        base_gross_ir = cell.get("공통", {}).get("sel_gross_ir")
                    out["cost"][f"N{n}-R{e}-K{m}"] = cell
                    for key in CROSS_HEDGES:
                        rs, _, _ = run_hedge(rl2, inst.reindex(rl2.index), key, c_h=cost)
                        out["cross"][f"N{n}-R{e}-K{m}|{key}"] = {w: ir(rs, d) for w, d in wd.items()}
            for e in EVERY:
                for m in MULTS:
                    c = out["cost"][f"N{n}-R{e}-K{m}"].get("공통")
                    if c and base_gross_ir:
                        c["retention"] = c["sel_gross_ir"] / base_gross_ir
            guard(f"{source} N{n} ④")
        res["src"][source] = out
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(f"→ {OUT / 'results.json'}", flush=True)
    return 0


# --------------------------------------------------------------------------- ⑤ shadow


def shadow(_args: argparse.Namespace) -> int:
    from quant_rl_trading.accounting import performance as perf
    from quant_rl_trading.store import Store
    now = _wall()
    out: dict = {}
    for root in ("data/_shadow", "data/_g1us_shadow"):
        st = Store(root=Path(root))
        d = perf._sleeve_daily(st, as_of=now)
        rec: dict = {}
        if d is not None:
            d = d.assign(cash_share=d["cash_usd"].astype(float) / d["nav_usd"])
            rec["daily"] = {str(k): round(float(v), 4) for k, v in zip(d["day"], d["cash_share"], strict=True)}
            rec["cash_mean"] = float(d["cash_share"].mean())
            rec["cash_gt50"] = int((d["cash_share"] > 0.5).sum())
            rec["sessions"] = len(d)
        o = st.get("orders", as_of=now, lookback=60)
        o = o[o["market"] == "US"].sort_values("observed_at").groupby(["session_id", "entity_id", "side", "slice_seq"]).tail(1)
        rec["orders"] = {f"{k[0]}|{k[1]}|{k[2]}": int(v) for k, v in o.groupby(["session_id", "side", "status"]).size().items()}
        rec["reasons"] = o[o["status"] != "filled"]["reason"].fillna("").str[:80].value_counts().head(10).to_dict()
        t = st.get("trades", as_of=now, lookback=60)
        t = t[t["market"] == "US"].copy()
        t["day"] = pd.to_datetime(t["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date.astype(str)
        t["notional"] = t["quantity"].astype(float) * t["price"].astype(float)
        rec["trades"] = {f"{k[0]}|{k[1]}": round(float(v), 0) for k, v in t.groupby(["day", "side"])["notional"].sum().items()}
        # 재조정마다 교체율 — 매수 주문 세션의 목표 명단 대 직전 보유(체결 누적)
        buys = o[(o["side"] == "buy")].groupby("session_id")["entity_id"].apply(set)
        sells = o[(o["side"] == "sell") & (o["target_weight"].astype(float) == 0.0)].groupby("session_id")["entity_id"].apply(set)
        rec["full_exits"] = {k: len(v) for k, v in sells.items()}
        rec["buys"] = {k: len(v) for k, v in buys.items()}
        filled = set(t["order_id"])
        o["filled"] = (o["session_id"] + "|" + o["entity_id"] + "|" + o["side"]).isin(filled)
        rec["fill_rate"] = {f"{k[0]}|{k[1]}": round(float(v), 3) for k, v in o.groupby(["session_id", "side"])["filled"].mean().items()}
        out[root] = rec
    w = Store(root=Path("data")).get("analyst_weights", as_of=now, lookback=60)
    w = w[w["market"] == "US"]
    w["obs"] = pd.to_datetime(w["observed_at"]).dt.tz_convert("Asia/Seoul").dt.strftime("%Y-%m-%d %H:%M")
    out["weights"] = w.pivot_table(index="obs", columns="entity_id", values="weight", aggfunc="last").round(3).to_dict(orient="index")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "shadow.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    for root in ("data/_shadow", "data/_g1us_shadow"):
        r = out[root]
        print(f"== {root}: 세션 {r.get('sessions')} · 현금 평균 {r.get('cash_mean', float('nan')):.1%} · 현금 > 50% {r.get('cash_gt50')} 세션")
        print("  체결률:", r["fill_rate"])
        print("  미체결 사유:", r["reasons"])
    return 0


# --------------------------------------------------------------------------- 표


def _f(v, fmt: str = "%") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    return f"{v:+.1%}" if fmt == "%" else f"{v:+.2f}" if fmt == "s" else f"{v:.2f}"


def report(_args: argparse.Namespace) -> int:
    res = json.loads((OUT / "results.json").read_text())
    print(f"편도 비용 {res['cost']:.2%} · 명단 규칙 {res['rules']} · 검산 C {res['check_C']} · 검산 D {res.get('check_D')}")
    print(f"세션당 종목 U-panel {res['n_panel']} · U-clean {res['n_clean']} · 공통 창 국면 {res['regime_share']}\n")
    print("### ① IC(h5) — 시드 평균, 괄호 NW t\n")
    ws = list(WINDOWS)
    print("| 군 | " + " | ".join(ws) + " |\n|---|" + "---|" * len(ws))
    for k, v in res["ic"].items():
        print(f"| {k} | " + " | ".join(f"{v[w]['ic']:+.3f} ({v[w]['nw']:+.1f})" if w in v else "—" for w in ws) + " |")
    print("\n### ① 상위 N(기본 R10·3N) — 연 · SPY 대비 IR · MDD · 회전 · 선택(대 U-clean, 비용 후) 연/IR\n")
    print("| 군 · 명단 · N | 창 | 연 | IR | MDD | 회전 | 선택 연 | 선택 IR | 시드 IR |\n|---|---|---|---|---|---|---|---|---|")
    for k, v in res["topn"].items():
        for w, m in v.items():
            print(f"| {k} | {w} | {_f(m['ann'])} | {_f(m['ir'], 's')} | {_f(m['mdd'])} | {m['turn']:.1f} | {_f(m['sel_ann'])} | "
                  f"{_f(m['sel_ir'], 's')} | {min(m['seed_ir']):+.2f}~{max(m['seed_ir']):+.2f} |")
    print("\n### ② 기준선 대 ETF — 연환산(그리고 U-clean 과의 추적 상관 · 추적오차)\n")
    for w, row in res["baseline"].items():
        etf = " · ".join(f"{e} {_f(row[e])} (ρ {row[e + '_corr']:.2f}, TE {row[e + '_te']:.1%})" for e in STYLE_ETFS)
        print(f"- {w}: U-clean {_f(row['U-clean'])} · U-panel {_f(row['U-panel'])} · {etf}")
    for source, out in res["src"].items():
        print(f"\n## 원천 {source}\n\n### ② 분해 — 롱(L-clean) − SPY = 선택 + 스타일, 연\n")
        print("| N | 창 | 롱−SPY | 선택 후 (IR) | 선택 전 (IR) | 스타일 (IR) | 스타일 β | 참고 U-panel 선택/스타일 |\n|---|---|---|---|---|---|---|---|")
        for n, dec in out["decomp"].items():
            for w, d in dec.items():
                ref = f"{_f(d.get('panel_sel'))} / {_f(d.get('panel_style'))}" if "panel_sel" in d else "—"
                print(f"| {n} | {w} | {_f(d['long_minus_spy'])} | {_f(d['sel_net'])} ({_f(d['sel_net_ir'], 's')}) | "
                      f"{_f(d['sel_gross'])} ({_f(d['sel_gross_ir'], 's')}) | {_f(d['style'])} ({_f(d['style_ir'], 's')}) | {_f(d['style_beta'])} | {ref} |")
        print("\n### ③ 헤지 — 슬리브 IR(창별) · 공통 창 연·MDD·추적ρ·잔여β(SPY/U)·스타일 잔여·h·헤지비용 · 시드 IR(N24)\n")
        print("| N · 헤지 | " + " | ".join(w for w in WINDOWS) + " | 연 | MDD | ρ | β SPY / U | 스타일 잔여 | Σh | 헤지 비용 | carry2 / H10 | 시드 IR |")
        print("|---|" + "---|" * (len(WINDOWS) + 9))
        for n, hrow in out["hedge"].items():
            for key, wrow in hrow.items():
                c = wrow.get("공통", {})
                sens = out["sens"].get(n, {}).get(key, {}).get("공통", {})
                seeds = wrow.get("seed_ir") or []
                print(f"| {n} {key} | " + " | ".join(_f(wrow[w]["ir"], "s") if w in wrow and isinstance(wrow[w], dict) else "—" for w in WINDOWS)
                      + f" | {_f(c.get('ann'))} | {_f(c.get('mdd'))} | {c.get('track_corr', float('nan')):.2f} | "
                      f"{_f(c.get('beta_spy'), 's')} / {_f(c.get('beta_u'), 's')} | {_f(c.get('style_resid_ann'))} | {c.get('h_sum', float('nan')):.2f} | "
                      f"{_f(c.get('hedge_cost'))} | {_f(sens.get('carry2'), 's')} / {_f(sens.get('H10'), 's')} | "
                      + (f"{min(seeds):+.2f}~{max(seeds):+.2f}" if seeds else "—") + " |")
        print("\n### ③ 국면별(S&P 규칙, 공통 창) 슬리브 연 / IR\n")
        for n, reg in out["regime"].items():
            for key, r in reg.items():
                print(f"- {n} {key}: " + " · ".join(f"{g} {_f(v['ann'])}/{_f(v['ir'], 's')} (n {v['n']})" for g, v in r.items()))
        print("\n### ④ 비용 격자 — 공통 창 선택 알파(대 U-clean)\n")
        print("| 조합 | 전 연 (IR) | 후 연 (IR) | NW t | 롱 연 | 회전 | 비용 | 유지율 | IC | BR | TC | 이론 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k, cell in out["cost"].items():
            c = cell.get("공통")
            if not c:
                continue
            print(f"| {k} | {_f(c['sel_gross'])} ({_f(c['sel_gross_ir'], 's')}) | {_f(c['sel_net'])} ({_f(c['sel_net_ir'], 's')}) | {_f(c['sel_nw'], 's')} | "
                  f"{_f(c['long'])} | {c['turn']:.1f} | {_f(c['cost'])} | {c.get('retention', float('nan')):.2f} | {c['ic']:+.3f} | {c['BR']:.0f} | {c['TC']:.2f} | {c['theory']:.2f} |")
        print("\n### ④×③ 교차 — N 마다 공통 창 비용 후 슬리브 IR 상위 3(실행 가능) + 기본 · HU(표본 안 선택, 낙관적)\n")
        for n in NS:
            rows = {k: v.get("공통") for k, v in out["cross"].items() if k.startswith(f"N{n}-")}
            ex = sorted(((k, v) for k, v in rows.items() if v is not None and not k.endswith("|HU")), key=lambda kv: -kv[1])[:3]
            base = {k: v for k, v in rows.items() if k.startswith(f"N{n}-R{BASE_EVERY}-K{BASE_MULT}|")}
            print(f"- N{n}: 상위 " + " · ".join(f"{k} {v:+.2f}" for k, v in ex) + " | 기본 " + " · ".join(f"{k.split('|')[1]} {_f(v, 's')}" for k, v in base.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch-etf").set_defaults(func=fetch_etf)
    sub.add_parser("check").set_defaults(func=check)
    b = sub.add_parser("bake")
    b.add_argument("--arm", choices=ARMS, required=True)
    b.add_argument("--force", action="store_true", help="시간 관문 무시(스모크 전용)")
    b.set_defaults(func=bake)
    r = sub.add_parser("run")
    r.add_argument("--force", action="store_true", help="시간 관문 무시(스모크 전용)")
    r.set_defaults(func=run)
    sub.add_parser("shadow").set_defaults(func=shadow)
    sub.add_parser("report").set_defaults(func=report)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
