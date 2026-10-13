"""공통 평가 사다리(tools/eval_ladder.py) — 합성 자료로만 시험한다(창고·캐시를 읽지 않는다)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tools import eval_ladder as el

KST = timezone(timedelta(hours=9))


def _context(n_days: int = 260, n_ent: int = 200, strength: float = 0.004, seed: int = 0) -> tuple[el.Context, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    days = [d.date() for d in pd.bdate_range("2023-02-01", periods=n_days + 10)]
    ents = [f"KR:{i:06d}" for i in range(n_ent)]
    sig = rng.standard_normal((len(days), n_ent))
    for t in range(1, len(days)):                  # 끈끈한 신호(AR 0.9)
        sig[t] = 0.9 * sig[t - 1] + np.sqrt(1 - 0.81) * sig[t]
    daily = np.zeros_like(sig)
    daily[1:] = strength * sig[:-1] + 0.02 * rng.standard_normal((len(days) - 1, n_ent))   # 어제 신호 → 오늘 수익
    close = pd.DataFrame(100 * np.cumprod(1 + daily, axis=0), index=days, columns=ents)
    ret = (close.shift(-2) / close.shift(-1) - 1.0)
    k = close.mean(axis=1)
    bench = k.shift(-2) / k.shift(-1) - 1.0
    trad = {d: set(ents) for d in days}
    uni = ret.mean(axis=1)
    ctx = el.Context(close=close, ret=ret, bench=bench, k200_close=k, trad=trad, uni=uni,
                     scale=pd.Series(1.0, index=days))
    # 점수 = t+1 의 신호(t+1→t+2 수익을 겨눔) + 잡음, 시드 셋
    preds = {}
    for s in range(3):
        noise = np.random.default_rng(100 + s).standard_normal(sig.shape)
        pv = pd.DataFrame(np.roll(sig, -1, axis=0) + 0.5 * noise, index=days, columns=ents).iloc[:n_days]
        long = pv.stack().rename("pred").reset_index()
        long.columns = ["session", "entity_id", "pred"]
        preds[s] = long[["entity_id", "session", "pred"]]
    return ctx, preds  # type: ignore[return-value]


def test_date_cap_is_pinned() -> None:
    assert el.DATE_CAP == date(2026, 9, 30)
    assert el.CAP_AS_OF == datetime(2026, 9, 30, 16, 0, tzinfo=UTC)
    assert el.CAP_AS_OF.astimezone(KST).date() == date(2026, 10, 1)   # 10/1 01:00 KST — 장 전
    assert el.CAP_AS_OF.astimezone(KST).hour < 9


def test_guard_cap_rejects_sealed_window() -> None:
    el.guard_cap([date(2026, 9, 30)], "시험")
    with pytest.raises(SystemExit) as exc:
        el.guard_cap([date(2026, 9, 29), date(2026, 10, 1)], "시험")
    assert exc.value.code == 2


def test_read_scores_rejects_late_session(tmp_path) -> None:
    ok = pd.DataFrame({"entity_id": ["KR:1"], "session": [date(2026, 9, 30)], "pred": [0.1], "seed": [0]})
    ok.to_parquet(tmp_path / "ok.parquet")
    assert set(el.read_scores(tmp_path / "ok.parquet")) == {0}
    bad = ok.assign(session=[date(2026, 10, 1)])
    bad.to_parquet(tmp_path / "bad.parquet")
    with pytest.raises(SystemExit):
        el.read_scores(tmp_path / "bad.parquet")


def test_write_scores_rejects_late_session(tmp_path) -> None:
    p = pd.DataFrame({"entity_id": ["KR:1"], "session": [date(2026, 10, 2)], "pred": [0.1]})
    with pytest.raises(SystemExit):
        el.write_scores({0: p}, tmp_path / "x.parquet")


def test_bundle_is_seed_mean() -> None:
    a = pd.DataFrame({"entity_id": ["A", "B"], "session": [date(2023, 1, 2)] * 2, "pred": [1.0, 3.0]})
    b = a.assign(pred=[3.0, 5.0])
    out = el.bundle({0: a, 1: b}).set_index("entity_id")["pred"]
    assert out["A"] == pytest.approx(2.0) and out["B"] == pytest.approx(4.0)


def test_label_is_t1_to_t6_and_cap_leaves_nan() -> None:
    ctx, _ = _context(n_days=30, n_ent=5)
    y = ctx.y5().set_index(["session", "entity_id"])["target"]
    d0, e = ctx.close.index[0], ctx.close.columns[0]
    expect = ctx.close[e].iloc[6] / ctx.close[e].iloc[1] - 1
    assert y.loc[(d0, e)] == pytest.approx(expect)
    last = ctx.close.index[-3]
    assert (last, e) not in y.index             # t+6 가 없는 세션은 라벨이 없다(만들지 않는다)


def test_ladder_passes_real_signal_and_reports_rungs() -> None:
    ctx, preds = _context()
    res = el.ladder(preds, ctx, control=None, thresholds=el.Thresholds(t_star=3.0), n_tests=2)
    c = res["checks"]
    assert c["①a IC>0·t≥t*"][0] is True
    assert c["①b D10−D1 t≥t*"][0] is True
    assert c["②a ΔIC>0·t≥t*"][0] is None        # 대조 없음 → 판정 불가 표시(조용히 O 로 두지 않는다)
    assert res["n_tests"] == 2
    assert res["bonferroni"]["ic_p_adj"] <= 1.0
    assert "| ①a" in el.render("합성", res)


def test_ladder_fails_noise_at_rung_one() -> None:
    ctx, preds = _context()
    rng = np.random.default_rng(7)
    noise = {s: p.assign(pred=rng.standard_normal(len(p))) for s, p in preds.items()}
    res = el.ladder(noise, ctx, control=preds, thresholds=el.Thresholds(t_star=3.0))
    assert res["checks"]["①a IC>0·t≥t*"][0] is False
    assert res["first_fail"] == "①"
    assert res["checks"]["②a ΔIC>0·t≥t*"][0] is False   # 신호 대비 잡음은 ΔIC 가 음수


def test_loss_table_has_every_stage() -> None:
    ctx, preds = _context(n_days=200)
    lt = el.loss_table(preds, ctx)
    assert [k for k, _ in el.STAGES] == list(lt["rows"])
    assert lt["rows"]["ic"]["seed_mean"]["ann"] > 0
    assert lt["rows"]["d10_d1"]["seed_mean"]["ann"] > 0
    assert lt["bundle_port"] is not None
    # 비용 전 ≥ 비용 후(같은 장부)
    assert lt["rows"]["top_gross"]["seed_mean"]["ann"] >= lt["rows"]["top_net"]["seed_mean"]["ann"]
    assert "손실표" in el.render_loss("합성", lt)


def test_special_windows_are_calendar_constants() -> None:
    assert el.SPECIAL_SPANS["코로나"] == (date(2020, 2, 17), date(2020, 6, 30))
    assert el.SPECIAL_SPANS["급등장"] == (date(2025, 1, 1), date(2026, 6, 30))
    assert el.EXTREME_DAYS == (date(2026, 3, 4), date(2026, 7, 28), date(2026, 7, 31))
    cal = [d.date() for d in pd.bdate_range("2026-07-20", "2026-08-10")]
    sp = el.special_sessions(cal, cal, label_days=2)
    # 7/28 이 실현 종가(t+1 또는 t+2)인 결정 세션 7/24(금)·7/27 은 특수, 7/22 는 아님
    assert date(2026, 7, 24) in sp and date(2026, 7, 27) in sp and date(2026, 7, 22) not in sp
    assert date(2026, 8, 5) not in sp


def test_signal_only_in_rally_is_marked_special() -> None:
    """신호가 급등장(2025~)에서만 있으면 전체로는 통과해도 S(능력이지만 특수)로 떨어진다."""
    rng = np.random.default_rng(3)
    days = [d.date() for d in pd.bdate_range("2023-06-01", "2026-06-20")]
    ents = [f"KR:{i:06d}" for i in range(120)]
    sig = rng.standard_normal((len(days), len(ents)))
    on = np.array([d >= date(2025, 1, 1) for d in days])[:, None]
    daily = np.zeros_like(sig)
    daily[1:] = 0.01 * sig[:-1] * on[1:] + 0.02 * rng.standard_normal((len(days) - 1, len(ents)))
    close = pd.DataFrame(100 * np.cumprod(1 + daily, axis=0), index=days, columns=ents)
    ret = close.shift(-2) / close.shift(-1) - 1.0
    k = close.mean(axis=1)
    ctx = el.Context(close=close, ret=ret, bench=k.shift(-2) / k.shift(-1) - 1.0, k200_close=k,
                     trad={d: set(ents) for d in days}, uni=ret.mean(axis=1), scale=pd.Series(1.0, index=days))
    pv = pd.DataFrame(np.roll(sig, -1, axis=0), index=days, columns=ents).iloc[:-10]
    long = pv.stack().rename("pred").reset_index()
    long.columns = ["session", "entity_id", "pred"]
    res = el.ladder({0: long[["entity_id", "session", "pred"]]}, ctx, thresholds=el.Thresholds(t_star=3.0))
    assert res["checks"]["①a IC>0·t≥t*"][0] == "S"
    assert res["first_fail"] == "①"
    assert "①a IC>0·t≥t*" in res["special_flags"]


@pytest.mark.parametrize(("hh", "mm", "wd", "ok"), [
    (22, 19, 1, True), (22, 20, 1, False), (0, 44, 2, False), (0, 45, 2, True),
    (8, 19, 2, True), (8, 20, 2, False), (15, 49, 2, False), (15, 50, 2, True), (10, 0, 5, True),
])
def test_heavy_window(hh: int, mm: int, wd: int, ok: bool) -> None:
    base = date(2026, 10, 12)                 # 월요일
    day = base + timedelta(days=wd)
    now = datetime(day.year, day.month, day.day, hh, mm, tzinfo=KST)
    assert el.heavy_allowed(now)[0] is ok
