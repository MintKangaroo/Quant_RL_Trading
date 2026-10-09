"""Chronos-Bolt 분위수 예측(10·50·90%) — `.venv-bench` 전용, 입력 계열만 읽는다. 오를 종목 탐색(상단 꼬리) 진단용(2026-10-09).

    .venv-bench/bin/python bench/tsfm_quantile_infer.py data/_diag/tsfm-daily [data/_diag/tsfm-daily-long]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import torch

LEVELS = [0.1, 0.5, 0.9]


def main() -> int:
    from chronos import BaseChronosPipeline
    torch.set_num_threads(10)
    pipe = BaseChronosPipeline.from_pretrained("amazon/chronos-bolt-small", device_map="cpu", torch_dtype=torch.float32)
    for root in map(Path, sys.argv[1:]):
        x = np.load(root / "inputs" / "ctx-KR-D.npz")["price"][:, -256:].astype(np.float32)
        t0, out = time.perf_counter(), []
        for i in range(0, len(x), 256):
            q, _ = pipe.predict_quantiles(torch.from_numpy(x[i:i + 256]), prediction_length=6, quantile_levels=LEVELS)
            out.append(q.numpy().astype(np.float32))
        np.savez(root / "preds" / "chronos_bolt_q.npz", q=np.concatenate(out), levels=np.array(LEVELS))
        print(f"{root}: {len(x):,}계열 · {time.perf_counter() - t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
