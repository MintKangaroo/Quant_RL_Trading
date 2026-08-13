"""미장 일봉 수집 — LS 해외주식 g3204.

port(collectors): Invest_USA_Stock_Project broker/ls_client.py 의 **TR 스펙**을
이식한다. 코드가 아니라 엔드포인트·InBlock 필드 구성이다.

## 왜 종목 축인가 (국장은 세션 축인데)

`backfill.py` 는 거래일 하나를 단위로 돈다. KRX 가 날짜 축으로 전종목을 한 번에
주기 때문이고, 그 덕에 그날 상장돼 있던 종목이 자동으로 다 잡힌다.

**LS 해외주식은 반대다.** g3204 는 심볼 하나의 기간 전체를 준다. 세션 축으로
돌면 600종목 × 1,500세션 = 90만 호출이고 초당 1건 제한에서는 끝나지 않는다.
종목 축이면 종목당 4호출(500행 상한) ≈ 2,400호출로 끝난다.

## 그 대가 — 생존편향

종목 축은 "오늘 존재하는 종목 목록"에서 출발한다. 그래서 **상장폐지된 종목이
통째로 빠진다.** 국장 백필이 세션 축을 고른 이유가 이것이었다.

미장에서는 이 편향을 피할 방법이 없다. LS 는 상폐 종목의 과거 시세를 아예
주지 않는다 — 실측했다 (2026-08-13):

    AAPL 2024-01  21행 ✅
    ATVI 2023-01   0행   (MS 인수 2023-10 상폐)
    TWTR 2022-01   0행   (2022-10 상폐)
    SIVB 2023-01   0행   (SVB 파산 2023-03)
    FRC  2023-01   0행   (2023-05 상폐)

유니버스를 EDGAR 로 완벽히 복원해도 가격이 없으므로 편향은 그대로다.
**그러므로 이 소스로 잰 미장 IC 는 부풀려진 값이다.** 6년이면 출발 유니버스의
20~30%가 사라지는데 사라진 쪽은 대체로 성적이 나빴던 종목이다. 살아남은
종목만 보고 재면 어떤 신호든 실제보다 잘 맞는 것처럼 보인다.

숨기지 말고 갖고 간다: 저장되는 모든 행의 ``source`` 가 ``ls_us`` 이므로
어느 데이터가 이 제약 아래 있는지 창고에서 바로 구분된다.

## 수정주가를 받지 않는다

``sujung="N"`` 으로 원주가를 받는다. 수정주가는 **미래의 분할이 과거 가격에
섞인 값**이라, 그걸 저장하면 백테스트가 아직 일어나지 않은 분할을 알게 된다
(backfill.py 의 ``adj_factor`` 주석과 같은 이유).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.ls_client import (
    MIN_INTERVAL_SEC_US,
    LSClient,
    LSCredentials,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.publication import NotYetPublished, ObservedAtPolicy
from quant_rl_trading.replay.clock import Clock

PRICES = "prices"
SOURCE = "ls_us"

PATH_CHART = "/overseas-stock/chart"
PATH_MARKET = "/overseas-stock/market-data"

TR_CHART = "g3204"
TR_MASTER = "g3101"

#: 거래소 코드. LS 해외주식 규약이다.
NYSE = "81"
NASDAQ = "82"
EXCHANGES = (NASDAQ, NYSE)

#: g3204 한 번에 받을 수 있는 최대 행수. 넘겨도 잘려서 온다.
MAX_ROWS_PER_CALL = 500

#: 미장 환경변수 접두사. 국장(``LS_``)과 **다른 appkey** 를 쓴다.
US_ENV_PREFIX = "LS_US_"


class UsSymbolUnavailable(CollectorError):
    """LS 가 그 심볼을 모른다. 상장폐지가 가장 흔한 원인이다."""


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _session(yyyymmdd: str) -> date | None:
    try:
        return datetime.strptime(str(yyyymmdd), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def _session_timestamp(day: date) -> datetime:
    """거래일 → valid_from. UTC 자정 고정 — 국장 백필과 같은 규칙이다."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@dataclass
