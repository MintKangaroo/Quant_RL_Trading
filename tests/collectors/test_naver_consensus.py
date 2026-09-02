from datetime import UTC, date, datetime

import pytest

from quant_rl_trading.collectors import naver_consensus as nc

HTML = '''<table summary="투자의견 정보" class="rwidth"><caption>투자의견</caption>
<tr><th scope="row">투자의견<span class="bar">l</span>목표주가</th><td> <span class="f_up"><em>4.05</em>매수</span>
<span class="bar">l</span> <em>487,045</em> </td></tr></table>
<table summary="PER/EPS 정보"><tr><td><em id="_per">11.24</em>배 <em id="_eps">22,292</em>원</td></tr>
<tr><td><em id="_cns_per">5.00</em>배 <em id="_cns_eps">48,339</em>원</td></tr>
<tr><td><em id="_pbr">2.91</em>배</td></tr><tr><td><em id="_dvr">0.67</em>%</td></tr></table>'''


def test_parse_main_page() -> None:
    p = nc.parse_main_page(HTML)
    assert p["rating"] == 4.05 and p["target_price"] == 487045.0
    assert p["eps_ttm"] == 22292.0 and p["per_ttm"] == 11.24
    assert p["eps_fwd"] == 48339.0 and p["per_fwd"] == 5.0
    assert p["pbr"] == 2.91 and p["dividend_yield"] == 0.67


def test_no_coverage_makes_no_row() -> None:
    p = nc.parse_main_page('<table summary="PER/EPS 정보"><em id="_eps">1</em></table>')
    assert nc.row_for("000000", day=date(2026, 9, 2), observed_at=datetime(2026, 9, 2, tzinfo=UTC), parsed=p) is None


def test_not_a_stock_page_raises() -> None:
    with pytest.raises(nc.ConsensusUnavailable):
        nc.parse_main_page("<html>없음</html>")
