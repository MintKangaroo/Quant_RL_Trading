"""알파 탐색 프로그램 Phase 0 — HF 모델 CPU 벤치(docs/design/alpha-research-program.md §5.2).

**속도와 메모리만 잰다.** 입력은 전부 합성이다(무작위 보행·합성 표·합성 한국어 문장). 창고·수익·라벨을 읽지 않는다.
실전 `.venv` 가 아니라 `.venv-bench` 로만 돈다. 한 번에 모델 하나를 재고 결과를 JSON 한 줄로 낸다.
최대 RSS 는 바깥 러너(`bench/run_cpu_bench.sh`)가 `/usr/bin/time -v` 로 잰다.

    .venv-bench/bin/python bench/cpu_bench.py ttm --threads 10 --out data/_bench/x.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path

N_SERIES = 2800          # 국장 유니버스 크기 — "매일 전 종목" 이 몇 분인가
CONTEXT = 512
HORIZON = 20             # 20세션 변동성 지평
TABLE_FEATURES = 12      # 국면·노출 표의 피처 수(추정)
PROMPT_TOKENS = 1500     # LLM Analyst 한 종목 입력(추정, alpha-research-program §4.4)
GEN_TOKENS = 100

if os.environ.get("BENCH_SMOKE"):   # 배선 확인용 — 숫자는 쓰지 않는다
    N_SERIES, PROMPT_TOKENS, GEN_TOKENS = 32, 200, 8


def _series(n: int, length: int):
    import numpy as np

    rng = np.random.default_rng(0)
    r = rng.standard_t(df=4, size=(n, length)) * 0.02     # 두꺼운 꼬리 일수익
    return np.abs(r).astype("float32")                     # 변동성 대리(|r|) — 지평 예측의 입력 모양만 맞춘다


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


# ── 시계열 ──────────────────────────────────────────────────────────────

def bench_ttm(a):
    import torch
    from tsfm_public import TinyTimeMixerForPrediction

    model, load_s = _timed(lambda: TinyTimeMixerForPrediction.from_pretrained("ibm-granite/granite-timeseries-ttm-r2").eval())
    x = torch.from_numpy(_series(N_SERIES, CONTEXT)).unsqueeze(-1)

    def run():
        with torch.no_grad():
            for i in range(0, N_SERIES, 256):
                model(past_values=x[i:i + 256])

    _, s = _timed(run)
    return {"load_s": load_s, "series": N_SERIES, "context": CONTEXT, "horizon": 96, "infer_s": s}


def bench_chronos_bolt(a):
    import torch
    from chronos import BaseChronosPipeline

    pipe, load_s = _timed(lambda: BaseChronosPipeline.from_pretrained("amazon/chronos-bolt-small", device_map="cpu", torch_dtype=torch.float32))
    x = torch.from_numpy(_series(N_SERIES, CONTEXT))

    def run():
        for i in range(0, N_SERIES, 256):
            pipe.predict_quantiles(x[i:i + 256], prediction_length=HORIZON, quantile_levels=[0.1, 0.5, 0.9])

    _, s = _timed(run)
    return {"load_s": load_s, "series": N_SERIES, "context": CONTEXT, "horizon": HORIZON, "infer_s": s}


def bench_chronos2(a):
    import torch
    from chronos import BaseChronosPipeline

    pipe, load_s = _timed(lambda: BaseChronosPipeline.from_pretrained("amazon/chronos-2", device_map="cpu", torch_dtype=torch.float32))
    x = torch.from_numpy(_series(N_SERIES, CONTEXT))

    def run():
        for i in range(0, N_SERIES, 256):
            pipe.predict_quantiles(x[i:i + 256].unsqueeze(1), prediction_length=HORIZON, quantile_levels=[0.1, 0.5, 0.9])

    _, s = _timed(run)
    return {"load_s": load_s, "series": N_SERIES, "context": CONTEXT, "horizon": HORIZON, "infer_s": s}


def bench_timesfm(a):
    import timesfm

    def load():
        m = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
        m.compile(timesfm.ForecastConfig(max_context=CONTEXT, max_horizon=32, normalize_inputs=True, per_core_batch_size=64))
        return m

    model, load_s = _timed(load)
    x = list(_series(N_SERIES, CONTEXT))
    _, s = _timed(lambda: model.forecast(horizon=HORIZON, inputs=x))
    return {"load_s": load_s, "series": N_SERIES, "context": CONTEXT, "horizon": HORIZON, "infer_s": s}


def bench_moirai2(a):
    import torch
    from uni2ts.model.moirai2 import Moirai2Forecast, Moirai2Module

    def load():
        return Moirai2Forecast(
            module=Moirai2Module.from_pretrained("Salesforce/moirai-2.0-R-small"),
            prediction_length=HORIZON, context_length=CONTEXT, target_dim=1,
            feat_dynamic_real_dim=0, past_feat_dynamic_real_dim=0,
        )

    model, load_s = _timed(load)
    x = torch.from_numpy(_series(N_SERIES, CONTEXT)).unsqueeze(-1)

    def run():
        with torch.no_grad():
            for i in range(0, N_SERIES, 256):
                b = x[i:i + 256]
                model(past_target=b, past_observed_target=torch.ones_like(b, dtype=torch.bool),
                      past_is_pad=torch.zeros(b.shape[:2], dtype=torch.bool))

    _, s = _timed(run)
    return {"load_s": load_s, "series": N_SERIES, "context": CONTEXT, "horizon": HORIZON, "infer_s": s}


# ── 표 ──────────────────────────────────────────────────────────────────

def _tabpfn(a, version: str):
    import numpy as np
    from huggingface_hub import hf_hub_download
    from tabpfn import TabPFNClassifier

    repo, name = {
        "v2": ("Prior-Labs/TabPFN-v2-clf", "tabpfn-v2-classifier.ckpt"),
        "v2.5": ("Prior-Labs/tabpfn_2_5", "tabpfn-v2.5-classifier-v2.5_default.ckpt"),
    }[version]
    ckpt = hf_hub_download(repo, name)
    rng = np.random.default_rng(0)
    out = {"version": version, "features": TABLE_FEATURES, "rows": {}}
    for rows in a.rows:
        X = rng.normal(size=(rows + 200, TABLE_FEATURES)).astype("float32")
        y = (X[:, 0] + 0.5 * rng.normal(size=rows + 200) > 0).astype(int)    # 합성 국면 표지
        clf = TabPFNClassifier(model_path=ckpt, device="cpu", ignore_pretraining_limits=True, n_estimators=4)
        try:
            _, fit_s = _timed(lambda: clf.fit(X[:rows], y[:rows]))
            _, pred_s = _timed(lambda: clf.predict_proba(X[rows:]))
            out["rows"][str(rows)] = {"fit_s": fit_s, "predict_200_s": pred_s}
        except Exception as e:  # noqa: BLE001 — 한도 초과도 결과다
            out["rows"][str(rows)] = {"error": f"{type(e).__name__}: {e}"[:300]}
    return out


def bench_tabpfn_v2(a):
    return _tabpfn(a, "v2")


def bench_tabpfn_v25(a):
    return _tabpfn(a, "v2.5")


# ── LLM ─────────────────────────────────────────────────────────────────

GGUF = {
    "qwen3_1p7b": ("Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf"),
    "qwen3_8b": ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf"),
    "exaone4_1p2b": ("LGAI-EXAONE/EXAONE-4.0-1.2B-GGUF", "EXAONE-4.0-1.2B-Q4_K_M.gguf"),
}

_KO = ("당사는 이사회 결의에 따라 아래와 같이 단일판매ㆍ공급계약을 체결하였음을 공시합니다. 계약금액은 최근 매출액 대비 일정 비율이며, "
       "계약기간과 대금 지급 조건은 계약 상대방과의 협의에 따릅니다. 본 공시는 합성 문장으로 실제 회사와 무관합니다. ")


def _prompt(target_tokens: int, count) -> str:
    text = ""
    while count(text) < target_tokens:
        text += _KO
    return ("다음 공시를 읽고 향후 5거래일 주가에 대한 방향(-1, 0, +1)과 확신(0~1)을 JSON 으로만 답하라.\n\n" + text)


def _bench_gguf(a, key: str):
    from huggingface_hub import hf_hub_download
    from llama_cpp import Llama

    path = hf_hub_download(*GGUF[key])
    llm, load_s = _timed(lambda: Llama(model_path=path, n_ctx=4096, n_threads=a.threads, n_batch=512, verbose=False, seed=0))
    count = lambda t: len(llm.tokenize(t.encode("utf-8")))  # noqa: E731
    prompt = _prompt(PROMPT_TOKENS, count)
    n_prompt = count(prompt)

    t0 = time.perf_counter()
    first = None
    n_gen = 0
    for _ in llm.create_completion(prompt, max_tokens=GEN_TOKENS, temperature=0.0, stream=True):
        if first is None:
            first = time.perf_counter()
        n_gen += 1
    end = time.perf_counter()
    prompt_s = (first or end) - t0
    gen_s = end - (first or end)
    per_item = prompt_s + gen_s
    return {
        "load_s": load_s, "file": GGUF[key][1], "prompt_tokens": n_prompt, "gen_tokens": n_gen,
        "prompt_s": prompt_s, "prompt_tok_per_s": n_prompt / prompt_s if prompt_s else None,
        "gen_s": gen_s, "gen_tok_per_s": (n_gen - 1) / gen_s if gen_s and n_gen > 1 else None,
        "per_item_s": per_item, "items_100_h": per_item * 100 / 3600, "items_2800_h": per_item * 2800 / 3600,
    }


def bench_qwen3_1p7b(a):
    return _bench_gguf(a, "qwen3_1p7b")


def bench_qwen3_8b(a):
    return _bench_gguf(a, "qwen3_8b")


def bench_exaone4_1p2b(a):
    return _bench_gguf(a, "exaone4_1p2b")


def bench_chronogpt(a):
    """ChronoGPT-instruct(1.55B, 컷오프 2022-12-31). 저장소 코드는 KV 캐시 없이 매 토큰 전체를 다시 계산한다 —
    그래서 생성이 아니라 **한 번의 순전파**(점수화 용도)와 짧은 생성 8토큰을 잰다. 영어 합성 문장."""
    import importlib.util

    import tiktoken
    import torch
    from huggingface_hub import hf_hub_download

    repo = "manelalab/chrono-gpt-instruct-v1-20221231"
    code = hf_hub_download(repo, "ChronoGPT_instruct.py")
    spec = importlib.util.spec_from_file_location("chronogpt", code)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def load():
        cfg = torch.load(hf_hub_download(repo, "config.pt"), weights_only=False)
        with torch.device("meta"):
            m = mod.ChronoGPT(**cfg)
        sd = torch.load(hf_hub_download(repo, "pytorch_model.bin"), mmap=True, weights_only=True, map_location="cpu")
        m.load_state_dict(sd, assign=True)
        return m.eval()

    model, load_s = _timed(load)
    tok = tiktoken.get_encoding("gpt2")
    text = ("The company announced a supply contract with a customer; the amount equals a share of recent revenue. "
            "This is a synthetic sentence unrelated to any real firm. ") * 60
    ids = torch.tensor(tok.encode(text)[:1024]).unsqueeze(0)
    _, fwd_s = _timed(lambda: model(ids))
    _, gen_s = _timed(lambda: mod.generate(model, ids, max_new_tokens=8, context_size=1792))
    return {"load_s": load_s, "forward_tokens": int(ids.shape[1]), "forward_s": fwd_s, "gen8_s": gen_s,
            "items_100_forward_h": fwd_s * 100 / 3600, "items_2800_forward_h": fwd_s * 2800 / 3600}


BENCHES = {k[len("bench_"):]: v for k, v in globals().items() if k.startswith("bench_")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model", choices=sorted(BENCHES))
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--rows", type=int, nargs="+", default=[500, 1000, 3000])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    import torch

    torch.set_num_threads(a.threads)
    rec = {"model": a.model, "threads": a.threads, "host": platform.node(), "cpu_count": os.cpu_count(),
           "torch": torch.__version__, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        rec["result"] = BENCHES[a.model](a)
        rc = 0
    except Exception as e:  # noqa: BLE001 — 실패도 결과로 적는다
        rec["error"] = f"{type(e).__name__}: {e}"[:500]
        rc = 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    print(json.dumps(rec, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
