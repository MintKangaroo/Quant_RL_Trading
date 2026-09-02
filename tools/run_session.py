"""하루치 세션 — shadow 운용의 실행기.

    uv run python tools/run_session.py                    # 마지막 거래일
    uv run python tools/run_session.py --day 2026-08-13

## 종료코드

    0  정상 (후보 0개도 정상일 수 있다 — 살 게 없는 날이 있다)
    1  세션이 돌지 않았다 (거래일이 아니거나 창고가 비었다)
    2  안전장치가 주문을 차단했다 (`blocked_by`) — 사고가 아니라 일한 것이다
    3  알파 Analyst 가 0종이라 선정이 시작조차 못 했다 (`fault`)

## 백테스트 루프를 그대로 쓴다

하루를 굴리는 코드는 이미 있다(`backtest/loop.py`). shadow 를 위해 두 번째
루프를 만들면 **shadow 와 백테스트가 다른 코드를 타게 되고**, 그때부터 둘의
성적 차이는 전략의 차이가 아니라 코드의 차이일 수 있다 (불변식 5).

그래서 이 도구가 하는 일은 구간을 하루로 잡아 같은 함수를 부르는 것뿐이다.
어제 낸 주문이 오늘 체결되고, 회계 스냅샷이 남고, 오늘 종가로 내일 주문이
만들어진다.

## 체결은 전날이 있어야 돈다

``loop.run`` 은 그 호출 **안에서** 겪은 이전 세션의 주문만 체결시킨다
(``previous_session`` 은 호출마다 새로 ``None`` 에서 시작한다 — backtest.md
§1). 구간을 하루(``start == end``)로만 잡으면 그 하루가 첫 세션이 되어
``previous_session`` 이 끝까지 ``None`` 이고, 체결 단계는 **한 번도 불리지
않는다.** 화면에는 "체결 0" 으로 뜨는데, 그건 유동성이 없어서도 지정가가
안 맞아서도 아니라 체결 코드 자체가 실행되지 않은 것이다.

그래서 전날 하루를 ``warmup_days=1`` 로 같이 굴린다. 전날의 결정·신호는
이미 창고에 있으므로(ingest_run_id 로 중복이 막힌다) 다시 써도 아무 일도
안 나고, 전날이 이번 호출의 ``previous_session`` 이 되어 오늘 이터레이션에서
비로소 체결이 돈다. 성적 집계에는 전날을 넣지 않는다 — warmup 이 하는 일과
같다.


## 왜 샌드박스인가

shadow 는 **돈이 오가지 않는 운용**이다. 그 체결을 실전 창고의 ``trades`` 에
적으면 회계가 그것을 진짜 보유로 계산하고, 실제 입금이 시작되는 날 장부가
이미 오염돼 있다. append-only 창고에서 그건 되돌릴 수 없다.

읽기는 실제 창고를 그대로 본다(오버레이 링크). 실거래로 넘어갈 때 바꾸는
것은 ``--sandbox`` 를 빼는 것 하나다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.backtest import loop  # noqa: E402
from quant_rl_trading.broker import factory as broker_factory  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402

DEFAULT_SANDBOX = REPO_ROOT / "data" / "_shadow"


def last_settled_day(store: Store, market: Market, now: datetime) -> date | None:
    """**스냅샷 시각이 이미 지난** 마지막 거래일.

    오늘 날짜를 그냥 쓰면 장중에 돌렸을 때 15:40 기준 세션이 만들어지고, 그
    주문은 ``observed_at`` 이 미래라 어떤 조회에서도 안 보인다. 화면에는 "주문
    0건" 으로 뜨는데 창고에는 있다 — 가장 헷갈리는 종류의 고장이다.

    휴장일도 같은 방식으로 처리된다. 조용히 아무것도 안 하는 대신 직전
    거래일을 잡는다.
    """
    for day in reversed(trading_days(market, now.date() - timedelta(days=14), now.date())):
        probe = datetime.combine(day, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=loop.SEOUL)
        if loop.snapshot_moment(store, day, as_of=probe, market=market) <= now:
            return day
    return None


def exit_code(entry: loop.DayResult) -> int:
    """하루 결과를 종료코드로. **조용한 실패를 rc 로 내보내는 자리다.**

    차단은 사고가 아니다 — 안전장치가 일한 것이다. 그래도 rc 로 알린다:
    shadow 10거래일 무사고를 사람이 로그를 뒤져 판정하게 두지 않는다.

    **설비 고장은 차단과 다른 코드로 나간다.** 알파 Analyst 가 0종이면 그날은
    "살 게 없어서" 후보가 빈 것이 아니라 선정이 시작조차 못 한 것이다. US
    세션이 2026-08 내내 이 상태로 rc=0 을 내며 "후보 0" 만 찍었고, 정상 운용과
    로그에서 구별되지 않아 몇 주를 갔다(태스크 #12). 커밋 7ad5680 이 Analyst
    사망에 대해 세운 규칙과 같다 — **조용한 실패는 rc 로 내보낸다.**

    둘이 겹치면 차단이 먼저다. 주문이 막힌 날은 그 사실이 더 급하다.
    """
    if entry.blocked_by:
        return 2
    if entry.fault:
        print(
            f"알파 Analyst 가 0종이다 ({entry.fault}). 후보가 빈 것이 아니라 "
            "선정이 못 돈 것이다.",
            file=sys.stderr,
        )
        return 3
    return 0


def _account_mode(store: Store, *, as_of: datetime) -> str:
    """``execution.account_mode``. 못 읽으면 모의로 본다 — factory 와 같은 규약."""
    try:
        raw = store.config(broker_factory.ACCOUNT_MODE_KEY, as_of=as_of)
        return str(raw or broker_factory.MODE_PAPER).strip().lower()
    except Exception:  # ConfigNotFound 포함
        return broker_factory.MODE_PAPER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--day", help="세션 날짜 (기본: 마지막 거래일)")
    parser.add_argument("--sandbox", default=str(DEFAULT_SANDBOX))
    parser.add_argument(
        "--live-store",
        action="store_true",
        help="실제 창고에 쓴다. **실거래 자본이 들어온 뒤에만** 쓴다",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=0.0,
        help="첫 실행에 넣을 자본. 두 번째부터는 0 (창고가 중복을 거부한다)",
    )
    parser.add_argument(
        "--live-broker",
        action="store_true",
        help="실브로커를 붙인다. **주문이 실제로 나간다** — execution.live_trading "
        "과 계좌 지문이 둘 다 맞아야 실제로 열린다(broker/factory.py)",
    )
    args = parser.parse_args(argv)
    load_env()
    source = build_store(None)
    if args.live_broker and not args.live_store:
        # 샌드박스에 쓰면서 실주문을 내면 **체결이 실계좌에 쌓이는데 장부는
        # 샌드박스에 남는다.** 다음 세션이 실전 창고에서 장부를 접으면 보유
        # 수량이 0 으로 보이고, 같은 종목을 또 산다.
        #
        # **모의 계좌는 예외다** (backtest.md §9). 그 계좌는 이 프로젝트만 쓰고
        # 장부는 `--sandbox`(data/_paper) 하나뿐이라 1:1 이 지켜진다. 실전 창고
        # `data/` 에는 실계좌 배선 검증 체결이 있어 모의 체결을 섞으면 회계가
        # 둘을 못 가른다. 실전(real)은 예전대로 --live-store 를 요구한다.
        mode = _account_mode(source, as_of=LiveClock().now())
        if mode != broker_factory.MODE_PAPER:
            print(
                f"--live-broker 는 --live-store 와 함께 써야 한다 (account_mode={mode}). "
                "실주문을 내면서 장부를 샌드박스에 적으면 보유가 장부에서 사라진다.",
                file=sys.stderr,
            )
            return 2
        if Path(args.sandbox).resolve() == DEFAULT_SANDBOX.resolve():
            print(
                "모의 계좌 실운용은 shadow 샌드박스(data/_shadow)에 쓰지 않는다 — "
                "--sandbox data/_paper 를 준다. 두 장부의 차이가 곧 체결 비용이다.",
                file=sys.stderr,
            )
            return 2
    if args.live_store:
        store = source
    else:
        layer = overlay.build(
            root=Path(args.sandbox), source=source.root, writable=JOURNAL
        )
        store = Store(root=layer.root)

    market = Market(args.market)
    day = (
        date.fromisoformat(args.day)
        if args.day
        else last_settled_day(store, market, LiveClock().now())
    )
    if day is None:
        print(f"{market} 거래일을 찾지 못했다.", file=sys.stderr)
        return 1

    print(f"{market} {day} · 창고 {store.root}")

    # **실브로커는 여기서 딱 한 번 만든다.** 조건이 안 맞으면 PaperBroker 와
    # 이유가 돌아오고 세션은 평소대로 끝까지 돈다 — 무인 실행에서 예외로
    # 죽이면 주문만 안 나가는 게 아니라 회계·기록까지 안 남는다.
    broker = None
    if args.live_broker:
        broker, reason = broker_factory.build_broker(
            store, market=args.market, as_of=LiveClock().now()
        )
        print(f"  브로커: {reason}")

    result = loop.run(
        store,
        start=day,
        end=day,
        market=args.market,
        capital=args.capital,
        # 전날을 워밍업으로 같이 굴려야 체결 단계가 돈다 — 위 "체결은 전날이
        # 있어야 돈다" 참고. 0 이면 체결 코드가 아예 불리지 않는다.
        #
        # **실브로커 세션은 워밍업을 안 돈다** (backtest.md §9). 체결은 봉 시뮬레이션이
        # 아니라 계좌 대사(reconcile_fills)가 적으므로 체결 단계가 필요 없고, 워밍업
        # 날은 브로커 없이 돌아 그날의 **모의 주문을 새로 만들어** 다음 날 봉으로
        # 체결시킨다 — 2026-08-28 첫 실운용에서 그렇게 계좌에 없는 가상 보유 23종목이
        # 장부에 생겼고, 실제 주문은 그 가상 보유 대비 차액만 나갔다.
        warmup_days=0 if broker is not None else 1,
        # 신호는 일일 실행기가 실전 창고에 이미 쌓았다. 여기서 또 만들지 않는다.
        produce_signals=False,
        broker=broker,
    )
    for note in result.notes:
        print(f"  ⚠️  {note}")
    if not result.days:
        print("세션이 돌지 않았다 (거래일이 아니다).", file=sys.stderr)
        return 1

    # 워밍업(전날)도 result.days 에 들어간다 — 우리가 보고할 날은 마지막이다.
    entry = result.days[-1]
    print(
        f"  NAV {entry.nav:,.0f} · 지수 {entry.index_value:.2f} · 낙폭 {entry.drawdown:.2%}\n"
        f"  후보 {len(entry.candidates)} · 주문 {entry.planned_orders} · 체결 {entry.filled}"
        + (f" · 차단 {entry.blocked_by}" if entry.blocked_by else "")
    )
    for note in entry.notes:
        print(f"    · {note}")
    return exit_code(entry)


if __name__ == "__main__":
    raise SystemExit(main())
