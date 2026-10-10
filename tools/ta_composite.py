"""기술적 합성 점수(시행 TG, docs/protocols/ta-floor-2026-10.md) — 실전 신호(`tools/ta_signal.py`)와 금고 굽기(`tools/vault_ta_bake.py`)가 같은 함수를 쓴다.

아홉의 정의는 진단(`tools/diag_ta_features.features` · `tools/diag_ta_round2.signals`)과 같은 코드다. 합성 = 원 부호로 맞춘 횡단면 백분위의
동일 가중 평균(`tools/diag_ta_possibility.composite`). 점수 대상 = 20일 평균 거래대금 ≥ 10억원 · 이력 252세션(진단 우주와 같다).
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from quant_rl_trading.store import Store
from tools import diag_ta_features as T
from tools import diag_ta_possibility as P
from tools import diag_ta_round2 as R2

ALL9 = P.ALL9


def composite_panel(store: Store, end: datetime, start: date = date(2020, 9, 1)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(합성 점수 넓은 표[세션 × 종목, 높을수록 좋음, 대상 밖 NaN], 대상 표). ``end`` 시점까지 관측된 가격만 읽는다."""
    d = T.load(store, end=end, start=start)
    c, v = d["close"], d["value"]
    label = c.shift(-6) / c.shift(-1) - 1.0                     # trend_factor 의 과거 회귀용 — 6세션 지연으로 닫힌 것만 쓴다
    label = label.where(label.abs() <= 0.5)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    f1 = T.features(d)
    sig = R2.signals(store, d, uni, label.where(uni))
    sig["shadow_asym_20"] = f1["upper_shadow_20"] - f1["lower_shadow_20"]
    sig["pv_corr_20"], sig["skew_60"] = f1["pv_corr_20"], f1["skew_60"]
    comp = P.composite({k: sig[k].where(uni) for k in ALL9}, ALL9, uni)
    return comp, uni
