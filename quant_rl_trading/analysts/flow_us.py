"""flow_us Analyst — 미장 수급.

**지금은 입력이 하나도 없다.** 창고에 US 시장 행이 0건이고(prices·flows 전부
KR), short interest·13F·ETF 자금흐름·옵션 미결제약정 수집기도 없다. 그래서
이 Analyst 는 항상 빈 신호를 낸다 — 그리고 그게 맞는 동작이다.

## 왜 빈 파일이 아니라 이 파일인가

명단에서 빼면 아무도 "왜 없지" 를 묻지 않게 된다 (agent_health.PLANNED 와
같은 이유). 그리고 미장 데이터가 들어오는 날, 붙일 자리가 이미 정해져
있어야 한다.

## 왜 국장 모델을 재사용하지 않는가

미장에는 **투자자별 순매수 공시가 없다** (agents.md §2). flow_kr 의 피처는
외인·기관·개인 순매수가 전부라 미장에서는 계산 자체가 불가능하다. 국장
모델을 미장에 그대로 돌리면 전 종목 결측 → 전원 z=0 → 점수가 전부 같아지고,
IC 는 0 이 나오는데 그건 "안 먹혔다" 가 아니라 "잰 게 없다" 다. 두 모델을
따로 두는 이유가 이것이다.

## 들어오면 쓸 것 (agents.md §2)

- short interest — **격주 공시, 공표 지연 T+8일 안팎**. observed_at 을
  공표일로 찍지 않으면 통째로 미래를 본다
- 13F — 분기·45일 지연. 지연이 워낙 커서 저빈도 신호로만 쓴다
- ETF 자금흐름 — 종목 축으로 내리려면 보유 비중 매핑이 필요하다
- 옵션 put/call 비율·미결제약정

지연이 큰 데이터뿐이라, 붙일 때 horizon 5일이 맞는지부터 다시 본다.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_rl_trading.analysts.base import Analyst

#: 붙일 때 쓸 테이블. 아직 창고에 없다 — 있다고 가정하고 읽지 않는다.
REQUIRED_TABLES = ("short_interest", "institutional_holdings", "etf_flows", "options_oi")

#: 국장 피처를 재활용하지 않는 이유는 모듈 docstring 에 있다.
WEIGHTS: dict[str, float] = {}


class FlowUsAnalyst(Analyst):
    name = "flow_us"
    version = "flow_us-v0.0.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        """입력이 없으므로 항상 빈 프레임.

        빈 프레임을 내면 base 의 ``run`` 이 빈 신호 목록을 낸다. 0 점을
        지어내지 않는다 — 0 점은 "중립 의견" 이라는 뜻인데, 이 Analyst 는
        의견이 없는 것이지 중립인 것이 아니다.
        """
        return pd.DataFrame()

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return pd.Series(dtype=float)
