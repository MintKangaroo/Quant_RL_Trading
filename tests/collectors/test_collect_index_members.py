"""지수 구성종목 수집 — 이미 받은 세션은 죽지 않고 건너뛴다.

휴장일엔 '마지막 완성 세션' 이 어제와 같다. 예전엔 같은 실행 id 로 다시 써서
``DuplicateIngestRun`` 으로 크론이 죽었다 (2026-09-23/24 logs/index-members-202609.log).
"""
from __future__ import annotations

import sys
import types
from datetime import date

import pytest


@pytest.fixture
def fake_pykrx(monkeypatch):  # type: ignore[no-untyped-def]
    """KRX 대신 200종목을 돌려주는 가짜. 호출 횟수를 센다."""
    calls: list[tuple[str, str]] = []

    class _Stock:
        @staticmethod
        def get_index_portfolio_deposit_file(code: str, day: str) -> list[str]:
            calls.append((code, day))
            return [f"{i:06d}" for i in range(1, 201)]

    module = types.ModuleType("pykrx")
    module.stock = _Stock  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", module)
    return calls


def test_run_id_separates_backfill_from_live() -> None:
    from tools.collect_index_members import run_id_for

    day = date(2026, 9, 23)
    assert run_id_for("KOSPI200", day) == "index-members-KOSPI200-20260923"
    assert run_id_for("KOSPI200", day, backfill=True) == "index-members-KOSPI200-20260923-bf"


def test_second_run_on_the_same_session_skips_instead_of_raising(store, fake_pykrx, capsys) -> None:  # type: ignore[no-untyped-def]
    from tools import collect_index_members as cim

    argv = ["--root", str(store.root)]
    assert cim.main(argv) == 0
    assert len(fake_pykrx) == 1

    # 휴장일 재실행 — 같은 세션이다. 적을 게 없지만 실패는 아니다.
    assert cim.main(argv) == 0
    assert len(fake_pykrx) == 1, "이미 받은 세션은 KRX 를 다시 두드리지 않는다"
    assert "이미 기록됨 — 건너뜀" in capsys.readouterr().out
