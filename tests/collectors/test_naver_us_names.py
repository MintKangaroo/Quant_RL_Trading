from quant_rl_trading.collectors.naver_us_names import SUFFIXES, not_exist, parse_basic


def test_parse_basic() -> None:
    out = parse_basic('{"stockName":"마이크론 테크놀로지","stockNameEng":"Micron Technology  Inc.","exchangeName":"NASDAQ"}')
    assert out == {"name_ko": "마이크론 테크놀로지", "name_en": "Micron Technology  Inc.", "exchange": "NASDAQ"}
    assert parse_basic('{"stockName":""}') is None


def test_not_exist_is_the_409_that_means_no_such_code() -> None:
    # 2026-09-16 실측 본문. 이걸 속도 제한으로 보고 2초 자면 없는 종목 1,801개에 3.5시간이 든다.
    assert not_exist(409, '{"code":"StockConflict","message":"Not Exist Master [ABT.O]"}')
    assert not not_exist(200, "")
    assert not not_exist(409, "")  # 본문이 다르면 속도 제한일 수 있다 — 기존 재시도로


def test_nyse_bare_suffix_is_tried() -> None:
    # JPM·ABT·A 는 접미사 없이만 200 이다 — 빠지면 NYSE 대형주 1,800개가 영영 '없음' 이다.
    assert "" in SUFFIXES
