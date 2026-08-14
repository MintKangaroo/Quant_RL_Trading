"""시스템 탭 API 계약 테스트.

이 화면의 질문은 하나다 — **지금 이 시스템이 제대로 돌고 있는가.** 그래서
여기서 고정하는 사실은 네 가지다.

1. 모든 GET 라우트가 ``as_of`` 를 받고 되돌려준다 (불변식 9)
2. 크론 작업 이력은 **로그**에서 온다 — 창고가 아니므로 as_of 로 안 되감긴다
3. 킬스위치·설정 판번호·테이블 최신성은 **창고**에서 오므로 정직하게 되감긴다
4. CPU·메모리·디스크·프로세스는 **이 기계**에서 온다 — 창고도 로그도 아니다

``system`` 블루프린트는 ``app.py`` 에 배선돼 있으므로 다른 탭 테스트와 같이
``create_app(store=..., clock=...)`` 을 쓴다.
``test_data_quality_api.py::test_every_api_route_accepts_as_of`` 가 전체
``/api/`` 라우트를 훑을 때 이 블루프린트도 자동으로 검사받는다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.dashboard.services import system as system_service
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 14, 11, 0, tzinfo=UTC)  # 2026-08-14 20:00 KST


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return store


def client_for(seeded_store: Any) -> Any:
    app = create_app(store=seeded_store, clock=ReplayClock(NOW))
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def client(seeded):  # type: ignore[no-untyped-def]
    return client_for(seeded)


def body(response: Any) -> dict[str, Any]:
    return response.get_json()  # type: ignore[no-any-return]


API_PATHS = ["summary", "jobs", "tables", "latency", "cache", "safety", "resources", "processes"]


# -- 불변식 9 -----------------------------------------------------------------


def test_every_system_route_accepts_as_of(client) -> None:
    past = (NOW - timedelta(days=1)).isoformat()
    for path in API_PATHS:
        response = client.get(f"/api/system/{path}?as_of={past}")
        assert response.status_code == 200, f"{path} → {response.status_code}"
        assert body(response)["as_of"] == past, f"{path} 가 as_of 를 되돌려주지 않는다"


def test_live_request_marks_itself(client) -> None:
    response = client.get("/api/system/summary")
    payload = body(response)
    assert payload["live"] is True
    assert payload["as_of"] == NOW.isoformat()


# -- 크론 작업 이력 — 창고가 아니라 로그 ----------------------------------------


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """테스트가 **레포의 진짜 로그**를 읽지 않게 격리한다.

    격리하지 않으면 개발 기계에서는 통과하고 CI 에서는 실패한다(또는 그 반대).
    기본값은 없는 디렉터리라 "로그가 없다" 가 기본 상태다 — 로그를 심는
    테스트만 `write_log` 로 덮는다.
    """
    monkeypatch.setenv(system_service.LOGS_DIR_ENV, str(tmp_path / "no-logs"))


def write_log(repo_root: Path, name: str, text: str) -> None:
    """가짜 크론 로그를 심고 서비스가 그걸 보게 한다.

    로그 위치는 창고가 아니라 **레포**에 딸린 것이라 store 경로에서 유도하지
    않는다(그렇게 하면 data/_demo 같은 창고에서 엉뚱한 곳을 뒤진다). 대신
    환경변수로 덮는다 — 시험할 수 없는 파서는 형식이 바뀐 날 조용히 틀린다.
    """
    logs = repo_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text(text, encoding="utf-8")
    os.environ[system_service.LOGS_DIR_ENV] = str(logs)


def test_jobs_are_parsed_from_logs(seeded) -> None:
    write_log(
        seeded.root.parent,
        "collect-202608.log",
        "=== 2026-08-14 15:55:03 market=KR sessions=3 ===\n"
        "  시세·유니버스 rc=0\n"
        "  수급 rc=0\n"
        "  거시 rc=0\n"
        "=== 2026-08-13 15:55:02 market=KR sessions=3 ===\n"
        "  시세·유니버스 rc=1\n"
        "  수급 rc=0\n"
        "  거시 rc=0\n",
    )
    data = body(client_for(seeded).get("/api/system/jobs"))["data"]

    collect = next(job for job in data if job["key"] == "collect_daily")
    assert collect["last_run_ok"] is True
    assert collect["runs"][0]["ok"] is True
    assert collect["runs"][1]["ok"] is False
    # 최근 실행이 실패였어도 이전 실행이 성공이면 그 시각이 마지막 성공이다.
    assert collect["last_success_at"] is not None

    # 등록되지 않은 작업(예: 백필)은 명단에 없다 — 로그가 있어도 crontab 에
    # 없는 것을 "돌고 있다"고 지어내지 않는다.
    expected_keys = {"collect_daily", "run_daily", "run_shadow", "collect_news"}
    assert {job["key"] for job in data} == expected_keys


def test_job_with_no_rc_line_is_unconfirmed_not_failed(seeded) -> None:
    """완주하지 못해 rc= 를 못 찾은 실행은 실패로 단정하지 않는다."""
    write_log(
        seeded.root.parent,
        "daily-202608.log",
        "=== 2026-08-14 16:10:01 market=KR ===\nTraceback (most recent call last):\n  OOM\n",
    )
    data = body(client_for(seeded).get("/api/system/jobs"))["data"]

    daily = next(job for job in data if job["key"] == "run_daily")
    assert daily["runs"][0]["ok"] is None
    assert daily["last_success_at"] is None


def test_jobs_route_ignores_as_of(seeded) -> None:
    """운영 로그는 되감기지 않는다 — 과거 as_of 로 조회해도 최신 로그를 본다."""
    write_log(
        seeded.root.parent,
        "shadow-202608.log",
        "=== 2026-08-14 16:30:00 market=KR ===\nrc=0\n",
    )
    past = (NOW - timedelta(days=30)).isoformat()
    data = body(client_for(seeded).get(f"/api/system/jobs?as_of={past}"))["data"]

    shadow = next(job for job in data if job["key"] == "run_shadow")
    assert shadow["last_run_ok"] is True


def test_missing_logs_directory_is_empty_not_an_error(client) -> None:
    data = body(client.get("/api/system/jobs"))["data"]
    assert all(job["runs"] == [] for job in data)
    assert all(job["last_run_ok"] is None for job in data)


# -- 창고 경유 — 되감긴다 ------------------------------------------------------


def test_table_freshness_reflects_the_warehouse(seeded) -> None:
    seeded.append(
        "prices",
        [{
            "entity_id": "KR:005930", "valid_from": NOW - timedelta(hours=3),
            "observed_at": NOW - timedelta(hours=2), "source": "krx", "market": "KR",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000.0, "value": None, "adj_factor": None,
        }],
        ingest_run_id="t-prices",
    )
    data = body(client_for(seeded).get("/api/system/tables"))["data"]

    prices = next(row for row in data if row["table"] == "prices")
    assert prices["rows_recent"] == 1
    assert prices["stale_days"] == 0

    # 아직 한 행도 없는 M3 테이블(orders 등)은 0/None 으로 정직하게 남는다.
    orders = next(row for row in data if row["table"] == "orders")
    assert orders["rows_recent"] == 0
    assert orders["latest_valid_from"] is None


def test_killswitch_state_is_read_from_the_warehouse(seeded) -> None:
    seeded.append(
        "killswitch",
        [{
            "entity_id": "FUND", "valid_from": NOW - timedelta(hours=1),
            "observed_at": NOW - timedelta(hours=1), "source": "executor",
            "state": "engaged", "reason": "낙폭 30% 초과", "triggered_by": "guard",
        }],
        ingest_run_id="t-ks",
    )
    data = body(client_for(seeded).get("/api/system/safety"))["data"]

    assert data["killswitch_engaged"] is True
    assert data["killswitch_reason"] == "낙폭 30% 초과"
    assert data["config_version"] == 1


def test_summary_warns_when_killswitch_is_engaged(seeded) -> None:
    seeded.append(
        "killswitch",
        [{
            "entity_id": "FUND", "valid_from": NOW - timedelta(hours=1),
            "observed_at": NOW - timedelta(hours=1), "source": "executor",
            "state": "engaged", "reason": "테스트 발동", "triggered_by": "guard",
        }],
        ingest_run_id="t-ks2",
    )
    data = body(client_for(seeded).get("/api/system/summary"))["data"]

    assert data["killswitch_engaged"] is True
    assert any("킬스위치" in w for w in data["warnings"])


def test_cache_stats_count_entries_not_hits(seeded) -> None:
    seeded.append(
        "agent_cache",
        [
            {
                "entity_id": "KR:005930", "valid_from": NOW, "observed_at": NOW,
                "source": "news@v1", "agent": "news", "agent_version": "v1",
                "features_hash": "abc123", "output": "{}", "computed_at": NOW,
            },
            {
                "entity_id": "KR:000660", "valid_from": NOW, "observed_at": NOW,
                "source": "news@v1", "agent": "news", "agent_version": "v1",
                "features_hash": "def456", "output": "{}", "computed_at": NOW,
            },
        ],
        ingest_run_id="t-cache",
    )
    data = body(client_for(seeded).get("/api/system/cache"))["data"]

    assert data["total"] == 2
    assert data["rows"][0]["agent"] == "news"
    assert data["rows"][0]["entries"] == 2


def test_thresholds_come_from_store_config(seeded) -> None:
    """system 섹션 임계치도 store.config 출처다 (불변식 10) — 코드에 숫자를 적지 않는다."""
    seeded.append(
        "prices",
        [{
            "entity_id": "KR:005930", "valid_from": NOW, "observed_at": NOW,
            "source": "krx", "market": "KR",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000.0, "value": None, "adj_factor": None,
        }],
        ingest_run_id="t-prices-fresh",
    )
    before = body(client_for(seeded).get("/api/system/summary"))["data"]
    assert before["table_stale_count"] == 0  # 방금 들어온 데이터라 기본 임계치(3일)로는 안 걸린다

    # 임계치를 -1로 낮추면 같은 창고에서 경고 판정이 바뀐다는 것을 확인한다.
    seeded.append(
        "config",
        [{
            "entity_id": "system.table_stale_warn_days",
            "valid_from": datetime(2026, 1, 1, tzinfo=UTC),
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
            "source": "test", "revision": 1, "value_json": "-1",
        }],
        ingest_run_id="t-config-tighten",
    )
    after = body(client_for(seeded).get("/api/system/summary"))["data"]
    assert after["table_stale_count"] >= 1


# -- 서버 리소스 — 창고도 로그도 아니라 이 기계 --------------------------------


def test_resources_reports_this_machine(client) -> None:
    """CPU/메모리/디스크는 리눅스 /proc·statvfs 실측이다. 못 재면 None 이지
    0 으로 채우지 않는다."""
    data = body(client.get("/api/system/resources"))["data"]

    assert set(data) == {"cpu", "memory", "disk"}
    for key in ("cpu", "memory", "disk"):
        assert data[key] is None or isinstance(data[key], dict)
    if data["disk"] is not None:
        assert data["disk"]["used_pct"] is None or 0 <= data["disk"]["used_pct"] <= 100


def test_processes_only_match_this_repo(seeded) -> None:
    """cwd 가 창고 루트 밖인 프로세스(테스트 러너 자체 등)는 안 잡힌다 —
    이 fixture 의 store 루트는 tmp_path 라 실제로 도는 어떤 프로세스의
    cwd 도 아니다."""
    data = body(client_for(seeded).get("/api/system/processes"))["data"]

    assert data["processes"] == []
    assert data["total_rss_mb"] in (0.0, None)
