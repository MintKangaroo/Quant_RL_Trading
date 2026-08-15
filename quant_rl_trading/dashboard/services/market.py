"""마켓 탭 집계 — **지금 시장이 어떤 상태인가.**

트레이딩 탭이 "우리 포트폴리오" 를 보여준다면, 이 화면은 그 바깥의 **환경**을
보여준다. 지수·환율·거시지표·시가총액 상위 종목 — 전부 우리가 아직 사지
않았어도 알아야 하는 것들이다.

## 화면은 좌우 두 시장이다 — 그래서 집계도 시장별로 한다

국장과 미장은 **같은 모양의 판 둘**이다. 그래서 이 모듈은 시장별 집계
(`market_panel`)를 하나 만들어 KR·US 에 똑같이 돌린다. 한쪽만 있는 패널을
만들면 화면의 좌우 밀도가 갈라지고, 그러면 "미장은 원래 볼 게 없다" 처럼
보인다 — 실제로는 **없는 것이 아니라 아직 안 받은 것**이다.

시장을 가르지 않고 함께 보는 것은 둘이다 — **원달러 환율**(두 시장 사이의
다리이지 어느 한쪽의 지표가 아니다)과 **상단의 대표 지수 차트**
(`index_chart`). 후자는 좌우로 갈라 놓으면 답이 안 나오는 질문 하나를 맡는다:
"지금 어느 시장이 앞서 가나." 그건 네 선을 겹쳐야만 보인다.

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

#: 상단 차트가 그리는 **대표 지수의 짝**. 시장마다 config 가 정한 대표 지수
#: (`benchmark.kr_index` / `us_index`) 하나에 이 한 종을 더해 넷이 된다.
#:
#: 여기 적힌 이름은 **창고의 entity_id 그대로**다. config 가 정하는 것은
#: "우리가 견줘 평가받는 지수" 이고 그건 시장마다 하나뿐인데, 화면의 질문은
#: "그 시장이 어떤가" 라 대형주 지수 하나로는 답이 안 된다 — 2026-08 처럼
#: 코스피가 오르는 동안 코스닥이 내리는 국면이 그 차이다. 그래서 대표를
#: 바꿔치기하지 않고 **옆에 나란히 놓는다**. 대표가 무엇인지는 응답의
#: ``role`` 이 계속 말한다.
COMPANION_INDICES = {"KR": ("KR:IDX:KOSDAQ",), "US": ("US:IDX:NASDAQ",)}

#: **변동성 지수는 가격지수가 아니다.** VIX 는 옵션 내재변동성이라 "20 → 24"
#: 가 +20% 수익이 아니고, 기준일 100 으로 정규화해 가격지수와 같은 축에 두면
#: 급등이 수익률로 읽힌다. 상단 차트에서 빼고, 목록에서는 따로 묶는다.
VOLATILITY_INDICES = frozenset({"US:IDX:VIX", "US:IDX:VXN", "US:IDX:RVX"})

#: 오늘 많이 오른/내린 종목 줄 수.
MOVER_ROWS = 5

#: 등락 상위를 고르는 모집단 — **거래대금 상위 이만큼 안에서만 고른다.**
#: 전 종목에서 고르면 하루 거래대금 몇백만 원짜리 동전주가 상위를 독차지한다.
#: 그건 "오늘 시장이 어디로 움직였나" 의 답이 아니다.
MOVER_POOL = 300


def _names(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, str]:
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
    상단 차트 자리는 대표 지수 넷이 가져갔고(``index_chart``), 환율은 KPI
    카드와 그 스파크라인으로 남는다. ``sessions``·``rates`` 는 그 스파크라인이
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


def indices(store: Store, *, as_of: datetime, market: str, headline: str) -> dict[str, Any]:
    """한 시장의 지수 현황. 종가와 직전 세션 대비 등락률.

    ``headline`` 은 config 가 정한 대표 지수다 — 여기서 고르지 않는다(모듈
    docstring 참고). 창고에 그 이름이 없으면 ``headline`` 은 ``None`` 이고,
    화면이 "아직 안 들어왔다" 고 말한다. **대용치로 바꿔치기하지 않는다** —
    KRX 300 을 코스피라 부르는 순간 화면이 거짓말을 시작한다.

    `indices` 테이블에는 국장 KRX 업종·테마 지수와 해외 지수가 함께 산다
    (prices 와 나눈 이유는 store/tables.py 참고 — 종목 유니버스에 지수가
    섞이면 커버리지 통계가 오염된다).
    """
    frame = store.get(
        INDICES,
        as_of=as_of,
        lookback=RECENT_DAYS,
        market=market,
        columns=["entity_id", "market", "close", "valid_from"],
    )
    if frame.empty:
        return {"headline": None, "headline_id": headline, "others": [], "total": 0}

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

    by_entity = {row["entity_id"]: row for row in rows}
    others = [row for row in rows if row["entity_id"] != headline]
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
        "headline": by_entity.get(headline),
        "headline_id": headline,
        "others": others,
        "total": len(rows),
    }


# -- 상단 대표 지수 차트 ---------------------------------------------------------


def _index_history(
    store: Store, *, as_of: datetime, lookback: int, market: str, entities: list[str]
) -> dict[str, pd.Series]:
    """지수별 ``세션 → 종가``. 종가 0·NaN 세션은 버린다.

    시장으로 한 번, entity 로 한 번 좁혀 읽는다 — 창고의 지수는 KR 44종·US
    10종이라 창을 길게 열면(1년 이상) 안 그릴 지수까지 통째로 스캔한다.
    """
    frame = store.get(
        INDICES,
        as_of=as_of,
        lookback=lookback,
        market=market,
        entity=entities,
        columns=["entity_id", "close", "valid_from"],
    )
    out: dict[str, pd.Series] = {}
    if frame.empty:
        return out
    for entity, group in frame.groupby("entity_id"):
        series = pd.Series(
            group["close"].astype(float).to_numpy(),
            index=[_session(v) for v in group["valid_from"]],
        )
        # 종가 0/NaN 인 세션, 그리고 valid_from 이 NaT 라 세션을 못 짚은 행을
        # 버린다. 후자를 남기면 색인에 None 이 섞여 정렬이 터진다.
        series = series[(series > 0) & pd.notna(series.index)]
        # 같은 세션이 여러 관측으로 들어올 수 있다(정정본). 마지막 것만.
        series = series[~series.index.duplicated(keep="last")].sort_index()
        if not series.empty:
            out[str(entity)] = series
    return out


def index_chart(
    store: Store, *, as_of: datetime, lookback: int, headlines: dict[str, str]
) -> dict[str, Any]:
    """상단 차트 — **대표 지수 넷을 기준일 100 으로 정규화한 한 그림**.

    ## 왜 한 축에 정규화인가 — 네 조각이 아니라

    코스피 ~4,000 · 코스닥 ~900 · 나스닥 ~26,800 · S&P500 ~7,800 이다. 한 축에
    원값을 그대로 그리면 코스닥은 바닥에 붙은 직선이 된다. 남는 선택은 둘이다.

    - **차트를 넷으로 쪼갠다**: 각 지수의 절대 수준은 살지만, 화면이 답해야 하는
      질문 — "지금 어느 시장이 앞서 가나" — 은 눈으로 네 그림을 옮겨 다니며
      풀어야 한다. 상단 한 줄에 넷을 넣으면 각 그림이 폭 1/4 라 축도 못 단다.
    - **기준일 100 정규화**: 네 선의 벌어진 간격이 곧 그 답이다. 잃는 것은 절대
      수준인데, 그건 이미 KPI 스트립과 지수 패널이 종가로 말하고 있다.

    그래서 정규화를 고른다. 기준값은 ``benchmark.base_value`` 로, 회계가 벤치마크
    지수를 이어붙일 때 쓰는 것과 같은 100 이다 — 화면이 따로 들지 않는다.

    ## 기준일은 넷이 **다 있는 첫 세션**이다

    지수마다 창고에 들어온 시점이 다르다. 각자 자기 첫 세션에서 100 으로 잡으면
    기준일이 서로 다른 선을 겹쳐 놓게 되고, 그러면 벌어진 간격이 성과 차이가
    아니라 출발선 차이가 된다. 그래서 **모두가 값을 가진 첫 세션**을 공통
    기준일로 잡고 그 앞은 자른다.

    국장과 미장은 휴장일이 다르다 — 축은 두 달력의 합집합이고, 그 시장이 쉰
    날은 ``None`` 이다. 0 이나 직전값으로 메우지 않는다.

    ## 변동성 지수는 여기 없다

    VIX·VXN·RVX 는 가격지수가 아니다(``VOLATILITY_INDICES``). 정규화해 같은 축에
    두면 급등이 수익률로 읽힌다.
    """
    base_value = float(store.config("benchmark.base_value", as_of=as_of))

    wanted: list[tuple[str, str, str]] = []  # (entity_id, market, role)
    for code in MARKETS:
        wanted.append((headlines[code], code, "headline"))
        wanted.extend((entity, code, "companion") for entity in COMPANION_INDICES.get(code, ()))

    history: dict[str, pd.Series] = {}
    for code in MARKETS:
        entities = [entity for entity, market, _ in wanted if market == code]
        history.update(
            _index_history(
                store, as_of=as_of, lookback=lookback, market=code, entities=entities
            )
        )

    missing = [
        {"entity_id": entity, "market": market, "role": role}
        for entity, market, role in wanted
        if entity not in history
    ]
    present = [(entity, market, role) for entity, market, role in wanted if entity in history]
    if not present:
        return {
            "base_value": base_value,
            "base_session": None,
            "sessions": [],
            "series": [],
            "missing": missing,
            "lookback": lookback,
        }

    # 공통 기준일 — 모두가 값을 가진 첫 세션. 그 앞은 자른다.
    base_session = max(str(history[entity].index[0]) for entity, _, _ in present)
    sessions = sorted(
        {s for entity, _, _ in present for s in history[entity].index if str(s) >= base_session}
    )

    series: list[dict[str, Any]] = []
    for entity, market, role in present:
        window = history[entity]
        window = window[[str(s) >= base_session for s in window.index]]
        base = float(window.iloc[0])
        closes = [float(window[s]) if s in window.index else None for s in sessions]
        series.append(
            {
                "entity_id": entity,
                "market": market,
                "role": role,
                # 자기 첫 세션이 공통 기준일과 다를 수 있다 — 그날 그 시장이
                # 쉬었으면 하루 뒤가 자기 기준일이다. 화면이 그대로 적는다.
                "base_session": str(window.index[0]),
                "base_close": base,
                "close": float(window.iloc[-1]),
                "session": str(window.index[-1]),
                "change": (float(window.iloc[-1]) / float(window.iloc[-2]) - 1.0)
                if len(window) >= 2
                else None,
                # 기준일 대비 누적. 정규화했다는 사실 자체를 화면이 말해야 하므로
                # 원 종가(``closes``)도 같이 실어 툴팁이 둘 다 보여준다.
                "values": [
                    None if c is None else round(c / base * base_value, 4) for c in closes
                ],
                "closes": closes,
                "total": float(window.iloc[-1]) / base - 1.0,
            }
        )

    return {
        "base_value": base_value,
        "base_session": base_session,
        "sessions": [str(s) for s in sessions],
        "series": series,
        "missing": missing,
        "lookback": lookback,
    }


# -- 시세로 만드는 것 — 시장 폭 · 많이 움직인 종목 ---------------------------------


def _changes(store: Store, *, as_of: datetime, market: str) -> pd.DataFrame:
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
    names = _names(store, as_of=as_of, entities=entities)

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

    names = _names(store, as_of=as_of, entities=entities)
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
    store: Store, *, as_of: datetime, market: str, headline: str
) -> dict[str, Any]:
    """한 시장의 판 전부. KR·US 가 **같은 함수, 같은 모양**이다.

    시세는 한 번만 읽는다(`_changes`). 시장 폭·movers·리더 등락률·트리맵이
    전부 그 한 표에서 나온다 — 패널마다 따로 읽으면 같은 파티션을 네 번 연다.

    ``universe`` 는 그 시장에서 오늘 시세가 잡힌 종목 수다. 명단
    (`universe` 테이블) 이 아니라 **거래가 관측된 수**라 breadth 의 분모와
    같은 숫자다.
    """
    changes = _changes(store, as_of=as_of, market=market)
    top = leaders(store, changes, as_of=as_of, market=market, limit=TREEMAP_TOP_N)
    return {
        "market": market,
        "currency": CURRENCY.get(market, ""),
        "indices": indices(store, as_of=as_of, market=market, headline=headline),
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
        "index_chart": index_chart(
            store, as_of=as_of, lookback=lookback, headlines=headlines
        ),
        "total_return": bool(section.get("total_return", False)),
        "markets": {
            code: market_panel(store, as_of=as_of, market=code, headline=headlines[code])
            for code in MARKETS
        },
    }
