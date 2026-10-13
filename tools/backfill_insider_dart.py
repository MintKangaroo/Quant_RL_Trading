"""DART elestock — 임원ㆍ주요주주 소유상황보고를 `insider_trades` 에 넣는다 (사전등록 시행 D 의 재료, 6차 G4·AR 의 입력).

    .venv/bin/python tools/backfill_insider_dart.py --recent-docs 10            # 매일 증분(크론): 최근 10일 소유보고가 있는 회사만
    .venv/bin/python tools/backfill_insider_dart.py --recent-docs 50 --max-calls 1500   # 따라잡기
    .venv/bin/python tools/backfill_insider_dart.py --observed filing [--since 2021-01-01]   # 옛 전 종목 백필(원래 방식)

회사당 콜 1건(전체 이력이 한 응답에 온다 — 삼성전자 3,399행). 단 elestock 은 **최근 약 2년만** 준다(창고 최소 2024-08-30).

## 증분 (2026-10-13)

이 표는 8/28 에서 멈춰 있었다 — 매일 도는 길이 없었다(AR shadow 가 G4 를 0 으로 채점할 뻔했다). 그래서:

- **이미 있는 접수번호(+보고자)는 건너뛴다.** 예전엔 회사마다 "마지막 보고일 이후" 만 받았는데, 같은 날 늦게 들어온
  보고를 그날이 이미 적재됐다는 이유로 영영 놓친다.
- `--recent-docs N` — 창고 `documents`(KR 일일 수집이 매일 채운다)에 최근 N일 「임원ㆍ주요주주…소유상황보고서」 가 있는
  회사만 부른다. 전 종목(2,700콜)을 매일 부르지 않는다 — DART 일 한도 2만을 원문 백필과 나눠 쓴다.
- `observed_at` 은 기본이 **실제 받은 시각**(`--observed now`, 라이브 규칙)이다. 옛 백필과 같은 "접수일 18:00"
  (data-contract §6 백필 규칙)은 `--observed filing` 으로만. `valid_from` 은 어느 쪽이든 접수일 18:00 KST.
- 접수일 18:00 이 아직 안 된 보고는 싣지 않는다(내일 받는다).

rc: 0 = 끝 · 1 = 실패한 회사가 있다 · 3 = DART 한도 초과로 멈췄다(받은 만큼은 적재됨).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
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
TITLE = "임원ㆍ주요주주특정증권등소유상황보고서"


def _num(text: object) -> float | None:
    raw = str(text or "").replace(",", "").strip()
    if raw in ("", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def rows_for(code: str, payload: list[dict], *, since: date, seen: set[tuple[str, str]], now: datetime,
             observed: str = "now") -> list[dict]:
    """elestock 응답 → 적재 행. ``seen`` 은 이미 있는 (접수번호, 보고자) — 건너뛴다.
    ``observed`` = "now"(실제 받은 시각) | "filing"(접수일 18:00, 옛 백필 규칙)."""
    out = []
    for item in payload:
        try:
            day = date.fromisoformat(str(item.get("rcept_dt") or ""))
        except ValueError:
            continue
        if day < since:
            continue
        rcept, reporter = str(item.get("rcept_no") or ""), str(item.get("repror") or "")
        if (rcept, reporter) in seen:
            continue
        moment = datetime.combine(day, dtime(18, 0), tzinfo=SEOUL)
        if moment > now:
            continue  # 아직 알 수 없는 시각 — 내일 받는다
        out.append({
            "entity_id": f"KR:{code}", "valid_from": moment, "observed_at": now if observed == "now" else moment,
            "source": "dart", "market": "KR",
            "rcept_no": rcept, "reporter": reporter,
            "registered_executive": str(item.get("isu_exctv_rgist_at") or ""),
            "position": str(item.get("isu_exctv_ofcps") or ""),
            "major_shareholder": str(item.get("isu_main_shrholdr") or ""),
            "shares": _num(item.get("sp_stock_lmp_cnt")), "change": _num(item.get("sp_stock_lmp_irds_cnt")),
            "rate": _num(item.get("sp_stock_lmp_rate")), "change_rate": _num(item.get("sp_stock_lmp_irds_rate")),
        })
        seen.add((rcept, reporter))
    return out


def recent_filers(store, now: datetime, days: int) -> set[str]:
    """최근 ``days`` 일 documents 에 임원ㆍ주요주주 소유상황보고서가 있는 종목코드."""
    docs = store.get("documents", as_of=now, lookback=days, market="KR", columns=["entity_id", "doc_type", "title"])
    docs = docs[docs["doc_type"] == "ownership"]
    compact = docs["title"].fillna("").astype(str).str.replace(" ", "", regex=False)
    docs = docs[compact.str.contains(TITLE)]
    return {str(e).split(":", 1)[1] for e in docs["entity_id"].unique()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--since", default="2021-01-01")
    parser.add_argument("--limit", type=int, default=None, help="이번에 부를 회사 수 상한")
    parser.add_argument("--recent-docs", type=int, default=0, help="최근 N일 소유보고가 있는 회사만(0 = 전 종목)")
    parser.add_argument("--max-calls", type=int, default=0, help="DART 콜 상한(0 = 없음)")
    parser.add_argument("--observed", choices=("now", "filing"), default="now")
    parser.add_argument("--pause", type=float, default=0.12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    now = clock.now()
    store = build_store(args.data_root)
    since = date.fromisoformat(args.since)
    dart = DartSource()
    codes = dart.corp_codes()  # 종목코드 → corp_code
    existing = store.get(TABLE, as_of=now, columns=["rcept_no", "reporter", "valid_from"])
    seen = set(zip(existing["rcept_no"].astype(str), existing["reporter"].astype(str), strict=True)) if not existing.empty else set()
    last = existing["valid_from"].max().tz_convert(SEOUL).date() if not existing.empty else None
    targets = sorted(codes.items())
    if args.recent_docs:
        wanted = recent_filers(store, now, args.recent_docs)
        targets = [(c, k) for c, k in targets if c in wanted]
    if args.limit:
        targets = targets[: args.limit]
    if args.max_calls:
        targets = targets[: args.max_calls]
    print(f"{now.astimezone(SEOUL):%Y-%m-%d %H:%M} 회사 {len(targets)} · 적재된 접수 {len(seen):,} (마지막 접수일 {last}) · "
          f"observed={args.observed}", flush=True)
    total, failed, calls = 0, 0, 0
    run = f"insider-{now:%Y%m%dT%H%M%S}"
    for done, (code, corp) in enumerate(targets, start=1):
        try:
            calls += 1
            response = dart._call("/elestock.json", {"corp_code": corp})
            payload = response.json()
        except (DartUnavailable, ValueError) as exc:
            failed += 1
            print(f"  {code} 실패: {exc}", file=sys.stderr)
            continue
        status = str(payload.get("status"))
        if status == "020":
            print("DART 일일 한도 초과 — 내일 이어서", file=sys.stderr)
            print(f"멈춤 — {total}행 · 콜 {calls}", flush=True)
            return 3
        rows = rows_for(code, payload.get("list") or [], since=since, seen=seen, now=clock.now(), observed=args.observed)
        if rows and not args.dry_run:
            store.append(TABLE, rows, ingest_run_id=f"{run}-{code}")
        total += len(rows)
        if done % 100 == 0:
            print(f"  … {done}/{len(targets)} · 누적 {total}행", flush=True)
        time.sleep(args.pause)
    after = store.get(TABLE, as_of=clock.now() + timedelta(seconds=1), lookback=60, columns=["valid_from"]) if not args.dry_run else None
    newest = after["valid_from"].max().tz_convert(SEOUL).date() if after is not None and not after.empty else last
    print(f"완료 — {total}행 · 콜 {calls} · 실패 {failed} · 마지막 접수일 {newest}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
