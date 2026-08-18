"""대시보드 엔드포인트 응답시간 측정기.

    uv run python tools/bench_dashboard.py --port 5091
    uv run python tools/bench_dashboard.py --port 5091 --repeat 7 --only trading market

**중앙값이 아니라 최솟값이 코드의 하한이다.** 이 저장소에서 성능 판단이 두 번
틀렸는데 두 번 다 원인이 코드가 아니라 머신 부하였다 — 같은 코드가 5초와 297초를,
0.88초와 2.28초를 오갔다. 그래서 여기서는 셋을 다 찍는다:

- ``min`` — 캐시가 다 데워진 하한. 코드를 고쳤는지 판단할 유일한 숫자
- ``median`` — 실제로 사람이 겪는 시간
- ``max`` — 부하가 얼마나 흔드는지

그리고 측정 **중**의 loadavg·가용 메모리를 같이 적는다. 이 값이 다르면 두 측정은
비교할 수 없다 — 개선 전후를 비교하려면 loadavg 가 비슷할 때 잰 것이어야 한다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: 화면이 실제로 부르는 경로들. static/*.js 의 fetchJson 호출에서 뽑았다.
#: 화면 하나가 여러 개를 동시에 부르므로, 탭 체감 시간은 그 탭 경로들의 합이 아니라
#: 최댓값에 가깝다 — 그래도 어느 하나가 느리면 탭 전체가 그만큼 늦는다.
ENDPOINTS: tuple[str, ...] = (
    "data-quality/summary",
    "data-quality/jobs",
    "data-quality/coverage",
    "data-quality/missing",
    "data-quality/latency",
    "data-quality/universe",
    "data-quality/failures",
    "agent-health/summary",
    "agent-health/roster",
    "agent-health/ic-history",
    "agent-health/signals",
    "agent-health/verdicts",
    "briefing/summary",
    "briefing/explain",
    "briefing/news",
    "briefing/calendar",
    "trading",
    "trading/calendar",
    "market",
    "headlines",
    "system/summary",
    "system/jobs",
    "system/tables",
    "system/latency",
    "system/cache",
    "system/safety",
    "system/llm-usage",
    "system/resources",
    "system/processes",
    "learning/status",
    "learning/gate",
    "learning/ic-history",
    "learning/walk-forward",
    "ai-review/summary",
    "ai-review/calls",
    "ai-review/costs",
    "ai-review/verdicts",
    "ai-review/documents",
)


def _loadavg() -> float:
    return float(Path("/proc/loadavg").read_text().split()[0])


def _available_mb() -> int:
    """``MemAvailable`` — free 가 아니라 이것을 본다. 페이지 캐시는 회수 가능하다."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return -1


def _call(url: str, timeout: float) -> tuple[float, int, int]:
    """(초, HTTP 상태, 응답 바이트). 실패해도 던지지 않는다 — 한 엔드포인트가
    죽었다고 나머지 측정을 버릴 이유가 없다."""
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            return time.perf_counter() - started, response.status, len(body)
    except urllib.error.HTTPError as error:
        body = error.read()
        return time.perf_counter() - started, error.code, len(body)
    except Exception:
        return time.perf_counter() - started, 0, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5091)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--repeat", type=int, default=5, help="엔드포인트당 호출 횟수")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--only", nargs="*", default=None, help="이 문자열을 포함하는 경로만"
    )
    parser.add_argument("--json-out", type=Path, default=None, help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args(argv)

    targets = [
        path
        for path in ENDPOINTS
        if args.only is None or any(needle in path for needle in args.only)
    ]
    if not targets:
        print("측정할 경로가 없다")
        return 2

    base = f"http://{args.host}:{args.port}/api/"
    load_before, mem_before = _loadavg(), _available_mb()
    print(f"측정 시작 · loadavg {load_before:.2f} · 가용 {mem_before:,}MB · {args.repeat}회씩")
    print()

    rows: list[dict[str, object]] = []
    loads: list[float] = [load_before]
    for path in targets:
        url = base + path
        samples: list[float] = []
        status = 0
        size = 0
        for _ in range(args.repeat):
            elapsed, status, size = _call(url, args.timeout)
            samples.append(elapsed)
        loads.append(_loadavg())
        rows.append(
            {
                "path": path,
                "status": status,
                "bytes": size,
                "min": min(samples),
                "median": statistics.median(samples),
                "max": max(samples),
                "samples": samples,
            }
        )
        print(
            f"  {path:<28} {status:>3}  "
            f"min {min(samples):6.3f}s  med {statistics.median(samples):6.3f}s  "
            f"max {max(samples):6.3f}s  {size / 1024:8,.1f}KB",
            flush=True,
        )

    load_after, mem_after = _loadavg(), _available_mb()
    rows.sort(key=lambda row: row["median"], reverse=True)  # type: ignore[arg-type,return-value]
    total_median = sum(float(row["median"]) for row in rows)
    total_min = sum(float(row["min"]) for row in rows)

    print()
    print("── 느린 순 ──")
    for row in rows[:12]:
        print(
            f"  {row['path']:<28} med {float(row['median']):6.3f}s  "
            f"min {float(row['min']):6.3f}s  {float(row['bytes']) / 1024:8,.1f}KB"
        )
    print()
    print(f"합계 med {total_median:.2f}s · min {total_min:.2f}s ({len(rows)}개)")
    print(
        f"loadavg {load_before:.2f} → {load_after:.2f} (측정 중 최대 {max(loads):.2f}) · "
        f"가용 {mem_before:,}MB → {mem_after:,}MB"
    )

    if args.json_out:
        args.json_out.write_text(
            json.dumps(
                {
                    "rows": rows,
                    "loadavg": {"before": load_before, "after": load_after, "peak": max(loads)},
                    "available_mb": {"before": mem_before, "after": mem_after},
                    "repeat": args.repeat,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"JSON → {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
