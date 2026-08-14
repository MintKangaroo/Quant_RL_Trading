"""DART 표준산업분류 수집 — 진짜 업종.

    uv run python tools/collect_sectors.py

## 이건 시계열이 아니라 스냅샷이다

DART `company.json` 은 "지금 이 회사의 업종" 만 준다 — 언제부터 그 업종
이었는지는 모른다. 그래서 이 실행이 안 시각(``LiveClock.now()``)을 그대로
``valid_from``·``observed_at`` 둘 다에 쓴다. **과거로 심지 않는다** — 오늘
받은 업종을 5년 전 ``valid_from`` 으로 넣으면 그날 시점 백테스트가 미래의
분류를 미리 아는 셈이 된다. 대신 이 실행 이전 시점을 조회하면 이 관측이
안 보인다 — 그게 정직한 결과다.

## 파티션 폭발을 피한다

회사당 API 콜은 하나씩(``fetch``)이지만, 창고에는 전부 모았다가 **한 번만**
``store.append()`` 한다(dart_sectors.py 모듈 docstring). 종목 축으로 개별
append 하면 파일이 종목 수만큼 생긴다 — 과거 미장 백필이 그렇게 파일 247만
개를 만들었다.

## 실행 시간

KR 유니버스가 약 2,900종목이라 회사당 한 콜씩, 대략 15~25분 걸린다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.dart_sectors import (  # noqa: E402
    SOURCE,
    SectorCollector,
    normalize_sectors,
)
from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--market", default="KR")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="맨 앞 N종목만(시험용). 기본은 전체.",
    )
    parser.add_argument(
        "--run-id", default=None,
        help="ingest_run_id 를 덮어쓴다. 기본은 날짜 기반 — 하루에 두 번 돌리면 "
        "(예: --limit 로 시험한 뒤 전체를 돌릴 때) 두 번째가 같은 날짜의 "
        "run_id 를 이미 적재된 것으로 보고 건너뛴다. 그럴 때 이 옵션으로 다른 "
        "이름을 준다 — 겹치는 종목은 새 관측이 하나 더 쌓일 뿐이고, 읽기는 "
        "정정본 규칙대로 최신 하나를 고르므로 해롭지 않다.",
    )
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    now = clock.now()

    source = DartSource()
    if not source.api_key:
        print("OPENDART_API_KEY 가 없다.", file=sys.stderr)
        return 2

    print("corpCode.xml 받는 중...")
    try:
        corp_codes = source.corp_codes()
    except DartUnavailable as error:
        print(f"corpCode.xml 실패: {error}", file=sys.stderr)
        return 2
    print(f"  DART 상장사 매핑 {len(corp_codes)}개")

    universe = store.get(
        "universe", as_of=now, lookback=10, market=args.market, columns=["entity_id"],
    )
    codes = sorted({str(e).split(":", 1)[1] for e in universe["entity_id"].unique()})
    if args.limit:
        codes = codes[: args.limit]
    print(f"유니버스({args.market}) {len(codes)}종목")

    mapping = {code: corp_codes.get(code, "") for code in codes}

    collector = SectorCollector(source=source)
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 200 == 0 or done == total:
            elapsed = time.monotonic() - started
            print(f"  {done}/{total} ({elapsed:.0f}초 경과)")

    report = collector.fetch(mapping, market=args.market, on_progress=progress)
    print(report.render())
    if report.failures:
        print("실패 목록(최대 10개):")
        for entity_id, message in report.failures[:10]:
            print(f"  {entity_id}: {message}")

    rows = normalize_sectors(report.rows, market=args.market, valid_from=now, observed_at=now)
    print(f"업종을 받은 종목 {len(rows)} / 시도한 종목 {report.fetched}")

    if not rows:
        print("적재할 행이 없다.")
        return 1

    run_id = args.run_id or f"dart-sectors-{args.market}-{now.date().isoformat()}"
    if store.ingest_run_recorded("sectors", run_id):
        print(f"이미 적재됨: sectors/{run_id}. 재실행하려면 날짜가 바뀔 때까지 기다릴 것.")
        return 0

    written = store.append("sectors", rows, ingest_run_id=run_id, source=SOURCE)
    print(f"sectors 적재: {written}행 (ingest_run_id={run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
