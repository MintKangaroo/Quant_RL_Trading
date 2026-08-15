"""미장 상장주식수 — SEC EDGAR companyfacts.

## 왜 이게 필요했나

``market_stats`` 는 KR 675만 행 · **US 0행**이었다. 조인 버그가 아니라 수집
공백이다 — ``market_cap = 주가 × 상장주식수`` 인데 상장주식수를 받는 곳이
``krx_openapi`` (KRX Open API, 국장 전용) 하나뿐이었다. 미장은 시세와 명단이
다 들어와 있어도 시가총액이 만들어질 수 없었고, 그래서 마켓 탭의 미장
리더·트리맵이 항상 빈 목록이었고 ``fundamental`` 의 밸류 팩터가 미장에서
통째로 꺼져 있었다.

## 소스 선정 — 전부 실제로 호출해 보고 골랐다 (2026-08-15)

**LS 해외주식 g3101 (종목마스터) — ❌**
상장주식수·시가총액 **필드 자체가 없다.** 주는 것은 현재가·PER(``perv``)·
EPS(``epsv``)뿐이고, 그마저 **오늘 값 한 점**이라 과거가 없다.

**SEC ``companyconcept`` API — ❌ 믿을 수 없다.**
사실이 있는 CIK 에도 200 + 빈 ``units`` 를 돌려준다. HCA(860730)·POOL(945841)·
VFC(103379) 는 ``companyconcept`` 이 0행인데 ``companyfacts`` 에는 각각 61·64·
다수 행이 있다. 표본 120종목에서 이것 때문에 커버리지가 77% 로 **보였다** —
문서만 보고 골랐으면 그 수치를 사실로 적었을 것이다.

**SEC ``frames`` API — ❌**
분기당 한 콜로 4,600종목을 주지만 **``filed``(공시일)를 주지 않는다.**
관측시각을 지어내야 하므로 쓸 수 없다. 다중클래스 기업(GOOGL)도 프레임에서
빠진다.

**SEC ``companyfacts.zip`` (벌크) — ✅ 채택.**
1.4GB, 실측 2.6분(8.8MB/s). CIK 하나가 파일 하나이고 사실마다 ``end``·``val``·
``filed`` 가 다 붙어 온다. 무료·키 불필요(User-Agent 만 필요).

즉 호출 한 번으로 전 종목·전 기간이 끝난다. 종목당 호출이 아니므로 레이트
리밋도, 6,648번의 재시도 관리도 없다.

## 두 시각을 어떻게 찍는가 — 여기가 이 파일의 핵심이다

상장주식수는 **분기 공시**다. 시세처럼 매일 바뀌지 않는다. 그래서 하루의
시가총액은 "그날 종가 × **그날까지 알려진 마지막** 상장주식수" 다.

| 필드 | 값 | 왜 |
|---|---|---|
| ``valid_from`` | 사실의 기준일 (``end``) | 표지에 적힌 "이 날짜 현재 발행주식수" |
| ``observed_at`` | 공시일 (``filed``) + EDGAR 접수 마감시각 | **우리가 알 수 있었던 시각** |

``end`` 와 ``filed`` 는 보통 30~90일 벌어진다 (AAPL: end=2026-07-17,
filed=2026-07-31). ``filed`` 를 무시하고 ``end`` 로 관측시각을 찍으면 **분기
말에 아직 공시되지 않은 주식수를 알고 있는 것**이 되어 미래를 본다. 반대로
전부 오늘로 찍으면 5년치가 통째로 과거 조회에서 사라진다 (``publication``
모듈 docstring 과 같은 함정).

시각은 EDGAR 접수 마감(17:30 ET) 이후로 잡는다. 그날 장중에 낸 공시를 그날
종가에 반영하지 않는 쪽이 안전하고, 미장 일봉의 관측시각이 16:20 ET 이므로
**같은 날 공시는 다음 세션부터** 시가총액에 들어간다. DART 공시를 18시(KST)로
잡은 것과 같은 규칙이다 (``backfill.dart_publication_hour_kst``).

## 태그 사슬 — 하나로는 안 된다

``dei:EntityCommonStockSharesOutstanding`` 이 표준이지만 **다중 클래스 기업은
이 태그를 아예 내지 않는다.** GOOGL(CIK 1652044)이 그렇다 — 실측에서
``dei`` 는 없고 ``us-gaap:CommonStockSharesOutstanding`` 이 12.23B(A+B+C 합계)를
준다. 그래서 사슬로 찾는다.

**사슬을 섞지 않는다.** 종목 하나는 처음으로 값이 나온 태그 **하나만** 쓴다.
분기마다 태그를 갈아타면 정의가 다른 값들이 한 시계열에 섞여, 실제로는 없던
발행주식수 급변이 만들어진다.

## 못 하는 것 — 숨기지 않는다

- **클래스별 주식수는 못 준다.** ``companyfacts`` 는 세그먼트(클래스) 축을
  접어서 회사 합계만 준다. GOOGL·GOOG 는 같은 CIK 라 같은 값을 받는다.
  밸류 팩터가 쓰는 "회사 전체 시가총액" 에는 이쪽이 맞지만, 클래스 하나의
  유통 주식수는 아니다.
- **티커가 CIK 를 갈아타면 과거가 끊긴다.** 실측: XOM 이 CIK 2115436 으로
  잡히고 그 CIK 에는 2026-06-30 한 행뿐이다. 옛 CIK 의 이력은 오늘자
  티커→CIK 표(``us_universe``)로는 닿지 않는다.
- ETF·폐쇄형 펀드(DIA·FRA·CLM…)와 워런트는 발행주식수 태그가 없다. 행을
  만들지 않는다 — **모르는 것을 채우지 않는다** (``sectors`` 와 같은 원칙).
- 상폐 종목은 애초에 시세가 없다 (``ls_us_source`` 참조). 여기서도 안 는다.
- **ADR(20-F·6-K)은 통째로 뺀다** — ``FOREIGN_ISSUER_FORMS`` 주석 참조.

## 커버리지 (2026-08-15 실측)

**6,648종목 중 4,502개(68%).** 태그별로는 ``dei`` 4,307 · ``us-gaap`` 195 —
사슬의 두 번째 칸이 195종목(GOOGL 포함)을 건졌다. 못 채우는 쪽은 ADR 907개,
CIK 자체가 없는 184개, 발행주식수 태그가 없는 1,962개(ETF·폐쇄형 펀드·워런트)다.

68% 를 100% 로 만드는 방법은 지금 없다. **빈 자리를 채우지 않는 것이 답이다** —
없는 시가총액은 그 종목이 리더·트리맵·밸류 팩터에서 빠지는 것으로 끝나지만,
틀린 시가총액은 정렬 맨 위에 올라와 다른 종목을 밀어낸다.
"""

