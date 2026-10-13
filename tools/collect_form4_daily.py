#!/usr/bin/env python
"""SEC Form 4 **일별 증분** 적재 — `form4_trades` (AR shadow 미장 내부자 입력, 2026-10-13).

    .venv/bin/python tools/collect_form4_daily.py                         # 마지막 적재 다음 날 ~ 어제(ET), 최대 --max-days
    .venv/bin/python tools/collect_form4_daily.py --start 2026-10-01 --end 2026-10-12
    .venv/bin/python tools/collect_form4_daily.py --dry-run --start 2026-10-01 --end 2026-10-01

하루 = EDGAR 일별 색인 1콜 + Form 4 접수 원문 N콜(하루 1,000~1,800건). 초당 8건(SEC 한도 10).
실행 id 가 날짜라서 **이미 받은 날은 건너뛴다.** 색인이 404 면 휴장일·주말이다(실패 아님).
`observed_at` = 실제로 받은 시각(라이브 규칙), `valid_from` = 접수일 18:00 ET.

rc: 0 = 받았거나 받을 날이 없다 · 1 = 한 날이라도 실패(받은 날은 적재됨) · 2 = User-Agent 없음.
"""
from __future__ import annotations

import argparse
import os
import sys
import time as time_module
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import sec_form4 as bulk  # noqa: E402
from quant_rl_trading.collectors import sec_form4_daily as daily  # noqa: E402
from quant_rl_trading.collectors.edgar_filings import USER_AGENT_ENV, EdgarPolicy, EdgarSource  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
RATE = 8.0
RETRIES = 4


class Http:
    def __init__(self, user_agent: str) -> None:
        import httpx

        self.client = httpx.Client(timeout=60.0, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                                   follow_redirects=True)
        self.last = 0.0
        self.calls = 0

    def get(self, url: str) -> tuple[int, str]:
        import httpx

        error: Exception | None = None
        for attempt in range(RETRIES):
            wait = 1.0 / RATE - (time_module.monotonic() - self.last)
            if wait > 0:
                time_module.sleep(wait)
            self.last = time_module.monotonic()
            self.calls += 1
            try:
                response = self.client.get(url)
            except httpx.TransportError as exc:
                error = exc
                time_module.sleep(2.0 * 2**attempt)
                continue
            if response.status_code in (429,) or response.status_code >= 500:
                error = RuntimeError(f"HTTP {response.status_code}")
                time_module.sleep(2.0 * 2**attempt)
                continue
            return response.status_code, response.text
        raise RuntimeError(f"{RETRIES}회 실패: {url} — {error}")


def last_loaded_day(store: Store, now: datetime) -> date | None:
    frame = store.get(bulk.TABLE, as_of=now, lookback=200, market="US", columns=["valid_from"])
    if frame.empty:
        return None
    return frame["valid_from"].max().tz_convert(EASTERN).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--max-days", type=int, default=14, help="자동 시작일일 때 최대 며칠 뒤로")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    user_agent = os.environ.get(USER_AGENT_ENV, "")
    if "@" not in user_agent:
        print(f"{USER_AGENT_ENV} 에 연락 가능한 이메일이 있어야 한다(SEC 신원 규칙).", file=sys.stderr)
        return 2
    clock = LiveClock()
    now = clock.now()
    store = Store(root=Path(args.root))
    today_et = now.astimezone(EASTERN).date()
    end = date.fromisoformat(args.end) if args.end else today_et - timedelta(days=1)
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        last = last_loaded_day(store, now)
        start = max((last + timedelta(days=1)) if last else end, end - timedelta(days=args.max_days))
    policy = EdgarPolicy(clock=clock)
    tickers = EdgarSource(user_agent=user_agent).tickers()
    http = Http(user_agent)
    print(f"{now.astimezone(ZoneInfo('Asia/Seoul')):%Y-%m-%d %H:%M} KST · {start}~{end}(ET) · CIK→티커 {len(tickers):,}", flush=True)
    failed = 0
    day = start
    while day <= end:
        rid = daily.run_id(day)
        if day.weekday() >= 5 or (not args.dry_run and store.ingest_run_recorded(bulk.TABLE, rid)):
            day += timedelta(days=1)
            continue
        try:
            status, text = http.get(daily.index_url(day))
            if status == 404:
                print(f"{day}: 색인 없음(휴장일) — 건너뜀", flush=True)
                day += timedelta(days=1)
                continue
            if status != 200:
                raise RuntimeError(f"색인 HTTP {status}")
            paths = daily.form4_paths(text)
            valid_from = policy.for_filing(day)
            rows, why = [], {}
            for path in paths:
                code, body = http.get(daily.ARCHIVE_URL.format(path=path))
                if code != 200:
                    why["http"] = why.get("http", 0) + 1
                    continue
                parsed = daily.parse(body)
                if parsed is None:
                    why["no_xml"] = why.get("no_xml", 0) + 1
                    continue
                new, reason = daily.to_rows(parsed, accession=daily.accession_of(path), filed=day, valid_from=valid_from,
                                            observed_at=clock.now(), tickers=tickers)
                if reason:
                    why[reason] = why.get(reason, 0) + 1
                rows.extend(new)
            codes: dict[str, int] = {}
            for r in rows:
                codes[r["trans_code"]] = codes.get(r["trans_code"], 0) + 1
            print(f"{day}: Form 4 {len(paths):,}건 → {len(rows):,}행 · 버림 {why} · 코드 {dict(sorted(codes.items(), key=lambda kv: -kv[1])[:6])}",
                  flush=True)
            if rows:  # 분기 벌크가 이미 넣은 접수는 건너뛴다(같은 거래를 두 번 세지 않게)
                have = store.get(bulk.TABLE, as_of=clock.now(), lookback=(now.date() - day).days + 3, market="US",
                                 columns=["accession", "valid_from"])
                known = set(have.loc[have["valid_from"] == valid_from, "accession"].astype(str))
                if known:
                    rows = [r for r in rows if r["accession"] not in known]
                    print(f"{day}: 이미 있는 접수 {len(known):,}건 건너뜀 → {len(rows):,}행", flush=True)
            if rows and not args.dry_run:
                store.append(bulk.TABLE, rows, ingest_run_id=rid, source=daily.SOURCE)
        except Exception as error:  # noqa: BLE001 — 하루가 죽어도 나머지는 받는다, rc 로 알린다
            print(f"{day}: 실패 {error}", file=sys.stderr, flush=True)
            failed += 1
        day += timedelta(days=1)
    print(f"끝 — SEC 호출 {http.calls:,} · 실패 {failed}일", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
