"""Claude 일일 리뷰 (M5) — **해설자, 심판 아님**.

하루 장이 끝나고 회계가 접힌 뒤(23:20 refresh_accounting) 그 사실을 Claude 에게
읽히고 사람 말로 된 리뷰 한 편을 받는다. 헤드라인은 Fund(트레이딩) 화면 머리에,
본문은 AI 리뷰 탭에 뜬다.

## 지키는 것

- **보상 함수·가중치에 들어가지 않는다** (불변식 8). 이 모듈의 출력을 읽는 곳은
  화면과 메일뿐이다.
- **사실은 화면과 같은 함수에서 온다** — `accounting.performance.daily` 와
  `executor.pipeline.action_reflection_rate`. 리뷰가 다른 숫자를 말하면 화면과
  리뷰 중 누가 맞는지 판정할 방법이 없다.
- **같은 사실이면 다시 안 묻는다.** 사실을 해시해 `agent_cache` 에 두고, 리플레이·
  재실행은 캐시가 답한다(`replay/cache.py`).
- **예산을 넘기면 안 부른다.** `llm.monthly_budget_usd` 대 이달 실측 지출
  (`dashboard.services.ai_review.cost_activity`, `llm_usage` 표). 안 부른 날도
  `reviews` 에 `skipped_budget` 로 남긴다 — "리뷰가 없다" 와 "안 불렀다" 는 다른
  사실이다.
- **주어진 숫자만 쓴다.** 시스템 프롬프트가 못박고, 출력은 tool_use 스키마로 받는다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_rl_trading.accounting import performance as performance_module
from quant_rl_trading.executor import pipeline as executor_pipeline
from quant_rl_trading.replay.cache import AgentCache, CacheKey, features_hash
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store
from quant_rl_trading.store import mode as store_mode

logger = logging.getLogger(__name__)

AGENT = "daily_review"
VERSION = "daily_review-v0.1.0"
KEY_ENV = "ANTHROPIC_API_KEY"
#: config `llm.review_model` 의 짧은 이름 → 실제 모델 문자열(단가표 키와 같다).
MODEL_BY_NAME = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}
TABLE = "reviews"

SYSTEM = """\
당신은 소규모 시스템 펀드의 하루를 운용자에게 설명하는 해설자다. 심판이 아니다 —
전략을 바꾸라거나 종목을 사라고 하지 않는다.

**주어진 숫자만 쓴다.** 값을 지어내거나 기억 속 시장 뉴스를 끌어오지 않는다.
숫자가 없는 항목은 "기록이 없다" 고 쓴다.

읽는 사람은 전문가가 아니다. 쉬운 말로, 다음 순서로:
1. 오늘 결과 한 줄 (얼마 벌었/잃었나, 시장 대비 어땠나)
2. 왜 그랬나 — 체결·보유·시장 중 **숫자가 말해 주는 것만**
3. 눈여겨볼 것 하나 (낙폭·반영률·데이터 지연 같은 운영 신호가 있으면 그것)