from __future__ import annotations

import json
import zipfile
from bisect import bisect_right
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.market_hours import Market

MARKET_STATS = "market_stats"
SOURCE = "sec_edgar"

#: 벌크 파일. 매일 새로 만들어진다 (실측 last-modified 가 전날).
BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

#: SEC 는 User-Agent 로 신원을 요구한다. 없으면 403 이다 (``us_universe`` 와 같다).
UA_ENV = "SEC_EDGAR_USER_AGENT"

#: 발행주식수를 찾는 순서. **종목마다 하나만 골라 끝까지 쓴다** — 모듈
#: docstring "태그 사슬" 참조.
TAG_CHAIN: tuple[tuple[str, str], ...] = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesIssued"),
)

#: companyfacts 가 발행주식수에 쓰는 단위. 다른 단위가 오면 그 사실은 버린다.
SHARES_UNIT = "shares"

#: **외국 발행인(ADR) 서식.** 이 서식으로 온 주식수는 쓰지 않는다.
#:
#: 20-F 를 내는 회사는 SEC 에 **원주(ordinary share)** 수를 신고하는데, 미국에
#: 붙은 시세는 **ADR 한 장** 값이다. 1 ADR = N 원주라서 둘을 그냥 곱하면
#: ADR 비율만큼 틀린다 — 실측(2026-08-15, 이 필터를 넣기 전 리더 상위):
#:
#:     LTM   5,742억 원주 × ADR 가격 → 30,112 조원… 실제는 100억 달러대
#:     TSM   259억 원주 (1 ADR = 5 원주) → 11,129 B$, 실제의 약 5배
#:     BSAC · BCH  같은 이유로 각각 6,409 B$ · 4,031 B$
#:
#: ADR 비율은 무료로 신뢰성 있게 얻을 수 없다. **그래서 채우지 않고 버린다** —
#: 틀린 시가총액은 없는 것보다 나쁘다. 트리맵·리더 정렬이 그 거짓을 맨 위에
#: 올리고, 밸류 팩터는 그 종목을 영원히 초저평가로 읽는다.
#:
#: 40-F(캐나다 MJDS)는 뺀다. 대개 ADR 이 아니라 보통주 직상장이라 단위가 같다.
FOREIGN_ISSUER_FORMS = ("20-F", "6-K")

