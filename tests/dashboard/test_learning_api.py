"""학습 탭 API.

이 화면의 존재 이유는 두 개다.

1. **없는 것을 지어내지 않는다.** RL(M4)은 아직 없다 — ``/status`` 가 그
   사실을 명시적으로 돌려줘야 하고, 값이 0 이나 가짜 숫자로 채워지면 안 된다.
2. **있는 것은 정확히 보여준다.** Analyst IC 게이트는 Agent Health 화면과
   같은 사실이어야 한다 — 계산을 두 번 하면 언젠가 두 화면이 어긋난다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask

from quant_rl_trading.dashboard.api import learning as learning_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.dashboard.services.agent_health import PLANNED
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 14, tzinfo=UTC)
MEASURED_AT = NOW - timedelta(days=1)


def weight_row(analyst: str, ic: float, passed: bool, sample_days: int) -> dict[str, Any]:
    return {
        "entity_id": analyst,
        "valid_from": MEASURED_AT,
        "observed_at": MEASURED_AT,
        "source": "ic-measure",
        "analyst_version": f"{analyst}-v0.1.0",
        "weight": 1.0 if passed else 0.0,
        "ic": ic,
        "ic_threshold": 0.03,
        "sample_days": sample_days,
        "passed": passed,
        "market": "KR",
    }


def make_app(store: Any, clock: Any) -> Flask:
    """app.py 를 건드리지 않고 이 탭의 블루프린트만 단 최소 앱.

    실제 배선(app.py 에 register_blueprint)은 별도로 이뤄진다 — 여기서는
    API 자체의 계약만 검증한다.
    """
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = clock
    app.json.ensure_ascii = False  # type: ignore[attr-defined]
    app.register_blueprint(learning_api.bp)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append(
        "analyst_weights",
        [
            weight_row("risk", 0.0777, True, 300),
            weight_row("chart", 0.0014, False, 300),
        ],
        ingest_run_id="ic-seed",
    )
    return store


@pytest.fixture
def client(seeded):  # type: ignore[no-untyped-def]
    return make_app(seeded, ReplayClock(NOW)).test_client()


def body(response: Any) -> dict[str, Any]:
    return response.get_json()  # type: ignore[no-any-return]


# -- 없는 것은 지어내지 않는다 ---------------------------------------------------


def test_m4_status_says_it_is_not_active(client) -> None:
    """RL(M4)은 아직 없다. 화면이 그 사실을 명시적으로 말해야 한다."""
    data = body(client.get("/api/learning/status"))["data"]

    assert data["active"] is False
    assert data["milestone"] == "M4"
    assert "M4" in data["note"]
    # 자리는 지키되(§5 의 위젯 6개) 값은 없다.
    assert len(data["widgets"]) == 6
    assert all("key" in w and "label" in w for w in data["widgets"])


def test_status_accepts_as_of_even_though_answer_is_static(client) -> None:
    """불변식 9 — 답이 시점에 안 달라져도 파라미터는 받아야 한다."""
    past = (MEASURED_AT - timedelta(days=100)).isoformat()
    response = client.get(f"/api/learning/status?as_of={past}")

    assert response.status_code == 200
    assert body(response)["as_of"] == past


# -- 있는 것은 정확히 보여준다 ---------------------------------------------------


def test_gate_matches_agent_health_facts(client) -> None:
    """가중치 게이트는 agent_health 와 같은 사실이어야 한다."""
    data = body(client.get("/api/learning/gate"))["data"]

    assert data["total"] == len(PLANNED)
    assert data["active"] == ["risk"]
    assert data["active_count"] == 1
    assert data["measured_count"] == 2
    assert data["active_weight"] == 1.0

    roster = {item["analyst"]: item for item in data["roster"]}
    assert roster["risk"]["passed"] is True
    assert roster["chart"]["passed"] is False
    assert roster["flow_us"]["measured"] is False


def test_ic_history_carries_threshold_from_config(client) -> None:
    """합격선은 코드가 아니라 store.config 에서 온다 (불변식 10)."""
    data = body(client.get("/api/learning/ic-history"))["data"]

    assert data["threshold"] == pytest.approx(0.03)
    assert data["points"] == 2
    analysts = {series["analyst"] for series in data["series"]}
    assert analysts == {"risk", "chart"}


def test_ic_history_is_empty_not_fabricated_when_nothing_measured(seeded) -> None:
    """측정이 없으면 빈 배열이지 0 으로 채운 계열이 아니다."""
    empty_store_app = make_app(seeded, ReplayClock(MEASURED_AT - timedelta(days=200)))
    client = empty_store_app.test_client()

    data = body(client.get("/api/learning/ic-history"))["data"]

    assert data["series"] == []
    assert data["points"] == 0


def test_every_route_returns_the_as_of_it_was_given(client) -> None:
    """불변식 9 — 이 탭의 모든 GET 라우트가 as_of 를 받고 되돌려준다."""
    past = MEASURED_AT.isoformat()
    paths = (
        "/api/learning/status",
        "/api/learning/gate",
        "/api/learning/ic-history",
        "/api/learning/walk-forward",
    )
    for path in paths:
        response = client.get(f"{path}?as_of={past}")
        assert response.status_code == 200, path
        assert body(response)["as_of"] == past, path


# -- 워크포워드(샌드박스) 대 라이브(실전 창고) 비교 -------------------------------


def test_walk_forward_compares_fixed_snapshot_against_live_store(client) -> None:
    """워크포워드 값은 코드에 고정된 과거 측정, 라이브 값만 창고에서 읽는다."""
    data = body(client.get("/api/learning/walk-forward"))["data"]

    assert data["measured_at"] == "2026-01-02"
    assert "data/_backtest" in data["source"]
    assert data["threshold"] == pytest.approx(0.03)

    rows = {row["analyst"]: row for row in data["rows"]}
    # risk 는 워크포워드·라이브 둘 다 측정돼 있다 (seeded 픽스처).
    assert rows["risk"]["wf_passed"] is True
    assert rows["risk"]["live_measured"] is True
    expected_delta = rows["risk"]["live_ic"] - rows["risk"]["wf_ic"]
    assert rows["risk"]["delta_ic"] == pytest.approx(expected_delta)

    # fundamental 은 워크포워드엔 있지만 seeded 픽스처엔 측정 기록이 없다 —
    # 지어내지 않고 "미측정" 을 그대로 돌려준다.
    assert rows["fundamental"]["live_measured"] is False
    assert rows["fundamental"]["live_ic"] is None
    assert rows["fundamental"]["delta_ic"] is None


# -- 학습 지표 (M4) ----------------------------------------------------------
#
# **픽스처가 새 경로를 밟게 한다.** 이 저장소에서 두 번, 테스트는 전부
# 통과하는데 화면만 깨진 적이 있다 — 픽스처에 새 필드가 없어서 새로 만든
# 분기를 아무 테스트도 지나가지 않았기 때문이다. 그래서 여기서는 0행일
# 때와 기록이 있을 때를 **둘 다** 밟는다.


def _update_row(run_id: str, update: int, ev: float) -> dict[str, Any]:
    return {
        "entity_id": run_id,
        "valid_from": NOW,
        "observed_at": NOW,
        "source": "ppo",
        "update": update,
        "step": update * 2048,
        "seed": 7,
        "market": "KR",
        "curriculum": "C1",
        "explained_variance": ev,
        "approx_kl": 0.015,
        "entropy": 1.2,
        "grad_norm": 0.8,
        "action_reflection": 0.42,
        "policy_churn": 0.11,
        "concentration_sum": 30.0,
        "episode_reward": 0.03,
        "cash_weight": 0.59,
        "git_commit": "abc1234",
        "config_fingerprint": "fp",
    }


def test_학습_기록이_없으면_없다고_말한다(client) -> None:
    """0행과 '쟀는데 0' 은 다른 사실이다 (불변식 3)."""
    data = body(client.get("/api/learning/training-runs"))["data"]

    assert data["has_data"] is False
    assert data["runs"] == []
    # 경고선은 기록이 없어도 온다 — 화면이 축을 그릴 수 있어야 한다.
    assert data["guards"]["explained_variance"]["floor"] == 0.1


def test_학습_기록이_있으면_곡선을_돌려준다(seeded) -> None:
    seeded.append(
        "rl_updates",
        [_update_row("rl-20260819-a", i, 0.05 * i) for i in range(1, 4)],
        ingest_run_id="rl-seed",
    )
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    data = body(client.get("/api/learning/training-runs"))["data"]

    assert data["has_data"] is True
    assert len(data["runs"]) == 1
    run = data["runs"][0]
    assert run["updates"] == [1, 2, 3]
    assert run["series"]["explained_variance"] == pytest.approx([0.05, 0.10, 0.15])
    # 재현성 정보가 화면까지 온다 (§11) — 없으면 좋은 성적을 다시 못 만든다.
    assert run["seed"] == 7
    assert run["git_commit"] == "abc1234"


def test_마지막으로_기록한_실행이_맨_위다(seeded) -> None:
    """run_id 문자열로 정렬하면 "rl-2026…" 이 "m4-…" 를 이겨 옛 판이 화면을
    차지한다 (2026-08-27 실측). 최신은 이름이 아니라 **마지막 기록 시각**이다."""
    from datetime import timedelta

    old = [{**_update_row("rl-20260819-a", i, 0.1), "observed_at": NOW - timedelta(days=3)} for i in (1, 2)]
    new = [
        {**_update_row("m4-round2-r6", i, 0.2), "observed_at": NOW - timedelta(minutes=3 * (3 - i))}
        for i in (1, 2, 3)
    ]
    seeded.append("rl_updates", old + new, ingest_run_id="rl-seed-order")
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    data = body(client.get("/api/learning/training-runs"))["data"]

    assert [r["run_id"] for r in data["runs"]] == ["m4-round2-r6", "rl-20260819-a"]
    live = data["runs"][0]
    # 3분 간격으로 기록이 이어졌고 마지막이 지금이면 살아 있는 것이다.
    assert live["status"] == "running"
    assert live["last_update"] == 3
    assert live["total_updates"] > 3
    assert live["pace_minutes"] == pytest.approx(3.0)
    assert live["eta_minutes"] == pytest.approx(3.0 * (live["total_updates"] - 3))
    # 사흘 전에 멈춘 판은 멈춘 것이다 — "진행 중" 으로 꾸미지 않는다.
    assert data["runs"][1]["status"] == "stopped"


def test_총량에_닿은_실행은_완주다(seeded) -> None:
    from quant_rl_trading.allocator.budget import total_updates

    total = total_updates()
    seeded.append(
        "rl_updates",
        [_update_row("m4-done", u, 0.3) for u in (total - 1, total)],
        ingest_run_id="rl-seed-done",
    )
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    data = body(client.get("/api/learning/training-runs"))["data"]
    assert data["runs"][0]["status"] == "completed"
    assert data["runs"][0]["eta_minutes"] is None


def test_쉬운_말_요약이_배우는_중인지_말한다(seeded) -> None:
    from datetime import timedelta

    rows = []
    for i in range(1, 121):
        row = _update_row("m4-plain", i, 0.5)
        row["observed_at"] = NOW - timedelta(minutes=3 * (121 - i))
        row["episode_reward"] = -0.01 + 0.0002 * i   # 오른다
        row["cash_weight"] = 0.03
        row["action_reflection"] = 0.55
        rows.append(row)
    seeded.append("rl_updates", rows, ingest_run_id="rl-seed-plain")
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    run = body(client.get("/api/learning/training-runs"))["data"]["runs"][0]
    assert "배우고 있다" in run["plain"]
    assert "도망은 없다" in run["plain"]
    assert "55%" in run["plain"]


# -- 정책 평가 (rl_evaluations) --------------------------------------------------


def _evaluation_rows(run_id: str, at: datetime, *, envs: int, gap_oos: float) -> list[dict]:
    rows = []
    for window, gap in (("train", 0.001), ("oos", gap_oos)):
        for arm in ("policy", "equal"):
            rows.append({
                "entity_id": run_id, "valid_from": at, "observed_at": at,
                "source": "test", "eval_window": window, "arm": arm,
                "episode_days": 20, "envs": envs, "steps": 30, "eval_seed": 0,
                "train_seed": 0, "market": "KR",
                "reward_mean": 0.004 if arm == "policy" else 0.006,
                "reward_sum": 0.12, "reward_std": 0.01, "cash_weight": 0.18,
                "action_reflection": 0.86, "cost": 0.00018, "turnover": 0.18,
                "drawdown": 0.03,
                "gap_vs_equal": gap if arm == "policy" else None,
                "verdict": ("overfit" if gap_oos <= 0 else "generalizes") if arm == "policy" else None,
                "checkpoint": "data/rl_checkpoints/x.pt", "update": 1220,
            })
    return rows


def test_evaluations_are_absent_until_measured(client) -> None:
    data = body(client.get(f"/api/learning/evaluations?as_of={NOW.isoformat()}"))["data"]
    assert data["has_data"] is False
    assert data["train_seeds"] == []


def test_evaluations_report_latest_batch_and_spread(seeded) -> None:
    """최신 배치가 표가 되고, 같은 run 의 이전 평가는 편차 이력이 된다.
    학습 시드는 하나면 하나라고 말한다."""
    first = NOW - timedelta(hours=2)
    seeded.append("rl_evaluations", _evaluation_rows("r6", first, envs=4, gap_oos=-0.0019),
                  ingest_run_id="e1")
    seeded.append("rl_evaluations", _evaluation_rows("r6", NOW - timedelta(hours=1), envs=16,
                                                     gap_oos=-0.0017), ingest_run_id="e2")
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    data = body(client.get(f"/api/learning/evaluations?as_of={NOW.isoformat()}"))["data"]
    assert data["has_data"] is True
    latest = data["latest"]
    assert latest["run_id"] == "r6"
    assert latest["envs"] == 16
    assert latest["verdict"] == "overfit"
    assert latest["table"]["oos"]["gap"] == pytest.approx(-0.0017)
    assert latest["table"]["oos"]["policy"]["cash_weight"] == pytest.approx(0.18)
    assert [h["envs"] for h in data["history"]] == [4, 16]
    assert data["train_seeds"] == [0]

    # 되감으면 나중 평가는 안 보인다 (불변식 9).
    earlier = body(client.get(f"/api/learning/evaluations?as_of={first.isoformat()}"))["data"]
    assert earlier["latest"]["envs"] == 4


# -- 커리큘럼 (C0~C5) ------------------------------------------------------------


def test_curriculum_marks_c1_failed_when_oos_is_overfit(seeded, tmp_path, monkeypatch) -> None:
    """실행이 있는 단계만 상태가 붙고, 판정은 rl_evaluations 에서 온다. 나머지는 미착수."""
    from quant_rl_trading.dashboard.services import system as system_service

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "gate-c1.log").write_text("[PASS] ①\n[PASS] ②\n[PASS] ③\nPASS 3 · FAIL 0 / 3\n")
    monkeypatch.setenv(system_service.LOGS_DIR_ENV, str(logs))
    at = NOW - timedelta(hours=1)
    seeded.append("rl_updates", [{
        "entity_id": "r6", "valid_from": at, "observed_at": at, "source": "test",
        "update": 1220, "step": 1220 * 16384, "seed": 0, "market": "KR", "curriculum": "C1",
        "explained_variance": 0.99, "approx_kl": 0.02, "entropy": -98.0, "grad_norm": 25.0,
        "action_reflection": 0.67, "policy_churn": 0.25, "concentration_sum": 90.0,
        "episode_reward": 0.016, "cash_weight": 0.025, "git_commit": "abc", "config_fingerprint": "f",
    }], ingest_run_id="u")
    seeded.append("rl_evaluations", _evaluation_rows("r6", at, envs=16, gap_oos=-0.0017),
                  ingest_run_id="e")
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    data = body(client.get(f"/api/learning/curriculum?as_of={NOW.isoformat()}"))["data"]
    by = {s["stage"]: s for s in data["stages"]}
    assert by["C0"]["status"] == "passed"
    assert by["C1"]["status"] == "failed"
    assert "과적합" in by["C1"]["note"]
    assert by["C2"]["status"] == "pending"
    assert data["current"] == "C1"
