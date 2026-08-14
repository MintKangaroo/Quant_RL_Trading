"""DART 기업개황 — **진짜 업종 분류**.

## 왜 KRX 로는 안 되는가 (실측 2026-08-15)

`sectors` 테이블은 KRX Open API 일별매매의 `SECT_TP_NM` 에서 왔는데, 이건
업종(GICS 류) 분류가 아니라 **소속부**다 — KOSPI 942종목 전부 빈 문자열이고
KOSDAQ 만 우량기업부·벤처기업부 등으로 갈린다 (krx_openapi.py 모듈 docstring).
업종분류를 KRX Open API 에서 따로 받을 수 있는지 실제 호출로 확인했다:

    /svc/apis/sto/stk_isu_base_info  (유가증권 종목기본정보) → 942행
    /svc/apis/sto/ksq_isu_base_info  (코스닥 종목기본정보)   → 1,821행
    /svc/apis/sto/knx_isu_base_info  (코넥스 종목기본정보)   → 109행

셋 다 응답에 `SECT_TP_NM` 만 있고 업종 필드는 없다 — KRX Open API(정식
경로, data-dbg.krx.co.kr) 의 상품 목록(openapi.krx.co.kr 서비스 목록, 실측
확인) 에도 업종분류 상품이 없다. `data.krx.co.kr` 의 "업종분류현황" 화면은
있지만 OTP 기반 CSV 다운로드로만 열려 있고, 이건 이 프로젝트가 이미 금지한
`pykrx` 스크래핑과 같은 경로다(krx_openapi.py: "약관상 자동화 수집이 금지돼
있고 실제로 IP 가 차단됐다"). 그래서 안 쓴다.

## DART 로 되는 이유 (실측 2026-08-15)

`company.json`(기업개황) 이 `induty_code`(표준산업분류 코드)를 준다 — 삼성전자
(005930) 실측: `induty_code: "264"`. 무작위 KR 종목 40개로 확인한 결과
38/40 이 코드를 받았다(2개는 corpCode.xml 에 매핑이 아예 없는 종목 —
우선주 등 특수 종목류로 보인다, 실패가 아니라 DART 가 다루지 않는 종목).

**한 콜에 회사 하나다.** `fnlttMultiAcnt` 같은 배치가 없다. KR 전체가 약
2,900콜이고, DART 일 한도(20,000, dart_source.py 참고)의 15% 다 — 다른
DART 수집기(재무·공시)와 하루를 나눠 써도 여유가 크다.

## `sectors` 테이블의 자연키 (team-lead 가 고침, 2026-08-15)

`store/tables.py` 의 `sectors` TableSpec 은 원래 `natural_key` 를 선언하지
않아 기본값 `(entity_id, valid_from)` 를 썼다. 그러면 KRX 소속부와 이 DART
업종을 같은 종목·같은 날로 넣었을 때 게이트가 최신 관측 하나만 남기고
나머지를 읽기에서 지웠을 것이다 — 소속부는 소속부대로 유효한 사실이라 그건
틀린 동작이었다. team-lead 가 `natural_key = (entity_id, valid_from, source)`
로 넓혀서 고쳤다 — 이제 두 분류체계가 같은 날짜에 공존해도 둘 다 읽힌다.
`selector/candidates.py` 의 `sector_map()` 도 `source` 를 필수 인자로 받게
바뀌어, 호출부가 어느 체계를 쓸지 고르지 않으면 아예 부를 수 없다.

읽는 쪽에서 이 컬렉터가 만든 행을 고르려면 `source="dart_company"` 를 준다.

## 파티션 폭발을 피한다

과거에 종목 축으로 `store.append()` 를 개별 호출해 파일 247만 개를 만든
전례가 있다(us-backfill). 여기서는 **전 종목의 행을 메모리에 모았다가 한
번에(또는 관측일별로 몇 번) `store.append()` 한다** — 회사당 API 콜은
2,900번이지만 창고에 쓰는 콜은 1번이다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_rl_trading.collectors.dart_source import DartSource, DartUnavailable
from quant_rl_trading.collectors.errors import CollectorError

SECTORS = "sectors"
#: KRX 소속부(SECT_TP_NM, source="krx_openapi")와 같은 열을 쓰지만 값의
#: 근원이 다르다는 것을 source 컬럼으로 남긴다. natural_key 가 넓어지기
#: 전까지는 이 컬럼이 두 스킴을 구분하는 유일한 단서다(모듈 docstring).
SOURCE = "dart_company"

#: 값 자체에도 스킴을 접두어로 남긴다. source 컬럼을 안 보고 `sector` 값만
#: 봐도 "이건 소속부가 아니라 표준산업분류다" 를 알 수 있게 — 소속부 값은
#: "우량기업부" 처럼 순한글이라 접두어가 있으면 사람이 훑어도 안 헷갈린다.
SECTOR_PREFIX = "KSIC:"


def normalize_sectors(
    rows: Iterable[tuple[str, dict[str, Any] | None]],
    *,
    market: str,
    valid_from: datetime,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """(entity_id, company.json 응답) 목록 → sectors 행.

    응답이 ``None``(데이터 없음)이거나 ``induty_code`` 가 비어 있으면 행을
    만들지 않는다 — 모르는 종목을 "기타" 로 채우면 그 종목들이 한 섹터로
    묶여 상한이 엉뚱한 종목을 걸러낸다(sectors TableSpec 의 기존 경고와
    같은 이유).
    """
    out: list[dict[str, Any]] = []
    for entity_id, payload in rows:
        if not payload:
            continue
        code = str(payload.get("induty_code") or "").strip()
        if not code:
            continue
        out.append(
            {
                "entity_id": entity_id,
                "valid_from": valid_from,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": market,
                "sector": f"{SECTOR_PREFIX}{code}",
            }
        )
    return out


@dataclass
class SectorFetchReport:
    """수집 진행 보고. **적재는 여기서 하지 않는다** — fetch 와 append 를
    가른 이유는 실패한 콜 몇 개 때문에 전부 다시 받는 일을 없애기 위해서다.
    """

    requested: int = 0
    fetched: int = 0
    no_corp_code: int = 0
    no_induty: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    rows: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"요청 {self.requested} · 수신 {self.fetched} · "
            f"corp_code 없음 {self.no_corp_code} · induty 없음 {self.no_induty} · "
            f"실패 {len(self.failures)}"
        )


@dataclass
class SectorCollector:
    """KR 종목의 DART 업종 분류를 모은다. 창고에는 쓰지 않는다 — 그건
    호출부가 ``normalize_sectors`` + ``store.append()`` 한 번으로 한다
    (모듈 docstring: 파티션 폭발을 피한다).
    """

    source: DartSource
    pause_sec: float = 0.08
    sleep: Callable[[float], None] = time.sleep

    def fetch(
        self,
        codes_to_corp: dict[str, str],
        *,
        market: str = "KR",
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SectorFetchReport:
        """``{종목코드: corp_code}`` 를 받아 회사 하나씩 개황을 부른다.

        corp_code 가 없는 종목(우선주 등 DART 미매핑)은 애초에 콜을 안
        한다 — 실패로 세지 않는다, 시도할 수 없는 것과 실패한 것은 다르다.

        ``on_progress`` 는 콜마다(성공·실패·건너뜀 무관하게) 불린다.
        회사 수천 개를 개별 콜로 돌면 수십 분이 걸린다 — 진행 상황이 없으면
        느린 것과 멈춘 것을 구분할 수 없다.
        """
        report = SectorFetchReport(requested=len(codes_to_corp))
        total = len(codes_to_corp)
        for done, (code, corp_code) in enumerate(sorted(codes_to_corp.items()), start=1):
            entity_id = f"{market}:{code}"
            if not corp_code:
                report.no_corp_code += 1
                if on_progress:
                    on_progress(done, total)
                continue
            try:
                payload = self.source.company_info(corp_code)
            except (DartUnavailable, CollectorError) as error:
                report.failures.append((entity_id, str(error)))
                if on_progress:
                    on_progress(done, total)
                continue
            report.fetched += 1
            if payload is None or not str(payload.get("induty_code") or "").strip():
                report.no_induty += 1
            report.rows.append((entity_id, payload))
            self.sleep(self.pause_sec)
            if on_progress:
                on_progress(done, total)
        return report
