"""FA(전 피처) 76열 — 마지막 모델 회차(docs/protocols/final-model-round-2026-10.md)가 고정한 열과 순서.

BE2(트랜스포머 + GBM·FA 순위 평균)를 얼린 모델은 **이 순서의 열**을 먹는다. 판정 때 열 목록은
`tools/final_round_kit.feature_names(blocks_of())` 가 원피처 캐시 헤더를 읽어 만들었다 — 실전은 캐시를 못 읽으므로
여기 상수로 박아 두고, 얼리기 도구(`tools/freeze_be2.py`)가 kit 의 목록과 **한 글자라도 다르면 멈춘다.**
테스트(`tests/analysts/test_fa_features.py`)는 묶음 정의(`ranker_sources.GROUPS`·`valueup.NEW`)와 이 상수를 맞춘다.

이 모듈은 상수뿐이다 — `store/tables.py` 가 `fa_features` 표의 열을 여기서 읽는다(Analyst 를 import 하면 순환).
"""
from __future__ import annotations

#: 창고 `fa_features` 의 `feature_set` 값. 열 정의나 순서가 바뀌면 올린다 — 옛 행과 섞이지 않는다.
FEATURE_SET = "fa76-v1"

#: 점수 6 — `analysts/ranker.SCORE_FEATURES` 와 같다(국장 flow = flow_kr, 미장 flow = flow_us).
SCORE_COLUMNS: tuple[str, ...] = ("chart", "event", "flow", "fundamental", "regime", "risk")

#: 원피처 — 두 시장 합집합 35(국장 33 · 미장 28). 이름 규칙 `raw_{analyst}_{feature}`(시행 W).
#: kit 은 `sorted(set(...))` 로 모은다 — 순서는 아래 `RAW_COLUMNS` 가 정렬로 다시 만든다.
_RAW = (
    "raw_chart_ma_gap", "raw_chart_momentum_20", "raw_chart_momentum_60", "raw_chart_range_position",
    "raw_chart_reversal_5",
    "raw_event_buyback", "raw_event_contract", "raw_event_dilution", "raw_event_distress",
    "raw_event_dividend", "raw_event_maturity",
    "raw_flow_kr_foreign_20", "raw_flow_kr_foreign_5", "raw_flow_kr_foreign_persistence",
    "raw_flow_kr_institution_20", "raw_flow_kr_retail_20",
    "raw_flow_us_days_to_cover", "raw_flow_us_short_interest_change",
    "raw_fundamental_book_to_market", "raw_fundamental_current_ratio", "raw_fundamental_earnings_yield",
    "raw_fundamental_low_leverage", "raw_fundamental_operating_margin", "raw_fundamental_profit_growth",
    "raw_fundamental_revenue_growth", "raw_fundamental_roe", "raw_fundamental_sales_to_price",
    "raw_regime_beta", "raw_regime_downside_beta", "raw_regime_idio_volatility",
    "raw_regime_index_correlation", "raw_regime_rate_steadiness",
    "raw_risk_liquidity", "raw_risk_low_beta", "raw_risk_low_volatility",
)
RAW_COLUMNS: tuple[str, ...] = tuple(sorted(_RAW))

#: 원피처를 내는 Analyst(시장별) — `tools/trial_raw_feature_ranker.ANALYSTS` 와 같다.
RAW_ANALYSTS: dict[str, tuple[str, ...]] = {
    "KR": ("chart", "event", "flow_kr", "fundamental", "regime", "risk"),
    "US": ("chart", "event", "flow_us", "fundamental", "regime", "risk"),
}

#: 묶음 G1~G7 — `analysts/ranker_sources.GROUPS` 의 같은 이름(테스트가 맞춘다). G8·G10·G11 은 FA 밖이다(리드 결정).
GROUP_COLUMNS: dict[str, tuple[str, ...]] = {
    "G1": ("turnover_decay", "amihud_20", "zero_volume_20"),
    "G2": ("distress_120", "dilution_60", "filing_burst_20"),
    "G3": ("short_intensity_20", "short_intensity_chg", "days_to_cover"),
    "G4": ("insider_sell_60", "insider_net_60"),
    "G5": ("accrual_wc", "z_lite", "loss_streak"),
    "G6": ("pead_2d", "days_since_earn", "earn_gap"),
    "G7": ("form4_sell_60", "form4_sellers_20", "form4_buy_60"),
}

#: 밸류업 5 — `analysts/valueup.NEW`. 개수 넷은 rank-gauss 전에 0 으로 채운다(`zero_first`).
BA_COLUMNS: tuple[str, ...] = ("vu_plan", "vu_cancel", "vu_buyback", "vu_dividend", "vu_plan_age")
BA_ZERO_FIRST: tuple[str, ...] = ("vu_plan", "vu_cancel", "vu_buyback", "vu_dividend")

#: 묶음 순서 = `final_round_kit.BLOCK_ORDER`. 표지 이름 = `miss_{묶음 소문자}` (score 는 표지 없음).
BLOCK_ORDER: tuple[str, ...] = ("score", "raw", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "ba")
BLOCK_COLUMNS: dict[str, tuple[str, ...]] = {
    "score": SCORE_COLUMNS, "raw": RAW_COLUMNS, **GROUP_COLUMNS, "ba": BA_COLUMNS,
}
FLAG_OF: dict[str, str] = {name: f"miss_{name.lower()}" for name in BLOCK_ORDER if name != "score"}
#: 묶음이 자료를 갖는 시장 — kit 의 `Group.markets`. ba 는 국장 전용(DART 공시).
BLOCK_MARKETS: dict[str, tuple[str, ...]] = {name: (("KR",) if name == "ba" else ("KR", "US")) for name in BLOCK_ORDER}

#: 모델 입력 열 전부 — kit `feature_names`: 묶음 피처 → 결측 표지 → is_us.
FA_FEATURES: tuple[str, ...] = (
    *(c for name in BLOCK_ORDER for c in BLOCK_COLUMNS[name]),
    *(FLAG_OF[name] for name in BLOCK_ORDER if name in FLAG_OF),
    "is_us",
)
#: rank-gauss 를 거치는 열(표지·is_us 는 0/1 그대로).
SCALED: tuple[str, ...] = tuple(c for name in BLOCK_ORDER for c in BLOCK_COLUMNS[name])
FLAGS: tuple[str, ...] = tuple(FLAG_OF[name] for name in BLOCK_ORDER if name in FLAG_OF)

assert len(FA_FEATURES) == 76, len(FA_FEATURES)  # noqa: S101 — 모듈 로드 때 한 번, 등록 문서의 76열

__all__ = [
    "BA_COLUMNS", "BA_ZERO_FIRST", "BLOCK_COLUMNS", "BLOCK_MARKETS", "BLOCK_ORDER", "FA_FEATURES",
    "FEATURE_SET", "FLAGS", "FLAG_OF", "GROUP_COLUMNS", "RAW_ANALYSTS", "RAW_COLUMNS", "SCALED",
    "SCORE_COLUMNS",
]
