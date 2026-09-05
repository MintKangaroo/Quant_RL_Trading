"""LLM Analyst — Claude 에게 **숫자만 보여 주고** 사업 전망을 묻는다 (사전등록 시행 G).

## 왜 이것이 랭커·RL 과 다른가

LightGBM 랭커도 RL 도 진 이유는 하나다: **표본 300세션에 과적합**. 우리 데이터로 학습하는
모델은 그 300개를 외운다. LLM 은 우리 표본에서 배우지 않으므로 그 실패 방식이 적용되지
않는다. 대신 다른 위험이 있고, 그것을 코드로 눌러 둔다:

- **지식 컷오프 이후를 모른다** → 그래서 판단 재료를 프롬프트로 전부 준다. 종목명·티커를
  주지 않으면 좋겠지만 공시 제목에 회사 이름이 들어 있어 완전 차단은 불가능하다. 대신
  **가격 예측을 묻지 않는다** — "이 회사의 사업이 향후 1~3개월 좋아 보이나" 만 묻는다.
- **같은 입력에 다른 답** → `agent_cache` 가 입력 해시로 답을 고정한다. 같은 캐시로 다시
  돌리면 같은 점수가 나온다(프로토콜의 재현성 조항).

## 지키는 것

- **직접 수집하지 않는다** (Analyst 계약). 입력은 `store` 와 `as_of` 뿐이고 전부 게이트를
  지난다 — 공시 전 재무는 애초에 안 보인다.
- **불변식 8**: 이 점수는 **보상 함수·가중치에 들어가지 않는다.** 종목 선정 점수일 뿐이고,
  Analyst 로 채택되더라도 관찰 모드(가중치 0)에서 시작한다.
- **예산을 넘기면 안 부른다** (`llm.monthly_budget_usd`). 안 부른 종목은 **점수가 없다** —
  0 으로 채우지 않는다. "없다" 와 "중립이다" 는 다른 사실이고, 0 으로 채우면 그 종목이
  횡단면 한가운데에 서서 순위를 조용히 왜곡한다.
- **모르는 값은 null 로 보낸다.** 창고에 배당 이력이 없으면 배당수익률은 `null` 이다.
  추정치로 채우면 모델이 없는 사실을 근거로 쓴다.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.analysts.base import Analyst, rank_score
from quant_rl_trading.store.errors import DuplicateIngestRun
from quant_rl_trading.analysts.fundamental import (
    FLOW_METRICS,
    FUNDAMENTALS,
    SOURCE_BY_MARKET,
    to_quarterly,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.cache import AgentCache, CacheKey, features_hash
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.schemas.signal import Evidence
from quant_rl_trading.store import Store

logger = logging.getLogger(__name__)

AGENT = "llm"
VERSION = "llm-v0.1.0"
KEY_ENV = "ANTHROPIC_API_KEY"
USAGE = "llm_usage"
DOCUMENTS = "documents"
MARKET_STATS = "market_stats"

#: config `llm.review_model` 의 짧은 이름 → 실제 모델 문자열 (단가표 키와 같다).
MODEL_BY_NAME = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}

#: 재무 창. `fundamental.LOOKBACK_DAYS` 보다 짧다 — 여기는 **최근 4분기**만 보여 주면 되고
#: 전년 동기 TTM 을 만들 필요가 없다. 8분기 대신 5~6분기가 잡히게 700일.
FUNDAMENTALS_LOOKBACK = 700
#: 최근 공시 제목을 찾는 창(달력일)과 개수.
DOCUMENT_LOOKBACK = 60
MAX_DOCUMENTS = 5
#: 20일 수익률·변동성.
PRICE_WINDOW = 20
PRICE_LOOKBACK = 45
#: 시가총액을 거슬러 찾는 창. 매일 관측되므로 길 필요가 없다.
MARKET_CAP_LOOKBACK = 45
#: 한 번의 호출에 담는 종목 수. 늘리면 호출 수가 줄지만 한 응답이 길어져 잘릴 위험이 는다.
BATCH_SIZE = 20
#: 최근 4분기.
QUARTERS = 4

SYSTEM = """\
당신은 한국 상장사의 **사업 전망**을 숫자로만 판단한다.

각 종목에 대해 **향후 1~3개월 이 회사의 사업이 좋아 보이는가**를 -1 ~ +1 로 매긴다.

## 반드시 지킬 것

