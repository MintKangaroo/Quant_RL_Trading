"""뉴스 2단계 판정 — 키워드가 걸러낸 것을 Claude 가 확인한다.

## 왜 2단계가 필요한가

키워드 1단계만으로는 못 쓴다. 실측한 오탐이다 (2026-08-13, 후보 3종목):

    "SK하이닉스, 日키오시아 최대주주 등극"   → 최대주주가 **된** 호재인데
                                              INSIDER_SELL 로 잡힘
    "충칭공장 지분 매각 검토"                → 공장 지분(자산) 매각이지
                                              최대주주 매도가 아님
    삼성전자로 검색한 기사에 SK하이닉스 악재  → 종목 귀속이 틀림

넷 중 넷이 오탐이었다. 방향(호재/악재)·주체(회사가 한 일인지 당한 일인지)·
귀속(그 종목 이야기가 맞는지)은 문자열 매칭으로 판별할 수 없다.

## 무엇을 맡기고 무엇을 안 맡기는가

Claude 는 **1단계가 이미 걸러낸 것을 기각하는 일만** 한다. 새로 차단 대상을
찾지 않는다. 그래서 LLM 이 오작동해도 최악이 "덜 막는다" 이고, 이 필터의
비대칭(매수 금지만, 매도 권한 없음)과 방향이 같다.

**불변식 8을 지킨다** — 이 출력은 보상 함수에 들어가지 않는다. 매수 후보를
거를 뿐이고, 점수도 IC 도 만들지 않는다. Claude 는 심판이 아니라 해설자다.

## 실패하면 1단계 결과를 그대로 쓴다

API 가 죽거나 키가 없으면 키워드 판정이 살아남는다. 막는 쪽이 기본값인 이유는
News 필터의 오작동 비용이 비대칭이기 때문이다 — 잘못 막으면 기회를 놓치고,
잘못 통과시키면 손실이 확정된다.

## 캐시

같은 (기사, 종목, 사유) 는 두 번 묻지 않는다. ``agent_cache`` 의 ``observed_at``
은 계산한 벽시계가 아니라 ``as_of`` 다 — 출력이 as_of 이전 데이터만의 함수라
그 시점에 알 수 있었던 것이 맞고, 벽시계를 찍으면 과거 리플레이에서 영영
안 보인다 (tables.py).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lattice.replay.clock import Clock
from lattice.schemas.verdict import Category

AGENT_CACHE = "agent_cache"
AGENT = "news-screen"
VERSION = "news-screen-v0.1.0"

KEY_ENV = "ANTHROPIC_API_KEY"

#: 판정 모델. 기각은 짧은 판단이라 최상위 모델이 필요 없지만, 오탐을 남기는
#: 비용이 호출 비용보다 크므로 아끼지 않는다.
MODEL = "claude-opus-5"

#: 한 번에 묻는 최대 건수. 1단계 히트는 후보 수 × 기사 수라 수십 건이 한계다.
MAX_ITEMS = 40

SYSTEM = """\
당신은 한국·미국 주식의 뉴스 헤드라인을 검토한다.

키워드 필터가 "구조적 악재"로 의심해 걸러낸 건들을 받는다. 당신의 일은
**그 의심이 맞는지 확인하고, 틀린 것을 기각하는 것**이다. 새로운 악재를
찾지 않는다.

각 건에 대해 셋을 판단한다.

1. 귀속 — 이 기사가 그 종목에 대한 것인가. 다른 회사 이야기에 종목명이
   스쳐 지나가는 것이면 기각한다.
2. 방향 — 그 종목에게 악재인가. 같은 단어라도 반대다:
   "A가 B의 최대주주가 됐다"는 A에게 악재가 아니다.
   "공장 지분 매각"은 자산 매각이지 최대주주의 지분 처분이 아니다.
3. 사유 — 붙은 분류가 맞는가. 방향과 귀속이 맞아도 분류가 틀리면 기각한다.

**셋 다 명확히 맞을 때만 유지한다.** 애매하면 기각한다 — 이 필터는 매수를
막을 뿐이라, 잘못 기각하면 기회를 얻고 잘못 유지하면 기회를 잃는다.

