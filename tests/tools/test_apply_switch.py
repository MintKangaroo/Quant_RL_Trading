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
    (tmp_path / "data" / "_tg_shadow").mkdir()
    (tmp_path / "config" / "shadow" / "tg.config-overrides.yaml").write_text("selector.extra_floor_analyst: ta\n")
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
    assert not (repo / "data" / "_tg_shadow" / "config-overrides.yaml").exists()   # TG 는 실전을 따라간다(FOLLOW_LIVE)
    assert (repo / "config" / "shadow" / "tg.config-overrides.yaml").read_text() == "selector.extra_floor_analyst: ta\n"
    i, m = sw.yaml_line("selector.extra_floor_analyst")
    assert m.group(3).strip() == "# 두 번째 하한"


def test_exclusive_and_unknown() -> None:
    with pytest.raises(SystemExit):
        sw.changes(["TF", "TB"])
    with pytest.raises(SystemExit):
        sw.changes(["TF", "TC"])
    with pytest.raises(SystemExit):
        sw.changes(["TD"])
    with pytest.raises(SystemExit):
        sw.changes(["TF", "TG"])                # 두 번째 하한 자리는 하나
    assert sw.changes(["TG"])["selector.extra_floor_analyst"] == "ta"
    with pytest.raises(SystemExit):
        sw.changes(["N1"])                      # 실전 신호가 없어 아직 적용 경로가 없다


def test_be2_is_kr_only(repo: Path) -> None:
    """BE2 는 국장 공통 키만 바꾸고 미장 키는 {} 로 못 박는다 — 미장이 공통 키로 물러서 be2 없이 동결되지 않게(10/11 점검)."""
    text = (repo / "config" / "quant_rl_trading.yaml").read_text()
    (repo / "config" / "quant_rl_trading.yaml").write_text(text.replace(
        "  weights_override: {}\n", "  weights_override: {}\n  weights_override_us: {}\n"))
    want = sw.changes(["BE2"])
    assert want["selector.weights_override"] == "{be2: 1.0, risk: 1.0}"
    assert want["selector.weights_override_us"] == "{}"
    lines = (repo / "config" / "quant_rl_trading.yaml").read_text().splitlines()
    for k, v in want.items():
        i, m = sw.yaml_line(k)
        lines[i] = f"{m.group(1)}{k.split('.', 1)[1]}: {v}{m.group(3) or ''}"
    import yaml

    after = yaml.safe_load("\n".join(lines))
    assert after["selector"]["weights_override"] == {"be2": 1.0, "risk": 1.0}
    assert after["selector"]["weights_override_us"] == {}


def test_unchanged_keys_are_not_pinned(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """BE2 의 미장 키는 지금 값({})을 못 박을 뿐이라 실험 장부에 고정 줄을 늘리지 않는다(be2 템플릿은 키 하나 그대로)."""
    text = (repo / "config" / "quant_rl_trading.yaml").read_text()
    (repo / "config" / "quant_rl_trading.yaml").write_text(text.replace(
        "  weights_override: {}\n", "  weights_override: {}\n  weights_override_us: {}\n"))
    assert sw.main(["--components", "BE2"]) == 0
    out = capsys.readouterr().out
    assert "selector.weights_override_us" not in out.split("== 실험 shadow 고정")[1]
