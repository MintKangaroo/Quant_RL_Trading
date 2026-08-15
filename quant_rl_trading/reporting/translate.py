"""미장 뉴스 제목 — 영어를 한국어로 옮긴다. **화면용이다.**

## 왜 필요한가

브리핑 메일은 아침에 한 번 훑는 것이다. 국장 뉴스(``newsapi``, entity 15종
전부 KR)는 이미 한국어라 문제가 없지만, 미장 뉴스가 들어오기 시작하면
제목이 영어로 나간다 — "META Zuckerberg's $250 Billion Louisiana Data
Center Will Create Just 1,000 Jobs" 를 그대로 읽고 넘어가라는 것은 이
메일의 취지("찾아오는 것")에 안 맞는다.

## 불변식 8 — 이건 판단이 아니라 번역이다

``news_screen``·``macro_brief`` 는 판정·해설이라 신중히 다루지만, 여기서
Claude 가 하는 일은 **뜻을 옮기는 것뿐**이다. 새 정보를 만들지 않고, 신호·
피처·보상 어디에도 들어가지 않는다 — 화면에 무엇을 찍을지의 문제다.
그래서 판정 모델(``claude-opus-5``)이 아니라 더 가벼운 모델을 쓴다:
헤드라인 번역은 추론이 아니라 변환이고, 매일 아침 크론이 도는 경로라
지연·비용이 쌓인다.

## 원문을 버리지 않는다

번역 결과는 ``NewsRow.title_ko`` 에 **따로** 담긴다. ``title``(원문)은
그대로 남는다 — 오역이 나왔을 때 검증할 방법이 없으면 번역은 못 믿을
필터가 된다(``news_section`` 이 뉴스 선별 기준을 밝히는 것과 같은 이유).
렌더러는 번역이 있으면 번역을 앞세우고 원문을 작게 함께 보여준다.

## 실패해도 메일은 나간다

리포트는 비필수 경로다(reporting.md §2). 키가 없거나 API 가 죽으면
``title_ko`` 가 채워지지 않고, 렌더러는 원문 제목으로 조용히 대체한다.
뉴스 섹션이 비거나 메일 전체가 죽는 것보다 영어 제목이 낫다.

## 캐시

같은 (종목, 제목) 은 두 번 안 묻는다 — ``news_screen``·``macro_brief`` 와
같은 ``agent_cache`` 패턴이다(자연키: entity_id·valid_from·agent·
agent_version·features_hash, features_hash = 종목+제목 해시). ``observed_at``
은 다른 agent_cache 모듈과 같은 이유로 벽시계가 아니라 ``as_of`` 다 —
출력이 as_of 이전 데이터만의 함수라 과거 리플레이에서도 보여야 한다.

**``persist=True`` 가 기본값이다** (팀 리드 승인, 2026-08-15). 매일 아침
크론이 같은 기사를 다시 번역하면 돈과 시간을 그냥 버리는 것이다.
``persist=False`` 로 끌 수도 있다 — 캐시 오염을 의심할 때처럼 끄고 돌려야
할 때가 있다(``tools/send_briefing.py`` 의 ``--no-persist-translations``).

**캐시 히트/미스는 로그에 남긴다** (``logging`` — ``logger.info``). 안
남기면 캐시가 실제로 먹는지 아무도 모르고, 언젠가 키가 안 맞아 매번
미스가 나도 조용히 돈만 나간다.

## 어디에도 안 쓰인다

``build_briefing`` 은 ``translate`` 인자가 ``None`` 이면(기본값) 이 모듈을
아예 안 건드린다 — 테스트·백테스트·리플레이가 API 키나 네트워크 없이도
결정론을 지킨다. 실제 발송 도구(``tools/send_briefing.py``)만 이 클래스를
환경에서 만들어 명시적으로 넘긴다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_rl_trading.replay.clock import Clock

logger = logging.getLogger(__name__)

AGENT_CACHE = "agent_cache"
AGENT = "news-title-translate"
VERSION = "news-title-translate-v0.1.0"

KEY_ENV = "ANTHROPIC_API_KEY"

#: 헤드라인 번역은 추론이 아니라 변환이다 — 판정 모델을 아낀다(모듈 독스트링).
MODEL = "claude-sonnet-5"

#: 한 번에 옮길 최대 건수. ``config.reporting.news_rows`` 가 시장당 몇 건 안
#: 되지만(2026-08 기준 3), 여유를 넉넉히 둔다.
MAX_ITEMS = 20

SYSTEM = """\
당신은 미국 증시 뉴스 제목을 영어에서 한국어로 옮긴다.

**뜻만 옮긴다.** 없는 맥락을 붙이거나 풀어서 설명하지 않는다. 회사명·티커·
인명 등 고유명사는 원형을 유지한다(필요하면 한글 표기를 붙여도 된다).
숫자·통화는 한국어 표기 관행으로 바꿔도 된다 ($250 Billion → 2,500억 달러).

