"""장기 작업 진행률 — 백필·IC 측정이 지금 어디까지 왔나.

## 왜 필요한가

백필은 10시간, IC 측정은 4시간이 걸린다. 그동안 화면에 아무것도 안 나오면
사람은 두 가지를 구분할 수 없다 — **느린 것**과 **죽은 것**. 이 프로젝트는
실제로 그 구분을 못 해서 죽은 큐를 몇 시간 방치한 적이 있다.

## 진행률의 출처는 ProgressLog 다

``data/backfill/<plan_id>/progress.jsonl`` — append-only 이고 작업 단위마다
한 줄이다. 로그를 세는 것이지 프로세스에 물어보는 것이 아니라서, **작업이
죽어도 어디까지 갔는지 남는다.**

## as_of 를 받지만 되감지는 않는다

다른 화면과 달리 이건 **운영 상태**지 관측된 사실이 아니다. 어제 시점의
"백필이 몇 % 였나" 는 창고에 없다(ProgressLog 는 창고가 아니다). 그래서
``as_of`` 는 규약대로 받되 응답에 되돌려주기만 하고, 진행률은 언제나 현재를
보여준다. **이 사실을 화면에 적는다** — 타임머신이 듣는 줄 알면 거짓말이 된다.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROGRESS_DIR = "backfill"
PROGRESS_FILE = "progress.jsonl"

#: 진행 로그를 몇 개까지 훑을지. 오래된 계획은 이미 끝난 것이라 의미가 없다.
MAX_PLANS = 12

#: 총량을 계획 이름에서 못 읽는 작업이 있다. 알려진 것만 적는다 —
#: 모르면 **모른다고 표시**하고 진행률 대신 처리 건수만 보여준다.
KNOWN_TOTALS = {"US-prices": 8}  # 900종목 × 8배치


@dataclass(frozen=True)
class Job:
    """작업 하나의 현재 상태."""

    plan_id: str
    kind: str
    done: int
    total: int | None
    failed: int
    last_unit: str
    last_at: str
    rows: int
    running: bool

    @property
    def ratio(self) -> float | None:
        if not self.total:
            return None
        return min(1.0, self.done / self.total)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "kind": self.kind,
            "done": self.done,
            "total": self.total,
            "ratio": self.ratio,
            "failed": self.failed,
            "last_unit": self.last_unit,
            "last_at": self.last_at,
            "rows": self.rows,
            "running": self.running,
        }


def _running_commands() -> list[str]:
    """지금 돌고 있는 quant_rl_trading 작업의 명령줄.

    ``pgrep -af`` 로 훑되 **자기 자신은 세지 않는다** — 대시보드 프로세스의
    cmdline 이 패턴에 걸리면 항상 "돌고 있음" 이 되어 신호가 죽는다.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-af", "tools/"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        line
        for line in out.splitlines()
        if "pgrep" not in line and "flask" not in line
    ]


def _kind(plan_id: str) -> str:
    """계획 이름 → 사람이 읽는 작업 종류."""
    if plan_id.startswith("US-prices"):
        return "미장 일봉 백필"
    if "-flows-" in plan_id:
        return "국장 수급 백필"
    if "-dart-" in plan_id:
        return "DART 재무 백필"
    return "국장 일봉 백필"


def _matches(plan_id: str, commands: list[str]) -> bool:
    """이 계획이 지금 돌고 있는가. 계획 이름과 명령줄을 느슨하게 맞춘다."""
    if plan_id.startswith("US-prices"):
        return any("--market US" in cmd for cmd in commands)
    if "-flows-" in plan_id:
        return any("flows-ls" in cmd for cmd in commands)
    if "-dart-" in plan_id:
        return any("fundamentals-dart" in cmd for cmd in commands)
    return any("backfill.py" in cmd and "--market US" not in cmd for cmd in commands)


def read_progress(path: Path) -> tuple[int, int, str, str, int]:
    """(완료, 실패, 마지막 단위, 마지막 시각, 누적 행수)."""
    done = failed = rows = 0
    unit = at = ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0, 0, "", "", 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        done += 1
        if record.get("error"):
            failed += 1
        unit = str(record.get("unit") or unit)
        at = str(record.get("at") or at)
        rows += sum(int(v) for v in (record.get("counts") or {}).values())
    return done, failed, unit, at, rows


def collect_jobs(root: Path) -> list[Job]:
    """진행 로그를 훑어 작업 목록으로. 최근에 움직인 것부터."""
    base = root / PROGRESS_DIR
    if not base.is_dir():
        return []

    commands = _running_commands()
    jobs: list[Job] = []
    for directory in sorted(base.iterdir()):
        path = directory / PROGRESS_FILE
        if not path.is_file():
            continue
        done, failed, unit, at, rows = read_progress(path)
        if not done:
            continue
        plan_id = directory.name
        total = next(
            (value for key, value in KNOWN_TOTALS.items() if plan_id.startswith(key)),
            _total_from_plan(plan_id),
        )
        jobs.append(
            Job(
                plan_id=plan_id,
                kind=_kind(plan_id),
                done=done,
                total=total,
                failed=failed,
                last_unit=unit,
                last_at=at,
                rows=rows,
                running=_matches(plan_id, commands),
            )
        )

    jobs.sort(key=lambda job: (job.running, job.last_at), reverse=True)
    return jobs[:MAX_PLANS]


def _total_from_plan(plan_id: str) -> int | None:
    """계획 이름의 날짜 구간에서 총 작업 수를 추정한다.

    세션 축 백필은 이름이 ``KR-2021-08-13-2026-08-12`` 라 구간이 들어 있다.
    **거래일 수는 여기서 세지 않는다** — 달력일로 세면 실제보다 많아 진행률이
    영원히 100%에 못 닿는다. 모르면 None 을 돌려주고 화면이 건수만 보여준다.
    """
    return None


def ic_runs(root: Path) -> list[dict[str, Any]]:
    """IC 측정 진행. 로그 파일의 `… 75/300` 줄을 읽는다.

    ProgressLog 를 쓰지 않는 작업이라 로그를 파싱한다. 형식이 바뀌면 조용히
    빈 목록이 되므로, 화면에는 "없음" 이 아니라 **로그를 못 읽었다**고 적는다.
    """
    logs = root.parent / "logs"
    if not logs.is_dir():
        return []

    pattern = re.compile(r"(\d+)/(\d+)")
    out: list[dict[str, Any]] = []
    commands = _running_commands()
    for path in sorted(logs.glob("ic-*.log")):
        name = path.stem.replace("ic-", "")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        progress = next(
            (pattern.search(line) for line in reversed(lines) if "…" in line), None
        )
        finished = any("IC  " in line or "합격선" in line for line in lines)
        out.append(
            {
                "analyst": name,
                "done": int(progress.group(1)) if progress else (300 if finished else 0),
                "total": int(progress.group(2)) if progress else 300,
                "finished": finished,
                "running": any(f"--analyst {name}" in cmd for cmd in commands),
            }
        )
    return out


def summary(root: Path, *, as_of: datetime) -> dict[str, Any]:
    jobs = collect_jobs(root)
    return {
        "jobs": [job.as_dict() for job in jobs],
        "ic": ic_runs(root),
        "running": sum(1 for job in jobs if job.running),
        # 진행률은 되감기지 않는다 — 이유는 모듈 docstring.
        "live_only": True,
    }
