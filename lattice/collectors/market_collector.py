"""시장 수집자 — LS API 에서 가격·유니버스를 받아 curated 에 적재한다.

Collector 는 수집만 한다. 점수를 내지 않는다.

**수정주가를 받지 않는다.** LS t8410 은 ``sujung="Y"`` 가 기본이고 LS_KR 도
그대로 썼지만, 수정주가에는 **미래의 분할·증자가 이미 반영돼 있다**. 그것을
과거 시점 데이터로 저장하면 백테스트는 그 시점에 알 수 없었던 정보를 보게 된다
(data-contract §4). 원주가(``sujung="N"``)를 받아 저장하고, 조정계수는 발효일과
함께 별도로 관리한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lattice.collectors.latency import LatencyRecorder
from lattice.collectors.ls_client import PATH_MARKET, LSClient
from lattice.collectors.market_hours import Market
from lattice.collectors.raw import RawArchive
from lattice.replay.clock import Clock
from lattice.store import Store

SOURCE = "ls_api"

#: 원주가. "Y" 는 수정주가이며 미래를 포함한다.
SUJUNG_RAW = "N"


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _bar_timestamp(yyyymmdd: str) -> datetime:
    """LS 의 ``date`` 는 KST 거래일이다. 그 날의 UTC 자정으로 고정한다.

    거래일을 시각으로 바꿀 때 지역 자정을 쓰면 서머타임·시차에서 하루가
    밀린다. 저장은 UTC 로 통일하고 표시할 때만 지역 시각으로 바꾼다.
    """
    return datetime.strptime(yyyymmdd, "%Y%m%d").replace(tzinfo=UTC)


def normalize_ohlcv(
    rows: list[dict[str, Any]],
    *,
    entity_id: str,
    market: Market,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """t8410OutBlock1 → prices 행.

    필드맵 port: LS_KR broker/ls_client.py:406-429
    (``date, open, high, low, close, jdiff_vol``).

    ``observed_at`` 은 **수집 시각**이다. 봉의 날짜가 아니다 — 그날의 봉을
    그날 자정에 알 수 있었을 리 없다.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        raw_date = str(row.get("date") or "").strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            continue
        normalized.append(
            {
                "entity_id": entity_id,
                "valid_from": _bar_timestamp(raw_date),
                "observed_at": observed_at,
                "source": SOURCE,
                "market": str(market),
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "volume": _to_float(row.get("jdiff_vol")),
                "value": None,
                "adj_factor": None,
            }
        )
    return normalized


def normalize_master(
    rows: list[dict[str, Any]],
    *,
    market: Market,
    trading_day: datetime,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """t8436OutBlock → universe 행.

    필드맵 port: LS_KR universe/stock_master.py:185-199
    (``shcode``, ``hname``, ``etfgubun``, ``spac_gubun``).

    상장폐지 종목을 지우지 않는다. 오늘 명단에 없다고 과거 명단에서 빼면
    생존편향이 그대로 들어온다.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("shcode") or "").strip()
        if not code:
            continue
        is_etf = str(row.get("etfgubun", "0")) != "0"
        is_spac = str(row.get("spac_gubun", "N")) == "Y"
        normalized.append(
            {
                "entity_id": f"{market}:{code}",
                "valid_from": trading_day,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": str(market),
                "name": str(row.get("hname") or code),
                "is_listed": True,
                # ETF·스팩은 상장돼 있어도 우리가 살 대상은 아니다.
                # 데이터 유니버스와 매매 유니버스는 다르다 (data-contract §6).
                "is_tradable": not (is_etf or is_spac),
                "delisted_on": None,
            }
        )
    return normalized


@dataclass
class MarketCollector:
    store: Store
    client: LSClient
    clock: Clock
    archive: RawArchive
    market: Market = Market.KR

    def collect_ohlcv(
        self,
        symbol: str,
        *,
        ingest_run_id: str,
        count: int = 100,
    ) -> int:
        """일봉 수집 → 원본 보존 → 정규화 → 적재. 단계마다 지연을 잰다."""
        entity_id = f"{self.market}:{symbol}"
        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=SOURCE, ingest_run_id=ingest_run_id
        )

        with latency.stage("fetch", entity_id):
            payload = self.client.request_tr(
                PATH_MARKET,
                "t8410",
                {
                    "t8410InBlock": {
                        "shcode": symbol.lstrip("A"),
                        "gubun": "2",
                        "qrycnt": count,
                        "sdate": "",
                        "edate": "99999999",
                        "cts_date": "",
                        "comp_yn": "N",
                        "sujung": SUJUNG_RAW,
                    }
                },
            )

        observed_at = self.clock.now()
        with latency.stage("archive", entity_id):
            self.archive.save(
                SOURCE,
                payload,
                observed_at=observed_at,
                ingest_run_id=ingest_run_id,
                label=f"t8410-{symbol}",
            )

        with latency.stage("normalize", entity_id):
            rows = normalize_ohlcv(
                payload.get("t8410OutBlock1") or [],
                entity_id=entity_id,
                market=self.market,
                observed_at=observed_at,
            )

        with latency.stage("append", entity_id):
            written = self.store.append("prices", rows, ingest_run_id=ingest_run_id)

        latency.flush()
        return written

    def collect_universe(self, *, ingest_run_id: str, gubun: str = "0") -> int:
        """그날의 상장종목 명단 스냅샷."""
        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=SOURCE, ingest_run_id=ingest_run_id
        )

        with latency.stage("fetch", "universe"):
            payload = self.client.request_tr(
                PATH_MARKET, "t8436", {"t8436InBlock": {"gubun": gubun}}
            )

        observed_at = self.clock.now()
        with latency.stage("archive", "universe"):
            self.archive.save(
                SOURCE,
                payload,
                observed_at=observed_at,
                ingest_run_id=ingest_run_id,
                label="t8436",
            )

        with latency.stage("normalize", "universe"):
            rows = normalize_master(
                payload.get("t8436OutBlock") or [],
                market=self.market,
                trading_day=observed_at.replace(hour=0, minute=0, second=0, microsecond=0),
                observed_at=observed_at,
            )

        with latency.stage("append", "universe"):
            written = self.store.append("universe", rows, ingest_run_id=ingest_run_id)

        latency.flush()
        return written
