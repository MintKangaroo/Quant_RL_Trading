"""6차 G11 — 미장 8-K 항목 단위 사건 피처.

무효가 되는 길 셋: 제목의 항목코드를 하나만 읽는 것(한 공시가 여러 항목을 담는다),
정정(8-K/A)을 새 사건으로 세는 것, 그리고 이력이 2년을 못 덮는 구간에서 전부 "처음" 이라고
말하는 것. 여기서 그 셋을 못 박는다. 관측 시각(개장 전)은 `store.get(as_of=)` 의 일이므로
이 테스트는 **함수에 넘어온 행만** 쓰이는지, 즉 창 밖·미래 접수분이 새지 않는지를 본다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd

from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.ranker_sources import GROUPS, build
from quant_rl_trading.analysts.ranker_sources_g11 import (
    K8_AGE_CAP,
    K8_FIRST_HISTORY_DAYS,
    eight_k_items,
)

ENTITIES = ("US:AAA", "US:BBB", "US:CCC")


class FakeStore:
    """`documents` 만 준다. as_of 필터는 창고의 일이므로 여기서는 받은 행을 그대로 돌려준다 —
    대신 테스트가 as_of 뒤 접수분을 아예 넘기지 않는다(그것이 창고의 계약이다)."""

    def __init__(self, rows: pd.DataFrame) -> None:
        self.rows = rows
        self.seen: list[dict[str, object]] = []

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:
        assert table == "documents"
        self.seen.append(kwargs)
        return self.rows


class FakeAnalyst:
    market = "US"
    wide = staticmethod(Analyst.wide)

    def __init__(self, rows: pd.DataFrame, sessions: list[date]) -> None:
        self.store = FakeStore(rows)
        self.sessions = sessions

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        return pd.DataFrame([
            {"entity_id": e, "session": s, "close": 10.0, "volume": 100.0, "value": 1000.0}
            for s in self.sessions for e in ENTITIES
        ])

    def tradable_entities(self, as_of: datetime, *, lookback: int) -> None:
        return None


# 600영업일 ≈ 2.3년 — "2년 만에 처음" 을 시험할 수 있는 최소 길이.
def _sessions(count: int = 600) -> list[date]:
    return list(pd.bdate_range("2024-01-02", periods=count).date)


def _doc(entity: str, day: date, title: str) -> dict[str, object]:
    return {
        "entity_id": entity,
        "valid_from": datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        "title": title,
    }


def _panel(rows: list[dict[str, object]], sessions: list[date]) -> pd.DataFrame:
    analyst = FakeAnalyst(pd.DataFrame(rows), sessions)
    as_of = datetime.combine(sessions[-1], datetime.min.time(), tzinfo=UTC)
    return eight_k_items(analyst, as_of)  # type: ignore[arg-type]


def test_한_공시의_여러_항목을_전부_센다() -> None:
    sessions = _sessions()
    raw = _panel([
        # 한 줄에 부정 항목 둘(4.02·3.01)과 무관 항목 둘 — 부정은 2건으로 세야 한다.
        _doc("US:AAA", sessions[-3], "8-K 4.02 3.01 7.01 9.01"),
        _doc("US:BBB", sessions[-5], "8-K 2.03 9.01"),
        _doc("US:BBB", sessions[-40], "8-K 3.02"),
    ], sessions)
    assert raw.loc["US:AAA", "k8_negative_20"] == 2.0
    assert raw.loc["US:AAA", "k8_negative_age"] == 2.0
    # 사건이 없으면 0 이다 — 결측이 아니라 사실이다(G2·G4·G7 과 같은 규약).
    assert raw.loc["US:CCC", "k8_negative_20"] == 0.0
    assert raw.loc["US:CCC", "k8_negative_age"] == float(K8_AGE_CAP)
    assert raw.loc["US:BBB", "k8_debt_60"] == 1.0
    assert raw.loc["US:BBB", "k8_equity_60"] == 1.0


def test_정정은_새_사건이_아니라_amend_로만_센다() -> None:
    sessions = _sessions()
    raw = _panel([
        _doc("US:AAA", sessions[-4], "8-K/A 4.02 9.01"),
        _doc("US:AAA", sessions[-6], "8-K/A 2.03"),
    ], sessions)
    assert raw.loc["US:AAA", "k8_amend_20"] == 2.0
    # 정정의 항목은 부정 건수·부채 건수에 들어가지 않는다.
    assert raw.loc["US:AAA", "k8_negative_20"] == 0.0
    assert raw.loc["US:AAA", "k8_debt_60"] == 0.0
    assert raw.loc["US:AAA", "k8_negative_age"] == float(K8_AGE_CAP)


def test_창_밖_공시는_세지_않는다() -> None:
    sessions = _sessions()
    raw = _panel([
        # 21세션 전 — 20세션 창 밖(부정 건수 0), 그래도 경과 세션 수는 120 창 안이라 값이 있다.
        _doc("US:AAA", sessions[-22], "8-K 3.01"),
        # 61세션 전 — 2.03 의 60세션 창 밖.
        _doc("US:BBB", sessions[-62], "8-K 2.03"),
        # 121세션 전 — 5.01 의 120세션 창 밖.
        _doc("US:CCC", sessions[-130], "8-K 5.01"),
    ], sessions)
    assert raw.loc["US:AAA", "k8_negative_20"] == 0.0
    assert raw.loc["US:AAA", "k8_negative_age"] == 21.0
    assert raw.loc["US:BBB", "k8_debt_60"] == 0.0
    assert raw.loc["US:CCC", "k8_control_120"] == 0.0


def test_같은_항목이_2년_안에_있었으면_처음이_아니다() -> None:
    sessions = _sessions()
    raw = _panel([
        # 이력이 2년을 덮는다는 근거(창 밖 옛 공시 한 줄) — 없으면 아래 판단이 결측이다.
        _doc("US:CCC", sessions[0], "8-K 7.01"),
        _doc("US:AAA", sessions[-2], "8-K 4.01"),
        # 같은 항목이 1년 전에 있었다 — 처음이 아니다.
        _doc("US:AAA", sessions[-260], "8-K 4.01"),
        # B 는 같은 창에 처음 보는 항목이다.
        _doc("US:BBB", sessions[-2], "8-K 4.02"),
        _doc("US:BBB", sessions[-260], "8-K 3.01"),
    ], sessions)
    assert raw.loc["US:AAA", "k8_first_in_2y"] == 0.0
    assert raw.loc["US:BBB", "k8_first_in_2y"] == 1.0
    assert raw.loc["US:CCC", "k8_first_in_2y"] == 0.0


def test_이력이_2년을_못_덮으면_첫_발생은_결측이다() -> None:
    sessions = _sessions()
    # 가장 오래된 공시가 최근이면 "2년 만에 처음" 을 말할 근거가 없다 → 전부 결측.
    raw = _panel([_doc("US:AAA", sessions[-2], "8-K 4.01")], sessions)
    assert raw["k8_first_in_2y"].isna().all()

    # 2년을 덮는 이력이 한 줄이라도 있으면 판단할 수 있다.
    old = sessions[-21] - timedelta(days=K8_FIRST_HISTORY_DAYS + 5)
    raw2 = _panel([
        _doc("US:AAA", sessions[-2], "8-K 4.01"),
        _doc("US:CCC", old, "8-K 1.02"),
    ], sessions)
    assert raw2.loc["US:AAA", "k8_first_in_2y"] == 1.0


def test_미래_접수분은_창고가_막고_국장_행은_걸러진다() -> None:
    sessions = _sessions()
    rows = [
        _doc("US:AAA", sessions[-2], "8-K 3.01"),
        # 국장 공시 — 접두사가 달라 빠져야 한다(제목에 항목처럼 보이는 숫자가 있어도).
        _doc("KR:005930", sessions[-2], "8-K 3.01"),
        # 8-K 가 아닌 미장 공시 — 제목이 숫자를 담아도 세지 않는다.
        _doc("US:BBB", sessions[-2], "10-Q 1.02"),
    ]
    analyst = FakeAnalyst(pd.DataFrame(rows), sessions)
    as_of = datetime.combine(sessions[-1], datetime.min.time(), tzinfo=UTC)
    raw = eight_k_items(analyst, as_of)  # type: ignore[arg-type]
    assert raw.loc["US:AAA", "k8_negative_20"] == 1.0
    assert raw.loc["US:BBB", "k8_negative_20"] == 0.0
    assert "KR:005930" not in raw.index
    # 미래를 안 보는 유일한 방법은 창고에 as_of 를 넘기는 것이다 — 함수가 그것을 넘겼는지 본다.
    assert analyst.store.seen and analyst.store.seen[0]["as_of"] == as_of
    assert analyst.store.seen[0]["lookback"] >= 730


def test_등록된_열만_등록된_순서로_나온다() -> None:
    sessions = _sessions()
    analyst = FakeAnalyst(
        pd.DataFrame([_doc("US:AAA", sessions[-2], "8-K 5.01")]), sessions,
    )
    as_of = datetime.combine(sessions[-1], datetime.min.time(), tzinfo=UTC)
    raw = build("G11", analyst, as_of)  # type: ignore[arg-type]
    assert list(raw.columns) == list(GROUPS["G11"])
    assert raw.loc["US:AAA", "k8_control_120"] == 1.0
