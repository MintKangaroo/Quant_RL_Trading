"""KRX Open API — 정식 경로.

## 왜 이게 중요한가

pykrx(data.krx.co.kr 스크래핑)는 **약관상 자동화 수집이 금지**돼 있고 실제로
IP 가 차단됐다. 이건 KRX 가 직접 안내한 공식 경로다 — 인증키를 발급받아
쓰는 것이 허용된 방법이다.

## 실측으로 확인한 것 (2026-08-12)

일별매매 응답에 **``LIST_SHRS``(상장주식수)와 ``MKTCAP``(시가총액)** 이 들어
있다. 이 둘이 없어서 ``fundamental`` 의 밸류 팩터(PER·PBR·PSR)를 못 만들고
있었다. 별도 조회 없이 시세와 같은 콜에서 나온다.

- ``/sto/stk_bydd_trd`` 유가증권 일별매매 — 954행
- ``/sto/ksq_bydd_trd`` 코스닥 일별매매 — 1,737행
- ``/idx/krx_dd_trd`` 지수 일별 — 38개 지수
- 5년 전(2021-08-12)도 정상 응답

날짜당 한 콜이라 시장 두 개면 세션당 2콜. 종목 축으로 돌 필요가 없다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import CollectorError

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
SOURCE = "krx_openapi"

#: 인증키는 헤더로 보낸다. 쿼리스트링에 실으면 로그·프록시에 그대로 남는다.
AUTH_HEADER = "AUTH_KEY"

#: 시장별 일별매매 경로. ALL 은 없고 시장마다 따로 부른다.
BOARDS = {
    "KOSPI": "/sto/stk_bydd_trd",
    "KOSDAQ": "/sto/ksq_bydd_trd",
    "KONEX": "/sto/knx_bydd_trd",
}

INDEX_PATH = "/idx/krx_dd_trd"

#: 응답 필드 → 우리 이름. 대문자 축약을 코드 전체에 퍼뜨리지 않는다.
#: **주의**: 일별매매의 ``ISU_CD`` 는 단축코드(095570)다. 같은 이름이
#: 종목기본정보에서는 ISIN(KR7095570008)이라 그대로 옮기면 code 가 None 이
#: 되고 전 행이 조용히 버려진다 — 실측에서 그렇게 0행이 나왔다.
TRADE_FIELDS = {
    "ISU_CD": "code",
    "ISU_NM": "name",
    "MKT_NM": "board",
    "SECT_TP_NM": "sector",
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value",
    "LIST_SHRS": "shares",      # ← 밸류 팩터를 막고 있던 것
    "MKTCAP": "market_cap",     # ← 그리고 이것
}

INDEX_FIELDS = {
    "IDX_NM": "name",
    "IDX_CLSS": "index_class",
    "OPNPRC_IDX": "open",
    "HGPRC_IDX": "high",
    "LWPRC_IDX": "low",
    "CLSPRC_IDX": "close",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value",
    "MKTCAP": "market_cap",
}


class KrxOpenApiUnavailable(CollectorError):
    """KRX Open API 가 데이터를 주지 않았다."""


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class KrxOpenApi:
    """정식 KRX Open API 클라이언트. 레포에서 이 API 를 부르는 유일한 곳."""

    api_key: str = ""
    name: str = SOURCE
    timeout: float = 60.0
    retries: int = 3
    retry_pause_sec: float = 1.5
    sleep: Callable[[float], None] = time.sleep
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("KRX_OPENAPI_KEY", "").strip()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(transport=self.transport, timeout=self.timeout)
        return self._client

    def _call(self, path: str, day: date) -> list[dict[str, Any]]:
        """외부 세계와의 경계. 여기서만 넓게 잡는다.

        빈 결과와 실패를 구분한다 — 휴장일은 빈 목록이 정상이고, 인증 실패는
        고쳐야 할 문제다. 둘을 뭉치면 키가 죽은 것을 휴장일로 착각한다.
        """
        if not self.api_key:
            raise KrxOpenApiUnavailable("KRX_OPENAPI_KEY 가 없다")

        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self._http().get(
                    f"{BASE_URL}{path}",
                    headers={AUTH_HEADER: self.api_key},
                    params={"basDd": day.strftime("%Y%m%d")},
                )
                if response.status_code == 401:
                    # 재시도해도 소용없다. 키 문제는 사람이 고쳐야 한다.
                    raise KrxOpenApiUnavailable(
                        f"인증 실패 ({response.text[:80]}). 인증키와 활용신청 상태를 확인할 것"
                    )
                if response.status_code != 200:
                    raise KrxOpenApiUnavailable(
                        f"{path} HTTP {response.status_code}: {response.text[:80]}"
                    )
                return list(response.json().get("OutBlock_1") or [])
            except KrxOpenApiUnavailable:
                raise
            except Exception as error:
                last = error
                if attempt + 1 < self.retries:
                    self.sleep(self.retry_pause_sec * (attempt + 1))
        raise KrxOpenApiUnavailable(
            f"{path} 실패 ({type(last).__name__}: {last})"
        ) from last

    # -- 조회 -------------------------------------------------------------------

    def trades_on(self, day: date) -> list[dict[str, Any]]:
        """그날 전 시장 일별매매. 시장마다 한 콜씩."""
        rows: list[dict[str, Any]] = []
        for board, path in BOARDS.items():
            for raw in self._call(path, day):
                mapped = {name: raw.get(field) for field, name in TRADE_FIELDS.items()}
                mapped["board"] = mapped.get("board") or board
                rows.append(mapped)
        # 빈 응답을 장애로 보지 않는다. API 가 200 을 주면서 0행이면 그날은
        # 장이 안 선 것이다 — 실측: 2026-07-17 은 exchange_calendars 가 거래일로
        # 보지만 KRX 는 양 시장 모두 0행을 준다(앞뒤 날짜는 정상). 달력
        # 라이브러리가 모르는 휴장일이 존재한다.
        #
        # 장애는 _call 이 예외로 구분한다. 둘을 뭉치면 진짜 장애가 휴장일로
        # 위장된다.
        return sorted(rows, key=lambda item: str(item.get("code") or ""))

    def indices_on(self, day: date) -> list[dict[str, Any]]:
        """그날 지수. regime Analyst 의 입력."""
        rows = [
            {name: raw.get(field) for field, name in INDEX_FIELDS.items()}
            for raw in self._call(INDEX_PATH, day)
        ]
        return sorted(rows, key=lambda item: str(item.get("name") or ""))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


# -----------------------------------------------------------------------------
# 정규화
# -----------------------------------------------------------------------------


def normalize_shares(
    rows: list[dict[str, Any]],
    *,
    market: str,
    valid_from: datetime,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """상장주식수·시가총액 → **market_stats** 행 (long 포맷).

    처음엔 fundamentals 에 넣었는데 그게 잘못이었다. 분기에 4번 바뀌는 공시와
    매일 바뀌는 관측을 한 테이블에 두니, 재무를 읽을 때마다 일별 데이터
    435만 행이 딸려 왔다. fundamental Analyst 가 2.2GB 를 올리다 OOM 으로
    죽고 나서야 드러났다.

    Analyst 는 둘을 따로 읽어 합친다. 재무는 창이 길고(1150일) 시가총액은
    최신 하나면 되므로, 창 길이가 다른 것을 같이 읽을 이유가 없다.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        for metric in ("shares", "market_cap"):
            value = _number(row.get(metric))
            if value is None or value <= 0:
                continue
            out.append(
                {
                    "entity_id": f"{market}:{code}",
                    "valid_from": valid_from,
                    "observed_at": observed_at,
                    "source": SOURCE,
                    "market": market,
                    "metric": metric,
                    "value": value,
                }
            )
    return out


