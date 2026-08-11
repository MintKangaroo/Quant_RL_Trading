"""LS증권 Open API 클라이언트.

port(collectors): LS_KR broker/ls_client.py 이식 (인증·토큰갱신·TR호출·레이트리밋).
학습·상태설계·보상 관련 코드는 가져오지 않았다 — CLAUDE.md 참고 규칙.

이식하면서 바꾼 것:

1. ``time.time()`` → Clock 주입 (불변식 2). 원본은 레이트리밋과 토큰 만료를
   모두 벽시계로 쟀다. Lattice 에서는 시간의 출처가 하나뿐이다.
2. ``requests`` → ``httpx``. 테스트에서 transport 를 갈아끼워 네트워크 없이 돈다.
3. 성공 판정은 **KR 판을 기준으로 삼는다.** US 판의 ``rsp_cd.startswith("0")`` 은
   지나치게 포괄적이라 실패를 성공으로 읽는다 (postmortem-ls.md §6-2).

가장 값진 이식물은 코드가 아니라 **예외코드 지식**이다. 아래 성공코드 셋은
문서가 아니라 실거래 사고로 하나씩 발견된 것이다. 이 리스트를 잃으면 같은
사고를 다시 겪는다.
"""

from __future__ import annotations

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

from lattice.collectors.errors import LSAPIError, MissingCredentials
from lattice.replay.clock import Clock, LiveClock

PATH_OAUTH = "/oauth2/token"
PATH_MARKET = "/stock/market-data"
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
#: 무효화한다. Lattice 는 KR/US 별도 appkey 를 쓰는 것을 전제로 하지만,
#: 복구 경로는 남겨 둔다 (postmortem-ls.md §6-1).
TOKEN_INVALID = "IGW00121"

#: paper 모드에서도 허용되는 read-only TR. 잔고(t0424)·주문(CSPAT*)은 절대 불허.
#: port: LS_KR ls_client.py:58-66.
PAPER_ALLOWED_TR = frozenset(
    {
        "t8436",  # 종목 마스터
        "t1102",  # 현재가
        "t8407",  # 복수종목 현재가
        "t8410",  # 일봉
        "t1511",  # 업종 지수
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

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LSCredentials:
        source = env if env is not None else dict(os.environ)
        return cls(
            appkey=source.get("LS_APPKEY", "").strip(),
            appsecret=source.get("LS_APPSECRET", "").strip(),
            base_url=source.get("LS_REST_BASE_URL", "").rstrip("/"),
        )

    def usable(self) -> bool:
        """키가 실제로 채워졌는지. 템플릿 값(``YOUR_``)은 없는 것으로 본다."""
        return bool(self.appkey and self.appsecret) and not self.appkey.startswith("YOUR_")


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
            for attempt in range(2):
                self._respect_rate_limit()
                token = self.ensure_token(allow_paper=allow_paper)
                response = self._send(url, tr_cd, tr_cont, tr_cont_key, token, body)

                # 토큰 무효는 HTTP 500 + 본문으로 오기도, rsp_cd 로 오기도 한다.
                if TOKEN_INVALID in (response.text or "") and attempt == 0:
                    self.invalidate_token()
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
                    raise LSAPIError(
                        f"TR {tr_cd} rsp_cd={rsp_cd} msg={data.get('rsp_msg')}",
                        rsp_cd=str(rsp_cd),
                        payload=data,
                    )

                if count_health:
                    self._record_call(True)
                return data

            raise LSAPIError(f"TR {tr_cd} 토큰 재발급 후에도 실패 ({TOKEN_INVALID})")
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
    """주문용 IsuNo. port: LS_KR ls_client.py:323-331."""
    stripped = (code or "").strip()
    if not stripped:
        return stripped
    return stripped if stripped.startswith("A") else f"A{stripped}"
