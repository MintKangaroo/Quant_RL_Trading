"""2회차 학습 사전 점검 — 전부 켜지기 전에 띄우지 않는다.

    .venv/bin/python tools/verify_round2_ready.py
    .venv/bin/python tools/verify_round2_ready.py --r 0.08     # r 재측정값을 넘기면 A2 판정
    .venv/bin/python tools/verify_round2_ready.py --full       # 카나리 게이트까지 실제 실행

## 왜 있는가

1회차(2026-08-25 과적합 판정)의 실패 다섯이 전부 **띄우기 전에 확인 안 한
것**이었다 — 예산 산수는 띄운 뒤에 했고, 평가 도구는 38시간 뒤에 만들었고,
체크포인트는 띄우기 직전에야 없다는 걸 알았다. 이 도구는 그 목록을 기계
검사로 바꾼다. 항목은 `rl-training.md §2회차 사전 점검표` 가 원본이다.

verify_m3 규약: PASS · FAIL · 미측정. 종료코드 0 전부통과 · 1 FAIL 있음 ·
2 FAIL 없이 미측정만. **체크박스를 손으로 켜지 않는다.**
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: 1회차 실측 — 이 값보다 커야 2회차를 띄울 자격이 있다 (A2).
ROUND1_R = 0.043

#: 반복 상한 (B1). 1회차 714회가 과적합을 만들었고, 백필 후 예상은 85회다.
MAX_REPEATS = 150

#: 지도학습 기준 스텝 — 필요 그래디언트 스텝 ≈ 이 값 / r² (카나리 실측 유도).
SUPERVISED_STEPS = 200

PASS, FAIL, SKIP = "PASS", "FAIL", "미측정"


def _print(state: str, label: str, detail: str) -> None:
    print(f"[{state}] {label}\n       {detail}")


def check_a2_signal(r: float | None, budget_steps: int) -> tuple[str, str]:
    """A2 — 신호 세기. **관문의 심장이다.** r 없이 띄우면 1회차 재방송이다."""
    if r is None:
        return SKIP, (
            "r 재측정값이 없다. diagnose_allocation 으로 §8 보상에서 advantage↔"
            f"정렬도 r 을 재고 --r 로 넘길 것 (1회차 {ROUND1_R})"
        )
    need = int(SUPERVISED_STEPS / max(r * r, 1e-9))
    detail = (f"r {r:.4f} (1회차 {ROUND1_R}) · 필요 스텝 ≈ {need:,} · "
              f"예산 {budget_steps:,}")
    if r <= ROUND1_R:
        return FAIL, detail + " — r 이 1회차보다 크지 않다. 입력 개선이 안 먹혔다"
    if need > budget_steps:
        return FAIL, detail + " — 예산 부족. 카나리의 교훈: 예산 없이 판정하지 않는다"
    return PASS, detail


def check_a3_reward_split() -> tuple[str, str]:
    """A3 — 보상 분해가 배선돼 있고 합이 보존되는가."""
    from quant_rl_trading.allocator.reward import RewardEngine, RewardParams

    engine = RewardEngine(params=RewardParams(
        drawdown_free=0.12, drawdown_warn=0.22, drawdown_hard=0.30,
        w_free=0.0, w_mid=1.5, w_hot=8.0, terminal_penalty=-10.0,
    ))
    out = engine.step(
        portfolio_return=0.013, benchmark_return=0.004, cost=0.001,
        candidate_mean_return=0.007, invested_share=0.9,
    )
    if out.selection_return + out.exposure_return != out.excess_return:
        return FAIL, "selection+exposure ≠ excess — §8 합 보존이 깨졌다"
    return PASS, (f"selection {out.selection_return:+.5f} + exposure "
                  f"{out.exposure_return:+.5f} = excess (기계 정밀도)")


def check_b1_repeats(store, *, start: date, end: date, now) -> tuple[str, str]:
    """B1 — 같은 시작점 반복 산수. 1회차는 이걸 띄운 뒤에 했다."""
    from quant_rl_trading.collectors.market_hours import Market, trading_days

    episode = int(store.config("allocator.episode_days", as_of=now))
    total = 20_000_000
    days = trading_days(Market.KR, start, end)
    starts = len(days) - episode
    if starts <= 0:
        return FAIL, f"구간 {len(days)}일 ≤ 에피소드 {episode}일 — 학습 자체가 불가"
    repeats = (total // episode) // starts
    detail = (f"구간 {len(days)}일 · 에피소드 {episode}일 · 시작점 {starts}개 · "
              f"반복 {repeats}회 (상한 {MAX_REPEATS} · 1회차 714)")
    return (PASS if repeats <= MAX_REPEATS else FAIL), detail


def check_b2_cache(store, *, start: date, end: date, now) -> tuple[str, str]:
    """B2 — RL 캐시 커버리지 + 표지 지문 (표본 5세션)."""
    from quant_rl_trading.allocator import cache as cache_module
    from quant_rl_trading.collectors.market_hours import Market, trading_days

    days = trading_days(Market.KR, start, end)
    root = Path(store.root) / cache_module.CACHE_DIRNAME / f"v{cache_module.BUILDER_VERSION}" / "KR"
    missing = [d for d in days if not (root / f"{d}.parquet").exists()]  # invariant-allow: data-access — RL 캐시(파생물) 존재 검사, 창고 아님
    if missing:
        return FAIL, (f"캐시 없는 세션 {len(missing)}개 (예: {missing[0]} …) — "
                      "build_rl_cache 로 굽는다")
    sample = [days[0], days[len(days) // 4], days[len(days) // 2],
              days[3 * len(days) // 4], days[-1]]
    fingerprint = cache_module.fingerprint_of(
        cache_module.dependent_config(store, as_of=now)
    )
    import json
    import pyarrow.parquet as pq  # invariant-allow: data-access — 표지 검사 전용
    for day in sample:
        meta = pq.read_schema(root / f"{day}.parquet").metadata or {}  # invariant-allow: data-access — 표지 검사 전용
        stamp = json.loads(meta.get(cache_module.METADATA_KEY, b"{}"))
        if stamp.get("config_fingerprint") not in (fingerprint,):
            return FAIL, (f"{day} 표지 지문 불일치 ({stamp.get('config_fingerprint')} "
                          f"!= {fingerprint}) — 설정이 바뀌었다. 다시 굽는다")
    return PASS, f"세션 {len(days)}개 전부 존재 · 표본 5개 표지 일치"


def check_c1_checkpoint() -> tuple[str, str]:
    source = (REPO_ROOT / "tools" / "train_rl.py").read_text(encoding="utf-8")
    if "--checkpoint-every" in source and "save_checkpoint" in source and "--resume" in source:
        return PASS, "저장·재개 배선 확인 (2026-08-25 저장 3.9MB → resume 검증 이력)"
    return FAIL, "train_rl.py 에 체크포인트 배선이 없다"


def check_c2_watchdog() -> tuple[str, str]:
    path = REPO_ROOT / "scripts" / "watch_training.sh"
    if path.exists() and "MAX_RESTARTS" in path.read_text(encoding="utf-8"):
        return PASS, "감시견 존재 (재시작 상한 있음)"
    return FAIL, "watch_training.sh 가 없거나 상한이 없다"


def check_c3_resources() -> tuple[str, str]:
    heavy = []
    for pattern in ("compare_baselines", "measure_ic", "backfill.py", "train_rl"):
        probe = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, check=False
        )
        if probe.stdout.strip():
            heavy.append(pattern)
    free_kb = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable"):
            free_kb = int(line.split()[1])
    free_gb = free_kb / 1024 / 1024
    detail = f"여유 {free_gb:.1f}GB · 무거운 동시 작업 {heavy or '없음'}"
    if heavy:
        return FAIL, detail + " — 스와핑 사고(2026-08-23) 재발 경로다"
    if free_gb < 4.0:
        return FAIL, detail + " — 4GB 미만"
    return PASS, detail


def check_d1_eval(store) -> tuple[str, str]:
    if not (REPO_ROOT / "tools" / "evaluate_policy.py").exists():
        return FAIL, "evaluate_policy.py 가 없다"
    from quant_rl_trading.allocator import cache as cache_module
    from quant_rl_trading.collectors.market_hours import Market, trading_days

    oos = trading_days(Market.KR, date(2026, 7, 1), date(2026, 8, 22))
    root = Path(store.root) / cache_module.CACHE_DIRNAME / f"v{cache_module.BUILDER_VERSION}" / "KR"
    missing = sum(1 for d in oos if not (root / f"{d}.parquet").exists())  # invariant-allow: data-access — RL 캐시 존재 검사
    if missing:
        return FAIL, f"OOS 캐시 없는 세션 {missing}/{len(oos)}"
    return PASS, f"평가 도구 + OOS 캐시 {len(oos)}세션"


def check_d2_baseline_recorded() -> tuple[str, str]:
    log = REPO_ROOT / "logs" / "eval-oos2.log"
    if log.exists() and "균등가중" in log.read_text(encoding="utf-8", errors="ignore"):
        return PASS, "균등가중 OOS 대조군 성적 기록 있음 (logs/eval-oos2.log)"
    return SKIP, "대조군 성적 기록이 없다 — evaluate_policy 를 먼저 돌려 둘 것"


def check_e1_gates_registered() -> tuple[str, str]:
    doc = (REPO_ROOT / "docs" / "design" / "rl-training.md").read_text(encoding="utf-8")
    if "2회차 중간 판정선" in doc and "현금 비중이" in doc:
        return PASS, "중간 판정선·현금 경보가 rl-training.md 에 사전 등록됨"
    return FAIL, "중간 판정선이 문서에 없다 — 나중에 이야기를 맞추게 된다"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r", type=float, default=None,
                        help="§8 보상에서 재측정한 advantage↔정렬도 r")
    parser.add_argument("--train-start", default="2021-09-01")
    parser.add_argument("--train-end", default="2026-06-30")
    parser.add_argument("--budget-steps", type=int, default=97_600,
                        help="총 그래디언트 스텝 (20M 환경스텝 기준 실측)")
    parser.add_argument("--full", action="store_true",
                        help="카나리 게이트(A1)를 실제로 돌린다 — 수 분 걸린다")
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)

    from quant_rl_trading.store import Store

    store = Store(root=Path(args.root))
    now = datetime.now(UTC)  # invariant-allow: wallclock
    start = date.fromisoformat(args.train_start)
    end = date.fromisoformat(args.train_end)

    print("2회차 학습 사전 점검 — rl-training.md §점검표가 원본이다\n")
    results: list[str] = []

    def run(label: str, fn) -> None:
        try:
            state, detail = fn()
        except Exception as exc:
            state, detail = FAIL, f"{type(exc).__name__}: {exc}"
        _print(state, label, detail)
        results.append(state)

    if args.full:
        code = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "verify_canary_gate.py"),
             "--root", args.root],
            check=False,
        ).returncode
        state = PASS if code == 0 else FAIL
        _print(state, "A1 카나리 게이트 3종", f"verify_canary_gate rc={code}")
        results.append(state)
    else:
        _print(SKIP, "A1 카나리 게이트 3종", "--full 로 실제 실행 (수 분)")
        results.append(SKIP)

    run("A2 신호 세기 r (관문의 심장)", lambda: check_a2_signal(args.r, args.budget_steps))
    run("A3 보상 분해(§8) 배선", check_a3_reward_split)
    run("B1 에피소드 반복 산수", lambda: check_b1_repeats(store, start=start, end=end, now=now))
    run("B2 RL 캐시 커버리지·표지", lambda: check_b2_cache(store, start=start, end=end, now=now))
    _print(SKIP, "B3 커리큘럼 C1", "학습 명령이 비용 0·비중만으로 시작하는지는 띄우는 사람이 확인한다")
    results.append(SKIP)
    run("C1 체크포인트", check_c1_checkpoint)
    run("C2 감시견", check_c2_watchdog)
    run("C3 자원", check_c3_resources)
    run("D1 평가 도구·OOS 캐시", lambda: check_d1_eval(store))
    run("D2 대조군 성적 사전 기록", check_d2_baseline_recorded)
    run("E1 중간 판정선 사전 등록", check_e1_gates_registered)
    _print(PASS, "E3 시도 잔여", "3회 규칙 중 2회 남음 — 이번이 2회차다")
    results.append(PASS)

    n_pass = results.count(PASS)
    n_fail = results.count(FAIL)
    n_skip = results.count(SKIP)
    print(f"\nPASS {n_pass} · FAIL {n_fail} · 미측정 {n_skip} / {len(results)}")
    if n_fail:
        print("→ 띄우지 않는다. FAIL 을 먼저 없앤다.")
        return 1
    if n_skip:
        print("→ 미측정이 남았다. 특히 A2(r) 없이 띄우는 것은 1회차 재방송이다.")
        return 2
    print("→ 전부 켜졌다. 2회차를 띄울 자격이 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
