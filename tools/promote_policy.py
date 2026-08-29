"""정책을 장부에 끼우거나 뺀다 — `allocator.rl.checkpoint` · `allocator.rl.modes` 정정본.

    # OOS 판정을 통과한 체크포인트를 모의계좌(paper)에 끼운다
    .venv/bin/python tools/promote_policy.py --checkpoint data/rl_checkpoints/<run>.pt

    # 실제 장부로 오늘의 결정을 미리 본다 (설정은 안 바꾼다)
    .venv/bin/python tools/promote_policy.py --checkpoint ... --dry-run --store data/_paper

    # 뺀다 — 다음 세션부터 룰로 돌아간다
    .venv/bin/python tools/promote_policy.py --off

설정은 **실전 창고(data/)의 config 표**에 적는다. 모의계좌 장부(`data/_paper`)와
shadow(`data/_shadow`)는 config 를 링크로 그 표를 본다 — 어느 장부가 정책을
쓰는지는 `modes` 가 가른다(기본 paper 만). yaml 의 `checkpoint: ""` 는
"창고가 정본" 이라는 뜻이라 시딩이 이 키를 만지지 않는다 (store/config.py).

정정본은 지금부터 발효한다(`effective_at` = 벽시계). 과거 as_of 에서는 정책이
없었던 그대로다 — 백테스트·캐시 지문이 소급해서 바뀌지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.tables import CONFIG_TABLE  # noqa: E402
from tools.backfill import build_store  # noqa: E402

CHECKPOINT_KEY = "allocator.rl.checkpoint"
MODES_KEY = "allocator.rl.modes"
SOURCE = "promote-policy"


def _next_revision(store: Store, name: str, now: datetime) -> int:
    frame = store.get(CONFIG_TABLE, as_of=now, entity=name)
    return int(frame["revision"].max()) + 1 if not frame.empty else 0


def write_config(store: Store, *, checkpoint: str, modes: list[str], now: datetime) -> None:
    rows = []
    for name, value in ((CHECKPOINT_KEY, checkpoint), (MODES_KEY, modes)):
        rows.append({
            "entity_id": name, "valid_from": now, "observed_at": now, "source": SOURCE,
            "revision": _next_revision(store, name, now),
            "value_json": json.dumps(value, ensure_ascii=False),
        })
    store.append(CONFIG_TABLE, rows, ingest_run_id=f"promote-policy-{now:%Y%m%d%H%M%S}")


def dry_run(store_root: Path, *, checkpoint: str, market: str, now: datetime) -> int:
    """오늘 장부로 정책의 결정을 한 번 낸다. 창고에는 아무것도 쓰지 않는다."""
    from quant_rl_trading.accounting import ledger, snapshot
    from quant_rl_trading.accounting.rates import Rates
    from quant_rl_trading.allocator import live
    from quant_rl_trading.collectors.market_hours import Market
    from quant_rl_trading.replay.clock import ReplayClock
    from quant_rl_trading.selector import pipeline as selector_pipeline
    from tools.run_session import last_settled_day

    store = Store(root=store_root)
    day = last_settled_day(store, Market(market), now)
    if day is None:
        print("거래일을 찾지 못했다", file=sys.stderr)
        return 1
    as_of = now.replace(year=day.year, month=day.month, day=day.day)
    rates = Rates.from_store(store, as_of=as_of)
    book = ledger.build_book(store, as_of=as_of, rates=rates)
    snap = snapshot.take(store, ReplayClock(as_of), as_of=as_of, book=book)
    held = [e for e, p in book.positions.items() if p.quantity > 0]
    selection = selector_pipeline.run(
        store, as_of=as_of, market=market, equity=snap.valuation.nav, held=held
    )
    decision = live.decide(
        store, as_of=as_of, market=market, book=book, nav=snap.valuation.nav,
        drawdown=snap.drawdown,
        candidates=[(i.entity_id, i.score) for i in selection.candidates],
        params=live.LiveParams(checkpoint=checkpoint, modes=("paper",)),
        hyper_as_of=as_of,
    )
    print(
        f"{market} {day} · 창고 {store.root} · NAV {snap.valuation.nav:,.0f} · "
        f"낙폭 {snap.drawdown:.2%}"
    )
    print(
        f"  체크포인트 {decision.checkpoint} · 업데이트 {decision.update} · "
        f"슬롯 {len(decision.slots)} (빠짐 {decision.slots_dropped}) · "
        f"concentration 합 {decision.concentration_total:.1f}"
    )
    print(f"  현금 {decision.cash_weight:.1%} · 미룬 매수 {len(decision.deferred)}")
    for entity, weight in sorted(decision.weights.items(), key=lambda kv: -kv[1])[:30]:
        mark = " (보유)" if entity in held else ""
        wait = decision.delays.get(entity, 0)
        print(f"    {entity:<12} {weight:6.1%}{mark}{f' · 지연 {wait}일' if wait else ''}")
    for note in decision.notes:
        print(f"  · {note}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--checkpoint", default="", help="정책 체크포인트 경로 (저장소 루트 기준)")
    parser.add_argument(
        "--modes", default="paper", help="정책이 결정하는 장부 모드, 쉼표 구분 (기본 paper)"
    )
    parser.add_argument("--off", action="store_true", help="정책을 뺀다 — checkpoint 를 빈 값으로")
    parser.add_argument("--dry-run", action="store_true", help="설정을 바꾸지 않고 결정만 출력")
    parser.add_argument("--store", default="data/_paper", help="--dry-run 이 볼 장부")
    parser.add_argument("--data-root", default=None, help="config 를 적을 실전 창고")
    parser.add_argument("--market", default="KR")
    args = parser.parse_args()

    load_env()
    now = LiveClock().now()
    if not args.off:
        if not args.checkpoint:
            parser.error("--checkpoint 나 --off 중 하나는 있어야 한다")
        path = Path(args.checkpoint)
        if not path.exists():
            parser.error(f"체크포인트가 없다: {path}")
        # 열리는지 먼저 본다. 모양이 안 맞는 파일을 설정에 적으면 다음 세션이 죽는다.
        from quant_rl_trading.allocator import live
        from quant_rl_trading.allocator.env import EnvParams

        probe = build_store(Path(args.data_root) if args.data_root else None)
        params = EnvParams.from_store(probe, as_of=now, hyper_as_of=now)
        _policy, update, overrides = live.load_policy(path, params)
        print(f"체크포인트 열림: {path} · 업데이트 {update} · 환경 {overrides or '기본'}")

    if args.dry_run:
        return dry_run(Path(args.store), checkpoint=args.checkpoint, market=args.market, now=now)

    store = build_store(Path(args.data_root) if args.data_root else None)
    modes = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    checkpoint = "" if args.off else str(Path(args.checkpoint))
    write_config(store, checkpoint=checkpoint, modes=modes, now=now)
    state = (
        "뺐다 — 다음 세션부터 룰" if args.off
        else f"끼웠다 — 모드 {modes} 의 다음 세션부터 정책"
    )
    print(f"{store.root} config 정정 ({now:%Y-%m-%d %H:%M}) · {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
