"""장중 현재가 — **화면 참고용이지 회계용이 아니다.**

## 왜 따로 두는가

대시보드의 모든 숫자는 창고의 **일봉 종가**에서 온다. NAV·수익률·낙폭·벤치마크가
전부 한국시간 15:40 하루 한 번 같은 시각으로 못 박혀 있고(accounting.md §2),
거기에 실시간 값을 섞으면 "포트폴리오는 지금 값, 벤치마크는 어제 값" 이 되어
그 차이가 통째로 가짜 초과수익·가짜 낙폭이 된다.

그래서 여기서 받아오는 값은 **표시 전용**이다. 회계 경로(`accounting/`)나
장부에 절대 들어가지 않는다. 화면에서도 "참고" 로 구분해 보여줘야 한다 —
안 그러면 다음 사람이 이 숫자로 손익을 맞춰 보려 한다.

## 왜 캐시가 필요한가

국장 콜 간격은 3.1초다(`MIN_INTERVAL_SEC_KR`). 종목마다 부르면 24종목에
74초가 들어 화면이 못 뜬다. 다행히 **다중 조회 TR 이 있다** — `t8407` 은
6자리 코드를 이어 붙여 한 번에 받는다(2026-08-18 실측: 3종목 1콜, 호가까지).

그래도 요청마다 부르면 새로고침이 곧 API 호출이 된다. 그래서 **짧은 TTL 캐시**를
두고, 만료 전에는 같은 값을 돌려준다. 캐시는 프로세스 수명 동안 살지만
TTL 이 짧아 낡은 값을 오래 보여주지 않는다 — `store/memo.py` 가 캐시를 요청
경계에서 버리는 것과 다른 선택이고, 이유는 여기 값이 **원래 흐르는 값**이라
"몇 초 낡음" 이 정상 상태이기 때문이다.

## 실패는 조용히 넘기되 사실은 남긴다

장외·장애·키 없음이면 값이 없다. 그때 **0 이나 종가로 때우지 않는다** — 0 은
폭락으로 읽히고 종가로 때우면 실시간인 척하는 거짓이 된다. 없으면 없다고
돌려주고(빈 dict), 화면이 "장외" 로 그린다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

#: 다중 현재가 TR. 경로는 ``/stock/market-data`` 다 — ``/stock/chart`` 로
#: 부르면 IGW00215 가 온다(t8410 과 같은 함정, ls-api.md §경로).
TR_MULTI_KR = "t8407"

#: 한 번에 보낼 수 있는 종목 수. LS 문서 기준 50 이고, 실측은 3종목까지
#: 확인했다. 넘겨 보내면 잘리므로 **나눠서** 부른다.
MAX_CODES_KR = 50

#: 캐시 수명(초). 장중 화면은 이 정도면 "지금 값" 으로 읽힌다. 짧게 잡을수록
#: API 호출이 늘고, 길게 잡을수록 화면이 낡는다.
TTL_SECONDS = 20.0


@dataclass(frozen=True)
class LiveQuote:
    """한 종목의 장중 값. **모두 그 시장 통화 그대로다** — 환산하지 않는다."""

    entity_id: str
    price: float
    change_rate: float
    bid: float
    ask: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "price": self.price,
            "change_rate": self.change_rate,
            "bid": self.bid,
            "ask": self.ask,
        }


def _bare(entity_id: str) -> str:
    """``KR:067290`` → ``067290``. 창고 정본에서 시장 접두어를 뗀다."""
    _, _, code = entity_id.strip().rpartition(":")
    return code or entity_id.strip()


class LiveQuoteCache:
    """TTL 캐시. **호출부는 이 객체 하나만 들고 있으면 된다.**

    스레드 안전하게 둔다 — Flask 는 요청을 동시에 처리하고, 캐시가 깨지면
    화면이 종목을 섞어 보여준다. 그건 조용히 틀리는 종류의 고장이다.
    """

    def __init__(self, client_factory: Any, *, ttl: float = TTL_SECONDS) -> None:
        self._factory = client_factory
        self._ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._quotes: dict[str, LiveQuote] = {}
        #: **물어본 것**을 따로 기억한다. 받은 것만 기억하면, 응답에 없는 종목
        #: (상장폐지·거래정지)이 하나라도 섞이는 순간 캐시가 영원히 안 맞아
        #: 매 요청마다 다시 부른다 — 실측으로 응답이 2.7초에서 9.7초가 됐다.
        #: 물어봤는데 안 온 것도 "이번 창에서는 없다" 는 사실이다.
        self._asked: set[str] = set()
        #: 클라이언트를 **재사용한다.** 호출마다 새로 만들면 그때마다 토큰을
        #: 다시 발급받는다. 그게 위 9.7초의 나머지 절반이었다.
        self._client: Any = None

    def get(self, entity_ids: list[str]) -> dict[str, LiveQuote]:
        """요청한 종목의 장중 값. 없는 것은 **빠진 채로** 돌려준다."""
        wanted = [e for e in dict.fromkeys(entity_ids) if e.startswith("KR:")]
        if not wanted:
            return {}
        with self._lock:
            fresh = (time.monotonic() - self._at) < self._ttl
            if fresh and self._asked.issuperset(wanted):
                return {e: self._quotes[e] for e in wanted if e in self._quotes}
        fetched = self._fetch(wanted)
        with self._lock:
            self._quotes.update(fetched)
            self._asked.update(wanted)
            self._at = time.monotonic()
        return fetched

    def _client_or_none(self) -> Any:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _fetch(self, entity_ids: list[str]) -> dict[str, LiveQuote]:
        client = self._client_or_none()
        if client is None:
            return {}
        out: dict[str, LiveQuote] = {}
        try:
            for start in range(0, len(entity_ids), MAX_CODES_KR):
                chunk = entity_ids[start : start + MAX_CODES_KR]
                codes = "".join(_bare(e) for e in chunk)
                data = client.request_tr(
                    "/stock/market-data",
                    TR_MULTI_KR,
                    {f"{TR_MULTI_KR}InBlock": {"nrec": len(chunk), "shcode": codes}},
                )
                for row in data.get(f"{TR_MULTI_KR}OutBlock1") or []:
                    code = str(row.get("shcode", "")).strip()
                    if not code:
                        continue
                    out[f"KR:{code}"] = LiveQuote(
                        entity_id=f"KR:{code}",
                        price=float(row.get("price") or 0.0),
                        # ``diff`` 는 퍼센트 문자열이다. 비율로 바꿔 담는다 —
                        # 화면 포맷터가 전부 비율을 받는다.
                        change_rate=float(row.get("diff") or 0.0) / 100.0,
                        bid=float(row.get("bidho") or 0.0),
                        ask=float(row.get("offerho") or 0.0),
                    )
        except Exception:
            # **장외·장애는 정상이다.** 여기서 예외를 올리면 화면이 통째로
            # 안 뜬다. 값이 없으면 없는 대로 그린다.
            #
            # 클라이언트는 닫지 않는다 — 재사용해야 토큰을 매번 다시 안 받는다.
            # 토큰이 만료되면 다음 호출이 실패하고 여기로 오는데, 그때 버려서
            # 다음 번에 새로 만들게 한다.
            self._client = None
            return out
        return out


# -- 지수 실시간 ------------------------------------------------------------------

#: 업종(지수) 현재가 TR. **경로가 `/indtp/market-data` 다.**
#: `/stock/market-data` 로 부르면 `IGW00215 유효하지 않은 TR CD` 가 온다 —
#: 같은 함정을 `t8410`(일봉)에서 한 번 겪었다(docs/design/ls-api.md).
TR_INDEX_KR = "t1511"
PATH_INDEX_KR = "/indtp/market-data"

#: 지수 → LS 업종코드. 선행 프로젝트(ls_kr_rl_trader)에서 검증된 값이다.
INDEX_UPCODE: dict[str, str] = {
    "KR:IDX:KOSPI": "001",
    "KR:IDX:KOSDAQ": "301",
}

#: 미장 대표 ETF 는 종목이라 `g3104` 로 받는다. 지수 자체는 LS 에 없다
#: (SPX·VIX 는 빈 응답 — 2026-08-19 실측). ETF 는 지수가 아니므로 화면이
#: 그 사실을 말해야 한다.
TR_QUOTE_US = "g3104"
PATH_QUOTE_US = "/overseas-stock/market-data"
US_PROXY_EXCHANGE: dict[str, str] = {
    "US:SPY": "81", "US:DIA": "81", "US:QQQ": "82", "US:SOXX": "82",
}


class LiveIndexCache:
    """지수·대표 ETF 의 장중 값. `LiveQuoteCache` 와 같은 규약이다.

    **왜 따로 두나** — 종목 시세는 `t8407` 로 50개씩 묶어 받는데 지수는
    업종코드 하나씩 다른 TR 을 쳐야 하고, 미장 ETF 는 또 다른 경로다.
    한 클래스에 세 경로를 넣으면 어느 실패가 어느 축인지 안 갈린다.

    실패는 조용히 넘긴다. **장외·권한 없음은 정상**이고, 여기서 예외를 올리면
    마켓 탭이 통째로 안 뜬다.
    """

    def __init__(self, client_factory: Any, *, ttl: float = TTL_SECONDS) -> None:
        self._factory = client_factory
        self._ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._quotes: dict[str, LiveQuote] = {}
        self._asked: set[str] = set()
        self._client: Any = None

    def get(self, entity_ids: list[str]) -> dict[str, LiveQuote]:
        wanted = [
            e for e in dict.fromkeys(entity_ids)
            if e in INDEX_UPCODE or e in US_PROXY_EXCHANGE
        ]
        if not wanted:
            return {}
        with self._lock:
            fresh = (time.monotonic() - self._at) < self._ttl
            if fresh and self._asked.issuperset(wanted):
                return {e: self._quotes[e] for e in wanted if e in self._quotes}
            fetched = self._fetch(wanted)
            self._quotes.update(fetched)
            # **물어본 것**을 기억한다 — 응답이 없는 축(권한 없음)을 매번 다시
            # 묻지 않기 위해서다. `LiveQuoteCache` 와 같은 이유.
            self._asked.update(wanted)
            self._at = time.monotonic()
            return {e: self._quotes[e] for e in wanted if e in self._quotes}

    def _client_or_none(self) -> Any:
        if self._client is None:
            try:
                self._client = self._factory()
            except Exception:
                return None
        return self._client

    def _fetch(self, entity_ids: list[str]) -> dict[str, LiveQuote]:
        client = self._client_or_none()
        if client is None:
            return {}
        out: dict[str, LiveQuote] = {}
        for entity in entity_ids:
            try:
                if entity in INDEX_UPCODE:
                    out.update(self._fetch_index(client, entity))
                else:
                    out.update(self._fetch_us_proxy(client, entity))
            except Exception:
                # 한 축이 막혀도 나머지는 살린다. 토큰 만료면 다음 호출에서
                # 다시 만들도록 클라이언트를 버린다.
                self._client = None
                continue
        return out

    def _fetch_index(self, client: Any, entity: str) -> dict[str, LiveQuote]:
        data = client.request_tr(
            PATH_INDEX_KR, TR_INDEX_KR,
            {f"{TR_INDEX_KR}InBlock": {"upcode": INDEX_UPCODE[entity]}},
        )
        block = data.get(f"{TR_INDEX_KR}OutBlock") or {}
        price = float(block.get("pricejisu") or 0.0)
        if price <= 0:
            return {}
        return {
            entity: LiveQuote(
                entity_id=entity,
                price=price,
                # ``diffjisu`` 가 등락률(%)이다. ``change`` 는 포인트 차이라
                # 헷갈리기 쉽다 — 실측으로 갈랐다(2026-08-19).
                change_rate=float(block.get("diffjisu") or 0.0) / 100.0,
                bid=0.0, ask=0.0,
            )
        }

    def _fetch_us_proxy(self, client: Any, entity: str) -> dict[str, LiveQuote]:
        symbol = entity.split(":", 1)[1]
        exchange = US_PROXY_EXCHANGE[entity]
        data = client.request_tr(
            PATH_QUOTE_US, TR_QUOTE_US,
            {f"{TR_QUOTE_US}InBlock": {
                "keysymbol": f"{exchange}{symbol}", "exchcd": exchange, "symbol": symbol,
            }},
        )
        block = data.get(f"{TR_QUOTE_US}OutBlock") or {}
        price = float(block.get("clos") or 0.0)
        prev = float(block.get("pcls") or 0.0)
        if price <= 0:
            return {}
        return {
            entity: LiveQuote(
                entity_id=entity,
                price=price,
                # g3104 는 등락률을 안 준다. 전일 종가(pcls)로 직접 낸다 —
                # 없으면 0 이 아니라 **비운다**(0 은 "안 움직였다" 로 읽힌다).
                change_rate=(price / prev - 1.0) if prev > 0 else 0.0,
                bid=0.0, ask=0.0,
            )
        }
