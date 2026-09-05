"""DART elestock — 임원ㆍ주요주주 소유상황보고를 `insider_trades` 에 넣는다 (사전등록 시행 D 의 재료).

    .venv/bin/python tools/backfill_insider_dart.py [--since 2021-01-01] [--limit N] [--dry-run]

회사당 콜 1건(전체 이력이 한 응답에 온다 — 삼성전자 3,399행). 상장사 ~2,700 → 하루 한도(20,000) 안.
이미 적재된 회사는 **마지막 보고일 이후**만 더한다. 매일 수집(collect_daily KR)에 붙이면 증분이 된다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TABLE = "insider_trades"
SEOUL = ZoneInfo("Asia/Seoul")


def _num(text: object) -> float | None:
    raw = str(text or "").replace(",", "").strip()
    if raw in ("", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def rows_for(code: str, payload: list[dict], *, since: date, after: date | None, observed_at: datetime) -> list[dict]:
    out = []
    for item in payload:
        try:
            day = date.fromisoformat(str(item.get("rcept_dt") or ""))
        except ValueError:
            continue
        if day < since or (after is not None and day <= after):
            continue
        moment = datetime.combine(day, dtime(18, 0), tzinfo=SEOUL)
        out.append({
            "entity_id": f"KR:{code}", "valid_from": moment, "observed_at": moment,
            "source": "dart", "market": "KR",
            "rcept_no": str(item.get("rcept_no") or ""), "reporter": str(item.get("repror") or ""),
            "registered_executive": str(item.get("isu_exctv_rgist_at") or ""),
            "position": str(item.get("isu_exctv_ofcps") or ""),
            "major_shareholder": str(item.get("isu_main_shrholdr") or ""),
            "shares": _num(item.get("sp_stock_lmp_cnt")), "change": _num(item.get("sp_stock_lmp_irds_cnt")),
            "rate": _num(item.get("sp_stock_lmp_rate")), "change_rate": _num(item.get("sp_stock_lmp_irds_rate")),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--since", default="2021-01-01")
    parser.add_argument("--limit", type=int, default=None, help="이번에 부를 회사 수 상한")
    parser.add_argument("--pause", type=float, default=0.12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    since = date.fromisoformat(args.since)
    dart = DartSource()
    codes = dart.corp_codes()  # 종목코드 → corp_code
    latest: dict[str, date] = {}
    existing = store.get(TABLE, as_of=now, columns=["entity_id", "valid_from"])
    if not existing.empty:
        for entity, day in existing.groupby("entity_id")["valid_from"].max().items():
            latest[str(entity)] = day.tz_convert(SEOUL).date()
    targets = sorted(codes.items())[: args.limit] if args.limit else sorted(codes.items())
    print(f"회사 {len(targets)} · 이미 적재 {len(latest)} · since {since}")
    total, failed = 0, 0
    for done, (code, corp) in enumerate(targets, start=1):
        try:
            response = dart._call("/elestock.json", {"corp_code": corp})
            payload = response.json()
        except (DartUnavailable, ValueError) as exc:
            failed += 1
            print(f"  {code} 실패: {exc}", file=sys.stderr)
            continue
        status = str(payload.get("status"))
        if status == "020":
            print("DART 일일 한도 초과 — 내일 이어서", file=sys.stderr)
            break
        rows = rows_for(code, payload.get("list") or [], since=since, after=latest.get(f"KR:{code}"), observed_at=now)
        if rows and not args.dry_run:
            store.append(TABLE, rows, ingest_run_id=f"insider-{code}-{now:%Y%m%dT%H%M%S}")
        total += len(rows)
        if done % 100 == 0:
            print(f"  … {done}/{len(targets)} · 누적 {total}행", flush=True)
        time.sleep(args.pause)
    print(f"완료 — {total}행 · 실패 {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
