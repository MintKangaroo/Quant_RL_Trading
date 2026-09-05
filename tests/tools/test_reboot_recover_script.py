"""scripts/reboot_recover.sh — **관문 배선**.

관문을 파이썬에 만들어 놓고 셸이 rc 를 안 보면 아무것도 안 막힌다. 이 레포가
이미 한 번 당한 종류의 결함이다([[silent-failure-needs-nonzero-rc]]).

그래서 여기서는 판정을 테스트하지 않는다(그건 test_plan_recovery.py 가 한다).
**스크립트를 실제로 돌려서 무엇을 부르고 무엇을 안 부르는지** 본다.
test_collect_daily_script.py 와 같은 방식이다 — 임시 디렉터리 안에 같은 경로로
껍데기를 만들고, 스크립트 원문은 ``cd`` 경로만 바꿔 그대로 돌린다.

``date`` 도 껍데기로 바꿔 낀다. 안 그러면 **평일 낮에 돌린 테스트만** 3단계
장중 가드에 걸려 조기 종료하고, 그건 테스트가 요일에 따라 다른 것을 검증한다는
뜻이다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "reboot_recover.sh"

#: 파이썬 껍데기 — `plan_recovery.py` 흉내만 낸다. 인자를 받아적고, 관문
#: 모드에서는 시험이 지정한 rc 를 낸다.
PYTHON_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${RECORD}"
case "$*" in
    *--follow-up*)     echo "미룬 세션 없음"; exit 0 ;;
    *"--gate prices"*) echo "gate prices"; exit "${GATE_PRICES_RC:-0}" ;;
    *"--gate session"*) echo "gate session"; exit "${GATE_SESSION_RC:-0}" ;;
esac
printf '%s\\n' "${PLAN:-}"
exit 0
"""

#: 셸 껍데기 — 어떤 스크립트가 불렸는지만 남긴다.
SH_STUB = """#!/usr/bin/env bash
printf 'sh %s %s\\n' "$(basename "$0")" "$*" >>"${RECORD}"
exit 0
"""

#: 시각 껍데기 — 토요일 20:00 으로 고정한다. 장중 가드를 확실히 통과하는 값.
DATE_STUB = """#!/usr/bin/env bash
case "$1" in
    +%H%M) echo "2000" ;;
    +%u)   echo "6" ;;
    *)     echo "2026-08-22 20:00:00" ;;
esac
"""

STUB_SCRIPTS = (
    "restart_dashboards.sh",
    "health_watch.sh",
    "collect_daily.sh",
    "run_daily.sh",
    "run_shadow.sh",
    "refresh_accounting.sh",
)


def _run(
    tmp_path: Path, *, plan: str, prices_rc: int = 0, session_rc: int = 0
) -> list[str]:
    """복구 스크립트를 실제로 돌리고, 무엇을 불렀는지 순서대로 돌려준다."""
    stub = tmp_path / ".venv" / "bin" / "python"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(PYTHON_STUB, encoding="utf-8")
    stub.chmod(0o755)

    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in STUB_SCRIPTS:
        target = scripts / name
        target.write_text(SH_STUB, encoding="utf-8")
        target.chmod(0o755)

    # curl·date·pgrep 은 PATH 로 가로챈다. 네트워크 대기와 시각 분기를 시험이
    # 쥐고 있어야 결과가 요일·회선 상태에 안 흔들린다.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "curl").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "pgrep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "date").write_text(DATE_STUB, encoding="utf-8")
    for name in ("curl", "pgrep", "date"):
        (fake_bin / name).chmod(0o755)

    (tmp_path / "logs").mkdir(exist_ok=True)
    record = tmp_path / "calls.txt"
    script = tmp_path / "reboot_recover.sh"
    script.write_text(
        SCRIPT.read_text(encoding="utf-8").replace(str(REPO), str(tmp_path)),
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RECORD": str(record),
        "PLAN": plan,
        "GATE_PRICES_RC": str(prices_rc),
        "GATE_SESSION_RC": str(session_rc),
    }
    subprocess.run(["bash", str(script)], env=env, check=True, timeout=120)
    return record.read_text(encoding="utf-8").splitlines() if record.exists() else []


NEED_SESSION = "\n".join(
    [
        "기대 세션 KR 2026-08-21",
        "OK   collect   KR 시세: 2026-08-21",
        "NEED session   KR 세션: 창고가 2026-08-20 까지다 (기대 2026-08-21)",
    ]
)


def _called(calls: list[str], name: str) -> bool:
    return any(line.startswith(f"sh {name}") for line in calls)


def test_관문이_다_열리면_세션과_shadow_를_돌린다(tmp_path: Path) -> None:
    calls = _run(tmp_path, plan=NEED_SESSION)

    assert _called(calls, "run_daily.sh")
    assert _called(calls, "run_shadow.sh")


def test_시세_관문이_막으면_run_daily_부터_안_돈다(tmp_path: Path) -> None:
    """시세가 비면 그 위에서 만든 신호도 빈다. 앞에서 멈추는 것이 맞다."""
    calls = _run(tmp_path, plan=NEED_SESSION, prices_rc=3)

    assert not _called(calls, "run_daily.sh")
    assert not _called(calls, "run_shadow.sh")


def test_세션_관문이_막으면_shadow_만_안_돈다(tmp_path: Path) -> None:
    """**이것이 2026-08-20 을 막았을 배선이다.**

    신호는 만들어도 된다 — `run_daily` 는 그날 signals 를 쓸 뿐이고, 원장에
    박히는 체결은 `run_shadow` 가 쓴다.
    """
    calls = _run(tmp_path, plan=NEED_SESSION, session_rc=3)

    assert _called(calls, "run_daily.sh")
    assert not _called(calls, "run_shadow.sh")


def test_관문은_run_daily_뒤에_다시_묻는다(tmp_path: Path) -> None:
    """오늘 Analyst 가 살았는지는 `run_daily` 가 돌아야 알 수 있다.

    한 번에 물으면 아직 만들지도 않은 것을 없다고 하게 된다.
    """
    calls = _run(tmp_path, plan=NEED_SESSION)
    order = [
        line for line in calls
        if "--gate session" in line or line.startswith("sh run_daily.sh")
    ]

    assert order[0].startswith("sh run_daily.sh")
    assert "--gate session" in order[1]


def test_미룬_세션을_다시_묻는다(tmp_path: Path) -> None:
    """조용히 건너뛰지 않는다 — 미룬 것은 다음 복구가 같은 관문으로 재판정한다."""
    calls = _run(tmp_path, plan=NEED_SESSION, session_rc=3)

    assert any("--follow-up" in line for line in calls)


def test_회계는_관문과_무관하게_돈다(tmp_path: Path) -> None:
    """값이 달라졌을 때만 정정본이 쌓이므로 헛돌아도 행이 안 는다. 반대로
    빠뜨리면 NAV 가 하루 밀린 채로 굳는다."""
    calls = _run(tmp_path, plan=NEED_SESSION, prices_rc=3)

    assert _called(calls, "refresh_accounting.sh")


@pytest.mark.parametrize("plan", ["OK   session   KR 세션: 2026-08-21"])
def test_빈_것이_없으면_세션을_안_건드린다(tmp_path: Path, plan: str) -> None:
    calls = _run(tmp_path, plan=plan)

    assert not _called(calls, "run_daily.sh")
    assert not _called(calls, "run_shadow.sh")
