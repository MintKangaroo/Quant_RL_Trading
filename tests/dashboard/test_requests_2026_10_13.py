"""사용자 요청(2026-10-13 밤) — dashboard.md '2026-10-13 밤 사용자 요청 여섯' 의 서버 쪽 사실.

1. 캘린더 하루 = 수익률 + **수익금**(회계 정의: NAV 증감 − 입출금). 입금일에 입금이 손익으로 섞이지 않는다.
5. 일정 달력의 '오늘' 은 as_of 의 서울 날짜 — 되감으면 그날이 오늘이다.
3. 13F 겹친 종목에 기관 수 변화(직전 분기)·우리 보유 겹침을 싣는다 — 우리 보유를 모르면 None(모름).
6. 병행 트랙 패널에 C1·AR shadow 는 나가지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.dashboard.services import model_story, schedule, thirteen_f
from quant_rl_trading.dashboard.services import trading as trading_service

# 2026-10-07(수)·10-08(목) 16:00 KST — 둘 다 국장 거래일.
D1 = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
D2 = datetime(2026, 10, 8, 7, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _nav(moment: datetime, *, nav: float, inflow: float, twr: float) -> dict[str, Any]:
    return {
        "entity_id": ledger_module.ACCOUNT, "valid_from": moment, "observed_at": moment, "source": "test",
        "nav": nav, "inflow": inflow, "twr_return": twr, "index_value": 100.0 * (1 + twr), "drawdown": 0.0,
        "cash_krw": nav, "cash_usd": 0.0, "equity_kr": 0.0, "equity_us": 0.0, "accrued_dividend": 0.0,
        "payable": 0.0, "fx_rate": 1_350.0, "tax_provision": 0.0, "nav_after_tax": nav,
        "benchmark_index": None, "benchmark_note": None,
    }


def test_캘린더_하루에_수익금이_실리고_입금은_섞이지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    # 10/7: NAV 1억. 10/8: 5천만 입금 + 100만 벌어 1.51억. TWR = (1.51억 − 0.5억)/1억 − 1 = +1%.
    store.append("nav_daily", [
        _nav(D1, nav=100_000_000.0, inflow=0.0, twr=0.0),
        _nav(D2, nav=151_000_000.0, inflow=50_000_000.0, twr=0.01),
    ], ingest_run_id="nav")
    cal = trading_service.returns_calendar(store, as_of=NOW, lookback=30, market="KR")
    day = {d["session"]: d for d in cal["days"]}["2026-10-08"]
    assert day["return"] == pytest.approx(0.01)
    assert day["pnl"] == pytest.approx(1_000_000.0)       # 증감 5,100만이 아니다
    assert day["inflow"] == pytest.approx(50_000_000.0)
    assert day["currency"] == "KRW"


def test_일정_달력의_오늘은_as_of_의_서울_날짜다(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    late = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)          # 10/9 01:30 KST — UTC 로는 아직 10/8
    assert schedule.month_schedule(store, as_of=late)["today"] == "2026-10-09"
    rewound = late - timedelta(days=30)
    data = schedule.month_schedule(store, as_of=rewound)
    assert data["today"] == "2026-09-09" and data["month"] == "2026-09"
    assert schedule.upcoming(store, as_of=late, days=7)["today"] == "2026-10-09"


def _filing(cik: str, name: str, quarter: datetime, cusip: str, value: float) -> dict[str, Any]:
    return {
        "entity_id": f"CUSIP:{cusip}", "valid_from": quarter, "observed_at": quarter + timedelta(days=45),
        "source": "test", "filer_cik": cik, "filer_name": name, "cusip": cusip, "issuer": f"ISSUER {cusip}",
        "value_usd": value, "shares": value / 10, "weight": 0.5, "folded_rows": 1, "lag_days": 45,
    }


def test_겹친_종목은_기관_수_변화를_싣는다(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    q1 = datetime(2026, 3, 31, tzinfo=UTC)
    q2 = datetime(2026, 6, 30, tzinfo=UTC)
    store.append("filings_13f", [
        _filing("1", "A", q1, "AAA", 10.0), _filing("1", "A", q2, "AAA", 10.0), _filing("1", "A", q2, "BBB", 5.0),
        _filing("2", "B", q1, "CCC", 3.0), _filing("2", "B", q2, "AAA", 7.0), _filing("2", "B", q2, "BBB", 2.0),
    ], ingest_run_id="13f")
    rows = {r["cusip"]: r for r in thirteen_f.consensus(store, as_of=NOW)}
    assert rows["AAA"]["filers"] == 2 and rows["AAA"]["filers_prev"] == 1
    assert rows["BBB"]["filers"] == 2 and rows["BBB"]["filers_prev"] == 0
    assert rows["AAA"]["filers_with_prev"] == 2
    meta = thirteen_f.mark_ours(list(rows.values()), store, as_of=NOW, ours=None)
    assert meta == {"ours_known": False, "ours_count": None}
    assert all(r["ours"] is None for r in rows.values())   # 모름 — False(안 들었다)가 아니다


def test_우리_보유를_모르면_None_이다(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    assert thirteen_f.our_us_holdings(None, as_of=NOW) is None
    assert thirteen_f.our_us_holdings(store, as_of=NOW) is None   # 실현 비중 기록이 없다


def test_병행_트랙에_C1_AR_shadow_는_나가지_않는다(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    hidden = {"name": "C1", "ledger": "data/_c1_shadow", "started": "2026-10-01", "compares": "x", "doc": "README.md"}
    hidden_cmp = {"name": "X", "ledger": "data/_x_shadow", "started": "2026-10-01", "compares": "x", "doc": "README.md",
                  "compare_ledger": "data/_ar_shadow", "compare_name": "AR"}
    monkeypatch.setattr(model_story, "SHADOW_TRACKS", [*model_story.SHADOW_TRACKS, hidden, hidden_cmp])
    tracks = model_story._tracks(tmp_path, NOW, NOW.date())
    ledgers = " ".join(t["ledger"] + str(t.get("compare_ledger", "")) for t in tracks)
    for name in model_story.HIDDEN_TRACK_LEDGERS:
        assert name not in ledgers
    for track in model_story.SHADOW_TRACKS:
        assert not set(model_story._track_ledgers(track)) & model_story.HIDDEN_TRACK_LEDGERS or track in (hidden, hidden_cmp)


def test_트랙_성과는_장부가_없으면_없다고_적는다(tmp_path: Path) -> None:
    tracks = [{"name": "Z2", "ledger": "data/_z2_shadow", "started": "2026-09-23", "compares": "x", "doc": "README.md"}]
    model_story.track_performance(tmp_path, tracks, None, NOW)
    perf = tracks[0]["perf"]
    assert perf[0]["status"] == "no_book" and perf[0]["name"] == "Z2"
