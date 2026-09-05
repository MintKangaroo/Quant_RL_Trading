"""과거 시점 IC 백필 — RL 학습 구간을 여는 도구.

    uv run python tools/backfill_ic_history.py --start 2024-08 --end 2026-08 --save

## 왜 필요한가

``analyst_weights`` 의 모든 행이 2026-08-12 이후 ``observed_at`` 이었다. 창고는
이중시간이라 그보다 이른 ``as_of`` 로 읽으면 **가중치가 0건**이고, 그러면 합성
점수도 후보도 0건이 된다. 그 위에서 도는 백테스트·RL 에피소드는 전액 현금으로
굴러간다 — 실패로 보이지 않고 **아무 일도 안 일어난 것처럼** 보인다.

그래서 과거 시점마다 **그 시점에 실제로 잴 수 있었던 IC** 를 다시 재서, 그
시점의 ``observed_at`` 으로 넣는다. 오늘 날짜로 찍으면 지금 문제가 그대로
재발한다.

## 왜 measure_ic.py 를 그냥 25번 돌리지 않나

``tools/measure_ic.py --as-of`` 는 이미 정확히 이 일을 한다 (그 파일의
``measured_at = clock.now()`` 가 ReplayClock 이면 과거 시각이 된다). 결과가
틀려서가 아니라 **비용 때문에** 별도 도구를 둔다: 시점 하나에 Analyst 6종을
300세션씩 채점하면 2.5~3시간이고, 25시점이면 60시간을 넘는다.

줄이는 근거는 ``measure_ic.score_sessions`` 의 성질이다. 거기서 Analyst 는
**측정 시점(cutoff)이 아니라 그 세션 자신의 공표 시각**으로 돌아간다::

    as_of = policy.for_session(session)
    analyst.clock = ReplayClock(as_of)

즉 세션 S 의 점수는 cutoff 가 2024-09 이든 2026-08 이든 **완전히 같은 값**이다.
근사가 아니라 항등이다. 그러니 세션별 점수를 구간 전체에 대해 **한 번만** 채점해
두고, 시점마다 자기 창(마지막 300세션)만 잘라 채점하면 된다. 25시점의 창은 거의
겹치므로, 비용은 25배가 아니라 "합집합 구간 / 300세션" 배 — 약 2.6배다.

라벨(``ic.build_targets``)과 합격선(``ic.thresholds``)은 cutoff 마다 다시
만든다. 이쪽은 시점당 10초라 아낄 이유가 없고, 아끼면 그 시점에 안 보였을
정정본을 라벨에 섞게 된다.

## 누수

없다. 두 방향 모두 그 시점까지만 본다.

- **피처**: 세션 S 의 점수는 ``store.get(as_of=공표시각(S))`` 만 거친다. S 는
  언제나 cutoff 이전이므로, cutoff 이후 관측은 애초에 보이지 않는다.
- **라벨**: ``build_targets(as_of=cutoff)`` — cutoff 시점에 관측된 종가만 쓴다.
  라벨이 자기 세션의 미래(5일 전방수익률)를 보는 것은 라벨의 정의고, 그것을
  피처와 겹치지 않게 막는 것이 purged K-fold + embargo 다 (``analysts/ic.py``).
- **적재**: ``valid_from = observed_at = cutoff``. 그래서 cutoff 이전 as_of 로
  읽으면 이 행은 안 보인다 — 뒤늦게 안 것을 그때 알았던 것처럼 만들지 않는다.

## 단계

무겁고 오래 걸리므로 중간 산출물을 작업 디렉터리에 남긴다. 다시 돌리면 이미
만들어진 단계는 건너뛴다 — 8시간짜리 채점을 처음부터 다시 하지 않기 위해서다.

1. ``targets-<날짜>`` — 시점별 라벨과 창(세션 목록)
2. ``scores-<analyst>-<블록>`` — 합집합 구간 전체의 세션별 점수 (제일 오래 걸린다)
3. 시점 × Analyst 로 ``ic.evaluate`` → ``--save`` 면 ``analyst_weights`` 적재
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import (  # noqa: E402
    NotYetPublished,
    publication_policy,
)
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from quant_rl_trading.session.signals import SCORERS  # noqa: E402
from tools.measure_ic import ANALYSTS, score_sessions, target_span  # noqa: E402

#: 알파가 아닌(제약으로 옮긴) Analyst 도 계속 잰다. 측정과 사용은 다른 일이다 —
#: risk 의 IC 는 알파 가중치가 아니라 **제약 임계치를 판단할 때** 쓴다
#: (selector/constraints.py). 안 재면 그 판단의 근거가 사라진다.
#:
#: **시장별로 다르다.** 미장에 flow_kr 을 돌리면 빈 점수가 나오고, 정작 flow_us 는
#: 채점이 안 돼 backfill_signals 가 "채점 결과 없음" 으로 건너뛴다 — 2026-09-02 에
#: 그렇게 미장 이력에 flow_us 만 없었다. 명단의 단일 소스는 session/signals.SCORERS.
def default_analysts(market: Market) -> list[str]:
    return sorted(SCORERS[market])


def month_ends(start: str, end: str) -> list[date]:
    """``YYYY-MM`` 두 개 사이의 각 달 말일(달력).

    재측정 주기를 **월 1회**로 잡은 근거: 라벨이 5일 horizon 이고 창이
    300세션이라, 창은 한 달에 약 7%(21/300)만 바뀐다. 매 거래일 재면 20번
    측정해서 창의 7%를 갱신하는 셈이라 계산만 20배가 되고 IC 는 사실상 같은
    값을 낸다. 반대로 분기 1회면 갱신 사이에 창이 25% 바뀌어, 가중치가 국면
    전환을 한 분기 늦게 따라간다. 월 1회는 그 사이다 — 창 갱신폭 7%p 는
    폴드 표준오차(실측 0.01~0.02) 안에서 유의한 변화를 잡을 만큼 크고,
    비용은 25시점으로 끝난다.
    """
    first = datetime.strptime(start, "%Y-%m").date().replace(day=1)
    last = datetime.strptime(end, "%Y-%m").date().replace(day=1)
    out: list[date] = []
    cursor = first
    while cursor <= last:
        nxt = (cursor.replace(day=28) + timedelta(days=7)).replace(day=1)
        out.append(nxt - timedelta(days=1))
        cursor = nxt
    return out


def cutoffs(store: Store, market: Market, *, start: str, end: str) -> list[datetime]:
    """측정 시점들. **그 측정을 실제로 할 수 있었던 순간**이어야 한다.

    달 말일의 직전 거래일을 잡고, 그 세션의 **공표 시각**을 쓴다. 자정으로
    두면 그날 종가를 못 보고, 오늘 날짜로 두면 이 작업이 고치려는 문제가
    그대로 재발한다.
    """
    live = LiveClock()
    policy = publication_policy(store, market, clock=live)
    now = live.now()
    out: list[datetime] = []
    for day in month_ends(start, end):
        # 말일이 휴장이면 그 앞 거래일로 물러난다. 30일을 다 훑을 일은 없다.
        window = trading_days(market, day - timedelta(days=30), day)
        # **아직 오지 않은 달의 말일은 잘라낸다.** 마지막 달은 대개 진행 중이라
        # 말일이 미래고, 그대로 물으면 정책이 NotYetPublished 로 거부한다.
        # 거부가 옳다 — 미래에 공표될 사실을 지금 관측했다고 적을 수는 없다.
        # 여기서는 그 달의 **이미 공표된 마지막 세션**으로 물러난다.
        while window:
            try:
                moment = policy.for_session(window[-1])
            except NotYetPublished:
                window = window[:-1]
                continue
            if moment <= now:
                out.append(moment)
            break
    return out


def work_file(work: Path, name: str) -> Path:
    """중간 산출물의 경로.

    ``invariant_guard`` 의 data-access 규칙을 여기 한 줄에서만 면제한다. 이
    파일들은 **창고가 아니다** — 창고 조회 결과를 이 도구가 다시 돌 때 건너뛰려고
    적어 두는 작업 파일이고, 지워도 결과가 달라지지 않는다. 창고 데이터는 여전히
    ``store.get(as_of=...)`` 로만 들어온다 (불변식 1).
    """
    return work / f"{name}.parquet"  # invariant-allow: data-access — 창고가 아닌 작업 파일


def read_work(path: Path) -> pd.DataFrame:
    """작업 파일 읽기. 면제 이유는 ``work_file`` 과 같다."""
    return pd.read_parquet(path)  # invariant-allow: data-access — 창고가 아닌 작업 파일


def write_work(frame: pd.DataFrame, path: Path) -> None:
    """작업 파일 쓰기. 면제 이유는 ``work_file`` 과 같다."""
    frame.to_parquet(path, index=False)  # invariant-allow: data-access — 창고가 아닌 작업 파일


def work_blocks(work: Path, prefix: str) -> list[Path]:
    """``prefix`` 로 시작하는 블록 파일들. 면제 이유는 ``work_file`` 과 같다."""
    return sorted(work.glob(f"{prefix}-*.parquet"))  # invariant-allow: data-access — 작업 파일


def stamp(moment: datetime) -> str:
    return f"{moment:%Y%m%dT%H%M%S}"


# -----------------------------------------------------------------------------
# 1단계 — 시점별 라벨과 창
# -----------------------------------------------------------------------------


def build_windows(
    store: Store, *, points: list[datetime], market: Market, sessions: int, work: Path
) -> dict[str, list[date]]:
    """시점마다 라벨을 만들어 저장하고, 채점할 창(세션 목록)을 돌려준다."""
    windows: dict[str, list[date]] = {}
    index_path = work / "windows.json"
    if index_path.exists():
        cached = json.loads(index_path.read_text())
        windows = {key: [date.fromisoformat(day) for day in value] for key, value in cached.items()}

    for point in points:
        key = stamp(point)
        target_path = work_file(work, f"targets-{key}")
        if key in windows and target_path.exists():
            continue
        started = time.monotonic()  # invariant-allow: wallclock
        targets = ic.build_targets(
            store, as_of=point, lookback=target_span(sessions), market=str(market)
        )
        if targets.empty:
            print(f"  {key}: 라벨 0행 — 건너뛴다", flush=True)
            continue
        available = sorted(targets["session"].unique())
        window = available[-sessions:]
        # 라벨이 있는 세션만 채점한다. 창 밖 라벨을 들고 있어 봐야 merge 에서
        # 버려지고, 그 사이 메모리만 차지한다.
        write_work(targets[targets["session"].isin(set(window))], target_path)
        windows[key] = list(window)
        elapsed = time.monotonic() - started  # invariant-allow: wallclock
        print(
            f"  {key}: 라벨 {len(targets):,}행 → 창 {len(window)}세션 "
            f"({window[0]}~{window[-1]}) {elapsed:.1f}s",
            flush=True,
        )
        del targets

    index_path.write_text(
        json.dumps({key: [day.isoformat() for day in value] for key, value in windows.items()})
    )
    return windows


# -----------------------------------------------------------------------------
# 2단계 — 합집합 구간 채점 (제일 오래 걸린다)
# -----------------------------------------------------------------------------


#: 한 번에 채점하고 디스크로 내리는 세션 수. 800세션을 통째로 concat 하면
#: 2백만 행 프레임이 만들어지고, 그 봉우리가 DuckDB 한도와 겹치면 램 9.7GB
#: 짜리 기계가 멈춘다. 블록으로 끊으면 봉우리가 블록 하나 크기로 눌린다.
SCORE_BLOCK_SESSIONS = 150


def score_union(
    store: Store,
    *,
    analyst_name: str,
    calendar: list[date],
    market: Market,
    work: Path,
) -> None:
    """Analyst 하나를 합집합 구간 전체에 대해 한 번만 채점한다.

    세션별 점수는 cutoff 에 의존하지 않으므로(모듈 독스트링) 시점마다 다시
    채점할 이유가 없다. 블록 단위로 작업 파일에 내리고, 재실행에서는 이미 있는
    블록을 건너뛴다 — 여기가 전체 비용의 대부분이라 중간에 죽으면 앞부분을
    다시 계산하는 것이 제일 아깝다.
    """
    done = work / f"scores-{analyst_name}.version"

    # 생성 시각의 clock 은 세션마다 덮어써진다(score_sessions). 마지막 세션의
    # 공표 시각을 줘서, 혹시 생성 시점에 시계를 읽는 Analyst 가 있어도 미래를
    # 보지 않게 한다.
    policy = publication_policy(store, market, clock=LiveClock())
    clock = ReplayClock(policy.for_session(calendar[-1]))
    analyst = ANALYSTS[analyst_name](store, clock, market=market)

    blocks = [
        calendar[start : start + SCORE_BLOCK_SESSIONS]
        for start in range(0, len(calendar), SCORE_BLOCK_SESSIONS)
    ]
    for index, block in enumerate(blocks):
        path = work_file(work, f"scores-{analyst_name}-{index:02d}")
        if path.exists():
            continue
        started = time.monotonic()  # invariant-allow: wallclock
        scores = score_sessions(analyst, store, block, market, verbose=True)
        scores.attrs.clear()
        write_work(scores, path)
        elapsed = timedelta(seconds=int(time.monotonic() - started))  # invariant-allow: wallclock
        print(
            f"  {analyst_name} 블록 {index + 1}/{len(blocks)} "
            f"({block[0]}~{block[-1]}): {len(scores):,}행 {elapsed}",
            flush=True,
        )
        del scores

    done.write_text(analyst.version)


def load_scores(work: Path, name: str, window: set[date]) -> pd.DataFrame:
    """창에 걸리는 블록만 읽어 이어 붙인다. 창 밖은 애초에 안 올린다."""
    frames = []
    for path in work_blocks(work, f"scores-{name}"):
        chunk = read_work(path)
        if chunk.empty:
            continue
        chunk = chunk[chunk["session"].isin(window)]
        if not chunk.empty:
            frames.append(chunk)
    if not frames:
        return pd.DataFrame(columns=["entity_id", "session", "score"])
    return pd.concat(frames, ignore_index=True)


# -----------------------------------------------------------------------------
# 3단계 — 시점 × Analyst 채점
# -----------------------------------------------------------------------------


def evaluate_all(
    store: Store,
    *,
    analysts: list[str],
    points: list[datetime],
    windows: dict[str, list[date]],
    market: Market,
    work: Path,
) -> dict[str, dict[str, ic.ICResult]]:
    """{시점: {analyst: ICResult}}.

    Analyst 를 바깥 루프로 둔다 — 점수 프레임 하나가 수백 MB 라, 6종을 동시에
    들고 있으면 램 4~5GB 짜리 기계에서 봉우리가 한도를 넘는다.
    """
    results: dict[str, dict[str, ic.ICResult]] = {stamp(point): {} for point in points}

    for name in analysts:
        version_path = work / f"scores-{name}.version"
        if not version_path.exists():
            print(f"  {name}: 채점 결과가 없다 — 건너뛴다", flush=True)
            continue
        version = version_path.read_text().strip()

        for point in points:
            key = stamp(point)
            window = windows.get(key)
            target_path = work_file(work, f"targets-{key}")
            if not window or not target_path.exists():
                continue
            scores = load_scores(work, name, set(window))
            if scores.empty:
                print(f"  {key} {name:<12} 점수 0행 — 잴 것이 없다", flush=True)
                continue
            targets = read_work(target_path)
            threshold, min_days, t_min = ic.thresholds(store, as_of=point)
            result = ic.evaluate(
                scores,
                targets,
                analyst=name,
                analyst_version=version,
                market=str(market),
                threshold=threshold,
                min_sample_days=min_days,
        t_min=t_min,
            )
            results[key][name] = result
            print(
                f"  {key} {name:<12} IC {result.ic:+.4f}  표본 {result.sample_days:>3}일  "
                f"가중치 {result.weight}",
                flush=True,
            )
            del targets, scores

    return results


# -----------------------------------------------------------------------------
# 적재
# -----------------------------------------------------------------------------


def save(
    store: Store,
    *,
    results: dict[str, dict[str, ic.ICResult]],
    points: list[datetime],
    market: Market,
    run_tag: str = "",
) -> int:
    """시점마다 한 번의 append. run_id 는 (시장, 시점) 으로 정해진다.

    ``run_tag`` 는 **뒤늦게 생긴 Analyst 를 같은 시점에 덧붙일 때** 쓴다(2026-09-03, ranker).
    시점 run id 가 이미 적재돼 있으면 건너뛰는 규칙 때문에, 태그 없이는 새 Analyst 의 행이
    영영 안 들어간다. 조회는 entity_id 별 최신 행을 고르므로 다른 run id 라도 같이 읽힌다.

    ``valid_from`` 과 ``observed_at`` 을 **둘 다 cutoff** 로 찍는다. 이 측정은
    그 시점에 만들어졌고(observed_at) 그 시점부터 유효하다(valid_from) —
    가중치는 소급 적용되는 사실이 아니다 (docs/design/data-contract.md).
    """
    written = 0
    for point in points:
        key = stamp(point)
        rows = [
            result.row(as_of=point, observed_at=point, source="ic-backfill")
            for result in results.get(key, {}).values()
        ]
        if not rows:
            continue
        run_id = f"ic-{market}-{key}" + (f"-{run_tag}" if run_tag else "")
        if store.ingest_run_recorded("analyst_weights", run_id):
            print(f"  {run_id}: 이미 적재됨 — 건너뛴다", flush=True)
            continue
        count = store.append("analyst_weights", rows, ingest_run_id=run_id)
        written += count
        print(f"  {run_id}: {count}행", flush=True)
    return written


def render_table(
    results: dict[str, dict[str, ic.ICResult]], points: list[datetime], analysts: list[str]
) -> str:
    """시점 × Analyst IC 표. 마크다운 문서에 그대로 붙인다."""
    header = "| 시점 (observed_at) | " + " | ".join(analysts) + " |"
    rule = "|---|" + "---|" * len(analysts)
    lines = [header, rule]
    for point in points:
        key = stamp(point)
        row = results.get(key, {})
        cells = []
        for name in analysts:
            result = row.get(name)
            if result is None or result.sample_days == 0:
                cells.append("—")
            else:
                mark = "**" if result.passed else ""
                cells.append(f"{mark}{result.ic:+.4f}{mark}")
        lines.append(f"| {point:%Y-%m-%d %H:%M%z} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-08", help="YYYY-MM")
    parser.add_argument("--end", default="2026-08", help="YYYY-MM")
    parser.add_argument("--analyst", nargs="+", choices=sorted(ANALYSTS))
    parser.add_argument("--sessions", type=int, default=300, help="시점마다 잴 거래일 수")
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--work", type=Path, required=True, help="중간 산출물 디렉터리")
    parser.add_argument("--limit", type=int, help="앞에서부터 이만큼 시점만 (예행용)")
    parser.add_argument("--save", action="store_true", help="analyst_weights 에 적재")
    parser.add_argument("--run-tag", default="", help="run id 꼬리표 — 뒤늦게 생긴 Analyst 를 기존 시점에 덧붙일 때")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    market = Market(args.market)
    if not args.analyst:
        args.analyst = default_analysts(market)
    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)

    points = cutoffs(store, market, start=args.start, end=args.end)
    if args.limit:
        points = points[: args.limit]
    print(f"시점 {len(points)}개: {points[0]:%Y-%m-%d} ~ {points[-1]:%Y-%m-%d}", flush=True)

    print("\n=== 1단계 · 시점별 라벨과 창 ===", flush=True)
    windows = build_windows(store, points=points, market=market, sessions=args.sessions, work=work)
    covered = sorted({day for window in windows.values() for day in window})
    if not covered:
        print("창이 비었다. prices 백필을 확인할 것.", file=sys.stderr)
        return 2
    print(
        f"합집합 구간: {covered[0]} ~ {covered[-1]} ({len(covered)}세션) "
        f"— 시점당 {args.sessions}세션을 따로 채점했다면 "
        f"{len(points) * args.sessions:,}세션이었을 일이다",
        flush=True,
    )

    print("\n=== 2단계 · 합집합 구간 채점 ===", flush=True)
    for name in args.analyst:
        score_union(store, analyst_name=name, calendar=covered, market=market, work=work)

    print("\n=== 3단계 · 시점 × Analyst IC ===", flush=True)
    results = evaluate_all(
        store,
        analysts=args.analyst,
        points=points,
        windows=windows,
        market=market,
        work=work,
    )

    print("\n=== IC 표 ===", flush=True)
    print(render_table(results, points, args.analyst), flush=True)

    if args.save:
        print("\n=== 적재 ===", flush=True)
        total = save(store, results=results, points=points, market=market, run_tag=args.run_tag)
        print(f"analyst_weights 적재 합계: {total}행")
    else:
        # 창고는 append-only 다. 잘못 넣으면 되돌릴 수 없으므로, 적재하기 전에
        # **실제로 들어갈 행**을 그대로 보여준다 — 특히 observed_at 이 오늘이
        # 아니라 그 시점으로 찍히는지가 이 작업의 전부다.
        print("\n--save 가 없다. 적재하지 않았다. 들어갔을 행은 이렇다:", flush=True)
        for point in points:
            for result in results.get(stamp(point), {}).values():
                row = result.row(as_of=point, observed_at=point, source="ic-backfill")
                print(
                    f"  run_id=ic-{market}-{stamp(point)} "
                    f"entity_id={row['entity_id']:<12} "
                    f"valid_from={row['valid_from'].isoformat()} "
                    f"observed_at={row['observed_at'].isoformat()} "
                    f"weight={row['weight']} ic={row['ic']:+.4f}",
                    flush=True,
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
