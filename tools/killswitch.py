"""킬스위치 상태 확인·수동 발동·해제 — 런북 §4 의 명령.

    .venv/bin/python tools/killswitch.py status                     # 기본 창고: 모의계좌 장부(data/_paper)
    .venv/bin/python tools/killswitch.py engage --reason "..."
    .venv/bin/python tools/killswitch.py release --confirm --by 이름 [--verified "증거"]

## 왜 이 도구가 있나

런북은 ``python -m quant_rl_trading.executor.killswitch`` 를 적어 두었는데 그 모듈은 없었다. 2026-09-22 13:19 에 해제할 때
``guards.release`` 를 손으로 불렀다. 해제는 **사람만** 한다(guards.release 의 계약) — 그러니 사람이 쓰는 명령이 있어야 한다.

## 해제는 미확정 주문을 먼저 본다

그날의 발동 원인은 "전송 결과 미확정"(submitting) 주문 하나였다. 해제 전 체크리스트(런북 §4)의 "미체결 주문이 남아 있지 않은가" 를
도구가 대신 센다: 장부에 **submitting · 주문번호 없는 sent · 취소/정정 미확정**이 남아 있으면, ``--verified`` 로 **확인한 증거**
(예: "t0425 전체 조회에 없음, 보유 24종목 수량 일치")를 적어야 해제된다. 증거는 해제 사유에 그대로 남는다.
이 도구는 증권사를 조회하지 않는다 — 확인은 사람이 하고, 도구는 그 확인이 기록되게 한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.executor import guards  # noqa: E402
from quant_rl_trading.executor.orders import client_order_id  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402

#: 해제 전에 사람이 확인해야 하는 상태 — reconcile_fills.UNRESOLVED_STATUSES 와 같은 뜻.
UNRESOLVED = frozenset({"submitting", "sent", "cancel_unknown", "modify_unknown"})
BROKER_ORDER_NO_PREFIX = "broker_order_no="


def open_store(sandbox: str) -> Store:
    if not sandbox:
        return build_store(None)
    layer = overlay.build(root=Path(sandbox), source=build_store(None).root, writable=JOURNAL)
    return Store(root=layer.root)


def unresolved(store: Store, now) -> list[str]:
    """최근 3일 장부에서 마지막 상태가 미확정인 주문. sent 는 주문번호가 없을 때만(있으면 대사가 찾는다)."""
    frame = store.get("orders", as_of=now, lookback=3)
    if frame.empty:
        return []
    last = frame.sort_values(["observed_at", "revision"]).groupby(["session_id", "entity_id", "slice_seq"]).tail(1)
    out = []
    for row in last.itertuples():
        if row.status not in UNRESOLVED:
            continue
        if row.status == "sent" and str(row.reason or "").startswith(BROKER_ORDER_NO_PREFIX):
            continue
        oid = client_order_id(session=row.session_id, entity_id=row.entity_id, slice_seq=int(row.slice_seq))
        out.append(f"{row.session_id} {row.entity_id} 조각 {int(row.slice_seq)} · {row.status} · {oid}"
                   + (f" · {row.reason}" if row.reason else ""))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sandbox", default="data/_paper", help="장부(기본 모의계좌). 빈 문자열이면 주 창고")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_engage = sub.add_parser("engage")
    p_engage.add_argument("--reason", required=True)
    p_engage.add_argument("--by", default="manual")
    p_release = sub.add_parser("release")
    p_release.add_argument("--confirm", action="store_true", help="해제 확인(없으면 체크리스트만 보이고 끝난다)")
    p_release.add_argument("--by", required=True, help="해제한 사람")
    p_release.add_argument("--verified", default="", help="미확정 주문을 증권사 기록으로 확인한 증거")
    args = parser.parse_args(argv)

    store = open_store(args.sandbox)
    now = LiveClock().now()
    state, reason = guards.killswitch_state(store, as_of=now)
    print(f"킬스위치: {state} — {reason or '(사유 없음)'} · 장부 {args.sandbox or '주 창고'}")

    if args.cmd == "status":
        pending = unresolved(store, now)
        print(f"미확정 주문 {len(pending)}건" + ("" if not pending else ":"))
        for line in pending:
            print(f"  · {line}")
        return 0

    if args.cmd == "engage":
        guards.engage(store, as_of=now, observed_at=now, reason=args.reason, by=args.by)
        print(f"발동: {guards.killswitch_state(store, as_of=LiveClock().now())}")
        return 0

    # release
    if str(state) != "engaged":
        print("걸려 있지 않다 — 할 일이 없다.")
        return 0
    pending = unresolved(store, now)
    print("해제 전 체크리스트(런북 §4): ① 발동 원인 해소 ② 장부·계좌 보유 수량 일치 ③ 데이터 품질 게이트 ④ 미확정 주문 없음")
    print(f"④ 미확정 주문 {len(pending)}건")
    for line in pending:
        print(f"  · {line}")
    if pending and not args.verified:
        print("미확정 주문이 남아 있다 — 증권사 기록(t0425 등)으로 확인하고 --verified \"증거\" 로 다시 부른다.", file=sys.stderr)
        return 2
    if not args.confirm:
        print("--confirm 이 없어 해제하지 않았다.")
        return 1
    note = f"{args.by} 수동 해제 — 발동 사유: {reason}"
    if args.verified:
        note += f" · 확인: {args.verified}"
    guards.release(store, as_of=now, observed_at=now, by=args.by, reason=note)
    print(f"해제: {guards.killswitch_state(store, as_of=LiveClock().now())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
