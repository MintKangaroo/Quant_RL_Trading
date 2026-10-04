"""LS 투자정보 TR `t3518`(해외지수·환율 일봉)·`t3521`(현재 종가) — Yahoo 대체 (2026-10-04).

robots.txt 가 `Disallow: /` 인 Yahoo 위에 서 있던 미장 지수·원달러 수집을 이 둘로 옮겼다
(`docs/design/data-contract.md` §4-2, `docs/design/ls-api.md` 0-12-1).

**함정 둘** (실측 2026-10-04):

- `t3518` 지수(kind=S) 일봉은 **소수점이 두 자리 밀려** 온다 — S&P 500 7722.72 가 ``"77.2272"``.
  환율(kind=R)은 정상이다. 배율을 짐작하지 않고 `t3521` 종가(정상)와 견줘 10의 거듭제곱으로 맞춘다.
- 모르는 심볼·빈 날은 오류가 아니라 **0 으로 채운 줄**로 온다. 0 은 값이 아니다 — 버린다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

PATH = "/stock/investinfo"
TR_DAILY = "t3518"
TR_CLOSE = "t3521"
KIND_INDEX = "S"
KIND_FX = "R"


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float


def _number(value: object) -> float | None:
    try:
        out = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _day(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def parse_daily(payload: dict[str, Any]) -> list[DailyBar]:
    """`t3518` 응답 → 날짜 오름차순 일봉. 종가·시가가 0 인 줄(빈 날·모르는 심볼)은 버린다."""
    out: dict[date, DailyBar] = {}
    for row in payload.get(f"{TR_DAILY}OutBlock1") or []:
        day = _day(row.get("date"))
        close = _number(row.get("price"))
        opened = _number(row.get("open"))
        if day is None or close is None or opened is None:
            continue
        out[day] = DailyBar(
            day=day, open=opened, high=_number(row.get("high")), low=_number(row.get("low")), close=close
        )
    return [out[d] for d in sorted(out)]


def parse_close(payload: dict[str, Any]) -> tuple[date, float] | None:
    """`t3521` 응답 → (날짜, 종가). 모르는 심볼은 빈 블록(close "0")이라 None."""
    block = payload.get(f"{TR_CLOSE}OutBlock") or {}
    day = _day(block.get("date"))
    close = _number(block.get("close"))
    if day is None or close is None:
        return None
    return day, close


def decimal_scale(bars: list[DailyBar], reference: tuple[date, float]) -> float:
    """`t3518` 지수 값에 곱할 10의 거듭제곱 — 같은 날짜의 `t3521` 종가와 견준다.

    같은 날짜 봉이 없으면 정할 수 없다(ValueError). 비율이 10의 거듭제곱에서 1% 넘게 벗어나면
    배율 문제가 아니라 다른 값을 보고 있는 것이라 역시 거부한다.
    """
    ref_day, ref_close = reference
    match = [bar for bar in bars if bar.day == ref_day]
    if not match:
        raise ValueError(f"t3521 기준일 {ref_day} 의 t3518 봉이 없다 — 배율을 정할 수 없다")
    ratio = ref_close / match[0].close
    scale = 10.0 ** round(math.log10(ratio))
    if abs(ratio / scale - 1.0) > 0.01:
        raise ValueError(f"t3521/t3518 비율 {ratio:.6g} 가 10의 거듭제곱이 아니다 — 다른 값을 보고 있다")
    return scale


def scaled(bars: list[DailyBar], scale: float) -> list[DailyBar]:
    def mul(value: float | None) -> float | None:
        return None if value is None else round(value * scale, 6)

    return [
        DailyBar(day=b.day, open=mul(b.open), high=mul(b.high), low=mul(b.low), close=float(mul(b.close) or 0.0))
        for b in bars
    ]


def fetch_daily(client: Any, kind: str, symbol: str, *, count: int) -> list[DailyBar]:
    data = client.request_tr(
        PATH,
        TR_DAILY,
        {f"{TR_DAILY}InBlock": {
            "kind": kind, "symbol": symbol, "cnt": int(count), "jgbn": "0", "nmin": 0,
            "cts_date": " ", "cts_time": " ",
        }},
    )
    return parse_daily(data)


def fetch_close(client: Any, kind: str, symbol: str) -> tuple[date, float] | None:
    data = client.request_tr(PATH, TR_CLOSE, {f"{TR_CLOSE}InBlock": {"kind": kind, "symbol": symbol}})
    return parse_close(data)


def fetch_index_daily(client: Any, symbol: str, *, count: int) -> list[DailyBar]:
    """지수 일봉을 배율까지 맞춰 돌려준다. 심볼을 모르면 빈 목록."""
    bars = fetch_daily(client, KIND_INDEX, symbol, count=count)
    reference = fetch_close(client, KIND_INDEX, symbol)
    if not bars or reference is None:
        return []
    return scaled(bars, decimal_scale(bars, reference))

