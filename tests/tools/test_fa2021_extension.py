"""2022 하락장 확장 입력(FA2021) — **기본값이 그대로인지**와 새 인자의 규칙만 본다(창고·실캐시를 읽지 않는다).

등록된 시행(final-model-round 'KR+US-20220701-20260630', BE3 패널, vault-early …)은 kit 의 기본값으로 캐시를 찾는다.
기본값이 한 글자라도 바뀌면 그 시행들의 캐시 꼬리표·입력이 바뀌어 재현성이 깨진다 — 이 파일이 그것을 지킨다.
"""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from tools import final_round_kit as kit


def test_registered_defaults_are_unchanged() -> None:
    assert kit.RAW_DIRS == {"KR": Path("data/_diag/kr-long"), "US": Path("data/_diag/w-us")}
    assert kit.SOURCES_CACHE == Path("data/_diag/ranker-sources")
    assert kit.CACHE == Path("data/_diag/final-round")
    sig = inspect.signature(kit.load_full_panel).parameters
    assert sig["window"].default == (date(2022, 7, 1), date(2026, 6, 30))
    assert sig["cache_dir"].default == kit.CACHE
    assert sig["include"].default == kit.BLOCK_ORDER
    assert sig["raw_dirs"].default is None and sig["sources_dirs"].default is None


def test_fa2021_is_a_separate_set_of_paths() -> None:
    fa = kit.FA2021
    assert fa["window"] == (kit.PANEL_FIRST, date(2026, 6, 30))
    assert kit.check_window(fa["window"]) == fa["window"]
    for market, path in fa["raw_dirs"].items():
        assert path != kit.RAW_DIRS[market]
    assert fa["cache_dir"] != kit.CACHE and kit.CACHE not in Path(fa["cache_dir"]).parents
    # 새 조각 디렉터리가 앞, 기존이 뒤 — 기존 조각은 덮지 않고 그대로 읽힌다.
    assert fa["sources_dirs"][-1] == kit.SOURCES_CACHE and fa["sources_dirs"][0] != kit.SOURCES_CACHE
    assert set(fa) == {"window", "raw_dirs", "sources_dirs", "cache_dir"}
    assert set(fa) <= set(inspect.signature(kit.load_full_panel).parameters)


def test_extended_control_tag_differs_from_registered() -> None:
    panel = pd.DataFrame({"market": ["KR", "US"], "session": [date(2021, 11, 10), date(2026, 6, 30)]})
    assert kit.control_tag(panel) == "KR+US-20211110-20260630"
    assert kit.control_tag(panel) != "KR+US-20220701-20260630"


def _part(path: Path, sessions: list[date], value: float) -> None:
    pd.DataFrame({"entity_id": ["A"] * len(sessions), "session": sessions,
                  "turnover_decay": value, "amihud_20": value, "zero_volume_20": value}).to_parquet(path, index=False)


def test_group_block_default_reads_only_sources_cache(tmp_path, monkeypatch) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    _part(old / "G1-KR-202204.parquet", [date(2022, 4, 1)], 1.0)  # invariant-allow: data-access — 합성 작업 조각
    _part(new / "G1-KR-202111.parquet", [date(2021, 11, 10)], 2.0)  # invariant-allow: data-access — 합성 작업 조각
    monkeypatch.setattr(kit, "SOURCES_CACHE", old)
    window = (date(2021, 11, 10), date(2022, 6, 30))
    default = kit._group_block("G1", "KR", window)
    assert default["session"].tolist() == [date(2022, 4, 1)]
    explicit = kit._group_block("G1", "KR", window, (old,))
    pd.testing.assert_frame_equal(default, explicit)
    both = kit._group_block("G1", "KR", window, (new, old))
    assert sorted(both["session"]) == [date(2021, 11, 10), date(2022, 4, 1)]


def test_group_block_first_dir_wins_on_same_month(tmp_path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    _part(a / "G1-KR-202111.parquet", [date(2021, 11, 10)], 2.0)  # invariant-allow: data-access — 합성 작업 조각
    _part(b / "G1-KR-202111.parquet", [date(2021, 11, 10), date(2021, 11, 11)], 9.0)  # invariant-allow: data-access — 합성 작업 조각
    out = kit._group_block("G1", "KR", (date(2021, 11, 10), date(2021, 11, 30)), (a, b))
    assert out["turnover_decay"].tolist() == [np.float32(2.0)]


def test_raw_span_reads_the_given_dirs(tmp_path) -> None:
    pd.DataFrame({"session": [date(2021, 11, 10), date(2022, 3, 31)]}).to_pickle(tmp_path / "calendar-KR.pkl")
    assert kit.raw_span("KR", {"KR": tmp_path}) == (date(2021, 11, 10), date(2022, 3, 31))
    ok, lines = kit.coverage_ready((date(2021, 11, 10), date(2022, 3, 31)), markets=("KR",),
                                   raw_dirs={"KR": tmp_path})
    assert ok and str(tmp_path) in lines[0]
