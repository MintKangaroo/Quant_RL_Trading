"""시행 묶음 next-four(① TB · ③ RE · ④ DF) — 가중 표 적용 · HMM 확률은 as_of 까지만 · BE2 합성 = 회차 판정 규칙 · 기준 경계 · 관문.

합성 자료다("이긴다" 는 안 잰다). 규칙은 진짜 부품에서 온다(kit · trial_final_dfl 기울이기 장부 · 회차 rank_average · v2_regime_hmm).
"""

from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tools import trial_next_four as nf

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")

REPO = Path(__file__).resolve().parents[2]


def _preds(seed: int, n_days: int = 6, n_names: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = [date(2024, 1, 2) + timedelta(days=i) for i in range(n_days)]
    rows = [(f"{m}:{i:03d}", d, m) for m in ("KR", "US") for d in days for i in range(n_names)]
    frame = pd.DataFrame(rows, columns=nf.KEYS)
    return frame.assign(pred=rng.normal(size=len(frame)))


def _probs_for(frame: pd.DataFrame, p: tuple[float, float, float]) -> dict[str, pd.DataFrame]:
    out = {}
    for m, part in frame.groupby("market"):
        days = sorted(set(part["session"]))
        out[str(m)] = pd.DataFrame([p] * len(days), index=days, columns=["p0", "p1", "p2"])
    return out


# --------------------------------------------------------------------------- 가중 표


def test_RE_표는_행마다_합1이고_국면마다_가설의_모델이_가장_무겁다() -> None:
    for name, row in nf.RE_TABLE.items():
        assert set(row) == set(nf.RE_MODELS)
        assert sum(row.values()) == pytest.approx(1.0)
    top = {name: max(row, key=row.get) for name, row in nf.RE_TABLE.items()}   # type: ignore[arg-type]
    assert top == {"crisis": "BF2", "box": "C1", "rally": "BE1"}
    assert nf.STATE_NAMES == ("crisis", "box", "rally")      # 상태 0 = 평균 일수익이 가장 낮다(v2_regime_hmm.fit)


@pytest.mark.parametrize("state", [0, 1, 2])
def test_한_국면_확률이_1이면_그_행_그대로다(state: int) -> None:
    p = np.zeros(3)
    p[state] = 1.0
    probs = pd.DataFrame([{"session": date(2024, 1, 2), "market": "KR", "p0": p[0], "p1": p[1], "p2": p[2]}])
    w = nf.re_weights(probs).iloc[0]
    for model in nf.RE_MODELS:
        assert w[model] == pytest.approx(nf.RE_TABLE[nf.STATE_NAMES[state]][model])


def test_섞인_확률은_표의_볼록결합이고_합1이다() -> None:
    p = (0.2, 0.3, 0.5)
    probs = pd.DataFrame([{"session": date(2024, 1, 2), "market": "US", "p0": p[0], "p1": p[1], "p2": p[2]}])
    w = nf.re_weights(probs).iloc[0]
    for model in nf.RE_MODELS:
        want = sum(p[s] * nf.RE_TABLE[nf.STATE_NAMES[s]][model] for s in range(3))
        assert w[model] == pytest.approx(want)
    assert sum(w[m] for m in nf.RE_MODELS) == pytest.approx(1.0)


def test_가중_점수는_세션_가중과_세션_안_백분위의_곱의_합이다() -> None:
    be1, c1, bf1 = _preds(0), _preds(1), _preds(2)
    bf2 = nf.bf2_scores(bf1, c1)
    pcts = nf.pct_frame({"BE1": be1, "C1": c1, "BF2": bf2})
    probs = _probs_for(pcts, (0.1, 0.2, 0.7))
    attached = nf.attach_probs(pcts, probs)
    got = nf.weighted(pcts, nf.re_weights(attached))
    w = nf.re_weights(attached).iloc[0]
    want = w["BE1"] * pcts["BE1"] + w["C1"] * pcts["C1"] + w["BF2"] * pcts["BF2"]
    assert np.allclose(got["pred"].to_numpy(), want.to_numpy())
    # 백분위는 (세션, 시장) 안에서 — 한 세션·시장의 값이 (0, 1] 이고 최댓값이 1
    one = pcts[(pcts["session"] == pcts["session"].iloc[0]) & (pcts["market"] == "KR")]
    assert one["BE1"].max() == pytest.approx(1.0) and one["BE1"].min() > 0


def test_가중이_없는_세션은_조용히_빼지_않는다() -> None:
    pcts = nf.pct_frame({"C1": _preds(1)})
    w = nf.constant_weights(pcts, C1=1.0)
    w = w[w["market"] == "KR"]
    with pytest.raises(ValueError, match="가중이 없는 세션"):
        nf.weighted(pcts, w)


# --------------------------------------------------------------------------- BE2 · BF2 = 회차 판정과 같은 규칙


def test_BE2_합성은_회차_판정이_쓴_함수_그대로다() -> None:
    from tools.trial_final_transformer import rank_average

    be1, c1 = _preds(0), _preds(1)
    got = nf.be2_scores(be1, c1)
    want = rank_average(be1, c1)
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True))
    # 회차 판정 로그의 BE2 = 시드마다 rank_average(BE1_s, C1_s) — 그 줄이 코드에 그대로 있는지 못 박는다
    src = (REPO / "tools/trial_final_transformer.py").read_text()
    assert 'rank_average(preds[s], ctrl["C1"][s])' in src


