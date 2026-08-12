"""News·SNS 필터 — 점수를 내지 않는다. 매수만 막는다.

## 왜 이 둘만 다른가

나머지 Analyst 는 IC 로 검증된다. 이 둘은 **검증할 수 없다** — 과거 뉴스·SNS
데이터를 시점 정합성 있게 확보할 수 없기 때문이다. 기사 발행시각은 사후에
수정되고, 삭제된 글은 백필에서 아예 사라진다(생존편향).

검증할 수 없는 신호에 위험한 권한을 주지 않는다. 그래서 셋을 강제한다.

1. **매수 금지만. 매도 권한 없음.** 오작동해도 기회를 놓칠 뿐 손실이 확정되지
   않는다. 비대칭을 의도적으로 만든 것이다
2. **영구 차단 불가.** ``expires_at`` 은 스키마가 강제한다. 한 번 차단된 종목이
   영원히 후보에서 빠지면 그 종목에서 났을 수익을 아무도 모른 채 사라진다
3. **하루 거부 상한.** 후보의 30%(설정값)를 넘겨 막을 수 없다. 전부 차단하면
   살 종목이 남지 않고, 그건 필터가 아니라 정지 버튼이다

## 검증은 성적표로 한다

IC 를 못 쓰므로 **차단한 종목의 이후 수익률**을 추적한다. 차단된 종목이 실제로
더 떨어졌으면 필터가 일한 것이고, 오히려 올랐으면 기회를 버린 것이다.
그 계산이 ``scorecard.py`` 다.

## 지금 상태

수집기가 없다. 과거 데이터를 검증할 수 없어 M3 실전 직전에 붙인다
(``docs/design/agents.md`` §3). 여기서는 **판정 규칙과 배관**을 완성해 두고,
데이터가 붙는 날 소스만 갈아 끼운다. 빈 판정을 내는 것과 판정 로직이 없는 것은
다르다 — 전자는 상한·만료·성적표가 이미 검증돼 있다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, ClassVar

from lattice.collectors.market_hours import Market
from lattice.replay.clock import Clock
from lattice.schemas.verdict import Category, Decision, Verdict
from lattice.store import Store

#: 필터가 참고하는 원자료. 지금은 비어 있다.
DOCUMENTS = "documents"


class VerdictAnalyst(ABC):
    """판정만 내는 Analyst. ``score`` 를 만들지 않는다."""

    name: str
    version: str

    def __init__(self, store: Store, clock: Clock, *, market: Market = Market.KR) -> None:
        self.store = store
        self.clock = clock
        self.market = market

    # -- 하위 클래스가 채우는 것 ----------------------------------------------

    @abstractmethod
    def candidates_to_block(
        self, entities: Sequence[str], as_of: datetime
    ) -> list[tuple[str, Category, float, str]]:
        """차단 후보. ``(종목, 사유, 심각도, 설명)`` 목록.

        심각도 순으로 정렬해 돌려준다 — 상한에 걸리면 위에서부터 자른다.
        """

    # -- 공통 실행 -------------------------------------------------------------

    def run(self, entities: Sequence[str], as_of: datetime) -> list[Verdict]:
        """판정. **상한과 만료를 여기서 강제한다.**

        하위 클래스가 상한을 어길 수 없게 base 가 자른다. 각 필터가 알아서
        지키게 두면 언젠가 하나가 안 지킨다.
        """
        if not entities:
            return []

        cap_ratio = float(self.store.config("analyst.block_ratio_cap", as_of=as_of))
        ttl_days = int(self.store.config("analyst.verdict_ttl_days", as_of=as_of))
        limit = int(len(entities) * cap_ratio)

        blocked = self.candidates_to_block(entities, as_of)
        # 심각도 높은 것부터. 상한에 걸리면 덜 심각한 것이 살아남는다.
        blocked = sorted(blocked, key=lambda item: -item[2])[:limit]

        expires = as_of + timedelta(days=ttl_days)
        return [
            Verdict(
                analyst=self.name,
                analyst_version=self.version,
                entity_id=entity,
                as_of=as_of,
                decision=Decision.BLOCK,
                severity=severity,
                category=category,
                reason=reason,
                expires_at=expires,
            )
            for entity, category, severity, reason in blocked
        ]

    def rows(self, verdicts: Sequence[Verdict], *, observed_at: datetime) -> list[dict[str, Any]]:
        return [verdict.row(observed_at=observed_at, source=self.name) for verdict in verdicts]


class NewsAnalyst(VerdictAnalyst):
    """공시·뉴스 기반 차단.

    **사실 기반의 구조적 악재만 막는다.** 단순 주가 하락 기사·목표가 하향·
    일반적 부정 논조는 차단하지 않는다 — 전부 차단하면 살 종목이 남지 않는다.

    막을 것: 회계부정·감사의견거절 / 횡령·배임 / 상폐 실질심사 / 거래정지 /
    대규모 소송 패소 / 리콜 / 유상증자·CB(희석) / 최대주주 매도 / 실적 쇼크.
    """

    name = "news"
    version = "news-v0.1.0"

    #: 공시 제목에서 찾을 신호. 수집기가 붙으면 이 표가 1차 스크리닝이 된다.
    #: LLM 2단계 호출(저비용 스크리닝 → 의심 건만 고성능)의 0단계에 해당한다.
    PATTERNS: ClassVar[dict[Category, tuple[str, ...]]] = {
        Category.ACCOUNTING: ("감사의견", "의견거절", "한정", "회계처리기준 위반"),
        Category.EMBEZZLEMENT: ("횡령", "배임"),
        Category.DELISTING: ("상장폐지", "실질심사", "관리종목"),
        Category.TRADING_HALT: ("거래정지", "매매거래 정지"),
        Category.LITIGATION: ("소송", "손해배상"),
        Category.DILUTION: ("유상증자", "전환사채", "신주인수권부사채"),
        Category.INSIDER_SELL: ("최대주주", "지분 매각"),
    }

    def candidates_to_block(
        self, entities: Sequence[str], as_of: datetime
    ) -> list[tuple[str, Category, float, str]]:
        documents = self.store.get(DOCUMENTS, as_of=as_of, lookback=5)
        if documents.empty:
            # 수집기가 아직 없다. 지어내지 않는다.
            return []

        wanted = set(entities)
        found: list[tuple[str, Category, float, str]] = []
        for row in documents.to_dict(orient="records"):
            entity = str(row["entity_id"])
            if entity not in wanted:
                continue
            title = str(row.get("title") or "")
            for category, needles in self.PATTERNS.items():
                hit = next((needle for needle in needles if needle in title), None)
                if hit is not None:
                    found.append((entity, category, _severity(category), f"{hit}: {title[:80]}"))
                    break
        return found


class SnsAnalyst(VerdictAnalyst):
    """SNS 펌핑 탐지.

    **긍정 신호는 쓰지 않는다.** 언급량 폭증은 대부분 이미 오른 뒤이거나
    작전이다. 유일한 용도는 펌핑 탐지 — 언급량 z-score 급등 + 신규·저품질 계정
    비율 급증 + 다채널 동시 게시 + 반박 없는 일방적 찬양.
    """

    name = "sns"
    version = "sns-v0.1.0"

    def candidates_to_block(
        self, entities: Sequence[str], as_of: datetime
    ) -> list[tuple[str, Category, float, str]]:
        # SNS 수집기는 없다. 네이버 HTML 스크래핑·X API 의존은 브로커 배관과
        # 달리 외부 변경에 취약해 필요한 소스만 선별해 재구축한다
        # (postmortem-ls.md §6-7).
        return []


def _severity(category: Category) -> float:
    """사유별 심각도. 상한에 걸릴 때 무엇을 먼저 막을지 정한다."""
    return {
        Category.ACCOUNTING: 1.0,
        Category.EMBEZZLEMENT: 1.0,
        Category.DELISTING: 1.0,
        Category.TRADING_HALT: 0.9,
        Category.LITIGATION: 0.6,
        Category.RECALL: 0.6,
        Category.DILUTION: 0.5,
        Category.INSIDER_SELL: 0.5,
        Category.EARNINGS_SHOCK: 0.4,
        Category.PUMP: 0.7,
    }.get(category, 0.5)
