"""롱 쪽 무료 재료 진단 셋 — 외국인 순매수 지속성 · 잔차 모멘텀 · 공급계약 드리프트 (2026-10-09, 탐색 · 시행 아님).

    nice -n 10 .venv/bin/python tools/diag_long_side_free.py foreign|resid|contract|all

문헌 조사(메모리 lit-review-2026-10-09)의 무료 대용 후보. 창고에 쓰지 않고 합격선이 없다. 금고 앞(2026-06-30)까지만 읽는다.
수익은 같은 시총 구간(1~200 · 201~700, `trial_p1a_prime` 구간 규칙) 동일가중 대비. 신호 진단은 10세션마다 표본을 떠
다음 20세션 수익의 10분위를 본다(겹침을 줄인다). 결과는 `docs/diag/long-side-events-2026-10.md` 에 옮긴다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools import trial_p1a_prime as p  # noqa: E402
from tools.trial_overlay import _prices  # noqa: E402

END = date(2026, 6, 30)
AS_OF = datetime(2026, 7, 1, tzinfo=UTC)
STEP, H = 10, 20
ZONES = {"A1": (1, 200), "A2": (201, 700)}
PERIODS = {"금지 전": (date(2021, 1, 1), date(2023, 11, 5)), "금지 중": (date(2023, 11, 6), date(2025, 3, 30)),
           "재개 후": (date(2025, 3, 31), END)}


class Ctx:
    def __init__(self, store: Store) -> None:
        p.ZONES = ZONES
        self.store = store
        self.caps = pd.read_parquet(p.CAPS)  # invariant-allow: data-access — 시행 작업 캐시
        self.caps = self.caps[self.caps["session"] <= END]
        sessions = sorted(self.caps["session"].unique())
        px = _prices(store, [sessions[0], END])
        self.px = px[(px.index >= sessions[0]) & (px.index <= END)]
        self.cal = list(self.px.index)
        trad = {d: set(g["entity_id"]) for d, g in self.caps.groupby("session")}
        self.mem = p.zone_members(self.caps, trad)
        self.capw = self.caps.pivot_table(index="session", columns="entity_id", values="cap", aggfunc="last")


def decile_report(ctx: Ctx, signal: pd.DataFrame, name: str) -> None:
    """signal: 날짜 × 종목(높을수록 '오를 것'). 10세션마다 구간 안 10분위 → 다음 20세션 수익 − 구간 EW."""
    for z in ZONES:
        rows = []
        for i in range(0, len(ctx.cal) - H, STEP):
            d, e = ctx.cal[i], ctx.cal[i + H]
            if d not in signal.index:
                continue
            names = [n for n in ctx.mem[z].get(d, ()) if n in ctx.px.columns]
            sc = signal.loc[d].reindex(names).dropna()
            if len(sc) < 50:
                continue
            r = (ctx.px.loc[e, sc.index] / ctx.px.loc[d, sc.index] - 1)
            ok = r.notna() & (r.abs() < 3)
            sc, r = sc[ok], r[ok]
            q = pd.qcut(sc.rank(method="first"), 10, labels=False)
            base = r.mean()
            rows.append({"d": d, **{f"D{k + 1}": float(r[q == k].mean() - base) for k in range(10)}})
        f = pd.DataFrame(rows).set_index("d")
        ann = 245 / H
        top, bot = f["D10"], f["D1"]
        t = top.mean() / top.std() * np.sqrt(len(top))
        print(f"{name} · {z} · 표본 {len(f)}일: D1 {bot.mean() * ann:+.1%} · D10 {top.mean() * ann:+.1%}(t {t:+.2f}) · "
              f"D9 {f['D9'].mean() * ann:+.1%} · 단조(D6~D10 평균) {f[['D6', 'D7', 'D8', 'D9', 'D10']].mean().mean() * ann:+.1%}", flush=True)
        print("    기간별 D10: " + " · ".join(
            f"{k} {top[(top.index >= a) & (top.index <= b)].mean() * ann:+.1%}" for k, (a, b) in PERIODS.items()), flush=True)


def foreign(ctx: Ctx) -> None:
    """외국인 순매수 지속성 — 직전 20·60세션 외인계 순매수 금액 합 / 시총(그날)."""
    look = (END - ctx.cal[0]).days + 120
    fl = ctx.store.get("flows", as_of=AS_OF, lookback=look, market="KR", columns=["entity_id", "valid_from", "investor", "net_value"])
    fl = fl[fl["investor"] == "외인계"]
    fl["day"] = pd.to_datetime(fl["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    net = fl.pivot_table(index="day", columns="entity_id", values="net_value", aggfunc="last").reindex(ctx.cal)
    print(f"외국인 순매수 {len(fl):,}행 · 종목 {net.shape[1]:,}", flush=True)
    for w in (20, 60):
        s = net.rolling(w, min_periods=int(w * 0.8)).sum() / ctx.capw.reindex(index=ctx.cal, columns=net.columns)
        decile_report(ctx, s, f"외국인 {w}세션(당일 종가 진입)")
        # 수급은 장 마감 뒤 공표된다 — 하루 늦춘 값(= 전날까지의 수급으로 오늘 종가 진입)이 실행 가능한 쪽이다.
        decile_report(ctx, s.shift(1), f"외국인 {w}세션(하루 늦춤)")


def resid(ctx: Ctx) -> None:
    """잔차 모멘텀 — 직전 252세션 중 최근 21세션을 뺀 구간에서 구간 EW 를 뺀 잔차 누적 / 잔차 변동성."""
    lr = np.log(ctx.px).diff()
    out = {}
    for i in range(260, len(ctx.cal) - H, STEP):     # STEP 배수 — decile_report 표본일과 맞춘다
        d = ctx.cal[i]
        win = lr.iloc[i - 252:i - 21]
        for z in ZONES:
            names = [n for n in ctx.mem[z].get(d, ()) if n in win.columns]
            sub = win[names].dropna(axis=1, thresh=int(len(win) * 0.8))
            m = sub.mean(axis=1)
            x = m - m.mean()
            beta = ((sub - sub.mean()).mul(x, axis=0)).sum() / (x ** 2).sum()
            e = sub - sub.mean() - np.outer(x, beta)
            out.setdefault(d, {}).update((e.sum() / (e.std() * np.sqrt(len(e)))).to_dict())
    sig = pd.DataFrame.from_dict(out, orient="index")
    decile_report(ctx, sig, "잔차 모멘텀 12-1")


def contract(ctx: Ctx) -> None:
    """'단일판매ㆍ공급계약체결' 공시 뒤 — 관측 뒤 첫 종가 진입, h5·h20·h60 대 같은 구간 EW. 정정 공시는 뺀다."""
    look = (END - ctx.cal[0]).days + 30
    d = ctx.store.get("documents", as_of=AS_OF, lookback=look, market="KR", columns=["entity_id", "observed_at", "title", "source"])
    t = d["title"].astype(str).str.replace(" ", "")
    d = d[(d["source"].astype(str) == "dart") & t.str.contains("단일판매ㆍ공급계약") & ~t.str.contains("정정")]
    d["obs"] = pd.to_datetime(d["observed_at"]).dt.tz_convert("Asia/Seoul")
    d = d[d["obs"].dt.date <= END].sort_values("obs")
    d["od"] = d["obs"].dt.date
    d = d.drop_duplicates(["entity_id", "od"])
    zone_of = {(day, e): z for z, m in ctx.mem.items() for day, names in m.items() for e in names}
    pos = {day: i for i, day in enumerate(ctx.cal)}
    rows = []
    for _, r in d.iterrows():
        e, ob = r["entity_id"], r["obs"]
        if e not in ctx.px.columns:
            continue
        nxt = [day for day in ctx.cal if day > ob.date() or (day == ob.date() and ob.time() < time(15, 30))]
        if not nxt:
            continue
        i0 = pos[nxt[0]]
        z = zone_of.get((ctx.cal[i0], e))
        if z is None:
            continue
        for h in (5, 20, 60):
            if i0 + h >= len(ctx.cal):
                continue
            a, b = ctx.cal[i0], ctx.cal[i0 + h]
            names = [n for n in ctx.mem[z].get(a, ()) if n in ctx.px.columns]
            rz = (ctx.px.loc[b, names] / ctx.px.loc[a, names] - 1)
            rz = rz[rz.abs() < 3].mean()
            ri = ctx.px.at[b, e] / ctx.px.at[a, e] - 1
            if np.isfinite(ri):
                rows.append({"e": e, "day": a, "zone": z, "h": h, "car": ri - rz})
    f = pd.DataFrame(rows)
    print(f"공급계약 사건 {len(d):,}(구간 안 진입 {f[f.h == 20]['e'].count():,})", flush=True)
    for h in (5, 20, 60):
        g = f[f.h == h]
        t = g["car"].mean() / (g.groupby("day")["car"].mean().std() / np.sqrt(g["day"].nunique()))
        by = " · ".join(f"{z} {v['car'].mean():+.2%}(n {len(v)})" for z, v in g.groupby("zone"))
        print(f"  h{h}: 평균 {g['car'].mean():+.2%} · 중앙 {g['car'].median():+.2%} · 승률 {(g['car'] > 0).mean():.0%} · t {t:+.2f} · {by}",
              flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("foreign", "resid", "contract", "all"))
    args = ap.parse_args(argv)
    ctx = Ctx(Store(root=Path("data")))
    todo = ("foreign", "resid", "contract") if args.what == "all" else (args.what,)
    for w in todo:
        print(f"\n=== {w} ===", flush=True)
        {"foreign": foreign, "resid": resid, "contract": contract}[w](ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
