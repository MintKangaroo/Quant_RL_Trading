"""실브로커를 만들어야 하는지 판정하고, 만든다. **무인 경로의 유일한 관문.**

    broker, reason = build_broker(store, market="KR", as_of=now)

``executor/pipeline.run`` 은 ``broker`` 를 안 주면 ``PaperBroker`` 를 쓴다.
그래서 지금까지 크론 경로(``run_session.py``)는 주문을 만들고 창고에 적기만
했고, **나가는 경로가 존재하지 않았다.** 이 모듈이 그 경로를 만든다.

## 왜 예외가 아니라 (broker, reason) 인가

무인 실행이다. 조건이 안 맞을 때 예외를 던지면 세션이 통째로 죽고, 그러면
**주문을 안 낸 것이 아니라 회계·기록까지 안 남는다.** 그건 안전이 아니라
관측 불가다. 그래서 조건이 안 맞으면 ``PaperBroker`` 와 **왜 그랬는지**를
돌려주고, 세션은 평소대로 끝까지 돈다. 호출부가 그 이유를 로그에 남긴다.

## 통과해야 하는 네 관문

1. **``execution.live_trading``** — 비즈니스 결정. 꺼져 있으면 끝
2. **계좌 지문 일치** — 코드는 모의·실전을 판별할 수 없다(모의 appkey 로도
   ``t0424`` 가 정상 응답한다, 2026-08-15 실측). 사람이 ``.env`` 에 선언하고
   그 선언을 설정의 지문에 묶는다. ``.env`` 가 바뀌면 지문이 달라져 여기서
   막힌다 — "모의인 줄 알았다" 를 막는 유일한 방법이다
3. **자격증명 존재** — 키가 없으면 만들 이유가 없다
4. **미장은 지문 미선언도 거부** — 국장은 기존 규약(빈 값 = 고정 안 함)을
   그대로 두지만(2026-08-18 검증이 그 규약 위에 있다), 미장은 새 경로라
   처음부터 조인다. ``tools/verify_live_order.py`` 의 ``allow_unpinned`` 와
   같은 규칙이다

## 신용·미수는 여기서 막지 않는다 — 이미 아래에서 막혀 있다

- **신용**: ``broker/ls_order.py`` 의 ``MGNTRN_NONE = "000"`` 이 주문 본문에
  상수로 박혀 있다. 설정이 아니라 상수라 실수로 켤 수 없다
- **미수**: ``accounting.ledger.available_cash`` 가 미결제 매도대금을 빼고
  주문가능금액을 준다. 그것이 ``executor/sizing.py`` 의 예산이다

여기서 한 번 더 검사하면 두 벌이 되고, 두 벌은 반드시 한쪽만 조여진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.broker import Broker, PaperBroker

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

#: 전송 여부를 가르는 비즈니스 설정.
LIVE_TRADING_KEY = "execution.live_trading"

#: 계좌 지문 고정 키. **``tools/verify_live_order.py`` 가 이 상수를 쓴다** —
#: 문자열을 양쪽에 따로 적으면 한쪽만 조여진다.
FINGERPRINT_KEY_KR = "execution.live_account_fingerprint"
FINGERPRINT_KEY_KR_PAPER = "execution.live_account_fingerprint_paper"
FINGERPRINT_KEY_US = "execution.live_account_fingerprint_us"

#: 어느 계좌로 나갈지. ``paper`` | ``real``.
#:
#: **기본값은 ``paper`` 다.** 설정이 없거나 못 읽으면 모의로 간다 — 모르는
#: 상태에서 실전으로 흐르는 경로를 만들지 않는다. `live_trading` 이 꺼져
#: 있으면 어차피 아무것도 안 나가지만, 두 관문의 기본값이 **둘 다 안전한
#: 쪽**이어야 한 관문이 실수로 열려도 사고가 안 난다.
ACCOUNT_MODE_KEY = "execution.account_mode"
MODE_PAPER = "paper"
MODE_REAL = "real"


@dataclass(frozen=True)
class LiveProfile:
    """(시장, 계좌모드)별 전송 배선 규칙."""

    env_prefix: str
    fingerprint_key: str
    #: 지문이 **비어 있어도** 진행할지. 국장 실전은 기존 규약(빈 값 = 고정
    #: 안 함)을 그대로 둔다 — 2026-08-18 검증이 그 규약 위에 있다. 미장과
    #: 모의 경로는 새로 만든 것이라 처음부터 조인다.
    allow_unpinned: bool
    #: ``.env`` 의 ``*_ACCOUNT_KIND`` 가 이 값이어야 한다.
    #:
    #: **코드는 모의·실전을 판별할 수 없다** — 같은 호스트를 쓰고, 모의
    #: appkey 로도 ``t0424`` 가 정상 응답한다 (``LSCredentials.declared_kind``
    #: 실측 기록). 그래서 사람의 선언과 설정의 모드가 **어긋나면 멈춘다.**
    #: 지문 고정만으로도 막히지만, 지문은 "이 키가 맞나" 를 묻고 이 검사는
    #: "이 키가 어느 쪽이라고 선언됐나" 를 묻는다. 둘은 다른 질문이다.
    expected_kind: str


PROFILES: dict[tuple[str, str], LiveProfile] = {
    ("KR", MODE_REAL): LiveProfile(
        env_prefix="LS_",
        fingerprint_key=FINGERPRINT_KEY_KR,
        allow_unpinned=True,
        expected_kind=MODE_REAL,
    ),
    ("KR", MODE_PAPER): LiveProfile(
        env_prefix="LS_PAPER_",
        fingerprint_key=FINGERPRINT_KEY_KR_PAPER,
        allow_unpinned=False,
        expected_kind=MODE_PAPER,
    ),
    ("US", MODE_REAL): LiveProfile(
        env_prefix="LS_US_",
        fingerprint_key=FINGERPRINT_KEY_US,
        allow_unpinned=False,
        expected_kind=MODE_REAL,
    ),
}


def build_broker(
    store: Store,
    *,
    market: str,
    as_of: datetime,
) -> tuple[Broker, str]:
    """(브로커, 이유). 조건이 안 맞으면 ``PaperBroker`` 와 이유를 돌려준다.

    **이유는 언제나 채워진다** — 실전일 때도 "왜 실전인지" 를 남긴다. 로그에
    "실전 전송" 이 안 보이면 안 나간 것이고, 그 판정을 사람이 로그 부재로
    추론하게 두지 않는다.
    """
    # **모드를 먼저 정한다.** 어느 계좌로 갈지가 정해져야 어느 키를 볼지도
    # 정해진다. 못 읽으면 모의다 — 모르는 상태에서 실전으로 흐르지 않는다.
    try:
        raw_mode = store.config(ACCOUNT_MODE_KEY, as_of=as_of)
        mode = str(raw_mode or MODE_PAPER).strip().lower()
    except Exception:  # ConfigNotFound 포함 — 없으면 모의로 본다
        mode = MODE_PAPER
    if mode not in (MODE_PAPER, MODE_REAL):
        return PaperBroker(), (
            f"{ACCOUNT_MODE_KEY} 값을 모르겠다({mode!r}) — paper|real 만 안다. 보내지 않는다"
        )

    profile = PROFILES.get((market.upper(), mode))
    if profile is None:
        return PaperBroker(), f"{market} · {mode} 는 전송 배선이 없다"

    try:
        enabled = bool(store.config(LIVE_TRADING_KEY, as_of=as_of))
    except Exception as error:  # ConfigNotFound 포함 — 없으면 끈 것으로 본다
        return PaperBroker(), f"{LIVE_TRADING_KEY} 를 읽지 못했다({error}) — 보내지 않는다"
    if not enabled:
        return PaperBroker(), f"{LIVE_TRADING_KEY} 꺼짐 — 주문을 만들되 보내지 않는다"

    # 자격증명. 여기서 처음 .env 를 본다 — 위 관문을 통과하기 전에는 키를
    # 만질 이유가 없다.
    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials

    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    if not credentials.usable():
        return PaperBroker(), (
            f"{profile.env_prefix}* 자격증명이 없다 — 보내지 않는다"
        )

    # **선언과 모드가 맞는지 본다.** 지문 고정과 다른 질문이다 — 지문은
    # "이 키가 맞나", 이쪽은 "이 키가 어느 쪽이라고 선언됐나" 를 묻는다.
    # 코드는 모의·실전을 판별할 수 없으므로(같은 호스트·모의 키로도 t0424
    # 응답) 사람의 선언이 유일한 정보이고, 그 선언이 설정과 어긋나면 둘 중
    # 하나가 틀린 것이다. 어느 쪽이 틀렸는지 모르므로 멈춘다.
    declared = credentials.declared_kind
    if declared != profile.expected_kind:
        return PaperBroker(), (
            f"{profile.env_prefix}ACCOUNT_KIND 가 "
            f"{declared or '미선언'!r} 인데 {ACCOUNT_MODE_KEY} 는 {mode!r} 다 — "
            "선언과 모드가 어긋난다. 보내지 않는다"
        )

    fingerprint = credentials.fingerprint
    pinned = str(store.config(profile.fingerprint_key, as_of=as_of) or "")
    if not pinned:
        if not profile.allow_unpinned:
            return PaperBroker(), (
                f"{profile.fingerprint_key} 가 비어 있다 — 어느 계좌에 주문할지 "
                f"선언되지 않았다. 지금 키의 지문은 {fingerprint} 다"
            )
    elif pinned != fingerprint:
        return PaperBroker(), (
            f"계좌 지문 불일치 — 고정된 계좌는 {pinned} 인데 지금 키는 "
            f"{fingerprint} 다. .env 가 바뀌었는지 확인할 것"
        )

    # 전송 계층의 물리적 게이트. store 설정과 **별개**다 — 배선 실수로
    # 하나만 켜져도 나가지 않게 둘 다 켜야 한다 (broker/ls_order.py 문서).
    client = LSClient(credentials=credentials, live_trading=True)

    if market.upper() == "US":
        # **미장 무인 전송은 아직 못 연다.** ``LSUSBroker`` 는 인스턴스당
        # ``market_code`` 하나를 들고 있는데(``OrdMktCode`` 81=NYSE · 82=NASDAQ),
        # 그 값은 종목마다 다르다 — 2026-08-16 실측으로 SNAP 은 81, WEN 은 82 다.
        # ``verify_live_order.py`` 는 종목 하나를 시세 조회로 확인해서 넣기 때문에
        # 맞지만, 24종목 포트폴리오를 한 인스턴스로 내면 **절반이 틀린 시장코드로
        # 나간다.** 거부되면 그나마 다행이고, 받아들여지면 무엇이 어디로 갔는지
        # 모른다.
        #
        # 종목→시장코드 해석을 브로커 안으로 넣는 것이 옳은 수정이고, 그건
        # 8/17 미장 1주 검증에서 이 경로를 직접 본 뒤에 한다.
        return PaperBroker(), (
            "미장 무인 전송은 아직 열지 않았다 — LSUSBroker 가 종목별 "
            "OrdMktCode(81 NYSE / 82 NASDAQ)를 해석하지 못한다. 국장만 나간다"
        )

    from quant_rl_trading.broker.ls_order import LSBroker

    label = "모의투자 전송" if mode == MODE_PAPER else "실전 전송"
    return LSBroker(client=client, store=store), (
        f"{label} — 국장 계좌 {fingerprint} (모드 {mode})"
        + ("" if pinned else " · 지문 미고정")
    )
