#!/usr/bin/env python
"""``risk`` 가 알파에 있어야 하나, 제약에 있어야 하나 — 측정만 한다.

    uv run python tools/measure_risk_placement.py --sessions 60
    uv run python tools/measure_risk_placement.py --entity KR:005930 KR:000660

## 왜 이 도구가 있나

2026-08-15 분해에서 삼성전자·SK하이닉스가 후보에 못 든 이유가 `risk` 로
좁혀졌다. `risk` 는 `low_volatility .45 · liquidity .35 · low_beta .20` 이라
**급등 대형주는 정의상 65% 의 축에서 벌점**을 받는다. 그런데 `risk` 자기
독스트링은 "어느 종목이 오를까가 아니라 어느 종목이 포트폴리오를 위험하게
만드나" 를 잰다고 적고 있다.

그런 점수를 다른 알파와 평균 내면 **저위험이 곧 고수익 신호로 둔갑한다.**
원래 자리는 알파(`combine.combined_scores` 의 분자)가 아니라 제약 쪽
아니냐는 것이 남은 질문이다.

## 이 도구가 하지 않는 것

**설계를 바꾸지 않는다.** 가중치를 창고에 쓰지도 않는다. `risk` 를 뺐을 때
순위가 어떻게 움직이는지 숫자로만 보여 준다 — 배치 변경은 사용자 결정이고,
그 결정에 필요한 것은 의견이 아니라 표다.

백테스트도 돌리지 않는다. 그건 순위가 실제로 달라진다는 것이 확인된 뒤에
할 일이고, 비용이 두 자릿수 시간 단위로 다르다.

## 읽는 법

`risk` 를 빼서 상위 N 이 거의 그대로면 — `risk` 는 범인이 아니다. 크게
갈리면 그때 "무엇으로 갈렸나" 를 봐야 한다. 상위권이 통째로 고변동 종목으로
바뀌면 `risk` 는 알파가 아니라 **제약으로서 일하고 있던 것**이고, 빼는 게
아니라 옮기는 게 답이다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.selector.combine import combined_scores  # noqa: E402
from quant_rl_trading.selector.weights import analyst_weights  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402

SIGNALS = "signals"
RISK = "risk"

#: 세션 시각. 신호 공표가 16:00 이므로 그 뒤여야 그날 신호가 보인다
#: (`backtest.loop.snapshot_moment` 과 같은 이유).
SESSION_HOUR = 16

#: 기본 관심 종목. 이 질문을 만든 두 종목이다.
DEFAULT_ENTITIES = ("KR:005930", "KR:000660")


def _moment(day, market: Market) -> datetime:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul" if market is Market.KR else "America/New_York")
    return datetime.combine(day, datetime.min.time(), tzinfo=zone).replace(
        hour=SESSION_HOUR
    )


def _rank_of(scores: pd.Series, entity_id: str) -> tuple[int | None, float | None]:
    """순위(1-based)와 점수. 점수가 없으면 (None, None) — 0 위로 치지 않는다."""
    if entity_id not in scores.index:
        return None, None
    rank = int(scores.index.get_loc(entity_id)) + 1
    return rank, float(scores.loc[entity_id])


def measure_session(
    store: Store, *, as_of: datetime, market: Market, entities: list[str], top_n: int
) -> dict | None:
    """한 세션의 두 세계 비교. 신호가 없으면 None — 0 으로 채우지 않는다."""
    weights = analyst_weights(store, as_of=as_of, market=str(market))
    if not weights:
        return None
    if float(weights.get(RISK, 0.0)) == 0.0:
        # risk 가 이미 0 이면 비교할 두 세계가 없다. 조용히 같은 값을 두 번
        # 내놓으면 "차이 없음" 으로 읽혀서 정반대 결론이 된다.
        return {"as_of": as_of, "skipped": "risk 가중치가 이미 0 이다"}

    signals = store.get(SIGNALS, as_of=as_of, lookback=5)
    if signals.empty:
        return None

    without = dict(weights)
    without[RISK] = 0.0

    with_risk = combined_scores(signals, weights)
    no_risk = combined_scores(signals, without)
    if with_risk.empty or no_risk.empty:
        return None

    top_before = list(with_risk.head(top_n).index)
    top_after = list(no_risk.head(top_n).index)
    overlap = len(set(top_before) & set(top_after))

    record: dict = {
        "as_of": as_of,
        "scored": len(with_risk),
        "top_overlap": overlap,
        "top_n": top_n,
        "entered": [e for e in top_after if e not in top_before][:5],
        "left": [e for e in top_before if e not in top_after][:5],
    }
    for entity in entities:
        before_rank, before_score = _rank_of(with_risk, entity)
        after_rank, after_score = _rank_of(no_risk, entity)
        record[entity] = {
            "rank_before": before_rank, "score_before": before_score,
            "rank_after": after_rank, "score_after": after_score,
        }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--sessions", type=int, default=20, help="최근 거래일 수")
    parser.add_argument("--top-n", type=int, default=24, help="후보 수 (기본: 실제 운용값)")
    parser.add_argument("--entity", nargs="+", default=list(DEFAULT_ENTITIES))
    parser.add_argument("--as-of", default=None, help="이 시점까지만 (ISO8601)")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(None)
    market = Market(args.market)
    now = (
        datetime.fromisoformat(args.as_of)
        if args.as_of
        else LiveClock().now()
    )

    # 세션을 넉넉히 뽑고 뒤에서 자른다 — 휴장일이 섞이면 개수가 모자란다.
    span = args.sessions * 2 + 20
    days = trading_days(
        market, (now - pd.Timedelta(days=span)).date(), now.date()
    )[-args.sessions :]
    if not days:
        print("거래일이 없다.", file=sys.stderr)
        return 1

    records: list[dict] = []
    for day in days:
        moment = _moment(day, market)
        if moment > now:
            continue
        record = measure_session(
            store, as_of=moment, market=market,
            entities=args.entity, top_n=args.top_n,
        )
        if record is None:
            print(f"{day}  신호 없음 — 건너뜀")
            continue
        if "skipped" in record:
            print(f"{day}  {record['skipped']}")
            continue
        records.append(record)
        line = (
            f"{day}  점수 {record['scored']:,}종목 · "
            f"상위{args.top_n} 유지 {record['top_overlap']}/{args.top_n}"
        )
        for entity in args.entity:
            item = record[entity]
            before = f"{item['rank_before']}위" if item["rank_before"] else "없음"
            after = f"{item['rank_after']}위" if item["rank_after"] else "없음"
            line += f" · {entity} {before}→{after}"
        print(line)

    if not records:
        print("\n비교할 세션이 없었다.", file=sys.stderr)
        return 1

    print(f"\n=== 요약 · {len(records)}세션 ===")
    mean_overlap = sum(r["top_overlap"] for r in records) / len(records)
    print(
        f"상위 {args.top_n} 유지 평균 {mean_overlap:.1f}/{args.top_n} "
        f"({mean_overlap / args.top_n:.0%})"
    )
    for entity in args.entity:
        befores = [r[entity]["rank_before"] for r in records if r[entity]["rank_before"]]
        afters = [r[entity]["rank_after"] for r in records if r[entity]["rank_after"]]
        if not befores or not afters:
            print(f"{entity}: 점수가 잡힌 세션이 없다")
            continue
        entered = sum(
            1 for r in records
            if r[entity]["rank_after"] and r[entity]["rank_after"] <= args.top_n
        )
        was_in = sum(
            1 for r in records
            if r[entity]["rank_before"] and r[entity]["rank_before"] <= args.top_n
        )
        print(
            f"{entity}: 중앙 순위 {sorted(befores)[len(befores) // 2]:,}위 → "
            f"{sorted(afters)[len(afters) // 2]:,}위 · "
            f"상위{args.top_n} 진입 {was_in} → {entered} 세션"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
