"""LS `t1511` 로 국장 대표지수(코스피·코스닥) 일봉을 채운다 — KRX 가 늦는 자리.

    .venv/bin/python tools/collect_indices_ls.py            # 마지막 완료 세션
    .venv/bin/python tools/collect_indices_ls.py --dry-run

## 왜 필요한가

KRX Open API 는 그날 지수를 **다음 날 오후**에야 낸다(실측 2026-08-26/27: 15:55·22:40·
다음날 06:00 전부 0건, 다음날 15:55 에 들어옴). 그래서 06:30 브리핑은 늘 전전날
지수를 들고 있었고, 23:05 shadow 의 벤치마크는 매일 null 로 시작했다. LS `t1511`
은 장 마감 직후부터 그날 종가·시가·고가·저가·거래량을 준다 — 같은 값을 하루
먼저 아는 길이다.

## 언제 어느 세션인가

`t1511` 에는 날짜 필드가 없다. **지금이 그 시장의 마감(15:30) 뒤면 오늘, 아니면
직전 거래일**의 값이다. 장중(09:00~15:30)에 부르면 미완성 값이므로 **적지 않는다**.

## KRX 행과의 관계

같은 (entity_id, valid_from) 에 나중에 KRX 행이 들어오면 같은 종가의 정정본이
된다 — 값이 같으니 어느 쪽이 이겨도 상관없다. 이 도구는 KRX 가 이미 적재한
세션은 건너뛴다(source 무관, 행이 있으면 됨).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import (  # noqa: E402
    SPECS, Market, is_trading_day, local_time,
)
from quant_rl_trading.collectors.panels import session_timestamp  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TABLE = "indices"
SOURCE = "ls_t1511"
TR = "t1511"
PATH = "/indtp/market-data"
#: entity → (업종코드, board). 화면·회계가 기대하는 이름(`KR:IDX:KOSPI`)과 같다.
INDICES: dict[str, tuple[str, str]] = {
    "KR:IDX:KOSPI": ("001", "KOSPI"),
    "KR:IDX:KOSDAQ": ("301", "KOSDAQ"),
}


def completed_session(now: datetime, *, market: Market = Market.KR) -> date | None:
    """지금 값이 어느 세션의 완료된 종가인가. 장중이면 None."""
    here = local_time(market, now)
    day = here.date()
    spec = SPECS[market]
    if is_trading_day(market, day):
        if here.time() >= spec.regular_close:
            return day
        if here.time() >= spec.regular_open:
            return None  # 장중 — 미완성
    # 개장 전이거나 휴장 — 직전 거래일
    for back in range(1, 15):
        prev = day - timedelta(days=back)
        if is_trading_day(market, prev):
            return prev
    return None


def _number(value: object) -> float | None:
    try:
        out = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def rows_from_client(client, *, day: date, observed_at: datetime) -> list[dict]:
    """t1511 두 번(코스피·코스닥) → indices 행. 종가가 없으면 그 지수는 뺀다."""
    out: list[dict] = []
    for entity, (upcode, board) in INDICES.items():
        data = client.request_tr(PATH, TR, {f"{TR}InBlock": {"upcode": upcode}})
        block = data.get(f"{TR}OutBlock") or {}
        close = _number(block.get("pricejisu"))
        if close is None:
            print(f"  {entity}: t1511 종가 없음 — 건너뛴다", file=sys.stderr)
            continue
        out.append({
            "entity_id": entity,
            "valid_from": session_timestamp(day),
            "observed_at": observed_at,
            "source": SOURCE,
            "market": "KR",
            "board": board,
            "open": _number(block.get("openjisu")),
            "high": _number(block.get("highjisu")),
            "low": _number(block.get("lowjisu")),
            "close": close,
            "volume": _number(block.get("volume")),
            "value": _number(block.get("value")),
        })
    return out


def already_loaded(store: Store, *, day: date, as_of: datetime) -> set[str]:
    frame = store.get(TABLE, as_of=as_of, lookback=5)
    if frame.empty:
        return set()
    stamp = session_timestamp(day)
    hit = frame[(frame["valid_from"] == stamp) & (frame["entity_id"].isin(INDICES))]
    return set(hit["entity_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    store = build_store(args.data_root)
    day = completed_session(now)
    if day is None:
        print("장중이다 — 미완성 지수는 적지 않는다.")
        return 0
    have = already_loaded(store, day=day, as_of=now)
    wanted = [e for e in INDICES if e not in have]
    if not wanted:
        print(f"{day} 지수는 이미 있다 ({', '.join(sorted(have))}) — 할 일 없음")
        return 0

    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
    from tools.verify_live_order import resolve_profile

    profile = resolve_profile(store, market="KR", as_of=now)
    client = LSClient(
        credentials=LSCredentials.from_env(prefix=profile.env_prefix),
        live_trading=False,  # t1511 은 조회 TR(PAPER_ALLOWED_TR) — 주문 경로가 없다
        min_interval_sec=profile.min_interval_sec,
    )
    rows = [r for r in rows_from_client(client, day=day, observed_at=now) if r["entity_id"] in wanted]
    for r in rows:
        print(f"  {r['entity_id']} {day} 종가 {r['close']:,.2f} (시 {r['open']} 고 {r['high']} 저 {r['low']})")
    if not rows:
        print("적을 행이 없다.")
        return 1
    if args.dry_run:
        print("드라이런 — 적지 않는다.")
        return 0
    written = store.append(TABLE, rows, ingest_run_id=f"indices-ls-{day.isoformat()}-{now:%H%M}", source=SOURCE)
    print(f"indices 적재: {written}행 ({SOURCE} · {day})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
