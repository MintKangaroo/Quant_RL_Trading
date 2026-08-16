"""실브로커 관문 계약. **틀리면 실제 돈이 나간다.**

여기서 지키는 것은 넷이다.

1. **기본은 안 나간다** — 설정이 꺼져 있거나 없으면 ``PaperBroker``
2. **이유가 언제나 있다** — 안 나간 것을 로그 부재로 추론하게 두지 않는다
3. **지문이 다르면 안 나간다** — "모의인 줄 알았다" 를 막는 유일한 장치
4. **예외로 죽지 않는다** — 무인 실행에서 죽으면 회계·기록까지 안 남는다
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quant_rl_trading.broker import PaperBroker
from quant_rl_trading.broker import factory as broker_factory

NOW = datetime(2026, 8, 19, 6, 40, tzinfo=UTC)


class FakeStore:
    """``store.config`` 만 흉내낸다. 실창고를 쓰면 이 테스트가 느려지고,
    느려지면 안 돌리게 된다."""

    def __init__(self, values: dict[str, object]):
        self.values = values

    def config(self, key: str, *, as_of: datetime) -> object:
        if key not in self.values:
            raise KeyError(f"설정 없음: {key}")
        return self.values[key]


def test_설정이_꺼져_있으면_안_나간다() -> None:
    store = FakeStore({broker_factory.LIVE_TRADING_KEY: False})
    broker, reason = broker_factory.build_broker(store, market="KR", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "꺼짐" in reason


def test_설정이_아예_없으면_안_나간다() -> None:
    """**없는 것을 켜진 것으로 보지 않는다.** ConfigNotFound 로 죽지도 않는다."""
    broker, reason = broker_factory.build_broker(FakeStore({}), market="KR", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "읽지 못했다" in reason


def test_모르는_시장은_안_나간다() -> None:
    store = FakeStore({broker_factory.LIVE_TRADING_KEY: True})
    broker, reason = broker_factory.build_broker(store, market="JP", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "실전 배선이 없는" in reason


def test_자격증명이_없으면_안_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LS_APPKEY", "LS_APPSECRET"):
        monkeypatch.delenv(key, raising=False)
    store = FakeStore({
        broker_factory.LIVE_TRADING_KEY: True,
        broker_factory.FINGERPRINT_KEY_KR: "",
    })
    broker, reason = broker_factory.build_broker(store, market="KR", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "자격증명이 없다" in reason


def test_지문이_다르면_안_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """``.env`` 가 바뀌면 지문이 달라진다. 그게 유일한 판별 수단이다 —
    코드는 모의·실전을 알아낼 수 없다(모의 appkey 로도 t0424 가 응답한다)."""
    monkeypatch.setenv("LS_APPKEY", "some-real-looking-key")
    monkeypatch.setenv("LS_APPSECRET", "secret")
    store = FakeStore({
        broker_factory.LIVE_TRADING_KEY: True,
        broker_factory.FINGERPRINT_KEY_KR: "다른지문입니다",
    })
    broker, reason = broker_factory.build_broker(store, market="KR", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "지문 불일치" in reason


def test_미장은_지문을_선언해도_아직_안_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LSUSBroker`` 는 인스턴스당 OrdMktCode 하나를 들고 있는데 그 값은
    종목마다 다르다(SNAP 81 NYSE · WEN 82 NASDAQ, 2026-08-16 실측). 24종목을
    한 인스턴스로 내면 절반이 틀린 시장코드로 나간다.
    """
    monkeypatch.setenv("LS_US_APPKEY", "us-key")
    monkeypatch.setenv("LS_US_APPSECRET", "us-secret")
    from quant_rl_trading.collectors.ls_client import LSCredentials

    fingerprint = LSCredentials.from_env(prefix="LS_US_").fingerprint
    store = FakeStore({
        broker_factory.LIVE_TRADING_KEY: True,
        broker_factory.FINGERPRINT_KEY_US: fingerprint,
    })
    broker, reason = broker_factory.build_broker(store, market="US", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "OrdMktCode" in reason


def test_미장은_지문_미선언이면_안_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """국장은 빈 값 = 고정 안 함이라는 기존 규약을 두지만, 미장은 새 경로라
    처음부터 조인다."""
    monkeypatch.setenv("LS_US_APPKEY", "us-key")
    monkeypatch.setenv("LS_US_APPSECRET", "us-secret")
    store = FakeStore({
        broker_factory.LIVE_TRADING_KEY: True,
        broker_factory.FINGERPRINT_KEY_US: "",
    })
    broker, reason = broker_factory.build_broker(store, market="US", as_of=NOW)
    assert isinstance(broker, PaperBroker)
    assert "비어 있다" in reason


def test_국장은_지문이_맞으면_실브로커다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LS_APPKEY", "kr-key")
    monkeypatch.setenv("LS_APPSECRET", "kr-secret")
    from quant_rl_trading.broker.ls_order import LSBroker
    from quant_rl_trading.collectors.ls_client import LSCredentials

    fingerprint = LSCredentials.from_env(prefix="LS_").fingerprint
    store = FakeStore({
        broker_factory.LIVE_TRADING_KEY: True,
        broker_factory.FINGERPRINT_KEY_KR: fingerprint,
    })
    broker, reason = broker_factory.build_broker(store, market="KR", as_of=NOW)
    assert isinstance(broker, LSBroker)
    assert "실전 전송" in reason
    # 전송 계층 게이트도 같이 열려야 한다 — 하나만 켜지면 안 나간다.
    assert broker.client.live_trading is True
