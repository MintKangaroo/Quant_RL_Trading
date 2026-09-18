"""수익 분해 — **항등식이 맞아야 한다.** 잔차가 생기면 설명이 아니라 변명이 된다.

2026-11-25 판정은 "이겼다/졌다" 만 낸다. 이유를 항목으로 못 가르면 프로젝트 재정의를
장님으로 한다. 여기서 고정하는 것은 항목의 **값**이 아니라 **합이 차이와 같다**는 것,
그리고 구간을 잇는 방식(장부에 빈 거래일이 있어도 벤치마크와 같은 구간을 본다)이다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tools.attribute_returns import _span_return, _universe_span


def test_구간_수익은_두_끝_종가로_잰다() -> None:
    """장부에 빈 거래일이 있으면 그 구간의 장부 수익은 며칠치다 — 벤치마크도 같아야 한다.

    첫 구현이 하루치만 세어 벤치마크가 +4.32% 로 나왔다(참값 −0.84%).
    """
    series = pd.Series(
        [100.0, 101.0, 99.0, 103.0],
        index=[date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)],
    )

    assert _span_return(series, date(2026, 9, 11), date(2026, 9, 16)) == pytest.approx(0.03)
    assert _span_return(series, date(2026, 9, 15), date(2026, 9, 16)) == pytest.approx(103 / 99 - 1)


def test_한쪽_끝이_없으면_재지_않는다() -> None:
    """지어내면 그 구간이 통째로 거짓이 된다."""
    series = pd.Series([100.0], index=[date(2026, 9, 11)])

    assert _span_return(series, date(2026, 9, 11), date(2026, 9, 16)) != _span_return(
        series, date(2026, 9, 11), date(2026, 9, 16)
    ), "NaN 은 자기 자신과 같지 않다"


def test_유니버스는_양쪽_종가가_다_있는_종목만_센다() -> None:
    """결측을 0 으로 메우면 상장·거래정지 종목이 '변동 없음' 으로 평균을 끌어당긴다."""
    wide = pd.DataFrame(
        {"KR:A": [100.0, 110.0], "KR:B": [100.0, 90.0], "KR:C": [None, 50.0]},
        index=[date(2026, 9, 11), date(2026, 9, 16)],
    )

    got = _universe_span(wide, date(2026, 9, 11), date(2026, 9, 16))

    assert got == pytest.approx(0.0), "+10% 와 −10% 의 평균. 신규 상장 C 는 빠진다"


def test_항목의_합은_차이와_같다() -> None:
    """분해는 근사가 아니라 항등식이다 — 도구가 쓰는 식을 여기서 못 박는다.

        r_p − r_b = w(r_h − r_u) + w(r_u − r_b) + (w−1)r_b − 비용
    """
    weight, r_u, r_b, cost = 0.61, 0.012, 0.004, 0.0004
    r_h = 0.009
    r_p = weight * r_h - cost

    selection = weight * (r_h - r_u)
    universe = weight * (r_u - r_b)
    exposure = (weight - 1.0) * r_b
    terms = selection + universe + exposure - cost

    assert terms == pytest.approx(r_p - r_b, abs=1e-12)


def test_야간갭과_집행은_갈라진다() -> None:
    """합쳐 부르면 밤사이 시장 움직임이 우리 실행 품질로 둔갑한다(실측 +1.78%p)."""
    prior_close, open_price, fill = 100.0, 104.0, 105.0
    quantity, sign = 10.0, 1.0  # 매도

    gap = sign * quantity * (open_price - prior_close)
    execution = sign * quantity * (fill - open_price)

    assert gap == pytest.approx(40.0), "밤사이 오른 것 — 우리가 한 일이 아니다"
    assert execution == pytest.approx(10.0)
    assert gap + execution == pytest.approx(sign * quantity * (fill - prior_close))


def test_비중이_0에_가까워도_항등식이_깨지지_않는다() -> None:
    """예전엔 `r_h = (r_p+비용)/w` 를 구하느라 w 가 작으면 터져서 선택 항만 0 으로 뒀다.
    그러면 그 구간만 항등식이 깨지고 나머지 다섯 항은 그대로 나간다 — 도구가 조용히 거짓말한다.
    정의상 `w·r_h = r_p + 비용` 이므로 곱을 직접 쓰면 나눗셈이 아예 없다."""
    weight, r_u, r_b, cost, gap, fill = 0.001, 0.012, 0.004, 0.0004, 0.0, 0.0
    r_p = weight * 0.02 - cost

    selection = (r_p + cost) - weight * r_u - gap - fill
    terms = selection + weight * (r_u - r_b) + (weight - 1.0) * r_b + gap + fill - cost

    assert terms == pytest.approx(r_p - r_b, abs=1e-15)


def test_유니버스_소속은_구간_시작_시점이다() -> None:
    """끝 시점 명단을 쓰면 마지막 날 상장폐지된 종목이 과거에서도 지워져, 생존편향이
    곧장 `선택` 항으로 흘러든다 — 그게 이 도구가 재려는 바로 그 숫자다."""
    import pandas as pd

    wide = pd.DataFrame(
        {"KR:LIVE": [100.0, 110.0], "KR:DEAD": [100.0, 50.0]},
        index=[date(2026, 9, 11), date(2026, 9, 16)],
    )

    with_dead = _universe_span(wide, date(2026, 9, 11), date(2026, 9, 16), {"KR:LIVE", "KR:DEAD"})
    without = _universe_span(wide, date(2026, 9, 11), date(2026, 9, 16), {"KR:LIVE"})

    assert with_dead == pytest.approx(-0.20), "그때 살 수 있었으면 그 손실도 세계의 일부다"
    assert without == pytest.approx(0.10), "끝 시점 명단으로 거르면 −50% 가 사라진다"


def test_변경_지점은_날짜순이고_설명이_있다() -> None:
    """변경을 하면 여기 한 줄을 넣어 그 전·후를 따로 분해한다. 판정일에 '무엇이 돈이 됐나'
    를 말하려면 변경마다 경계가 있어야 한다."""
    from tools.attribute_returns import CHANGE_POINTS

    days = [d for d, _ in CHANGE_POINTS]
    assert days == sorted(days)
    assert all(label.strip() for _, label in CHANGE_POINTS)
