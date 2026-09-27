"""G10(대량보유 변동) **등록 전 점검** — 수익·IC 는 계산하지 않는다.

    .venv/bin/python tools/check_major_holders_sources.py [--as-of 2025-06-30,2025-12-31,2026-06-30]

세 가지만 본다.
1. **커버리지** — 세션마다 0 아닌 종목 비율, 연도별 원자료 행 수.
2. **성향(스타일) 대리 상관** — 크기(log 시총)·위험(β·변동성)·가치(B/M)·배당(배당 공시 건수)과의 순위상관.
   BA(9/27 기각)의 교훈이다: 공시 **개수**는 "무엇이 새로 알려졌나" 가 아니라 "어떤 회사인가" 를 말했고,
   그 스타일이 판정 창에서 뒤처져 랭커를 흐렸다. **|ρ| 가 크면 그 피처는 사건이 아니라 스타일이다.**
3. **기존 입력과의 겹침** — 랭커 기본 7피처(`RankerAnalyst.input_features`)·G4 내부자 피처와의 순위상관.

수익률·미래 수익과의 상관은 **일부러 계산하지 않는다**(사전등록 전). 그래서 이 도구에는 그 코드가 없다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.ranker import RankerAnalyst  # noqa: E402
from quant_rl_trading.analysts.ranker_sources import GROUPS, build  # noqa: E402
from quant_rl_trading.analysts.risk import RiskAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

GROUP = "G10"
DEFAULT_AS_OF = "2025-06-30,2025-12-31,2026-06-30"
#: 스타일 대리변수 창. β·변동성은 120세션, 배당 공시는 365달력일(BA 와 같은 창).
STYLE_PRICE_DAYS = 200
STYLE_DOC_DAYS = 365


def styles(analyst: RiskAnalyst, as_of: datetime) -> pd.DataFrame:
    """크기·위험·가치·배당 대리변수. 이 넷은 랭커가 이미 아는 것이고, 새 피처가
    그중 하나의 다른 이름이면 넣을 이유가 없다."""
    prices = analyst.price_panel(as_of, lookback=STYLE_PRICE_DAYS)
    if prices.empty:
        return pd.DataFrame()
    close = analyst.wide(prices, "close")
    returns = close.pct_change(fill_method=None).tail(120)
    market = returns.mean(axis=1)
    out = pd.DataFrame(index=close.columns)
    out["vol_120"] = returns.std()
    var = float(market.var())
    out["beta_120"] = returns.apply(lambda s: s.cov(market)) / var if var > 0 else np.nan

    caps = analyst.store.get("market_stats", as_of=as_of, lookback=30, market=str(analyst.market),
                             columns=["entity_id", "valid_from", "metric", "value"])
    if not caps.empty:
        caps = caps[caps["metric"] == "market_cap"].sort_values("valid_from")
        cap = caps.groupby("entity_id")["value"].last()
        out["log_cap"] = np.log(cap.reindex(out.index).where(lambda s: s > 0))
        equity = analyst.store.get("fundamentals", as_of=as_of, lookback=500, market=str(analyst.market),
                                   columns=["entity_id", "valid_from", "metric", "value"])
        if not equity.empty:
            equity = equity[equity["metric"] == "total_equity"].sort_values("valid_from")
            book = equity.groupby("entity_id")["value"].last()
            out["bm"] = book.reindex(out.index) / cap.reindex(out.index).where(lambda s: s > 0)

    docs = analyst.store.get("documents", as_of=as_of, lookback=STYLE_DOC_DAYS, market=str(analyst.market),
                             columns=["entity_id", "valid_from", "title"])
    if not docs.empty:
        div = docs[docs["title"].astype(str).str.contains("배당", regex=False)]
        out["div_docs_365"] = div.groupby("entity_id").size().reindex(out.index).fillna(0.0)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    args = parser.parse_args(argv)
    store = Store(args.root)
    pd.set_option("display.width", 220)
    columns = list(GROUPS[GROUP])

    # 0. 원자료 — 연도별 행 수와 보고자 구성 (커버리지의 바탕)
    raw = store.get(mh_table := "major_holders", as_of=LiveClock().now(), lookback=2200, market="KR",
                    columns=["entity_id", "valid_from", "reporter_class", "purpose", "ratio", "ratio_change"])
    print(f"== 원자료 {mh_table}: {len(raw):,}행 · {raw['entity_id'].nunique():,}종목")
    if not raw.empty:
        print(raw.groupby(raw["valid_from"].dt.year).size().to_string())
        print("보고자:", raw["reporter_class"].value_counts().to_dict())
        print("목적:", raw["purpose"].value_counts().to_dict())

    for day in args.as_of.split(","):
        as_of = datetime.fromisoformat(day).replace(tzinfo=UTC)
        analyst = RiskAnalyst(store, ReplayClock(as_of), market=Market("KR"))
        feats = build(GROUP, analyst, as_of)
        print(f"\n===== {day} · {len(feats):,}종목")
        if feats.empty:
            print("  빈 표 — 그 시점엔 자료가 없다(rank-gauss 가 결측을 중앙으로 보낸다)")
            continue

        # 1. 커버리지
        cover = pd.DataFrame({
            "nonzero_share": (feats.fillna(0.0) != 0).mean().round(4),
            "missing_share": feats.isna().mean().round(4),
            "p95": feats.quantile(0.95).round(3),
            "max": feats.max().round(3),
        })
        print("-- 커버리지\n" + cover.to_string())

        # 2. 스타일 대리 상관
        style = styles(analyst, as_of)
        if not style.empty:
            both = feats.join(style, how="inner")
            rho = both.corr(method="spearman").loc[columns, [c for c in style.columns if c in both]]
            print("-- 스타일 대리 순위상관\n" + rho.round(3).to_string())

        # 3. 기존 입력·G4 와의 겹침
        ranker = RankerAnalyst(store, ReplayClock(as_of), market=Market("KR"))
        base = ranker.input_features(as_of)
        g4 = build("G4", analyst, as_of)
        other = base.drop(columns=["is_us"], errors="ignore").join(g4, how="outer")
        if not other.empty:
            joined = feats.join(other, how="inner")
            cols = [c for c in other.columns if c in joined]
            if cols:
                rho = joined.corr(method="spearman").loc[columns, cols]
                print(f"-- 기존 입력·G4 순위상관 (겹치는 종목 {len(joined):,})\n" + rho.round(3).to_string())
            else:
                print("-- 기존 입력 없음(그 시점 signals 미적재)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
