"""마켓 탭 집계 — **지금 시장이 어떤 상태인가.**

트레이딩 탭이 "우리 포트폴리오" 를 보여준다면, 이 화면은 그 바깥의 **환경**을
보여준다. 지수·환율·거시지표·시가총액 상위 종목 — 전부 우리가 아직 사지
않았어도 알아야 하는 것들이다.

## 화면은 좌우 두 시장이다 — 그래서 집계도 시장별로 한다

국장과 미장은 **같은 모양의 판 둘**이다. 그래서 이 모듈은 시장별 집계
(`market_panel`)를 하나 만들어 KR·US 에 똑같이 돌린다. 한쪽만 있는 패널을
만들면 화면의 좌우 밀도가 갈라지고, 그러면 "미장은 원래 볼 게 없다" 처럼
보인다 — 실제로는 **없는 것이 아니라 아직 안 받은 것**이다.

시장을 가르지 않고 함께 보는 것은 **원달러 환율 하나뿐**이다. 그건 두 시장
사이의 다리이지 어느 한쪽의 지표가 아니라, 좌우로 갈리기 전의 공통 줄(KPI)에
있다.

대표 지수는 한때 넷을 한 그림에 겹쳐 정규화했었다. 지금은 **지수마다 자기
패널**이고(`index_panels`), 각 패널이 자기 축을 가지므로 **원 종가를 그대로**
그린다 — 정규화는 축을 공유해야 할 때만 필요했던 트릭이었다.

## 이 화면에서 계산하지 않는 것

NAV·낙폭·손익은 여기 없다. 그건 트레이딩 탭의 것이고 회계(`accounting/`)
에서만 온다. 이 화면이 스스로 수익률을 계산하면 두 화면의 숫자가 어긋난다.

## 없는 것은 없다고 말한다

- 지수·시총 리더는 **그날 값이 창고에 없으면 목록에서 빠진다.** 0 으로
  채우면 "그 종목은 시총이 0" 이라는 다른 사실이 된다.
- 거시지표 실측이 아직 없는(예정) 건은 이 화면에 올리지 않는다 — 그건
  뉴스·일정 탭의 "예정" 패널이 하는 일이다. 여기는 **이미 일어난 것**만 본다.
- 등락률을 못 잰 종목(그날 거래가 없어 직전 종가가 없는 등)은 ``change`` 를
  ``null`` 로 둔다. 화면이 그걸 회색으로 그린다 — 0% 로 채우면 "보합" 이라는
  다른 사실이 된다. 시장 폭(breadth)의 보합 칸에도 넣지 않는다.

## 대표 지수는 config 가 정한다 — 화면이 고르지 않는다

각 시장의 대표 지수는 `config.benchmark.kr_index` / `us_index` 다. 우리가
실제로 그것과 견줘 평가받는 지수이므로, 화면의 대표도 그것이어야 한다.
여기서 이름을 따로 들면 화면과 회계가 다른 지수를 "대표" 라 부르게 된다
(불변식 10). 코스피·코스닥·나스닥이 창고에 들어오면 config 의 이름만 바뀌고
이 화면은 그대로 따라간다 — **여기서 이름을 짐작하지 않는다.**

그리고 그 지수는 **가격지수(PR)** 다. 총수익지수를 못 구해서(배당 미반영)
우리가 연 2~3%p 만큼 유리하게 보인다 — 화면이 그 배지를 계속 달고 있어야
한다. `benchmark.total_return` 이 true 가 되면 배지는 사라진다.

## 나머지 지수는 "오늘 많이 움직인 순" 으로 쌓는다 — 자르지 않는다

창고의 KR 지수는 44종(코스피·코스닥 + KRX 업종·테마)이고 US 는 10종이다
(2026-08-15 에 2종에서 늘었다 — 다우 3종·NASDAQ100·SOX·변동성 3종).
44종을 그냥 깔면 왼쪽이 오른쪽의 몇 배 높이가 되어 좌우로 나눈 의미가
없어진다. 그래서 **줄 수를 자르는 대신 패널을 낮게 고정하고 그 안에서
스크롤한다**(market.css). 자르면 오늘 조용했던 코스닥이 목록에서 사라지는데,
"안 움직였다" 는 사실도 화면이 말해야 하는 사실이다. 순서만 오늘 변동이 큰
순이고, 빠지는 지수는 없다.

## 시장 폭(breadth)·움직인 종목 — 시세만으로 만드는 미장 화면

미장은 시세(`prices`)와 유니버스(`universe`)는 들어와 있는데 시총·수급·재무가
없다. 그래도 **시세만으로 답할 수 있는 질문**이 있다 — 오늘 몇 종목이 오르고
몇 종목이 내렸나, 거래대금은 어디에 몰렸나. 그게 breadth 와 movers 다.
지수 하나로는 "지수는 올랐는데 종목의 70%는 내렸다" 를 볼 수 없다.

## 시가총액은 시세보다 늦게 온다 — 없는 것과 늦은 것은 다르다

`leaders` 와 트리맵이 참조하는 `market_stats` 는 **시가총액 = 주가 ×
상장주식수** 다. 상장주식수는 시세와 **다른 수집기**가 넣는다(KR: KRX Open
API, US: SEC). 그래서 두 세션이 어긋난다 — 실측(2026-08-18): 국장 시세
08-14 · 시총 08-11, 미장은 둘 다 08-17.

그 어긋남을 창으로 덮으면 안 된다. 창을 5일로 잡았을 때 국장 시총이 통째로
0행이 되어 화면이 "상장주식수가 없다" 고 말했는데, 있는데 못 본 것이었다.
그래서 시총은 **넓은 창**(`CAP_RECENT_DAYS`)으로 찾고, **찾은 세션을 응답에
같이 싣는다**. 낡은 값을 오늘 것처럼 쓰지 않으려면 화면이 그 날짜를 적어야
한다.

US 는 오래 0행이었다(상장주식수 소스가 없었다). 지금은 SEC 로 들어온다 —
다만 ADR·ETF 는 여전히 주식수가 없어 그 종목들은 빠진다. 지어내지 않고
빼고, 화면이 그 이유를 문구로 말한다.

## 트리맵을 왜 시장별로 완전히 쪼개는가

finviz 식 맵은 보통 섹터로 묶는다. 이 창고에도 `sectors` 테이블이 있지만
**업종 분류가 아니다.** `SECT_TP_NM`(KRX Open API 일별매매) 은 소속부 —
KOSDAQ 만 우량기업부·벤처기업부 등으로 갈리고, **KOSPI 942 종목은 전부
빈 문자열(미상)** 이다 (`store/tables.py` sectors TableSpec 참고). 이 축으로
묶으면 코스피가 통째로 "미상" 한 덩어리가 된다. 그래서 확실히 아는 축 —
**시장** — 으로 나눈다. 원·달러가 섞인 시가총액을 한 사각형 트리에 나란히
두면 두 시장의 스케일이 뒤섞여 읽힌다(원화 시총이 절대적으로 더 크다).
좌우 분할 화면에서는 그 분리가 그대로 배치가 된다 — 국장 맵은 왼쪽 칸,
미장 맵은 오른쪽 칸이다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.dashboard.services import trading as trading_service
from quant_rl_trading.indicators import wilder_rsi_series
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

INDICES = "indices"
MARKET_STATS = "market_stats"
FX = "fx"
MACRO_RELEASES = "macro_releases"
UNIVERSE = "universe"

#: 화면이 다루는 시장. 좌우 두 칸의 순서이기도 하다.
MARKETS = ("KR", "US")

#: 시장별 통화. 시총·거래대금을 화면이 어느 단위로 읽을지 정한다 — 원과
#: 달러를 한 숫자 축에 섞지 않기 위해 응답에 같이 싣는다.
CURRENCY = {"KR": "KRW", "US": "USD"}

#: `config.benchmark` 안에서 그 시장의 대표 지수를 들고 있는 키.
BENCHMARK_KEY = {"KR": "kr_index", "US": "us_index"}

#: 등락을 재는 창. 화면의 lookback(타임머신 창)과 다르다 — 여기는 "어제 대비"
#: 하나만 있으면 되고, 창을 키워도 최신 두 세션만 쓴다.
RECENT_DAYS = 5

#: 시가총액을 찾아 거슬러 올라가는 창. **등락 창(RECENT_DAYS)보다 넓다.**
#:
#: 시세와 시총은 다른 수집기가 넣고, 시총 쪽이 며칠씩 밀린다 — 실측
#: (2026-08-18): 국장 시세는 08-14 인데 `market_stats` 의 마지막 시총은
#: **08-11** 이었다. 5일 창으로 읽으면 그 표가 통째로 0행이 되고, 화면은
#: "시총을 만들 수 없다"(= 상장주식수가 없다)고 말한다 — 실제로는 있는데
#: 창 밖이라 못 본 것이라, 엉뚱한 데를 파게 된다.
#:
#: 그래서 창을 넓히고 **찾은 세션을 응답에 같이 싣는다**. 낡은 시총을 몰래
#: 오늘 것처럼 쓰지 않기 위해서다 — 화면이 그 날짜를 적는다.
CAP_RECENT_DAYS = 15

#: 시가총액 상위 표의 행수. 화면은 한눈에 보는 것이지 전종목 스크리너가 아니다.
#: 좌우 두 칸으로 나누면서 폭이 절반이 됐다 — 15줄에서 줄였다.
LEADER_ROWS = 10

#: 트리맵 시장별 상위 종목 수. 수천 종목을 다 그리면 사각형이 1픽셀이 되어
#: 브라우저가 느려진다. 60은 finviz 가 한 섹터 타일에 보통 담는 규모다.
TREEMAP_TOP_N = 60

#: 최근 거시지표 발표 카드 개수(시장별). 지표별 전체 현황은 뉴스·일정 탭이
#: 맡는다 — 여기는 "방금 무엇이 발표됐나" 만 짚는다.
MACRO_ROWS = 5

#: 시장별 패널이 무엇으로 만들어지나. **국장은 지수, 미장은 ETF** 다.
#:
#: 갈라진 이유는 취향이 아니라 데이터다. 창고의 미장 지수(`US:IDX:*`)는
#: FRED 에서 오고 **종가만 있다** — open/high/low/volume 이 전부 NULL 이라
#: 캔들을 그릴 수 없다. 미장 ETF(LS 해외)는 OHLC 가 완전하다.
PANEL_KIND = {"KR": "index", "US": "etf"}

#: 패널이 그릴 수 있는 봉. **분봉 다섯은 트레이딩 탭의 상수를 그대로 쓴다** —
#: 여기서 이름을 다시 지으면 두 화면의 버튼이 언젠가 갈라진다.
#:
#: 순서가 곧 화면 버튼의 순서다(짧은 것 → 긴 것).
DAILY_INTERVAL = "1D"
WEEKLY_INTERVAL = "1W"
PANEL_INTERVALS = (*trading_service.INTRADAY_INTERVALS, DAILY_INTERVAL, WEEKLY_INTERVAL)

#: 주봉을 만들려고 읽는 **일봉**의 창(일).
#:
#: 주봉은 별도 수집이 아니다 — 창고의 일봉을 주 단위로 접은 것이다. 그래서
#: 이 값은 "몇 주를 보여줄까" 를 일 단위로 적은 것뿐이고(730일 ≈ 104주),
#: 화면의 lookback(타임머신 창, 기본 90일)과는 다른 축이다. lookback 을 그대로
#: 쓰면 주봉이 13개짜리 토막이 되어 주봉을 켜는 뜻이 없어진다.
#:
#: 여기 있는 이유는 이 값이 **화면의 창**이지 매매 임계치가 아니기 때문이다 —
#: 같은 이유로 위의 RECENT_DAYS·CAP_RECENT_DAYS 도 config 가 아니라 여기 있다.
#: 학습·Executor·리포트 중 이 숫자를 읽는 곳은 없다.
WEEKLY_LOOKBACK_DAYS = 730

#: 국장 패널의 곁 지수. 대표는 `config.benchmark.kr_index` 가 정하고 여기
#: 없다 — 화면이 대표를 고르지 않는다.
COMPANION_INDICES = {"KR": ("KR:IDX:KOSDAQ",)}

#: 미장 패널 — **(entity_id, 추종 지수)**. 첫 줄이 화면 대표다.
#:
#: ⚠️ **제목은 티커다. 지수 이름이 아니다.** "S&P 500" 이라 쓰고 SPY 를 그리는
#: 것이 이 저장소가 금지하는 대용치 바꿔치기다. 추종 대상은 ``tracks`` 로
#: 부제에만 적는다.
#:
#: ETF 는 지수가 아니라서 값이 어긋난다 — 분배락에 가격이 떨어지고(지수와
#: 방식이 다르다), 보수를 떼며(SPY 연 0.0945%) 추적오차가 쌓이고, 시장가격이
#: NAV 와 벌어진다(프리미엄/디스카운트). 화면이 그 셋을 적는다.
#:
#: `US:SOXX` 도 2026-08-19 부터 찬다 — LS 해외로 받는다(1,257세션, 2021-08~).
#: 오래 "미수집" 이었던 것은 SEC 명단에서 유도하는 미장 유니버스에 ETF 가 안
#: 들어와서였다. 명단이 아니라 **티커를 직접 지정해** 받으면 온다
#: (``collectors/ls_us_source`` 의 ``INDEX_PROXY_ETFS``).
#: (SOXX 는 2021-06 부터 필라델피아 SOX 가 아니라 ICE 반도체 지수를 좇는다.)
US_ETF_PANELS = (
    ("US:SPY", "S&P 500"),
    ("US:QQQ", "나스닥 100"),
    ("US:DIA", "다우존스 산업평균"),
    ("US:SOXX", "ICE 반도체"),
    # **국채는 캔들이 아니라 선이다.** FRED H.15 고정만기 수익률이라 하루에
    # 값 하나뿐이고 시가·고가·저가가 없다. 패널이 `has_ohlc=False` 를 보고
    # 알아서 선을 긋는다 — 없는 OHLC 를 지어내면 그럴듯한 거짓말이 된다.
    #
    # 단위가 **%** 라 위 넷과 다르다. 등락도 "가격이 몇 % 올랐다" 가 아니라
    # "금리가 몇 %p 움직였다" 는 뜻이다. 이름에 (%) 를 달아 그 차이를 적는다.
    ("US:RATE:UST10Y", "미국 국채 10년 (%)"),
)

#: **변동성 지수는 가격지수가 아니다.** VIX 는 옵션 내재변동성이라 "20 → 24"
#: 가 +20% 수익이 아니라 공포가 커진 것이다. 패널로 세우지 않고, 목록에서는
#: 가격지수와 갈라서 묶는다 — 등락에 손익 색도 쓰지 않는다.
VOLATILITY_INDICES = frozenset({"US:IDX:VIX", "US:IDX:VXN", "US:IDX:RVX"})

#: 순위표 4종 — **거래량(주식 수) 상위는 없다.**
#:
#: 2026-08-12 미장 거래량 상위 1·5위가 $1.52(RMCF, +126%) · $1.36(OFAL, +90.7%)
#: 였다. **하한을 안 걸어서가 아니다** — 하한($1·$5M)은 거래량 순위에도 걸려
#: 있었고 둘 다 통과했다. 주식 수로 세는 한 싼 쪽이 유리한 것은 **척도 자체의
#: 성질**이라 하한은 바닥만 자를 뿐 순위의 기울기를 못 바꾼다. 같은 날 거래대금
#: 상위는 MU·SPY·NVDA 였다 — 같은 자리를 훨씬 잘 채운다.
#:
#: 메일 브리핑이 먼저 같은 결론에 닿았다(`reporting/briefing.py` RANKINGS).
#: **두 곳이 다른 순위표를 들면 사용자가 어느 쪽을 믿을지 모른다** —
#: tests/dashboard 가 두 목록이 어긋나지 않는지 지킨다.
#:
#: ⚠️ **상승률과 하락률은 같은 모집단을 쓴다.** config 키 이름이 ``gainer_pool``
#: 이라 상승 전용처럼 읽히는데 아니다 — 두 표가 **거래대금 상위 같은 300종목**
#: 안에서 고른다. 한쪽에만 하한을 걸면 두 표가 서로 다른 세계를 보게 되고,
#: "상위 5개는 다 급등주인데 하위 5개는 듣도 보도 못한 종목" 같은 화면이 된다.
#: 키 이름은 창고에 이미 심겨 못 바꾸므로 여기 적어 둔다.
RANKINGS = (
    ("value", "거래대금 상위", "prices.value — 체결 금액"),
    ("market_cap", "시가총액 상위", "market_stats.market_cap"),
    ("gainers", "상승률 상위", "직전 세션 대비 등락률 — 거래대금 상위 모집단 안에서"),
    ("losers", "하락률 상위", "직전 세션 대비 등락률 — 상승률과 같은 모집단"),
)



def entity_names(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, str]:
    if not entities:
        return {}
    frame = store.get(
        UNIVERSE,
        as_of=as_of,
        entity=entities,
        lookback=10,
        columns=["name", "valid_from", "observed_at"],
    )
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "observed_at"]).groupby("entity_id").tail(1)
    return {str(row["entity_id"]): str(row["name"]) for row in latest.to_dict(orient="records")}


def _session(value: Any) -> str | None:
    stamp = pd.Timestamp(value)
    return None if pd.isna(stamp) else stamp.date().isoformat()


# -- 환율 --------------------------------------------------------------------


def fx(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """원달러 환율. 최신값·전일대비·시계열.

    **시장별 판에 넣지 않는다.** 환율은 국장의 지표도 미장의 지표도 아니라
    둘 사이의 다리다. 화면에서도 좌우로 갈리기 전의 공통 줄(KPI)에 있다 —
    카드와 그 스파크라인이 전부다. ``sessions``·``rates`` 는 그 스파크라인이
    쓴다.

    창고에 있는 것은 USDKRW 하나다 (KR-US 사이에서만 거래하므로). 다른
    통화쌍이 필요해지면 여기서 entity 를 넓힌다.
    """
    frame = store.get(FX, as_of=as_of, entity="FX:USDKRW", lookback=lookback)
    if frame.empty:
        return {"rate": None, "change": None, "sessions": [], "rates": []}
    ordered = frame.sort_values("valid_from")
    rates = ordered["rate"].astype(float)
    last = float(rates.iloc[-1])
    previous = float(rates.iloc[-2]) if len(rates) >= 2 else None
    return {
        "rate": last,
        "change": (last / previous - 1.0) if previous else None,
        "sessions": [pd.Timestamp(v).date().isoformat() for v in ordered["valid_from"]],
        "rates": [float(v) for v in rates],
    }


# -- 지수 ------------------------------------------------------------------


def _kind(entity_id: str) -> str:
    """``"price"`` 아니면 ``"volatility"``.

    화면이 둘을 섞지 않게 하는 유일한 표시다 — 변동성 지수의 등락률은 수익률이
    아니라 공포의 크기 변화라, 같은 표에서 초록·빨강으로 칠하면 "VIX 가 올라서
    좋다" 로 읽힌다.
    """
    return "volatility" if entity_id in VOLATILITY_INDICES else "price"


def indices(
    store: Store, *, as_of: datetime, market: str, exclude: set[str] | None = None
) -> dict[str, Any]:
    """한 시장의 **나머지** 지수 목록. 종가와 직전 세션 대비 등락률.

    ``exclude`` 는 이미 개별 패널로 세운 지수다(:func:`index_panels`). 대표
    지수를 여기서 고르지 않는다는 규칙은 그대로다 — 무엇이 대표인지는
    ``config.benchmark`` 만 알고, 이 함수는 그 결정을 **전달받을 뿐**이다.

    **목록은 자르지 않는다.** 국장 44종·미장 10종을 그냥 깔면 왼쪽이 몇 배
    높아지므로 패널 높이를 고정하고 안에서 스크롤한다(market.css). 순서만
    오늘 변동이 큰 순이고, 빠지는 지수는 위 패널로 옮겨 간 것뿐이다 —
    ``total`` 이 그 사실을 셀 수 있게 옮겨 가기 전 개수를 센다.

    `indices` 테이블에는 국장 KRX 업종·테마 지수와 해외 지수가 함께 산다
    (prices 와 나눈 이유는 store/tables.py 참고 — 종목 유니버스에 지수가
    섞이면 커버리지 통계가 오염된다).
    """
    excluded = exclude or set()
    frame = store.get(
        INDICES,
        as_of=as_of,
        lookback=RECENT_DAYS,
        market=market,
        columns=["entity_id", "market", "close", "valid_from"],
    )
    if frame.empty:
        return {"others": [], "total": 0, "excluded": sorted(excluded)}

    rows: list[dict[str, Any]] = []
    for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
        # 휴장·미수집 세션은 종가가 0 이나 NaN 으로 들어올 수 있다 — 없는 값을
        # 등락 계산에 섞으면 지수가 하루 만에 -100% 로 보인다.
        closes = group["close"].astype(float)
        closes = closes[closes > 0]
        if closes.empty:
            continue
        last = float(closes.iloc[-1])
        previous = float(closes.iloc[-2]) if len(closes) >= 2 else None
        rows.append(
            {
                "entity_id": str(entity),
                "market": str(group["market"].iloc[-1]),
                "kind": _kind(str(entity)),
                "close": last,
                "change": (last / previous - 1.0) if previous else None,
                "session": _session(group["valid_from"].iloc[-1]),
            }
        )

    others = [row for row in rows if row["entity_id"] not in excluded]
    # 가격지수 먼저, 변동성 지수는 뒤로 묶는다 — 한 목록에서 섞이면 VIX 의
    # +12% 가 나스닥의 +12% 와 같은 뜻으로 읽힌다. 그 안에서 오늘 많이 움직인
    # 순. 등락을 못 잰 지수는 뒤로 — 앞자리는 "오늘 무슨 일이 있었나" 에
    # 답하는 줄의 몫이다.
    others.sort(
        key=lambda row: (
            row["kind"] == "volatility",
            row["change"] is None,
            -abs(row["change"] or 0.0),
        )
    )
    return {
        "others": others,
        # 옮겨 가기 전 전체 개수. 화면이 "44종 중 2종은 위 패널" 이라고 적는다.
        "total": len(rows),
        "excluded": sorted(excluded),
    }


# -- 지수·ETF 개별 패널 ----------------------------------------------------------


def _panel_label(entity_id: str, kind: str) -> str:
    """화면에 찍히는 제목.

    ETF 는 **티커 그대로**다(`US:SPY` → `SPY`). 지수는 지수 이름이다
    (`KR:IDX:KOSPI` → `KOSPI`). ETF 자리에 추종 지수 이름을 넣지 않는다 —
    그게 대용치 바꿔치기다.
    """
    # 금리는 티커가 아니다. `US:RATE:UST10Y` 를 `:` 로 자르면 `RATE:UST10Y`
    # 라는 읽을 수 없는 이름이 나온다 — 실측 2026-08-21 화면.
    _, rate_marker, rate_name = entity_id.partition("RATE:")
    if rate_marker:
        return rate_name
    if kind == "etf":
        _, _, ticker = entity_id.partition(":")
        return ticker or entity_id
    _, marker, name = entity_id.partition("IDX:")
    return name if marker else entity_id


def _panel_frame(
    store: Store, *, as_of: datetime, lookback: int, market: str, kind: str, entities: list[str]
) -> dict[str, pd.DataFrame]:
    """패널 하나당 ``세션 → OHLC·거래량`` 표. **소스가 종류마다 다르다.**

    - ``kind="index"`` → `indices` 테이블 (국장 지수, KRX Open API)
    - ``kind="etf"``   → `prices` 테이블 (미장 ETF, LS 해외)

    ETF 는 ``read_prices`` 를 지난다 — 종가 0 세션을 걷어내는 자리가 창고에
    하나뿐이어야 하기 때문이다(store/prices.py). 지수는 그 헬퍼의 대상이
    아니라 여기서 같은 규칙(종가 0 제외)을 직접 적용한다.
    """
    columns = ["entity_id", "open", "high", "low", "close", "volume", "valid_from"]

    # **한 무리 안에 두 표가 섞인다.** 미장 패널은 대부분 ETF(=`prices`)인데
    # 국채 금리는 `indices` 에 산다. 예전에는 무리 전체를 한 표에서만 읽어서,
    # 국채 패널이 조용히 "미수집" 으로 빠졌다 (실측 2026-08-21).
    #
    # 티커가 아니라 **접두어로 가른다** — `US:RATE:` · `KR:IDX:` 처럼 축이
    # 이름에 적혀 있다.
    def _from_indices(entity: str) -> bool:
        return ":IDX:" in entity or ":RATE:" in entity

    if kind == "etf":
        price_ids = [e for e in entities if not _from_indices(e)]
        index_ids = [e for e in entities if _from_indices(e)]
    else:
        price_ids, index_ids = [], list(entities)

    frames = []
    if price_ids:
        frames.append(read_prices(
            store, as_of=as_of, entity=price_ids, lookback=lookback,
            market=market, columns=columns,
        ))
    if index_ids:
        frames.append(store.get(
            INDICES, as_of=as_of, entity=index_ids, lookback=lookback,
            market=market, columns=columns,
        ))
    frames = [f for f in frames if not f.empty]
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    out: dict[str, pd.DataFrame] = {}
    if frame.empty:
        return out
    for entity, group in frame.groupby("entity_id"):
        rows = group.copy()
        rows["session"] = [_session(v) for v in rows["valid_from"]]
        rows = rows[rows["session"].notna() & (rows["close"].astype(float) > 0)]
        if rows.empty:
            continue
        # 같은 세션이 여러 관측으로 들어올 수 있다(정정본). 마지막 것만.
        rows = rows.sort_values("valid_from").drop_duplicates("session", keep="last")
        out[str(entity)] = rows.set_index("session")
    return out


def _has_ohlc(rows: pd.DataFrame) -> bool:
    """캔들을 그릴 수 있나 — **넷이 다 있어야 봉이다.**

    FRED 가 주는 미장 지수는 종가만 있고 시가·고가·저가가 전부 NULL 이다.
    없는 셋을 종가로 채우면 모든 봉이 십자가가 되는데, 그건 "그날 변동이
    없었다" 는 **다른 사실**이다. 그래서 넷이 다 있을 때만 캔들이고,
    아니면 선으로 그리고 화면이 그 이유를 적는다.
    """
    for column in ("open", "high", "low"):
        values = pd.to_numeric(rows[column], errors="coerce")
        if values.isna().any() or (values <= 0).any():
            return False
    return True


def instrument_panels(
    store: Store, *, as_of: datetime, lookback: int, market: str, benchmark_id: str
) -> dict[str, Any]:
    """그 시장의 패널들. **국장은 지수, 미장은 ETF** — 성격이 다르다.

    ## 왜 미장만 ETF 인가

    창고의 미장 지수(`US:IDX:*`)는 FRED 에서 오고 **종가만 있다** — 실측으로
    open/high/low/volume 이 전부 NULL 이다(SP500 0/381, 나머지 0/52~53).
    캔들을 그릴 수 없고, 없는 값을 지어내면 화면이 창고보다 많이 아는 것처럼
    보인다. 반면 `US:SPY`·`QQQ`·`DIA` 는 LS 해외에서 와서 OHLC 가 1253/1253
    완전하다.

    ## 그래서 **이름도 ETF 티커로 적는다**

    지수 이름을 달고 ETF 데이터를 그리는 것이 이 저장소가 금지하는 대용치
    바꿔치기다. "S&P 500" 이라 쓰고 SPY 를 그리면 화면이 거짓말을 시작한다.
    제목은 ``label``(= 티커)이고, 무엇을 좇는지는 ``tracks`` 로 **부제에만**
    적는다.

    ## ``benchmark`` 와 ``role`` 은 다른 것이다 — 같은 필드를 재사용하지 않는다

    - ``role="primary"`` — **화면에서** 그 칸의 첫 자리다.
    - ``benchmark=True`` — **`config.benchmark` 가 정한** 지수다. 회계·백테스트가
      우리를 견주는 그것이고, 여기서 바꾸면 성적의 기준이 조용히 바뀐다.

    미장에서 둘이 갈린다: SPY 는 화면 대표(``role="primary"``)이지만
    **벤치마크가 아니다**(``benchmark=False``). 벤치마크는 여전히
    ``config.benchmark.us_index`` = `US:IDX:SP500` 이고 이 화면은 그걸 안 바꾼다.
    한 필드로 뭉치면 언젠가 화면 사정으로 벤치마크가 갈아치워진다.

    ## 없으면 자리를 지키고 이유를 적는다

    창고에 없는 것은 ``missing`` 으로 나가 패널 자리를 유지한다. 수집이
    들어오면 **저절로 찬다** — 여기 고칠 것이 없다. 실제로 그렇게 됐다:
    `US:SOXX` 가 오래 비어 있다가 2026-08-19 수집이 붙자 이 파일을 한 줄도
    안 고치고 채워졌다.
    """
    kind = PANEL_KIND[market]
    if kind == "etf":
        wanted = [
            (entity, "primary" if i == 0 else "companion", tracks)
            for i, (entity, tracks) in enumerate(US_ETF_PANELS)
        ]
    else:
        wanted = [(benchmark_id, "primary", None)]
        wanted += [(entity, "companion", None) for entity in COMPANION_INDICES.get(market, ())]

    history = _panel_frame(
        store, as_of=as_of, lookback=lookback, market=market, kind=kind,
        entities=[entity for entity, _, _ in wanted],
    )
    # 봉 전환 버튼이 무엇을 켤지. **패널 수만큼 창고를 열지 않는다** — 한 번
    # 읽어 나눈다(`panel_intervals`). 첫 그림에 같이 실어 보내는 이유는,
    # 화면이 버튼을 그리려고 패널마다 따로 물어보면 첫 로드에 요청이 여섯 개
    # 더 나가기 때문이다.
    intervals = panel_intervals(
        store, as_of=as_of, market=market, entities=[entity for entity, _, _ in wanted]
    )

    panels: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for entity, role, tracks in wanted:
        label = _panel_label(entity, kind)
        # 벤치마크는 config 가 정한 그 이름일 때만이다. 미장 ETF 는 절대 아니다.
        is_benchmark = kind == "index" and entity == benchmark_id
        # **금리는 ETF 가 아니다.** 무리(kind)는 "etf" 지만 국채 금리는 대용치가
        # 아니라 원래 값이다. 무리 이름을 그대로 물려주면 화면이 "ETF · S&P 500
        # 추종" 같은 배지를 금리에 달고, 벤치마크 후보 검사도 ETF 규칙으로
        # 돈다. 칸마다 자기 종류를 든다.
        row_kind = "rate" if ":RATE:" in entity else kind
        stub = {
            "entity_id": entity, "market": market, "kind": row_kind, "label": label,
            "tracks": tracks, "role": role, "benchmark": is_benchmark,
        }
        rows = history.get(entity)
        if rows is None or rows.empty:
            missing.append(stub)
            continue

        closes = rows["close"].astype(float)
        first, last = float(closes.iloc[0]), float(closes.iloc[-1])
        candles = _has_ohlc(rows)
        # **RSI 는 여기서 한 번만 낸다.** 화면이 JS 로 다시 구하면 평활 방식이
        # 갈릴 수 있고, 그러면 메일의 RSI 와 화면의 RSI 가 달라진다.
        rsi_period = int(store.config("reporting.rsi_period", as_of=as_of))
        rsi_series = wilder_rsi_series(closes, rsi_period)

        panels.append(
            {
                **stub,
                # 지금 그린 것은 일봉이다. 화면의 봉 전환 버튼이 이 값으로
                # 어느 버튼에 불을 켤지 정한다.
                "interval": DAILY_INTERVAL,
                "intervals": intervals.get(entity, [DAILY_INTERVAL, WEEKLY_INTERVAL]),
                "partial": None,
                "reason": None,
                "sessions": [str(s) for s in rows.index],
                # ECharts 캔들 순서 [시가, 종가, 저가, 고가]. 캔들을 못 그리면
                # 빈 목록이고 화면은 ``closes`` 로 선을 긋는다.
                "ohlc": (
                    [
                        [float(r["open"]), float(r["close"]), float(r["low"]), float(r["high"])]
                        for _, r in rows.iterrows()
                    ]
                    if candles
                    else []
                ),
                "closes": [float(v) for v in closes],
                # RSI 줄. 계산은 `quant_rl_trading.indicators` 한 벌만 쓴다 —
                # 화면과 메일이 각자 구하면 같은 지수의 RSI 가 두 개가 된다.
                # 창이 모자라는 앞머리는 None 이라 화면이 그 구간을 안 긋는다.
                "rsi": rsi_series,
                "rsi_period": rsi_period,
                "has_ohlc": candles,
                "close": last,
                "session": str(rows.index[-1]),
                "change": (last / float(closes.iloc[-2]) - 1.0) if len(closes) >= 2 else None,
                "total": (last / first - 1.0) if first else None,
                "first_session": str(rows.index[0]),
            }
        )

    return {"panels": panels, "missing": missing, "lookback": lookback, "kind": kind}


# -- 패널의 봉 전환: 분봉 · 일봉 · 주봉 ---------------------------------------------
#
# 마켓 탭 패널은 오래 **일봉 한 종류**였다. 트레이딩 탭에는 이미 분봉 전환이
# 있었고(1m/5m/15m/1H/4H/1D), 같은 질문 — "지금 이게 어떻게 움직이고 있나" —
# 을 마켓 탭에서 물으면 답이 하루 한 점씩밖에 없었다.
#
# 여기서 새로 만드는 것은 **주봉 하나뿐**이다. 분봉은 트레이딩 탭의 서비스를
# 그대로 부른다 — 같은 표(`prices_intraday`)를 읽는 코드가 두 벌이 되면
# 한쪽만 고쳐진 채로 남는다.


def panel_intervals(
    store: Store, *, as_of: datetime, market: str, entities: list[str]
) -> dict[str, list[str]]:
    """패널마다 **실제로 그릴 수 있는** 봉 목록.

    일봉·주봉은 항상 있다 — 패널로 세워졌다는 것 자체가 일봉이 있다는 뜻이고,
    주봉은 그 일봉을 접은 것이라 따로 물어볼 창고가 없다.

    분봉은 다르다. 수집기가 **보유·워치리스트·shadow 보유 종목만** 받으므로
    (`collectors/intraday_collector.py`) 지수(`KR:IDX:*`)와 미장 ETF 에는
    한 봉도 없다. 없는 것을 켜 두면 눌렀을 때 빈 화면이 뜨고, 그게 고장인지
    수집 범위 밖인지 사용자가 구분 못 한다 — 그래서 창고에 물어보고 끈다.
    """
    have = trading_service.available_intraday_intervals_by_entity(
        store, as_of=as_of, entities=entities, market=market
    )
    return {
        entity: [*have.get(entity, []), DAILY_INTERVAL, WEEKLY_INTERVAL]
        for entity in entities
    }


def weekly_bars(
    rows: pd.DataFrame, *, market: str, as_of: datetime
) -> list[dict[str, Any]]:
    """일봉을 **그 시장의 거래일 달력**으로 주 단위로 접는다.

    ## 집계 규칙

    시가 = 그 주 **첫 세션**의 open · 고가 = max(high) · 저가 = min(low) ·
    종가 = **마지막 세션**의 close · 거래량 = 합.

    시가·종가를 pandas 의 ``first``/``last`` 로 접지 않는다 — 그 둘은 NaN 을
    건너뛰므로, 첫 세션의 open 이 비어 있으면 **둘째 세션의 open** 이 그 주의
    시가로 올라앉는다. 없는 값이 조용히 다른 값으로 바뀌는 종류의 거짓이다.

    ## 주의 경계를 왜 달력에 물어보나

    묶는 단위 자체는 ISO 주(월~일)다. 달력이 필요한 곳은 **그 주가 끝났는지**
    를 가르는 자리다. "금요일이 지났으면 끝난 주" 로 재면 금요일이 휴장인 주가
    영원히 미완성으로 남고, 연휴가 낀 주는 세션이 둘뿐인데도 고장처럼 보인다.
    그래서 그 주의 거래일을 달력에서 받아 **마지막 거래일이 지났는가**로
    가른다.

    ## 미완성 주를 버리지 않는다

    오늘이 수요일이면 이번 주 봉은 세션 세 개짜리다. 그게 맞는 모습이고,
    지우면 화면에서 최신이 사라진다 — 대신 ``complete=False`` 와
    ``sessions``/``expected`` 를 실어 **미완성이라는 사실이 화면에 남게** 한다.
    """
    if rows.empty:
        return []

    days = [date.fromisoformat(str(session)) for session in rows.index]
    weeks = [day - timedelta(days=day.weekday()) for day in days]
    frame = rows.copy()
    frame["_day"] = days
    frame["_week"] = weeks
    frame = frame.sort_values("_day")

    starts = sorted(set(weeks))
    # 달력은 **한 번만** 연다. 주마다 부르면 같은 달력을 백 번 훑는다.
    # 끝을 마지막 주의 일요일까지 잡는 이유: 진행 중인 주는 남은 거래일이
    # 아직 미래라, 오늘까지만 물어보면 "이번 주는 3거래일짜리" 가 된다.
    span = trading_days(Market(market), starts[0], starts[-1] + timedelta(days=6))
    expected: dict[date, list[date]] = {}
    for day in span:
        expected.setdefault(day - timedelta(days=day.weekday()), []).append(day)

    today = as_of.date()
    bars: list[dict[str, Any]] = []
    for start in starts:
        group = frame[frame["_week"] == start]
        opens = pd.to_numeric(group["open"], errors="coerce")
        highs = pd.to_numeric(group["high"], errors="coerce")
        lows = pd.to_numeric(group["low"], errors="coerce")
        closes = pd.to_numeric(group["close"], errors="coerce")
        volumes = pd.to_numeric(group["volume"], errors="coerce").fillna(0.0)
        week_days = expected.get(start, [])
        bars.append(
            {
                # x 축에 찍히는 값은 **그 주의 월요일**이다. 마지막 세션으로
                # 찍으면 진행 중인 주의 라벨이 하루마다 바뀌어, 새로고침할
                # 때마다 마지막 봉이 옮겨 다니는 것처럼 보인다.
                "week": start.isoformat(),
                "open": None if pd.isna(opens.iloc[0]) else float(opens.iloc[0]),
                "high": None if highs.isna().all() else float(highs.max()),
                "low": None if lows.isna().all() else float(lows.min()),
                "close": float(closes.iloc[-1]),
                "volume": float(volumes.sum()),
                "sessions": len(group),
                # 그 주에 원래 몇 거래일이 있(었)나. 세션 수와 어긋나면
                # 수집이 빈 것이고, 그 차이를 화면이 볼 수 있어야 한다.
                "expected": len(week_days),
                # 마지막 거래일이 지났는가 = 그 주가 끝났는가.
                "complete": bool(week_days) and week_days[-1] <= today,
                "first_session": group["_day"].iloc[0].isoformat(),
                "last_session": group["_day"].iloc[-1].isoformat(),
            }
        )
    return bars


def _ma(closes: list[float]) -> dict[str, list[float | None]]:
    """이동평균 셋. 트레이딩 탭 ``candles()`` 와 같은 창(5·20·60)이다."""
    series = pd.Series(closes, dtype="float64")
    return {
        f"ma{window}": [
            None if pd.isna(value) else float(value)
            for value in series.rolling(window).mean()
        ]
        for window in (5, 20, 60)
    }


def panel_candles(
    store: Store,
    *,
    as_of: datetime,
    lookback: int,
    market: str,
    entity_id: str,
    interval: str,
) -> dict[str, Any]:
    """패널 하나를 그 구간으로 다시 그린 것. **패널 dict 와 같은 모양이다.**

    ``instrument_panels`` 이 내놓는 패널과 키가 같다(``sessions``·``ohlc``·
    ``closes``·``has_ohlc``·``close``·``change``·``total``…). 화면의 그리는
    코드가 "처음 그릴 때" 와 "버튼을 눌렀을 때" 를 분기 없이 같이 쓰기
    위해서다 — 두 벌이 되면 한쪽만 고쳐진 채로 남는다.

    없으면 **빈 채로 두고 ``reason`` 에 이유를 적는다.** 분봉이 없는 종목이
    흔하고(수집 대상이 32종목뿐이다), 그때 화면이 "왜 비었나" 를 말하지
    못하면 사용자는 고장으로 읽는다.
    """
    if interval not in PANEL_INTERVALS:
        raise ValueError(f"모르는 interval: {interval!r} ({PANEL_INTERVALS} 중 하나여야 한다)")

    kind = PANEL_KIND[market]
    available = panel_intervals(
        store, as_of=as_of, market=market, entities=[entity_id]
    )[entity_id]
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "market": market,
        "kind": kind,
        "label": _panel_label(entity_id, kind),
        "interval": interval,
        "intervals": available,
        "sessions": [],
        "ohlc": [],
        "closes": [],
        "volume": [],
        "ma": {},
        "has_ohlc": False,
        "close": None,
        "change": None,
        "total": None,
        "session": None,
        "first_session": None,
        "partial": None,
        "reason": None,
    }

    if interval in trading_service.INTRADAY_INTERVALS:
        if interval not in available:
            base["reason"] = (
                f"{entity_id} 의 {interval} 봉이 창고에 없다 — 분봉 수집은 "
                "보유·워치리스트·shadow 보유 종목만 받는다(전 종목이 아니다)."
            )
            return base
        # **분봉은 트레이딩 탭의 서비스를 그대로 부른다.** 같은 표를 읽는
        # 코드를 두 벌 두지 않는다 — 당일만 보여주는 규칙(INTRADAY_TODAY_ONLY)
        # 도 저쪽에 있고, 여기서 다시 적으면 두 화면이 갈라진다.
        data = trading_service.intraday_candles(
            store, as_of=as_of, entity_id=entity_id, market=market, interval=interval
        )
        closes = [row[1] for row in data["ohlc"]]
        base.update(
            {
                "sessions": data["sessions"],
                "ohlc": data["ohlc"],
                "closes": closes,
                "volume": data["volume"],
                "ma": data["ma"],
                "has_ohlc": bool(data["ohlc"]),
            }
        )
        if not closes:
            base["reason"] = (
                f"{entity_id} 의 {interval} 봉이 오늘 아직 없다 — 분봉은 "
                "당일 것만 보여준다(장 시작 전이거나 수집이 아직 안 돌았다)."
            )
            return base
        base.update(
            {
                "close": closes[-1],
                "change": (closes[-1] / closes[-2] - 1.0) if len(closes) >= 2 else None,
                "total": (closes[-1] / closes[0] - 1.0) if closes[0] else None,
                "session": data["sessions"][-1],
                "first_session": data["sessions"][0],
            }
        )
        return base

    # 일봉·주봉. **주봉은 그 일봉을 접은 것**이라 읽는 표가 같다.
    window = WEEKLY_LOOKBACK_DAYS if interval == WEEKLY_INTERVAL else lookback
    history = _panel_frame(
        store, as_of=as_of, lookback=window, market=market, kind=kind,
        entities=[entity_id],
    )
    rows = history.get(entity_id)
    if rows is None or rows.empty:
        base["reason"] = f"{entity_id} 의 시세가 창({window}일) 안에 없다 — 아직 수집되지 않았다."
        return base

    # 넷이 다 있을 때만 봉이다 — 주봉도 같은 규칙을 물려받는다. 일봉의
    # 시가·고가·저가가 없으면(FRED 미장 지수) 그것을 접은 주봉에도 없다.
    candles = _has_ohlc(rows)

    if interval == WEEKLY_INTERVAL:
        bars = weekly_bars(rows, market=market, as_of=as_of)
        sessions = [bar["week"] for bar in bars]
        closes = [bar["close"] for bar in bars]
        ohlc = (
            [[bar["open"], bar["close"], bar["low"], bar["high"]] for bar in bars]
            if candles
            else []
        )
        volume = [bar["volume"] for bar in bars]
        last = bars[-1]
        # **미완성 주를 지우지 않는다.** 대신 그 사실을 실어 보낸다 —
        # 화면이 마지막 봉에 "진행 중" 을 적을 수 있게.
        base["partial"] = (
            None
            if last["complete"]
            else {
                "week": last["week"],
                "sessions": last["sessions"],
                "expected": last["expected"],
                "last_session": last["last_session"],
            }
        )
        first_session, last_session = bars[0]["week"], last["week"]
    else:
        sessions = [str(session) for session in rows.index]
        closes = [float(value) for value in rows["close"].astype(float)]
        ohlc = (
            [
                [float(r["open"]), float(r["close"]), float(r["low"]), float(r["high"])]
                for _, r in rows.iterrows()
            ]
            if candles
            else []
        )
        volume = [
            float(value)
            for value in pd.to_numeric(rows["volume"], errors="coerce").fillna(0.0)
        ]
        first_session, last_session = sessions[0], sessions[-1]

    base.update(
        {
            "sessions": sessions,
            "ohlc": ohlc,
            "closes": closes,
            "volume": volume,
            "ma": _ma(closes),
            "has_ohlc": candles,
            "close": closes[-1],
            "change": (closes[-1] / closes[-2] - 1.0) if len(closes) >= 2 else None,
            "total": (closes[-1] / closes[0] - 1.0) if closes[0] else None,
            "session": last_session,
            "first_session": first_session,
        }
    )
    return base


# -- 시세로 만드는 것 — 시장 폭 · 많이 움직인 종목 ---------------------------------


def session_changes(store: Store, *, as_of: datetime, market: str) -> pd.DataFrame:
    """종목별 **직전 세션 대비 등락**. 시장 폭·movers 가 같은 표를 나눠 쓴다.

    돌려주는 표는 entity_id 색인에 ``close``·``prev``·``change``·``value``·
    ``session`` 을 담는다. 종가가 하나뿐인 종목(신규 상장·거래정지 복귀)은
    ``change`` 가 NaN 이다 — 0 으로 채우면 "보합" 이라는 다른 사실이 된다.
    """
    # 휴장일 종가 0 은 ``read_prices`` 가 뺀다. 여기서 또 거르면 규칙이 두
    # 벌이 되고, 두 벌은 언젠가 서로 달라진다.
    frame = read_prices(
        store,
        as_of=as_of,
        lookback=RECENT_DAYS,
        market=market,
        columns=["entity_id", "close", "value", "valid_from"],
    )
    empty = pd.DataFrame(columns=["close", "prev", "change", "value", "session"])
    if frame.empty:
        return empty

    live = frame.copy()
    live = live.sort_values(["entity_id", "valid_from"]).drop_duplicates(
        ["entity_id", "valid_from"], keep="last"
    )

    tail = live.groupby("entity_id").tail(2)
    last = tail.groupby("entity_id").tail(1).set_index("entity_id")
    # 두 행이 있는 종목만 직전 종가를 갖는다. head(1) 은 한 행짜리 종목에서
    # tail(1) 과 같은 행을 집으므로 개수로 한 번 거른다.
    pair = tail.groupby("entity_id")["close"].size()
    prev = (
        tail[tail["entity_id"].isin(pair[pair >= 2].index)]
        .groupby("entity_id")
        .head(1)
        .set_index("entity_id")
    )

    out = pd.DataFrame(index=last.index)
    out["close"] = last["close"].astype(float)
    out["prev"] = prev["close"].astype(float)
    out["change"] = out["close"] / out["prev"] - 1.0
    out["value"] = last["value"].astype(float)
    out["session"] = [_session(v) for v in last["valid_from"]]
    return out


def breadth(changes: pd.DataFrame, *, market: str) -> dict[str, Any]:
    """시장 폭 — 오늘 몇 종목이 오르고 몇 종목이 내렸나.

    지수 하나로는 "지수는 올랐는데 종목의 70%는 내렸다" 를 볼 수 없다. 그리고
    이건 **시세만 있으면 만들 수 있다** — 미장에 시총·수급이 없어도 이 패널은
    국장과 같은 밀도로 찬다.

    등락을 못 잰 종목은 어느 칸에도 넣지 않는다. 보합에 넣으면 "오늘 안
    움직였다" 라는 다른 사실이 된다 — 별도 칸(``unmeasured``)으로 센다.
    """
    if changes.empty:
        return {
            "advancers": 0, "decliners": 0, "unchanged": 0, "unmeasured": 0,
            "traded": 0, "value": None, "session": None,
            "currency": CURRENCY.get(market, ""),
        }
    change = changes["change"]
    measured = change.notna()
    return {
        "advancers": int((change > 0).sum()),
        "decliners": int((change < 0).sum()),
        "unchanged": int((change == 0).sum()),
        "unmeasured": int((~measured).sum()),
        "traded": len(changes),
        "value": float(changes["value"].fillna(0.0).sum()),
        "session": next((s for s in changes["session"] if s), None),
        "currency": CURRENCY.get(market, ""),
    }


# -- 시가총액 상위 -------------------------------------------------------------


def leaders(
    store: Store, changes: pd.DataFrame, caps: pd.Series, *, as_of: datetime, limit: int
) -> list[dict[str, Any]]:
    """시가총액 상위 ``limit`` 종목. 순위는 오늘 알 수 있는 마지막 시총 기준이다.

    시총은 **읽지 않고 받는다**(`_rank_caps` 가 읽은 것). 순위표와 트리맵이
    각자 `market_stats` 를 열면 같은 파티션을 두 번 훑고, 더 나쁘게는 둘이
    서로 다른 세션의 시총으로 줄을 세울 수 있다 — 화면 안에서 같은 "시총
    1위" 가 갈린다.

    등락률도 ``changes``(시세로 이미 만든 표)에서 꺼내 쓴다 — 같은 이유다.
    """
    if caps.empty:
        return []
    top = caps.sort_values(ascending=False).head(limit)
    entities = [str(e) for e in top.index]

    names = entity_names(store, as_of=as_of, entities=entities)
    changed = changes["change"] if not changes.empty else pd.Series(dtype=float)

    rows: list[dict[str, Any]] = []
    for entity, cap in top.items():
        entity = str(entity)
        change = changed.get(entity)
        rows.append(
            {
                "entity_id": entity,
                "name": names.get(entity, entity),
                "market_cap": float(cap),
                # 종가가 아예 없는 종목(오늘 거래정지 등)은 표에 키가 없다.
                # 거래는 있었는데 직전이 없는 경우와 같은 값(None)이지만, 둘 다
                # "등락을 못 잰다" 라는 같은 사실이라 화면에서 구분하지 않는다.
                "change": None if change is None or pd.isna(change) else float(change),
            }
        )
    return rows


# -- 순위표 3종 -----------------------------------------------------------------


def ranking_floor(store: Store, *, as_of: datetime, market: str) -> dict[str, Any] | None:
    """순위 하한. **`config.reporting` 에서 읽는다 — 여기서 만들지 않는다.**

    메일 브리핑이 쓰는 것과 **같은 값**이다(`reporting/briefing.py` `Floor`).
    두 화면이 다른 "상승 종목" 을 말하면 안 되므로 출처가 하나여야 한다
    (불변식 10).

    셋을 같이 거는 이유: 거래대금만 걸면 저가주가 호가 한 칸으로 몇 %씩 뛰는
    것을 못 막고, 주가만 걸면 거래가 거의 없는 고가주가 들어온다. 거기에
    순위 하한(``pool``)이 셋째 축이다 — 상승률은 **거래대금 상위 ``pool``
    안에서만** 고른다.

    그 섹션이 창고에 없으면 ``None`` 이다. **기본값으로 때우지 않는다** —
    코드가 조용히 자기 숫자를 들면 화면과 메일이 갈라지고, 갈라진 사실조차
    아무도 모른다. 화면이 "설정이 없다" 고 말하는 편이 낫다.
    """
    try:
        section = store.config("reporting", as_of=as_of)
    except Exception:
        return None
    currency = CURRENCY.get(market, "")
    suffix = currency.lower()
    try:
        return {
            "currency": currency,
            "min_turnover": float(section[f"min_turnover_{suffix}"]),
            "min_price": float(section[f"min_price_{suffix}"]),
            # 상승률·하락률 **두 표가 공유하는** 모집단이다. 이름이 gainer 로
            # 시작하는 것은 이 키가 상승 표에서 먼저 생겼기 때문이다.
            "pool": int(section["gainer_pool"]),
            # 줄 수도 여기서 온다. 한때 모듈 상수였는데, 같은 값을 두 곳에
            # 두면 언젠가 화면과 메일이 다른 줄 수를 든다 (불변식 10).
            "rows": int(section["ranking_rows"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _rank_caps(store: Store, *, as_of: datetime, market: str) -> tuple[pd.Series, str | None]:
    """종목별 최신 시가총액과 그 세션.

    ``until=as_of`` 를 **반드시** 준다. ``lookback`` 은 valid_from 의 하한만
    자르므로, 표지 날짜가 미래인 행이 있으면 그것이 "최신" 으로 뽑힌다 —
    창고의 미장 ``shares`` 에 2028-08-01 짜리 행이 실제로 있다. 창 없이 읽으면
    2년 뒤 값으로 순위를 매기게 된다(`reporting/briefing.py` `market_caps` 와
    같은 이유, 같은 처방).

    창은 ``CAP_RECENT_DAYS`` 다 — 시총 수집이 시세보다 며칠 밀리기 때문이고,
    등락 창(RECENT_DAYS)을 그대로 쓰면 그 며칠에 표가 통째로 사라진다.

    **한 세션으로 맞춘다.** 종목마다 각자의 마지막 시총을 쓰면 어제 값과
    지난주 값이 한 줄에 섞여 순위가 뒤집힌다 — 가장 최근 세션 하나만 남기고,
    그 세션을 같이 돌려준다. 화면은 그 날짜를 적어야 한다.
    """
    frame = store.get(
        MARKET_STATS,
        as_of=as_of,
        lookback=CAP_RECENT_DAYS,
        until=as_of,
        market=market,
        columns=["entity_id", "metric", "value", "valid_from"],
    )
    if frame.empty:
        return pd.Series(dtype=float), None
    caps = frame[frame["metric"] == "market_cap"].copy()
    if caps.empty:
        return pd.Series(dtype=float), None
    caps["session"] = [_session(v) for v in caps["valid_from"]]
    sessions = sorted({s for s in caps["session"] if s})
    if not sessions:
        return pd.Series(dtype=float), None
    latest = caps[caps["session"] == sessions[-1]].drop_duplicates("entity_id", keep="last")
    return latest.set_index("entity_id")["value"].astype(float), sessions[-1]


def rankings(
    store: Store,
    changes: pd.DataFrame,
    caps: pd.Series | None = None,
    cap_session: str | None = None,
    *,
    as_of: datetime,
    market: str,
) -> dict[str, Any]:
    """한 시장의 순위표 3종. 시세는 ``changes``(이미 읽은 판)를 나눠 쓴다.

    각 줄은 **종목명 · 가격 · 등락률 · 시총 + 그 표의 기준값**을 든다. 시총을
    모든 표에 싣는 이유는 "거래대금 1위가 대형주인가 아닌가" 가 그 자체로
    읽을거리이기 때문이다.

    ## 표마다 세션이 다를 수 있다

    시세와 시총은 다른 수집기가 넣는다. 실측(2026-08-15): 국장 시세는 08-14
    인데 **시총은 08-11** 이다. 표마다 자기 ``session`` 을 들고 나가고, 화면이
    그걸 적는다 — 나란히 놓으면 같은 날로 읽힌다.
    """
    floor = ranking_floor(store, as_of=as_of, market=market)
    base = {
        "market": market,
        "currency": CURRENCY.get(market, ""),
        "floor": floor,
        "rows": None if floor is None else floor["rows"],
        "universe": 0 if changes.empty else len(changes),
        "eligible": 0,
        "tables": [],
    }
    if floor is None:
        # 하한을 모르면 순위를 매기지 않는다. 무엇이 없는지는 화면이 말한다.
        base["reason"] = (
            "config.reporting 이 창고에 없다 — 순위 하한(거래대금·주가·모집단)을 "
            "읽을 수 없다. seed_config_defaults 로 심어야 한다."
        )
        return base

    # 시총은 한 판에서 한 번만 읽는다(`market_panel`). 안 주고 부르면 여기서
    # 읽는다 — 이 함수 하나만 떼어 부르는 자리(테스트)를 위해서다.
    if caps is None:
        caps, cap_session = _rank_caps(store, as_of=as_of, market=market)
    session = None if changes.empty else next((s for s in changes["session"] if s), None)

    if changes.empty:
        liquid = changes
    else:
        liquid = changes[
            (changes["value"].fillna(0.0) >= floor["min_turnover"])
            & (changes["close"] >= floor["min_price"])
        ]
    base["eligible"] = len(liquid)

    limit = floor["rows"]

    def rows_of(
        frame: pd.DataFrame, column: str, *, ascending: bool = False
    ) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        top = frame.sort_values(column, ascending=ascending).head(limit)
        entities = [str(e) for e in top.index]
        names = entity_names(store, as_of=as_of, entities=entities)
        out: list[dict[str, Any]] = []
        for entity, row in top.iterrows():
            key = str(entity)
            change = row.get("change")
            close = row.get("close")
            cap = caps.get(key)
            out.append(
                {
                    "entity_id": key,
                    # 미장 유니버스의 name 은 티커 그대로다(NVDA/NVDA) — 없는
                    # 이름을 지어내지 않고 티커를 그대로 쓴다.
                    "name": names.get(key, key),
                    "close": None if close is None or pd.isna(close) else float(close),
                    "change": None if change is None or pd.isna(change) else float(change),
                    "market_cap": None if cap is None or pd.isna(cap) else float(cap),
                    "metric": float(row[column]),
                }
            )
        return out

    for key, label, sort_by in RANKINGS:
        if key == "market_cap":
            if caps.empty:
                base["tables"].append(
                    {"key": key, "label": label, "sort_by": sort_by, "session": None,
                     "rows": [], "pooled": None}
                )
                continue
            frame = pd.DataFrame({"market_cap": caps})
            if not changes.empty:
                frame = frame.join(changes[["close", "change"]], how="left")
            base["tables"].append(
                {"key": key, "label": label, "sort_by": sort_by, "session": cap_session,
                 "rows": rows_of(frame, "market_cap"), "pooled": None}
            )
            continue
        if key in ("gainers", "losers"):
            # **거래대금 상위 pool 안에서만 고른다.** 이 축이 없으면 등락률
            # 순위는 시황이 아니라 동전주 목록이 된다. 두 표가 **같은 모집단**을
            # 쓴다 — 한쪽에만 걸면 서로 다른 세계를 보게 된다.
            pool = liquid.sort_values("value", ascending=False).head(floor["pool"])
            measured = pool[pool["change"].notna()] if not pool.empty else pool
            base["tables"].append(
                {"key": key, "label": label, "sort_by": sort_by, "session": session,
                 "rows": rows_of(measured, "change", ascending=key == "losers"),
                 "pooled": len(pool)}
            )
            continue
        base["tables"].append(
            {"key": key, "label": label, "sort_by": sort_by, "session": session,
             "rows": rows_of(liquid, key), "pooled": None}
        )
    return base


# -- 거시지표 ------------------------------------------------------------------


def macro_recent(store: Store, *, as_of: datetime, market: str) -> list[dict[str, Any]]:
    """한 시장에서 최근 발표된 거시지표. **이미 발표된 것만** — 예정은
    뉴스·일정 탭의 몫이다.

    지표당 최신 관측만 남긴다. 같은 발표가 여러 수집 회차에 걸쳐 여러 행으로
    쌓일 수 있어서다(자연키 = entity_id·scheduled_at, 정정본은 새 행).
    """
    frame = store.get(
        MACRO_RELEASES,
        as_of=as_of,
        lookback=60,
        market=market,
        columns=[
            "entity_id", "market", "indicator", "release_name", "scheduled_at",
            "actual", "previous", "unit", "status", "observed_at",
        ],
    )
    if frame.empty:
        return []
    released = frame[
        (frame["status"] == "released") & frame["actual"].notna() & (frame["scheduled_at"] <= as_of)
    ]
    if released.empty:
        return []
    # 같은 자연키(entity_id, scheduled_at)의 최신 관측만.
    deduped = released.sort_values("observed_at").groupby(
        ["entity_id", "scheduled_at"], as_index=False
    ).last()
    top = deduped.sort_values("scheduled_at", ascending=False).head(MACRO_ROWS)
    return [
        {
            "entity_id": str(row["entity_id"]),
            "market": str(row["market"]),
            "indicator": str(row["indicator"]),
            "release_name": str(row["release_name"]),
            "scheduled_at": row["scheduled_at"].isoformat(),
            "actual": float(row["actual"]) if pd.notna(row["actual"]) else None,
            "previous": float(row["previous"]) if pd.notna(row["previous"]) else None,
            "unit": str(row["unit"]) if pd.notna(row["unit"]) else "",
        }
        for row in top.to_dict(orient="records")
    ]


# -- 시장 한 칸 -----------------------------------------------------------------


def market_panel(
    store: Store,
    *,
    as_of: datetime,
    lookback: int,
    market: str,
    headline: str,
    live_quotes: Any = None,
    live_index: Any = None,
) -> dict[str, Any]:
    """한 시장의 판 전부. KR·US 가 **같은 함수, 같은 모양**이다.

    시세는 한 번만 읽는다(`session_changes`). 시장 폭·movers·리더 등락률·트리맵이
    전부 그 한 표에서 나온다 — 패널마다 따로 읽으면 같은 파티션을 네 번 연다.

    ``universe`` 는 그 시장에서 오늘 시세가 잡힌 종목 수다. 명단
    (`universe` 테이블) 이 아니라 **거래가 관측된 수**라 breadth 의 분모와
    같은 숫자다.
    """
    changes = session_changes(store, as_of=as_of, market=market)
    # 시총도 한 번만 읽는다 — 순위표·리더·트리맵이 **같은 세션의 같은 표**를
    # 나눠 쓴다. 각자 읽으면 창고를 세 번 열고, 셋이 서로 다른 세션의 시총으로
    # 줄을 설 수 있다.
    caps, cap_session = _rank_caps(store, as_of=as_of, market=market)
    top = leaders(store, changes, caps, as_of=as_of, limit=TREEMAP_TOP_N)
    panels = instrument_panels(
        store, as_of=as_of, lookback=lookback, market=market, benchmark_id=headline
    )
    # 패널로 세운 **지수**만 목록에서 뺀다 — 같은 지수를 한 칸에 두 번 적으면
    # 목록의 "N종" 이 무엇을 세는 숫자인지 흐려진다. 미장 패널은 ETF 라 애초에
    # 이 목록의 식구가 아니고, 그래서 `US:IDX:*` 는 VIX 계열까지 전부 남는다.
    paneled = {
        row["entity_id"]
        for row in panels["panels"] + panels["missing"]
        if row["kind"] == "index"
    }
    ranked = rankings(store, changes, caps, cap_session, as_of=as_of, market=market)

    # 장중 값은 **표를 다 세운 뒤에** 얹는다. 순위는 종가로 서고, 실시간은
    # 그 옆 칸에만 앉는다 — 순서를 바꾸면 새로고침할 때마다 순위가 흔들린다.
    # 순위표는 ``tables`` 안에 표 넷이 있고 종목 행은 그 안의 ``rows`` 다.
    # 바깥 dict 를 그대로 훑으면 하한·집계 같은 종목 아닌 값이 섞인다.
    #
    # **리더(트리맵 60종목)에는 장중 값을 안 붙인다.** 트리맵은 종가 시총으로
    # 칸 크기를 정하는 그림이라 장중 시세가 필요 없다. 그런데 그 60종목까지
    # 물으면 t8407 이 두 콜이 되고, LS 는 콜당 2.5초쯤 걸린다 — 실측
    # 2026-08-19: /api/market 첫 호출이 **20초**였다(캐시가 더워지면 1.1초).
    # 순위표 20종목만 물으면 한 콜로 끝난다.
    live_rows: list[dict[str, Any]] = []
    for table in ranked.get("tables") or []:
        live_rows.extend(table.get("rows") or [])
    filled = attach_live(live_rows, live_quotes)

    # **지수·대표 ETF 는 다른 캐시다.** 화면 첫 줄(대표 지수 카드)이 이걸
    # 읽는다 — 안 붙이면 그 카드가 전일 종가를 오늘 값으로 보여준다.
    # 실측 2026-08-19 12:31: 화면 코스피 6,869.83(-1.55%)은 **전일 종가**였고
    # 그때 실시간은 6,488.37(-5.55%) 이었다.
    idx_list = indices(store, as_of=as_of, market=market, exclude=paneled)

    # **대표 하나만 실시간으로 묻는다.**
    #
    # 지수·ETF 는 종목처럼 묶어 받는 TR 이 없어 **하나씩 따로** 쳐야 한다
    # (국장 t1511 은 업종코드 단건, 미장 g3104 도 심볼 단건). LS 콜이 2.5초쯤
    # 걸리므로 패널 전부를 물으면 여섯 콜 = 15초다 — 실측 2026-08-19:
    # /api/market 첫 호출 **20초**. 캐시가 더워지면 1초지만, 처음 여는 사람은
    # 매번 그 20초를 낸다.
    #
    # 화면에서 장중 값이 실제로 필요한 자리는 **칸 머리의 대표 카드** 하나다.
    # 나머지 패널은 종가로 둔다 — 그 사실을 화면이 `· 종가` 로 말한다.
    index_rows = [
        block for block in (panels.get("panels") or [])
        if block.get("role") == "primary"
    ]
    index_filled = attach_live(index_rows, live_index)

    return {
        "market": market,
        "currency": CURRENCY.get(market, ""),
        # **국장만 장중 값을 받는다.** 조용히 비면 "장외라 없다" 와 "이 시장은
        # 애초에 안 물어본다" 가 같아 보인다.
        "live": {
            "supported": market == "KR",
            "filled": filled,
            "reason": None if market == "KR" else "미장은 실시간 조회 경로가 아직 없다",
        },
        "instrument_panels": panels,
        "indices": idx_list,
        # 지수 쪽이 장중 값을 몇 개 받았는지. 0 이면 화면이 종가를 보여준다.
        "live_index_filled": index_filled,
        "breadth": breadth(changes, market=market),
        "rankings": ranked,
        "leaders": top[:LEADER_ROWS],
        # 시총 세션을 같이 싣는다. **시세 세션과 다를 수 있다** — 화면이 그걸
        # 적지 않으면 낡은 시총이 오늘 것으로 읽힌다. 창(CAP_RECENT_DAYS) 도
        # 같이 준다: 비었을 때 "없는 것" 과 "창 밖인 것" 을 화면이 갈라 말한다.
        "treemap": {
            "rows": top,
            "top_n": TREEMAP_TOP_N,
            "session": cap_session,
            "lookback": CAP_RECENT_DAYS,
        },
        "macro": macro_recent(store, as_of=as_of, market=market),
    }


# -- 장중 값 --------------------------------------------------------------------


def attach_live(rows: list[dict[str, Any]], cache: Any) -> int:
    """장중 시세를 **참고 열에만** 채운다. 몇 종목이 찼는지 돌려준다.

    ``live_price``·``live_change`` 는 종가 기반 값(``change``·``market_cap``)을
    **덮지 않는다.** 트레이딩 탭이 ``live_nav`` 를 종가 NAV 와 갈라 둔 것과 같은
    규약이다 — 회계와 순위는 확정된 종가로만 서고, 장중 값은 그 옆에 앉는다.
    섞으면 화면이 "지금 시총 1위" 를 말하는데 그 숫자는 회계에 없는 값이 된다.

    **실패·장외를 종가로 때우지 않는다.** 값이 없으면 열을 비운 채로 둔다.
    때우면 화면이 실시간인 척하게 되고, 그건 조용히 틀리는 종류의 거짓이다.

    ``LiveQuoteCache`` 는 국장(t8407)만 안다 — 미장은 appkey 가 따로고 호가가
    응답에 없다(g3104, 2026-08-17 실측). 그래서 미장 종목은 조회 자체가 안
    나가고, 화면은 아래 ``live`` 블록의 ``supported`` 로 그 사실을 말한다.
    """
    if cache is None:
        return 0
    # 순위표 안에는 종목 행이 아닌 것도 섞여 있다(하한 설명·집계 줄). 종목이
    # 아닌 행에 장중 값을 붙일 수는 없으므로 조용히 건너뛴다 — 여기서 죽으면
    # 마켓 탭이 통째로 500 이 된다.
    targets = [row for row in rows if isinstance(row, dict) and row.get("entity_id")]
    if not targets:
        return 0
    quotes = cache.get([str(row["entity_id"]) for row in targets])
    filled = 0
    for row in targets:
        quote = quotes.get(str(row["entity_id"]))
        if quote is None or quote.price <= 0:
            continue
        row["live_price"] = quote.price
        row["live_change"] = quote.change_rate
        filled += 1
    return filled


# -- 한 판 ---------------------------------------------------------------------


def payload(
    store: Store, *, as_of: datetime, lookback: int, live_quotes: Any = None,
    live_index: Any = None,
) -> dict[str, Any]:
    """마켓 탭 한 판. **국장 왼쪽 · 미장 오른쪽**, 두 판이 같은 모양이다.

    ``benchmark.total_return`` 을 같이 싣는다 — 대표 지수가 가격지수라
    배당이 빠져 있고, 그만큼 우리가 유리하게 보인다. 화면이 그 배지를
    계속 달아야 하는데, 언제 떼야 하는지는 config 만 안다.
    """
    section = store.config("benchmark", as_of=as_of)
    headlines = {code: str(section[BENCHMARK_KEY[code]]) for code in MARKETS}
    return {
        "fx": fx(store, as_of=as_of, lookback=lookback),
        "total_return": bool(section.get("total_return", False)),
        "markets": {
            code: market_panel(
                store,
                as_of=as_of,
                lookback=lookback,
                market=code,
                headline=headlines[code],
                live_quotes=live_quotes,
                live_index=live_index,
            )
            for code in MARKETS
        },
    }
