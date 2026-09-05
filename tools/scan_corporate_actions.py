#!/usr/bin/env python
"""기업행위 조정계수 스캔 — LS 에서 계수를 받아 파일로 떨군다.

**창고에 쓰지 않는다.** 쓰기는 ``tools/backfill_adj_factor.py`` 가 이 결과를
받아 한다. 스캔이 몇 시간짜리라 중간에 죽어도 다시 안 돌리게, 그리고 무엇을
쓸지 사람이 먼저 볼 수 있게 둘로 갈랐다.

## 세 가지 모드

``--daily``         **매일 도는 것.** 최근 며칠 안에 기업행위 공시가 난 종목만
                    본다. 보통 0~3종목이라 30초면 끝난다.
``--from-filings``  창고 ``documents`` 의 기업행위 공시로 후보를 좁힌다.
                    5년 700종목 남짓, 3~4시간. **백필은 이걸 먼저 돌린다.**
``--all``           유니버스 전체. 목적은 발견이 아니라 **검증**이다 —
                    공시로 좁힌 목록이 무엇을 놓쳤는지 본다.

## 왜 공시로 좁히나 — 그리고 왜 그것만 믿지 않나

권리락 공시 접수일 **+1 거래일이 발효일**이라는 것을 4/4 로 맞췄다(실측
2026-08-15: KR:373170·406820·417840·458870). 그래서 공시는 후보를 좁히는 데
쓸 만하다.

그래도 전 종목 스캔을 한 번은 돌린다. 공시가 사건의 전부라는 보장이 없고,
돌려 보지 않으면 "놓친 게 없다" 를 영영 확인할 수 없다. **결과가 비어도 값이
있다** — 앞으로는 공시로 좁혀도 된다는 근거가 된다.

## 비용

국장은 콜당 3.1초(``MIN_INTERVAL_SEC_KR``)이고 한 콜이 500세션이라, 5년치
종목 하나에 원주가 3콜 + 수정주가 3콜 = 약 19초다.

    --daily         종목당 약 6초 (창이 1콜로 끝난다)
    후보 700종목    약 3.7시간
    전 종목 3,221   약 17시간   (``--years 2`` 로 줄이면 약 5.7시간)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.corporate_actions import (  # noqa: E402
    MIN_LOG_FACTOR_CONFIG,
    AdjustmentUnavailable,
    LSAdjustmentSource,
    detect_events,
)
from quant_rl_trading.collectors.errors import CollectorError  # noqa: E402
from quant_rl_trading.collectors.ls_client import (  # noqa: E402
    MIN_INTERVAL_SEC_KR,
    MIN_INTERVAL_SEC_US,
    LSClient,
    LSCredentials,
)
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

#: 기업행위 공시의 제목 조각. ``documents.doc_type`` 하나로는 못 고른다 —
#: 감자·주식병합이 ``distress``·``other`` 로 흩어져 있다 (실측).
FILING_NEEDLES = (
    "권리락",
    "주식분할",
    "액면분할",
    "감자",
    "무상증자",
    "주식병합",
    "주권변경상장",
)


def _entities_from_filings(store: Store, *, as_of: datetime, days: int) -> list[str]:
    """기업행위 공시가 한 번이라도 있었던 종목.

    ``days`` 는 **달력일**이다. 공시는 휴장일에도 접수되므로 거래일로 자르면
    그 공시를 영영 못 받는다 (``scripts/collect_daily.sh`` 의 DART 단계와 같은
    이유).
    """
    # ``documents`` 에는 ``market`` 컬럼이 없다. 시장은 entity_id 접두어로 가른다.
    docs = store.get(
        "documents",
        as_of=as_of,
        lookback=days,
        columns=["entity_id", "title"],
    )
    if docs.empty:
        return []
    compact = docs["title"].fillna("").str.replace(r"\s+", "", regex=True)
    hit = docs[compact.str.contains("|".join(FILING_NEEDLES), regex=True, na=False)]
    codes = hit["entity_id"].astype(str)
    return sorted(set(codes[codes.str.startswith("KR:")]))


def _entities_from_prices(
    store: Store, *, as_of: datetime, years: int, market: str
) -> list[str]:
    frame = read_prices(
        store,
        as_of=as_of,
        market=market,
        lookback=years * 366,
        columns=["entity_id"],
    )
    return sorted(set(frame["entity_id"].astype(str))) if not frame.empty else []


def _scan(
    source: LSAdjustmentSource,
    entities: Sequence[str],
    *,
    start: date,
    end: date,
    min_log_factor: float,
    out: Path,
    resume: dict[str, dict],
) -> dict[str, dict]:
    """종목마다 계수를 받아 사건을 뽑는다. 한 종목 끝날 때마다 파일에 남긴다.

    중간에 죽어도 이어서 돌 수 있어야 한다 — 3시간짜리 작업이 마지막에
    터지면 처음부터 다시 도는 것이 제일 비싼 실패다.
    """
    result = dict(resume)
    total = len(entities)
    for index, entity_id in enumerate(entities, start=1):
        if entity_id in result:
            continue
        symbol = entity_id.split(":")[-1]
        try:
            # 미장은 차트 TR 이 거래소 코드를 요구한다. 국장은 빈 문자열이다.
            exchange = source.exchange_of(symbol)
            ratios = source.ratios(symbol, start=start, end=end, exchange=exchange)
        except AdjustmentUnavailable as error:
            result[entity_id] = {"status": "no_rows", "detail": str(error)}
        except CollectorError as error:
            result[entity_id] = {"status": "error", "detail": str(error)[:200]}
        else:
            events = detect_events(
                ratios, entity_id=entity_id, min_log_factor=min_log_factor
            )
            result[entity_id] = {
                "status": "ok",
                "sessions": len(ratios),
                "events": [
                    {
                        "effective_on": action.effective_on.isoformat(),
                        "factor": action.factor,
                        "ratio_before": action.ratio_before,
                        "ratio_after": action.ratio_after,
                    }
                    for action in events
                ],
            }
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        found = result[entity_id]
        mark = (
            f"사건 {len(found['events'])}"
            if found["status"] == "ok"
            else found["status"]
        )
        print(f"[{index}/{total}] {entity_id} {mark}", flush=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--daily", action="store_true", help="최근 공시 종목만 (매일)")
    scope.add_argument("--from-filings", action="store_true", help="공시로 후보를 좁힌다")
    scope.add_argument("--all", action="store_true", help="유니버스 전체 (검증용)")
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument(
        "--filing-days",
        type=int,
        default=14,
        help="--daily 가 볼 공시 창(달력일). 권리락은 발효 하루 전에 뜬다",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N종목만 (시험용)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    as_of = clock.now()
    store = Store()
    market = Market.KR if args.market == "KR" else Market.US

    min_log_factor = float(store.config(MIN_LOG_FACTOR_CONFIG, as_of=as_of))

    # --daily 는 창도 좁힌다. 계수는 계단 하나만 보면 되고, 창이 길수록 콜이
    # 늘어난다 — 500세션이면 한 콜로 끝나 종목당 6초다.
    years = 2 if args.daily else args.years
    if args.daily:
        entities = _entities_from_filings(store, as_of=as_of, days=args.filing_days)
    elif args.from_filings:
        # 공시(DART)는 국장만 있다. 미장은 대응물이 없어 전 종목이 유일한 길이다.
        if market is Market.US:
            parser.error("--from-filings 는 국장 전용이다 (DART). 미장은 --all 을 써라")
        entities = _entities_from_filings(store, as_of=as_of, days=args.years * 366)
    else:
        entities = _entities_from_prices(
            store, as_of=as_of, years=args.years, market=args.market
        )
    if args.limit:
        entities = entities[: args.limit]

    credentials = LSCredentials.from_env(prefix="LS_" if market is Market.KR else "LS_US_")
    client = LSClient(
        credentials=credentials,
        clock=clock,
        live_trading=True,
        min_interval_sec=(
            MIN_INTERVAL_SEC_KR if market is Market.KR else MIN_INTERVAL_SEC_US
        ),
    )
    source = LSAdjustmentSource(client=client, market=market)

    end = as_of.date()
    start = end - timedelta(days=years * 366)
    resume: dict[str, dict] = {}
    # --daily 는 이어서 돌면 안 된다. 매일 같은 파일을 쓰는데 어제 결과가
    # 남아 있으면 오늘 새로 난 사건을 통째로 건너뛴다.
    if args.out.exists() and not args.daily:
        resume = json.loads(args.out.read_text(encoding="utf-8"))
        print(f"이어서 돈다 — 이미 끝난 {len(resume)}종목은 건너뛴다", flush=True)

    print(
        f"{args.market} {len(entities)}종목 · {start}~{end} · "
        f"min_log_factor={min_log_factor}",
        flush=True,
    )
    result = _scan(
        source,
        entities,
        start=start,
        end=end,
        min_log_factor=min_log_factor,
        out=args.out,
        resume=resume,
    )

    ok = [item for item in result.values() if item["status"] == "ok"]
    with_events = [item for item in ok if item["events"]]
    events = sum(len(item["events"]) for item in ok)
    print(
        f"\n끝. {len(result)}종목 조회 · 정상 {len(ok)} · "
        f"사건 있는 종목 {len(with_events)} · 사건 {events}건",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
