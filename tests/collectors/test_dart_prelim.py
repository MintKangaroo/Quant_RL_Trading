"""DART 잠정실적 파서 — 원문 두 형식(신형·구형)을 줄 단위로 옮긴 것으로 지킨다(판정 창 밖 원문의 모양, 2026-09-23 확인)."""

from __future__ import annotations

from quant_rl_trading.collectors.dart_prelim import basis, parse

NEW_FORMAT = "\n".join([
    "1. 연결실적내용", "구분(단위 : 백만원, %)", "당기실적", "전기실적",
    "매출액", "당해실적", "88,770", "80,832", "9.8", "-", "80,467", "10.3", "-",
    "누계실적", "169,602", "-", "-", "-", "173,762", "-2.4", "-",
    "영업이익", "당해실적", "3,997", "2,100", "90.3", "-", "-1,250", "-", "흑자전환",
])
OLD_FORMAT = "\n".join([
    "구분(단위 : 억원, %)",
    "매출액", "당해실적", "1,200", "1,100", "+100", "(+9.1%)", "1,000", "+200", "(+20.0%)",
    "영업이익", "당해실적", "15,185", "6,078", "+9,107", "(+149.8%)", "19,975", "-4,790", "(-24.0%)",
])
EMPTY = "\n".join(["단위 : 백만원, %", "영업이익", "당해실적", "-", "-", "-", "-", "-", "누계실적", "-"])


def test_신형은_당기와_전년동기를_원으로_읽는다() -> None:
    p = parse(NEW_FORMAT)
    assert p is not None and p.usable
    assert p.sales_cur == 88_770e6 and p.sales_base == 80_467e6
    assert p.op_cur == 3_997e6 and p.op_base == -1_250e6   # 흑자전환 — 증감률 칸이 비어도 값으로 계산한다


def test_구형도_다섯째_칸이_전년동기다() -> None:
    p = parse(OLD_FORMAT)
    assert p is not None and p.op_cur == 15_185e8 and p.op_base == 19_975e8 and p.sales_base == 1_000e8


def test_값이_없거나_단위가_없으면_쓰지_않는다() -> None:
    p = parse(EMPTY)
    assert p is not None and not p.usable
    assert parse(NEW_FORMAT.replace("구분(단위 : 백만원, %)", "구분")) is None


def test_자회사_실적과_잠정이_아닌_공시는_뺀다() -> None:
    assert basis("연결재무제표기준영업(잠정)실적(공정공시)") == "consolidated"
    assert basis("[기재정정]영업(잠정)실적(공정공시)") == "separate"
    assert basis("영업(잠정)실적(공정공시)(자회사의 주요경영사항)") is None
    assert basis("분기보고서 (2026.06)") is None


def test_약식은_넷째_칸이_전년동기다() -> None:
    short = "\n".join(["구분(단위 : 백만원, %)",
                        "매출액", "당해실적", "10,000", "9,000", "11.1%", "8,000", "25.0%",
                        "영업이익", "당해실적", "-22,409", "121,398", "적자전환", "76,748", "적자전환", "누계실적", "414,139"])
    p = parse(short)
    assert p is not None and p.op_cur == -22_409e6 and p.op_base == 76_748e6 and p.sales_base == 8_000e6