제목만으로 판단할 수 없으면 기각한다. 추측하지 않는다.
"""

TOOL = {
    "name": "record_screen",
    "description": (
        "각 건의 유지/기각 판정을 기록한다. 입력으로 받은 모든 id 에 대해 "
        "정확히 하나씩 판정을 낸다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "입력으로 받은 건의 id"},
                        "keep": {
                            "type": "boolean",
                            "description": "true = 차단 유지, false = 기각(오탐)",
                        },
                        "reason": {
                            "type": "string",
                            "description": "판단 근거 한 문장. 기각이면 왜 오탐인지.",
                        },
                    },
                    "required": ["id", "keep", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass(frozen=True)
class Candidate:
    """1단계가 걸러낸 건 하나."""

    entity_id: str
    category: Category
    severity: float
    reason: str
    title: str

    @property
    def fingerprint(self) -> str:
        """캐시 키. 같은 (종목, 사유, 제목) 은 같은 판정이다."""
        raw = f"{self.entity_id}|{self.category}|{self.title}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class NewsScreen:
    """2단계 판정기. 키가 없으면 아무것도 기각하지 않는다."""

    store: Any
    clock: Clock
    api_key: str = ""
    model: str = MODEL
    client: Any = None
    #: 이번 호출에서 기각한 건들. 성적표가 이 값을 본다.
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)

    @classmethod
    def from_env(cls, store: Any, clock: Clock, env: dict[str, str] | None = None) -> NewsScreen:
        source = env if env is not None else dict(os.environ)
        return cls(store=store, clock=clock, api_key=(source.get(KEY_ENV) or "").strip())

    def usable(self) -> bool:
        return bool(self.api_key) or self.client is not None

    # -- 판정 -------------------------------------------------------------------

    def screen(self, candidates: list[Candidate], *, as_of: datetime) -> list[Candidate]:
        """오탐을 뺀 목록. 실패하면 입력을 그대로 돌려준다."""
        self.rejected = []
        if not candidates:
            return []
        if not self.usable():
            # 키가 없다. 1단계 결과를 그대로 쓴다 — 조용히 다 통과시키면
            # 필터가 꺼진 것과 같은데 아무도 모른다.
            return candidates

        decided: dict[str, bool] = {}
        reasons: dict[str, str] = {}
        pending: list[Candidate] = []

        for candidate in candidates:
            cached = self._cached(candidate, as_of=as_of)
            if cached is None:
                pending.append(candidate)
            else:
                decided[candidate.fingerprint], reasons[candidate.fingerprint] = cached

        if pending:
            try:
                fresh = self._ask(pending[:MAX_ITEMS], as_of=as_of)
            except Exception:
                # API 실패. 판정을 못 했으니 1단계 결과를 유지한다.
                fresh = {}
            for candidate in pending:
                verdict = fresh.get(candidate.fingerprint)
                if verdict is None:
                    # 못 물어본 건(상한 초과·응답 누락)은 유지한다.
                    decided[candidate.fingerprint] = True
                    reasons[candidate.fingerprint] = "미판정 — 1단계 유지"
                else:
                    decided[candidate.fingerprint] = verdict[0]
                    reasons[candidate.fingerprint] = verdict[1]

        kept: list[Candidate] = []
        for candidate in candidates:
            if decided.get(candidate.fingerprint, True):
                kept.append(candidate)
            else:
                self.rejected.append((candidate, reasons.get(candidate.fingerprint, "")))
        return kept

    # -- 호출 -------------------------------------------------------------------

    def _ask(
        self, candidates: list[Candidate], *, as_of: datetime
    ) -> dict[str, tuple[bool, str]]:
        client = self.client or self._client()
        payload = [
            {
                "id": candidate.fingerprint,
                "entity_id": candidate.entity_id,
                "category": str(candidate.category),
                "title": candidate.title,
            }
            for candidate in candidates
        ]

        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "record_screen"},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, indent=2),
                }
            ],
        )

        out: dict[str, tuple[bool, str]] = {}
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            for item in block.input.get("verdicts", []):
                key = str(item.get("id") or "")
                if key:
                    out[key] = (bool(item.get("keep", True)), str(item.get("reason") or ""))

        for candidate in candidates:
            if candidate.fingerprint in out:
                self._remember(candidate, out[candidate.fingerprint], as_of=as_of)
        return out

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    # -- 캐시 -------------------------------------------------------------------

    def _cached(self, candidate: Candidate, *, as_of: datetime) -> tuple[bool, str] | None:
        frame = self.store.get(AGENT_CACHE, as_of=as_of, entity=[candidate.entity_id], lookback=30)
        if frame.empty:
            return None
        hit = frame[
            (frame["agent"] == AGENT)
            & (frame["agent_version"] == VERSION)
            & (frame["features_hash"] == candidate.fingerprint)
        ]
        if hit.empty:
            return None
        try:
            payload = json.loads(str(hit.iloc[-1]["output"]))
        except (TypeError, ValueError):
            return None
        return bool(payload.get("keep", True)), str(payload.get("reason") or "")

    def _remember(
        self, candidate: Candidate, verdict: tuple[bool, str], *, as_of: datetime
    ) -> None:
        keep, reason = verdict
        run_id = f"{AGENT}-{as_of:%Y%m%dT%H%M%S}-{candidate.fingerprint}"
        if self.store.ingest_run_recorded(AGENT_CACHE, run_id):
            return
        self.store.append(
            AGENT_CACHE,
            [
                {
                    "entity_id": candidate.entity_id,
                    "valid_from": as_of,
                    # 벽시계가 아니라 as_of. 이유는 모듈 docstring 참조.
                    "observed_at": as_of,
                    "source": AGENT,
                    "agent": AGENT,
                    "agent_version": VERSION,
                    "features_hash": candidate.fingerprint,
                    "output": json.dumps(
                        {"keep": keep, "reason": reason}, ensure_ascii=False
                    ),
                    "computed_at": self.clock.now(),
                }
            ],
            ingest_run_id=run_id,
        )
