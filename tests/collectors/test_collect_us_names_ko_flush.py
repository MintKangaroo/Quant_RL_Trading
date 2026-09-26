"""미장 한글명 조각 적재 — 같은 조각 id 가 겹치면 접미사를 올려 다시 쓴다.

2026-09-16 밤 재실행이 낮 실행의 ``p1600`` 과 부딪혀 ``DuplicateIngestRun`` 으로
1,400종목에서 죽고 200종목을 잃었다 (logs/names-ko-202609.log).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from quant_rl_trading.collectors import naver_us_names as nn
from quant_rl_trading.store.errors import DuplicateIngestRun
from tools.collect_us_names_ko import _flush

SEEN = datetime(2026, 9, 16, 12, tzinfo=UTC)
DAY = date(2026, 9, 16)


def _rows(tickers: list[str]) -> list[dict]:
    parsed = {"name_ko": "이름", "name_en": "Name", "exchange": "NASDAQ"}
    return [nn.row_for(t, day=DAY, observed_at=SEEN, parsed=parsed) for t in tickers]


def test_colliding_chunk_id_retries_with_a_new_suffix(store) -> None:  # type: ignore[no-untyped-def]
    run_id = nn.run_id_for(DAY, limit=0, tag="")
    assert _flush(store, _rows(["AAPL"]), run_id, 200) == 1
    # 같은 날 두 번째 실행 — 조각 id 가 겹친다. 내용은 다른 행이므로 접미사를 올려 쓴다.
    assert _flush(store, _rows(["MSFT"]), run_id, 200) == 1
    assert store.ingest_run_recorded(nn.NAMES_KO, f"{run_id}-p200")
    assert store.ingest_run_recorded(nn.NAMES_KO, f"{run_id}-p200-r1")
    assert len(store.get(nn.NAMES_KO, as_of=SEEN, market="US")) == 2


def test_flush_gives_up_loudly_after_ten_collisions(store) -> None:  # type: ignore[no-untyped-def]
    run_id = nn.run_id_for(DAY, limit=0, tag="")
    for i in range(10):
        assert _flush(store, _rows([f"T{i}"]), run_id, 200) == 1
    with pytest.raises(DuplicateIngestRun):
        _flush(store, _rows(["T99"]), run_id, 200)


def test_empty_flush_writes_nothing(store) -> None:  # type: ignore[no-untyped-def]
    assert _flush(store, [], nn.run_id_for(DAY, limit=0, tag=""), 200) == 0
