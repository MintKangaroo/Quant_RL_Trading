#!/usr/bin/env python
"""재부팅·정전 뒤 **무엇이 비었는지** 묻고 할 일을 적어 낸다.

    uv run python tools/plan_recovery.py --market KR

## 왜 "돌았나" 가 아니라 "비었나" 를 묻는가

놓친 크론을 찾는 방법은 둘이다. 하나는 **로그에 오늘 기록이 있나** 를 보는
것이고, 하나는 **창고에 오늘 세션이 있나** 를 보는 것이다.

앞의 방법은 틀린다. `collect_daily.sh KR` 은 하루 두 번(15:55·22:40) 같은
로그에 쓴다. 15:55 것만 돌고 22:40 것이 빠진 날에도 "오늘 기록이 있다" 가
되어 통과한다. 게다가 로그는 rc=0 으로 끝나면서 0행을 받아 올 수 있다 —
그건 [[fresh-is-not-fresh]] 가 이미 적어 둔 함정이다.

**창고를 본다.** 기대 세션(`sessions.expected_session`, 공표가 끝난 마지막
거래일)과 창고에 실제로 들어온 마지막 세션을 견준다. 그러면 크론이 돌았든
안 돌았든, 성공했든 조용히 0행이었든 **같은 질문 하나로 답이 나온다.**

## 판정을 여기 한 곳에만 둔다

기대 세션 계산은 브리핑이 쓰는 `reporting.sessions` 를 그대로 부른다.
복구가 자기 달력을 따로 들면 브리핑은 "안 들어왔다" 는데 복구는 "다 됐다"
고 말하는 날이 온다.

## 세션을 따라잡기 전에 입력을 확인한다

세션을 돌리는 것은 **창고에 쓰는 일**이다. 그리고 창고는 append-only라 먼저
쓴 쪽이 이긴다(불변식 4). 그러니 "돌릴 수 있나" 가 아니라 **"지금 돌리면
정규 크론이 낼 답과 같은 답이 나오나"** 를 물어야 한다.

실제로 어긋난 적이 있다. 2026-08-20 18:51 복구가 shadow 를 돌렸는데, 그때는
8/19 신호 3종(event·fundamental·regime)이 아직 죽어 있는 상태였다. 반쪽
신호로 고른 8/19 후보가 그대로 주문이 되고 8/20 봉에 체결돼 26행/562주가
창고에 박혔다. 22:55 정규 실행이 신호를 채우고 23:05 에 다시 계산하니
30행/632주였지만 ``ingest_run_id`` 가 같아 막혔다 — **경고만 남고 회계는 낡은
26행을 쓴다.**

무엇이 필요한지는 ``backtest/loop.py`` 가 정한다. ``run_session.py`` 는
``warmup_days=1`` 로 **전날과 당일 이틀**을 굴린다(그래야 D+1 체결 단계가
돈다). 그래서 필요한 것은 이틀치다:

    시세  D, D-1  ``backtest/market.py`` 가 그날 봉이 없는 종목을 통째로
                  빼고, 빠진 종목은 미체결로 적힌다
    신호  D, D-1  ``produce_signals=False`` 라 shadow 는 **창고에 이미 있는
                  신호**로 후보를 고른다. D-1 신호가 반쪽이면 D-1 주문이
                  반쪽이 되고, 그 주문의 체결이 곧 창고에 박히는 그 행이다

## 나가는 형식

셸이 읽는다. 한 줄에 하나:

    NEED collect   KR 시세: 창고가 2026-08-18 까지다 (2개 세션)
    OK   session   KR 세션: 2026-08-20 이 있다

관문 모드(``--gate``)는 한 줄로 답한다:

    READY  signals  KR 2026-08-20: 시세·신호가 이틀치 다 있다
    DEFER  signals  KR 2026-08-20: 2026-08-19 신호에 event 가 없다
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.reporting.sessions import expected_session  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

#: 창고를 거꾸로 훑는 창. 연휴가 끼어도 마지막 거래일이 들어오도록 넉넉히.
LOOKBACK_DAYS = 14

#: 미룬 세션을 적어 두는 곳. **창고가 아니라 로그다** — 이건 시장에 대한
#: 사실이 아니라 우리 실행기가 한 판단이라, `observed_at` 을 붙일 사실이
#: 없다(불변식 3). 대신 `--gate` 를 다시 부르면 같은 관문으로 재판정한다.
DEFERRAL_LOG = REPO_ROOT / "logs" / "recovery-deferrals.log"

#: run_session.py 가 ``warmup_days=1`` 로 굴리는 날 수. 세션 하루를 돌리려면
#: 전날도 같이 굴려야 D+1 체결 단계가 돌기 때문이다.
WARMUP_DAYS = 1

#: 미룬 세션을 되짚어 보는 창(달력일). 무한정 되짚으면 두 가지가 같이 는다 —
#: 화면의 줄 수와, 석 달 전 날짜를 덮으려고 여는 파티션 수. 그때쯤이면 답은
#: 이미 "그 세션은 영영 빠졌다" 이고, 그건 M3 카운터가 들고 있는 사실이다.
FOLLOW_UP_DAYS = 30


def latest_session(
    store: Store, table: str, *, as_of: datetime, market: str | None, column: str
) -> "pd.Timestamp | None":
    """그 표에 들어온 **마지막 세션 날짜**. 없으면 ``None``.

    ``lookback`` 을 반드시 준다 — 안 주면 파티션 프루닝이 꺼져 5년치를
    통째로 훑는다 ([[partition-pruning-was-off]]).

    ``market`` 이 ``None`` 인 표가 있다. `nav_daily` 는 포트폴리오 전체가
    한 줄이라 시장 축이 없다 — 거기에 시장을 넘기면 조회가 통째로 실패한다.
    """
    try:
        frame = store.get(
            table,
            as_of=as_of,
            lookback=LOOKBACK_DAYS,
            columns=[column],
            **({"market": market} if market else {}),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN {table} 조회 실패: {exc}", file=sys.stderr)
        return None
    if frame.empty:
        return None
    return pd.to_datetime(frame[column]).max().date()


# -- 세션 입력 관문 -----------------------------------------------------------
#
# 여기서 묻는 것은 "돌아가나" 가 아니라 **"지금 돌리면 정규 크론과 같은 답이
# 나오나"** 다. 창고는 append-only 라 먼저 쓴 쪽이 이기고(불변식 4), 체결은
# 되돌리기가 회계보다 훨씬 위험하다(`backtest/execution.py:_stale_note`).
# 그러니 낡은 답을 쓰고 나서 고치는 대신 **아예 안 쓴다.**


def session_window(market: Market, session: date) -> list[date]:
    """세션 하나를 돌릴 때 **실제로 굴러가는 거래일들**.

    ``run_session.py`` 가 ``warmup_days=1`` 로 부른다. 전날을 같이 굴리지
    않으면 ``previous_session`` 이 끝까지 ``None`` 이라 D+1 체결 단계가 한
    번도 안 불린다 — 그래서 필요한 입력도 하루치가 아니라 이틀치다.
    """
    span = timedelta(days=int(WARMUP_DAYS * 7 / 5) + 14)
    days = [day for day in trading_days(market, session - span, session) if day <= session]
    return days[-(WARMUP_DAYS + 1) :]


def _lookback_for(as_of: datetime, days: list[date]) -> int:
    """그 날들을 덮는 가장 짧은 창. **반드시 준다** — 안 주면 프루닝이 꺼진다."""
    return max((as_of.date() - min(days)).days + 2, 2)


def missing_prices(
    store: Store, *, market: str, as_of: datetime, days: list[date]
) -> list[str]:
    """봉이 없는 날. ``backtest/market.py`` 가 그날 봉 없는 종목을 통째로 뺀다."""
    # **`read_prices` 를 경유한다.** 종가 0 세션이 걸러진 뒤의 프레임이라야
    # 세션이 실제로 볼 봉과 같다 — 0 은 데이터 사고이고, 그 행을 세면 봉이
    # 있는 것으로 착각한다 ([[lattice-zero-close-bug]]).
    try:
        frame = read_prices(
            store,
            as_of=as_of,
            lookback=_lookback_for(as_of, days),
            market=market,
            columns=["valid_from"],
        )
    except Exception as exc:
        return [f"시세 조회 실패: {exc}"]
    have = (
        set(pd.to_datetime(frame["valid_from"]).dt.date) if not frame.empty else set()
    )
    return [f"{day.isoformat()} 시세가 없다" for day in days if day not in have]


def failed_analysts(
    store: Store, *, market: str, as_of: datetime, days: list[date]
) -> list[str]:
    """그 이틀에 **예외로 죽은 Analyst**. 있으면 그날 결정은 반쪽이다.

    shadow 는 ``produce_signals=False`` 로 돈다 — 창고에 이미 있는 신호로
    후보를 고른다. 죽은 Analyst 는 나중에 정정본(rev1)으로 되살아나므로,
    지금 반쪽 신호로 고른 후보는 22:55 정규 실행이 낼 후보와 다르다.

    ``signals`` 를 세어 판정하지 않는다. Analyst 명단은 늘어난다 —
    새로 붙인 Analyst 하나 때문에 지난 세션이 전부 "미완" 이 되면 복구는
    영영 안 돈다. **죽었다는 사실은 그 세션에 남는다**(`analyst_failures`,
    2026-08-20 에 이 표를 만든 이유가 그것이다).
    """
    try:
        frame = store.get(
            "analyst_failures",
            as_of=as_of,
            lookback=_lookback_for(as_of, days),
            market=market,
            columns=["valid_from", "entity_id"],
        )
    except Exception as exc:
        return [f"Analyst 실패 조회 실패: {exc}"]
    if frame.empty:
        return []
    when = pd.to_datetime(frame["valid_from"]).dt.date
    out: list[str] = []
    for day in days:
        names = sorted(set(frame.loc[when == day, "entity_id"].astype(str)))
        if names:
            out.append(f"{day.isoformat()} 에 {', '.join(names)} 가 죽었다")
    return out


def missing_orders(
    store: Store, *, market: str, as_of: datetime, warmup: date
) -> list[str]:
    """전날 주문이 아직 창고에 없으면 미룬다. **여기가 핵심이다.**

    D+1 체결은 **전날(D-1) 주문**을 오늘 봉에 맞추고, 그 결과를
    ``backtest-trades-{market}-{D-1}`` 로 쓴다. 그 ``ingest_run_id`` 는 한 번만
    먹히므로 **먼저 쓴 쪽이 이긴다.** 전날 주문이 이미 창고에 있으면 이번
    실행의 워밍업은 같은 자연키를 다시 만드는 재생일 뿐이라 결과가 같다.
    없으면 우리가 **지금 창고 내용으로 전날 결정을 지어내는 것**이고, 그
    결정이 그대로 원장에 박힌다 — 2026-08-20 18:51 에 일어난 일이 그것이다.

    ``store`` 는 세션이 **실제로 쓸** 창고여야 한다. shadow 는 ``data/_shadow``
    오버레이에 주문을 쓴다 — 원본 창고를 보면 언제나 "없다" 가 나온다.
    """
    try:
        frame = store.get(
            "orders",
            as_of=as_of,
            lookback=_lookback_for(as_of, [warmup]),
            market=market,
            columns=["valid_from"],
        )
    except Exception as exc:
        return [f"주문 조회 실패: {exc}"]
    have = (
        set(pd.to_datetime(frame["valid_from"]).dt.date) if not frame.empty else set()
    )
    if warmup in have:
        return []
    return [
        f"{warmup.isoformat()} 주문이 창고에 없다 — 지금 돌리면 그날 결정을 "
        "지어내고 그 체결이 원장에 박힌다"
    ]


#: 관문 단계. **순서가 있다.**
#:
#:   prices   수집 직후. 시세가 없으면 `run_daily` 가 만드는 신호부터 빈다
#:   session  `run_daily` 직후. 그때라야 오늘 Analyst 가 살았는지 알 수 있다
#:
#: 그래서 셸이 두 번 묻는다. 한 번에 묻으면 아직 만들지도 않은 것을 없다고
#: 하게 된다.
GATE_STAGES = ("prices", "session")


def gate(
    store: Store,
    *,
    market: str,
    as_of: datetime,
    session: date,
    stage: str,
    session_store: Store | None = None,
) -> list[str]:
    """세션을 돌려도 되는지. **빈 목록이면 돌려도 된다.**

    ``session_store`` 는 세션이 주문·체결을 쓰는 창고다(shadow 는
    ``data/_shadow``). 안 주면 읽기 창고와 같은 것으로 본다 — 실전 세션이
    그렇다.
    """
    days = session_window(Market(market), session)
    if not days:
        return [f"{session.isoformat()} 앞뒤로 거래일을 못 찾았다"]
    reasons = missing_prices(store, market=market, as_of=as_of, days=days)
    if stage == "session":
        reasons += failed_analysts(store, market=market, as_of=as_of, days=days)
        reasons += missing_orders(
            session_store or store, market=market, as_of=as_of, warmup=days[0]
        )
    return reasons


def record_deferral(
    *, at: datetime, market: str, session: date, stage: str, reasons: list[str]
) -> None:
    """미룬 세션을 남긴다. **조용히 건너뛰지 않는다.**

    한 줄에 하나, 탭으로 가른다. 나중에 ``--follow-up`` 이 같은 관문으로
    재판정해 "그 뒤에 채워졌나" 를 답한다 — 로그를 사람이 읽어 판정하게
    두면 아무도 안 읽는다.
    """
    DEFERRAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = "\t".join(
        [at.isoformat(), market, session.isoformat(), stage, " · ".join(reasons)]
    )
    with DEFERRAL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def follow_up(
    store: Store, *, as_of: datetime, session_store: Store | None = None
) -> list[str]:
    """미뤄 둔 세션들이 그 뒤에 정규 경로로 채워졌는지 재판정한다.

    같은 관문 함수를 다시 부른다. 판정이 두 벌이 되면 "미룰 때는 없다더니
    확인할 때는 있다더라" 가 나온다.
    """
    if not DEFERRAL_LOG.exists():
        return []
    horizon = as_of.date() - timedelta(days=FOLLOW_UP_DAYS)
    seen: dict[tuple[str, str], str] = {}
    for raw in DEFERRAL_LOG.read_text(encoding="utf-8").splitlines():
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        _, market, session, stage = parts[:4]
        try:
            when = date.fromisoformat(session)
        except ValueError:
            continue
        if when < horizon:
            continue
        seen[(market, session)] = stage
    out: list[str] = []
    for (market, session), stage in sorted(seen.items()):
        reasons = gate(
            store,
            market=market,
            as_of=as_of,
            session=date.fromisoformat(session),
            stage=stage,
            session_store=session_store,
        )
        verdict = "채워졌다" if not reasons else f"아직 비었다 — {' · '.join(reasons)}"
        out.append(f"미룬 세션 {market} {session} ({stage}): {verdict}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    parser.add_argument(
        "--session-root",
        default="data/_shadow",
        help="세션이 주문·체결을 쓰는 창고. shadow 는 오버레이라 원본과 다르다",
    )
    parser.add_argument(
        "--gate",
        choices=GATE_STAGES,
        help="세션을 돌려도 되는지만 답한다. rc 0=READY, 3=DEFER "
        "(prices: 수집 뒤 · signals: run_daily 뒤에 묻는다)",
    )
    parser.add_argument(
        "--follow-up",
        action="store_true",
        help="미뤄 둔 세션이 그 뒤에 채워졌는지 같은 관문으로 재판정한다",
    )
    args = parser.parse_args(argv)

    # **여기서는 벽시계가 맞다.** "지금 무엇이 비었나" 가 질문이라 고정된
    # as_of 로 물으면 언제 돌려도 같은 답이 나와 복구가 안 된다.
    now = datetime.now(UTC)  # invariant-allow: wallclock
    market = args.market
    store = Store(root=Path(args.root))
    # 오버레이가 아직 없을 수도 있다(첫 실행). 그때는 주문도 당연히 없고,
    # 관문은 "없다" 로 답하는 것이 맞다 — 없는 것을 있다고 하면 안 된다.
    session_store = Store(root=Path(args.session_root))

    if args.follow_up:
        lines = follow_up(store, as_of=now, session_store=session_store)
        print("\n".join(lines) if lines else "미뤄 둔 세션 없음")
        return 0

    expected = expected_session(store, Market(market), as_of=now)
    if expected is None:
        if args.gate:
            # 휴장이면 돌릴 세션 자체가 없다. 그건 미룬 것이 아니라 할 일이
            # 없는 것이라 기록을 남기지 않는다.
            print(f"DEFER  {args.gate:8s} {market}: 공표된 세션이 없다 (휴장)")
            return 3
        print(f"OK   -         {market}: 최근 {LOOKBACK_DAYS}일에 공표된 세션이 없다 (휴장)")
        return 0

    if args.gate:
        reasons = gate(
            store,
            market=market,
            as_of=now,
            session=expected,
            stage=args.gate,
            session_store=session_store,
        )
        if reasons:
            print(
                f"DEFER  {args.gate:8s} {market} {expected.isoformat()}: "
                f"{' · '.join(reasons)}"
            )
            record_deferral(
                at=now, market=market, session=expected,
                stage=args.gate, reasons=reasons,
            )
            return 3
        window = session_window(Market(market), expected)
        span = "·".join(day.isoformat() for day in window)
        print(f"READY  {args.gate:8s} {market} {expected.isoformat()}: {span} 입력이 다 있다")
        return 0

    print(f"기대 세션 {market} {expected.isoformat()}")

    # -- 시세 ----------------------------------------------------------------
    observed = latest_session(
        store, "prices", as_of=now, market=market, column="valid_from"
    )
    if observed is None:
        print(f"NEED collect   {market} 시세: 최근 {LOOKBACK_DAYS}일에 한 행도 없다 (기대 {expected})")
    elif observed < expected:
        print(f"NEED collect   {market} 시세: 창고가 {observed.isoformat()} 까지다 (기대 {expected})")
    else:
        print(f"OK   collect   {market} 시세: {observed.isoformat()}")

    # -- 세션(신호·후보) -----------------------------------------------------
    #
    # 세션이 돌았는지는 `nav_daily` 로 본다. 세션의 마지막 산출물이라
    # 여기까지 왔으면 앞 단계는 다 지난 것이다.
    for table, job, label, axis in (("nav_daily", "session", "세션", None),):
        observed = latest_session(
            store, table, as_of=now, market=axis, column="valid_from"
        )
        if observed is None:
            print(f"NEED {job:9s} {market} {label}: 최근 {LOOKBACK_DAYS}일에 한 행도 없다 (기대 {expected})")
        elif observed < expected:
            print(f"NEED {job:9s} {market} {label}: 창고가 {observed.isoformat()} 까지다 (기대 {expected})")
        else:
            print(f"OK   {job:9s} {market} {label}: {observed.isoformat()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
