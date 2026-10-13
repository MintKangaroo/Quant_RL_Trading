"""TTM 일봉판(r2.1) 대 현행 main — 제로샷 추론(docs/diag/tsfm-ttm-daily-2026-10-13.md). `.venv-bench` 에서만 돈다.

10/9 TSFM 제로샷 진단(docs/diag/tsfm-zeroshot.md)의 **같은 입력 계열**(`data/_diag/tsfm-zeroshot/inputs/ctx-KR-{A,B}.npz`,
보정 종가 256개)만 읽는다 — 라벨 경로를 받지 않는다(방화벽). 예측은 `data/_diag/tsfm-ttm-daily/preds/{변형}-KR-{창}.npz`.
기존 `tsfm_zeroshot_infer.py`·`tsfm_signal_infer.py`(밤 00:15 신호)는 건드리지 않는다.

    nice -n 10 .venv-bench/bin/python bench/tsfm_ttm_variants.py --threads 4
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import torch

SRC = Path("data/_diag/tsfm-zeroshot/inputs")
OUT = Path("data/_diag/tsfm-ttm-daily")
REPO = "ibm-granite/granite-timeseries-ttm-r2"
H = 6
BATCH = 256
FREQ_D = 8      # tsfm_public DEFAULT_FREQUENCY_MAPPING["D"]
FREQ_OOV = 0
# 변형: (리비전, 쓰는 실제 종가 수, 빈도 토큰 | None)
VARIANTS = {
    "V0_main": ("main", 256, None),                         # 현행 — 256 을 512 로 앞채움·관측표지 0
    "V1_180-60-l1": ("180-60-ft-l1-r2.1", 180, FREQ_D),    # 패딩 없음(마지막 180)
    "V1b_90-30": ("90-30-ft-r2.1", 90, FREQ_D),             # 패딩 없음(마지막 90)
    "V2_360-60-l1": ("360-60-ft-l1-r2.1", 256, FREQ_D),    # 입력이 256 뿐 — 앞 104 를 채우고 가린다
    "V3_512-48": ("512-48-ft-r2.1", 256, FREQ_D),          # V0 와 같은 패딩, 일봉 토큰
    "V3o_512-48-oov": ("512-48-ft-r2.1", 256, FREQ_OOV),   # V3 에서 토큰만 oov — 토큰 효과 분리
}


def forecast(m, x: np.ndarray, freq: int | None) -> np.ndarray:
    ctx = m.config.context_length
    x = x[:, -ctx:]
    pad = ctx - x.shape[1]
    xt = torch.from_numpy(np.concatenate([np.repeat(x[:, :1], pad, axis=1), x], axis=1)).unsqueeze(-1)
    mask = torch.ones_like(xt, dtype=torch.bool)
    mask[:, :pad] = False
    kw = {} if freq is None else {"freq_token": torch.full((len(x),), freq, dtype=torch.long)}
    with torch.no_grad():
        y = m(past_values=xt, past_observed_mask=mask, **kw).prediction_outputs
    return y[:, :H, 0].numpy()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    a = ap.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    torch.set_num_threads(a.threads)
    from huggingface_hub import snapshot_download
    from tsfm_public import TinyTimeMixerForPrediction

    (OUT / "preds").mkdir(parents=True, exist_ok=True)
    rec = {"threads": a.threads, "cells": {}}
    for v in a.variants:
        rev, use, freq = VARIANTS[v]
        snap = snapshot_download(REPO, revision=rev)       # 오프라인 — 이미 받은 스냅숏 경로(커밋 해시를 기록한다)
        m = TinyTimeMixerForPrediction.from_pretrained(snap).eval()
        sha = Path(snap).name
        for w in ("A", "B"):
            x = np.load(SRC / f"ctx-KR-{w}.npz")["price"][:, -use:].astype(np.float32)
            t0 = time.perf_counter()
            out = np.concatenate([forecast(m, x[i:i + BATCH], freq) for i in range(0, len(x), BATCH)])
            secs = time.perf_counter() - t0
            np.savez(OUT / "preds" / f"{v}-KR-{w}.npz", price=out.astype(np.float32))
            rec["cells"][f"{v}-KR-{w}"] = {"revision": rev, "snapshot": sha, "context": m.config.context_length, "closes": use,
                                           "freq_token": freq, "n": len(x), "seconds": secs, "ms_per_series": 1000 * secs / len(x)}
            print(v, w, rec["cells"][f"{v}-KR-{w}"], flush=True)
        del m
    (OUT / "preds" / "run.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
