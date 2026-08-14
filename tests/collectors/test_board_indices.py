"""시장 대표지수(코스피·코스닥) 수집.

화면의 "지수 대비" 패널이 계속 "없음" 이었던 이유는 화면 버그가 아니라
``KR:IDX:KOSPI`` 이름의 지수가 창고에 정말 없어서였다. KRX 통합지수 경로
(``/idx/krx_dd_trd``)에는 KRX 300·KRX 100 만 있고 대표지수가 없다 — 대표지수는
시장별 경로에 따로 있다.

여기서 지키는 것 둘.

1. **명단 밖은 안 받는다.** 두 경로가 같은 이름('화학'·'제조')을 쓰므로
   이름만으로 entity_id 를 만들면 서로 다른 지수가 한 이름에 합쳐진다.
2. **run_key 가 갈려 있다.** 한 테이블(``indices``)을 두 패널이 채우므로
   매니페스트 이름이 같으면 한쪽이 다른 쪽의 세션을 완료로 보고 건너뛴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.collectors.krx_openapi import (
    BOARD_INDEX_PATHS,
    normalize_board_indices,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.panels import OPENAPI_PANELS, PanelBackfiller, run_id_for
from quant_rl_trading.collectors.publication import PublicationPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

VALID_FROM = datetime(2026, 8, 13, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 8, 13, 9, tzinfo=UTC)

MON = date(2024, 3, 4)
NOW = datetime(2024, 6, 1, tzinfo=UTC)
LAG = 1800.0
KST_OFFSET = timedelta(hours=9)


# -- 정규화 ---------------------------------------------------------------------


def _row(name: str, board: str, close: float = 2610.0) -> dict[str, Any]:
    return {
        "name": name, "index_class": board, "open": 2600.0, "high": 2620.0,
        "low": 2590.0, "close": close, "volume": 1.0e8, "value": 1.0e13,
    }


def test_대표지수는_화면이_기대하는_식별자로_들어온다() -> None:
    out = normalize_board_indices(
        [_row("코스피", "KOSPI", 2610.0), _row("코스닥", "KOSDAQ", 861.37)],
        market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT,
    )

    ids = {row["entity_id"]: row for row in out}
    assert set(ids) == {"KR:IDX:KOSPI", "KR:IDX:KOSDAQ"}
    assert ids["KR:IDX:KOSDAQ"]["close"] == pytest.approx(861.37)
    assert ids["KR:IDX:KOSPI"]["board"] == "KOSPI"
    assert ids["KR:IDX:KOSPI"]["observed_at"] == OBSERVED_AT


def test_같은_이름의_업종지수는_두_시장에서_섞이지_않는다() -> None:
    """'화학' 은 코스피에도 코스닥에도 있다. 이름만으로 entity_id 를 만들면
    서로 다른 지수가 한 이름에 합쳐져 조용히 뒤섞인다 — 그래서 안 받는다."""
    out = normalize_board_indices(
        [_row("화학", "KOSPI", 100.0), _row("화학", "KOSDAQ", 200.0)],
        market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT,
    )

    assert out == []


def test_명단에_있어도_소속이_다르면_안_받는다() -> None:
    """(이름, 소속) 쌍으로 고른다. 이름만 맞는 행을 받으면 경로가 바뀌었을 때
    엉뚱한 지수가 코스피 행세를 한다."""
    assert normalize_board_indices(
        [_row("코스피", "KOSDAQ")],
        market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT,
    ) == []


def test_종가가_없으면_버린다() -> None:
    row = _row("코스피", "KOSPI")
    row["close"] = None
    assert normalize_board_indices(
        [row], market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT
    ) == []


# -- 패널 배선 -------------------------------------------------------------------


@dataclass
class FakeOpenApi:
    """대표지수 경로만 흉내 낸다. 통합지수 경로와 응답이 겹치지 않는다."""

    name: str = "krx_openapi-fake"
    calls: list[tuple[str, date]] = field(default_factory=list)

    def board_indices_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(("board", day))
        return [
            _row("코스피", "KOSPI", 2610.0),
            _row("코스닥", "KOSDAQ", 861.37),
            _row("화학", "KOSPI", 100.0),  # 명단 밖 — 버려져야 한다
        ]

    def indices_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(("krx", day))
        return [
            {"name": "KRX 300", "index_class": "KRX", "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1500.0, "volume": 0.0, "value": 0.0},
        ]


def _published(day: date) -> datetime:
    """세션 마감 15:30 KST + 30분."""
    return datetime(day.year, day.month, day.day, 16, 0) - KST_OFFSET


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC)


def _make(store, tmp_path, panel_name: str, source: FakeOpenApi) -> PanelBackfiller:  # type: ignore[no-untyped-def]
    clock = ReplayClock(NOW)
    return PanelBackfiller(
        store=store,
        source=source,  # type: ignore[arg-type]
        clock=clock,
        archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock),
        panel=OPENAPI_PANELS[panel_name],
        market=Market.KR,
    )


def test_대표지수_패널이_명단만_적재한다(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    result = _make(store, tmp_path, "indices-board", FakeOpenApi()).run_session(MON)

    assert result.rows == 2
    frame = store.get("indices", as_of=_utc(_published(MON)) + timedelta(minutes=1))
    assert set(frame["entity_id"]) == {"KR:IDX:KOSPI", "KR:IDX:KOSDAQ"}
    # 지수는 종목 유니버스에 끼면 안 된다.
    assert store.get("prices", as_of=_utc(_published(MON))).empty


def test_두_지수_패널이_서로의_세션을_건너뛰지_않는다(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """같은 테이블·같은 날이라도 매니페스트 이름이 갈려 있어야 한다.

    갈려 있지 않으면 통합지수(KRX 300)가 이미 들어간 1,000여 세션을 대표지수
    패널이 전부 완료로 보고 건너뛴다 — 백필이 조용히 0행으로 끝난다.
    """
    store.seed_config_defaults()
    source = FakeOpenApi()

    assert _make(store, tmp_path, "indices-krx", source).run_session(MON).rows == 1
    assert _make(store, tmp_path, "indices-board", source).run_session(MON).rows == 2

    frame = store.get("indices", as_of=_utc(_published(MON)) + timedelta(minutes=1))
    assert set(frame["entity_id"]) == {
        "KR:IDX:KRX 300", "KR:IDX:KOSPI", "KR:IDX:KOSDAQ"
    }
    assert run_id_for("indices-board", Market.KR, MON) != run_id_for(
        "indices", Market.KR, MON
    )


def test_대표지수_패널은_재개된다(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    source = FakeOpenApi()
    assert _make(store, tmp_path, "indices-board", source).run_session(MON).ok
    assert _make(store, tmp_path, "indices-board", source).run_session(MON).skipped


def test_대표지수_경로는_통합지수_경로와_다르다() -> None:
    """문서가 아니라 실측이 근거다(2026-08-15). 통합지수 경로에는 코스피가 없다."""
    assert set(BOARD_INDEX_PATHS) == {"KOSPI", "KOSDAQ"}
    assert "/idx/krx_dd_trd" not in BOARD_INDEX_PATHS.values()


# -- 미장 지수 -------------------------------------------------------------------


def test_나스닥은_FRED_시리즈로_들어온다() -> None:
    """``NASDAQCOM`` 은 ``/fred/series`` 로 이름을 대조해 고른 것이다
    (실측 2026-08-15: "NASDAQ Composite"). **시리즈 ID 를 짐작하지 않는다** —
    release_id 18 을 소매판매로 짐작했다가 금리 일정이 소매판매라는 이름으로
    화면에 뜬 적이 있다 (data-contract.md §4).
    """
    from quant_rl_trading.collectors.macro_source import FRED_INDICES

    assert FRED_INDICES["NASDAQCOM"][0] == "US:IDX:NASDAQ"


def test_미장_지수는_마감_전_세션을_저장하지_않는다() -> None:
    """오늘 종가를 오늘 낮에 저장하면 그 자체가 미래를 보는 것이다."""
    from quant_rl_trading.collectors.macro_source import index_rows

    observed_at = datetime(2026, 8, 13, 12, tzinfo=UTC)  # 그날 세션 마감 전
    rows = index_rows(
        "NASDAQCOM",
        [{"date": "2026-08-12", "value": "26800.0"}, {"date": "2026-08-13", "value": "26900.0"}],
        observed_at=observed_at,
    )

    assert [row["entity_id"] for row in rows] == ["US:IDX:NASDAQ"]
    assert rows[0]["valid_from"].date() == date(2026, 8, 12)
    assert rows[0]["observed_at"] <= observed_at


def test_미장_지수는_값이_비면_버린다() -> None:
    """FRED 는 휴장일을 '.' 로 준다. 0 으로 바꾸면 그날 수익률이 -100% 가 된다."""
    from quant_rl_trading.collectors.macro_source import index_rows

    rows = index_rows(
        "NASDAQCOM",
        [{"date": "2021-01-01", "value": "."}, {"date": "2021-01-04", "value": "12698.45"}],
        observed_at=datetime(2026, 8, 15, tzinfo=UTC),
    )

    assert len(rows) == 1
    assert rows[0]["close"] == pytest.approx(12698.45)
