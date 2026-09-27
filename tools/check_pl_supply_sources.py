"""G12·G13 등록 전 점검 — 커버리지와 **상관**만 본다. 수익·IC 는 계산하지 않는다.

    .venv/bin/python tools/check_pl_supply_sources.py --group G12,G13 --sessions 12

사전등록 전에 봐도 되는 것은 "이 피처가 이미 있는 것과 같은 말을 하는가" 뿐이다(self-improvement.md §1②).
보는 상관 넷:

1. **성향 대리** — log 시총 · `low_beta` · `book_to_market`·`earnings_yield`(가치) · `dividend`(배당 공시).
   BA(밸류업 공시 개수)가 스타일 노출을 넣어 기각된 뒤의 필수 점검이다.
2. **기존 6점수** — chart·event·flow·fundamental·regime·risk. 랭커가 이미 보는 것과 같으면 한계기여가 없다.
3. **G8**(잠정실적) — G12 와 겹칠 수 있다. 둘 다 "실적이 크게 바뀌었다" 를 말한다.
4. **event 의 `contract`·`distress` 피처** — G13 은 event 가 건수로 세는 그 공시를 금액으로 바꾼 것이다.

기준 자료는 확장 패널을 구울 때 남긴 조각(`data/_diag/features-*.pkl`·`scores-*.pkl`)을 그대로 읽는다 —
여기서 새로 계산하면 판정 루프와 다른 값을 비교하게 된다. 창고에서 읽는 것은 시가총액 하나뿐이다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.ranker_sources import GROUPS, build  # noqa: E402
from quant_rl_trading.analysts.risk import RiskAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

DIAG = Path("data/_diag")
#: 기준 자료 — (조각 이름, 쓸 열). 판정 루프가 쓰는 것과 같은 조각이다.
FEATURE_PARTS = {
    "features-event-KR": ("contract", "dividend", "distress"),
    "features-fundamental-KR": ("book_to_market", "earnings_yield"),
    "features-risk-KR": ("low_beta", "low_volatility"),
}
SCORE_PARTS = ("chart", "event", "flow_kr", "fundamental", "regime", "risk")
#: 판정 창 안. 6차 국장 판정 구간(2025-05~2026-06)을 덮는 월말 세션을 고른다.
DEFAULT_END = date(2026, 6, 30)


def _sessions(count: int, end: date) -> list[date]:
    """월말 근처 세션 `count` 개. 거래일 여부는 패널 쪽에서 걸러진다."""
    months = pd.period_range(end=pd.Period(end, freq="M"), periods=count, freq="M")
    return [m.end_time.date() for m in months]


#: 조각은 한 번만 읽는다 — 세션마다 읽으면 84만 행 × 세션 수를 다시 푼다(첫 판 6분).
_CACHE: dict[str, pd.DataFrame] = {}


def _part(name: str) -> pd.DataFrame:
    if name not in _CACHE:
        _CACHE[name] = pd.read_pickle(DIAG / f"{name}.pkl")
    return _CACHE[name]


def _panel_at(part: str, columns: tuple[str, ...], session: date) -> pd.DataFrame:
    frame = _part(part)
    frame = frame[frame["session"] == session]
    if frame.empty:
        return pd.DataFrame()
    return frame.set_index("entity_id")[list(columns)]


def _scores_at(session: date) -> pd.DataFrame:
    out = {}
    for name in SCORE_PARTS:
        frame = _part(f"scores-{name}-KR")
        frame = frame[frame["session"] == session]
        if not frame.empty:
            out[f"score_{name}"] = frame.set_index("entity_id")["score"]
    return pd.DataFrame(out)


def _log_cap(store: Store, as_of: datetime) -> pd.Series:
    caps = store.get("market_stats", as_of=as_of, lookback=20, market="KR",
                     columns=["entity_id", "valid_from", "metric", "value"])
    if caps.empty:
        return pd.Series(dtype=float)
    caps = caps[caps["metric"] == "market_cap"].sort_values("valid_from")
    last = caps.groupby("entity_id")["value"].last()
    return np.log(last.where(last > 0)).rename("log_cap")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--group", default="G12,G13")
    parser.add_argument("--sessions", type=int, default=12, help="월말 세션 개수")
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--dates", default="", help="쉼표로 준 날짜만 본다(월말 대신). 거래일을 직접 고를 때")
    args = parser.parse_args(argv)

    store = Store(args.root)
    groups = [g.strip() for g in args.group.split(",") if g.strip()]
    wanted = [c for g in groups for c in GROUPS[g]]
    # G8 은 비교 대상이다 — 상관을 보려고 같이 만든다(등록: G12 와 겹칠 수 있다).
    made = sorted(set(groups) | {"G8"})

    days = ([date.fromisoformat(d.strip()) for d in args.dates.split(",") if d.strip()]
            or _sessions(args.sessions, args.end))
    per_session: list[pd.DataFrame] = []
    for session in days:
        as_of = datetime.combine(session, datetime.min.time(), tzinfo=UTC)
        analyst = RiskAnalyst(store, ReplayClock(as_of), market=Market("KR"))
        parts = [build(g, analyst, as_of) for g in made]
        parts = [p for p in parts if not p.empty]
        if not parts:
            continue
        table = pd.concat(parts, axis=1)
        refs = [_scores_at(session), _log_cap(store, as_of + timedelta(hours=1))]
        refs += [_panel_at(part, cols, session) for part, cols in FEATURE_PARTS.items()]
        refs = [r for r in refs if not r.empty]
        if not refs:
            continue
        joined = table.join(pd.concat(refs, axis=1), how="inner")
        if len(joined) < 50:
            continue
        rows = [c for c in joined.columns if c in wanted]
        others = [c for c in joined.columns if c not in wanted]
        corr = joined.corr(method="spearman", min_periods=30).loc[rows, others + rows]
        # **세션마다 열이 다르다**(G12 는 결산 시즌에만 있다). DataFrame 을 그냥 더하면
        # 한 세션에 없는 열이 전부 NaN 이 된다 — 긴 형태로 모아 결측을 빼고 평균한다.
        per_session.append(corr.stack().rename("rho").reset_index().assign(session=session))
        print(f"{session}: {len(joined):,}종목 · 피처 {len(rows)}", flush=True)

    if not per_session:
        print("비교할 세션이 없다 — 표 또는 조각이 비어 있다", file=sys.stderr)
        return 1
    long = pd.concat(per_session, ignore_index=True)
    long.columns = ["feature", "other", "rho", "session"]
    mean = long.pivot_table(index="feature", columns="other", values="rho", aggfunc="mean")
    seen = long.pivot_table(index="feature", columns="other", values="rho", aggfunc="count")
    pd.set_option("display.width", 250)
    print(f"\n세션 {long['session'].nunique()}개 평균 스피어만 상관 (행 = G12·G13 피처)")
    print(mean.round(3).to_string())
    print("\n그 상관을 잰 세션 수 (피처가 없는 세션은 빠진다)")
    print(seen.to_string())
    outside = [c for c in mean.columns if c not in wanted]
    worst = mean[outside].abs().max(axis=1)
    print("\n기존 것과의 최대 |상관|:")
    print(worst.round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