#: EDGAR 접수 마감. 이 시각 이후 접수분은 다음 영업일자로 찍히므로, 공시일을
#: 이 시각으로 잡으면 **실제보다 이르게 아는 일이 없다**.
EDGAR_TIMEZONE = "America/New_York"
EDGAR_CUTOFF_HOUR = 18

#: 이 설정 키가 있으면 마감시각을 그쪽에서 읽는다 (불변식 10).
CONFIG_CUTOFF_KEY = "backfill.sec_filing_hour_et"

SHARES = "shares"
MARKET_CAP = "market_cap"


class SecBulkUnavailable(CollectorError):
    """SEC 벌크 파일을 못 받았거나 열 수 없다."""


# -----------------------------------------------------------------------------
# 소스
# -----------------------------------------------------------------------------


def filing_moment(filed: date, *, hour: int = EDGAR_CUTOFF_HOUR) -> datetime:
    """공시일 → 관측시각(UTC). 서머타임은 tz 데이터가 처리한다."""
    local = datetime(filed.year, filed.month, filed.day, hour, tzinfo=ZoneInfo(EDGAR_TIMEZONE))
    return local.astimezone(UTC)


@dataclass
class SecBulkFacts:
    """``companyfacts.zip`` 한 벌. 레포에서 이 파일을 여는 유일한 곳.

    **압축을 풀지 않는다.** 안에 CIK 하나당 파일 하나로 100만 개가 들어 있어
    다 풀면 창고가 아니라 파일시스템이 먼저 죽는다 (``us_backfill`` 의 파티션
    폭발과 같은 실패 모양). 필요한 6,648개만 zip 에서 직접 읽는다.
    """

    path: Path
    user_agent: str = ""
    timeout: float = 300.0
    #: 받아 둔 벌크 파일을 이만큼까지만 재사용한다. 주 단위 갱신에 맞춘다.
    max_age_days: int = 7
    _zip: zipfile.ZipFile | None = field(default=None, repr=False)

    def age(self, *, now: datetime) -> timedelta:
        """받아 둔 파일이 얼마나 묵었나. 없으면 무한대로 본다."""
        if not self.path.is_file():
            return timedelta.max
        modified = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
        return now - modified

    def download(self, *, now: datetime, client: httpx.Client | None = None) -> Path:
        """벌크 파일을 받는다. 받아 둔 것이 ``max_age_days`` 안이면 받지 않는다.

        **"있으면 안 받는다" 로 두면 안 된다.** 한 번 받아 둔 zip 이 영원히
        재사용되면서 창고의 상장주식수가 그 날짜에 얼어붙는다 — 화면은 멀쩡해
        보이고 낡은 것은 입력뿐인, 이 저장소에서 이미 한 번 겪은 실패 모양이다.

        ``.part`` 로 받아 다 받은 뒤에 이름을 바꾼다. 중간에 끊긴 파일이 정상
        파일 이름으로 남으면 다음 실행이 그 깨진 zip 을 열려 한다.

        **이 파일만은 덮어쓴다** — ``data/raw`` 는 원칙적으로 삭제 금지지만
        (data-contract §2), companyfacts 는 **누적본**이라 새 zip 이 옛 zip 의
        내용을 전부 포함한다. 옛 스냅샷에만 있는 사실이 없으므로 "원본에서 다시
        만들 수 있다" 는 성질이 깨지지 않는다. 주마다 1.4GB 를 쌓으면 1년에
        73GB 이고, 그걸로 되사는 것이 없다.
        """
        if self.path.is_file() and self.age(now=now) <= timedelta(days=self.max_age_days):
            return self.path
        if not self.user_agent.strip():
            raise SecBulkUnavailable(f"{UA_ENV} 가 없다. SEC 는 신원 없는 요청을 막는다.")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(self.path.suffix + ".part")
        owned = client is None
        http = client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        try:
            with http.stream("GET", BULK_URL, headers={"User-Agent": self.user_agent}) as response:
                if response.status_code != 200:
                    raise SecBulkUnavailable(f"SEC {response.status_code}")
                with staging.open("wb") as handle:
                    for chunk in response.iter_bytes(1 << 22):
                        handle.write(chunk)
        finally:
            if owned:
                http.close()
        staging.rename(self.path)
        return self.path

    def _archive(self) -> zipfile.ZipFile:
        if self._zip is None:
            if not self.path.is_file():
                raise SecBulkUnavailable(f"{self.path} 가 없다. 먼저 download() 를 부른다.")
            try:
                self._zip = zipfile.ZipFile(self.path)
            except zipfile.BadZipFile as error:
                raise SecBulkUnavailable(f"{self.path} 를 열 수 없다: {error}") from error
        return self._zip

    def facts_for(self, cik: int) -> dict[str, Any] | None:
        """CIK 하나의 companyfacts — **필요한 태그만** 파싱한 것.

        문서를 통째로 ``json.loads`` 하면 안 쓰는 us-gaap 태그 수백 개까지
        객체로 만든다. 실측: 압축 해제는 300종목에 1.6초인데 전체 파싱은
        10.7초 — 비용의 85%가 우리가 버릴 것을 만드는 데 든다. 그래서 태그
        사슬에 있는 셋만 잘라 읽는다.

        돌려주는 모양은 원본과 같다 (``{"facts": {taxonomy: {tag: ...}}}``) —
        ``share_facts`` 는 이 함수를 거쳤는지 아닌지 알 필요가 없고, 테스트는
        SEC 응답 원본을 그대로 넣을 수 있다.
        """
        try:
            blob = self._archive().read(f"CIK{cik:010d}.json")
        except KeyError:
            return None
        except (zipfile.BadZipFile, OSError):
            return None
        return _extract_tags(blob)

    def __contains__(self, cik: int) -> bool:
        try:
            self._archive().getinfo(f"CIK{cik:010d}.json")
        except KeyError:
            return False
        return True

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None


