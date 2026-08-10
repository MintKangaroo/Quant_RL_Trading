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
            # 장중 잠정치와 마감 후 확정치는 별도 행으로 들어온다.
            "is_final": pa.bool_(),
        },
        doc="수급. 잠정/확정 구분.",
    ),
    "fundamentals": TableSpec(
        name="fundamentals",
        columns={
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
