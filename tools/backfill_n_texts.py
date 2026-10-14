#!/usr/bin/env python
"""새 정보 N1·N3·N7 의 **원문이 없던 공시** 백필 — 담보·전환가액 조정·신탁 해지결과·최대주주 소유 변동(2026-10-14).

    .venv/bin/python tools/backfill_n_texts.py --status                 # 남은 양만(호출 0)
    .venv/bin/python tools/backfill_n_texts.py --cap 3000               # 우선순위대로 받는다(15:50 뒤·22:20 전)
    .venv/bin/python tools/backfill_n_texts.py --cap 20 --dry-run       # 받아만 보고 적재하지 않는다

정규 원문 수집(`collect_filing_texts.py`)의 분류·제목 정규식에 이 공시들이 없어 원문이 0% 였다(10/14 실측: 담보 1,923·해제 206·
전환가액 조정 4,143·행사가액 조정 352·신탁 해지결과 1,612·최대주주등소유주식변동 11,800 중 216).
**규약은 정규 수집과 글자 하나까지 같다** — 원문은 `data/_docs/dart/<연>/<월>/<접수번호>.txt.gz`, 창고에는 `documents` 정정본으로
경로만(observed_at = 받은 시각, `dart_documents.revision_row`). 이어받기는 `raw_path` 가 찬 행이 `pending` 에서 빠지는 것으로 된다.

**일 한도**: 키 한도 2만 중 원문 백필(00:45)이 1.5만을 쓴다. 이 도구는 날짜별 장부 `data/_dart_quota/<KST 날짜>.n-texts.json` 으로
자기 몫(`--cap`, 기본 3,000)만 세고, DART 가 막으면(020 등) 받은 만큼 적재하고 멈춘다(rc 3).
**실행 창**: 평일 08:20~15:50(장중 수집과 같은 표에 쓰지 않는다)·매일 22:20~00:45(원문 백필·밤 작업) 에는 거부한다(rc 4).

rc: 0 = 받았거나 할 일 없음 · 1 = 대상이 있는데 한 건도 못 받음 · 2 = 키 없음 · 3 = DART 가 막음 · 4 = 금지 창.
"""

from __future__ import annotations

import argparse
import json
import sys
import time as time_module
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
QUOTA_DIR = Path("data/_dart_quota")
PAUSE_SEC = 0.12
FLUSH_EVERY = 200
#: (이름, 공백 뗀 제목 정규식) — **우선순위 순**. 신탁 해지결과(N7) → 담보(N3) → 조정(N1) → 소유 변동(N3, 가장 많다).
#: 신탁 해지결과를 맨 앞으로(2026-10-14 리드): N7 이 원문 0% 로 구워져 판정 보류 — 남은 약 1,620건을 10/22 전에 먼저 받는다.
TARGETS: tuple[tuple[str, str], ...] = (
    ("bb_trust_end", r"신탁계약해지결과보고서"),
    ("pledge", r"최대주주변경을수반하는주식담보제공계약(?:체결|해제)"),
    ("refix", r"전환가액의조정|행사가액의조정"),
    ("holder_change", r"최대주주등소유주식변동신고서"),
)
#: 판정·특징에 쓰는 범위 — 확장 패널(2021-08)부터.
FLOOR = pd.Timestamp("2021-08-01", tz="UTC")


def blocked(now_kst) -> str | None:  # type: ignore[no-untyped-def]
    t = now_kst.time()
    if t >= time(22, 20) or t < time(0, 45):
        return "22:20~00:45 금지 창"
    if now_kst.weekday() < 5 and time(8, 20) <= t < time(15, 50):
        return "평일 08:20~15:50 금지 창(장중 수집과 겹침)"
    return None


def _quota_path(now_kst) -> Path:  # type: ignore[no-untyped-def]
    return QUOTA_DIR / f"{now_kst:%Y-%m-%d}.n-texts.json"


def _used(now_kst) -> int:  # type: ignore[no-untyped-def]
    p = _quota_path(now_kst)
    return int(json.loads(p.read_text()).get("n_texts", 0)) if p.exists() else 0


