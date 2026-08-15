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

## 미장 시가총액 — 아직 못 그린다

`leaders(market="US")` 와 트리맵이 참조하는 `market_stats` 는 **시가총액 =
주가 × 상장주식수** 로 계산된 값인데, 이 창고에서 상장주식수(shares
outstanding)를 수집하는 곳은 `collectors/krx_openapi.py` (KRX Open API, KR
전용) 하나뿐이다. `market_stats` 를 시장별로 세어 보면 KR 만 있고 US 는
0행이다 — US 종목은 시세와 유니버스는 있어도 상장주식수가 없어 market_cap
자체가 만들어지지 않는다. 지어내지 않고 US 리더·트리맵을 빈 목록으로 둔다 —
화면이 그 이유를 문구로 말한다.
**필요한 수집 작업**: US 상장주식수 소스를 찾아 `market_stats`(market="US",
metric="shares"/"market_cap") 를 채우는 새 수집기. `collectors/*` 는 이 탭의
소유가 아니라 여기서 만들지 않는다.

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

from datetime import datetime
from typing import Any

import pandas as pd

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
#: `US:SOXX` 는 아직 창고에 0행이다. 자리를 만들어 두고 "미수집" 을 띄운다 —
#: 수집이 들어오면 저절로 찬다. (SOXX 는 2021-06 부터 필라델피아 SOX 가 아니라
#: ICE 반도체 지수를 좇는다.)
US_ETF_PANELS = (
    ("US:SPY", "S&P 500"),
    ("US:QQQ", "나스닥 100"),
    ("US:DIA", "다우존스 산업평균"),
    ("US:SOXX", "ICE 반도체"),
)

#: **변동성 지수는 가격지수가 아니다.** VIX 는 옵션 내재변동성이라 "20 → 24"
#: 가 +20% 수익이 아니라 공포가 커진 것이다. 패널로 세우지 않고, 목록에서는
#: 가격지수와 갈라서 묶는다 — 등락에 손익 색도 쓰지 않는다.
VOLATILITY_INDICES = frozenset({"US:IDX:VIX", "US:IDX:VXN", "US:IDX:RVX"})

#: 오늘 많이 오른/내린 종목 줄 수.
MOVER_ROWS = 5

#: 등락 상위를 고르는 모집단 — **거래대금 상위 이만큼 안에서만 고른다.**
#: 전 종목에서 고르면 하루 거래대금 몇백만 원짜리 동전주가 상위를 독차지한다.
#: 그건 "오늘 시장이 어디로 움직였나" 의 답이 아니다.
MOVER_POOL = 300


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
    if kind == "etf":
        frame = read_prices(
            store, as_of=as_of, entity=entities, lookback=lookback,
            market=market, columns=columns,
        )
    else:
        frame = store.get(
            INDICES, as_of=as_of, entity=entities, lookback=lookback,
            market=market, columns=columns,
        )

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

    창고에 없는 것(지금은 `US:SOXX`)도 ``missing`` 으로 나가 패널 자리를
    유지한다. 수집이 들어오면 **저절로 찬다** — 여기 고칠 것이 없다.
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

    panels: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for entity, role, tracks in wanted:
        label = _panel_label(entity, kind)
        # 벤치마크는 config 가 정한 그 이름일 때만이다. 미장 ETF 는 절대 아니다.
        is_benchmark = kind == "index" and entity == benchmark_id
        stub = {
            "entity_id": entity, "market": market, "kind": kind, "label": label,
            "tracks": tracks, "role": role, "benchmark": is_benchmark,
        }
        rows = history.get(entity)
        if rows is None or rows.empty:
            missing.append(stub)
            continue

        closes = rows["close"].astype(float)
        first, last = float(closes.iloc[0]), float(closes.iloc[-1])
        candles = _has_ohlc(rows)
        panels.append(
            {
                **stub,
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
                "has_ohlc": candles,
                "close": last,
                "session": str(rows.index[-1]),
                "change": (last / float(closes.iloc[-2]) - 1.0) if len(closes) >= 2 else None,
                "total": (last / first - 1.0) if first else None,
                "first_session": str(rows.index[0]),
            }
        )

    return {"panels": panels, "missing": missing, "lookback": lookback, "kind": kind}


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


