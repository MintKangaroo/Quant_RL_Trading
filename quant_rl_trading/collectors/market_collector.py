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

from quant_rl_trading.collectors.latency import LatencyRecorder
from quant_rl_trading.collectors.ls_client import PATH_MARKET, LSClient
from quant_rl_trading.collectors.market_hours import (
    Market,
    is_trading_day,
    local_time,
    previous_trading_day,
)
from quant_rl_trading.collectors.publication import ObservedAtPolicy, resolve
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store

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


def session_of(market: Market, observed_at: datetime) -> datetime:
    """수집 시각이 속한 **거래일**. valid_from 으로 쓴다.

    관측시각의 UTC 날짜를 그대로 쓰면 안 된다. KST 09:00 이전에 수집하면
    UTC 날짜가 하루 전이라 명단이 통째로 하루 밀리고, 휴장일에 수집하면
    그날이 세션인 것처럼 기록된다.

    지역 시각으로 날짜를 뽑고, 그날이 거래일이 아니면 직전 거래일로 붙인다 —
    장 마감 후·주말에 받은 명단은 마지막으로 열린 세션의 명단이다.
    """
    local_date = local_time(market, observed_at).date()
    session = (
        local_date if is_trading_day(market, local_date)
        else previous_trading_day(market, local_date)
    )
    return datetime(session.year, session.month, session.day, tzinfo=UTC)


def normalize_ohlcv(
    rows: list[dict[str, Any]],
    *,
    entity_id: str,
    market: Market,
    observed_at: datetime | ObservedAtPolicy,
) -> list[dict[str, Any]]:
    """t8410OutBlock1 → prices 행.

    필드맵 port: LS_KR broker/ls_client.py:406-429
    (``date, open, high, low, close, jdiff_vol``).

    ``observed_at`` 은 **수집 시각**이다. 봉의 날짜가 아니다 — 그날의 봉을
    그날 자정에 알 수 있었을 리 없다.

    백필은 여기에 ``PublicationPolicy`` 를 넣어 봉마다 원 공표 시각을 찍는다.
    라이브는 이미 계산된 수집 시각 하나를 그대로 넘긴다. 코드는 같고 정책만
    다르다 (불변식 5).
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        raw_date = str(row.get("date") or "").strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            continue
        bar_ts = _bar_timestamp(raw_date)
        normalized.append(
            {
                "entity_id": entity_id,
                "valid_from": bar_ts,
                "observed_at": resolve(observed_at, bar_ts.date()),
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
    is_listed: bool = True,
    delisted_on: datetime | None = None,
) -> list[dict[str, Any]]:
    """t8436OutBlock → universe 행.

    필드맵 port: LS_KR universe/stock_master.py:185-199
    (``shcode``, ``hname``, ``etfgubun``, ``spac_gubun``).

    상장폐지 종목을 지우지 않는다. 오늘 명단에 없다고 과거 명단에서 빼면
    생존편향이 그대로 들어온다. 상폐는 행을 지우는 대신 ``is_listed=False`` 인
    새 행을 남기는 것으로 표현한다 (append-only, 불변식 4).
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
                "is_listed": is_listed,
                # ETF·스팩은 상장돼 있어도 우리가 살 대상은 아니다.
                # 데이터 유니버스와 매매 유니버스는 다르다 (data-contract §6).
                "is_tradable": is_listed and not (is_etf or is_spac),
                "delisted_on": delisted_on,
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
                trading_day=session_of(self.market, observed_at),
                observed_at=observed_at,
            )

        with latency.stage("append", "universe"):
            written = self.store.append("universe", rows, ingest_run_id=ingest_run_id)

        latency.flush()
        return written
