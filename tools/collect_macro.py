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

    written = IndexCollector(
        store=store, source=source, clock=clock, archive=RawArchive(root=store.root)
    ).collect()
    print(f"indices 적재: {written}행 (US — 시장 반응 측정용)")


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
