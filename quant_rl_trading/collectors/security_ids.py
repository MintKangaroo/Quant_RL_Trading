"""CUSIP → 티커 — OpenFIGI.

    uv run python tools/map_cusip_tickers.py --dry-run
    uv run python tools/map_cusip_tickers.py

## 왜 이 파일이 필요한가

SEC 는 13F 에 **CUSIP 만** 준다. 그래서 ``filings_13f`` 의 ``entity_id`` 가
``CUSIP:02079K305`` 이고, 창고의 다른 모든 미장 테이블은 ``US:GOOGL`` 이다.
둘이 안 붙으므로 13F 는 개설 이래 종목 축에 한 번도 못 올라왔다 —
``flow_us`` 가 신호 0건인 이유의 하나가 이것이다.

SEC 가 배포하는 ``company_tickers.json`` 은 **CIK↔티커**라 CUSIP 이 없다.
CUSIP 은 CUSIP Global Services 의 유료 자산이고 SEC 가 매핑표를 내주지
않는다. OpenFIGI(Bloomberg 의 공개 식별자 서비스)는 키 없이 CUSIP→티커를
주는, 실측으로 확인된 유일한 무료 경로다.

## 이름으로 때우면 안 되는 이유

``issuer`` 문자열이 있으니 이름으로 붙이고 싶어진다. 하지 않는다. "APPLE
INC" 같은 이름은 유일하지 않고, 클래스주(ALPHABET INC-CL A / CL C)는 이름이
사실상 같다. **틀린 매핑은 에러를 내지 않는다** — 남의 종목 수급이 이 종목
신호가 되고, 그 신호로 주문이 나갈 때까지 아무도 모른다.

## 실측 두 가지 (2026-08-19)

### 1. 문자로 시작하는 식별자는 ``ID_CUSIP`` 으로 못 찾는다

외국 발행사는 CUSIP 이 아니라 **CINS**(CUSIP International Numbering
System)를 쓴다 — ``G1151C101``(ACCENTURE PLC), ``H8817H100``(TRANSOCEAN).
13F 는 이것도 ``cusip`` 칸에 담아 보낸다. OpenFIGI 에 ``ID_CUSIP`` 으로
물으면 **"No identifier found."** 가 온다. 없는 게 아니라 **묻는 방식이
틀린 것**이다. ``ID_CINS`` 로 물으면 나온다. 우리 CUSIP 4,150개 중 361개가
여기 해당한다 — 이걸 모르면 8.7% 를 "매핑 실패" 로 적고 넘어가게 된다.

### 2. 키 없이는 요청당 10건이다

문서에 100건이라고 적힌 곳이 많은데 그건 API 키가 있을 때다. 키 없이
100건을 보내면 ``413 Request may only contain 10 mapping jobs.`` 가 온다.
분당 25요청이므로 4,150건이면 415요청 ≈ 17분이다.

## 이 표의 이중시간 — 여기가 이 파일에서 제일 조심스러운 곳

OpenFIGI 는 **지금 시점의 식별자 상태**만 준다. "2024년에 이 CUSIP 이 어느
티커였나" 는 안 알려준다. 그래서 두 선택지가 있었다:

    (a) valid_from = observed_at = 조회한 날
    (b) valid_from = observed_at = 선언한 기준시점(IDENTITY_EPOCH)

(a) 는 관측축에 정직하지만, ``store.get(as_of=과거)`` 가 이 표를 **한 행도**
못 본다. 그러면 13F 는 오늘 이후로만 종목 축에 오르고 IC 는 영영 못 잰다.

(b) 를 골랐다. 근거: **식별자는 그 시점에도 공개된 사실**이었다. CUSIP
037833100 이 AAPL 이라는 것은 2015년에도 알 수 있었다. 우리가 오늘 물어본
것뿐이다.

**남는 위험을 숨기지 않는다.** 그 사이에 티커가 바뀐 종목은 옛 보유가
**오늘 티커**로 붙는다. 다만 창고의 ``prices``·``universe`` 도 오늘 명단·
오늘 티커라(us_universe 모듈 docstring 의 생존편향), 옛 티커로 붙이면 오히려
가격이 없어 조인이 깨진다. 진짜 오염은 **CUSIP 이 다른 회사로 재배정된**
경우인데 드물다. 그리고 우리가 실제로 언제 물어봤는지는 ``mapped_at`` 에
남긴다 — 그 열이 있으면 나중에 "이 매핑은 언제 찍은 스냅샷인가" 를 물을 수
있다.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import CollectorError

TABLE = "security_ids"
SOURCE = "openfigi"
MARKET = "US"

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

#: 키 없이 부를 때의 상한. 넘기면 413 이다 (모듈 docstring 실측 2).
MAX_JOBS_PER_REQUEST = 10

#: 분당 25요청. 25 로 딱 맞추면 경계에서 429 가 나므로 여유를 둔다.
MIN_INTERVAL_SEC = 2.6

#: 미국 통합(composite) 거래소 코드. NYSE·나스닥을 따로 받으면 한 CUSIP 이
#: 티커 하나에 여러 줄로 붙어 조인이 뻥튀기된다.
US_EXCH_CODE = "US"

#: 식별자가 문자로 시작하면 CINS 다 (모듈 docstring 실측 1).
CUSIP_TYPE = "ID_CUSIP"
CINS_TYPE = "ID_CINS"

#: 매핑의 기준시점. **왜 오늘이 아닌가는 모듈 docstring 참고.**
IDENTITY_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)


class SecurityIdError(CollectorError):
    pass


@dataclass(frozen=True)
class Mapping:
    """CUSIP 하나에 대한 미국 통합 상장 하나."""

    identifier: str
    id_type: str
    ticker: str
    name: str
    figi: str
    composite_figi: str
    security_type: str

    @property
    def entity_id(self) -> str:
        return f"{MARKET}:{self.ticker}"


@dataclass(frozen=True)
class Miss:
    """못 붙인 것. **버리지 않고 이유와 함께 돌려준다.**

    "매핑 0건" 과 "매핑을 시도한 적 없음" 을 구분해야 하고, 이유별로 성격이
    다르다 — 채권·옵션은 애초에 종목 축이 아니고, 미상장은 언젠가 상장한다.
    """

    identifier: str
    id_type: str
    reason: str


def id_type_for(identifier: str) -> str:
    """``G1151C101`` 은 CUSIP 이 아니라 CINS 다. 틀리면 "없다" 가 온다."""
    return CINS_TYPE if identifier[:1].isalpha() else CUSIP_TYPE


def mapping_job(identifier: str) -> dict[str, str]:
    """OpenFIGI 요청 한 건.

    ``exchCode`` 를 요청에 넣는 이유: 안 넣으면 한 CUSIP 에 상장 거래소별로
    100줄 넘게 온다(ACCENTURE 실측 118줄). 받아서 거르나 안 받나 결과는
    같은데, 받으면 응답이 100배 커진다.
    """
    return {"idType": id_type_for(identifier), "idValue": identifier, "exchCode": US_EXCH_CODE}


def batched(items: Sequence[str], size: int = MAX_JOBS_PER_REQUEST) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def parse_result(identifier: str, result: dict[str, Any]) -> Mapping | Miss:
    """응답 한 칸 → 매핑 하나 또는 실패 하나.

    **여러 줄이 오면 붙이지 않는다.** ``exchCode=US`` 로 좁혔는데도 둘 이상이
    나온다면 그 CUSIP 은 우리가 모르는 방식으로 갈라져 있는 것이고, 그때
    아무거나 고르면 그게 바로 "조용히 틀린 매핑" 이다.
    """
    id_type = id_type_for(identifier)
    if "error" in result:
        return Miss(identifier, id_type, f"openfigi_error:{result['error']}")
    rows = result.get("data") or []
    if not rows:
        # 채권·옵션·비상장·미국 밖 상장이 전부 여기로 온다. 이유를 더 쪼갤
        # 수 없다 — OpenFIGI 는 "없다" 만 말한다.
        return Miss(identifier, id_type, "not_found")
    if len(rows) > 1:
        tickers = sorted({str(row.get("ticker")) for row in rows})
        return Miss(identifier, id_type, f"ambiguous:{','.join(tickers)}")

    row = rows[0]
    ticker = str(row.get("ticker") or "").strip().upper()
    if not ticker:
        return Miss(identifier, id_type, "no_ticker")
    return Mapping(
        identifier=identifier,
        id_type=id_type,
        ticker=ticker,
        name=str(row.get("name") or ""),
        figi=str(row.get("figi") or ""),
        composite_figi=str(row.get("compositeFIGI") or ""),
        security_type=str(row.get("securityType") or ""),
    )


class OpenFigiClient:
    """OpenFIGI 호출. **재시도로 밀어붙이지 않는다.**

    429 가 왔다는 것은 우리가 약속을 어겼다는 뜻이다. 곧바로 다시 때리면
    차단으로 간다 — 한 번 기다렸다가 그래도 안 되면 그냥 실패로 올린다.
    """

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        interval: float = MIN_INTERVAL_SEC,
        sleep: Any = time.sleep,
    ) -> None:
        self._timeout = timeout
        self._client = client
        self._interval = interval
        self._sleep = sleep
        self._last_call: float | None = None

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self._interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = time.monotonic()

    def _post(self, jobs: list[dict[str, str]]) -> list[dict[str, Any]]:
        http = self._client or httpx.Client(timeout=self._timeout)
        try:
            self._pace()
            response = http.post(OPENFIGI_URL, json=jobs)
            if response.status_code == 429:
                # 한 번만 물러선다. 그 이상은 우리 쪽 속도 설정이 틀린 것이고,
                # 그건 재시도가 아니라 코드가 고쳐야 할 문제다.
                self._sleep(self._interval * 5)
                self._pace()
                response = http.post(OPENFIGI_URL, json=jobs)
            if response.status_code != 200:
                raise SecurityIdError(
                    f"OpenFIGI {response.status_code}: {response.text[:200]}"
                )
            return list(response.json())
        finally:
            if self._client is None:
                http.close()

    def map_batch(self, identifiers: Sequence[str]) -> tuple[list[Mapping], list[Miss]]:
        if len(identifiers) > MAX_JOBS_PER_REQUEST:
            raise SecurityIdError(
                f"요청당 {MAX_JOBS_PER_REQUEST}건까지다 (키 없이). {len(identifiers)}건을 보냈다"
            )
        results = self._post([mapping_job(one) for one in identifiers])
        if len(results) != len(identifiers):
            # 순서로 짝을 맞추므로 개수가 다르면 짝이 어긋난다 — 그 상태로
            # 진행하면 **A 의 답이 B 의 매핑**이 된다. 멈추는 게 옳다.
            raise SecurityIdError(
                f"응답 개수가 다르다: 요청 {len(identifiers)} · 응답 {len(results)}"
            )
        mapped: list[Mapping] = []
        missed: list[Miss] = []
        for identifier, result in zip(identifiers, results, strict=True):
            parsed = parse_result(identifier, result)
            (mapped if isinstance(parsed, Mapping) else missed).append(parsed)  # type: ignore[arg-type]
        return mapped, missed


def to_rows(mappings: Iterable[Mapping], *, mapped_at: datetime) -> list[dict[str, Any]]:
    """창고 행으로.

    ``valid_from``·``observed_at`` 이 둘 다 ``IDENTITY_EPOCH`` 인 이유는 모듈
    docstring 에 있다. 실제 조회 시각은 ``mapped_at`` 에 남는다.
    """
    if mapped_at.tzinfo is None:
        raise SecurityIdError("mapped_at 에 타임존이 없다")
    rows: list[dict[str, Any]] = []
    for mapping in mappings:
        rows.append({
            "entity_id": mapping.entity_id,
            "valid_from": IDENTITY_EPOCH,
            "observed_at": IDENTITY_EPOCH,
            "source": SOURCE,
            "market": MARKET,
            "id_type": "CINS" if mapping.id_type == CINS_TYPE else "CUSIP",
            "id_value": mapping.identifier,
            "figi": mapping.figi,
            "composite_figi": mapping.composite_figi,
            "security_type": mapping.security_type,
            "name": mapping.name,
            "mapped_at": mapped_at,
        })
    return rows


def ingest_run_id(snapshot: datetime) -> str:
    """스냅샷 하루가 한 실행이다.

    눈금 규칙은 "다시 받는 주기보다 잘게" 다. 이 표는 새 CUSIP 이 생길 때
    (분기마다) 다시 받으므로 날짜 눈금이면 충분하고, 같은 날 두 번 돌리면
    두 번째가 거부된다 — 그게 맞다. 하루에 두 번 물어봐야 할 데이터가
    아니다.
    """
    return f"figi-{snapshot.astimezone(UTC).date().isoformat().replace('-', '')}"
