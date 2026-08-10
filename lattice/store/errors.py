"""store 계층 예외.

저장 거부는 조용히 넘어가면 안 된다. 거부 사유가 타입으로 구분돼야
Collector 가 "스키마가 틀렸다" 와 "관측시각이 없다" 를 다르게 처리할 수 있다.
"""

from __future__ import annotations


class StoreError(Exception):
    """store 계층의 모든 예외의 뿌리."""


class UnknownTable(StoreError):
    """레지스트리에 없는 테이블. 오타로 새 테이블이 생기는 것을 막는다."""


class SchemaViolation(StoreError):
    """필수 컬럼 누락, 미등록 컬럼, 타입 불일치."""


class MissingObservedAt(SchemaViolation):
    """불변식 3 — observed_at 없는 레코드는 저장을 거부한다."""


class NaiveTimestamp(SchemaViolation):
    """타임존 없는 시각. 국장·미장을 한 창고에 넣는 이상 허용할 수 없다."""


class DuplicateIngestRun(StoreError):
    """이미 성공한 ingest_run_id 의 재적재.

    append-only 라 잘못 들어간 행을 지울 수 없다. 중복은 사후 정정이 아니라
    사전 거부로 막는다. 백필 재개(5단계)가 이 보장 위에 선다.
    """


class ConfigNotFound(StoreError):
    """해당 as_of 시점에 존재하지 않는 설정 키."""
