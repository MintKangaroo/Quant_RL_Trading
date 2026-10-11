"""BE2 채점 배치(2026-10-11) — 일일 실행기 직후·실전 그림자 전, 잠금·rc 전달·재부팅 따라잡기. 정적 검사."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _text(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_scripts_parse() -> None:
    for name in ("run_score_be2.sh", "run_shadow.sh", "run_shadow_be2.sh", "reboot_recover.sh"):
        assert subprocess.run(["bash", "-n", str(SCRIPTS / name)], check=False).returncode == 0, name


def test_score_runner_waits_on_daily_and_passes_rc() -> None:
    text = _text("run_score_be2.sh")
    assert "data/_locks/score-be2.lock" in text and "flock -w" in text
    assert "tools/run_dail[y].py --market KR" in text          # 일일 실행기 프로세스를 기다린다(자기 매칭 방지)
    assert '-ne 3' in text                                      # rc 3(일일 실행기 신호 없음)이면 다시 본다
    assert text.rstrip().endswith('exit "${RC}"')


def test_live_shadow_kr_waits_for_score_lock_before_session() -> None:
    text = _text("run_shadow.sh")
    assert text.index("exec 9>data/_locks/score-be2.lock") < text.index(".venv/bin/python tools/run_session.py")


def test_be2_shadow_runner_shares_the_lock() -> None:
    text = _text("run_shadow_be2.sh")
    assert text.index("exec 9>data/_locks/score-be2.lock") < text.index(".venv/bin/python tools/score_be2.py") < text.index("flock -u 9")


def test_reboot_recovery_scores_between_daily_and_shadow() -> None:
    text = _text("reboot_recover.sh")
    daily = text.index("bash scripts/run_daily.sh KR")
    score = text.index("bash scripts/run_score_be2.sh once")
    shadow = text.index("bash scripts/run_shadow.sh KR")
    assert daily < score < shadow
    assert text.count("bash scripts/run_score_be2.sh once") == 2   # 세션을 안 따라잡는 경우의 채점 따라잡기
