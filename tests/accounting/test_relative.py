"""KODEX200 대비 성과 — 종료 판정·실자금 관문 1·대시보드 IR 이 같이 쓰는 함수 (accounting.md §8.2).

손계산과 맞춘다. 대조값은 numpy 가 아니라 ``statistics`` 로 따로 계산한다 — 같은 라이브러리로 검산하면
ddof 같은 실수를 같이 저지른다.

- 결손일(장부에 없는 거래일)은 건너뛴 구간을 한 걸음으로 잇고, **분배금은 넘은 거래일 수만큼** 붙는다.
- 롤링 창(``last``)은 장부 세션 N개, 결손일은 그 첫 세션부터 센다.
- 누적 초과 곡선의 끝점 == 초과수익(곡선과 숫자가 어긋나면 그 자체가 결함이다).
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from quant_rl_trading.accounting import ledger, relative
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF

D = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]
#: 하루 0.0001 — 손계산이 깔끔하게.
ANNUAL = 0.0001 * relative.TRADING_DAYS_PER_YEAR


def _series(values: dict[date, float]) -> pd.Series:
    return pd.Series(values, dtype=float)


@pytest.fixture
def pair() -> tuple[pd.Series, pd.Series]:
    ours = _series({D[0]: 100.0, D[1]: 101.0, D[3]: 99.0, D[4]: 100.5})      # 9/9 결손
    etf = _series({D[0]: 1000.0, D[1]: 1010.0, D[2]: 1005.0, D[3]: 1020.0, D[4]: 1015.0})
    return ours, etf


def test_손계산과_같다_결손일은_한_걸음으로_분배금은_거래일로(pair) -> None:  # type: ignore[no-untyped-def]
    ours, etf = pair
    r = relative.compare(ours, etf, window=D, annual_yield=ANNUAL)
    assert r is not None
    assert r.sessions == (D[0], D[1], D[3], D[4])
    assert r.missing == (D[2],)
    # 세션 3걸음이지만 거래일은 4일을 넘었다 — 분배금은 4일치. 세션으로 세면 3일치가 되어 벤치마크가 낮아진다.
    assert r.dividend == pytest.approx(0.0004)
    assert r.ours_total == pytest.approx(0.005)
    assert r.etf_price == pytest.approx(0.015)
    assert r.etf_total == pytest.approx(0.0154)
    assert r.excess == pytest.approx(0.005 - 0.0154)
    assert r.our_mdd == pytest.approx(0.99 / 1.01 - 1)
    assert r.etf_mdd == pytest.approx(1.015 / 1.02 - 1)

    a = [1.0, 1.01, 0.99, 1.005]
    b = [1.0, 1.01, 1.02, 1.015]
    ra = [a[i] / a[i - 1] - 1 for i in range(1, 4)]
    rb = [b[i] / b[i - 1] - 1 for i in range(1, 4)]
    div = [0.0001, 0.0002, 0.0001]                                                   # 9/8→9/10 걸음은 이틀
    e = [x - (y + z) for x, y, z in zip(ra, rb, div, strict=True)]
    root = math.sqrt(245)
    assert r.ir == pytest.approx(statistics.mean(e) / statistics.stdev(e) * root)
    assert r.tracking_error == pytest.approx(statistics.stdev(e) * root)
    # β 는 표본 공분산 / 표본 분산, ETF **가격** 수익으로(2026-09-18 등록 수식). α 는 총수익으로.
    beta = statistics.covariance(ra, rb) / statistics.variance(rb)
    assert r.beta == pytest.approx(beta)
    assert r.alpha == pytest.approx(0.005 - beta * 0.0154)


def test_누적_초과_곡선의_끝점은_초과수익이다(pair) -> None:  # type: ignore[no-untyped-def]
    ours, etf = pair
    r = relative.compare(ours, etf, window=D, annual_yield=ANNUAL)
    assert r is not None
    assert [d for d, _ in r.curve] == list(r.sessions)
    assert r.curve[0][1] == 0.0
    assert r.curve[2][1] == pytest.approx((0.99 - 1) - (0.02 + 0.0003))               # 9/10 — 분배금 3일치 누적
    assert r.curve[-1][1] == pytest.approx(r.excess)


def test_롤링_창은_장부_세션_N개다(pair) -> None:  # type: ignore[no-untyped-def]
    ours, etf = pair
    r = relative.compare(ours, etf, window=D, annual_yield=ANNUAL, last=3)
    assert r is not None
    assert r.sessions == (D[1], D[3], D[4])
    assert r.missing == (D[2],)                                                      # 창 첫 세션(9/8)부터 센다
    assert r.dividend == pytest.approx(0.0003)

    short = relative.compare(ours, etf, window=D, annual_yield=ANNUAL, last=2)
    assert short is not None and short.sessions == (D[3], D[4]) and short.missing == ()
    # 한 걸음 — 표준편차가 없다. 0 이 아니라 None(못 쟀음).
    assert short.ir is None and short.tracking_error is None
    assert short.beta is None and short.alpha is None


def test_through_뒤는_안_보고_세션이_둘_미만이면_None(pair) -> None:  # type: ignore[no-untyped-def]
    ours, etf = pair
    r = relative.compare(ours, etf, window=D, annual_yield=ANNUAL, through=D[2])
    assert r is not None and r.sessions == (D[0], D[1]) and r.missing == (D[2],)
    assert relative.compare(ours, etf, window=D[:1], annual_yield=ANNUAL) is None
    assert relative.compare(ours, etf, window=[], annual_yield=ANNUAL) is None


def test_베타는_걸음이_셋_이하면_안_잰다(pair) -> None:  # type: ignore[no-untyped-def]
    """판정 도구의 기존 조건 ``len(rb) > 2`` — 세 걸음부터 잰다."""
    ours, etf = pair
    two_steps = relative.compare(ours, etf, window=D, annual_yield=ANNUAL, last=3)
    assert two_steps is not None and two_steps.steps == 2 and two_steps.beta is None
    assert two_steps.ir is not None                                                  # IR 은 두 걸음이면 잰다


def test_같은_날_여러_행은_마지막_행이_그날이다() -> None:
    frame = pd.DataFrame({
        "valid_from": pd.to_datetime([
            datetime(2026, 9, 7, 7, 0, tzinfo=UTC), datetime(2026, 9, 7, 7, 30, tzinfo=UTC),
            datetime(2026, 9, 8, 7, 0, tzinfo=UTC),
        ]),
        "index_value": [100.0, 100.5, 101.0],
    })
    series = relative.session_series(frame, "index_value")
    assert series.to_dict() == {D[0]: 100.5, D[1]: 101.0}


def test_창고에서_읽는다_as_of_뒤는_안_보인다(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append(ledger.NAV_DAILY, [{
        "entity_id": ledger.ACCOUNT, "valid_from": ts(d.year, d.month, d.day, 7), "observed_at": ts(d.year, d.month, d.day, 7),
        "source": "test", "nav": 1e8 * v, "index_value": v,
    } for d, v in zip(D, [100.0, 101.0, 102.0, 103.0, 104.0], strict=True)], ingest_run_id="nav")
    store.append("indices", [{
        "entity_id": BENCHMARK_ETF, "valid_from": ts(d.year, d.month, d.day, 6, 50),
        "observed_at": ts(d.year, d.month, d.day, 6, 50), "source": "test", "market": "KR", "board": "ETF",
        "close": c,
    } for d, c in zip(D, [1000.0, 1001.0, 1002.0, 1003.0, 1004.0], strict=True)], ingest_run_id="etf")
    as_of = ts(2026, 9, 9, 8)
    ours = relative.book_index(store, as_of=as_of, lookback=None)
    etf = relative.etf_close(store, as_of=as_of, lookback=30)
    assert list(ours.index) == D[:3] and list(etf.index) == D[:3]
    # 체크인된 등록값(config/quant_rl_trading.yaml)을 그대로 읽는다 — 코드에 숫자가 없다.
    assert relative.distribution_yield(store, as_of=as_of) == pytest.approx(0.015)
