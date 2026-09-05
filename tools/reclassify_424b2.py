#!/usr/bin/env python
"""424B2 를 dilution 에서 other 로 옮기는 **정정본**을 쓴다.

    uv run python tools/reclassify_424b2.py --dry-run
    uv run python tools/reclassify_424b2.py

## 왜

`collectors/edgar_filings.py` 의 `FORM_TYPES` 를 고쳤지만, 이미 적재된 행은
`doc_type="dilution"` 인 채로 남는다. 창고는 append-only 라 UPDATE 가 없다
(불변식 4) — 같은 자연키에 **revision 을 올린 새 행**을 얹는다. 조회는
자연키마다 최신 revision 을 고르므로 그것으로 정정이 끝난다.

근거는 `edgar_filings.FORM_TYPES` 의 주석에 있다. 요약하면 424B2 는 은행이
선반등록 하에 구조화 노트를 찍을 때 내는 폼이고 보통주 주주는 희석되지
않는다. 매매 대상에 떨어지는 비율이 19.3% 로 다른 doc_type(79.7%)과 유별나게
다르다.

## observed_at 을 그대로 둔다 — 이 도구의 핵심 판단

정정본에 **원래 행의 `observed_at` 을 그대로 옮긴다.** 지금 시각을 넣지
않는다.

`doc_type` 은 `title` 의 폼 이름에서 **유도되는 값**이다. EDGAR 가 준
데이터는 처음부터 "424B2" 라고 말하고 있었고, 우리 분류 코드가 그것을
잘못 읽었을 뿐이다. 새로 알게 된 사실이 아니라 **이미 갖고 있던 데이터를
잘못 계산한 것**이다.

여기에 지금 시각을 넣으면 두 가지가 망가진다. 첫째, 과거 as_of 로 도는
백테스트·IC 는 여전히 틀린 분류를 본다 — 고치는 목적이 사라진다. 둘째,
코드를 고칠 때마다 "우리가 나중에 알게 됐다" 는 가짜 사건이 창고에 쌓여,
백테스트가 실제보다 나쁜 피처로 영영 돌게 된다 (불변식 5).

**반대로, 원본이 늦게 준 데이터라면 절대 이렇게 하면 안 된다.** 그건
진짜로 그때는 몰랐던 것이고, 앞당기면 미래를 보는 것이 된다. 이 도구가
이렇게 해도 되는 이유는 **입력이 아니라 계산이 틀렸기 때문**이다.

## 두 번 돌려도 안전하다

`ingest_run_id` 가 연도마다 결정론적이라 같은 연도를 두 번 넣으려 하면
창고가 거부한다. 그리고 이미 정정된 행(`doc_type != "dilution"`)은 애초에
대상에서 빠진다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.edgar_filings import OTHER  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.errors import DuplicateIngestRun  # noqa: E402

TABLE = "documents"
FORM = "424B2"
STALE_TYPE = "dilution"

#: 창고를 읽어 오는 컬럼. **좁혀서 읽는다** — 40만 행에서 안 쓰는 문자열
#: 컬럼까지 퍼오면 그것만으로 메모리가 넘친다.
COLUMNS = ["doc_id", "doc_type", "title", "filer", "url", "raw_path",
           "revision", "observed_at", "source"]


def chunk_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """1년짜리 창으로 자른다. **한 번에 다 읽지 않는다.**

    5년치 424B2 가 40만 행이고 `documents` 전체는 129만 행이다. 통째로
    퍼오면 `ulimit -v` 에 닿는다 — 오늘 Analyst 세 개가 그렇게 죽었다.
    """
    windows = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=366), end)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data")
    parser.add_argument("--dry-run", action="store_true", help="세기만 하고 안 쓴다")
    parser.add_argument("--start", default="2021-01-01")
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    # 정정본을 만드는 도구라 "지금 창고에 있는 것 전부" 가 대상이다.
    now = datetime.now(UTC)  # invariant-allow: wallclock
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)

    total_seen = total_written = 0
    for lo, hi in chunk_windows(start, now):
        label = lo.date().isoformat()
        # **`lookback` 은 `until` 이 아니라 `as_of` 에서 거꾸로 잰다.**
        # 창 길이를 그대로 주면 [now-길이, hi] 가 되어 앞 구간이 통째로
        # 빈다 — 실측으로 40만 행 중 11만만 잡혔다. 0행은 "없다" 와
        # 모양이 같아서 그냥 넘어갈 뻔했다.
        frame = store.get(
            TABLE, as_of=now, until=hi, columns=COLUMNS,
            lookback=int((now - lo).days) + 1,
        )
        if frame.empty:
            print(f"  [{label}] 0행", flush=True)
            continue
        # `documents` 에는 market 컬럼이 없다. 접두사로 가른다.
        target = frame[
            # **경계를 반열린 구간으로 자른다.** `until` 이 닫힌 구간이라
            # 다음 창이 같은 시각에서 시작하면 그날 행이 두 창에 다 잡힌다
            # — 실측 404,496 vs 403,981 로 515행이 겹쳤다. 그대로 두면 같은
            # 자연키에 revision 1 이 두 개 생긴다.
            (frame["valid_from"] >= lo)
            & frame["entity_id"].astype(str).str.startswith("US:")
            & (frame["doc_type"] == STALE_TYPE)
            & frame["title"].astype(str).str.startswith(FORM)
        ]
        total_seen += len(target)
        if target.empty:
            print(f"  [{label}] 대상 0행", flush=True)
            continue

        run_id = f"reclass-424b2-{label}"
        if store.ingest_run_recorded(TABLE, run_id):
            print(f"  [{label}] 이미 넣음 — 건너뜀 ({len(target):,}행)", flush=True)
            continue
        if args.dry_run:
            print(f"  [{label}] 대상 {len(target):,}행 (dry-run)", flush=True)
            continue

        rows = [
            {
                "entity_id": str(row["entity_id"]),
                "valid_from": row["valid_from"],
                # **원래 관측시각을 그대로 옮긴다.** 위 독스트링 참조 —
                # 새로 안 사실이 아니라 잘못 계산한 값이다.
                "observed_at": row["observed_at"],
                "source": str(row["source"]),
                "revision": int(row["revision"]) + 1,
                "doc_id": str(row["doc_id"]),
                "doc_type": OTHER,
                "title": str(row["title"]),
                "filer": str(row["filer"]),
                "url": str(row["url"]),
                "raw_path": str(row["raw_path"]),
            }
            for row in target.to_dict(orient="records")
        ]
        try:
            written = int(store.append(TABLE, rows, ingest_run_id=run_id))
        except DuplicateIngestRun:
            print(f"  [{label}] 중복 — 건너뜀", flush=True)
            continue
        total_written += written
        print(f"  [{label}] {written:,}행 정정본", flush=True)

    print()
    print(f"대상 {total_seen:,}행 · 쓴 정정본 {total_written:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
