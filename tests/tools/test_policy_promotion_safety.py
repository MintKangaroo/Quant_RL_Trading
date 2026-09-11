"""Legacy reward/equal reports must never authorize changes to the operating policy."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.store import Store
from tools import promote_policy, promotion_gate

NOW = datetime(2026, 9, 9, 9, tzinfo=UTC)


def _config(store: Store, *, at: datetime, checkpoint: str = "") -> None:
    store.append("config", [
        {
            "entity_id": key, "valid_from": at, "observed_at": at,
            "source": "test", "revision": 0, "value_json": json.dumps(value),
        }
        for key, value in (
            (promote_policy.CHECKPOINT_KEY, checkpoint),
            (promote_policy.MODES_KEY, ["paper"]),
            ("allocator.action_reflection_floor", 0.3),
        )
    ], ingest_run_id="initial-config")


@pytest.mark.parametrize("modes", [["paper"], ["shadow"], ["live"], ["paper", "live"]])
def test_direct_activation_cannot_bypass_gate(store: Store, modes: list[str]) -> None:
    _config(store, at=NOW - timedelta(days=1))
    with pytest.raises(ValueError, match="승격 미지원"):
        promote_policy.write_config(store, checkpoint="candidate.pt", modes=modes, now=NOW)
    assert store.config(promote_policy.CHECKPOINT_KEY, as_of=NOW) == ""
    assert store.config(promote_policy.MODES_KEY, as_of=NOW) == ["paper"]


def test_off_preserves_historical_policy(store: Store) -> None:
    before = NOW - timedelta(days=1)
    _config(store, at=before, checkpoint="old-policy.pt")
    promote_policy.write_config(store, checkpoint="", modes=["paper"], now=NOW)
    assert store.config(promote_policy.CHECKPOINT_KEY, as_of=before) == "old-policy.pt"
    assert store.config(promote_policy.CHECKPOINT_KEY, as_of=NOW) == ""


@pytest.mark.parametrize("window", ["oos", "valid"])
def test_favorable_legacy_evaluation_is_not_promotion_evidence(
    store: Store, window: str, capsys: pytest.CaptureFixture[str],
) -> None:
    now = LiveClock().now()
    _config(store, at=now - timedelta(days=1))
    store.append("rl_evaluations", [{
        "entity_id": "old-run", "valid_from": now, "observed_at": now,
        "source": "evaluate_policy", "revision": 0,
        "eval_window": window, "arm": "policy", "verdict": "generalizes",
        "gap_vs_equal": 0.01, "action_reflection": 1.0,
        "drawdown": 0.01, "cost": 0.001, "turnover": 0.01,
        "checkpoint": "old-policy.pt", "train_seed": 0,
    }], ingest_run_id="old-evaluation")
    assert promotion_gate.main([
        "--root", str(store.root), "--run", "old-run", "--window", window,
    ]) == 2
    assert "승격 미지원" in capsys.readouterr().out


def test_cli_blocks_activation_before_loading_artifact_or_opening_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"must not deserialize this file")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Activation must be rejected before opening a store")

    monkeypatch.setattr(promote_policy, "build_store", unexpected)
    monkeypatch.setattr(sys, "argv", ["promote_policy", "--checkpoint", str(checkpoint)])
    assert promote_policy.main() == 2
    assert "승격 미지원" in capsys.readouterr().err


def test_cli_off_does_not_need_a_checkpoint_or_evaluation(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(store, at=NOW - timedelta(days=1), checkpoint="missing-old-policy.pt")
    monkeypatch.setattr(promote_policy.LiveClock, "now", lambda self: NOW)
    monkeypatch.setattr(promote_policy, "load_env", lambda: None)
    monkeypatch.setattr(promote_policy, "build_store", lambda *args: store)
    monkeypatch.setattr(sys, "argv", ["promote_policy", "--off"])
    assert promote_policy.main() == 0
    assert store.config(promote_policy.CHECKPOINT_KEY, as_of=NOW) == ""


def test_off_and_dry_run_cannot_report_a_successful_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Conflicting command must fail before evaluating an empty checkpoint")

    monkeypatch.setattr(promote_policy, "dry_run", unexpected)
    monkeypatch.setattr(sys, "argv", ["promote_policy", "--off", "--dry-run"])
    with pytest.raises(SystemExit) as error:
        promote_policy.main()
    assert error.value.code == 2


def test_dry_run_remains_available_without_writing_config(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quant_rl_trading.allocator import live
    from quant_rl_trading.allocator.env import EnvParams

    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test artifact, loader is stubbed")
    _config(store, at=NOW - timedelta(days=1))
    monkeypatch.setattr(promote_policy.LiveClock, "now", lambda self: NOW)
    monkeypatch.setattr(promote_policy, "load_env", lambda: None)
    monkeypatch.setattr(promote_policy, "build_store", lambda *args: store)
    monkeypatch.setattr(EnvParams, "from_store", lambda *args, **kwargs: object())
    monkeypatch.setattr(live, "load_policy", lambda *args: (object(), 1, {}))
    calls: list[str] = []

    def preview(root: Path, *, checkpoint: str, market: str, now: datetime) -> int:
        calls.append(checkpoint)
        return 0

    monkeypatch.setattr(promote_policy, "dry_run", preview)
    monkeypatch.setattr(sys, "argv", [
        "promote_policy", "--checkpoint", str(checkpoint), "--dry-run",
    ])
    assert promote_policy.main() == 0
    assert calls == [str(checkpoint)]
    assert store.config(promote_policy.CHECKPOINT_KEY, as_of=NOW) == ""


@pytest.mark.parametrize("name", ["chain_20260830.sh", "chain_r7_full.sh", "chain_drl_r4.sh"])
def test_archived_chain_stops_before_work_or_creating_logs(tmp_path: Path, name: str) -> None:
    # Run a copy without dependencies or warehouse access, even if the guard regresses.
    source = Path(__file__).resolve().parents[2] / "scripts" / name
    script = tmp_path / "scripts" / name
    script.parent.mkdir()
    script.write_text(source.read_text())
    before = set(tmp_path.rglob("*"))
    result = subprocess.run(
        ["/bin/bash", str(script)], cwd=tmp_path,
        env={"PATH": str(tmp_path / "no-external-commands")},
        capture_output=True, text=True, timeout=3, check=False,
    )
    assert result.returncode == 2
    assert "보관된 실험 체인" in result.stderr
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("interrupted_log", [False, True])
def test_supervisor_does_not_resurrect_archived_rl_chains(
    tmp_path: Path, interrupted_log: bool,
) -> None:
    source = Path(__file__).resolve().parents[2] / "scripts" / "job_supervisor.sh"
    script = tmp_path / "scripts" / source.name
    script.parent.mkdir()
    # Wait only joins the stub children so their call log is complete before asserting.
    script.write_text(source.read_text() + "\nwait\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    if interrupted_log:
        (logs / "chain-r7.log").write_text("started, no completion marker\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in (
        ("pgrep", "exit 1\n"),
        ("setsid", 'printf "%s\\n" "$*" >> "$RECORD"\n'),
    ):
        stub = fake_bin / name
        stub.write_text("#!/bin/bash\n" + body)
        stub.chmod(0o755)
    record = tmp_path / "calls.txt"
    subprocess.run(
        ["/bin/bash", str(script)], cwd=tmp_path,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "RECORD": str(record)},
        capture_output=True, text=True, timeout=5, check=True,
    )
    calls = record.read_text()
    assert "chain_r7_full.sh" not in calls
    assert "chain_20260830.sh" not in calls
    assert "compare_baselines_overnight.py" in calls
