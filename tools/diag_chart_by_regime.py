"""chart Analyst 국면별 IC 진단 — docs/diag/chart-by-regime.md §1 대로 한 번 잰다.

    nice -n 10 .venv/bin/python tools/diag_chart_by_regime.py bake --market KR   # 원피처 빈 구간 굽기(무거움)
    nice -n 10 .venv/bin/python tools/diag_chart_by_regime.py run

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/chart-regime/` 에 둔다.

규칙은 베끼지 않는다: 원피처 굽기는 `diagnose_ic.cache_chart_features`(실제 Analyst), IC 는 `ic.daily_ic`,
t 는 `ic.newey_west_t`, 국면은 실전 순수 함수 `analysts.regime.classify`, 묶음 합성은 `analysts.base.combine`.
"""

from __future__ import annotations

import argparse
import glob
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
from quant_rl_trading.analysts.base import combine  # noqa: E402
from quant_rl_trading.analysts.chart import WEIGHTS  # noqa: E402
from quant_rl_trading.analysts.regime import CRISIS_FLOOR_KEY, LOOKBACK_DAYS, MARKET_PROXIES, classify  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.diagnose_ic import cache_chart_features  # noqa: E402

OUT = REPO_ROOT / "data" / "_diag" / "chart-regime"
FEATURES = list(WEIGHTS)
MOMENTUM = {k: v for k, v in WEIGHTS.items() if k != "reversal_5"}
#: 문서 §1 — 지수(가) 와 사후 달력(나).
REGIME_INDEX = {"KR": MARKET_PROXIES[0], "US": "US:IDX:SP500"}
PERIODS = [
    ("그 앞", date(2000, 1, 1), date(2021, 12, 31)),
    ("하락", date(2022, 1, 3), date(2022, 9, 30)),
    ("반등", date(2022, 10, 1), date(2023, 1, 31)),
    ("박스", date(2023, 2, 1), date(2024, 12, 31)),
    ("급등", date(2025, 1, 1), date(2100, 1, 1)),
]
#: 조건부 규칙 — 상승 추세 국면 → +1(모멘텀 부호), 그 밖 → −1(반전 부호), 제외 → 0. 문서 §1 지표 4.
SIGN = {
    "bull": 1, "bear": -1, "volatile": -1, "crisis": -1, "unknown": 0,
    "급등": 1, "박스": -1, "하락": -1, "반등": 0, "그 앞": 0,
}
NW_LAG = ic.HORIZON_DAYS - 1
KR_BAKE = (date(2021, 11, 10), date(2022, 3, 31))
US_BAKE = (date(2021, 11, 8), date(2025, 7, 1))


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.date


# -----------------------------------------------------------------------------
# 자료
# -----------------------------------------------------------------------------


def load_scores(market: str) -> pd.DataFrame:
    if market == "KR":
        frame = pd.read_pickle(REPO_ROOT / "data/_diag-long/scores-chart-KR.pkl")  # invariant-allow: data-access — 연구 캐시
    else:
        parts = []
        for order, d in enumerate(("data/ic-history-us-early", "data/ic-history-us")):
            for p in sorted(glob.glob(str(REPO_ROOT / d / "scores-chart-*.parquet"))):  # invariant-allow: data-access — IC 이력 캐시
                parts.append(pd.read_parquet(p).assign(_o=order))  # invariant-allow: data-access — IC 이력 캐시
        frame = pd.concat(parts, ignore_index=True)
        frame["session"] = _dates(frame["session"])
        frame = (frame.sort_values("_o").drop_duplicates(["entity_id", "session"], keep="last")
                 .drop(columns="_o"))
    frame["session"] = _dates(frame["session"])
    return frame[["entity_id", "session", "score"]]


