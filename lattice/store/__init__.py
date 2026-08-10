"""데이터 게이트 — Parquet + DuckDB 위의 이중시간 저장·조회.

레포에서 Parquet/DuckDB 를 직접 만질 수 있는 유일한 패키지다.
유일한 조회 API 는 ``get(table, as_of, entity=None, lookback=None)`` 이며
내부에서 ``observed_at <= as_of`` 를 강제한다.

명세: docs/design/data-contract.md
"""
