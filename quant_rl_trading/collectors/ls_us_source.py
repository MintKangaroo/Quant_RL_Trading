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
from quant_rl_trading.collectors.market_hours import Market, trading_days
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

#: 미장 지수 **대용 ETF** — 티커 → 거래소 코드. 실측으로 확정한 값이다
#: (2026-08-19: SPY/DIA 는 81, QQQ/SOXX 는 82 에서만 행이 온다).
#:
#: ## 왜 지수가 아니라 ETF 인가
#:
#: LS 해외에는 지수가 없다. SPX·VIX 로 물으면 오류가 아니라 **빈 응답**이
#: 온다. 지수는 FRED 가 주는데 FRED 는 하루 늦다 — 실측 2026-08-19 08:55 KST
#: (뉴욕 마감 4시간 뒤) 에 SP500·NASDAQ·SOX·VIX 가 아직 08-17 에 멈춰 있었고,
#: 같은 시각 LS 는 08-18 을 줬다. 아침 브리핑이 늘 하루 낡던 이유가 이것이다.
#:
#: ## 그래서 **지수 자리에 넣지 않는다**
#:
#: 이 명단은 ``indices`` 가 아니라 ``prices`` 로 간다. ETF 는 지수가 아니다 —
#: 분배락에 가격이 떨어지고, 운용보수를 떼며(SPY 연 0.0945%), 시장가격이 NAV
#: 와 벌어진다. SPY 종가 767 은 S&P 500 의 7,745 가 아니다. 대용치에 원본의
#: 이름을 달아 주는 것이 이 저장소가 금지하는 바꿔치기다
#: (``config/quant_rl_trading.yaml`` benchmark 절).
#:
#: 거래소를 여기 적어 두는 이유: 모르면 나스닥으로 치고 0행이면 뉴욕으로 다시
#: 친다 — 네 종목이라 시간은 안 아깝지만, **0행이 "그 날 데이터가 없다" 와
#: 같은 모양**이라 거래소를 틀린 채 조용히 빈 수집이 될 수 있다.
INDEX_PROXY_ETFS: dict[str, str] = {
    "SPY": NYSE,
    "QQQ": NASDAQ,
    "DIA": NYSE,
    "SOXX": NASDAQ,
}

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

    def recent_bars(
        self, symbol: str, *, exchange: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """최근 구간 일봉. **``daily_bars`` 와 달리 한 번만 부른다.**

        구간이 500행(``MAX_ROWS_PER_CALL``) 안에 드는 것이 전제다. 증분 창은
        길어야 몇 주라 항상 든다.

        페이징을 없앤 것이 이 메서드의 존재 이유다. ``daily_bars`` 는 받은 것
        중 가장 오래된 날이 ``start`` 보다 뒤면 ``edate`` 를 밀어 한 번 더
        묻는데, 짧은 창에서는 그 두 번째 호출이 **주말·휴장 구간을 묻는 빈
        호출**로 끝난다. 종목당 1호출이 2호출이 되고, 6,648종목이면 111분이
        222분이 된다.

        실측 2026-08-18: 2026-08-10~08-18 요청 → 7행, 1호출.
        """
        payload = self._chart_call(
            symbol, exchange=exchange, start=start, end=end, cts_date="", cts_info=""
        )
        rows = payload.get(f"{TR_CHART}OutBlock1") or []
        kept: dict[date, dict[str, Any]] = {}
        for row in rows:
            day = _session(row.get("date"))
            # 구간 밖의 행이 섞여 오면 버린다. 이 호출이 책임지는 범위만 쓴다.
            if day is not None and start <= day <= end:
                kept[day] = row
        return [kept[day] for day in sorted(kept)]

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


# -----------------------------------------------------------------------------
# 일일 증분
# -----------------------------------------------------------------------------
#
# 위의 ``UsPriceBackfiller`` 는 5년을 채우는 물건이다. 종목당 4~8호출이고
# 6,648종목이면 반나절이 든다 — **하루 한 번 도는 크론에 넣을 수 없다.** 그래서
# 미장 일봉은 사람이 백필을 돌릴 때만 들어왔고, 그 사이 창고는 며칠씩 밀렸다
# (2026-08-18 실측: 국장 08-14, 미장 08-12 — 3세션 결손).
#
# ## 다중조회 TR 을 찾아봤고, 없었다 — 실측 2026-08-18
#
# 국장에는 ``t8407``(복수종목 현재가)이 있어 한 콜에 여러 종목이 온다. 미장에
# 대응물이 있는지 ``/overseas-stock`` 의 TR 을 실제로 쳐 봤다:
#
#     g3104  현재가        1종목  (keysymbol 단건)
#     g3106  현재가+호가   1종목
#     g3102  시간대별체결  1종목
#     g3202  N틱봉         1종목  ← 분봉이 아니다 (아래 정정)
#     g3203  N분봉         1종목
#     g3190  ─            InBlock 조합 5가지 전부 ``00009 해당 자료가 없습니다``
#     g3204  일봉          1종목
#
# **미장은 전부 종목 단건이다.** 짐작이 아니라 호출해서 확인했다 (이 저장소는
# ``t8410`` 을 잘못된 경로로 불러 "TR 이 없다" 는 결론에 도달한 적이 있다).
#
# ## 정정 2026-08-18 — ``g3202`` 는 분봉이 아니었다
#
# 위 표가 ``g3202`` 를 N분봉으로 적어 뒀는데 **틀렸다.** 개발자포털 카탈로그의
# ``trName`` 으로 확인했다:
#
#     g3202  "차트NTICK 조회"   ← N**틱**
#     g3203  "차트NMIN 조회"    ← N**분**
#
# 실호출로도 갈렸다 — ``g3203`` 에 ``ncnt=10`` 을 주면 ``loctime`` 이 실제로
# 10분 간격으로 온다(01:10:00 → 01:20:00 → …). AAPL 로 ncnt 1/5/15/60/240
# 전부 응답을 받았다.
#
# 틀린 주석이 더 위험한 이유가 있다. 없는 TR 을 부르면 에러가 나서 바로
# 알지만, **있는데 뜻이 다른 TR** 은 200 을 주고 봉을 준다 — 틱을 분봉으로
# 알고 창고에 넣으면 아무도 모른다.
#
# ## ``loctime`` 은 KST 가 아니다
#
# 거래소 현지시간이다. 같은 응답의 ``timediff`` 로 환산한다 —
# 거래소 UTC오프셋 = 9 + timediff. 실측: UTC 07:24 에 loctime="031900",
# timediff=-13 → 오프셋 -4(EDT) → UTC 07:19, 실제와 맞았다.
#
# **오프셋을 박아 두지 마라.** 미국은 서머타임이 있어서 1년에 두 번, 특정
# 날짜에만 한 시간씩 틀어진다 — 그런 결함은 몇 달 뒤에 발견되고 그때는 이미
# 데이터가 오염돼 있다.
#
# ## 그래서 무엇이 달라지는가 — 호출 수를 줄이는 게 아니라 **구간**을 줄인다
#
# 종목당 1호출은 못 피한다. 대신 **받는 구간**을 5년에서 최근 며칠로 줄이면
# ``daily_bars`` 의 페이징(edate 를 뒤로 미는 반복)이 통째로 사라진다. 실측:
# 최근 10일 구간이면 응답 7행 · **1호출**이다. 1.05초 간격에서 종목당 1.00초,
# 6,648종목에 약 111분 — 야간 크론이 감당할 수 있는 값이다.
#
# ## 왜 세션 × 배치로 기록하는가
#
# 백필은 배치 하나를 통째로 하나의 ``ingest_run_id`` 로 건다. 증분에 그 방식을
# 쓰면 **창이 움직이므로 매일 같은 세션을 다시 쓴다** — 3일 창이면 세션마다
# 행이 3벌 쌓인다. 반대로 세션만으로 걸면(국장 방식) 배치 하나가 실패했을 때
# 그 세션이 "적재됨" 으로 남아 나머지 배치가 영영 못 들어온다.
#
# 그래서 **(세션, 배치)** 로 건다. 겹치는 창을 매일 돌려도 이미 받은 칸은
# 매니페스트가 건너뛰고 — 호출 전에 판정하므로 API 도 안 친다 — 빠진 칸만
# 다시 받는다.
#
# ## 부분 수집은 이름을 달리 쓴다
#
# ``scope`` 가 그 표지다. 상위 N종목만 받거나 몇 종목만 시험 적재할 때 전체
# 실행과 같은 run_id 를 쓰면, 그 칸이 "적재됨" 으로 잠겨 **나머지 종목이 영영
# 안 들어온다.** 이 저장소에서 제일 비싸게 배운 실패 모양이다. 부분 실행은
# ``inc-prices-US-2026-08-17-top500-b000`` 처럼 별도 이름을 쓰고, 나중에 전체
# 실행이 같은 세션을 다시 채운다 — 겹치는 행은 읽기 경로가 자연키로 하나만
# 고르므로(reader 의 revision·observed_at 선택) 결과가 흔들리지 않는다.

#: 증분 배치 크기. 백필(900)보다 크게 잡는다 — 증분은 배치당 행이 세션 수
#: 만큼(3~5행)뿐이라 메모리가 문제되지 않고, 배치 수가 곧 파티션당 파일 수라
#: 작을수록 좋다 (``us_run_id`` 주석의 247만 파일 사고 참조).
INCREMENTAL_BATCH_SIZE = 1700


def incremental_run_id(market: Market, day: date, batch: int, *, scope: str = "") -> str:
    """(세션, 배치) 하나당 하나. ``scope`` 는 부분 수집의 표지다."""
    tag = f"-{scope}" if scope else ""
    return f"inc-{PRICES}-{market}-{day.isoformat()}{tag}-b{batch:03d}"


@dataclass(frozen=True)
class UsIncrementalResult:
    batch: int
    symbols: int
    rows: int
    #: 이번에 실제로 적재한 세션.
    sessions: tuple[date, ...] = ()
    #: 이미 매니페스트에 있어 호출조차 안 한 세션.
    already: tuple[date, ...] = ()
    #: 아직 공표 전이라 뺀 세션 수. **실패가 아니다** — 내일 실행이 받는다.
    deferred_sessions: int = 0
    #: LS 가 봉을 주지 않은 심볼. 상장폐지·거래정지가 대부분이라 실패가 아니다.
    missing: tuple[str, ...] = ()
    #: 실제로 나간 API 호출 수. 예상 소요를 재는 유일한 근거다.
    calls: int = 0

    @property
    def skipped(self) -> bool:
        return self.calls == 0

    @property
    def unit(self) -> str:
        return f"b{self.batch:03d}"


@dataclass
class UsIncrementalCollector:
    """최근 며칠치 미장 일봉만 받아 채운다. 크론이 부르는 경로다."""

    store: Any
    source: LsUsSource
    clock: Clock
    archive: Any
    policy: ObservedAtPolicy
    market: Market = Market.US
    exchanges: dict[str, str] = field(default_factory=dict)
    batch_size: int = INCREMENTAL_BATCH_SIZE
    #: 부분 수집 표지. 빈 문자열이면 전체 수집이다.
    scope: str = ""
    on_symbol: Any = None

    def batches(self, symbols: list[str]) -> list[tuple[int, list[str]]]:
        """(배치 번호, 종목들). 번호가 run_id 에 박히므로 **명단 순서에 고정**된다.

        호출부가 항상 같은 규칙으로 정렬해 넘겨야 어제의 배치 000 과 오늘의
        배치 000 이 같은 종목 묶음이다. 다르면 재개가 성립하지 않는다.
        """
        return [
            (index, symbols[offset : offset + self.batch_size])
            for index, offset in enumerate(range(0, len(symbols), self.batch_size))
        ]

    def published(self, day: date) -> bool:
        """그 세션을 지금 저장해도 되는가. 정책이 유일한 판정자다."""
        try:
            self.policy.for_session(day)
        except NotYetPublished:
            return False
        except Exception:
            # 거래일이 아닌 날. 달력이 진실이고 응답이 아니다.
            return False
        return True

    def pending_sessions(self, batch: int, sessions: list[date]) -> list[date]:
        """이 배치에서 아직 안 받은 세션. **호출 전에** 판정한다.

        여기서 걸러야 API 를 안 친다. 받아 놓고 버리면 111분이 매일 그대로
        든다 — 증분의 의미가 없어진다.
        """
        return [
            day
            for day in sessions
            if not self.store.ingest_run_recorded(
                PRICES, incremental_run_id(self.market, day, batch, scope=self.scope)
            )
        ]

    def _fetch(
        self, symbol: str, *, start: date, end: date
    ) -> tuple[str, list[dict[str, Any]], int]:
        """(거래소, 봉, 실제 호출 수). 거래소를 모르면 **차트로** 알아낸다.

        ``resolve_exchange`` 는 종목마스터(g3101)를 최대 2번 불러 거래소를
        확정하고, 그 뒤에 차트를 또 부른다 — 종목당 최대 3호출이다. 6,647
        종목이면 그 차이가 111분과 250분이다.

        여기서는 마스터를 부르지 않고 **차트를 바로 친다.** 거래소가 틀리면
        LS 는 오류가 아니라 **0행**을 준다(실측 2026-08-18: AAPL/exchcd=81 →
        0행, /82 → 5행; JPM 은 그 반대). 그러니 0행이면 다른 거래소로 한 번
        더 치면 된다 — 맞으면 1호출, 틀리면 2호출이고, 맞힌 값은 호출부가
        캐시에 남겨 다음날부터 항상 1호출이다.

        나스닥을 먼저 본다. 종목 수가 더 많아 평균 호출이 준다.
        """
        known = self.exchanges.get(symbol)
        order = [known, *(code for code in EXCHANGES if code != known)] if known else list(
            EXCHANGES
        )

        calls = 0
        for exchange in order:
            bars = self.source.recent_bars(
                symbol, exchange=exchange, start=start, end=end
            )
            calls += 1
            if bars:
                return exchange, bars, calls
        # 전부 0행이면 상장폐지·거래정지·LS 미취급이다. 사실이지 실패가 아니다.
        return order[0], [], calls

    def run_batch(
        self, batch: int, symbols: list[str], *, start: date, end: date
    ) -> UsIncrementalResult:
        # **아직 공표 안 된 세션은 후보에서 뺀다.** 넣어 두면 그 세션이
        # 영원히 "안 받은 칸" 으로 남아, 같은 날 두 번째 실행이 6,647종목을
        # 통째로 다시 부른다 — 받아 봐야 ``normalize_bars`` 가 전부 버린다.
        sessions = [
            day for day in trading_days(self.market, start, end) if self.published(day)
        ]
        wanted = set(self.pending_sessions(batch, sessions))
        already = tuple(day for day in sessions if day not in wanted)
        if not wanted:
            return UsIncrementalResult(batch, len(symbols), 0, already=already)

        by_session: dict[date, list[dict[str, Any]]] = {}
        payloads: dict[str, Any] = {}
        missing: list[str] = []
        deferred_total = 0
        calls = 0

        for symbol in symbols:
            try:
                exchange, bars, spent = self._fetch(symbol, start=start, end=end)
            except CollectorError as error:
                # 한 종목 실패로 배치를 통째로 버리지 않는다. 그 세션은
                # 매니페스트에 남지 않으니 내일 실행이 다시 받는다.
                missing.append(f"{symbol}({error})")
                continue
            calls += spent

            if not bars:
                missing.append(symbol)
                continue
            self.exchanges[symbol] = exchange

            payloads[symbol] = bars
            rows, deferred = normalize_bars(
                bars, symbol=symbol, market=self.market, observed_at_for=self.policy
            )
            deferred_total += len(deferred)
            for row in rows:
                day = row["valid_from"].date()
                if day in wanted:
                    by_session.setdefault(day, []).append(row)
            if self.on_symbol is not None:
                self.on_symbol(symbol, len(rows))

        if not by_session:
            # **빈 것을 완료로 기록하지 않는다.** 기록하면 나중에 데이터가
            # 생겨도 영영 건너뛴다.
            return UsIncrementalResult(
                batch,
                len(symbols),
                0,
                already=already,
                deferred_sessions=deferred_total,
                missing=tuple(missing),
                calls=calls,
            )

        self.archive.save(
            self.source.name,
            payloads,
            observed_at=self.clock.now(),
            ingest_run_id=incremental_run_id(self.market, end, batch, scope=self.scope),
            label=f"{SOURCE}-inc-b{batch:03d}",
        )

        written = 0
        stored: list[date] = []
        for day in sorted(by_session):
            run_id = incremental_run_id(self.market, day, batch, scope=self.scope)
            written += self.store.append(PRICES, by_session[day], ingest_run_id=run_id)
            stored.append(day)

        return UsIncrementalResult(
            batch,
            len(symbols),
            written,
            sessions=tuple(stored),
            already=already,
            deferred_sessions=deferred_total,
            missing=tuple(missing),
            calls=calls,
        )

    def run(
        self, symbols: list[str], *, start: date, end: date
    ) -> Iterator[UsIncrementalResult]:
        for batch, group in self.batches(symbols):
            yield self.run_batch(batch, group, start=start, end=end)
