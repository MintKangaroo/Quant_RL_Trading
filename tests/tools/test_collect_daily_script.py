"""scripts/collect_daily.sh — **스케줄러 배선**.

이 레포가 반복해서 당한 결함은 "수집기가 틀렸다" 가 아니라 **"수집기는 맞는데
아무도 안 부른다"** 다. 지수가 그랬고(08-11 에서 멈춤), fx 가 그랬고(7일 밀림),
공시가 그랬다(수동 백필뿐이라 관리종목 지정을 영영 못 봄). 셋 다 코드는 정상
작동했고, 테스트도 전부 통과했다 — 호출부가 없다는 것만 아무도 안 봤다.

그래서 여기서는 수집기를 테스트하지 않는다. **스크립트를 실제로 실행해서
무엇을 부르는지** 본다. 그러려면 두 가지만 바꿔 끼우면 된다.

- ``cd`` 대상 → 임시 디렉터리
- ``.venv/bin/python`` → argv 를 받아적는 껍데기

파이썬을 텍스트 치환하지 않고 임시 디렉터리 안에 같은 경로로 껍데기를
만들어 둔다. 스크립트 원문을 최대한 그대로 돌려야 배선을 검증하는 값이 있다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "collect_daily.sh"

STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${RECORD}"
for arg in "$@"; do
    if [ "${arg}" = "${FAIL_ON:-}" ]; then exit 1; fi
done
exit 0
"""


def _run(tmp_path: Path, market: str, *, fail_on: str = "") -> list[str]:
    """스크립트를 실제로 돌리고, 파이썬에 넘어간 인자줄을 순서대로 돌려준다."""
    stub = tmp_path / ".venv" / "bin" / "python"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    (tmp_path / "logs").mkdir(exist_ok=True)

    record = tmp_path / "calls.txt"
    script = tmp_path / "collect_daily.sh"
    script.write_text(
        SCRIPT.read_text(encoding="utf-8").replace(str(REPO), str(tmp_path)),
        encoding="utf-8",
    )

    env = {**os.environ, "RECORD": str(record), "FAIL_ON": fail_on}
    subprocess.run(["bash", str(script), market], env=env, check=True, timeout=60)
    return record.read_text(encoding="utf-8").splitlines() if record.exists() else []


def _tables(calls: list[str]) -> set[str]:
    out: set[str] = set()
    for line in calls:
        parts = line.split()
        if "--table" in parts:
            out.add(parts[parts.index("--table") + 1])
    return out


def test_국장_수집이_공시를_부른다(tmp_path: Path) -> None:
    """**이 테스트가 이 작업의 본체다.**

    ``documents-dart`` 백필은 예전부터 정상 작동했다. 다만 어디서도 안 불렸고,
    그래서 selector 의 ``distressed()`` 가 읽는 ``documents`` 표가 마지막 수동
    백필 이후로 자라지 않았다 — 관리종목으로 지정된 종목을 유니버스 필터가
    "정상" 이라고 통과시켰다.
    """
    assert "documents-dart" in _tables(_run(tmp_path, "KR"))


def test_공시는_달력일_창을_받는다(tmp_path: Path) -> None:
    """``--sessions`` 가 없으면 설정의 5년 창으로 돌아간다.

    공시가 없는 날은 남길 배치가 없어 매니페스트에 안 남고 영원히 "남은"
    상태다. 창을 안 주면 그 주말·연휴를 **매일** 다시 DART 에 물어보게 된다.
    """
    (call,) = [
        line for line in _run(tmp_path, "KR") if "documents-dart" in line
    ]
    parts = call.split()
    assert "--sessions" in parts
    assert int(parts[parts.index("--sessions") + 1]) >= 3


def test_공시가_실패해도_뒤가_계속_돈다(tmp_path: Path) -> None:
    """공시 수집 실패가 시세·거시·환율을 죽이면 안 된다.

    스크립트에 ``set -e`` 가 없어서 성립하는 성질이라 조용히 깨질 수 있다.
    """
    calls = _run(tmp_path, "KR", fail_on="documents-dart")
    assert any("documents-dart" in line for line in calls)
    assert any("collect_macro.py" in line for line in calls)
    assert any("collect_fx.py" in line for line in calls)


def test_국장에서_한번_낡았던_것들이_전부_들어있다(tmp_path: Path) -> None:
    """지수·수급·공시 — 셋 다 "호출부 0건" 으로 창고를 낡게 만든 전력이 있다."""
    tables = _tables(_run(tmp_path, "KR"))
    assert {"flows", "indices-krx", "indices-board", "documents-dart"} <= tables


def test_미장은_공시를_안_부른다(tmp_path: Path) -> None:
    """DART 는 국내 공시다. ``--market US`` 로 부르면 빈 응답만 받는다."""
    assert "documents-dart" not in _tables(_run(tmp_path, "US"))
