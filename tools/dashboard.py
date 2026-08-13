"""대시보드 실행기.

    uv run python tools/dashboard.py

호스트·포트는 ``.env`` 의 ``DASHBOARD_HOST`` / ``DASHBOARD_PORT`` 를 따른다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lattice.dashboard import create_app  # noqa: E402
from lattice.replay.clock import LiveClock  # noqa: E402
from lattice.store import ConfigNotFound, Store  # noqa: E402
from tools.backfill import load_env  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6060


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    store = Store(root=args.data_root) if args.data_root else Store()

    # 임계치가 없으면 화면이 뜨자마자 503 을 낸다. 여기서 심어 둔다.
    try:
        store.config("data_quality.default_lookback_days", as_of=clock.now())
    except ConfigNotFound:
        store.seed_config_defaults()

    host = args.host or os.environ.get("DASHBOARD_HOST") or DEFAULT_HOST
    port = args.port or int(os.environ.get("DASHBOARD_PORT") or DEFAULT_PORT)

    print(f"Quant_RL_Trading 대시보드 → http://{host}:{port}/  (창고: {store.root})")
    create_app(store=store, clock=clock).run(host=host, port=port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
