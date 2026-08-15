"""tools/verify_m3.py — M3 완료 기준 검증기.

세 가지를 못 박는다.

1. **미측정과 FAIL 을 섞지 않는다.** 아직 잴 수 없는 상태(선행조건 미비)를
   FAIL 로 적으면 "고장났다" 로 읽힌다. PASS 로 적으면 거짓이 된다.
2. **아무것도 안 하면 통과하는 함정을 막는다.** 시뮬레이션 체결만 있는
   경우, NAV 가 고정된 경우, 백테스트가 오래돼 완주로 오인될 수 있는
   경우 — 전부 미측정이지 PASS 가 아니다.
3. **판정마다 근거가 남는다.** 상태만 보고 왜 그런지 모르면 검증기가
   아니라 신탁이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_rl_trading.store import Store, overlay
from tools import verify_m3

AS_OF = datetime(2026, 8, 14, tzinfo=UTC)


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "entity_id": entity, "valid_from": moment, "observed_at": moment,
        "source": "test", **extra,
    }


def _nav_row(moment: datetime, nav: float, drawdown: float) -> dict[str, Any]:
    return _row(
        "FUND", moment, nav=nav, inflow=0.0, twr_return=0.0, index_value=100.0,
        drawdown=drawdown, cash_krw=nav, cash_usd=0.0, equity_kr=0.0, equity_us=0.0,
        accrued_dividend=0.0, payable=0.0, fx_rate=1_350.0, tax_provision=0.0,
        nav_after_tax=nav, benchmark_index=None,
    )


def _trade_row(moment: datetime, *, source: str) -> dict[str, Any]:
    return _row(
        "KR:005930", moment, source=source, market="KR", side="buy", quantity=1.0,
        price=70_000.0, currency="KRW", fee=0.0, tax=0.0, order_id=f"{source}-1",
    )


# -- Check — 상태는 셋뿐이다 ---------------------------------------------------


def test_check은_PASS_FAIL_미측정만_받는다() -> None:
    verify_m3.Check("이름", "PASS", [])
    verify_m3.Check("이름", "FAIL", [])
    verify_m3.Check("이름", "미측정", [])
    with pytest.raises(ValueError):
        verify_m3.Check("이름", "SKIP", [])


# -- 기준 4: 실전 소액 투입 ------------------------------------------------------


def test_체결_자체가_없으면_미측정이다(store: Store) -> None:
    check = verify_m3.check_live_trade(store, AS_OF, "KR")

    assert check.status == "미측정"


def test_시뮬레이션_체결만으로는_통과가_아니다(store: Store) -> None:
    """``trades.source == "backtest"`` 는 replay/fills.py 가 모델로 만든 것이라
    실전 배선 검증이 아니다 — 그래도 있는 걸 없다고 하지 않고 미측정으로 말한다."""
    store.append(
        "trades", [_trade_row(AS_OF - timedelta(days=1), source="backtest")],
        ingest_run_id="trades-backtest", source="backtest",
    )

    check = verify_m3.check_live_trade(store, AS_OF, "KR")

    assert check.status == "미측정"
    assert any("broker" in line for line in check.evidence)


def test_실전_체결이_있으면_통과한다(store: Store) -> None:
    store.append(
        "trades", [_trade_row(AS_OF - timedelta(days=1), source="broker")],
        ingest_run_id="trades-broker", source="broker",
    )

    check = verify_m3.check_live_trade(store, AS_OF, "KR")

    assert check.status == "PASS"
    assert any("실전 체결" in line for line in check.evidence)


# -- 기준 2: OOS 백테스트 MDD -----------------------------------------------------


def test_백테스트_샌드박스가_없으면_미측정이다(
    store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_m3, "BACKTEST_SANDBOX", tmp_path / "no-such-sandbox")

    check = verify_m3.check_oos_mdd(store, AS_OF, "KR")

    assert check.status == "미측정"


def test_완주하지_못한_오래된_결과는_참고치일뿐_판정에_안_쓴다(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.seed_config_defaults()
    monkeypatch.setattr(verify_m3, "BACKTEST_SANDBOX", store.root)
    start = datetime(2025, 9, 1, tzinfo=UTC)
    rows = [_nav_row(start + timedelta(days=i), 1e8 * (1 - 0.01 * i), -0.01 * i) for i in range(30)]
    store.append("nav_daily", rows, ingest_run_id="nav-stale")
    # as_of 가 마지막 관측보다 한참 뒤 — 재실행 중인 워크포워드가 멈춘 흔적이다.
    stale_as_of = start + timedelta(days=180)

    check = verify_m3.check_oos_mdd(store, stale_as_of, "KR")

    assert check.status == "미측정"
    assert any("완주" in line for line in check.evidence)


def test_매매_0건_MDD_0퍼센트는_완벽한_성적이_아니라_미측정이다(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.seed_config_defaults()
    monkeypatch.setattr(verify_m3, "BACKTEST_SANDBOX", store.root)
    start = datetime(2025, 9, 1, tzinfo=UTC)
    rows = [_nav_row(start + timedelta(days=i), 1e8, 0.0) for i in range(200)]
    store.append("nav_daily", rows, ingest_run_id="nav-flat")

    check = verify_m3.check_oos_mdd(store, start + timedelta(days=200), "KR")

    assert check.status == "미측정"
    assert any("매매가 없었다" in line for line in check.evidence)


def test_완주한_결과가_게이트_안이면_통과한다(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.seed_config_defaults()
    monkeypatch.setattr(verify_m3, "BACKTEST_SANDBOX", store.root)
    start = datetime(2025, 9, 1, tzinfo=UTC)
    # -5% ~ -10% 사이를 오간다. config 기본 게이트는 20%.
    rows = [
        _nav_row(
            start + timedelta(days=i),
            1e8 * (1 - (0.05 + 0.05 * abs((i % 40) - 20) / 20)),
            -(0.05 + 0.05 * abs((i % 40) - 20) / 20),
        )
        for i in range(200)
    ]
    store.append("nav_daily", rows, ingest_run_id="nav-ok")

    check = verify_m3.check_oos_mdd(store, start + timedelta(days=200), "KR")

    assert check.status == "PASS"


def test_완주한_결과가_게이트_밖이면_실패한다(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.seed_config_defaults()
    monkeypatch.setattr(verify_m3, "BACKTEST_SANDBOX", store.root)
    start = datetime(2025, 9, 1, tzinfo=UTC)
    # -27%~-32% — config 기본 게이트(20%) 밖이다.
    rows = [
        _nav_row(
            start + timedelta(days=i),
            1e8 * (1 - (0.27 + 0.05 * (i % 2))),
            -(0.27 + 0.05 * (i % 2)),
        )
        for i in range(200)
    ]
    store.append("nav_daily", rows, ingest_run_id="nav-breach")

    check = verify_m3.check_oos_mdd(store, start + timedelta(days=200), "KR")

    assert check.status == "FAIL"


# -- 기준 1: shadow 10거래일 무사고 -----------------------------------------------


def _shadow_store(tmp_path: Path, source_root: Path) -> Store:
    layer = overlay.build(
        root=tmp_path / "shadow", source=source_root,
        writable=frozenset({"events", "trades", "killswitch", "nav_daily"}),
    )
    return Store(root=layer.root)


def _session_events(shadow: Store, *, market: str, day: datetime, stages: list[str]) -> None:
    run_id = f"session-{market}-{day.date().isoformat()}"
    rows = [
        {
            "entity_id": run_id, "valid_from": day, "observed_at": day, "source": "test",
            "seq": i, "stage": stage, "actor": "test", "payload_hash": "x", "payload": "{}",
        }
        for i, stage in enumerate(stages)
    ]
    shadow.append("events", rows, ingest_run_id=f"events-{run_id}", source="test")


def test_shadow_샌드박스가_없으면_미측정이다(
    store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_m3, "SHADOW_SANDBOX", tmp_path / "no-such-shadow")

    check = verify_m3.check_shadow(store, AS_OF)

    assert check.status == "미측정"


def test_shadow_NAV가_고정돼_있으면_체결이_있어도_미측정이다(
    store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """docs/milestones.md 가 2026-08-13·14 이틀에 실제로 겪은 함정 —
    체결은 있는데 nav_daily 가 안 움직이면 무사고가 아니라 미검증이다."""
    monkeypatch.setattr(verify_m3, "SHADOW_SANDBOX", tmp_path / "shadow")
    shadow = _shadow_store(tmp_path, store.root)
    day = datetime.combine(verify_m3.SHADOW_RESTART_DATE, datetime.min.time(), tzinfo=UTC)

    stages = ["observe", "select", "allocate", "execute"]
    _session_events(shadow, market="KR", day=day, stages=stages)
    shadow.append(
        "trades", [_trade_row(day, source="broker")],
        ingest_run_id="shadow-trade", source="broker",
    )
    # **입금을 심어야 사고가 재현된다.** 판정이 "NAV 가 누적 입금과 같은가" 를
    # 보므로, 입금 기록이 없으면 NAV 1천만원이 "0원에서 움직인 것" 으로 읽혀
    # 정상 판정이 난다. 실제 shadow 는 자본 1천만원을 넣고 시작한다.
    shadow.append(
        "capital_flows",
        [_row("FUND", day, currency="KRW", amount=1e7, kind="deposit")],
        ingest_run_id="shadow-seed",
    )
    shadow.append("nav_daily", [_nav_row(day, 1e7, 0.0)], ingest_run_id="shadow-nav")

    check = verify_m3.check_shadow(store, day + timedelta(hours=6))

    assert check.status == "미측정"
    # 근거는 **"NAV 값이 전부 같다"** 가 아니다. 매수만 있고 종가가 안 움직인
    # 하루는 NAV 가 거의 안 변하는 것이 정상이라(현금↓ + 주식평가↑) 그 기준으로는
    # 정상과 고장을 못 가른다 — 실제 2026-08-14 의 올바른 NAV 는 초기 자본과
    # 345원 차이였다. 지금은 **회계가 체결을 반영했는지**를 본다
    # (`verify_m3._stale_pnl_days` 독스트링).
    assert any("손익 경로 미검증" in line for line in check.evidence)


def test_shadow_파이프라인이_중도에_끊기면_실패다(
    store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_m3, "SHADOW_SANDBOX", tmp_path / "shadow")
    shadow = _shadow_store(tmp_path, store.root)
    day = datetime.combine(verify_m3.SHADOW_RESTART_DATE, datetime.min.time(), tzinfo=UTC)

    # execute 단계가 없다 — 세션이 도중에 죽은 것이다.
    _session_events(shadow, market="KR", day=day, stages=["observe", "select"])

    check = verify_m3.check_shadow(store, day + timedelta(hours=6))

    assert check.status == "FAIL"
    assert any("중도 종료" in line for line in check.evidence)


def test_shadow_킬스위치가_발동하면_실패다(
    store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_m3, "SHADOW_SANDBOX", tmp_path / "shadow")
    shadow = _shadow_store(tmp_path, store.root)
    day = datetime.combine(verify_m3.SHADOW_RESTART_DATE, datetime.min.time(), tzinfo=UTC)

    stages = ["observe", "select", "allocate", "execute"]
    _session_events(shadow, market="KR", day=day, stages=stages)
    shadow.append(
        "killswitch",
        [_row("FUND", day, state="ENGAGED", reason="test", triggered_by="test")],
        ingest_run_id="ks-1",
    )

    check = verify_m3.check_shadow(store, day + timedelta(hours=6))

    assert check.status == "FAIL"


def test_shadow_체결_완주_NAV_변화가_모두_있으면_통과한다(
    store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_m3, "SHADOW_SANDBOX", tmp_path / "shadow")
    # 10일치를 만드는 대신 문턱을 낮춰 로직만 검증한다 — 함정 회피 자체가
    # 목적인 위 테스트들과 달리, 이건 "정상 경로가 실제로 PASS 를 낼 수
    # 있는가" 를 확인하는 것이다.
    monkeypatch.setattr(verify_m3, "REQUIRED_SHADOW_DAYS", 2)
    shadow = _shadow_store(tmp_path, store.root)
    restart = datetime.combine(verify_m3.SHADOW_RESTART_DATE, datetime.min.time(), tzinfo=UTC)
    day1, day2 = restart, restart + timedelta(days=1)

    for day, nav in ((day1, 1.00e7), (day2, 1.01e7)):
        stages = ["observe", "select", "allocate", "execute"]
        _session_events(shadow, market="KR", day=day, stages=stages)
        shadow.append(
            "trades", [_trade_row(day, source="broker")],
            ingest_run_id=f"trade-{day.date()}", source="broker",
        )
        shadow.append("nav_daily", [_nav_row(day, nav, 0.0)], ingest_run_id=f"nav-{day.date()}")

    check = verify_m3.check_shadow(store, day2 + timedelta(hours=6))

    assert check.status == "PASS"
