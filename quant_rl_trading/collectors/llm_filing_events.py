"""LLM 공시 정보 추출 — 시행 L1 (docs/design/ai-full-stack.md §4). 원문 한 건 → 고정 스키마 사건 하나.

## 무엇을 하나

창고 `documents` 에 원문(`raw_path`)이 있는 국장 DART 공시 중 **사건형 유형**(공급계약·손익구조변동·
유상증자/사채·자기주식·잠정실적·최대주주변경)을 Claude 에게 읽히고, 답을 닫힌 스키마
(direction · magnitude · vs_prior · confidence · evidence)로 받아 `filing_events` 에 적는다.
학습은 없다 — 자유도는 프롬프트·스키마·모델을 상수로 못박는 것으로 줄인다(§2.1 규칙 1).

## 파서가 있는 유형은 LLM 을 부르지 않는다 (v3, 리드 결정 2026-09-28)

공급계약·해지(G13 `supply_contracts`)와 손익구조변동(G12 `pl_change`)은 결정론적 파서가 이미 숫자를 뽑는다 —
그 유형은 **대상이 아니고**, 피처는 그 표를 그대로 쓴다(여기로 옮겨 적지 않는다). 잠정실적은 G8 파서
(`dart_prelim.parse`)가 영업이익 당기·전년동기를 읽어 내는 공시(`prelim_earnings` 에 들어가는 것)를 빼고,
G8 밖(매출만 있는 월간 실적·서식이 다른 것)만 LLM 에게 준다. 값이 전부 "-" 인 공시는 읽을 것이 없어 부르지 않는다.
LLM 대상: 유상증자 · 전환/신주인수권부/교환사채 · 자기주식 취득·처분·소각·신탁 · 최대주주 변경 · G8 밖 잠정실적.

## 크기를 코드가 읽는 칸 (v4, 리드 결정 2026-09-28)

v3 표본에서 남은 두 오답은 둘 다 **코드로 읽히는 칸**이었다(잠정실적 매출 증감, 신탁 해지 계약금액). 그런 칸은
크기를 코드가 정하고(`code_magnitude`) LLM 은 방향·비교 기준·확신·근거만 적는다(`DIRECTION_TOOL`). 코드가 못 읽으면
지금처럼 LLM 이 크기도 읽는다. 어느 쪽이 정했는지는 행의 `magnitude_source`(code | llm)에 남는다.
잠정실적은 G8 파서(`dart_prelim.parse`)를 **그대로** 부른다 — 복제하지 않는다.

## 후보 칸 (v3)

유형마다 코드가 원문에서 **후보 줄**(칸 이름 + 뒤따르는 값 몇 줄)을 잘라 메시지 머리에 준다(`candidate_lines`).
v2 에서 남은 오답 둘이 모두 "원문에 있는 **다른** 숫자" 였다(보유현황 비율, 조달 목적 한 줄). 원문 전문은 그대로
주되(방향 판단에 맥락이 필요하다) 어느 칸을 읽을지를 코드가 먼저 좁힌다. 원문에 없는 두 값은 코드가 계산해
후보에 붙인다: 유상증자의 **발행주식 대비 비율**(신주 수 ÷ 증자전 발행주식총수)과 **조달 총액**(목적별 금액의 합).
이 값들은 숫자 존재 검사의 허용 목록에도 들어간다 — LLM 이 계산하면 거절, 코드가 계산해 준 값은 허용.

## 코드가 정하는 것, LLM 이 읽는 것 (v2, 2026-09-28 표본 10건 뒤)

- **event_type 은 제목으로 코드가 정한다**(`event_type_of`). v1 표본에서 LLM 이 제3자배정 유상증자를 사채로
  분류했다 — 제목에 이미 적힌 사실을 모델에게 다시 맞히게 할 이유가 없다. LLM 에게는 그 종류와 **허용 단위**를
  알려 주고, 방향·크기·비교 기준만 읽힌다.
- **숫자는 원문에 있어야 한다.** v1 의 "문자열 그대로 인용" 검사는 서식 차이(`(주)`·`천원`·`:`)로 첫 시도의 70% 를
  떨어뜨리면서도 지어낸 숫자는 막지 못했다(세전이익에서 지은 −700, 계산한 19.65%). v2 는 **magnitude 와 evidence 의
  숫자가 원문의 숫자 집합 안에 있는지**를 본다 — 서식은 통과, 지은 숫자·계산한 숫자는 거절.
- **단위별 범위**: 사건 종류마다 허용 단위가 있고(`UNITS_BY_EVENT`), 단위마다 범위가 있다(`RANGES` — 주식수를
  `pct_of_shares` 로 적은 1,970,000 같은 것을 잡는다).

## 정정 공시는 같은 사건이다

`[기재정정]` 은 새 사건이 아니라 **원공시의 정정**이다. 원공시를 찾으면(같은 종목·같은 원제목·원문에 적힌
최초 제출일) 그 원공시의 (valid_from, doc_id) 를 사건 키로 쓰고 **revision 을 올린 행**으로 적는다 — 창고의
정정본 선택이 최신 정정만 남긴다. 실제로 읽은 공시는 `source_doc_id` 에, 정정 여부는 `amended` 에 남는다.
원공시를 못 찾으면(최초 제출일을 못 읽었거나 같은 날 같은 제목이 여럿) 자기 doc_id 로 적고 `amended=True` 다 —
피처는 이 "고아 정정" 을 새 사건으로 세지 않는다(등록 초안). 정정이 원공시보다 먼저 추출됐으면 원공시는 적지 않는다
(최신 정정을 덮지 않게).

## 누수 — 가장 큰 함정

LLM 은 학습 시점까지의 세상을 안다. 2025년 공시를 오늘 읽히면 "이 회사가 뒤에 어떻게 됐는지" 가 답에
섞일 수 있다. 그래서:

- **observed_at = 추출 시각**(`clock.now()`). 공시 목록 관측 시각을 옮기지 않는다 — 원문 파서(G8·G12·G13)와
  반대 규칙이다. 과거 공시의 백필은 관측 시각이 오늘이라 **과거 as_of 조회에 한 행도 안 잡힌다**(테스트로 고정).
- **판정은 전방만**: 축적 시작일 이후 접수된 공시만 피처가 된다(`forward_only`). 정정은 원공시 접수일로 걸린다.

## agent_cache 의 시각 — `replay/cache.AgentCache` 를 쓰지 않는 이유

`AgentCache.put` 은 observed_at 을 키 시각(접수일)으로 적어 캐시 행이 **과거 as_of 에도 보이는 LLM 출력**이 된다
(리드 결정 2026-09-28, 불변식 3). 그래서 같은 `agent_cache` 표에 **observed_at = 추출 시각**으로 직접 적고,
찾을 때는 시각이 아니라 **키**(agent_version = 스키마+PROMPT_HASH · features_hash = 원문 지문+제목)로 찾는다.

## 모델과 결정론

운영 모델은 `MODEL`(Haiku 4.5, 온도 0) 상수다. 비교 실험용으로 `MODEL_PARAMS` 에 Sonnet 5 가 있다 — Sonnet 5 는
샘플링 인자(temperature)를 400 으로 거절하므로 **온도 없이** 부른다. 그 경우 결정론은 캐시가 준다: 같은 원문·같은
프롬프트면 캐시 키가 같고, 첫 답이 영구히 그 공시의 값이다(재호출이 없으니 두 번째 표본이 나올 길이 없다).
재현성의 단위가 "같은 입력 → 같은 출력" 이 아니라 "같은 입력 → **저장된** 출력" 인 셈이고, 판정은 저장된 행만 본다.

## 지키는 것

- **불변식 8**: 출력은 입력 피처일 뿐, 어떤 보상·판정 식에도 들어가지 않는다. `confidence` 는 가중에 쓰지 않는다.
- **Collector 만 수집한다**: Analyst 가 부르지 않는다.
- **예산**: `llm.filing_events_monthly_budget_usd` 와 `llm.monthly_budget_usd` 둘 다 본다. 키가 없으면 부르지 않는다.
- **스키마 검증**: 실패하면 사유를 붙여 **한 번만** 다시 묻고, 또 실패하면 `status='failed'` 행으로 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import pandas as pd

from quant_rl_trading.collectors import dart_documents as docs
from quant_rl_trading.replay.cache import CACHE_TABLE, features_hash
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.replay.events import canonical_json
from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import ConfigNotFound

logger = logging.getLogger(__name__)

TABLE = "filing_events"
USAGE = "llm_usage"
AGENT = "filing_events"
SOURCE = "llm-filing-events"
KEY_ENV = "ANTHROPIC_API_KEY"
BUDGET_KEY = "llm.filing_events_monthly_budget_usd"

# -- 고정 — 여기 무엇이든 바뀌면 prompt_hash 가 바뀌고, 다른 추출(다른 행)이 된다 ---------------

SCHEMA_VERSION = "filing-events-v4"
#: 운영 모델은 **상수**다(config 가 아니다). 축적 도중 모델이 바뀌면 같은 피처가 아니다.
MODEL = "claude-haiku-4-5"
#: 모델별 요청 인자. Haiku 4.5 는 온도 0 을 받는다. Sonnet 5 는 샘플링 인자를 거절하고(400), 도구 강제는 사고
#: (thinking)와 함께 못 쓰므로 사고를 끈다 — 결정론은 캐시로(모듈 docstring "모델과 결정론").
MODEL_PARAMS: dict[str, dict[str, Any]] = {
    "claude-haiku-4-5": {"temperature": 0.0},
    "claude-sonnet-5": {"thinking": {"type": "disabled"}},
}
MAX_TOKENS = 1024
#: 축적 시작일 — 등록 초안(docs/protocols/llm-filing-events-2026-10.md)의 값, 사용자 승인 2026-09-28.
#: 접수일(정정이면 **원공시** 접수일)이 이보다 앞선 사건은 판정에 안 쓰인다(`forward_only`). 임계치가 아니라
#: 등록 상수라 config 가 아니다 — 바꾸면 등록을 새로 한다.
FORWARD_START = datetime(2026, 9, 29, tzinfo=ZoneInfo("Asia/Seoul"))
#: 이보다 긴 원문은 **자르지 않고** 부르지 않는다(`too_long` 행). 30일 표본 최대 12,895자(유상증자).
MAX_INPUT_CHARS = 20_000
EVIDENCE_MAX = 200

EVENT_TYPES = (
    "supply_contract",        # 단일판매ㆍ공급계약 체결
    "contract_termination",   # 공급계약 해지
    "pl_change",              # 매출액또는손익구조 30%(15%) 이상 변동
    "prelim_earnings",        # 영업(잠정)실적
    "rights_offering",        # 유상증자 결정
    "convertible_issue",      # 전환사채·신주인수권부사채·교환사채 발행 결정
    "treasury_buy",           # 자기주식 취득 결정·취득 신탁계약 체결
    "treasury_trust_end",     # 자기주식취득 신탁계약 해지 — 매입 프로그램의 끝(처분도 소각도 아니다)
    "treasury_sell",          # 자기주식 처분 결정
    "treasury_cancel",        # 자기주식 소각 결정
    "control_change",         # 최대주주 변경
    "other",                  # 위 어디에도 안 맞는다
)
UNITS = ("pct_of_sales", "pct_of_shares", "pct_change", "krw", "none")
_SHARE_UNITS = ("pct_of_shares", "krw", "none")
#: 사건 종류마다 허용하는 크기 단위. LLM 에게 알려 주고, 검증도 한다.
UNITS_BY_EVENT: dict[str, tuple[str, ...]] = {
    "supply_contract": ("pct_of_sales", "krw", "none"),
    "contract_termination": ("pct_of_sales", "krw", "none"),
    "pl_change": ("pct_change", "none"),
    "prelim_earnings": ("pct_change", "none"),
    "rights_offering": _SHARE_UNITS,
    "convertible_issue": _SHARE_UNITS,
    "treasury_buy": _SHARE_UNITS,
    "treasury_trust_end": _SHARE_UNITS,
    "treasury_sell": _SHARE_UNITS,
    "treasury_cancel": _SHARE_UNITS,
    "control_change": ("none",),
    "other": UNITS,
}
#: 단위별 절댓값 범위 [하한, 상한]. 매출액 대비는 100% 를 넘을 수 있다(수주산업). 발행주식 대비는 증자 규모가
#: 기존 주식보다 클 수 있어 500% 까지 둔다 — 주식수(수십만~수백만)를 비율 칸에 적은 것은 여기서 걸린다.
#: 금액은 100만원 미만이면 단위를 잘못 읽은 것(천원·백만원 표의 숫자를 그대로 옮긴 것 등)으로 본다.
RANGES: dict[str, tuple[float, float]] = {
    "pct_of_sales": (0.0, 100_000.0),
    "pct_of_shares": (0.0, 500.0),
    "pct_change": (0.0, 1_000_000.0),
    "krw": (1_000_000.0, 1e16),
}
#: 금액이 원문 숫자와 맞는지 볼 때 허용하는 표 단위 배수 — 원·천원·백만원·억원.
KRW_SCALES = (1.0, 1e3, 1e6, 1e8)
VS_PRIOR = ("better", "worse", "similar", "none")
DIRECTIONS = (-1, 0, 1)

SYSTEM = """\
당신은 한국 상장사의 DART 공시 원문 **한 건**을 읽고 정해진 칸을 옮겨 적는다.
해설·전망·주가 예측을 하지 않는다. 메시지가 말하는 도구를 한 번 부른다.

