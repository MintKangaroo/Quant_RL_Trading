#!/usr/bin/env python
"""공시 원문 **과거 백필** — 시행 TX 재료(2026-10-02 사용자 승인, docs/protocols/tx-filing-text-2026-10.md).

    .venv/bin/python tools/backfill_filing_texts.py                 # 밤 한 회차(크론 00:45)
    .venv/bin/python tools/backfill_filing_texts.py --status        # 남은 양만 찍는다(호출 0)

정규 수집기(`collect_filing_texts.py`, 01:20)는 최근 600일만 본다. 이 도구는 그 창 **밖**(더 오래된 것)을
같은 규약으로 채운다 — 원문은 `data/_docs/dart/<연>/<월>/<접수번호>.txt.gz`, 창고에는 `documents` 정정본으로
경로만(observed_at = 받은 시각, `dart_documents.revision_row`). 이어받기는 정규 수집기와 같다: `raw_path` 가 찬
행은 `pending` 에서 저절로 빠진다.

차례 — 앞 단계가 끝나야 다음으로 간다:

1. `events`   — 수집 유형(`DEFAULT_TYPES` + 제목)의 **수시공시**, 2021-08-01 ~ 정규 창 앞
2. `periodic` — 정기보고서(분기·반기·사업), 같은 기간
3. `prior-list` — 2020-08-01 ~ 2021-08-16 공시 **목록**(`FilingsBackfiller`, 날짜 축). 목록의 observed_at 은
   접수일 18:00 KST(`backfill.dart_publication_hour_kst`, data-contract "백필 — 관측시각") — 기존 목록 백필과 같다
4. `prior-periodic` — 그 1년의 정기보고서 원문(첫해 Lazy Prices 의 비교 대상)

**일 한도.** 이 도구의 DART 호출은 날짜별 장부(`data/_dart_quota/<KST 날짜>.json`)로 센다. 몫은
`collectors.dart_text_backfill_daily_cap`(키 일 한도 `collectors.dart_daily_limit` 중 나머지는 정규 수집들 몫).
몫에 닿으면 받은 만큼 적재하고 멈춘다 — 정상 종료다. 정규 원문 수집기가 도는 동안은 기다린다(같은 표에 같이 쓰지 않는다).

rc: 0 = 받았거나(몫·마감 시각에서 멈춤 포함) 할 일이 없다 · 1 = 받을 것이 있었는데 한 건도 못 받았다 ·
2 = 키가 없다 · 3 = DART 가 중간에 막았다(받은 만큼은 적재됨). 조용한 실패 금지.
키 값은 어디에도 찍지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors.dart_filings import FilingsBackfiller  # noqa: E402
from quant_rl_trading.collectors.dart_source import (  # noqa: E402
    DartSource,
    DartUnavailable,
    FilingPolicy,
)
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.collect_filing_texts import DEFAULT_TITLES, DEFAULT_TYPES, PAUSE_SEC  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
#: 확장 패널 시작(2021-08)과 그 1년 앞(첫해 정기보고서의 비교 대상).
FLOOR = date(2021, 8, 1)
PRIOR_FLOOR = date(2020, 8, 1)
#: 창고 `documents`(국장 목록)가 시작하는 날의 전날 — prior-list 의 끝.
PRIOR_LIST_END = date(2021, 8, 16)
#: 정규 수집기 창(`--lookback 600`). 백필은 그 창의 앞 끝을 **20일 겹쳐** 덮는다 — 정규 창의 앞 끝은 매일
#: 하루씩 밀려 올라가므로, 겹치지 않으면 그 사이 공시가 아무도 안 받는 틈에 남는다. 겹친 구간을 둘이 같이
#: 받는 일은 없다: 정규 수집기가 도는 동안 백필은 기다리고, 받은 것은 `raw_path` 가 차서 `pending` 에서 빠진다.
REGULAR_WINDOW_DAYS = 600
REGULAR_OVERLAP_DAYS = 20
#: 같은 공시 원문 파싱이 이만큼 실패하면 다음 밤부터 건너뛴다(장부에 남는다).
MAX_FAILURES = 3
#: 몇 건마다 적재하나. 중간에 죽어도 그 밤의 일이 통째로 날아가지 않게.
FLUSH_EVERY = 200
QUOTA_DIR = Path("data/_dart_quota")
FAILURES_FILE = Path("data/_dart_quota/text-backfill-failures.jsonl")
#: prior-list 를 한 바퀴 다 돌았다는 표식. 목록 백필은 공시가 없는 날(휴일)을 매니페스트에 안 남기므로
#: `FilingsBackfiller.pending` 만 보면 그 날들이 영원히 "남은" 채로 매일 밤 다시 불린다.
PRIOR_LIST_DONE = Path("data/_dart_quota/prior-list.done")
REGULAR_COLLECTOR = "tools/collect_filing_texts.py"
PHASES = ("events", "periodic", "prior-list", "prior-periodic")
#: 정기보고서 제목. 정정 표지([기재정정] 등)를 뗀 뒤 맨 앞. "사업보고서제출기한연장신고서" 는 아니다.
_PERIODIC = re.compile(r"^(분기|반기|사업)보고서(\(|$)")
_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")
COLUMNS = ["entity_id", "valid_from", "revision", "source", "doc_id", "doc_type", "title", "filer", "url", "raw_path"]


class QuotaReached(DartUnavailable):
    """이 도구의 오늘 몫을 다 썼다. DART 가 막은 것이 아니다 — 정상 종료 사유."""


@dataclass
class QuotaLedger:
    """날짜별 호출 장부. 호출 **전에** 센다 — 센 뒤 죽어도 덜 센 쪽으로는 안 틀린다."""

    root: Path
    day: date
    cap: int

    @property
    def path(self) -> Path:
        return self.root / f"{self.day.isoformat()}.json"

    @property
    def used(self) -> int:
        try:
            return int(json.loads(self.path.read_text()).get("text_backfill", 0))
        except (FileNotFoundError, ValueError):
            return 0

    @property
    def left(self) -> int:
        return max(self.cap - self.used, 0)

    def take(self) -> None:
        used = self.used
        if used >= self.cap:
            raise QuotaReached(f"오늘({self.day}) 백필 몫 {self.cap:,}콜을 다 썼다")
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(".part")
        staging.write_text(json.dumps({"text_backfill": used + 1, "cap": self.cap}))
        staging.replace(self.path)


@dataclass
class MeteredDartSource(DartSource):
    """모든 DART 호출이 지나는 `_call` 앞에서 장부를 센다 — 목록의 페이지 넘김까지 센다."""

    ledger: QuotaLedger | None = None

    def _call(self, path: str, params: dict[str, Any]) -> Any:
        if self.ledger is not None:
            self.ledger.take()
        return super()._call(path, params)


def is_periodic(title: Any) -> bool:
    return bool(_PERIODIC.match(re.sub(r"\s+", "", _PREFIX.sub("", str(title or "")))))


def ceiling(now: datetime) -> date:
    """이 날짜 **전** 공시만 백필한다 — 그 뒤는 정규 수집기(600일 창)의 몫. 정규 창과 20일 겹친다."""
    return (now.astimezone(KST) - timedelta(days=REGULAR_WINDOW_DAYS - REGULAR_OVERLAP_DAYS)).date()


def _ts(day: date) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC")


def load_frame(store: Store, now: datetime, *, start: date, end: date) -> pd.DataFrame:
    """[start, end) 의 국장 DART 공시. 쓰는 열만 읽는다(RSS 1GB 아래)."""
    span = (now.date() - start).days + 2
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=span, until=datetime.combine(end, datetime.min.time(), UTC),
                      columns=COLUMNS, market="KR")
    if frame.empty:
        return frame
    frame = frame[(frame["valid_from"] >= _ts(start)) & (frame["valid_from"] < _ts(end))]
    return frame


def todo_for(frame: pd.DataFrame, phase: str) -> pd.DataFrame:
    """단계의 남은 공시(최근 것부터). 이미 받은 것·'원문 없음' 표식은 `pending` 이 뺀다."""
    if frame.empty:
        return frame
    if phase == "events":
        todo = docs.pending(frame, doc_types=DEFAULT_TYPES, titles=DEFAULT_TITLES)
        return todo[~todo["title"].map(is_periodic)]
    todo = docs.pending(frame)
    return todo[todo["title"].map(is_periodic)]


def read_failures(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    for line in path.read_text().splitlines():
        try:
            doc_id = str(json.loads(line)["doc_id"])
        except (ValueError, KeyError):
            continue
        counts[doc_id] = counts.get(doc_id, 0) + 1
    return counts


def regular_collector_running(own_pid: int | None = None) -> bool:
    """정규 원문 수집기가 도는가. /proc 를 직접 본다 — pgrep 은 자기 셸 명령줄에 걸린다."""
    own = own_pid if own_pid is not None else os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        if REGULAR_COLLECTOR in cmdline and "python" in cmdline:
            return True
    return False


@dataclass
class Night:
    """한 회차의 셈. rc 와 진행률 줄이 여기서 나온다."""

    got: int = 0
    empty: int = 0
    failed: int = 0
    listed_days: int = 0
    listed_rows: int = 0
    stop: str = ""             # quota | deadline | blocked | done
    blocked: str = ""
    by_phase: dict[str, int] = field(default_factory=dict)


@dataclass
class Backfill:
    store: Store
    source: Any
    clock: Clock
    ledger: QuotaLedger
    stop_at: datetime
    text_root: Path = docs.TEXT_ROOT
    failures_path: Path = FAILURES_FILE
    prior_list_done: Path = PRIOR_LIST_DONE
    phases: tuple[str, ...] = PHASES
    #: 남은 밤 어림의 분모 — 설정의 밤 몫. `--cap` 으로 낮춘 시험 회차가 "9,968밤" 을 찍지 않게.
    nightly: int = 0
    sleep: Callable[[float], None] = time_module.sleep
    busy: Callable[[], bool] = regular_collector_running
    log: Callable[[str], None] = print
    night: Night = field(default_factory=Night)

    # -- 멈춤 조건 -----------------------------------------------------------------

    def _should_stop(self) -> bool:
        if self.ledger.left <= 0:
            self.night.stop = "quota"
            return True
        if self.clock.now() >= self.stop_at:
            self.night.stop = "deadline"
            return True
        return False

    def _wait_for_regular(self) -> None:
        """정규 수집기가 끝날 때까지 기다린다. 마감 시각이 오면 그만 기다린다."""
        announced = False
        while self.busy():
            if self.clock.now() >= self.stop_at:
                return
            if not announced:
                self.log(f"{self._hhmm()} 정규 원문 수집기가 돈다 — 끝날 때까지 기다린다")
                announced = True
            self.sleep(30)

    def _nightly(self) -> int:
        return max(self.nightly or self.ledger.cap, 1)

    def _hhmm(self) -> str:
        return f"{self.clock.now().astimezone(KST):%H:%M}"

    # -- 원문 ----------------------------------------------------------------------

    def texts(self, phase: str, todo: pd.DataFrame, *, remaining: Callable[[], int]) -> None:
        failures = read_failures(self.failures_path)
        rows: list[dict[str, Any]] = []
        batch = 0
        for record in todo.to_dict(orient="records"):
            doc_id = str(record["doc_id"])
            if failures.get(doc_id, 0) >= MAX_FAILURES:
                continue
            if self._should_stop():
                break
            self._wait_for_regular()
            if self._should_stop():
                break
            try:
                content = self.source.document(doc_id)
            except QuotaReached:
                self.night.stop = "quota"
                break
            except DartUnavailable as error:
                self.night.stop = "blocked"
                self.night.blocked = str(error)[:200]
                break
            now = self.clock.now()
            text = ""
            if content:
                try:
                    text = docs.extract(content)
                except (ValueError, OSError) as error:
                    self._record_failure(doc_id, phase, error)
                    continue
            if not text:
                self.night.empty += 1
                rows.append(docs.revision_row(record, raw_path=docs.NO_TEXT, observed_at=now))
            else:
                path = docs.text_path(doc_id, pd.Timestamp(record["valid_from"]).to_pydatetime(), root=self.text_root)
                docs.save_text(text, path)
                rows.append(docs.revision_row(record, raw_path=str(path.as_posix()), observed_at=now))
                self.night.got += 1
                self.night.by_phase[phase] = self.night.by_phase.get(phase, 0) + 1
            if len(rows) >= FLUSH_EVERY:
                batch += 1
                self._flush(rows, phase, batch, remaining=remaining())
                rows = []
            self.sleep(PAUSE_SEC)
        if rows:
            batch += 1
            self._flush(rows, phase, batch, remaining=remaining())

    def _record_failure(self, doc_id: str, phase: str, error: Exception) -> None:
        self.night.failed += 1
        self.failures_path.parent.mkdir(parents=True, exist_ok=True)
        with self.failures_path.open("a") as handle:
            handle.write(json.dumps({"doc_id": doc_id, "phase": phase, "at": self.clock.now().isoformat(),
                                     "error": f"{type(error).__name__}: {error}"[:300]}, ensure_ascii=False) + "\n")
        self.log(f"  {doc_id} 원문 파싱 실패 ({type(error).__name__}) — 장부에 적고 건너뛴다")

    def _flush(self, rows: list[dict[str, Any]], phase: str, batch: int, *, remaining: int) -> None:
        moment = self.clock.now()
        # 첫 접수번호를 붙인다 — 한 공시는 한 번만 받히므로(받으면 pending 에서 빠진다) 같은 초에 두 회차가 돌아도 겹치지 않는다.
        self.store.append(docs.DOCUMENTS, rows, ingest_run_id=f"dart-docs-backfill-{moment:%Y%m%dT%H%M%S}-{phase}-{batch}-{rows[0]['doc_id']}",
                          source=docs.SOURCE)
        cap = self.ledger.cap
        self.log(f"{self._hhmm()} [{phase}] 적재 {len(rows)} · 이번 밤 받음 {self.night.got:,} 없음 {self.night.empty} "
                 f"실패 {self.night.failed} · 콜 {self.ledger.used:,}/{cap:,} · 남은 약 {max(remaining, 0):,}건 "
                 f"≈ {math.ceil(max(remaining, 0) / self._nightly())}밤")

    # -- 목록(2020-08 ~ 2021-08) ---------------------------------------------------

    def listing(self, start: date, end: date) -> bool:
        """남은 날짜를 받는다. 한 바퀴를 끝까지 돌았으면 True(표식을 남긴다). 재개 단위는 (날짜, 시장) — 기존 목록 백필과 같다."""
        if self.prior_list_done.exists():
            return True
        now = self.clock.now()
        backfiller = FilingsBackfiller(
            store=self.store, source=self.source,
            policy=FilingPolicy(hour_kst=int(self.store.config("backfill.dart_publication_hour_kst", as_of=now)),
                                clock=self.clock),
            market="KR",
        )
        pending = backfiller.pending(backfiller.plan(start, end))
        if not pending:
            return True
        self.log(f"{self._hhmm()} [prior-list] 남은 (날짜×시장) {len(pending)}개")
        for day, corp_class in pending:
            if self._should_stop():
                return False
            result = backfiller.run_day(day, corp_class)
            if result.error:
                if self.ledger.left <= 0:
                    self.night.stop = "quota"   # 페이지 중간에 몫이 끝났다 — 매니페스트가 없으니 다음 밤 다시 받는다
                else:
                    self.night.stop = "blocked"
                    self.night.blocked = result.error[:200]
                return False
            self.night.listed_days += 1
            self.night.listed_rows += result.rows
            if self.night.listed_days % 50 == 0:
                self.log(f"{self._hhmm()} [prior-list] {self.night.listed_days}/{len(pending)} · 공시 {self.night.listed_rows:,}행 "
                         f"· 콜 {self.ledger.used:,}/{self.ledger.cap:,}")
            self.sleep(PAUSE_SEC)
        self.log(f"{self._hhmm()} [prior-list] 끝 · 날짜 {self.night.listed_days} · 공시 {self.night.listed_rows:,}행")
        self.prior_list_done.parent.mkdir(parents=True, exist_ok=True)
        self.prior_list_done.write_text(f"{self.clock.now().isoformat()} {start}~{end}\n")
        return True

    # -- 한 회차 -------------------------------------------------------------------

    def run(self, *, status_only: bool = False) -> int:
        now = self.clock.now()
        top = ceiling(now)
        main = load_frame(self.store, now, start=FLOOR, end=top)
        prior = load_frame(self.store, now, start=PRIOR_FLOOR, end=FLOOR)
        todo = {"events": todo_for(main, "events"), "periodic": todo_for(main, "periodic"),
                "prior-periodic": todo_for(prior, "prior-periodic")}
        del main
        prior_listed = self.prior_list_done.exists() or "prior-list" not in self.phases
        del prior
        todo = {k: v for k, v in todo.items() if k in self.phases}
        counts = {k: len(v) for k, v in todo.items()}
        left = dict(counts)

        def remaining() -> int:
            return sum(left.values()) - self.night.got - self.night.empty

        total = sum(counts.values())
        spent_before = self.ledger.left <= 0
        self.log(f"=== {now.astimezone(KST):%F %T} 공시 원문 백필 · 범위 {PRIOR_FLOOR}~{top}(정규 창 앞) · 몫 {self.ledger.cap:,}"
                 f"(오늘 이미 {self.ledger.used:,}) · 마감 {self.stop_at.astimezone(KST):%H:%M}")
        self.log("  남은: " + " · ".join(f"{k} {v:,}" for k, v in counts.items())
                 + ("" if prior_listed else " · prior-list 목록 미수집(그 1년 정기보고서 약 1만 건은 목록 뒤 확정)"))
        if status_only:
            return 0
        had_work = total > 0 or not prior_listed

        for phase in self.phases:
            if self._should_stop():
                break
            if phase == "prior-list":
                if not self.listing(PRIOR_FLOOR, PRIOR_LIST_END):
                    break
                # 목록이 늘었으니 그 1년의 정기보고서를 다시 고른다.
                prior = load_frame(self.store, self.clock.now(), start=PRIOR_FLOOR, end=FLOOR)
                todo["prior-periodic"] = todo_for(prior, "prior-periodic")
                left["prior-periodic"] = len(todo["prior-periodic"])
                del prior
                continue
            if phase not in todo or todo[phase].empty:
                continue
            self.texts(phase, todo[phase], remaining=remaining)
            if self.night.stop:
                break

        n = self.night
        if not n.stop and remaining() <= 0:
            n.stop = "done"
        self.log(f"--- {self.clock.now().astimezone(KST):%F %T} 끝 · 받음 {n.got:,} · 원문 없음 {n.empty} · 파싱 실패 {n.failed} · "
                 f"목록 {n.listed_days}일 {n.listed_rows:,}행 · 콜 {self.ledger.used:,}/{self.ledger.cap:,} · 멈춘 이유 {n.stop or '-'} · "
                 f"남은 약 {max(remaining(), 0):,}건 ≈ {math.ceil(max(remaining(), 0) / self._nightly())}밤")
        if n.stop == "blocked":
            self.log(f"DART 가 막았다: {n.blocked}")
            return 3
        progressed = n.got + n.empty + n.listed_days > 0
        if had_work and not progressed:
            if spent_before:
                self.log("오늘 몫은 이미 다 썼다 — 다음 밤에 잇는다")
                return 0
            self.log("받을 것이 있었는데 한 건도 못 받았다")
            return 1
        return 0


def parse_stop_at(text: str, now: datetime) -> datetime:
    """'07:30' → 오늘(KST) 그 시각. 이미 지났으면 지금(곧바로 멈춤)."""
    hour, minute = (int(x) for x in text.split(":"))
    local = now.astimezone(KST)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--stop-at", default="07:30", help="KST 이 시각에 멈춘다")
    parser.add_argument("--status", action="store_true", help="남은 양만 찍는다(DART 호출 0)")
    parser.add_argument("--cap", type=int, default=0, help="오늘 몫을 이 값으로 **낮춘다**(시험용). config 몫보다 크게는 못 한다")
    parser.add_argument("--phases", default=",".join(PHASES), help=f"쉼표로, 이 차례대로. 기본 {','.join(PHASES)}")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    store = Store(root=Path(args.root))
    now = clock.now()
    cap = int(store.config("collectors.dart_text_backfill_daily_cap", as_of=now))
    limit = int(store.config("collectors.dart_daily_limit", as_of=now))
    if cap >= limit:
        print(f"백필 몫 {cap} 이 키 일 한도 {limit} 이상이다 — 정규 수집 몫이 없다", file=sys.stderr)
        return 2
    nightly = cap
    if args.cap > 0:
        cap = min(cap, args.cap)
    ledger = QuotaLedger(Path(args.root) / QUOTA_DIR.relative_to("data"), now.astimezone(KST).date(), cap)
    source = MeteredDartSource(ledger=ledger)
    if not source.api_key and not args.status:
        print("OPENDART_API_KEY 가 없다", file=sys.stderr)
        return 2
    job = Backfill(store=store, source=source, clock=clock, ledger=ledger,
                   stop_at=parse_stop_at(args.stop_at, now),
                   failures_path=Path(args.root) / FAILURES_FILE.relative_to("data"),
                   prior_list_done=Path(args.root) / PRIOR_LIST_DONE.relative_to("data"),
                   nightly=nightly,
                   phases=tuple(p.strip() for p in args.phases.split(",") if p.strip() in PHASES),
                   log=lambda line: print(line, flush=True))
    try:
        return job.run(status_only=args.status)
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
