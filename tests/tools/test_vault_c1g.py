"""시행 C1G(얼린 C1 대 C0, 금고 second) — 판정부 배선과 c1fa Analyst. 합성 자료만(금고 창 자료를 읽지 않는다).

지키는 것:
 ① 미고정 문서는 second 창에 안 붙는다(붙으면 second 창 전체가 거부된다) — `--plan` 은 "고정 대기" 로 보여 준다.
 ② 기준 ①~⑤ 는 BE2 등록 숫자 그대로(대 C0) — 각 기준이 혼자서도 기각을 낸다, 지표가 비면 멈춘다.
 ③ 모델 해시는 C1G 문서에서 읽고, C1 GBM 파일 지문까지 대조한다(실제 파일이 있으면 실제로).
 ④ c1fa Analyst 의 시드 평균 원점수 = 판정부 `be2_predictions` 의 C1 군을 시드 평균한 것(같은 함수) · 사이드카 지문이 다르면 안 낸다.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts import be2 as be2_module
from quant_rl_trading.analysts import fa_features
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.schemas.fa import FA_FEATURES, FLAGS
from tools import vault_judge as vj


@pytest.fixture(autouse=True)
def _restore_window():  # type: ignore[no-untyped-def]
    yield
    vj.use_window(vj.windows()["registered"])


# --------------------------------------------------------------------------- ① 등록·창


def test_unpinned_c1g_does_not_attach_to_second() -> None:
    second = vj.windows()["second"]
    assert vj.protocol_path("C1G") == vj.C1G_PROTOCOL and vj.C1G_PROTOCOL.exists()
    assert vj.ENTITY["C1G"] == "c1-gbm-fa-second-2026-10:C1G" and vj.FAMILY["C1G"] == "ranker"
    assert "C1G" in vj.RUNNERS
    if vj.C1G_PROTOCOL_HASH is None:
        assert "C1G" not in vj.trials_of(second)
        assert "C1G" in vj.pending_trials(second)
        assert vj.registration_problems(second) == []          # 기존 second 시행은 그대로 열린다
    else:
        assert "C1G" in vj.trials_of(second)


def test_c1g_attached_with_matching_hash_passes_and_changed_doc_is_refused(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    second = vj.windows()["second"]
    doc = tmp_path / "c1g.md"
    doc.write_text(vj.C1G_PROTOCOL.read_text())
    digest = hashlib.sha256(doc.read_bytes()).hexdigest()[:16]
    monkeypatch.setitem(vj.EXTRA_TRIALS, "second", (*vj.EXTRA_TRIALS["second"], "C1G"))
    monkeypatch.setitem(vj.EXTRA_PROTOCOLS, "C1G", (doc, digest))
    assert not [p for p in vj.extra_problems(second) if "C1G" in p]
    doc.write_text(doc.read_text() + "\n")                      # 고정 뒤 덧붙임 — 해시가 깨진다
    assert any("C1G" in p and "바뀌었다" in p for p in vj.extra_problems(second))
    monkeypatch.setitem(vj.EXTRA_PROTOCOLS, "C1G", (doc, None))  # 미고정 문서가 붙으면 창 전체 거부
    assert any("C1G" in p and "고정되지 않았다" in p for p in vj.extra_problems(second))


def test_plan_lists_c1g_as_pending(capsys) -> None:  # type: ignore[no-untyped-def]
    assert vj.main(["--plan", "--window", "second"]) == 0
    out = capsys.readouterr().out
    if vj.C1G_PROTOCOL_HASH is None:
        assert "고정 대기" in out and "C1G" in out
    assert "--trials" in out


def test_judge_refuses_c1g_while_unpinned() -> None:
    if vj.C1G_PROTOCOL_HASH is not None:
        pytest.skip("이미 고정됐다")
    with pytest.raises(SystemExit):                               # argparse error → 창에서 심사하지 않는 시행
        vj.main(["--judge", "--window", "second", "--trials", "C1G", "--root", "/nonexistent-never-opened"])


def test_record_note_marks_selection_bias() -> None:
    assert "선택 편향" in vj.RECORD_NOTE["C1G"]


# --------------------------------------------------------------------------- ② 기준


def _m(ann: float, *, h1: float | None = None, h2: float | None = None, mdd: float = -0.10, turn: float = 15.0,
       ic: float = 0.05) -> dict[str, float]:
    return {"ann": ann, "h1": ann if h1 is None else h1, "h2": ann if h2 is None else h2, "mdd": mdd, "turn": turn,
            "ic": ic, "beta": 1.0}


def _res(c1: list[float], c0: list[float], **kw) -> dict:  # type: ignore[no-untyped-def]
    return {"C1": [_m(a, **kw.get("t", {})) for a in c1], "C0": [_m(a, **kw.get("b", {})) for a in c0]}


def test_c1g_uses_be2_gate_numbers() -> None:
    assert (vj.BE2_GATE_MEAN, vj.BE2_GATE_SHARE, vj.BE2_GATE_HALF, vj.BE2_GATE_IC, vj.BE2_GATE_MDD, vj.BE2_GATE_TURN) == (
        0.02, 0.80, -0.01, 0.0, 0.02, 1.2)
    body = vj.C1G_PROTOCOL.read_text()
    for text in ("C0 + **2%p**", "**80% 이상(5 중 4)**", "C0 − **1%p**", "≥ **0**", "**2%p** 넘게", "C0 × **1.2**"):
        assert text in body, text


def test_c1g_adopts_on_all_five() -> None:
    lines, verdict = vj.judge_c1g(_res([0.12] * 5, [0.08] * 5))
    assert verdict.startswith("채택 후보") and "재승인" in verdict
    assert all(k in "".join(lines) for k in ("①", "②", "③", "④", "⑤"))


@pytest.mark.parametrize("kw,c1", [
    ({}, [0.09] * 5),                                                     # ① +1%p < +2%p
    ({}, [0.20, 0.20, 0.20, 0.05, 0.05]),                                 # ② 3/5
    ({"t": {"h1": 0.065, "h2": 0.175}}, [0.12] * 5),                     # ③ 전반 −1.5%p
    ({"t": {"ic": 0.045}}, [0.12] * 5),                                  # ④ ΔIC < 0
    ({"t": {"mdd": -0.125}}, [0.12] * 5),                                # ⑤ MDD 2.5%p 깊음
    ({"t": {"turn": 18.5}}, [0.12] * 5),                                 # ⑤ 회전 ×1.23
])
def test_c1g_rejects_on_each_gate(kw, c1) -> None:  # type: ignore[no-untyped-def]
    assert vj.judge_c1g(_res(c1, [0.08] * 5, **kw))[1].startswith("기각")


def test_c1g_early_numbers_would_fail_three_and_five() -> None:
    """금고 early 의 C1·C0 숫자(문서 '선택 편향' 2) — 이 기준이면 ③·⑤ 에서 떨어진다. 기준을 C1 에 맞추지 않았다는 확인."""
    c1 = [_m(a, h1=0.425, h2=0.465, mdd=-0.159, turn=26.5, ic=0.0638) for a in (0.336, 0.440, 0.323, 0.523, 0.604)]
    c0 = [_m(a, h1=0.438, h2=-0.345, mdd=-0.067, turn=20.4, ic=0.0414) for a in (0.062, 0.084, 0.098, -0.017, 0.005)]
    lines, verdict = vj.judge_c1g({"C1": c1, "C0": c0})
    assert verdict.startswith("기각")
    text = "\n".join(lines)
    assert "③두 구간 -1.3%p" in text and "×1.30" in text


def test_c1g_stops_on_missing_metric() -> None:
    res = _res([0.12] * 5, [0.08] * 5)
    del res["C1"][2]["ic"]
    with pytest.raises(ValueError):
        vj.judge_c1g(res)
    res = _res([0.12] * 4, [0.08] * 5)
    with pytest.raises(ValueError):
        vj.judge_c1g(res)


# --------------------------------------------------------------------------- ③ 모델 해시


def test_c1g_document_pins_the_frozen_files() -> None:
    found = vj.c1g_hashes()
    assert found[vj.BE2_STEM] == "e3c347d129cc1c33" and found[vj.C0_STEM] == "2f856986d7e668a8"
    early = vj.frozen_hashes("BE2")                              # 금고 앞당김 문서와 같은 사이드카
    assert found[vj.BE2_STEM] == early[vj.BE2_STEM] and found[vj.C0_STEM] == early[vj.C0_STEM]
    from quant_rl_trading.analysts import c1fa

    assert found[vj.BE2_STEM] == c1fa.SIDECAR_HASH


def test_real_frozen_c1_matches_the_c1g_document() -> None:
    if not (vj.BE2_MODELS / f"{vj.BE2_STEM}.json").exists():
        pytest.skip("얼린 BE2·C0 파일이 없는 환경")
    model, c0 = vj.frozen_c1(vj.c1g_hashes())
    assert sorted(c0) == sorted(model.seeds) == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------- ④ c1fa = 판정 함수


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pytest.importorskip("torch")
    from tools import freeze_be2

    folder = be2_module.model_dir(tmp_path_factory.mktemp("c1root"))
    common = ["--synthetic", "--steps", "2", "--max-epochs", "1", "--threads", "2", "--seeds", "0,1", "--out", str(folder)]
    assert freeze_be2.main(common) == 0
    assert freeze_be2.main([*common, "--arm", "C0"]) == 0
    return folder


def _expected(folder: Path) -> dict[str, str]:
    out = {stem: hashlib.sha256((folder / f"{stem}.json").read_bytes()).hexdigest()[:16] for stem in (vj.BE2_STEM, vj.C0_STEM)}
    for seed in (0, 1):
        name = f"{vj.BE2_STEM}-gbm-seed{seed}.txt"
        out[name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()[:16]
    return out


def _as_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)


def _seed_fa(store, last: date, n: int = 60) -> list[str]:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(5)
    entities = [f"KR:{i:06d}" for i in range(n)]
    first = be2_module.time_axis(last)[-be2_module.WINDOW - 5]
    for d in trading_days(Market.KR, first, last):
        f = pd.DataFrame(rng.normal(size=(n, len(FA_FEATURES))).astype(np.float32), columns=list(FA_FEATURES))
        for flag in FLAGS:
            f[flag] = (rng.random(n) < 0.1).astype(np.float32)
        f["is_us"] = np.float32(0.0)
        f.insert(0, "market", "KR")
        f.insert(0, "session", d)
        f.insert(0, "entity_id", entities)
        fa_features.write_session(store, f, market="KR", session=d, as_of=_as_of(d))
    return entities


def test_frozen_c1_refuses_changed_gbm_file(frozen: Path, tmp_path: Path) -> None:
    expected = _expected(frozen)
    model, _ = vj.frozen_c1(expected, folder=frozen)
    assert list(model.seeds) == [0, 1]
    wrong = dict(expected)
    wrong[f"{vj.BE2_STEM}-gbm-seed1.txt"] = "0" * 16
    with pytest.raises(SystemExit) as err:
        vj.frozen_c1(wrong, folder=frozen)
    assert "C1G 문서" in str(err.value)


def test_c1fa_matches_judge_c1_arm(store, frozen: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from quant_rl_trading.analysts import c1fa
    from quant_rl_trading.replay.clock import ReplayClock

    store.seed_config_defaults()
    day = date(2026, 7, 15)
    _seed_fa(store, day)
    model, c0 = vj.frozen_c1(_expected(frozen), folder=frozen)
    preds = vj.be2_predictions(store, [day], model, c0, as_of_of=_as_of)
    want = pd.concat([pd.Series(be2_module.percentile(p["pred"].to_numpy()), index=p["entity_id"].to_numpy())
                      for p in preds["C1"].values()], axis=1).mean(axis=1)
    root = frozen.parent.parent
    # 사이드카 지문이 고정값과 다르면 안 낸다
    analyst = c1fa.C1FaAnalyst(store, ReplayClock(_as_of(day)), market=Market.KR, models_root=root)
    assert analyst.run(_as_of(day)) == [] and "사이드카 지문" in analyst.skip_reason
    # 합성 사이드카 지문으로 고정하면 판정부 C1 군의 시드 평균과 같다
    monkeypatch.setattr(c1fa, "SIDECAR_HASH", _expected(frozen)[vj.BE2_STEM])
    analyst = c1fa.C1FaAnalyst(store, ReplayClock(_as_of(day)), market=Market.KR, models_root=root)
    signals = analyst.run(_as_of(day))
    assert analyst.skip_reason == "" and len(signals) == len(want)
    np.testing.assert_allclose(analyst._pred.reindex(want.index).to_numpy(), want.to_numpy(), rtol=1e-9)
    assert {s.analyst for s in signals} == {"c1fa"}
    # be2 는 같은 훅에서 BE2 순위 평균을 그대로 낸다(리팩터로 바뀌지 않았다)
    be2_want = pd.concat([p.set_index("entity_id")["pred"] for p in preds["BE2"].values()], axis=1).mean(axis=1)
    be2 = be2_module.Be2Analyst(store, ReplayClock(_as_of(day)), market=Market.KR, models_root=root)
    be2.features(_as_of(day))
    np.testing.assert_allclose(be2._pred.reindex(be2_want.index).to_numpy(), be2_want.to_numpy(), rtol=1e-9)


def test_c1fa_scores_nothing_before_usable_from(store, frozen: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from quant_rl_trading.analysts import c1fa
    from quant_rl_trading.replay.clock import ReplayClock

    monkeypatch.setattr(c1fa, "SIDECAR_HASH", _expected(frozen)[vj.BE2_STEM])
    day = date(2026, 6, 15)
    analyst = c1fa.C1FaAnalyst(store, ReplayClock(_as_of(day)), market=Market.KR, models_root=frozen.parent.parent)
    assert analyst.run(_as_of(day)) == [] and analyst.skip_reason
