#!/usr/bin/env python
"""국장 내부자 보고서 **원문** 받기·파싱 — `insider_reports` (AR2 계열 탐색, 2026-10-13).

    .venv/bin/python tools/backfill_insider_reports.py --status              # 남은 양·오늘 몫만(호출 0)
    .venv/bin/python tools/backfill_insider_reports.py --cap 3000            # 최근 것부터 3,000콜
    .venv/bin/python tools/backfill_insider_reports.py --text-share-free     # N2·TX 원문 백필이 끝난 뒤(몫 1.5만이 빈다)

대상은 창고 `documents` 의 「임원ㆍ주요주주특정증권등소유상황보고서」 **원본**(정정본 제외, 2021-08~).
원문은 `data/_docs/dart_ownership/<연>/<월>/<접수번호>.xml.gz`(창고 밖, 원본 바이트 그대로)에 두고,
파싱한 세부변동 줄만 창고 `insider_reports` 에 넣는다(`collectors/dart_insider_reports.py`).
**원문 파일이 이미 있으면 DART 를 부르지 않고 다시 파싱만 한다** — 파서를 고쳐도 호출이 들지 않는다.

## 일 한도 — 남의 몫을 먹지 않는다

OpenDART 키 하나의 일 한도(`collectors.dart_daily_limit`, 2만)를 정규 수집·원문 백필과 나눠 쓴다.
이 도구의 오늘 몫 = 한도 − 원문 백필 몫(`collectors.dart_text_backfill_daily_cap`, 그 장부의 실사용이 더 크면 그것)
− 예비(`--reserve`, 기본 1,500: 정규 수집기·KR 일일 수집의 공시 목록) − 이 도구가 오늘 이미 쓴 것.
원문 백필(N2 정기보고서 등)이 끝났으면 `--text-share-free` 로 그 몫을 뺀다 — 끝났는지는 사람이 본다.
장부는 `data/_dart_quota/<KST 날짜>.insider.json` — 원문 백필 장부와 **파일을 나눈다**(그 도구가 자기 장부를 통째로 다시 쓴다).

## 시각

평일 08:20~15:50 KST 에는 시작하지 않는다(장중 수집과 창고 쓰기가 겹친다, 리드 규칙 2026-10-13).
DART 가 한도 초과(020)를 내면 받은 만큼 적재하고 멈춘다.

rc: 0 = 받았거나 할 일·몫이 없다 · 1 = 받을 것이 있었는데 한 건도 못 받았다 · 2 = 키 없음 · 3 = DART 가 막았다(받은 만큼 적재됨)
· 4 = 장중이라 시작 안 함. 키 값은 찍지 않는다.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import time as time_module
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_insider_reports as ir  # noqa: E402
from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable, FilingPolicy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
TITLE = "임원ㆍ주요주주특정증권등소유상황보고서"
FLUSH_EVERY = 200
PAUSE_SEC = 0.2
#: 장중 창고 쓰기 금지 창(평일, KST).
QUIET = ((8, 20), (15, 50))


def raw_root(root: Path) -> Path:
    return root / "_docs" / "dart_ownership"


def raw_path(root: Path, rcept_no: str) -> Path:
    return raw_root(root) / rcept_no[:4] / rcept_no[4:6] / f"{rcept_no}.xml.gz"


def skip_file(root: Path) -> Path:
    return root / "_dart_quota" / "insider-reports-skip.jsonl"


def ledger_path(root: Path, day: date) -> Path:
    return root / "_dart_quota" / f"{day.isoformat()}.insider.json"


def used_today(root: Path, day: date) -> int:
    try:
        return int(json.loads(ledger_path(root, day).read_text()).get("insider_reports", 0))
    except (FileNotFoundError, ValueError):
        return 0


def text_used_today(root: Path, day: date) -> int:
    try:
        return int(json.loads((root / "_dart_quota" / f"{day.isoformat()}.json").read_text()).get("text_backfill", 0))
    except (FileNotFoundError, ValueError):
        return 0


def take(root: Path, day: date) -> None:
    path = ledger_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".part")
    staging.write_text(json.dumps({"insider_reports": used_today(root, day) + 1}))
    staging.replace(path)


def skipped(root: Path) -> set[str]:
    path = skip_file(root)
    if not path.exists():
        return set()
    return {json.loads(line)["rcept_no"] for line in path.read_text().splitlines() if line.strip()}


def mark_skip(root: Path, rcept_no: str, why: str) -> None:
    path = skip_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"rcept_no": rcept_no, "why": why}, ensure_ascii=False) + "\n")


def targets(store: Store, now: datetime, *, since: date, until: date | None) -> pd.DataFrame:
    """documents 의 소유상황보고서 원본(정정 표지가 붙은 제목은 뺀다), 접수번호마다 한 행."""
    docs = store.get("documents", as_of=now, market="KR", columns=["entity_id", "valid_from", "doc_id", "doc_type", "title"])
    docs = docs[docs["doc_type"] == "ownership"]
    compact = docs["title"].fillna("").astype(str).str.replace(" ", "", regex=False)
    docs = docs[compact.str.startswith(TITLE)]  # "[기재정정]…" 은 여기서 빠진다
    docs = docs.drop_duplicates("doc_id")
    docs = docs.assign(day=docs["doc_id"].astype(str).str[:8])
    docs = docs[docs["day"] >= since.strftime("%Y%m%d")]
    if until is not None:
        docs = docs[docs["day"] <= until.strftime("%Y%m%d")]
    return docs[["entity_id", "doc_id", "day"]].reset_index(drop=True)


def done_receipts(store: Store, now: datetime) -> set[str]:
    have = store.get(ir.TABLE, as_of=now, columns=["rcept_no"])
    return set(have["rcept_no"].astype(str)) if not have.empty else set()


def first_member(content: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = sorted(n for n in archive.namelist() if not n.endswith("/"))
        if not names:
            raise ValueError("원문 ZIP 이 비었다")
        return archive.read(names[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--since", default="2021-08-01")
    parser.add_argument("--until", default="", help="접수일 상한(포함). 비우면 끝까지")
    parser.add_argument("--order", choices=("recent", "oldest"), default="recent")
    parser.add_argument("--cap", type=int, default=0, help="이번 실행의 콜 상한(0 = 오늘 몫 전부)")
    parser.add_argument("--reserve", type=int, default=1500, help="정규 수집 몫으로 남길 콜")
    parser.add_argument("--text-share-free", action="store_true", help="원문 백필 몫(1.5만)이 비었다 — 끝난 것을 사람이 확인한 뒤에만")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reparse-only", action="store_true", help="원문 파일이 있는 것만 다시 파싱(호출 0)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    local = now.astimezone(KST)
    root = Path(args.root)
    store = Store(root=root)
    today = local.date()

    limit = int(store.config("collectors.dart_daily_limit", as_of=now))
    text_cap = int(store.config("collectors.dart_text_backfill_daily_cap", as_of=now))
    text_share = 0 if args.text_share_free else max(text_cap, text_used_today(root, today))
    budget = max(limit - text_share - args.reserve - used_today(root, today), 0)
    if args.cap:
        budget = min(budget, args.cap)

    todo = targets(store, now, since=date.fromisoformat(args.since),
                   until=date.fromisoformat(args.until) if args.until else None)
    have = done_receipts(store, now)
    skip = skipped(root)
    todo = todo[~todo["doc_id"].astype(str).isin(have | skip)]
    todo = todo.sort_values("day", ascending=args.order == "oldest")
    cached = todo["doc_id"].astype(str).map(lambda r: raw_path(root, r).exists())
    print(f"{local:%Y-%m-%d %H:%M} 대상 {len(todo) + len(have):,} · 적재됨 {len(have):,} · 건너뜀 {len(skip):,} · 남음 {len(todo):,}"
          f"(원문 파일 있음 {int(cached.sum()):,}) · 오늘 몫 {budget:,}콜 (한도 {limit:,} − 원문백필 {text_share:,} − 예비 {args.reserve:,}"
          f" − 오늘 사용 {used_today(root, today):,})", flush=True)
    if args.status:
        return 0
    if args.reparse_only:
        todo = todo[cached.to_numpy()]
    if local.weekday() < 5 and QUIET[0] <= (local.hour, local.minute) < QUIET[1]:
        print("평일 장중(08:20~15:50)엔 창고에 쓰지 않는다 — 시작 안 함", flush=True)
        return 4

    policy = FilingPolicy(hour_kst=int(store.config("backfill.dart_publication_hour_kst", as_of=now)), clock=clock)
    source = DartSource()
    if not source.api_key:
        print("OPENDART_API_KEY 가 없다", file=sys.stderr)
        return 2
    rows: list[dict] = []
    stats = {"called": 0, "cached": 0, "reports": 0, "lines": 0, "no_text": 0, "no_table": 0, "bad": 0}
    blocked = False
    batch = 0

    def flush() -> None:
        nonlocal rows, batch
        if rows:
            store.append(ir.TABLE, rows, ingest_run_id=ir.run_id(now, f"b{batch:04d}"), source=ir.SOURCE)
            batch += 1
            rows = []

    for record in todo.itertuples(index=False):
        rcept = str(record.doc_id)
        path = raw_path(root, rcept)
        if path.exists():
            member = gzip.decompress(path.read_bytes())
            stats["cached"] += 1
        else:
            if args.reparse_only or stats["called"] >= budget:
                continue
            try:
                take(root, today)
                content = source.document(rcept)
            except DartUnavailable as error:
                print(f"DART 가 막았다({rcept}): {error} — 받은 만큼 적재하고 멈춘다", file=sys.stderr, flush=True)
                blocked = True
                break
            stats["called"] += 1
            time_module.sleep(PAUSE_SEC)
            if not content:
                mark_skip(root, rcept, "no_text")
                stats["no_text"] += 1
                continue
            try:
                member = first_member(content)
            except (zipfile.BadZipFile, ValueError) as error:
                mark_skip(root, rcept, f"bad_zip:{error}")
                stats["bad"] += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            staging = path.with_suffix(".part")
            staging.write_bytes(gzip.compress(member))
            staging.replace(path)
        parsed = ir.parse_bytes(member)
        if not parsed["lines"]:
            mark_skip(root, rcept, "no_table")
            stats["no_table"] += 1
            continue
        moment = policy.for_filing(date(int(rcept[:4]), int(rcept[4:6]), int(rcept[6:8])))
        new = ir.to_rows(parsed, rcept_no=rcept, entity_id=str(record.entity_id), moment=moment)
        rows.extend(new)
        stats["reports"] += 1
        stats["lines"] += len(new)
        if stats["reports"] % FLUSH_EVERY == 0:
            flush()
            print(f"  … 보고서 {stats['reports']:,} · 줄 {stats['lines']:,} · 콜 {stats['called']:,}/{budget:,}", flush=True)
    flush()
    print(f"끝 — {stats} · 오늘 이 도구 사용 {used_today(root, today):,}콜", flush=True)
    if blocked:
        return 3
    if len(todo) and not stats["reports"] and not stats["no_text"] and not stats["no_table"] and budget > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
