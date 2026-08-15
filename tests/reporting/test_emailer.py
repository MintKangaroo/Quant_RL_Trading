"""Gmail 발송 (port: LS_KR ``reports/emailer.py``).

**진짜 메일은 한 통도 나가지 않는다.** ``Transport`` 를 목으로 갈아 끼운다 —
목을 ``smtplib`` 안쪽이 아니라 이 이음매에 두는 이유는, 테스트가 표준
라이브러리 내부 구조를 알면 그 구조가 바뀔 때 테스트가 먼저 깨지기 때문이다.

여기서 고정하는 것:

1. **자격증명이 없으면 조용히 건너뛴다** — 예외가 아니다. CI·개발기에서
   리포트를 파일로 떨구는 것은 정상적인 사용이다
2. **실패가 새어 나가지 않는다** — 리포트는 비필수 경로다 (reporting.md §2).
   SMTP 가 죽어도 예외가 매매 경로로 전파되면 안 된다
3. **자격증명이 로그·repr 에 안 찍힌다**
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from quant_rl_trading.reporting import emailer

ENV = {
    "SMTP_SERVER": "smtp.example.com",
    "SMTP_SENDER_EMAIL": "sender@example.com",
    "SMTP_SENDER_PASSWORD": "app-password-1234",
    "RECIPIENT_EMAIL": "reader@example.com",
}


class MockTransport:
    """보낸 척만 한다. 실패를 지시하면 그 횟수만큼 실패한다."""

    def __init__(self, fail_times: int = 0, error: type[Exception] = RuntimeError) -> None:
        self.sent: list[EmailMessage] = []
        self.fail_times = fail_times
        self.error = error
        self.calls = 0

    def send(self, message: EmailMessage, config: emailer.SmtpConfig) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error("SMTP 가 거절했다")
        self.sent.append(message)


def _send(transport: MockTransport, **kwargs: object) -> emailer.SendResult:
    return emailer.send(
        subject="[시황] 2026-08-15",
        html="<p>본문</p>",
        text="본문",
        config=emailer.load_config(ENV),
        transport=transport,
        sleep=lambda _: None,
        **kwargs,  # type: ignore[arg-type]
    )


# -- 설정 ----------------------------------------------------------------------


def test_missing_credentials_skip_instead_of_raising() -> None:
    """``.env`` 가 없어도 안전하게 실패한다 (R-5 6번)."""
    assert emailer.load_config({}) is None
    result = emailer.send(subject="s", html="h", text="t", config=None, transport=MockTransport())
    assert result.status == "skipped"
    assert result.ok is False


def test_partial_credentials_also_skip() -> None:
    partial = dict(ENV)
    del partial["SMTP_SENDER_PASSWORD"]
    assert emailer.load_config(partial) is None


def test_no_default_server_name() -> None:
    """서버 이름에 기본값을 두지 않는다 — 기본값이 있으면 오타 난 설정이
    조용히 통과하고, 메일이 엉뚱한 곳으로 간다."""
    blank = dict(ENV, SMTP_SERVER="")
    assert emailer.load_config(blank) is None


# -- 발송 ----------------------------------------------------------------------


def test_sends_one_message_with_both_bodies() -> None:
    """HTML 만 실으면 텍스트 클라이언트에서 빈 메일이 된다."""
    transport = MockTransport()
    result = _send(transport)

    assert result.ok and result.status == "sent"
    assert len(transport.sent) == 1, "하루 1통 — 한 번 호출에 한 통이다"
    message = transport.sent[0]
    assert message["Subject"] == "[시황] 2026-08-15"
    assert message["To"] == "reader@example.com"
    types = {part.get_content_type() for part in message.walk()}
    assert "text/plain" in types
    assert "text/html" in types


def test_retries_then_succeeds() -> None:
    """Gmail SMTP 는 일시적 거절이 흔하다. 원본(LS_KR)은 한 번 실패로 끝났다."""
    transport = MockTransport(fail_times=2)
    result = _send(transport)
    assert result.ok
    assert result.attempts == 3
    assert len(transport.sent) == 1


def test_failure_is_returned_not_raised() -> None:
    """**실패 격리** (reporting.md §2). SMTP 가 죽어도 예외가 밖으로 안 나간다.

    호출부가 except 를 잊어도 격리가 유지되어야 한다 — 메일 서버 장애가
    Collector·Selector·Executor 를 멈추면 안 되기 때문이다.
    """
    transport = MockTransport(fail_times=99)
    result = _send(transport)  # raise 하지 않는다
    assert result.ok is False
    assert result.status == "failed"
    assert transport.calls == emailer.ATTEMPTS
    assert transport.sent == []


def test_send_never_raises_even_on_odd_errors() -> None:
    transport = MockTransport(fail_times=99, error=OSError)
    assert _send(transport).status == "failed"


# -- 자격증명 유출 ---------------------------------------------------------------


def test_config_repr_hides_the_password() -> None:
    """예외 트레이스백 한 줄이 자격증명 유출 경로가 되지 않게 한다."""
    config = emailer.load_config(ENV)
    assert config is not None
    text = repr(config)
    assert "app-password-1234" not in text
    assert "reader@example.com" not in text


def test_result_masks_the_recipient() -> None:
    result = _send(MockTransport())
    assert result.detail == "r****r@example.com"


def test_failure_log_carries_no_password(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        _send(MockTransport(fail_times=99))
    assert "app-password-1234" not in caplog.text


def test_mask() -> None:
    assert emailer.mask("ab@x.com") == "a***@x.com"
    assert emailer.mask("someone@x.com") == "s*****e@x.com"
    assert emailer.mask("broken") == "***"
