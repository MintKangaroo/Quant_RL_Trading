"""진단(본 자료) — 시행 ④ DF(`trial_next_four.py df`) 가 왜 급등 국면에서 −1.8%p 를 잃었나, 방어 +1.4%p MDD 는 어디서 왔나.

    OMP_NUM_THREADS=2 .venv/bin/python tools/diag_df.py [--seeds 5] [--out logs/diag-df-2026-09-30.log]

**판정이 아니다.** 판정 창(2023-02~2026-06)은 DF 판정에 이미 썼다 — 이 도구는 그 창을 다시 보는 **탐색**이고,
여기서 문턱·섞는 비율을 골라 다시 판정하면 과적합이다. 개선안(DF2)은 안 본 자료에서만 판정한다(`docs/protocols/df2-2026-10.md`).

등록 도구의 판정 경로는 건드리지 않는다 — 점수 합성(`pct_frame`·`df_weights`·`weighted`)·HMM(`load_probs`·`attach_probs`)은
`trial_next_four` 의 함수를 그대로 부르고, 포트는 `kit.evaluate` 와 같은 두 함수(국장 `rkit.portfolio` · 미장 `kit.us_m1_wide` →
`ukit.book`)에서 **일별 수익**을 꺼낸다. 시드마다 `kit.evaluate_all` 의 연수익·MDD 와 맞는지 대조한다(어긋나면 멈춘다).

분해 — 날 t 의 차 d_t = r_DF − r_C1′ 을 그 날 보유를 정한 **재조정일 R** 의 상태로 가른다(포트는 10세션마다만 명단을 바꾼다):
  지속 = R 과 직전 재조정일 모두 발동 · 진입 = R 발동·직전 아님 · 여운 = R 미발동이나 (직전 재조정일, R) 사이 발동 ·
  잔류 = 그 창에 발동 없음(완충 3N 이 예전 차이를 들고 있다).
국면 연수익 차 = 252 · Σ d_t / (그 국면 일수) 이라 네 칸의 합이 판정 줄의 차와 같다.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import final_round_kit as kit  # noqa: E402
from tools import trial_next_four as nf  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools import trial_us_kit as ukit  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END  # noqa: E402

ANN = 252
CATS = ("지속", "진입", "여운", "잔류")


def daily_of(pred: pd.DataFrame, book: kit.MarketBook) -> tuple[pd.Series, list[date]]:
    """`kit.evaluate` 와 같은 포트 — (일별 수익, 재조정일). 재조정 일정은 두 함수의 `step % every` 와 같은 자리다."""
    every = kit.REBALANCE_EVERY
    if book.market == "KR":
        daily, _ = rkit.portfolio(pred, book.ret, book.trad, every=every)
        axis = sorted(pd.unique(pred["session"]))
    else:
        wide = kit.us_m1_wide(pred, book.fund)
        daily, _ = ukit.book(wide, book.ret, book.cost, n=kit.N, exit_mult=kit.EXIT_MULT, every=every)
        axis = list(wide.index)
    days = [d for d in axis if d in book.ret.index]
    return daily, days[::every]


def mdd_window(daily: pd.Series) -> tuple[float, date, date]:
    nav = (1.0 + daily).cumprod()
    dd = nav / nav.cummax() - 1.0
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    return float(dd.min()), peak, trough


def drawdown_between(daily: pd.Series, first: date, last: date) -> float:
    part = daily[(daily.index > first) & (daily.index <= last)]
    return float((1.0 + part).prod() - 1.0)


def episodes(fire: pd.Series) -> list[int]:
    """연속 발동 구간의 길이(세션)."""
    runs, n = [], 0
    for v in fire.to_numpy():
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return runs


def classify(days: pd.Index, rebal: list[date], fire: pd.Series) -> pd.Series:
    """날 → 네 칸(지속·진입·여운·잔류). fire 는 세션 축의 발동 여부(bool)."""
    axis = list(fire.index)
    pos = {d: i for i, d in enumerate(axis)}
    anchor_cat: dict[date, str] = {}
    prev = None
    for r in rebal:
        f_r = bool(fire.get(r, False))
        f_p = bool(fire.get(prev, False)) if prev is not None else False
        lo = pos[prev] if prev is not None and prev in pos else max(0, pos.get(r, 0) - kit.REBALANCE_EVERY)
        between = fire.iloc[lo:pos[r]].any() if r in pos else False
        anchor_cat[r] = ("지속" if f_p else "진입") if f_r else ("여운" if between else "잔류")
        prev = r
    anchors = pd.Series(sorted(anchor_cat), dtype=object)
    idx = np.searchsorted(np.array(anchors, dtype=object), np.array(list(days), dtype=object), side="right") - 1
    return pd.Series([anchor_cat[anchors.iloc[i]] if i >= 0 else "잔류" for i in idx], index=days)


def ema_dose(fire: pd.Series, blend: float = nf.DF_BLEND) -> pd.Series:
    """재조정일 점수 안 BF2 의 실효 몫 — EMA5(adjust, `kit.SPAN`) 가 섞는 발동 비율 × 섞는 비율. 발동 첫날엔 1/3 · 반만 들어간다."""
    return (fire.astype(float) * blend).ewm(span=kit.SPAN).mean()


def run(seeds: Sequence[int], root: str) -> list[str]:
    from quant_rl_trading.store import Store

    store = Store(root=Path(root))
    panel, sessions = nf.light_panel()
    tag = kit.control_tag(panel)
    books = kit.market_books(store, sessions, panel=panel)
    probs = nf.load_probs(store)
    out: list[str] = [f"입력: 패널 {len(panel):,}행 · 시드 {len(seeds)} · RSS {kit.rss_mb():.0f}MB"]
    rows: list[dict] = []
    mdd_rows: list[dict] = []
    fire_of: dict[str, pd.Series] = {}
    for s in seeds:
        c1 = kit.share_objects(pd.read_pickle(kit.control_path("C1", s, tag)))  # invariant-allow: data-access — 회차 대조군 캐시
        pcts = nf.pct_frame({"C1": c1, "BF2": nf.bf2_scores(nf.read_bf1(s, tag), c1)})
        attached = nf.attach_probs(pcts, probs)
        dfp = nf.weighted(pcts, nf.df_weights(attached.dropna(subset=["p0"])))
        c1p = nf.weighted(pcts, nf.constant_weights(pcts, C1=1.0))
        # 재현 대조는 첫 시드만(포트를 두 번 도는 비용) — 같은 함수·같은 입력 모양이라 한 시드로 충분하다.
        by_df, by_c1 = (kit.evaluate_all(dfp, books)[0], kit.evaluate_all(c1p, books)[0]) if s == seeds[0] else ({}, {})
        for market in nf.MARKETS:
            part = attached[attached["market"] == market].set_index("session").sort_index()
            fire = part["p0"] > nf.DF_THRESHOLD
            fire_of.setdefault(market, fire)
            book = books[market]
            r_df, rebal = daily_of(dfp[dfp["market"] == market][["entity_id", "session", "pred"]], book)
            r_c1, rebal_c = daily_of(c1p[c1p["market"] == market][["entity_id", "session", "pred"]], book)
            if rebal != rebal_c or not r_df.index.equals(r_c1.index):
                raise ValueError(f"{market} 시드 {s}: DF·C1′ 의 날 축이 다르다 — 분해가 뜻이 없다")
            for name, daily, ref in (("DF", r_df, by_df.get(market)), ("C1′", r_c1, by_c1.get(market))):
                if ref is None:
                    continue
                ann = float(daily.mean() * ANN)
                if abs(ann - ref["ann"]) > 1e-9 or abs(mdd_window(daily)[0] - ref["mdd"]) > 1e-9:
                    raise ValueError(f"{market} 시드 {s} {name}: 일별 재현이 kit.evaluate 와 다르다 "
                                     f"(연 {ann:.6f} 대 {ref['ann']:.6f})")
            d = r_df - r_c1
            cat = classify(d.index, rebal, fire)
            bench = book.bench.reindex(d.index).fillna(0.0)
            for regime, mask in (("box", d.index <= BOX_END), ("rally", d.index > BOX_END)):
                n = int(mask.sum())
                for c in CATS:
                    sel = mask & (cat == c).to_numpy()
                    up = sel & (bench > 0).to_numpy()
                    rows.append({"seed": s, "market": market, "regime": regime, "cat": c, "days": int(sel.sum()),
                                 "contrib": float(d[sel].sum() * ANN / n),
                                 "contrib_up": float(d[up].sum() * ANN / n),
                                 "contrib_down": float(d[sel & ~up].sum() * ANN / n)})
            m_c1, pk, tr = mdd_window(r_c1)
            m_df, pk2, tr2 = mdd_window(r_df)
            win = (d.index > pk) & (d.index <= tr)
            mdd_rows.append({"seed": s, "market": market, "c1_mdd": m_c1, "c1_peak": pk, "c1_trough": tr,
                             "df_mdd": m_df, "df_peak": pk2, "df_trough": tr2,
                             "df_in_c1_window": drawdown_between(r_df, pk, tr),
                             "fire_share_window": float(fire[(fire.index > pk) & (fire.index <= tr)].mean()),
                             "win_by_cat": {c: float(d[win & (cat == c).to_numpy()].sum()) for c in CATS}})
        del c1, pcts, attached, dfp, c1p
        out.append(f"시드 {s}: {'kit.evaluate 재현 일치 · ' if s == seeds[0] else ''}RSS {kit.rss_mb():.0f}MB")
    return out + report(pd.DataFrame(rows), mdd_rows, fire_of)


def report(rows: pd.DataFrame, mdd_rows: list[dict], fire_of: dict[str, pd.Series]) -> list[str]:
    out: list[str] = []
    first = min(min(f.index) for f in fire_of.values())
    out.append("\n## 1. 발동 모양(깜빡임) — 판정 세션")
    out.append("| 시장 | 발동 | 박스 창 발동 | 급등 창 발동 | 전환(켜짐+꺼짐) | 발동 구간 수 | 길이 중앙 | 1~2세션 구간 | 재조정일 발동 중 실효 BF2 몫(평균) |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for market, fire in fire_of.items():
        fire = fire[fire.index >= first]
        runs = episodes(fire)
        trans = int((fire.astype(int).diff().abs() > 0).sum())
        dose = ema_dose(fire)
        box = fire[[d <= BOX_END for d in fire.index]]
        rally = fire[[d > BOX_END for d in fire.index]]
        out.append(f"| {market} | {fire.mean():.0%} ({int(fire.sum())}/{len(fire)}) | {box.mean():.0%} | {rally.mean():.0%} | "
                   f"{trans} | {len(runs)} | {int(np.median(runs)) if runs else 0} | "
                   f"{sum(r <= 2 for r in runs)} ({sum(r <= 2 for r in runs) / max(1, len(runs)):.0%}) | "
                   f"{dose[fire].mean():.2f} (최대 {nf.DF_BLEND:.2f}) |")

    out.append("\n## 2. 국면 연수익 차(DF − C1′)의 출처 — 시드 평균 · 합동 = 두 시장 평균(판정과 같은 합산), %p")
    g = rows.groupby(["regime", "market", "cat"])[["contrib", "contrib_up", "contrib_down", "days"]].sum() / rows["seed"].nunique()
    for regime in ("box", "rally"):
        out.append(f"\n**{'박스(~2024-12)' if regime == 'box' else '급등(2025-01~)'}**")
        out.append("| 칸 | KR 일수 | KR 기여 | KR 상승일/하락일 | US 일수 | US 기여 | US 상승일/하락일 | 합동 기여 |")
        out.append("|---|---|---|---|---|---|---|---|")
        tot = {"KR": 0.0, "US": 0.0}
        for c in CATS:
            cells = []
            pooled = 0.0
            for market in nf.MARKETS:
                v = g.loc[(regime, market, c)] if (regime, market, c) in g.index else None
                if v is None:
                    cells += ["0", "+0.00", "—"]
                    continue
                cells += [f"{v['days']:.0f}", f"{v['contrib'] * 100:+.2f}",
                          f"{v['contrib_up'] * 100:+.2f}/{v['contrib_down'] * 100:+.2f}"]
                pooled += v["contrib"] / 2
                tot[market] += v["contrib"]
            out.append(f"| {c} | " + " | ".join(cells) + f" | {pooled * 100:+.2f} |")
        out.append(f"| **합** | | {tot['KR'] * 100:+.2f} | | | {tot['US'] * 100:+.2f} | | **{(tot['KR'] + tot['US']) / 2 * 100:+.2f}** |")

    out.append("\n## 3. 시드별 급등 국면 차(%p) — 한 시드가 끌었나")
    sd = rows[rows["regime"] == "rally"].groupby(["seed", "market"])["contrib"].sum().unstack() * 100
    out.append("| 시드 | KR | US | 합동 |")
    out.append("|---|---|---|---|")
    for s, r in sd.iterrows():
        out.append(f"| {s} | {r.get('KR', 0):+.2f} | {r.get('US', 0):+.2f} | {(r.get('KR', 0) + r.get('US', 0)) / 2:+.2f} |")

    out.append("\n## 4. MDD — 어느 낙폭 구간인가(시드별, C1′ 의 최대 낙폭 창)")
    out.append("| 시드 | 시장 | C1′ MDD | 창(고점→저점) | DF 같은 창 | DF 자기 MDD(창) | 창 안 발동 | 창 안 차: 지속/진입/여운/잔류 (누적 %p) |")
    out.append("|---|---|---|---|---|---|---|---|")
    for m in mdd_rows:
        w = m["win_by_cat"]
        out.append(f"| {m['seed']} | {m['market']} | {m['c1_mdd']:.1%} | {m['c1_peak']}→{m['c1_trough']} | "
                   f"{m['df_in_c1_window']:.1%} | {m['df_mdd']:.1%} ({m['df_peak']}→{m['df_trough']}) | {m['fire_share_window']:.0%} | "
                   + "/".join(f"{w[c] * 100:+.1f}" for c in CATS) + " |")
    mr = pd.DataFrame(mdd_rows)
    for market, part in mr.groupby("market"):
        out.append(f"- {market}: MDD 차(DF − C1′) 시드 평균 {(part['df_mdd'] - part['c1_mdd']).mean() * 100:+.2f}%p · "
                   f"C1′ 저점 최빈 {part['c1_trough'].mode().iloc[0]} · DF 저점 최빈 {part['df_trough'].mode().iloc[0]}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=len(kit.SEEDS))
    parser.add_argument("--root", default="data")
    parser.add_argument("--out", default="logs/diag-df-2026-09-30.log")
    args = parser.parse_args(argv)
    lines = ["# 진단(본 자료) — ④ DF 판정 창 분해. 판정 아님 · 문턱·비율을 여기서 고르지 않는다", *run(kit.SEEDS[: args.seeds], args.root),
             f"\n최대 RSS {kit.rss_mb():.0f}MB"]
    text = "\n".join(lines)
    print(text, flush=True)
    Path(args.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
