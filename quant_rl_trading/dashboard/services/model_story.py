"""학습 탭 ②③ — "지금 매매에 쓰이는 모델" 과 "과거 학습 내역".

사용자 요청(2026-09-29): 학습 탭에서 **지금 무엇이 어떻게 매매를 정하는지**, 그리고 **무엇을 해봤고 왜
안 됐는지**를 비전문가가 읽을 수 있게. 이 모듈은 두 가지를 한곳에 둔다.

- ``PIPELINE_STAGES`` — 매매 흐름 단계마다 쉬운 한 줄 설명과 "언제 채택됐나". **설명 문구는 여기 한 곳**에만 있다
  (화면·메일이 각자 문구를 들면 언젠가 서로 다른 말을 한다). 채택 기록은 README·설정 파일 주석·시행 문서에
  적힌 사실만 옮겼다 — 모르면 적지 않는다.
- 단계마다 보이는 **실제 값은 여기 적지 않는다.** 설정 키 이름만 들고, 값은 요청마다 ``store.config(key, as_of)``
  로 읽는다(불변식 10). 되감으면 그때의 값이 나온다(불변식 9).
- 과거 시행은 ``docs/trials-catalog.yaml`` (사람이 postmortem·시행 문서에서 옮겨 적은 요약)과 창고
  ``research_trials``(시행 도구가 판정 때 적는 대장)를 합친다. **여기서 판정을 새로 내리지 않는다** — 카탈로그의
  결과를 그대로, 대장에만 있는 줄은 대장 문구 그대로 보인다. 카탈로그는 판정 날짜 ≤ as_of 인 것만 나간다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from quant_rl_trading.dashboard.services import agent_health
from quant_rl_trading.store import ConfigNotFound, Store

KST = ZoneInfo("Asia/Seoul")

REPO_ROOT = Path(__file__).resolve().parents[3]
#: 사람이 쓴 시행 요약. 판정은 원문 문서에서 옮긴 것이다.
CATALOG_PATH = REPO_ROOT / "docs" / "trials-catalog.yaml"

#: 분류와 결과는 이 넷·여섯만. 카탈로그에 다른 말이 오면 그 줄은 버리지 않고 '기타' 로 보인다.
CATEGORIES: tuple[str, ...] = ("선정", "랭커", "RL", "노출", "집행", "재료")
RESULTS: tuple[str, ...] = ("채택", "기각", "보류", "진행중")

#: 시행 대장은 8월부터 쌓였다. 조회 창은 넉넉히 — 표가 작다(수십 행).
LEDGER_LOOKBACK_DAYS = 3650

#: 대장의 family → 화면 분류. 대장에만 있는 줄을 필터에 걸기 위한 것이고, 모르는 family 는 '기타'.
FAMILY_CATEGORY: dict[str, str] = {
    "ranker": "랭커", "selection": "선정", "portfolio": "선정", "exposure": "노출",
    "rl": "RL", "rl-config": "RL", "sources": "재료", "analyst": "재료",
    "ic-analyst": "재료", "ic-feature": "재료", "execution": "집행",
}


# --------------------------------------------------------------------------- ② 지금 매매에 쓰이는 모델

#: 매매 흐름. 순서가 곧 화면 순서다. `settings` 는 (설정 키, 사람이 읽는 이름, 시장) — 값은 config 에서.
#: `adopted` 는 (시행 이름, 날짜, 문서, 한 줄) — 출처: README "지금 — 2026-09-28" · config/quant_rl_trading.yaml 주석 ·
#: docs/protocols/*.md. 시행이 아니라 설정 변경이면 시행 이름 칸에 그렇다고 적는다.
PIPELINE_STAGES: list[dict[str, Any]] = [
    {
        "key": "collect",
        "title": "재료 수집",
        "plain": "매일 장이 끝나면 시세·수급·재무·공시·지수를 모은다. 판단은 모두 이 재료 위에서 한다.",
        "detail": "수집은 Collector 만 한다(Analyst 가 직접 모으지 않는다). 수집이 먼저, 판단은 그 뒤 — 순서가 바뀌면 판정이 조용히 0건이 된다.",
        "adopted": [],
        "settings": [],
    },
    {
        "key": "analysts",
        "title": "Analyst 점수",
        "plain": "재무·수급·차트·이벤트·위험 등 Analyst 여럿이 종목마다 점수를 낸다. 랭커가 이 점수들을 재료로 쓴다.",
        "detail": "새 Analyst 는 적중도(IC, 점수와 이후 수익의 순위 상관)가 합격선을 넘어야 가중치를 받는다. 넘기 전에는 관찰만 한다.",
        "adopted": [
            {"trial": "AT", "date": "2026-09-25", "doc": "docs/protocols/us-missing-fundamental-2026-09.md",
             "note": "미장만 — 재무 점수가 없는 종목을 0(중립)으로 계산(빈 칸 때문에 외국 발행사가 상위를 독차지하던 문제)"},
        ],
        "settings": [
            ("analyst.ic_threshold", "합격선(적중도 IC)", None),
            ("selector.missing_as_zero_us", "결측을 0 으로 두는 점수", "US"),
        ],
    },
    {
        "key": "ranker",
        "title": "랭커 (LightGBM)",
        "plain": "Analyst 점수 여섯을 입력으로 받아 '5일 뒤 누가 더 오를까' 의 순위를 매기는 기계학습 모델. 지금 매매 점수의 거의 전부다.",
        "detail": "정답을 수익률 그대로가 아니라 '그날 안에서의 순위'로 바꿔 학습한다(순위 목적). 같은 GBM 이 이 한 가지를 바꾸자 두 시장 모두에서 기존 최강 점수(재무)를 이겼다. 모델은 월말마다 한 벌씩 있고, 홀드아웃 금고(2026-07-01~) 때문에 2026-06-30 학습분에서 멈춰 있다.",
        "adopted": [
            {"trial": "L", "date": "2026-09-03", "doc": "docs/protocols/rank-objective-ranker-2026-09.md",
             "note": "순위 목적 GBM 이 국장·미장 모두 통과해 채택"},
        ],
        "settings": [],
    },
    {
        "key": "screen",
        "title": "살 수 있는 종목 걸러내기",
        "plain": "거래대금이 너무 적거나 상장한 지 얼마 안 됐거나 관리종목·불성실공시처럼 부실한 종목은 점수와 상관없이 먼저 뺀다.",
        "detail": "자본 규모에 맞춰 하루 거래대금 하한을 둔다 — 우리 주문이 시세를 움직일 만큼 작은 종목은 사고팔 때 비용이 알파를 먹는다. 빠진 종목과 사유는 선정 흔적에 한 줄씩 남는다.",
        "adopted": [],
        "settings": [],
    },
    {
        "key": "floors",
        "title": "하한 · 상한 — 나쁜 종목 빼기",
        "plain": "점수가 높아도 위험 점수가 아래쪽인 종목은 뺀다. 설정이 켜지면 시계열 예측(TTM)이나 기술적 지표(TA) 하위·상위도 한 번 더 뺀다.",
        "detail": "국장에서 일관되게 통한 것은 '오를 종목 고르기' 보다 '나쁜 종목 피하기' 였다. 하한은 점수가 아니라 제약이라 합성 점수에는 안 들어간다. 두 번째 하한·상한·결합 하한은 비율이 0 이거나 Analyst 가 비어 있으면 꺼져 있다.",
        "adopted": [],
        "settings": [
            ("selector.risk_floor_percentile", "위험 하위 잘라내는 비율", None),
            ("selector.floor_analyst", "첫 하한을 재는 Analyst", None),
            ("selector.extra_floor_analyst", "두 번째 하한 Analyst(비면 꺼짐)", None),
            ("selector.extra_floor_percentile", "두 번째 하한 비율", None),
            ("selector.ceiling_analyst", "상한 Analyst(비면 꺼짐)", None),
            ("selector.ceiling_percentile", "상한 비율(위쪽에서 빼는 몫)", None),
            ("selector.combo_floor_analyst", "결합 하한 Analyst(비면 꺼짐)", None),
            ("selector.combo_floor_percentile", "결합 하한 비율", None),
        ],
    },
    {
        "key": "verdicts",
        "title": "뉴스 매수 금지",
        "plain": "악재 뉴스가 걸린 종목은 며칠 동안 새로 사지 않는다. 이미 가진 것을 팔게 하지는 않는다 — 매수만 막는다.",
        "detail": "1단계는 키워드 규칙이 걸러내고, 2단계에서 LLM(Claude)이 그 기사를 읽고 오탐이면 풀어 준다. LLM 은 막은 것을 풀어 주기만 하고 새로 막지 않는다. 이 판정은 보상이나 점수에 들어가지 않는다(불변식 8).",
        "adopted": [],
        "settings": [],
    },
    {
        "key": "select",
        "title": "평활 · 완충 · 상위 선정",
        "plain": "랭커 점수를 며칠 치 평균처럼 부드럽게(평활) 한 뒤 상위 종목을 산다. 이미 가진 종목은 순위가 조금 밀려도(완충 구간 안이면) 팔지 않는다.",
        "detail": "점수가 매일 조금씩 흔들리는데 그대로 사고팔면 거래비용이 알파를 먹는다. 평활과 완충은 그 헛매매를 줄이는 장치다. 위험 하위 일정 비율은 먼저 걸러낸다.",
        "adopted": [
            {"trial": "N", "date": "2026-09-04", "doc": "docs/protocols/selection-smoothing-2026-09.md",
             "note": "평활 5일 + 완충 72 — 회전을 줄여 비용 후 수익을 지켰다(국장)"},
            {"trial": "R", "date": "2026-09-04", "doc": "docs/protocols/selection-q-r-2026-09.md",
             "note": "미장에 같은 평활을 넣는 안은 기각 — 미장은 평활 없음·완충 48 그대로"},
        ],
        "settings": [
            ("selector.n_candidates", "살 종목 수", None),
            ("ranker.smoothing_span", "평활(최근 N세션)", "KR"),
            ("ranker.smoothing_span_us", "평활(최근 N세션)", "US"),
            ("selector.exit_rank", "완충 — 이 순위 안이면 보유 유지", "KR"),
            ("selector.exit_rank_us", "완충 — 이 순위 안이면 보유 유지", "US"),
            ("selector.swap_min_z", "교체 문턱(z 차이, 0 = 끔)", None),
        ],
    },
    {
        "key": "rebalance",
        "title": "재조정 주기",
        "plain": "종목 교체는 매일 하지 않고 정해진 세션마다 한 번 한다. 그 사이엔 가진 종목을 그대로 든다(주식 비중 조절만 매일 따른다).",
        "detail": "매일 고르면 점수의 흔들림이 곧 매매가 된다. 10세션마다 고르자 원천 넷 평균 연수익이 +6.0% → +12.2%, 회전이 34 → 21 로 바뀌었다(시행 AO 문서).",
        "adopted": [
            {"trial": "AO", "date": "2026-09-24", "doc": "docs/protocols/rebalance-cadence-2026-10.md",
             "note": "국장 10세션마다(C10) 채택"},
            {"trial": "AU", "date": "2026-09-26", "doc": "docs/protocols/us-selection-chain-2026-09.md",
             "note": "미장도 매일 → 10세션(C10) 채택, 사용자 승인 9/26"},
        ],
        "settings": [
            ("selector.rebalance_every", "몇 세션마다 교체", "KR"),
            ("selector.rebalance_every_us", "몇 세션마다 교체", "US"),
            ("selector.rebalance_anchor", "주기를 세는 기준일", None),
        ],
    },
    {
        "key": "weights",
        "title": "비중 — 리스크 패리티",
        "plain": "고른 종목에 돈을 똑같이 나누지 않고, 각 종목이 포트폴리오 전체 흔들림에 비슷하게 기여하도록 나눈다. 덜 흔들리는 종목에 조금 더 싣는다.",
        "detail": "한 종목·한 섹터가 위험을 독차지하지 않게 상한을 둔다. 2026-04~06 하락장 비교에서 점수 비례 배분보다 수익·낙폭 둘 다 나았다(설정 파일 주석).",
        "adopted": [
            {"trial": "§7 비교(시행 아님)", "date": "2026-09-01", "doc": "docs/design/portfolio-construction.md",
             "note": "점수 비례 → 리스크 패리티, 사용자 확정"},
        ],
        "settings": [
            ("allocator.baseline", "배분 방식", None),
            ("allocator.max_position_weight", "한 종목 비중 상한", None),
            ("allocator.name_risk_cap", "한 종목 위험 기여 상한", None),
            ("allocator.sector_risk_cap", "한 섹터 위험 기여 상한", None),
            ("allocator.cash_buffer", "남겨 두는 현금", None),
        ],
    },
    {
        "key": "exposure",
        "title": "노출 — 시장 국면 규칙(V6)",
        "plain": "시장이 위기·약세 국면이면 주식 비중을 줄이고, 평소엔 다 들고 간다. 규칙은 '국면' 하나만 쓴다.",
        "detail": "추세·변동성 압축 축도 있었지만 낙폭을 한 자리도 못 바꾸고 수익만 깎아 껐다(시행 V). 위기 판단은 21세션 모멘텀이 −3% 아래일 때로 좁혔다(시행 R). 올릴 때는 며칠 확인하고, 내릴 때는 바로 내린다.",
        "adopted": [
            {"trial": "V (V6)", "date": "2026-09-18", "doc": "docs/protocols/exposure-axes-2026-09.md",
             "note": "노출 축 셋 중 국면만 남김"},
            {"trial": "R", "date": "2026-09-07", "doc": "docs/protocols/regime-crisis-rule-2026-09.md",
             "note": "위기 문턱을 모멘텀 −3% 로"},
        ],
        "settings": [
            ("exposure.regime_scale.bull", "주식 비중 배수 — 상승 국면", None),
            ("exposure.regime_scale.bear", "주식 비중 배수 — 약세 국면", None),
            ("exposure.regime_scale.crisis", "주식 비중 배수 — 위기 국면", None),
            ("exposure.crisis_momentum_floor", "위기 판단 모멘텀 문턱", None),
            ("exposure.regime_confirm_sessions", "올릴 때 확인하는 세션 수", None),
            ("exposure.below_trend", "추세 축 배수(1 = 꺼짐)", None),
            ("exposure.squeezed", "압축 축 배수(1 = 꺼짐)", None),
            ("exposure.source", "노출 결정 출처(없으면 규칙)", None),
        ],
    },
    {
        "key": "execute",
        "title": "집행 — 나눠서 주문",
        "plain": "주문을 한 번에 내지 않고 몇 조각으로 나눠 한 시간 간격으로 낸다. 개장 직후 호가가 얇을 때 한꺼번에 사면 비싸게 사기 때문이다.",
        "detail": "집행 코드 안에는 AI 가 없다(불변식 6) — 마지막 안전장치는 예측 가능해야 한다. 조각 수·간격을 AI(밴딧)가 고르는 안은 만들어 두었지만 꺼져 있다.",
        "adopted": [
            {"trial": "설정 변경(시행 아님)", "date": "2026-09-01", "doc": "config/quant_rl_trading.yaml",
             "note": "조각 간격 60초 → 3600초(한 시간) — 네 조각이 오전에 퍼진다"},
        ],
        "settings": [
            ("execution.slice_count", "주문 조각 수", None),
            ("execution.slice_interval_sec", "조각 간격(초)", None),
            ("execution.order_type", "주문 방식", None),
            ("execution.plan_source", "집행 계획 출처(rule = 규칙)", None),
        ],
    },
    {
        "key": "paper",
        "title": "모의계좌",
        "plain": "국장은 증권사 모의투자 계좌에 실제로 주문을 낸다(가상 돈). 미장은 증권사 모의투자가 해외를 막아 돈이 오가지 않는 시뮬레이션(shadow)으로 돈다.",
        "detail": "강화학습(RL) 정책은 지금 매매에 쓰이지 않는다 — 네 번의 시도가 규칙을 못 이겼다. 아래 'RL 정책' 칸이 비어 있으면 꺼져 있다는 뜻이다.",
        "adopted": [
            {"trial": "첫 가동", "date": "2026-08-28", "doc": "README.md", "note": "모의계좌 실주문 시작"},
        ],
        "settings": [
            ("risk.max_positions", "최대 보유 종목 수", None),
            ("risk.max_daily_loss", "하루 손실 한도", None),
            ("allocator.rl.checkpoint", "RL 정책(비어 있으면 꺼짐)", None),
        ],
    },
    {
        "key": "commentary",
        "title": "LLM 해설 — 매매 밖",
        "plain": "매일 Claude 가 결과를 읽고 해설을 쓴다. 해설일 뿐 매매에 들어가지 않는다 — 점수·비중·주문 어디에도 쓰이지 않는다.",
        "detail": "LLM 출력은 보상 함수에 들어가지 않는다(불변식 8). Claude 는 심판이 아니라 해설자다. 숫자는 코드가 내고, 말은 Claude 가 붙인다.",
        "adopted": [],
        "settings": [],
    },
]

#: 돈이 오가지 않는 병행 장부. 무엇을 비교하려는지 한 줄씩. 출처: 시행 문서의 shadow 절·README 운영표.
#: `started` 는 첫 세션 날짜 — as_of 이전에 시작한 것만 나간다.
SHADOW_TRACKS: list[dict[str, str]] = [
    {"name": "미장 shadow", "ledger": "data/_shadow", "started": "2026-09-02",
     "compares": "국장과 같은 방식(랭커 상위·10세션 재조정)을 미장에 — 달러 37만 달러로 시뮬레이션. 미장 지수 대비로 본다.",
     "doc": "README.md"},
    {"name": "Z2", "ledger": "data/_z2_shadow", "started": "2026-09-23",
     "compares": "코스피200 안에서만 고르고 유동시총 가중(한 종목 10% 상한) — 지금 국장 방식과 20세션 비교.",
     "doc": "docs/design/portfolio-construction.md"},
    {"name": "G1 미장", "ledger": "data/_g1us_shadow", "started": "2026-09-25",
     "compares": "미장 시총 상위 500 을 시총 가중으로 그대로 든다 — 'S&P 만큼 따라가나'. 기존 미장 shadow·SPY 와 비교.",
     "doc": "docs/protocols/us-index-minus-losers-2026-09.md"},
    {"name": "K200 HMM / V6", "ledger": "data/_k200hmm_shadow · data/_k200v6_shadow", "started": "2026-09-28",
     "compares": "같은 코스피200 포트에 노출 규칙만 다르다 — 학습된 국면 확률(HMM) vs 지금 규칙(V6). 20세션 뒤(10/27 경) 비교.",
     "doc": "docs/protocols/v2-hmm-exposure-2026-10.md"},
    {"name": "W72 / N24", "ledger": "data/_w72_shadow · data/_n24_shadow", "started": "2026-09-28",
     "compares": "지금 방식 그대로 폭만 72종목 vs 24종목. 기록만 하고 이 장부로 판정하지 않는다(11월 금고 심사 참고용).",
     "doc": "docs/protocols/breadth72-forward-2026-09.md"},
    # 시작일을 적지 않는다("auto") — 장부에 첫 NAV 가 생긴 날이 시작일이고, 그 전에는 줄이 안 나간다.
    # 수익은 회계가 적은 TWR 지수(nav_daily.index_value)끼리만 견준다 — 여기서 NAV 를 다시 계산하지 않는다.
    {"name": "BE2", "ledger": "data/_be2_shadow", "started": "auto",
     "compares": "마지막 모델 회차 채택 후보 BE2(트랜스포머 + GBM 순위 평균)를 얼린 모델로 — 지금 모의계좌와 규칙은 같고 "
                 "종목 점수만 BE2. 모의계좌와 같은 창 수익을 나란히 본다(체결은 시뮬레이션이라 비용 차이가 섞인다).",
     "doc": "docs/design/be2-shadow.md", "compare_ledger": "data/_paper", "compare_name": "모의계좌"},
    {"name": "지수+V6", "ledger": "data/_idxv6_shadow", "started": "auto",
     "compares": "KODEX200 한 종목을 100% 로, 노출만 지금 규칙(V6) 그대로 — 실자금 투입 관문 ② 의 '지수+V6' 대안을 계산값이 "
                 "아니라 실제 매매 기록으로. 모의계좌와 같은 정보 시점으로 결정한다(분배금은 안 받는다 — 그만큼 불리하게 적힌다).",
     "doc": "docs/design/portfolio-construction.md", "compare_ledger": "data/_paper", "compare_name": "모의계좌"},
    {"name": "P1-b′", "ledger": "data/_p1b_shadow", "started": "auto",
     "compares": "회전 줄이기 — 100종목·20세션 재조정·확실히 나을 때만 교체(θ1.0), 나머지는 지금 규칙 그대로. 지금 방식 24종목(N24)과 "
                 "같은 창 수익·회전을 나란히 본다. 기록만 — 판정은 11/23 금고 창이다.",
     "doc": "docs/protocols/p1b-prime-2026-10.md", "compare_ledger": "data/_n24_shadow", "compare_name": "N24"},
]


#: **후보 → 흐름 단계** — 매매 과정 탭의 '후보' 칩(dashboard.md §7-2). 그 단계를 바꾸려고 등록·대기 중인 시행 하나에 한 줄.
#: 새 후보가 생기면 **여기에 한 줄만 더한다**(다른 곳은 고치지 않는다). 칸:
#:   ``stage``  — 흐름도 단계 키(`pipeline_view.FLOW_KEYS`)  · ``change`` — 무엇을 바꾸나 한 줄
#:   ``doc``    — 등록 문서(해시는 화면이 파일에서 잰다 — 머리가 '고정' 일 때만)
#:   ``judge``  — 판정 일정 [{"label", "date"(YYYY-MM-DD 또는 None)}] — date > as_of 이고 대장 행이 없으면 "판정 전 — 결과 없음"
#:   ``ledger`` — 시행 대장 `research_trials` 의 entity_id(판정 도구가 적는 이름) · ``catalog`` — docs/trials-catalog.yaml id
#:   ``pair``   — shadow 장부 짝 순차 검정 라벨(`alpha_ir.PAIRS`) · ``switch`` — `tools/apply_switch.COMPONENTS` 구성 요소 이름
#: 결과는 옮기기만 한다 — 판정을 여기서 새로 내리지 않는다.
STAGE_CANDIDATES: list[dict[str, Any]] = [
    {"id": "BE2", "stage": "model", "title": "BE2 점수 모델",
     "change": "점수 모델을 랭커(LightGBM) → BE2(세트 트랜스포머 + GBM 순위 평균, FA 76열)로 바꾼다.",
     "doc": "docs/protocols/final-model-round-2026-10.md",
     "judge": [{"label": "금고 early(7/1~9/30)", "date": "2026-10-13"}, {"label": "금고 second 확인", "date": "2026-11-23"}],
     "ledger": ["final-model-round-2026-10:BE", "final-model-round-2026-10:BE2"], "catalog": "BE",
     "pair": "BE2 − N24", "switch": "BE2"},
    {"id": "TK", "stage": "model", "title": "TK — 기술적 지표 여섯을 랭커 입력에",
     "change": "점수 모델 입력에 꼬리 비대칭·pv_corr·왜도·FIP·12-1 모멘텀·거래대금 변동계수를 더한다.",
     "doc": "docs/protocols/ta-ranker-features-2026-10.md",
     "judge": [{"label": "금고 second(10/1~11/13)", "date": "2026-11-23"}],
     "ledger": ["ta-ranker-features-2026-10:TK"], "catalog": None, "pair": None, "switch": None},
    {"id": "TR", "stage": "model", "title": "TR — TTM 예측을 랭커 입력에",
     "change": "점수 모델 입력에 TTM 예측 백분위(하루 늦춤) 하나를 더한다.",
     "doc": "docs/protocols/tsfm-ranker-and-finetune-2026-10.md",
     "judge": [{"label": "패널 판정 뒤 금고 second", "date": "2026-11-23"}],
     "ledger": ["tsfm-ranker-and-finetune-2026-10:TR"], "catalog": None, "pair": None, "switch": None},
    {"id": "TF", "stage": "extra_floor", "title": "TF — TTM 패자 하한",
     "change": "위험 하한 뒤에 TTM 예측 하위 20% 를 한 번 더 뺀다.",
     "doc": "docs/protocols/vault-early-additions-2026-10.md",
     "judge": [{"label": "금고 early · 과거 금고", "date": "2026-10-13"}],
     "ledger": ["vault-early-additions-2026-10:TF"], "catalog": None, "pair": None, "switch": "TF"},
    {"id": "TB", "stage": "ceiling", "title": "TB — TTM 하한 + 칼날 상한",
     "change": "TTM 예측 하위 20% 와 상위 10%(떨어지는 칼날)를 함께 뺀다.",
     "doc": "docs/protocols/vault-early-additions-2026-10.md",
     "judge": [{"label": "금고 early · 과거 금고", "date": "2026-10-13"}],
     "ledger": ["vault-early-additions-2026-10:TB"], "catalog": None, "pair": None, "switch": "TB"},
    {"id": "TC", "stage": "combo_floor", "title": "TC — 점수·TTM 결합 하한",
     "change": "(합성 점수 백분위 + TTM 백분위)/2 하위 10% 를 뺀다.",
     "doc": "docs/protocols/vault-early-additions-2026-10.md",
     "judge": [{"label": "금고 early", "date": "2026-10-13"}],
     "ledger": ["vault-early-additions-2026-10:TC"], "catalog": None, "pair": None, "switch": "TC"},
    {"id": "TG", "stage": "extra_floor", "title": "TG — 기술적 합성(TA) 하한",
     "change": "두 번째 하한 자리를 TA 합성 점수 하위 20% 로(실전이 TTM 이면 'TTM 대신 TA', 비면 'TA 더하기').",
     "doc": "docs/protocols/ta-floor-2026-10.md",
     "judge": [{"label": "과거 금고", "date": "2026-10-13"}, {"label": "금고 second", "date": "2026-11-23"}],
     "ledger": ["ta-floor-2026-10:TG"], "catalog": None, "pair": "TG − 실전 그림자", "switch": None},
    {"id": "TT", "stage": "extra_floor", "title": "TT — TTM 국장 미세조정",
     "change": "TTM 을 국장 종가로 조금 더 가르쳐(머리만) 패자 판별을 낫게 한다.",
     "doc": "docs/protocols/tsfm-ranker-and-finetune-2026-10.md",
     "judge": [{"label": "패널 판정 뒤 금고 second", "date": "2026-11-23"}],
     "ledger": ["tsfm-ranker-and-finetune-2026-10:TT"], "catalog": None, "pair": None, "switch": None},
    {"id": "IX-T", "stage": "risk_floor", "title": "IX-T — 지수 − TTM 패자",
     "change": "코스피200 을 유동시총으로 들되 TTM 예측 하위 10% 만 뺀다(전방 장부).",
     "doc": "docs/protocols/ixt-tsfm-losers-2026-10.md",
     "judge": [{"label": "전방 약 250세션", "date": None}],
     "ledger": ["ixt-tsfm-losers-2026-10:IX-T"], "catalog": None, "pair": "IX-T − IX0", "switch": None},
    {"id": "IX", "stage": "risk_floor", "title": "IX — 지수 − 랭커 패자",
     "change": "코스피200 을 유동시총으로 들되 랭커 하위 10% 만 뺀다(전방 장부).",
     "doc": "docs/protocols/ix-index-minus-losers-2026-10.md",
     "judge": [{"label": "전방 약 250세션", "date": None}],
     "ledger": ["ix-index-minus-losers-2026-10:IX"], "catalog": None, "pair": "IX − IX0", "switch": None},
    {"id": "AQ", "stage": "select", "title": "AQ — 72종목",
     "change": "상위 24 → 72종목(완충 216)으로 넓혀 운에 덜 흔들리게 한다.",
     "doc": "docs/protocols/breadth72-forward-2026-09.md",
     "judge": [{"label": "금고 early", "date": "2026-10-13"}],
     "ledger": ["breadth72-forward-2026-09:AQ"], "catalog": None, "pair": "W72 − N24", "switch": "AQ"},
    {"id": "P1B2", "stage": "cadence", "title": "P1-b″ — 회전 줄이기",
     "change": "100종목 · 완충 500 · 20세션 재조정 · 확실히 나을 때만 교체(θ 1.0).",
     "doc": "docs/protocols/vault-early-additions-2026-10.md",
     "judge": [{"label": "금고 early", "date": "2026-10-13"}],
     "ledger": ["vault-early-additions-2026-10:P1B2"], "catalog": None, "pair": None, "switch": "P1B2"},
    {"id": "P1B", "stage": "cadence", "title": "P1-b′ — 회전 줄이기(전방)",
     "change": "P1-b″ 와 같은 구성을 금고 second 창에서 한 번 더(전방 shadow 장부 기록).",
     "doc": "docs/protocols/p1b-prime-2026-10.md",
     "judge": [{"label": "금고 second", "date": "2026-11-23"}],
     "ledger": ["p1b-prime-2026-10:P1B"], "catalog": None, "pair": "P1-b′ − N24", "switch": None},
    {"id": "BB", "stage": "exposure", "title": "HMM 국면 확률 노출",
     "change": "노출 배수를 규칙 국면(V6) 대신 HMM 이 추정한 국면 확률로 정한다(코스피200 장부 둘로 비교).",
     "doc": "docs/protocols/v2-hmm-exposure-2026-10.md",
     "judge": [{"label": "K200 HMM vs V6 장부 20세션 비교", "date": "2026-10-27"}],
     "ledger": ["v2-hmm-exposure-2026-10:BB"], "catalog": "BB", "pair": None, "switch": None},
    {"id": "R0", "stage": "exposure", "title": "R0 — 밴딧 노출",
     "change": "맥락적 밴딧이 노출 배수를 고른다(v2 첫 학습 노출).",
     "doc": "docs/protocols/v2-bandit-exposure-2026-10.md",
     "judge": [{"label": "사용자 승인 뒤", "date": None}],
     "ledger": ["v2-bandit-exposure-2026-10:R0"], "catalog": None, "pair": None, "switch": None},
]


def _book_index(root: Path, as_of: datetime) -> pd.Series:
    """장부의 TWR 지수(국장 원화, 세션 날짜 → index_value). 장부가 없으면 빈 계열 — 여기서 NAV 를 계산하지 않는다."""
    from quant_rl_trading.accounting import ledger as ledger_module

    if not (root / "curated" / "nav_daily").is_dir():
        return pd.Series(dtype=float)
    try:
        nav = Store(root=root).get("nav_daily", as_of=as_of, entity=ledger_module.ACCOUNT, lookback=400,
                                   columns=["index_value"])
    except Exception:  # noqa: BLE001 — 병행 장부 하나가 깨져도 학습 탭 전체가 죽지 않는다
        return pd.Series(dtype=float)
    if nav.empty:
        return pd.Series(dtype=float)
    days = pd.to_datetime(nav["valid_from"]).dt.tz_convert(KST).dt.date
    return nav.assign(day=days).sort_values("valid_from").groupby("day")["index_value"].last().astype(float)


def track_returns(root: Path, track: dict[str, str], as_of: datetime) -> dict[str, Any] | None:
    """병행 장부의 첫 세션부터 as_of 까지 수익과, 같은 창의 비교 장부 수익. 장부가 아직 없으면 None."""
    book = _book_index(root / Path(track["ledger"]).name, as_of)
    if book.empty:
        return None
    since = book.index[0]

    def total(series: pd.Series) -> float | None:
        return float(series.iloc[-1] / series.iloc[0] - 1.0) if len(series) >= 2 and series.iloc[0] else None

    out: dict[str, Any] = {"since": since.isoformat(), "sessions": int(len(book)), "book": total(book),
                           "compare": None, "compare_name": track.get("compare_name")}
    if track.get("compare_ledger"):
        other = _book_index(root / Path(track["compare_ledger"]).name, as_of)
        out["compare"] = total(other[other.index >= since])
    return out


def _kst_day(as_of: datetime) -> date:
    stamp = pd.Timestamp(as_of)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp
    return stamp.tz_convert(KST).date()


def _display(value: Any) -> str:
    """설정 값을 사람이 읽는 글자로. 값을 **바꾸지 않는다** — 모양만."""
    if value is None:
        return "—"
    if isinstance(value, dict):
        return " · ".join(f"{k} {_display(v)}" for k, v in value.items()) if value else "(없음)"
    if isinstance(value, (list, tuple)):
        return ", ".join(_display(v) for v in value) if value else "(없음)"
    if isinstance(value, str):
        return value if value else "(비어 있음)"
    if isinstance(value, bool):
        return "켬" if value else "끔"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _setting(store: Store, key: str, label: str, market: str | None, as_of: datetime) -> dict[str, Any]:
    """설정 하나. 창고에 없으면 값을 지어내지 않고 ``found: false`` 로 보낸다."""
    try:
        value = store.config(key, as_of=as_of)
    except ConfigNotFound:
        return {"key": key, "label": label, "market": market, "found": False, "value": None, "display": "설정 없음"}
    if isinstance(value, (pd.Timestamp, datetime, date)):
        value = str(value)
    return {"key": key, "label": label, "market": market, "found": True, "value": value, "display": _display(value)}


def _ranker_model(models_root: Path | None, as_of: datetime) -> dict[str, Any] | None:
    """as_of 에 실전이 집을 랭커 모델 한 벌(사이드카 JSON) — `analysts.ranker.usable_model` 과 같은 규칙.

    파일 메타는 창고가 아니라 모델 폴더에서 읽는다(연구 작업 목록과 같은 이유). 그래도 **as_of 로 거른다** —
    `usable_from ≤ as_of` 인 것만. 폴더가 없으면 None(모름)."""
    if models_root is None:
        return None
    from quant_rl_trading.analysts.ranker import model_dir, usable_model

    folder = model_dir(models_root)
    if not folder.is_dir():
        return None
    model = usable_model(models_root, as_of=datetime.combine(_kst_day(as_of), datetime.min.time()))
    if model is None:
        return None
    sidecar = model.path.with_name(model.path.name[: -len(".txt")] + ".json")
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    day = _kst_day(as_of)
    usable = 0
    for path in folder.glob(f"{model.version}-*.json"):
        try:
            if date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["usable_from"]) <= day:
                usable += 1
        except (OSError, ValueError, KeyError):
            continue
    gain = meta.get("gain") or {}
    return {
        "file": model.path.name,
        "version": model.version,
        "trained_through": model.trained_through.isoformat(),
        "usable_from": model.usable_from.isoformat(),
        "features": list(model.features),
        "rows": model.rows,
        "rounds": meta.get("rounds"),
        "objective": (meta.get("params") or {}).get("objective"),
        "protocol_hash": meta.get("protocol_hash"),
        # 입력별 기여(gain) — 큰 것부터. 모델이 무엇에 기대는지 한눈에.
        "gain": sorted(({"feature": k, "share": float(v)} for k, v in gain.items()), key=lambda g: -g["share"]),
        "models_available": usable,
    }


#: 랭커 주간 IC 추이에 실을 최근 측정 수(시장마다).
RANKER_HISTORY_POINTS = 8


def _ranker_markets(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """시장별 랭커의 최근 IC·가중치·측정 시각 — `analyst_weights`(주간 IC 측정 `--save` 가 적는다)."""
    frame = store.get(agent_health.WEIGHTS, as_of=as_of, lookback=max(lookback, 120))
    if frame.empty or "entity_id" not in frame.columns:
        return []
    frame = frame[frame["entity_id"].astype(str) == "ranker"].sort_values("valid_from")
    out = []
    for market, part in frame.groupby("market", sort=True):
        last = part.iloc[-1]
        tail = part.tail(RANKER_HISTORY_POINTS)
        out.append({
            "market": str(market),
            "ic": None if pd.isna(last.get("ic")) else float(last["ic"]),
            "ic_t": None if "ic_t" not in part.columns or pd.isna(last.get("ic_t")) else float(last["ic_t"]),
            "weight": None if pd.isna(last.get("weight")) else float(last["weight"]),
            "measured_at": str(last["valid_from"]),
            "history": [{"at": str(r["valid_from"]), "ic": None if pd.isna(r["ic"]) else float(r["ic"])}
                        for _, r in tail.iterrows()],
        })
    return out


def live_models(store: Store, *, as_of: datetime, lookback: int = 90,
                models_root: Path | None = None) -> dict[str, Any]:
    """② 지금 매매에 쓰이는 모델 — 흐름 단계(설명 + 채택 기록 + 설정 실제 값) · 랭커의 살아 있는 사실 · 병행 트랙."""
    day = _kst_day(as_of)
    stages = []
    for stage in PIPELINE_STAGES:
        stages.append({
            "key": stage["key"], "title": stage["title"], "plain": stage["plain"], "detail": stage["detail"],
            # 채택이 as_of 뒤면 그때는 아직 채택 전이다 — 보이지 않는다.
            "adopted": [dict(a) for a in stage["adopted"] if date.fromisoformat(a["date"]) <= day],
            "settings": [_setting(store, key, label, market, as_of) for key, label, market in stage["settings"]],
        })
    threshold = _setting(store, "analyst.ic_threshold", "합격선", None, as_of)
    return {
        "stages": stages,
        "ranker": {
            "markets": _ranker_markets(store, as_of=as_of, lookback=lookback),
            "model": _ranker_model(models_root, as_of),
            "threshold": threshold["value"] if threshold["found"] else None,
        },
        "tracks": _tracks(Path(models_root) if models_root is not None else Path(store.root), as_of, day),
    }


def _tracks(root: Path, as_of: datetime, day: date) -> list[dict[str, Any]]:
    """as_of 에 돌던 병행 장부. 시작일이 "auto" 인 장부는 첫 NAV 가 있어야 나가고, 수익 비교를 같이 싣는다."""
    out: list[dict[str, Any]] = []
    for track in SHADOW_TRACKS:
        if track["started"] == "auto":
            returns = track_returns(root, track, as_of)
            if returns is None:
                continue
            out.append({**track, "started": returns["since"], "returns": returns})
        elif date.fromisoformat(track["started"]) <= day:
            out.append(dict(track))
    return out


# --------------------------------------------------------------------------- ③ 과거 학습 내역


@dataclass
class Catalog:
    lessons: list[dict[str, Any]] = field(default_factory=list)
    trials: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    """카탈로그 YAML 을 읽는다. 날짜를 못 읽는 줄은 **버리고 이유를 남긴다**(화면에 '읽지 못한 줄 N' 으로)."""
    catalog = Catalog()
    if not path.is_file():
        catalog.problems.append(f"{path.name} 없음")
        return catalog
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        catalog.problems.append(f"{path.name} 을 읽지 못했다: {error}")
        return catalog
    for item in raw.get("lessons") or []:
        if isinstance(item, dict) and item.get("text"):
            catalog.lessons.append({"text": str(item["text"]), "source": str(item.get("source") or ""),
                                    "date": _as_date(item.get("date"))})
    for index, item in enumerate(raw.get("trials") or []):
        if not isinstance(item, dict) or not item.get("id"):
            catalog.problems.append(f"{index}번째 줄: id 없음")
            continue
        day = _as_date(item.get("date"))
        if day is None:
            catalog.problems.append(f"{item['id']}: 날짜를 읽지 못함")
            continue
        category = str(item.get("category") or "")
        result = str(item.get("result") or "")
        ledger = item.get("ledger") or []
        catalog.trials.append({
            "id": str(item["id"]),
            "date": day,
            "category": category if category in CATEGORIES else "기타",
            "title": str(item.get("title") or ""),
            "what": str(item.get("what") or ""),
            "result": result if result in RESULTS else "기타",
            "why": str(item.get("why") or ""),
            "doc": str(item.get("doc") or ""),
            "ledger_ids": [str(x) for x in (ledger if isinstance(ledger, list) else [ledger])],
        })
    return catalog


def _ledger_rows(store: Store, as_of: datetime) -> list[dict[str, Any]]:
    """시행 대장 — 같은 시행의 마지막 줄만(다시 돌린 판정은 새 줄로 쌓인다, append-only)."""
    frame = store.get("research_trials", as_of=as_of, lookback=LEDGER_LOOKBACK_DAYS)
    if frame.empty:
        return []
    frame = frame.sort_values("valid_from").groupby("entity_id", sort=False).tail(1)
    rows = []
    for _, row in frame.iterrows():
        at = pd.Timestamp(row["valid_from"])
        at = at.tz_localize("UTC") if at.tzinfo is None else at
        rows.append({
            "entity_id": str(row["entity_id"]),
            "family": str(row.get("family") or ""),
            "at": at.isoformat(),
            "day": at.tz_convert(KST).date(),
            "detail": str(row.get("detail") or ""),
            "protocol_hash": str(row.get("protocol_hash") or ""),
        })
    return rows


def _matches(entry: dict[str, Any], entity_id: str) -> bool:
    """카탈로그 줄 ↔ 대장 줄. 카탈로그가 적은 id 가 우선이고, 없으면 '문서이름:시행' 규약으로 맞춘다."""
    if entity_id in entry["ledger_ids"]:
        return True
    stem, _, trial = entity_id.partition(":")
    return bool(trial) and trial == entry["id"] and bool(entry["doc"]) and Path(entry["doc"]).stem == stem


#: 대장 판정 문구가 이 말로 시작하면 그 말을 결과로 보인다. 그 밖은 결과 칸을 비운다(판정을 짐작하지 않는다).
_LEDGER_LEADS = ("채택", "기각", "보류")

#: 대장 문구는 한 줄에 판정 근거를 몰아 적어 길다. 화면 한 칸에 실을 만큼만.
LEDGER_DETAIL_CHARS = 280


def trial_history(store: Store, *, as_of: datetime, catalog_path: Path = CATALOG_PATH) -> dict[str, Any]:
    """③ 과거 학습 내역 — 카탈로그(날짜 ≤ as_of) + 시행 대장. 최신이 위."""
    catalog = load_catalog(catalog_path)
    day = _kst_day(as_of)
    entries = [e for e in catalog.trials if e["date"] <= day]
    ledger = _ledger_rows(store, as_of)
    used: set[str] = set()
    rows: list[dict[str, Any]] = []
    for entry in entries:
        hits = [r for r in ledger if _matches(entry, r["entity_id"])]
        used.update(r["entity_id"] for r in hits)
        rows.append({
            **{k: v for k, v in entry.items() if k != "ledger_ids"},
            "date": entry["date"].isoformat(),
            "source": "catalog",
            "ledger": [{"entity_id": r["entity_id"], "at": r["at"], "detail": r["detail"][:LEDGER_DETAIL_CHARS]}
                       for r in hits],
        })
    for r in ledger:
        if r["entity_id"] in used:
            continue
        detail = r["detail"].strip()
        lead = next((word for word in _LEDGER_LEADS if detail.startswith(word)), None)
        stem, _, trial = r["entity_id"].partition(":")
        rows.append({
            "id": trial or r["entity_id"],
            "date": r["day"].isoformat(),
            "category": FAMILY_CATEGORY.get(r["family"], "기타"),
            "title": stem if trial else "",
            "what": "",
            "result": lead,
            "why": detail[:LEDGER_DETAIL_CHARS],
            "doc": f"docs/protocols/{stem}.md" if trial and (REPO_ROOT / "docs" / "protocols" / f"{stem}.md").is_file() else "",
            "source": "ledger_only",
            "ledger": [{"entity_id": r["entity_id"], "at": r["at"], "detail": r["detail"][:LEDGER_DETAIL_CHARS]}],
        })
    rows.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    counts = {name: sum(1 for r in rows if r["result"] == name) for name in RESULTS}
    return {
        "trials": rows,
        "summary": {
            "total": len(rows),
            "by_result": counts,
            "by_category": {name: sum(1 for r in rows if r["category"] == name) for name in (*CATEGORIES, "기타")},
            "catalog": len(entries),
            "ledger_only": sum(1 for r in rows if r["source"] == "ledger_only"),
        },
        "lessons": [{"text": x["text"], "source": x["source"]} for x in catalog.lessons
                    if x["date"] is None or x["date"] <= day][:3],
        "categories": list(CATEGORIES),
        "results": list(RESULTS),
        "problems": catalog.problems,
    }


__all__ = [
    "CATALOG_PATH",
    "PIPELINE_STAGES",
    "SHADOW_TRACKS",
    "live_models",
    "load_catalog",
    "trial_history",
]
