"""Gmail SMTP 발송.

port: LS_KR ``ls_kr_rl_trader/reports/emailer.py`` (SMTP 연결·앱 비밀번호 로딩·
실패 로깅). 이식하면서 ``docs/design/reporting.md`` §2 대로 고친 것:

1. **자격증명은 ``.env`` 에서만 읽는다.** 원본은 ``settings.env`` 를 거쳤고
   기본 서버 이름이 코드에 있었다. 여기서는 네 값 모두 환경변수뿐이고,
   기본값도 두지 않는다 — 기본값이 있으면 오타 난 설정이 조용히 통과한다
2. **재시도가 있다.** 원본은 한 번 실패하면 끝이었다. Gmail SMTP 는 일시적
   거절이 흔하다
3. **발송 한도.** 하루 1통이 원칙이라 종목별·이벤트별 호출부를 만들지 않는다.
   이 모듈에는 "한 번에 한 통" 함수 하나만 있다

## 실패는 여기서 끝난다 (reporting.md §2)

리포트는 비필수 경로다. SMTP 실패가 Collector·Selector·Executor 로
전파되면 메일 서버 장애가 매매를 멈춘다. 그래서 이 모듈의 공개 함수는
**예외를 던지지 않는다** — 결과를 ``SendResult`` 로 돌려준다. 호출부가
except 를 잊어도 격리가 유지되는 유일한 모양이다.

## 값을 로그에 찍지 않는다

앱 비밀번호는 물론이고 수신자 주소도 마스킹해서 남긴다. 로그는 사람이
붙여넣기 쉬운 곳이다.

## 앱 비밀번호 재발급

Gmail 앱 비밀번호는 2단계 인증이 켜진 계정에서만 발급된다. 만료·회수되면
myaccount.google.com/apppasswords 에서 새로 만들어 ``.env`` 의
``SMTP_SENDER_PASSWORD`` 를 갈아 끼운다. 코드 변경은 필요 없다.
"""

from __future__ import annotations

import logging
import os
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)

#: 필요한 환경변수. **기본값을 두지 않는다** — 없으면 없다고 말한다.
REQUIRED = ("SMTP_SERVER", "SMTP_SENDER_EMAIL", "SMTP_SENDER_PASSWORD", "RECIPIENT_EMAIL")

#: 포트만은 기본값을 둔다. STARTTLS 587 은 Gmail 이 바꿀 수 없는 상수에 가깝고,
#: 이걸 필수로 만들면 기존 ``.env`` 가 전부 깨진다.
DEFAULT_PORT = 587

ATTEMPTS = 3
BACKOFF_SECONDS = 5.0


class Transport(Protocol):
    """SMTP 한 통을 보내는 것. 그것만 한다.

    테스트가 진짜 메일을 보내지 않게 하는 이음매다. 목으로 막는 지점이
    ``smtplib`` 안쪽이 아니라 여기라서, 테스트가 표준 라이브러리 내부 구조를
    알 필요가 없다.
    """

    def send(self, message: EmailMessage, config: SmtpConfig) -> None: ...


@dataclass(frozen=True)
class SmtpConfig:
    server: str
    port: int
    sender: str
    password: str
    recipient: str

    def __repr__(self) -> str:
        # 기본 repr 은 비밀번호를 그대로 뱉는다. 예외 트레이스백 한 줄이
        # 자격증명 유출 경로가 되지 않게 막는다.
        return f"SmtpConfig(server={self.server!r}, sender={mask(self.sender)!r})"


@dataclass(frozen=True)
class SendResult:
    ok: bool
    #: ``"sent"`` | ``"skipped"`` | ``"failed"``
    status: str
    detail: str
    attempts: int = 0


def mask(address: str) -> str:
    """``yjun273@gmail.com`` → ``y****3@gmail.com``. 로그에 남길 형태."""
    name, _, domain = address.partition("@")
    if not domain:
        return "***"
    if len(name) <= 2:
        return f"{name[:1]}***@{domain}"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}@{domain}"


def load_config(env: dict[str, str] | None = None) -> SmtpConfig | None:
    """``.env`` 에서 SMTP 설정을 읽는다. **하나라도 없으면 ``None``.**

    없는 것을 예외로 만들지 않는다 — 자격증명이 없는 환경(CI·개발기)에서
    리포트를 만들어 파일로 떨구는 것은 정상적인 사용이다. 발송만 못 할 뿐이다.
    """
    source = env if env is not None else dict(os.environ)
    missing = [key for key in REQUIRED if not (source.get(key) or "").strip()]
    if missing:
        logger.info("SMTP 미설정 — %s 가 .env 에 없다. 발송을 건너뛴다", ", ".join(missing))
        return None
    return SmtpConfig(
        server=source["SMTP_SERVER"].strip(),
        port=int(source.get("SMTP_PORT") or DEFAULT_PORT),
        sender=source["SMTP_SENDER_EMAIL"].strip(),
        password=source["SMTP_SENDER_PASSWORD"].strip(),
        recipient=source["RECIPIENT_EMAIL"].strip(),
    )


def build_message(
    *, subject: str, html: str, text: str, config: SmtpConfig
) -> EmailMessage:
    """텍스트 대체본을 함께 실은 한 통.

    HTML 만 실으면 텍스트만 받는 클라이언트에서 빈 메일이 된다. 순서가
    중요하다 — ``set_content`` 로 텍스트를 먼저 깔고 HTML 을 대체본으로 얹는다.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.recipient
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


class SmtpTransport:
    """진짜 Gmail. 운영에서만 쓴다."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def send(self, message: EmailMessage, config: SmtpConfig) -> None:
        with smtplib.SMTP(config.server, config.port, timeout=self.timeout) as smtp:
            smtp.starttls()
            smtp.login(config.sender, config.password)
            smtp.send_message(message)


def send(
    *,
    subject: str,
    html: str,
    text: str,
    config: SmtpConfig | None = None,
    transport: Transport | None = None,
    attempts: int = ATTEMPTS,
    sleep: object = time.sleep,
) -> SendResult:
    """한 통 보낸다. **예외를 던지지 않는다.**

    설정이 없으면 ``skipped``, 다 실패하면 ``failed`` 를 돌려준다. 호출부는
    결과를 로그에 남기기만 하면 되고, 그 실패가 매매 경로로 새지 않는다.
    """
    resolved = config or load_config()
    if resolved is None:
        return SendResult(ok=False, status="skipped", detail="SMTP 미설정 (.env)")

    carrier = transport or SmtpTransport()
    message = build_message(subject=subject, html=html, text=text, config=resolved)
    last = ""
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            carrier.send(message, resolved)
        except Exception as error:
            last = f"{type(error).__name__}: {error}"
            logger.warning("메일 발송 실패 (%d/%d): %s", attempt, attempts, last)
            if attempt < attempts:
                sleep(BACKOFF_SECONDS * attempt)  # type: ignore[operator]
            continue
        logger.info(
            "메일 발송 완료 → %s (%d회차)", mask(resolved.recipient), attempt
        )
        return SendResult(ok=True, status="sent", detail=mask(resolved.recipient), attempts=attempt)
    return SendResult(ok=False, status="failed", detail=last, attempts=attempts)
