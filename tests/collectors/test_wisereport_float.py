from quant_rl_trading.collectors.wisereport_float import parse_company_page

HTML = '<th>발행주식수/유동비율</th><td class="num">234,000,000주 / 20.29%</td><th>외국인지분율</th>'


def test_parse_float_ratio() -> None:
    out = parse_company_page(HTML)
    assert out == {"shares_outstanding": 234_000_000.0, "float_ratio": 0.2029}


def test_missing_cell_is_none() -> None:
    assert parse_company_page("<html>아무것도 없다</html>") is None
    assert parse_company_page(HTML.replace("20.29", "0")) is None
