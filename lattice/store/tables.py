"""테이블 레지스트리.

등록되지 않은 이름으로는 저장도 조회도 되지 않는다. 오타 하나로 새 테이블이
조용히 생기면, 그 테이블은 아무도 검증하지 않는 데이터가 된다.
"""

from __future__ import annotations

import pyarrow as pa

from lattice.store.errors import UnknownTable
from lattice.store.schema import TableSpec

CONFIG_TABLE = "config"

_SPECS: dict[str, TableSpec] = {
    "prices": TableSpec(
        name="prices",
        columns={
            "market": pa.string(),
            "open": pa.float64(),
            "high": pa.float64(),
            "low": pa.float64(),
            "close": pa.float64(),
            "volume": pa.float64(),
            "value": pa.float64(),
            # 수정주가는 저장하지 않는다. 원주가 + 조정계수로 시점별 재계산한다.
            # 수정주가에는 미래의 분할·증자가 이미 반영돼 있기 때문이다.
            "adj_factor": pa.float64(),
        },
        doc="일봉. 원주가 기준.",
    ),
    "flows": TableSpec(
        name="flows",
        columns={
            "market": pa.string(),
            "investor": pa.string(),
            "net_value": pa.float64(),
            "net_volume": pa.float64(),
            # 장중 잠정치와 마감 후 확정치는 별도 행으로 들어온다.
            "is_final": pa.bool_(),
        },
        natural_key=("entity_id", "valid_from", "investor"),
        # 그날의 순매수를 그날보다 먼저 관측할 수는 없다. 늦게 도착하는 쪽
        # (백필·정정본)은 하한 위에 있으므로 안 잘린다. 3일은 KST/UTC 경계와
        # 연휴용 여유다. **이 선언이 없으면 하한 프루닝이 통째로 꺼져서**
        # 45일을 달라고 해도 1,200여 개 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "투자자별 순매수. 주체가 자연키에 들어간다. 그날 그 주체가 손대지 "
            "않은 종목은 행 자체가 없다 — 없는 것을 0으로 채우지 않는다. "
            "'순매수 0' 과 '거래 없음' 은 다른 사실이다."
        ),
    ),
    "shorting": TableSpec(
        name="shorting",
        columns={
            "market": pa.string(),
            "board": pa.string(),
            "short_volume": pa.float64(),
            "total_volume": pa.float64(),
            "short_ratio": pa.float64(),
        },
        doc=(
            "공매도. **T+1~2 에 공표된다** — observed_at 이 세션 당일이면 "
            "flow_kr 이 통째로 미래를 본다. backfill.shorting_lag_days 로 "
            "거래일 단위로 늦춰 찍는다."
        ),
    ),
    "indices": TableSpec(
        name="indices",
        columns={
            "market": pa.string(),
            "board": pa.string(),
            "open": pa.float64(),
            "high": pa.float64(),
            "low": pa.float64(),
            "close": pa.float64(),
            "volume": pa.float64(),
            "value": pa.float64(),
        },
        doc=(
            "지수 일봉. regime Analyst 의 입력. prices 와 섞지 않는다 — "
            "지수가 종목 유니버스에 끼면 커버리지 통계와 횡단면 z 가 오염된다."
        ),
    ),
    "fundamentals": TableSpec(
        name="fundamentals",
        columns={
            "market": pa.string(),
            "metric": pa.string(),
            "value": pa.float64(),
            "fiscal_period": pa.string(),
            "report_type": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "metric"),
        doc="재무. 회계기간 종료일이 아니라 공시일 기준.",
    ),
    "fx": TableSpec(
        name="fx",
        columns={"rate": pa.float64()},
        doc="환율. entity_id 예: FX:USDKRW",
    ),
    "universe": TableSpec(
        name="universe",
        columns={
            "market": pa.string(),
            "name": pa.string(),
            "is_listed": pa.bool_(),
            "is_tradable": pa.bool_(),
            "delisted_on": pa.timestamp("us", tz="UTC"),
        },
        doc="매 거래일 상장종목 명단 스냅샷. 상폐 종목을 지우지 않는다.",
    ),
    "documents": TableSpec(
        name="documents",
        columns={
            "doc_id": pa.string(),
            "doc_type": pa.string(),
            "title": pa.string(),
            "filer": pa.string(),
            "url": pa.string(),
            "raw_path": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "doc_id"),
        doc=(
            "공시·뉴스. valid_from 은 공시 접수일, observed_at 은 우리가 받아온 시각. "
            "기사 발행시각은 사후 수정되므로 신뢰하지 않는다 (data-contract §4)."
        ),
    ),
    "ingest_latency": TableSpec(
        name="ingest_latency",
        columns={
            "stage": pa.string(),
            "elapsed_ms": pa.float64(),
            "ok": pa.bool_(),
            "detail": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "stage"),
        doc=(
            "파이프라인 단계별 실측 지연. entity_id = 수집 소스. "
            "백테스트 지연은 이 실측의 p90 을 쓴다 — 가정한 지연과 실제 지연의 "
            "차이가 백테스트를 거짓말로 만드는 대표적 원인이다 (data-contract §5)."
        ),
    ),
    "events": TableSpec(
        name="events",
        columns={
            "seq": pa.int64(),
            "stage": pa.string(),
            "actor": pa.string(),
            "payload_hash": pa.string(),
            "payload": pa.string(),
        },
        natural_key=("entity_id", "seq"),
        doc=(
            "이벤트 로그. entity_id = run_id, valid_from = ts_sim, "
            "observed_at = ts_wall. 같은 뜻의 필드를 두 벌 들지 않는다."
        ),
    ),
    "macro_releases": TableSpec(
        name="macro_releases",
        columns={
            "market": pa.string(),
            "indicator": pa.string(),
            "release_name": pa.string(),
            # 발표가 일어나는(또는 일어날) 시각. **valid_from 과 다르다.**
            "scheduled_at": pa.timestamp("us", tz="UTC"),
            "actual": pa.float64(),
            "previous": pa.float64(),
            "unit": pa.string(),
            # scheduled | released
            "status": pa.string(),
        },
        # 같은 발표는 한 행이다. 일정이 먼저 들어오고(actual 없음), 발표 후
        # 실측값이 같은 자연키로 덮는다 — append-only 규칙대로 새 행이 쌓이고
        # 게이트가 최신 것을 고른다.
        natural_key=("entity_id", "scheduled_at"),
        observation_lag_days=3,
        doc=(
            "CPI·PPI 등 거시지표 발표 일정과 실측값. **valid_from 은 우리가 그 "
            "사실을 안 시각이고, 발표 시각은 scheduled_at 이다.** 미래 일정을 "
            "valid_from 에 넣으면 게이트의 lookback 창이 미래를 못 보고, 그러면 "
            "'다가오는 일정'을 영영 못 찾는다."
        ),
    ),
    "market_stats": TableSpec(
        name="market_stats",
        columns={
            "market": pa.string(),
            "metric": pa.string(),
            "value": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "metric"),
        doc=(
            "일별 시장 관측 — 상장주식수·시가총액. **fundamentals 와 나눈다.** "
            "분기에 4번 바뀌는 공시와 매일 바뀌는 관측을 한 테이블에 두면, "
            "재무를 읽을 때마다 일별 데이터가 딸려 온다. 실제로 그렇게 만들었다가 "
            "fundamentals 가 460만 행(2.2GB)이 되어 Analyst 가 OOM 으로 죽었다."
        ),
    ),
    "signals": TableSpec(
        name="signals",
        columns={
            "analyst": pa.string(),
            "analyst_version": pa.string(),
            "score": pa.float64(),
            "confidence": pa.float64(),
            "horizon_days": pa.int32(),
            "features_hash": pa.string(),
            "evidence_json": pa.string(),
            "latency_ms": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "analyst", "analyst_version"),
        doc=(
            "Analyst 점수. valid_from = as_of(신호가 유효해진 시점), "
            "observed_at = 계산해서 알게 된 시점. analyst_version 이 자연키에 "
            "들어가는 이유는 나쁜 모델의 성적이 좋은 모델에 섞이면 안 되기 "
            "때문이다 — 버전별로 IC 를 따로 잰다."
        ),
    ),
    "verdicts": TableSpec(
        name="verdicts",
        columns={
            "analyst": pa.string(),
            "analyst_version": pa.string(),
            "decision": pa.string(),
            "severity": pa.float64(),
            "category": pa.string(),
            "reason": pa.string(),
            "expires_at": pa.timestamp("us", tz="UTC"),
        },
        natural_key=("entity_id", "valid_from", "analyst", "analyst_version"),
        doc=(
            "News·SNS 판정. 매수 금지만 가능하고 매도 권한은 없다. "
            "expires_at 없는 차단은 스키마가 거부한다 — 영구 차단은 존재할 수 "
            "없다. 차단된 종목의 이후 수익률로 성적표를 만든다."
        ),
    ),
    "analyst_weights": TableSpec(
        name="analyst_weights",
        columns={
            "analyst_version": pa.string(),
            "weight": pa.float64(),
            "ic": pa.float64(),
            "ic_threshold": pa.float64(),
            "sample_days": pa.int32(),
            "passed": pa.bool_(),
            "market": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "analyst_version"),
        doc=(
            "IC 검증 결과와 그에 따른 가중치. entity_id = analyst 이름. "
            "가중치를 손으로 켜고 끄면 언젠가 검증 안 된 것이 켜져 있다. "
            "통과 여부는 측정 결과에서만 나온다 — 통과 못 하면 weight 0."
        ),
    ),
    # -- 회계 (M3) ------------------------------------------------------------
    #
    # NAV 산출은 ``lattice/accounting/`` 한 곳에서만 한다 (accounting.md §8).
    # 이 테이블들은 그 모듈의 입력과 출력이다.
    "trades": TableSpec(
        name="trades",
        columns={
            "market": pa.string(),
            "side": pa.string(),
            "quantity": pa.float64(),
            "price": pa.float64(),
            "currency": pa.string(),
            "fee": pa.float64(),
            "tax": pa.float64(),
            # 체결 하나가 주문 하나가 아니다. 분할 집행이면 여러 행이 같은
            # 주문에서 나온다 — 되돌아가 볼 수 있게 주문 식별자를 남긴다.
            "order_id": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "order_id"),
        doc=(
            "체결. **발생주의(trade-date)** — 결제일이 아니라 체결 시점에 "
            "손익·수수료를 인식한다 (accounting.md §1). 수수료와 세금은 "
            "체결가에 녹이지 않고 따로 든다. 녹이면 나중에 비용만 따로 볼 수 없다."
        ),
    ),
    "capital_flows": TableSpec(
        name="capital_flows",
        columns={
            "currency": pa.string(),
            "amount": pa.float64(),
            "kind": pa.string(),
        },
        doc=(
            "입출금. **TWR 계산의 핵심 입력이다** — 이걸 모르면 입금일에 "
            "수익이 난 것으로 오인한다 (accounting.md §6). entity_id 는 계좌."
        ),
    ),
    "dividends": TableSpec(
        name="dividends",
        columns={
            "market": pa.string(),
            "currency": pa.string(),
            "per_share": pa.float64(),
            "tax_rate": pa.float64(),
            # 배당락일 = valid_from. 입금일은 따로 든다 — 인식은 배당락일에
            # 하되(§4) 실제 현금 이동은 이 날짜에 일어난다.
            "pay_date": pa.timestamp("us", tz="UTC"),
        },
        doc=(
            "배당. **valid_from 은 배당락일이다.** 입금일 인식으로 바꾸면 "
            "배당락 하락이 그대로 낙폭이 되어 보상 함수가 손실로 오인한다."
        ),
    ),
    "nav_daily": TableSpec(
        name="nav_daily",
        columns={
            "nav": pa.float64(),
            "inflow": pa.float64(),
            "twr_return": pa.float64(),
            "index_value": pa.float64(),
            "drawdown": pa.float64(),
            "cash_krw": pa.float64(),
            "cash_usd": pa.float64(),
            "equity_kr": pa.float64(),
            "equity_us": pa.float64(),
            "accrued_dividend": pa.float64(),
            "payable": pa.float64(),
            "fx_rate": pa.float64(),
            # 양도세는 일간 NAV 에 넣지 않는다 (§5). 충당금으로만 추적하고
            # 세후 성과를 따로 보고한다.
            "tax_provision": pa.float64(),
            "nav_after_tax": pa.float64(),
            "benchmark_index": pa.float64(),
        },
        doc=(
            "일간 회계 스냅샷. 한국시간 15:40 하루 한 번, 미장은 직전 종가 "
            "(accounting.md §2). **낙폭은 NAV 원금액이 아니라 TWR 누적지수로 "
            "계산한다** — 원금액으로 하면 입금이 낙폭을 지운다."
        ),
    ),
    # -- 실행 (M3) ------------------------------------------------------------
    "orders": TableSpec(
        name="orders",
        columns={
            "market": pa.string(),
            "session_id": pa.string(),
            "slice_seq": pa.int32(),
            "side": pa.string(),
            "quantity": pa.float64(),
            "limit_price": pa.float64(),
            "target_weight": pa.float64(),
            "status": pa.string(),
            "reason": pa.string(),
        },
        # **주문 식별자가 자연키다.** (session_id, entity_id, slice_seq) 로
        # 결정론적으로 만들므로, 재시작 후 같은 주문을 다시 만들어도 같은 키가
        # 나오고 창고가 중복을 거부한다 — 그것이 멱등성의 마지막 방어선이다.
        natural_key=("entity_id", "session_id", "slice_seq"),
        doc=(
            "집행한 주문. 프로세스는 반드시 죽었다 살아난다 — 그때 중복 주문이 "
            "나가지 않게 하는 것이 이 테이블의 존재 이유다."
        ),
    ),
    "killswitch": TableSpec(
        name="killswitch",
        columns={
            "state": pa.string(),
            "reason": pa.string(),
            "triggered_by": pa.string(),
        },
        doc=(
            "킬스위치 latch. **한번 걸리면 사람이 풀기 전까지 유지된다.** "
            "자동 해제를 두면 발동 원인이 남아 있는 채로 다시 매매한다."
        ),
    ),
    "realized_weights": TableSpec(
        name="realized_weights",
        columns={
            "market": pa.string(),
            "session_id": pa.string(),
            "target_weight": pa.float64(),
            "realized_weight": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "session_id"),
        doc=(
            "목표 비중과 **실제 체결된 비중** (불변식 7). 빠지면 RL 은 자기가 "
            "하지 않은 행동으로 보상받고 학습이 망가진다. 소액 구간에서는 정수 "
            "라운딩 때문에 둘이 크게 벌어진다."
        ),
    ),
    "agent_cache": TableSpec(
        name="agent_cache",
        columns={
            "agent": pa.string(),
            "agent_version": pa.string(),
            "features_hash": pa.string(),
            "output": pa.string(),
            "computed_at": pa.timestamp("us", tz="UTC"),
        },
        natural_key=("entity_id", "valid_from", "agent", "agent_version", "features_hash"),
        doc=(
            "에이전트·LLM 출력 캐시. observed_at 은 계산한 벽시계 시각이 아니라 "
            "as_of 다 — 출력이 as_of 이전 데이터만의 함수이므로 그 시점에 알 수 "
            "있었던 것이 맞고, 벽시계를 찍으면 과거 리플레이에서 영영 안 보인다. "
            "실제 계산 시각은 computed_at 에 따로 남긴다."
        ),
    ),
    CONFIG_TABLE: TableSpec(
        name=CONFIG_TABLE,
        columns={"value_json": pa.string()},
        doc=(
            "임계치. 이중시간으로 둔다 — 오늘 임계치를 바꿨다고 작년 백테스트 "
            "결과가 소급해 바뀌면 재현이 불가능해진다."
        ),
    ),
}


def get_spec(table: str) -> TableSpec:
    try:
        return _SPECS[table]
    except KeyError:
        raise UnknownTable(
            f"등록되지 않은 테이블: {table!r}. 등록된 것: {sorted(_SPECS)}"
        ) from None


def table_names() -> list[str]:
    return sorted(_SPECS)
