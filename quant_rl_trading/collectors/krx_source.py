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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Any, Protocol, runtime_checkable

from quant_rl_trading.collectors.errors import CollectorError

SOURCE = "krx"

#: pykrx 가 세션 쿠키를 얻기 위해 읽는 환경변수. 없으면 모든 조회가 빈 결과다.
CREDENTIAL_ENV = ("KRX_ID", "KRX_PW")

#: 수급 주체. KRX 는 주체마다 따로 물어야 준다.
#: 개인·외국인·기관은 항등식(합=0)을 이루고, 나머지는 기관의 세부다.
#: 세부를 함께 받는 이유는 연기금과 투신의 방향이 자주 엇갈리기 때문이다 —
#: "기관 순매수" 하나로 뭉치면 그 정보가 사라진다.
INVESTORS = ("외국인", "기관합계", "개인", "연기금", "금융투자", "투신")

#: 공매도·지수는 시장 단위로만 조회된다. "ALL" 을 받지 않는다.
SHORTING_BOARDS = ("KOSPI", "KOSDAQ")

#: 밸류·퀄리티 원자료. 섹터 내 백분위 변환은 Analyst 가 한다 (agents.md §2).
VALUATION_COLUMNS = ("BPS", "PER", "PBR", "EPS", "DIV", "DPS")

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
class PanelSource(Protocol):
    """패널 백필이 소스에 요구하는 전부.

    조회 메서드는 Panel.fetch 가 알아서 부르므로 여기서 강제하지 않는다.
    소스마다 주는 것이 다른데 프로토콜이 전부를 요구하면, 하나만 주는 소스는
    쓰지도 않을 메서드를 빈 껍데기로 구현해야 한다.
    """

    name: str


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
    #: 일시적 오류 재시도 횟수. KRX 는 종종 HTML 오류 페이지를 돌려준다.
    retries: int = 3
    retry_pause_sec: float = 1.5
    #: **정상 호출 사이의 최소 간격.** 재시도에만 쉬고 성공 호출은 쉬지 않으면
    #: 백필이 초당 여러 번 때린다 — 2026-09-01 공매도 백필이 그렇게 86세션을
    #: 받고 그 뒤로 계속 빈 응답(IndexError)·HTML 오류를 받았다. 데이터가 없는
    #: 것이 아니라 **거절당한 것**이었다. `LSClient.min_interval_sec` 과 같은 관용구다.
    #:
    #: 3초로 둔다. 하루 5세션(=10콜)이면 30초라 일상 수집에는 부담이 없고,
    #: 과거 백필은 어차피 한 번만 하는 일이라 느려도 된다. **남의 서버를 우리
    #: 편의로 두들기지 않는다** — 막히면 데이터를 통째로 잃는다.
    min_interval_sec: float = 3.0
    sleep: Callable[[float], None] = time.sleep
    _last_call_at: float = field(default=0.0, repr=False)
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

    def _call(self, label: str, action: Callable[[], Any]) -> Any:
        """소스 호출 하나. **여기가 외부 세계와의 경계다.**

        pykrx 는 KRX 가 HTML 오류 페이지를 돌려주면 ``JSONDecodeError`` 를,
        응답 모양이 다르면 ``KeyError`` 를 그대로 던진다. 그게 엔진까지 올라가면
        1,223 세션짜리 무인 실행이 800번째에서 통째로 죽는다.

        경계에서 ``KRXUnavailable`` 로 바꾼다. 소스 문제는 재시도할 일이고,
        우리 코드의 버그는 그대로 올라가야 하므로 **이 경계 밖에서는 넓게 잡지
        않는다.** 재시도는 잠깐 쉬고 몇 번만 — KRX 를 두들기면 더 막힌다.
        """
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                # **때리기 전에 쉰다.** 남의 서버다.
                gap = self.min_interval_sec - (time.monotonic() - self._last_call_at)
                if gap > 0:
                    self.sleep(gap)
                result = action()
                self._last_call_at = time.monotonic()
                return result
            except Exception as error:  # 소스가 던지는 모든 것 — 경계이므로 넓게 잡는다
                last = error
                if attempt + 1 < self.retries:
                    self.sleep(self.retry_pause_sec * (attempt + 1))
        raise KRXUnavailable(f"KRX {label} 실패 ({type(last).__name__}: {last})") from last

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
        series = self._call(
            f"명단 {stamp}", lambda: self._krx().get_market_ticker_and_name(stamp, "ALL")
        )
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
        frame = self._call(
            f"일봉 {stamp}", lambda: self._stock().get_market_ohlcv_by_ticker(stamp, market="ALL")
        )
        if frame is None or frame.empty:
            raise KRXUnavailable(f"KRX {stamp} 일봉이 비었다")
        # 휴장일에도 KRX 는 **전 종목을 0 으로 채운 표**를 준다. 빈 응답이
        # 아니라서 그대로 적재되고, 창고에는 "그날 전 종목이 0원이 됐다" 는
        # 기록이 남는다. 실제로 2026-06-03(지방선거)·2026-07-17 두 세션이
        # 그렇게 들어와 차트의 y축이 0까지 늘어났고, Analyst 는 수익률이
        # inf 가 되어 조용히 침묵했다.
        #
        # 거래일 판단을 여기서 하지 않는 이유: 달력은 틀릴 수 있고 응답은
        # 사실이다. **전 종목이 0** 이라는 것 자체가 휴장의 증거다.
        rows = normalize_krx_frame(frame)
        if rows and not any(row.get("close") for row in rows):
            raise KRXUnavailable(f"KRX {stamp} 은 거래일이 아니다 — 전 종목 종가 0")
        return rows


    # -- flow_kr / fundamental 용 ---------------------------------------------

    def flows_on(self, day: date) -> list[dict[str, Any]]:
        """주체별 순매수. 주체마다 한 번씩 호출한다 (KRX 가 그렇게만 준다).

        주체별로 종목 수가 다르다 — 연기금은 586개, 개인은 2,700개. 그날 그
        주체가 손대지 않은 종목은 아예 응답에 없다. 없는 것을 0으로 채우지
        않는다. "순매수 0" 과 "거래 없음" 은 다른 사실이고, 0으로 채우면
        모델이 존재하지 않는 관측을 학습한다.
        """
        self._require_credentials()
        stamp = day.strftime("%Y%m%d")
        api = self._stock()
        rows: list[dict[str, Any]] = []
        for investor in INVESTORS:
            frame = self._call(
                f"수급 {stamp} {investor}",
                partial(
                    api.get_market_net_purchases_of_equities_by_ticker,
                    stamp, stamp, "ALL", investor,
                ),
            )
            if frame is None or frame.empty:
                continue
            for code, record in frame.to_dict(orient="index").items():
                rows.append(
                    {
                        "code": str(code),
                        "investor": investor,
                        "net_value": record.get("순매수거래대금"),
                        "net_volume": record.get("순매수거래량"),
                    }
                )
        if not rows:
            raise KRXUnavailable(f"KRX {stamp} 수급이 비었다")
        return sorted(rows, key=lambda item: (str(item["code"]), str(item["investor"])))

    def shorting_on(self, day: date) -> list[dict[str, Any]]:
        """공매도 거래량·비중. KOSPI/KOSDAQ 을 따로 물어야 한다 (ALL 불가)."""
        self._require_credentials()
        stamp = day.strftime("%Y%m%d")
        api = self._stock()
        rows: list[dict[str, Any]] = []
        for board in SHORTING_BOARDS:
            frame = self._call(
                f"공매도 {stamp} {board}",
                partial(api.get_shorting_volume_by_ticker, stamp, board),
            )
            if frame is None or frame.empty:
                continue
            for code, record in frame.to_dict(orient="index").items():
                rows.append(
                    {
                        "code": str(code),
                        "board": board,
                        "short_volume": record.get("공매도"),
                        "total_volume": record.get("매수"),
                        "short_ratio": record.get("비중"),
                    }
                )
        if not rows:
            raise KRXUnavailable(f"KRX {stamp} 공매도가 비었다")
        return sorted(rows, key=lambda item: str(item["code"]))

    def fundamentals_on(self, day: date) -> list[dict[str, Any]]:
        """PER/PBR/EPS/BPS/DIV/DPS + 시가총액."""
        self._require_credentials()
        stamp = day.strftime("%Y%m%d")
        api = self._stock()
        valuation = self._call(
            f"펀더멘털 {stamp}",
            lambda: api.get_market_fundamental_by_ticker(stamp, market="ALL"),
        )
        if valuation is None or valuation.empty:
            raise KRXUnavailable(f"KRX {stamp} 펀더멘털이 비었다")

        caps = self._call(
            f"시총 {stamp}", lambda: api.get_market_cap_by_ticker(stamp, market="ALL")
        )
        cap_by_code = (
            {str(code): record for code, record in caps.to_dict(orient="index").items()}
            if caps is not None and not caps.empty
            else {}
        )

        rows: list[dict[str, Any]] = []
        for code, record in valuation.to_dict(orient="index").items():
            cap = cap_by_code.get(str(code), {})
            merged = {name: record.get(name) for name in VALUATION_COLUMNS}
            merged.update(
                {
                    "market_cap": cap.get("시가총액"),
                    "shares": cap.get("상장주식수"),
                }
            )
            rows.append({"code": str(code), **merged})
        return sorted(rows, key=lambda item: str(item["code"]))

    def indices_on(self, day: date) -> list[dict[str, Any]]:
        """지수 OHLCV. regime Analyst 의 입력이다."""
        self._require_credentials()
        stamp = day.strftime("%Y%m%d")
        api = self._stock()
        rows: list[dict[str, Any]] = []
        for board in SHORTING_BOARDS:
            frame = self._call(
                f"지수 {stamp} {board}",
                partial(api.get_index_ohlcv_by_ticker, stamp, board),
            )
            if frame is None or frame.empty:
                continue
            for code, record in frame.to_dict(orient="index").items():
                mapped = {
                    english: record.get(korean)
                    for korean, english in OHLCV_COLUMNS.items()
                }
                rows.append({"code": f"{board}:{code}", "board": board, **mapped})
        if not rows:
            raise KRXUnavailable(f"KRX {stamp} 지수가 비었다")
        return sorted(rows, key=lambda item: str(item["code"]))


def normalize_krx_frame(frame: Any) -> list[dict[str, Any]]:
    """KRX DataFrame → 코드별 dict 목록. 한글 컬럼은 여기서 끝난다."""
    rows: list[dict[str, Any]] = []
    for code, record in frame.to_dict(orient="index").items():
        row: dict[str, Any] = {"code": str(code)}
        for korean, english in OHLCV_COLUMNS.items():
            row[english] = record.get(korean)
        rows.append(row)
    return sorted(rows, key=lambda item: str(item["code"]))
