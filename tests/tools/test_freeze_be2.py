"""BE2 얼리기 도구 — 합성 자료만(창고·실자료는 안 읽는다).

지키는 것:
 ① 자르는 날 — 첫 사용일을 블록 시작으로 본 퍼지 5 + 엠바고 5(kit `train_end`), 재학습 사슬 = 판정 블록 0·5·10….
 ② 산출물 — 사이드카(버전·학습 끝·usable_from·피처·해시·시드·구조·지문) + 시드마다 파일, 실전 로더가 문제 없이 싣는다.
 ③ 재현 — 같은 시드면 같은 가중치·같은 GBM. 중간에 끊겨도 끝난 시드는 다시 안 돈다.
 ④ 누수 — 트랜스포머·GBM 모두 자르는 날 뒤의 라벨을 안 본다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from quant_rl_trading.analysts import be2 as be2_module
from quant_rl_trading.schemas.fa import FA_FEATURES
from tools import final_round_kit as kit
from tools import freeze_be2

torch = pytest.importorskip("torch")
torch.set_num_threads(2)

ARGS = ["--synthetic", "--steps", "2", "--max-epochs", "1", "--threads", "2"]


def test_plan_cut_is_purge_plus_embargo_before_first_use() -> None:
    _panel, _feats, _groups, sessions = freeze_be2.synthetic_panel()
    the_plan = freeze_be2.plan(kit, sessions, date(2026, 7, 1))
    assert the_plan["cut"] == sessions[len(sessions) - kit.GAP - 1]
    assert the_plan["data_end"] == sessions[-1] == date(2026, 6, 30)
    from tools import trial_final_transformer as be

    blocks = the_plan["blocks"]
    assert [n for n, _ in the_plan["chain"]] == [n for n in range(len(blocks)) if n % be.RETRAIN_EVERY == 0]
    assert all(cut == kit.train_end(sessions, blocks[n][0]) for n, cut in the_plan["chain"])
    with pytest.raises(ValueError):
        freeze_be2.plan(kit, sessions, sessions[-1])                     # 첫 사용일은 패널 뒤여야 한다


def test_plan_flag_trains_nothing(tmp_path: Path) -> None:
    assert freeze_be2.main([*ARGS, "--plan", "--out", str(tmp_path / "m")]) == 0
    assert not (tmp_path / "m").exists()


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("frozen") / "be2"
    assert freeze_be2.main([*ARGS, "--seeds", "0,1", "--out", str(out)]) == 0
    return out


def test_sidecar_and_files(frozen: Path) -> None:
    sidecar = next(frozen.glob("*.json"))
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["version"] == be2_module.VERSION
    assert meta["protocol_hash"] == be2_module.PROTOCOL_HASH
    assert meta["usable_from"] == "2026-07-01" and meta["data_end"] == "2026-06-30"
    assert date.fromisoformat(meta["trained_through"]) < date(2026, 6, 30)
    assert meta["features"] == list(FA_FEATURES)
    assert meta["seeds"] == [0, 1]
    assert meta["arch"] == be2_module.ARCH
    assert meta["training"]["schedule"] == "chain" and meta["training"]["synthetic"] is True
    model = be2_module.Be2Model.load(sidecar)
    assert model.problems() == []
    for seed in (0, 1):
        assert model.transformer[seed].exists() and model.gbm[seed].exists()
        assert meta["sha256"][model.transformer[seed].name] == be2_module.file_digest(model.transformer[seed])
    logs = meta["transformer_log"]["0"]["retrains"]
    assert logs[-1]["at"].startswith("첫 사용일") and logs[-1]["cut"] == meta["trained_through"]


def test_same_seed_same_model_and_resume(frozen: Path, tmp_path: Path) -> None:
    other = tmp_path / "be2"
    assert freeze_be2.main([*ARGS, "--seeds", "0", "--out", str(other)]) == 0
    a = torch.load(next(frozen.glob("*-transformer-seed0.pt")), weights_only=True)
    b = torch.load(next(other.glob("*-transformer-seed0.pt")), weights_only=True)
    assert a.keys() == b.keys() and all(torch.equal(a[k], b[k]) for k in a)
    assert next(frozen.glob("*-gbm-seed0.txt")).read_text() == next(other.glob("*-gbm-seed0.txt")).read_text()
    # 이어 돌기 — 끝난 시드 파일을 건드리지 않는다.
    stamp = next((other / "partial").glob("*-transformer-seed0.pt")).stat().st_mtime_ns
    assert freeze_be2.main([*ARGS, "--seeds", "0", "--out", str(other)]) == 0
    assert next((other / "partial").glob("*-transformer-seed0.pt")).stat().st_mtime_ns == stamp


def test_training_never_sees_labels_after_cut(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import trial_final_transformer as be
    from tools import trial_ranker_kit as rkit

    panel, _f, _g, sessions = freeze_be2.synthetic_panel()
    cut = freeze_be2.plan(kit, sessions, date(2026, 7, 1))["cut"]
    cube_sessions = sorted(panel["session"].unique())
    seen: list[int] = []
    real_train = be.train_model

    def spy_train(*args, **kwargs):  # type: ignore[no-untyped-def]
        model, log = real_train(*args, **kwargs)
        seen.extend(log.seen_days | log.val_days)
        return model, log

    rows: list[int] = []
    real_fit = rkit.fit

    def spy_fit(X, y, **kwargs):  # type: ignore[no-untyped-def]
        rows.append(len(y))
        return real_fit(X, y, **kwargs)

    monkeypatch.setattr(be, "train_model", spy_train)
    monkeypatch.setattr(rkit, "fit", spy_fit)
    assert freeze_be2.main([*ARGS, "--seeds", "0", "--out", str(tmp_path / "be2")]) == 0
    assert seen and max(cube_sessions[i] for i in seen) <= cut
    expected = int(((panel["session"] <= cut) & panel["y5"].notna()).sum())
    assert rows == [expected]
    assert np.isfinite(expected)
