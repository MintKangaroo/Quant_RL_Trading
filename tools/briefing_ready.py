"""브리핑을 보내도 되나 — **기준일 검사.** 핵심 데이터가 기대 세션에 못 미치면 rc=1.

    .venv/bin/python tools/briefing_ready.py          # 0 이면 보낸다, 1 이면 늦은 것이 있다

검사 대상: 국장 시세·국장 지수·미장 지수·환율 (`dashboard/services/freshness.DATASETS`).
미장 시세는 뺀다 — 6,600종목 수집이 브리핑 뒤(08:40)에 도는 설계다.
"늦었다" 는 원본이 안 낸 것일 수도, 우리가 못 받은 것일 수도 있다 — 어느 쪽이든 그 숫자로
브리핑을 보내면 사용자는 두 번 검증해야 한다(2026-08-28). 그래서 send_briefing.sh 가 이
검사를 통과할 때까지 수집기를 다시 돌리고, 07:10 이 넘으면 ⚠ 를 달아 보낸다.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.dashboard.services.freshness import summary  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

REQUIRED = ("kr_prices", "kr_index", "us_index", "fx")


def main() -> int:
    load_env()
    result = summary(build_store(None), as_of=LiveClock().now())
    # 환율(Yahoo/FRED)은 본래 D+1 이라 1세션까지는 늦은 것이 아니다.
    tolerance = {"fx": 1}
    late = [i for i in result["items"] if i["key"] in REQUIRED
            and (i["status"] == "unknown" or (i["lag_sessions"] or 0) > tolerance.get(i["key"], 0))]
    for item in result["items"]:
        mark = "✓" if item["status"] == "ok" else ("⚠" if item["status"] == "stale" else "?")
        print(f"  {mark} {item['label']:<6} 창고 {item['observed'] or '없음'} · 기대 {item['expected']}"
              + (f" · {item['lag_sessions']}세션 지연" if item["status"] == "stale" else ""))
    if late:
        print("늦은 것: " + ", ".join(i["label"] for i in late))
        return 1
    print("전부 최신 — 보내도 된다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
