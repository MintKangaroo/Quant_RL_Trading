"""분봉 수집 도구의 국장 넓히기 배선 — 설정 읽기, 장중 봉 수, 주문 우선(ls-api.md §0-14)."""
from __future__ import annotations

import pytest

from quant_rl_trading.collectors.intraday_collector import MAX_ROWS_PER_CALL


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return store


def test_params_come_from_the_checked_in_config(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    from tools.collect_intraday import INTRADAY_KEYS, _intraday_params

    params = _intraday_params(seeded, as_of=ts(2026, 10, 6, 1))

    assert params is not None and set(params) == set(INTRADAY_KEYS)
    assert params["kr_top_n"] == 300
    assert list(params["wide_intervals"]) == ["1m", "5m"]


def test_missing_config_means_no_widening(store, ts) -> None:  # type: ignore[no-untyped-def]
    """설정이 안 심긴 창고는 예전 동작(보유·후보만)으로 돈다 — 짐작한 값으로 넓히지 않는다."""
    from tools.collect_intraday import _intraday_params

    assert _intraday_params(store, as_of=ts(2026, 10, 6, 1)) is None


def test_first_round_after_open_fetches_full_window(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    """개장 뒤 첫 회차는 500봉 — 전날 15:45~20:00(NXT 애프터)과 오늘 08:00~ 를 덮는다."""
    from tools.collect_intraday import _intraday_params, _live_qrycnt

    params = _intraday_params(seeded, as_of=ts(2026, 10, 6, 1))
    assert params is not None

    assert _live_qrycnt(params, interval="1m", now=ts(2026, 10, 6, 0, 0)) == MAX_ROWS_PER_CALL   # 09:00 KST
    assert _live_qrycnt(params, interval="5m", now=ts(2026, 10, 6, 0, 10)) == MAX_ROWS_PER_CALL  # 09:10 KST
    assert _live_qrycnt(params, interval="1m", now=ts(2026, 10, 6, 0, 30)) == 120                # 09:30 KST
    assert _live_qrycnt(params, interval="5m", now=ts(2026, 10, 6, 5, 0)) == 30                  # 14:00 KST


def test_paper_mode_orders_do_not_share_the_collector_key(seeded, ts) -> None:  # type: ignore[no-untyped-def]
    """지금(account_mode: paper)은 주문이 LS_PAPER_ 키 — 분봉(LS_)과 키부터 다르다."""
    from tools.collect_intraday import _orders_share_key

    assert seeded.config("execution.account_mode", as_of=ts(2026, 10, 6, 1)) == "paper"
    assert not _orders_share_key(seeded, as_of=ts(2026, 10, 6, 1))


def test_real_mode_orders_share_the_key_so_widening_is_off(store, ts, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """실전 모드면 주문이 LS_ 키를 쓴다 — 넓히기는 실측 전까지 끈다(주문 우선)."""
    from tools import collect_intraday as tool

    monkeypatch.setattr(type(store), "config", lambda self, name, *, as_of: "real")
    assert tool._orders_share_key(store, as_of=ts(2026, 10, 6, 1))


def test_unknown_mode_is_treated_as_shared(store, ts, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tools import collect_intraday as tool

    monkeypatch.setattr(type(store), "config", lambda self, name, *, as_of: "mystery")
    assert tool._orders_share_key(store, as_of=ts(2026, 10, 6, 1))
