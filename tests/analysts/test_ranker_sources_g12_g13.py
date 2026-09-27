"""6차 G12·G13 — 국장 손익구조 변동 · 공급계약 금액 피처.

무효가 되는 길 넷을 못 박는다.

1. G12 의 증감을 **직전 영업이익으로 나누는 것** — 적자에서 적자로 가면 부호가 뒤집힌다.
2. G12 의 시가총액을 **공시 당일 것**으로 쓰는 것 — 그 시총은 공시의 반응을 이미 담고 있다.
3. 60세션 창을 넘은 공시를 결측으로 두지 않는 것(발표 효과의 창).
4. G13 의 해지를 체결과 **부호로 합치는 것** — 해지 없는 종목과 상쇄된 종목이 같아진다.

관측 시각(개장 전)은 `store.get(as_of=)` 의 일이므로, 여기서는 함수에 넘어온 행만 쓰이는지를 본다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.ranker_sources import (
    GROUPS,
    PL_MAX_SESSIONS,
    SUPPLY_SESSIONS,
    build,
)

ENTITIES = ("KR:AAA", "KR:BBB", "KR:CCC")
SESSIONS = list(pd.bdate_range("2025-01-02", periods=300).date)
AS_OF = datetime(2026, 3, 2, tzinfo=UTC)
CAP = 1_000_000_000.0


class FakeStore:
    """표 이름으로 갈라 준다. 시가총액은 `market_stats` 에서 온다(G12)."""

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self.tables = tables
        self.seen: list[tuple[str, dict[str, object]]] = []

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:
        self.seen.append((table, kwargs))
        return self.tables.get(table, pd.DataFrame())


class FakeAnalyst:
    market = "KR"
    wide = staticmethod(Analyst.wide)

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self.store = FakeStore(tables)

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        return pd.DataFrame([
            {"entity_id": e, "session": s, "close": 10.0, "volume": 100.0, "value": 1000.0}
            for s in SESSIONS for e in ENTITIES
        ])

    def tradable_entities(self, as_of: datetime, *, lookback: int) -> None:
        return None


def _caps(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame([
        {"entity_id": e, "valid_from": pd.Timestamp(d, tz="Asia/Seoul"),
         "metric": "market_cap", "value": CAP}
        for d in days for e in ENTITIES
    ])


def _pl(entity: str, day: date, *, op_cur: float, op_base: float,
        basis: str = "consolidated", turn: str = "") -> dict[str, object]:
    return {
        "entity_id": entity,
        "valid_from": pd.Timestamp(day, tz="Asia/Seoul"),
        "observed_at": pd.Timestamp(day, tz="Asia/Seoul"),
        "basis": basis, "op_cur": op_cur, "op_base": op_base,
        "sales_cur": 1e11, "sales_base": 1e11, "net_cur": op_cur, "net_base": op_base,
        "op_turn": turn, "period_end": "2025-12-31",
    }


def _run_g12(rows: list[dict[str, object]], *, cap_days: list[date] | None = None) -> pd.DataFrame:
    tables = {
        "pl_change": pd.DataFrame(rows),
        "market_stats": _caps(cap_days if cap_days is not None else SESSIONS),
    }
    return build("G12", FakeAnalyst(tables), AS_OF)


def _sc(entity: str, day: date, kind: str, ratio: float) -> dict[str, object]:
    return {
        "entity_id": entity,
        "valid_from": pd.Timestamp(day, tz="Asia/Seoul"),
        "kind": kind, "sales_ratio": ratio,
    }


def _run_g13(rows: list[dict[str, object]]) -> pd.DataFrame:
    return build("G13", FakeAnalyst({"supply_contracts": pd.DataFrame(rows)}), AS_OF)


IN_WINDOW = SESSIONS[-5]
OUT_WINDOW = SESSIONS[-(PL_MAX_SESSIONS + 20)]


# --------------------------------------------------------------------------- G12


def test_g12_등록된_열만_등록된_순서로_돌려준다() -> None:
    got = _run_g12([_pl("KR:AAA", IN_WINDOW, op_cur=2e8, op_base=1e8)])
    assert list(got.columns) == list(GROUPS["G12"])


def test_g12_증감을_시가총액으로_나눈다() -> None:
    got = _run_g12([_pl("KR:AAA", IN_WINDOW, op_cur=2e8, op_base=1e8)])
    assert got.loc["KR:AAA", "pl_op_delta_cap"] == (2e8 - 1e8) / CAP


def test_g12_적자에서_적자로_가도_개선은_양수다() -> None:
    """분모를 직전 영업이익(음수)으로 두면 부호가 뒤집힌다 — 그래서 시총으로 나눈다."""
    got = _run_g12([_pl("KR:AAA", IN_WINDOW, op_cur=-1e8, op_base=-3e8)])
    assert got.loc["KR:AAA", "pl_op_delta_cap"] > 0


def test_g12_전환표지는_원문_칸을_그대로_쓴다() -> None:
    got = _run_g12([
        _pl("KR:AAA", IN_WINDOW, op_cur=1e8, op_base=-1e8, turn="흑자전환"),
        _pl("KR:BBB", IN_WINDOW, op_cur=-1e8, op_base=1e8, turn="적자전환"),
        _pl("KR:CCC", IN_WINDOW, op_cur=1e8, op_base=2e8, turn="흑자지속"),
    ])
    assert got.loc["KR:AAA", "pl_turn_sign"] == 1.0
    assert got.loc["KR:BBB", "pl_turn_sign"] == -1.0
    assert got.loc["KR:CCC", "pl_turn_sign"] == 0.0     # 지속은 사건이 아니라 상태다


def test_g12_창을_넘은_공시는_결측이고_공시_없는_종목도_결측이다() -> None:
    got = _run_g12([_pl("KR:AAA", OUT_WINDOW, op_cur=2e8, op_base=1e8)])
    assert got.empty or "KR:AAA" not in got.index
    got = _run_g12([_pl("KR:AAA", IN_WINDOW, op_cur=2e8, op_base=1e8)])
    assert "KR:BBB" not in got.index                     # 0 이 아니라 결측이다


def test_g12_연결을_별도보다_먼저_쓴다() -> None:
    got = _run_g12([
        _pl("KR:AAA", IN_WINDOW, op_cur=5e8, op_base=1e8, basis="separate"),
        _pl("KR:AAA", IN_WINDOW, op_cur=2e8, op_base=1e8, basis="consolidated"),
    ])
    assert got.loc["KR:AAA", "pl_op_delta_cap"] == (2e8 - 1e8) / CAP


def test_g12_공시_당일_시가총액은_쓰지_않는다() -> None:
    """당일 시총은 그 공시의 반응을 이미 담고 있다 — 전날까지의 마지막 값을 쓴다."""
    got = _run_g12([_pl("KR:AAA", IN_WINDOW, op_cur=2e8, op_base=1e8)],
                   cap_days=[IN_WINDOW])                 # 그 날짜 것밖에 없으면 분모가 없다
    assert got.empty or pd.isna(got.loc["KR:AAA", "pl_op_delta_cap"])


# --------------------------------------------------------------------------- G13


def test_g13_등록된_열만_등록된_순서로_돌려준다() -> None:
    got = _run_g13([_sc("KR:AAA", IN_WINDOW, "contract", 12.0)])
    assert list(got.columns) == list(GROUPS["G13"])


def test_g13_체결은_합치고_가장_최근_한_건을_따로_적는다() -> None:
    got = _run_g13([
        _sc("KR:AAA", SESSIONS[-10], "contract", 12.0),
        _sc("KR:AAA", SESSIONS[-3], "contract", 5.0),
    ])
    assert got.loc["KR:AAA", "sc_ratio_60"] == 17.0
    assert got.loc["KR:AAA", "sc_last_ratio"] == 5.0


def test_g13_해지를_체결과_부호로_합치지_않는다() -> None:
    got = _run_g13([
        _sc("KR:AAA", SESSIONS[-10], "contract", 12.0),
        _sc("KR:AAA", SESSIONS[-3], "termination", 12.0),
        _sc("KR:BBB", SESSIONS[-3], "contract", 12.0),
    ])
    assert got.loc["KR:AAA", "sc_ratio_60"] == got.loc["KR:BBB", "sc_ratio_60"] == 12.0
    assert got.loc["KR:AAA", "sc_cancel_60"] == 12.0
    assert got.loc["KR:BBB", "sc_cancel_60"] == 0.0      # 상쇄되지 않는다


def test_g13_계약이_없으면_0_이고_창_밖은_세지_않는다() -> None:
    got = _run_g13([
        _sc("KR:AAA", IN_WINDOW, "contract", 12.0),
        _sc("KR:BBB", SESSIONS[-(SUPPLY_SESSIONS + 20)], "contract", 99.0),
    ])
    assert got.loc["KR:BBB", "sc_ratio_60"] == 0.0       # 결측이 아니라 0(사실이다)
    assert got.loc["KR:CCC", "sc_ratio_60"] == 0.0


def test_g13_비율이_결측인_행만_빠진다() -> None:
    """공시유보로 금액이 비어 온 행. 종목이 빠지는 것이 아니다."""
    got = _run_g13([
        _sc("KR:AAA", SESSIONS[-10], "contract", float("nan")),
        _sc("KR:AAA", SESSIONS[-3], "contract", 7.0),
    ])
    assert got.loc["KR:AAA", "sc_ratio_60"] == 7.0
