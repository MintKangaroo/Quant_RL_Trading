"""기술적 합성 점수 신호 — 시행 TG 의 하한 기준(docs/protocols/ta-floor-2026-10.md).

    .venv/bin/python tools/ta_signal.py [--dry]     # 마지막 국장 종가까지 → signals 에 analyst=ta 적재

`valid_from = observed_at = 계산 시각` → 그 다음 결정 세션에만 보인다(하루 늦춤, TF·tsfm 과 같다). `ta` 는 제약 Analyst
(`selector.constraints.CONSTRAINT_ANALYSTS`) — 알파 합성에 들어가지 않고 신뢰도 0. 점수 = 합성의 대상 안 백분위를 [−1, 1] 로.
rc: 0 적재 · 3 이미 적재(같은 세션) · 4 입력 없음.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.signal import Signal  # noqa: E402
from quant_rl_trading.selector.constraints import TA_ANALYST  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.ta_composite import composite_panel  # noqa: E402

VERSION = "ta9-ew-pct-v1"
SOURCE = "ta_signal"


def main() -> int:
    dry = "--dry" in sys.argv[1:]
    store = Store(root=Path("data"))
    clock = LiveClock()
    started = clock.now()
    comp, _uni = composite_panel(store, end=started)
    rows_ok = comp.notna().sum(axis=1)
    rows_ok = rows_ok[rows_ok >= 100]
    if rows_ok.empty:
        print("입력 없음 — 합성 점수가 100종목 이상인 세션이 없다", flush=True)
        return 4
    session = rows_ok.index[-1]
    run_id = f"{SOURCE}-{session}-{VERSION}"
    if store.ingest_run_recorded("signals", run_id):
        print(f"{session} 이미 적재 — 건너뛴다", flush=True)
        return 3
    last = comp.loc[session].dropna()
    score = (2.0 * last.rank(pct=True) - 1.0).clip(-1.0, 1.0)
    finished = clock.now()
    latency = (finished - started).total_seconds() * 1000.0 / max(len(score), 1)
    digest = hashlib.sha256(np.ascontiguousarray(last.to_numpy()).tobytes()).hexdigest()[:16]
    rows = [Signal(analyst=TA_ANALYST, analyst_version=VERSION, entity_id=str(e), as_of=finished, score=float(s), confidence=0.0,
                   horizon_days=5, features_hash=digest, latency_ms=latency).row(observed_at=finished, source=SOURCE)
            for e, s in score.items()]
    if dry:
        print(f"--dry — 적재하지 않았다 ({len(rows)}행)", flush=True)
    else:
        store.append("signals", rows, ingest_run_id=run_id, source=SOURCE)
    print(f"{session} 종가까지 · {len(rows)}종목 ta 적재(valid_from {finished:%F %T}) · 하위 20% {int(len(rows) * 0.2)}종목 · 다음 결정 세션부터 보인다", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
