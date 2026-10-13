"""ar_insider Analyst(AR shadow) — 얼린 AR 처리 모델을 판정 패널과 같은 규칙으로 채점한다. 합성 자료만.

지키는 것:
 ① 등록 문서와 같다 — 모델 해시·문서 해시·열 순서(`freeze_ar` 의 treat).
 ② 피처 = 판정 패널 규칙(`trial_pooled_rank.rank_gauss` → `trial_ranker_sources.attach`)과 같은 값.
 ③ 얼린 모델만 — usable_from 전·파일 없음·지문 불일치면 점수를 안 낸다(이유를 남긴다). 점수 6 열이 비면 안 낸다.
 ④ 끝에서 끝 — 합성 모델 → Analyst.run → ar_insider 신호(시드 평균 백분위의 순위).
 ⑤ 설치 도구 — 지문이 맞을 때만 복사, 자리에 다른 파일이 있으면 덮지 않는다.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts import ar_insider
from quant_rl_trading.analysts.ranker import rank_gauss
from quant_rl_trading.analysts.ranker_sources import GROUPS
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

REPO = Path(__file__).resolve().parents[2]
SESSION = date(2026, 10, 14)
AS_OF = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)     # 16:00 KST


# --------------------------------------------------------------------------- ① 등록 문서


def test_constants_match_the_registration() -> None:
    from tools import vault_judge as vj
    from tools.trial_insider_forward import INSIDER, PROTOCOLS, SEEDS
    from tools.trial_pooled import FEATS

    doc = REPO / ar_insider.PROTOCOL
    assert PROTOCOLS["AR"] == Path(ar_insider.PROTOCOL)
    assert hashlib.sha256(doc.read_bytes()).hexdigest()[:16] == ar_insider.PROTOCOL_HASH
    assert vj.pinned_hashes(REPO / vj.EARLY_PROTOCOL)["doc:AR"] == ar_insider.PROTOCOL_HASH
    pinned = vj.HASH_LINE.findall(doc.read_text()[doc.read_text().index("## 모델 해시"):])
    assert {k: v for k, v in pinned if k.startswith("AR-treat")} == ar_insider.MODEL_HASHES
    treat = [*FEATS, "is_us", *(c for g in INSIDER for c in GROUPS[g])]
    assert list(ar_insider.FEATURES) == treat
    assert ar_insider.SEEDS == SEEDS and ar_insider.INSIDER_GROUPS == INSIDER


def test_rank_gauss_matches_the_research_function() -> None:
    from tools.trial_pooled_rank import rank_gauss as research

    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"a": rng.normal(size=300), "b": np.round(rng.normal(size=300), 1)})
    frame.loc[::7, "a"] = np.nan
    ours = rank_gauss(frame, ["a", "b"])
    theirs = research(frame.assign(market="KR", session=SESSION), ["a", "b"])
    np.testing.assert_allclose(ours[["a", "b"]].to_numpy(), theirs[["a", "b"]].to_numpy(float), atol=1e-6)


# --------------------------------------------------------------------------- ② 피처 = 판정 패널 규칙


def _synthetic(monkeypatch, *, empty_flow: bool = False) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(1)
    names = [f"KR:{i:06d}" for i in range(80)]
    scores = pd.DataFrame({"entity_id": names, "session": SESSION})
    for c in ("chart", "event", "flow", "fundamental", "regime", "risk"):
        scores[c] = rng.normal(size=len(names)).astype(np.float32)
        scores.loc[rng.random(len(names)) < 0.1, c] = np.nan
    if empty_flow:
        scores["flow"] = np.nan
    priced = set(names[5:]) | {"KR:999999"}                    # 앞 다섯은 시세 없음(타깃 행 밖) · 점수 없는 시세 종목 하나
    g4 = pd.DataFrame({"entity_id": [*names[::3], "KR:999999"], "session": SESSION})
    g4["insider_sell_60"] = rng.exponential(size=len(g4))
    g4["insider_net_60"] = rng.normal(size=len(g4))
    monkeypatch.setattr(ar_insider.fa_features, "_scores", lambda store, m, s, a: scores.copy())
    monkeypatch.setattr(ar_insider, "priced_on", lambda store, m, s, a: priced)
    monkeypatch.setattr(ar_insider.fa_features, "_group",
                        lambda store, g, m, s: g4.copy() if g == "G4" else None)
    return {"scores": scores, "g4": g4, "priced": pd.DataFrame({"entity_id": sorted(priced)})}


def test_session_features_match_the_judge_panel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tools.trial_pooled_rank import rank_gauss as research_rg
    from tools.trial_ranker_sources import attach

    raw = _synthetic(monkeypatch)
    ours = ar_insider.session_features(None, "KR", SESSION, AS_OF)
    assert isinstance(ours, pd.DataFrame) and list(ours.columns) == list(ar_insider.FEATURES)
    # 판정 패널: 점수 ∩ 타깃 행 → 시장·세션 안 rank-gauss → is_us → attach(G4) → attach(G7, 0행)
    panel = raw["scores"][raw["scores"]["entity_id"].isin(set(raw["priced"]["entity_id"]))].copy()
    panel["market"] = "KR"
    panel = research_rg(panel, ["chart", "event", "flow", "fundamental", "regime", "risk"])
    panel["is_us"] = 0.0
    panel = attach(panel, raw["g4"], list(GROUPS["G4"]))
    panel = attach(panel, pd.DataFrame(columns=["entity_id", "session", *GROUPS["G7"]]), list(GROUPS["G7"]))
    want = panel.set_index("entity_id").loc[ours.index, list(ar_insider.FEATURES)].to_numpy(float)
    np.testing.assert_allclose(ours.to_numpy(float), want, atol=1e-5)
    assert "KR:999999" not in ours.index and "KR:000000" not in ours.index
    assert (ours[list(GROUPS["G7"])] == 0).all().all()


def test_session_features_refuse_an_empty_score_column(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _synthetic(monkeypatch, empty_flow=True)
    out = ar_insider.session_features(None, "KR", SESSION, AS_OF)
    assert isinstance(out, str) and "flow" in out


# --------------------------------------------------------------------------- ③④ 얼린 모델 · 끝에서 끝


@pytest.fixture
def models(tmp_path: Path, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    import lightgbm as lgb

    rng = np.random.default_rng(2)
    x = rng.normal(size=(3000, len(ar_insider.FEATURES))).astype(np.float32)
    y = (x[:, 0] - x[:, 6] + rng.normal(size=3000) * 0.1).astype(np.float32)
    folder = ar_insider.model_dir(tmp_path)
    folder.mkdir(parents=True)
    hashes = {}
    for seed in ar_insider.SEEDS:
        params = {"objective": "regression", "num_leaves": 7, "min_data_in_leaf": 50, "verbose": -1, "seed": seed}
        text = lgb.train(params, lgb.Dataset(x, y), num_boost_round=10).model_to_string()
        (folder / f"AR-treat-s{seed}.txt").write_text(text)
        hashes[f"AR-treat-s{seed}"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    monkeypatch.setattr(ar_insider, "MODEL_HASHES", hashes)
    return tmp_path


def test_model_refused_before_usable_from_or_on_bad_file(models: Path) -> None:
    model, reason = ar_insider.usable_ar_model(models, as_of=datetime(2026, 6, 30, 7, tzinfo=UTC))
    assert model is None and "usable_from" in reason
    model, reason = ar_insider.usable_ar_model(models, as_of=AS_OF)
    assert model is not None and reason == ""
    victim = ar_insider.model_dir(models) / "AR-treat-s1.txt"
    victim.write_text(victim.read_text() + "\n")
    model, reason = ar_insider.usable_ar_model(models, as_of=AS_OF)
    assert model is None and "지문 불일치" in reason
    victim.unlink()
    assert "파일 없음" in ar_insider.usable_ar_model(models, as_of=AS_OF)[1]


def test_analyst_end_to_end(store, models: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    _synthetic(monkeypatch)
    monkeypatch.setattr(ar_insider, "insider_staleness", lambda st, m, a: "")
    analyst = ar_insider.ArInsiderAnalyst(store, ReplayClock(AS_OF), market=Market.KR, models_root=models)
    signals = analyst.run(AS_OF, confidence=1.0)
    assert analyst.skip_reason == "" and signals
    assert {s.analyst for s in signals} == {"ar_insider"} and all(s.as_of == AS_OF for s in signals)
    # 시드 평균 백분위의 순위 = 점수 순위
    frame = ar_insider.session_features(store, "KR", SESSION, AS_OF)
    model, _ = ar_insider.usable_ar_model(models, as_of=AS_OF)
    assert model is not None
    from quant_rl_trading.analysts.be2 import percentile

    pred = pd.Series(np.mean([percentile(p) for p in model.seed_predictions(frame.to_numpy(np.float32)).values()], axis=0),
                     index=frame.index)
    got = pd.Series({s.entity_id: s.score for s in signals}).reindex(pred.index)
    assert got.rank().corr(pred.rank()) > 0.999


def test_analyst_skips_when_models_missing(store, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _synthetic(monkeypatch)
    analyst = ar_insider.ArInsiderAnalyst(store, ReplayClock(AS_OF), market=Market.KR, models_root=tmp_path)
    assert analyst.run(AS_OF) == [] and "얼린 AR 모델" in analyst.skip_reason
    us = ar_insider.ArInsiderAnalyst(store, ReplayClock(AS_OF), market=Market.US, models_root=tmp_path)
    assert us.run(AS_OF) == [] and "얼린 AR 모델" in us.skip_reason      # 미장도 같은 관문(시장 거부가 아니다)


def test_us_session_features_mark_is_us_and_zero_g4(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """미장: is_us = 1, G4(DART)는 미장 행이 없어 0, G7(Form 4)는 값이 있다 — 판정 패널(load_us → attach)과 같다."""
    rng = np.random.default_rng(4)
    names = [f"US:T{i:03d}" for i in range(60)]
    scores = pd.DataFrame({"entity_id": names, "session": SESSION})
    for c in ("chart", "event", "flow", "fundamental", "regime", "risk"):
        scores[c] = rng.normal(size=len(names)).astype(np.float32)
    g7 = pd.DataFrame({"entity_id": names[::2], "session": SESSION, "form4_sell_60": rng.exponential(size=30),
                       "form4_sellers_20": rng.integers(0, 3, size=30).astype(float), "form4_buy_60": rng.exponential(size=30)})
    monkeypatch.setattr(ar_insider.fa_features, "_scores", lambda store, m, s, a: scores.copy())
    monkeypatch.setattr(ar_insider, "priced_on", lambda store, m, s, a: set(names))
    monkeypatch.setattr(ar_insider.fa_features, "_group", lambda store, g, m, s: g7.copy() if g == "G7" else None)
    out = ar_insider.session_features(None, "US", SESSION, AS_OF)
    assert isinstance(out, pd.DataFrame)
    assert (out["is_us"] == 1.0).all()
    assert (out[list(GROUPS["G4"])] == 0).all().all()
    assert out["form4_sell_60"].std() > 0


# --------------------------------------------------------------------------- ⑤ 설치 도구


def test_install_copies_only_matching_files(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tools import install_ar_shadow_models as tool

    source = tmp_path / "src"
    source.mkdir()
    hashes = {}
    for seed in ar_insider.SEEDS:
        text = f"model {seed}\n"
        (source / f"AR-treat-s{seed}.txt").write_text(text)
        hashes[f"AR-treat-s{seed}"] = hashlib.sha256(text.encode()).hexdigest()[:16]
    monkeypatch.setattr(tool, "MODEL_HASHES", hashes)
    root = tmp_path / "data"
    assert tool.install(source, root, apply=False) == 0 and not ar_insider.model_dir(root).exists()
    assert tool.install(source, root, apply=True) == 0
    assert all((ar_insider.model_dir(root) / f"AR-treat-s{s}.txt").exists() for s in ar_insider.SEEDS)
    assert tool.install(source, root, apply=True) == 0                     # 이미 같은 파일 — 그대로 통과
    (ar_insider.model_dir(root) / "AR-treat-s0.txt").write_text("other\n")
    assert tool.install(source, root, apply=True) == 3                     # 자리에 다른 파일 — 덮지 않는다
    (source / "AR-treat-s1.txt").write_text("tampered\n")
    assert tool.install(source, tmp_path / "fresh", apply=True) == 2       # 원본 지문 불일치


def test_fund_usd_once(store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """미장 shadow 첫 자본은 달러 한 번 — 두 번째는 넣지 않는다(입금일 NAV 가 수익으로 잡힌다)."""
    from tools import fund_shadow_usd as tool

    moment = datetime(2026, 10, 13, 20, 20, tzinfo=UTC)
    monkeypatch.setattr(tool, "opening_moment", lambda st, m, now: moment)
    now = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    assert tool.fund(store, usd=370000.0, now=now, apply=False) == 0
    assert store.get("capital_flows", as_of=now, lookback=3650).empty
    assert tool.fund(store, usd=370000.0, now=now, apply=True) == 0
    flows = store.get("capital_flows", as_of=now, lookback=3650)
    assert len(flows) == 1 and flows.iloc[0]["currency"] == "USD" and float(flows.iloc[0]["amount"]) == 370000.0
    assert tool.fund(store, usd=370000.0, now=now, apply=True) == 3
    assert tool.main(["--sandbox", "data/_paper", "--usd", "1"]) == 2


def test_analyst_refuses_stale_insider_data(store, models: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """내부자 원자료 수집이 멈췄으면(최근 관측 0행) 채점하지 않는다 — 0 으로 빈 내부자 열로 조용히 돌지 않는다."""
    _synthetic(monkeypatch)
    assert "수집이 멈췄다" in ar_insider.insider_staleness(store, "KR", AS_OF)       # 빈 창고 = 최근 관측 없음
    analyst = ar_insider.ArInsiderAnalyst(store, ReplayClock(AS_OF), market=Market.KR, models_root=models)
    assert analyst.run(AS_OF) == [] and "insider_trades" in analyst.skip_reason
    us = ar_insider.ArInsiderAnalyst(store, ReplayClock(AS_OF), market=Market.US, models_root=models)
    assert us.run(AS_OF) == [] and "form4_trades" in us.skip_reason