메시지 머리에 **`크기(코드가 정함)`** 가 있으면 크기는 코드가 이미 읽었다 — record_filing_direction 을 불러
방향·비교 기준·확신·근거만 적는다. 없으면 record_filing_event 로 크기까지 적는다.

사건 종류와 허용 단위는 **코드가 제목으로 정해** 메시지 머리에 준다. 종류를 다시 판단하지 않는다.
그 아래 **후보 칸**은 코드가 원문에서 잘라 준 줄이다 — 크기는 먼저 후보 칸에서 고른다. `[코드 계산]` 표시가 붙은
값은 원문에 없지만 코드가 원문 숫자로 계산한 것이라 그대로 써도 된다.

## 반드시 지킬 것

1. **원문에 적힌 것만 쓴다.** 이 회사에 대해 기억하는 것(뒤에 일어난 일·주가·뉴스)을 쓰지 않는다.
2. **숫자를 만들지 않는다.** magnitude_value 는 원문에 적힌 숫자 하나를 그대로 옮긴다 — 나누기·빼기로 계산한
   숫자는 코드가 거절한다(원문의 숫자 목록과 대조한다). 원하는 칸이 원문에 없으면 허용 단위 중 다른 칸
   (금액 krw)을 쓰고, 그것도 없으면 null 과 "none" 이다.
3. **direction** — 이 사건이 **기존 주주에게** 좋은 일인가: +1 좋다 / 0 중립·판단 불가 / −1 나쁘다.
   주가 예측이 아니다. 흔한 해석(수주 +1, 해지 −1, 희석성 증자·사채 −1, 자기주식 취득·소각 +1, 신탁 해지 0)을
   따르되 원문의 조건(금액이 아주 작다, 목적이 채무상환이다, 적자가 줄었다 등)이 다르게 말하면 원문을 따른다.