"때문에" 보다 "와 함께" 가 정확할 때가 많다. 인과를 단정하지 않는다.
"""

TOOL = {
    "name": "record_review",
    "description": "하루 리뷰를 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tone": {
                "type": "string",
                "enum": ["good", "bad", "mixed", "quiet"],
                "description": "오늘이 운용자에게 어떻게 읽히는가. quiet = 거의 안 움직인 날.",
            },
            "headline": {"type": "string", "description": "한 문장. 40자 안팎. 숫자 하나는 들어간다."},
            "body": {"type": "string", "description": "3~5문장. 쉬운 말. 위 순서대로."},
        },
        "required": ["tone", "headline", "body"],
    },
}


def gather_facts(store: Store, *, as_of: datetime, market: str) -> dict[str, Any]:
    """리뷰의 재료. **화면이 쓰는 함수 그대로** 부른다."""
    perf = performance_module.daily(store, as_of=as_of, fill_limit=12).as_dict()
    facts: dict[str, Any] = {
        "market": market,
        "mode": store_mode.of(store.root).code,
        "session": perf.get("session"),
        "nav": perf.get("nav"),
        "nav_change": perf.get("nav_change"),
        "daily_return": perf.get("daily_return"),
        "cumulative_return": perf.get("cumulative_return"),
        "drawdown": perf.get("drawdown"),
        "total_pnl": perf.get("total_pnl"),
        "realized_pnl": perf.get("realized_pnl"),
        "fill_count": perf.get("fill_count"),
        "buy_count": perf.get("buy_count"),
        "sell_count": perf.get("sell_count"),
        "fills": [
            {k: fill.get(k) for k in ("entity_id", "name", "side", "quantity", "price", "realized_pnl")}
            for fill in (perf.get("fills") or [])[:12]
        ],
        "note": perf.get("note"),
    }
    try:
        facts["action_reflection_rate"] = round(
            executor_pipeline.action_reflection_rate(store, as_of=as_of), 4
        )
    except Exception as exc:  # 반영률은 부가 정보다 — 없어도 리뷰는 쓴다
        facts["action_reflection_rate"] = None
        logger.info("반영률을 못 읽었다: %s", exc)
    facts["benchmark"] = _benchmark_move(store, as_of=as_of, market=market)
    return facts


def _benchmark_move(store: Store, *, as_of: datetime, market: str) -> dict[str, Any] | None:
    """벤치마크 지수의 그날 등락. 없으면 None — 지어내지 않는다."""
    try:
        index_id = str(store.config(f"benchmark.{market.lower()}_index", as_of=as_of))
        frame = store.get("indices", as_of=as_of, lookback=12, entity=index_id, columns=["valid_from", "close"])
    except Exception:  # noqa: BLE001
        return None
    if frame.empty or len(frame) < 2:
        return None
    frame = frame.sort_values("valid_from")
    last, prev = float(frame.iloc[-1]["close"]), float(frame.iloc[-2]["close"])
    return {
        "index": index_id,
        "session": frame.iloc[-1]["valid_from"].date().isoformat(),
        "return": round(last / prev - 1.0, 5) if prev > 0 else None,
    }


@dataclass
class DailyReviewer:
    store: Store          # 리뷰·캐시·사용량을 **적는** 창고 (실전 창고)
    facts_store: Store    # 사실을 **읽는** 창고 (모의계좌 장부 등)
    clock: Clock
    api_key: str = ""
    model: str = MODEL_BY_NAME["sonnet"]
    client: Any = None
    budget_usd: float | None = None
    month_to_date_usd: float = 0.0
    failures: list[str] = field(default_factory=list)

    @classmethod
    def from_store(
        cls, store: Store, facts_store: Store, clock: Clock, *, as_of: datetime, env: dict[str, str] | None = None
    ) -> DailyReviewer:
        import os

        from quant_rl_trading.dashboard.services import ai_review

        source = env if env is not None else dict(os.environ)
        name = str(store.config("llm.review_model", as_of=as_of) or "sonnet")
        cost = ai_review.cost_activity(store, as_of=as_of, lookback=35)
        return cls(
            store=store, facts_store=facts_store, clock=clock,
            api_key=(source.get(KEY_ENV) or "").strip(),
            model=MODEL_BY_NAME.get(name, name),
            budget_usd=float(cost["budget_usd"]),
            month_to_date_usd=float(cost["month_to_date_usd"] or 0.0),
        )

    # -- 진입 -------------------------------------------------------------------

    def review(self, *, as_of: datetime, market: str) -> dict[str, Any]:
        facts = gather_facts(self.facts_store, as_of=as_of, market=market)
        entity = f"{facts['mode']}:{market}"
        digest = features_hash(facts)
        key = CacheKey(agent=AGENT, agent_version=VERSION, entity_id=entity, as_of=as_of, features_hash=digest)
        cache = AgentCache(self.store, self.clock)
        cached = cache.get(key)
        if cached is not None:
            return self._record(entity, as_of, facts, cached, digest, status="cached")
        if self.budget_usd is not None and self.month_to_date_usd >= self.budget_usd:
            skipped = {
                "tone": "quiet",
                "headline": f"이달 LLM 예산 ${self.budget_usd:.0f} 소진 — 리뷰를 쓰지 않았다",
                "body": f"이달 실측 지출 ${self.month_to_date_usd:.2f}. 사실은 화면 숫자 그대로다.",
            }
            return self._record(entity, as_of, facts, skipped, digest, status="skipped_budget")
        if not (self.api_key or self.client is not None):
            skipped = {"tone": "quiet", "headline": "API 키가 없어 리뷰를 쓰지 않았다", "body": ""}
            return self._record(entity, as_of, facts, skipped, digest, status="skipped_no_key")
        output = self._ask(facts, as_of=as_of)
        cache.put(key, output, ingest_run_id=f"{AGENT}-{entity}-{as_of:%Y%m%dT%H%M%S}")
        return self._record(entity, as_of, facts, output, digest, status="written")

    # -- LLM --------------------------------------------------------------------

    def _ask(self, facts: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
        client = self.client or self._client()
        response = client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "record_review"},
            messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2, default=str)}],
        )
        self._record_usage(response, as_of=as_of)
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                data = dict(block.input)
                return {
                    "tone": str(data.get("tone") or "quiet"),
                    "headline": str(data.get("headline") or "").strip(),
                    "body": str(data.get("body") or "").strip(),
                }
        raise RuntimeError("Claude 가 record_review 를 부르지 않았다")

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    def _record_usage(self, response: Any, *, as_of: datetime) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        request_id = str(getattr(response, "id", "") or "")
        run_id = f"{AGENT}-usage-{as_of:%Y%m%dT%H%M%S}-{request_id}"
        if self.store.ingest_run_recorded("llm_usage", run_id):
            return
        self.store.append(
            "llm_usage",
            [{
                "entity_id": AGENT, "valid_from": as_of, "observed_at": as_of, "source": AGENT,
                "agent": AGENT, "agent_version": VERSION, "model": self.model,
                "request_id": request_id, "items": 1,
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                "computed_at": self.clock.now(),
            }],
            ingest_run_id=run_id,
        )

    # -- 기록 -------------------------------------------------------------------

    def _record(
        self, entity: str, as_of: datetime, facts: dict[str, Any], output: dict[str, Any],
        digest: str, *, status: str,
    ) -> dict[str, Any]:
        row = {
            "entity_id": entity, "valid_from": as_of, "observed_at": self.clock.now(), "source": AGENT,
            "market": str(facts["market"]), "mode": str(facts["mode"]),
            "headline": str(output.get("headline") or ""), "body": str(output.get("body") or ""),
            "tone": str(output.get("tone") or "quiet"), "model": self.model,
            "features_hash": digest, "status": status,
        }
        run_id = f"{AGENT}-{entity}-{as_of:%Y%m%dT%H%M%S}-{digest}"
        if not self.store.ingest_run_recorded(TABLE, run_id):
            self.store.append(TABLE, [row], ingest_run_id=run_id)
        return {**row, "facts": facts}


def latest_review(store: Store, *, as_of: datetime, market: str, lookback: int = 10) -> dict[str, Any] | None:
    """화면이 읽는 진입점 — as_of 시점에 있던 가장 최근 리뷰."""
    frame = store.get(TABLE, as_of=as_of, lookback=lookback)
    if frame.empty:
        return None
    frame = frame[frame["market"] == market]
    if frame.empty:
        return None
    row = frame.sort_values(["valid_from", "observed_at"]).iloc[-1]
    return {
        "session_at": row["valid_from"].isoformat(),
        "written_at": row["observed_at"].isoformat(),
        "mode": str(row["mode"]), "market": str(row["market"]),
        "headline": str(row["headline"]), "body": str(row["body"]), "tone": str(row["tone"]),
        "model": str(row["model"]), "status": str(row["status"]),
    }
