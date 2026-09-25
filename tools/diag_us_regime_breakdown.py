"""진단(시행 아님, 예산 없음) — 미장 성과를 **미장 자체 국면**으로 나눠 본다.

    .venv/bin/python tools/diag_us_regime_breakdown.py

사용자 질문(2026-09-26): "미장도 상승장에선 빅테크가 주도하지만 횡보·하락장에선 아닐 수도 있잖아, 따로 체크해보자."
지금까지 미장 판정은 국장 달력(박스 ~2024-12 · 급등 2025-01~)으로 나눴다. 여기서는 S&P500 으로 세션마다 국면을 나눈다 —
① 규칙 `regime.classify`(실전과 같은 400일 창, 개장 전 = 전날까지) ② v2 HMM 상태(data/_diag/v2/hmm-US.pkl, 전날까지 거른 확률의 argmax).
비교 대상: SPY · 시총가중 지수 대용(X0) · 점수로 기울인 지수(X3) · 거래대금 상위 1,000 동일가중 · 상위 24 C10(AT M1 합성, 3시드 평균).
**결과를 보고 국면별 전략을 고르지 않는다** — 국면 전환 전략은 이 진단 뒤 따로 사전등록해야 한다(새 창·전방 자료).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.regime import LOOKBACK_DAYS, classify  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import ANN  # noqa: E402
from tools.trial_us_index_minus_losers import top_caps  # noqa: E402
from tools.trial_us_index_tilt import run as tilt_run  # noqa: E402
from tools.trial_us_kit import SPX, book, market, scores  # noqa: E402
from tools.trial_us_missing_fundamental import JUDGE_END, JUDGE_START, SEEDS  # noqa: E402


def spx_states(store: Store, days: list) -> tuple[pd.Series, pd.Series]:
    end = datetime.combine(days[-1], time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(days[-1] - days[0]).days + LOOKBACK_DAYS + 10, market="US",
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == SPX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    rule = {d: classify(closes[(closes.index > d - timedelta(days=LOOKBACK_DAYS)) & (closes.index < d)], crisis_floor=-0.03) for d in days}
    h = pd.read_pickle("data/_diag/v2/hmm-US.pkl")  # invariant-allow: data-access — v2 연구 캐시
    h.index = pd.to_datetime(pd.Series(h.index)).dt.date.values
    names = {0: "HMM0(나쁨)", 1: "HMM1(중간)", 2: "HMM2(좋음)"}
    hmm = {}
    for d in days:
        prev = h[h.index < d]
        hmm[d] = names[int(prev.iloc[-1]["state"])] if len(prev) else "없음"
    return pd.Series(rule), pd.Series(hmm)


def main() -> int:
    store = Store(root=Path("data"))
    ret, bench = market()
    cost = float(store.config("accounting.fee_us", as_of=datetime.now(UTC)))  # invariant-allow: wallclock — 설정 현행값
    now = datetime.combine(JUDGE_END, time(23), tzinfo=UTC)
    caps = top_caps(store, now, (JUDGE_END - JUDGE_START).days + 60)
    caps = caps[(caps.index >= JUDGE_START) & (caps.index <= JUDGE_END)]
    ranks = caps.rank(axis=1, ascending=False)
    series: dict[str, pd.Series] = {}
    x0, x3, top = [], [], []
    for s in SEEDS:
        sc = scores(s)
        x0.append(tilt_run("X0", caps, ranks, sc, ret, cost)[0])
        x3.append(tilt_run("X3", caps, ranks, sc, ret, cost)[0])
        top.append(book(sc, ret, cost, every=10)[0])
    series["지수 대용 X0"] = x0[0]
    series["기울인 지수 X3"] = pd.concat(x3, axis=1).mean(axis=1)
    series["상위24 C10"] = pd.concat(top, axis=1).mean(axis=1)
    universe = scores(SEEDS[0]).notna()
    eq = (ret.reindex(universe.index).where(universe)).mean(axis=1)
    series["거래대금1000 동일가중"] = eq
    days = sorted(set.intersection(*[set(v.dropna().index) for v in series.values()]))
    series["SPY"] = bench
    rule, hmm = spx_states(store, days)
    frame = pd.DataFrame({k: v.reindex(days) for k, v in series.items()})
    for label, st in (("규칙 국면(S&P500)", rule), ("HMM 상태(S&P500)", hmm)):
        print(f"\n### {label} — 연율 수익(세션 수)\n")
        cols = list(frame.columns)
        print("| 국면 | 세션 | " + " | ".join(cols) + " | 상위24−SPY | X3−X0 |")
        print("|---|---|" + "---|" * (len(cols) + 2))
        for g, part in frame.groupby(st.reindex(days).values):
            m = part.mean() * ANN
            print(f"| {g} | {len(part)} | " + " | ".join(f"{m[c]:+.1%}" for c in cols)
                  + f" | {m['상위24 C10'] - m['SPY']:+.1%}p | {m['기울인 지수 X3'] - m['지수 대용 X0']:+.1%}p |")
    # 국면별 "대형주 주도" 정도 — 시총가중(X0) − 동일가중
    print("\n시총가중 − 동일가중(대형주 주도 정도): " + " · ".join(
        f"{g} {(part['지수 대용 X0'] - part['거래대금1000 동일가중']).mean() * ANN:+.1%}p"
        for g, part in frame.groupby(rule.reindex(days).values)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
