"""AR shadow 모델 설치 — 금고 early 가 판정한 **바로 그 파일**(얼린 AR 처리 GBM 시드 0·1·2)을 `data/models/ar_insider/` 로 복사한다.

    .venv/bin/python tools/install_ar_shadow_models.py            # 점검만(무엇을 복사할지 인쇄)
    .venv/bin/python tools/install_ar_shadow_models.py --apply    # 복사

다시 학습하지 않는다(docs/design/c1-ar-shadow.md '왜 얼린 모델인가'). 원본(`data/_diag/vault-reviews/AR-treat-s*.txt`,
`tools/trial_insider_forward.OUT`)의 sha256 앞 16자를 등록 문서 해시(`ar_insider.MODEL_HASHES` — 테스트가 문서와 맞춘다)와
대조하고, 복사본도 다시 잰다. 자리에 **다른** 파일이 이미 있으면 덮지 않고 멈춘다(rc 3).

rc: 0 정상(복사했거나 이미 같은 파일) · 2 원본 없음·지문 불일치 · 3 자리에 다른 파일.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts.ar_insider import MODEL_HASHES, SEEDS, model_dir  # noqa: E402

SOURCE = Path("data/_diag/vault-reviews")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def install(source: Path, root: Path, *, apply: bool) -> int:
    target = model_dir(root)
    plan: list[tuple[Path, Path]] = []
    for seed in SEEDS:
        name = f"AR-treat-s{seed}"
        src, dst = source / f"{name}.txt", target / f"{name}.txt"
        if not src.exists():
            print(f"원본 없음: {src} — 금고 판정 파일이 없으면 다시 학습하지 않고 멈춘다", flush=True)
            return 2
        if _digest(src) != MODEL_HASHES[name]:
            print(f"원본 지문 불일치 {src}: {_digest(src)} ≠ 등록 {MODEL_HASHES[name]}", flush=True)
            return 2
        if dst.exists():
            if _digest(dst) == MODEL_HASHES[name]:
                print(f"  {dst}: 이미 같은 파일 ({MODEL_HASHES[name]})", flush=True)
                continue
            print(f"자리에 다른 파일: {dst} ({_digest(dst)}) — 덮지 않는다", flush=True)
            return 3
        plan.append((src, dst))
    for src, dst in plan:
        print(f"  {'복사' if apply else '복사 예정'} {src} → {dst}", flush=True)
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            if _digest(dst) != _digest(src):
                print(f"복사본 지문이 다르다: {dst}", flush=True)
                return 2
    if not apply and plan:
        print("--apply 가 없다 — 복사하지 않았다", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    return install(args.source, Path(args.root), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
