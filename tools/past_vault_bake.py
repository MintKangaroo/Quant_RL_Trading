"""과거 금고 점수 굽기 — TG(기술적 합성)·TF/TB(TTM r̂). **가격만 읽는다**(수익·라벨을 출력하지 않는다). 등록 `docs/protocols/past-vault-2026-10.md`.

    .venv/bin/python tools/past_vault_bake.py --what ta     # → data/_past_vault/_bake/ta.pkl   (session · entity_id · r_hat=합성 점수)
    .venv/bin/python tools/past_vault_bake.py --what ttm    # → data/_past_vault/_bake/ttm.pkl  (session · entity_id · r_hat)

- TA: `tools.ta_composite.composite_panel`(실전·early 와 같은 함수)을 두 조각으로 — ≤2017 은 2009-12 부터, ≥2018 은 2013-11 부터 계산한 값.
  합성의 가장 긴 의존(hs_season 4년 · trend_factor 250+6 세션)보다 겹침이 길어 한 번에 계산한 것과 같다(메모리 때문에 나눈다).
- TTM: 연 단위로 입력 계열을 만들고 `.venv-bench` 추론(`bench/tsfm_signal_infer.py`) — `tools/vault_tsfm_bake.py` 와 같은 `_context` 규칙.
  결정 세션 2011-01-03~ 와 그 앞 한 세션(하루 늦춤).
- 산출은 봉인 창고 안(`data/_past_vault/_bake`)에만 둔다. 출력은 행 수·세션 수뿐.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import wide_close_and_turnover  # noqa: E402

ROOT = Path("data/_past_vault")
OUT = ROOT / "_bake"
END = date(2021, 8, 10)
FIRST_DECISION = date(2011, 1, 3)


def bake_ta(store: Store) -> pd.DataFrame:
    from tools.ta_composite import composite_panel
    end = datetime.combine(END, time(16, 0), tzinfo=UTC)
    a, _ = composite_panel(store, end=end, start=date(2009, 12, 1))
    a = a.loc[[d for d in a.index if d < date(2018, 1, 1)]]
    b, _ = composite_panel(store, end=end, start=date(2013, 11, 1))
    b = b.loc[[d for d in b.index if d >= date(2018, 1, 1)]]
    comp = pd.concat([a, b]).sort_index()
    comp = comp.loc[[d for d in comp.index if d >= date(2010, 12, 1)]]
    long = comp.stack().rename("r_hat").reset_index()
    long.columns = ["session", "entity_id", "r_hat"]
    return long


def bake_ttm(store: Store) -> pd.DataFrame:
    from tools import diag_tsfm_zeroshot as z
    close, _ = wide_close_and_turnover(store, as_of=datetime.combine(END, time(23, 59), tzinfo=UTC),
                                       lookback=(END - date(2009, 12, 1)).days, market="KR")
    cols = [c for c in close.columns if isinstance(c, str) and c.startswith("KR:") and not c.startswith(("KR:ETF:", "KR:IDX:"))]
    close = close[cols]
    idx = list(close.index)
    pos_all = [i for i, d in enumerate(idx) if d >= FIRST_DECISION]
    pos_all = [pos_all[0] - 1, *pos_all]
    parts = []
    for year in sorted({idx[i].year for i in pos_all}):
        pos = [i for i in pos_all if idx[i].year == year]
        keys, prices = [], []
        for i in pos:
            p, _s, keep = z._context(close, i, list(close.iloc[i].dropna().index))
            keys.append(pd.DataFrame({"session": idx[i], "entity_id": keep}))
            prices.append(p)
        work = OUT / f"ttm-{year}"
        work.mkdir(parents=True, exist_ok=True)
        np.savez(work / "ctx.npz", price=np.concatenate(prices))
        proc = subprocess.run([".venv-bench/bin/python", "bench/tsfm_signal_infer.py", str(work)], capture_output=True, text=True,
                              env={"HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "6", "PATH": "/usr/bin:/bin"})
        if proc.returncode != 0:
            raise SystemExit(f"{year} 추론 실패: {proc.stderr[-600:]}")
        pred = np.load(work / "pred.npz")["price"]
        k = pd.concat(keys, ignore_index=True)
        k["r_hat"] = pred[:, -1] / pred[:, 0] - 1.0
        (work / "ctx.npz").unlink()
        parts.append(k)
        print(f"  {year}: {len(pos)}세션 · {len(k):,}행", flush=True)
    return pd.concat(parts, ignore_index=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--what", choices=("ta", "ttm"), required=True)
    args = ap.parse_args(argv)
    out = OUT / f"{args.what}.pkl"
    if out.exists():
        print(f"{out} 있음 — 건너뛴다")
        return 0
    store = Store(root=ROOT)
    frame = bake_ta(store) if args.what == "ta" else bake_ttm(store)
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(out)
    print(f"→ {out} · {len(frame):,}행 · 세션 {frame['session'].nunique()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
