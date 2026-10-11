"""KRX 개별주식선물 일별매매 → ``stock_futures`` 표 (N3, 2026-10-11 사용자 승인).

## 무엇을 받나

KRX Open API 파생 카테고리의 주식선물(유가 ``/drv/eqsfu_stk_bydd_trd`` · 코스닥 ``/drv/eqkfu_ksq_bydd_trd``).
하루 1콜 × 시장 2. 응답 15필드(정산가 ``SETL_PRC`` · 기초자산 현물가 ``SPOT_PRC`` · 미결제약정 ``ACC_OPNINT_QTY`` …)를
이름만 바꿔 **전부** 남긴다. 설계: docs/design/data-contract.md §4-3.

## 거르는 것 둘 (원본은 RawArchive 에 그대로 남는다)

- **스프레드**(``ISU_NM`` 에 ``SP``, 예 "… SP 2403-2406") — 두 월물의 차이 상품이라 가격 수준이 다르다. 월물(``F``)만 둔다.
- **ETF 선물**(``PROD_NM`` 이 ``ETF`` 로 시작) — 기초자산이 종목이 아니다.

## 기초자산 매핑 — 이름으로만 할 수 있다

응답에 기초자산 종목코드가 없다(``ISU_CD`` 는 선물 코드). ``PROD_NM`` 에서 " 선물" 을 떼어 창고 ``universe``(KR)의
종목명 이력과 맞춘다. 실측(2015-08-03 응답): ``PROD_NM`` 은 **그 날의 이름이 아니라 지금의 상품명**이 과거에도 붙어 온다
("HD현대인프라코어"·"iM금융지주" 가 2015 행에 있다). 그래서 날짜에 그 이름을 쓰던 종목이 없으면 시간상 가장 가까운
구간의 종목을 고른다. 이름이 지금과 다른 줄임말("NC"·"한국가스"·"케이티앤지")은 ``ALIASES`` 로, 그래도 안 되면
유일한 접두 일치로 잡는다. 셋 다 실패하면 ``underlying_id`` 를 비우고 ``map_method='none'`` 으로 **행은 남긴다** —
모르는 것을 지어내지 않는다. 실패 목록은 백필 도구가 따로 적는다.

매핑은 **식별자**지 예측 정보가 아니다. 다만 universe 는 2021-08 부터만 있어 그 앞 행은 뒤에 관측된 이름으로 매핑된다
(그 앞에 상장폐지된 기초자산 — 우리은행·CJ E&M 등 — 은 매핑 실패로 남는다). 읽는 쪽은 매핑을 같은 날 현물 종가
(``SPOT_PRC`` == 원주가 종가)로 검산할 수 있다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from quant_rl_trading.collectors.krx_openapi import SOURCE
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.panels import Panel

TABLE = "stock_futures"
RUN_KEY = "stock-futures"

#: 응답 필드 → 저장 열. 15필드 중 ``BAS_DD``(=valid_from)·``PROD_NM``·``ISU_CD``·``ISU_NM`` 은 따로 다룬다.
NUMERIC_FIELDS = {
    "TDD_CLSPRC": "close",
    "CMPPREVDD_PRC": "change",
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "SPOT_PRC": "spot_price",
    "SETL_PRC": "settle_price",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value",
    "ACC_OPNINT_QTY": "open_interest",
}

#: 상품명(줄임) → 창고 universe 의 종목명. 이름이 바뀐 것이 아니라 **KRX 파생 상품명이 줄임말인 것**만 둔다.
#: 백필 실패 목록을 보고 늘린다 — 짐작으로 채우지 않는다.
ALIASES: dict[str, str] = {
    "NC": "엔씨소프트",
    "한국가스": "한국가스공사",
    "케이티앤지": "KT&G",
    "삼성SDS": "삼성에스디에스",
    "오씨아이홀딩스": "OCI홀딩스",
    # 2024-04-22 신규 상장 상품 넷 — KRX 파생 상품명이 정식 사명(한글 풀이)이다(백필 실패 목록 2026-10-11).
    "에이치디현대일렉트릭": "HD현대일렉트릭",
    "롯데칠성음료": "롯데칠성",
    "코오롱인더스트리": "코오롱인더",
    "원익큐엔씨": "원익QNC",
    "PSK홀딩스": "피에스케이홀딩스",   # 2026-09-14 신규 상품
}

_CONTRACT = re.compile(r"\sF\s+(\d{6})\s*$")
_SPREAD = re.compile(r"\sSP\s")
_ENTITY = re.compile(r"^KR:[0-9A-Z]{6}$")


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def norm_name(name: str) -> str:
    """비교용 이름 — 공백·(주)·대소문자 차이를 없앤다. "POSCO 홀딩스" == "POSCO홀딩스"."""
    s = str(name or "").replace("(주)", "").replace("㈜", "")
    return re.sub(r"\s+", "", s).upper()


def underlying_name(prod_nm: str) -> str:
    """'삼성전자 선물' → '삼성전자'."""
    s = str(prod_nm or "").strip()
    return s[: -len("선물")].strip() if s.endswith("선물") else s


def is_etf(prod_nm: str) -> bool:
    return str(prod_nm or "").strip().upper().startswith("ETF")


def contract_month(isu_nm: str) -> str | None:
    """'BNK금융지  F 201509 ' → '201509'. 스프레드·형식 밖은 None."""
    m = _CONTRACT.search(str(isu_nm or ""))
    return m.group(1) if m else None


@dataclass(frozen=True)
class NameSpan:
    entity_id: str
    first: date
    last: date


@dataclass
class UnderlyingResolver:
    """상품명 → 기초자산 entity_id. 이름 이력(그 이름이 쓰인 첫날·끝날)으로 날짜에 맞는 종목을 고른다."""

    spans: dict[str, list[NameSpan]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=lambda: dict(ALIASES))

    @classmethod
    def from_records(cls, records: Iterable[tuple[str, str, date, date]]) -> UnderlyingResolver:
        """(entity_id, 종목명, 첫날, 끝날) 묶음에서 만든다. 보통주 6자리 코드만 받는다."""
        spans: dict[str, list[NameSpan]] = {}
        for entity_id, name, first, last in records:
            if not _ENTITY.match(str(entity_id)) or not name:
                continue
            spans.setdefault(norm_name(name), []).append(NameSpan(str(entity_id), first, last))
        return cls(spans=spans)

    @classmethod
    def from_store(cls, store: Any, *, as_of: datetime) -> UnderlyingResolver:
        """창고 ``universe``(KR) 전 이력에서 (종목, 이름)별 첫날·끝날을 모은다."""
        lookback = (as_of.date() - date(2000, 1, 1)).days
        frame = store.get("universe", as_of=as_of, market="KR", lookback=lookback,
                          columns=["entity_id", "valid_from", "name"])
        if frame.empty:
            return cls()
        frame = frame.dropna(subset=["name"])
        frame["day"] = frame["valid_from"].dt.date
        grouped = frame.groupby(["entity_id", "name"])["day"].agg(["min", "max"]).reset_index()
        return cls.from_records(
            (row.entity_id, row.name, row.min, row.max) for row in grouped.itertuples(index=False)
        )

    @staticmethod
    def _pick(spans: list[NameSpan], day: date) -> str:
        covering = [s for s in spans if s.first <= day <= s.last]
        if covering:
            return covering[0].entity_id

        def gap(s: NameSpan) -> int:
            return (s.first - day).days if day < s.first else (day - s.last).days

        return min(spans, key=gap).entity_id

    def resolve(self, prod_nm: str, day: date) -> tuple[str | None, str]:
        """(entity_id 또는 None, 방법). 방법: exact | alias | prefix | none."""
        key = norm_name(underlying_name(prod_nm))
        if not key:
            return None, "none"
        if key in self.spans:
            return self._pick(self.spans[key], day), "exact"
        alias = self.aliases.get(underlying_name(prod_nm))
        if alias is not None and norm_name(alias) in self.spans:
            return self._pick(self.spans[norm_name(alias)], day), "alias"
        # 유일한 접두 일치 — "한국가스" → "한국가스공사". 우선주("…우")는 뺀다. 둘 이상이면 고르지 않는다.
        hits = [n for n in self.spans if n.startswith(key) and not re.search(r"\d?우[A-Z]?$", n)]
        ents = {s.entity_id for n in hits for s in self.spans[n]}
        if len(ents) == 1:
            return self._pick([s for n in hits for s in self.spans[n]], day), "prefix"
        return None, "none"


def normalize_stock_futures(
    rows: Iterable[dict[str, Any]],
    *,
    market: str,
    valid_from: datetime,
    observed_at: datetime,
    resolver: UnderlyingResolver,
) -> list[dict[str, Any]]:
    """원 응답 → ``stock_futures`` 행. 월물만, ETF 선물 제외. 매핑 실패도 행은 남긴다(``underlying_id`` None)."""
    day = valid_from.date()
    out: list[dict[str, Any]] = []
    for raw in rows:
        isu_cd = str(raw.get("ISU_CD") or "").strip()
        prod_nm = str(raw.get("PROD_NM") or "").strip()
        isu_nm = str(raw.get("ISU_NM") or "")
        if not isu_cd or not prod_nm or is_etf(prod_nm) or _SPREAD.search(isu_nm):
            continue
        month = contract_month(isu_nm)
        if month is None:
            continue
        underlying, method = resolver.resolve(prod_nm, day)
        out.append(
            {
                "entity_id": f"{market}:FUT:{isu_cd}",
                "valid_from": valid_from,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": market,
                "board": str(raw.get("_board") or ""),
                "mkt_nm": str(raw.get("MKT_NM") or ""),
                "prod_nm": prod_nm,
                # ISU_CD 2~4번째 글자 = 상품(기초자산) 코드. 월물이 바뀌어도 같다("1BTK9000"·"1BTKC000" → BTK).
                "product_code": isu_cd[1:4],
                "isu_cd": isu_cd,
                "isu_nm": isu_nm.strip(),
                "contract_month": month,
                "underlying_id": underlying,
                "map_method": method,
                **{name: _number(raw.get(src)) for src, name in NUMERIC_FIELDS.items()},
            }
        )
    return out


def stock_futures_panel(resolver: UnderlyingResolver) -> Panel:
    """PanelBackfiller 용 패널. 매핑기를 물고 있어야 해서 모듈 상수(OPENAPI_PANELS)가 아니라 공장 함수다."""

    def expand(row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime) -> list[dict[str, Any]]:
        return normalize_stock_futures([row], market=str(market), valid_from=valid_from,
                                       observed_at=observed_at, resolver=resolver)

    return Panel(
        table=TABLE,
        run_key=RUN_KEY,
        fetch=lambda source, day: source.stock_futures_on(day),  # type: ignore[attr-defined]
        expand=expand,
    )
