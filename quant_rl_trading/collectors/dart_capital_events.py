"""DART 자본·지배 사건 원문 → 숫자. 새 정보 N1·N3·N7 의 입력(2026-10-14, 사용자 10/13 "다 돌려").

원문은 `collect_filing_texts.py`·`backfill_filing_texts.py`·`backfill_n_texts.py` 가 받아 둔 텍스트
(`dart_documents.read_text`)다. G13(`dart_supply_contract`)과 같이 표가 **레이블 한 줄 · 값 한 줄** 로 풀려 있다.

| 종류(`kind`) | 제목 | 뽑는 것 | 쓰는 재료 |
|---|---|---|---|
| `cb` · `bw` · `eb` | 주요사항보고서(전환사채권·신주인수권부사채권·교환사채권 발행결정) | 권면총액·전환가액·최저 조정가액·발행할 주식수·주식총수 대비(%)·사모 여부 | N1 잠재 희석 |
| `rights` | 주요사항보고서(유상증자결정) | 신주 수·증자 전 주식총수·증자방식(제3자/주주/일반공모)·할인율 | N1 실제 희석 |
| `refix` · `bwrefix` | 전환가액의조정 · 신주인수권행사가액의조정 | 조정 전후 가액·전환가능 주식수(회차 합) | N1 리픽싱 |
| `bb_decision` · `bb_trust` | 자기주식취득결정 · 자기주식취득신탁계약체결결정 | 예정 주식수·금액(직접) / 계약금액(신탁) | N7 |
| `bb_result` · `bb_trust_end` | 자기주식취득결과보고서 · 신탁계약해지결과보고서 | 실제 취득 주식수·금액, 예정 대비 | N7 이행률 |
| `pledge` · `pledge_off` | 최대주주변경을수반하는주식담보제공계약체결 · 해제ㆍ취소등 | 누적 담보 주식수·최대주주 지분율·전부 실행 시 지분율 | N3 |
| `holder_change` | 최대주주등소유주식변동신고서 | 최대주주등 지분 증감(%p)·발행주식총수 | N3 |
| `control_change` | 최대주주변경(담보·양수도 계약 아닌 실제 변경) | 변경 전후 소유비율·사유 | N3 |

**정정(`[기재정정]`)은 같은 레이블이 두 번 나온다** — 본문이 정정 표 뒤에 오므로 **마지막** 값을 읽는다(G13 규칙).
**`(자회사의주요경영사항)` 은 뺀다** — 자회사 사건이다(G8·G13 규칙).
값을 못 읽으면 None 이다. 0 으로 채우지 않는다 — '없음' 과 '못 읽음' 은 다른 사실이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

#: (종류, 공백을 뗀 제목 정규식). **순서가 규칙이다** — 위에서부터 첫 일치. 담보 계약은 '최대주주변경' 보다 먼저.
TITLE_KINDS: tuple[tuple[str, str], ...] = (
    ("pledge_off", r"주식담보제공계약해제"),
    ("pledge", r"최대주주변경을수반하는주식담보제공계약체결"),
    ("holder_change", r"최대주주등소유주식변동신고서"),
    ("control_change", r"^(?:\[[^\]]*\])?최대주주변경$"),
    ("cb", r"전환사채권발행결정"),
    ("bw", r"신주인수권부사채권발행결정"),
    ("eb", r"교환사채권발행결정"),
    ("rights", r"유상증자결정"),
    ("bwrefix", r"신주인수권행사가액의조정|행사가액의조정"),
    ("refix", r"전환가액의조정"),
    ("bb_trust", r"자기주식취득신탁계약체결결정"),
    ("bb_decision", r"자기주식취득결정"),
    ("bb_result", r"자기주식취득결과보고서"),
    ("bb_trust_end", r"신탁계약해지결과보고서"),
)

#: 원문이 아직 창고에 없는 유형 — `backfill_n_texts.py` 가 받는다(2026-10-14 실측: 담보·조정·해지결과·소유변동 원문 0%).
NEEDS_TEXT = ("pledge", "pledge_off", "refix", "bwrefix", "bb_trust_end", "holder_change")

_NUM = re.compile(r"^[△\-−]?[\d,]+(?:\.\d+)?%?$")
_DATE = re.compile(r"(\d{4})\s*[-.년]\s*(\d{1,2})\s*[-.월]\s*(\d{1,2})")


def kind_of(title: str) -> str | None:
    """제목 → 종류 | None. 자회사 공시·거래정지 공시는 None."""
    text = str(title or "").replace(" ", "")
    if "자회사의주요경영사항" in text or "거래정지" in text:
        return None
    for kind, pattern in TITLE_KINDS:
        if re.search(pattern, text):
            return kind
    return None


def number(token: str | None) -> float | None:
    if token is None:
        return None
    t = str(token).strip().replace(",", "").replace("△", "-").replace("−", "-").rstrip("%").strip()
    if t in ("", "-", "--", "미정", "해당사항없음", "해당없음"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _lines(text: str) -> list[str]:
    return [ln.strip().replace("&cr;", "") for ln in str(text).splitlines() if ln.strip()]


def _key(line: str) -> str:
    return line.replace(" ", "").replace("ㆍ", "").replace("&cr;", "")


def _find(lines: list[str], pattern: str, *, last: bool = True, start: int = 0) -> int | None:
    """정규식(공백 뗀 줄)에 맞는 줄 번호. 기본은 마지막(정정 표 뒤 본문)."""
    hits = [i for i in range(start, len(lines)) if re.search(pattern, _key(lines[i]))]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def _value_after(lines: list[str], pattern: str, *, sub: str | None = None, span: int = 6, last: bool = True,
                 numeric: bool = True) -> str | None:
    """레이블 줄 다음의 값. `sub` 가 있으면 레이블 뒤 `span` 줄 안의 소제목(예: 보통주식) 다음 값."""
    i = _find(lines, pattern, last=last)
    if i is None:
        return None
    if sub is not None:
        j = next((k for k in range(i + 1, min(i + 1 + span, len(lines))) if re.search(sub, _key(lines[k]))), None)
        if j is None:
            return None
        i = j
    for k in range(i + 1, min(i + 1 + span, len(lines))):
        v = lines[k]
        if not numeric:
            return v
        if _NUM.match(v.replace(" ", "")) or v.strip() == "-":
            return v
    return None


def _date(token: str | None) -> date | None:
    if not token:
        return None
    m = _DATE.search(str(token))
    if m is None:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _numbers_between(lines: list[str], start_pat: str, end_pat: str) -> list[float]:
    """두 레이블 사이의 숫자 줄들(마지막 시작 레이블 기준)."""
    i = _find(lines, start_pat)
    if i is None:
        return []
    j = _find(lines, end_pat, last=False, start=i + 1)
    out = []
    for ln in lines[i + 1:(j if j is not None else len(lines))]:
        if _NUM.match(ln.replace(" ", "")):
            v = number(ln)
            if v is not None:
                out.append(v)
    return out


# --------------------------------------------------------------------------- 결과 묶음


@dataclass(frozen=True)
class Parsed:
    """종류마다 쓰는 칸만 찬다. 나머지는 None."""

    kind: str
    amount: float | None = None          # 원 — 권면총액·취득예정금액·계약금액·실제 취득금액·채무(차입)금액
    shares: float | None = None          # 주 — 발행할 주식수·신주 수·예정/실제 취득 주식수·누적 담보 주식수·증감 주식수
    base_shares: float | None = None     # 주 — 증자 전 주식총수·발행주식총수(분모)
    ratio: float | None = None           # % — 주식총수 대비·지분율(공시일 현재)·이행률(실제/예정 ×100)·지분 증감 %p
    price: float | None = None           # 원 — 전환가액·신주 발행가액·조정 후 가액
    price2: float | None = None          # 원 — 최저 조정가액·조정 전 가액
    ratio2: float | None = None          # % — 할인율·담보 전부 실행 시 지분율·변경 후 소유비율
    method: str = ""                     # 사모/공모 · 제3자/주주/공모 · direct/trust · 사유
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return any(v is not None for v in (self.amount, self.shares, self.ratio, self.price))


def parse_bond(text: str, kind: str) -> Parsed:
    """CB·BW·EB 발행결정."""
    ln = _lines(text)
    amount = number(_value_after(ln, r"^2\.사채의권면(\(전자등록\))?총액") or _value_after(ln, r"사채의권면(\(전자등록\))?총액\(원\)"))
    price = number(_value_after(ln, r"^(전환|행사|교환)가액\(원/?주\)|^(전환|행사|교환)가액\(원\)"))
    floor = number(_value_after(ln, r"^최저조정가액\(원\)"))
    shares = number(_value_after(ln, r"^주식수$"))
    ratio = number(_value_after(ln, r"^주식총수대비비율\(%\)"))
    method = (_value_after(ln, r"^\d+\.사채발행방법$", numeric=False) or "").replace(" ", "")
    return Parsed(kind=kind, amount=amount, shares=shares, ratio=ratio, price=price, price2=floor,
                  method="사모" if "사모" in method else ("공모" if "공모" in method else method[:20]))


#: 증자방식 → 짧은 표지. 순서가 규칙이다('주주배정후실권주일반공모' 는 주주배정).
_RIGHTS_METHODS = (("제3자", "third"), ("주주배정", "holders"), ("일반공모", "public"))


def parse_rights(text: str) -> Parsed:
    """유상증자결정."""
    ln = _lines(text)
    new = number(_value_after(ln, r"^1\.신주의종류와수$", sub=r"^보통주식"))
    pre = number(_value_after(ln, r"^3\.증자전발행주식총수", sub=r"^보통주식"))
    raw = _key(_value_after(ln, r"^5\.증자방식$", numeric=False) or "")
    method = next((tag for key, tag in _RIGHTS_METHODS if key in raw), raw[:20])
    price = number(_value_after(ln, r"^6\.신주발행가액", sub=r"^보통주식"))
    disc = number(_value_after(ln, r"기준주가에대한할인율또는할증율"))
    ratio = (new / pre * 100.0) if (new and pre) else None
    return Parsed(kind="rights", shares=new, base_shares=pre, ratio=ratio, price=price, ratio2=disc, method=method)


def parse_refix(text: str, kind: str) -> Parsed:
    """전환가액·행사가액 조정 — 회차가 여럿일 수 있다. 가능 주식수 증가분을 회차 합으로."""
    ln = _lines(text)
    p = _numbers_between(ln, r"^조정후(전환|행사)가액\(원\)", r"^2\.")
    before_price = after_price = None
    if len(p) >= 3:
        # [회차, 조정전, 조정후] 반복 — 첫 회차의 가액만(가격 비율은 회차마다 거의 같다).
        before_price, after_price = p[1], p[2]
    q = _numbers_between(ln, r"^조정후(전환|행사)가능주식수", r"^3\.조정사유")
    added = None
    if len(q) >= 4 and len(q) % 4 == 0:
        added = sum(q[k + 3] - q[k + 2] for k in range(0, len(q), 4))
    reason = _key(_value_after(ln, r"^3\.조정사유$", numeric=False) or "")
    tag = "down" if "하락" in reason else ("up" if "상승" in reason else ("dilution" if ("증자" in reason or "발행" in reason) else reason[:20]))
    return Parsed(kind=kind, shares=added, price=after_price, price2=before_price, method=tag)


def parse_bb_decision(text: str) -> Parsed:
    ln = _lines(text)
    shares = number(_value_after(ln, r"^1\.취득예정주식\(주\)", sub=r"^보통주식"))
    amount = number(_value_after(ln, r"^2\.취득예정금액\(원\)", sub=r"^보통주식"))
    how = _key(_value_after(ln, r"^6\.취득방법$", numeric=False) or "")
    return Parsed(kind="bb_decision", shares=shares, amount=amount, method="trust" if "신탁" in how else "direct")


def parse_bb_trust(text: str) -> Parsed:
    ln = _lines(text)
    return Parsed(kind="bb_trust", amount=number(_value_after(ln, r"^1\.계약금액\(원\)")), method="trust")


def parse_bb_result(text: str) -> Parsed:
    """자기주식취득결과보고서 — '3. 취득내용' 의 '계' 행(주문·취득·평단·총액)과 '4.' 의 예정 대비."""
    ln = _lines(text)
    i3 = _find(ln, r"^3\.취득내용")
    i4 = _find(ln, r"^4\.취득예정내용과")
    acquired = amount = None
    if i3 is not None:
        stop = i4 if (i4 is not None and i4 > i3) else len(ln)
        tot = [k for k in range(i3, stop) if _key(ln[k]) in ("계", "합계")]
        if tot:
            nums = [number(x) for x in ln[tot[-1] + 1: tot[-1] + 8] if _NUM.match(x.replace(" ", "")) or x == "-"]
            nums = [x for x in nums if x is not None]
            # [주문수량, 취득수량, 평단, 총액] — 주문수량이 없는 양식이 있어 뒤에서 센다.
            if len(nums) >= 3:
                amount, acquired = nums[3] if len(nums) >= 4 else nums[2], nums[1] if len(nums) >= 4 else nums[0]
    planned = None
    if i4 is not None:
        nums = []
        for x in ln[i4 + 1: i4 + 20]:
            if x.startswith("주") or x.startswith("5."):
                break
            if _NUM.match(x.replace(" ", "")) or x == "-":
                nums.append(number(x))
        # [예정 보통, 예정 기타, 실제 보통, 실제 기타, ...]
        if len(nums) >= 3:
            planned = nums[0]
            if acquired is None:
                acquired = nums[2]
    rate = (acquired / planned * 100.0) if (acquired is not None and planned) else None
    if rate is not None and rate < 1.0:
        # '4.' 표 머리가 '취득예정금액(원)' 인데 값은 주식수인 양식과 섞여 단위가 어긋난다(예정 금액 ÷ 실제 주식수) — 못 읽음.
        rate = None
    return Parsed(kind="bb_result", shares=acquired, amount=amount, ratio=rate, extra={"planned": planned} if planned else {})


def parse_bb_trust_end(text: str) -> Parsed:
    """신탁계약해지결과보고서 — 해지금액(=계약금액)과 월별 취득 금액 합(실제 매입)."""
    ln = _lines(text)
    contract = number(_value_after(ln, r"^계약해지금액$", span=4))
    i4 = _find(ln, r"^4\.신탁계약을통한")
    bought = shares = 0.0
    seen = False
    if i4 is not None:
        k = i4
        while k < len(ln):
            if re.match(r"^\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일$", ln[k]) or re.match(r"^\d{4}[.-]\d{2}[.-]\d{2}$", ln[k]):
                row = ln[k + 1: k + 9]
                if row and row[0].replace(" ", "").startswith("보통"):
                    q, a = number(row[1]), number(row[2])
                    if q is not None:
                        shares += q
                        seen = True
                    if a is not None:
                        bought += a
            if _key(ln[k]).startswith("5."):
                break
            k += 1
    rate = (bought / contract * 100.0) if (seen and contract) else None
    return Parsed(kind="bb_trust_end", amount=bought if seen else None, shares=shares if seen else None, ratio=rate,
                  extra={"contract": contract} if contract else {})


def parse_pledge(text: str, kind: str) -> Parsed:
    """최대주주변경을 수반하는 주식담보제공 계약 체결 · 해제."""
    ln = _lines(text)
    if kind == "pledge":
        pledged = number(_value_after(ln, r"^누적담보제공주식총수\(주\)"))
        own = number(_value_after(ln, r"^-공시일현재$", sub=r"^소유주식수", span=3))
        pct = number(_value_after(ln, r"^-공시일현재$", sub=r"^지분율", span=5))
        pct_exec = number(_value_after(ln, r"^-담보권전부실행시$", sub=r"^지분율", span=5))
        debt = number(_value_after(ln, r"^2\.채무\(차입\)금액총액"))
        exec_own = _value_after(ln, r"^-담보권전부실행시$", sub=r"^소유주식수", span=3)
        if pledged is None and own and exec_own is not None and exec_own.strip() == "-":
            # 누적 칸이 비고 '전부 실행 시 소유 주식 수' 가 '-' 면 전량 담보다(2025-10-21 디와이디 실측).
            pledged, pct_exec = own, 0.0
    else:
        i = _find(ln, r"^담보제공주식총수\(주\)$")
        pledged = None
        if i is not None:
            vals = [number(x) for x in ln[i + 1:i + 3]]
            pledged = vals[1] if len(vals) > 1 and vals[1] is not None else (0.0 if len(vals) > 1 and ln[i + 2] == "-" else None)
        own = number(_value_after(ln, r"^-소유주식수\(주\)"))
        pct = number(_value_after(ln, r"^-지분율\(%\)"))
        pct_exec, debt = None, None
    total = (own / pct * 100.0) if (own and pct) else None
    ratio = (pledged / total * 100.0) if (pledged is not None and total) else None
    return Parsed(kind=kind, shares=pledged, base_shares=total, ratio=ratio, ratio2=pct_exec, amount=debt,
                  extra={k: v for k, v in (("holder_pct", pct), ("holder_shares", own)) if v is not None})


def parse_holder_change(text: str) -> Parsed:
    """최대주주등소유주식변동신고서 — '증감' 합계(주·%p)와 발행주식총수."""
    ln = _lines(text)
    total = None
    i = _find(ln, r"^발행주식총수\(1\+2\)$")
    if i is not None:
        nums = [number(x) for x in ln[i + 1:i + 4] if _NUM.match(x.replace(" ", ""))]
        if nums:
            total = nums[-1]
    j = _find(ln, r"^증감$")
    shares = pct = None
    if j is not None:
        k = _find(ln, r"^합계$", last=False, start=j + 1)
        if k is not None and k - j < 15:
            shares, pct = number(ln[k + 1]) if k + 1 < len(ln) else None, number(ln[k + 2]) if k + 2 < len(ln) else None
    after = None
    a = _find(ln, r"^이번보고서제출일$")
    if a is not None:
        k = _find(ln, r"^합계$", last=False, start=a + 1)
        if k is not None and k + 2 < len(ln):
            after = number(ln[k + 2])
    return Parsed(kind="holder_change", shares=shares, base_shares=total, ratio=pct, ratio2=after)


def parse_control_change(text: str) -> Parsed:
    ln = _lines(text)
    before = number(_value_after(ln, r"^변경전$", sub=r"^소유비율", span=6, last=False))
    after = number(_value_after(ln, r"^변경후$", sub=r"^소유비율", span=6, last=False))
    reason = _key(_value_after(ln, r"^2\.변경사유$", numeric=False) or "")[:30]
    return Parsed(kind="control_change", ratio=before, ratio2=after, method=reason)


def parse(text: str, kind: str) -> Parsed:
    """종류 → 파서."""
    if kind in ("cb", "bw", "eb"):
        return parse_bond(text, kind)
    if kind == "rights":
        return parse_rights(text)
    if kind in ("refix", "bwrefix"):
        return parse_refix(text, kind)
    if kind == "bb_decision":
        return parse_bb_decision(text)
    if kind == "bb_trust":
        return parse_bb_trust(text)
    if kind == "bb_result":
        return parse_bb_result(text)
    if kind == "bb_trust_end":
        return parse_bb_trust_end(text)
    if kind in ("pledge", "pledge_off"):
        return parse_pledge(text, kind)
    if kind == "holder_change":
        return parse_holder_change(text)
    if kind == "control_change":
        return parse_control_change(text)
    raise ValueError(f"모르는 종류 {kind}")