_DECODER = json.JSONDecoder()


def _extract_tags(blob: bytes) -> dict[str, Any] | None:
    """companyfacts 원문에서 ``TAG_CHAIN`` 태그의 값만 잘라낸다.

    태그 이름이 문서 안에서 유일하기 때문에 성립한다 — 키 문자열을 찾고 그
    뒤의 객체 하나만 ``raw_decode`` 한다. 못 찾으면 그 태그는 없는 것이다.
    잘못 잘릴 위험은 없다: 찾은 위치에서 여는 중괄호를 못 만나거나 디코딩이
    실패하면 그 태그를 버린다.
    """
    text = blob.decode("utf-8", errors="replace")
    facts: dict[str, dict[str, Any]] = {}
    for taxonomy, tag in TAG_CHAIN:
        # 앞뒤 따옴표와 콜론까지 붙여 찾는다. 따옴표가 없으면
        # ``CommonStockSharesOutstanding`` 이 ``EntityCommonStockShares...`` 에
        # 걸리고, 콜론이 없으면 설명문 안의 같은 낱말에 걸린다.
        key = f'"{tag}":'
        start = text.find(key)
        if start < 0:
            continue
        brace = start + len(key)
        if brace >= len(text) or text[brace] != "{":
            continue
        try:
            value, _ = _DECODER.raw_decode(text, brace)
        except ValueError:
            continue
        facts.setdefault(taxonomy, {})[tag] = value
    return {"facts": facts}


# -----------------------------------------------------------------------------
# 정규화
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ShareFact:
    """공시 하나에서 읽은 발행주식수 한 점."""

    end: date
    value: float
    filed: date
    tag: str
    #: 같은 ``end`` 에 대한 몇 번째 값인가. 0 = 원본, 1+ = 정정 (불변식 4).
    revision: int = 0


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _rows_for_tag(payload: Mapping[str, Any], taxonomy: str, tag: str) -> list[dict[str, Any]]:
    unit = (
        payload.get("facts", {})
        .get(taxonomy, {})
        .get(tag, {})
        .get("units", {})
        .get(SHARES_UNIT)
    )
    return list(unit or [])


def _is_foreign_issuer_form(form: Any) -> bool:
    """20-F·6-K 와 그 정정본(``20-F/A``)까지 잡는다."""
    head = str(form or "").split("/", 1)[0].strip().upper()
    return head in FOREIGN_ISSUER_FORMS


