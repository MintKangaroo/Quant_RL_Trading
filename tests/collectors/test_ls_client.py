"""LSClient — 이식한 예외코드 지식이 살아 있는지.

성공코드 리스트는 이 레포에서 가장 값진 이식물이다. 문서가 아니라 실거래
사고로 하나씩 발견된 것이라, 잃으면 같은 사고를 다시 겪는다.
네트워크는 쓰지 않는다 — MockTransport 로 응답을 만든다.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from quant_rl_trading.collectors.errors import LSAPIError, MissingCredentials
from quant_rl_trading.collectors.ls_client import (
    PATH_ORDER,
    HealthParams,
    LSClient,
    LSCredentials,
    isu_code,
)
from quant_rl_trading.replay.clock import ReplayClock

CREDS = LSCredentials(appkey="key", appsecret="secret", base_url="https://api.test")


def client(handler, ts, *, live=True, **kwargs) -> LSClient:  # type: ignore[no-untyped-def]
    slept: list[float] = []
    instance = LSClient(
        credentials=CREDS,
        clock=ReplayClock(ts(2026, 8, 10, 1)),
        transport=httpx.MockTransport(handler),
        live_trading=live,
        sleep=slept.append,
        **kwargs,
    )
    instance.slept = slept  # type: ignore[attr-defined]
    return instance


def token_response() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}
    )


def routing(tr_response):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return token_response()
        return tr_response(request)

    return handler


@pytest.mark.parametrize("code", ["00000", "00039", "00040", "00463"])
def test_known_order_completion_codes_are_success(code, ts) -> None:  # type: ignore[no-untyped-def]
    """00039/00040/00463 을 실패로 읽으면, 실제로 체결된 주문이 예외가 된다."""
    api = client(routing(lambda _: httpx.Response(200, json={"rsp_cd": code, "rsp_msg": "ok"})), ts)

    assert api.request_tr(PATH_ORDER, "CSPAT00601", {})["rsp_cd"] == code


def test_completion_message_is_a_safety_net(ts) -> None:  # type: ignore[no-untyped-def]
    """미발견 완료코드 대비. 띄어쓰기가 달라 00463 을 놓쳤던 이력이 있다."""
    spaced = {"rsp_cd": "09999", "rsp_msg": "취소 완료 되었습니다"}
    api = client(routing(lambda _: httpx.Response(200, json=spaced)), ts)

    assert api.request_tr(PATH_ORDER, "CSPAT00801", {})["rsp_cd"] == "09999"


def test_unknown_error_code_raises(ts) -> None:  # type: ignore[no-untyped-def]
    api = client(
        routing(lambda _: httpx.Response(200, json={"rsp_cd": "40510", "rsp_msg": "잔고부족"})), ts
    )

    with pytest.raises(LSAPIError) as caught:
        api.request_tr(PATH_ORDER, "CSPAT00601", {})

    assert caught.value.rsp_cd == "40510"


def test_invalid_token_is_recovered_once(ts) -> None:  # type: ignore[no-untyped-def]
    """KR/US appkey 공유 충돌(IGW00121) 자동 복구. port: LS_KR ls_client.py:231-296."""
    attempts: list[int] = []

    def tr(_: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(200, json={"rsp_cd": "IGW00121", "rsp_msg": "토큰 무효"})
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "ok"})

    api = client(routing(tr), ts)

    assert api.request_tr(PATH_ORDER, "CSPAT00601", {})["rsp_cd"] == "00000"
    assert len(attempts) == 2


def test_invalid_token_twice_gives_up(ts) -> None:  # type: ignore[no-untyped-def]
    api = client(
        routing(lambda _: httpx.Response(200, json={"rsp_cd": "IGW00121", "rsp_msg": "무효"})), ts
    )

    with pytest.raises(LSAPIError, match="IGW00121"):
        api.request_tr(PATH_ORDER, "CSPAT00601", {})


def test_token_is_cached_until_it_nearly_expires(ts) -> None:  # type: ignore[no-untyped-def]
    issued: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            issued.append(1)
            return httpx.Response(
                200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 120}
            )
        return httpx.Response(200, json={"rsp_cd": "00000"})

    api = client(handler, ts)
    api.request_tr(PATH_ORDER, "CSPAT00601", {})
    api.request_tr(PATH_ORDER, "CSPAT00601", {})
    assert len(issued) == 1

    # 만료 여유(60초) 안으로 들어가면 재발급한다.
    api.clock.advance(timedelta(seconds=90))
    api.request_tr(PATH_ORDER, "CSPAT00601", {})
    assert len(issued) == 2


def test_rate_limit_sleeps_between_calls(ts) -> None:  # type: ignore[no-untyped-def]
    """경과 시간은 Clock 으로 잰다. time.time() 을 쓰면 불변식 2 위반이다."""
    api = client(routing(lambda _: httpx.Response(200, json={"rsp_cd": "00000"})), ts)

    api.request_tr(PATH_ORDER, "CSPAT00601", {})
    api.clock.advance(timedelta(seconds=1))
    api.request_tr(PATH_ORDER, "CSPAT00601", {})

    assert api.slept and api.slept[-1] == pytest.approx(2.1, abs=0.01)


def test_no_sleep_when_enough_time_has_passed(ts) -> None:  # type: ignore[no-untyped-def]
    api = client(routing(lambda _: httpx.Response(200, json={"rsp_cd": "00000"})), ts)

    api.request_tr(PATH_ORDER, "CSPAT00601", {})
    api.clock.advance(timedelta(seconds=10))
    api.request_tr(PATH_ORDER, "CSPAT00601", {})

    assert api.slept == []


def test_paper_mode_blocks_order_tr(ts) -> None:  # type: ignore[no-untyped-def]
    """paper 에서 주문 TR 은 절대 나가지 않는다. port: LS_KR ls_client.py:58-66."""
    api = client(routing(lambda _: httpx.Response(200, json={"rsp_cd": "00000"})), ts, live=False)

    assert api.request_tr(PATH_ORDER, "CSPAT00601", {})["paper"] is True


def test_paper_mode_allows_read_only_tr(ts) -> None:  # type: ignore[no-untyped-def]
    api = client(
        routing(lambda _: httpx.Response(200, json={"rsp_cd": "00000", "t8410OutBlock1": []})),
        ts,
        live=False,
    )

    assert api.request_tr("/stock/market-data", "t8410", {})["rsp_cd"] == "00000"


def test_missing_credentials_are_reported(ts) -> None:  # type: ignore[no-untyped-def]
    api = LSClient(
        credentials=LSCredentials(appkey="", appsecret="", base_url="https://api.test"),
        clock=ReplayClock(ts(2026, 8, 10)),
        live_trading=True,
    )

    with pytest.raises(MissingCredentials):
        api.ensure_token()


def test_template_key_counts_as_missing() -> None:
    assert not LSCredentials("YOUR_KEY", "secret", "https://x").usable()


def test_http_error_is_wrapped(ts) -> None:  # type: ignore[no-untyped-def]
    api = client(routing(lambda _: httpx.Response(500, text="boom")), ts)

    with pytest.raises(LSAPIError, match="HTTP 500"):
        api.request_tr(PATH_ORDER, "CSPAT00601", {})


def test_error_rate_uses_a_rolling_window(ts) -> None:  # type: ignore[no-untyped-def]
    """US 의 누적 카운터는 옛 실패를 영원히 끌고 간다. KR 의 롤링 윈도를 쓴다."""
    api = client(routing(lambda _: httpx.Response(500, text="boom")), ts)

    for _ in range(10):
        with pytest.raises(LSAPIError):
            api.request_tr(PATH_ORDER, "CSPAT00601", {})
        api.clock.advance(timedelta(seconds=5))

    # 임계치는 HealthParams 로 주입된다. 인자 기본값으로 들면 킬스위치가 어떤
    # 기준으로 발동했는지 사후에 재구성할 수 없다 (불변식 10).
    assert api.recent_error_rate() == 1.0
    api.clock.advance(timedelta(seconds=600))
    assert api.recent_error_rate() == 0.0


def test_health_params_come_from_store(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    as_of = ts(2026, 1, 1)
    params = HealthParams.from_store(store, as_of=as_of)

    assert params.window_sec == store.config("collector.error_rate_window_sec", as_of=as_of)
    assert params.min_samples == store.config("collector.error_rate_min_samples", as_of=as_of)


def test_small_sample_does_not_trip_the_kill_switch(ts) -> None:  # type: ignore[no-untyped-def]
    """표본이 적을 때 성급히 끄면 잡음 하나로 파이프라인이 멈춘다."""
    api = client(routing(lambda _: httpx.Response(500, text="boom")), ts)
    api.health = HealthParams(window_sec=120.0, min_samples=8, history_sec=600.0)

    for _ in range(3):
        with pytest.raises(LSAPIError):
            api.request_tr(PATH_ORDER, "CSPAT00601", {})

    assert api.recent_error_rate() == 0.0


def test_isu_code_prefix() -> None:
    assert isu_code("005930") == "A005930"
    assert isu_code("A005930") == "A005930"
    assert isu_code("") == ""
