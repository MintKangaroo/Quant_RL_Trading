"""공시 원문 수집 — `documents.raw_path` 를 채운다 (재료 대장 #6 의 선행).

    .venv/bin/python tools/collect_filing_texts.py --limit 200
    .venv/bin/python tools/collect_filing_texts.py --limit 500 --doc-type distress,dilution
    .venv/bin/python tools/collect_filing_texts.py --limit 50 --dry-run   # 받기만, 적재 안 함

DART 일 한도는 20,000 콜이다. **한 번에 다 받지 않는다** — 최근 것부터 끊어
받고, 이미 받은 것은 `raw_path` 가 차 있어 저절로 빠진다. 원문은 파일
(`data/_docs/dart/<연>/<월>/`)로 두고 창고에는 경로만 정정본으로 적는다.

rc: 0 = 받았거나 받을 것이 없다 · 1 = 한 건도 못 받았는데 받을 것은 있었다
(조용한 실패 금지). 중간에 DART 가 막으면 **거기서 멈추고** 받은 만큼 적재한다.
"""

from __future__ import annotations

import argparse
import sys
import time as time_module
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

#: 호출 사이의 예의. DART 는 초당 제한을 문서로 밝히지 않는다 — 목록 수집기의
#: 페이지 간격과 같은 값을 쓴다.
PAUSE_SEC = 0.2

#: 본문을 볼 값이 큰 것부터. `other` 는 34만 중 22만이라 기본에서 뺀다 —
#: 임베딩의 첫 표본은 "무너질 종목" 쪽에서 만든다(재료 대장 추천 기준).
DEFAULT_TYPES = ("distress", "dilution", "earnings", "contract", "buyback", "split")


def collect(
    store: Store,
    source: DartSource,
    clock: Clock,
    *,
    limit: int,
    doc_types: tuple[str, ...] | None,
    lookback_days: int,
    root: Path,
    dry_run: bool = False,
    sleep=time_module.sleep,
) -> tuple[int, int, int]:
    """(받은 건수, 원문 없음, 대상 건수). 적재는 마지막에 한 번.

    받은 건수는 **적재가 아니라 원문을 실제로 뽑은 수**다. `--dry-run` 은 적재를
    건너뛸 뿐 받기는 하므로, 적재 수로 세면 잘 받고도 "한 건도 못 받았다" 가 된다.
    """
    now = clock.now()
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=lookback_days)
    todo = docs.pending(frame, limit=limit, doc_types=doc_types)
    if todo.empty:
        return 0, 0, 0

    rows: list[dict] = []
    got = 0
    empty = 0
    for index, record in enumerate(todo.to_dict(orient="records")):
        doc_id = str(record["doc_id"])
        try:
            content = source.document(doc_id)
        except DartUnavailable as error:
            # 한도 초과·점검이다. 여기서 멈춘다 — 계속 두드리면 그날 남은
            # 호출을 전부 헛되이 쓰고, 다음 회차까지 못 받는다.
            print(f"  DART 가 막았다 ({error}) — 여기서 멈춘다", file=sys.stderr)
            break
        if not content:
            empty += 1
            continue
        try:
            text = docs.extract(content)
        except (ValueError, OSError) as error:
            print(f"  {doc_id} 원문 파싱 실패 ({error}) — 건너뛴다", file=sys.stderr)
            continue
        if not text:
            empty += 1
            continue
        got += 1
        valid_from = record["valid_from"]
        path = docs.text_path(doc_id, valid_from, root=root)
        if not dry_run:
            docs.save_text(text, path)
            rows.append(
                docs.revision_row(
                    record, raw_path=str(path.as_posix()), observed_at=clock.now()
                )
            )
        if index + 1 < len(todo):
            sleep(PAUSE_SEC)

    if rows and not dry_run:
        store.append(
            docs.DOCUMENTS, rows, ingest_run_id=docs.run_id(now, limit=limit), source=docs.SOURCE
        )
    return got, empty, len(todo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--limit", type=int, default=200, help="한 번에 받을 건수 (0 = 전부)")
    parser.add_argument(
        "--doc-type",
        default=",".join(DEFAULT_TYPES),
        help=f"쉼표로. 기본 {','.join(DEFAULT_TYPES)} · 'all' 이면 전부",
    )
    parser.add_argument("--lookback", type=int, default=400, help="창고 조회 창(일)")
    parser.add_argument("--text-root", default=str(docs.TEXT_ROOT))
    parser.add_argument("--dry-run", action="store_true", help="받아만 보고 적재하지 않는다")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    store = Store(root=Path(args.root))
    types = None if args.doc_type == "all" else tuple(
        t.strip() for t in args.doc_type.split(",") if t.strip()
    )
    saved, empty, total = collect(
        store,
        DartSource(),
        clock,
        limit=args.limit,
        doc_types=types,
        lookback_days=args.lookback,
        root=Path(args.text_root),
        dry_run=args.dry_run,
    )
    if total == 0:
        print("원문이 없는 공시가 없다 — 할 일 없음")
        return 0
    kept = "받기만 함(--dry-run)" if args.dry_run else "적재"
    print(f"원문 {saved}건 {kept} · 원문 없음 {empty}건 · 대상 {total}건")
    if saved == 0:
        print("대상은 있었는데 한 건도 못 받았다", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
