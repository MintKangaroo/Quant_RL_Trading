"""수집 계층 예외."""

from __future__ import annotations

from typing import Any


class CollectorError(Exception):
    """수집 계층의 모든 예외의 뿌리."""


class LSAPIError(CollectorError):
    """LS API 오류.

    port(collectors): LS_KR broker/ls_client.py:77-84 이식.
    ``rsp_cd`` 를 들고 다녀야 호출자가 "토큰 무효" 와 "잔고 부족" 을 구분한다.
    """

    def __init__(self, message: str, rsp_cd: str | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.rsp_cd = rsp_cd
        self.payload = payload


class MissingCredentials(CollectorError):
    """API 키 미설정. paper 모드에서도 read-only 호출에는 키가 필요하다."""
