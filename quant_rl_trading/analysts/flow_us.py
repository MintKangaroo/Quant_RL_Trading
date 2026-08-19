"""flow_us Analyst — 미장 수급.

**아직 빈 신호를 낸다. 다만 이유가 2026-08-19 로 바뀌었다.** 입력이 없어서가
아니라, 있는 입력(13F)으로는 **아직 잴 수가 없어서**다. 아래 실측 참고.

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

## 들어오면 쓸 것 (agents.md §2)

- short interest — **격주 공시, 공표 지연 T+8일 안팎**. observed_at 을
  공표일로 찍지 않으면 통째로 미래를 본다
- 13F — 분기·45일 지연. 위 참고. 읽는 길은 ``store/holdings.py`` 에 났다
- ETF 자금흐름 — 종목 축으로 내리려면 보유 비중 매핑이 필요하다
- 옵션 put/call 비율·미결제약정

지연이 큰 데이터뿐이라, 붙일 때 horizon 5일이 맞는지부터 다시 본다. 13F 는
분기 데이터라 5일 horizon 자체가 안 맞을 수 있다.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_rl_trading.analysts.base import Analyst

#: 붙일 때 쓸 테이블. ``filings_13f``·``security_ids`` 는 **이미 창고에
#: 있다**(읽는 길은 ``store/holdings.py``). 나머지 셋은 아직 없다 — 있다고
#: 가정하고 읽지 않는다.
REQUIRED_TABLES = (
    "filings_13f",
    "security_ids",
    "short_interest",
    "etf_flows",
    "options_oi",
)

#: 국장 피처를 재활용하지 않는 이유는 모듈 docstring 에 있다.
WEIGHTS: dict[str, float] = {}


class FlowUsAnalyst(Analyst):
    name = "flow_us"
    version = "flow_us-v0.0.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        """아직 빈 프레임.

        13F 는 붙었지만 **잴 수 있는 분기가 둘뿐**이라 여기 넣지 않는다
        (모듈 docstring 의 실측). 빈 프레임을 내면 base 의 ``run`` 이 빈 신호
        목록을 낸다. 0 점을 지어내지 않는다 — 0 점은 "중립 의견" 이라는
        뜻인데, 이 Analyst 는 의견이 없는 것이지 중립인 것이 아니다.
        """
        return pd.DataFrame()

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return pd.Series(dtype=float)
