"""벤치용 모델 가중치 내려받기(docs/design/alpha-research-program.md §5.2). huggingface.co 허용 경로만, 필요한 파일만."""
from huggingface_hub import hf_hub_download, snapshot_download

SNAPSHOTS = [
    "ibm-granite/granite-timeseries-ttm-r2",
    "amazon/chronos-bolt-small",
    "amazon/chronos-2",
    "google/timesfm-2.5-200m-pytorch",
    "Salesforce/moirai-2.0-R-small",
    "manelalab/chrono-gpt-instruct-v1-20221231",
]
FILES = [
    ("Prior-Labs/TabPFN-v2-reg", "tabpfn-v2-regressor-v2_default.ckpt"),
    ("Prior-Labs/TabPFN-v2-clf", "tabpfn-v2-classifier.ckpt"),
    ("Prior-Labs/tabpfn_2_5", "tabpfn-v2.5-classifier-v2.5_default.ckpt"),
    ("Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf"),
    ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf"),
    ("LGAI-EXAONE/EXAONE-4.0-1.2B-GGUF", "EXAONE-4.0-1.2B-Q4_K_M.gguf"),
]

for repo in SNAPSHOTS:
    print(repo, snapshot_download(repo), flush=True)
for repo, name in FILES:
    for extra in ("config.json",):
        try:
            hf_hub_download(repo, extra)
        except Exception:
            pass
    print(repo, hf_hub_download(repo, name), flush=True)
