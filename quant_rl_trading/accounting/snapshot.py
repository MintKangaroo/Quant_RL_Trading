"""일간 회계 스냅샷 — 한국시간 15:40 하루 한 번 (accounting.md §2).

두 시장의 하루가 어긋난다(국장 15:30 마감, 미장 새벽 05:00). 어긋난 것을
어긋난 채로 두면 "오늘 수익률" 이 무엇인지 말할 수 없으므로, **기준 시각을
하나로 못 박고 미장은 직전 종가를 쓴다.**

벤치마크도 정확히 같은 시각·같은 규칙으로 계산한다. 포트폴리오는 15:40
기준인데 벤치마크가 미장 실시간이면 그 차이가 통째로 가짜 초과수익이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.accounting import benchmark as benchmark_module
from quant_rl_trading.accounting import ledger
from quant_rl_trading.accounting.book import Book
from quant_rl_trading.accounting.nav import BASE_INDEX, Valuation, twr_return, value
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.store.prices import read_prices

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

SOURCE = "accounting"


@dataclass(frozen=True)
class Snapshot:
    """그날의 회계 결과. 저장 전 형태."""

    as_of: datetime
    valuation: Valuation
    inflow: float
    twr_return: float
    index_value: float
    drawdown: float

    def row(
        self,
        *,
        observed_at: datetime,
        benchmark: benchmark_module.Benchmark | None = None,
    ) -> dict:
        valuation = self.valuation
        benchmark = benchmark or benchmark_module.Benchmark(index_value=None, note=None)
        return {
            "entity_id": ledger.ACCOUNT,
            "valid_from": self.as_of,
            "observed_at": observed_at,
            "source": SOURCE,
            "nav": valuation.nav,
            "inflow": self.inflow,
            "twr_return": self.twr_return,
            "index_value": self.index_value,
            "drawdown": self.drawdown,
            "cash_krw": valuation.cash_krw,
            "cash_usd": valuation.cash_usd,
            "equity_kr": valuation.equity_kr,
            "equity_us": valuation.equity_us,
            "accrued_dividend": valuation.accrued_dividend,
            "payable": valuation.payable,
            "fx_rate": valuation.fx_rate,
            "tax_provision": valuation.tax_provision,
            "nav_after_tax": valuation.nav_after_tax,
            "benchmark_index": benchmark.index_value,
            "benchmark_note": benchmark.note,
        }


def last_prices(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, float]:
    """평가 가격. **미장은 직전 종가다** — 그게 15:40 에 알 수 있는 전부다.

    거래정지로 그날 봉이 없는 종목은 창 안의 마지막 종가를 쓴다. 0 으로 치면
    그 종목이 사라진 것과 같아져 NAV 가 조용히 떨어진다 (nav.value 참조).

    **0 이하를 먼저 걷어내고 그 다음에 마지막 행을 고른다. 순서가 전부다.**
    반대로 하면 창의 마지막 세션이 종가 0 일 때 그 0 행이 뽑히고, 뒤이은
    필터가 그것을 버리면서 **종목이 결과에서 통째로 사라진다.** 그러면
    `nav.value` 가 "가격이 없다" 로 예외를 던지고 스냅샷이 죽는다 — 위
    문장이 약속한 "창 안의 마지막 종가" 는 영영 쓰이지 않는다.

    실제로 그렇게 죽었다. KRX 는 휴장일에도 전 종목을 0 으로 채운 표를 주고,
    방어(`krx_source.ohlcv_on`)가 생기기 전에 2026-06-03(지방선거)·2026-07-17
    두 세션이 그렇게 적재됐다. 워크포워드 백테스트가 06-03 세션에서
    `KeyError: 'KR:138930: 가격이 없다'` 로 죽었고, 증상은 "며칠째 안 끝난다"
    였다. 그 두 날은 받아올 시세가 존재하지 않으므로(소스가 지금도 0 을 준다)
    **정정본으로 메울 수도 없다** — 읽는 쪽이 견뎌야 한다.

    ## 창이 두 개인 이유 — 상장폐지된 보유 종목

    보통은 30일 창이면 충분하다. 그런데 **상장폐지·장기 거래정지 종목을 들고
    있으면 마지막 종가가 그 창보다 오래됐다.** 그러면 여기서 빠지고
    `nav.value` 가 예외를 던져 스냅샷이 죽는다 — 거래정지를 견디겠다는 위
    약속이 정작 제일 오래 정지된 종목에서 깨진다.

    실제로 그렇게 죽었다(2026-08-17). `KR:005390` 은 2025-09-29 를 마지막으로
    시세가 끊겼는데, 백테스트가 그 종목을 들고 있었다. 정확히 30일 뒤인
    2025-10-30 세션에서 창 밖으로 밀려나며 6시간짜리 워크포워드가 끝났다.
    **창을 넓히기만 하면 경계가 옮겨갈 뿐**이라, 빠진 종목만 골라 훨씬 긴
    창으로 한 번 더 묻는다. 흔한 경로는 30일 그대로라 비용이 안 붙는다.

    **팔 수 없는 종목이라 계속 들고 있는 것이 맞다.** 상장폐지 종목은 시장이
    없어서 매도 주문이 나가지 않는다(후보 밖 보유 매도 경로가 있어도 체결될
    호가가 없다). 마지막 종가로 평가하는 것은 관례이고, 0 으로 치면 없는
    낙폭을 만들고 빼 버리면 NAV 가 조용히 준다. **정리매매 대금이 실제로
    얼마였는지는 별개 문제**이고, 그건 이 함수가 아니라 기업행위 쪽에서
    풀어야 한다.
    """
    if not entities:
        return {}
    # 거르는 규칙은 ``store/prices.py`` 한 벌뿐이다. 여기에 따로 두면 두 곳이
    # 서로 다르게 자라고, 어느 쪽이 맞는지 아무도 모르게 된다.
    prices = _latest_close(store, as_of=as_of, entities=entities, lookback=RECENT_LOOKBACK_DAYS)

    stale = [entity for entity in entities if entity not in prices]
    if stale:
        prices.update(
            _latest_close(
                store, as_of=as_of, entities=stale, lookback=STALE_LOOKBACK_DAYS
            )
        )
    return prices


#: 보통 경로의 조회 창(달력일). 거래정지 몇 주까지는 이 안에서 끝난다.
RECENT_LOOKBACK_DAYS = 30

#: 위 창에서 못 찾은 종목만 다시 묻는 창. 상장폐지 종목을 들고 있는 경우라
#: 넉넉히 잡는다 — 여기서도 없으면 그건 정말 모르는 것이고, `nav.value` 가
#: 예외를 던지는 것이 맞다.
STALE_LOOKBACK_DAYS = 800


def _latest_close(
    store: Store, *, as_of: datetime, entities: list[str], lookback: int
) -> dict[str, float]:
    """창 안의 마지막 종가. 0 이하는 ``read_prices`` 가 이미 걷어냈다."""
    frame = read_prices(store, as_of=as_of, entity=entities, lookback=lookback)
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "observed_at"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): float(row["close"])
        for row in latest.to_dict(orient="records")
    }


def take(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    book: Book | None = None,
) -> Snapshot:
    """그 시점의 스냅샷을 만든다. 저장하지는 않는다.

    ``book`` 을 주면 그것을 쓰고, 없으면 기록에서 재구성한다. 주입을 허용하는
    이유는 백테스트가 이미 장부를 손에 들고 있기 때문이다 — 같은 계산을 두 번
    하지 않게 한다. **계산식은 어느 쪽이든 하나다.**
    """
    rates = Rates.from_store(store, as_of=as_of)
    if book is None:
        book = ledger.build_book(store, as_of=as_of, rates=rates)

    rate = ledger.fx_rate(store, as_of=as_of)
    prices = last_prices(store, as_of=as_of, entities=sorted(book.positions))
    valuation = value(book, prices=prices, fx_rate=rate)

    previous = ledger.previous_snapshot(store, as_of=as_of)
    if previous is None:
        # 첫날. 수익률 0, 지수는 기준값. 없는 어제를 지어내지 않는다.
        return Snapshot(
            as_of=as_of,
            valuation=valuation,
            inflow=book.inflow,
            twr_return=0.0,
            index_value=BASE_INDEX,
            drawdown=0.0,
        )

    previous_nav = float(previous["nav"])
    # **입금은 "직전 스냅샷이 몰랐던 돈" 이다 — 발효 시각 창이 아니라.**
    # valid_from 창((직전, 이번])으로 세면 **뒤늦게 관측된 입금**이 새어 나간다: 달러
    # 입금을 05:00 에 발효시켰는데 05:20 스냅샷이 찍힌 뒤에야 관측됐다(2026-09-02
    # 미장 shadow 실측). 그 행은 05:20 창엔 관측이 안 됐고 16:00 창엔 발효가 밖이라
    # 어느 창에도 안 들어가, $370k 가 통째로 수익(+97%)이 될 참이었다. 원금을 두
    # 지식 상태(직전 스냅샷 시점·지금)로 각각 재서 빼면 "새로 알게 된 입금" 이
    # 정확히 나온다 — store.get(as_of=) 가 observed_at 을 거르기 때문이다.
    # NAV 도 같은 지식 상태의 장부에서 나오므로 둘이 어긋나지 않는다.
    previous_as_of = pd.Timestamp(previous["valid_from"]).to_pydatetime()
    inflow = ledger.principal(store, as_of=as_of) - ledger.principal(store, as_of=previous_as_of)
    daily = twr_return(nav=valuation.nav, previous_nav=previous_nav, inflow=inflow)
    index_value = float(previous["index_value"]) * (1.0 + daily)

    # 낙폭은 **누적지수 기준**이다. NAV 원금액으로 재면 입금이 낙폭을 지운다.
    peak = _peak_index(store, as_of=as_of, current=index_value)
    return Snapshot(
        as_of=as_of,
        valuation=valuation,
        inflow=inflow,
        twr_return=daily,
        index_value=index_value,
        drawdown=index_value / peak - 1.0 if peak > 0 else 0.0,
    )


def _peak_index(store: Store, *, as_of: datetime, current: float) -> float:
    """지금까지의 최고 누적지수. **오늘은 ``current`` 로만 센다.**

    창고에 있는 그날 행은 빼야 한다 — 정정 중이라면 그건 **낡은 오늘**이고,
    낡은 값이 더 높으면 그 차이가 그대로 가짜 낙폭이 된다. 오늘의 올바른
    값은 이미 ``current`` 로 들어온다 (ledger.previous_snapshot 참고).
    """
    frame = store.get(ledger.NAV_DAILY, as_of=as_of, entity=ledger.ACCOUNT)
    if frame.empty:
        return current
    frame = frame[frame["valid_from"] < pd.Timestamp(as_of)]
    if frame.empty:
        return current
    return max(float(frame["index_value"].max()), current)


#: 정정 여부를 가르는 컬럼. 여기 값이 하나라도 달라지면 그날 회계가 달라진
#: 것이다. ``observed_at`` 처럼 실행할 때마다 달라지는 값은 넣지 않는다 —
#: 넣으면 같은 입력을 두 번 돌려도 매번 새 revision 이 쌓인다.
_MATERIAL = (
    "nav", "inflow", "twr_return", "index_value", "drawdown",
    "cash_krw", "cash_usd", "equity_kr", "equity_us",
    "accrued_dividend", "payable", "fx_rate", "tax_provision", "nav_after_tax",
    "benchmark_index",
)

#: 부동소수 잡음과 진짜 정정을 가르는 문턱. NAV 는 천만원 단위라 상대오차로
#: 재고, 수익률·지수처럼 작은 값은 절대오차가 같이 걸려야 한다.
_TOLERANCE = 1e-9


def _differs(new: dict, old: dict) -> bool:
    for column in _MATERIAL:
        left, right = new.get(column), old.get(column)
        left_missing = left is None or (isinstance(left, float) and pd.isna(left))
        right_missing = right is None or pd.isna(right)
        if left_missing or right_missing:
            if left_missing != right_missing:
                return True
            continue
        if abs(float(left) - float(right)) > _TOLERANCE * max(1.0, abs(float(right))):
            return True
    return False


def write(
    store: Store,
    clock: Clock,
    *,
    snapshot: Snapshot,
    benchmark: benchmark_module.Benchmark | None = None,
) -> int:
    """스냅샷 적재. 하루에 한 행이고, **같은 값이면 두 번 쓰지 않는다.**

    벤치마크를 안 넘기면 **여기서 계산한다.** 호출부에 맡기면 한 곳만 빠져도
    그 경로의 ``benchmark_index`` 가 통째로 null 이 되고 — 실제로 그랬다 —
    화면은 초과수익을 그릴 근거를 잃는다. 여기서 계산하면 NAV 가 쓴 것과
    **같은 as_of, 같은 환율**이라는 것이 구조로 보장된다 (accounting.md §2).

    ## 왜 "이미 썼으면 끝" 이 아닌가

    처음에는 ``ingest_run_id`` 하나로 막았다. 그런데 그러면 **그날 회계를
    영영 고칠 수 없다.** shadow 가 정확히 그렇게 멈췄다: 2026-08-14 16:30 에
    세션이 돌아 NAV 를 썼는데 그때는 D+1 체결 단계가 안 돌아 체결이 0 이었고,
    같은 날 23:31 에 고친 실행기로 다시 돌려 체결 1건(``KR:000890`` 1,004주)이
    들어왔지만 ``nav-2026-08-14`` 매니페스트가 이미 있어서 NAV 는 초기 자본
    10,000,000 에 그대로 얼어붙었다. 창고에는 체결이 있는데 NAV 는 그 전
    세계를 가리키는 상태 — 검증기가 "손익 경로가 안 돌았다" 로 읽은 것이
    이것이다.

    그래서 **기록이 달라졌으면 revision 을 올린 정정본을 얹는다** (불변식 4).
    UPDATE 가 아니다 — 옛 행은 그대로 남고, 조회는 자연키마다 최신 revision
    하나를 고른다(reader.py). 값이 그대로면 아무것도 쓰지 않으므로 같은
    입력을 몇 번 돌려도 행이 늘지 않는다.
    """
    if benchmark is None:
        benchmark = benchmark_module.level(
            store, as_of=snapshot.as_of, fx_rate=snapshot.valuation.fx_rate
        )
    row = snapshot.row(observed_at=clock.now(), benchmark=benchmark)

    existing = store.get(
        ledger.NAV_DAILY, as_of=snapshot.as_of, entity=ledger.ACCOUNT, lookback=5
    )
    if not existing.empty:
        existing = existing[existing["valid_from"] == pd.Timestamp(snapshot.as_of)]
    if not existing.empty:
        latest = existing.sort_values("revision").iloc[-1].to_dict()
        if not _differs(row, latest):
            return 0
        row["revision"] = int(latest["revision"]) + 1

    revision = int(row.get("revision", 0))
    # 날짜만 쓰면 국장 스냅샷(16:00 KST = 07:00 UTC)과 미장 스냅샷(16:20 ET = 20:20
    # UTC)이 같은 UTC 날짜에 떨어져 뒤의 것이 "이미 적재됨" 으로 막힌다. 시각까지
    # 넣는다. 예전 행의 `nav-YYYY-MM-DD` 는 그대로 두며, 같은 값 재실행은 위의
    # valid_from 비교가 이미 막는다.
    run_id = f"nav-{snapshot.as_of.astimezone(UTC):%Y-%m-%dT%H%M}"
    if revision:
        run_id = f"{run_id}-r{revision}"
    if store.ingest_run_recorded(ledger.NAV_DAILY, run_id):
        # 같은 revision 을 같은 실행에서 두 번 쓰려는 것이다. 창고가 거부하기
        # 전에 여기서 멈춘다 — 정정 자체는 위에서 이미 판정했다.
        return 0
    return store.append(
        ledger.NAV_DAILY,
        [row],
        ingest_run_id=run_id,
        source=SOURCE,
    )
