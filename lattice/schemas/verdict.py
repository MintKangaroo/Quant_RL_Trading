"""Verdict — News·SNS Analyst 가 내는 판정.

이 둘은 **점수를 내지 않는다.** 매수 금지만 할 수 있고 매도 권한은 없다
(CLAUDE.md 금지 사항). 오작동해도 기회를 놓칠 뿐 손실이 확정되지 않는다 —
비대칭을 의도적으로 만든 것이다.

과거 뉴스·SNS 데이터를 확보할 수 없으므로 이 판정은 **RL 상태값에 들어가지
않고 IC 로 검증되지도 않는다.** 검증할 수 없는 신호에 위험한 권한을 주지
않는다는 원칙의 구체적 형태다.

대신 **성적표를 만든다.** 차단한 종목의 이후 수익률을 추적해서, 이 필터가
실제로 손실을 피하게 해 줬는지 사후에 따진다 (``scorecard.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Decision(StrEnum):
    #: 매수 금지. 보유분 매도는 이 판정으로 일어나지 않는다.
    BLOCK = "block"
    PASS = "pass"


class Category(StrEnum):
    """차단 사유. **사실 기반의 구조적 악재만** (agents.md §3).

    단순 주가 하락·목표가 하향·일반적 부정 논조는 차단하지 않는다. 전부
    차단하면 살 종목이 남지 않는다.
    """

    ACCOUNTING = "accounting"        # 회계부정·감사의견거절
    EMBEZZLEMENT = "embezzlement"    # 횡령·배임
    DELISTING = "delisting"          # 상폐 실질심사
    TRADING_HALT = "trading_halt"    # 거래정지
    LITIGATION = "litigation"        # 대규모 소송 패소
    RECALL = "recall"                # 리콜
    DILUTION = "dilution"            # 유상증자·CB 발행
    INSIDER_SELL = "insider_sell"    # 최대주주 매도
    EARNINGS_SHOCK = "earnings_shock"
    PUMP = "pump"                    # SNS 펌핑 탐지


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    analyst: str
    analyst_version: str
    entity_id: str
    as_of: datetime
    decision: Decision
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    category: Category | None = None
    reason: str = ""
    #: **필수다. 영구 차단은 존재할 수 없다** (agents.md §3).
    #: 한 번 차단된 종목이 영원히 후보에서 빠지면, 그 종목에서 났을 수익은
    #: 아무도 모르는 채로 사라지고 성적표에도 안 잡힌다.
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> Verdict:
        for name, moment in (("as_of", self.as_of), ("expires_at", self.expires_at)):
            if moment is not None and (
                moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None
            ):
                raise ValueError(f"{name} 에 타임존이 없다: {moment!r}")

        if self.decision is Decision.BLOCK:
            if self.expires_at is None:
                raise ValueError("차단에는 expires_at 이 필수다. 영구 차단 금지")
            if self.expires_at <= self.as_of:
                raise ValueError("expires_at 은 as_of 보다 뒤여야 한다")
            if self.category is None:
                raise ValueError("차단에는 category 가 필요하다. 사유 없는 차단 금지")
        return self

    def active_at(self, moment: datetime) -> bool:
        return (
            self.decision is Decision.BLOCK
            and self.expires_at is not None
            and self.as_of <= moment < self.expires_at
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "analyst": self.analyst,
            "analyst_version": self.analyst_version,
            "entity_id": self.entity_id,
            "as_of": self.as_of.isoformat(),
            "decision": str(self.decision),
            "severity": round(self.severity, 6),
            "category": str(self.category) if self.category else None,
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def row(self, *, observed_at: datetime, source: str) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "valid_from": self.as_of,
            "observed_at": observed_at,
            "source": source,
            "analyst": self.analyst,
            "analyst_version": self.analyst_version,
            "decision": str(self.decision),
            "severity": self.severity,
            "category": str(self.category) if self.category else None,
            "reason": self.reason,
            "expires_at": self.expires_at,
        }


def default_expiry(as_of: datetime, trading_days: int = 3) -> datetime:
    """기본 만료. 달력일이 아니라 거래일 기준이 옳지만, 보수적으로 넉넉히 잡는다."""
    return as_of + timedelta(days=trading_days * 2)
