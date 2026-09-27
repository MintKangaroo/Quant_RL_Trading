"""저장된 doc_type 정정 — **무엇을 건드리지 않는가**를 지킨다.

미장 8-K 5,725건이 `source='dart'` 로 적혀 있다(2026-09-16~19 밤 배치가 SEC 접수번호를 DART 에
물어 '원문 없음' 정정본을 남긴 흔적). source 로 골랐더니 그 행들이 전부 `other` 로 뒤집혔다 —
실제로 미리보기에서 5,725행이 잡혔다. 그래서 접수번호 형식과 시장을 함께 본다.
"""

from __future__ import annotations

import pandas as pd

from tools.reclassify_filings import rows_for, stale


class _Clock:
    def now(self) -> pd.Timestamp:
        return pd.Timestamp("2026-09-27 12:00", tz="UTC")


def _frame(rows: list[dict]) -> pd.DataFrame:
    base = {
        "entity_id": "KR:005930", "valid_from": pd.Timestamp("2026-05-01", tz="UTC"),
        "observed_at": pd.Timestamp("2026-05-01 09:00", tz="UTC"), "revision": 0,
        "source": "dart", "doc_id": "20260501800001", "doc_type": "other",
        "title": "", "filer": "삼성전자", "url": "u", "raw_path": None,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def test_규칙이_바뀐_행만_고른다() -> None:
    frame = _frame([
        {"doc_id": "20260501800001", "title": "매출액또는손익구조30%(대규모법인은15%)이상변동"},
        {"doc_id": "20260501800002", "title": "유상증자결정", "doc_type": "dilution"},
    ])
    changed = stale(frame)
    assert list(changed["doc_id"]) == ["20260501800001"]
    assert list(changed["want"]) == ["pl_change"]


def test_미장_8K_는_source_가_dart_여도_건드리지_않는다() -> None:
    frame = _frame([
        {"entity_id": "US:AAPL", "doc_id": "0001193125-26-117614",
         "title": "8-K 5.03 5.07 9.01", "doc_type": "ownership", "source": "dart"},
        {"entity_id": "US:AAPL", "doc_id": "20260501800009",
         "title": "매출액또는손익구조30%이상변동", "source": "dart"},
    ])
    assert stale(frame).empty          # 접수번호 형식도, 시장도 DART 가 아니다


def test_최신_revision_만_보고_원문_경로를_그대로_옮긴다() -> None:
    frame = _frame([
        {"revision": 0, "title": "매출액또는손익구조30%이상변동", "raw_path": None},
        {"revision": 1, "title": "매출액또는손익구조30%이상변동", "raw_path": "data/_docs/a.txt.gz"},
    ])
    changed = stale(frame)
    assert len(changed) == 1
    rows = rows_for(changed, clock=_Clock())
    assert rows[0]["revision"] == 2                      # 최신 위에 쌓는다
    assert rows[0]["raw_path"] == "data/_docs/a.txt.gz"  # 정정본이 옛 값을 지우면 안 된다
    assert rows[0]["doc_type"] == "pl_change"
