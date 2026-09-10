"""Two independent processes must not both acquire one durable submission claim."""

import multiprocessing
from datetime import UTC, datetime

from quant_rl_trading.store import DuplicateIngestRun, Store


def _claim(root, start, outcomes):
    store = Store(root=root)
    moment = datetime(2026, 9, 9, tzinfo=UTC)
    start.wait(timeout=10)
    try:
        store.append(
            "orders",
            [
                {
                    "entity_id": "KR:A",
                    "valid_from": moment,
                    "observed_at": moment,
                    "source": "test",
                    "market": "KR",
                    "session_id": "KR-2026-09-09",
                    "slice_seq": 0,
                    "side": "buy",
                    "quantity": 1.0,
                    "limit_price": 1000.0,
                    "target_weight": 0.1,
                    "status": "submitting",
                    "reason": "",
                }
            ],
            ingest_run_id="submit-one-intent",
        )
        outcomes.put("claimed")
    except DuplicateIngestRun:
        outcomes.put("duplicate")


def test_submission_claim_is_atomic_between_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    start, outcomes = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_claim, args=(tmp_path, start, outcomes)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        assert sorted(outcomes.get(timeout=2) for _ in processes) == ["claimed", "duplicate"]
        assert Store(root=tmp_path).ingest_run_recorded("orders", "submit-one-intent")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
