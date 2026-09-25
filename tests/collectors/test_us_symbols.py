"""미장 증권 종류 분류 — 2026-09-25 실측 증권명으로."""

from __future__ import annotations

import pytest

from quant_rl_trading.collectors.us_symbols import classify, merge, parse


@pytest.mark.parametrize(("name", "etf", "expected"), [
    ("Apple Inc. - Common Stock", False, "common"),
    ("Alphabet Inc. - Class A Common Stock", False, "common"),
    ("Akari Therapeutics Plc - American Depositary Shares", False, "adr"),
    ("Nanobiotix S.A. - ADSs", False, "adr"),
    ("T-Mobile US, Inc. - 6.250% Senior Notes due 2069", False, "note"),
    ("AGNC Investment Corp. - Depositary Shares Each Representing a 1/1,000th Interest in a Share of 7.75% Series G", False, "preferred"),
    ("EPR Properties Series E Cumulative Conv Pfd S", False, "preferred"),
    ("BrightSpring Health Services, Inc. - Tangible Equity Unit", False, "unit"),
    ("MicroSectors FANG  Index 2X Leveraged ETNs due January 8, 2038", False, "etn"),
    ("Pimco High Income Fund", False, "fund"),
    ("United States Commodity Index Fund ETV", True, "etf"),
    ("Hess Midstream LP Class A Representing Limited Partner Interests", False, "common"),
    ("Trane Technologies plc", False, "other"),
])
def test_증권명으로_분류한다(name: str, etf: bool, expected: str) -> None:
    assert classify(name, etf=etf) == expected


def test_두_파일의_머리가_달라도_읽는다() -> None:
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n" \
             "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\nFile Creation Time: 0925202612:00|||||||\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n" \
            "USCI|United States Commodity Index Fund ETV|P|USCI|Y|100|N|USCI\nZBZX|BATS BZX Exchange test issue|Z|ZBZX|N|100|Y|ZBZX\n"
    m = merge([parse(nasdaq), parse(other)])
    assert m["AAPL"].instrument == "common" and m["USCI"].instrument == "etf" and m["ZBZX"].test_issue


def test_머리가_바뀌면_멈춘다() -> None:
    with pytest.raises(ValueError):
        parse("Ticker|Name\nAAPL|Apple\n")
