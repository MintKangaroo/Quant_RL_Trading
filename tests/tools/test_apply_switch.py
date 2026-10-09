"""10/13 전환 적용기 — 임시 폴더에서만. yaml 줄 교체(주석 보존) · shadow 고정(없으면 만듦) · 배타 묶음 · 장 중 거부."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import apply_switch as sw

YAML_TEXT = """selector:
  n_candidates: 24
  extra_floor_analyst: ''      # 두 번째 하한
  extra_floor_percentile: 0.0
  weights_override: {}
dashboard:
  ir_reset_date: "2026-11-26"
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config" / "shadow").mkdir(parents=True)
    (tmp_path / "config" / "quant_rl_trading.yaml").write_text(YAML_TEXT)
    (tmp_path / "config" / "shadow" / "ix0.config-overrides.yaml").write_text("selector.n_candidates: 200\n")
    (tmp_path / "data" / "_n24_shadow").mkdir(parents=True)
    (tmp_path / "data" / "_w72_shadow").mkdir(parents=True)
    (tmp_path / "data" / "_w72_shadow" / "config-overrides.yaml").write_text("selector.n_candidates: 72\n")
    (tmp_path / "data" / "_shadow").mkdir()
    monkeypatch.setattr(sw, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sw, "YAML", tmp_path / "config" / "quant_rl_trading.yaml")
    monkeypatch.setattr(sw, "DATA", tmp_path / "data")
    return tmp_path


def test_pin_then_switch(repo: Path) -> None:
    want = sw.changes(["TF"])
    assert want["dashboard.ir_reset_date"] == '"2026-10-15"'
    sw.pin_shadows(list(want), apply=True)
    n24 = (repo / "data" / "_n24_shadow" / "config-overrides.yaml").read_text()
    assert "selector.extra_floor_analyst: ''" in n24 and "selector.extra_floor_percentile: 0.0" in n24
    assert "selector.extra_floor_analyst: ''" in (repo / "config" / "shadow" / "ix0.config-overrides.yaml").read_text()
    assert not (repo / "data" / "_shadow" / "config-overrides.yaml").exists()      # 실전의 그림자는 실전을 따른다
    i, m = sw.yaml_line("selector.extra_floor_analyst")
    assert m.group(3).strip() == "# 두 번째 하한"


def test_exclusive_and_unknown() -> None:
    with pytest.raises(SystemExit):
        sw.changes(["TF", "TB"])
    with pytest.raises(SystemExit):
        sw.changes(["TF", "TC"])
    with pytest.raises(SystemExit):
        sw.changes(["TD"])
