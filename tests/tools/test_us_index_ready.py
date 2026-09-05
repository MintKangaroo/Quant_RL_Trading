"""**미장 FRED 지수가 따라잡았나** — 브리핑 앞 재시도의 유일한 판정.

2026-08-22 06:30 발송분이 머리말에 ``미장 2026-08-21`` 을 달고 8/20 종가를
실었다. 수집은 rc=0 으로 끝났다 — 받을 것이 아직 없었을 뿐이라 실패로 안
잡힌다. 조용한 실패라 이렇게 밖에서 물어보는 수밖에 없다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tools.us_index_ready import HEADLINE_INDEX, caught_up

#: 2026-08-22 06:00 KST = 08-21 21:00 UTC. 미장 8/21 마감·공표가 끝난 시각이다.
REFRESH = datetime(2026, 8, 21, 21, 0, tzinfo=UTC)


def _seed(store, day: datetime, *, close: float = 7674.37) -> None:  # type: ignore[no-untyped-def]
    # 공표 정책이 store.config 를 읽는다 (불변식 10).
    store.seed_config_defaults()
    store.append(
        "indices",
        [{
            "entity_id": HEADLINE_INDEX, "market": "US",
            "valid_from": day, "observed_at": day,
            "source": "test", "close": close,
        }],
        ingest_run_id=f"idx-{day.date()}",
    )


def test_직전_세션이_있으면_재시도하지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    _seed(store, datetime(2026, 8, 21, tzinfo=UTC))
    ready, line = caught_up(store, as_of=REFRESH)
    assert ready is True
    assert "2026-08-21 까지 들어왔다" in line


def test_하루_밀려_있으면_아직이라고_한다(store) -> None:  # type: ignore[no-untyped-def]
    """**8/22 사고가 정확히 이 모양이다.** 8/20 은 있고 8/21 이 없다."""
    _seed(store, datetime(2026, 8, 20, tzinfo=UTC), close=7641.16)
    ready, line = caught_up(store, as_of=REFRESH)
    assert ready is False
    assert "창고 2026-08-20 · 기대 2026-08-21" in line


def test_창고가_비어도_같은_판정이다(store) -> None:  # type: ignore[no-untyped-def]
    """빈 창고를 "따라잡았다" 로 세면 재시도가 영영 안 돈다."""
    store.seed_config_defaults()
    ready, _ = caught_up(store, as_of=REFRESH)
    assert ready is False


def test_종가_0_은_들어온_것이_아니다(store) -> None:  # type: ignore[no-untyped-def]
    """휴장·미수집 세션이 종가 0 으로 들어온다 (``briefing._quotes`` 와 같은 규칙).

    이걸 세면 "8/21 이 들어왔다" 가 되고 재시도가 안 돈다 — 그리고 브리핑은
    그 0 을 -100% 로 그린다.
    """
    _seed(store, datetime(2026, 8, 20, tzinfo=UTC), close=7641.16)
    _seed(store, datetime(2026, 8, 21, tzinfo=UTC), close=0.0)
    ready, line = caught_up(store, as_of=REFRESH)
    assert ready is False
    assert "창고 2026-08-20" in line
