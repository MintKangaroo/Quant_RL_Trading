from quant_rl_trading.collectors.naver_us_names import parse_basic


def test_parse_basic() -> None:
    out = parse_basic('{"stockName":"마이크론 테크놀로지","stockNameEng":"Micron Technology  Inc.","exchangeName":"NASDAQ"}')
    assert out == {"name_ko": "마이크론 테크놀로지", "name_en": "Micron Technology  Inc.", "exchange": "NASDAQ"}
    assert parse_basic('{"stockName":""}') is None
