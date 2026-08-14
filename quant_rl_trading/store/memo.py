"""한 세션 동안만 사는 읽기 캐시.

## 왜 필요한가

하루 세션은 Analyst 를 여섯 번 돌린다. 여섯이 **같은 as_of · 같은 창**으로
`prices` 와 `universe` 를 각자 조회한다 — 같은 질의를 여섯 번 하는 것이고,
그 질의가 세션 비용의 대부분이다.

## 왜 Store 자체에 캐시를 넣지 않는가

Store 는 프로세스 수명 동안 산다. 거기에 캐시를 붙이면 대시보드처럼 오래 뜬
프로세스가 **낡은 데이터를 계속 보여주게** 되고, 그건 조용히 틀리는 종류의
고장이다. 캐시는 수명이 짧고 경계가 분명해야 한다 — 그래서 세션이 자기
캐시를 만들어 쓰고 버린다.

## 무엇을 보장하는가

- `get` 은 **같은 인자면 같은 프레임**을 돌려준다. 게이트를 우회하지 않는다 —
  첫 호출은 진짜 `store.get` 이다 (불변식 1)
- `append` 는 위임하고 **그 테이블의 캐시를 버린다.** 세션 안에서 신호를 쓰고
  다시 읽는 경로가 있어서, 안 버리면 방금 쓴 것을 못 본다
- 돌려주는 프레임은 **사본**이다. 호출자가 제자리에서 고쳐도 다음 호출자가
  오염된 것을 보지 않는다. 사본 비용보다 조용한 오염이 비싸다
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from quant_rl_trading.store import Store


def _key(
    table: str,
    as_of: datetime,
    entity: str | Sequence[str] | None,
    lookback: timedelta | int | None,
    until: datetime | None,
    columns: Sequence[str] | None,
    market: str | None,
) -> tuple[Any, ...]:
    """조회 하나를 가리키는 키. **인자가 하나라도 다르면 다른 질의다.**"""
    return (
        table,
        as_of,
        entity if isinstance(entity, str) or entity is None else tuple(entity),
        lookback,
        until,
        tuple(columns) if columns is not None else None,
        market,
    )


class MemoStore:
    """읽기만 기억하는 Store 껍데기. 세션 하나가 쓰고 버린다."""

    def __init__(self, inner: Store) -> None:
        self._inner = inner
        self._frames: dict[tuple[Any, ...], pd.DataFrame] = {}
        self.hits = 0
        self.misses = 0

    # -- 조회 -----------------------------------------------------------------

    def get(
        self,
        table: str,
        *,
        as_of: datetime,
        entity: str | Sequence[str] | None = None,
        lookback: timedelta | int | None = None,
        until: datetime | None = None,
        columns: Sequence[str] | None = None,
        market: str | None = None,
    ) -> pd.DataFrame:
        key = _key(table, as_of, entity, lookback, until, columns, market)
        cached = self._frames.get(key)
        if cached is None:
            self.misses += 1
            cached = self._inner.get(
                table,
                as_of=as_of,
                entity=entity,
                lookback=lookback,
                until=until,
                columns=columns,
                market=market,
            )
            self._frames[key] = cached
        else:
            self.hits += 1
        return cached.copy()

    def config(self, name: str, *, as_of: datetime) -> Any:
        # 설정은 작고 자주 읽힌다. 캐시하지 않는다 — 이득이 없고, 세션 중간에
        # 설정이 바뀌는 경우를 굳이 막을 이유도 없다.
        return self._inner.config(name, as_of=as_of)

    # -- 적재 -----------------------------------------------------------------

    def append(
        self,
        table: str,
        records: Sequence[Mapping[str, object]],
        *,
        ingest_run_id: str,
        source: str | None = None,
    ) -> int:
        written = self._inner.append(
            table, records, ingest_run_id=ingest_run_id, source=source
        )
        self.invalidate(table)
        return written

    def invalidate(self, table: str) -> None:
        """그 테이블의 기억을 버린다. 방금 쓴 것을 못 보는 일을 막는다."""
        for key in [key for key in self._frames if key[0] == table]:
            del self._frames[key]

    # -- 위임 -----------------------------------------------------------------

    def ingest_run_recorded(self, table: str, ingest_run_id: str) -> bool:
        return self._inner.ingest_run_recorded(table, ingest_run_id)

    def tables(self) -> list[str]:
        return self._inner.tables()

    @property
    def root(self) -> Any:
        return self._inner.root

    def __getattr__(self, name: str) -> Any:
        # 위에서 다루지 않은 것은 그대로 넘긴다. 캐시가 Store 의 기능을
        # 가리지 않게 한다.
        return getattr(self._inner, name)
