"""D: 에 받은 가중치의 크기·sha256 을 HF API(lfs.sha256)와 대조한다. 읽기만 한다."""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

HUB = Path("/mnt/d/quant_rl_trading/hf/hub")
bad = 0
ONLY = sys.argv[1:]           # 저장소 이름 일부를 주면 그것만
for repo_dir in sorted(HUB.glob("models--*")):
    if ONLY and not any(o in repo_dir.name for o in ONLY):
        continue
    repo = repo_dir.name[len("models--"):].replace("--", "/")
    snap = next((repo_dir / "snapshots").iterdir())
    meta = json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}/revision/{snap.name}?blobs=true"))
    want = {s["rfilename"]: s for s in meta["siblings"]}
    for f in sorted(p for p in snap.rglob("*") if p.is_file()):
        rel = str(f.relative_to(snap))
        s = want.get(rel, {})
        lfs = (s.get("lfs") or {}).get("sha256")
        size = f.stat().st_size
        if lfs is None:
            ok = size == s.get("size", size)
            print(f"{repo}\t{rel}\t{size}\t(작은 파일)\t{'OK' if ok else 'SIZE?'}", flush=True)
            continue
        h = hashlib.sha256()
        retries = 0
        with open(f, "rb") as fh:
            off = 0
            while True:
                for attempt in range(5):   # 외장 HDD(drvfs) 읽기가 간헐적으로 EIO 를 낸다(10/7) — 같은 자리를 다시 읽는다
                    try:
                        fh.seek(off)
                        chunk = fh.read(1 << 22)
                        break
                    except OSError:
                        retries += 1
                        time.sleep(2)
                else:
                    raise OSError(f"{rel}: offset {off} 다섯 번 읽기 실패")
                if not chunk:
                    break
                h.update(chunk)
                off += len(chunk)
        ok = h.hexdigest() == lfs and size == s["size"]
        bad += not ok
        print(f"{repo}\t{rel}\t{size}\t{h.hexdigest()}\t{'OK' if ok else 'MISMATCH'}\t재시도 {retries}", flush=True)
print("불일치", bad)
sys.exit(1 if bad else 0)
