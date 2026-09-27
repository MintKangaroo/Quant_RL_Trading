"""6차 G11 — 미장 8-K **항목 단위** 사건 피처.

사전등록 초안 `docs/protocols/ranker-sources-g11-us-8k-items-2026-10.md`.
`ranker_sources.py` 가 파일 끝에서 이 묶음을 `GROUPS`·`BUILDERS` 에 등록한다 —
다른 묶음(G10·G12·G13)이 같은 파일을 동시에 고치므로 본체는 여기 따로 둔다.

## 왜 별도 묶음인가

창고 `documents`(source=edgar) 는 8-K 제목에 **항목코드를 그대로 담는다**
("8-K 1.01 3.02 9.01"). 그런데 지금 그 정보를 쓰는 곳이 둘뿐이고 둘 다 뭉갠다:

- `collectors/edgar_filings.EIGHT_K_ITEMS` 는 27개 항목 중 7개만 doc_type 으로 접는다.
- `analysts/event.FILING_SIGNS` 는 그 doc_type 을 다시 ±1.0 으로 뭉갠다.

그래서 "감사인이 갈렸다"(4.01) 와 "재무제표를 믿을 수 없다"(4.02) 와
"상장규정을 위반했다"(3.01) 가 전부 같은 −1.0 이거나, 아니면 `other` 로 버려진다.
**수집은 하나도 없다** — 이미 창고에 있는 것을 항목별로 다시 읽는 것뿐이다
(불변식: Analyst 는 수집하지 않는다).

## 규칙 — BA 복기에서 배운 것

BA(밸류업 공시 **개수**)는 "어떤 회사인가" 를 넣어 기각됐다. 그래서 여기 피처는 전부
**사건형**이다 — 짧은 창(20·60·120세션) 안의 발생, 경과 세션 수, 2년 만의 첫 발생.
보유 수준·누적 건수·"공시를 많이 내는 회사" 표지는 쓰지 않는다.
2.02(실적 발표)는 **넣지 않는다** — G6 이 이미 그 항목으로 등록돼 있다(겹침 금지).
"사건 없음" 은 결측이 아니라 사실이므로 0 이다(G2·G4·G7·G10 과 같은 규약).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst

#: 등록된 열과 순서. `ranker_sources.GROUPS["G11"]` 이 이것을 그대로 쓴다.
G11_FEATURES: tuple[str, ...] = (
    "k8_negative_20",
    "k8_negative_age",
    "k8_first_in_2y",
    "k8_debt_60",
    "k8_equity_60",
    "k8_control_120",
    "k8_amend_20",
)

#: 2년 이력(첫 발생 판별) + 20세션 창 + 여유. `documents` 만 이만큼 본다.
K8_LOOKBACK_DAYS = 800
#: "2년 만에 처음" 의 2년(달력일).
K8_FIRST_HISTORY_DAYS = 730
#: **시세는 세션 축과 명단에만 쓴다** — 가장 긴 창(120세션 ≈ 175달력일)만 덮으면 된다.
#: 여기에 800일을 주면 미장 패널이 280만 행이 되고 build 한 번이 183초다(실측 2026-09-27,
#: 200일로는 25초). 창고 읽기가 아니라 판다스가 시간을 먹는다.
K8_PRICE_LOOKBACK_DAYS = 200

#: **부정 항목** — 방향이 한쪽으로 분명한 것만. 나머지(1.01 계약 체결·2.01 인수 완료·
#: 5.03 정관 변경·3.03 주주권리 변경)는 부호를 말할 수 없어 뺐다. 부호를 모르는 재료를
#: 한 칸에 섞으면 GBM 이 배우는 것은 "공시를 냈다" 뿐이다 — BA 가 그렇게 실패했다.
K8_NEGATIVE_ITEMS: tuple[str, ...] = (
    "1.02",  # 중요 계약의 해지
    "2.05",  # 구조조정 비용 확정
    "2.06",  # 자산 손상
    "3.01",  # 상장규정 위반·상폐 통보
    "4.01",  # 감사인 교체
    "4.02",  # 기존 재무제표를 믿을 수 없다
)
#: 단독으로 세는 항목 → 창(세션).
K8_SINGLE_ITEMS: dict[str, tuple[str, int]] = {
    "k8_debt_60": ("2.03", 60),      # 직접·우발 부채의 발생
    "k8_equity_60": ("3.02", 60),    # 미등록 주식 매각(PIPE·전환 — 실제 희석)
    "k8_control_120": ("5.01", 120),  # 지배권 변경
}
K8_NEGATIVE_SESSIONS = 20
K8_AMEND_SESSIONS = 20
#: 경과 세션 수의 상한. 사건이 없으면 이 값이다 — "아주 오래 전" 과 같은 칸에 둔다.
K8_AGE_CAP = 120

#: 제목에서 8-K 와 8-K/A 를 가른다(정정은 새 사건이 아니라 앞 공시의 수정이다).
_K8_TITLE = r"^8-K(?:/A)?\b"
_K8_AMEND_TITLE = r"^8-K/A\b"
_ITEM_CODE = r"[0-9]\.[0-9]{2}"


def _session_index(prices: pd.DataFrame) -> list[date]:
    return sorted(prices["session"].unique())


def eight_k_items(analyst: Analyst, as_of: datetime) -> pd.DataFrame:
    """G11. 미장 8-K 항목 단위 사건. 미장만 값이 있다(국장 `documents` 엔 8-K 가 없다).

    - ``k8_negative_20``  = 최근 20세션 부정 항목(1.02·2.05·2.06·3.01·4.01·4.02) 공시 건수
    - ``k8_negative_age`` = 마지막 부정 항목 공시 뒤 지난 세션 수(상한 120, 없으면 120)
    - ``k8_first_in_2y``  = 최근 20세션 부정 항목 중 **직전 2년에 없던 항목**이 있으면 1
                            (이력이 2년을 못 덮는 구간은 결측 — 없는 것과 처음인 것을 못 가른다)
    - ``k8_debt_60``      = 최근 60세션 2.03(부채 발생) 건수
    - ``k8_equity_60``    = 최근 60세션 3.02(미등록 주식 매각) 건수
    - ``k8_control_120``  = 최근 120세션 5.01(지배권 변경) 건수
    - ``k8_amend_20``     = 최근 20세션 8-K/A(정정) 건수

    창은 **접수일**(valid_from)로 센다. 관측 시각은 `store.get(as_of=)` 이 거른다 —
    창고 실측으로 `observed_at − valid_from` 이 전부 +22h(익일 07:00 KST = 접수일 18:00 ET,
    그 날 종가 뒤)여서 세션 s 개장 시점에는 s−1 접수분까지만 보인다. 여기서 시각을 당기지 않는다.
    """
    docs = analyst.store.get(
        "documents", as_of=as_of, lookback=K8_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from", "title"],
    )
    if docs.empty:
        return pd.DataFrame()
    prefix = f"{analyst.market}:"
    docs = docs[
        docs["entity_id"].astype(str).str.startswith(prefix)
        & docs["title"].astype(str).str.match(_K8_TITLE, na=False)
    ].copy()
    if docs.empty:
        return pd.DataFrame()
    prices = analyst.price_panel(as_of, lookback=K8_PRICE_LOOKBACK_DAYS)
    if prices.empty:
        return pd.DataFrame()
    sessions = _session_index(prices)
    if len(sessions) < K8_AGE_CAP:
        return pd.DataFrame()
    entities = pd.Index(sorted(prices["entity_id"].unique()), name="entity_id")

    docs["day"] = docs["valid_from"].dt.date
    docs["amend"] = docs["title"].astype(str).str.match(_K8_AMEND_TITLE, na=False)
    history_from = min(docs["day"])

    def since(count: int) -> date:
        return sessions[-min(count, len(sessions))]

    raw = pd.DataFrame(index=entities)

    # 항목을 행으로 펼친다. 한 공시가 여러 항목을 담으므로(예: "8-K 1.01 3.02 9.01")
    # 건수는 **항목 단위**로 센다 — 같은 공시의 다른 항목은 다른 사건이다.
    items = docs.loc[~docs["amend"], ["entity_id", "day", "title"]].copy()
    items["item"] = items["title"].astype(str).str.findall(_ITEM_CODE)
    items = items.explode("item").dropna(subset=["item"]).drop(columns="title")

    negative = items[items["item"].isin(K8_NEGATIVE_ITEMS)]
    window_start = since(K8_NEGATIVE_SESSIONS)
    recent_negative = negative[negative["day"] >= window_start]
    raw["k8_negative_20"] = (
        recent_negative.groupby("entity_id").size().reindex(entities).fillna(0.0).astype(float)
    )

    # 경과 세션 수 — 세션 축으로 센다(달력일이면 연휴가 값을 흔든다).
    age_from = since(K8_AGE_CAP)
    aged = negative[negative["day"] >= age_from]
    position = {day: index for index, day in enumerate(sessions)}
    if aged.empty:
        raw["k8_negative_age"] = float(K8_AGE_CAP)
    else:
        last_day = aged.groupby("entity_id")["day"].max()
        last_pos = last_day.map(lambda d: next((position[s] for s in sessions if s >= d), np.nan))
        age = (len(sessions) - 1 - last_pos).astype(float).clip(lower=0.0, upper=float(K8_AGE_CAP))
        raw["k8_negative_age"] = age.reindex(entities).fillna(float(K8_AGE_CAP))

    # 2년 만의 첫 발생. 이력이 2년을 못 덮으면 전부 "처음" 으로 보여 거짓이 된다 → 결측.
    required_from = window_start - timedelta(days=K8_FIRST_HISTORY_DAYS)
    if history_from > required_from:
        raw["k8_first_in_2y"] = np.nan
    elif recent_negative.empty:
        raw["k8_first_in_2y"] = 0.0
    else:
        prior = negative[(negative["day"] < window_start) & (negative["day"] >= required_from)]
        seen = set(map(tuple, prior[["entity_id", "item"]].to_numpy()))
        pairs = map(tuple, recent_negative[["entity_id", "item"]].to_numpy())
        known = pd.Series([pair in seen for pair in pairs], index=recent_negative.index)
        fresh = recent_negative[~known]
        first = pd.Series(1.0, index=pd.Index(fresh["entity_id"].unique(), name="entity_id"))
        raw["k8_first_in_2y"] = first.reindex(entities).fillna(0.0)

    for column, (item, window) in K8_SINGLE_ITEMS.items():
        part = items[(items["item"] == item) & (items["day"] >= since(window))]
        raw[column] = part.groupby("entity_id").size().reindex(entities).fillna(0.0).astype(float)

    amended = docs[docs["amend"] & (docs["day"] >= since(K8_AMEND_SESSIONS))]
    raw["k8_amend_20"] = (
        amended.groupby("entity_id").size().reindex(entities).fillna(0.0).astype(float)
    )

    tradable = analyst.tradable_entities(as_of, lookback=K8_PRICE_LOOKBACK_DAYS)
    if tradable is not None:
        raw = raw.loc[raw.index.intersection(pd.Index(sorted(tradable)))]
    return raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