def test_RE_의_가중을_반반으로_고정하면_BE2_와_정확히_같다() -> None:
    be1, c1 = _preds(0), _preds(1)
    pcts = nf.pct_frame({"BE1": be1, "C1": c1})
    half = nf.weighted(pcts, nf.constant_weights(pcts, BE1=0.5, C1=0.5))
    be2 = nf.be2_scores(be1, c1)
    m = half.merge(be2, on=nf.KEYS, suffixes=("_re", "_be2"))
    assert len(m) == len(be2) == len(half)
    assert np.allclose(m["pred_re"], m["pred_be2"])


def test_BF2_합성은_BF_판정이_쓴_함수_그대로다() -> None:
    from tools.trial_final_lambdarank import rank_average

    bf1, c1 = _preds(3), _preds(1)
    got = nf.bf2_scores(bf1.assign(label=1), c1)            # BF1 캐시에는 label 열이 있다 — 결과에 안 섞인다
    want = rank_average(bf1, c1, ["session", "market"])[nf.KEYS + ["pred"]]
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True))
    src = (REPO / "tools/trial_final_lambdarank.py").read_text()
    assert "rank_average(bf1, c1p, keys)" in src


# --------------------------------------------------------------------------- DF


def test_위기_세션이_없으면_DF_는_C1_프라임과_정확히_같다() -> None:
    c1, bf2 = _preds(1), nf.bf2_scores(_preds(3), _preds(1))
    pcts = nf.pct_frame({"C1": c1, "BF2": bf2})
    attached = nf.attach_probs(pcts, _probs_for(pcts, (0.5, 0.2, 0.3)))   # 0.5 는 문턱 "초과" 가 아니다
    df = nf.weighted(pcts, nf.df_weights(attached))
    c1p = nf.weighted(pcts, nf.constant_weights(pcts, C1=1.0))
    assert np.array_equal(df["pred"].to_numpy(), c1p["pred"].to_numpy())


def test_위기_세션에만_BF2_와_반반이다() -> None:
    c1, bf2 = _preds(1), nf.bf2_scores(_preds(3), _preds(1))
    pcts = nf.pct_frame({"C1": c1, "BF2": bf2})
    probs = _probs_for(pcts, (0.1, 0.4, 0.5))
    crisis_day = sorted(set(pcts["session"]))[2]
    probs["KR"].loc[crisis_day] = (0.51, 0.29, 0.20)
    attached = nf.attach_probs(pcts, probs)
    w = nf.df_weights(attached)
    fire = w[(w["C1"] < 1.0)]
    assert set(zip(fire["session"], fire["market"], strict=True)) == {(crisis_day, "KR")}
    assert (fire["C1"] == 1 - nf.DF_BLEND).all() and (fire["BF2"] == nf.DF_BLEND).all()
    df = nf.weighted(pcts, w).merge(pcts, on=nf.KEYS)
    hit = (df["session"] == crisis_day) & (df["market"] == "KR")
    assert np.allclose(df.loc[hit, "pred"], 0.5 * df.loc[hit, "C1"] + 0.5 * df.loc[hit, "BF2"])
    assert np.allclose(df.loc[~hit, "pred"], df.loc[~hit, "C1"])


