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

## 감성 점수 — 측정 전용 (2026-08-30, 사전등록 시행 F)

판정과 함께 같은 헤드라인에서 **방향 점수(−1~+1)** 를 받아 ``news_sentiment`` 에
세션·종목당 한 행으로 적는다. 이것은 차단 결정이 아니고, **보상 함수·가중치에
들어가지 않는다**(불변식 8). 60세션이 쌓인 뒤 IC 를 재서 통과해야 비로소 Analyst
피처가 된다 — 그 전까지는 창고에 쌓이기만 한다. 옛 캐시(점수 없는 판정)는
"점수 없음" 으로 두고 다시 묻지 않는다.

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

from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.schemas.verdict import Category

AGENT_CACHE = "agent_cache"
SENTIMENT_TABLE = "news_sentiment"
AGENT = "news-screen"
# **v0.2.0 — 감성 점수를 출력에 더하면서 올렸다 (2026-09-01).**
# 2026-08-30 에 sentiment 를 스키마에 넣고도 이 버전을 안 올려서, 옛 캐시(점수
# 없는 판정 510행)가 그대로 맞아떨어져 **다시 묻지 않았다.** 그래서 news_sentiment
# 가 한 달 내내 0행이었다 — 기능은 들어갔는데 조용히 아무것도 안 쌓았다.
# 출력 스키마를 바꾸면 이 값을 올린다. 캐시 키가 이 값을 포함하는 이유가 이것이다.
VERSION = "news-screen-v0.2.0"

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

각 건에 **감성 점수**도 함께 낸다 — 그 헤드라인이 그 종목에게 얼마나 호재(+1)인지
악재(-1)인지. 이것은 차단 판정과 별개의 측정값이다: 기각한 건도 점수는 낸다
(예: "최대주주 등극" 은 기각이지만 +0.6). 주어진 제목에서만 읽고, 기억 속
다른 뉴스나 시세를 끌어오지 않는다. 판단이 안 서면 0 과 낮은 확신도.
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
                        "sentiment": {
                            "type": "number",
                            "description": (
                                "그 종목에게 이 헤드라인의 방향. -1(명백한 악재) ~ +1(명백한 호재). "
                                "차단 판정과 별개 — 기각한 건도 점수를 낸다. 제목만으로 낸다."
                            ),
                        },
                        "sentiment_confidence": {
                            "type": "number",
                            "description": "감성 점수의 확신도 0~1. 제목이 모호하면 낮게.",
                        },
                    },
                    "required": ["id", "keep", "reason", "sentiment", "sentiment_confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    },
    "strict": True,
}


