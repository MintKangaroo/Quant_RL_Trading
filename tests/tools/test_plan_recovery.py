"""tools/plan_recovery.py — 세션 입력 관문.

재부팅 복구가 던지는 질문은 "돌릴 수 있나" 가 아니다. **"지금 돌리면 정규
크론이 낼 답과 같은 답이 나오나"** 다. 창고는 append-only 라 먼저 쓴 쪽이
이기고(불변식 4), 체결은 되돌리기가 회계보다 위험하다.

2026-08-20 18:51 복구가 그 질문을 안 하고 shadow 를 돌렸다. 8/19 주문이 아직
창고에 없어서 그날 결정을 그 시점 창고 내용으로 지어냈고, 그 체결 26행/562주가
`backtest-trades-KR-2026-08-19` 로 박혔다. 23:05 정규 실행은 30행/632주를
얻었지만 같은 run_id 라 막혔다.

여기서 못 박는 것 셋.

1. **필요한 입력은 이틀치다.** run_session 이 warmup_days=1 로 굴리므로
   전날이 같이 돈다. 하루만 보면 D+1 체결이 읽는 것을 못 본다.
2. **전날 주문이 없으면 미룬다.** 그것이 8/20 을 막았을 관문이다.
3. **미룬 것은 남고, 나중에 같은 관문으로 재판정된다.** 조용히 넘어가는
   건너뛰기는 건너뛰지 않은 것과 로그에서 구별되지 않는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.store import Store
from tools import plan_recovery

#: 2026-08-20(목)·08-21(금) — 둘 다 거래일이다.
SESSION = date(2026, 8, 21)
WARMUP = date(2026, 8, 20)
AS_OF = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)  # 23:00 KST


def _moment(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)  # 16:00 KST


def _price_row(day: date, entity: str = "KR:005930") -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": _moment(day),
        "observed_at": _moment(day),
        "source": "test",
        "market": "KR",
        "open": 70_000.0, "high": 71_000.0, "low": 69_000.0, "close": 70_500.0,
        "volume": 1_000.0, "value": 70_500_000.0, "adj_factor": 1.0,
    }


def _order_row(day: date) -> dict[str, Any]:
    return {
        "entity_id": "KR:005930",
        "valid_from": _moment(day),
        "observed_at": _moment(day),
        "source": "test",
        "market": "KR",
        "session_id": day.isoformat(),
        "slice_seq": 0,
        "side": "buy",
        "quantity": 10.0,
        "limit_price": 70_500.0,
        "target_weight": 0.1,
        "status": "submitted",
        "reason": "",
    }


def _failure_row(day: date, name: str) -> dict[str, Any]:
    return {
        "entity_id": name,
        "valid_from": _moment(day),
        "observed_at": _moment(day),
        "source": "test",
        "market": "KR",
        "stage": "score",
        "error_type": "MemoryError",
        "detail": "테스트",
    }


@pytest.fixture
def ready(store: Store) -> Store:
    """관문을 통과하는 창고 — 이틀치 시세와 전날 주문이 다 있다."""
    for day in (WARMUP, SESSION):
        store.append(
            "prices", [_price_row(day)],
            ingest_run_id=f"prices-{day}", source="test",
        )
    store.append(
        "orders", [_order_row(WARMUP)], ingest_run_id="orders-warmup", source="test"
    )
    return store


# -- 굴러가는 창은 이틀이다 ------------------------------------------------------


def test_세션_창은_전날을_포함한다() -> None:
    """run_session 이 warmup_days=1 로 굴린다 — 하루만 보면 체결 입력을 못 본다."""
    assert plan_recovery.session_window(Market.KR, SESSION) == [WARMUP, SESSION]


def test_주말을_건너뛴다() -> None:
    """8/24(월)의 전날은 8/23(일)이 아니라 8/21(금)이다."""
    assert plan_recovery.session_window(Market.KR, date(2026, 8, 24))[0] == SESSION


# -- 시세 관문 -------------------------------------------------------------------


def test_시세가_다_있으면_통과한다(ready: Store) -> None:
    assert plan_recovery.gate(
        ready, market="KR", as_of=AS_OF, session=SESSION, stage="prices"
    ) == []


def test_전날_시세가_없으면_미룬다(store: Store) -> None:
    """당일 봉만 있어도 전날이 비면 안 된다. 전날 결정이 그 봉 위에서 나온다."""
    store.append(
        "prices", [_price_row(SESSION)], ingest_run_id="prices-today", source="test"
    )

    reasons = plan_recovery.gate(
        store, market="KR", as_of=AS_OF, session=SESSION, stage="prices"
    )

    assert reasons == [f"{WARMUP.isoformat()} 시세가 없다"]


def test_당일_시세가_없으면_미룬다(store: Store) -> None:
    """D+1 체결이 읽는 것이 당일 봉이다 — 없으면 전부 미체결로 적힌다."""
    store.append(
        "prices", [_price_row(WARMUP)], ingest_run_id="prices-warmup", source="test"
    )

    reasons = plan_recovery.gate(
        store, market="KR", as_of=AS_OF, session=SESSION, stage="prices"
    )

    assert reasons == [f"{SESSION.isoformat()} 시세가 없다"]


# -- 세션 관문 — 2026-08-20 을 막았을 관문 -----------------------------------------


def test_전날_주문이_없으면_세션을_미룬다(store: Store) -> None:
    """**이것이 8/20 18:51 을 막았을 관문이다.**

    전날 주문이 없다는 것은 전날 세션이 아직 안 돌았다는 뜻이고, 그러면
    워밍업이 재생이 아니라 **지어내기**가 된다. 그 결정의 체결이
    ``backtest-trades-KR-{전날}`` 로 박히고 정규 실행은 같은 run_id 에 막힌다.
    """
    for day in (WARMUP, SESSION):
        store.append(
            "prices", [_price_row(day)],
            ingest_run_id=f"prices-{day}", source="test",
        )

    reasons = plan_recovery.gate(
        store, market="KR", as_of=AS_OF, session=SESSION, stage="session"
    )

    assert len(reasons) == 1
    assert WARMUP.isoformat() in reasons[0]
    assert "주문이 창고에 없다" in reasons[0]


def test_전날_주문이_있으면_통과한다(ready: Store) -> None:
    assert plan_recovery.gate(
        ready, market="KR", as_of=AS_OF, session=SESSION, stage="session"
    ) == []


def test_시세_관문은_주문을_묻지_않는다(store: Store) -> None:
    """주문은 run_daily·run_shadow 가 만든다. 그 앞 관문에서 물으면 언제나 없다."""
    for day in (WARMUP, SESSION):
        store.append(
            "prices", [_price_row(day)],
            ingest_run_id=f"prices-{day}", source="test",
        )

    assert plan_recovery.gate(
        store, market="KR", as_of=AS_OF, session=SESSION, stage="prices"
    ) == []


def test_주문은_세션이_쓰는_창고에서_찾는다(ready: Store, tmp_path: Path) -> None:
    """shadow 는 주문을 ``data/_shadow`` 오버레이에 쓴다. 원본만 보면 늘 "없다" 다."""
    sandbox = Store(root=tmp_path / "sandbox")
    empty = Store(root=ready.root)

    # 읽기 창고에는 주문이 있지만, 세션이 쓸 창고는 비어 있다 → 미룬다.
    reasons = plan_recovery.gate(
        empty, market="KR", as_of=AS_OF, session=SESSION,
        stage="session", session_store=sandbox,
    )

    assert any("주문이 창고에 없다" in reason for reason in reasons)


def test_죽은_Analyst_가_있으면_미룬다(ready: Store) -> None:
    """반쪽 신호로 고른 후보는 정규 실행이 낼 후보와 다르다.

    ``signals`` 를 세어 판정하지 않는 이유도 여기 있다 — Analyst 명단은
    늘어난다. 새로 붙인 하나 때문에 지난 세션이 전부 "미완" 이 되면 복구는
    영영 안 돈다.
    """
    ready.append(
        "analyst_failures", [_failure_row(WARMUP, "event")],
        ingest_run_id="failure-event", source="test",
    )

    reasons = plan_recovery.gate(
        ready, market="KR", as_of=AS_OF, session=SESSION, stage="session"
    )

    assert reasons == [f"{WARMUP.isoformat()} 에 event 가 죽었다"]


def test_새_Analyst_가_붙어도_지난_세션이_미완이_되지_않는다(ready: Store) -> None:
    """실패 기록이 없으면 통과다. 명단이 늘어난 것은 그 세션의 사고가 아니다."""
    assert plan_recovery.gate(
        ready, market="KR", as_of=AS_OF, session=SESSION, stage="session"
    ) == []


# -- 미룬 것을 남기고 다시 묻는다 --------------------------------------------------


def test_미룬_세션은_로그에_남는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "recovery-deferrals.log"
    monkeypatch.setattr(plan_recovery, "DEFERRAL_LOG", log)

    plan_recovery.record_deferral(
        at=AS_OF, market="KR", session=SESSION, stage="session",
        reasons=["2026-08-20 주문이 창고에 없다"],
    )

    fields = log.read_text(encoding="utf-8").strip().split("\t")
    assert fields[1:4] == ["KR", SESSION.isoformat(), "session"]
    assert "주문이 창고에 없다" in fields[4]


def test_미룬_세션이_채워지면_그렇게_말한다(
    ready: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """판정은 한 함수에서만 한다 — 미룰 때와 확인할 때가 갈리면 서로를 반박한다."""
    log = tmp_path / "recovery-deferrals.log"
    monkeypatch.setattr(plan_recovery, "DEFERRAL_LOG", log)
    plan_recovery.record_deferral(
        at=AS_OF - timedelta(hours=4), market="KR", session=SESSION,
        stage="session", reasons=["2026-08-20 주문이 창고에 없다"],
    )

    lines = plan_recovery.follow_up(ready, as_of=AS_OF)

    assert lines == [f"미룬 세션 KR {SESSION.isoformat()} (session): 채워졌다"]


def test_아직_안_채워졌으면_이유까지_말한다(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "recovery-deferrals.log"
    monkeypatch.setattr(plan_recovery, "DEFERRAL_LOG", log)
    plan_recovery.record_deferral(
        at=AS_OF, market="KR", session=SESSION, stage="session",
        reasons=["2026-08-20 주문이 창고에 없다"],
    )

    lines = plan_recovery.follow_up(store, as_of=AS_OF)

    assert len(lines) == 1
    assert "아직 비었다" in lines[0]


def test_미룬_것이_없으면_조용하다(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan_recovery, "DEFERRAL_LOG", tmp_path / "none.log")

    assert plan_recovery.follow_up(store, as_of=AS_OF) == []


# -- CLI — 셸이 읽는 계약 ----------------------------------------------------------


def test_관문이_막으면_rc가_0이_아니다(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """**rc 로 내보낸다.** 셸이 보는 것은 그 값 하나다
    ([[silent-failure-needs-nonzero-rc]])."""
    monkeypatch.setattr(plan_recovery, "DEFERRAL_LOG", tmp_path / "defer.log")
    store.seed_config_defaults()  # 공표 지연을 읽어야 기대 세션이 나온다

    rc = plan_recovery.main(
        ["--market", "KR", "--root", str(store.root), "--gate", "prices"]
    )

    assert rc == 3
    assert capsys.readouterr().out.startswith("DEFER")


def test_오래된_미룸은_되짚지_않는다(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """석 달 전 날짜를 덮으려면 파티션을 그만큼 연다. 그때쯤 답은 이미
    "그 세션은 영영 빠졌다" 이고, 그건 M3 카운터가 들고 있는 사실이다."""
    log = tmp_path / "recovery-deferrals.log"
    monkeypatch.setattr(plan_recovery, "DEFERRAL_LOG", log)
    old = SESSION - timedelta(days=plan_recovery.FOLLOW_UP_DAYS + 1)
    plan_recovery.record_deferral(
        at=AS_OF, market="KR", session=old, stage="session", reasons=["옛날 일"]
    )

    assert plan_recovery.follow_up(store, as_of=AS_OF) == []
