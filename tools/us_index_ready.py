"""미장 FRED 지수가 **직전 세션까지 따라잡았나**. 따라잡았으면 rc=0, 아니면 rc=1.

브리핑 앞의 보충 수집(``scripts/refresh_before_briefing.sh``)이 이걸 보고
한 번 더 받을지 정한다.

## 왜 있어야 하나

2026-08-22 06:30 발송분이 머리말에 ``미장 2026-08-21`` 을 달고 **8/20 종가**를
실었다. 원인은 한 시장 안에서 출처 두 개의 지연이 달랐던 것이다:

    LS 해외 ETF (SPY·QQQ·DIA·SOXX)   8/21 세션이 08-22 05:20 에 들어왔다
    FRED 지수 (US:IDX:SP500 등)      아직 없었다

FRED 지수는 그 전까지 세션 D 가 D+1 06:00 관측으로 들어와 있었는데, **그
06:00 은 FRED 의 공표 시각이 아니라 우리 크론 시각이다**(``observed_at`` 은
"내가 알 수 있었던 시각" 이다 — 불변식 3). 즉 그 패턴이 말해 주는 것은
"그날들은 06:00 이전에 이미 공표돼 있었다" 뿐이고, 공표 시각 자체는 06:00
KST(=17:00 ET, 마감 한 시간 뒤) 언저리에서 흔들린다. 8/21 은 그 경계를
못 넘었다.

**그래서 실패로도 안 잡힌다** — 수집은 rc=0 으로 끝난다. 받을 것이 아직
없었을 뿐이다. 조용한 실패라 이렇게 밖에서 물어보는 수밖에 없다.

## 판정 기준

``sessions.expected_session`` 이 말하는 "as_of 시점에 이미 공표가 끝난 마지막
거래일" 과 창고의 마지막 지수 세션을 견준다. **달력이 아니라 공표 정책으로
센다** — 마감 직후 20분을 기대 세션이라 부르면 매일 "안 들어왔다" 가 된다.

대표 지수 하나만 본다(``US:IDX:SP500``). 넷을 다 보면 하나가 영구히 빠져
있을 때 재시도가 매일 끝까지 돈다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.dashboard.services import market as market_service  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.reporting.sessions import expected_session  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

#: 대표 지수. 브리핑 헤드라인이 쓰는 것과 같은 종목이다.
HEADLINE_INDEX = "US:IDX:SP500"

#: 창고를 되짚는 창(일). 연휴가 끼어도 마지막 세션이 들어오게 넉넉히.
LOOKBACK_DAYS = 20


def caught_up(store, *, as_of: datetime, entity: str = HEADLINE_INDEX) -> tuple[bool, str]:
    """(따라잡았나, 로그에 남길 한 줄)."""
    expected = expected_session(store, Market.US, as_of=as_of)
    if expected is None:
        # 공표가 끝난 세션이 최근에 없다 — 연휴 구간이다. 받을 것이 없으니
        # 재시도할 이유도 없다. **여기서 False 를 내면 연휴마다 재시도가
        # 마감까지 헛돈다.**
        return True, "미장: 최근에 공표가 끝난 세션이 없다 (휴장 구간)"

    frame = store.get(
        market_service.INDICES,
        as_of=as_of,
        lookback=LOOKBACK_DAYS,
        market="US",
        entity=[entity],
        columns=["entity_id", "close", "valid_from"],
    )
    # 휴장·미수집 세션은 종가 0 으로 들어온다. 섞으면 "들어왔다" 가 거짓이 된다
    # (``briefing._quotes`` 와 같은 규칙).
    live = frame[frame["close"].astype(float) > 0] if not frame.empty else frame
    observed = (
        max(value.date() for value in live["valid_from"]) if not live.empty else None
    )
    if observed is not None and observed >= expected:
        return True, f"미장 지수 {entity}: {observed} 까지 들어왔다 (기대 {expected})"
    where = observed.isoformat() if observed else "없음"
    return False, f"미장 지수 {entity}: 창고 {where} · 기대 {expected} — 아직이다"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--entity", default=HEADLINE_INDEX)
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    ready, line = caught_up(store, as_of=LiveClock().now(), entity=args.entity)
    print(line)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
