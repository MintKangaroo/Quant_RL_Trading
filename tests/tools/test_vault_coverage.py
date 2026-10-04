"""금고 창 캐시 세션 점검(tools/vault_coverage.py) — 합성 캐시만 쓴다. 금고 창 자료는 읽지 않는다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant_rl_trading.collectors.market_hours import Market, trading_days
from tools import backfill_ic_history
from tools import vault_coverage as vc
from tools import vault_judge as vj

START, END = date(2026, 9, 21), date(2026, 9, 30)


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> vj.Window:
    for name in ("VAULT_START", "VAULT_END", "OPEN_FROM", "VAULT", "US_WORK", "RAW_DIRS"):
        monkeypatch.setattr(vj, name, getattr(vj, name))   # check() 가 use_window 로 바꾼 모듈 변수를 되돌린다
    return vj.Window("t", START, END, END, END, ("AQ",), tmp_path / "vault")


def _frame(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame({"entity_id": "X", "session": days, "score": 0.0})


def _fill(win: vj.Window, insider: Path, *, drop_us_tail: int = 0) -> None:
    kr = list(trading_days(Market.KR, win.start, win.end))
    us = list(trading_days(Market.US, win.start, win.end))
    us_cut = us[: len(us) - drop_us_tail]
    win.cache.mkdir(parents=True)
    for name in vc.KR_SCORES:
        _frame(kr).to_pickle(win.cache / f"scores-{name}-KR.pkl")
    _frame(kr).to_pickle(win.cache / "targets-KR-h5.pkl")
    _frame(kr).to_pickle(win.cache / "tradable-KR.pkl")
    work = win.cache / "ic-history-us"
    work.mkdir()
    for name in vc.US_SCORES:
        _frame(us_cut).to_parquet(work / f"scores-{name}-00.parquet")
    _frame(us_cut).to_parquet(work / "targets-x.parquet")
    for market, days in (("KR", kr), ("US", us_cut)):
        raw = win.cache / f"raw-{market}"
        raw.mkdir()
        _frame(days).to_pickle(raw / f"calendar-{market}.pkl")
        for name in vj.RAW_ANALYSTS[market]:
            _frame(days).to_pickle(raw / f"features-{name}-{market}.pkl")
    insider.mkdir()
    _frame(kr).to_parquet(insider / "G4-KR-202609.parquet")
    _frame(us_cut).to_parquet(insider / "G7-US-202609.parquet")


def test_full_cache_has_no_problems(window: vj.Window, tmp_path: Path) -> None:
    _fill(window, tmp_path / "ins")
    assert vc.check(window, insider_dir=tmp_path / "ins") == []


def test_missing_us_tail_is_reported(window: vj.Window, tmp_path: Path) -> None:
    _fill(window, tmp_path / "ins", drop_us_tail=2)
    problems = vc.check(window, insider_dir=tmp_path / "ins")
    assert any(p.startswith("미장 점수 chart: 2세션 없음") for p in problems)
    assert any(p.startswith("내부자 G7 US: 2세션 없음") for p in problems)
    assert not any(p.startswith("국장") for p in problems)


def test_missing_file_is_reported(window: vj.Window, tmp_path: Path) -> None:
    _fill(window, tmp_path / "ins")
    (window.cache / "targets-KR-h5.pkl").unlink()
    assert vc.check(window, insider_dir=tmp_path / "ins") == ["국장 타깃 h5: 파일 없음"]


def test_backfill_last_session_refuses_save() -> None:
    with pytest.raises(SystemExit):
        backfill_ic_history.main(["--work", "x", "--last-session", "2026-09-30", "--save"])


def test_build_windows_drops_labels_after_last_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime
    days = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
    monkeypatch.setattr(backfill_ic_history.ic, "build_targets",
                        lambda *a, **k: pd.DataFrame({"entity_id": "X", "session": days, "target": 0.0}))
    point = datetime(2026, 10, 12, 20, 20, tzinfo=UTC)
    windows = backfill_ic_history.build_windows(None, points=[point], market=Market.US, sessions=120,  # type: ignore[arg-type]
                                                work=tmp_path, last_session=date(2026, 9, 30))
    assert windows["20261012T202000"] == days[:2]
    saved = pd.read_parquet(tmp_path / "targets-20261012T202000.parquet")
    assert set(saved["session"]) == set(days[:2])
