"""체크인된 설정 기본값을 창고 config 테이블에 심는다 — **발효시각을 명시해서**.

새 키(예: config_version=2 의 `risk.*`, `execution.circuit_breaker_drop`)는 창고가
모르면 세션이 ConfigNotFound 로 막힌다. 그런데 `Store.seed_config_defaults()` 를
발효시각 없이 부르면 에포크 시점과 비교해 이미 심긴 정정본까지 "바뀌었다" 고
거절한다(거짓 경고). 이 도구는 **지금 시점의 현재값**과 비교해 진짜 새 키와
진짜 바뀐 값만 정정본으로 넣고, 무엇을 넣었는지 출력한다.

    .venv/bin/python tools/seed_config.py --store data/_paper            # 미리보기
    .venv/bin/python tools/seed_config.py --store data/_paper --apply    # 적재

시각은 LiveClock 에서만 온다(불변식 2). 과거 as_of 조회는 옛 값을 그대로 본다.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.store import DEFAULT_CONFIG_FILE, Store

_config = importlib.import_module("quant_rl_trading.store.config")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="data", help="창고 루트")
    parser.add_argument("--source", default=str(DEFAULT_CONFIG_FILE), help="설정 yaml")
    parser.add_argument("--apply", action="store_true", help="실제로 적재한다 (기본은 미리보기)")
    args = parser.parse_args(argv)

    store = Store(args.store)
    source = Path(args.source).resolve()
    now = LiveClock().now()
    existing = _config.current_values(store.get(_config.CONFIG_TABLE, as_of=now))
    changed = _config.changed_names(source, existing)
    # **새 키는 에포크에, 바뀐 값은 지금 시각에.** 집행 임계치(risk.*, 서킷브레이커)는
    # 세션의 as_of(전날 마감)와 백테스트의 과거 시점에서도 읽혀야 한다 — 그때 없던
    # 키를 지금부터만 발효시키면 백테스트 주문이 전부 "설정 없음" 으로 막혀 백테스트와
    # 라이브가 갈라진다(불변식 5). 2026-09-11 09:24 재실행이 그렇게 터졌다. RL 캐시
    # 지문(2026-08-28 사고)은 RL 이 비활성이라 감수한다.
    changed_rows = [
        r for r in _config.defaults_rows(source, current=existing, effective_at=now)
        if r["entity_id"] in changed
    ]
    new_rows = [
        r for r in _config.defaults_rows(source, current=existing, effective_at=None)
        if r["entity_id"] not in changed
    ]
    rows = changed_rows + new_rows
    if not rows:
        print(f"{store.root}: 심을 것이 없다 — 창고가 {source.name} 과 같다")
        return 0
    for row in rows:
        name = row["entity_id"]
        kind = "정정" if name in changed else "신규"
        before = existing.get(name, ("—",))[0]
        born = row["valid_from"].isoformat()
        print(f"  {kind}  {name}: {before} → {row.get('value_json')!r} (발효 {born})")
    if not args.apply:
        print(f"{len(rows)}행 — 미리보기다. 적재하려면 --apply")
        return 0
    n = store.append(
        _config.CONFIG_TABLE,
        rows,
        ingest_run_id=_config.defaults_run_id(source, moment=now),
    )
    print(f"{store.root}: {n}행 적재 · 발효 {now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