# --------------------------------------------------------------------------- HMM — as_of 까지만


def _closes(n: int = 230, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    days = [d.date() for d in pd.bdate_range("2021-01-04", periods=n)]
    r = np.concatenate([rng.normal(0.001, 0.008, n // 2), rng.normal(-0.001, 0.02, n - n // 2)])
    return pd.Series(100 * np.exp(np.cumsum(r)), index=days)


@pytest.fixture
def fast_hmm(monkeypatch):  # type: ignore[no-untyped-def]
    from tools import v2_regime_hmm as hmm
    monkeypatch.setattr(hmm, "MIN_FIT", 60)
    monkeypatch.setattr(hmm, "EM_ITERS", 15)
    return hmm


def test_HMM_확률은_그_세션까지의_지수만_본다_앞부분만_준_적합과_같다(fast_hmm) -> None:  # type: ignore[no-untyped-def]
    closes = _closes()
    full = nf.regime_probs(closes)
    for cut in (full.index[5], full.index[len(full) // 2], full.index[-3]):
        part = nf.regime_probs(closes[closes.index <= cut])
        pd.testing.assert_frame_equal(part, full.loc[full.index <= cut], check_exact=False, rtol=1e-9, atol=1e-12)
    # 미래 지수를 크게 바꿔도 앞 확률은 그대로다
    shocked = closes.copy()
    cut = full.index[len(full) // 2]
    shocked[shocked.index > cut] *= np.linspace(1.0, 0.3, int((shocked.index > cut).sum()))
    again = nf.regime_probs(shocked)
    pd.testing.assert_frame_equal(again.loc[again.index <= cut], full.loc[full.index <= cut], rtol=1e-9, atol=1e-12)
    assert np.allclose(full.sum(axis=1), 1.0)


def test_확률은_세션_이하의_가장_최근_값만_붙고_미래_값은_절대_안_붙는다() -> None:
    keys = pd.DataFrame({"session": [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 20)], "market": "KR"})
    probs = {"KR": pd.DataFrame({"p0": [0.9, 0.1], "p1": [0.05, 0.1], "p2": [0.05, 0.8]},
                                index=[date(2024, 1, 3), date(2024, 1, 5)])}
    got = nf.attach_probs(keys, probs).set_index("session")
    assert got.loc[date(2024, 1, 3), "p0"] == pytest.approx(0.9) and got.loc[date(2024, 1, 3), "exact"]
    assert got.loc[date(2024, 1, 4), "p0"] == pytest.approx(0.9)          # 1/5 의 확률(미래)이 아니라 1/3 것
    assert not got.loc[date(2024, 1, 4), "exact"]
    assert np.isnan(got.loc[date(2024, 1, 20), "p0"])                      # 15일 묵은 것은 붙이지 않는다


def test_HMM_확률이_모자라면_rc8() -> None:
    keys = pd.DataFrame({"session": [date(2024, 1, 3), date(2024, 1, 20)], "market": "KR"})
    probs = {"KR": pd.DataFrame({"p0": [0.9], "p1": [0.05], "p2": [0.05]}, index=[date(2024, 1, 3)])}
    with pytest.raises(SystemExit) as exc:
        nf.require_probs(nf.attach_probs(keys, probs))
    assert exc.value.code == nf.HMM_EXIT


def test_지수_종가는_금고를_읽지_않는다() -> None:
    with pytest.raises(ValueError, match="금고"):
        nf.index_closes(object(), "KR", date(2026, 7, 1))
    assert nf.WINDOW[1] < date(2026, 7, 1)


# --------------------------------------------------------------------------- 기준 경계


def _m(**kw: float) -> dict[str, float]:
    base = {"ann": 0.20, "box_ann": 0.10, "rally_ann": 0.30, "mdd": -0.20, "turn": 10.0, "ic": 0.05,
            "ir": 0.5, "box_excess": 0.01, "rally_excess": 0.01, "beta": 1.0}
    base.update(kw)
    return base


EPS = 1e-6


def _seeds(**kw: float) -> dict[int, dict[str, float]]:
    return {s: _m(**kw) for s in range(5)}


@pytest.mark.parametrize(("field", "ok", "bad"), [
    ("mdd", -0.25 + nf.DF_GATE_MDD + EPS, -0.25 + nf.DF_GATE_MDD - EPS),
    ("ann", 0.20 + nf.DF_GATE_ANN + EPS, 0.20 + nf.DF_GATE_ANN - EPS),
    ("box_ann", 0.10 + nf.DF_GATE_REGIME + EPS, 0.10 + nf.DF_GATE_REGIME - EPS),
    ("rally_ann", 0.30 + nf.DF_GATE_REGIME + EPS, 0.30 + nf.DF_GATE_REGIME - EPS),
    ("turn", 10.0 * nf.DF_GATE_TURN - EPS, 10.0 * nf.DF_GATE_TURN + EPS),
])
def test_DF_기준_경계(field: str, ok: float, bad: float) -> None:
    c1p = _seeds(mdd=-0.25)
    good = {"mdd": -0.25 + nf.DF_GATE_MDD + EPS}
    assert nf.judge_df(_seeds(**{**good, field: ok}), c1p).verdict.startswith("채택 후보")
    assert nf.judge_df(_seeds(**{**good, field: bad}), c1p).verdict == "기각"


def test_RE_7번은_BE2_보다_1퍼센트포인트() -> None:
    c0, c1 = _seeds(ann=0.10), _seeds(ann=0.15)
    be2 = _seeds(ann=0.20)
    ok = nf.judge_re(_seeds(ann=0.20 + nf.RE_GATE_BE2 + EPS), c0, c1, be2)
    bad = nf.judge_re(_seeds(ann=0.20 + nf.RE_GATE_BE2 - EPS), c0, c1, be2)
    assert ok.verdict.startswith("채택 후보")
    assert bad.verdict.startswith("①~⑥ 통과·⑦ 미통과")
    assert any(line.startswith("⑦ 대 BE2") for line in ok.lines)


def test_RE_는_회차_기준_1부터_6을_먼저_본다() -> None:
    c0, c1, be2 = _seeds(ann=0.10), _seeds(ann=0.15), _seeds(ann=0.05)
    # ⑥(대 C1 +1%p) 미달이면 ⑦ 을 넘어도 기각
    assert nf.judge_re(_seeds(ann=0.155), c0, c1, be2).verdict == "기각"


@pytest.mark.parametrize(("kw", "passes"), [
    ({"ir": nf.IR_GATE + EPS}, True), ({"ir": nf.IR_GATE - EPS}, False),
    ({"box_excess": 0.0}, True), ({"box_excess": -EPS}, False), ({"rally_excess": -EPS}, False),
])
def test_TB_7_8번_경계(kw: dict[str, float], passes: bool) -> None:
    t0, t1 = _seeds(ann=0.10), _seeds(ann=0.15)
    v = nf.judge_tb(_seeds(ann=0.20, **kw), t0, t1, {"ann": 0.18, "ir": 0.1})
    assert v.verdict.startswith("채택 후보") is passes


def test_TB_9번은_B0_이상() -> None:
    t0, t1 = _seeds(ann=0.10), _seeds(ann=0.15)
    assert nf.judge_tb(_seeds(ann=0.20), t0, t1, {"ann": 0.20 - EPS}).verdict.startswith("채택 후보")
    assert nf.judge_tb(_seeds(ann=0.20), t0, t1, {"ann": 0.20 + EPS}).verdict == "기각"


def test_TB_6번_미통과면_T1_로_충분() -> None:
    t0, t1 = _seeds(ann=0.10), _seeds(ann=0.195)
    v = nf.judge_tb(_seeds(ann=0.20), t0, t1, {"ann": 0.18})
    assert v.verdict.startswith("①~⑤·⑦~⑨ 통과·⑥ 미통과")


def test_지표_키가_비면_조용히_기각하지_않는다() -> None:
    broken = _seeds()
    del broken[0]["mdd"]
    with pytest.raises(ValueError, match="mdd"):
        nf.judge_df(broken, _seeds())
    broken = _seeds()
    broken[1]["ir"] = float("nan")
    with pytest.raises(ValueError, match="ir"):
        nf.judge_tb(broken, _seeds(), _seeds(), {"ann": 0.1})


# --------------------------------------------------------------------------- 관문


def test_등록_플래그와_초안_머리줄이_없으면_판정을_거부한다(tmp_path: Path) -> None:
    doc = tmp_path / "p.md"
    doc.write_text("> **초안 2026-09-30** — 해시 미고정\n# 본문\n")
    with pytest.raises(SystemExit, match="--i-registered"):
        nf.require_registered(argparse.Namespace(i_registered=False), "판정", doc)
    with pytest.raises(SystemExit, match="초안"):
        nf.require_registered(argparse.Namespace(i_registered=True), "판정", doc)
    doc.write_text("> **고정 2026-10-01** — 승인\n# 본문\n")
    assert len(nf.require_registered(argparse.Namespace(i_registered=True), "판정", doc)) == 16


@pytest.mark.parametrize("cmd", ["tb", "re", "df"])
def test_지금_등록_문서는_초안이라_세_명령_다_안_돈다(cmd: str, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(REPO)
    assert (REPO / nf.PROTOCOL).read_text().startswith("> **초안")
    with pytest.raises(SystemExit, match="초안"):
        nf.main([cmd, "--i-registered"])
    with pytest.raises(SystemExit, match="--i-registered"):
        nf.main([cmd])


@pytest.mark.parametrize(("vault", "holds"), [
    ("채택 — BE2 금고 통과(①~⑥, 모델이 나아서)", True),
    ("①~⑤ 통과·⑥ 미통과 — 정보 효과: BE2 모델 주장 기각, C1(GBM·FA) 후보 여부는 사용자 결정", False),
    ("기각", False),
])
def test_BE2_금고가_채택이_아니면_TB_RE_의_전제가_소멸한다(vault: str, holds: bool) -> None:
    line = nf.premise_from(vault)
    assert line.startswith("전제 유지") is holds
    assert line.startswith("전제 소멸") is (not holds)
    assert nf.premise_line(object(), "DF").startswith("전제: 없음")


def test_처리_예측이_없으면_rc3_새로_학습하지_않는다(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        nf.require_inputs("KR+US-20220701-20260630", (0, 1), ("BE1", "BF1"), cache=tmp_path, be_dir=tmp_path)
    assert exc.value.code == nf.INPUT_EXIT


def test_처리와_대조의_채점_세션이_다르면_rc9() -> None:
    a = _preds(0)
    b = a[a["session"] != a["session"].iloc[0]]
    with pytest.raises(SystemExit) as exc:
        nf.same_sessions(a, b, label="시험")
    assert exc.value.code == nf.SPAN_EXIT


def test_패널_캐시가_없으면_rc3(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        nf.light_panel(tmp_path)
    assert exc.value.code == nf.INPUT_EXIT


# --------------------------------------------------------------------------- 합성 끝까지


@pytest.fixture(scope="module")
def smoke():  # type: ignore[no-untyped-def]
    inp = nf.synthetic_inputs(seeds=(0, 1))
    return inp, {cmd: nf.RUNNERS[cmd](inp, (0, 1)) for cmd in ("tb", "re", "df")}


def test_합성_세_경로가_끝까지_돌고_판정을_낸다(smoke) -> None:  # type: ignore[no-untyped-def]
    _inp, got = smoke
    for cmd, v in got.items():
        assert v.trial == cmd.upper()
        assert v.verdict.startswith(("기각", "채택 후보", "①~"))
        assert not any(line.startswith("판정:") for line in v.lines)    # 판정 줄은 cmd_judge 가 마지막에 하나만
    assert any(line.startswith("⑦⑧⑨") for line in got["tb"].lines)
    assert any("지수 구성 있는 판정 세션" in line for line in got["tb"].lines)
    assert any(line.startswith("⑦ 대 BE2") for line in got["re"].lines)
    assert any("발동 세션" in line for line in got["df"].lines)
    assert any("HMM 확률 그 날짜" in line for line in got["re"].lines)


def test_TB_는_D1b_기울이기_장부에_BE2_T0_T1_을_넣는다(smoke) -> None:  # type: ignore[no-untyped-def]
    from tools import trial_final_dfl as dfl

    inp, got = smoke
    tables = got["tb"].tables
    prep = dfl.prepare(inp.panel, [], {}, inp.sessions, inp.books, inp.index_raw)
    be2 = nf.be2_scores(inp.be1[1], inp.controls["C1"][1])
    for arm, pred in (("TB", be2), ("T0", inp.controls["C0"][1]), ("T1", inp.controls["C1"][1])):
        want = dfl.tilt_book(pred, prep.data, inp.books, inp.y, lam=0.5)[0]
        assert tables[arm][1]["ann"] == pytest.approx(want["ann"]), arm
    assert dfl.LAMBDA == 0.5
    assert any("⑥ 대 T1" in line for line in got["tb"].lines)


def test_RE_의_BE2_대조와_DF_의_C1_프라임은_같은_kit_포트다(smoke) -> None:  # type: ignore[no-untyped-def]
    inp, got = smoke
    be2 = nf.be2_scores(inp.be1[0], inp.controls["C1"][0])
    want = nf.pooled(be2, inp.books, inp.y)
    assert got["re"].tables["BE2"][0]["ann"] == pytest.approx(want["ann"])
    c1 = nf.pooled(inp.controls["C1"][0], inp.books, inp.y)
    assert got["df"].tables["C1"][0]["ann"] == pytest.approx(c1["ann"])
    assert got["re"].tables["C1"][0]["ann"] == pytest.approx(c1["ann"])


# --------------------------------------------------------------------------- 러너 · 가드 · 문서


def test_러너는_tb_re_df_순서로_도는_등록_관문을_둔다() -> None:
    text = (REPO / "scripts/next_four.sh").read_text()
    order = [text.index(f"for CMD in tb re df")]
    assert order
    assert "trial_next_four.py" in text and "--i-registered" in text and "--save" in text
    assert '"^> \\*\\*초안"' in text or "초안" in text
    assert "free -m" in text and "운영 창" in text


def test_가드_목록이_이_도구를_잡는다() -> None:
    cmdline = "/home/x/.venv/bin/python -u tools/trial_next_four.py re --i-registered --save"
    guard = (REPO / "scripts/memory_guard.sh").read_text()
    pats = re.findall(r'^\s*"([^"]+)"', guard, flags=re.M)
    assert any("next_fou" in p and re.search(p, cmdline) for p in pats)
    # 대괄호 트릭(자기매칭 회피) — 패턴 문자열이 자기 자신에 걸리지 않는다
    mine = next(p for p in pats if "next_fou" in p)
    assert not re.search(mine, mine)
    health = (REPO / "scripts/health_watch.sh").read_text()
    stoppable = re.search(r"STOPPABLE='([^']+)'", health)
    assert stoppable and re.search(stoppable.group(1), cmdline)


def test_등록_문서가_코드의_고정값을_적는다() -> None:
    text = (REPO / nf.PROTOCOL).read_text()
    for name, row in nf.RE_TABLE.items():
        assert name in text
        for model, v in row.items():
            assert model in text and f"{v:.1f}" in text
    assert f"{nf.DF_THRESHOLD}" in text and "λ = 0.5" in text
    assert "## ② " in text                         # 다른 에이전트가 채우는 자리
    for heading in ("① TB", "③ RE", "④ DF"):
        assert heading in text
    assert "miss_ba" in text and "전제" in text
