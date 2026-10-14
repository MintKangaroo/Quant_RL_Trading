"""백필 경계 가짜 confidence 정정 (tools/fix_backfill_confidence.py, data-contract.md "백필 경계 confidence").

증명하는 것:
(a) 저장 0 과 '증거 없음 재시작' 뒤 창을 의심하고, 재계산과 다른 것만 정정 대상으로 고른다
(b) 정정본은 같은 자연키·revision+1·observed_at 원래 그대로·confidence 만 바뀐다
(c) 적재 뒤 **그 세션 as_of 로** 읽으면 정정값이 보인다(지금 시각으로 찍으면 안 보인다 — 고치는 목적)
(d) 두 번째 계획은 비어 있다(멱등)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from quant_rl_trading.store import Store
from tools import fix_backfill_confidence as fix

DAYS = [datetime(2024, 2, 1, 7, 0, tzinfo=UTC) + timedelta(days=i) for i in range(10)]
ENTITIES = ["KR:000100", "KR:000200", "KR:000300"]
#: 저장 confidence — 앞 3세션은 앞 조각(정상), 4번째에서 1.0 재시작(뒤 조각을 먼저 넣었다), 6·7번째 가짜 0.
STORED = [0.09, 0.08, 0.085, 1.0, 1.0, 0.0, 0.0, 0.05, 0.07, 0.08]
TRUE = [0.09, 0.08, 0.085, 0.09, 0.10, 0.11, 0.10, 0.09, 0.07, 0.08]


def _seed(store: Store) -> None:
    for i, (day, conf) in enumerate(zip(DAYS, STORED, strict=True)):
        store.append("signals", [{
            "entity_id": entity, "valid_from": day, "observed_at": day, "source": "daily", "analyst": "ranker",
            "analyst_version": "ranker-v1", "score": 0.1 * k + i, "confidence": conf, "horizon_days": 5,
            "features_hash": "", "evidence_json": "[]", "latency_ms": 0.0,
        } for k, entity in enumerate(ENTITIES)], ingest_run_id=f"seed-{i}")


def _recompute(analyst: str, as_of: datetime) -> float:
    return TRUE[DAYS.index(as_of)]


NOW = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)


def test_suspects_재시작_뒤_창과_0() -> None:
    import pandas as pd

    stored = pd.DataFrame({"session": [d.date() for d in DAYS], "confidence": STORED})
    positions, restarts = fix.suspects(stored, check_all=False)
    assert restarts == [DAYS[3].date()]
    assert positions == list(range(3, 10))     # 재시작부터 창 끝까지(여기선 범위 끝)
    assert fix.suspects(stored, check_all=True)[0] == list(range(10))


def test_계획과_정정본(store: Store) -> None:
    _seed(store)
    result = fix.plan(store, analysts=["ranker"], market="KR", start=date(2024, 1, 1), end=date(2024, 3, 1), now=NOW,
                      recompute=_recompute)
    picked = {(c.session, c.reason) for c in result.corrections}
    # 3·4 = 증거 없음 재시작(1.0 → 0.09·0.10), 5·6 = 저장 0, 7 = 재시작 창 안 0.05 → 0.09. 8·9 는 같아서 안 고친다.
    assert picked == {(DAYS[3].date(), "증거 없음 재시작"), (DAYS[4].date(), "증거 없음 재시작"),
                      (DAYS[5].date(), "저장 0"), (DAYS[6].date(), "저장 0"), (DAYS[7].date(), "재시작 창")}

    item = next(c for c in result.corrections if c.session == DAYS[5].date())
    rows = fix.correction_rows(store, item, market="KR", now=NOW)
    assert len(rows) == len(ENTITIES)
    assert all(r["revision"] == 1 and r["confidence"] == TRUE[5] for r in rows)
    assert all(r["observed_at"] == DAYS[5] and r["valid_from"] == DAYS[5] for r in rows)
    assert sorted(r["score"] for r in rows) == [5.0, 5.1, 5.2]
    store.append("signals", rows, ingest_run_id=fix.run_id(item, "KR"))

    seen = store.get("signals", as_of=DAYS[5], lookback=1)
    seen = seen[seen["valid_from"] == DAYS[5]]
    assert set(seen["confidence"]) == {TRUE[5]}, "그 세션 as_of 로 정정값이 보여야 한다"
    assert set(seen["revision"]) == {1}


def test_멱등(store: Store) -> None:
    _seed(store)
    first = fix.plan(store, analysts=["ranker"], market="KR", start=date(2024, 1, 1), end=date(2024, 3, 1), now=NOW,
                     recompute=_recompute)
    for item in first.corrections:
        store.append("signals", fix.correction_rows(store, item, market="KR", now=NOW), ingest_run_id=fix.run_id(item, "KR"))
    again = fix.plan(store, analysts=["ranker"], market="KR", start=date(2024, 1, 1), end=date(2024, 3, 1), now=NOW,
                     recompute=_recompute)
    assert again.corrections == []
