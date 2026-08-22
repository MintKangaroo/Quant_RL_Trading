"""거시지표 발표 일정·실측값 수집.

    uv run python tools/collect_macro.py

라이브 경로다. 매 실행이 그 시점의 일정과 실측값을 새 행으로 남긴다 —
정정은 UPDATE 가 아니라 새 revision 이다 (불변식 4).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import json  # noqa: E402

from quant_rl_trading.collectors.kosis_source import (  # noqa: E402
    KOSIS_TABLES,
    KosisCollector,
    KosisSource,
)
from quant_rl_trading.collectors.macro_source import (  # noqa: E402
    EcosCollector,
    EcosSource,
    FredSource,
    IndexCollector,
    MacroCollector,
    MacroUnavailable,
)
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument(
        "--discover",
        metavar="PARENT_LIST_ID",
        nargs="?",
        const="",
        help=(
            "KOSIS 통계표 목록을 출력하고 끝낸다. 여기서 본 ORG_ID/TBL_ID 를 "
            "kosis_source.KOSIS_TABLES 에 손으로 옮긴다 — 자동 선택은 이름이 "
            "비슷한 표를 잘못 고른다"
        ),
    )
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()

    if args.discover is not None:
        kosis = KosisSource.from_env()
        if not kosis.api_key:
            print("KOSIS_API_KEY 가 없다. kosis.kr 에서 발급할 것.", file=sys.stderr)
            return 2
        listing = kosis.find_tables(parent_list_id=args.discover)
        for item in listing if isinstance(listing, list) else [listing]:
            print(json.dumps(item, ensure_ascii=False))
        return 0

    source = FredSource.from_env()
    if not source.usable():
        print("FRED_API_KEY 가 없다.", file=sys.stderr)
        return 2

    written = MacroCollector(
        store=store, source=source, clock=clock,
        archive=RawArchive(root=store.root), market=Market.US,
    ).collect()
    print(f"macro_releases 적재: {written}행 (US)")

    # 창 10세션. **기본값 400 을 그대로 두면 매 실행이 조각 파일 400개를 낳는다**
    # — 적재는 observed_date 파티션마다 파일 하나이고 run_id 는 실행 시각이라
    # 겹치지 않는다. 실측(2026-08-15) 결과 그렇게 쌓인 파일이 2,310개였고,
    # 읽기 비용은 데이터 양이 아니라 파일 개수에 붙는다(flows 에서 109만 개로
    # 겪은 것과 같은 사고).
    #
    # 과거는 tools/backfill_indices_us.py 가 (시리즈, 연도) 결정론 run_id 로
    # 한 번만 넣는다. 라이브는 최근 것만 따라잡으면 된다 — collect_daily 의
    # SESSIONS=3 과 같은 취지로, 연휴·장애로 며칠 놓쳐도 메워지게 넉넉히 준다.
    # **못 받은 것이 있으면 rc≠0 으로 나간다.** 시리즈 하나가 죽어도 나머지가
    # 적재되므로 행 수만 보면 성공처럼 보인다 — 2026-08-22 아침 브리핑이 8/21
    # 지수를 못 받고도 "적재 140행" 이었다. 셸의 `echo rc=$?` 가 이 값을
    # 삼키지 않도록 refresh_before_briefing.sh 도 함께 고쳤다.
    failed = False
    try:
        written = IndexCollector(
            store=store, source=source, clock=clock,
            archive=RawArchive(root=store.root), days=10,
        ).collect()
        print(f"indices 적재: {written}행 (US · 가격지수 — 배당 미반영)")
    except MacroUnavailable as error:
        # 여기서 멈추지 않는다. 아래 KR 수집은 FRED 와 무관하게 돌아야 한다.
        print(f"indices: {error}", file=sys.stderr)
        failed = True


    kosis = KosisSource.from_env()
    if kosis.usable():
        written = KosisCollector(
            store=store, source=kosis, clock=clock,
            archive=RawArchive(root=store.root), market=Market.KR,
        ).collect()
        print(f"macro_releases 적재: {written}행 (KR)")
    elif not kosis.api_key:
        print("KR: KOSIS_API_KEY 가 없다 — kosis.kr 에서 발급할 것.")
    elif not KOSIS_TABLES:
        print(
            "KR: KOSIS 키는 있지만 통계표가 미확인이다.\n"
            "    `--discover` 로 목록을 보고 ORG_ID/TBL_ID 를 "
            "kosis_source.KOSIS_TABLES 에 넣을 것 (짐작 금지)."
        )

    ecos = EcosSource.from_env()
    if ecos.usable():
        written = EcosCollector(
            store=store, source=ecos, clock=clock,
            archive=RawArchive(root=store.root), market=Market.KR,
        ).collect()
        print(f"macro_releases 적재: {written}행 (KR/ECOS — 금리)")
    else:
        print("KR: ECOS_API_KEY 가 없다.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