def normalize_sectors(
    rows: list[dict[str, Any]],
    *,
    market: str,
    valid_from: datetime,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """일별매매 응답 → **sectors** 행.

    ``SECT_TP_NM`` 이 담고 있다 — LS 유니버스(t8436)에는 이 필드가 없어서
    지금까지 창고에 섹터가 하나도 없었다. 시세와 같은 콜에서 나오므로
    추가 호출이 없다(normalize_shares 와 같은 자리).

    섹터가 빈 문자열이면 행을 만들지 않는다. **모르는 것을 채우지 않는다** —
    빈 문자열이나 "기타" 같은 바구니로 메우면 그 종목들이 selector 에서
    한 섹터로 묶여 섹터 상한이 엉뚱한 종목들을 걸러낸다.

    **주의(실측 2026-08-13):** 이 필드는 업종(GICS 류) 분류가 아니라
    **소속부**다. KOSPI 는 전량 빈 문자열(커버리지 0), KOSDAQ 만 우량기업부·
    벤처기업부·중견기업부·기술성장기업부 등으로 갈린다 — 업종 분산이 아니라
    KOSDAQ 시장 구분만 반영한다. store/tables.py 의 ``sectors`` 스펙 참조.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        sector = str(row.get("sector") or "").strip()
        if not code or not sector:
            continue
        out.append(
            {
                "entity_id": f"{market}:{code}",
                "valid_from": valid_from,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": market,
                "sector": sector,
            }
        )
    return out


def normalize_indices(
    rows: list[dict[str, Any]],
    *,
    market: str,
    valid_from: datetime,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """지수 → indices 행."""
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        close = _number(row.get("close"))
        if not name or close is None:
            continue
        out.append(
            {
                "entity_id": f"{market}:IDX:{name}",
                "valid_from": valid_from,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": market,
                "board": str(row.get("index_class") or ""),
                "open": _number(row.get("open")),
                "high": _number(row.get("high")),
                "low": _number(row.get("low")),
                "close": close,
                "volume": _number(row.get("volume")),
                "value": _number(row.get("value")),
            }
        )
    return out
