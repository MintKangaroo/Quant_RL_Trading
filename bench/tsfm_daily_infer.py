"""TSFM 매일 예측 — `.venv-bench` 전용. tools/diag_tsfm_daily.py extract 가 쓴 입력 계열만 읽는다(라벨을 보지 않는다).

    .venv-bench/bin/python bench/tsfm_daily_infer.py [--threads 10]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsfm_zeroshot_infer import Forecaster  # noqa: E402

ROOT = Path("data/_diag/tsfm-daily")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--models", nargs="+", default=["chronos_bolt", "ttm"])
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    price = np.load(ROOT / "inputs" / "ctx-KR-D.npz")["price"][:, -256:].astype(np.float32)
    (ROOT / "preds").mkdir(parents=True, exist_ok=True)
    for m in a.models:
        t0 = time.perf_counter()
        fc = Forecaster(m)
        fc.deadline = t0 + 3 * 3600
        out = fc(price)
        np.savez(ROOT / "preds" / f"{m}.npz", price=out)
        print(f"{m}: {len(price):,}계열 · {time.perf_counter() - t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
