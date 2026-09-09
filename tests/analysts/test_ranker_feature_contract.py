"""Training X is the actual decision-time feature panel, independent of future y."""

from datetime import UTC, datetime, timedelta

import pandas as pd
from pandas.testing import assert_frame_equal

from quant_rl_trading.analysts.ranker import SCORE_FEATURES, RankerAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock
from tools import train_ranker

NOW = datetime(2026, 6, 4, 7, tzinfo=UTC)


def seed_inputs(store):
    signals, universe = [], []
    for i, entity in enumerate(["KR:A", "KR:B", "KR:C", "KR:OUT"]):
        universe.append(
            {
                "entity_id": entity,
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "market": "KR",
                "name": entity,
                "is_listed": True,
                "is_tradable": entity != "KR:OUT",
                "delisted_on": None,
            }
        )
        for feature in SCORE_FEATURES:
            analyst = "flow_kr" if feature == "flow" else feature
            signals.append(
                {
                    "entity_id": entity,
                    "valid_from": NOW,
                    "observed_at": NOW,
                    "source": "test",
                    "analyst": analyst,
                    "analyst_version": "test",
                    "score": float(i),
                    "confidence": 1.0,
                    "horizon_days": 5,
                    "features_hash": "test",
                    "evidence_json": "[]",
                    "latency_ms": 0.0,
                }
            )
    store.append("universe", universe, ingest_run_id="eligible")
    store.append("signals", signals, ingest_run_id="signals")
    return signals


def label_frame(entities):
    return pd.DataFrame(
        {
            "entity_id": entities,
            "session": [NOW.date()] * len(entities),
            "target": [float(i) / 100 for i in range(len(entities))],
        }
    )


def test_labels_do_not_change_feature_normalization(store, monkeypatch):
    seed_inputs(store)
    labels = label_frame(["KR:A", "KR:B", "KR:C"])
    monkeypatch.setattr(train_ranker.ic, "build_targets", lambda *a, **kw: labels)
    full = train_ranker.build_frame(store, through=NOW.date())
    labels = label_frame(["KR:A", "KR:B"])
    reduced = train_ranker.build_frame(store, through=NOW.date())
    live = RankerAnalyst(store, ReplayClock(NOW), market=Market.KR).input_features(NOW)
    columns = list(SCORE_FEATURES)
    assert list(live.index) == ["KR:A", "KR:B", "KR:C"]
    expected = live.loc[["KR:A", "KR:B"], columns]
    assert_frame_equal(full.set_index("entity_id").loc[expected.index, columns], expected)
    assert_frame_equal(reduced.set_index("entity_id").loc[expected.index, columns], expected)


def test_late_signal_revision_does_not_rewrite_training_state(store):
    signals = seed_inputs(store)
    through = NOW + timedelta(days=5)
    before = train_ranker.load_signals(store, as_of=through, market=Market.KR)
    correction = dict(signals[0], observed_at=NOW + timedelta(days=1), score=100.0, revision=1)
    store.append("signals", [correction], ingest_run_id="late-correction")
    after = train_ranker.load_signals(store, as_of=through, market=Market.KR)
    assert_frame_equal(before, after)
