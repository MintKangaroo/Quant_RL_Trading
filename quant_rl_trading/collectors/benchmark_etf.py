"""종료 판정의 대조군 — KODEX200(069500) 일봉과 그 기초지수(K200).

## 왜 따로 받나

`milestones.md` 의 종료 기준은 **KODEX200 대비 비용 차감 후 초과수익**과 **낙폭**으로
판정한다. 그런데 2026-09-18 확인 결과 창고에 069500 이 **0행**이었다 — 판정일에
계산 자체가 불가능한 상태였다. ETF 는 유니버스에서 빠지므로(`prices` 는 종목만 받는다)
일상 수집 어디에도 걸리지 않았다.

기초지수(K200)도 같이 적는다. KRX 지수 수집은 하루 늦어(2026-09-17 세션에 코스피·코스닥
둘만 들어왔다) `KR:IDX:KOSPI200` 에 구멍이 생기는데, ETF 응답이 같은 값을 그날 들고 온다
(실측 2026-09-16: indices 1060.05 = 기초지수 1060.05, 정확히 일치).

## 어디에 적나

`indices` 다. `prices` 에 넣으면 ETF 가 종목 유니버스에 끼어 **커버리지 통계와 횡단면 z 를
오염시킨다**(tables.py 의 경고 그대로). 벤치마크는 지수 자리에 둔다.

## 총수익이 아니다

ETF **가격**이라 운용보수는 이미 값 안에 있지만 **분배금은 빠져 있다.** 그만큼 벤치마크가
낮게 잡히고 **우리가 이긴 것처럼 보인다.** 판정 도구가 사전등록된 분배금 가정
(`benchmark.kodex200_distribution_yield_annual`)으로 보정한 값을 함께 낸다 — 그 보정은
우리에게 불리한 쪽이라, 넣는 편이 자기비판적이다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

#: 판정 대조군. 이름은 `KR:IDX:*` 와 갈라 둔다 — 지수가 아니라 **살 수 있는 상품**이다.
BENCHMARK_ETF = "KR:ETF:069500"
#: ETF 응답이 함께 들고 오는 기초지수. 기존 KRX 경로와 같은 이름이라 정정본으로 합류한다.
UNDERLYING_INDEX = "KR:IDX:KOSPI200"
TABLE = "indices"
SOURCE = "krx_etf_069500"
TICKER = "069500"


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def rows_from_frame(frame: Any, *, observed_at: datetime) -> list[dict[str, Any]]:
    """pykrx `get_etf_ohlcv_by_date` 결과 → indices 행.

    종가가 0 이하인 날은 버린다. KRX 는 휴장일에도 0 으로 채운 표를 주고, 그것을
    그대로 적으면 "그날 지수가 0이 됐다" 가 창고에 남는다(krx_source.py 의 같은 교훈).
    """
    out: list[dict[str, Any]] = []
    for stamp, row in frame.iterrows():
        day = stamp.date() if hasattr(stamp, "date") else stamp
        close = _number(row.get("종가"))
        if close is None:
            continue
        valid_from = datetime(day.year, day.month, day.day, tzinfo=observed_at.tzinfo)
        base = {
            "valid_from": valid_from,
            "observed_at": observed_at,
            "source": SOURCE,
            "market": "KR",
        }
        out.append({
            **base,
            "entity_id": BENCHMARK_ETF,
            "board": "ETF",
            "open": _number(row.get("시가")),
            "high": _number(row.get("고가")),
            "low": _number(row.get("저가")),
            "close": close,
            "volume": _number(row.get("거래량")),
            "value": _number(row.get("거래대금")),
        })
        underlying = _number(row.get("기초지수"))
        if underlying is not None:
            # 기초지수는 종가 하나뿐이다 — OHLC 를 지어내지 않는다.
            out.append({
                **base, "entity_id": UNDERLYING_INDEX, "board": "KOSPI200",
                "open": None, "high": None, "low": None,
                "close": underlying, "volume": None, "value": None,
            })
    return out


def run_id(start: date, end: date) -> str:
    return f"benchmark-etf-{start.isoformat()}-{end.isoformat()}"
