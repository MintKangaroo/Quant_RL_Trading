"""DART `매출액또는손익구조30%(대규모법인은15%)이상변동` 원문 → 숫자. 6차 G12 의 입력.

구조화 API 가 없는 공시다(`fnlttMultiAcnt` 는 확정 재무만 준다). 원문은
`collect_filing_texts.py` 가 받아 둔 텍스트(`dart_documents.read_text`)다. 표가
**레이블 한 줄 · 값 여러 줄**로 풀려 있고, 형식이 잠정실적보다 훨씬 고르다:

    3. 매출액 또는 손익구조변동내용(단위: 원)      ← 단위는 이 줄에 있다
    당해사업연도
    직전사업연도
    증감금액
    증감비율(%)
    흑자적자전환여부

    - 매출액
    259,548,871,126        ← 당해
    202,861,487,817        ← 직전
    56,687,383,309         ← 증감금액
    27.9                   ← 증감비율(%)
    -                      ← 흑자적자전환여부

**증감비율 칸은 읽지 않고 두 값으로 계산한다** — G8(잠정실적)의 교훈 ②다. 흑자·적자
전환이면 비율 칸이 비고, 분모가 음수면 부호가 뒤집힌 값이 적혀 오기도 한다. 전환 여부는
별도 칸(`흑자적자전환여부`)을 그대로 읽는다.

레이블이 `- ` 로 시작한다(`- 매출액`). 정정 공시(`[기재정정]`)는 본문 앞에
`정정전/정정후` 표가 붙으므로 **마지막 occurrence** 를 읽는다 — 본문이 표 뒤에 온다.

`(자회사의 주요경영사항)` 은 자회사 실적이라 뺀다(G8 과 같은 규칙).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

UNITS = {"원": 1.0, "천원": 1e3, "백만원": 1e6, "억원": 1e8}
#: 읽을 항목. 값은 표의 열 이름 접두어다.
ITEMS = {"매출액": "sales", "영업이익": "op", "당기순이익": "net"}
TURNS = ("흑자전환", "적자전환", "적자지속", "흑자지속")

_UNIT = re.compile(r"단위\s*[:：]\s*(천원|백만원|억원|원)")
_DATE = re.compile(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})")


@dataclass(frozen=True)
class PlChange:
    unit: float
    basis: str | None                  # consolidated | separate
    sales_cur: float | None
    sales_base: float | None
    op_cur: float | None
    op_base: float | None
    net_cur: float | None
    net_base: float | None
    op_turn: str                       # 흑자전환 | 적자전환 | 적자지속 | 흑자지속 | ""
    period_end: date | None            # 당해사업연도 종료일

    @property
    def usable(self) -> bool:
        """영업이익 두 값이 있으면 쓸 수 있다 — G12 의 주 피처가 그것이다."""
        return self.op_cur is not None and self.op_base is not None


def is_target(title: str) -> bool:
    """이 파서가 읽을 공시인가. 자회사 실적은 뺀다."""
    text = title.replace(" ", "")
    return "손익구조" in text and "자회사의주요경영사항" not in text


def _number(token: str) -> float | None:
    token = token.strip().replace(",", "").replace("△", "-")
    if token in ("", "-", "해당사항없음"):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _unit(lines: list[str]) -> float | None:
    """단위는 3절 머리줄에 있다. 없으면 어떤 표의 단위인지 말할 수 없어 결측이다."""
    for line in lines:
        if "손익구조" in line and "단위" in line:
            match = _UNIT.search(line)
            if match:
                return UNITS[match.group(1)]
    for line in lines:  # 절 머리줄이 깨진 형식 — 문서 어디든 단위 선언 하나를 쓴다
        match = _UNIT.search(line)
        if match:
            return UNITS[match.group(1)]
    return None


def _basis(lines: list[str]) -> str | None:
    """`1. 재무제표의 종류` → 연결 | 개별(별도)."""
    for i, line in enumerate(lines):
        if "재무제표의종류" in line.replace(" ", ""):
            value = lines[i + 1] if i + 1 < len(lines) else ""
            if "연결" in value:
                return "consolidated"
            if "개별" in value or "별도" in value:
                return "separate"
            return None
    return None


def _period_end(lines: list[str]) -> date | None:
    """`2. 결산기간` 의 `- 종료일` 첫 값(당해사업연도)."""
    for i, line in enumerate(lines):
        if line.lstrip("-").strip() == "종료일" and i + 1 < len(lines):
            match = _DATE.search(lines[i + 1])
            if match:
                try:
                    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                except ValueError:
                    return None
    return None


def parse(text: str) -> PlChange | None:
    """원문 → PlChange. 단위를 못 읽으면 None(잠정실적과 같은 규칙: 단위 없는 공시는 결측)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    unit = _unit(lines)
    if unit is None:
        return None
    values: dict[str, tuple[float | None, float | None]] = {}
    turn = ""
    for i, line in enumerate(lines):
        key = ITEMS.get(line.lstrip("-").strip())
        if key is None:
            continue
        cells = lines[i + 1:i + 6]
        if len(cells) < 2:
            continue
        cur, base = _number(cells[0]), _number(cells[1])
        if cur is None and base is None:
            continue
        # **마지막 것이 이긴다** — 정정 공시는 본문 앞에 정정전/정정후 표가 붙는다.
        values[key] = (
            None if cur is None else cur * unit,
            None if base is None else base * unit,
        )
        if key == "op":
            turn = next((c for c in cells if c in TURNS), "")
    sales = values.get("sales", (None, None))
    op = values.get("op", (None, None))
    net = values.get("net", (None, None))
    return PlChange(
        unit=unit,
        basis=_basis(lines),
        sales_cur=sales[0], sales_base=sales[1],
        op_cur=op[0], op_base=op[1],
        net_cur=net[0], net_base=net[1],
        op_turn=turn,
        period_end=_period_end(lines),
    )
