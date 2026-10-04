"""섹터 순환(업종 모멘텀) 진단 — docs/diag/sector-rotation.md §1 대로 한 번 잰다.

    nice -n 10 .venv/bin/python tools/diag_sector_rotation.py fetch-us   # SPDR 11 → 연구 캐시 (LS g3204, sujung=Y)
    nice -n 10 .venv/bin/python tools/diag_sector_rotation.py build-kr   # KSIC 섹터 일수익(시총·동일) → 연구 캐시
    nice -n 10 .venv/bin/python tools/diag_sector_rotation.py run

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/sector-rotation/` 에 둔다.

규칙은 베끼지 않는다: 섹터 접기는 `selector.ksic.roll_up`, 시세는 `store.prices.read_prices(adjusted=True)`,
t 는 `ic.newey_west_t`, 국면은 `tools.diag_chart_by_regime` 의 `regime_states`·`period_of`·`runs`(실전 `analysts.regime.classify`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time as wall
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.selector.ksic import roll_up  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.diag_chart_by_regime import period_of, regime_states, runs  # noqa: E402

OUT = REPO_ROOT / "data" / "_diag" / "sector-rotation"
START = date(2021, 8, 11)
#: 금고(research.holdout.start = 2026-07-01) 앞날. 전방 창 끝이 이 날을 넘지 않는다.
END = date(2026, 6, 30)
MONTH = 21
SIGNALS = {"R1": (MONTH, 0), "R3": (3 * MONTH, 0), "R6": (6 * MONTH, 0), "R12": (12 * MONTH, 0), "R12-1": (12 * MONTH, MONTH)}
HORIZONS = (10, 21, 63)
MIN_MEMBERS = 10
MAX_ABS_RETURN = 0.35
CALENDAR_REGIMES = ("하락", "박스", "급등")

KRX16 = ("건설", "경기소비재", "기계장비", "반도체", "방송통신", "보험", "에너지화학", "운송", "유틸리티", "은행",
         "자동차", "정보기술", "증권", "철강", "필수소비재", "헬스케어")
KRX300 = ("금융", "산업재", "소재", "자유소비재", "정보기술", "커뮤니케이션서비스", "필수소비재", "헬스케어")
SPDR = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
UNIVERSES = ("KR-KSIC-cap", "KR-KSIC-ew", "KR-KRX16", "KR-KRX300", "US-SPDR")


def _now() -> datetime:
    return datetime.now(UTC)  # invariant-allow: wallclock — 진단 읽기 시각(as_of), 문서 §0


def _day(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.tz_convert("Asia/Seoul").dt.date


# -----------------------------------------------------------------------------
# 자료
# -----------------------------------------------------------------------------


def fetch_us(args: argparse.Namespace) -> int:
    """SPDR 11 일봉(수정주가 sujung=Y — 2025-12-05 2:1 분할). 연구 캐시에만 적는다."""
    from quant_rl_trading.collectors import ls_us_source as ls
    from quant_rl_trading.settings import load_env

    load_env()
    src = ls.LsUsSource.from_env()
    rows = []
    for symbol in SPDR:
        exchange = src.resolve_exchange(symbol)
        if exchange is None:
            print(f"{symbol}: 거래소 못 찾음", flush=True)
            return 1
        got: dict[str, float] = {}
        cursor = END
        while cursor >= date(2020, 1, 1):
            payload = src.client.request_tr(ls.PATH_CHART, ls.TR_CHART, {f"{ls.TR_CHART}InBlock": {
                "sujung": "Y", "delaygb": "R", "comp_yn": "N", "keysymbol": f"{exchange}{symbol}", "exchcd": exchange,
                "symbol": symbol, "gubun": "2", "qrycnt": ls.MAX_ROWS_PER_CALL, "sdate": "20200101",
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
        rows += [{"day": datetime.strptime(k, "%Y%m%d").date(), "sector": symbol, "close": v} for k, v in got.items()]
        print(f"{symbol}({exchange}) {len(got)}행 {min(got)}~{max(got)}", flush=True)
    frame = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT / "us-spdr.parquet")  # invariant-allow: data-access — 진단 원본(연구 캐시)
    levels = frame.pivot(index="day", columns="sector", values="close").sort_index()
    jumps = levels.pct_change().abs().stack()
    print(f"|일수익| > 15% 칸: {int((jumps > 0.15).sum())}", flush=True)
    return 0


def _sector_codes(store: Store, now: datetime) -> pd.Series:
    f = store.get("sectors", as_of=now, market="KR")
    f = f[f["source"] == "dart_company"].sort_values(["valid_from", "revision"])
    codes = f.groupby("entity_id")["sector"].last()
    return codes.map(roll_up).dropna()


def build_kr(args: argparse.Namespace) -> int:
    """KSIC 군집 일수익 — 전일 시총 가중·동일가중. 해 단위 창(겹침 10일)으로 읽어 창 안 수익만 쓴다."""
    store = Store(root=REPO_ROOT / "data")
    now = _now()
    sectors = _sector_codes(store, now)
    print(f"KSIC 군집 {sectors.nunique()} · 분류 종목 {len(sectors):,}", flush=True)
    rets, caps = [], []
    for year in range(START.year, END.year + 1):
        lo = max(START, date(year, 1, 1)) - timedelta(days=10)
        hi = min(date(year, 12, 31), END)
        until = datetime.combine(hi, datetime.max.time(), tzinfo=UTC)
        # lookback 은 as_of 기준 일수다(reader._valid_from_floor) — until 이 아니라
        span = (now.date() - lo).days + 1
        p = read_prices(store, as_of=now, until=until, lookback=span, market="KR",
                        columns=["entity_id", "valid_from", "close"], adjusted=True)
        p["day"] = _day(p["valid_from"])
        px = p.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
        r = px.pct_change(fill_method=None).iloc[1:]
        rets.append(r[r.index > (rets[-1].index.max() if rets else date.min)])
        m = store.get("market_stats", as_of=now, until=until, lookback=span + 10, market="KR",
                      columns=["entity_id", "valid_from", "metric", "value"])
        m = m[m["metric"] == "market_cap"]
        m["day"] = _day(m["valid_from"])
        caps.append(m.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last"))
        print(f"  {lo}~{hi}: 시세 {len(p):,}행 · 시총 {len(m):,}행", flush=True)
        del p, px, m
    ret = pd.concat(rets).sort_index()
    ret = ret[~ret.index.duplicated(keep="first")]
    cap = pd.concat(caps).sort_index()
    cap = cap[~cap.index.duplicated(keep="last")].reindex(columns=ret.columns)
    bad = ret.abs() > MAX_ABS_RETURN
    print(f"|일수익| > {MAX_ABS_RETURN:.0%} 버림: {int(bad.sum().sum())}칸", flush=True)
    ret = ret.mask(bad)
    # 전일 시총 — 같은 달력에서 한 세션 뒤로
    prev_cap = cap.reindex(ret.index.union(cap.index)).sort_index().ffill(limit=3).shift(1).reindex(ret.index)
    cols = [c for c in ret.columns if c in sectors.index]
    covered = ret.notna().sum(axis=1)
    print(f"분류된 종목 비율(수익 있는 칸 기준) {ret[cols].notna().sum().sum() / ret.notna().sum().sum():.1%}", flush=True)
    sec = sectors.reindex(cols)
    out_cap, out_ew, out_n = {}, {}, {}
    for name, members in sec.groupby(sec).groups.items():
        r = ret[list(members)]
        w = prev_cap[list(members)].where(r.notna())
        out_ew[name] = r.mean(axis=1)
        out_cap[name] = (r * w).sum(axis=1, min_count=1) / w.sum(axis=1, min_count=1)
        out_n[name] = r.notna().sum(axis=1)
    frame = pd.concat({"cap": pd.DataFrame(out_cap), "ew": pd.DataFrame(out_ew), "n": pd.DataFrame(out_n)}, axis=1)
    frame["_total", "_n"] = covered
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(OUT / "kr-ksic-daily.pkl")  # invariant-allow: data-access — 진단 원본
    print(f"KSIC 일수익 {frame.index.min()}~{frame.index.max()} {len(frame)}세션", flush=True)
    return 0


def levels_for(store: Store, now: datetime) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """묶음마다 (지수 수준, 적격 마스크). 날짜 축은 그 묶음의 세션."""
    out: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    k = pd.read_pickle(OUT / "kr-ksic-daily.pkl")  # invariant-allow: data-access — 진단 원본
    n = k["n"]
    ok = n >= MIN_MEMBERS
    for kind in ("cap", "ew"):
        r = k[kind].where(ok)
        out[f"KR-KSIC-{kind}"] = ((1 + r.fillna(0.0)).cumprod(), ok)
    ids = {f"KR:IDX:KRX {s}": s for s in KRX16} | {f"KR:IDX:KRX 300 {s}": f"300 {s}" for s in KRX300}
    f = store.get("indices", as_of=now, entity=list(ids), market="KR", lookback=(now.date() - date(2020, 1, 1)).days,
                  columns=["entity_id", "valid_from", "close", "revision"])
    f["day"] = _day(f["valid_from"])
    f = f[f["day"] <= END]
    px = (f.sort_values(["valid_from", "revision"]).pivot_table(index="day", columns="entity_id", values="close",
                                                                  aggfunc="last").rename(columns=ids).sort_index())
    px = px[(px > 0).all(axis=1)]
    for name, cols in (("KR-KRX16", list(KRX16)), ("KR-KRX300", [f"300 {s}" for s in KRX300])):
        lv = px[cols]
        out[name] = (lv, lv.notna())
    u = pd.read_parquet(OUT / "us-spdr.parquet")  # invariant-allow: data-access — 진단 원본(LS g3204 연구 캐시)
    u["day"] = pd.to_datetime(u["day"]).dt.date
    lv = u.pivot(index="day", columns="sector", values="close").sort_index()
    lv = lv[lv.index <= END]
    out["US-SPDR"] = (lv, lv.notna())
    return out


# -----------------------------------------------------------------------------
# 채점
# -----------------------------------------------------------------------------


def panel(levels: pd.DataFrame, ok: pd.DataFrame, look: int, skip: int, h: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(신호, 전방 수익). 전방 = t+1 → t+1+h. 결정일은 START 이후·전방 끝 ≤ END."""
    lv = levels.where(levels > 0)
    sig = lv.shift(skip) / lv.shift(look) - 1
    # 신호 창 안에 부적격 날이 있으면 그 섹터의 수준이 이어지지 않는다
    valid = ok.astype(float).rolling(look + 1, min_periods=look + 1).min().eq(1)
    sig = sig.where(valid & ok)
    fwd = lv.shift(-(1 + h)) / lv.shift(-1) - 1
    fwd_ok = ok.astype(float)[::-1].rolling(h + 2, min_periods=h + 2).min()[::-1].eq(1)
    fwd = fwd.where(fwd_ok)
    idx = pd.Series(levels.index, index=levels.index)
    end_day = idx.shift(-(1 + h))
    keep = (idx >= START) & end_day.notna() & (end_day <= END)
    return sig[keep], fwd[keep]


