"""HF_HUB_DISABLE_XET=1 로 부른다 — xet 내려받기가 메모리 3GB 를 잡아 가용이 6GB 아래로 내려갔다(10/7 18:25).
10/13 목록 추가 모델 — 외장 D: 에(사용자 10/7). HF_HOME=/mnt/d/quant_rl_trading/hf 로만 부른다(실전 .venv·~/.cache 무관).
가용 메모리가 6GB 아래면 다음 파일로 넘어가기 전에 멈춘다(rc 4)."""
import os
import subprocess
import sys
import threading
import time

from huggingface_hub import hf_hub_download, snapshot_download


def avail_mb() -> int:
    out = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines()[1].split()
    return int(out[6])


def _watch():  # 내려받는 도중에도 가용이 6GB 아래로 내려가면 바로 멈춘다(사용자 조건 10/7)
    while True:
        if avail_mb() < 6000:
            print(f"멈춤(도중): 가용 {avail_mb()}MB < 6000MB", flush=True)
            os._exit(4)
        time.sleep(10)


threading.Thread(target=_watch, daemon=True).start()

JOBS = [
    ("snap", "EleutherAI/polyglot-ko-5.8b", ["*.json", "*.safetensors", "*.txt", "tokenizer*"]),   # pytorch_model.bin(중복 11.9GB) 빼고
    ("file", "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF", "EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf"),
    ("snap", "manelalab/chrono-gpt-instruct-v1-20211231", None),
    ("snap", "manelalab/chrono-gpt-instruct-v1-20231231", None),
    ("snap", "manelalab/chrono-gpt-instruct-v1-20241231", None),
]
for kind, repo, arg in JOBS:
    if avail_mb() < 6000:
        print(f"멈춤: 가용 {avail_mb()}MB < 6000MB — {repo} 전", flush=True)
        sys.exit(4)
    path = snapshot_download(repo, allow_patterns=arg, max_workers=1) if kind == "snap" else hf_hub_download(repo, arg)
    print(repo, path, flush=True)
print("끝", flush=True)
