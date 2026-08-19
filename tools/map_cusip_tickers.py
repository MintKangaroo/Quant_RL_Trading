#!/usr/bin/env python
"""CUSIP → 티커 매핑 적재 — 13F 를 종목 축에 올리기 위한 한 걸음.

    uv run python tools/map_cusip_tickers.py --dry-run
    uv run python tools/map_cusip_tickers.py
    uv run python tools/map_cusip_tickers.py --limit 100

``filings_13f`` 에 있는 CUSIP 중 아직 ``security_ids`` 에 없는 것만 OpenFIGI
에 물어 적재한다. **기존 13F 행은 건드리지 않는다** — 창고는 append-only 이고
그 표는 화면이 이미 읽고 있다. 매핑은 옆에 따로 선다.

## 주기

**분기에 한 번.** 13F 가 분기 데이터라 새 CUSIP 은 분기마다만 늘어난다.
``tools/collect_13f.py`` 를 돌린 직후에 이어서 돌리면 된다.

    30 10 20 2,5,8,11 * cd <repo> && .venv/bin/python tools/map_cusip_tickers.py >> logs/13f.log

## 걸리는 시간

키 없이 부르면 요청당 10건·분당 25요청이다. 4,150건이면 415요청 ≈ 17분.
**재시도로 밀어붙이지 않는다** — 429 는 우리가 약속을 어겼다는 뜻이다.

## 못 붙인 CUSIP 은 매번 다시 물어본다

실패를 창고에 적어두지 않는다. ``security_ids`` 는 "이 식별자는 이 종목"
을 적는 표라, 못 붙인 것을 적을 자리가 없다(entity_id 가 없다). 그래서
다음 실행이 그것들을 다시 묻는다 — 그게 옳다. **오늘 없던 종목이 다음
분기에 상장할 수 있다.** 실측으로 남은 것은 144건뿐이라 15요청(40초)이다.

## 종료코드

    0  새로 적재했다
    1  창고에 매핑이 한 건도 없는데 한 건도 못 붙였다 — 실패다
    2  새로 붙은 것이 없다 (이미 다 붙었거나, 남은 것은 OpenFIGI 가 모른다)

**0행을 성공으로 적지 않는다.** 이 저장소는 rc=0 에 0행인 수집이 며칠씩
조용히 도는 사고를 여러 번 냈다. 다만 rc=1 은 **정말로 아무것도 없을 때**
만이다 — 남은 실패 144건을 매번 실패로 적으면 크론이 분기마다 빨간불을
켜고, 그러면 진짜 실패를 아무도 안 본다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.security_ids import (  # noqa: E402
    TABLE,
    Miss,
    OpenFigiClient,
    SecurityIdError,
    batched,
    ingest_run_id,
    to_rows,
)
from quant_rl_trading.collectors.thirteen_f import TABLE as FILINGS_TABLE  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store.holdings import mapping_coverage  # noqa: E402
from tools.backfill import build_store  # noqa: E402

#: 13F 를 읽을 창. 분기 데이터라 넉넉해야 한다 — 5년.
FILINGS_LOOKBACK_DAYS = 365 * 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="받아서 보여만 준다")
    parser.add_argument("--limit", type=int, help="이만큼만 물어본다 (시험용)")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)

    store = build_store(args.data_root)
    now = LiveClock().now()

    filings = store.get(
        FILINGS_TABLE,
        as_of=now,
        lookback=FILINGS_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from", "cusip", "issuer"],
    )
    if filings.empty:
        print(f"{FILINGS_TABLE} 가 비어 있다. 먼저 tools/collect_13f.py 를 돌려라.")
        return 1
    wanted = sorted(
        {str(one).strip().upper() for one in filings["cusip"].dropna() if str(one).strip()}
    )

    # 이미 붙인 것은 다시 묻지 않는다. **lookback 을 주면 안 된다** —
    # 이 표의 valid_from 은 2015 기준시점이라 창을 좁히는 순간 통째로
    # 프루닝돼 사라지고, 그러면 매번 4,150건을 다시 물어보게 된다.
    known = store.get(TABLE, as_of=now, columns=["entity_id", "id_value"])
    already = set(known["id_value"]) if not known.empty else set()
    todo = [one for one in wanted if one not in already]
    if args.limit:
        todo = todo[: args.limit]

    print(f"13F CUSIP {len(wanted):,}개 · 이미 매핑 {len(already):,}개 · 물어볼 것 {len(todo):,}개")
    if not todo:
        return 2

    requests = (len(todo) + 9) // 10
    print(f"OpenFIGI {requests:,}요청 ≈ {requests * 2.6 / 60:.1f}분")

    client = OpenFigiClient()
    mapped = []
    missed: list[Miss] = []
    for index, chunk in enumerate(batched(todo), start=1):
        try:
            batch_mapped, batch_missed = client.map_batch(chunk)
        except SecurityIdError as error:
            # 중간까지 받은 것은 버리지 않는다. 다음 실행이 이어받는다.
            print(f"  [{index}/{requests}] 실패: {error}")
            break
        mapped.extend(batch_mapped)
        missed.extend(batch_missed)
        if index % 20 == 0 or index == requests:
            print(f"  [{index}/{requests}] 매핑 {len(mapped):,} · 실패 {len(missed):,}")

    total = len(mapped) + len(missed)
    if total:
        print(f"\n매핑 {len(mapped):,}/{total:,} ({len(mapped) / total:.1%})")
    reasons = Counter(one.reason.split(":")[0] for one in missed)
    for reason, count in reasons.most_common():
        print(f"  실패 {reason}: {count:,}")
    for one in missed[:10]:
        print(f"    {one.identifier} ({one.id_type}) — {one.reason}")

    if not mapped:
        if already:
            print(
                "새로 붙은 것이 없다. 남은 식별자는 OpenFIGI 가 모르는 것들이고"
                "(상폐·비상장·옛 CUSIP), 다음 실행이 다시 물어본다."
            )
            _report_coverage(store, now)
            return 2
        return 1
    if args.dry_run:
        for one in mapped[:10]:
            print(f"    {one.identifier} → {one.entity_id}  {one.security_type}  {one.name}")
        print("--dry-run: 적재하지 않았다")
        return 0

    rows = to_rows(mapped, mapped_at=now)
    run_id = ingest_run_id(now)
    if store.ingest_run_recorded(TABLE, run_id):
        # 같은 날 두 번째 실행. append-only 라 같은 run_id 로 또 쓸 수 없다.
        run_id = f"{run_id}-{now.strftime('%H%M%S')}"
    written = store.append(TABLE, rows, ingest_run_id=run_id)
    print(f"적재 {written:,}행 · run_id={run_id}")

    _report_coverage(store, now)
    return 0


def _report_coverage(store, now) -> None:
    """**적재 건수로 끝내지 않는다.**

    이 도구를 돌리는 이유는 13F 를 종목 축으로 읽기 위해서이고, 그게
    되는지는 매핑 건수가 아니라 **분기별 커버리지**가 답한다. 커버리지가
    낮은 분기는 store/holdings.py 가 통째로 버리므로, 여기서 안 보여주면
    "왜 그 분기가 없지" 를 나중에 창고를 뒤져서 찾게 된다.
    """
    coverage = mapping_coverage(store, as_of=now)
    print("\n분기별 매핑 커버리지 (금액 기준)")
    for row in coverage.to_dict(orient="records"):
        print(
            f"  {row['valid_from'].date()}  "
            f"{int(row['mapped_rows']):,}/{int(row['rows']):,}행 · "
            f"금액 {row['mapped_value']:.1%}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