1. **주가를 예측하지 않는다.** "오를 것 같다" 가 아니라 "사업이 좋아 보인다" 를 매긴다.
   차트·수급·시장 심리를 근거로 쓰지 말 것. 주어진 20일 수익률·변동성은 **맥락일 뿐**이며
   그 방향을 그대로 점수로 옮기지 않는다.
2. **주어진 숫자만 쓴다.** 기억 속의 뉴스·실적·주가를 끌어오지 않는다. 당신이 아는 이 회사의
   과거는 이 데이터보다 오래되었을 수 있다.
3. **모르면 0 에 가깝게.** 재무가 비어 있거나 판단 재료가 모자라면 confidence 를 낮게 준다.
   억지로 방향을 만들지 않는다.
4. 단위는 원(KRW)이다. `null` 은 **관측이 없다**는 뜻이지 0 이 아니다.

## 무엇을 보나

매출·영업이익·순이익의 **분기 추세**(개선되고 있나, 꺾였나), 자본 대비 부채의 방향,
밸류에이션(PER·PBR 이 낮으면 같은 사업이 싸게 붙어 있다), 공시 제목이 말하는 사건.
"""

TOOL: dict[str, Any] = {
    "name": "record_outlook",
    "description": "각 종목의 사업 전망 점수를 기록한다. 입력으로 받은 모든 id 에 하나씩.",
    "input_schema": {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "입력에 있던 id 그대로."},
                        "outlook": {
                            "type": "number",
                            "description": (
                                "향후 1~3개월 **사업** 전망. -1(뚜렷이 나빠지는 중) ~ "
                                "+1(뚜렷이 좋아지는 중). 주가 예측이 아니다."
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "description": "이 판단의 확신도 0~1. 재무가 비었으면 낮게.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "한 문장. 어느 숫자를 보고 그렇게 봤는지.",
                        },
                    },
                    "required": ["id", "outlook", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["picks"],
        "additionalProperties": False,
    },
}


def _finite(value: Any) -> float | None:
    """숫자로 만들되 **못 만들면 None**. NaN 을 0 으로 바꾸지 않는다."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


