"""검증 폴드로 체크포인트를 고른다 — **마지막 정책이 아니라 가장 덜 외운 정책**.

    .venv/bin/python tools/select_checkpoint.py --run m4-round3-s0-c2-r7 \\
        --train-window 2023-01-02:2025-12-31 --valid-window 2026-01-02:2026-06-30 [--save]

`--keep-checkpoints` 로 남긴 `<run>-u<N>.pt` 를 전부 같은 자(짧은 에피소드·warm start·
균등가중 대조군)로 재서 학습 폴드 우위와 검증 폴드 우위를 나란히 놓는다. 2회차에서
확인한 것: 업데이트 220 은 균등가중과 같았고 1080/1220 은 학습에서만 이겼다 — 조기
종료로 고를 좋은 중간 시점이 그 판에는 없었다. 이 도구는 그 질문을 매 판마다 묻는다.

판정(미리 고정, rl-training.md 3회차 설계):
  - 파일럿 게이트: 마지막 몇 체크포인트의 검증 우위가 > 0 이고 학습 우위와 벌어지지 않을 것
  - 본 학습 선택: 검증 우위가 가장 큰 체크포인트. 동률이면 이른 것.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import evaluate_policy as ev  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="run_id (체크포인트 파일 접두사)")
    parser.add_argument("--checkpoint-dir", default="data/rl_checkpoints")
    parser.add_argument("--train-window", required=True)
    parser.add_argument("--valid-window", required=True)
    parser.add_argument("--episode-days", type=int, default=20)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--envs", type=int, default=16)
    parser.add_argument("--every", type=int, default=1, help="n 개마다 하나만 (빠른 훑기)")
    parser.add_argument("--save", action="store_true", help="rl_evaluations 에 valid 로 적재")
    args = parser.parse_args(argv)

    pattern = re.compile(rf"^{re.escape(args.run)}-u(\d+)\.pt$")
    files = sorted(
        ((int(m.group(1)), p) for p in Path(args.checkpoint_dir).glob(f"{args.run}-u*.pt")
         if (m := pattern.match(p.name))),
    )[:: args.every]
    if not files:
        print(f"{args.checkpoint_dir} 에 {args.run}-u*.pt 가 없다 — --keep-checkpoints 로 학습했나", file=sys.stderr)
        return 1
    print(f"{args.run} · 체크포인트 {len(files)}개 · 학습 {args.train_window} · 검증 {args.valid_window}\n")
    print(f"{'업데이트':>8} {'학습 우위':>10} {'검증 우위':>10} {'검증 현금':>9} {'검증 반영률':>10}")
    rows: list[tuple[int, float, float]] = []
    for update, path in files:
        argv_eval = [
            "--checkpoint", str(path), "--train-window", args.train_window,
            "--oos-window", args.valid_window, "--oos-label", "valid",
            "--episode-days", str(args.episode_days), "--steps", str(args.steps),
            "--envs", str(args.envs), "--warm-start",
        ] + (["--save"] if args.save else [])
        result = ev.evaluate(argv_eval)
        train_gap, valid_gap = result["train_gap"], result["oos_gap"]
        rows.append((update, train_gap, valid_gap))
        valid_policy = result["out"]["OOS(안 본 것)·정책"]
        print(f"{update:>8} {train_gap:>+10.5f} {valid_gap:>+10.5f} {valid_policy['cash']:>9.3f} "
              f"{valid_policy['reflection']:>10.3f}", flush=True)
    best = max(rows, key=lambda r: (r[2], -r[0]))
    tail = rows[-3:]
    gate = all(r[2] > 0 for r in tail) and not all(
        (r[1] - r[2]) > (rows[0][1] - rows[0][2]) + 1e-9 for r in tail
    )
    print(f"\n최선: 업데이트 {best[0]} (검증 우위 {best[2]:+.5f} · 학습 우위 {best[1]:+.5f})")
    print(f"파일럿 게이트: {'통과' if gate else '실패'} — 마지막 3개 검증 우위 "
          + ", ".join(f"{r[2]:+.5f}" for r in tail))
    return 0 if gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
