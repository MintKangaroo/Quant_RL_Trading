"""후보 종목 뉴스 수집.

    uv run python tools/collect_news.py --market KR --limit 30

**라이브 경로 전용이다.** 백필하지 않는다 — 이유는 ``news_source`` 모듈
docstring 에 있다. 무료 티어가 하루 100 요청이라 ``--limit`` 이 사실상
필수다.

후보 목록은 지금 Selector 가 없어서 **거래대금 상위**로 대신한다. M3 에서
Selector 가 붙으면 그 후보를 그대로 받는다 — 뉴스 필터가 봐야 하는 것은
살 뻔한 종목이지 전 종목이 아니다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.news_source import NewsCollector, NewsSource  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

UNIVERSE = "universe"

#: 후보를 고르는 창(달력일). 거래대금은 하루만 보면 이벤트성 급등에 흔들린다.
LOOKBACK_DAYS = 10


def top_by_turnover(
    store: Store, *, market: Market, as_of: datetime, limit: int
) -> dict[str, str]:
    """거래대금 상위 종목의 ``{entity_id: 종목명}``.

    검색어로 티커가 아니라 **종목명**을 쓴다. 국장에서 "005930" 으로는 기사가
    안 잡히고, 미장에서 "A" 같은 티커로 찾으면 관련 없는 기사가 쏟아진다.
    """
    prices = read_prices(
        store,
        as_of=as_of,
        lookback=LOOKBACK_DAYS,
        columns=["value"],
        market=str(market),
    )
    if prices.empty:
        return {}

    ranked = (
        prices.groupby("entity_id")["value"].mean().sort_values(ascending=False).head(limit)
    )

    universe = store.get(
        UNIVERSE, as_of=as_of, lookback=LOOKBACK_DAYS, columns=["name", "is_tradable"]
    )
    names: dict[str, str] = {}
    if not universe.empty:
        latest = universe.sort_values("valid_from").groupby("entity_id").tail(1)
        names = {
            str(row["entity_id"]): str(row["name"] or "")
            for row in latest.to_dict(orient="records")
            if row.get("is_tradable", True)
        }

    out: dict[str, str] = {}
    for entity_id in ranked.index:
        entity_id = str(entity_id)
        # 이름을 모르면 티커 부분을 쓴다. 미장은 티커가 곧 검색어로 쓸 만하다.
        out[entity_id] = names.get(entity_id) or entity_id.split(":", 1)[-1]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument(
        "--limit", type=int, default=30, help="조회할 종목 수 (무료 티어 하루 100)"
    )
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="검색어 목록만 출력")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    market = Market(args.market)
    as_of = clock.now().astimezone(UTC)

    source = NewsSource.from_env()
    if not source.usable():
        print("NEWS_API_KEY 가 없다.", file=sys.stderr)
        return 2

    entities = top_by_turnover(store, market=market, as_of=as_of, limit=args.limit)
    if not entities:
        print("후보가 없다. prices 백필을 먼저 확인할 것.", file=sys.stderr)
        return 1

    print(f"{market} 후보 {len(entities)}종목")
    if args.dry_run:
        for entity_id, query in entities.items():
            print(f"  {entity_id}  ← {query!r}")
        return 0

    collector = NewsCollector(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(root=store.root),
        market=market,
    )
    written = collector.collect(entities)
    print(f"documents 적재: {written}행")

    if collector.failures:
        print(f"조회 실패 {len(collector.failures)}종목 (앞 5건):")
        for entity_id, error in list(collector.failures.items())[:5]:
            print(f"  {entity_id}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