class LlmPickAnalyst(Analyst):
    """Claude 의 사업 전망 점수. 계약은 다른 Analyst 와 같다 (`features` → `raw_score`)."""

    name = AGENT
    version = VERSION

    def __init__(
        self,
        store: Store,
        clock: Clock,
        *,
        market: Market = Market.KR,
        entities: frozenset[str] | None = None,
        api_key: str = "",
        model: str | None = None,
        client: Any = None,
        budget_usd: float | None = None,
        month_to_date_usd: float = 0.0,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        super().__init__(store, clock, market=market)
        #: 이 종목들만 묻는다. None 이면 거래 가능한 전 종목 — 비용이 크므로 시행은 표본을 준다.
        self.entities = entities
        self.api_key = api_key
        self.model = model or MODEL_BY_NAME["sonnet"]
        self.client = client
        self.budget_usd = budget_usd
        self.month_to_date_usd = month_to_date_usd
        self.batch_size = batch_size
        #: 이번 실행의 진단. 시행 도구가 읽어 보고한다.
        self.calls = 0
        self.cache_hits = 0
        self.skipped_budget = 0
        self.reasons: dict[str, str] = {}
        self.failures: list[str] = []

    @classmethod
    def from_store(
        cls,
        store: Store,
        clock: Clock,
        *,
        as_of: datetime,
        market: Market = Market.KR,
        entities: frozenset[str] | None = None,
        env: dict[str, str] | None = None,
        client: Any = None,
    ) -> LlmPickAnalyst:
        """설정·예산·키를 창고와 환경에서 읽어 만든다 (`auditor.daily_review` 와 같은 규칙)."""
        from quant_rl_trading.dashboard.services import ai_review

        source = env if env is not None else dict(os.environ)
        name = str(store.config("llm.review_model", as_of=as_of) or "sonnet")
        cost = ai_review.cost_activity(store, as_of=as_of, lookback=35)
        return cls(
            store,
            clock,
            market=market,
            entities=entities,
            api_key=(source.get(KEY_ENV) or "").strip(),
            model=MODEL_BY_NAME.get(name, name),
            client=client,
            budget_usd=float(cost["budget_usd"]),
            month_to_date_usd=float(cost["month_to_date_usd"] or 0.0),
        )

    # -- 입력 만들기 -------------------------------------------------------------

    def payloads(self, as_of: datetime) -> dict[str, dict[str, Any]]:
        """종목별 프롬프트 입력. **전부 store 경유** — as_of 게이트가 미래를 막는다."""
        universe = self.tradable_entities(as_of, lookback=PRICE_LOOKBACK)
        wanted = set(self.entities) if self.entities is not None else universe
        if wanted is None:
            return {}
        if universe is not None:
            wanted = {entity for entity in wanted if entity in universe}
        if not wanted:
            return {}

        quarters = self._quarterly(as_of, wanted)
        caps = self._market_caps(as_of, wanted)
        moves = self._price_moves(as_of, wanted)
        titles = self._documents(as_of, wanted)

        out: dict[str, dict[str, Any]] = {}
        for entity in sorted(wanted):
            history = quarters.get(entity, [])
            latest = history[-1] if history else {}
            cap = caps.get(entity)
            net_income_ttm = sum(
                row["net_income"] for row in history[-QUARTERS:] if row.get("net_income") is not None
            ) if history else None
            equity = latest.get("total_equity")
            revenue_ttm = sum(
                row["revenue"] for row in history[-QUARTERS:] if row.get("revenue") is not None
            ) if history else None
            payload = {
                # 종목코드는 준다 — 공시 제목에 이미 회사가 드러나므로 숨겨 봐야 반쪽이고,
                # 캐시 키·응답 매칭에 안정된 식별자가 필요하다.
                "id": entity,
                "quarters": history[-QUARTERS:],
                "valuation": {
                    "market_cap_krw": _round(cap, 0),
                    "per": _round(_ratio(cap, net_income_ttm), 2),
                    "pbr": _round(_ratio(cap, equity), 2),
                    # 창고에 배당 이력이 없다(`dividends` 0행). 모르는 것은 null 이다.
                    "dividend_yield": None,
                },
                "price": moves.get(entity, {"return_20d": None, "volatility_20d": None}),
                "recent_disclosures": titles.get(entity, []),
                "currency": "KRW",
                "revenue_ttm_krw": _round(revenue_ttm, 0),
            }
            # 재무도 시세도 없는 종목은 물어봐야 답할 것이 없다 — 호출을 아낀다.
            if not history and payload["price"]["return_20d"] is None:
                continue
            out[entity] = payload
        return out

    def _quarterly(self, as_of: datetime, wanted: set[str]) -> dict[str, list[dict[str, Any]]]:
        """종목별 최근 분기 재무. `fundamental.to_quarterly` 로 Q4 누적을 되돌린다."""
        raw = self.store.get(FUNDAMENTALS, as_of=as_of, lookback=FUNDAMENTALS_LOOKBACK)
        if raw.empty:
            return {}
        raw = raw[
            (raw["source"] == SOURCE_BY_MARKET.get(str(self.market), "dart"))
            & (raw["market"] == str(self.market))
            & raw["entity_id"].isin(wanted)
            # 회계기간이 끝나기 전에 공시될 수 없다 (fundamental.py 의 같은 방어).
            & (raw["observed_at"] >= raw["valid_from"])
        ]
        if raw.empty:
            return {}
        quarterly = to_quarterly(raw)
        if quarterly.empty:
            return {}
        keep = [*FLOW_METRICS, "total_equity", "total_liabilities", "total_assets"]
        quarterly = quarterly[quarterly["metric"].isin(keep)]
        out: dict[str, list[dict[str, Any]]] = {}
        for entity, group in quarterly.groupby("entity_id"):
            table = group.pivot_table(
                index="fiscal_period", columns="metric", values="value", aggfunc="last"
            ).sort_index()
            rows: list[dict[str, Any]] = []
            for period, row in table.tail(QUARTERS).iterrows():
                record: dict[str, Any] = {"period": str(period)}
                for metric in keep:
                    record[metric] = _round(_finite(row.get(metric)), 0)
                rows.append(record)
            if rows:
                out[str(entity)] = rows
        return out

    def _market_caps(self, as_of: datetime, wanted: set[str]) -> dict[str, float]:
        frame = self.store.get(
            MARKET_STATS,
            as_of=as_of,
            lookback=MARKET_CAP_LOOKBACK,
            until=as_of,
            market=str(self.market),
            columns=["entity_id", "metric", "value", "valid_from"],
        )
        if frame.empty:
            return {}
        caps = frame[(frame["metric"] == "market_cap") & frame["entity_id"].isin(wanted)]
        if caps.empty:
            return {}
        latest = caps.sort_values("valid_from").groupby("entity_id")["value"].last()
        return {str(k): float(v) for k, v in latest.items() if float(v) > 0}

    def _price_moves(self, as_of: datetime, wanted: set[str]) -> dict[str, dict[str, float | None]]:
        """20일 수익률·변동성. 맥락으로만 준다 — 프롬프트가 이것을 점수로 옮기지 말라고 못박는다."""
        prices = self.price_panel(as_of, lookback=PRICE_LOOKBACK)
        if prices.empty:
            return {}
        prices = prices[prices["entity_id"].isin(wanted)]
        if prices.empty:
            return {}
        close = self.wide(prices, "close")
        window = close.tail(PRICE_WINDOW + 1)
        if len(window) < 3:
            return {}
        returns = window.pct_change()
        total = window.iloc[-1] / window.iloc[0] - 1.0
        volatility = returns.std()
        out: dict[str, dict[str, float | None]] = {}
        for entity in window.columns:
            out[str(entity)] = {
                "return_20d": _round(_finite(total.get(entity)), 4),
                "volatility_20d": _round(_finite(volatility.get(entity)), 4),
            }
        return out

    def _documents(self, as_of: datetime, wanted: set[str]) -> dict[str, list[str]]:
        frame = self.store.get(
            DOCUMENTS,
            as_of=as_of,
            lookback=DOCUMENT_LOOKBACK,
            columns=["entity_id", "valid_from", "title"],
        )
        if frame.empty:
            return {}
        frame = frame[frame["entity_id"].isin(wanted)]
        if frame.empty:
            return {}
        frame = frame.sort_values("valid_from")
        out: dict[str, list[str]] = {}
        for entity, group in frame.groupby("entity_id"):
            titles = [str(title) for title in group["title"].tail(MAX_DOCUMENTS)]
            if titles:
                out[str(entity)] = titles
        return out

    # -- Analyst 계약 -------------------------------------------------------------

    def features(self, as_of: datetime) -> pd.DataFrame:
        """``entity_id`` × (llm_outlook, llm_confidence). 점수 못 받은 종목은 **행이 없다**."""
        payloads = self.payloads(as_of)
        if not payloads:
            return pd.DataFrame()
        scored = self.score(payloads, as_of=as_of)
        if not scored:
            return pd.DataFrame()
        frame = pd.DataFrame.from_dict(scored, orient="index")
        frame.index.name = "entity_id"
        return frame.sort_index()

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """전망 점수의 횡단면 순위. 확신도는 가중에 쓰지 않는다 — 모델이 스스로 매긴 값이라
        (agents.md §1 의 이유로) 신뢰도로 쓰면 과신이 그대로 실린다. 진단으로만 남긴다."""
        if features.empty or "llm_outlook" not in features.columns:
            return pd.Series(dtype=float)
        return rank_score(features["llm_outlook"])

    def evidence_for(self, features: pd.DataFrame, entity_id: str) -> tuple[Evidence, ...]:
        row = features.loc[entity_id]
        return (
            Evidence(key="llm_outlook", value=float(row.get("llm_outlook", 0.0))),
            Evidence(key="llm_confidence", value=float(row.get("llm_confidence", 0.0))),
        )

    # -- 호출 ---------------------------------------------------------------------

    def score(self, payloads: dict[str, dict[str, Any]], *, as_of: datetime) -> dict[str, dict[str, float]]:
        """캐시 → (필요하면) 호출. 예산을 넘기면 **부르지 않고 점수도 없다**."""
        cache = AgentCache(self.store, self.clock)
        out: dict[str, dict[str, float]] = {}
        pending: dict[str, dict[str, Any]] = {}

        for entity, payload in payloads.items():
            key = CacheKey(
                agent=AGENT,
                agent_version=VERSION,
                entity_id=entity,
                as_of=as_of,
                features_hash=features_hash(payload),
            )
            hit = cache.get(key)
            if hit is not None:
                self.cache_hits += 1
                outlook = _finite(hit.get("outlook"))
                if outlook is None:
                    continue
                out[entity] = {
                    "llm_outlook": outlook,
                    "llm_confidence": _finite(hit.get("confidence")) or 0.0,
                }
                if hit.get("reason"):
                    self.reasons[entity] = str(hit["reason"])
                continue
            pending[entity] = payload

        if not pending:
            return out
        if self.budget_usd is not None and self.month_to_date_usd >= self.budget_usd:
            # 표본 부족으로 판정 보류 — 추정치로 안 채운다 (프로토콜).
            self.skipped_budget += len(pending)
            logger.warning(
                "LLM 예산 소진 ($%.2f / $%.0f) — %d종목을 묻지 않았다",
                self.month_to_date_usd, self.budget_usd, len(pending),
            )
            return out
        if not (self.api_key or self.client is not None):
            self.failures.append("ANTHROPIC_API_KEY 가 없다")
            return out

        items = list(pending.items())
        for start in range(0, len(items), self.batch_size):
            batch = dict(items[start : start + self.batch_size])
            try:
                answers = self._ask(list(batch.values()), as_of=as_of)
            except Exception as error:  # 한 배치 실패가 전체를 막지 않는다
                self.failures.append(f"{type(error).__name__}: {error}")
                logger.warning("LLM 호출 실패: %s", error)
                continue
            for entity, payload in batch.items():
                answer = answers.get(entity)
                if answer is None:
                    continue
                # 같은 run_id 가 이미 있으면 건너뛴다. run_id 에는 features_hash 가
                # 없어서, 피처가 바뀌면 캐시 조회(get)는 빗나가는데 적재(put)는
                # 충돌한다 — 강제 종료 후 재개한 시행 G 가 여기서 DuplicateIngestRun
                # 으로 두 번 죽었다(2026-08-31). record_usage 와 같은 관용구.
                cache_run_id = f"{AGENT}-{entity}-{as_of:%Y%m%dT%H%M%S}"
                try:
                    if not cache.store.ingest_run_recorded("agent_cache", cache_run_id):
                        cache.put(
                            CacheKey(
                                agent=AGENT,
                                agent_version=VERSION,
                                entity_id=entity,
                                as_of=as_of,
                                features_hash=features_hash(payload),
                            ),
                            answer,
                            ingest_run_id=cache_run_id,
                        )
                except DuplicateIngestRun:
                    # 사전 검사와 적재 사이의 어긋남까지 방어한다. 캐시 재적재
                    # 실패는 답을 잃는 것이 아니다 — answer 는 이미 손에 있고
                    # 다음 조회는 기존 캐시 행을 그대로 쓴다. 측정을 죽일
                    # 이유가 없다 (2026-08-31: 시행 G 가 여기서 세 번 죽었다).
                    logger.info("캐시 중복 적재 무시: %s", cache_run_id)
                outlook = _finite(answer.get("outlook"))
                if outlook is None:
                    continue
                out[entity] = {
                    "llm_outlook": outlook,
                    "llm_confidence": _finite(answer.get("confidence")) or 0.0,
                }
                if answer.get("reason"):
                    self.reasons[entity] = str(answer["reason"])
        return out

    def _ask(self, batch: list[dict[str, Any]], *, as_of: datetime) -> dict[str, dict[str, Any]]:
        client = self.client or self._client()
        response = client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(batch, ensure_ascii=False, indent=2, default=str),
                }
            ],
        )
        self.calls += 1
        self._record_usage(response, items=len(batch), as_of=as_of)
        out: dict[str, dict[str, Any]] = {}
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            for entry in dict(block.input).get("picks", []):
                key = str(entry.get("id") or "")
                if not key:
                    continue
                out[key] = {
                    "outlook": _finite(entry.get("outlook")),
                    "confidence": _finite(entry.get("confidence")) or 0.0,
                    "reason": str(entry.get("reason") or "").strip(),
                    "model": self.model,
                    "version": VERSION,
                }
        return out

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    def _record_usage(self, response: Any, *, items: int, as_of: datetime) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        request_id = str(getattr(response, "id", "") or "")
        run_id = f"{AGENT}-usage-{as_of:%Y%m%dT%H%M%S}-{request_id}-{self.calls}"
        if self.store.ingest_run_recorded(USAGE, run_id):
            return
        self.store.append(
            USAGE,
            [
                {
                    "entity_id": AGENT, "valid_from": as_of, "observed_at": as_of, "source": AGENT,
                    "agent": AGENT, "agent_version": VERSION, "model": self.model,
                    "request_id": request_id, "items": items,
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


__all__ = ["AGENT", "MODEL_BY_NAME", "SYSTEM", "TOOL", "VERSION", "LlmPickAnalyst"]