def movers(
    store: Store, changes: pd.DataFrame, *, as_of: datetime
) -> dict[str, list[dict[str, Any]]]:
    """오늘 많이 오른 종목·많이 내린 종목·거래대금 상위.

    등락 상위는 **거래대금 상위 ``MOVER_POOL`` 안에서만** 고른다 — 이유는
    상수 주석 참고. 이 필터가 없으면 화면이 동전주 목록이 된다.
    """
    if changes.empty:
        return {"gainers": [], "losers": [], "actives": []}

    ranked = changes.sort_values("value", ascending=False)
    pool = ranked.head(MOVER_POOL)
    measured = pool[pool["change"].notna()].sort_values("change", ascending=False)

    gainers = measured.head(MOVER_ROWS)
    losers = measured.tail(MOVER_ROWS).iloc[::-1]
    actives = ranked.head(MOVER_ROWS)

    entities = sorted(
        {str(e) for frame in (gainers, losers, actives) for e in frame.index}
    )
    names = entity_names(store, as_of=as_of, entities=entities)

    def rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for entity, row in frame.iterrows():
            key = str(entity)
            change = row["change"]
            out.append(
                {
                    "entity_id": key,
                    "name": names.get(key, key),
                    "close": float(row["close"]),
                    "change": None if pd.isna(change) else float(change),
                    "value": None if pd.isna(row["value"]) else float(row["value"]),
                }
            )
        return out

    return {"gainers": rows(gainers), "losers": rows(losers), "actives": rows(actives)}


# -- 시가총액 상위 -------------------------------------------------------------


def leaders(
    store: Store, changes: pd.DataFrame, *, as_of: datetime, market: str, limit: int
) -> list[dict[str, Any]]:
    """시가총액 상위 ``limit`` 종목. 순위는 오늘 알 수 있는 마지막 시총 기준이다.

    등락률은 ``changes``(시세로 이미 만든 표)에서 꺼내 쓴다 — 리더 표와
    트리맵이 각자 시세를 다시 읽으면 같은 창고를 두 번 훑는다.

    `market_stats` 는 종목마다 하루 두 행(market_cap·shares)이라 KR 만 해도
    수천 종목이다. 창을 짧게 열고(RECENT_DAYS) market 으로 SQL 단계에서
    거른다 — 안 그러면 국장·미장을 함께 스캔한다.
    """
    stats = store.get(
        MARKET_STATS,
        as_of=as_of,
        lookback=RECENT_DAYS,
        market=market,
        columns=["entity_id", "metric", "value", "valid_from"],
    )
    if stats.empty:
        return []
    caps = stats[stats["metric"] == "market_cap"]
    if caps.empty:
        return []
    latest = caps.sort_values("valid_from").groupby("entity_id").tail(1)
    top = latest.sort_values("value", ascending=False).head(limit)
    entities = [str(e) for e in top["entity_id"]]

    names = entity_names(store, as_of=as_of, entities=entities)
    changed = changes["change"] if not changes.empty else pd.Series(dtype=float)

    rows: list[dict[str, Any]] = []
    for row in top.to_dict(orient="records"):
        entity = str(row["entity_id"])
        change = changed.get(entity)
        rows.append(
            {
                "entity_id": entity,
                "name": names.get(entity, entity),
                "market_cap": float(row["value"]),
                # 종가가 아예 없는 종목(오늘 거래정지 등)은 표에 키가 없다.
                # 거래는 있었는데 직전이 없는 경우와 같은 값(None)이지만, 둘 다
                # "등락을 못 잰다" 라는 같은 사실이라 화면에서 구분하지 않는다.
                "change": None if change is None or pd.isna(change) else float(change),
            }
        )
    return rows


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
    store: Store, *, as_of: datetime, lookback: int, market: str, headline: str
) -> dict[str, Any]:
    """한 시장의 판 전부. KR·US 가 **같은 함수, 같은 모양**이다.

    시세는 한 번만 읽는다(`session_changes`). 시장 폭·movers·리더 등락률·트리맵이
    전부 그 한 표에서 나온다 — 패널마다 따로 읽으면 같은 파티션을 네 번 연다.

    ``universe`` 는 그 시장에서 오늘 시세가 잡힌 종목 수다. 명단
    (`universe` 테이블) 이 아니라 **거래가 관측된 수**라 breadth 의 분모와
    같은 숫자다.
    """
    changes = session_changes(store, as_of=as_of, market=market)
    top = leaders(store, changes, as_of=as_of, market=market, limit=TREEMAP_TOP_N)
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
    return {
        "market": market,
        "currency": CURRENCY.get(market, ""),
        "instrument_panels": panels,
        "indices": indices(store, as_of=as_of, market=market, exclude=paneled),
        "breadth": breadth(changes, market=market),
        "movers": movers(store, changes, as_of=as_of),
        "leaders": top[:LEADER_ROWS],
        "treemap": {"rows": top, "top_n": TREEMAP_TOP_N},
        "macro": macro_recent(store, as_of=as_of, market=market),
    }


# -- 한 판 ---------------------------------------------------------------------


def payload(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
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
            )
            for code in MARKETS
        },
    }