def load_targets(market: str) -> pd.DataFrame:
    if market == "KR":
        frame = pd.read_pickle(REPO_ROOT / "data/_diag-long/targets-KR-h5.pkl")  # invariant-allow: data-access — 연구 캐시
    else:
        files = []
        for d in ("data/ic-history-us-early", "data/ic-history-us"):
            files += glob.glob(str(REPO_ROOT / d / "targets-*.parquet"))  # invariant-allow: data-access — IC 이력 캐시
        # 세션마다 가장 늦은 측정 시점 것 — 문서 §1. 늦은 파일부터 읽고 이미 덮인 세션은 버린다(파일 35개를 다 쌓으면 5천만 행이다).
        parts, covered = [], set()
        for p in sorted(files, key=lambda x: Path(x).stem.split("-", 1)[1], reverse=True):
            part = pd.read_parquet(p, columns=["entity_id", "session", "target"])  # invariant-allow: data-access — IC 이력 캐시
            part["session"] = _dates(part["session"])
            part = part[~part["session"].isin(covered)]
            covered |= set(part["session"].unique())
            parts.append(part)
        frame = pd.concat(parts, ignore_index=True)
    frame["session"] = _dates(frame["session"])
    return frame[["entity_id", "session", "target"]]


def load_features(market: str) -> pd.DataFrame:
    if market == "KR":
        paths = [REPO_ROOT / "data/_diag/chart-regime/bake-KR/features-chart-KR.pkl",
                 REPO_ROOT / "data/_diag/kr-long/features-chart-KR.pkl"]
    else:
        paths = [REPO_ROOT / "data/_diag/chart-regime/bake-US/features-chart-US.pkl",
                 REPO_ROOT / "data/_diag/features-chart-US.pkl"]
    parts = []
    for order, p in enumerate(paths):
        if p.exists():
            parts.append(pd.read_pickle(p).assign(_o=order))  # invariant-allow: data-access — 연구 캐시
    frame = pd.concat(parts, ignore_index=True)
    frame["session"] = _dates(frame["session"])
    frame = frame.sort_values("_o").drop_duplicates(["entity_id", "session"], keep="last").drop(columns="_o")
    return frame[["entity_id", "session", *FEATURES]]


def regime_states(store: Store, market: str, days: list[date], now: datetime) -> pd.Series:
    """결정 세션마다 V6 원 판정 — **t 전날 종가까지**(diag_bear_2022.exposure_scales 와 같은 지연)."""
    floor = float(store.config(CRISIS_FLOOR_KEY, as_of=now))
    index_id = REGIME_INDEX[market]
    frame = store.get("indices", as_of=now, entity=[index_id], market=market,
                      lookback=(now.date() - days[0]).days + LOOKBACK_DAYS + 40,
                      columns=["entity_id", "valid_from", "close", "revision"])
    closes = (frame.sort_values(["valid_from", "revision"]).assign(day=lambda f: f["valid_from"].dt.date)
              .groupby("day")["close"].last().astype(float).sort_index())
    print(f"  국면 지수 {index_id} {closes.index[0]}~{closes.index[-1]} · crisis 하한 {floor:+.0%}", flush=True)
    out = {}
    for day in days:
        hist = closes[(closes.index < day) & (closes.index > day - timedelta(days=LOOKBACK_DAYS))]
        out[day] = classify(hist, crisis_floor=floor)
    return pd.Series(out)


def period_of(day: date) -> str:
    for name, lo, hi in PERIODS:
        if lo <= day <= hi:
            return name
    return "그 앞"


# -----------------------------------------------------------------------------
# 채점
# -----------------------------------------------------------------------------


def daily(frame: pd.DataFrame, column: str) -> pd.Series:
    out = ic.daily_ic(frame[["session", column, "target"]].dropna(), score=column)
    out.index = _dates(pd.Series(out.index)).values
    return out


def runs(labels: pd.Series) -> dict[str, tuple[int, int]]:
    """국면별 (런 개수, 20세션 이상 런 개수)."""
    ids = (labels != labels.shift()).cumsum()
    lengths = labels.groupby(ids).agg(["first", "size"])
    return {k: (int((g["size"] > 0).sum()), int((g["size"] >= 20).sum()))
            for k, g in lengths.groupby("first")}


def row(series: pd.Series) -> dict[str, float]:
    s = series.dropna()
    return {"ic": float(s.mean()) if len(s) else float("nan"),
            "t": ic.newey_west_t(s, lag=NW_LAG), "n": int(len(s))}