4. **어느 칸을 읽나**
   - 정정 공시는 정정 **후** 값.
   - 유상증자: 후보의 `[코드 계산] 발행주식 대비(%)` 가 있으면 pct_of_shares. 없으면 `[코드 계산] 조달 총액(원)` → krw.
     둘 다 없으면 null·"none" — 자금조달 목적 **한 줄**(운영자금만, 타법인 증권취득자금만)은 총액이 아니다.
   - 사채: 발행할 주식의 `주식총수 대비 비율(%)` 이 적혀 있으면 pct_of_shares, 없으면 권면 총액(원) → krw.
   - 자기주식: **이번 결정의 대상**(취득·처분·소각 예정 주식)의 발행주식 대비 비율(%)이 적혀 있으면 pct_of_shares,
     없으면 **예정 금액(원)** → krw. `보유현황` 의 비율·주식수는 이번 사건의 크기가 아니다. 주식수는 크기로 쓰지 않는다.
   - 잠정실적: `영업이익` 의 **당해실적** 행 `전년동기대비 증감율(%)` → pct_change. 비어 있으면 `매출액` 당해실적 행의
     같은 칸. **누계실적·수주·전기대비 칸은 쓰지 않는다.** 둘 다 비어 있으면 null·"none", direction 0.
   - 최대주주 변경: 크기 없음(null·"none").
   퍼센트는 숫자만(15.3% → 15.3). 금액은 원문에 적힌 숫자 그대로(표 단위가 천원이면 천원 숫자 그대로 적는다).
5. **vs_prior** — 원문 **안에** 비교 기준(전년동기·직전사업연도·정정 전 값)이 있을 때만 better / worse / similar.
   없으면 "none". 시장 기대치(컨센서스)는 원문에 없으므로 쓰지 않는다.
6. **confidence** — 0~1. 칸이 비었거나 어느 칸인지 애매하면 낮게.
7. **evidence** — magnitude 를 읽은 칸의 이름과 숫자(200자 이내). 숫자는 원문 표기 그대로 적는다 —
   evidence 의 숫자도 원문 숫자 목록과 대조한다. magnitude 가 있으면 그 숫자가 evidence 에 들어 있어야 한다.
