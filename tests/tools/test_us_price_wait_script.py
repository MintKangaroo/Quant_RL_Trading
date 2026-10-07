"""scripts/wait_us_prices.sh → run_daily.sh·run_shadow.sh — **시세 미완이면 미룬다** (2026-10-07).

2026-09-29 09:37 재부팅이 미장 수집을 첫 배치(A~D)에서 끊었다. 판정이 "봉이 하나라도 있으면 준비됨" 이었고, 마감을
넘겨도 "진행 — 품질 게이트가 막는다" 였다. 게이트는 매수만 막는다 — 반쪽 시세의 신호·매도·미체결은 창고에 그대로 남았다.

판정 자체(부분 수집)는 test_plan_recovery.py 가 본다. 여기서는 **셸이 그 판정의 rc 를 실제로 보는지** —
test_reboot_recover_script.py 와 같은 방식으로, 임시 디렉터리에 같은 경로로 껍데기를 깔고 스크립트 원문을 ``cd`` 만 바꿔 돌린다.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

PYTHON_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${RECORD}"
case "$*" in
    *plan_recovery.py*) printf '%s\\n' "${PLAN:-}"; exit 0 ;;
esac
exit 0
"""

#: 마감(13:30)을 넘긴 14:00 — 대기 루프가 sleep 없이 바로 판정을 낸다.
DATE_STUB = """#!/usr/bin/env bash
case "$1" in
    +%H:%M) echo "14:00" ;;
    +%T)    echo "14:00:00" ;;
    +%Y%m)  echo "202610" ;;
    *)      echo "2026-10-07 14:00:00" ;;
esac
"""

PARTIAL = "NEED collect   US 시세: 2026-09-28 시세가 일부뿐이다 (1,693/6,546종목 — 부분 수집)"
READY = "OK   collect   US 시세: 2026-09-28"


def _isolate(source: Path, tmp_path: Path) -> str:
    text = source.read_text(encoding="utf-8")
    commands = [line for line in text.splitlines() if line.startswith("cd ")]
    assert len(commands) == 1, "Refuse to run an unisolated shell script"
    return text.replace(commands[0], f"cd {shlex.quote(str(tmp_path))} || exit 1")


def _run(tmp_path: Path, script: str, *, plan: str) -> tuple[int, list[str]]:
    stub = tmp_path / ".venv" / "bin" / "python"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(PYTHON_STUB, encoding="utf-8")
    stub.chmod(0o755)
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ("wait_us_prices.sh", script):
        target = scripts / name
        target.write_text(_isolate(REPO / "scripts" / name, tmp_path), encoding="utf-8")
        target.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "date").write_text(DATE_STUB, encoding="utf-8")
    (fake_bin / "date").chmod(0o755)
    # G1 스크립트의 "기존 미장 shadow 가 끝났나" 대기 — 아무것도 안 돈다.
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "pgrep").chmod(0o755)
    (tmp_path / "logs").mkdir(exist_ok=True)
    record = tmp_path / "calls.txt"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "RECORD": str(record), "PLAN": plan}
    args = ["bash", str(scripts / script)] + (["US"] if script != "run_shadow_g1us.sh" else [])
    done = subprocess.run(args, env=env, timeout=60, check=False)
    calls = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return done.returncode, calls


@pytest.mark.parametrize(
    ("script", "runner"),
    [("run_daily.sh", "run_daily.py"), ("run_shadow.sh", "run_session.py"),
     ("run_shadow_g1us.sh", "run_session.py")],
)
def test_마감까지_부분_수집이면_세션을_미루고_rc6(tmp_path: Path, script: str, runner: str) -> None:
    rc, calls = _run(tmp_path, script, plan=PARTIAL)

    assert rc == 6
    assert not any(runner in line for line in calls)


@pytest.mark.parametrize(
    ("script", "runner"),
    [("run_daily.sh", "run_daily.py"), ("run_shadow.sh", "run_session.py"),
     ("run_shadow_g1us.sh", "run_session.py")],
)
def test_다_들어왔으면_마감_뒤에도_바로_돈다(tmp_path: Path, script: str, runner: str) -> None:
    """손으로 다시 돌릴 때 — 준비 판정이 마감 판정보다 먼저다."""
    rc, calls = _run(tmp_path, script, plan=READY)

    assert rc == 0
    assert any(runner in line for line in calls)
