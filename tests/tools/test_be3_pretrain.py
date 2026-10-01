"""시행 BE3(BE1 + 자기지도 사전학습) 단위 테스트 — 합성 자료만. 창고·실자료는 건드리지 않는다.

지키는 것:
 ① 컷오프 누수 차단 — 로더·사전학습·체크포인트 어디서든 컷오프 뒤 행을 하나라도 보면 멈춘다. 라벨을 안 본다.
 ② 사전학습 전후 구조 동일 — 넘기는 가중의 키·모양이 BE1 `make_model` 과 같고, 머리(`out`)는 그 시드의 BE1 초기화다.
 ③ 사전학습 없음 = BE1 경로 — `run_seed(init_state=None)` 과 BE1 기본 호출이 비트 동일, 자기 초기화를 넣어도 같다.
 ④ 가림 복원 — 결측 표지 존중, 창 전체 가림, 채움률 관문.
 ⑤ 기준 판정 경계 — ⑦(+1%p · 80%)·⑧(0 초과)·최종 판정 조합.
 ⑥ 등록 관문·러너·가드.
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tools import final_round_kit as kit
from tools import trial_be3_pretrain as b3
from tools import trial_final_transformer as be

torch = pytest.importorskip("torch")
torch.set_num_threads(2)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fa():
    panel, feats, groups, sessions = b3.synthetic_fa(n_sessions=200, n_entities=120, seed=3)
    return dict(panel=panel, feats=feats, groups=groups, sessions=sessions)


def _pre(fa, cutoff):
    return fa["panel"][fa["panel"]["session"] <= cutoff].drop(columns=["y5"])


# ① 누수 차단 ----------------------------------------------------------------------

def test_cutoff_is_the_first_judged_blocks_train_end(fa):
    sessions = fa["sessions"]
    cutoff = b3.pretrain_cutoff(sessions)
    first = kit.blocks(sessions)[0][0]
    assert cutoff == kit.train_end(sessions, first)
    # 컷오프와 첫 채점 세션 사이에 퍼지+엠바고(10) 세션이 비어 있다
    between = [s for s in sessions if cutoff < s < sessions[first]]
    assert len(between) == kit.GAP


def test_loader_asks_for_window_ending_at_cutoff_and_drops_labels(fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    seen = {}

    def loader(markets, window, **kw):
        seen["window"] = window
        part = fa["panel"][fa["panel"]["session"] <= window[1]]
        return part, fa["feats"], fa["groups"], sorted(part["session"].unique())

    panel, _feats, _groups = b3.load_pretrain_panel(cutoff, loader=loader, start=fa["sessions"][0])
    assert seen["window"] == (fa["sessions"][0], cutoff)
    assert "y5" not in panel.columns
    assert max(panel["session"]) <= cutoff


def test_loader_refuses_a_single_row_after_cutoff(fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    after = fa["sessions"][fa["sessions"].index(cutoff) + 1]

    def leaky(markets, window, **kw):
        part = fa["panel"][fa["panel"]["session"] <= cutoff]
        one = fa["panel"][fa["panel"]["session"] == after].head(1)       # 단 한 행
        return pd.concat([part, one]), fa["feats"], fa["groups"], []

    with pytest.raises(b3.PretrainLeak):
        b3.load_pretrain_panel(cutoff, loader=leaky, start=fa["sessions"][0])


def test_enforce_cutoff_refuses_empty():
    with pytest.raises(b3.PretrainLeak):
        b3.enforce_cutoff(pd.DataFrame({"session": []}), date(2023, 1, 31))


def test_pretrain_refuses_a_cube_that_reaches_past_cutoff(fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    whole = fa["panel"].drop(columns=["y5"])           # 컷오프 뒤 행이 섞인 패널
    with pytest.raises(b3.PretrainLeak):
        b3.run_pretrain(whole, fa["feats"], fa["groups"], cutoff, epochs=1, steps_per_epoch=2)
    c = b3.make_cube(whole, fa["feats"])
    with pytest.raises(b3.PretrainLeak):
        b3.pretrain(c.cube, c.observed, c.market_id, c.sessions, cutoff, {}, {}, {}, None, epochs=1)


def test_pretrain_never_reads_past_cutoff_and_uses_full_windows(fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    _state, log, c, _fill, _ch = b3.run_pretrain(_pre(fa, cutoff), fa["feats"], fa["groups"], cutoff,
                                                epochs=2, steps_per_epoch=6)
    assert log.seen and max(log.seen) <= cutoff
    assert log.anchors and log.val_anchors
    assert not (log.anchors & log.val_anchors), "조기 종료 검증 앵커로 학습하지 않는다"
    first_ok = c.sessions[be.WINDOW - 1 + b3.CONTRAST_SHIFT]
    assert min(log.anchors | log.val_anchors) >= first_ok, "앞이 0 으로 채워진 창으로 배우지 않는다"
    assert "y5" not in c.columns


def test_pretrain_has_no_label_argument():
    import inspect
    params = set(inspect.signature(b3.pretrain).parameters)
    assert not params & {"targets", "y", "labels"}


def test_checkpoint_round_trip_and_mismatch_refusals(tmp_path, fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    state, log, c, fill, ch = b3.run_pretrain(_pre(fa, cutoff), fa["feats"], fa["groups"], cutoff,
                                              epochs=1, steps_per_epoch=3)
    path = tmp_path / b3.CKPT_NAME
    b3.save_checkpoint(path, state, columns=c.columns, cutoff=cutoff, log=log, channels=ch, fill=fill,
                       protocol_hash="t")
    ck = b3.load_checkpoint(path, columns=c.columns, cutoff=cutoff)
    assert ck["cutoff"] == cutoff.isoformat()
    with pytest.raises(b3.PretrainLeak):
        b3.load_checkpoint(path, columns=c.columns, cutoff=cutoff + timedelta(days=1))
    with pytest.raises(b3.PretrainLeak):
        b3.load_checkpoint(path, columns=list(reversed(c.columns)), cutoff=cutoff)
    log.seen.add(cutoff + timedelta(days=3))                 # 본 세션이 컷오프를 넘은 체크포인트
    b3.save_checkpoint(path, state, columns=c.columns, cutoff=cutoff, log=log, channels=ch, fill=fill,
                       protocol_hash="t")
    with pytest.raises(b3.PretrainLeak):
        b3.load_checkpoint(path, columns=c.columns, cutoff=cutoff)
    with pytest.raises(SystemExit) as e:
        b3.load_checkpoint(tmp_path / "없다.pt", columns=c.columns, cutoff=cutoff)
    assert e.value.code == b3.INPUT_EXIT


# ② 구조 동일 ----------------------------------------------------------------------

def test_transfer_keeps_be1_structure_and_seed_head(fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    state, _log, c, _f, _ch = b3.run_pretrain(_pre(fa, cutoff), fa["feats"], fa["groups"], cutoff,
                                              epochs=1, steps_per_epoch=3)
    n = c.cube.shape[2]
    for seed in (0, 3):
        ref = be.make_model(seed, n).state_dict()
        got = b3.transfer_state(state, seed, n)
        assert set(got) == set(ref)
        assert all(tuple(got[k].shape) == tuple(ref[k].shape) for k in ref)
        for k in ref:
            if k.startswith("out."):
                assert torch.equal(got[k], ref[k]), "머리는 그 시드의 BE1 초기화 그대로"
            else:
                assert torch.equal(got[k], state[k]), "인코더는 사전학습 가중"
        be.make_model(seed, n).load_state_dict(got)           # strict 로드가 된다
    assert any(not torch.equal(state[k], be.make_model(0, n).state_dict()[k])
               for k in state if not k.startswith("out.")), "사전학습이 인코더를 바꿨어야 한다"


def test_transfer_refuses_foreign_structure():
    n = 5
    state = be.make_model(0, n).state_dict()
    with pytest.raises(b3.PretrainLeak):
        b3.transfer_state({k: v for k, v in state.items() if k != "out.bias"}, 0, n)
    bad = dict(state)
    bad["inp.weight"] = torch.zeros(be.D_MODEL, n + 1)
    with pytest.raises(b3.PretrainLeak):
        b3.transfer_state(bad, 0, n)


def test_encode_matches_be1_forward():
    model = be.make_model(1, 7).eval()
    x = torch.randn(60, be.N_STEPS, 7)
    with torch.inference_mode():
        assert torch.allclose(model.out(b3.encode(model, x)).squeeze(-1), model(x), atol=1e-6)


def test_hyperparameters_are_be1s():
    """구조·최적화 상수를 따로 두지 않는다 — BE1 것을 그대로 쓴다(한 축만 바꾼다)."""
    src = (REPO / "tools/trial_be3_pretrain.py").read_text()
    for name in ("D_MODEL", "HEADS", "FFN", "DROPOUT", "TIME_LAYERS", "SET_LAYERS", "LR ", "WEIGHT_DECAY", "WINDOW ="):
        assert not re.search(rf"^{name}\s*=", src, flags=re.M), name
    assert b3.CONTRAST_SHIFT == kit.REBALANCE_EVERY
    assert b3.W_REC == b3.W_NCE == 1.0


# ③ 사전학습 없음 = BE1 경로 ---------------------------------------------------------

def _walk(fa, **kw):
    panel, feats, groups, sessions = fa["panel"], fa["feats"], fa["groups"], fa["sessions"]
    c = b3.make_cube(panel, feats)
    targets = be.target_columns(panel, c.sessions, c.entities)
    aug = be.Aug(kit.drop_groups, dict(groups), c.index_of)
    blocks = list(kit.blocks(sessions))[:1]
    torch.manual_seed(0)
    judge, _train, _logs, _m = be.run_seed(kit, c.cube, c.observed, c.market_id, c.entities, c.sessions, sessions,
                                          blocks, targets, aug, 2, max_epochs=1, **kw)
    return judge, c


def test_no_pretrain_is_bitwise_the_be1_path(fa):
    a, c = _walk(fa)
    b, _ = _walk(fa, init_state=None, trial="BE3", source="x")
    pd.testing.assert_frame_equal(a, b)
    own = be.make_model(2, c.cube.shape[2]).state_dict()     # 자기 초기화를 넣어도 같은 경로
    d, _ = _walk(fa, init_state=own)
    pd.testing.assert_frame_equal(a, d)


def test_pretrained_init_changes_the_start(fa):
    cutoff = b3.pretrain_cutoff(fa["sessions"])
    state, *_ = b3.run_pretrain(_pre(fa, cutoff), fa["feats"], fa["groups"], cutoff, epochs=1, steps_per_epoch=3)
    a, c = _walk(fa)
    b, _ = _walk(fa, init_state=b3.transfer_state(state, 2, c.cube.shape[2]))
    assert len(a) == len(b) and not np.allclose(a["pred"], b["pred"])


# ④ 가림 · 채움 --------------------------------------------------------------------

def _gidx():
    groups = {"s": ["a", "b"], "g": ["c", "d", "miss_g"]}
    index_of = {"a": 0, "b": 1, "c": 2, "d": 3, "miss_g": 4}
    return groups, index_of, b3.group_index(groups, index_of)


def test_mask_respects_missing_flag_and_masks_whole_window():
    _groups, _idx, gidx = _gidx()
    x = np.ones((400, be.N_STEPS, 5), np.float32)
    x[:, :, 4] = 0.0
    x[:200, 0, 4] = 1.0                  # 앞 200 종목은 그 날 g 묶음이 없다
    x[:200, 0, 2:4] = 0.0
    out, target = b3.mask_groups(x, gidx, {"s", "g"}, np.random.default_rng(0), p=0.5)
    assert not target[:200, 2:4].any(), "그 날 없는 묶음은 복원 대상이 아니다"
    hit = np.flatnonzero(target[:, 2])
    assert len(hit) > 50
    assert (out[hit][:, :, 2:4] == 0).all() and (out[hit][:, :, 4] == 1).all(), "창 전체를 결측 모양으로"
    assert (target[:, 2] == target[:, 3]).all(), "묶음 단위"
    assert not target[:, 4].any(), "표지는 복원 대상이 아니다"


def test_mask_skips_groups_outside_channels():
    _groups, _idx, gidx = _gidx()
    x = np.ones((300, be.N_STEPS, 5), np.float32)
    x[:, :, 4] = 0.0
    _out, target = b3.mask_groups(x, gidx, {"s"}, np.random.default_rng(1), p=0.9)
    assert target[:, :2].any() and not target[:, 2:].any()


def test_mask_presence_uses_reference_not_noisy_input():
    _groups, _idx, gidx = _gidx()
    ref = np.zeros((300, be.N_STEPS, 5), np.float32)
    ref[:, :, 4] = 1.0                                  # 아무것도 없다
    noisy = ref + np.random.default_rng(2).normal(0, 0.1, ref.shape).astype(np.float32)
    _o, target = b3.mask_groups(noisy, gidx, {"s", "g"}, np.random.default_rng(3), p=0.9, ref=ref)
    assert not target.any()


def test_fill_and_channels_drop_thin_groups(fa):
    fill = b3.group_fill(fa["panel"], fa["groups"])
    f = fill.set_index(["market", "group"])["fill"]
    assert f[("KR", "gB")] == pytest.approx(0.70, abs=0.03)
    assert f[("KR", "gC")] == pytest.approx(0.05, abs=0.02)
    ch = b3.pretrain_channels(fill)
    assert ch["KR"] == {"gA", "gB"} and ch["US"] == {"gA", "gB"}
    c = b3.make_cube(fa["panel"], fa["feats"])
    masks = b3.channel_masks(fa["groups"], c.index_of, ch)
    zero, flags = masks[0]
    assert sorted(zero) == sorted(c.index_of[x] for x in fa["groups"]["gC"] if not x.startswith("miss_"))
    assert list(flags) == [c.index_of["miss_gc"]]
    x = np.zeros((3, be.N_STEPS, len(c.columns)), np.float32) + 0.5
    b3.apply_channel_mask(x, masks[0])
    assert (x[:, :, zero] == 0).all() and (x[:, :, flags] == 1).all()


def test_info_nce_is_one_at_chance_and_low_when_aligned():
    same = torch.ones(400, 16)                     # 구별 못 함(모든 로짓이 같다) = 우연 수준
    assert float(b3.info_nce(same, same)) == pytest.approx(1.0, abs=1e-5)
    z = torch.randn(400, 16)
    assert float(b3.info_nce(z, z)) < 0.5


# ⑤ 기준 판정 경계 ------------------------------------------------------------------

def _t(**per_seed):
    return {s: {"ann": a, "box_ann": a} for s, a in per_seed.items()}


def test_criterion_seven_boundaries():
    be2 = {s: {"ann": 0.20} for s in range(5)}
    ok, _ = b3.criterion_ensemble({s: {"ann": 0.21} for s in range(5)}, be2)
    assert ok                                                   # 정확히 +1%p · 5/5
    ok, _ = b3.criterion_ensemble({s: {"ann": 0.2099} for s in range(5)}, be2)
    assert not ok                                               # +0.99%p
    four = {0: {"ann": 0.25}, 1: {"ann": 0.25}, 2: {"ann": 0.25}, 3: {"ann": 0.21}, 4: {"ann": 0.19}}
    assert b3.criterion_ensemble(four, be2)[0]                  # 4/5 = 80% · 평균 +3%p
    three = {0: {"ann": 0.30}, 1: {"ann": 0.30}, 2: {"ann": 0.30}, 3: {"ann": 0.19}, 4: {"ann": 0.19}}
    assert not b3.criterion_ensemble(three, be2)[0]             # 3/5 = 60%


def test_criterion_eight_requires_strict_improvement():
    be1 = {s: {"box_ann": 0.10} for s in range(5)}
    assert not b3.criterion_box({s: {"box_ann": 0.10} for s in range(5)}, be1)[0]
    assert b3.criterion_box({s: {"box_ann": 0.1001} for s in range(5)}, be1)[0]
    assert not b3.criterion_box({s: {"box_ann": 0.09} for s in range(5)}, be1)[0]


def test_verdict_combinations():
    yes, c1only, no = "채택 — 모델이 나아서(⑥ 통과)", "채택 후보 C1 — 정보가 늘어서", "기각"
    assert b3.verdict(yes, no, False, True).startswith("채택 후보 — BE3 단독")
    assert b3.verdict(yes, no, True, False).startswith("채택 후보 — BE3+C1") is False
    assert b3.verdict(no, yes, True, True).startswith("채택 후보 — BE3+C1")
    assert b3.verdict(no, yes, True, False).startswith("기각")         # ⑧ 없이는 후보 아님
    assert b3.verdict(no, yes, False, True).startswith("기각")
    assert b3.verdict(c1only, c1only, True, True).startswith("기각")   # ⑥ 미통과는 모델 주장이 아니다


def test_missing_metric_key_is_loud():
    with pytest.raises(ValueError):
        b3.require_keys({"BE3": {0: {"ann": 0.1}}})


# ⑥ 등록 관문 · 러너 · 가드 ----------------------------------------------------------

def test_registration_gate(tmp_path):
    draft = tmp_path / "p.md"
    draft.write_text("> **초안 — 승인 전**\n\n# x\n")
    with pytest.raises(SystemExit):
        b3.require_registered(argparse.Namespace(i_registered=False), "x", draft)
    with pytest.raises(SystemExit):
        b3.require_registered(argparse.Namespace(i_registered=True), "x", draft)
    fixed = tmp_path / "q.md"
    fixed.write_text("> **고정 2026-10-01**\n\n# x\n")
    assert len(b3.require_registered(argparse.Namespace(i_registered=True), "x", fixed)) == 16


def test_real_runs_refuse_without_registration():
    """플래그 없이는 셋 다 거부. 등록 문서가 초안인 동안은 플래그가 있어도 거부(이 초안 커밋 시점)."""
    for argv in (["pretrain"], ["train"], ["judge"]):
        with pytest.raises(SystemExit):
            b3.main(argv)
    if (REPO / b3.PROTOCOL).read_text().startswith("> **초안"):
        for argv in (["pretrain", "--i-registered"], ["train", "--i-registered"], ["judge", "--i-registered"]):
            with pytest.raises(SystemExit):
                b3.main(argv)


def test_flags_refused_outside_synthetic():
    assert b3.main(["pretrain", "--epochs", "3"]) == 2
    assert b3.main(["train", "--no-pretrain"]) == 2


def test_synthetic_smoke_end_to_end(tmp_path):
    assert b3.main(["pretrain", "--synthetic", "--threads", "2", "--epochs", "1", "--steps", "3",
                    "--out", str(tmp_path)]) == 0
    assert (tmp_path / b3.CKPT_NAME).exists()
    assert b3.main(["train", "--synthetic", "--threads", "2", "--seeds", "0", "--smoke", "1",
                    "--max-epochs", "1", "--steps", "3"]) == 0
    assert b3.main(["train", "--synthetic", "--threads", "2", "--seeds", "0", "--smoke", "1",
                    "--max-epochs", "1", "--no-pretrain"]) == 0


def test_runner_has_gates():
    text = (REPO / "scripts/be3_pretrain.sh").read_text()
    assert "trial_be3_pretrain.py" in text and "--i-registered" in text and "--save" in text
    assert "초안" in text and "free -m" in text and "운영 창" in text
    for window in ("830", "910", "1200", "1240", "1515", "1640", "2235", "2335"):
        assert window in text
    assert '"${NFAIL}" -ge 2' in text
    assert "pretrain.pt" in text and "seed${s}-judge.parquet" in text


def test_guards_catch_this_tool():
    cmdline = "/home/x/.venv/bin/python -u tools/trial_be3_pretrain.py train --i-registered --seeds 0"
    guard = (REPO / "scripts/memory_guard.sh").read_text()
    pats = re.findall(r'^\s*"([^"]+)"', guard, flags=re.M)
    mine = [p for p in pats if "be3" in p]
    assert mine and re.search(mine[0], cmdline) and not re.search(mine[0], mine[0])
    health = (REPO / "scripts/health_watch.sh").read_text()
    stoppable = re.search(r"STOPPABLE='([^']+)'", health)
    assert stoppable and re.search(stoppable.group(1), cmdline)


def test_protocol_records_the_fixed_values():
    text = (REPO / b3.PROTOCOL).read_text()
    assert text.splitlines()[0].startswith("> **고정 2026-10-01**")   # 10/1 사용자 승인
    import hashlib
    assert hashlib.sha256((REPO / b3.PROTOCOL).read_bytes()).hexdigest()[:16] == "76c4bd0058cc33f4"
    for token in (f"{b3.MASK_P}", f"{b3.MIN_FILL:.0%}", f"{b3.TAU}", "2023-01-31", "2023-02-15",
                  b3.ENTITY, "⑦", "⑧", f"{b3.PRETRAIN_EPOCHS}"):
        assert token in text, token
