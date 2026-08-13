"""hook 배선 검증.

CLAUDE.md 의 지시는 권고고 hook 은 강제다. 그 강제가 실제로 연결돼 있는지,
그리고 위반 파일에 대해 exit 2(모델에게 피드백)를 내는지 확인한다.
배선이 끊긴 hook 은 조용히 통과하므로 테스트로 붙잡아 둔다.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "tools" / "invariant_guard.py"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


def run_hook(file_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), "--hook"],
        input=json.dumps({"tool_input": {"file_path": str(file_path)}}),
        capture_output=True,
        text=True,
        check=False,
    )


def test_settings_registers_the_guard_on_file_edits() -> None:
    config = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = config["hooks"]["PostToolUse"]
    commands = [h["command"] for entry in entries for h in entry["hooks"]]
    assert any("invariant_guard.py" in c and "--hook" in c for c in commands), commands
    assert any("Write" in entry["matcher"] and "Edit" in entry["matcher"] for entry in entries)


def test_hook_blocks_a_violating_file(tmp_path: Path) -> None:
    target = REPO_ROOT / "quant_rl_trading" / "analysts" / "_hook_probe.py"
    target.write_text("from datetime import datetime\nx = datetime.now()\n", encoding="utf-8")
    try:
        result = run_hook(target)
    finally:
        target.unlink()
    assert result.returncode == 2, result
    assert "wallclock" in result.stderr


def test_hook_passes_a_clean_file() -> None:
    result = run_hook(GUARD)
    assert result.returncode == 0, result.stderr


def test_hook_ignores_files_outside_scan_scope(tmp_path: Path) -> None:
    outside = tmp_path / "scratch.py"
    outside.write_text("from datetime import datetime\nx = datetime.now()\n", encoding="utf-8")
    result = run_hook(outside)
    assert result.returncode == 0, result.stderr
