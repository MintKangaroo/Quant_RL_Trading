"""국장 거시지표 — KOSIS(국가통계포털).

ECOS(한국은행)가 2026-08-13 현재 모든 엔드포인트에서 503 을 돌려줘서
(UA 를 바꿔도 같다 — 서버 쪽이다) KOSIS 를 대체 경로로 쓴다. KOSIS 는 같은
시점에 정상 응답한다: 키 없이 호출하면 ``{err:"11", errMsg:"유효하지않은
인증KEY입니다."}`` 를 준다. 즉 막힌 것은 네트워크가 아니라 키 하나다.

## 통계표 ID 를 짐작하지 않는다

같은 세션에서 FRED ``release_id`` 를 짐작해 넣었다가, 18이 소매판매가 아니라
H.15 금리여서 화면에 "소매판매"라는 이름으로 금리 일정이 떴다. 같은 실수를
두 번 하지 않는다.

그래서 이 모듈은 **ID 표를 먼저 채워 두지 않는다.** :meth:`KosisSource.find_tables`
로 이름을 조회해 눈으로 확인한 ID 만 ``KOSIS_TABLES`` 에 넣는다. 확인 전까지
그 표는 비어 있고, 비어 있으면 수집기는 아무것도 적재하지 않는다 — 지어낸
숫자를 화면에 올리는 것보다 빈 화면이 낫다.

## KOSIS 는 발표 일정을 주지 않는다

FRED 와 다른 점이다. KOSIS 는 **이미 발표된 값**만 준다. 그래서 국장은
``status="released"`` 행만 생기고, ``scheduled_at`` 은 그 통계의 기준시점이
아니라 **공표 시각**이다. 기준시점(예: 2026년 7월 소비자물가)은 관측된 값의
라벨이지 발표 시각이 아니다 — 둘을 섞으면 7월 지표를 7월부터 알고 있던 것이
된다.

공표 시각을 KOSIS 가 정확히 주지 않으므로 ``PRD_DE`` (기준시점) 기준으로
**공표 지연**을 더해 잡는다. 통계청 소비자물가는 익월 초에 공표된다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from lattice.collectors.errors import CollectorError, MissingCredentials
from lattice.collectors.market_hours import Market
from lattice.replay.clock import Clock

MACRO_RELEASES = "macro_releases"
SOURCE = "kosis"

KOSIS_BASE = "https://kosis.kr/openapi"
KEY_ENV = "KOSIS_API_KEY"

KST = ZoneInfo("Asia/Seoul")
#: 통계청 공표는 오전 8시다. 분 단위가 틀리면 장 시작 전인지 후인지가 뒤집힌다.
KR_RELEASE_TIME = time(8, 0)

#: **전부 ``--discover`` 로 이름을 조회해 눈으로 확인한 값이다** (2026-08-13).
#: 짐작해서 넣지 않는다 — 이유는 모듈 docstring 참조.
#:
#: CPI 는 ``itmId`` 가 짧은 코드(``T``)인데 PPI 는 긴 내부 코드다. 두 표의
#: 구조가 다르므로 한쪽을 보고 다른 쪽을 유추할 수 없다.
#:
#: ``publication_lag_days`` 는 익월 1일로부터의 지연이다. 실제 공표일과
#: 맞춰 잡았다 — CPI 202607 은 2026-08-04 공표(``SEND_DE``), PPI 는 익월
#: 하순이다.
KOSIS_TABLES: dict[str, dict[str, str]] = {
    "CPI": {
        "org_id": "101",  # 통계청
        "tbl_id": "DT_1J22003",  # 소비자물가지수(2020=100)
        "itm_id": "T",  # 소비자물가지수(총지수)
        "obj_l1": "T10",  # 전국
        "cycle": "M",
        "unit": "2020=100",
        "publication_lag_days": "3",
    },
    "PPI": {
        "org_id": "301",  # 한국은행
        "tbl_id": "DT_404Y014",  # 생산자물가지수(기본분류)
        "itm_id": "13103134604999",
        "obj_l1": "13102134604ACC_CD.*AA",  # 계정코드별 총지수
        "cycle": "M",
        "unit": "2020=100",
        "publication_lag_days": "21",
    },
    "UNEMPLOYMENT": {
        "org_id": "101",  # 통계청
        "tbl_id": "DT_1DA7102S",  # 성/연령별 실업률
        "itm_id": "T80",  # 실업률
        # 이 표는 분류축이 둘이다. 하나만 주면 err=21 로 거부당한다.
        "obj_l1": "0",  # 성별: 계
        "obj_l2": "00",  # 연령계층별: 계
        "cycle": "M",
        "unit": "%",
        "publication_lag_days": "11",
    },
    "GDP": {
        "org_id": "301",  # 한국은행
        "tbl_id": "DT_200Y102",  # 국민계정 주요지표(분기지표)
        "itm_id": "13103136269999",
        "obj_l1": "13102136269ACC_ITEM.10111",  # 실질 GDP, 계절조정 전기비
        "cycle": "Q",
        "unit": "%",
        # 속보치는 분기 종료 후 약 4주. 분기의 **마지막 달** 기준이다.
        "publication_lag_days": "25",
    },
}


class KosisUnavailable(CollectorError):
    """KOSIS 가 응답하지 않았거나 키가 거부됐다."""


#: KOSIS 는 ``format=json`` 을 줘도 **따옴표 없는 키**를 쓴다
#: (``[{LIST_NM:"인구",LIST_ID:"A"}]``). content-type 도 ``text/html`` 이다.
#: 표준 JSON 이 아니라 JS 객체 리터럴이라 ``response.json()`` 이 터진다.
_BARE_KEY = re.compile(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:')


def _loads(text: str) -> Any:
    """KOSIS 응답 → 파이썬 객체.

    키에 따옴표를 씌워 표준 JSON 으로 만든 뒤 파싱한다. **값은 건드리지
    않는다** — 값은 이미 따옴표 안에 있고, 거기까지 손대면 한글이나 콜론이
    들어간 통계표 이름이 깨진다.
    """
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return json.loads(_BARE_KEY.sub(r'\1"\2":', stripped))
    except ValueError as error:
        raise KosisUnavailable(f"KOSIS 응답을 읽을 수 없다: {stripped[:200]}") from error


def _number(value: Any) -> float | None:
    if value is None or value in ("", "-", "."):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def publication_moment(period: str, *, lag_days: int) -> datetime | None:
    """기준시점 → 공표 시각(UTC). 월(``YYYYMM``)과 분기(``YYYYQn``) 를 받는다.

    기준시점을 그대로 발표 시각으로 쓰면 7월 지표를 7월 1일부터 알고 있던
    것이 된다. 공표 지연을 더해야 한다.

    분기는 **그 분기의 마지막 달**을 기준으로 삼는다. 2분기 GDP 는 6월까지의
    실적이므로 7월 이후에나 나온다 — 4월 기준으로 잡으면 두 달을 미리 아는
    셈이 된다.
    """
    text = str(period).strip().upper()

    if "Q" in text and len(text) == 6:
        # 20262Q / 2026Q2 두 표기가 모두 돌아다닌다.
        digits = text.replace("Q", "")
        if not digits.isdigit() or len(digits) != 5:
            return None
        year, quarter = int(digits[:4]), int(digits[4])
        if not 1 <= quarter <= 4:
            return None
        month = quarter * 3
    elif len(text) == 6 and text.isdigit():
        year, month = int(text[:4]), int(text[4:])
    else:
        return None

    if not 1 <= month <= 12:
        return None
    base = datetime(year, month, 1, tzinfo=KST) + timedelta(days=32)
    first_of_next = base.replace(day=1)
    local = datetime.combine(
        (first_of_next + timedelta(days=lag_days)).date(), KR_RELEASE_TIME, tzinfo=KST
    )
    return local.astimezone(UTC)


@dataclass
class KosisSource:
    """KOSIS 조회. 키가 없으면 아무것도 하지 않는다."""

    api_key: str
    name: str = SOURCE
    timeout: float = 25.0
    client: httpx.Client | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> KosisSource:
        source = env if env is not None else dict(os.environ)
        return cls(api_key=(source.get(KEY_ENV) or "").strip())

    def usable(self) -> bool:
        return bool(self.api_key) and bool(KOSIS_TABLES)

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            raise MissingCredentials(f"{KEY_ENV} 미설정")
        owned = self.client is None
        http = self.client or httpx.Client(timeout=self.timeout)
        try:
            response = http.get(
                f"{KOSIS_BASE}{path}",
                params={**params, "apiKey": self.api_key, "format": "json"},
            )
        finally:
            if owned:
                http.close()
        if response.status_code != 200:
            raise KosisUnavailable(f"KOSIS {response.status_code}: {response.text[:200]}")
        payload = _loads(response.text)
        # KOSIS 는 오류도 HTTP 200 으로 준다. 본문을 봐야 실패를 안다.
        if isinstance(payload, dict) and payload.get("err"):
            raise KosisUnavailable(f"KOSIS err={payload['err']}: {payload.get('errMsg')}")
        return payload

    def find_tables(self, *, parent_list_id: str = "", vw_cd: str = "MT_ZTITLE") -> Any:
        """통계표 목록. **ID 를 눈으로 확인하기 위한 것이다.**

        여기서 받은 ``ORG_ID``/``TBL_ID`` 를 ``KOSIS_TABLES`` 에 손으로 옮긴다.
        자동으로 채우지 않는 이유는, 이름이 비슷한 표가 여럿이라 자동 선택이
        틀리면 화면에 엉뚱한 지표가 올바른 이름으로 뜨기 때문이다.
        """
        return self._get(
            "/statisticsList.do",
            {"method": "getList", "vwCd": vw_cd, "parentListId": parent_list_id},
        )

    def observations(self, spec: dict[str, str], *, periods: int = 2) -> list[dict[str, Any]]:
        """최근 관측값. 직전 값까지 받아야 화면에 변화를 보여줄 수 있다."""
        payload = self._get(
            "/Param/statisticsParameterData.do",
            {
                "method": "getList",
                "orgId": spec["org_id"],
                "tblId": spec["tbl_id"],
                "itmId": spec["itm_id"],
                "objL1": spec["obj_l1"],
                # 분류축이 둘인 표가 있다(예: 실업률 = 성별 × 연령계층별).
                # 하나만 주면 KOSIS 가 err=21 로 거부한다.
                **({"objL2": spec["obj_l2"]} if spec.get("obj_l2") else {}),
                "prdSe": spec.get("cycle", "M"),
                "newEstPrdCnt": str(periods),
            },
        )
        return list(payload) if isinstance(payload, list) else []


def observation_rows(
    indicator: str,
    spec: dict[str, str],
    observations: list[dict[str, Any]],
    *,
    observed_at: datetime,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """KOSIS 관측값 → macro_releases 행.

    **공표 시각이 ``observed_at`` 보다 미래면 버린다.** KOSIS 가 아직 공표되지
    않은 잠정치를 주는 경우, 그걸 저장하면 공표 전부터 알고 있던 것이 된다.
    """
    lag_days = int(spec.get("publication_lag_days", 0))
    ordered = sorted(observations, key=lambda row: str(row.get("PRD_DE", "")), reverse=True)
    if not ordered:
        return []

    latest = ordered[0]
    scheduled_at = publication_moment(latest.get("PRD_DE", ""), lag_days=lag_days)
    if scheduled_at is None or scheduled_at > observed_at:
        return []

    actual = _number(latest.get("DT"))
    previous = _number(ordered[1].get("DT")) if len(ordered) > 1 else None
    if actual is None:
        return []

    return [
        {
            "entity_id": f"{Market.KR}:{indicator}",
            "valid_from": observed_at,
            "observed_at": observed_at,
            "source": source,
            "market": str(Market.KR),
            "indicator": indicator,
            "release_name": str(latest.get("TBL_NM") or indicator),
            "scheduled_at": scheduled_at,
            "actual": actual,
            "previous": previous,
            "unit": str(latest.get("UNIT_NM") or spec.get("unit", "")),
            # KOSIS 는 일정을 주지 않는다. 받은 것은 전부 이미 공표된 값이다.
            "status": "released",
        }
    ]


@dataclass
class KosisCollector:
    """확인된 통계표만 돈다. 표가 비어 있으면 아무것도 하지 않는다."""

    store: Any
    source: KosisSource
    clock: Clock
    archive: Any
    market: Market = Market.KR

    def collect(self) -> int:
        if not self.source.usable():
            return 0

        observed_at = self.clock.now().astimezone(UTC)
        run_id = f"macro-{self.market}-{observed_at:%Y%m%dT%H%M%S}"
        if self.store.ingest_run_recorded(MACRO_RELEASES, run_id):
            return 0

        rows: list[dict[str, Any]] = []
        payloads: dict[str, Any] = {}
        for indicator, spec in KOSIS_TABLES.items():
            try:
                observations = self.source.observations(spec)
            except CollectorError:
                # 한 지표 실패로 나머지를 버리지 않는다.
                continue
            payloads[indicator] = observations
            rows.extend(
                observation_rows(indicator, spec, observations, observed_at=observed_at)
            )

        if not rows:
            return 0

        self.archive.save(
            self.source.name,
            payloads,
            observed_at=observed_at,
            ingest_run_id=run_id,
            label=f"kosis-{observed_at:%Y%m%d}",
        )
        return int(self.store.append(MACRO_RELEASES, rows, ingest_run_id=run_id))
