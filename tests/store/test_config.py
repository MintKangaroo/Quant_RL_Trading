"""store.config — 임계치도 이중시간이다 (불변식 10).

임계치를 평범한 설정 파일에 두면 오늘 값을 바꾼 순간 작년 백테스트가
소급해 바뀐다. 그러면 재현이 불가능하고, 재현되지 않는 성적표는 성적표가 아니다.
"""

from __future__ import annotations

import pytest

from lattice.store.errors import ConfigNotFound, SchemaViolation


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


def test_reseeding_identical_defaults_writes_nothing(seeded) -> None:  # type: ignore[no-untyped-def]
    """같은 사실을 두 번 적지 않는다. 멱등이어야 재실행이 안전하다."""
    assert seeded.seed_config_defaults() == 0


def test_new_keys_land_without_an_effective_at(seeded, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """설정을 **추가**하는 것은 발효 시점을 요구하지 않는다.

    새 키에는 덮어쓸 과거가 없으므로 소급 변경 위험이 없다. 여기서 함께
    막으면 설정을 추가할 때마다 기존 창고가 그 키를 영영 모르는 채로 남고,
    그 사실은 몇 주 뒤 ConfigNotFound 로 백필이 죽을 때에야 드러난다.
    """
    from lattice.store import DEFAULT_CONFIG_FILE

    text = DEFAULT_CONFIG_FILE.read_text(encoding="utf-8")
    extended = tmp_path / "with-new-key.toml"
    extended.write_text(text + "\n[m2]\nbrand_new_knob = 7\n", encoding="utf-8")

    assert seeded.seed_config_defaults(extended) == 1
    assert seeded.config("m2.brand_new_knob", as_of=ts(2026, 1, 1)) == 7
    # 새 키는 epoch 로 들어간다 — 아무리 과거로 조회해도 보인다.
    assert seeded.config("m2.brand_new_knob", as_of=ts(2001, 1, 1)) == 7


def test_changing_a_default_without_effective_at_is_refused(seeded, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """발효 시점 없이 덮으면 과거 as_of 조회까지 소급해 바뀐다.

    이 방어가 없으면 자연키·revision·observed_at 이 전부 같은 행이 두 개가 되고,
    승자는 최신값이 아니라 row_hash 가 작은 쪽이 된다 — 편집한 값이 조용히
    무시되거나, 무시되지 않으면 작년 백테스트가 재현되지 않는다.
    """
    edited = _edited(tmp_path, "ic_threshold = 0.03", "ic_threshold = 0.05")

    with pytest.raises(SchemaViolation, match=r"analyst\.ic_threshold"):
        seeded.seed_config_defaults(edited)


def test_changed_default_lands_as_a_restatement(seeded, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    edited = _edited(tmp_path, "ic_threshold = 0.03", "ic_threshold = 0.05")
    change = ts(2026, 6, 1)

    # 바뀐 한 줄만 들어간다. 나머지 설정은 건드리지 않는다.
    assert seeded.seed_config_defaults(edited, effective_at=change) == 1

    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 5, 31)) == 0.03
    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 6, 2)) == 0.05
    # 첫 시딩은 epoch 라 아무리 과거로 조회해도 보인다.
    assert seeded.config("analyst.ic_threshold", as_of=ts(2001, 1, 1)) == 0.03


def test_reverting_a_default_is_allowed(seeded, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """A→B→A 되돌리기는 정상적인 운영 행위다. run id 충돌로 막히면 안 된다."""
    changed = _edited(tmp_path, "ic_threshold = 0.03", "ic_threshold = 0.05")
    seeded.seed_config_defaults(changed, effective_at=ts(2026, 6, 1))

    original = _edited(tmp_path, "ic_threshold = 0.03", "ic_threshold = 0.03")
    assert seeded.seed_config_defaults(original, effective_at=ts(2026, 7, 1)) == 1

    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 6, 15)) == 0.05
    assert seeded.config("analyst.ic_threshold", as_of=ts(2026, 7, 2)) == 0.03


def _edited(tmp_path, old: str, new: str):  # type: ignore[no-untyped-def]
    """체크인된 defaults.toml 의 한 줄만 바꾼 사본."""
    from lattice.store import DEFAULT_CONFIG_FILE

    text = DEFAULT_CONFIG_FILE.read_text(encoding="utf-8")
    assert old in text, f"{old!r} 가 defaults.toml 에 없다"
    target = tmp_path / f"defaults-{abs(hash(new))}.toml"
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


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