직역이 어색해도 의역으로 없는 정보를 지어내는 것보다 낫다. 확실하지
않은 부분은 더 보수적으로 옮긴다.
"""

TOOL = {
    "name": "record_translation",
    "description": (
        "각 제목의 한국어 번역을 기록한다. 입력으로 받은 모든 id 에 대해 "
        "정확히 하나씩."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "입력으로 받은 건의 id"},
                        "ko": {"type": "string", "description": "한국어 번역"},
                    },
                    "required": ["id", "ko"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["translations"],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass(frozen=True)
class Headline:
    """번역할 제목 하나. 렌더 계층의 ``NewsRow`` 와 1:1 이지만 이 모듈은
    ``NewsRow`` 를 몰라도 되게 최소 필드만 받는다."""

    entity_id: str
    title: str

    @property
    def fingerprint(self) -> str:
        """캐시 키. 같은 (종목, 제목) 은 같은 번역이다."""
        raw = f"{self.entity_id}|{self.title}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class NewsTitleTranslate:
    """미장 뉴스 제목 번역기. 키가 없으면 아무것도 안 옮긴다(원문 그대로)."""

    store: Any
    clock: Clock
    api_key: str = ""
    model: str = MODEL
    client: Any = None
    #: **기본 켜짐** (팀 리드 승인, 2026-08-15). 모듈 독스트링 "캐시" 참고.
    #: 캐시 오염을 의심할 때처럼 끄고 돌려야 할 때는 ``persist=False``.
    persist: bool = True
    failures: list[str] = field(default_factory=list)

    @classmethod
    def from_env(
        cls, store: Any, clock: Clock, env: dict[str, str] | None = None, *, persist: bool = True
    ) -> NewsTitleTranslate:
        source = env if env is not None else dict(os.environ)
        return cls(
            store=store, clock=clock, api_key=(source.get(KEY_ENV) or "").strip(), persist=persist
        )

    def usable(self) -> bool:
        return bool(self.api_key) or self.client is not None

    # -- 번역 -------------------------------------------------------------------

    def translate(self, headlines: list[Headline], *, as_of: datetime) -> dict[str, str]:
        """``{fingerprint: 한국어 번역}``. 못 옮긴 건은 키에서 빠진다 —

        호출부가 ``dict.get(fp)`` 로 원문 폴백을 스스로 결정한다.
        """
        self.failures = []
        if not headlines:
            return {}
        if not self.usable():
            self.failures.append(f"{KEY_ENV} 없음 — 원문 유지")
            return {}

        out: dict[str, str] = {}
        pending: list[Headline] = []
        for headline in headlines:
            cached = self._cached(headline, as_of=as_of)
            if cached is not None:
                out[headline.fingerprint] = cached
            else:
                pending.append(headline)

        # 히트/미스를 남긴다 — 안 남기면 캐시가 실제로 먹는지 아무도 모르고,
        # 언젠가 키가 안 맞아 매번 미스가 나도 조용히 돈만 나간다.
        hits, misses = len(headlines) - len(pending), len(pending)
        logger.info(
            "%s: 캐시 히트 %d건 · 미스 %d건 (persist=%s)", AGENT, hits, misses, self.persist
        )

        if pending:
            try:
                fresh = self._ask(pending[:MAX_ITEMS], as_of=as_of)
            except Exception as error:
                # API 실패. 못 옮긴 건은 호출부가 원문으로 폴백한다(reporting.md §2).
                self.failures.append(f"번역 실패: {type(error).__name__}")
                fresh = {}
            out.update(fresh)
        return out

    # -- 호출 -------------------------------------------------------------------

    def _ask(self, headlines: list[Headline], *, as_of: datetime) -> dict[str, str]:
        client = self.client or self._client()
        payload = [
            {"id": headline.fingerprint, "title": headline.title} for headline in headlines
        ]

        response = client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "record_translation"},
            messages=[
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}
            ],
        )
        self._record_usage(response, item_count=len(headlines), as_of=as_of)

        out: dict[str, str] = {}
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            for item in block.input.get("translations", []):
                key = str(item.get("id") or "")
                ko = str(item.get("ko") or "")
                if key and ko:
                    out[key] = ko

        if self.persist:
            for headline in headlines:
                if headline.fingerprint in out:
                    self._remember(headline, out[headline.fingerprint], as_of=as_of)
        return out

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    def _record_usage(self, response: Any, *, item_count: int, as_of: datetime) -> None:
        """실제 토큰 사용량을 ``llm_usage`` 에 남긴다.

        ``persist`` 와 무관하게 남긴다 — 비용 집계는 창고에 캐시를 남기는
        문제와는 별개다. usage 가 없으면(테스트 스텁 등) 조용히 넘어간다.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        request_id = str(
            getattr(response, "_request_id", None) or getattr(response, "id", None) or ""
        )
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

    def _cached(self, headline: Headline, *, as_of: datetime) -> str | None:
        frame = self.store.get(
            AGENT_CACHE, as_of=as_of, entity=[headline.entity_id], lookback=180
        )
        if frame.empty:
            return None
        hit = frame[
            (frame["agent"] == AGENT)
            & (frame["agent_version"] == VERSION)
            & (frame["features_hash"] == headline.fingerprint)
        ]
        if hit.empty:
            return None
        try:
            payload = json.loads(str(hit.iloc[-1]["output"]))
        except (TypeError, ValueError):
            return None
        ko = payload.get("ko")
        return str(ko) if ko else None

    def _remember(self, headline: Headline, ko: str, *, as_of: datetime) -> None:
        run_id = f"{AGENT}-{as_of:%Y%m%dT%H%M%S}-{headline.fingerprint}"
        if self.store.ingest_run_recorded(AGENT_CACHE, run_id):
            return
        self.store.append(
            AGENT_CACHE,
            [
                {
                    "entity_id": headline.entity_id,
                    "valid_from": as_of,
                    # 벽시계가 아니라 as_of — 출력이 as_of 이전 데이터만의
                    # 함수라 그 시점에 알 수 있었던 것이 맞고, 벽시계를
                    # 찍으면 과거 리플레이에서 영영 안 보인다 (tables.py).
                    "observed_at": as_of,
                    "source": AGENT,
                    "agent": AGENT,
                    "agent_version": VERSION,
                    "features_hash": headline.fingerprint,
                    "output": json.dumps({"ko": ko}, ensure_ascii=False),
                    "computed_at": self.clock.now(),
                }
            ],
            ingest_run_id=run_id,
        )
