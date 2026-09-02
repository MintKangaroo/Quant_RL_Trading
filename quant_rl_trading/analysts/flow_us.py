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
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst, combine, rank_score

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

SHORT_FLOW = "short_flow"

#: 공매도 비율의 창. 짧은 창이 "오늘 얼마나 이례적인가", 긴 창이 "이 종목의
#: 평소" 다. 20일은 한 달치 거래일이라 실적발표 한 사이클이 들어간다.
SHORT_WINDOW = 5
LONG_WINDOW = 20
#: 평소를 재려면 최소 이만큼은 관측돼야 한다. 며칠짜리 표본으로 "평소" 를
#: 말하면 그 편차는 잡음이다.
MIN_OBSERVATIONS = 12
LOOKBACK_DAYS = 45

#: 피처 가중치. **전부 같은 부호다** — 공매도 압력이 높을수록 낮은 점수.
#: 이 부호가 맞는지는 IC 가 말한다. 반대로 나오면(음의 IC) 숏스퀴즈 쪽이
#: 이긴다는 뜻이고, 그때 부호를 뒤집는 것은 **측정 결과이지 추측이 아니다.**
WEIGHTS: dict[str, float] = {
    "short_pressure": 0.5,
    "short_acceleration": 0.3,
    "short_exempt": 0.2,
}


class FlowUsAnalyst(Analyst):
    name = "flow_us"
    version = "flow_us-v0.0.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        """FINRA 공매도 거래량에서 만든다 (#50).

        **13F 는 여전히 안 넣는다** — 잴 수 있는 분기가 둘뿐이라 58일의 IC 가
        사실상 같은 순위 하나를 되풀이한 것이다(모듈 docstring 의 실측).
        공매도 거래량은 매일·전 종목이라 그 한계가 없다.

        ## 수준이 아니라 편차다

        공매도 비율의 중앙값이 0.50 이다(2026-08-14 실측 0.4992). FINRA 집계에
        시장조성자 헤지가 섞여 있어서 그렇다 — "절반이 공매도" 가 아니라
        **그게 기준선**이다. 그래서 절대 수준을 쓰지 않고 **그 종목의 평소
        대비 얼마나 높은가**를 쓴다.
        """
        panel = self._short_panel(as_of)
        if panel is None:
            return pd.DataFrame()

        ratio = panel["ratio"]
        # 그 종목의 평소. 관측이 얇으면 "평소" 를 말할 수 없으므로 뺀다.
        counts = ratio.notna().sum()
        usable = counts[counts >= MIN_OBSERVATIONS].index
        if usable.empty:
            return pd.DataFrame()
        ratio = ratio[usable]

        base = ratio.tail(LONG_WINDOW)
        mean = base.mean()
        # 표준편차 0 은 나눗셈이 아니라 결측이다. 매일 같은 값이면 편차를
        # 말할 수 없다.
        spread = base.std().replace(0.0, np.nan)

        recent = ratio.tail(SHORT_WINDOW).mean()
        raw = pd.DataFrame(index=usable)
        # 오늘(최근 5일)이 평소보다 몇 표준편차 위인가.
        raw["short_pressure"] = (recent - mean) / spread
        # 가속. 5일이 20일보다 높아지고 있는가 — 수준이 높아도 식고 있으면
        # 다른 이야기다.
        raw["short_acceleration"] = recent - mean
        # 면제 공매도 비중. 업틱룰 면제라 대개 시장조성 활동이고, 그 비중이
        # 크면 위 신호가 방향성이 아닐 가능성이 높다 — **감점 요인이다.**
        exempt = panel["exempt_ratio"].tail(LONG_WINDOW).mean()
        raw["short_exempt"] = exempt.reindex(usable)

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if raw.empty:
            return pd.DataFrame()
        # 결측은 횡단면 순위 중앙(0). 앞뒤로 채우면 미래를 본다.
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """**부호를 뒤집는다** — 공매도 압력이 높을수록 낮은 점수.

        가중치는 전부 양수이고 여기서 한 번만 뒤집는다. 가중치에 음수를 섞으면
        어느 피처가 어느 방향인지 표에서 안 보인다.
        """
        return -combine(features, WEIGHTS)

    # -- 관측 -------------------------------------------------------------------

    def _short_panel(self, as_of: datetime) -> dict[str, pd.DataFrame] | None:
        """(session × entity) 공매도 비율 표 둘. 관측이 없으면 None.

        비율은 **여기서** 만든다. 창고에는 분자·분모가 따로 있는데, 그건
        나중에 다르게 물을 수 있게 하려는 것이지 읽는 쪽이 매번 다르게
        계산하라는 뜻이 아니다.
        """
        frame = self.store.get(
            SHORT_FLOW,
            as_of=as_of,
            lookback=LOOKBACK_DAYS,
            columns=[
                "entity_id", "valid_from", "kind",
                "short_volume", "short_exempt_volume", "total_volume",
            ],
        )
        if frame.empty:
            return None
        # **일별 계열만 쓴다.** 잔고(interest)는 월 2회라 같은 창에서 섞으면
        # 발표 사이 구간에 같은 값이 반복되고, 그게 "변화 없음" 이 아니라
        # "관측됨" 으로 읽힌다.
        frame = frame[frame["kind"].astype(str) == "volume"]
        if frame.empty:
            return None

        total = frame["total_volume"].astype(float).replace(0.0, np.nan)
        frame = frame.assign(
            ratio=frame["short_volume"].astype(float) / total,
            exempt_ratio=frame["short_exempt_volume"].astype(float) / total,
        )
        out: dict[str, pd.DataFrame] = {}
        for column in ("ratio", "exempt_ratio"):
            out[column] = (
                frame.pivot_table(
                    index="valid_from", columns="entity_id", values=column,
                    aggfunc="last",
                ).sort_index()
            )
        return out
