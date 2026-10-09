"""금고 early 창(2026-07-01~09-30) TTM 제로샷 예측 굽기 — 시행 TF·TB·TC 의 입력(docs/protocols/vault-early-additions-2026-10.md).

    .venv/bin/python tools/vault_tsfm_bake.py          # 입력 계열 → (.venv-bench) TTM → data/_diag/vault-early/tsfm-KR.pkl

**가격만 읽는다** — 라벨·수익을 만들지 않는다. 세션마다 그 세션 종가까지의 보정 종가 256일(제로샷 진단 `_context` 규칙),
대상 = 그날 거래가능 국장 보통주 전체(이력 240일 이상). 창 첫 세션 앞 한 세션을 더 굽는다 — 판정은 **하루 늦춤**(전날 밤 계산한 예측을
그날 결정에)이라 첫 세션의 제외 목록이 필요하다. 금고 굽기 허용일(10/1) 전에는 돌지 않는다(vault_judge 창 규칙).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import wide_close_and_turnover  # noqa: E402
from tools import diag_tsfm_zeroshot as z  # noqa: E402
from tools import vault_judge as vj  # noqa: E402

OUT = Path("data/_diag/vault-early/tsfm")


def main() -> int:
    win = vj.windows()["early"]
    if vj._today() < win.bake_from:
        print(f"금고 굽기 허용일({win.bake_from}) 전 — 돌지 않는다", flush=True)
        return 2
    store = Store(root=Path("data"))
    as_of = datetime.combine(win.end, time(23, 59), tzinfo=UTC)
    close, _ = wide_close_and_turnover(store, as_of=as_of, lookback=(win.end - date(2025, 1, 1)).days, market="KR")
    cols = [c for c in close.columns if isinstance(c, str) and c.startswith("KR:") and not c.startswith(("KR:ETF:", "KR:IDX:"))]
    close = close[cols]
    close = close[close.index <= win.end]
    assert max(close.index) <= win.end, "금고 창 끝 뒤가 읽혔다"
    idx = list(close.index)
    pos = [i for i, d in enumerate(idx) if d >= win.start]
    pos = [pos[0] - 1, *pos]                       # 첫 세션 앞 하나(하루 늦춤)
    keys, prices = [], []
    for i in pos:
        ents = list(close.iloc[i].dropna().index)
        p, _s, keep = z._context(close, i, ents)
        keys.append(pd.DataFrame({"session": idx[i], "entity_id": keep}))
        prices.append(p)
        print(f"  {idx[i]}: {len(keep)}종목", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    k = pd.concat(keys, ignore_index=True)
    np.savez(OUT / "ctx.npz", price=np.concatenate(prices))
    proc = subprocess.run([".venv-bench/bin/python", "bench/tsfm_signal_infer.py", str(OUT)], capture_output=True, text=True,
                          env={"HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "8", "PATH": "/usr/bin:/bin"})
    if proc.returncode != 0:
        print(f"추론 실패: {proc.stderr[-600:]}", flush=True)
        return 5
    pred = np.load(OUT / "pred.npz")["price"]
    k["r_hat"] = pred[:, -1] / pred[:, 0] - 1.0
    k.to_pickle(OUT.parent / "tsfm-KR.pkl")  # invariant-allow: data-access — 금고 작업 캐시
    print(f"→ {OUT.parent / 'tsfm-KR.pkl'} · {len(k):,}행 · 세션 {k['session'].nunique()} ({k['session'].min()}~{k['session'].max()})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
