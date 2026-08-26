"""과거 실험의 시행 횟수 소급 집계 — 자기개선 안전장치 ③ 의 개장 재고조사.

    .venv/bin/python tools/tally_trials.py          # 집계만 보여준다
    .venv/bin/python tools/tally_trials.py --save   # research_trials 에 적재

이미 몇 번을 썼는지 모르면 앞으로의 예산이 안 나온다(self-improvement.md §1③).
셀 수 있는 것은 창고·로그에서 세고, 로그가 안 남은 것은 문서에 남은 개수를
쓴다 — 각 행의 detail 에 근거를 적는다. **과소집계보다 과대집계가 안전하다**:
N 이 클수록 DSR 합격선이 높아진다.

소급분은 protocol_hash 가 빈 문자열이다. 그 자체가 "사전등록 없이 돌았다"
는 기록이고, 앞으로의 시행과 구분된다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.modelops.trials import TRIALS_TABLE  # noqa: E402


def count_ic_saved(store: Store, *, as_of: datetime) -> int:
    """창고에 저장된 Analyst IC 측정 — analyst_weights 의 ic-measure 행."""
    frame = store.get("analyst_weights", as_of=as_of)
    if frame.empty or "source" not in frame.columns:
        return 0
    return int((frame["source"] == "ic-measure").sum())


def count_ic_logs() -> int:
    """저장 안 된 IC 스윕(adj·rank2·v02·US·marginal·워크포워드) — 로그 파일 수.

    로그 하나에 여러 Analyst 가 든 큐 파일도 있어 **하한**이다.
    """
    logs = Path("logs")
    return sum(1 for p in logs.glob("ic-*.log") if not p.name.endswith(".pid"))


def count_canary_logs() -> int:
    return sum(1 for p in Path("logs").glob("canary*.log"))


def rows(store: Store, *, as_of: datetime) -> list[dict]:
    """소급 배치 행. valid_from 은 그 실험이 실제로 돌던 시기다 — 지금이 아니다."""
    stamp = as_of

    def d(iso: str) -> datetime:
        return datetime.fromisoformat(iso).replace(tzinfo=UTC)

    return [
        {
            "entity_id": "retro-ic-analyst-saved", "valid_from": d("2026-08-05T00:00"),
            "family": "ic-analyst", "n_trials": count_ic_saved(store, as_of=as_of),
            "detail": "창고 analyst_weights source=ic-measure 행 수 (M2~M3 저장 측정)",
        },
        {
            "entity_id": "retro-ic-analyst-sweeps", "valid_from": d("2026-08-18T00:00"),
            "family": "ic-analyst", "n_trials": count_ic_logs(),
            "detail": "logs/ic-*.log 파일 수 — adj·rank2·v02·US·marginal·워크포워드 무저장 스윕 (큐 로그는 여러 건 포함이라 하한)",
        },
        {
            "entity_id": "retro-ic-feature-31", "valid_from": d("2026-08-19T00:00"),
            "family": "ic-feature", "n_trials": 34,
            "detail": "피처 34개 전수 측정 (#31, docs/feature-registry.md)",
        },
        {
            "entity_id": "retro-ic-chart-trend", "valid_from": d("2026-08-18T00:00"),
            "family": "ic-feature", "n_trials": 8,
            "detail": "차트 추세 8종 (docs/ic-2026-08-18-chart-trend.md)",
        },
        {
            "entity_id": "retro-ic-registry2", "valid_from": d("2026-08-25T00:00"),
            "family": "ic-feature", "n_trials": 12,
            "detail": "사전등록 2차 — C군 8(처녀) + A군 4(확인) 측정분. B군 3은 커버리지 미달로 미측정 (docs/feature-registry-2.md)",
        },
        {
            "entity_id": "retro-evolution", "valid_from": d("2026-08-14T00:00"),
            "family": "evolution", "n_trials": 8,
            "detail": "Selector 진화 smoke 개체 평가 8건 (logs/evo-smoke2.log). 16x15 본판은 평가 0건에서 죽음",
        },
        {
            "entity_id": "retro-rl-canary", "valid_from": d("2026-08-22T00:00"),
            "family": "rl-config", "n_trials": count_canary_logs() + 3,
            "detail": "카나리 설정 변형 로그 + 대규모 확인사살 1 + 본학습 2라운드 (logs/canary*.log)",
        },
        {
            "entity_id": "retro-rl-diagnostics", "valid_from": d("2026-08-25T00:00"),
            "family": "manual", "n_trials": 10,
            "detail": "M4 준비 수동 실험 — 지도 200스텝 용량, advantage 정렬도 r, §8 분해 전후 비교, EV 기준 검토 등 (추정, 과대 쪽)",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true", help="research_trials 에 적재")
    args = parser.parse_args(argv)

    store = Store(root=Path("data"))
    # 소급 기록의 observed_at 은 지금이 맞다 — 우리가 아는 시각이 지금이다.
    now = datetime.now(UTC)  # invariant-allow: wallclock
    batch = rows(store, as_of=now)

    total = 0
    print(f"{'묶음':<28} {'계열':<12} {'시행':>5}  근거")
    for row in batch:
        total += row["n_trials"]
        print(f"{row['entity_id']:<28} {row['family']:<12} {row['n_trials']:>5}  {row['detail'][:60]}")
    print(f"\n누적 시행 합계: {total}")

    if not args.save:
        print("(--save 를 주면 research_trials 에 적재한다)")
        return 0

    records = [
        {**row, "observed_at": now, "market": "KR", "protocol_hash": "", "source": "retro-tally"}
        for row in batch
    ]
    store.append(TRIALS_TABLE, records, ingest_run_id=f"retro-tally-{now:%Y%m%dT%H%M%S}")
    print(f"적재 완료 — {len(records)}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
