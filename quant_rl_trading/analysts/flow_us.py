"""flow_us Analyst — 미장 수급.

**지금 입력은 FINRA 공매도 잔고다** (2026-09-02, 시행 I — 맨 아래 문단). 그 전
역사: 13F 는 잴 수 없었고(분기 둘뿐), 일별 공매도 거래량은 IC 미달이었다.
아래는 그 기록이다 — 같은 실수를 되풀이하지 않기 위해 남긴다.

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

## 13F 는 이제 종목 축에 붙는다 (2026-08-19)

``filings_13f`` 의 ``entity_id`` 는 ``CUSIP:02079K305`` 라 ``prices`` 의
``US:GOOGL`` 과 조인이 안 됐다. OpenFIGI 로 CUSIP→티커 매핑을 만들어
``security_ids`` 에 넣었고(4,150개 중 4,006개 = 96.5%, 금액 기준 99.8%),
``store/holdings.py`` 가 그 둘을 잇는다. 최근 분기 3,503종목 중 3,176종목
(90.7%)이 미장 시세와 붙는다 — 나머지는 ETF·클래스주(AKO/A)라 애초에
``us_universe`` 명단 밖이다.

## 그런데 왜 아직 피처를 안 넣나 — 실측 (US, 90세션, --save 없이)

    breadth       (몇 기관이 들고 있나)   IC +0.0188   58일
    log_value     (기관 보유 금액)        IC +0.0325   58일
    share_change  (전분기 대비 주식수)    IC +0.0034   58일
    combined                              IC +0.0345   58일

합격선 0.03 을 두 개가 넘지만 **통과가 아니다.** 표본 하한이 200일인데
58일이고, 더 중요한 것은 그 58일이 **독립이 아니라는 것**이다.

- 창고에 폭넓은 분기가 **둘뿐**이다(2026-04·2026-07). 나머지 두 분기는
  Scion 한 곳만 들어와 7~11종목이라 횡단면이 아니다
- 그래서 58일의 점수는 사실상 **같은 순위 하나를 58번 되풀이한 것**이다.
  일별 IC 를 58개 세었다고 표본이 58개인 것이 아니다
- ``share_change`` 는 더하다. 두 분기가 동시에 관측되는 세션이 **역사상
  3일**뿐이라(2026-08-14 공시 이후), +0.0034 는 "변화가 안 먹혔다" 가 아니라
  **잰 적이 없다**는 뜻이다
- ``log_value`` 는 통과선을 넘었지만 기관 보유 금액은 시가총액과 거의 같다.
  그 IC 는 13F 가 아니라 **사이즈 팩터**를 잰 것일 가능성이 높다

즉 지금 넣으면 **한 번의 베팅을 알파로 착각**하게 된다. 넣을 조건은 분명하다:
폭넓은 분기가 4개 이상 쌓일 때(2027-05 안팎, ``tools/collect_13f.py`` 를
분기마다 돌리면 자연히 찬다). 그때 볼 것은 보유 수준이 아니라 **변화**다.

## 공매도 거래량 피처의 실측 — 미달 (2026-09-02)

아래 세 피처(#50)를 2024-06~2026-06 508세션에 채점하고 월말 시점 11개에서
IC 를 쟀다(`tools/backfill_ic_history.py --market US --analyst flow_us`,
시점당 300일): **+0.0089 ~ +0.0127.** 부호는 맞았지만(공매도 압력 ↑ → 뒤에
덜 감) 합격선 0.03 의 ⅓ 이다. 일일 공매도 거래량은 시장조성자 헤지가 절반이라
"정보 거래자" 의 몫이 잡음에 묻힌다. 가중치 0 을 유지한다 — 피처는 남겨 두되
격주 short interest 가 들어오기 전엔 다시 재지 않는다.

## 들어오면 쓸 것 (agents.md §2)

- short interest — **격주 공시, 공표 지연 T+8일 안팎**. observed_at 을
  공표일로 찍지 않으면 통째로 미래를 본다
- 13F — 분기·45일 지연. 위 참고. 읽는 길은 ``store/holdings.py`` 에 났다
- ETF 자금흐름 — 종목 축으로 내리려면 보유 비중 매핑이 필요하다
- 옵션 put/call 비율·미결제약정

지연이 큰 데이터뿐이라, 붙일 때 horizon 5일이 맞는지부터 다시 본다. 13F 는
분기 데이터라 5일 horizon 자체가 안 맞을 수 있다.

## 잔고로 교체 (2026-09-02, 시행 I — new-sources-2026-09.md)

위 거래량 피처 셋은 뺐다. 잔고(short interest)는 빌려서 판 채 들고 있는
포지션이라 방향성 베팅이고, 학계의 결과도 잔고다. 결제일(매월 15일·말일)
집계를 FINRA 가 약 8영업일 뒤 공표하며, 수집기는 관측시각을 **결제일 뒤
10영업일 18:00 ET** 로 보수적으로 찍는다 — 그래서 여기서는 `store.get` 이
돌려주는 **최신 공표 잔고 하나**만 쓰면 미래를 안 본다. 반월 값을 매일
관측된 것처럼 펴지 않는다(그건 "변화 없음" 이 아니라 "관측됨" 으로 읽힌다).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst, combine, rank_score

#: 붙일 때 쓸 테이블. ``filings_13f``·``security_ids`` 는 **이미 창고에
#: 있다**(읽는 길은 ``store/holdings.py``). 나머지는 아직 없다 — 있다고
#: 가정하고 읽지 않는다.
REQUIRED_TABLES = (
    "filings_13f",
    "security_ids",
    "short_flow",
    "etf_flows",
    "options_oi",
)

SHORT_FLOW = "short_flow"
KIND_INTEREST = "interest"

#: 최신 잔고 하나가 항상 창 안에 있게 — 반월 주기 + 공표 지연 10영업일이면
#: 최대 30일 안팎이다. 60일이면 결제일 하나를 건너뛰어도 직전 것이 남는다.
LOOKBACK_DAYS = 60

#: 피처 가중치. **전부 같은 부호다** — 잔고가 크거나 늘수록 낮은 점수.
#: 부호는 사전등록(시행 I)에서 고정했고 사후에 뒤집지 않는다.
WEIGHTS: dict[str, float] = {
    "days_to_cover": 0.5,
    "short_interest_change": 0.5,
}


class FlowUsAnalyst(Analyst):
    name = "flow_us"
    version = "flow_us-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        """FINRA 공매도 잔고에서 만든다 (시행 I).

        - ``days_to_cover`` — 잔고 / 일평균거래량 (FINRA 산출값). 크면 되사기가 오래 걸린다.
        - ``short_interest_change`` — 잔고 / 직전 잔고 − 1. 늘고 있는가.

        종목마다 **최신 공표 잔고 하나**. 결측은 횡단면 순위 중앙(0).
        """
        latest = self._latest_interest(as_of)
        if latest is None:
            return pd.DataFrame()
        raw = pd.DataFrame(index=latest.index)
        raw["days_to_cover"] = latest["days_to_cover"].astype(float)
        previous = latest["previous_short_position"].astype(float).replace(0.0, np.nan)
        raw["short_interest_change"] = latest["short_position"].astype(float) / previous - 1.0
        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if raw.empty:
            return pd.DataFrame()
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """**부호를 뒤집는다** — 공매도 잔고가 클수록 낮은 점수.

        가중치는 전부 양수이고 여기서 한 번만 뒤집는다. 가중치에 음수를 섞으면
        어느 피처가 어느 방향인지 표에서 안 보인다.
        """
        return -combine(features, WEIGHTS)

    # -- 관측 -------------------------------------------------------------------

    def _latest_interest(self, as_of: datetime) -> pd.DataFrame | None:
        """종목별 최신 공표 잔고 한 행 (index = entity_id). 관측이 없으면 None.

        `store.get(as_of=)` 이 observed_at 으로 걸러 주므로 여기서는 "가장 최근
        결제일" 만 고르면 된다. 일별 거래량(kind=volume)은 읽지 않는다.
        """
        frame = self.store.get(
            SHORT_FLOW,
            as_of=as_of,
            lookback=LOOKBACK_DAYS,
            market=str(self.market),
            columns=[
                "entity_id", "valid_from", "kind",
                "short_position", "previous_short_position", "days_to_cover",
            ],
        )
        if frame.empty:
            return None
        frame = frame[frame["kind"].astype(str) == KIND_INTEREST]
        if frame.empty:
            return None
        latest = (
            frame.sort_values("valid_from")
            .groupby("entity_id", sort=False)
            .tail(1)
            .set_index("entity_id")
        )
        return latest
