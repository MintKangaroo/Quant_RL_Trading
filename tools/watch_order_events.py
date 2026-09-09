"""Collect authenticated KR SC2/SC3 confirmations; never submits/modifies orders.

Start before chase_orders. A disconnect or missing event retains reservations.
This collector requires a pinned account fingerprint, including legacy KR real mode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.factory import ACCOUNT_MODE_KEY, PROFILES  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.order_events import watch  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    client = None
    try:
        mode = str(store.config(ACCOUNT_MODE_KEY, as_of=clock.now()))
        profile = PROFILES[("KR", mode)]
        client = LSClient(
            credentials=LSCredentials.from_env(prefix=profile.env_prefix),
            clock=clock,
            live_trading=False,
        )
        result = watch(store, client, clock, seconds=args.seconds)
    except Exception as exc:
        # Authentication/transport exceptions may embed payloads; print no raw details.
        print(f"주문 확인 수집 중단 ({type(exc).__name__}) — 미확정 예약 유지", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
    print(
        f"취소·정정 확인 {result.confirmed}건 · 중복 {result.duplicates}건 · "
        f"로컬 주문과 연결되지 않은 이벤트 {result.unmatched}건"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
