"""국장 종가를 마감 직후 LS `t8407` 로 적는다 — KRX 일봉(22:40)이 오기 전 6시간의 공백.

    .venv/bin/python tools/collect_prices_ls.py            # 마지막 완료 세션, 명단 전체
    .venv/bin/python tools/collect_prices_ls.py --dry-run --limit 100

트레이딩 탭이 16:00~22:40 사이에 "종가 기준 · 일봉 수집 전" 으로 어제 값을 들고 있었다.
`t8407` 은 50종목씩 시가·고가·저가·현재가(마감 뒤 = 종가)·거래량·거래대금(백만원)을
준다 — 명단 2,900종목이면 60콜. KRX 행이 나중에 같은 (종목, 날짜)로 오면 정정본이 된다
(adj_factor 는 KRX 쪽이 채운다; 여기는 비워 둔다). 장중이면 적지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.collect_indices_ls import completed_session  # noqa: E402

TABLE = "prices"
SOURCE = "ls_t8407"
TR = "t8407"
PATH = "/stock/market-data"
CHUNK = 50
SEOUL = ZoneInfo("Asia/Seoul")


def rows_from_block(rows: list[dict], *, day: date, observed_at: datetime) -> list[dict]:
    out = []
    for row in rows:
        code = str(row.get("shcode", "")).strip()
        close = float(row.get("price") or 0)
        if not code or close <= 0:
            continue
        num = lambda k: (float(row.get(k)) if row.get(k) not in (None, "") else None)  # noqa: E731
        # **개장 전 스텁을 버린다.** 장이 열리기 전에 t8407 을 부르면 현재가엔 전일 종가가,
        # 시·고·저·거래량엔 0 이 온다. 2026-09-02 06:30(브리핑 전 보충)에 그게 9/1 봉으로
        # 2,874행 적혔다 — 차트는 0 에서 시작하는 봉을 그렸고, low 0 은 체결 시뮬레이션·
        # 슬리피지 측정을 오염시킨다. 시·고·저·거래량 중 하나라도 0 이면 그날 봉이 아니다.
        if any((num(k) or 0.0) <= 0 for k in ("open", "high", "low", "volume")):
            continue
        value = num("value")
        out.append({
            "entity_id": f"KR:{code}",
            "valid_from": datetime(day.year, day.month, day.day, 9, 0, tzinfo=SEOUL),  # KRX 행과 같은 시각
            "observed_at": observed_at, "source": SOURCE, "market": "KR",
            "open": num("open"), "high": num("high"), "low": num("low"), "close": close,
            "volume": num("volume"), "value": value * 1_000_000 if value is not None else None,  # t8407 은 백만원
            "adj_factor": None,
        })
    return out


def universe_codes(store: Store, *, as_of: datetime) -> list[str]:
    frame = store.get("universe", as_of=as_of, lookback=7, market="KR")
    if frame.empty:
        return []
    frame = frame.sort_values("valid_from").drop_duplicates("entity_id", keep="last")
    if "is_listed" in frame.columns:
        frame = frame[frame["is_listed"].astype(bool)]
    return sorted(str(e).split(":", 1)[1] for e in frame["entity_id"] if str(e).startswith("KR:"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    day = completed_session(now, market=Market.KR)
    if day is None:
        print("장중이다 — 미완성 종가는 적지 않는다."); return 0
    have = store.get(TABLE, as_of=now, lookback=3, market="KR", columns=["valid_from"])
    if not have.empty and (have["valid_from"].dt.date == day).any():
        print(f"{day} 시세는 이미 있다 — 할 일 없음"); return 0
    codes = universe_codes(store, as_of=now)[: args.limit]
    if not codes:
        print("명단이 비었다"); return 1

    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
    from tools.verify_live_order import resolve_profile

    profile = resolve_profile(store, market="KR", as_of=now)
    client = LSClient(credentials=LSCredentials.from_env(prefix=profile.env_prefix),
                      live_trading=False, min_interval_sec=profile.min_interval_sec)
    rows: list[dict] = []
    failed = 0
    for start in range(0, len(codes), CHUNK):
        chunk = codes[start:start + CHUNK]
        try:
            data = client.request_tr(PATH, TR, {f"{TR}InBlock": {"nrec": len(chunk), "shcode": "".join(chunk)}})
            rows.extend(rows_from_block(data.get(f"{TR}OutBlock1") or [], day=day, observed_at=now))
        except Exception as error:
            failed += 1
            print(f"  청크 {start // CHUNK + 1} 실패 {type(error).__name__}", file=sys.stderr)
    print(f"{day} · 명단 {len(codes)}종목 · 받은 종가 {len(rows)}행 · 실패 청크 {failed}")
    if not rows:
        return 1
    if args.dry_run:
        print("드라이런 — 적지 않는다"); return 0
    written = store.append(TABLE, rows, ingest_run_id=f"prices-ls-{day.isoformat()}-{now:%H%M}", source=SOURCE)
    print(f"prices 적재: {written}행 ({SOURCE})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
