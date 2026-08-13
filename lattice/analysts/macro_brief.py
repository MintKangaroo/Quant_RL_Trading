"""거시지표 해설 — 숫자는 코드가, 해석은 Claude 가.

## 왜 나누는가

"고용이 예상보다 나빠서 금리 인상을 막으니 오히려 호재" — 이 문장은 두 부분이
성질이 다르다.

- **"예상보다 나빴다", "지수가 +0.4% 올랐다"** — 계산이다. 재현 가능해야 하고,
  같은 as_of 로 두 번 물으면 같은 답이 나와야 한다
- **"그래서 금리 인상을 막고, 그건 주식에 호재다"** — 해석이다. 정책 경로와
  시장 심리를 엮는 일이라 규칙으로 못 적는다

그래서 **서프라이즈와 반응은 여기서 계산하고, Claude 에게는 그 숫자를 주고
해석만 시킨다.** Claude 가 숫자를 만들면 검증할 수 없고, 검증할 수 없는 숫자는
나중에 아무도 못 고친다.

**불변식 8 을 지킨다** — 이 출력은 보상 함수에 들어가지 않는다. 화면에 띄우는
해설일 뿐 점수도 IC 도 만들지 않는다. Claude 는 심판이 아니라 해설자다.

## "예상치" 의 한계 — 반드시 읽을 것

**블룸버그 컨센서스는 무료로 구할 수 없다.** 그래서 여기서 말하는 "예상"은
**직전값**이다. 즉 이 모듈이 재는 것은 정확히는

    서프라이즈 = 이번 값 − 직전 값        (컨센서스 대비가 아니다)

이다. 시장이 이미 예상해서 가격에 반영한 변화까지 "서프라이즈"로 세므로,
**컨센서스 대비 서프라이즈보다 과장된다.** 해설을 만들 때 Claude 에게 이
한계를 명시적으로 알려주고, 단정적으로 쓰지 말라고 지시한다.

반면 **시장 반응은 실측이다.** 발표 시각 전후 지수 수익률이라 추정이 아니다.
그래서 해설의 무게는 "예상 대비" 쪽이 아니라 "실제로 어떻게 반응했나" 쪽에
실어야 한다.

## 캐시

같은 (지표, 발표시각, 반응) 은 두 번 묻지 않는다. ``agent_cache`` 의
``observed_at`` 은 벽시계가 아니라 ``as_of`` 다 — 출력이 as_of 이전 데이터만의
함수이므로 (tables.py).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from lattice.replay.clock import Clock

AGENT_CACHE = "agent_cache"
INDICES = "indices"
AGENT = "macro-brief"
VERSION = "macro-brief-v0.1.0"

KEY_ENV = "ANTHROPIC_API_KEY"
MODEL = "claude-opus-5"

#: 시장 반응을 잴 지수. 국장은 KRX 광의 지수, 미장은 FRED S&P 500.
REACTION_INDEX = {"KR": ("KR:IDX:KRX 300", "KRX 300"), "US": ("US:IDX:SP500", "S&P 500")}

#: 반응을 재는 창(거래일). 발표 직전 종가 → 발표 다음 종가.
#: 하루로 잡는 이유는, 길게 잡으면 발표와 무관한 사건이 섞이기 때문이다.
REACTION_DAYS = 1

#: 한 번에 해설할 최대 건수. 발표가 몰린 날도 대여섯 건이다.
MAX_ITEMS = 8

SYSTEM = """\
당신은 거시지표 발표를 시장 참여자에게 설명한다.

각 건에 대해 **지표 → 정책 경로 → 시장 함의** 순서로 해석한다. 예를 들어
고용이 약하게 나오면, 그것 자체는 경기에 나쁜 소식이지만 금리 인상 압력을
낮춰 주식에는 호재로 읽힐 수 있다 — 이런 엇갈린 해석을 놓치지 않는다.

**주어진 숫자만 쓴다.** 값을 지어내거나 기억에서 다른 지표를 끌어오지 않는다.

## 반드시 지킬 것

1. **"예상치" 는 직전값이다.** 블룸버그 컨센서스가 아니다. 시장이 이미
   예상해 가격에 반영한 변화까지 서프라이즈로 세므로 **과장돼 있다.**
   "컨센서스를 상회했다" 처럼 단정하지 말고 "직전 대비" 로 쓴다.