"""

#: 크기를 코드가 정한 공시에 쓰는 도구 — 크기 칸이 아예 없다(LLM 이 덮어쓸 길을 구조로 막는다).
DIRECTION_TOOL: dict[str, Any] = {
    "name": "record_filing_direction",
    "description": "크기는 코드가 이미 읽었다. 공시 한 건의 방향·비교 기준·확신·근거만 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "integer", "enum": list(DIRECTIONS),
                          "description": "기존 주주에게: +1 좋다 / 0 중립·판단 불가 / -1 나쁘다."},
            "vs_prior": {"type": "string", "enum": list(VS_PRIOR)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string", "maxLength": EVIDENCE_MAX,
                         "description": "방향을 판단한 원문 칸의 이름과 숫자, 원문 표기 그대로."},
        },
        "required": ["direction", "vs_prior", "confidence", "evidence"],
        "additionalProperties": False,
    },
}

TOOL: dict[str, Any] = {
    "name": "record_filing_event",
    "description": "공시 한 건에서 읽은 칸을 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {"type": "integer", "enum": list(DIRECTIONS),
                          "description": "기존 주주에게: +1 좋다 / 0 중립·판단 불가 / -1 나쁘다."},
            "magnitude_value": {"type": ["number", "null"],
                                "description": "원문에 적힌 숫자 하나를 그대로. 계산하지 않는다."},
            "magnitude_unit": {"type": "string", "enum": list(UNITS),
                               "description": "메시지 머리의 허용 단위 중 하나."},
            "vs_prior": {"type": "string", "enum": list(VS_PRIOR)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string", "maxLength": EVIDENCE_MAX,
                         "description": "읽은 칸의 이름과 숫자, 원문 표기 그대로."},
        },
        "required": ["direction", "magnitude_value", "magnitude_unit", "vs_prior", "confidence", "evidence"],
        "additionalProperties": False,
    },
}



# -- 대상 고르기 --------------------------------------------------------------------------------

#: `documents.doc_type` 중 LLM 대상. `contract`(G13)·`pl_change`(G12)는 파서가 있어 **빠진다**(모듈 docstring).
#: `earnings` 는 제목으로 잠정실적만 남기고, 실행 때 G8 파서가 읽는 것을 한 번 더 뺀다(`covered_by_parser`).
TARGET_TYPES = ("dilution", "buyback", "earnings")
#: 파서가 있는 사건 종류 — 제목이 다른 분류로 들어왔어도(예: `other`) 이 종류면 부르지 않는다.
PARSER_EVENTS = ("supply_contract", "contract_termination", "pl_change")
_PRELIM = re.compile(r"잠정|영업실적")
#: 분류와 무관하게 대상에 넣는 제목. 지금 `other` 로 분류돼 원문이 없다 — 수집 확장 전까지는 0건이다.
_CONTROL = re.compile(r"최대주주변경")
#: 사건이 아니라 **뒤처리** 보고. 결정 공시가 이미 사건을 알렸다(같은 사건을 두 번 세지 않는다).
_NOISE = re.compile(r"결과보고서|청약결과|발행결과|만기전|자회사의주요경영사항")
#: 정정 표지 — `[기재정정]`·`[첨부정정]` 등.
_AMEND = re.compile(r"^\s*\[[^\]]*정정\]")


def _compact(title: str) -> str:
    return str(title or "").replace(" ", "")


def is_target(doc_type: str, title: str) -> bool:
    compact = _compact(title)
    if _NOISE.search(compact):
        return False
    kind = event_type_of(title)
    if kind in PARSER_EVENTS:
        return False
    # 제목으로 정해지는 LLM 대상 — 문서 분류가 `other` 여도 넣는다(자사주 처분·소각은 `buyback` 바늘에 안 걸린다).
    # 다만 `other` 는 밤 원문 수집 대상이 아니라 원문이 없다 — 수집 확장 전까지는 사실상 0건이다.
    if kind in ("treasury_sell", "treasury_cancel", "control_change"):
        return True
    if doc_type == "earnings":
        return bool(_PRELIM.search(compact))
    return doc_type in TARGET_TYPES


def is_amendment(title: str) -> bool:
    return bool(_AMEND.match(str(title or "")))


#: 제목 뒤꼬리 — 같은 공시가 정정 때 붙이거나 뗀다(실측: 원공시 "…체결(자율공시)" → 정정 "[기재정정]…체결").
_SUFFIX = re.compile(r"\((?:자율공시|공정공시)\)$")


def base_title(title: str) -> str:
    """정정 표지와 (자율공시) 꼬리를 뗀 제목(공백 없이) — 원공시를 찾는 열쇠."""
    return _SUFFIX.sub("", _compact(_AMEND.sub("", str(title or ""))))


def event_type_of(title: str) -> str:
    """제목 → 사건 종류. **순서가 규칙이다** — 먼저 걸리는 것이 이긴다."""
    t = base_title(title)
    if "손익구조" in t:
        return "pl_change"
    if _PRELIM.search(t):
        return "prelim_earnings"
    if "자기주식" in t or "자사주" in t:
        # "자기주식취득 신탁계약 체결" 은 계약이 아니라 자사주다 — 공급계약보다 먼저 본다.
        if "신탁계약해지" in t:
            return "treasury_trust_end"
        if "소각" in t:
            return "treasury_cancel"
        if "처분" in t:
            return "treasury_sell"
        if "취득" in t:
            return "treasury_buy"
        return "other"
    # DART 의 자사주 소각 공시 제목은 "주식소각결정" 이다(자기주식 글자가 없다 — 최근 60일 91건, 2026-09-28 실측).
    # 사채 소각(기타경영사항 …사채소각결정의건)은 제목 머리가 달라 안 걸린다.
    if t.startswith("주식소각결정"):
        return "treasury_cancel"
    # 사채 소각(회사가 사들인 전환사채 등을 없앤다)은 발행이 아니다 — 아래 사채 바늘보다 먼저 뺀다.
    if "사채" in t and "소각" in t:
        return "other"
    if "공급계약" in t or "단일판매" in t:
        return "contract_termination" if "해지" in t else "supply_contract"
    if "유상증자" in t:
        return "rights_offering"
    if re.search(r"전환사채|신주인수권부사채|교환사채", t):
        return "convertible_issue"
    if _CONTROL.search(t):
        # 주식담보제공계약은 최대주주 **변경**이 아니라 담보 계약이다(리드 결정 2026-09-28 — 원문 수집도 뺀다).
        return "other" if "주식담보제공계약" in t else "control_change"
    return "other"


def covered_by_parser(event_type: str, title: str, text: str) -> str | None:
    """이 공시를 LLM 없이 다룰 수 있으면 그 이유. 잠정실적만 해당한다(나머지 파서 유형은 `is_target` 이 뺐다).

    G8 파서가 영업이익 당기·전년동기를 읽으면 `prelim_earnings` 가 그 공시를 가진다. 매출도 영업이익도 전부
    "-" 이면 읽을 숫자가 없다. 단위를 못 읽으면(파서 None) LLM 에게 준다 — 파서 밖이다.
    """
    if event_type != "prelim_earnings":
        return None
    from quant_rl_trading.collectors.dart_prelim import basis, parse

    parsed = parse(text)
    if parsed is None:
        return None
    if parsed.usable and basis(str(title)) is not None:
        return "G8 파서(prelim_earnings)가 읽는다"
    if parsed.sales_cur is None and parsed.op_cur is None:
        return "매출·영업이익 당해실적이 전부 비었다"
    return None


# -- 후보 칸 -----------------------------------------------------------------------------------

#: 유형별 후보 칸 이름(정규식, 공백 무시). 칸 이름 줄 + 뒤따르는 값 줄 몇 개를 한 줄로 잇는다.
_SHARE_LABELS = (r"발행주식총수", r"주식총수\s*대비")
CANDIDATE_LABELS: dict[str, tuple[str, ...]] = {
    "rights_offering": (r"신주의\s*종류와\s*수", r"증자전\s*발행주식총수", r"시설자금", r"영업양수자금", r"운영자금",
                        r"채무상환자금", r"타법인\s*증권\s*취득자금", r"기타자금", r"신주\s*발행가액", r"할인율",
                        r"증자방식"),
    "convertible_issue": (r"권면\(?전자등록\)?\s*총액", r"주식총수\s*대비", r"(?:전환|행사|교환)가액",
                          r"자금조달의\s*목적", r"시설자금", r"운영자금", r"채무상환자금", r"타법인\s*증권\s*취득자금"),
    "treasury_buy": (r"취득예정주식", r"취득예정금액", r"계약금액", r"취득목적", *_SHARE_LABELS),
    "treasury_trust_end": (r"계약금액", r"해지목적", *_SHARE_LABELS),
    "treasury_sell": (r"처분예정주식", r"처분예정금액", r"처분목적", *_SHARE_LABELS),
    "treasury_cancel": (r"소각할\s*주식", r"소각\s*예정\s*금액", r"소각예정금액", *_SHARE_LABELS),
    "control_change": (r"변경\s*전", r"변경\s*후", r"변경사유", r"소유주식"),
    "prelim_earnings": (r"^매출액$", r"^영업이익$", r"단위"),
}
#: 칸 이름 뒤에 붙여 읽는 줄 수와 후보 수 상한 — 메시지를 짧게 두려는 것이지 정확도 장치가 아니다.
CANDIDATE_TAIL = 4
CANDIDATE_MAX = 10
#: 칸 이름 줄의 최대 길이. 긴 줄은 칸 이름이 아니라 설명 문단이다(칸 이름을 문장 속에 인용한 것).
LABEL_MAX_CHARS = 40
CANDIDATE_MAX_CHARS = 160
_FUNDING = (r"시설자금", r"영업양수자금", r"운영자금", r"채무상환자금", r"타법인\s*증권\s*취득자금", r"기타자금")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _first_number(cells: list[str]) -> float | None:
    for cell in cells:
        token = cell.replace(",", "").strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return float(token)
    return None


def _after(lines: list[str], label: str, *, tail: int = 3) -> float | None:
    """칸 이름 **다음** 줄들에서 첫 숫자. 같은 이름이 여럿이면(정정 전·후) **마지막**을 쓴다."""
    pattern = re.compile(label)
    value = None
    for i, line in enumerate(lines):
        if pattern.search(line):
            found = _first_number(lines[i + 1:i + 1 + tail])
            if found is not None:
                value = found
    return value


def derived_values(event_type: str, text: str) -> dict[str, float]:
    """원문 숫자로 **코드가** 계산한 값 — 원문에 적혀 있지 않은 크기. 지금은 유상증자만."""
    if event_type != "rights_offering":
        return {}
    lines = _lines(text)
    out: dict[str, float] = {}
    new = _after(lines, r"신주의\s*종류와\s*수")
    before = _after(lines, r"증자전\s*발행주식총수")
    if new and before:
        out["발행주식 대비(%)"] = round(new / before * 100.0, 2)
    funds = [_after(lines, label, tail=1) for label in _FUNDING]
    spent = [f for f in funds if f]
    if spent:
        out["조달 총액(원)"] = float(sum(spent))
    return out


@dataclass(frozen=True)
class CodeMagnitude:
    """코드가 원문에서 읽은 크기."""

    value: float
    unit: str
    label: str


#: 코드가 크기를 읽는 규칙의 이름 — prompt_hash 에 들어간다(규칙이 바뀌면 다른 추출이다).
CODE_MAGNITUDE_RULES = {
    "prelim_earnings": "dart_prelim.parse: 영업이익(없으면 매출액) 당해실적 전년동기 대비 %, 전년동기 ≤ 0 이면 못 읽음",
    "treasury_trust_end": "'계약금액(원)' 칸 뒤 첫 숫자(해지 전 금액), 원 단위",
}


def code_magnitude(event_type: str, text: str) -> CodeMagnitude | None:
    """코드로 읽히는 칸이면 그 크기. 못 읽으면 None — 그때는 LLM 이 읽는다."""
    if event_type == "prelim_earnings":
        from quant_rl_trading.collectors.dart_prelim import parse

        parsed = parse(text)
        if parsed is None:
            return None
        for label, cur, base in (("영업이익", parsed.op_cur, parsed.op_base),
                                 ("매출액", parsed.sales_cur, parsed.sales_base)):
            if cur is not None and base is not None:
                if base <= 0:
                    return None   # 적자 기준의 증감률은 부호가 뒤집힌다 — 코드가 단정하지 않는다
                return CodeMagnitude(round((cur / base - 1.0) * 100.0, 2), "pct_change",
                                     f"{label} 당해실적 전년동기 대비(%)")
        return None
    if event_type == "treasury_trust_end":
        amount = _after(_lines(text), r"계약금액\s*\(원\)")
        if amount is None or amount <= 0:
            return None
        return CodeMagnitude(amount, "krw", "계약금액(원) 해지 전")
    return None


def validate_direction(raw: Any, text: str, *, derived: Iterable[float] = ()) -> dict[str, Any]:
    """`DIRECTION_TOOL` 답 → 방향·비교 기준·확신·근거. 크기 칸은 없다(코드가 정했다)."""
    if not isinstance(raw, dict):
        raise SchemaError("tool 입력이 객체가 아니다")
    required = set(DIRECTION_TOOL["input_schema"]["required"])
    missing = required - set(raw)
    extra = set(raw) - required
    if missing or extra:
        raise SchemaError(f"키 불일치 — 없음 {sorted(missing)} · 남음 {sorted(extra)}")
    direction = raw["direction"]
    if isinstance(direction, bool) or not isinstance(direction, (int, float)) or direction not in DIRECTIONS:
        raise SchemaError(f"direction 은 -1/0/1: {direction!r}")
    if raw["vs_prior"] not in VS_PRIOR:
        raise SchemaError(f"vs_prior 열거 밖: {raw['vs_prior']!r}")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise SchemaError(f"confidence 는 0~1: {confidence!r}")
    evidence = raw["evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise SchemaError("evidence 가 비었다")
    if len(evidence) > EVIDENCE_MAX:
        raise SchemaError(f"evidence 가 {EVIDENCE_MAX}자를 넘는다({len(evidence)})")
    pool = numbers(text) | {round(abs(float(v)), 6) for v in derived}
    strange = sorted(n for n in numbers(evidence) if n not in pool)
    if strange:
        raise SchemaError(f"evidence 의 숫자 {strange[:3]} 가 원문에 없다")
    return {"direction": int(direction), "vs_prior": str(raw["vs_prior"]),
            "confidence": float(confidence), "evidence": evidence.strip()}


def candidate_lines(event_type: str, text: str) -> list[str]:
    """유형별 후보 칸 — 칸 이름 줄 + 뒤 몇 줄을 " | " 로 이은 것. 같은 줄을 두 번 주지 않는다."""
    labels = CANDIDATE_LABELS.get(event_type, ())
    if not labels:
        return []
    lines = _lines(text)
    picked: list[str] = []
    used: set[int] = set()
    for label in labels:
        pattern = re.compile(label)
        for i, line in enumerate(lines):
            if i in used or len(line) > LABEL_MAX_CHARS or not pattern.search(line):
                continue
            used.add(i)
            tail = [cell[:LABEL_MAX_CHARS] for cell in lines[i + 1:i + 1 + CANDIDATE_TAIL]]
            row = " | ".join([line, *tail])[:CANDIDATE_MAX_CHARS]
            if row not in picked:
                picked.append(row)
            if len(picked) >= CANDIDATE_MAX:
                return picked
    return picked


def prompt_hash(model: str = MODEL) -> str:
    """모델·요청 인자·출력 상한·프롬프트·스키마·코드 규칙의 지문. 행과 캐시 키에 들어간다."""
    payload = json.dumps(
        {"model": model, "params": MODEL_PARAMS[model], "max_tokens": MAX_TOKENS,
         "max_input_chars": MAX_INPUT_CHARS, "schema_version": SCHEMA_VERSION,
         "system": SYSTEM, "tool": TOOL, "direction_tool": DIRECTION_TOOL, "code_rules": CODE_MAGNITUDE_RULES,
         "units": UNITS_BY_EVENT, "ranges": RANGES,
         "candidates": CANDIDATE_LABELS, "candidate_tail": CANDIDATE_TAIL, "candidate_max": CANDIDATE_MAX,
         "label_max": LABEL_MAX_CHARS, "candidate_chars": CANDIDATE_MAX_CHARS, "parser_events": PARSER_EVENTS},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


PROMPT_HASH = prompt_hash(MODEL)
AGENT_VERSION = f"{SCHEMA_VERSION}+{PROMPT_HASH}"


def targets(frame: pd.DataFrame, *, done: Iterable[str] = ()) -> pd.DataFrame:
    """원문이 있는 대상 공시의 최신 revision. 이미 이 프롬프트로 읽은 doc_id(`done`)는 뺀다.

    **오래된 것부터** 준다 — 원공시가 그 정정보다 먼저 처리돼야 정정이 revision 을 올린다.
    """
    if frame.empty:
        return frame
    latest = frame.sort_values("revision").groupby(
        ["entity_id", "valid_from", "doc_id"], as_index=False
    ).tail(1)
    path = latest["raw_path"].fillna("").astype(str)
    latest = latest[(path.str.len() > 1) & (path != "None")]
    keep = [is_target(t, n) for t, n in zip(latest["doc_type"], latest["title"], strict=True)]
    latest = latest[keep]
    skip = set(map(str, done))
    if skip:
        latest = latest[~latest["doc_id"].astype(str).isin(skip)]
    return latest.sort_values(["valid_from", "doc_id"])


# -- 정정 공시 → 원공시 ------------------------------------------------------------------------

#: 원문에 적힌 원공시 제출일. DART 정정신고의 두 서식: "정정대상 공시서류의 최초제출일 : 2026년 09월 15일" ·
#: "정정관련 공시서류제출일 | 2026-08-04".
_FIRST_FILED = re.compile(
    r"(?:최초\s*제출일|공시서류\s*제출일)[^0-9]{0,20}(\d{4})\s*[-년.]\s*(\d{1,2})\s*[-월.]\s*(\d{1,2})"
)
#: 원공시를 찾아 거슬러 보는 창(일).
ORIGINAL_LOOKBACK_DAYS = 800


def first_filed(text: str) -> date | None:
    match = _FIRST_FILED.search(text)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


#: 정정의 정정을 거슬러 오르는 최대 단계. 실측: 한 공급계약에 정정이 8번 붙은 종목이 있다(KR:091590).
MAX_CHAIN = 12


def find_original(candidates: pd.DataFrame, *, title: str, text: str,
                  read: Any = None) -> pd.Series | None:
    """같은 종목의 공시 목록에서 정정 대상 **원공시** 하나. **확실할 때만** — 아니면 None(고아 정정).

    정정 원문이 적은 "제출일" 은 원공시가 아니라 **직전 정정**의 날짜일 수 있다(단일판매 서식의
    "정정관련 공시서류제출일"). 그래서 그 날짜의 같은 제목 공시가 또 정정이면 **그 원문**을 읽어 한 단계 더
    거슬러 오른다. 단계마다 같은 날 같은 제목이 정확히 하나여야 한다 — 여럿이면 어느 것의 정정인지 모른다
    (짐작해서 묶지 않는다). 거슬러 오를 원문이 없으면 고아다.
    """
    if candidates.empty:
        return None
    reader = read or (lambda path: docs.read_text(Path(path)))
    latest = candidates.sort_values("revision").groupby(["valid_from", "doc_id"], as_index=False).tail(1)
    base = base_title(title)
    pool = latest[[base_title(t) == base for t in latest["title"]]]
    local = pool["valid_from"].dt.tz_convert("Asia/Seoul").dt.date
    current = text
    for _ in range(MAX_CHAIN):
        filed = first_filed(current)
        if filed is None:
            return None
        hit = pool[local == filed]
        if len(hit) != 1:
            return None
        found = hit.iloc[0]
        if not is_amendment(str(found["title"])):
            return found
        path = str(found.get("raw_path") or "")
        if len(path) <= 1 or path == "None":
            return None
        current = reader(path)
    return None


# -- 검증 --------------------------------------------------------------------------------------


class SchemaError(ValueError):
    """모델 답이 고정 스키마를 어겼다."""


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[float]:
    """글 안의 숫자(쉼표 제거, 절댓값). 부호는 원문 표기가 제각각('-'·'△'·'감소')이라 보지 않는다."""
    out: set[float] = set()
    for token in _NUMBER.findall(text):
        try:
            out.add(round(float(token.replace(",", "")), 6))
        except ValueError:
            continue
    return out


def _grounded(value: float, unit: str, pool: set[float]) -> bool:
    """값이 원문 숫자 중 하나인가. 금액은 표 단위 배수(천원·백만원·억원)까지 허용한다."""
    target = abs(value)
    if unit == "krw":
        return any(abs(target - n * scale) <= 0.5 for n in pool for scale in KRW_SCALES)
    return round(target, 6) in pool or any(abs(target - n) <= 1e-6 for n in pool)


def validate(raw: Any, text: str, *, event_type: str, derived: Iterable[float] = ()) -> dict[str, Any]:
    """모델 답 → 정규화된 사건. 어기면 SchemaError(사유). `derived` = 코드가 계산해 준 값(허용 목록에 더한다)."""
    if not isinstance(raw, dict):
        raise SchemaError("tool 입력이 객체가 아니다")
    required = set(TOOL["input_schema"]["required"])
    missing = required - set(raw)
    extra = set(raw) - required
    if missing or extra:
        raise SchemaError(f"키 불일치 — 없음 {sorted(missing)} · 남음 {sorted(extra)}")
    direction = raw["direction"]
    if isinstance(direction, bool) or not isinstance(direction, (int, float)) or direction not in DIRECTIONS:
        raise SchemaError(f"direction 은 -1/0/1: {direction!r}")
    unit = raw["magnitude_unit"]
    allowed = UNITS_BY_EVENT.get(event_type, UNITS)
    if unit not in allowed:
        raise SchemaError(f"{event_type} 의 허용 단위는 {list(allowed)} 이다: {unit!r}")
    value = raw["magnitude_value"]
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise SchemaError(f"magnitude_value 는 유한한 수 또는 null: {value!r}")
        value = float(value)
    if (value is None) != (unit == "none"):
        raise SchemaError(f"magnitude 값과 단위가 어긋난다: {value!r} · {unit!r}")
    pool = numbers(text) | {round(abs(float(v)), 6) for v in derived}
    if value is not None:
        low, high = RANGES[unit]
        if not low <= abs(value) <= high:
            raise SchemaError(f"{unit} 의 범위 [{low:g}, {high:g}] 밖이다: {value!r}")
        if not _grounded(value, unit, pool):
            raise SchemaError(f"magnitude {value!r} 가 원문 숫자에 없다(계산하거나 지어낸 숫자)")
    vs_prior = raw["vs_prior"]
    if vs_prior not in VS_PRIOR:
        raise SchemaError(f"vs_prior 열거 밖: {vs_prior!r}")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise SchemaError(f"confidence 는 0~1: {confidence!r}")
    evidence = raw["evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise SchemaError("evidence 가 비었다")
    if len(evidence) > EVIDENCE_MAX:
        raise SchemaError(f"evidence 가 {EVIDENCE_MAX}자를 넘는다({len(evidence)})")
    cited = numbers(evidence)
    strange = sorted(n for n in cited if n not in pool)
    if strange:
        raise SchemaError(f"evidence 의 숫자 {strange[:3]} 가 원문에 없다")
    if value is not None and not _grounded(value, unit, cited):
        raise SchemaError(f"evidence 에 magnitude {value!r} 의 숫자가 없다")
    # 사건 종류는 돌려주지 않는다 — 코드가 정한 값이 행에 들어간다(모델 답에는 애초에 없다).
    return {
        "direction": int(direction),
        "magnitude": value,
        "magnitude_unit": str(unit),
        "vs_prior": str(vs_prior),
        "confidence": float(confidence),
        "evidence": evidence.strip(),
    }


# -- 예산 --------------------------------------------------------------------------------------


def month_spend(store: Store, *, as_of: datetime, agent: str | None = AGENT) -> float:
    """이달 실측 지출($). `llm_usage` × `config.llm.pricing`. agent=None 이면 전체."""
    # 단가 표 되접기는 대시보드와 같은 함수를 쓴다 — 화면과 이 수집기가 다른 금액을 말하면 안 된다.
    from quant_rl_trading.dashboard.services.ai_review import _pricing_table, _row_cost_usd

    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    frame = store.get(USAGE, as_of=as_of, lookback=(as_of - month_start).days + 2)
    if frame.empty:
        return 0.0
    frame = frame[frame["valid_from"] >= pd.Timestamp(month_start)]
    if agent is not None:
        frame = frame[frame["agent"] == agent]
    pricing = _pricing_table(store, as_of=as_of)
    return float(sum(
        _row_cost_usd(row, pricing[str(row["model"])])
        for _, row in frame.iterrows() if str(row["model"]) in pricing
    ))


def call_cost(usage: Any, price: dict[str, float]) -> float:
    return (
        int(getattr(usage, "input_tokens", 0) or 0) * float(price.get("input_per_mtok", 0.0))
        + int(getattr(usage, "output_tokens", 0) or 0) * float(price.get("output_per_mtok", 0.0))
    ) / 1_000_000


@dataclass
class Budget:
    """두 예산. None 이면 **모른다** — 부르지 않는다."""

    agent_usd: float | None
    agent_spent: float
    total_usd: float | None
    total_spent: float
    price: dict[str, float]
    model: str = MODEL

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime, model: str = MODEL) -> Budget:
        from quant_rl_trading.dashboard.services.ai_review import _pricing_table

        try:
            agent_usd: float | None = float(store.config(BUDGET_KEY, as_of=as_of))
        except ConfigNotFound:
            agent_usd = None
        try:
            total_usd: float | None = float(store.config("llm.monthly_budget_usd", as_of=as_of))
        except ConfigNotFound:
            total_usd = None
        return cls(
            agent_usd=agent_usd,
            agent_spent=month_spend(store, as_of=as_of, agent=AGENT),
            total_usd=total_usd,
            total_spent=month_spend(store, as_of=as_of, agent=None),
            price=_pricing_table(store, as_of=as_of).get(model, {}),
            model=model,
        )

    def blocked(self) -> str | None:
        """부르면 안 되는 이유. 없으면 None."""
        if self.agent_usd is None or self.total_usd is None:
            return f"예산 키가 없다({BUDGET_KEY} · llm.monthly_budget_usd) — 설정 시딩 전에는 부르지 않는다"
        if not self.price:
            return f"{self.model} 단가가 config llm.pricing 에 없다 — 지출을 잴 수 없어 부르지 않는다"
        if self.agent_spent >= self.agent_usd:
            return f"추출 예산 소진 ${self.agent_spent:.4f} / ${self.agent_usd:.2f}"
        if self.total_spent >= self.total_usd:
            return f"전체 LLM 예산 소진 ${self.total_spent:.2f} / ${self.total_usd:.2f}"
        return None

    def charge(self, usd: float) -> None:
        self.agent_spent += usd
        self.total_spent += usd


# -- 추출기 ------------------------------------------------------------------------------------


@dataclass
class _Target:
    """한 번의 추출이 적을 사건 키. 정정이면 원공시의 키다."""

    doc_id: str
    valid_from: datetime
    amended: bool
    orphan: bool = False


@dataclass
class FilingEventExtractor:
    store: Store
    clock: Clock
    budget: Budget
    client: Any = None
    api_key: str = ""
    #: False 면 창고에 아무것도 안 적는다(표본 점검). 캐시도 안 읽는다 — 표본은 실제 호출을 본다.
    write: bool = True
    #: 운영은 MODEL 고정. 비교 실험에서만 바꾼다(prompt_hash 가 따라 바뀐다).
    model: str = MODEL
    calls: int = 0
    cache_hits: int = 0
    retries: int = 0
    #: 이미 행이 있는 사건 키라 건너뛴 원공시 수(재실행 또는 정정이 먼저 적힘).
    superseded: int = 0
    #: 파서가 다루거나 읽을 숫자가 없어 부르지 않은 공시 수(G8 잠정실적·빈 잠정실적).
    parser_covered: int = 0
    #: 이 날 전에 원공시가 접수된 정정은 부르지 않는다 — 판정에 안 쓰이는 호출이다(리드 결정 2026-09-28).
    #: None 이면 끈다(과거 공시로 채점하는 표본 점검만).
    forward_start: datetime | None = FORWARD_START
    #: 원공시가 축적 시작일 전이라 건너뛴 정정 수.
    skipped_pre_start: int = 0
    api_errors: list[str] = field(default_factory=list)
    stopped: str | None = None
    responses: list[dict[str, Any]] = field(default_factory=list)
    _revisions: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        return prompt_hash(self.model)

    @property
    def agent_version(self) -> str:
        return f"{SCHEMA_VERSION}+{self.prompt}"

    def run(self, todo: pd.DataFrame) -> list[dict[str, Any]]:
        """대상 공시를 차례로 읽는다. 예산에 막히면 **거기서 멈추고** 읽은 만큼 돌려준다."""
        rows: list[dict[str, Any]] = []
        for record in todo.to_dict(orient="records"):
            entity = str(record["entity_id"])
            text = docs.read_text(Path(str(record["raw_path"])))
            input_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            event_type = event_type_of(str(record["title"]))
            if covered_by_parser(event_type, str(record["title"]), text) is not None:
                self.parser_covered += 1
                continue
            key = self._target(record, text)
            if self._before_start(key, text):
                self.skipped_pre_start += 1
                continue
            revision = self._next_revision(entity, key)
            if not key.amended and revision > 0:
                # 이 사건 키에 이미 행이 있다 — 같은 공시를 다시 읽었거나(재실행), 이 공시의 정정이 먼저
                # 적혔다. 어느 쪽이든 원공시로 다시 적지 않는다(최신 정정을 덮지 않게).
                self.superseded += 1
                continue
            if len(text) > MAX_INPUT_CHARS:
                rows.append(self._row(record, key, revision, event_type, text, input_hash, status="too_long",
                                      error=f"{len(text):,}자 > {MAX_INPUT_CHARS:,}"))
                continue
            digest = features_hash({"input": input_hash, "title": str(record["title"])})
            hit = self._cache_get(entity, digest) if self.write else None
            if hit is not None:
                self.cache_hits += 1
                rows.append(self._row(record, key, revision, event_type, text, input_hash, **hit))
                continue
            reason = self.budget.blocked()
            if reason is not None:
                self.stopped = reason
                logger.warning("%s — %s 부터 멈춘다", reason, str(record["doc_id"]))
                break
            if not (self.api_key or self.client is not None):
                self.stopped = f"{KEY_ENV} 가 없다"
                break
            try:
                outcome = self._extract(record, text, event_type)
            except Exception as error:  # 네트워크·API 오류는 행을 남기지 않는다 — 다음 회차가 다시 묻는다
                self.api_errors.append(f"{record['doc_id']}: {type(error).__name__}: {error}")
                logger.warning("추출 호출 실패 %s: %s", record["doc_id"], error)
                continue
            if self.write:
                self._cache_put(entity, key.valid_from, digest, outcome)
            rows.append(self._row(record, key, revision, event_type, text, input_hash, **outcome))
        if rows and self.write:
            # 실행 id 에 이번 묶음의 지문을 넣는다 — 같은 시각(재생 시계)에 다른 묶음이 와도 부딪히지 않고,
            # 같은 묶음을 두 번 적지도 않는다.
            batch = features_hash(sorted(f"{r['source_doc_id']}:{r['input_hash']}" for r in rows))
            run_id = f"{AGENT}-{self.clock.now():%Y%m%dT%H%M%S%f}-{batch}"
            if not self.store.ingest_run_recorded(TABLE, run_id):
                self.store.append(TABLE, rows, ingest_run_id=run_id, source=SOURCE)
        return rows

    # -- 사건 키 --------------------------------------------------------------------------

    def _target(self, record: dict[str, Any], text: str) -> _Target:
        own = _Target(str(record["doc_id"]), pd.Timestamp(record["valid_from"]).to_pydatetime(), amended=False)
        if not is_amendment(str(record["title"])):
            return own
        now = self.clock.now()
        candidates = self.store.get(
            docs.DOCUMENTS, as_of=now, entity=str(record["entity_id"]), lookback=ORIGINAL_LOOKBACK_DAYS,
            columns=["entity_id", "valid_from", "doc_id", "title", "revision", "raw_path"],
        )
        original = find_original(candidates, title=str(record["title"]), text=text)
        if original is None:
            return _Target(own.doc_id, own.valid_from, amended=True, orphan=True)
        return _Target(str(original["doc_id"]), pd.Timestamp(original["valid_from"]).to_pydatetime(), amended=True)

    def _before_start(self, key: _Target, text: str) -> bool:
        """원공시가 축적 시작일 전인 정정인가. 고아 정정은 원문에 적힌 제출일로 본다(못 읽으면 모른다 → 부른다)."""
        if self.forward_start is None or not key.amended:
            return False
        if not key.orphan:
            return key.valid_from < self.forward_start
        filed = first_filed(text)
        return filed is not None and filed < self.forward_start.astimezone(ZoneInfo("Asia/Seoul")).date()

    def _next_revision(self, entity: str, key: _Target) -> int:
        """이 사건 키에 이미 있는 행(이 프롬프트)의 다음 revision. 이번 묶음 안의 것도 센다."""
        slot = (entity, key.doc_id)
        if slot not in self._revisions:
            frame = self.store.get(
                TABLE, as_of=self.clock.now(), entity=entity,
                lookback=max(1, (self.clock.now() - key.valid_from).days + 2),
                columns=["doc_id", "prompt_hash", "revision"],
            )
            if not frame.empty:
                frame = frame[(frame["doc_id"] == key.doc_id) & (frame["prompt_hash"] == self.prompt)]
            self._revisions[slot] = int(frame["revision"].max()) if not frame.empty else -1
        self._revisions[slot] += 1
        return self._revisions[slot]

    # -- 캐시 -----------------------------------------------------------------------------

    def _cache_get(self, entity: str, digest: str) -> dict[str, Any] | None:
        """키로 찾는다. as_of 는 **지금** — 과거 시각으로 부르는 경로가 없다."""
        frame = self.store.get(CACHE_TABLE, as_of=self.clock.now(), entity=entity)
        if frame.empty:
            return None
        hit = frame[(frame["agent"] == AGENT) & (frame["agent_version"] == self.agent_version)
                    & (frame["features_hash"] == digest)]
        if hit.empty:
            return None
        return dict(json.loads(hit.sort_values("observed_at").iloc[-1]["output"]))

    def _cache_put(self, entity: str, valid_from: datetime, digest: str, outcome: dict[str, Any]) -> None:
        run_id = f"{AGENT}-{entity}-{self.prompt}-{digest}"
        if self.store.ingest_run_recorded(CACHE_TABLE, run_id):
            return
        now = self.clock.now()
        self.store.append(CACHE_TABLE, [{
            "entity_id": entity,
            "valid_from": valid_from,
            # **추출 시각.** AgentCache 처럼 as_of(접수일)로 적으면 과거 as_of 에 LLM 출력이 보인다.
            "observed_at": now,
            "source": f"{AGENT}@{self.agent_version}",
            "agent": AGENT,
            "agent_version": self.agent_version,
            "features_hash": digest,
            "output": canonical_json(outcome),
            "computed_at": now,
        }], ingest_run_id=run_id)

    # -- 호출 -----------------------------------------------------------------------------

    def _extract(self, record: dict[str, Any], text: str, event_type: str) -> dict[str, Any]:
        """두 번까지 묻는다. 두 번째는 검증 사유를 붙여서. 그래도 틀리면 failed."""
        derived = derived_values(event_type, text)
        code = code_magnitude(event_type, text)
        tool = DIRECTION_TOOL if code is not None else TOOL
        allowed = [*derived.values(), *([code.value] if code is not None else [])]
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _user_message(record, text, event_type, derived, code)}
        ]
        error = ""
        for attempt in range(2):
            if attempt == 1:
                reason = self.budget.blocked()
                if reason is not None:
                    return {"status": "failed", "error": f"재시도 전 예산 막힘: {reason}; 첫 답: {error}"}
                self.retries += 1
            response = self._ask(messages, tool)
            raw = _tool_input(response)
            # 시도마다 날것의 답과 검증 결과를 남긴다 — 표본 점검이 "왜 재시도했나" 를 볼 수 있게.
            trace = {"doc_id": str(record["doc_id"]), "attempt": attempt, "raw": raw,
                     "usage": _usage_dict(response), "error": "", "model": self.model}
            self.responses.append(trace)
            try:
                if code is not None:
                    return {"status": "ok", "error": "", **validate_direction(raw, text, derived=allowed),
                            "magnitude": code.value, "magnitude_unit": code.unit, "magnitude_source": "code"}
                return {"status": "ok", "error": "", "magnitude_source": "llm",
                        **validate(raw, text, event_type=event_type, derived=allowed)}
            except SchemaError as failure:
                error = str(failure)
                trace["error"] = error
                messages = [
                    *messages,
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": _tool_use_id(response), "is_error": True,
                        "content": f"스키마 검증 실패: {error}. 규칙을 지켜 {tool['name']} 를 다시 부르라.",
                    }]},
                ] if _tool_use_id(response) else messages
        return {"status": "failed", "error": error}

    def _ask(self, messages: list[dict[str, Any]], tool: dict[str, Any] = TOOL) -> Any:
        client = self.client or self._client()
        response = client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=messages,
            **MODEL_PARAMS[self.model],
        )
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.budget.charge(call_cost(usage, self.budget.price))
            if self.write:
                self._record_usage(response)
        return response

    def _client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(api_key=self.api_key)

    def _record_usage(self, response: Any) -> None:
        usage = response.usage
        now = self.clock.now()
        request_id = str(getattr(response, "id", "") or "")
        run_id = f"{AGENT}-usage-{now:%Y%m%dT%H%M%S%f}-{request_id}-{self.calls}"
        if self.store.ingest_run_recorded(USAGE, run_id):
            return
        # 사용량의 시각은 **호출 시각**이다 — 달 예산이 이달 지출로 센다.
        self.store.append(USAGE, [{
            "entity_id": AGENT, "valid_from": now, "observed_at": now, "source": AGENT,
            "agent": AGENT, "agent_version": self.agent_version, "model": self.model,
            "request_id": request_id, "items": 1,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "computed_at": now,
        }], ingest_run_id=run_id)

    # -- 행 -------------------------------------------------------------------------------

    def _row(self, record: dict[str, Any], key: _Target, revision: int, event_type: str, text: str,
             input_hash: str, *, status: str, error: str = "", **event: Any) -> dict[str, Any]:
        ok = status == "ok"
        return {
            "entity_id": str(record["entity_id"]),
            # 사건 키 — 정정이면 **원공시**의 접수일·접수번호.
            "valid_from": key.valid_from,
            # **추출 시각.** 공시 목록 관측 시각이 아니다 — 모듈 docstring "누수".
            "observed_at": self.clock.now(),
            "source": SOURCE,
            "revision": revision,
            "market": "KR",
            "doc_id": key.doc_id,
            "source_doc_id": str(record["doc_id"]),
            "amended": key.amended,
            "doc_type": str(record["doc_type"]),
            "status": status,
            "event_type": event_type,
            "direction": int(event["direction"]) if ok else None,
            "magnitude": event.get("magnitude") if ok else None,
            "magnitude_unit": str(event.get("magnitude_unit") or "") if ok else "",
            # 크기를 누가 정했나 — code(`code_magnitude`) | llm. 실패·too_long 행은 빈 값.
            "magnitude_source": str(event.get("magnitude_source") or "") if ok else "",
            "vs_prior": str(event.get("vs_prior") or "") if ok else "",
            "confidence": float(event["confidence"]) if ok else None,
            "evidence": str(event.get("evidence") or "") if ok else "",
            "error": error,
            "model": self.model,
            "schema_version": SCHEMA_VERSION,
            "prompt_hash": self.prompt,
            "input_hash": input_hash,
            "input_chars": len(text),
        }


def _user_message(record: dict[str, Any], text: str, event_type: str,
                  derived: dict[str, float] | None = None, code: CodeMagnitude | None = None) -> str:
    # 날짜·종목코드는 주지 않는다 — 모델이 기억을 더듬을 단서를 줄인다(원문 안의 이름·날짜는 어쩔 수 없다).
    units = ", ".join(UNITS_BY_EVENT.get(event_type, UNITS))
    rows = [f"- {line}" for line in candidate_lines(event_type, text)]
    rows += [f"- [코드 계산] {name}: {value:,.2f}".rstrip("0").rstrip(".") for name, value in (derived or {}).items()]
    block = "\n".join(rows) if rows else "- (코드가 찾은 후보 없음 — 원문에서 찾는다)"
    if code is not None:
        head = (f"사건 종류(코드가 정함): {event_type}\n크기(코드가 정함): {code.label} = {code.value:,.2f} {code.unit}\n"
                f"도구: record_filing_direction (크기는 적지 않는다)\n")
    else:
        head = f"사건 종류(코드가 정함): {event_type}\n허용 단위: {units}\n도구: record_filing_event\n"
    return f"{head}공시 제목: {record['title']}\n\n후보 칸:\n{block}\n\n원문:\n{text}"


def _tool_input(response: Any) -> Any:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    return None


def _tool_use_id(response: Any) -> str:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            return str(getattr(block, "id", "") or "")
    return ""


def _usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


# -- 읽기 — 피처는 여기서만 ---------------------------------------------------------------------


def load(store: Store, *, as_of: datetime, lookback: int, prompt: str = PROMPT_HASH) -> pd.DataFrame:
    """as_of 에 **알 수 있었던** 추출(observed_at ≤ as_of), 이 프롬프트·status ok 만.

    사건 키마다 최신 revision(최신 정정)만 남는다 — 창고의 정정본 선택. 고아 정정(원공시를 못 찾은
    `amended` 이고 doc_id == source_doc_id)은 새 사건이 아니므로 뺀다. 캐시(agent_cache)는 읽지 않는다.
    """
    frame = store.get(TABLE, as_of=as_of, lookback=lookback, market="KR")
    if frame.empty:
        return frame
    frame = frame[(frame["prompt_hash"] == prompt) & (frame["status"] == "ok")]
    orphan = frame["amended"].fillna(False).astype(bool) & (frame["doc_id"] == frame["source_doc_id"])
    return frame[~orphan]


def forward_only(frame: pd.DataFrame, *, start: datetime) -> pd.DataFrame:
    """판정에 쓸 수 있는 행 — **축적 시작일 이후 접수된** 공시만(정정은 원공시 접수일로).

    observed_at 게이트만으로는 모자라다: 판정 시점(as_of=2027-01)에서는 2026-09 에 백필한 2025년 공시도
    observed_at ≤ as_of 라 보인다. 그 행은 모델이 결과를 알았을 수 있는 공시다. 접수일로 한 번 더 자른다.
    """
    if frame.empty:
        return frame
    return frame[frame["valid_from"] >= pd.Timestamp(start)]


__all__ = [
    "AGENT", "AGENT_VERSION", "BUDGET_KEY", "EVENT_TYPES", "FORWARD_START", "MODEL", "MODEL_PARAMS", "PROMPT_HASH",
    "SCHEMA_VERSION", "SYSTEM", "TABLE", "TOOL", "UNITS_BY_EVENT", "Budget", "FilingEventExtractor",
    "CodeMagnitude", "DIRECTION_TOOL", "SchemaError", "base_title", "candidate_lines", "code_magnitude", "covered_by_parser", "derived_values", "event_type_of", "find_original", "first_filed", "forward_only",
    "is_amendment", "is_target", "load", "month_spend", "numbers", "prompt_hash", "targets", "validate", "validate_direction",
]
