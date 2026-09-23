"""DART 영업(잠정)실적(공정공시) 원문 → 숫자. 6차 G8(`docs/protocols/ranker-sources-g8-prelim-earnings-2026-09.md`)의 입력.

원문은 `collect_filing_texts.py` 가 받아 둔 텍스트(`dart_documents.read_text`)다. 표가 줄 단위로 풀려 있다:

    구분(단위 : 백만원, %)            ← 단위
    매출액 / 당해실적 / 88,770 / 80,832 / 9.8 / - / 80,467 / 10.3 / -        ← 신형: 당기·전기·%·전환·전년동기·%·전환
    영업이익 / 당해실적 / 15,185 / 6,078 / +9,107 / (+149.8%) / 19,975 / ...  ← 구형: 당기·전기·증감액·(%)·전년동기·...

    영업이익 / 당해실적 / 1,985 / 2,089 / -5.0% / 3,479 / -42.9%                  ← 약식: 당기·전기·%·전년동기·%

**당기는 늘 첫 칸이다. 전년동기는 약식이면 넷째, 나머지는 다섯째 칸이다** — 약식은 셋째 칸이 "%"로 끝나거나 전환 문구다.
그 둘만 읽고 증감률은 직접 계산한다(증감률 칸은 형식마다 자리가 다르고, 흑자·적자 전환이면 비어 있다).
2026-09-23 판정 창 밖 원문 670건: 신형·구형만 읽으면 69%, 약식을 더하면 82%(나머지 대부분은 값이 전부 "-" 인 공시).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNITS = {"원": 1.0, "천원": 1e3, "백만원": 1e6, "억원": 1e8}
TURNS = ("흑자전환", "적자전환", "적자지속", "흑자지속")
ITEMS = {"매출액": "sales", "영업이익": "op"}


@dataclass(frozen=True)
class Prelim:
    unit: float
    sales_cur: float | None
    sales_base: float | None
    op_cur: float | None
    op_base: float | None

    @property
    def usable(self) -> bool:
        return self.op_cur is not None and self.op_base is not None


def _number(token: str) -> float | None:
    token = token.strip().replace(",", "").replace("△", "-")
    if token in ("", "-") or token.startswith("("):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _unit(lines: list[str]) -> float | None:
    for line in lines:
        if "단위" in line:
            match = re.search(r"단위\s*[:：]\s*(천원|백만원|억원|원)", line)
            if match:
                return UNITS[match.group(1)]
    return None


def parse(text: str) -> Prelim | None:
    """원문 → Prelim. 단위를 못 읽으면 None(등록: 단위 없는 공시는 결측)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    unit = _unit(lines)
    if unit is None:
        return None
    values: dict[str, tuple[float | None, float | None]] = {}
    for i, line in enumerate(lines):
        key = ITEMS.get(line)
        if key is None or key in values or i + 1 >= len(lines) or lines[i + 1] != "당해실적":
            continue
        cells = lines[i + 2:i + 9]
        if len(cells) < 5:
            continue
        short_form = cells[2].endswith("%") or cells[2] in TURNS
        cur, base = _number(cells[0]), _number(cells[3] if short_form else cells[4])
        values[key] = (None if cur is None else cur * unit, None if base is None else base * unit)
    sales = values.get("sales", (None, None))
    op = values.get("op", (None, None))
    return Prelim(unit=unit, sales_cur=sales[0], sales_base=sales[1], op_cur=op[0], op_base=op[1])


def basis(title: str) -> str | None:
    """제목 → 'consolidated' | 'separate' | None(자회사 실적은 뺀다 — 등록)."""
    if "자회사의 주요경영사항" in title or "잠정" not in title:
        return None
    return "consolidated" if "연결" in title else "separate"
