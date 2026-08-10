"""store.config — 임계치도 이중시간이다 (불변식 10).

임계치를 평범한 설정 파일에 두면 오늘 값을 바꾼 순간 작년 백테스트가
소급해 바뀐다. 그러면 재현이 불가능하고, 재현되지 않는 성적표는 성적표가 아니다.
"""

from __future__ import annotations

import pytest

from lattice.store.errors import ConfigNotFound, DuplicateIngestRun


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return store


def test_checked_in_defaults_are_readable(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    assert seeded.config("rl.action_reflection_warn", as_of=ts(2026, 1, 1)) == 0.30
    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 1, 1)) == 0.03


def test_unknown_key_is_an_error_not_a_default(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    """없는 임계치에 조용히 기본값을 주면 하드코딩과 같아진다."""
    with pytest.raises(ConfigNotFound):
        seeded.config("rl.does_not_exist", as_of=ts(2026, 1, 1))


def test_reseeding_identical_defaults_is_rejected(seeded) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DuplicateIngestRun):
        seeded.seed_config_defaults()


def test_threshold_change_does_not_rewrite_the_past(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    seeded.append(
        "config",
        [
            {
                "entity_id": "analyst.ic_threshold",
                "valid_from": ts(2026, 6, 1),
                "observed_at": ts(2026, 6, 1),
                "source": "operator",
                "revision": 1,
                "value_json": "0.05",
            }
        ],
        ingest_run_id="raise-ic-bar",
    )

    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 5, 1)) == 0.03
    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 7, 1)) == 0.05


def test_scheduled_change_waits_for_its_effective_date(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    """"다음 달부터 올린다" 를 오늘 기록해도 오늘부터 적용되면 안 된다."""
    seeded.append(
        "config",
        [
            {
                "entity_id": "analyst.ic_threshold",
                "valid_from": ts(2026, 9, 1),
                "observed_at": ts(2026, 8, 1),
                "source": "operator",
                "revision": 1,
                "value_json": "0.07",
            }
        ],
        ingest_run_id="scheduled-ic-bar",
    )

    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 8, 15)) == 0.03
    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 9, 2)) == 0.07
