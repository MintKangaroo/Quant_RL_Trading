"""DART `단일판매ㆍ공급계약체결`·`해지` 원문 → 숫자. 6차 G13 의 입력.

원문은 `collect_filing_texts.py` 가 받아 둔 텍스트(`dart_documents.read_text`)다.
표가 **레이블 한 줄 · 값 한 줄**로 풀려 있어 G8(잠정실적)보다 다루기 쉽다:

    2. 계약내역
    계약금액(원)
    88,814,945,010
    최근매출액(원)
    2,067,791,451,854
    매출액대비(%)
    4.30

형식 변형이 셋이다.

1. **구형** — `계약금액(원)`.
2. **신형**(조건부 계약) — `확정 계약금액` / `조건부 계약금액` / `계약금액 총액(원)`.
   총액이 확정+조건부이므로 **총액을 먼저 쓴다**. 공백이 들어가는 자리가 서로 달라
   (`최근매출액(원)` vs `최근 매출액(원)`) 레이블은 공백을 지워 맞춘다.
3. **정정**(`[기재정정]`) — 본문 앞에 `정정전 / 정정후` 표가 붙는다. 같은 레이블이
   두 번 나오므로 **마지막 것을 읽는다** — 본문이 표 뒤에 온다.

**`-` 로 시작하는 레이블은 계약상대방의 것이다.** 신형은 상대방 블록에도
`- 최근 매출액(원)` 이 있고(표본 1,200건 중 568건), 그걸 우리 매출액으로 읽으면
비율이 통째로 뒤집힌다. 그래서 값이 있어도 버린다.

해지 공시는 `해지금액(원)` 이고 나머지 칸이 같다 — 같은 파서로 읽고 `kind` 로 가른다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: 계약금액. **순서가 규칙이다** — 총액이 확정+조건부라 먼저다.
AMOUNT_LABELS = ("계약금액총액(원)", "계약금액(원)", "확정계약금액")
#: 해지 공시의 금액 칸.
TERMINATION_LABELS = ("해지금액(원)",)
SALES_LABELS = ("최근매출액(원)",)
RATIO_LABELS = ("매출액대비(%)",)
COUNTERPARTY_LABELS = ("계약상대방", "계약상대")
RELATION_LABELS = ("회사와의관계",)
START_LABELS = ("시작일",)
END_LABELS = ("종료일",)

_DATE = re.compile(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})")


@dataclass(frozen=True)
class SupplyContract:
    kind: str                     # contract | termination
    amount: float | None          # 원
    recent_sales: float | None    # 원
    sales_ratio: float | None     # %
    counterparty: str
    relation: str
    period_start: date | None
    period_end: date | None

    @property
    def usable(self) -> bool:
        """비율을 말할 수 있는가. 금액만 있고 분모가 없으면 쓸 수 없다."""
        return self.amount is not None and (
            self.sales_ratio is not None or (self.recent_sales or 0) > 0
        )


def kind_of(title: str) -> str | None:
    """제목 → 'contract' | 'termination' | None(우리 공시가 아니다).

    `(자회사의 주요경영사항)` 은 자회사 계약이라 뺀다 — G8 과 같은 규칙.
    """
    text = title.replace(" ", "")
    if "단일판매" not in text and "공급계약" not in text:
        return None
    if "자회사의주요경영사항" in text:
        return None
    if "거래정지" in text:      # 주권매매거래정지(단일판매공급계약) — 계약 공시가 아니다
        return None
    if "해지" in text:
        return "termination"
    if "체결" in text:
        return "contract"
    return None


def _number(token: str) -> float | None:
    token = token.strip().replace(",", "").replace("△", "-").rstrip("%")
    if token in ("", "-", "미정", "해당사항없음"):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _labels(text: str, *, dashed: bool = False) -> dict[str, str]:
    """정규화한 레이블 → **마지막** 값.

    ``dashed=False`` 는 `-` 로 시작하는 레이블을 버린다 — **상대방 블록의 것**이다.
    ``dashed=True`` 는 그 블록만 읽는다(`- 회사와의 관계` 처럼 거기밖에 없는 칸).
    """
    lines = [line.strip() for line in text.splitlines()]
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        if not line or line.startswith("-") is not dashed:
            continue
        key = line.lstrip("-").replace(" ", "").replace("ㆍ", "")
        value = next((v.strip() for v in lines[i + 1:i + 2] if v.strip()), "")
        if value:
            out[key] = value
    return out


def _pick(labels: dict[str, str], wanted: tuple[str, ...]) -> str | None:
    """레이블 이름이 ``wanted`` 로 끝나는 첫 후보. 앞에 절 번호가 붙는 형식이 있다."""
    for name in wanted:
        if name in labels:
            return labels[name]
        for key, value in labels.items():
            if key.endswith(name):
                return value
    return None


def _date(token: str | None) -> date | None:
    if not token:
        return None
    match = _DATE.search(token)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse(text: str, *, kind: str) -> SupplyContract:
    """원문 → SupplyContract. 값이 없는 칸은 None 이다(공시마다 비는 칸이 다르다)."""
    labels = _labels(text)
    amount_labels = TERMINATION_LABELS if kind == "termination" else AMOUNT_LABELS
    amount = _number(_pick(labels, amount_labels) or "")
    if amount is None and kind == "termination":
        amount = _number(_pick(labels, AMOUNT_LABELS) or "")
    sales = _number(_pick(labels, SALES_LABELS) or "")
    ratio = _number(_pick(labels, RATIO_LABELS) or "")
    if ratio is None and amount is not None and sales is not None and sales > 0:
        ratio = amount / sales * 100.0
    return SupplyContract(
        kind=kind,
        amount=amount,
        recent_sales=sales,
        sales_ratio=ratio,
        counterparty=(_pick(labels, COUNTERPARTY_LABELS) or "")[:120],
        # 관계는 상대방 블록에만 있다(`- 회사와의 관계`). 숫자가 아니라 뒤집힐 위험이 없다.
        relation=(_pick(_labels(text, dashed=True), RELATION_LABELS) or "")[:60],
        period_start=_date(_pick(labels, START_LABELS)),
        period_end=_date(_pick(labels, END_LABELS)),
    )
