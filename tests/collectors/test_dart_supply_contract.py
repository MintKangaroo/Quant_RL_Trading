"""DART 단일판매ㆍ공급계약 파서 — 형식 셋(구형·신형·정정)과 해지를 원문 줄 구조로 지킨다(2026-09-27)."""

from __future__ import annotations

from datetime import date

from quant_rl_trading.collectors.dart_supply_contract import kind_of, parse

#: 구형 — `계약금액(원)` 하나. 실제 원문(유한양행 2025-05-22)의 줄 구조.
OLD = "\n".join([
    "단일판매ㆍ공급계약 체결",
    "1. 판매ㆍ공급계약 구분", "상품공급",
    "2. 계약내역",
    "계약금액(원)", "88,814,945,010",
    "최근매출액(원)", "2,067,791,451,854",
    "매출액대비(%)", "4.30",
    "대규모법인여부", "해당",
    "3. 계약상대", "길리어드 사이언스(Gilead Sciences)",
    "- 회사와의 관계", "-",
    "5. 계약기간", "시작일", "2025-05-21", "종료일", "2026-12-31",
])

#: 신형 — 조건부 계약. 상대방 블록에도 `- 최근 매출액(원)` 이 있다(표본 1,200건 중 568건).
NEW = "\n".join([
    "2. 계약내역",
    "조건부 계약여부", "미해당",
    "확정 계약금액", "8,030,680,000",
    "조건부 계약금액", "-",
    "계약금액 총액(원)", "8,030,680,000",
    "최근 매출액(원)", "14,708,000",
    "매출액 대비(%)", "54,600.76",
    "3. 계약상대방", "Media Broadcast Satellite GmbH",
    "- 최근 매출액(원)", "89,242,066,891",
    "- 회사와의 관계", "계열회사",
    "5. 계약기간", "시작일", "2025-09-19", "종료일", "2028-12-31",
])

TERMINATION = "\n".join([
    "단일판매ㆍ공급계약해지",
    "2. 해지내역",
    "해지금액(원)", "93,990,000,000",
    "최근매출액(원)", "130,279,123,723",
    "매출액대비(%)", "72.15",
    "3. 계약상대방", "안마해상풍력 주식회사",
    "4. 계약기간", "시작일", "2025-07-07", "종료일", "-",
])


def test_구형은_계약금액과_비율을_읽는다() -> None:
    c = parse(OLD, kind="contract")
    assert c.usable
    assert c.amount == 88_814_945_010 and c.recent_sales == 2_067_791_451_854
    assert c.sales_ratio == 4.30 and c.counterparty.startswith("길리어드")
    assert c.period_start == date(2025, 5, 21) and c.period_end == date(2026, 12, 31)


def test_신형은_총액을_쓰고_상대방_매출액을_우리_것으로_읽지_않는다() -> None:
    c = parse(NEW, kind="contract")
    assert c.amount == 8_030_680_000
    assert c.recent_sales == 14_708_000        # 상대방의 89,242,066,891 이 아니다
    assert c.sales_ratio == 54_600.76          # 매출 없는 회사의 큰 계약 — 실제 값이다
    assert c.relation == "계열회사"            # 관계는 상대방 블록에만 있다


def test_해지는_해지금액을_읽고_kind_로_방향을_말한다() -> None:
    c = parse(TERMINATION, kind="termination")
    assert c.usable and c.kind == "termination"
    assert c.amount == 93_990_000_000 and c.sales_ratio == 72.15
    assert c.period_end is None                 # '-' 는 날짜가 아니다


def test_정정공시는_본문이_정정전_표를_이긴다() -> None:
    amended = "\n".join([
        "정정신고(보고)", "4. 정정사항", "정정항목", "정정전", "정정후",
        "2. 계약내역", "계약금액 총액(원)", "136,820,450,000", "138,201,867,600",
        NEW,
    ])
    assert parse(amended, kind="contract").amount == 8_030_680_000   # 본문(마지막)의 값


def test_비율_칸이_비면_두_값으로_계산한다() -> None:
    c = parse(OLD.replace("매출액대비(%)\n4.30", "매출액대비(%)\n-"), kind="contract")
    assert c.sales_ratio is not None and round(c.sales_ratio, 2) == 4.30


def test_금액이_공시유보면_쓰지_않는다() -> None:
    held = OLD.replace("계약금액(원)\n88,814,945,010", "계약금액(원)\n-").replace("4.30", "-")
    assert not parse(held, kind="contract").usable


def test_제목으로_체결_해지_자회사를_가른다() -> None:
    assert kind_of("단일판매ㆍ공급계약체결") == "contract"
    assert kind_of("[기재정정]단일판매ㆍ공급계약체결(자율공시)") == "contract"
    assert kind_of("단일판매ㆍ공급계약해지") == "termination"
    assert kind_of("단일판매ㆍ공급계약체결(자회사의 주요경영사항)") is None
    assert kind_of("주권매매거래정지(단일판매공급계약)") is None
    assert kind_of("현금ㆍ현물배당결정") is None
