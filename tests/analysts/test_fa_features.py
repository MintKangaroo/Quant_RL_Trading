"""FA 76열 매일 경로 — 판정 패널 규칙과 같은가, 미래를 안 보는가.

지키는 것:
 ① 열 정의 — `schemas/fa` 상수가 묶음 정의(ranker_sources·valueup)·kit `feature_names` 와 한 글자도 다르지 않다.
 ② 정규화 — `fa_features.attach`·`finalize` 가 kit 의 `attach_block`·`finalize`(+ load_full_panel 뒷정리)와 **정확히** 같다.
 ③ 누수 없음 — 창고 조회는 전부 세션 as_of 이하로 가고, as_of 뒤에 관측된 행을 넣어도 결과가 안 바뀐다.
 ④ 행 집합·표지 — 점수 종목 ∪ 시세 종목, 판정 때 자료 없던 국장 G3·G6·G7 은 표지 1.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts import fa_features, ranker_sources, valueup
from quant_rl_trading.schemas import fa

SESSION = date(2026, 7, 15)
AS_OF = datetime(2026, 7, 15, 7, 0, tzinfo=UTC)          # 16:00 KST — 세션 공표 시각
LATER = AS_OF + timedelta(days=2)                        # 미래 관측


# ① 열 정의 ------------------------------------------------------------------------------------


def test_group_columns_match_registered_definitions() -> None:
    for name, cols in fa.GROUP_COLUMNS.items():
        assert tuple(ranker_sources.GROUPS[name]) == cols, name
    assert list(fa.BA_COLUMNS) == list(valueup.NEW)
    assert list(fa.BA_ZERO_FIRST) == list(valueup.KINDS)
    assert len(fa.FA_FEATURES) == 76 and len(set(fa.FA_FEATURES)) == 76


def test_fa_features_equal_kit_feature_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """kit 은 원피처 이름을 캐시 헤더에서 읽는다 — 헤더 대신 시장별 이름을 넣고 나머지 규칙은 진짜 kit 을 돌린다."""
    from tools import final_round_kit as kit

    by_market = {
        "KR": [c for c in fa.RAW_COLUMNS if not c.startswith("raw_flow_us_")],
        "US": [c for c in fa.RAW_COLUMNS if not c.startswith("raw_flow_kr_")],
    }
    monkeypatch.setattr(kit, "_raw_cols", lambda market: list(by_market[market]))
    assert kit.feature_names(kit.blocks_of(("KR", "US"))) == list(fa.FA_FEATURES)


def test_valueup_tool_reexports_package_function() -> None:
    from tools import trial_kr_valueup as tool

    assert tool.features is valueup.features
    assert tool.KINDS is valueup.KINDS and tool.NEW is valueup.NEW


# ② 정규화 = kit ---------------------------------------------------------------------------------


def _raw_panel(rng: np.random.Generator, n: int = 120) -> tuple[pd.DataFrame, dict[str, pd.DataFrame | None]]:
    """국장 한 세션 — 점수(결측·동점 포함) + 묶음 원값(일부 종목만) + BA 개수."""
    ent = [f"KR:{i:06d}" for i in range(n)]
    panel = pd.DataFrame({"entity_id": ent, "session": SESSION, "market": "KR"})
    for c in fa.SCORE_COLUMNS:
        v = rng.normal(size=n).round(1).astype(np.float32)          # 반올림 → 동점
        v[rng.random(n) < 0.15] = np.nan
        panel[c] = v
    panel["y5"] = rng.normal(size=n).astype(np.float32)
    frames: dict[str, pd.DataFrame | None] = {}
    take = rng.random(n) < 0.8
    raw = pd.DataFrame({"entity_id": np.array(ent)[take], "session": SESSION})
    for c in fa.RAW_COLUMNS:
        if c.startswith("raw_flow_us_"):
            continue
        raw[c] = rng.normal(size=int(take.sum())).astype(np.float32)
    frames["raw"] = raw
    for g in ("G1", "G2", "G4", "G5"):
        pick = rng.random(n) < 0.6
        f = pd.DataFrame({"entity_id": np.array(ent)[pick], "session": SESSION})
        for c in fa.GROUP_COLUMNS[g]:
            f[c] = rng.normal(size=int(pick.sum())).astype(np.float32)
        frames[g] = f
    for g in ("G3", "G6", "G7"):
        frames[g] = None
    pick = rng.random(n) < 0.4
    ba = pd.DataFrame({"entity_id": np.array(ent)[pick], "session": SESSION})
    for c in fa.BA_ZERO_FIRST:
        ba[c] = rng.integers(0, 3, int(pick.sum())).astype(np.float32)
    ba["vu_plan_age"] = np.where(ba["vu_plan"] > 0, rng.uniform(0, 365, int(pick.sum())), np.nan).astype(np.float32)
    frames["ba"] = ba
    return panel, frames


def test_attach_and_finalize_match_kit_exactly() -> None:
    from tools import final_round_kit as kit

    rng = np.random.default_rng(3)
    panel, frames = _raw_panel(rng)
    groups = {name: kit.Group(name, fa.BLOCK_COLUMNS[name], fa.FLAG_OF.get(name), fa.BLOCK_MARKETS[name],
                              zero_first=(fa.BA_ZERO_FIRST if name == "ba" else ()))
              for name in fa.BLOCK_ORDER}
    ours, theirs = panel.copy(), panel.copy()
    for name in fa.BLOCK_ORDER:
        if name == "score":
            continue
        ours = fa_features.attach(ours, frames[name], name)
        theirs = kit.attach_block(theirs, frames[name] if frames[name] is not None else pd.DataFrame(), groups[name])
    theirs, feats = kit.finalize(theirs, groups)
    ours = fa_features.finalize(ours)
    assert feats == list(fa.FA_FEATURES)
    expected = theirs.sort_values("entity_id").reset_index(drop=True)[list(fa.FA_FEATURES)]
    got = ours.sort_values("entity_id").reset_index(drop=True)[list(fa.FA_FEATURES)]
    pd.testing.assert_frame_equal(got, expected.astype(np.float32), check_exact=True)
    assert not got.isna().any().any()
    assert (got["miss_g3"] == 1.0).all() and (got["is_us"] == 0.0).all()


def test_rank_gauss_matches_research_function() -> None:
    from tools.trial_pooled_rank import rank_gauss as research

    rng = np.random.default_rng(5)
    frame = pd.DataFrame({"market": "KR", "session": SESSION, "x": rng.normal(size=50).round(1).astype(np.float32)})
    frame.loc[3, "x"] = np.nan
    pd.testing.assert_frame_equal(fa_features.rank_gauss(frame, ["x"]), research(frame, ["x"]), check_exact=True)


# ③④ 매일 경로 — 누수·행 집합 -------------------------------------------------------------------


class _FakeAnalyst:
    """원피처 대역 — 창고 `prices` 를 as_of 로 읽어 종가 두 개로 피처를 만든다(진짜 Analyst 처럼 게이트를 지난다)."""

    def __init__(self, store, clock, *, market) -> None:  # type: ignore[no-untyped-def]
        self.store, self.clock, self.market = store, clock, market

    def features(self, as_of: datetime) -> pd.DataFrame:
        from quant_rl_trading.store.prices import read_prices

        prices = read_prices(self.store, as_of=as_of, lookback=10, market="KR", columns=["close"])
        if prices.empty:
            return pd.DataFrame()
        last = prices.sort_values("valid_from").groupby("entity_id")["close"].last()
        return pd.DataFrame({"ma_gap": last / 100.0, "momentum_20": -last})


def _signals(store, as_of: datetime, observed: datetime, entities: list[str], *, bump: float = 0.0, tag: str) -> None:  # type: ignore[no-untyped-def]
    rows = []
    for i, e in enumerate(entities):
        for analyst in ("chart", "event", "flow_kr", "fundamental", "regime", "risk"):
            rows.append({"entity_id": e, "valid_from": as_of, "observed_at": observed, "source": "test",
                         "analyst": analyst, "analyst_version": f"{analyst}-t", "score": ((i * 7) % 11) / 11 - 0.5 + bump,
                         "confidence": 1.0, "horizon_days": 5, "features_hash": "x", "evidence_json": "[]",
                         "latency_ms": 0.0})
    store.append("signals", rows, ingest_run_id=tag)


def _prices(store, day: date, observed: datetime, entities: list[str], *, tag: str, scale: float = 1.0) -> None:  # type: ignore[no-untyped-def]
    moment = datetime(day.year, day.month, day.day, 6, 30, tzinfo=UTC)
    store.append("prices", [{
        "entity_id": e, "valid_from": moment, "observed_at": observed, "source": "test", "market": "KR",
        "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": (100.0 + i) * scale, "volume": 1e5,
        "value": 1e7, "adj_factor": None,
    } for i, e in enumerate(entities)], ingest_run_id=tag)


def _docs(store, entity: str, valid: datetime, observed: datetime, title: str, tag: str) -> None:  # type: ignore[no-untyped-def]
    store.append("documents", [{
        "entity_id": entity, "valid_from": valid, "observed_at": observed, "source": "test", "doc_id": tag,
        "doc_type": "dart", "title": title, "filer": "x", "url": "", "raw_path": "",
    }], ingest_run_id=tag)


@pytest.fixture
def seeded(store, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """세션 하나 분량의 창고 + 원피처·G 묶음 대역(모두 창고를 as_of 로 읽는다)."""
    store.seed_config_defaults()
    scored = [f"KR:{i:06d}" for i in range(60)]
    priced_only = ["KR:900001", "KR:900002"]
    _signals(store, AS_OF, AS_OF, scored, tag="sig-today")
    _prices(store, SESSION, AS_OF - timedelta(minutes=10), [*scored, *priced_only], tag="px-today")
    _docs(store, "KR:000003", datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 5, 1, 9, tzinfo=UTC),
          "현금ㆍ현물배당결정", "doc-past")
    monkeypatch.setattr(fa_features, "ANALYST_CLASSES", {name: _FakeAnalyst for name in fa_features.ANALYST_CLASSES})
    calls: list[datetime] = []

    def fake_build(group, analyst, as_of):  # type: ignore[no-untyped-def]
        calls.append(as_of)
        prices = analyst.store.get("prices", as_of=as_of, lookback=10, market="KR",  # invariant-allow: price-read — 대역
                                   columns=["close"])
        if prices.empty:
            return pd.DataFrame(columns=list(ranker_sources.GROUPS[group]))
        last = prices.sort_values("valid_from").groupby("entity_id")["close"].last()
        return pd.DataFrame({c: last * (k + 1) for k, c in enumerate(ranker_sources.GROUPS[group])})

    monkeypatch.setattr(ranker_sources, "build", fake_build)
    return store, scored, priced_only, calls


def test_build_session_rows_flags_and_shape(seeded) -> None:  # type: ignore[no-untyped-def]
    store, scored, priced_only, calls = seeded
    frame = fa_features.build_session(store, "KR", SESSION, AS_OF)
    assert set(frame["entity_id"]) == set(scored) | set(priced_only)        # 점수 종목 ∪ 시세 종목
    assert list(frame.columns) == ["entity_id", "session", "market", *fa.FA_FEATURES]
    assert not frame[list(fa.FA_FEATURES)].isna().any().any()
    for g in ("g3", "g6", "g7"):                                             # 판정 때 국장 자료가 없던 묶음
        assert (frame[f"miss_{g}"] == 1.0).all()
    ba = frame.set_index("entity_id")["miss_ba"]
    assert ba["KR:000003"] == 0.0 and ba["KR:000004"] == 1.0
    only = frame[frame["entity_id"].isin(priced_only)]
    assert (only[list(fa.SCORE_COLUMNS)] == 0.0).all().all()                 # 점수 없는 종목 = 순위 중앙
    # G 묶음은 세션 **개장** 시각(국장 00:00 UTC)으로 부른다 — 판정 월 조각과 같은 as_of.
    assert calls and all(c == datetime(2026, 7, 15, tzinfo=UTC) for c in calls)


def test_future_rows_do_not_change_the_session(seeded) -> None:  # type: ignore[no-untyped-def]
    store, scored, _priced_only, _calls = seeded
    before = fa_features.build_session(store, "KR", SESSION, AS_OF)
    # as_of 뒤에 관측된 것들: 같은 세션 점수의 정정본, 다음 세션 시세, 오늘 날짜의 늦은 공시.
    _signals(store, AS_OF, LATER, scored, bump=0.3, tag="sig-late-revision")
    _prices(store, SESSION + timedelta(days=1), LATER, scored, tag="px-tomorrow", scale=3.0)
    _prices(store, SESSION, LATER, scored, tag="px-late-correction", scale=0.5)
    _docs(store, "KR:000004", datetime(2026, 7, 14, tzinfo=UTC), LATER, "주식소각결정", "doc-late")
    after = fa_features.build_session(store, "KR", SESSION, AS_OF)
    pd.testing.assert_frame_equal(before, after, check_exact=True)
    # 같은 창고를 늦은 as_of 로 읽으면 달라진다 — 위 동일성이 "데이터가 안 보여서" 가 아님을 역검증.
    later = fa_features.build_session(store, "KR", SESSION, LATER)
    assert not later[list(fa.FA_FEATURES)].equals(before[list(fa.FA_FEATURES)])


def test_every_store_read_is_at_or_before_as_of(seeded, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    store, *_ = seeded
    seen: list[datetime] = []
    real_get = type(store).get

    def spy(self, table, *, as_of, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(as_of)
        return real_get(self, table, as_of=as_of, **kwargs)

    monkeypatch.setattr(type(store), "get", spy)
    fa_features.build_session(store, "KR", SESSION, AS_OF)
    assert seen and max(seen) <= AS_OF


def test_write_and_read_window_roundtrip(seeded) -> None:  # type: ignore[no-untyped-def]
    store, *_ = seeded
    frame = fa_features.build_session(store, "KR", SESSION, AS_OF)
    assert fa_features.write_session(store, frame, market="KR", session=SESSION, as_of=AS_OF) == len(frame)
    assert fa_features.write_session(store, frame, market="KR", session=SESSION, as_of=AS_OF) == 0   # 두 번 안 쓴다
    back = fa_features.read_window(store, as_of=AS_OF, market="KR", lookback_days=5)
    assert fa_features.read_window(store, as_of=AS_OF - timedelta(seconds=1), market="KR", lookback_days=5).empty
    back = back.sort_values("entity_id").reset_index(drop=True)
    mine = frame.sort_values("entity_id").reset_index(drop=True)
    assert (back["session"] == SESSION).all()
    np.testing.assert_array_equal(back[list(fa.FA_FEATURES)].to_numpy(), mine[list(fa.FA_FEATURES)].to_numpy())
