"""TSFM 제로샷 진단 — 2단계 추론(docs/diag/tsfm-zeroshot.md §1). `.venv-bench` 에서만 돈다.

**입력 계열 파일만 읽는다** — 라벨 경로를 받지 않는다(방화벽). 1단계 `tools/diag_tsfm_zeroshot.py extract` 가 쓴
`data/_diag/tsfm-zeroshot/inputs/ctx-{market}-{window}.npz`(price·sq, 행 = keys 파일(.pkl)의 행)를 읽어
모델마다 지평 6 중앙값 예측을 `preds/{model}-{market}-{window}-L{L}.npz` 로 쓴다.

    .venv-bench/bin/python bench/tsfm_zeroshot_infer.py --threads 10
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path("data/_diag/tsfm-zeroshot")
H = 6
BATCH = 256
BUDGET_S = 90 * 60
MODELS = ["ttm", "chronos_bolt", "moirai2", "timesfm", "chronos2", "kronos_small", "kronos_base"]
KRONOS = {"kronos_small": "NeoQuasar/Kronos-small", "kronos_base": "NeoQuasar/Kronos-base"}
KRONOS_HF = "/mnt/d/quant_rl_trading/hf"
# 외장 D: 는 chkdsk 정상 판정 뒤에만 읽는다(10/7 리드). 판정이 나면 리드 지시로 이 파일을 만든다. 없으면 Kronos 칸은
# D: 를 건드리지 않고 "미측정(디스크 점검 대기)" 로 적는다. 다른 모델(SSD)은 그대로 돈다.
D_DRIVE_OK = Path("data/_bench/D_DRIVE_OK")
KRONOS_TOP = 100      # §1-보충 2: 세션마다 거래대금 상위 100(keys 행 순서 = 거래대금 순)
KRONOS_CODE = "/mnt/d/quant_rl_trading/tools/kronos-67b630e"   # GitHub shiyu-coder/Kronos @ 67b630e 의 model/ (MIT)
# §1 순서: 창 A·L256 → 창 A·L64 → 창 B(TTM·Bolt 만, L256)
PLAN = [("A", 256), ("A", 64), ("B", 256)]
WINDOW_B_MODELS = {"ttm", "chronos_bolt"}
MARKETS = ["KR", "US"]


def avail_mb() -> int:
    return int(subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines()[1].split()[6])


class Forecaster:
    """모델별 '계열 (N, L) → 지평 6 중앙값 (N, 6)'."""

    def __init__(self, name: str):
        self.name = name
        self.revision = None
        self._cache: dict = {}

    deadline = float("inf")

    def __call__(self, x: np.ndarray) -> np.ndarray:
        L = x.shape[1]
        fn = getattr(self, f"_{self.name}")
        out = []
        for i in range(0, len(x), BATCH):
            if time.perf_counter() > self.deadline:
                raise TimeoutError("모델 예산 90분 소진(칸 도중)")
            out.append(np.asarray(fn(x[i:i + BATCH], L), dtype=np.float32))
        return np.concatenate(out) if out else np.zeros((0, H), np.float32)

    def _ttm(self, x, L):
        from tsfm_public import TinyTimeMixerForPrediction

        if "m" not in self._cache:
            self._cache["m"] = TinyTimeMixerForPrediction.from_pretrained("ibm-granite/granite-timeseries-ttm-r2").eval()
        m = self._cache["m"]
        ctx = m.config.context_length
        pad = ctx - L                                    # 앞을 첫 값으로 채우고 관측 표지 0 으로 가린다
        xt = torch.from_numpy(np.concatenate([np.repeat(x[:, :1], pad, axis=1), x], axis=1)).unsqueeze(-1)
        mask = torch.ones_like(xt, dtype=torch.bool)
        mask[:, :pad] = False
        with torch.no_grad():
            y = m(past_values=xt, past_observed_mask=mask).prediction_outputs
        return y[:, :H, 0].numpy()

    def _chronos_bolt(self, x, L):
        from chronos import BaseChronosPipeline

        if "p" not in self._cache:
            self._cache["p"] = BaseChronosPipeline.from_pretrained("amazon/chronos-bolt-small", device_map="cpu", torch_dtype=torch.float32)
        q, _ = self._cache["p"].predict_quantiles(torch.from_numpy(x), prediction_length=H, quantile_levels=[0.5])
        return q[:, :, 0].numpy()

    def _chronos2(self, x, L):
        from chronos import BaseChronosPipeline

        if "p" not in self._cache:
            self._cache["p"] = BaseChronosPipeline.from_pretrained("amazon/chronos-2", device_map="cpu", torch_dtype=torch.float32)
        q, _ = self._cache["p"].predict_quantiles(torch.from_numpy(x).unsqueeze(1), prediction_length=H, quantile_levels=[0.5])
        if isinstance(q, list):
            return np.stack([t.reshape(-1, H)[0].numpy() if t.shape[-1] == 1 else t[0, :, 0].numpy() for t in q])
        return q.reshape(len(x), -1, H)[:, 0, :].numpy()

    def _timesfm(self, x, L):
        import timesfm

        if L not in self._cache:
            m = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
            m.compile(timesfm.ForecastConfig(max_context=max(L, 64), max_horizon=32, normalize_inputs=True, per_core_batch_size=64))
            self._cache = {L: m}                          # 창 길이마다 하나만 들고 있는다(메모리)
        point, _ = self._cache[L].forecast(horizon=H, inputs=list(x))
        return np.asarray(point)[:, :H]

    def _moirai2(self, x, L):
        from uni2ts.model.moirai2 import Moirai2Forecast, Moirai2Module

        if "mod" not in self._cache:
            self._cache["mod"] = Moirai2Module.from_pretrained("Salesforce/moirai-2.0-R-small")
        if L not in self._cache:
            self._cache[L] = Moirai2Forecast(module=self._cache["mod"], prediction_length=H, context_length=L, target_dim=1,
                                             feat_dynamic_real_dim=0, past_feat_dynamic_real_dim=0)
        f = self._cache[L]
        b = torch.from_numpy(x).unsqueeze(-1)
        with torch.no_grad():
            q = f(past_target=b, past_observed_target=torch.ones_like(b, dtype=torch.bool),
                  past_is_pad=torch.zeros(b.shape[:2], dtype=torch.bool))
        qi = list(self._cache["mod"].quantile_levels).index(0.5)
        return q[:, qi, :H].reshape(len(x), H).numpy()


def kronos_cell(name: str, market: str, L: int, cache: dict, deadline: float) -> tuple[np.ndarray, np.ndarray]:
    """§1-보충: Kronos — 보정 OHLC·원 거래량·거래대금 → 평균 경로(표본 5). 종가 경로와 Parkinson 분산(일별)을 돌려준다."""
    import sys

    import pandas as pd

    if KRONOS_CODE not in sys.path:
        sys.path.insert(0, KRONOS_CODE)
    from model import Kronos, KronosPredictor, KronosTokenizer

    if "pred" not in cache:
        def local(repo: str) -> str:                     # 외장 D: 에 받은 스냅숏을 경로로 직접 연다(다른 모델은 ~/.cache 그대로)
            return str(next((Path(KRONOS_HF) / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots").iterdir()))

        tok = KronosTokenizer.from_pretrained(local("NeoQuasar/Kronos-Tokenizer-base"))
        cache["pred"] = KronosPredictor(Kronos.from_pretrained(local(KRONOS[name])), tok, device="cpu", max_context=512)
    d = np.load(ROOT / "inputs" / f"kronos-{market}-A.npz")
    x, rs, stamps = d["ohlcva"], d["row_session"], d["stamps"]
    rank_in_session = pd.Series(rs).groupby(rs).cumcount().to_numpy()
    rows = np.flatnonzero(rank_in_session < KRONOS_TOP)
    cols = ["open", "high", "low", "close", "volume", "amount"]
    price = np.full((len(x), H), np.nan, np.float32)
    sq = np.full((len(x), H), np.nan, np.float32)
    for i in range(0, len(rows), BATCH):
        if time.perf_counter() > deadline:
            raise TimeoutError("모델 예산 90분 소진(칸 도중)")
        torch.manual_seed(0)
        part = rows[i:i + BATCH]
        dfs = [pd.DataFrame(x[j, -L:], columns=cols) for j in part]
        xs = [pd.Series(pd.to_datetime(stamps[rs[j], -(L + H):-H])) for j in part]
        ys = [pd.Series(pd.to_datetime(stamps[rs[j], -H:])) for j in part]
        out = cache["pred"].predict_batch(dfs, xs, ys, pred_len=H, T=1.0, top_p=0.9, sample_count=5, verbose=False)
        for j, o in zip(part, out, strict=True):
            price[j] = o["close"].to_numpy(np.float32)
            hl = np.log(np.clip(o["high"].to_numpy(), 1e-9, None) / np.clip(o["low"].to_numpy(), 1e-9, None))
            sq[j] = (hl ** 2 / (4 * np.log(2))).astype(np.float32)
    return price, sq


def run_model(name: str, threads: int) -> dict:
    torch.set_num_threads(threads)
    fc = Forecaster(name)
    t_start = time.perf_counter()
    fc.deadline = t_start + BUDGET_S
    rec = {"model": name, "threads": threads, "cells": {}}
    (ROOT / "preds").mkdir(parents=True, exist_ok=True)
    for window, L in PLAN:
        if window == "B" and name not in WINDOW_B_MODELS:
            continue
        if name in KRONOS and window != "A":              # Kronos 는 창 A 만(§1-보충)
            continue
        if name in KRONOS and not D_DRIVE_OK.exists():
            for market in MARKETS:
                rec["cells"][f"{market}-{window}-L{L}"] = "미측정(디스크 점검 대기)"
            continue
        for market in MARKETS:
            key = f"{market}-{window}-L{L}"
            src = ROOT / "inputs" / f"ctx-{market}-{window}.npz"
            if not src.exists():
                rec["cells"][key] = "입력 없음"
                continue
            if time.perf_counter() - t_start > BUDGET_S:
                rec["cells"][key] = "미측정(시간)"
                continue
            if avail_mb() < 6000:
                rec["cells"][key] = f"미측정(메모리 {avail_mb()}MB)"
                continue
            t0 = time.perf_counter()
            try:
                if name in KRONOS:
                    price, sq = kronos_cell(name, market, L, fc._cache, fc.deadline)
                else:
                    d = np.load(src)
                    price = fc(d["price"][:, -L:].astype(np.float32))
                    sq = fc(d["sq"][:, -L:].astype(np.float32))
            except TimeoutError:
                rec["cells"][key] = "미측정(시간)"
                continue
            except Exception as e:  # 실패도 결과다
                rec["cells"][key] = f"오류 {type(e).__name__}: {e}"[:300]
                continue
            secs = time.perf_counter() - t0
            np.savez(ROOT / "preds" / f"{name}-{key}.npz", price=price, sq=sq)
            rec["cells"][key] = {"n": int(np.isfinite(price[:, 0]).sum()), "seconds": secs, "per_series_ms": 1000 * secs / max(int(np.isfinite(price[:, 0]).sum()), 1)}  # 계열 = (수익+변동성) 한 쌍
            print(name, key, rec["cells"][key], flush=True)
    rec["total_s"] = time.perf_counter() - t_start
    (ROOT / "preds" / f"{name}-run.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--models", nargs="+", default=MODELS, choices=MODELS)
    a = ap.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    bad = 0
    for m in a.models:
        rec = run_model(m, a.threads)
        bad += any(isinstance(v, str) and v.startswith("오류") for v in rec["cells"].values())
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
