"""SEC EDGAR 공시 — 미장 `event`·`news` Analyst 의 입력 (태스크 #38).

## 뉴스가 아니라 공시가 답이었다

`news` Analyst 가 차단하기로 한 목록을 다시 보면 여섯 개가 전부 **공시
사건**이다: 회계부정·감사의견거절·상폐심사·유상증자·최대주주매도·실적쇼크.
국장은 DART 로 그걸 받으면서 미장만 뉴스 API 를 뒤지고 있었다 — 그 비대칭이
"미장 뉴스 소스가 없다"(#38)의 정체였다. newsapi 305건이 전부 국장이었던 것도
소스가 없어서가 아니라 **엉뚱한 곳을 보고 있었기** 때문이다.

## 백필이 되므로 제약 하나가 풀린다

"과거 뉴스 데이터가 없어서 뉴스는 필터로만 쓴다" 는 결정이 있었다. EDGAR 는
2001년 이후 전문검색이 되므로 5년 백필이 된다 — 미장 `news` 를 필터가 아니라
**IC 측정 대상**으로 올릴 수 있다.

## 왜 전문검색 API 인가

일별 인덱스(`daily-index/.../form.YYYYMMDD.idx`)는 폼 종류만 주고 **8-K 항목
코드를 안 준다.** 8-K 는 항목이 곧 사건의 정체라(4.02 = 재무제표 신뢰불가,
2.02 = 실적발표) 항목 없이는 분류가 불가능하다.

`efts.sec.gov/LATEST/search-index` 는 `items` 를 그대로 준다 (2026-08-19 실측:
`items: ['2.02', '7.01', '9.01']`). 인증도 API 키도 필요 없고, User-Agent 에
이름과 이메일만 넣으면 된다.

## 분류는 국장과 **같은 어휘**로 접는다

`dart_filings.CATEGORIES` 가 이미 earnings·dividend·dilution·distress·
ownership 을 쓴다. 미장용 어휘를 새로 만들면 Analyst 가 시장마다 다른 문자열을
뒤지게 되고, 그 순간 규칙이 두 곳으로 흩어진다.

## 사용자가 준 매핑에서 넷을 고쳤다

    8-K 2.02  실적발표  → **차단이 아니라 earnings**
        사건이지 악재가 아니다. 이걸로 차단하면 어닝시즌마다 유니버스가
        통째로 비운다. 방향은 실제 수치가 말한다.

    S-3       선반등록  → **dilution 이 아니다**
        등록만 하고 몇 년 안 찍는 회사가 많다. 실제 발행인 424B 만 희석이다.

    Form 4    내부자거래 → **매도(코드 S)만**, 10b5-1 사전계획은 제외
        매수도 같은 폼으로 들어오고, 계획매도는 정보가 없다.
        **항목코드가 없는 폼이라 여기서는 ownership 으로만 접는다** — 거래
        코드는 문서 본문에 있어서 목록 조회로는 안 온다. 방향 판별은
        Analyst 가 아니라 별도 수집이 필요하다(아래 TODO).

    NT 10-K/Q 보고지연 → 그대로 distress. 이건 강한 신호가 맞다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 8-K 항목코드 → `dart_filings` 와 같은 doc_type.
#:
#: **여기 없는 항목은 버리지 않는다.** 8-K 가 났다는 것 자체가 사건이고,
#: 분류를 못 했다고 사실이 사라지지 않는다 (`dart_filings.OTHER` 와 같은 규약).
EIGHT_K_ITEMS: dict[str, str] = {
    # 위험 — 매수를 막는 쪽
    "1.03": "distress",  # 파산·법정관리
    "2.06": "distress",  # 자산 손상
    "3.01": "distress",  # 상장규정 위반·상폐 통보
    "4.02": "distress",  # 기존 재무제표를 믿을 수 없다
    # 사건이되 방향은 수치가 말한다
    "2.02": "earnings",  # 실적 발표
    # 주주환원·구조
    "1.01": "contract",  # 중요 계약 체결
    "5.02": "ownership",  # 임원 선임·사임
}

#: 폼 종류 → doc_type. 8-K 는 항목으로 다시 나뉘므로 여기 없다.
FORM_TYPES: dict[str, str] = {
    "10-K": "earnings",
    "10-Q": "earnings",
    # **보고 지연은 강한 위험 신호다.** 제때 못 내는 회사는 대개 이유가 있다.
    "NT 10-K": "distress",
    "NT 10-Q": "distress",
    # 실제 발행. S-3(선반등록)는 여기 없다 — 등록과 발행은 다른 사건이다.
    "424B1": "dilution",
    "424B2": "dilution",
    "424B3": "dilution",
    "424B4": "dilution",
    "424B5": "dilution",
    # 내부자·대주주. 방향(매수/매도)은 본문에 있어서 목록으로는 모른다.
    "4": "ownership",
    "SC 13D": "ownership",
    "SC 13G": "ownership",
}

OTHER = "other"
SOURCE = "edgar"

#: SEC 가 요구하는 것은 **연락 가능한 신원**뿐이다. 키도 등록도 없다.
#: 없이 부르면 403 이 온다 — 인증 실패가 아니라 예의 문제다.
USER_AGENT_ENV = "SEC_USER_AGENT"

#: 초당 10건까지 무료다. 여유를 두고 8건으로 잡는다 — 한도를 정확히 맞추면
#: 네트워크 지터에 걸려 429 를 받는다.
MAX_REQUESTS_PER_SECOND = 8.0

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data"


@dataclass(frozen=True)
class Filing:
    """공시 한 건. `documents` 표의 한 행이 된다."""

    entity_id: str
    doc_id: str
    doc_type: str
    title: str
    filer: str
    url: str
    filed_on: str  # YYYY-MM-DD


def classify(form: str, items: tuple[str, ...]) -> str:
    """폼·항목 → doc_type. **항목이 폼을 이긴다.**

    8-K 는 폼만으로는 아무 뜻이 없다 — 4.02(재무제표 신뢰불가)와 2.02(실적
    발표)가 같은 폼이다. 항목 여러 개가 붙은 공시는 **가장 나쁜 것**을 따른다:
    실적발표와 상폐통보가 같이 왔으면 그날 사건은 상폐통보다.
    """
    ranked = ("distress", "dilution", "ownership", "earnings", "contract")
    found = {EIGHT_K_ITEMS[item] for item in items if item in EIGHT_K_ITEMS}
    for kind in ranked:
        if kind in found:
            return kind
    normalized = form.strip().upper()
    if normalized in FORM_TYPES:
        return FORM_TYPES[normalized]
    return OTHER


def ticker_map(payload: dict[str, dict[str, object]]) -> dict[str, str]:
    """`company_tickers.json` → {CIK(10자리 0채움): 티커}.

    EDGAR 는 CIK 로 말하고 우리 창고는 티커로 말한다. 매핑에 없는 CIK 는
    **버린다** — 상장주식이 아니거나(펀드·SPV) 우리가 거래하지 않는 것이다.
    조용히 버리지 않고 몇 건인지 세어 보고한다(`FilingBatch.unmapped`).
    """
    out: dict[str, str] = {}
    for row in payload.values():
        cik = str(row.get("cik_str") or "").strip()
        ticker = str(row.get("ticker") or "").strip().upper()
        if cik and ticker:
            out[cik.zfill(10)] = ticker
    return out


@dataclass(frozen=True)
class FilingBatch:
    filings: tuple[Filing, ...]
    total: int
    unmapped: int


def normalize(
    hits: list[dict[str, object]], *, tickers: dict[str, str]
) -> FilingBatch:
    """전문검색 응답 → `Filing` 들.

    한 공시에 CIK 가 여럿 붙는 경우가 있다(공동 제출). **매핑되는 것 전부에
    행을 만든다** — 인수합병 공시가 양쪽 종목의 사건인 것은 맞기 때문이다.
    """
    out: list[Filing] = []
    unmapped = 0
    for hit in hits:
        source = hit.get("_source") or {}
        if not isinstance(source, dict):
            continue
        form = str(source.get("root_form") or source.get("form") or "")
        items = tuple(str(i) for i in (source.get("items") or []))
        doc_type = classify(form, items)
        accession = str(source.get("adsh") or "")
        filed = str(source.get("file_date") or "")
        names = [str(n) for n in (source.get("display_names") or [])]
        ciks = [str(c).zfill(10) for c in (source.get("ciks") or [])]

        matched = False
        for index, cik in enumerate(ciks):
            ticker = tickers.get(cik)
            if not ticker:
                continue
            matched = True
            out.append(
                Filing(
                    entity_id=f"US:{ticker}",
                    doc_id=accession,
                    doc_type=doc_type,
                    # 항목코드를 제목에 남긴다. 나중에 분류를 바꿀 때 원본을
                    # 다시 긁지 않아도 되고, 사람이 화면에서 왜 그렇게
                    # 분류됐는지 볼 수 있다.
                    title=f"{form} {' '.join(items)}".strip(),
                    filer=names[index] if index < len(names) else "",
                    url=f"{FILING_URL}/{cik.lstrip('0')}/{accession.replace('-', '')}",
                    filed_on=filed,
                )
            )
        if not matched:
            unmapped += 1
    return FilingBatch(filings=tuple(out), total=len(hits), unmapped=unmapped)


# -- 조회 -----------------------------------------------------------------------


PAGE_SIZE = 100
#: 전문검색이 한 질의로 돌려주는 상한. 넘으면 폼을 나눠 물어야 한다.
MAX_HITS = 10_000


class EdgarSource:
    """전문검색 조회. **키가 없다 — User-Agent 가 신원이다.**

    SEC 는 초당 10건까지 허용한다. 여기서는 8건으로 잡는다 — 한도를 정확히
    맞추면 네트워크 지터에 걸려 429 를 받고, 429 는 재시도 폭풍이 된다.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        client: object | None = None,
        timeout: float = 30.0,
        sleep: object | None = None,
    ) -> None:
        import time as _time

        if not user_agent or "@" not in user_agent:
            # 이메일 없는 UA 로 부르면 403 이다. 그 403 을 "차단당했다" 로
            # 읽으면 원인을 엉뚱한 데서 찾는다 — 여기서 먼저 막는다.
            raise ValueError(
                f"{USER_AGENT_ENV} 에 연락 가능한 이메일이 있어야 한다: {user_agent!r}"
            )
        self.user_agent = user_agent
        self.client = client
        self.timeout = timeout
        self._sleep = sleep or _time.sleep
        self._last = 0.0

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}

    def _throttle(self) -> None:
        import time as _time

        gap = 1.0 / MAX_REQUESTS_PER_SECOND
        wait = gap - (_time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = _time.monotonic()

    def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, object]:
        import httpx

        self._throttle()
        owned = self.client is None
        http = self.client or httpx.Client(
            timeout=self.timeout, headers=self._headers(), follow_redirects=True
        )
        try:
            response = http.get(url, params=params)  # type: ignore[union-attr]
            response.raise_for_status()
            return dict(response.json())
        finally:
            if owned:
                http.close()  # type: ignore[union-attr]

    def tickers(self) -> dict[str, str]:
        return ticker_map(self._get(TICKERS_URL))  # type: ignore[arg-type]

    def search_day(self, day: str, *, forms: str) -> list[dict[str, object]]:
        """하루치 공시 전부. **페이지를 끝까지 넘긴다.**

        한 페이지가 100건이라 첫 장만 받고 끝내면 그날 공시의 3분의 1만
        들어온다 — 실측 2026-08-14 의 8-K 는 287건이었다. 조용히 잘린 수집은
        "그날 사건이 적었다" 로 읽혀서 나중에 못 알아본다.
        """
        hits: list[dict[str, object]] = []
        start = 0
        while True:
            payload = self._get(
                SEARCH_URL,
                params={
                    "q": "",
                    "forms": forms,
                    "startdt": day,
                    "enddt": day,
                    "from": str(start),
                },
            )
            block = payload.get("hits") or {}
            page = list(block.get("hits") or []) if isinstance(block, dict) else []
            hits.extend(page)
            total = 0
            if isinstance(block, dict):
                meta = block.get("total") or {}
                total = int(meta.get("value", 0)) if isinstance(meta, dict) else 0
            start += PAGE_SIZE
            if not page or start >= min(total, MAX_HITS):
                if total > MAX_HITS:
                    # 상한에 닿으면 조용히 자르지 않고 알린다. 폼을 쪼개
                    # 다시 물어야 한다는 뜻이다.
                    raise CollectorLimit(
                        f"{day} {forms} 이 {total}건 — 전문검색 상한 {MAX_HITS} 초과"
                    )
                return hits


class CollectorLimit(RuntimeError):
    """조회 상한에 닿았다. 조용히 자르는 것보다 멈추는 편이 낫다."""
