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
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import pytest

SEOUL = ZoneInfo("Asia/Seoul")

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


def test_one_off_logs_with_the_same_prefix_are_ignored(seeded) -> None:
    """`collect-us-prices.log` 같은 단발 로그는 월별 이력이 아니다.

    2026-08-29 실제 사고: 이름순으로 마지막 두 파일이 단발 로그가 되어 수집이
    멀쩡한데 화면이 "완주 여부를 확인할 수 없음" 을 띄웠다.
    """
    write_log(
        seeded.root.parent,
        "collect-202608.log",
        "=== 2026-08-14 15:55:03 market=KR sessions=3 ===\n  시세·유니버스 rc=0\n",
    )
    for decoy in ("collect-us-prices.log", "collect-us-shares.log", "collect-us-universe.log"):
        write_log(seeded.root.parent, decoy, "미장 시세 6,414행\n")
    data = body(client_for(seeded).get("/api/system/jobs"))["data"]
    collect = next(job for job in data if job["key"] == "collect_daily")
    assert collect["run_count"] == 1
    assert collect["last_run_ok"] is True


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


def test_프로세스_목록은_창고_위치에_좌우되지_않는다(seeded) -> None:
    """**레포 밑에서 도는 프로세스**를 보여주는 것이지 창고 밑이 아니다.

    예전 구현은 ``root.parent`` 를 레포 루트로 삼았다. 실전 창고(``data/``)일
    때만 우연히 맞고 ``data/_shadow``·``data/_demo`` 로 띄우면 ``data/`` 를
    레포로 보아 **목록이 통째로 빈다.** 그런데 화면은 "고장" 이 아니라
    "도는 게 없다" 고 말하므로 아무도 이상하게 여기지 않는다 — 크론 이력에서
    한 번 잡고도 이 함수에 그대로 남아 있던 결함이다(2026-08-15).

    이 fixture 의 store 루트는 ``tmp_path`` 라 레포 밖이다. 그래도 지금
    돌고 있는 pytest 자신(cwd = 레포 루트)은 잡혀야 한다.
    """
    data = body(client_for(seeded).get("/api/system/processes"))["data"]

    assert data["processes"], "창고가 레포 밖이라고 프로세스 목록이 비면 안 된다"
    assert data["total_rss_mb"] and data["total_rss_mb"] > 0


# -- 지연 경보는 거래일로 센다 (태스크 #43) -------------------------------------


def test_지연은_달력일이_아니라_거래일로_센다() -> None:
    """2026-08-15(토)·16(일)·17(광복절 대체공휴일)이 붙어 사흘을 쉬었다.

    달력일로 세면 8/18 아침에 flows·universe·fx 가 전부 "4일 지연" 으로 뜬다 —
    **넷 다 마지막 거래일(8/14) 것이 정상으로 들어와 있었다.** 매일 뜨는
    빨간불은 곧 아무도 안 보는 빨간불이 되고, 그때 진짜 결손도 같이 묻힌다.
    """
    from quant_rl_trading.dashboard.services.system import _trading_days_since

    latest = datetime(2026, 8, 14, 9, 0, tzinfo=SEOUL)

    # 연휴 한복판 — 아직 한 거래일도 안 지났다.
    assert _trading_days_since(latest, datetime(2026, 8, 17, 16, 0, tzinfo=SEOUL)) == 0
    # 연휴 다음 첫 거래일. 달력으로는 나흘이지만 거래일로는 하루다.
    assert _trading_days_since(latest, datetime(2026, 8, 18, 16, 0, tzinfo=SEOUL)) == 1
    assert _trading_days_since(latest, datetime(2026, 8, 19, 16, 0, tzinfo=SEOUL)) == 2


def test_입출금은_지연_경보_대상이_아니다() -> None:
    """``capital_flows`` 는 사건이 있을 때만 생긴다 — 한 달에 한 번일 수도,
    반년을 안 넣을 수도 있다. "며칠째 안 들어왔다" 가 **고장이 아니라 정상**이라
    경보를 걸면 아무 일도 없었다는 사실이 매일 빨간불로 뜬다.

    표에서 지우는 것이 아니라 경보만 안 건다 — 마지막이 언제인지는 계속 본다.
    """
    from quant_rl_trading.dashboard.services.system import NO_STALENESS_ALARM

    assert "capital_flows" in NO_STALENESS_ALARM
    # 매일 들어와야 하는 것은 빠지면 안 된다 — 그게 빠지면 감시가 무의미해진다.
    assert "prices" not in NO_STALENESS_ALARM
    assert "universe" not in NO_STALENESS_ALARM


def test_freshness_는_기대_세션과_창고_최신_세션을_같이_준다(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/system/freshness").get_json()
    items = body["data"]["items"]
    assert {i["key"] for i in items} >= {"kr_prices", "kr_index", "us_index", "fx"}
    for item in items:
        assert item["status"] in {"ok", "stale", "unknown"}
