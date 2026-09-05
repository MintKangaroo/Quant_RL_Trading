"""장중 시세 캐시 계약.

여기서 지키는 것은 셋이다.

1. **응답에 없는 종목이 캐시를 무력화하지 않는다** — 상장폐지·거래정지 종목을
   하나 들고 있으면 그것만으로 매 요청이 API 를 다시 부른다. 실측으로
   대시보드 응답이 2.7초에서 9.7초가 됐다
2. **클라이언트를 재사용한다** — 호출마다 새로 만들면 그때마다 토큰을 다시
   받는다
3. **실패는 화면을 죽이지 않는다** — 장외·장애는 정상이고, 그때 값이 없는 것을
   0 이나 종가로 때우지 않는다
"""

from __future__ import annotations

from typing import Any

from quant_rl_trading.dashboard.services.live_quotes import LiveQuoteCache

WANTED = ["KR:067290", "KR:005930", "KR:999999"]


class FakeClient:
    """t8407 를 흉내낸다. ``KR:999999`` 는 **일부러 안 돌려준다** — 실제로
    상장폐지 종목이 그렇게 응답에서 빠진다."""

    def __init__(self) -> None:
        self.calls = 0

    def request_tr(self, path: str, tr: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {
            f"{tr}OutBlock1": [
                {"shcode": "067290", "price": 2210, "diff": "5.24",
                 "bidho": 2210, "offerho": 2220},
                {"shcode": "005930", "price": 270750, "diff": "-1.37",
                 "bidho": 270500, "offerho": 271000},
            ]
        }


def test_응답에_없는_종목이_캐시를_깨지_않는다() -> None:
    """**받은 것이 아니라 물어본 것**을 기억해야 한다."""
    client = FakeClient()
    made: list[int] = []

    def factory() -> FakeClient:
        made.append(1)
        return client

    cache = LiveQuoteCache(factory, ttl=60.0)
    first = cache.get(WANTED)
    second = cache.get(WANTED)

    assert len(first) == 2 and "KR:999999" not in first
    assert second == first
    assert client.calls == 1, "없는 종목 때문에 다시 불렀다"
    assert len(made) == 1, "클라이언트를 두 번 만들었다"


def test_TTL_이_지나면_다시_받는다() -> None:
    client = FakeClient()
    cache = LiveQuoteCache(lambda: client, ttl=0.0)
    cache.get(WANTED)
    cache.get(WANTED)
    assert client.calls == 2


def test_새_종목을_물으면_다시_받는다() -> None:
    """캐시가 덮는 범위 밖을 물으면 새로 받아야 한다 — 안 그러면 새로 산
    종목이 영영 장중값 없이 뜬다."""
    client = FakeClient()
    cache = LiveQuoteCache(lambda: client, ttl=60.0)
    cache.get(["KR:067290"])
    cache.get(["KR:067290", "KR:005930"])
    assert client.calls == 2


def test_자격증명이_없으면_빈_결과다() -> None:
    """대시보드는 키 없이도 떠야 한다(데모·백테스트 창고를 볼 때)."""
    cache = LiveQuoteCache(lambda: None, ttl=60.0)
    assert cache.get(WANTED) == {}


def test_예외가_나도_화면을_안_죽인다() -> None:
    """장외·장애는 정상이다. **0 이나 종가로 때우지 않는다** — 0 은 폭락으로
    읽히고 종가로 때우면 실시간인 척하는 거짓이 된다."""

    class Broken:
        def request_tr(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("장외")

    cache = LiveQuoteCache(lambda: Broken(), ttl=60.0)
    assert cache.get(WANTED) == {}


def test_미장은_아직_안_받는다() -> None:
    """t8407 은 국장 TR 이다. 미장 종목을 섞어 보내면 안 된다."""
    client = FakeClient()
    cache = LiveQuoteCache(lambda: client, ttl=60.0)
    assert cache.get(["US:SNAP"]) == {}
    assert client.calls == 0
