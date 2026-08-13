"""문서 수집자 — DART 공시.

port(collectors): LS_KR news/dart_collector.py 의 엔드포인트·필드맵 이식.
뉴스 수집기(네이버 HTML 스크래핑 폴백체인)는 **가져오지 않는다** — 외부 구조
변경에 취약해 배관이라기보다 부채다 (postmortem-ls.md §6-7).

``valid_from`` 은 공시 접수일, ``observed_at`` 은 우리가 받아온 시각이다.
접수일을 관측시각으로 쓰면 아직 공표되지 않은 공시를 그날 아침부터 알고 있던
것이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import CollectorError, MissingCredentials
from quant_rl_trading.collectors.latency import LatencyRecorder
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store

#: port: LS_KR news/dart_collector.py:27
DART_BASE = "https://opendart.fss.or.kr/api"
SOURCE = "dart"

VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def _receipt_timestamp(yyyymmdd: str) -> datetime:
    return datetime.strptime(yyyymmdd, "%Y%m%d").replace(tzinfo=UTC)


def normalize_filings(
    items: list[dict[str, Any]],
    *,
    observed_at: datetime,
    raw_path: str = "",
) -> list[dict[str, Any]]:
    """DART ``list.json`` 응답 → documents 행.

    필드맵 port: LS_KR news/dart_collector.py:29-46
    (``rcept_no, corp_code, corp_name, stock_code, report_nm, rcept_dt, flr_nm``).
    """
    normalized: list[dict[str, Any]] = []
    for item in items:
        receipt = str(item.get("rcept_no") or "").strip()
        receipt_date = str(item.get("rcept_dt") or "").strip()
        if not receipt or len(receipt_date) != 8 or not receipt_date.isdigit():
            continue

        stock_code = str(item.get("stock_code") or "").strip()
        # 종목코드가 없는 공시(비상장 제출인)는 DART 고유번호로 식별한다.
        entity_id = f"KR:{stock_code}" if stock_code else f"DART:{item.get('corp_code', '')}"

        normalized.append(
            {
                "entity_id": entity_id,
                "valid_from": _receipt_timestamp(receipt_date),
                "observed_at": observed_at,
                "source": SOURCE,
                "doc_id": receipt,
                "doc_type": "filing",
                "title": str(item.get("report_nm") or ""),
                "filer": str(item.get("flr_nm") or item.get("corp_name") or ""),
                "url": f"{VIEWER}{receipt}",
                "raw_path": raw_path,
            }
        )
    return normalized


@dataclass
class DocumentCollector:
    store: Store
    clock: Clock
    archive: RawArchive
    api_key: str
    transport: httpx.BaseTransport | None = None
    timeout: float = 10.0

    def available(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("YOUR_")

    def collect_filings(
        self,
        *,
        ingest_run_id: str,
        begin: str,
        end: str,
        page_count: int = 100,
    ) -> int:
        """기간 내 공시 목록 수집. ``begin``/``end`` 는 ``YYYYMMDD``."""
        if not self.available():
            raise MissingCredentials("OPENDART_API_KEY 미설정")

        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=SOURCE, ingest_run_id=ingest_run_id
        )

        with latency.stage("fetch", f"{begin}~{end}"):
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                response = client.get(
                    f"{DART_BASE}/list.json",
                    params={
                        "crtfc_key": self.api_key,
                        "bgn_de": begin,
                        "end_de": end,
                        "page_count": page_count,
                    },
                )
            if response.status_code != 200:
                raise CollectorError(f"DART HTTP {response.status_code}: {response.text[:200]}")
            payload = response.json()

        # DART 는 HTTP 200 에 status 코드로 실패를 싣는다. 013 = 조회 결과 없음.
        status = str(payload.get("status") or "")
        if status not in ("000", "013"):
            raise CollectorError(f"DART status={status} message={payload.get('message')}")

        observed_at = self.clock.now()
        with latency.stage("archive", f"{begin}~{end}"):
            path = self.archive.save(
                SOURCE,
                payload,
                observed_at=observed_at,
                ingest_run_id=ingest_run_id,
                label=f"list-{begin}-{end}",
            )

        with latency.stage("normalize", f"{begin}~{end}"):
            rows = normalize_filings(
                payload.get("list") or [], observed_at=observed_at, raw_path=str(path.name)
            )

        with latency.stage("append", f"{begin}~{end}"):
            written = self.store.append("documents", rows, ingest_run_id=ingest_run_id)

        latency.flush()
        return written
