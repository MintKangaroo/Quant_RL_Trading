"""저장된 `documents.doc_type` 을 **현재 분류 규칙**에 맞춘다 (DART 행만).

    .venv/bin/python tools/reclassify_filings.py                 # 미리보기(기본)
    .venv/bin/python tools/reclassify_filings.py --apply

분류는 수집 시점에 한 번 정해진다(`dart_filings.classify`). 그래서 `CATEGORIES`
에 유형을 새로 넣으면 **이미 들어온 행은 옛 분류를 그대로 들고 있다** — 새
유형으로 원문을 받거나 피처를 세려면 저장된 값을 먼저 맞춰야 한다.
2026-09-27: `pl_change`(손익구조 변동) 12,949건이 `other` 로 남아 있어 원문
수집기(`collect_filing_texts.py --doc-type`)가 고를 수 없었다.

**append-only 다**(불변식 4). 고치지 않고 `revision` 을 올린 새 행을 넣는다.
옛 as_of 조회는 옛 분류를 그대로 본다 — 어제 돌린 백테스트가 오늘 바뀌지 않는다.
`raw_path` 를 포함한 다른 열은 그대로 옮긴다(정정본이 옛 값을 지우면 안 된다).

**EDGAR·뉴스 행은 건드리지 않는다.** 미장 8-K 는 `edgar_filings.classify`(폼·항목)
가, 뉴스는 고정값이 분류한다 — DART 규칙을 들이대면 전부 틀리게 바뀐다.
그 5,725행이 `source='dart'` 인 원인(`collect_filing_texts` 가 시장 없이 읽은 것)은
2026-09-27 에 고쳤지만, 이미 남은 행은 그대로이므로 아래 필터는 계속 필요하다.

rc: 0 = 맞췄거나 맞출 것이 없다 · 1 = 맞출 것은 있었는데 적재가 0건이다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors.dart_filings import classify  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

COLUMNS = [
    "entity_id", "valid_from", "observed_at", "revision", "source",
    "doc_id", "doc_type", "title", "filer", "url", "raw_path",
]


def stale(frame: pd.DataFrame) -> pd.DataFrame:
    """분류가 현재 규칙과 어긋난 **최신 revision** 행. DART 것만."""
    if frame.empty:
        return frame
    latest = frame.sort_values("revision").groupby(
        ["entity_id", "valid_from", "doc_id"], as_index=False
    ).tail(1)
    # **source 로는 못 가른다.** 미장 8-K 5,725건이 source='dart' 로 적혀 있다 —
    # 2026-09-16~19 밤 배치가 SEC 접수번호를 DART 에 물어 '원문 없음' 정정본을
    # 남긴 흔적이다(복기: 배치의 최대 74%). DART 규칙을 그 행에 들이대면 8-K 제목이
    # 전부 other 로 뒤집힌다(미장 --market US 미리보기에서 5,725행 확인).
    # 그래서 **DART 접수번호 형식(14자리 숫자) + 국장 종목**만 고친다.
    is_dart = (
        latest["doc_id"].astype(str).str.fullmatch(r"\d{14}").fillna(False)
        & latest["entity_id"].astype(str).str.startswith("KR:")
    )
    latest = latest[is_dart]
    if latest.empty:
        return latest
    want = latest["title"].astype(str).map(classify)
    return latest.assign(want=want)[want != latest["doc_type"].astype(str)]


def rows_for(frame: pd.DataFrame, *, clock: Clock) -> list[dict[str, object]]:
    """정정본 행. `raw_path` 를 포함한 다른 열은 그대로 옮긴다."""
    now = clock.now()
    out: list[dict[str, object]] = []
    for r in frame.itertuples():
        out.append({
            "entity_id": str(r.entity_id),
            "valid_from": pd.Timestamp(r.valid_from).to_pydatetime(),
            "observed_at": now,
            "source": docs.SOURCE,
            "revision": int(r.revision) + 1,
            "doc_id": str(r.doc_id),
            "doc_type": str(r.want),
            "title": str(r.title),
            "filer": str(r.filer or ""),
            "url": str(r.url or ""),
            "raw_path": None if pd.isna(r.raw_path) else str(r.raw_path),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--lookback", type=int, default=2000, help="창고 조회 창(일)")
    parser.add_argument("--apply", action="store_true", help="적재한다 (기본은 미리보기)")
    args = parser.parse_args(argv)

    clock = LiveClock()
    now = clock.now()
    store = Store(root=Path(args.root))
    frame = store.get(
        docs.DOCUMENTS, as_of=now, lookback=args.lookback, market=args.market, columns=COLUMNS
    )
    changed = stale(frame)
    if changed.empty:
        print("분류가 어긋난 행이 없다 — 할 일 없음")
        return 0
    pairs = changed.groupby([changed["doc_type"].astype(str), changed["want"]]).size()
    for (before, after), count in pairs.sort_values(ascending=False).items():
        print(f"  {before} → {after}: {count:,}행")
    print(f"합계 {len(changed):,}행 · {changed['entity_id'].nunique():,}종목")
    if not args.apply:
        print("미리보기다 — 적재하려면 --apply")
        return 0
    written = store.append(
        docs.DOCUMENTS, rows_for(changed, clock=clock),
        ingest_run_id=f"reclassify-{args.market}-{now:%Y%m%dT%H%M%S}", source=docs.SOURCE,
    )
    print(f"정정본 {written:,}행 적재")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