def _save_used(now_kst, used: int, cap: int) -> None:  # type: ignore[no-untyped-def]
    QUOTA_DIR.mkdir(parents=True, exist_ok=True)
    _quota_path(now_kst).write_text(json.dumps({"n_texts": used, "cap": cap}))


def todo(store: Store, now) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=(now - FLOOR.to_pydatetime()).days + 1, market="KR")
    frame = frame[frame["entity_id"].astype(str).str.startswith("KR:")]
    parts = []
    for rank, (name, pattern) in enumerate(TARGETS):
        p = docs.pending(frame, doc_types=(), titles=pattern)
        p = p[~p["title"].astype(str).str.replace(" ", "").str.contains("자회사의주요경영사항")]
        parts.append(p.assign(_target=name, _rank=rank))
    out = pd.concat(parts, ignore_index=True).drop_duplicates("doc_id")
    return out.sort_values(["_rank", "valid_from"], ascending=[True, False])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="data")
    ap.add_argument("--cap", type=int, default=3000, help="이 도구의 오늘 몫(호출 수)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--text-root", default=str(docs.TEXT_ROOT))
    args = ap.parse_args(argv)
    clock = LiveClock()
    now = clock.now()
    now_kst = now.astimezone(KST)
    store = Store(root=Path(args.root))
    t = todo(store, now)
    print(f"남은 원문 {len(t):,}건 — " + " · ".join(f"{k} {v:,}" for k, v in t["_target"].value_counts().items()), flush=True)
    if args.status or t.empty:
        return 0
    why = blocked(now_kst)
    if why and not args.dry_run:
        print(f"거부 — {why}", file=sys.stderr)
        return 4
    load_env()
    source = DartSource()
    if not source.api_key:
        print("OPENDART_API_KEY 가 없다", file=sys.stderr)
        return 2
    used = _used(now_kst)
    room = max(0, args.cap - used)
    batch = t.head(room).drop(columns=["_target", "_rank"])
    rows: list[dict] = []
    got = empty = calls = flushes = 0
    rc = 0
    run_id = f"n-texts-{now:%Y%m%dT%H%M%S}"
    root = Path(args.text_root)
    for record in batch.to_dict(orient="records"):
        if not args.dry_run and blocked(clock.now().astimezone(KST)):
            print("  금지 창에 들어섰다 — 멈춘다", flush=True)
            break
        doc_id = str(record["doc_id"])
        try:
            content = source.document(doc_id)
            calls += 1
        except DartUnavailable as error:
            print(f"  DART 가 막았다 ({str(error)[:120]}) — 여기서 멈춘다", file=sys.stderr)
            rc = 3
            break
        text = ""
        if content:
            try:
                text = docs.extract(content)
            except (ValueError, OSError) as error:
                print(f"  {doc_id} 원문 파싱 실패 ({error}) — 건너뛴다", file=sys.stderr)
                continue
        if not text:
            empty += 1
            if not args.dry_run:
                rows.append(docs.revision_row(record, raw_path=docs.NO_TEXT, observed_at=clock.now()))
        else:
            got += 1
            if not args.dry_run:
                path = docs.text_path(doc_id, record["valid_from"], root=root)
                docs.save_text(text, path)
                rows.append(docs.revision_row(record, raw_path=str(path.as_posix()), observed_at=clock.now()))
        if rows and len(rows) >= FLUSH_EVERY:
            flushes += 1
            # 적재 단위마다 다른 run id — 같은 id 로 두 번 적재하면 창고가 거부한다(DuplicateIngestRun, 2026-10-14 첫 실행).
            store.append(docs.DOCUMENTS, rows, ingest_run_id=f"{run_id}-p{flushes:03d}", source=docs.SOURCE)
            rows = []
            _save_used(now_kst, used + calls, args.cap)
        time_module.sleep(PAUSE_SEC)
    if rows and not args.dry_run:
        store.append(docs.DOCUMENTS, rows, ingest_run_id=f"{run_id}-p{flushes + 1:03d}", source=docs.SOURCE)
    if not args.dry_run:
        _save_used(now_kst, used + calls, args.cap)
    print(f"호출 {calls} · 원문 {got} · 원문 없음 {empty} · 오늘 누적 {used + calls}/{args.cap}", flush=True)
    if rc:
        return rc
    return 1 if (len(batch) and got == 0 and empty == 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
