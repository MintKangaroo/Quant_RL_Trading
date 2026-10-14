"""새 정보 N1·N3·N7 원문 파서 — 실측 원문 모양(레이블 한 줄·값 한 줄)을 줄여 옮긴 표본."""

from __future__ import annotations

import pytest

from quant_rl_trading.collectors import dart_capital_events as ce

CB = """전환사채권 발행결정
1. 사채의 종류
회차
28
2. 사채의 권면(전자등록)총액 (원)
10,000,000,000
8. 사채발행방법
사모
9. 전환에 관한&cr; 사항
전환비율 (%)
100
전환가액 (원/주)
1,450
주식수
3,448,275
주식총수 대비&cr;비율(%)
5.06
최저 조정가액 (원)
1,015
"""

RIGHTS = """유상증자 결정
1. 신주의 종류와 수
보통주식 (주)
265,700
기타주식 (주)
-
3. 증자전 발행주식총수 (주)
보통주식 (주)
5,293,148
5. 증자방식
제3자배정증자
6. 신주 발행가액
보통주식 (원)
7,530
7-2. 기준주가에 대한 할인율 또는 할증율 (%)
0
"""

REFIX = """전환가액의 조정
1. 조정에 관한 사항
회차
상장여부
조정전 전환가액 (원)
조정후 전환가액 (원)
1
비상장
42,850
40,800
2. 전환가능주식수 변동
회차
미전환사채의 권면(전자등록)총액 (통화단위)
조정전 전환가능 주식수 (주)
조정후 전환가능 주식수 (주)
1
50,000,000,000
KRW : South-Korean Won
1,166,849
1,225,455
3. 조정사유
시가하락에 따른 전환가액 조정
"""

BB_RESULT = """3. 취득내용
일 자
종 류
2026년 07월 24일
보통주식
25,000
25,000
799
19,976,326
미래에셋증권
00111722
계
-
126,504
126,504
790
99,999,463
-
-
4. 취득 예정 내용과 실제 취득 내용의 일치 여부
취득예정금액(원)
취득가액총액(원)
보통주식
기타주식
보통주식
기타주식
138,696
-
126,504
-
-
-
주1) 설명
5. 취득후 자기주식등의 보유상황
"""

PLEDGE = """1. 담보제공자(최대주주) 관련 사항
- 명칭(성명, 법인명, 조합명, 단체명)
김영섬
- 공시일 현재
소유 주식 수(주)
2,863,877
지분율(%)
22.88
- 담보권 전부 실행시
소유 주식 수(주)
2,221,161
지분율(%)
17.74
2. 채무(차입)금액 총액(원)
6,500,000,000
4. 담보제공 내역
누적 담보제공 계약 건수(건)
4
누적 담보제공 주식 총수(주)
642,716
"""

HOLDER = """2. 발행주식수 정보
보통주식총수(1)
종류주식총수(2)
발행주식총수(1+2)
141,144,600
0
141,144,600
3. 보고의 개요
이번보고서제출일
2024-04-19
보통주식
78,896,435
55.90
합계
78,896,435
55.90
증감
보통주식
736,600
0.52
합계
736,600
0.52
"""


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("주요사항보고서(전환사채권발행결정)", "cb"),
        ("[기재정정]주요사항보고서(유상증자결정)", "rights"),
        ("최대주주변경을수반하는주식담보제공계약체결", "pledge"),
        ("최대주주변경을수반하는주식담보제공계약해제ㆍ취소등", "pledge_off"),
        ("최대주주변경", "control_change"),
        ("최대주주변경을수반하는주식양수도계약체결", None),
        ("신주인수권행사가액의조정", "bwrefix"),
        ("주요사항보고서(자기주식취득신탁계약체결결정)", "bb_trust"),
        ("주요사항보고서(자기주식취득결정)(자회사의주요경영사항)", None),
    ],
)
def test_kind_of(title: str, kind: str | None) -> None:
    assert ce.kind_of(title) == kind


def test_bond() -> None:
    p = ce.parse(CB, "cb")
    assert (p.amount, p.price, p.price2, p.shares, p.ratio, p.method) == (1e10, 1450, 1015, 3448275, 5.06, "사모")


def test_rights() -> None:
    p = ce.parse(RIGHTS, "rights")
    assert p.shares == 265700 and p.base_shares == 5293148 and p.method == "third"
    assert p.ratio == pytest.approx(265700 / 5293148 * 100)


def test_refix_added_shares() -> None:
    p = ce.parse(REFIX, "refix")
    assert p.shares == 1225455 - 1166849 and p.price == 40800 and p.price2 == 42850 and p.method == "down"


def test_buyback_result_rate() -> None:
    p = ce.parse(BB_RESULT, "bb_result")
    assert p.shares == 126504 and p.amount == 99999463
    assert p.ratio == pytest.approx(126504 / 138696 * 100)


def test_pledge_ratio_of_total_shares() -> None:
    p = ce.parse(PLEDGE, "pledge")
    total = 2863877 / 22.88 * 100
    assert p.shares == 642716 and p.ratio == pytest.approx(642716 / total * 100) and p.ratio2 == 17.74


def test_holder_change() -> None:
    p = ce.parse(HOLDER, "holder_change")
    assert (p.shares, p.ratio, p.base_shares, p.ratio2) == (736600, 0.52, 141144600, 55.90)


def test_missing_values_stay_none() -> None:
    p = ce.parse("전환사채권 발행결정\n2. 사채의 권면(전자등록)총액 (원)\n-\n", "cb")
    assert p.amount is None and not p.usable
