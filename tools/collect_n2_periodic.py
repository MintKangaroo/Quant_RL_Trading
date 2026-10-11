#!/usr/bin/env python
"""N2(Lazy Prices) 진단용 **과거 정기보고서 섹션** 수집 — 2016-01-01 ~ 2021-08-16(창고 documents 시작 전날).

    .venv/bin/python tools/collect_n2_periodic.py --status            # 남은 양만(DART 호출 0)
    .venv/bin/python tools/collect_n2_periodic.py --cap 3000 --force  # 시험 회차(TX 백필 완료 확인을 건너뜀)
    scripts/collect_n2_periodic.sh                                    # 밤 회차(크론)

**창고에 넣지 않는다.** documents 시작일(2021-08-17)이 당겨지면 등록된 시행(TX 등)의 연구 캐시(event 이력)가 다시 구울 때
달라진다(scripts/backfill_filing_texts.sh 주석, 리드 10/2). 그래서 진단 조각과 같은 취급으로 창고 밖에 둔다:

- ``data/_diag/n2_periodic/list/<YYYY-MM>.json`` — 그 달 정기공시(``pblntf_ty=A``) 목록 원본(전 페이지를 다 받은 달만 적는다 = 재개 단위)
- ``data/_diag/n2_periodic/sec/<연>/<접수번호>.json.gz`` — **섹션만**: biz(II. 사업의 내용) · risk(그 밖에 투자자 보호) · mdna(이사의 경영진단).
  절단기는 ``tools/diag_n2_lazy_prices.sections`` (창고 원문에 쓰는 것과 같은 함수, ``&cr;`` 를 줄바꿈으로 풀고 자른다)
- ``data/_diag/n2_periodic/index/<회차>-<n>`` (파케이) — 색인: 접수번호·종목코드·제목·접수일·observed_at(접수일 18:00 KST,
  ``backfill.dart_publication_hour_kst`` — 목록 백필 규약과 같다)·fetched_at(받은 시각)·경로·상태·섹션 길이

**원본만** 받는다 — 정정류([기재정정]·[첨부정정]·[첨부추가] 등)는 뺀다(Lazy Prices 는 원본끼리 비교). 최근 것부터 받는다 —
2020-08~2021-08 이 먼저 차야 창고 첫해(2021-08~2022-08) 보고서의 '직전 동종' 이 생긴다.

**일 한도는 TX 원문 백필과 같은 몫을 나눠 쓴다** — 같은 장부(``data/_dart_quota/<KST 날짜>.json`` 의 ``text_backfill``)와 같은 몫
(``collectors.dart_text_backfill_daily_cap`` = 1.5만)을 쓴다. 그래서 둘을 합쳐 하룻밤 1.5만 콜을 못 넘는다. 기본으로는 TX 백필
(``logs/dart-backfill-*.log`` 의 마지막 '멈춘 이유')이 ``done`` 일 때만 돈다(``--force`` 로 건너뜀).

rc: 0 = 받았거나(몫·마감 포함) 할 일 없음·TX 대기 · 1 = 받을 것이 있었는데 0건 · 2 = 키·설정 · 3 = DART 가 막음.
키 값은 어디에도 찍지 않는다.
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import json
import re
import sys
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors.dart_source import DartUnavailable  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill_filing_texts import (  # noqa: E402
    QUOTA_DIR,
    MeteredDartSource,
    QuotaLedger,
    QuotaReached,
    parse_stop_at,
)
from tools.collect_filing_texts import PAUSE_SEC  # noqa: E402
from tools.diag_n2_lazy_prices import EXTRA_ROOT, parse_title, sections  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
START = date(2016, 1, 1)
#: 창고 documents(국장 목록)가 2021-08-17 부터다 — 그 전날까지 받아 틈을 남기지 않는다.
END = date(2021, 8, 16)
FLUSH_EVERY = 200
MAX_FAILURES = 3
TX_LOG_GLOB = "dart-backfill-*.log"
_STOP_REASON = re.compile(r"멈춘 이유 (\S+)")


def months(start: date, end: date) -> list[tuple[date, date]]:
    """[start, end] 를 달 단위 창으로, **최근 달부터**. list.json 은 회사 미지정 시 3개월 창까지 받는다."""
    out = []
    y, m = end.year, end.month
    while (y, m) >= (start.year, start.month):
        a = max(date(y, m, 1), start)
        b = min(date(y, m, calendar.monthrange(y, m)[1]), end)
        out.append((a, b))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def tx_backfill_done(log_dir: Path) -> tuple[bool, str]:
    """TX 원문 백필의 가장 최근 '멈춘 이유'. done 이면 그 백필은 할 일이 없다."""
    logs = sorted(log_dir.glob(TX_LOG_GLOB))
    for path in reversed(logs):
        reasons = _STOP_REASON.findall(path.read_text(errors="ignore"))
        if reasons:
            return reasons[-1] == "done", reasons[-1]
    return False, "로그 없음"


def is_target(row: dict[str, Any]) -> bool:
    """정기보고서 **원본**이고 종목코드가 있는 것."""
    parsed = parse_title(str(row.get("report_nm") or ""))
    return parsed is not None and not parsed[3] and bool(re.fullmatch(r"\d{6}", str(row.get("stock_code") or "").strip()))


def section_path(root: Path, rcept_no: str, rcept_dt: str) -> Path:
    return root / "sec" / rcept_dt[:4] / f"{rcept_no}.json.gz"


def save_sections(sec: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".part")
    with gzip.open(staging, "wt", encoding="utf-8") as handle:  # invariant-allow: data-access — 진단 수집분(창고 밖 원문 조각)
        json.dump(sec, handle, ensure_ascii=False)
    staging.replace(path)


@dataclass
class Collector:
    source: Any
    clock: Clock
    ledger: QuotaLedger
    stop_at: datetime
    hour_kst: int
    root: Path = EXTRA_ROOT
    start: date = START
    end: date = END
    sleep: Callable[[float], None] = time_module.sleep
    log: Callable[[str], None] = print
    stop: str = ""
    blocked: str = ""
    got: int = 0
    empty: int = 0
    nosec: int = 0
    failed: int = 0
    listed: int = 0
    run: str = ""
    batch: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)

    # -- 공통 -----------------------------------------------------------------

    def _should_stop(self) -> bool:
        if self.ledger.left <= 0:
            self.stop = "quota"
            return True
        if self.clock.now() >= self.stop_at:
            self.stop = "deadline"
            return True
        return False

    def _hhmm(self) -> str:
        return f"{self.clock.now().astimezone(KST):%H:%M}"

    # -- 목록 -----------------------------------------------------------------

    def list_path(self, a: date) -> Path:
        return self.root / "list" / f"{a:%Y-%m}.json"

    def list_month(self, a: date, b: date) -> list[dict[str, Any]] | None:
        """한 달 정기공시 목록(전 페이지). 중간에 멈추면 None — 다음 회차가 그 달을 처음부터 다시 받는다."""
        collected: list[dict[str, Any]] = []
        page = 1
        while True:
            if self._should_stop():
                return None
            try:
                response = self.source._call("/list.json", {
                    "bgn_de": a.strftime("%Y%m%d"), "end_de": b.strftime("%Y%m%d"), "pblntf_ty": "A",
                    "last_reprt_at": "N", "page_no": str(page), "page_count": "100"})
            except QuotaReached:
                self.stop = "quota"
                return None
            except DartUnavailable as error:
                self.stop, self.blocked = "blocked", str(error)[:200]
                return None
            try:
                payload = response.json() if response.status_code == 200 else {}
            except ValueError:
                payload = {"status": "JSON 아님"}
            status = str(payload.get("status"))
            if status == "013":
                break
            if status != "000":
                self.stop, self.blocked = "blocked", f"list.json HTTP {response.status_code} status={status} msg={payload.get('message')}"[:200]
                return None
            collected.extend(payload.get("list") or [])
            if page >= int(payload.get("total_page") or 1):
                break
            page += 1
            self.sleep(PAUSE_SEC)
        return collected

    def listing(self) -> bool:
        """남은 달을 받는다. 다 받았으면 True."""
        for a, b in months(self.start, self.end):
            path = self.list_path(a)
            if path.exists():
                continue
            rows = self.list_month(a, b)
            if rows is None:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            staging = path.with_suffix(".part")
            staging.write_text(json.dumps({"bgn": a.isoformat(), "end": b.isoformat(), "fetched_at": self.clock.now().isoformat(),
                                           "list": rows}, ensure_ascii=False))
            staging.replace(path)
            self.listed += 1
            self.log(f"{self._hhmm()} [목록] {a:%Y-%m} {len(rows):,}건 · 콜 {self.ledger.used:,}/{self.ledger.cap:,}")
            self.sleep(PAUSE_SEC)
        return True

    def targets(self) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "list").glob("*.json")):
            rows.extend(r for r in json.loads(path.read_text())["list"] if is_target(r))
        if not rows:
            return pd.DataFrame(columns=["rcept_no", "rcept_dt"])
        t = pd.DataFrame(rows).drop_duplicates("rcept_no")
        t = t[(t["rcept_dt"] >= self.start.strftime("%Y%m%d")) & (t["rcept_dt"] <= self.end.strftime("%Y%m%d"))]
        done = self.done()
        return t[~t["rcept_no"].astype(str).isin(done)].sort_values(["rcept_dt", "rcept_no"], ascending=False)

    # -- 색인 -----------------------------------------------------------------

    def index_frame(self) -> pd.DataFrame:
        parts = sorted((self.root / "index").glob("*.parquet"))  # invariant-allow: data-access — 진단 수집분 색인(창고 밖)
        if not parts:
            return pd.DataFrame(columns=["rcept_no", "status"])
        return pd.concat([pd.read_parquet(p, columns=["rcept_no", "status"]) for p in parts], ignore_index=True)  # invariant-allow: data-access — 진단 수집분 색인

    def done(self) -> set[str]:
        idx = self.index_frame()
        fails = idx[idx["status"] == "failed"]["rcept_no"].value_counts()
        settled = set(idx.loc[idx["status"] != "failed", "rcept_no"].astype(str))
        return settled | set(fails[fails >= MAX_FAILURES].index.astype(str))

    def flush(self) -> None:
        if not self.rows:
            return
        self.batch += 1
        path = self.root / "index" / f"{self.run}-{self.batch:04d}.parquet"  # invariant-allow: data-access — 진단 수집분 색인
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_suffix(".part")
        pd.DataFrame(self.rows).to_parquet(staging, index=False)  # invariant-allow: data-access — 진단 수집분 색인(창고 밖)
        staging.replace(path)
        self.rows = []

    # -- 원문 → 섹션 ----------------------------------------------------------

    def observed_at(self, rcept_dt: str) -> datetime:
        day = datetime.strptime(rcept_dt, "%Y%m%d").date()
        return datetime.combine(day, time(self.hour_kst), KST).astimezone(UTC)

    def texts(self, todo: pd.DataFrame, total: int) -> None:
        for rec in todo.to_dict(orient="records"):
            if self._should_stop():
                break
            rno, rdt = str(rec["rcept_no"]), str(rec["rcept_dt"])
            try:
                content = self.source.document(rno)
            except QuotaReached:
                self.stop = "quota"
                break
            except DartUnavailable as error:
                self.stop, self.blocked = "blocked", str(error)[:200]
                break
            row = {"rcept_no": rno, "corp_code": str(rec.get("corp_code") or ""), "stock_code": str(rec["stock_code"]).strip(),
                   "corp_name": str(rec.get("corp_name") or ""), "corp_cls": str(rec.get("corp_cls") or ""),
                   "report_nm": str(rec.get("report_nm") or ""), "rcept_dt": f"{rdt[:4]}-{rdt[4:6]}-{rdt[6:]}",
                   "observed_at": self.observed_at(rdt).isoformat(), "fetched_at": self.clock.now().isoformat(),
                   "path": "", "status": "", "len_biz": 0, "len_risk": 0, "len_mdna": 0, "error": ""}
            if not content:
                row["status"] = "no_text"
                self.empty += 1
            else:
                try:
                    sec = sections(docs.extract(content))
                except (ValueError, OSError) as error:
                    row["status"], row["error"] = "failed", f"{type(error).__name__}: {error}"[:200]
                    self.failed += 1
                    sec = None
                if sec is not None:
                    row.update({f"len_{k}": len(v) for k, v in sec.items()})
                    if any(sec.values()):
                        path = section_path(self.root, rno, rdt)
                        save_sections(sec, path)
                        row["path"] = path.as_posix()
                    row["status"] = "ok" if sec["biz"] else "nosec"
                    self.got += row["status"] == "ok"
                    self.nosec += row["status"] == "nosec"
            self.rows.append(row)
            if len(self.rows) >= FLUSH_EVERY:
                self.flush()
                n = self.got + self.empty + self.nosec + self.failed
                self.log(f"{self._hhmm()} [섹션] 받음 {self.got:,} 섹션없음 {self.nosec} 원문없음 {self.empty} 실패 {self.failed} · "
                         f"콜 {self.ledger.used:,}/{self.ledger.cap:,} · 남은 약 {max(total - n, 0):,}건 (접수 {row['rcept_dt']})")
            self.sleep(PAUSE_SEC)
        self.flush()

    def go(self, *, status_only: bool = False) -> int:
        now = self.clock.now()
        listed = sum(1 for a, _ in months(self.start, self.end) if self.list_path(a).exists())
        n_months = len(months(self.start, self.end))
        todo = self.targets()
        self.log(f"=== {now.astimezone(KST):%F %T} N2 정기보고서 섹션 수집 · {self.start}~{self.end} · 몫 {self.ledger.cap:,}"
                 f"(오늘 이미 {self.ledger.used:,}, TX 백필과 공유) · 마감 {self.stop_at.astimezone(KST):%H:%M}")
        self.log(f"  목록 {listed}/{n_months}달 · 받을 원본(받은 달 기준) {len(todo):,}건")
        if status_only:
            return 0
        had_work = listed < n_months or len(todo) > 0
        if self.listing():
            todo = self.targets()
            self.texts(todo, len(todo))
        if not self.stop and not self.targets().shape[0]:
            self.stop = "done"
        self.log(f"--- {self.clock.now().astimezone(KST):%F %T} 끝 · 목록 {self.listed}달 · 받음 {self.got:,} · 섹션없음 {self.nosec} · "
                 f"원문없음 {self.empty} · 실패 {self.failed} · 콜 {self.ledger.used:,}/{self.ledger.cap:,} · 멈춘 이유 {self.stop or '-'}")
        if self.stop == "blocked":
            self.log(f"DART 가 막았다: {self.blocked}")
            return 3
        if had_work and self.got + self.empty + self.nosec + self.failed + self.listed == 0:
            self.log("받을 것이 있었는데 한 건도 못 받았다" if self.stop != "quota" else "오늘 몫은 이미 다 썼다 — 다음 밤에 잇는다")
            return 0 if self.stop == "quota" else 1
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop-at", default="07:30", help="KST 이 시각에 멈춘다")
    ap.add_argument("--status", action="store_true", help="남은 양만(DART 호출 0)")
    ap.add_argument("--cap", type=int, default=0, help="오늘 몫을 이 값으로 **낮춘다**(장부는 TX 백필과 공유 — 이미 쓴 콜 포함)")
    ap.add_argument("--force", action="store_true", help="TX 백필 완료(done) 확인을 건너뛴다")
    ap.add_argument("--start", default=START.isoformat())
    ap.add_argument("--end", default=END.isoformat())
    args = ap.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    store = Store(root=Path("data"))
    cap = int(store.config("collectors.dart_text_backfill_daily_cap", as_of=now))
    limit = int(store.config("collectors.dart_daily_limit", as_of=now))
    hour = int(store.config("backfill.dart_publication_hour_kst", as_of=now))
    if cap >= limit:
        print(f"백필 몫 {cap} 이 키 일 한도 {limit} 이상이다", file=sys.stderr)
        return 2
    if args.cap > 0:
        cap = min(cap, args.cap)
    if not args.status and not args.force:
        ok, reason = tx_backfill_done(REPO_ROOT / "logs")
        if not ok:
            print(f"{now.astimezone(KST):%F %T} TX 원문 백필이 아직 끝나지 않았다(마지막 멈춘 이유 {reason}) — 이번 회차는 건너뛴다")
            return 0
    ledger = QuotaLedger(QUOTA_DIR, now.astimezone(KST).date(), cap)
    source = MeteredDartSource(ledger=ledger)
    if not source.api_key and not args.status:
        print("OPENDART_API_KEY 가 없다", file=sys.stderr)
        return 2
    job = Collector(source=source, clock=clock, ledger=ledger, stop_at=parse_stop_at(args.stop_at, now), hour_kst=hour,
                    start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
                    run=f"{now.astimezone(KST):%Y%m%dT%H%M%S}", log=lambda line: print(line, flush=True))
    try:
        return job.go(status_only=args.status)
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
