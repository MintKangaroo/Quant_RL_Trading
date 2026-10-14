"""진단 도구 둘의 봉인선·구간 규칙 (diag_confidence_drop · diag_n3_ladder, 2026-10-14).

증명하는 것: 상한 9/30 뒤 세션은 거부한다 · N3 구간 셋은 겹치지 않고 상한을 넘지 않으며 공매도 금지기 달력과 같다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tools import diag_confidence_drop as D
from tools import diag_n3_ladder as N
from tools import eval_ladder as L


def test_상한_뒤_세션은_거부() -> None:
    D._cap([date(2026, 9, 30)], "ok")
    with pytest.raises(SystemExit):
        D._cap([date(2026, 10, 1)], "봉인")
    assert D.CAP_DAY == L.DATE_CAP


def test_요약은_빈_계열도_견딘다() -> None:
    assert D._summ(pd.Series(dtype=float))["n"] == 0
    s = D._summ(pd.Series([0.1, 0.2, 0.3] * 5))
    assert s["n"] == 15 and s["mean"] == pytest.approx(0.2)


def test_N3_구간은_겹치지_않고_상한_안() -> None:
    spans = list(N.WINDOWS.values())
    assert N.WINDOWS["금지기"] == (date(2023, 11, 6), date(2025, 3, 30))
    for (_, hi), (lo, _) in zip(spans, spans[1:]):
        assert hi < lo
    assert all(hi <= L.DATE_CAP for _, hi in spans)