def _sentiment_of(item: dict[str, Any]) -> tuple[float, float] | None:
    """응답·캐시의 감성 칸. 없거나 숫자가 아니면 None — 지어내지 않는다."""
    raw = item.get("sentiment")
    if raw is None:
        return None
    try:
        score = float(raw)
        confidence = float(item.get("sentiment_confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    return max(-1.0, min(1.0, score)), max(0.0, min(1.0, confidence))


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
        sentiments: dict[str, tuple[float, float]] = {}
        pending: list[Candidate] = []

        for candidate in candidates:
            cached = self._cached(candidate, as_of=as_of)
            if cached is None:
                pending.append(candidate)
            else:
                decided[candidate.fingerprint], reasons[candidate.fingerprint] = cached[0], cached[1]
                if cached[2] is not None:
                    sentiments[candidate.fingerprint] = cached[2]

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
                    if verdict[2] is not None:
                        sentiments[candidate.fingerprint] = verdict[2]

        self._record_sentiment(candidates, sentiments, as_of=as_of)

        kept: list[Candidate] = []
        for candidate in candidates:
            if decided.get(candidate.fingerprint, True):
                kept.append(candidate)
            else:
                self.rejected.append((candidate, reasons.get(candidate.fingerprint, "")))
        return kept

    def _record_sentiment(
        self,
        candidates: list[Candidate],
        sentiments: dict[str, tuple[float, float]],
        *,
        as_of: datetime,
    ) -> None:
        """종목·세션당 한 행. 확신도 가중 평균. 점수가 하나도 없는 종목은 적지 않는다."""
        by_entity: dict[str, list[tuple[Candidate, tuple[float, float]]]] = {}
        for candidate in candidates:
            score = sentiments.get(candidate.fingerprint)
            if score is None:
                continue
            by_entity.setdefault(candidate.entity_id, []).append((candidate, score))
        rows = []
        for entity, items in sorted(by_entity.items()):
            weights = [max(confidence, 1e-6) for _c, (_s, confidence) in items]
            total = sum(weights)
            sentiment = sum(s * w for (_c, (s, _conf)), w in zip(items, weights, strict=True)) / total
            confidence = sum(conf for _c, (_s, conf) in items) / len(items)
            digest = hashlib.sha256(
                "|".join(sorted(c.fingerprint for c, _ in items)).encode("utf-8")
            ).hexdigest()[:16]
            run_id = f"{AGENT}-sentiment-{as_of:%Y%m%dT%H%M%S}-{entity}"
            if self.store.ingest_run_recorded(SENTIMENT_TABLE, run_id):
                continue
            rows.append((run_id, {
                "entity_id": entity,
                "valid_from": as_of,
                "observed_at": as_of,  # agent_cache 와 같은 이유 — 출력은 as_of 의 함수다
                "source": AGENT,
                "market": entity.split(":", 1)[0],
                "analyst": AGENT,
                "analyst_version": VERSION,
                "sentiment": float(max(-1.0, min(1.0, sentiment))),
                "confidence": float(max(0.0, min(1.0, confidence))),
                "headline_count": len(items),
                "model": self.model,
                "features_hash": digest,
            }))
        for run_id, row in rows:
            self.store.append(SENTIMENT_TABLE, [row], ingest_run_id=run_id)

    # -- 호출 -------------------------------------------------------------------

    def _ask(
        self, candidates: list[Candidate], *, as_of: datetime
    ) -> dict[str, tuple[bool, str, tuple[float, float] | None]]:
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
        self._record_usage(response, item_count=len(candidates), as_of=as_of)

        out: dict[str, tuple[bool, str, tuple[float, float] | None]] = {}
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            for item in block.input.get("verdicts", []):
                key = str(item.get("id") or "")
                if key:
                    out[key] = (
                        bool(item.get("keep", True)),
                        str(item.get("reason") or ""),
                        _sentiment_of(item),
                    )

        for candidate in candidates:
            if candidate.fingerprint in out:
                self._remember(candidate, out[candidate.fingerprint], as_of=as_of)
        return out

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    def _record_usage(self, response: Any, *, item_count: int, as_of: datetime) -> None:
        """실제 토큰 사용량을 ``llm_usage`` 에 남긴다.

        비용은 여기서 계산하지 않는다 — 단가는 store.config 소관이고(불변식 10),
        이 함수는 계산에 쓸 원재료(토큰 수)만 정직하게 적는다. usage 가 없으면
        (테스트 스텁 등) 조용히 넘어간다 — 기록 실패가 판정 경로를 막으면 안 된다.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        request_id = str(getattr(response, "_request_id", None) or getattr(response, "id", None) or "")
        if not request_id:
            return
        run_id = f"{AGENT}-usage-{as_of:%Y%m%dT%H%M%S}-{request_id}"
        if self.store.ingest_run_recorded("llm_usage", run_id):
            return
        self.store.append(
            "llm_usage",
            [
                {
                    "entity_id": AGENT,
                    "valid_from": as_of,
                    # 벽시계가 아니라 as_of. 이유는 agent_cache 와 같다.
                    "observed_at": as_of,
                    "source": AGENT,
                    "agent": AGENT,
                    "agent_version": VERSION,
                    "model": self.model,
                    "request_id": request_id,
                    "items": item_count,
                    "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                    "cache_creation_input_tokens": int(
                        getattr(usage, "cache_creation_input_tokens", 0) or 0
                    ),
                    "cache_read_input_tokens": int(
                        getattr(usage, "cache_read_input_tokens", 0) or 0
                    ),
                    "computed_at": self.clock.now(),
                }
            ],
            ingest_run_id=run_id,
        )

    # -- 캐시 -------------------------------------------------------------------

    def _cached(
        self, candidate: Candidate, *, as_of: datetime
    ) -> tuple[bool, str, tuple[float, float] | None] | None:
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
        # 옛 캐시(점수 없는 판정)는 점수 없음으로 둔다 — 다시 묻지 않는다.
        return bool(payload.get("keep", True)), str(payload.get("reason") or ""), _sentiment_of(payload)

    def _remember(
        self,
        candidate: Candidate,
        verdict: tuple[bool, str, tuple[float, float] | None],
        *,
        as_of: datetime,
    ) -> None:
        keep, reason, sentiment = verdict
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
                        {
                            "keep": keep,
                            "reason": reason,
                            **(
                                {"sentiment": sentiment[0], "sentiment_confidence": sentiment[1]}
                                if sentiment is not None
                                else {}
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    "computed_at": self.clock.now(),
                }
            ],
            ingest_run_id=run_id,
        )
