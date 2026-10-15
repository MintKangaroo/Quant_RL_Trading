"""장부 리셋(2026-10-15 LS 모의계좌 만료) — 옛 장부는 통째로 보관돼 어떤 as_of 로도 그대로, 새 장부는 입금 한 행에서 시작.

지키는 것: 리셋 뒤 NAV 기준 = 새 입금·보유 0·원금 = 새 입금, 입금은 known_at 전 as_of 에 안 보인다(이중시간),
원화·달러 두 입금이 자연키로 접히지 않는다, 장 중·미래 known_at·두 번째 리셋은 거부한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.accounting import ledger as L
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store, overlay
from tools import reset_ledger as R
from tools.run_backtest import JOURNAL

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 15, 16, 5, tzinfo=SEOUL)
VALID = "2026-10-15T08:00+09:00"
KNOWN = "2026-10-15T12:45+09:00"
SESSION = datetime(2026, 10, 15, 16, 0, tzinfo=SEOUL)   # 첫 세션 KR-2026-10-15 의 as_of


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = Store(root=tmp_path / "data")
    source.seed_config_defaults()
    book = tmp_path / "data" / "_paper"
    old = Store(root=overlay.build(root=book, source=source.root, writable=JOURNAL).root)
    opened = datetime(2026, 8, 25, 16, 0, tzinfo=SEOUL)
    old.append(L.CAPITAL_FLOWS, [{"entity_id": L.ACCOUNT, "valid_from": opened, "observed_at": opened,
                                  "source": "backtest", "currency": "KRW", "amount": 507_211_421.0, "kind": "deposit"}],
               ingest_run_id="backtest-seed-2026-08-25")
    bought = datetime(2026, 9, 1, 9, 10, tzinfo=SEOUL)
    old.append(L.TRADES, [{"entity_id": "KR:005930", "valid_from": bought, "observed_at": bought, "source": "broker",
                           "market": "KR", "side": "buy", "quantity": 100.0, "price": 70_000.0, "currency": "KRW",
                           "fee": 1_050.0, "tax": 0.0, "order_id": "KR-2026-08-31|KR:005930|0#100"}],
               ingest_run_id="fills-test")
    monkeypatch.setattr(R, "build_store", lambda root=None: source)
    monkeypatch.setattr(R, "LiveClock", lambda: ReplayClock(NOW))
    monkeypatch.setattr(R, "in_market_hours", lambda now: False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _open(repo: Path, rel: str) -> Store:
    return Store(root=overlay.build(root=repo / rel, source=repo / "data", writable=JOURNAL).root)


def _args(*extra: str) -> list[str]:
    return ["--sandbox", "data/_paper", "--flow", "KRW:500000000", "--valid-from", VALID, "--known-at", KNOWN,
            "--reason", "test", *extra]


def test_미리보기는_아무것도_안_바꾼다(repo: Path) -> None:
    assert R.main(_args()) == 0
    assert not (repo / "data" / "_archive").exists()
    assert len(L.build_book(_open(repo, "data/_paper"), as_of=NOW, rates=None).positions) == 1  # type: ignore[arg-type]


def test_리셋_뒤_새_장부는_입금만_옛_장부는_그대로(repo: Path) -> None:
    assert R.main(_args("--apply")) == 0
    fresh = _open(repo, "data/_paper")
    book = L.build_book(fresh, as_of=SESSION, rates=Rates.from_store(fresh, as_of=SESSION))
    assert book.positions == {} and book.cash["KRW"] == 500_000_000.0
    assert L.principal(fresh, as_of=SESSION) == 500_000_000.0
    # TWR 기준점: 첫 세션의 유입 = 새 입금 전부 → 지수 100 출발(accounting.md §6).
    assert L.daily_inflow(fresh, as_of=SESSION, since=SESSION - timedelta(days=1)) == 500_000_000.0
    # 이중시간: 잔고를 알기 전 as_of 에는 새 장부에 아무것도 없다.
    assert fresh.get(L.CAPITAL_FLOWS, as_of=datetime(2026, 10, 15, 12, 0, tzinfo=SEOUL), lookback=None).empty
    flows = fresh.get(L.CAPITAL_FLOWS, as_of=NOW, lookback=None)
    assert set(flows["source"]) == {R.SOURCE}
    assert (repo / "data" / "_paper" / R.NOTE).exists()
    # 옛 장부는 지우지도 고치지도 않았다 — 보관 위치에서 같은 as_of 로 같은 장부.
    archived = _open(repo, "data/_archive/paper-2026-08-25_2026-10-15")
    old = L.build_book(archived, as_of=NOW, rates=Rates.from_store(archived, as_of=NOW))
    assert old.positions["KR:005930"].quantity == 100.0
    assert L.principal(archived, as_of=NOW) == 507_211_421.0


def test_원화_달러_두_입금이_한_행으로_접히지_않는다(repo: Path) -> None:
    assert R.main([*_args("--apply"), "--flow", "USD:370000"]) == 0
    flows = _open(repo, "data/_paper").get(L.CAPITAL_FLOWS, as_of=NOW, lookback=None)
    assert sorted(flows["currency"]) == ["KRW", "USD"]
    assert flows["valid_from"].nunique() == 2


def test_두_번째_리셋은_거부한다(repo: Path) -> None:
    assert R.main(_args("--apply")) == 0
    # 새 장부도 같은 리셋일·첫 입금일이 아니라 이름이 갈리지만, 같은 명령을 다시 치는 실수는 보관 위치 충돌로 막힌다.
    (repo / "data" / "_archive" / "paper-2026-10-15_2026-10-15").mkdir()
    with pytest.raises(SystemExit, match="이미 있다"):
        R.main(_args("--apply"))


def test_장_중과_미래_known_at_은_거부한다(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="지금보다 늦다"):
        R.main(["--sandbox", "data/_paper", "--flow", "KRW:1", "--valid-from", VALID,
                "--known-at", "2026-10-15T17:00+09:00", "--reason", "x"])
    monkeypatch.setattr(R, "in_market_hours", lambda now: True)
    assert R.main(_args("--apply")) == 2
    assert not (repo / "data" / "_archive").exists()


def test_장_시간_판정() -> None:
    assert R.in_market_hours(datetime(2026, 10, 15, 15, 50, tzinfo=SEOUL))
    assert not R.in_market_hours(datetime(2026, 10, 15, 15, 51, tzinfo=SEOUL))
    assert not R.in_market_hours(datetime(2026, 10, 17, 10, 0, tzinfo=SEOUL))   # 토요일
