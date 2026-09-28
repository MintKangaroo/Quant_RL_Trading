"""거부 성적표 — News·SNS 필터가 일을 했는지 사후에 따진다.

이 둘은 IC 로 검증할 수 없다. 점수가 아니라 판정이고, 과거 뉴스·SNS 를 시점
정합성 있게 확보할 수 없기 때문이다. **그래서 성적표가 유일한 검증 수단이다.**

성적표가 없으면 이 필터는 검증되지 않은 채로 매수만 막는 장치가 된다. 그리고
막힌 종목이 나중에 올랐는지 떨어졌는지는 **아무도 물어보지 않게 된다** — 그건
포트폴리오에 없으니 손익에 안 잡히고, 화면에도 안 나온다.

## 무엇을 재나

차단 기간(``as_of`` ~ ``expires_at``) 동안 **차단된 종목의 수익률 - 같은 날
시장 중앙값**. 음수면 필터가 손실을 피하게 해 준 것이고, 양수면 기회를 버린
것이다.

시장 중앙값을 빼는 이유는 시장이 통째로 빠진 날의 하락을 필터의 공으로
세지 않기 위해서다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

VERDICTS = "verdicts"


def _returns_between(
    prices: pd.DataFrame, start: datetime, end: datetime
) -> pd.Series:
    """구간 수익률. 종목별 첫 종가 → 마지막 종가.

    **정의(참조 구현)다.** 성적표는 같은 답을 빠르게 내는 ``_Windows`` 를 쓰고,
    ``tests/analysts/test_verdicts.py`` 가 둘이 같음을 고정한다.
    """
    window = prices[(prices["valid_from"] >= start) & (prices["valid_from"] <= end)]
    if window.empty:
        return pd.Series(dtype=float)
    ordered = window.sort_values("valid_from")
    grouped = ordered.groupby("entity_id")["close"]
    first, last = grouped.first(), grouped.last()
    return (last / first - 1.0).replace([np.inf, -np.inf], np.nan).dropna()


class _Windows:
    """``_returns_between`` 을 구간마다 55만 행 마스킹·정렬·문자열 groupby 로 내지 않는다.

    실측 2026-09-29(모의 창고, 90일 창): 서로 다른 구간 28개 × 약 100ms = 2.8초가
    성적표 한 번의 값이었고, 대시보드 두 탭의 요약·판정 API 가 각자 그걸 냈다.
    시각으로 **한 번** 정렬해 두면 구간은 연속 조각(``searchsorted``)이고, 종목을
    정수 코드로 한 번 바꿔 두면 첫·끝 종가는 ``np.unique`` 의 첫 등장 위치다.

    **결과는 ``_returns_between`` 과 비트 단위로 같다.** 같은 종가 두 개로 같은
    나눗셈을 한다 — 종목·세션당 행이 하나(자연키)라 정렬 안정성도 답을 안 바꾼다.
    ``groupby().first()`` 가 NaN 을 건너뛰는 것과 맞추려고 NaN 종가는 미리 뺀다.
    """

    def __init__(self, prices: pd.DataFrame) -> None:
        usable = prices.loc[prices["close"].notna(), ["entity_id", "valid_from", "close"]]
        ordered = usable.sort_values("valid_from", kind="stable")
        self._times = pd.DatetimeIndex(ordered["valid_from"])
        codes, self._names = pd.factorize(ordered["entity_id"])
        self._codes = np.asarray(codes)
        self._close = ordered["close"].to_numpy(dtype=float)

    def returns(self, start: datetime, end: datetime) -> pd.Series:
        tz = self._times.tz
        lo = self._times.searchsorted(pd.Timestamp(start).tz_convert(tz), side="left")
        hi = self._times.searchsorted(pd.Timestamp(end).tz_convert(tz), side="right")
        if hi <= lo:
            return pd.Series(dtype=float)
        codes = self._codes[lo:hi]
        close = self._close[lo:hi]
        present, first_at = np.unique(codes, return_index=True)
        # 끝 종가 = 뒤집은 배열의 첫 등장. ``np.unique`` 는 코드 순서로 돌려주므로
        # 두 호출의 코드 배열이 같은 순서로 맞물린다.
        _, last_from_end = np.unique(codes[::-1], return_index=True)
        last_at = len(codes) - 1 - last_from_end
        # 0 종가는 inf 가 되고 아래에서 빠진다 — pandas 나눗셈처럼 경고 없이.
        with np.errstate(divide="ignore", invalid="ignore"):
            values = close[last_at] / close[first_at] - 1.0
        series = pd.Series(values, index=pd.Index(self._names[present], name="entity_id"))
        return series.replace([np.inf, -np.inf], np.nan).dropna()


def evaluate_blocks(
    store: Store, *, as_of: datetime, lookback: int
) -> dict[str, Any]:
    """차단 건별 성적. 만료된 차단만 채점한다.

    아직 유효한 차단은 결과가 안 나왔으므로 넣지 않는다 — 진행 중인 것을
    성적에 넣으면 성적이 매일 흔들린다.
    """
    verdicts = store.get(VERDICTS, as_of=as_of, lookback=lookback)
    if verdicts.empty:
        return _empty()

    blocked = verdicts[verdicts["decision"] == "block"]
    settled = blocked[blocked["expires_at"] <= as_of]
    if settled.empty:
        return _empty(pending=len(blocked))

    # 구간 수익률을 낸다 — **보정가여야 한다.** 판정 구간에 분할이 끼면 그
    # 종목의 성적이 배율만큼 찍히고, Analyst 가 하지 않은 실수로 점수를 잃는다.
    # 컬럼을 좁힌다. 이 함수가 쓰는 것은 종가 하나인데, 안 좁히면 source ·
    # ingest_run_id · row_hash 같은 문자열까지 55만 행어치 퍼온다
    # (실측 1.35s → 0.58s). 대시보드 네 곳이 이 함수를 부른다.
    #
    # 창의 앞머리도 좁힌다 — 채점할 가장 이른 차단 시각부터면 된다. 판정도 같은
    # ``lookback`` 으로 읽었으므로 이 하한은 늘 원래 창 안이다(90일 창에서 실측
    # 57만 → 절반). **답은 같다:** 보정은 뒤(창의 끝)에서 앞으로 누적하므로 앞을
    # 잘라도 남은 행의 보정가가 한 비트도 안 바뀌고, 창의 끝(as_of)은 그대로다.
    earliest = settled["valid_from"].min()
    prices = read_prices(
        store,
        as_of=as_of,
        lookback=(pd.Timestamp(as_of) - earliest).to_pytimedelta(),
        columns=["close"],
        adjusted=True,
    )
    if prices.empty:
        return _empty(pending=len(blocked))

    # 같은 구간이 여러 번 나온다 — 하루치 판정은 보통 같은 시각에 걸리고 같은
    # 만료를 갖는다. 구간마다 한 번만 낸다 (실측 2,259건 → 서로 다른 구간 28개).
    # 구간마다 (수익률, 시장 중앙값). 중앙값도 구간의 값이다 — 판정 건마다 다시 내면
    # 2천여 건 × 9천 종목 정렬이 된다(실측 0.58s).
    windows: dict[tuple[Any, Any], tuple[pd.Series, float]] = {}
    sliced = _Windows(prices)

    records: list[dict[str, Any]] = []
    for row in settled.to_dict(orient="records"):
        start, end = row["valid_from"], row["expires_at"]
        span = (start, end)
        if span not in windows:
            found = sliced.returns(start, end)
            windows[span] = (found, found.median() if not found.empty else float("nan"))
        returns, median = windows[span]
        if returns.empty:
            continue
        entity = str(row["entity_id"])
        if entity not in returns.index:
            continue

        # 시장 중앙값을 빼야 시장이 통째로 빠진 날의 하락을 공으로 세지 않는다.
        excess = float(returns[entity] - median)
        records.append(
            {
                "entity_id": entity,
                "analyst": str(row["analyst"]),
                "category": str(row["category"]),
                "blocked_at": start.isoformat(),
                "expired_at": end.isoformat(),
                "excess_return": excess,
                # 음수면 필터가 손실을 피하게 해 줬다.
                "helped": excess < 0,
            }
        )

    if not records:
        return _empty(pending=len(blocked))

    frame = pd.DataFrame(records)
    return {
        "settled": len(frame),
        "pending": int(len(blocked) - len(settled)),
        "hit_rate": float(frame["helped"].mean()),
        "mean_excess": float(frame["excess_return"].mean()),
        "median_excess": float(frame["excess_return"].median()),
        "by_category": [
            {
                "category": str(name),
                "count": len(group),
                "hit_rate": float(group["helped"].mean()),
                "mean_excess": float(group["excess_return"].mean()),
            }
            for name, group in frame.groupby("category")
        ],
        "worst_calls": frame.nlargest(5, "excess_return").to_dict(orient="records"),
    }


def _empty(pending: int = 0) -> dict[str, Any]:
    """채점할 것이 없다. **0 으로 채우지 않는다** — 0과 '모름'은 다른 사실이다."""
    return {
        "settled": 0,
        "pending": pending,
        "hit_rate": None,
        "mean_excess": None,
        "median_excess": None,
        "by_category": [],
        "worst_calls": [],
    }


__all__ = ["evaluate_blocks"]
