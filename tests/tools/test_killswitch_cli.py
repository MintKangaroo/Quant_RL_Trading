"""tools/killswitch.py — 런북 §4 의 명령. 2026-09-22 에 이 명령이 없어 guards.release 를 손으로 불렀다.

지키는 것: 미확정(submitting) 주문이 남아 있으면 **증거 없이 해제되지 않는다**, 증거는 해제 사유에 남는다,
``--confirm`` 없이는 아무것도 바뀌지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.executor.test_pipeline_broker import (
    FakeBroker,
    seeded,  # noqa: F401 — fixture 재사용
    targets,
)

from quant_rl_trading.broker import BrokerError
from quant_rl_trading.executor import guards, pipeline
from quant_rl_trading.executor.orders import client_order_id, session_id
from quant_rl_trading.replay.clock import ReplayClock
from tools import killswitch as cli

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


@pytest.fixture
def stuck(seeded, monkeypatch):  # type: ignore[no-untyped-def]  # noqa: F811
    """BrokerError 로 킬스위치가 걸리고 submitting 주문 하나가 남은 창고."""
    oid = client_order_id(session=session_id(as_of=NOW, market="KR"), entity_id="KR:A", slice_seq=0)
    pipeline.run(seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(), holdings={},
                 equity=10_000_000.0, broker=FakeBroker(raises={oid: BrokerError("ReadTimeout")}))
    monkeypatch.setattr(cli, "open_store", lambda sandbox: seeded)
    monkeypatch.setattr(cli, "LiveClock", lambda: ReplayClock(LATER))
    return seeded


def test_status_는_미확정_주문을_보인다(stuck, capsys) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "engaged" in out and "오늘 거래일 1건" in out and "KR:A 조각 0 · submitting" in out


def test_증거_없이는_해제되지_않는다(stuck) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["release", "--confirm", "--by", "tester"]) == 2
    assert str(guards.killswitch_state(stuck, as_of=LATER)[0]) == "engaged"


def test_confirm_없이는_바뀌지_않는다(stuck) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["release", "--by", "tester", "--verified", "t0425 에 없음"]) == 1
    assert str(guards.killswitch_state(stuck, as_of=LATER)[0]) == "engaged"


def test_증거와_함께_해제하면_사유에_남는다(stuck) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["release", "--confirm", "--by", "tester", "--verified", "t0425 전체 조회에 없음"]) == 0
    state, reason = guards.killswitch_state(stuck, as_of=LATER + timedelta(seconds=1))
    assert str(state) == "released"
    assert "t0425 전체 조회에 없음" in reason and "tester" in reason and "ReadTimeout" in reason


def test_끝난_거래일의_미확정은_해제를_막지_않는다(stuck, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """당일 유효 주문은 장 마감에 소멸한다 — 예산 계산(risk.account)과 같은 판단으로, 다음 거래일엔 증거 없이 풀린다."""
    next_day = NOW + timedelta(days=1)   # 2026-08-13 목 10:00 KST
    monkeypatch.setattr(cli, "LiveClock", lambda: ReplayClock(next_day))
    assert cli.main(["status"]) == 0
    assert "끝난 거래일 미확정 1건" in capsys.readouterr().out
    assert cli.main(["release", "--confirm", "--by", "tester"]) == 0
    assert str(guards.killswitch_state(stuck, as_of=next_day + timedelta(seconds=1))[0]) == "released"
