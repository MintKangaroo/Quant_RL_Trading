"""**Analyst 가 죽으면 조용히 끝나지 않는다.**

2026-08-18~20 세 세션 동안 event·fundamental·regime 이 MemoryError 로 죽었는데
`run_daily.sh` 는 rc=0 으로 끝났다. 6종 중 3종이 빠진 신호로 후보를 고르는
동안 크론도 브리핑도 아무 말을 안 했다 — 사람이 로그를 열어 봐야만 알 수 있는
고장은 없는 고장과 같다.

여기서 못 박는 것은 두 가지다.

1. **"신호 0건" 과 "예외로 죽음" 을 가른다.** 한 목록에 있으면 호출부가
   문자열을 뜯어봐야 하고, 그러면 아무도 안 가른다.
2. **실패는 rc 로 나간다.** 화면에 찍는 것과 크론이 보는 것은 다른 일이다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.session import signals as signals_module

AS_OF = datetime(2026, 8, 20, 7, 0, tzinfo=UTC)


class BoomAnalyst(Analyst):
    """반드시 죽는 Analyst. 창고를 안 건드린다."""

    name = "boom"
    version = "boom-v0"

    def features(self, as_of: datetime):  # type: ignore[no-untyped-def]
        raise MemoryError("Unable to allocate 94.7 MiB")

    def raw_score(self, features):  # type: ignore[no-untyped-def]
        raise AssertionError("여기까지 오면 안 된다")


class SilentAnalyst(Analyst):
    """안 죽지만 아무것도 안 내는 Analyst."""

    name = "silent"
    version = "silent-v0"

    def features(self, as_of: datetime):  # type: ignore[no-untyped-def]
        import pandas as pd

        return pd.DataFrame()

    def raw_score(self, features):  # type: ignore[no-untyped-def]
        import pandas as pd

        return pd.Series(dtype=float)


@pytest.fixture
def scorers(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setitem(
        signals_module.SCORERS,
        Market.KR,
        {"boom": BoomAnalyst, "silent": SilentAnalyst},
    )


def test_crash_lands_in_failures_not_only_warnings(store, scorers):  # type: ignore[no-untyped-def]
    """죽은 것은 ``failures`` 에, 빈 것은 ``warnings`` 에만."""
    store.seed_config_defaults()
    result = signals_module.produce(store, market=Market.KR, as_of=AS_OF)

    assert len(result.failures) == 1
    assert "boom" in result.failures[0]
    assert "MemoryError" in result.failures[0]

    # 빈 Analyst 는 실패가 아니다 — 데이터가 아직 없는 정상 상태일 수 있고,
    # 그것까지 실패로 치면 경보가 매일 울려 아무도 안 보게 된다.
    assert not any("silent" in message for message in result.failures)
    assert any("silent" in message for message in result.warnings)


def test_run_daily_returns_nonzero_when_analyst_dies(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """``run_daily.main`` 은 실패가 있으면 0 이 아닌 값으로 끝난다."""
    from tools import run_daily as run_daily_module

    monkeypatch.setattr(
        run_daily_module, "load_env", lambda: None, raising=False
    )
    monkeypatch.setattr(
        run_daily_module, "build_store", lambda _root: object(), raising=False
    )
    monkeypatch.setattr(
        run_daily_module, "last_published", lambda *a, **k: AS_OF, raising=False
    )
    monkeypatch.setattr(
        run_daily_module,
        "run_scorers",
        lambda *a, **k: (0, ["boom: MemoryError"], ["boom: MemoryError"]),
        raising=False,
    )
    monkeypatch.setattr(
        run_daily_module, "run_filters", lambda *a, **k: (0, []), raising=False
    )

    assert run_daily_module.main(["--market", "KR"]) == 3
    assert "죽었다" in capsys.readouterr().out


def test_run_daily_returns_zero_when_only_empty(monkeypatch):  # type: ignore[no-untyped-def]
    """빈 신호만으로는 실패로 치지 않는다."""
    from tools import run_daily as run_daily_module

    monkeypatch.setattr(run_daily_module, "load_env", lambda: None, raising=False)
    monkeypatch.setattr(
        run_daily_module, "build_store", lambda _root: object(), raising=False
    )
    monkeypatch.setattr(
        run_daily_module, "last_published", lambda *a, **k: AS_OF, raising=False
    )
    monkeypatch.setattr(
        run_daily_module,
        "run_scorers",
        lambda *a, **k: (0, ["silent: 신호 0건"], []),
        raising=False,
    )
    monkeypatch.setattr(
        run_daily_module, "run_filters", lambda *a, **k: (0, []), raising=False
    )

    assert run_daily_module.main(["--market", "KR"]) == 0