class LsUsSource:
    """LS 해외주식 조회. 읽기 전용 TR 만 쓴다."""

    client: LSClient
    name: str = SOURCE

    @classmethod
    def from_env(
        cls, env: dict[str, str] | None = None, *, clock: Clock | None = None
    ) -> LsUsSource:
        source = env if env is not None else dict(os.environ)
        client = LSClient(
            credentials=LSCredentials.from_env(source, prefix=US_ENV_PREFIX),
            min_interval_sec=MIN_INTERVAL_SEC_US,
            **({"clock": clock} if clock is not None else {}),
        )
        return cls(client=client)

    def usable(self) -> bool:
        return self.client.credentials.usable()

    # -- 조회 -------------------------------------------------------------------

    def daily_bars(
        self, symbol: str, *, exchange: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """``start``~``end`` 일봉 전체.

        **``cts_date`` 를 쓰지 않는다.** g3204 는 요청 구간의 **최신 500행**을
        주면서 ``cts_date`` 를 처음부터 ``99999999`` 로 돌려준다. 그 키를 믿고
        연속조회하면 첫 응답에서 끝난 것으로 판단해 6년을 달라고 해도 최근
        500행만 얻는다 (실측: 2020-01~2026-08 요청 → 2024-08~2026-08 만 옴).

        그래서 **``edate`` 를 뒤로 밀며** 받는다. 받은 것 중 가장 오래된 날의
        하루 전을 다음 ``edate`` 로 삼는다. 진전이 없으면 멈춘다 — 그 검사가
        없으면 LS 가 같은 구간을 되돌려줄 때 영원히 돈다.
        """
        collected: dict[date, dict[str, Any]] = {}
        cursor = end

        while cursor >= start:
            payload = self._chart_call(
                symbol, exchange=exchange, start=start, end=cursor,
                cts_date="", cts_info="",
            )
            rows = payload.get(f"{TR_CHART}OutBlock1") or []
            if not rows:
                break

            days = []
            for row in rows:
                day = _session(row.get("date"))
                # 요청 구간 밖의 행이 섞여 오면 버린다. 넘겨받은 구간만이
                # 이 호출이 책임지는 범위다.
                if day is not None and start <= day <= end:
                    collected[day] = row
                    days.append(day)
            if not days:
                break

            oldest = min(days)
            if oldest <= start:
                break
            next_cursor = oldest - timedelta(days=1)
            if next_cursor >= cursor:
                break
            cursor = next_cursor

        return [collected[day] for day in sorted(collected)]

    def master(self, symbol: str, *, exchange: str) -> dict[str, Any]:
        """종목 단건 정보. 심볼이 살아 있는지 확인하는 용도로도 쓴다.

        **목록 조회가 아니다** — g3101 은 심볼을 알아야 부를 수 있다. 그래서
        미장 유니버스는 LS 밖에서 와야 한다.
        """
        payload = self.client.request_tr(
            PATH_MARKET,
            TR_MASTER,
            {
                f"{TR_MASTER}InBlock": {
                    "keysymbol": f"{exchange}{symbol}",
                    "exchcd": exchange,
                    "symbol": symbol,
                }
            },
        )
        block = payload.get(f"{TR_MASTER}OutBlock")
        if not block:
            raise UsSymbolUnavailable(f"{symbol}({exchange}): {payload.get('rsp_msg')}")
        return dict(block)

    def resolve_exchange(self, symbol: str) -> str | None:
        """심볼이 어느 거래소에 있는지. 없으면 None.

        나스닥을 먼저 본다 — 후보 종목 수가 더 많아 평균 호출이 줄어든다.
        """
        for exchange in EXCHANGES:
            try:
                self.master(symbol, exchange=exchange)
            except (UsSymbolUnavailable, CollectorError):
                continue
            return exchange
        return None

    def _chart_call(
        self,
        symbol: str,
        *,
        exchange: str,
        start: date,
        end: date,
        cts_date: str,
        cts_info: str,
    ) -> dict[str, Any]:
        return self.client.request_tr(
            PATH_CHART,
            TR_CHART,
            {
                f"{TR_CHART}InBlock": {
                    # 원주가. 이유는 모듈 docstring 참조.
                    "sujung": "N",
                    "delaygb": "R",
                    "comp_yn": "N",
                    "keysymbol": f"{exchange}{symbol}",
                    "exchcd": exchange,
                    "symbol": symbol,
                    # 2 = 일봉.
                    "gubun": "2",
                    "qrycnt": MAX_ROWS_PER_CALL,
                    "sdate": start.strftime("%Y%m%d"),
                    "edate": end.strftime("%Y%m%d"),
                    "cts_date": cts_date,
                    "cts_info": cts_info,
                }
            },
        )


# -----------------------------------------------------------------------------
# 정규화
# -----------------------------------------------------------------------------


def normalize_bars(
    bars: list[dict[str, Any]],
    *,
    symbol: str,
    market: Market,
    observed_at_for: ObservedAtPolicy,
    source: str = SOURCE,
) -> tuple[list[dict[str, Any]], list[date]]:
    """일봉 → prices 행. (행, 아직 공표 전이라 뺀 세션) 을 돌려준다.

    ``observed_at`` 을 **행마다 그 세션 기준으로** 찍는다. 한 번에 6년을
    받아왔다고 오늘 시각을 전부에 찍으면, 2021년 종가를 2026년에야 알 수
    있었다는 뜻이 되어 그 구간이 백테스트에서 통째로 안 보이게 된다.
    """
    rows: list[dict[str, Any]] = []
    deferred: list[date] = []

    for bar in bars:
        day = _session(bar.get("date"))
        if day is None:
            continue
        try:
            observed_at = observed_at_for.for_session(day)
        except NotYetPublished:
            # 아직 장이 안 끝난 세션. 저장하면 미래를 보는 것이 된다.
            deferred.append(day)
            continue
        except Exception:
            # 거래일이 아닌 날짜를 LS 가 줄 때가 있다. 그 행은 버린다 —
            # 달력이 진실이고 응답이 아니다.
            continue

        rows.append(
            {
                "entity_id": f"{market}:{symbol}",
                "valid_from": _session_timestamp(day),
                "observed_at": observed_at,
                "source": source,
                "market": str(market),
                "open": _number(bar.get("open")),
                "high": _number(bar.get("high")),
                "low": _number(bar.get("low")),
                "close": _number(bar.get("close")),
                "volume": _number(bar.get("volume")),
                # LS 는 거래대금을 amount 로 준다.
                "value": _number(bar.get("amount")),
                # 원주가를 저장하므로 조정계수는 비운다 (국장과 같은 규칙).
                "adj_factor": None,
            }
        )

    return rows, deferred


def us_run_id(market: Market, batch: int) -> str:
    """결정론적 run id. 작업 단위는 종목이 아니라 **배치**다.

    국장은 ``bf-prices-KR-20240304`` 처럼 날짜로 건다. 미장은 종목 축이라
    날짜로 걸 수가 없다 — 한 번의 호출이 여러 세션을 한꺼번에 채운다.

    **왜 종목이 아니라 배치인가.** 창고는 ``observed_date`` 로 파티션한다.
    종목 하나를 append 하면 그 종목의 5년치가 1,250개 파티션으로 흩어지면서
    **파티션마다 파일이 하나씩** 생긴다. 종목 축으로 2,551개를 넣었더니
    파티션당 2,164개, 전체 247만 개가 됐고 DuckDB 가 질의마다 그 푸터를 전부
    열어야 해서 ``prices`` 를 만지는 모든 작업이 죽었다.

    배치로 묶으면 파티션당 파일이 배치 수만큼만 생긴다. DART 수집기가 분기×
    배치로 도는 것과 같은 이유다.
    """
    return f"bf-{PRICES}-{market}-b{batch:03d}"


# -----------------------------------------------------------------------------
# 백필
# -----------------------------------------------------------------------------


#: 한 배치에 묶을 종목 수. 파티션당 파일 수 = 배치 수이므로 작을수록 좋지만,
#: 배치가 크면 append 전까지 들고 있는 행이 는다. 900종목 × 1,253행 ≈ 113만 행
#: (약 90MB) 이라 메모리 여유 안에 든다.
BATCH_SIZE = 900


@dataclass(frozen=True)
class UsPriceResult:
    batch: int
    symbols: int
    rows: int
    skipped: bool
    error: str | None = None
    #: 아직 공표되지 않아 뺀 세션 수. **실패가 아니다.**
    deferred_sessions: int = 0
    #: LS 가 모르는 심볼(대개 상장폐지·미취급). 실패가 아니라 사실이다.
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def counts(self) -> dict[str, int]:
        return {PRICES: self.rows}

    @property
    def unit(self) -> str:
        return f"b{self.batch:03d}"


@dataclass
class UsPriceBackfiller:
    """종목 하나씩 채운다. 재개는 창고 매니페스트가 판단한다."""

    store: Any
    source: LsUsSource
    clock: Clock
    archive: Any
    policy: ObservedAtPolicy
    market: Market = Market.US
    #: 심볼 → 거래소 코드. 모르면 조회해서 채운다.
    exchanges: dict[str, str] = field(default_factory=dict)

    #: 한 배치에 묶을 종목 수.
    batch_size: int = BATCH_SIZE
    #: 종목 하나를 마칠 때마다 부른다. 배치가 크면 진행이 안 보이기 때문이다.
    on_symbol: Any = None

    def batches(self, symbols: list[str]) -> list[tuple[int, list[str]]]:
        """(배치 번호, 종목들). 번호는 **순서에 고정**돼야 재개가 성립한다.

        명단이 바뀌면 번호와 종목의 대응도 바뀐다. 그래서 명단은 항상 같은
        규칙으로 정렬해 넘긴다 (``us_universe.fetch_listings`` 가 티커 순).
        """
        return [
            (index, symbols[offset : offset + self.batch_size])
            for index, offset in enumerate(range(0, len(symbols), self.batch_size))
        ]

    def pending(self, symbols: list[str]) -> list[tuple[int, list[str]]]:
        return [
            item
            for item in self.batches(symbols)
            if not self.store.ingest_run_recorded(PRICES, us_run_id(self.market, item[0]))
        ]

    def run_batch(self, batch: int, symbols: list[str], *, start: date, end: date) -> UsPriceResult:
        run_id = us_run_id(self.market, batch)
        if self.store.ingest_run_recorded(PRICES, run_id):
            return UsPriceResult(batch, len(symbols), 0, skipped=True)

        rows: list[dict[str, Any]] = []
        payloads: dict[str, Any] = {}
        missing: list[str] = []
        deferred_total = 0

        for symbol in symbols:
            try:
                exchange = self.exchanges.get(symbol) or self.source.resolve_exchange(symbol)
                if exchange is None:
                    # 상장폐지거나 LS 가 취급하지 않는 종목.
                    missing.append(symbol)
                    continue
                self.exchanges[symbol] = exchange
                bars = self.source.daily_bars(symbol, exchange=exchange, start=start, end=end)
            except CollectorError as error:
                # 배치 하나가 통째로 날아가면 900종목을 다시 받아야 한다.
                # 한 종목 실패는 그 종목만 빼고 간다.
                missing.append(f"{symbol}({error})")
                continue

            if not bars:
                missing.append(symbol)
                continue

            payloads[symbol] = bars
            symbol_rows, deferred = normalize_bars(
                bars, symbol=symbol, market=self.market, observed_at_for=self.policy
            )
            rows.extend(symbol_rows)
            deferred_total += len(deferred)
            if self.on_symbol is not None:
                self.on_symbol(symbol, len(symbol_rows))

        if not rows:
            # 빈 것을 완료로 기록하면 나중에 데이터가 생겨도 영영 건너뛴다.
            return UsPriceResult(
                batch, len(symbols), 0, skipped=False, missing=tuple(missing)
            )

        self.archive.save(
            self.source.name,
            payloads,
            observed_at=self.clock.now(),
            ingest_run_id=run_id,
            label=f"{SOURCE}-b{batch:03d}",
        )
        written = self.store.append(PRICES, rows, ingest_run_id=run_id)
        return UsPriceResult(
            batch,
            len(symbols),
            written,
            skipped=False,
            deferred_sessions=deferred_total,
            missing=tuple(missing),
        )

    def run(
        self, symbols: list[str], *, start: date, end: date
    ) -> Iterator[UsPriceResult]:
        for batch, group in self.pending(symbols):
            yield self.run_batch(batch, group, start=start, end=end)
