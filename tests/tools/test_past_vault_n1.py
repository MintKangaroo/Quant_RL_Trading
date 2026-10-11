"""과거 금고 N1 — 합성 자료만(봉인 창고를 열지 않는다). 점수 식 · 제외 규칙 · 해시 미고정 거부."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from tools import past_vault_judge_n1 as jn
from tools import past_vault_n1_bake as nb
from tools import vault_judge as vj


def _panel(n: int = 700) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = [date(2015, 1, 1) + timedelta(days=i) for i in range(n)]
    S = pd.DataFrame(1_000_000.0, index=idx, columns=["KR:000001", "KR:000002", "KR:000003", "KR:000004"])
    F = pd.DataFrame(np.nan, index=idx, columns=S.columns)
    # 000002: 300번째 세션에 2:1 분할 — 같은 날 주식수 2배·가격 배율 0.5 (상쇄돼야 하고, 사건 창(252+120) 동안 결측이어야 한다)
    S.iloc[300:, 1] = 2_000_000.0
    F.iloc[300, 1] = 0.5
    # 000003: 사건 없이 꾸준히 발행(400번째부터 +20%)
    S.iloc[400:, 2] = 1_200_000.0
    # 000004: 사건 없는 하루 1/20 급감(보정 안 된 감자) — 급변 규칙으로 결측
    S.iloc[350:, 3] = 50_000.0
    return S, F


def test_score_formula() -> None:
    S, F = _panel()
    p = nb.nsi_panel(S, F)
    last = p.index[-1]
    assert p.iloc[:nb.WINDOW]["KR:000001"].isna().all()                  # 이력 252 전엔 없다
    assert p.at[last, "KR:000001"] == 0.0
    assert math.isclose(p.at[p.index[500], "KR:000003"], math.log(1.2))   # 진짜 발행(창 안)
    assert math.isclose(p.at[last, "KR:000003"], 0.0, abs_tol=1e-12)      # 252세션 지나면 변화 없음
    assert np.isnan(p.at[p.index[400], "KR:000002"])                      # 사건 창(252+120) 안
    assert math.isclose(p.at[last, "KR:000002"], 0.0, abs_tol=1e-12)      # 창을 벗어나면 분할은 상쇄
    assert np.isnan(p.at[p.index[500], "KR:000004"])                      # 2배 이상 급변이 창 안
    assert not np.isnan(p.at[last, "KR:000004"])                          # 252세션 지나면 다시 나온다


def test_exclusion_rule_top10_and_nan_kept() -> None:
    before = dict(vj.TF_RULES)
    assert "N1" not in vj.TF_RULES                                        # import 만으로 vault_judge 규칙이 안 바뀐다
    jn.install_rule()
    try:
        r = pd.Series({f"KR:{i:06d}": float(i) for i in range(30)})
        r["KR:999999"] = np.nan                                           # 결측 = 빼지 않는다
        cut = vj.tf_exclusions("N1", {date(2015, 6, 1): r})[date(2015, 6, 1)]
        assert cut == {"KR:000029", "KR:000028", "KR:000027"}            # ⌊0.1 × 30⌋ = 3, 상위(발행 많은 쪽)
        assert {k: v for k, v in vj.TF_RULES.items() if k != "N1"} == before
    finally:
        vj.TF_RULES.pop("N1", None)


def test_refuses_without_fixed_hash(capsys) -> None:
    assert jn.PROTOCOL_HASH is None or len(jn.PROTOCOL_HASH) == 16
    if jn.PROTOCOL_HASH is None:
        assert jn.main([]) == 2
        assert "N1 거부: 등록 문서 미고정" in capsys.readouterr().out
    vj.TF_RULES.pop("N1", None)


def test_refuses_on_hash_mismatch(monkeypatch, capsys) -> None:
    monkeypatch.setattr(jn, "PROTOCOL_HASH", "0" * 16)
    assert jn.main([]) == 2
    assert "N1 거부: 등록 문서 해시" in capsys.readouterr().out
    vj.TF_RULES.pop("N1", None)


def test_refuses_before_open(monkeypatch, capsys) -> None:
    import hashlib
    monkeypatch.setattr(jn, "PROTOCOL_HASH", hashlib.sha256(jn.PROTOCOL.read_bytes()).hexdigest()[:16])

    class Early:
        def now(self):
            return jn.OPEN_AT - timedelta(minutes=1)
    monkeypatch.setattr(jn, "LiveClock", Early)
    assert jn.main([]) == 2
    assert "개봉 시각" in capsys.readouterr().out
    vj.TF_RULES.pop("N1", None)


def test_bake_fingerprint_is_in_protocol() -> None:
    text = jn.PROTOCOL.read_text()
    assert jn.BAKE_SHA256 in text and f"{jn.BAKE_ROWS:,}" in text
    assert jn.check_bake.__doc__


def test_judge_one_n1_integration(monkeypatch) -> None:
    """합성 시세 — judge_one('N1') 이 상위 10%(발행 많은 쪽)를 빼고 기준 셋 줄을 낸다."""
    from tools import past_vault_judge as pj
    rng = np.random.default_rng(0)
    idx = [date(2015, 1, 1) + timedelta(days=i) for i in range(420)]
    cols = [f"KR:{i:06d}" for i in range(60)]
    close = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, 0.01, (len(idx), len(cols))), axis=0)) * 1000, index=idx, columns=cols)
    value = pd.DataFrame(2e9, index=idx, columns=cols)
    mcap = pd.DataFrame(np.tile(np.arange(60, 0, -1, dtype=float) * 1e11, (len(idx), 1)), index=idx, columns=cols)
    score = pd.DataFrame(np.tile(np.arange(60, dtype=float), (len(idx), 1)), index=idx, columns=cols)   # KR:000054~59 가 늘 상위 10%
    long = score.stack().rename("r_hat").reset_index()
    long.columns = ["session", "entity_id", "r_hat"]
    seen = []
    orig = vj.tf_exclusions

    def spy(kind, lagged, c0_pred=None):
        out = orig(kind, lagged, c0_pred)
        seen.append((kind, out))
        return out
    monkeypatch.setattr(vj, "tf_exclusions", spy)
    jn.install_rule()
    try:
        jn.assert_rule()
        per = (("앞", idx[260], idx[330]), ("뒤", idx[331], idx[410]))
        lines, verdict, info = pj.judge_one("N1", long, close, value, mcap, idx[260], idx[410], per)
    finally:
        vj.TF_RULES.pop("N1", None)
    kind, excl = seen[0]
    assert kind == "N1"
    assert all(v == {f"KR:{i:06d}" for i in range(54, 60)} for v in excl.values())
    assert lines[0].startswith("N1: ①원리") and "②넓은 포트" in lines[1] and "③대형주" in lines[2]
    assert verdict in ("기각",) or verdict.startswith("과거 금고 통과") and isinstance(info["t"], float)


def test_assert_rule_catches_overwrite() -> None:
    import pytest
    vj.TF_RULES["N1"] = (0.10, 0.0, 0.0)      # 누가 하위 10% 로 덮어쓴 경우
    try:
        with pytest.raises(SystemExit):
            jn.assert_rule()
    finally:
        vj.TF_RULES.pop("N1", None)


def test_exact_double_is_a_jump() -> None:
    """1:1 무상증자(정확히 2배, 배율 사건이 늦게 오거나 없음)도 급변 규칙(≥2배)에 걸린다."""
    idx = [date(2015, 1, 1) + timedelta(days=i) for i in range(400)]
    S = pd.DataFrame(1_234_567.0, index=idx, columns=["KR:000001"])
    S.iloc[300:, 0] = 2_469_134.0
    F = pd.DataFrame(np.nan, index=idx, columns=S.columns)
    p = nb.nsi_panel(S, F)
    assert np.isnan(p.iloc[350, 0]) and p.iloc[299, 0] == 0.0


def test_event_pad_boundary() -> None:
    """사건 창 경계 — 사건 e 에서 k = e+371 까지 결측, e+372 부터 값(창 252 + 여유 120)."""
    assert nb.WINDOW + nb.EVENT_PAD == 372
    idx = [date(2015, 1, 1) + timedelta(days=i) for i in range(800)]
    S = pd.DataFrame(1_000_000.0, index=idx, columns=["KR:000001"])
    F = pd.DataFrame(np.nan, index=idx, columns=S.columns)
    e = 300
    S.iloc[e:, 0] = 1_100_000.0          # 10% 무상(급변 규칙엔 안 걸린다)
    F.iloc[e, 0] = 1 / 1.1
    p = nb.nsi_panel(S, F)
    assert np.isnan(p.iloc[e + 371, 0])
    assert math.isclose(p.iloc[e + 372, 0], 0.0, abs_tol=1e-12)


def test_protocol_hash_matches_tool() -> None:
    """고정 뒤: 문서 sha256 앞 16자 == 도구 PROTOCOL_HASH. 미고정(None)이면 실패 대신 '미고정' 경고를 남긴다."""
    import hashlib
    import warnings
    got = hashlib.sha256(jn.PROTOCOL.read_bytes()).hexdigest()[:16]
    if jn.PROTOCOL_HASH is None:
        warnings.warn(f"N1 등록 문서 미고정 — 현재 해시 {got}", stacklevel=1)
        return
    assert got == jn.PROTOCOL_HASH
