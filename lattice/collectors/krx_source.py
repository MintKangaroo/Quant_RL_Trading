"""과거 시세 소스 — 백필 전용.

라이브 수집은 LS API 를 쓴다 (``market_collector.py``). 백필은 KRX 를 쓴다.
소스를 가른 이유는 두 가지이고, 둘 다 M1 완료 기준에 직결된다.

1. **상장폐지 종목.** LS ``t8436`` 종목마스터는 현재 상장분만 돌려준다. 그것만
   백필하면 5년 전 명단에서 그동안 사라진 종목이 통째로 빠지고, 생존편향이
   데이터 자체에 새겨진다. KRX 는 임의 과거일의 그날 명단을 돌려준다.
2. **원주가.** 네이버 계열 무료 소스는 수정주가만 준다 — 2021-04-08 카카오
   종가를 109,992 로 돌려준다(실제 549,000, 일주일 뒤 액면분할이 소급 반영된
   값이다). 그것을 저장하면 5년 구간 전체가 미래를 본다. KRX 는 원주가를 준다.

레포에서 pykrx 를 import 하는 유일한 파일이다. 프로토콜로 잘라 뒀으므로
테스트는 가짜 소스를 끼우고 네트워크 없이 돈다.

**KRX 는 2025년부터 data.krx.co.kr 로그인을 요구한다.** ``KRX_ID`` / ``KRX_PW``
환경변수가 필요하며, 없으면 pykrx 는 예외 대신 **빈 결과**를 돌려준다 — 조용한
실패다. 그래서 이 모듈은 빈 결과를 성공으로 취급하지 않고 명시적으로 던진다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from lattice.collectors.errors import CollectorError

SOURCE = "krx"

#: pykrx 가 세션 쿠키를 얻기 위해 읽는 환경변수. 없으면 모든 조회가 빈 결과다.
CREDENTIAL_ENV = ("KRX_ID", "KRX_PW")

#: KRX 응답 컬럼 → 우리 이름. 한글 컬럼명을 코드 전체에 퍼뜨리지 않는다.
OHLCV_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "value",
}


class KRXUnavailable(CollectorError):
    """KRX 가 데이터를 주지 않았다. 자격증명 부재가 가장 흔한 원인이다."""


@runtime_checkable
class HistoricalSource(Protocol):
    """과거 한 세션을 통째로 읽는 것. 종목 단위가 아니라 날짜 단위다."""

    name: str

    def listed_on(self, day: date) -> list[dict[str, Any]]: ...

    def ohlcv_on(self, day: date) -> list[dict[str, Any]]: ...


def credentials_present(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else dict(os.environ)
    return all(source.get(key, "").strip() for key in CREDENTIAL_ENV)


@dataclass
class KrxSource:
    """pykrx 래퍼. 날짜 하나당 한 번 호출한다.

    종목당 호출이 아니라 **날짜당 호출**이라는 점이 중요하다. 5년치 전종목이
    종목 축으로는 3천 콜, 날짜 축으로는 1,230 콜이다.
    """

    name: str = SOURCE
    _api: Any = field(default=None, repr=False)
    _names: Any = field(default=None, repr=False)

    def _stock(self) -> Any:
        if self._api is None:
            from pykrx import stock  # 무거운 import 를 실제 호출 시점까지 미룬다

            self._api = stock
        return self._api

    def _krx(self) -> Any:
        """종목명을 1콜로 받기 위한 하위 레이어.

        공개 API 의 ``get_market_ticker_name`` 은 종목당 1콜이라 하루치 명단에
        3천 콜이 든다. ``get_market_ticker_and_name`` 은 같은 엔드포인트를
        한 번만 때린다.
        """
        if self._names is None:
            from pykrx.website import krx

            self._names = krx
        return self._names

    def _require_credentials(self) -> None:
        if not credentials_present():
            raise KRXUnavailable(
                "KRX_ID / KRX_PW 가 없다. data.krx.co.kr 계정이 있어야 과거 시세를 "
                "받을 수 있고, 없으면 pykrx 는 예외 없이 빈 결과를 돌려준다"
            )

    def listed_on(self, day: date) -> list[dict[str, Any]]:
        """그날 상장돼 있던 전종목. 지금은 상장폐지된 종목도 포함된다."""
        self._require_credentials()
        stamp = day.strftime("%Y%m%d")
        series = self._krx().get_market_ticker_and_name(stamp, "ALL")
        if series is None or series.empty:
            raise KRXUnavailable(f"KRX {stamp} 종목 명단이 비었다")
        return sorted(
            ({"code": str(code), "name": str(name)} for code, name in series.items()),
            key=lambda item: str(item["code"]),
        )

    def ohlcv_on(self, day: date) -> list[dict[str, Any]]:
        """그날 전종목 일봉. 원주가 — 수정주가는 미래를 포함한다."""
        self._require_credentials()
        stamp = day.strftime("%Y%m%d")
        frame = self._stock().get_market_ohlcv_by_ticker(stamp, market="ALL")
        if frame is None or frame.empty:
            raise KRXUnavailable(f"KRX {stamp} 일봉이 비었다")
        return normalize_krx_frame(frame)


def normalize_krx_frame(frame: Any) -> list[dict[str, Any]]:
    """KRX DataFrame → 코드별 dict 목록. 한글 컬럼은 여기서 끝난다."""
    rows: list[dict[str, Any]] = []
    for code, record in frame.to_dict(orient="index").items():
        row: dict[str, Any] = {"code": str(code)}
        for korean, english in OHLCV_COLUMNS.items():
            row[english] = record.get(korean)
        rows.append(row)
    return sorted(rows, key=lambda item: str(item["code"]))