def cross_section(sig: pd.DataFrame, fwd: pd.DataFrame) -> pd.DataFrame:
    """세션마다 순위 IC · 상위−하위 · 상위−전체 · 상위 k 이름."""
    rows = {}
    for day in sig.index:
        s, f = sig.loc[day], fwd.loc[day]
        both = s.notna() & f.notna()
        if both.sum() < 3:
            continue
        s, f = s[both], f[both]
        n = len(s)
        k = max(2, n // 4)
        order = s.sort_values()
        top, bottom = order.index[-k:], order.index[:k]
        rows[day] = {"ic": float(s.rank().corr(f.rank())), "spread": float(f[top].mean() - f[bottom].mean()),
                     "top_all": float(f[top].mean() - f.mean()), "n": n, "top": list(top)}
    return pd.DataFrame.from_dict(rows, orient="index")


def stat(series: pd.Series, h: int) -> dict[str, float]:
    s = series.dropna().astype(float)
    if len(s) < 5:
        return {"mean": float("nan"), "t": float("nan"), "n": int(len(s))}
    return {"mean": float(s.mean()), "t": float(ic.newey_west_t(s, lag=h - 1)), "n": int(len(s))}


def run(args: argparse.Namespace) -> int:
    store = Store(root=REPO_ROOT / "data")
    now = _now()
    t0 = wall.monotonic()
    univ = levels_for(store, now)
    regimes: dict[str, tuple[pd.Series, pd.Series]] = {}
    results: dict[str, object] = {"_spans": {}}
    for name, (lv, ok) in univ.items():
        market = name[:2]
        if market not in regimes:
            days = sorted(d for d in lv.index if d >= START)
            v6 = regime_states(store, market, days, now)
            regimes[market] = (v6, pd.Series({d: period_of(d) for d in days}))
        v6, per = regimes[market]
        results["_spans"][name] = [str(lv.index.min()), str(lv.index.max()), int(lv.shape[1])]
        cells: dict[str, object] = {}
        for sname, (look, skip) in SIGNALS.items():
            for h in HORIZONS:
                sig, fwd = panel(lv, ok, look, skip, h)
                xs = cross_section(sig, fwd)
                if xs.empty:
                    continue
                cell: dict[str, object] = {
                    "span": [str(xs.index.min()), str(xs.index.max())],
                    "sectors_mean": float(xs["n"].mean()),
                    "ic": stat(xs["ic"], h), "spread": stat(xs["spread"], h), "top_all": stat(xs["top_all"], h),
                    "independent": round(len(xs) / h, 1),
                }
                for lname, labels in (("V6", v6), ("3구간", per)):
                    lab = labels.reindex(xs.index)
                    cell[lname] = {g: {"ic": stat(xs["ic"][lab == g], h), "spread": stat(xs["spread"][lab == g], h)}
                                   for g in lab.dropna().unique()}
                if h == MONTH:
                    counts = pd.Series([t for tops in xs["top"] for t in tops]).value_counts() / len(xs)
                    cell["top_freq"] = counts.head(3).round(2).to_dict()
                cells[f"{sname}|h{h}"] = cell
        results[name] = cells
        print(f"[{name}] {lv.shape[1]}섹터 {lv.index.min()}~{lv.index.max()} · {wall.monotonic() - t0:.0f}s", flush=True)
    for market, (v6, _) in regimes.items():
        results[f"_v6_runs_{market}"] = runs(v6)
        results[f"_v6_share_{market}"] = v6.value_counts(normalize=True).round(3).to_dict()
    lv16 = univ["KR-KRX16"][0]
    d = lv16[["반도체", "정보기술"]].pct_change(fill_method=None).dropna()
    results["_semis_vs_it_corr"] = float(d.corr().iloc[0, 1])
    results["_verdict"] = verdict(results)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    report(results)
    return 0


def verdict(results: dict) -> dict[str, dict[str, object]]:
    """§1 판단 규칙 셋 — (신호, 지평)마다."""
    out = {}
    kr = [u for u in UNIVERSES if u.startswith("KR")]
    for key in results["US-SPDR"]:
        us = results["US-SPDR"][key]
        sign = np.sign(us["ic"]["mean"])
        kr_same = [u for u in kr if key in results[u] and np.sign(results[u][key]["ic"]["mean"]) == sign
                   and abs(results[u][key]["ic"]["t"]) >= 2]
        rule1 = abs(us["ic"]["t"]) >= 2 and len(kr_same) >= 3
        regimes_ok = {}
        for u in [*kr, "US-SPDR"]:
            c = results[u].get(key, {}).get("3구간", {})
            regimes_ok[u] = sum(1 for g in CALENDAR_REGIMES if g in c and c[g]["ic"]["n"] >= 5
                                and np.sign(c[g]["ic"]["mean"]) == sign)
        rule2 = all(regimes_ok[u] >= 2 for u in ["US-SPDR", *kr_same]) if rule1 else False
        rule3 = all(np.sign(results[u][key]["spread"]["mean"]) == sign for u in ["US-SPDR", *kr_same]) if rule1 else False
        out[key] = {"sign": int(sign), "us_t": round(us["ic"]["t"], 2), "kr_same_t2": kr_same,
                    "rule1": rule1, "rule2": rule2, "rule3": rule3, "go": bool(rule1 and rule2 and rule3)}
    return out


def _f(c: dict[str, float], pct: bool = False) -> str:
    if not c or not c.get("n") or c["mean"] != c["mean"]:
        return "—"
    v = f"{c['mean'] * 100:+.2f}%" if pct else f"{c['mean']:+.3f}"
    return f"{v} ({c['t']:+.1f})"


def report(results: dict) -> None:
    print("\n## 묶음 범위")
    for u, (a, b, n) in results["_spans"].items():
        print(f"- {u}: {a}~{b}, {n}섹터")
    print(f"- KRX 반도체 vs KRX 정보기술 일수익 상관 {results['_semis_vs_it_corr']:.2f}")
    for m in ("KR", "US"):
        print(f"- V6 분포 {m}: {results[f'_v6_share_{m}']} · 런(전체, ≥20) {results[f'_v6_runs_{m}']}")
    for u in UNIVERSES:
        print(f"\n### {u} — 전 구간 (평균 (NW t))\n")
        print("| 신호 | h | 결정 구간 | 섹터 | 독립 | IC | 상위−하위 | 상위−전체 | 하락 IC | 박스 IC | 급등 IC |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for key, c in results[u].items():
            s, h = key.split("|")
            reg = c["3구간"]
            print(f"| {s} | {h[1:]} | {c['span'][0]}~{c['span'][1]} | {c['sectors_mean']:.0f} | {c['independent']} | "
                  f"{_f(c['ic'])} | {_f(c['spread'], True)} | {_f(c['top_all'], True)} | "
                  + " | ".join(_f(reg.get(g, {}).get("ic", {})) for g in CALENDAR_REGIMES) + " |")
        print(f"\n{u} — V6 국면별 IC (평균 (t), n)\n")
        states = ("bull", "bear", "volatile", "crisis")
        print("| 신호 | h | " + " | ".join(states) + " |")
        print("|---|---|" + "---|" * len(states))
        for key, c in results[u].items():
            s, h = key.split("|")
            print(f"| {s} | {h[1:]} | " + " | ".join(
                (_f(c["V6"][g]["ic"]) + f", {c['V6'][g]['ic']['n']}") if g in c["V6"] else "—" for g in states) + " |")
        tops = {k.split("|")[0]: c["top_freq"] for k, c in results[u].items() if "top_freq" in c}
        print(f"\n{u} — 상위 k 빈도 상위 3 (h21 결정일 기준): " + "; ".join(
            f"{s}: " + ", ".join(f"{n} {v:.0%}" for n, v in t.items()) for s, t in tops.items()))
    print("\n### 판단 규칙 (§1)\n")
    print("| 신호|h | 부호 | US t | KR |t|≥2 같은 부호 | 규칙1 | 규칙2 | 규칙3 | 시행 가치 |")
    print("|---|---|---|---|---|---|---|---|")
    for key, v in results["_verdict"].items():
        print(f"| {key} | {v['sign']:+d} | {v['us_t']:+.2f} | {', '.join(v['kr_same_t2']) or '없음'} | "
              f"{'O' if v['rule1'] else 'X'} | {'O' if v['rule2'] else 'X'} | {'O' if v['rule3'] else 'X'} | "
              f"{'있음' if v['go'] else '없음'} |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-us").set_defaults(func=fetch_us)
    sub.add_parser("build-kr").set_defaults(func=build_kr)
    sub.add_parser("run").set_defaults(func=run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