def share_facts(payload: Mapping[str, Any]) -> list[ShareFact]:
    """companyfacts → 발행주식수 시계열.

    세 가지를 여기서 끝낸다.

    1. **태그 사슬**: 값이 나오는 첫 태그 하나만 쓴다 (모듈 docstring).
    2. **ADR 배제**: 외국 발행인(20-F·6-K)의 주식수는 **원주 기준**이라 ADR
       가격과 단위가 다르다. 버린다 (``FOREIGN_ISSUER_FORMS`` 주석의 실측).
       서식을 걸러 태그가 통째로 비면 사슬의 다음 태그로 내려간다 — 같은
       회사가 국내 서식으로도 신고했다면 그쪽이 쓰인다.
    3. **재공시 정리**: 같은 ``end`` 가 여러 번 온다. 실측한 것은 두 종류다 —
       10-K 를 8-K·10-K/A 가 **같은 값으로** 다시 낸 경우(JPM 2011-01-31,
       AAPL 2009-10-16)와, 값이 실제로 바뀐 경우. 앞엣것은 새 사실이 아니므로
       **가장 먼저 알 수 있었던 공시 하나만** 남기고, 뒤엣것만 ``revision`` 을
       올린 새 행이 된다. 같은 값을 정정본으로 또 쌓으면 창고가 "정정이
       있었다" 는 거짓을 기록한다.
    """
    for taxonomy, tag in TAG_CHAIN:
        raw = _rows_for_tag(payload, taxonomy, tag)
        if not raw:
            continue

        by_end: dict[date, list[tuple[date, float]]] = {}
        for item in raw:
            if _is_foreign_issuer_form(item.get("form")):
                # 원주 기준이라 ADR 가격과 곱할 수 없다.
                continue
            end = _as_date(item.get("end"))
            filed = _as_date(item.get("filed"))
            value = item.get("val")
            if end is None or filed is None or not isinstance(value, (int, float)):
                continue
            if value <= 0:
                continue
            by_end.setdefault(end, []).append((filed, float(value)))

        facts: list[ShareFact] = []
        for end, observations in by_end.items():
            seen: dict[float, date] = {}
            # 공시일 순. 같은 값이 여러 번 오면 **가장 이른 공시**가 진실이다.
            for filed, value in sorted(observations):
                if value in seen:
                    continue
                seen[value] = filed
            for revision, (value, filed) in enumerate(
                sorted(seen.items(), key=lambda pair: (pair[1], pair[0]))
            ):
                facts.append(
                    ShareFact(
                        end=end, value=value, filed=filed,
                        tag=f"{taxonomy}:{tag}", revision=revision,
                    )
                )
        if facts:
            return sorted(facts, key=lambda fact: (fact.end, fact.revision))
    return []


