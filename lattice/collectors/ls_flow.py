"""투자자별 수급 — LS ``t1717``.

KRX 정식 경로(Open API)에는 수급이 없고, data.krx.co.kr 스크래핑은 약관 위반이라
막혔다. 남은 정식 경로는 **우리가 사용권을 가진 증권사 API** 다.

실측으로 확인한 것 (2026-08-11):

- 경로는 ``/stock/frgr-itt`` 다. ``/stock/market-data`` 가 아니다
- 한 콜에 **250행**(약 1년)까지. ``tr_cont`` 연속조회는 제공되지 않으므로
  날짜 창을 옮겨 가며 페이징한다
- **종목 축이다.** t1702·t1716·t1717 전부 ``shcode`` 가 필수라 날짜 하나로
  전종목을 받는 경로가 없다. 그래서 이 백필만 유일하게 종목 단위로 돈다

수량(``_vol``)과 단가(``_dan``)를 함께 주므로 순매수 **금액**을 만들 수 있다.
금액이 필요한 이유는 시총 대비 정규화 때문이다 — 순매수 100만주는 대형주에선
무의미하고 소형주에선 폭등 신호다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from lattice.collectors.errors import LSAPIError
from lattice.collectors.krx_source import KRXUnavailable
from lattice.collectors.ls_client import LSClient

#: 수급 조회 경로. 문서에서 확인하고 실호출로 검증했다.
PATH_FLOW = "/stock/frgr-itt"

TR_FLOW = "t1717"

#: 한 콜의 최대 응답 행수. 실측값이다 — 5년을 요청해도 250행만 온다.
MAX_ROWS_PER_CALL = 250

SOURCE = "ls_api"

#: 응답 필드 → 투자자 이름. 기관 세부를 함께 받는 이유는 연기금과 투신의
#: 방향이 자주 엇갈리기 때문이다. "기관 순매수" 하나로 뭉치면 그게 사라진다.
INVESTOR_FIELDS = {
    "tjj0016": "외인계",
    "tjj0018": "기관",
    "tjj0008": "개인",
    "tjj0006": "기금",
    "tjj0003": "투신",
    "tjj0001": "증권",
    "tjj0002": "보험",
    "tjj0000": "사모펀드",
    "tjj0017": "기타계",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def date_windows(start: date, end: date, *, sessions_per_call: int) -> Iterator[tuple[date, date]]:
    """조회 창을 오래된 쪽부터 낸다.

    응답이 250행에서 잘리므로 달력일로 넉넉히 잡되 창을 겹치지 않게 민다.
    거래일은 달력일의 약 68% 라 여유를 두고 자른다.
    """
    span = timedelta(days=int(sessions_per_call * 1.4))
    cursor = start
    while cursor <= end:
        stop = min(cursor + span, end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def normalize_flow_rows(
    rows: list[dict[str, Any]],
    *,
    entity_id: str,
    market: str,
    observed_at_for: Any,
) -> list[dict[str, Any]]:
    """t1717OutBlock → flows 행.

    투자자 하나가 행 하나다. 응답은 한 줄에 모든 주체를 담고 있지만, 그대로
    넓게 저장하면 주체가 늘 때마다 스키마를 고쳐야 하고 "이 주체는 언제부터
    존재했나" 가 데이터에서 사라진다.

    ``observed_at_for`` 는 거래일 → 관측시각 함수다. 봉과 마찬가지로 그날
    자정이 아니라 **마감 후 공표 시각**이어야 한다.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        raw_date = str(row.get("date") or "").strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            continue
        session = datetime.strptime(raw_date, "%Y%m%d").date()
        try:
            observed_at = observed_at_for(session)
        except Exception:
            # 아직 공표되지 않았거나 거래일이 아니다. 지어내지 않고 버린다.
            continue

        valid_from = datetime(session.year, session.month, session.day, tzinfo=UTC)
        for code, investor in INVESTOR_FIELDS.items():
            volume = _number(row.get(f"{code}_vol"))
            if volume is None:
                continue
            price = _number(row.get(f"{code}_dan"))
            normalized.append(
                {
                    "entity_id": entity_id,
                    "valid_from": valid_from,
                    "observed_at": observed_at,
                    "source": SOURCE,
                    "market": market,
                    "investor": investor,
                    # 단가가 없으면 금액도 없다. 0 으로 채우지 않는다.
                    "net_value": None if price is None else volume * price,
                    "net_volume": volume,
                    "is_final": True,
                }
            )
    return normalized


