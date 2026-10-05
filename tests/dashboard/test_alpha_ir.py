"""장부별 지수 대비 IR (dashboard.md §4 "지수 대비 IR", accounting.md §8.2).

지켜야 하는 것:
- 숫자는 ``accounting/relative.compare`` 그대로다 — 화면이 IR 을 따로 계산하는 길이 없다(판정 도구와 같은 함수).
- 세션이 ``dashboard.ir_min_sessions`` 보다 적은 창은 IR·β·α·추적오차가 **null**(표본 부족). 누적 초과는 싣는다.
- 창 길이·표본 하한·리셋일은 창고 config 의 그 시점 값이다(불변식 10). 없으면 지어내지 않는다.
- as_of 뒤의 NAV·ETF 는 안 보인다(불변식 9). 모의계좌 '전체' 창은 종료 기준 창 시작(8/28)부터.
- 미장·혼합 장부는 대상 아님, 첫 NAV 가 없는 장부는 '장부 아직 없음'.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

from quant_rl_trading.accounting import ledger, relative
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.dashboard.api import trading as trading_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.dashboard.services import alpha_ir as service
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store

SEOUL = ZoneInfo("Asia/Seoul")
#: 8/26·8/27 은 종료 기준 창 시작(8/28) 전, 9/14·9/15 는 결손(실제 9/13~16 정지와 같은 자리).
DAYS = trading_days(Market.KR, date(2026, 8, 26), date(2026, 10, 2))
GAP = {date(2026, 9, 14), date(2026, 9, 15)}
NOW = datetime(2026, 10, 5, 9, tzinfo=SEOUL)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)


def _nav(root: Path, days: list[date], step: float, tag: str) -> None:
    value, rows = 100.0, []
    for i, d in enumerate(days):
        value *= 1 + step * (1 if i % 3 else -1.5)
        rows.append({"entity_id": ledger.ACCOUNT, "valid_from": _at(d, 16), "observed_at": _at(d, 16), "source": "test",
                     "nav": 5e6 * value, "index_value": value})
    Store(root=root).append(ledger.NAV_DAILY, rows, ingest_run_id=tag)


def _config(store: Store, name: str, value: object, revision: int = 1) -> None:
    moment = datetime(2026, 8, 1, tzinfo=SEOUL)
    store.append("config", [{"entity_id": name, "valid_from": moment, "observed_at": moment, "source": "test",
                             "revision": revision, "value_json": json.dumps(value)}], ingest_run_id=f"cfg-{name}")


@pytest.fixture
def research(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    store = Store(root=root)
    store.seed_config_defaults()
    etf, close = [], 30_000.0
    for i, d in enumerate(DAYS):
        close *= 1 + 0.004 * (1 if i % 2 else -0.8)
        etf.append({"entity_id": BENCHMARK_ETF, "valid_from": _at(d, 15, 50), "observed_at": _at(d, 15, 50),
                    "source": "test", "market": "KR", "board": "ETF", "close": close})
    store.append("indices", etf, ingest_run_id="etf")
    _nav(root / "_paper", [d for d in DAYS if d not in GAP], 0.003, "paper")
    _nav(root / "_z2_shadow", DAYS[-6:], 0.002, "z2")
    _nav(root / "_g1us_shadow", DAYS[-6:], 0.002, "g1")      # 미장 장부 — NAV 가 있어도 대상 아님
    return root


def _run(root: Path, as_of: datetime = NOW) -> dict:
    store = Store(root=root)
    return service.alpha_ir(root, config=store, source=store, as_of=as_of)


def _book(result: dict, key: str) -> dict:
    return next(b for b in result["books"] if b["key"] == key)


def test_숫자는_판정_도구와_같은_함수에서_온다(research: Path) -> None:
    result = _run(research)
    assert result["available"] and result["through"] == "2026-10-02"
    paper = _book(result, "paper")
    assert paper["since"] == "2026-08-28"                               # 종료 기준 창 시작 — 8/26·8/27 은 빠진다

    store = Store(root=research)
    ours = relative.book_index(Store(root=research / "_paper"), as_of=NOW, lookback=None)
    etf = relative.etf_close(store, as_of=NOW, lookback=60)
    window = trading_days(Market.KR, date(2026, 8, 28), date(2026, 10, 2))
    expected = relative.compare(ours, etf, window=window, annual_yield=0.015)
    assert expected is not None
    full = paper["windows"]["all"]
    assert full["sessions"] == len(expected.sessions) == len(window) - 2
    assert full["missing"] == ["2026-09-14", "2026-09-15"]               # 결손일을 그대로 싣는다
    assert full["sufficient"] is True
    assert full["ir"] == pytest.approx(expected.ir)
    assert full["beta"] == pytest.approx(expected.beta)
    assert full["alpha"] == pytest.approx(expected.alpha)
    assert full["tracking_error"] == pytest.approx(expected.tracking_error)
    assert full["excess"] == pytest.approx(expected.excess)
    assert paper["curve"][-1][1] == pytest.approx(expected.excess)       # 추이 끝점 == 누적 초과

    rolling = paper["windows"]["20"]
    last20 = relative.compare(ours, etf, window=window, annual_yield=0.015, last=20)
    assert rolling["sessions"] == 20 and rolling["sufficient"] is True
    assert rolling["ir"] == pytest.approx(last20.ir)
    # 60세션 창은 아직 안 찼다 — 24세션으로 '최근 60세션 IR' 을 내면 그건 전체 IR 이다.
    assert paper["windows"]["60"]["sufficient"] is False and paper["windows"]["60"]["ir"] is None


def test_표본이_적으면_비율은_null_누적_초과는_싣는다(research: Path) -> None:
    z2 = _book(_run(research), "z2")
    full = z2["windows"]["all"]
    assert full["sessions"] == 6 and full["need"] == 20 and full["sufficient"] is False
    assert full["ir"] is None and full["beta"] is None and full["alpha"] is None and full["tracking_error"] is None
    assert full["excess"] is not None


def test_대상_아님과_장부_없음(research: Path) -> None:
    result = _run(research)
    assert _book(result, "g1us")["status"] == "not_applicable"
    assert _book(result, "shadow")["status"] == "not_applicable"
    idx = _book(result, "idxv6")
    assert idx["status"] == "no_book" and idx["windows"] == {}
    assert [b["key"] for b in result["books"]] == [b["key"] for b in service.BOOKS]


def test_되감으면_그때까지만_보인다(research: Path) -> None:
    rewound = _run(research, as_of=datetime(2026, 9, 10, 9, tzinfo=SEOUL))
    assert rewound["through"] == "2026-09-09"
    paper = _book(rewound, "paper")["windows"]["all"]
    assert paper["last"] == "2026-09-09" and paper["sufficient"] is False and paper["ir"] is None
    assert _book(rewound, "z2")["status"] == "no_book"                  # 9월 말에 시작한 장부는 그때 아직 없다


def test_창_길이와_표본_하한은_config_에서(research: Path) -> None:
    store = Store(root=research)
    _config(store, "dashboard.ir_windows_sessions", [5])
    _config(store, "dashboard.ir_min_sessions", 4)
    result = _run(research)
    assert [c["key"] for c in result["columns"]] == ["5", "all", "reset"]
    z2 = _book(result, "z2")
    assert z2["windows"]["5"]["sessions"] == 5 and z2["windows"]["5"]["sufficient"] is True
    assert z2["windows"]["all"]["sufficient"] is True and z2["windows"]["all"]["ir"] is not None


def test_리셋_뒤_창은_리셋일부터(research: Path) -> None:
    result = _run(research)
    assert result["reset_date"] == "2026-11-26" and result["reset_open"] is False
    assert _book(result, "paper")["windows"]["reset"] is None           # 리셋 전 — 자리만 있다

    _config(Store(root=research), "dashboard.ir_reset_date", "2026-09-21")
    moved = _run(research)
    reset = _book(moved, "paper")["windows"]["reset"]
    assert moved["reset_open"] is True and reset["first"] == "2026-09-21" and reset["missing"] == []


def test_설정이_없으면_지어내지_않는다(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    result = service.alpha_ir(empty, config=Store(root=empty), source=Store(root=empty), as_of=NOW)
    assert result["available"] is False and "dashboard.ir_windows_sessions" in result["note"]


# --------------------------------------------------------------------------- API


def make_app(store: Store) -> Flask:
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = ReplayClock(NOW)
    app.register_blueprint(trading_api.bp)
    app.config.update(TESTING=True)
    return app


def test_API_는_as_of_를_받고_되돌려준다(research: Path) -> None:
    # 화면의 주 장부는 모의계좌(data/_paper)다 — 장부·ETF 는 그 부모(연구 창고)에서 찾는다.
    paper = Store(root=research / "_paper")
    paper.seed_config_defaults()
    client = make_app(paper).test_client()
    body = client.get("/api/trading/alpha-ir", query_string={"as_of": "2026-09-30T09:00:00+09:00"}).get_json()
    assert body["as_of"].startswith("2026-09-30T09:00:00") and body["live"] is False
    assert body["data"]["through"] == "2026-09-29"
    assert _book(body["data"], "paper")["windows"]["all"]["last"] == "2026-09-29"
    assert client.get("/api/trading/alpha-ir", query_string={"as_of": "2026-09-30T09:00:00"}).status_code == 400
    live = client.get("/api/trading/alpha-ir").get_json()
    assert live["live"] is True and live["data"]["through"] == "2026-10-02"
    assert NOW - timedelta(days=1) < datetime.fromisoformat(live["as_of"]) <= NOW
