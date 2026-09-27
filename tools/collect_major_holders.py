"""DART majorstock → `major_holders` 표. 6차 G10(대량보유 변동)의 입력.

    .venv/bin/python tools/collect_major_holders.py --limit 100 --dry-run     # 스모크
    .venv/bin/python tools/collect_major_holders.py                           # 전 상장사 (백필)
    .venv/bin/python tools/collect_major_holders.py --incremental             # 매일 — 이미 적재된 회사는 마지막 접수일 이후만

회사당 콜 1건(최근 2년 이력이 한 응답에 온다). 상장사 ~4,000 → 하루 한도(20,000) 안이다.

**이어받기**는 두 겹이다.
1. 완료 표시 파일(`data/_progress/major-holders/done-YYYYMMDD.txt`) — 그 회사를 이미 물었다. 0행이어도 남으므로
   재시작이 API 를 다시 쓰지 않는다. 날짜가 붙어 있어 다음 날 실행은 전 회사를 다시 훑는다.
2. `--incremental` 은 창고의 그 종목 마지막 접수일 **이후**만 더한다. 매일 수집에 붙일 때 쓴다.

한도 초과(020)를 만나면 **그 자리에서 멈춘다**(rc=2) — 남은 호출을 헛되이 쓰지 않는다. 완료 표시가 남아 있으니
다음 실행이 이어받는다.

## 회사마다 적재하지 않는다 — 파티션이 터진다

창고는 `observed_date` × `ingest_run_id` 로 파일을 나눈다. 회사당 한 번 append 하면 접수일이 다 다르므로
**행 수만큼 파일이 생긴다**: 첫 스모크에서 637행이 파일 600개·6.1MB 였다(2026-09-27 실측, 메모
`us-backfill-partition-blowup` 과 같은 모양). 그래서 `--flush-every` 회사마다 한 번만 적재하고, 끝나면
`tools/compact_partitions.py` 로 접는다. 크래시로 잃는 것은 마지막 묶음의 API 호출뿐이다(완료 표시도 같이 미룬다).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import dart_major_holders as mh  # noqa: E402
from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

PROGRESS_DIR = "_progress/major-holders"
#: 한도 초과 상태코드. DartSource 가 문자열로 실어 올린다.
QUOTA_EXCEEDED = "020"


def _done_path(root: Path, day: date) -> Path:
    return root / PROGRESS_DIR / f"done-{day:%Y%m%d}.txt"


def _load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _observed_by_receipt(store: Store, *, now, lookback: int) -> dict[str, object]:
    """접수번호 → 공시 목록 첫 관측 시각.

    `documents` 는 이 공시를 제목으로 이미 다 들고 있다(51,463건). 그 행의 **첫 revision**
    관측 시각이 "시장이 이 공시를 언제 알았나" 다 — API 응답에는 그 시각이 없다.
    """
    frame = store.get(
        "documents", as_of=now, lookback=lookback, market=mh.MARKET,
        columns=["entity_id", "valid_from", "observed_at", "doc_id", "title", "revision"],
    )
    if frame.empty:
        return {}
    frame = frame[frame["title"].astype(str).str.contains("대량보유", regex=False)]
    if frame.empty:
        return {}
    # store.get 은 최신 revision 만 준다. 대량보유 행은 전부 revision 0 이라(9/27 실측)
    # 그 값이 곧 첫 관측이다. 정정본이 생기면 아래 정렬이 낮은 revision 을 고른다.
    frame = frame.sort_values(["doc_id", "revision"])
    first = frame.groupby(frame["doc_id"].astype(str))["observed_at"].first()
    return {str(k): v for k, v in first.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--since", default="2021-01-01", help="이 접수일 이전은 버린다")
    parser.add_argument("--limit", type=int, default=None, help="이번에 부를 회사 수 상한 (스모크)")
    parser.add_argument("--pause", type=float, default=0.12)
    parser.add_argument("--incremental", action="store_true", help="창고의 마지막 접수일 이후만")
    parser.add_argument("--flush-every", type=int, default=500, help="이 회사 수마다 한 번 적재 (파티션 수를 줄인다)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    now = LiveClock().now()
    since = date.fromisoformat(args.since)
    root = Path(args.root)
    store = Store(root=root)
    lookback = (now.date() - since).days + 2

    dart = DartSource()
    codes = dart.corp_codes()  # 종목코드 → corp_code
    observed = _observed_by_receipt(store, now=now, lookback=lookback)
    print(f"상장사 {len(codes)} · 공시 목록에서 찾은 접수번호 {len(observed)}", flush=True)

    latest: dict[str, date] = {}
    if args.incremental:
        have = store.get(mh.TABLE, as_of=now, lookback=lookback, market=mh.MARKET,
                         columns=["entity_id", "valid_from"])
        if not have.empty:
            for entity, day in have.groupby("entity_id")["valid_from"].max().items():
                latest[str(entity)] = day.tz_convert(mh.SEOUL).date()
        print(f"이미 적재된 종목 {len(latest)}", flush=True)

    done_path = _done_path(root, now.date())
    done = _load_done(done_path)
    targets = [(s, c) for s, c in sorted(codes.items()) if s not in done]
    if args.limit:
        targets = targets[: args.limit]
    print(f"물을 회사 {len(targets)} · 이미 물은 회사 {len(done)}", flush=True)

    total, failed, hit_quota, batch = 0, 0, False, 0
    pending: list[dict] = []
    pending_codes: list[str] = []
    if not args.dry_run:
        done_path.parent.mkdir(parents=True, exist_ok=True)

    def flush() -> None:
        """묶음 하나를 한 번에 적재한다. 적재가 성공한 뒤에 완료 표시를 쓴다."""
        nonlocal batch, pending, pending_codes
        if not pending_codes or args.dry_run:
            pending, pending_codes = [], []
            return
        batch += 1
        if pending:
            store.append(mh.TABLE, pending, ingest_run_id=mh.batch_run_id(batch, now), source=mh.SOURCE)
        with done_path.open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{code}\n" for code in pending_codes))
        pending, pending_codes = [], []

    for index, (stock, corp) in enumerate(targets, start=1):
        try:
            payload = dart.major_stock(corp)
        except DartUnavailable as error:
            if f"status={QUOTA_EXCEEDED}" in str(error):
                print("DART 일일 한도 초과 — 멈춘다. 다음 실행이 이어받는다", file=sys.stderr)
                hit_quota = True
                break
            failed += 1
            print(f"  {stock} 실패: {error}", file=sys.stderr)
            continue
        rows = mh.normalize(
            stock, payload, since=since,
            after=latest.get(f"{mh.MARKET}:{stock}") if args.incremental else None,
            observed_by_receipt=observed,
        )
        pending.extend(rows)
        pending_codes.append(stock)
        total += len(rows)
        if len(pending_codes) >= args.flush_every:
            flush()
        if index % 100 == 0:
            print(f"  … {index}/{len(targets)} · 누적 {total}행 · 실패 {failed}", flush=True)
        time.sleep(args.pause)
    flush()

    suffix = " (dry-run)" if args.dry_run else ""
    print(f"완료 — {total}행 · 묶음 {batch} · 실패 {failed}{suffix}")
    if hit_quota:
        return 2
    # 조용한 실패는 rc 로 내보낸다: 5% 넘게 실패하면 성공이 아니다.
    if targets and failed > max(5, len(targets) // 20):
        print(f"실패가 많다 ({failed}/{len(targets)})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