FLOWS = "flows"


def flow_run_id(market: str, symbol: str) -> str:
    """재개 단위는 **종목**이다.

    종목 하나가 5콜(약 15초)이라, 창 단위로 쪼개면 매니페스트가 15,000개가 되고
    이득은 15초뿐이다. 종목 단위로 묶으면 중간에 죽어도 그 종목만 다시 받는다.
    """
    return f"bf-{FLOWS}-{market}-{symbol}"


@dataclass(frozen=True)
class FlowResult:
    symbol: str
    rows: int
    skipped: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def counts(self) -> dict[str, int]:
        return {FLOWS: self.rows}

    @property
    def unit(self) -> str:
        """종목 축이라 날짜가 아니라 종목코드가 단위다."""
        return self.symbol


@dataclass
class LSFlowBackfiller:
    """종목 축 백필. 레포에서 유일하게 날짜가 아니라 종목으로 도는 수집이다."""

    store: Any
    source: LSFlowSource
    clock: Any
    archive: Any
    observed_at_for: Any
    market: str = "KR"

    def pending(self, symbols: list[str]) -> list[str]:
        return [
            symbol
            for symbol in symbols
            if not self.store.ingest_run_recorded(FLOWS, flow_run_id(self.market, symbol))
        ]

    def run_symbol(self, symbol: str, start: date, end: date) -> FlowResult:
        run_id = flow_run_id(self.market, symbol)
        if self.store.ingest_run_recorded(FLOWS, run_id):
            return FlowResult(symbol=symbol, rows=0, skipped=True)

        entity_id = f"{self.market}:{symbol}"
        collected: list[dict[str, Any]] = []
        try:
            for window_start, window_end in date_windows(
                start, end, sessions_per_call=MAX_ROWS_PER_CALL
            ):
                raw = self.source.fetch(symbol, window_start, window_end)
                if not raw:
                    continue
                self.archive.save(
                    self.source.name,
                    raw,
                    observed_at=self.clock.now(),
                    ingest_run_id=run_id,
                    label=f"t1717-{symbol}-{window_start:%Y%m%d}",
                )
                collected.extend(raw)
        except KRXUnavailable as error:
            return FlowResult(symbol=symbol, rows=0, skipped=False, error=str(error))

        rows = normalize_flow_rows(
            collected,
            entity_id=entity_id,
            market=self.market,
            observed_at_for=self.observed_at_for,
        )
        if not rows:
            # 상장 전이거나 수급이 없는 종목. 빈 것을 성공으로 기록하지 않는다 —
            # 매니페스트가 남으면 나중에 데이터가 생겨도 영영 건너뛴다.
            return FlowResult(symbol=symbol, rows=0, skipped=False)
        return FlowResult(
            symbol=symbol,
            rows=self.store.append(FLOWS, rows, ingest_run_id=run_id),
            skipped=False,
        )


@dataclass
class LSFlowSource:
    """t1717 한 종목·한 창을 읽는다."""

    client: LSClient
    exchange: str = "K"  # K: KRX, N: NXT, U: 통합
    name: str = SOURCE
    _calls: int = field(default=0, repr=False)

    def fetch(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        body = {
            "t1717InBlock": {
                "shcode": symbol,
                # 0 = 일간 순매수. 1(기간누적)을 쓰면 일별 신호가 사라진다.
                "gubun": "0",
                "fromdt": start.strftime("%Y%m%d"),
                "todt": end.strftime("%Y%m%d"),
                "exchgubun": self.exchange,
            }
        }
        try:
            payload = self.client.request_tr(PATH_FLOW, TR_FLOW, body)
        except LSAPIError as error:
            raise KRXUnavailable(f"LS {TR_FLOW} {symbol} {start}~{end}: {error}") from error
        self._calls += 1
        rows = payload.get(f"{TR_FLOW}OutBlock") or []
        return [dict(row) for row in rows]