def _midnight(day: date) -> datetime:
    """거래일/기준일 → ``valid_from``. UTC 자정 고정 — 국장 백필과 같은 규칙."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def shares_rows(
    facts: Sequence[ShareFact],
    *,
    ticker: str,
    market: Market = Market.US,
    cutoff_hour: int = EDGAR_CUTOFF_HOUR,
    since: date | None = None,
) -> list[dict[str, Any]]:
    """발행주식수 사실 → ``market_stats`` 행 (metric=``shares``).

    ``since`` 는 ``end`` 하한이다. 5년 창을 벗어난 옛 공시까지 넣으면 창고만
    커지고 아무도 읽지 않는다.
    """
    rows: list[dict[str, Any]] = []
    for fact in facts:
        if since is not None and fact.end < since:
            continue
        # **표지 날짜가 제출일보다 뒤면 버린다.** 제출자가 연도를 잘못 적는
        # 일이 실제로 있다 — 실측으로 6건 나왔고 최대 10년을 앞섰다
        # (US:ASLE end=2034-03-05, filed=2024-03-28). 값 자체는 진짜지만
        # 날짜가 미래라 **그 종목의 영원한 최신 행**이 되어, 뒤에 나온 정확한
        # 주식수를 앞으로도 계속 덮는다. 아무도 그 사실을 눈치채지 못한다.
        # 아직 오지 않은 날짜의 사실을 공시할 수는 없으므로 이건 오타가 확실하다.
        if fact.end > fact.filed:
            continue
        rows.append(
            {
                "entity_id": f"{market}:{ticker}",
                # 표지에 적힌 기준일. 그 날짜 현재의 발행주식수다.
                "valid_from": _midnight(fact.end),
                # 공시된 순간. 그 전에는 아무도 이 숫자를 몰랐다.
                "observed_at": filing_moment(fact.filed, hour=cutoff_hour),
                "source": SOURCE,
                "revision": fact.revision,
                "market": str(market),
                "metric": SHARES,
                "value": fact.value,
            }
        )
    return rows


# -----------------------------------------------------------------------------
# 시가총액 — 마지막으로 알려진 주식수 × 그날 종가
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SharesTimeline:
    """종목 하나의 (관측시각, 주식수). **관측시각 순으로 정렬돼 있다.**"""

    observed: tuple[datetime, ...]
    values: tuple[float, ...]

    def known_at(self, moment: datetime) -> float | None:
        """``moment`` 에 알 수 있었던 마지막 주식수. 없으면 None.

        여기가 미래 훔쳐보기가 들어오는 유일한 문이다. **``moment`` 는 그
        세션 종가의 관측시각**이고, 그보다 늦게 공시된 주식수는 그날 쓸 수
        없다. ``bisect_right`` 라 같은 순간의 공시는 포함된다.
        """
        index = bisect_right(self.observed, moment)
        if index == 0:
            return None
        return self.values[index - 1]


def _cover_date_after_filing(row: Mapping[str, Any]) -> bool:
    """표지 기준일이 제출일보다 뒤인가 — 있을 수 없는 일이므로 오타다.

    ``valid_from`` 은 ``end`` 의 UTC 자정(``_midnight``)이고 ``observed_at`` 은
    ``filed`` 의 EDGAR 마감시각(ET, ``filing_moment``)이다. **시각끼리 비교하면
    안 된다** — 마감시각이 UTC 로는 다음 날이라, 같은 날 제출된 정상 행이 몇
    시간 차이로 걸린다. 각자 자기 시간대의 달력 날짜로 되돌려 비교한다.
    """
    valid_from, observed_at = row.get("valid_from"), row.get("observed_at")
    if valid_from is None or observed_at is None:
        return False
    end = valid_from.astimezone(UTC).date()
    filed = observed_at.astimezone(ZoneInfo(EDGAR_TIMEZONE)).date()
    return end > filed


def build_timelines(rows: Iterable[Mapping[str, Any]]) -> dict[str, SharesTimeline]:
    """``market_stats``(metric=shares) 행 → 종목별 시계열.

    같은 관측시각에 값이 둘이면(정정본) ``revision`` 이 큰 쪽이 이긴다 —
    게이트의 정정본 선택과 같은 규칙이다.

    ## 표지 날짜가 제출일보다 뒤인 행은 버린다 — 쓰기 규칙과 대칭이다

    ``shares_rows`` 가 ``fact.end > fact.filed`` 를 이미 막지만(위 참조),
    **그 방어가 생기기 전에 들어온 행이 창고에 99행 남아 있다**(82종목,
    실측 2026-08-15). 창고는 append-only 라 지울 수 없고, 자연키가
    ``(entity_id, valid_from, metric)`` 이라 **정정본으로 덮이지도 않는다** —
    날짜가 다르면 다른 행이다. 게다가 고쳐진 수집기는 그 사실을 정정하는 게
    아니라 버리므로, **재수집해도 정정본 자체가 만들어지지 않는다.**

    그래서 읽는 쪽에서 막는다. 창고의 과거는 못 고치니 읽는 쪽이 견딘다 —
    ``store/prices.py`` 의 종가 0 세션과 같은 처지다.

    영향은 시계열의 값이지 날짜가 아니다. 시가총액은 ``valid_from`` 을 보지
    않고 ``observed_at`` 축으로만 고르므로(``known_at``), 미래 날짜가 "영원한
    최신 행" 이 되는 일은 이 경로에서는 일어나지 않는다. 실제로 틀어지는
    것은 그 행이 timeline 에 얹은 **값**이다 — 실측으로 최근 400세션에서
    21종목 1,293행의 시가총액이 달라졌고, 최대 편차는 -92%/+102% 였다.
    """
    staged: dict[str, dict[datetime, tuple[int, float]]] = {}
    for row in rows:
        entity = str(row["entity_id"])
        moment = row["observed_at"]
        if _cover_date_after_filing(row):
            continue
        revision = int(row.get("revision") or 0)
        current = staged.setdefault(entity, {}).get(moment)
        if current is None or revision >= current[0]:
            staged[entity][moment] = (revision, float(row["value"]))

    timelines: dict[str, SharesTimeline] = {}
    for entity, points in staged.items():
        ordered = sorted(points.items())
        timelines[entity] = SharesTimeline(
            observed=tuple(moment for moment, _ in ordered),
            values=tuple(value for _, (_, value) in ordered),
        )
    return timelines


def market_cap_rows(
    bars: Iterable[Mapping[str, Any]],
    timelines: Mapping[str, SharesTimeline],
    *,
    market: Market = Market.US,
) -> list[dict[str, Any]]:
    """세션 하나의 시세 행 → ``market_stats`` 행 (metric=``market_cap``).

    ``observed_at`` 은 **그 봉의 관측시각을 그대로** 쓴다. 주식수는 그 시각
    이전에 이미 공시돼 있던 값이므로, 둘 중 늦은 쪽이 봉이다. 지어내지 않는다
    (``us_universe_panel`` 이 명단 관측시각을 봉에서 가져오는 것과 같다).

    주식수를 모르는 종목은 **행을 만들지 않는다.** 0 을 넣으면 "시가총액이 0"
    이라는 다른 사실이 되고, 트리맵·리더 정렬이 그 거짓을 읽는다.
    """
    rows: list[dict[str, Any]] = []
    for bar in bars:
        entity = str(bar["entity_id"])
        timeline = timelines.get(entity)
        if timeline is None:
            continue
        close = bar.get("close")
        if close is None or float(close) <= 0:
            # 종가 0 세션이 있다 — 그대로 곱하면 시총 0 이 창고에 남는다.
            continue
        observed_at = bar["observed_at"]
        shares = timeline.known_at(observed_at)
        if shares is None:
            continue
        rows.append(
            {
                "entity_id": entity,
                "valid_from": bar["valid_from"],
                "observed_at": observed_at,
                "source": SOURCE,
                "market": str(market),
                "metric": MARKET_CAP,
                "value": float(close) * shares,
            }
        )
    return rows


# -----------------------------------------------------------------------------
# 적재
# -----------------------------------------------------------------------------


def shares_run_id(market: Market, stamp: str) -> str:
    """분기 단위 실행 id. 같은 분기를 두 번 돌리면 창고가 거부한다."""
    return f"bf-{MARKET_STATS}-{market}-shares-{stamp}"


def market_cap_run_id(market: Market, day: date) -> str:
    """세션당 하나.

    **파티션 폭발을 막는 것이 이 단위다.** 창고는 ``observed_at`` 날짜로
    파티션하는데, 시가총액 행의 관측시각은 그 세션의 봉 관측시각이므로 한
    세션이 한 파티션에 통째로 들어간다 — 세션당 한 번 append 하면 파티션당
    파일이 하나다. 종목 축으로 넣으면 6,648개 파일이 파티션마다 생긴다
    (``ls_us_source.us_run_id`` 가 겪은 247만 개와 같은 실패).
    """
    return f"bf-{MARKET_STATS}-{market}-mcap-{day.strftime('%Y%m%d')}"


def refresh_stamp(moment: datetime) -> str:
    """관측시각 → ``2026W33``. **실행 단위는 주다.**

    상장주식수 자체는 분기 공시지만, 회사마다 결산월과 제출일이 달라 **새
    공시는 매주 들어온다.** 분기에 한 번만 돌리면 그 사이 석 달치 공시가
    창고에 없는 상태로 시가총액이 계산된다 — 값은 그럴듯한데 최대 세 달 묵은
    주식수를 쓴 값이다.

    스탬프가 곧 일정표다. 매일 불러도 그 주에 이미 돈 실행은 창고의
    매니페스트가 걸러 준다 — 별도의 주간 크론을 두지 않는 이유다.
    """
    year, week, _ = moment.isocalendar()
    return f"{year}W{week:02d}"


@dataclass
class SharesReport:
    tickers: int = 0
    rows: int = 0
    #: companyfacts 에 CIK 자체가 없는 종목.
    unknown: list[str] = field(default_factory=list)
    #: CIK 는 있는데 발행주식수 태그가 없는 종목 (ETF·워런트·다중클래스 일부).
    untagged: list[str] = field(default_factory=list)
    #: 태그별 종목 수. 사슬 중 어느 쪽이 실제로 일하는지 보여준다.
    tags: dict[str, int] = field(default_factory=dict)
    skipped: bool = False

    def render(self) -> str:
        covered = self.tickers - len(self.unknown) - len(self.untagged)
        ratio = covered / self.tickers if self.tickers else 0.0
        tags = ", ".join(f"{tag} {count:,}" for tag, count in sorted(self.tags.items()))
        return (
            f"종목 {self.tickers:,}개 중 {covered:,}개 커버({ratio:.0%}) · "
            f"{self.rows:,}행 · CIK 없음 {len(self.unknown):,} · "
            f"태그 없음 {len(self.untagged):,}\n  태그별: {tags}"
        )


@dataclass
class UsSharesBackfiller:
    """미장 발행주식수를 ``market_stats`` 에 넣는다.

    **한 번의 append 로 끝낸다.** 종목마다 append 하면 종목당 25개 공시가
    25개 파티션으로 흩어져 파일이 6,648 × 25 = 16만 개가 된다. 한 번에 넣으면
    파일은 서로 다른 공시일 수(≈1,300개)만큼만 생긴다.
    """

    store: Any
    facts: SecBulkFacts
    market: Market = Market.US
    cutoff_hour: int = EDGAR_CUTOFF_HOUR
    #: 이 날짜보다 이른 ``end`` 는 넣지 않는다.
    since: date | None = None
    #: 이미 창고에 있는 (종목, 기준일) → 값들. 같은 값을 다시 넣지 않는다.
    known: Mapping[tuple[str, date], set[float]] = field(default_factory=dict)
    on_ticker: Any = None

    def run(self, listings: Sequence[Any], *, run_id: str) -> SharesReport:
        """``listings`` 는 ``us_universe.UsListing`` — ``ticker`` 와 ``cik`` 를 쓴다."""
        report = SharesReport(tickers=len(listings))
        if self.store.ingest_run_recorded(MARKET_STATS, run_id):
            report.skipped = True
            return report

        rows: list[dict[str, Any]] = []
        for listing in listings:
            payload = self.facts.facts_for(int(listing.cik))
            if payload is None:
                report.unknown.append(listing.ticker)
                continue
            facts = share_facts(payload)
            if not facts:
                report.untagged.append(listing.ticker)
                continue
            report.tags[facts[0].tag] = report.tags.get(facts[0].tag, 0) + 1

            entity = f"{self.market}:{listing.ticker}"
            fresh = [
                fact
                for fact in facts
                if fact.value not in self.known.get((entity, fact.end), ())
            ]
            ticker_rows = shares_rows(
                fresh, ticker=listing.ticker, market=self.market,
                cutoff_hour=self.cutoff_hour, since=self.since,
            )
            rows.extend(ticker_rows)
            if self.on_ticker is not None:
                self.on_ticker(listing.ticker, len(ticker_rows))

        if not rows:
            # 새 공시가 하나도 없으면 매니페스트를 남기지 않는다 — 빈 것을
            # 완료로 기록하면 그 주에 뒤늦게 들어온 공시를 영영 건너뛴다.
            #
            # 그 대가로 **그 주는 매일 다시 훑는다**(zip 읽기 ~40초). 스탬프가
            # 주 단위라 실제로는 거의 일어나지 않는다 — 6,600개 기업의 결산월이
            # 흩어져 있어 새 공시가 없는 주가 사실상 없기 때문이다. 스탬프를
            # 분기로 잡았다면 이 절충이 성립하지 않는다.
            return report
        report.rows = self.store.append(MARKET_STATS, rows, ingest_run_id=run_id)
        return report


def existing_shares(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, date], set[float]]:
    """이미 적재된 shares 행 → (종목, 기준일) → 값 집합.

    창고는 append-only 라 지울 수 없다. 같은 값을 다시 넣으면 정정본이 아닌데
    ``revision`` 만 늘어난 유령 행이 쌓인다 — 그것을 막는 것이 이 사전이다.
    """
    known: dict[tuple[str, date], set[float]] = {}
    for row in rows:
        moment = row["valid_from"]
        key = (str(row["entity_id"]), moment.date() if isinstance(moment, datetime) else moment)
        known.setdefault(key, set()).add(float(row["value"]))
    return known


def session_bars(frame: Any) -> Iterator[tuple[date, list[dict[str, Any]]]]:
    """가격 프레임을 세션별로 쪼갠다. 세션 하나가 적재 단위다
    (``us_universe_panel.group_sessions`` 와 같은 이유 — 파티션 폭발 방지)."""
    if frame.empty:
        return
    days = frame["valid_from"].dt.date
    for day in sorted(set(days)):
        yield day, frame[days == day].to_dict(orient="records")


def year_windows(start: date, end: date, *, days: int = 365) -> Iterator[tuple[date, date]]:
    """긴 구간을 나눠 읽기 위한 창. ``as_of`` 가 아니라 **창**을 옮긴다.

    ``as_of`` 를 옮기면 뒤늦게 도착한 정정본을 놓친다 (``store.get`` docstring).
    미장 시세는 5년에 645만 행(513MB)이라 한 번에 퍼오면 메모리가 아깝다.
    """
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)
