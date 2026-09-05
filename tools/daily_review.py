"""Claude 일일 리뷰를 쓴다 (M5). 회계가 접힌 뒤(23:20) 23:35 크론.

    .venv/bin/python tools/daily_review.py --store data/_paper --market KR [--dry-run]

사실은 ``--store``(모의계좌 장부)에서 읽고, 리뷰·캐시·사용량은 실전 창고(config·llm_usage 가
있는 곳)에 적는다. 같은 사실 해시면 agent_cache 가 답해 LLM 을 다시 부르지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.auditor.daily_review import DailyReviewer, gather_facts  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="data/_paper", help="사실을 읽을 장부")
    parser.add_argument("--data-root", type=Path, help="리뷰를 적을 실전 창고")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--dry-run", action="store_true", help="사실만 출력, LLM 안 부름")
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    now = clock.now()
    facts_store = Store(root=Path(args.store))
    store = build_store(args.data_root)
    if args.dry_run:
        import json

        print(json.dumps(gather_facts(facts_store, as_of=now, market=args.market), ensure_ascii=False, indent=2, default=str))
        return 0
    reviewer = DailyReviewer.from_store(store, facts_store, clock, as_of=now)
    print(f"모델 {reviewer.model} · 이달 지출 ${reviewer.month_to_date_usd:.2f} / 예산 ${reviewer.budget_usd:.0f}")
    result = reviewer.review(as_of=now, market=args.market)
    print(f"[{result['status']}] {result['entity_id']} {result['tone']} — {result['headline']}")
    if result["body"]:
        print(result["body"])
    return 0 if result["status"] in ("written", "cached") else 3


if __name__ == "__main__":
    raise SystemExit(main())
