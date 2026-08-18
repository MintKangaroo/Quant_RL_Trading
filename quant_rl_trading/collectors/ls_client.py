"""LS증권 Open API 클라이언트.

port(collectors): LS_KR broker/ls_client.py 이식 (인증·토큰갱신·TR호출·레이트리밋).
학습·상태설계·보상 관련 코드는 가져오지 않았다 — CLAUDE.md 참고 규칙.

이식하면서 바꾼 것:

1. ``time.time()`` → Clock 주입 (불변식 2). 원본은 레이트리밋과 토큰 만료를
   모두 벽시계로 쟀다. Quant_RL_Trading 에서는 시간의 출처가 하나뿐이다.
2. ``requests`` → ``httpx``. 테스트에서 transport 를 갈아끼워 네트워크 없이 돈다.
3. 성공 판정은 **KR 판을 기준으로 삼는다.** US 판의 ``rsp_cd.startswith("0")`` 은
   지나치게 포괄적이라 실패를 성공으로 읽는다 (postmortem-ls.md §6-2).

가장 값진 이식물은 코드가 아니라 **예외코드 지식**이다. 아래 성공코드 셋은
문서가 아니라 실거래 사고로 하나씩 발견된 것이다. 이 리스트를 잃으면 같은
사고를 다시 겪는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import LSAPIError, MissingCredentials
from quant_rl_trading.replay.clock import Clock, LiveClock

PATH_OAUTH = "/oauth2/token"
PATH_MARKET = "/stock/market-data"
#: 외인/기관 수급. **market-data 가 아니다** — 경로를 틀리면 TR 이 없다고 나오고
#: (IGW00215), 그러면 "LS 에는 수급이 없다" 는 잘못된 결론에 도달한다.
PATH_FLOW = "/stock/frgr-itt"
#: 국장 차트(일봉 ``t8410`` 등). **``PATH_MARKET`` 이 아니다** — 실측
#: 2026-08-15: ``/stock/market-data`` 로 t8410 을 부르면 HTTP 500
#: ``IGW00215``(유효하지 않은 TR CD), ``/stock/chart`` 로 부르면 정상 200행.
#: ``PATH_FLOW`` 와 같은 함정이고, 미장도 차트만 별도 경로다
#: (``ls_us_source.PATH_CHART`` = ``/overseas-stock/chart``).
PATH_CHART = "/stock/chart"
PATH_INVESTOR = "/stock/investor"
PATH_ACCNO = "/stock/accno"
PATH_ORDER = "/stock/order"

#: port: LS_KR ls_client.py:55. 3초당 1건 권장 → 3.1초 여유.
#: 미장은 1.05초였다(US ls_client.py:57). 시장별로 다르므로 인자로 뺐다.
MIN_INTERVAL_SEC_KR = 3.1
MIN_INTERVAL_SEC_US = 1.05

#: 토큰 만료 여유. port: LS_KR ls_client.py:183.
TOKEN_LEEWAY = timedelta(seconds=60)

#: port: LS_KR ls_client.py:283-287.
#: 00000 만 성공으로 보면 정상 주문을 에러로 오분류한다 — 실제로 체결됐는데
#: 예외가 났다. 아래 코드는 전부 실거래 중 사고로 발견됐다.
#:   00039 = 매도완료 (2026-06-22 발견)
#:   00040 = 매수완료 (2026-06-19 발견)
#:   00463 = 취소완료 (2026-07-15 발견)
OK_CODES = frozenset({"00000", "00039", "00040", "00463"})

#: 미발견 완료코드 대비 안전망. LS 응답이 "완료 되었습니다"/"완료되었습니다" 로
#: 띄어쓰기가 달라 00463 을 놓쳤던 이력이 있어 공백을 지우고 비교한다.
OK_MESSAGE = "완료되었습니다"

#: 토큰 무효 코드. KR/US 가 같은 appkey 를 공유하면 한쪽 재발급이 다른 쪽을
#: 무효화한다. Quant_RL_Trading 는 KR/US 별도 appkey 를 쓰는 것을 전제로 하지만,
#: 복구 경로는 남겨 둔다 (postmortem-ls.md §6-1).
TOKEN_INVALID = "IGW00121"

#: 호출 한도 초과. "호출 거래건수를 초과하였습니다" 로 온다.
#: **에러가 아니라 속도 신호다.** 미장 일봉(g3204)을 1.05초 간격으로 치면
#: 수십 호출 만에 나온다 — 간격만으로는 못 막고 물러섰다 다시 쳐야 한다.
RATE_LIMITED = "IGW00201"

#: 한도에 걸렸을 때 물러설 횟수. 지수 백오프라 마지막엔 수십 초를 쉰다.
RATE_LIMIT_RETRIES = 5

#: 백오프 기준 초. attempt 마다 2배씩 는다 (4·8·16·32·64초).
RATE_LIMIT_BACKOFF_SEC = 4.0

#: ``live_trading=False`` 일 때도 나가는 read-only TR. 나머지는 보내지 않고
#: ``{"paper": True}`` 를 돌려준다.
#:
#: ⚠️ **이건 우리 쪽 안전장치이지 LS 가 막아 주는 것이 아니다.** 예전 주석은
#: "잔고(t0424)·주문(CSPAT*)은 LS 가 막는다" 고 적고 있었는데 **틀렸다** —
#: 2026-08-15 실측으로 **모의 appkey 로도 t0424 가 정상 응답했다**. 모의투자
#: 계좌는 잔고 조회도 주문도 되는 것이 정상이다(그게 모의투자의 목적이다).
#:
#: 그래서 ``paper`` 라는 이름을 "모의 계좌다" 로 읽으면 안 된다. 그 값은
#: **"우리가 이 TR 을 안 보냈다"** 는 뜻일 뿐이다. 계좌가 모의인지 실전인지는
#: :meth:`LSCredentials.declared_kind` 로만 알 수 있다 — 아래 참고.
#: port: LS_KR ls_client.py:58-66.
PAPER_ALLOWED_TR = frozenset(
    {
        "t8436",  # 종목 마스터
        "t1102",  # 현재가
        "t8407",  # 복수종목 현재가
        "t8410",  # 일봉
        "t1511",  # 업종 지수
        "t1717",  # 외인기관종목별동향 — 읽기 전용 시세 정보다
        # 해외주식. 국장 TR 과 이름이 겹치지 않아 한 집합에 둔다.
        "g3101",  # 해외주식 종목 단건 조회
        "g3104",  # 해외주식 현재가
        "g3204",  # 해외주식 일/주/월 차트
    }
)


@dataclass(frozen=True)
class HealthParams:
    """수집 건전성 판정 임계치. 전부 store.config 에서 온다 (불변식 10).

    ``FillParams`` 와 같은 패턴이다 — 호출자가 as_of 시점 설정을 읽어 넣고,
    클라이언트 자체는 입력만으로 결정되는 상태로 남는다.
    """

    window_sec: float = 120.0
    min_samples: int = 8
    history_sec: float = 600.0

    @classmethod
    def from_store(cls, store: Any, *, as_of: datetime) -> HealthParams:
        return cls(
            window_sec=float(store.config("collector.error_rate_window_sec", as_of=as_of)),
            min_samples=int(store.config("collector.error_rate_min_samples", as_of=as_of)),
            history_sec=float(store.config("collector.call_history_sec", as_of=as_of)),
        )


@dataclass
class Token:
    access_token: str
    token_type: str
    expires_at: datetime

    def valid_at(self, moment: datetime) -> bool:
        return self.expires_at > moment + TOKEN_LEEWAY


@dataclass
class LSCredentials:
    appkey: str
    appsecret: str
    base_url: str
    #: ``real`` | ``paper`` | ``""``(미선언). 사람이 ``LS_ACCOUNT_KIND`` 로
    #: 선언한다 — 코드는 알아낼 수 없다(:meth:`declared_kind` 참고).
    kind: str = ""

    @classmethod
    def from_env(
        cls, env: dict[str, str] | None = None, *, prefix: str = "LS_"
    ) -> LSCredentials:
        """``prefix`` 로 키 묶음을 고른다 (``LS_`` = 국장, ``LS_US_`` = 미장).

        **국장과 미장은 별도 appkey 를 쓴다.** LS 는 appkey 당 토큰 하나만
        유효해서, 같은 키를 공유하면 한쪽이 재발급할 때 다른 쪽 토큰이
        IGW00121 로 죽는다 (postmortem-ls.md §6-1).
        """
        source = env if env is not None else dict(os.environ)
        return cls(
            appkey=source.get(f"{prefix}APPKEY", "").strip(),
            appsecret=source.get(f"{prefix}APPSECRET", "").strip(),
            # base_url 은 국장·미장이 같은 호스트다. 미장 전용 값이 없으면
            # 국장 것을 쓴다.
            base_url=(
                source.get(f"{prefix}REST_BASE_URL")
                or source.get("LS_REST_BASE_URL", "")
            ).rstrip("/"),
            # 미선언이면 빈 문자열. **모르는 것을 'paper' 로 기본값 잡지 않는다** —
            # 그게 이 결함의 원인이었다("모의겠거니" 하고 실전에 주문).
            kind=source.get(f"{prefix}ACCOUNT_KIND", "").strip().lower(),
        )

    def usable(self) -> bool:
        """키가 실제로 채워졌는지. 템플릿 값(``YOUR_``)은 없는 것으로 본다."""
        return bool(self.appkey and self.appsecret) and not self.appkey.startswith("YOUR_")

    @property
    def fingerprint(self) -> str:
        """appkey 의 지문. **키 자체는 절대 남기지 않는다.**

        같은 계좌인지 비교하고 로그·설정에 적기 위한 값이라, 되돌릴 수 없는
        해시여야 한다. 앞 네 글자를 그대로 쓰는 식이면 그건 키 조각이다.
        """
        if not self.appkey:
            return ""
        return hashlib.sha256(self.appkey.encode()).hexdigest()[:12]

    @property
    def declared_kind(self) -> str:
        """이 계좌가 모의인지 실전인지 — **사람이 선언한 값**이다.

        **코드는 이걸 알아낼 방법이 없다.** 2026-08-15 에 실측으로 확인한 것:

        - 모의·실전이 **같은 호스트**(``openapi.ls-sec.co.kr:8080``)를 쓴다
        - 둘 다 appkey 가 ``PS`` 로 시작하고 길이가 같다
        - **모의 키로도 t0424(잔고)가 정상 응답한다** — LS 가 안 막는다
        - 예수금·보유종목으로 짐작할 수는 있지만 그건 추측이다
          (모의 5.18억 / 실전 5천원이었을 뿐, 반대일 수도 있다)

        판별할 수 없으면 **선언하게 하고 그 선언을 지문에 묶는 것**이 유일하게
        정직한 방법이다. 선언이 없으면 빈 문자열이고, 주문 경로는 그 상태에서
        진행하면 안 된다 (``tools/verify_live_order.py``).
        """
        return self.kind


@dataclass
class LSClient:
    """TR 호출 하나에 필요한 전부.

    ``live_trading`` 이 False 면 read-only TR 만 나간다. 키가 없어도 죽지 않고
    paper 응답을 돌려준다 — 원본의 좋은 설계라 그대로 가져왔다
    (LS_KR config/loader.py:85-114).
    """

    credentials: LSCredentials
    clock: Clock = field(default_factory=LiveClock)
    transport: httpx.BaseTransport | None = None
    timeout: float = 10.0
    live_trading: bool = False
    min_interval_sec: float = MIN_INTERVAL_SEC_KR
    #: 기본값은 선행 프로젝트에서 검증된 값이다. 운영에서는 호출자가
    #: HealthParams.from_store 로 as_of 시점 설정을 읽어 넣는다.
    health: HealthParams = field(default_factory=HealthParams)
    sleep: Callable[[float], None] = time.sleep
    _token: Token | None = field(default=None, repr=False)
    _last_call: datetime | None = field(default=None, repr=False)
    _calls: deque[tuple[datetime, bool]] = field(default_factory=deque, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _client: httpx.Client | None = field(default=None, repr=False)

    # -- 건전성 ---------------------------------------------------------------

    def _record_call(self, ok: bool) -> None:
        """port: LS_KR ls_client.py:110-126. 롤링 윈도.

        US 는 누적 카운터라 하루가 지나면 옛 실패가 영원히 남는다. KR 방식을 쓴다.
        """
        self._calls.append((self.clock.now(), ok))
        cutoff = self.clock.now() - timedelta(seconds=self.health.history_sec)
        while self._calls and self._calls[0][0] < cutoff:
            self._calls.popleft()

    def recent_error_rate(self) -> float:
        """최근 오류율. 킬스위치가 이 값을 본다.

        임계치를 인자 기본값으로 들지 않는다 — 킬스위치가 언제 어떤 기준으로
        발동했는지가 코드에만 남으면 사후에 재구성할 수 없다 (불변식 10).
        """
        cutoff = self.clock.now() - timedelta(seconds=self.health.window_sec)
        recent = [ok for moment, ok in self._calls if moment >= cutoff]
        if len(recent) < self.health.min_samples:
            return 0.0
        return sum(1 for ok in recent if not ok) / len(recent)

    # -- 레이트리밋 -----------------------------------------------------------

    def _respect_rate_limit(self) -> None:
        """호출 간 최소 간격 보장.

        경과 시간은 Clock 으로 잰다. ``time.time()`` 을 쓰면 리플레이에서
        레이트리밋이 시뮬레이션 시각을 따라가 버린다 (불변식 2).
        """
        now = self.clock.now()
        if self._last_call is not None:
            elapsed = (now - self._last_call).total_seconds()
            wait = self.min_interval_sec - elapsed
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.clock.now()

    def _back_off(self, attempt: int) -> None:
        """한도 초과 후 물러서기. 지수적으로 는다.

        간격을 영구적으로 늘리지 않는 이유는, 한도가 순간 버스트에 걸리는
        것이지 평균 속도에 걸리는 것이 아니기 때문이다. 평균을 늦추면 7천
        종목 백필이 하루를 넘긴다.
        """
        self.sleep(RATE_LIMIT_BACKOFF_SEC * (2**attempt))
        self._last_call = self.clock.now()

    # -- 토큰 -----------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(transport=self.transport, timeout=self.timeout)
        return self._client

    def invalidate_token(self) -> None:
        with self._lock:
            self._token = None

    def ensure_token(self, *, allow_paper: bool = False) -> Token:
        """OAuth client_credentials. port: LS_KR ls_client.py:137-186."""
        if not self.live_trading and not (allow_paper and self.credentials.usable()):
            return Token(
                access_token="PAPER_PLACEHOLDER",
                token_type="Bearer",
                expires_at=self.clock.now() + timedelta(hours=1),
            )

        with self._lock:
            if self._token and self._token.valid_at(self.clock.now()):
                return self._token

            if not self.credentials.usable():
                raise MissingCredentials("LS_APPKEY / LS_APPSECRET 미설정")

            response = self._http().post(
                f"{self.credentials.base_url}{PATH_OAUTH}",
                headers={"content-type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "appkey": self.credentials.appkey,
                    "appsecretkey": self.credentials.appsecret,
                    "scope": "oob",
                },
            )
            if response.status_code != 200:
                raise LSAPIError(
                    f"OAuth 실패 status={response.status_code} body={response.text[:300]}"
                )
            body = response.json()
            access = body.get("access_token")
            if not access:
                raise LSAPIError(f"OAuth 응답에 access_token 없음: {body}")

            expires_in = int(body.get("expires_in", 86400))
            self._token = Token(
                access_token=str(access),
                token_type=str(body.get("token_type", "Bearer")),
                expires_at=self.clock.now() + timedelta(seconds=max(60, expires_in)),
            )
            return self._token

    # -- TR 호출 --------------------------------------------------------------

    def request_tr(
        self,
        path: str,
        tr_cd: str,
        body: dict[str, Any],
        *,
        tr_cont: str = "N",
        tr_cont_key: str = "",
        count_health: bool = True,
    ) -> dict[str, Any]:
        """공통 TR 호출. port: LS_KR ls_client.py:199-309."""
        allow_paper = tr_cd in PAPER_ALLOWED_TR
        if not self.live_trading and not (allow_paper and self.credentials.usable()):
            return {"paper": True, "tr_cd": tr_cd, "echo": body}

        url = f"{self.credentials.base_url}{path}"
        try:
            for attempt in range(RATE_LIMIT_RETRIES + 2):
                self._respect_rate_limit()
                token = self.ensure_token(allow_paper=allow_paper)
                response = self._send(url, tr_cd, tr_cont, tr_cont_key, token, body)

                # 토큰 무효는 HTTP 500 + 본문으로 오기도, rsp_cd 로 오기도 한다.
                if TOKEN_INVALID in (response.text or "") and attempt == 0:
                    self.invalidate_token()
                    continue

                # 한도 초과도 HTTP 500 + 본문으로 온다. 이건 실패가 아니라
                # **너무 빨랐다**는 뜻이라, 던지지 말고 물러섰다 다시 친다.
                # 던지면 백필이 그 종목을 실패로 넘기고, 한도가 풀린 뒤에도
                # 빈 채로 남는다.
                if RATE_LIMITED in (response.text or "") and attempt < RATE_LIMIT_RETRIES:
                    self._back_off(attempt)
                    continue

                if response.status_code != 200:
                    raise LSAPIError(
                        f"TR {tr_cd} HTTP {response.status_code} body={response.text[:300]}"
                    )

                try:
                    data: dict[str, Any] = response.json()
                except ValueError as error:
                    raise LSAPIError(
                        f"TR {tr_cd} JSON 파싱 실패: {response.text[:300]}"
                    ) from error

                rsp_cd = data.get("rsp_cd")
                if rsp_cd and rsp_cd not in OK_CODES and not _is_completion(data):
                    if rsp_cd == TOKEN_INVALID and attempt == 0:
                        self.invalidate_token()
                        continue
                    if rsp_cd == RATE_LIMITED and attempt < RATE_LIMIT_RETRIES:
                        self._back_off(attempt)
                        continue
                    raise LSAPIError(
                        f"TR {tr_cd} rsp_cd={rsp_cd} msg={data.get('rsp_msg')}",
                        rsp_cd=str(rsp_cd),
                        payload=data,
                    )

                if count_health:
                    self._record_call(True)
                return data

            raise LSAPIError(f"TR {tr_cd} 재시도 소진 (토큰 재발급·한도 백오프 후에도 실패)")
        except LSAPIError:
            if count_health:
                self._record_call(False)
            raise

    def _send(
        self,
        url: str,
        tr_cd: str,
        tr_cont: str,
        tr_cont_key: str,
        token: Token,
        body: dict[str, Any],
    ) -> httpx.Response:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"{token.token_type} {token.access_token}",
            "tr_cd": tr_cd,
            "tr_cont": tr_cont,
            "tr_cont_key": tr_cont_key,
            "mac_address": "",
        }
        try:
            return self._http().post(url, headers=headers, content=json.dumps(body))
        except httpx.HTTPError as error:
            raise LSAPIError(f"TR {tr_cd} 네트워크 오류: {error}") from error

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _is_completion(data: dict[str, Any]) -> bool:
    """미발견 완료코드 안전망. port: LS_KR ls_client.py:285-290."""
    message = str(data.get("rsp_msg") or "")
    return OK_MESSAGE in message.replace(" ", "")


def isu_code(code: str) -> str:
    """주문용 IsuNo. port: LS_KR ls_client.py:323-331.

    **시장 접두어를 먼저 뗀다.** 창고의 정본은 ``KR:067290`` 이고 LS 는
    ``A067290`` 을 받는다. 안 떼면 ``AKR:067290`` 이 나간다.

    미장 ``us_symbol()`` 과 짝이다 — 그쪽은 떼기만 하고 이쪽은 떼고 붙인다.
    2026-08-18 실주문에서 이 비대칭이 드러났다: 주문은 나갔는데(도구가 순수
    코드를 넘겨서) 체결을 ``trades`` 에 적을 때 ``entity_id='067290'`` 이
    스키마 위반으로 튕겼다. 정본을 접두어 있는 쪽으로 통일하고, 접두어를
    떼는 책임을 여기로 모은다.
    """
    stripped = (code or "").strip()
    if not stripped:
        return stripped
    _, _, bare = stripped.rpartition(":")
    bare = bare or stripped
    return bare if bare.startswith("A") else f"A{bare}"
