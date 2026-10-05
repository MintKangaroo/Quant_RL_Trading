"""종료 판정 도구 — 수식을 ``accounting/relative.py`` 로 옮긴 뒤에도 등록된 계산 그대로인가 (2026-10-05).

옮기기 전 출력과 실제 창고에서 글자 하나 다르지 않음을 diff 로 확인했고, 여기서는 손계산으로 못 박는다:

- 결손일(9/1)이 있어도 분배금은 **창의 거래일 수**로 붙는다(세션 수가 아니다).
- 결손일 개수를 적는다. β 는 표본 공분산 / 표본 분산(ETF 가격 수익).
"""

from __future__ import annotations

import statistics
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.accounting import ledger
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store
from tools import verify_exit_criterion as tool

SEOUL = ZoneInfo("Asia/Seoul")
OURS = {date(2026, 8, 28): 100.0, date(2026, 8, 31): 101.0, date(2026, 9, 2): 100.0,     # 9/1 결손
        date(2026, 9, 3): 102.0, date(2026, 9, 4): 103.0}
ETF = {date(2026, 8, 28): 1000.0, date(2026, 8, 31): 1005.0, date(2026, 9, 1): 1010.0,
       date(2026, 9, 2): 995.0, date(2026, 9, 3): 1020.0, date(2026, 9, 4): 1030.0}


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)


@pytest.fixture
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = Store(root=tmp_path / "data")
    source.seed_config_defaults()
    source.append("indices", [{
        "entity_id": BENCHMARK_ETF, "valid_from": _at(d, 15, 50), "observed_at": _at(d, 15, 50), "source": "test",
        "market": "KR", "board": "ETF", "close": c,
    } for d, c in ETF.items()], ingest_run_id="etf")
    book = Store(root=tmp_path / "data" / "_paper")
    book.append(ledger.NAV_DAILY, [{
        "entity_id": ledger.ACCOUNT, "valid_from": _at(d, 16), "observed_at": _at(d, 16), "source": "test",
        "nav": 5e8 * v / 100, "index_value": v,
    } for d, v in OURS.items()], ingest_run_id="nav")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool, "LiveClock", lambda: ReplayClock(_at(date(2026, 9, 5), 9)))
    return tmp_path


def test_결손일이_있어도_분배금은_창의_거래일로_붙는다(warehouse: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["--sandbox", "data/_paper"]) == 0
    out = capsys.readouterr().out
    assert "세션 5/60 · 우리 장부에 없는 거래일 1개" in out
    assert "우리              +3.00%   MDD   -0.99%" in out
    # ETF 낙폭도 겹치는 세션으로만 잰다 — 9/1(1010)은 장부에 없어 고점이 아니다: 995/1005 − 1.
    assert "KODEX200 가격     +3.00%   MDD   -1.00%" in out
    # 8/28~9/4 거래일 6개 → 5일치 = 0.015 × 5 / 245 = +0.03%p. 세션(4걸음)으로 세면 4일치다.
    assert "KODEX200 총수익   +3.03%   (분배금 가정 연 1.50% → 이 창 +0.03%p)" in out
    assert "① 초과수익 -0.03%p → 중단 쪽(≤0)" in out
    assert "② 낙폭     우리 -0.99% vs ETF -1.00% → 계속 쪽(개선)" in out
    assert "판정: **계속**" in out

    days = list(OURS)
    a = [OURS[d] / OURS[days[0]] for d in days]
    b = [ETF[d] / ETF[days[0]] for d in days]
    ra = [a[i] / a[i - 1] - 1 for i in range(1, len(a))]
    rb = [b[i] / b[i - 1] - 1 for i in range(1, len(b))]
    beta = statistics.covariance(ra, rb) / statistics.variance(rb)
    alpha = (a[-1] - 1) - beta * ((b[-1] - 1) + 0.015 * 5 / 245)
    assert f"베타 {beta:.2f} · 베타 보정 알파 {alpha * 100:+.2f}%p" in out


def test_장부가_비면_계산_불가_rc2(warehouse: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["--sandbox", "data/_empty"]) == 2
    assert "nav_daily 가 비었다" in capsys.readouterr().err
