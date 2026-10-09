"""TTM 추론 — `.venv-bench` 전용. tools/tsfm_signal.py 가 쓴 <dir>/ctx.npz(가격 계열만)를 읽어 <dir>/pred.npz 를 쓴다."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsfm_zeroshot_infer import Forecaster  # noqa: E402


def main() -> int:
    d = Path(sys.argv[1])
    torch.set_num_threads(4)
    price = np.load(d / "ctx.npz")["price"][:, -256:].astype(np.float32)
    fc = Forecaster("ttm")
    np.savez(d / "pred.npz", price=fc(price))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
