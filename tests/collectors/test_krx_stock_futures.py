"""KRX 개별주식선물(N3) 수집 — ``stock_futures`` 표 (data-contract §4-3).

지키는 것.

1. **월물만** 들어온다 — 스프레드("SP")·ETF 선물은 거른다(원본은 RawArchive 에 남는다).
2. **원 필드 보존** — 정산가·현물가·미결제약정·OHLC·거래량·대금이 이름만 바뀌어 들어온다.
3. **기초자산 매핑은 지어내지 않는다** — 못 찾으면 underlying_id 는 None, 행은 남는다.
4. 같은 이름을 쓴 종목이 시기마다 다르면 **그 날짜의 종목**을 고른다.
5. observed_at 은 다른 KRX Open API 패널 백필과 같은 규칙(세션 마감 + 공표 지연), 재실행은 건너뛴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.collectors.krx_openapi import STOCK_FUTURES_PATHS, KrxOpenApi
from quant_rl_trading.collectors.krx_stock_futures import (
    UnderlyingResolver,
    contract_month,
    normalize_stock_futures,
    stock_futures_panel,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.panels import PanelBackfiller, run_id_for
from quant_rl_trading.collectors.publication import PublicationPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

MON = date(2024, 3, 4)
NOW = datetime(2024, 6, 1, tzinfo=UTC)
LAG = 1800.0
VALID_FROM = datetime(2024, 3, 4, tzinfo=UTC)
OBSERVED_AT = datetime(2024, 3, 4, 7, tzinfo=UTC)


def _raw(prod: str, isu_cd: str, isu_nm: str, *, board: str = "KOSPI", oi: str = "1147") -> dict[str, Any]:
    return {
        "BAS_DD": "20240304", "PROD_NM": prod, "MKT_NM": "정규", "ISU_CD": isu_cd, "ISU_NM": isu_nm,
        "TDD_CLSPRC": "13,750.00", "CMPPREVDD_PRC": "-50.00", "TDD_OPNPRC": "13800.00", "TDD_HGPRC": "13850.00",
        "TDD_LWPRC": "13750.00", "SPOT_PRC": "13700.00", "SETL_PRC": "13750.00", "ACC_TRDVOL": "1147",
        "ACC_TRDVAL": "157998500", "ACC_OPNINT_QTY": oi, "_board": board,
    }


def _resolver() -> UnderlyingResolver:
    return UnderlyingResolver.from_records([
        ("KR:005930", "삼성전자", date(2021, 8, 11), date(2026, 10, 8)),
        ("KR:005935", "삼성전자우", date(2021, 8, 11), date(2026, 10, 8)),
        ("KR:036460", "한국가스공사", date(2021, 8, 11), date(2026, 10, 8)),
        ("KR:010060", "OCI", date(2021, 8, 11), date(2023, 5, 26)),
        ("KR:456040", "OCI", date(2023, 5, 30), date(2026, 10, 8)),
        ("KR:010060", "OCI홀딩스", date(2023, 5, 30), date(2026, 10, 8)),
        ("KR:CB0001", "BNK금융지주", date(2021, 8, 11), date(2026, 10, 8)),  # 6자리 영숫자 코드도 받는다
        ("US:AAPL", "삼성전자", date(2021, 8, 11), date(2026, 10, 8)),  # 다른 시장은 버린다
    ])


def _norm(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return normalize_stock_futures(rows, market="KR", valid_from=VALID_FROM, observed_at=OBSERVED_AT,
                                   resolver=_resolver())


def test_월물만_남고_스프레드와_ETF_선물은_거른다() -> None:
    out = _norm([
        _raw("삼성전자 선물", "111V3000", "삼성전자    F 202403 "),
        _raw("삼성전자 선물", "411V3V60", "삼성전자    SP 2403-2406 "),
        _raw("ETF KODEX 삼성그룹 선물", "1E1V3000", "KODEX삼성  F 202403 "),
    ])
    assert [r["entity_id"] for r in out] == ["KR:FUT:111V3000"]


def test_원_필드가_이름만_바뀌어_보존된다() -> None:
    (row,) = _norm([_raw("BNK금융지주 선물", "1BTK9000", "BNK금융지  F 202403 ")])
    assert row["settle_price"] == pytest.approx(13750.0)
    assert row["spot_price"] == pytest.approx(13700.0)
    assert row["open_interest"] == pytest.approx(1147.0)
    assert row["close"] == pytest.approx(13750.0)  # 천 단위 쉼표
    assert row["change"] == pytest.approx(-50.0)
    assert row["value"] == pytest.approx(157998500.0)
    assert row["contract_month"] == "202403"
    assert row["product_code"] == "BTK"
    assert row["board"] == "KOSPI" and row["mkt_nm"] == "정규"
    assert row["prod_nm"] == "BNK금융지주 선물"
    assert row["underlying_id"] == "KR:CB0001" and row["map_method"] == "exact"
    assert row["observed_at"] == OBSERVED_AT and row["valid_from"] == VALID_FROM


def test_빈_값은_0이_아니라_None() -> None:
    raw = _raw("삼성전자 선물", "111V6000", "삼성전자    F 202406 ")
    raw["TDD_CLSPRC"] = ""
    (row,) = _norm([raw])
    assert row["close"] is None


def test_줄임말은_별칭으로_우선주는_고르지_않는다() -> None:
    r = _resolver()
    assert r.resolve("한국가스 선물", MON) == ("KR:036460", "alias")
    assert r.resolve("삼성전자 선물", MON) == ("KR:005930", "exact")
    assert r.resolve("POSCO 홀딩스 선물", MON) == (None, "none")  # 창고에 없는 이름은 지어내지 않는다


def test_못_찾은_기초자산도_행은_남는다() -> None:
    (row,) = _norm([_raw("우리은행 선물", "1WRK9000", "우리은행    F 202403 ")])
    assert row["underlying_id"] is None and row["map_method"] == "none"


def test_같은_이름은_그_날짜의_종목으로() -> None:
    r = _resolver()
    assert r.resolve("OCI 선물", date(2022, 1, 3))[0] == "KR:010060"
    assert r.resolve("OCI 선물", date(2024, 1, 2))[0] == "KR:456040"
    # 이력 앞(universe 가 없는 2015) — 가장 가까운 구간
    assert r.resolve("OCI홀딩스 선물", date(2015, 8, 3))[0] == "KR:010060"


def test_월물_표기() -> None:
    assert contract_month("BNK금융지  F 201509 ") == "201509"
    assert contract_month("BNK금융지  SP 1509-1512 ") is None


def test_두_시장_경로() -> None:
    assert STOCK_FUTURES_PATHS == {"KOSPI": "/drv/eqsfu_stk_bydd_trd", "KOSDAQ": "/drv/eqkfu_ksq_bydd_trd"}


def test_클라이언트는_시장마다_한_콜_원필드_그대로() -> None:
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["AUTH_KEY"] == "k"
        assert request.url.params["basDd"] == "20240304"
        return httpx.Response(200, json={"OutBlock_1": [{"PROD_NM": "삼성전자 선물", "ISU_CD": "111V3000"}]})

    api = KrxOpenApi(api_key="k", transport=httpx.MockTransport(handler))
    rows = api.stock_futures_on(MON)
    assert len(seen) == 2
    assert {r["_board"] for r in rows} == {"KOSPI", "KOSDAQ"}
    assert rows[0]["PROD_NM"] == "삼성전자 선물"


# -- 패널 배선 -------------------------------------------------------------------


@dataclass
class FakeOpenApi:
    name: str = "krx_openapi-fake"
    calls: list[date] = field(default_factory=list)

    def stock_futures_on(self, day: date) -> list[dict[str, Any]]:
        self.calls.append(day)
        return [
            _raw("삼성전자 선물", "111V3000", "삼성전자    F 202403 "),
            _raw("삼성전자 선물", "111V6000", "삼성전자    F 202406 ", oi="0"),
            _raw("삼성전자 선물", "411V3V60", "삼성전자    SP 2403-2406 "),
        ]


def _make(store, tmp_path, source: FakeOpenApi) -> PanelBackfiller:  # type: ignore[no-untyped-def]
    clock = ReplayClock(NOW)
    return PanelBackfiller(
        store=store, source=source,  # type: ignore[arg-type]
        clock=clock, archive=RawArchive(root=tmp_path / "raw"),
        policy=PublicationPolicy(market=Market.KR, lag_seconds=LAG, clock=clock),
        panel=stock_futures_panel(_resolver()), market=Market.KR,
    )


def test_패널_적재_관측시각_재개(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    source = FakeOpenApi()
    result = _make(store, tmp_path, source).run_session(MON)
    assert result.rows == 2

    published = datetime(2024, 3, 4, 7, 0, tzinfo=UTC)  # 15:30 KST 마감 + 30분 = 07:00 UTC
    assert store.get("stock_futures", as_of=published - timedelta(seconds=1)).empty  # 공표 전엔 안 보인다
    frame = store.get("stock_futures", as_of=published + timedelta(minutes=1))
    assert set(frame["entity_id"]) == {"KR:FUT:111V3000", "KR:FUT:111V6000"}
    assert set(frame["underlying_id"]) == {"KR:005930"}
    assert (frame["observed_at"] == published).all()

    again = _make(store, tmp_path, source).run_session(MON)
    assert again.skipped and len(source.calls) == 1
    assert run_id_for("stock-futures", Market.KR, MON) == "bf-stock-futures-KR-20240304"


def test_매핑_정정은_revision_을_올린_새_행으로(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """별칭을 늘린 뒤 실패 행을 다시 매핑한다 — 원래 행은 남고, 읽기는 정정본을 고른다(불변식 4)."""
    from tools.backfill_stock_futures import remap

    store.seed_config_defaults()

    @dataclass
    class Src(FakeOpenApi):
        def stock_futures_on(self, day: date) -> list[dict[str, Any]]:
            return [_raw("코오롱인더스트리 선물", "1KLI3000", "코오롱인더  F 202403 ")]

    _make(store, tmp_path, Src()).run_session(MON)
    published = datetime(2024, 3, 4, 7, 0, tzinfo=UTC)
    before = store.get("stock_futures", as_of=published + timedelta(minutes=1))
    assert before["underlying_id"].isna().all()

    fixed = UnderlyingResolver.from_records([("KR:120110", "코오롱인더", date(2021, 8, 11), date(2026, 10, 8))])
    fixed.aliases["코오롱인더스트리"] = "코오롱인더"
    remap(store, fixed, NOW)

    after = store.get("stock_futures", as_of=published + timedelta(minutes=1))
    assert len(after) == 1
    row = after.iloc[0]
    assert row["underlying_id"] == "KR:120110" and row["map_method"] == "remap:alias"
    assert int(row["revision"]) == 1 and row["observed_at"] == published
    assert row["settle_price"] == pytest.approx(13750.0)