2. **시장 반응은 실측이다.** 해석의 무게를 여기에 싣는다.
3. 반응과 통상적 해석이 어긋나면 **어긋났다고 쓴다.** 억지로 이야기를 맞추지
   않는다 — 발표 당일 지수는 다른 이유로도 움직인다.
4. 인과를 단정하지 않는다. "때문에" 보다 "와 함께" 가 정확할 때가 많다.
"""

TOOL = {
    "name": "record_brief",
    "description": "각 발표에 대한 해설을 기록한다. 입력으로 받은 모든 id 에 하나씩.",
    "input_schema": {
        "type": "object",
        "properties": {
            "briefs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "tone": {
                            "type": "string",
                            "enum": ["risk_on", "risk_off", "mixed", "neutral"],
                            "description": (
                                "시장에 어떻게 읽히는가. 지표 자체의 좋고 나쁨이 "
                                "아니라 **주식시장 함의**다 — 고용 부진이 금리 "
                                "기대를 낮춰 호재로 읽히면 risk_on 이다."
                            ),
                        },
                        "headline": {
                            "type": "string",
                            "description": "한 문장 요약. 40자 안팎.",
                        },
                        "reading": {
                            "type": "string",
                            "description": (
                                "지표 → 정책 경로 → 시장 함의. 2~3문장. "
                                "반응이 통상 해석과 어긋나면 그 사실을 적는다."
                            ),
                        },
                    },
                    "required": ["id", "tone", "headline", "reading"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["briefs"],
        "additionalProperties": False,
    },
    "strict": True,
}


def _pct(before: float | None, after: float | None) -> float | None:
    if before in (None, 0) or after is None:
        return None
    return (after / before - 1.0) * 100.0


@dataclass(frozen=True)
class Reaction:
    """발표 전후 지수 움직임. **실측이다.**"""

    index_name: str
    before: float | None
    after: float | None
    change_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index_name,
            "before": self.before,
            "after": self.after,
            "change_pct": None if self.change_pct is None else round(self.change_pct, 3),
        }


def measure_reaction(
    store: Any, release: dict[str, Any], *, as_of: datetime
) -> Reaction | None:
    """발표 직전 종가 → 발표 다음 종가.

    발표 **당일** 종가를 쓰지 않는다. 미국 지표는 08:30 ET 에 나오고 그날
    종가에 반영되지만, 국장 지표는 장중에 나오는 것이 아니라 개장 전이라
    시장마다 기준이 달라진다. '발표 시각 이전 마지막 종가'와 '이후 첫 종가'로
    통일하면 두 시장을 같은 규칙으로 잰다.
    """
    entry = REACTION_INDEX.get(str(release.get("market") or ""))
    if entry is None:
        return None
    entity_id, index_name = entry

    scheduled = datetime.fromisoformat(str(release["scheduled_at"]))
    frame = store.get(
        INDICES, as_of=as_of, entity=[entity_id], lookback=30, columns=["close"]
    )
    if frame.empty:
        return None

    frame = frame.dropna(subset=["close"]).sort_values("valid_from")
    before_rows = frame[frame["valid_from"] < scheduled]
    after_rows = frame[frame["valid_from"] >= scheduled]
    if before_rows.empty or after_rows.empty:
        return None

    before = float(before_rows.iloc[-1]["close"])
    after = float(after_rows.iloc[0]["close"])
    return Reaction(index_name, before, after, _pct(before, after))


@dataclass
class MacroBrief:
    """해설기. 키가 없으면 해설 없이 숫자만 돌려준다."""

    store: Any
    clock: Clock
    api_key: str = ""
    model: str = MODEL
    client: Any = None
    failures: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, store: Any, clock: Clock, env: dict[str, str] | None = None) -> MacroBrief:
        source = env if env is not None else dict(os.environ)
        return cls(store=store, clock=clock, api_key=(source.get(KEY_ENV) or "").strip())

    def usable(self) -> bool:
        return bool(self.api_key) or self.client is not None

    # -- 숫자 -------------------------------------------------------------------

    @staticmethod
    def surprise(release: dict[str, Any]) -> dict[str, Any]:
        """직전값 대비 변화. **컨센서스 대비가 아니다** (모듈 docstring)."""
        actual, previous = release.get("actual"), release.get("previous")
        if actual is None or previous is None:
            return {"diff": None, "pct": None}
        diff = float(actual) - float(previous)
        return {
            "diff": round(diff, 4),
            "pct": None if previous in (0, None) else round(diff / float(previous) * 100, 3),
        }

    def fingerprint(self, release: dict[str, Any], reaction: Reaction | None) -> str:
        raw = json.dumps(
            {
                "e": release.get("entity_id"),
                "s": release.get("scheduled_at"),
                "a": release.get("actual"),
                "p": release.get("previous"),
                "r": None if reaction is None else reaction.change_pct,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # -- 해설 -------------------------------------------------------------------

    def explain(
        self, releases: list[dict[str, Any]], *, as_of: datetime
    ) -> list[dict[str, Any]]:
        """발표 목록 → 숫자 + 해설. 실패해도 숫자는 살아남는다."""
        self.failures = []
        enriched: list[dict[str, Any]] = []

        for release in releases:
            reaction = measure_reaction(self.store, release, as_of=as_of)
            enriched.append(
                {
                    **release,
                    "surprise": self.surprise(release),
                    "reaction": None if reaction is None else reaction.as_dict(),
                    "_fp": self.fingerprint(release, reaction),
                }
            )

        if not self.usable():
            self.failures.append(f"{KEY_ENV} 없음 — 숫자만 표시한다")
            return enriched

        pending: list[dict[str, Any]] = []
        for item in enriched:
            cached = self._cached(item, as_of=as_of)
            if cached is None:
                pending.append(item)
            else:
                item["brief"] = cached

        if pending:
            try:
                fresh = self._ask(pending[:MAX_ITEMS], as_of=as_of)
            except Exception as error:
                self.failures.append(f"해설 실패: {type(error).__name__}")
                fresh = {}
            for item in pending:
                brief = fresh.get(item["_fp"])
                if brief is not None:
                    item["brief"] = brief

        for item in enriched:
            item.pop("_fp", None)
        return enriched

    def _ask(
        self, items: list[dict[str, Any]], *, as_of: datetime
    ) -> dict[str, dict[str, Any]]:
        client = self.client or self._client()
        payload = [
            {
                "id": item["_fp"],
                "indicator": item.get("indicator"),
                "market": item.get("market"),
                "release_name": item.get("release_name"),
                "unit": item.get("unit"),
                "actual": item.get("actual"),
                "previous_as_expectation": item.get("previous"),
                "surprise_vs_previous": item["surprise"],
                "market_reaction": item.get("reaction"),
            }
            for item in items
        ]

        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "record_brief"},
            messages=[
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}
            ],
        )

        out: dict[str, dict[str, Any]] = {}
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            for entry in block.input.get("briefs", []):
                key = str(entry.get("id") or "")
                if not key:
                    continue
                out[key] = {
                    "tone": str(entry.get("tone") or "neutral"),
                    "headline": str(entry.get("headline") or ""),
                    "reading": str(entry.get("reading") or ""),
                }

        for item in items:
            if item["_fp"] in out:
                self._remember(item, out[item["_fp"]], as_of=as_of)
        return out

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    # -- 캐시 -------------------------------------------------------------------

    def _cached(self, item: dict[str, Any], *, as_of: datetime) -> dict[str, Any] | None:
        frame = self.store.get(
            AGENT_CACHE, as_of=as_of, entity=[str(item["entity_id"])], lookback=90
        )
        if frame.empty:
            return None
        hit = frame[
            (frame["agent"] == AGENT)
            & (frame["agent_version"] == VERSION)
            & (frame["features_hash"] == item["_fp"])
        ]
        if hit.empty:
            return None
        try:
            return dict(json.loads(str(hit.iloc[-1]["output"])))
        except (TypeError, ValueError):
            return None

    def _remember(
        self, item: dict[str, Any], brief: dict[str, Any], *, as_of: datetime
    ) -> None:
        run_id = f"{AGENT}-{as_of:%Y%m%dT%H%M%S}-{item['_fp']}"
        if self.store.ingest_run_recorded(AGENT_CACHE, run_id):
            return
        self.store.append(
            AGENT_CACHE,
            [
                {
                    "entity_id": str(item["entity_id"]),
                    "valid_from": as_of,
                    # 벽시계가 아니라 as_of (tables.py).
                    "observed_at": as_of,
                    "source": AGENT,
                    "agent": AGENT,
                    "agent_version": VERSION,
                    "features_hash": item["_fp"],
                    "output": json.dumps(brief, ensure_ascii=False),
                    "computed_at": self.clock.now(),
                }
            ],
            ingest_run_id=run_id,
        )
