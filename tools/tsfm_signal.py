"""TTM 제로샷 5일 예측 신호 — 시행 IX-T 의 하한 기준(docs/protocols/ixt-tsfm-losers-2026-10.md).

    .venv/bin/python tools/tsfm_signal.py [--dry]   # 입력 계열 → (.venv-bench) TTM 추론 → signals 에 analyst=tsfm 적재

밤마다 마지막 국장 종가까지로 돈다. `valid_from = observed_at = 계산 시각` 이라 **그날 밤 세션이 아니라 다음 세션**의 결정에만
보인다(하루 늦춤 — 진단 `logs/tsfm-ix-lag-20261009.log` 에서 늦춰도 남는 것을 확인했다). `tsfm` 은 제약 Analyst 다
(`selector.constraints.CONSTRAINT_ANALYSTS`) — 가중치가 붙어도 알파 합성에 들어가지 않고, 신뢰도는 0 으로 적는다.
대상: 그날 원 거래대금 20일 평균 상위 300 ∪ KOSPI200 구성종목(국장 보통주). 입력·예측 규칙은 제로샷 진단(`diag_tsfm_zeroshot`)과 같다.
rc: 0 적재 · 3 이미 적재(같은 세션) · 4 입력 없음 · 5 추론 실패.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.signal import Signal  # noqa: E402
from quant_rl_trading.selector.constraints import TSFM_ANALYST  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import wide_close_and_turnover  # noqa: E402
from tools import diag_tsfm_zeroshot as z  # noqa: E402

VERSION = "ttm-r2-zeroshot-L256-v1"
WORK = Path("data/_diag/tsfm-live")
BENCH_PY = Path(".venv-bench/bin/python")
SOURCE = "tsfm_signal"


def build_inputs(store: Store, now) -> tuple[pd.Timestamp, list[str], np.ndarray] | None:  # type: ignore[no-untyped-def]
    close, dv = wide_close_and_turnover(store, as_of=now, lookback=420, market="KR")
    cols = [c for c in close.columns if isinstance(c, str) and c.startswith("KR:") and not c.startswith(("KR:ETF:", "KR:IDX:"))]
    close, dv = close[cols], dv.reindex(columns=cols)
    if len(close) < z.CTX + 1:
        return None
    i = len(close) - 1
    session = close.index[i]
    top = list(dv.iloc[i].dropna().sort_values(ascending=False).index[: z.UNIVERSE])
    im = store.get("index_members", as_of=now, lookback=14, market="KR")
    if not im.empty:
        im = im[im["index_id"].astype(str).str.contains("KOSPI200")]
        last = pd.to_datetime(im["valid_from"]).max()
        top += [e for e in im[pd.to_datetime(im["valid_from"]) == last]["entity_id"] if e in close.columns and e not in top]
    price, _sq, keep = z._context(close, i, top)
    return session, keep, price


def main() -> int:
    dry = "--dry" in sys.argv[1:]
    store = Store(root=Path("data"))
    clock = LiveClock()
    now = clock.now()
    built = build_inputs(store, now)
    if built is None:
        print("입력 없음 — 가격 이력이 모자란다", flush=True)
        return 4
    session, keep, price = built
    run_id = f"{SOURCE}-{session}-{VERSION}"
    if store.ingest_run_recorded("signals", run_id):
        print(f"{session} 이미 적재 — 건너뛴다", flush=True)
        return 3
    day = WORK / str(session)
    day.mkdir(parents=True, exist_ok=True)
    np.savez(day / "ctx.npz", price=price)
    started = clock.now()
    proc = subprocess.run([str(BENCH_PY), "bench/tsfm_signal_infer.py", str(day)], capture_output=True, text=True,
                          env={"HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "4", "PATH": "/usr/bin:/bin"})
    if proc.returncode != 0 or not (day / "pred.npz").exists():
        print(f"추론 실패 rc {proc.returncode}: {proc.stderr[-800:]}", flush=True)
        return 5
    pred = np.load(day / "pred.npz")["price"]
    r_hat = pred[:, -1] / pred[:, 0] - 1.0
    ok = np.isfinite(r_hat)
    pct = pd.Series(r_hat[ok]).rank(pct=True).to_numpy()
    score = np.clip(2.0 * pct - 1.0, -1.0, 1.0)
    finished = clock.now()
    latency = (finished - started).total_seconds() * 1000.0 / max(int(ok.sum()), 1)
    digest = hashlib.sha256(price.tobytes()).hexdigest()[:16]
    rows = [Signal(analyst=TSFM_ANALYST, analyst_version=VERSION, entity_id=e, as_of=finished, score=float(s),
                   confidence=0.0, horizon_days=5, features_hash=digest, latency_ms=latency).row(observed_at=finished, source=SOURCE)
            for e, s in zip(np.array(keep)[ok], score, strict=True)]
    if dry:
        print(f"--dry — 적재하지 않았다 ({len(rows)}행)", flush=True)
    else:
        store.append("signals", rows, ingest_run_id=run_id, source=SOURCE)
    print(f"{session} 종가까지 · {len(rows)}종목 tsfm 적재(valid_from {finished:%F %T}) · 하위 10% 경계 r̂ "
          f"{np.nanquantile(r_hat, 0.1):+.4f} · 다음 세션 {session + timedelta(days=1)} 이후 결정에 보인다", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