def table(ics: pd.DataFrame, labels: pd.Series, columns: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {}
    lab = labels.reindex(ics.index)
    for name in list(dict.fromkeys(lab.dropna())) + ["전체"]:
        part = ics if name == "전체" else ics[lab == name]
        out[name] = {c: row(part[c]) for c in columns}
    return out


def conditional(ics: pd.DataFrame, labels: pd.Series) -> dict[str, float]:
    """규칙 고정 조건부(C1·C2) 와 오라클 상한. 같은 세션 집합(SIGN ≠ 0)에서 원 chart 와 비교."""
    sign = labels.reindex(ics.index).map(SIGN).fillna(0)
    keep = sign != 0
    sub, sg = ics[keep], sign[keep]
    c1 = sub["chart"] * sg
    c2 = sub["mom_bundle"].where(sg > 0, sub["rev_bundle"])
    lab = labels.reindex(sub.index)
    by = sub["chart"].groupby(lab).agg(["mean", "size"])
    oracle = float((by["mean"].abs() * by["size"]).sum() / by["size"].sum())
    return {"chart": row(sub["chart"]), "C1": row(c1), "C2": row(c2), "oracle": oracle, "n": int(keep.sum())}


def run(args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    store = Store(root=REPO_ROOT / "data")
    now = datetime.now(UTC)  # invariant-allow: wallclock — 오늘의 설정(crisis 하한)을 읽는 시각, 문서 §1
    path = OUT / "results.json"
    # 시장별로 따로 돌려도 앞 시장 결과를 지우지 않는다
    results: dict[str, object] = json.loads(path.read_text()) if path.exists() else {}
    for market in args.market:
        t0 = wall.monotonic()
        scores, targets = load_scores(market), load_targets(market)
        merged = scores.merge(targets, on=["entity_id", "session"], how="inner")
        ic_chart = daily(merged, "score").rename("chart")
        print(f"[{market}] 점수 {len(scores):,} · 타깃 {len(targets):,} · 결합 {len(merged):,} · "
              f"IC 세션 {len(ic_chart)} {ic_chart.index.min()}~{ic_chart.index.max()}", flush=True)
        del scores

        feats = load_features(market)
        fm = feats.merge(targets, on=["entity_id", "session"], how="inner")
        del feats, targets
        # 원피처는 순위 점수라 열마다 분산 > 0 — 패널 전체에 한 번 씌워도 세션별 combine 과 같은 가중이다
        fm["mom_bundle"] = combine(fm[list(MOMENTUM)], MOMENTUM)
        fm["rev_bundle"] = -fm["reversal_5"]
        ic_feat = pd.DataFrame({c: daily(fm, c) for c in [*FEATURES, "mom_bundle", "rev_bundle"]})
        fm["chart_rebuilt"] = combine(fm[FEATURES], WEIGHTS)
        ic_rebuilt = daily(fm, "chart_rebuilt")
        print(f"[{market}] 피처 결합 {len(fm):,} · 피처 IC 세션 {len(ic_feat)} "
              f"{ic_feat.index.min()}~{ic_feat.index.max()}", flush=True)
        del fm

        days = sorted(set(ic_chart.index) | set(ic_feat.index))
        v6 = regime_states(store, market, days, now)
        per = pd.Series({d: period_of(d) for d in days})

        ics = pd.concat([ic_chart, ic_feat], axis=1).sort_index()
        both = ics.dropna(subset=["chart", "mom_bundle"])
        check = pd.concat([ic_chart, ic_rebuilt.rename("re")], axis=1).dropna()
        res: dict[str, object] = {
            "span_chart": [str(ic_chart.index.min()), str(ic_chart.index.max())],
            "span_feat": [str(ic_feat.index.min()), str(ic_feat.index.max())],
            "rebuild_check": {"corr": float(check.corr().iloc[0, 1]), "mean_chart": float(check["chart"].mean()),
                              "mean_rebuilt": float(check["re"].mean()), "n": int(len(check))},
        }
        for lname, labels in (("V6", v6), ("3구간", per)):
            res[lname] = {
                "chart": table(ics[["chart"]], labels, ["chart"]),
                "features": table(ic_feat, labels, [*FEATURES, "mom_bundle", "rev_bundle"]),
                "runs": runs(labels.reindex(ic_chart.index).dropna()),
                "conditional": conditional(both, labels),
                "conditional_chart_only": conditional(
                    ics[["chart"]].assign(mom_bundle=0.0, rev_bundle=0.0), labels),
            }
        res["v6_share"] = v6.reindex(ic_chart.index).value_counts(normalize=True).round(3).to_dict()
        results[market] = res
        ics.assign(
            v6=v6.reindex(ics.index), period=per.reindex(ics.index)).to_pickle(OUT / f"daily-{market}.pkl")  # invariant-allow: data-access — 진단 원본
        print(f"[{market}] {wall.monotonic() - t0:.0f}s", flush=True)

    path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    report({m: results[m] for m in args.market})
    return 0


def fmt(cell: dict[str, float]) -> str:
    if not cell or not cell["n"]:
        return "—"
    return f"{cell['ic']:+.3f} (t {cell['t']:+.1f}, {cell['n']})"


def report(results: dict) -> None:
    for market, res in results.items():
        print(f"\n## {market} — chart 점수 {res['span_chart'][0]}~{res['span_chart'][1]} · "
              f"피처 {res['span_feat'][0]}~{res['span_feat'][1]}")
        rc = res["rebuild_check"]
        print(f"재합성 점검: 원피처로 다시 합친 chart 일별 IC 와 캐시 점수 IC 상관 {rc['corr']:.3f} "
              f"(평균 {rc['mean_chart']:+.4f} vs {rc['mean_rebuilt']:+.4f}, {rc['n']}세션)")
        print(f"V6 국면 분포: {res['v6_share']}")
        for lname in ("V6", "3구간"):
            part = res[lname]
            print(f"\n### {lname} — chart 합성 점수\n")
            print("| 국면 | IC h5 | NW t | 세션 | 비겹침 n/5 | 런 | 런≥20 |")
            print("|---|---|---|---|---|---|---|")
            for name, cols in part["chart"].items():
                c = cols["chart"]
                r = part["runs"].get(name, ("", ""))
                print(f"| {name} | {c['ic']:+.4f} | {c['t']:+.2f} | {c['n']} | {c['n'] // 5} | {r[0]} | {r[1]} |")
            print(f"\n### {lname} — 피처 분해 (IC, NW t, 세션)\n")
            cols = [*FEATURES, "mom_bundle", "rev_bundle"]
            print("| 국면 | " + " | ".join(cols) + " |")
            print("|---|" + "---|" * len(cols))
            for name, cells in part["features"].items():
                print(f"| {name} | " + " | ".join(fmt(cells[c]) for c in cols) + " |")
            for key, label in (("conditional_chart_only", "합성 점수 전 기간"), ("conditional", "피처 있는 기간")):
                c = part[key]
                print(f"\n조건부 [{label}, {c['n']}세션]: 원 chart {fmt(c['chart'])} → C1(부호 전환) {fmt(c['C1'])}"
                      + (f" · C2(묶음 전환) {fmt(c['C2'])}" if key == "conditional" else "")
                      + f" · 오라클 상한(과적합) {c['oracle']:+.4f}")


# -----------------------------------------------------------------------------
# 굽기 — 원피처 빈 구간
# -----------------------------------------------------------------------------


def bake(args: argparse.Namespace) -> int:
    from tools.backfill import build_store, load_env

    load_env()
    store = build_store(None)
    market = Market(args.market)
    lo, hi = KR_BAKE if args.market == "KR" else US_BAKE
    sessions = sorted(load_scores(args.market)["session"].unique())
    calendar = [d for d in sessions if lo <= d <= hi]
    if args.limit:
        calendar = calendar[: args.limit]
    out = OUT / (f"bake-{args.market}" if not args.limit else f"probe-{args.market}")
    out.mkdir(parents=True, exist_ok=True)
    print(f"굽기 {args.market} · {len(calendar)}세션 {calendar[0]}~{calendar[-1]} → {out}", flush=True)
    t0 = wall.monotonic()
    cache_chart_features(store, market=market, calendar=calendar, out=out, name="chart")
    print(f"{wall.monotonic() - t0:.0f}s ({(wall.monotonic() - t0) / len(calendar):.1f}s/세션)", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bake")
    b.add_argument("--market", required=True, choices=["KR", "US"])
    b.add_argument("--limit", type=int, default=0, help="앞에서 N 세션만(비용 실측용, probe- 폴더)")
    b.set_defaults(func=bake)
    r = sub.add_parser("run")
    r.add_argument("--market", nargs="+", default=["KR", "US"])
    r.set_defaults(func=run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
