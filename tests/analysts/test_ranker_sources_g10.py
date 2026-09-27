"""6차 G10 — 국장 5% 대량보유 변동 피처.

무효가 되는 길 셋: 창을 **접수일이 아닌 무언가**로 세는 것(아직 공시 전 변동을 본다),
개인·법인 대주주의 질권·담보 갱신을 기관 흐름에 섞는 것, 그리고 보고가 없는 종목을
결측으로 두는 것("변동이 없었다" 는 사실이다). 여기서 그 셋을 못 박는다.
관측 시각(개장 전)은 `store.get(as_of=)` 의 일이므로 이 테스트는 함수에 넘어온 행만 쓰이는지를 본다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.ranker_sources import (
    GROUPS,
    MAJOR_NEW_SESSIONS,
    MAJOR_SESSIONS,
    build,
)

ENTITIES = ("KR:AAA", "KR:BBB", "KR:CCC")
SESSIONS = list(pd.bdate_range("2025-01-02", periods=300).date)
AS_OF = datetime(2026, 3, 2, tzinfo=UTC)


class FakeStore:
    def __init__(self, rows: pd.DataFrame) -> None:
        self.rows = rows
        self.seen: list[dict[str, object]] = []

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:
        assert table == "major_holders"
        self.seen.append(kwargs)
        return self.rows


class FakeAnalyst:
    market = "KR"
    wide = staticmethod(Analyst.wide)

    def __init__(self, rows: pd.DataFrame) -> None:
        self.store = FakeStore(rows)

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        return pd.DataFrame([
            {"entity_id": e, "session": s, "close": 10.0, "volume": 100.0, "value": 1000.0}
            for s in SESSIONS for e in ENTITIES
        ])

    def tradable_entities(self, as_of: datetime, *, lookback: int) -> None:
        return None


def _report(entity: str, day: date, cls: str, ratio: float, change: float) -> dict[str, object]:
    return {
        "entity_id": entity,
        "valid_from": pd.Timestamp(day, tz="Asia/Seoul"),
        "reporter_class": cls,
        "ratio": ratio,
        "ratio_change": change,
    }


def _run(rows: list[dict[str, object]]) -> pd.DataFrame:
    return build("G10", FakeAnalyst(pd.DataFrame(rows)), AS_OF)


#: 창 안 / 60세션 밖이지만 180세션 안 / 180세션 밖.
IN_60 = SESSIONS[-5]
IN_180_ONLY = SESSIONS[-(MAJOR_SESSIONS + 20)]
OUT_180 = SESSIONS[-(MAJOR_NEW_SESSIONS + 20)]


def test_등록된_열만_등록된_순서로_돌려준다() -> None:
    got = _run([_report("KR:AAA", IN_60, "institution", 6.0, 1.0)])
    assert list(got.columns) == list(GROUPS["G10"])


def test_기관_증감을_합치고_개인_법인은_섞지_않는다() -> None:
    got = _run([
        _report("KR:AAA", IN_60, "institution", 6.0, 1.5),
        _report("KR:AAA", IN_60, "nps", 7.0, -0.5),
        _report("KR:AAA", IN_60, "foreign", 5.5, 0.25),
        # 개인·법인 대주주의 질권·담보 갱신 — 지분 이동이 아니다
        _report("KR:AAA", IN_60, "individual", 33.0, 9.0),
        _report("KR:AAA", IN_60, "corporate", 20.0, 9.0),
    ])
    assert got.loc["KR:AAA", "mh_inst_net_60"] == 1.25
    assert got.loc["KR:AAA", "mh_nps_change_120"] == -0.5


def test_창은_접수일로_세고_60세션_밖은_안_센다() -> None:
    got = _run([
        _report("KR:AAA", IN_60, "institution", 6.0, 1.0),
        _report("KR:AAA", IN_180_ONLY, "institution", 8.0, 2.0),
    ])
    assert got.loc["KR:AAA", "mh_inst_net_60"] == 1.0  # 60세션 밖의 2.0 은 안 들어온다


def test_신규_5퍼센트_진입은_증감이_보유비율과_같은_보고다() -> None:
    got = _run([
        _report("KR:AAA", IN_180_ONLY, "institution", 5.4, 5.4),   # 0 에서 올라온 신규
        _report("KR:BBB", IN_180_ONLY, "institution", 9.0, 1.0),   # 이미 들고 있던 추가 취득
        _report("KR:CCC", IN_180_ONLY, "institution", 4.9, 4.9),   # 5% 미만 — 보고 대상 아님
    ])
    assert got.loc["KR:AAA", "mh_new_inst_180"] == 5.4
    assert got.loc["KR:BBB", "mh_new_inst_180"] == 0.0
    assert got.loc["KR:CCC", "mh_new_inst_180"] == 0.0
    # 180세션 밖 신규는 안 센다
    assert _run([_report("KR:AAA", OUT_180, "institution", 5.4, 5.4)]).loc["KR:AAA", "mh_new_inst_180"] == 0.0


def test_5퍼센트_아래_이탈은_순감으로_잡힌다() -> None:
    """`mh_exit_60`(이탈 건수)은 피처에서 뺐다(6차 "묶음마다 ≤ 3", 리드 결정 2026-09-27).
    이탈이 신호에서 사라지지는 않는다 — 전문투자자 이탈은 `mh_inst_net_60` 의 순감이다."""
    got = _run([_report("KR:AAA", IN_60, "institution", 4.2, -1.3)])
    assert "mh_exit_60" not in got.columns
    assert got.loc["KR:AAA", "mh_inst_net_60"] == -1.3


def test_보고가_없는_종목은_0이다_결측이_아니다() -> None:
    """'변동이 없었다' 는 사실이다. 결측으로 두면 rank-gauss 가 중앙으로 보내
    '보고가 없는 회사' 와 '보고가 상쇄된 회사' 를 같은 값으로 만든다."""
    got = _run([_report("KR:AAA", IN_60, "institution", 6.0, 1.0)])
    assert set(got.index) == set(ENTITIES)
    assert (got.loc[["KR:BBB", "KR:CCC"]] == 0.0).all().all()


def test_표가_비면_빈_표다() -> None:
    assert _run([]).empty or _run([]).dropna(how="all").empty


def test_창고에_시장과_창을_넘긴다() -> None:
    """market 을 안 넘기면 미장 행이 국장 피처에 섞이고(memory `market-filter-must-be-in-sql`),
    lookback 을 안 넘기면 하한 프루닝이 꺼져 창고를 통째로 훑는다."""
    analyst = FakeAnalyst(pd.DataFrame([_report("KR:AAA", IN_60, "institution", 6.0, 1.0)]))
    build("G10", analyst, AS_OF)
    kwargs = analyst.store.seen[0]
    assert kwargs["market"] == "KR" and int(kwargs["lookback"]) >= 260
    assert kwargs["as_of"] == AS_OF
