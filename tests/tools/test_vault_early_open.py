"""금고 앞당김 개봉(docs/protocols/vault-early-open-2026-10.md) — 판정부의 잠금·창·BE2 경로.

금고 창 자료는 **전혀 읽지 않는다.** 합성 얼린 모델·합성 FA·임시 창고만 쓴다. 지키는 것:

(a) 해시 잠금 — 앞당김 문서가 초안(EARLY_PROTOCOL_HASH = None)이면 날짜가 지나도 --bake·--judge 가 창고를 열기 전에 멈춘다.
    문서가 고정 뒤 바뀌었거나, 문서가 고정한 시행 문서 해시가 달라져도 멈춘다.
(b) 날짜 잠금 — 굽기는 굽기 가능일부터, 판정은 판정 가능일(라벨이 닫힌 뒤)부터.
(c) 창 경계 — early = 7/1~9/30, second 는 바로 다음 날부터, 둘이 겹치지 않고, 판정 가능일이 h5 라벨이 닫힌 뒤다.
(d) BE2 경로 — 얼린 사이드카 해시·파일 지문을 대조하고, 어긋나면 거부. 실전 be2 Analyst 와 같은 함수로 채점하고,
    한 세션이라도 비면 창을 줄이지 않고 멈춘다.
(e) 기준 ①~⑥ 과 두 번째 금고 확인 · 기록(해시·동시 개봉 수) · 이미 연 창과 겹치면 거부.
(f) 금고 창 be2 신호(score_be2 --signals-from)는 등록 전에 거부.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, timedelta
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


def _fix_hash(monkeypatch: pytest.MonkeyPatch) -> str:
    """해시를 **지금 문서 그대로** 고정한 것처럼 — 고정 뒤의 동작을 본다."""
    digest = hashlib.sha256(vj.EARLY_PROTOCOL.read_bytes()).hexdigest()[:16]
    monkeypatch.setattr(vj, "EARLY_PROTOCOL_HASH", digest)
    return digest


# --------------------------------------------------------------------------- (c) 창 경계


def test_early_window_is_declared_by_the_draft() -> None:
    win = vj.windows()["early"]
    assert (win.start, win.end) == (date(2026, 7, 1), date(2026, 9, 30))
    assert win.protocol == vj.EARLY_PROTOCOL
    assert set(win.trials) == {"AQ", "AR", "AS", "BD", "BE2"}
    assert win.bake_from >= date(2026, 10, 1)                     # 9/30 세션 자료가 들어온 뒤
    assert len(trading_days(Market.KR, win.start, win.end)) == 62
    assert win.cache != vj.windows()["registered"].cache          # 원래 창 캐시와 섞이지 않는다


def test_judge_opens_only_after_h5_labels_close() -> None:
    """창 끝 세션의 h5 라벨(진입 t+1, 보유 5) = 그 뒤 6번째 거래일 종가. 판정 가능일은 그보다 뒤여야 한다(두 시장 다)."""
    win = vj.windows()["early"]
    for market in (Market.KR, Market.US):
        after = list(trading_days(market, win.end + timedelta(days=1), win.end + timedelta(days=30)))
        assert win.judge_from > after[5], market


def test_second_window_follows_without_overlap() -> None:
    wins = vj.windows()
    early, second = wins["early"], wins["second"]
    assert second.start == early.end + timedelta(days=1)
    assert second.end == vj.REGISTERED_END                         # 원래 창의 나머지
    assert second.judge_from >= vj.REGISTERED_OPEN
    assert set(second.trials) == {"BE2", "BD"}


def test_use_window_rebinds_the_module_window() -> None:
    vj.use_window(vj.windows()["early"])
    assert (date(2026, 7, 1), date(2026, 9, 30)) == (vj.VAULT_START, vj.VAULT_END)
    assert vj.US_WORK.parent == vj.VAULT and vj.RAW_DIRS["KR"].parent == vj.VAULT
    vj.use_window(vj.windows()["registered"])
    assert date(2026, 11, 13) == vj.VAULT_END


def test_unknown_window_is_an_argument_error() -> None:
    with pytest.raises(SystemExit):
        vj.main(["--judge", "--window", "someday"])


def test_trial_outside_the_window_is_refused() -> None:
    with pytest.raises(SystemExit):
        vj.main(["--judge", "--window", "second", "--trials", "AQ"])


# --------------------------------------------------------------------------- (a) 해시 잠금 · (b) 날짜


def test_draft_is_refused_even_after_the_date(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """지금 문서는 초안이다 — 날짜가 한참 지나도 창고를 열기 전에 멈춘다(금고 창 자료를 안 읽는다)."""
    assert vj.EARLY_PROTOCOL_HASH is None, "해시가 고정됐으면 이 테스트의 전제를 다시 본다"
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 12, 31))
    monkeypatch.setattr(vj, "Store", lambda **_: pytest.fail("등록 전에 창고를 열었다"))
    for argv in (["--judge", "--window", "early"], ["--bake", "--window", "early"],
                 ["--judge", "--window", "early", "--trials", "BE2", "--save"], ["--judge", "--window", "second"]):
        assert vj.main(argv) == 2
    assert "초안" in capsys.readouterr().out


def test_default_window_is_early_and_locked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 12, 31))
    monkeypatch.setattr(vj, "Store", lambda **_: pytest.fail("등록 전에 창고를 열었다"))
    assert vj.main(["--judge"]) == 2


def test_plan_reads_nothing_and_says_draft(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vj, "Store", lambda **_: pytest.fail("--plan 이 창고를 열었다"))
    assert vj.main(["--plan", "--window", "early"]) == 0
    out = capsys.readouterr().out
    assert "초안" in out and "freeze_be2.py --arm C0" in out and "--window early" in out


def test_fixed_hash_then_dates_gate_bake_and_judge(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _fix_hash(monkeypatch)
    win = vj.windows()["early"]
    assert vj.registration_problems(win) == []
    monkeypatch.setattr(vj, "_today", lambda: win.bake_from - timedelta(days=1))
    assert vj.locked("--bake", win) and vj.locked("--judge", win)
    monkeypatch.setattr(vj, "_today", lambda: win.bake_from)
    assert not vj.locked("--bake", win) and vj.locked("--judge", win)      # 굽기만 먼저 풀린다
    monkeypatch.setattr(vj, "_today", lambda: win.judge_from)
    assert not vj.locked("--judge", win)


def test_changed_document_after_fixing_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(vj, "EARLY_PROTOCOL_HASH", "0123456789abcdef")
    problems = vj.registration_problems(vj.windows()["early"])
    assert problems and "바뀌었다" in problems[0]


def test_changed_trial_document_is_refused(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """앞당김 문서가 고정한 시행 문서 해시와 지금 파일이 다르면 거부 — 창만 바꾼다는 약속의 방어선."""
    _fix_hash(monkeypatch)
    edited = tmp_path / "breadth72.md"
    edited.write_text(vj.PROTOCOL_OF["AQ"].read_text() + "\n사후 수정\n")
    monkeypatch.setitem(vj.PROTOCOL_OF, "AQ", edited)
    problems = vj.registration_problems(vj.windows()["early"])
    assert any("시행 문서가 바뀌었다" in p for p in problems)


def test_pinned_trial_hashes_match_current_documents() -> None:
    """초안이 적은 doc:… 해시가 지금 시행 문서와 같다 — 고정하면 곧바로 통과해야 한다."""
    pinned = vj.pinned_hashes(vj.EARLY_PROTOCOL)
    for trial, path in vj.PROTOCOL_OF.items():
        assert pinned[f"doc:{trial}"] == hashlib.sha256(path.read_bytes()).hexdigest()[:16], trial
    assert pinned["doc:BE2"] == be2_module.PROTOCOL_HASH


def test_registered_window_is_unaffected_by_the_draft(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    reg = vj.windows()["registered"]
    assert vj.registration_problems(reg) == []
    monkeypatch.setattr(vj, "_today", lambda: date(2026, 11, 23))
    assert vj.locked("--judge", reg) is False


def test_be2_model_hashes_come_from_the_early_document() -> None:
    hashes = vj.frozen_hashes("BE2")
    assert vj.BE2_STEM in hashes and len(hashes[vj.BE2_STEM]) == 16
    assert not any(k.startswith("doc:") for k in hashes)
    # C0 은 아직 안 얼렸다 — 초안에는 해시가 없고, 그래서 BE2 판정이 거부된다(아래 frozen_be2 테스트)
    assert vj.C0_STEM not in hashes


# --------------------------------------------------------------------------- (f) 금고 창 be2 신호


def test_score_be2_refuses_vault_signals_before_registration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tools import score_be2

    assert score_be2.vault_signals_refusal(None) == ""
    assert score_be2.vault_signals_refusal(date(2026, 10, 2)) == ""          # 금고 창 뒤(shadow 매일)
    assert "등록 해시 고정 뒤" in score_be2.vault_signals_refusal(date(2026, 7, 1))
    assert score_be2.main(["--start", "2026-07-01", "--end", "2026-07-02", "--signals-from", "2026-07-01",
                           "--root", "/nonexistent-never-opened"]) == score_be2.RC_BAD_ARGS
    _fix_hash(monkeypatch)
    assert score_be2.vault_signals_refusal(date(2026, 7, 1)) == ""


# --------------------------------------------------------------------------- (d) BE2 얼린 모델


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """합성 얼리기(BE2 시드 둘 + C0 같은 시드) — 같은 폴더. 줄기 이름이 실제와 같다(합성 패널 끝 2026-06-30)."""
    pytest.importorskip("torch")
    from tools import freeze_be2

    folder = be2_module.model_dir(tmp_path_factory.mktemp("be2root"))     # <root>/models/be2 — Analyst 가 찾는 자리
    common = ["--synthetic", "--steps", "2", "--max-epochs", "1", "--threads", "2", "--seeds", "0,1", "--out", str(folder)]
    assert freeze_be2.main(common) == 0
    assert freeze_be2.main([*common, "--arm", "C0"]) == 0
    assert (folder / f"{vj.BE2_STEM}.json").exists() and (folder / f"{vj.C0_STEM}.json").exists()
    return folder


def _expected(folder: Path) -> dict[str, str]:
    return {stem: hashlib.sha256((folder / f"{stem}.json").read_bytes()).hexdigest()[:16]
            for stem in (vj.BE2_STEM, vj.C0_STEM)}


def test_c0_freeze_uses_six_scores_and_same_cut(frozen: Path) -> None:
    meta = json.loads((frozen / f"{vj.C0_STEM}.json").read_text())
    be2_meta = json.loads((frozen / f"{vj.BE2_STEM}.json").read_text())
    assert tuple(meta["features"]) == vj.C0_FEATURES
    assert meta["trained_through"] == be2_meta["trained_through"]
    assert meta["seeds"] == be2_meta["seeds"]
    assert not any(p.name.startswith("be2-") and "c0" in p.name for p in frozen.iterdir())


def test_frozen_be2_loads_when_hashes_match(frozen: Path) -> None:
    model, c0 = vj.frozen_be2(_expected(frozen), folder=frozen)
    assert list(model.seeds) == [0, 1] and set(c0) == {0, 1}
    assert c0[0].num_feature() == len(vj.C0_FEATURES)


def test_frozen_be2_refuses_changed_sidecar(frozen: Path, tmp_path: Path) -> None:
    copy = tmp_path / "m"
    shutil.copytree(frozen, copy)
    expected = _expected(copy)
    sidecar = copy / f"{vj.BE2_STEM}.json"
    sidecar.write_text(sidecar.read_text() + "\n")
    with pytest.raises(SystemExit) as err:
        vj.frozen_be2(expected, folder=copy)
    assert "사이드카 해시 불일치" in str(err.value)


def test_frozen_be2_refuses_changed_model_file(frozen: Path, tmp_path: Path) -> None:
    """사이드카는 그대로인데 파일이 바뀌었다 — 파일 지문을 다시 재서 거부(BE2 GBM · C0 GBM 둘 다)."""
    for victim in (f"{vj.BE2_STEM}-gbm-seed1.txt", f"{vj.C0_STEM}-gbm-seed0.txt"):
        copy = tmp_path / victim
        shutil.copytree(frozen, copy)
        (copy / victim).write_text((copy / victim).read_text() + "\n")
        with pytest.raises(SystemExit) as err:
            vj.frozen_be2(_expected(copy), folder=copy)
        assert "지문" in str(err.value)


def test_frozen_be2_refuses_without_registered_c0_hash(frozen: Path) -> None:
    expected = _expected(frozen)
    del expected[vj.C0_STEM]
    with pytest.raises(SystemExit) as err:
        vj.frozen_be2(expected, folder=frozen)
    assert "--arm C0" in str(err.value)


# --------------------------------------------------------------------------- (d) BE2 채점 = 실전 함수


def _fa_frame(rng: np.random.Generator, entities: list[str], session: date) -> pd.DataFrame:
    f = pd.DataFrame(rng.normal(size=(len(entities), len(FA_FEATURES))).astype(np.float32), columns=list(FA_FEATURES))
    for flag in FLAGS:
        f[flag] = (rng.random(len(entities)) < 0.1).astype(np.float32)
    f["is_us"] = np.float32(0.0)
    f.insert(0, "market", "KR")
    f.insert(0, "session", session)
    f.insert(0, "entity_id", entities)
    return f


def _as_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)      # 16:00 KST


def _seed_fa(store, last: date, *, skip: date | None = None, n: int = 60) -> list[str]:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(3)
    entities = [f"KR:{i:06d}" for i in range(n)]
    first = be2_module.time_axis(last)[-be2_module.WINDOW - 5]
    for d in trading_days(Market.KR, first, last):
        if d != skip:
            fa_features.write_session(store, _fa_frame(rng, entities, d), market="KR", session=d, as_of=_as_of(d))
    return entities


def test_be2_predictions_use_live_functions(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    """두 세션 · 시드 둘 → 군 셋의 시드별 예측. BE2 는 백분위 평균(0~1), 시드 평균은 be2 Analyst 의 순위와 같다."""
    store.seed_config_defaults()
    days = list(trading_days(Market.KR, date(2026, 7, 14), date(2026, 7, 15)))
    entities = _seed_fa(store, days[-1])
    model, c0 = vj.frozen_be2(_expected(frozen), folder=frozen)
    preds = vj.be2_predictions(store, days, model, c0, as_of_of=_as_of)
    assert set(preds) == {"BE2", "C1", "C0"} and set(preds["BE2"]) == {0, 1}
    for arm in preds.values():
        for frame in arm.values():
            assert len(frame) == len(entities) * len(days) and frame["pred"].notna().all()
    assert preds["BE2"][0]["pred"].between(0, 1).all()
    # 실전 Analyst 의 시드 평균 원점수(EMA 전)와 같은 순서인가 — 같은 함수를 부르므로 같아야 한다.
    from quant_rl_trading.analysts.be2 import Be2Analyst
    from quant_rl_trading.replay.clock import ReplayClock

    analyst = Be2Analyst(store, ReplayClock(_as_of(days[-1])), market=Market.KR, models_root=frozen.parent.parent)
    analyst.features(_as_of(days[-1]))
    assert analyst.skip_reason == ""
    last = [p[p["session"] == days[-1]].set_index("entity_id")["pred"] for p in preds["BE2"].values()]
    ours = pd.concat(last, axis=1).mean(axis=1).reindex(analyst._pred.index)
    np.testing.assert_allclose(ours.to_numpy(), analyst._pred.to_numpy(), rtol=1e-6)


def test_be2_predictions_refuse_a_hole(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    """창 안 국장 세션 하나가 비면 멈춘다 — 판정 창을 조용히 줄이지 않는다."""
    store.seed_config_defaults()
    day = date(2026, 7, 15)
    hole = trading_days(Market.KR, day - timedelta(days=30), day)[2]
    _seed_fa(store, day, skip=hole)
    model, c0 = vj.frozen_be2(_expected(frozen), folder=frozen)
    with pytest.raises(SystemExit) as err:
        vj.be2_predictions(store, [day], model, c0, as_of_of=_as_of)
    assert "조용히 줄이지 않는다" in str(err.value)


# --------------------------------------------------------------------------- (e) 기준 ①~⑥


def _m(ann: float, *, h1: float | None = None, h2: float | None = None, mdd: float = -0.10, turn: float = 15.0,
       ic: float = 0.05) -> dict[str, float]:
    return {"ann": ann, "h1": ann if h1 is None else h1, "h2": ann if h2 is None else h2, "mdd": mdd, "turn": turn,
            "ic": ic, "beta": 1.0}


def _res(treat: list[float], base: list[float], fa: list[float], **kw) -> dict:  # type: ignore[no-untyped-def]
    return {"BE2": [_m(a, **kw.get("be", {})) for a in treat], "C0": [_m(a, **kw.get("c0", {})) for a in base],
            "C1": [_m(a) for a in fa]}


def test_be2_gates_match_the_round() -> None:
    from tools import final_round_kit as kit

    assert (vj.BE2_GATE_MEAN, vj.BE2_GATE_SHARE, vj.BE2_GATE_HALF) == (kit.GATE_MEAN, kit.GATE_SHARE, kit.GATE_REGIME)
    assert (vj.BE2_GATE_IC, vj.BE2_GATE_MDD, vj.BE2_GATE_TURN, vj.BE2_GATE_MODEL) == (
        kit.GATE_IC, kit.GATE_MDD, kit.GATE_TURN, kit.GATE_MODEL)


def test_be2_adopts_on_all_six() -> None:
    lines, verdict = vj.judge_be2(_res([0.12] * 5, [0.08] * 5, [0.10] * 5))
    assert verdict.startswith("채택")
    assert all(k in "".join(lines) for k in ("①", "②", "③", "④", "⑤", "⑥"))


def test_be2_information_effect_when_only_six_fails() -> None:
    verdict = vj.judge_be2(_res([0.12] * 5, [0.08] * 5, [0.115] * 5))[1]
    assert verdict.startswith("①~⑤ 통과") and "C1" in verdict


def test_be2_rejects_when_three_of_five_seeds() -> None:
    assert vj.judge_be2(_res([0.20, 0.20, 0.20, 0.05, 0.05], [0.08] * 5, [0.0] * 5))[1].startswith("기각")


def test_be2_rejects_when_one_half_collapses() -> None:
    res = _res([0.12] * 5, [0.08] * 5, [0.0] * 5, be={"h1": 0.00, "h2": 0.24})
    assert vj.judge_be2(res)[1].startswith("기각")


def test_be2_rejects_on_ic_loss_or_turnover() -> None:
    assert vj.judge_be2(_res([0.12] * 5, [0.08] * 5, [0.0] * 5, be={"ic": 0.04}))[1].startswith("기각")
    assert vj.judge_be2(_res([0.12] * 5, [0.08] * 5, [0.0] * 5, be={"turn": 19.0}))[1].startswith("기각")


def test_be2_missing_metric_raises_not_rejects() -> None:
    res = _res([0.12] * 5, [0.08] * 5, [0.0] * 5)
    res["C0"][2]["ic"] = float("nan")
    with pytest.raises(ValueError):
        vj.judge_be2(res)


def test_second_vault_confirmation_has_no_margin() -> None:
    assert vj.judge_be2(_res([0.081] * 5, [0.08] * 5, [0.2] * 5), confirm=True)[1].startswith("확인 —")
    assert vj.judge_be2(_res([0.079] * 5, [0.08] * 5, [0.0] * 5), confirm=True)[1].startswith("확인 실패")


# --------------------------------------------------------------------------- (e) 기록 · 한 번만


def _results() -> list[tuple[str, str, list[str]]]:
    return [("AQ", "기각 — 합성", ["AQ 줄"]), ("BD", "보류 — bear 세션 3 < 10", ["BD 줄"]),
            ("BE2", "채택 — 합성", ["BE2 줄"])]


def test_early_record_carries_early_hash_and_batch_size(store) -> None:  # type: ignore[no-untyped-def]
    win = vj.windows()["early"]
    vj.use_window(win)
    assert vj.record_verdicts(store, _results(), save=True, win=win) == 3
    now = datetime.now(UTC)
    rows = store.get("research_trials", as_of=now, lookback=5)
    early_hash = hashlib.sha256(vj.EARLY_PROTOCOL.read_bytes()).hexdigest()[:16]
    assert set(rows["protocol_hash"]) == {early_hash}
    be2 = rows[rows["entity_id"] == vj.ENTITY["BE2"]].iloc[0]
    assert "동시 개봉 3시행" in be2["detail"] and "final-model-round-2026-10.md 34abffde1d5e6bed" in be2["detail"]
    opened = store.get("holdout_access", as_of=now, lookback=5).iloc[0]
    assert (opened["window_start"], opened["window_end"]) == ("2026-07-01", "2026-09-30")
    assert opened["entity_id"] == "promotion-review-early-2026-09"
    assert "채택 1" in opened["detail"]


def test_opened_window_blocks_overlapping_windows(store) -> None:  # type: ignore[no-untyped-def]
    wins = vj.windows()
    vj.use_window(wins["early"])
    vj.record_verdicts(store, _results(), save=True, win=wins["early"])
    assert vj.consumed(store, wins["registered"])          # 7/1~11/13 은 이제 겹친다 → 판정 거부
    assert vj.consumed(store, wins["early"])
    assert vj.consumed(store, wins["second"]) == []        # 10/1~ 은 새 구간


def test_second_vault_reads_prior_verdicts(store) -> None:  # type: ignore[no-untyped-def]
    wins = vj.windows()
    assert vj.prior_verdict(store, "BE2") is None
    vj.use_window(wins["early"])
    vj.record_verdicts(store, _results(), save=True, win=wins["early"])
    need = vj.SECOND_NEEDS["second"]
    assert vj.prior_verdict(store, "BE2").startswith(need["BE2"])       # 채택 → 확인 대상
    assert vj.prior_verdict(store, "BD").startswith(need["BD"])         # 보류 → 한 번 더
    assert not vj.prior_verdict(store, "AQ").startswith(("채택", "①~⑤", "보류"))
