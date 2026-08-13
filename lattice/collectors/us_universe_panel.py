"""미장 명단 패널 — **이미 받은 시세에서 유도한다.**

미장 백필은 시세만 넣었다. 명단(``universe``)이 비어 있으면 Analyst 는 대상
종목이 0개라 아무 점수도 내지 않는다 — 실측에서 미장 chart·risk 가 "300세션
측정, 표본 0일 / 0행, IC nan" 으로 끝났다. **IC 가 나쁜 것과 잰 것이 없는
것은 다르다.** 이 모듈이 그 빈칸을 채운다.

## 왜 SEC 명단을 과거에 찍지 않는가

``us_universe.fetch_listings`` 가 주는 것은 **오늘 시점 명단**이다. 그걸 과거
전 세션에 그대로 찍으면, 2023년에 아직 상장하지 않은 종목이 2023년 명단에
들어간다. 그건 미래를 보는 것이다 (data-contract §3).

그래서 명단은 시세에서 유도한다 — **그날 봉이 있으면 그날 거래됐다.** 우리가
실제로 아는 사실만 쓰는 방식이고, 새 API 호출도 필요 없다.

``observed_at`` 도 지어내지 않는다. 그날 봉을 **우리가 알 수 있었던 시각**이
그대로 명단 행의 관측시각이다 (미장 일봉은 다음날 05:20 KST 공표 정책을
탄다). 명단이 시세보다 먼저 관측되는 일은 구조적으로 없다.

## 못 고치는 것 — 생존편향

LS 는 상장폐지 종목의 시세를 주지 않는다. 시세에 없으니 여기서 만드는
명단에도 없다. **미장 IC 는 이 편향 위에서 읽어야 한다** — 살아남은 종목만
보고 잰 수치라 실제보다 후하게 나온다. 명단 소스를 고쳐도 가격이 없어서
못 고친다 (``ls_us_source`` 모듈 docstring 의 실측 참조).

## 결측일을 상폐로 보지 않는다

국장 백필은 거래소 명단 스냅샷을 받으므로 "어제 있고 오늘 없으면 상폐" 가
성립한다. 미장에는 그 스냅샷이 없다. 거래가 없어 봉이 빠진 날까지 상폐로
찍으면 **유동성 낮은 종목이 매일 상폐와 재상장을 반복한다** — 그리고 그
종목은 매 세션 명단에서 빠졌다 들어왔다 하며 횡단면을 흔든다.

그래서 상폐는 하나의 조건으로만 찍는다: **마지막 봉이 패널 끝에서
``DEAD_SESSIONS`` 이상 떨어진 종목.** 그 사이 결측일에는 행을 만들지 않는다.
행이 없는 것은 "그날은 알 수 없었다" 이고, 그건 사실이다 — 없는 것을
'거래 가능' 으로 채우지 않는다 (``flows`` 테이블과 같은 원칙).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from lattice.collectors.market_hours import Market

#: 마지막 봉 이후 이만큼 세션이 지나도록 안 나오면 상폐로 본다. 미장에는
#: 명단 스냅샷이 없어서 상폐를 이 방법으로만 알 수 있다. 짧게 잡으면 긴
#: 거래정지가 상폐로 찍히고, 길게 잡으면 상폐가 늦게 반영된다.
DEAD_SESSIONS = 10

UNIVERSE = "universe"
SOURCE = "ls_us_derived"


def run_id_for(market: Market, day: date) -> str:
    """세션당 하나. 같은 세션을 두 번 넣으려 하면 창고가 거부한다."""
    return f"{market}-universe-{day.isoformat()}"


def delisting_run_id(market: Market, day: date) -> str:
    return f"{market}-universe-delisted-{day.isoformat()}"


def _ticker(entity_id: str) -> str:
    """``US:AAPL`` → ``AAPL``."""
    _, _, tail = entity_id.partition(":")
    return tail or entity_id


@dataclass
class SessionRows:
    day: date
    rows: list[dict[str, Any]]


def session_rows(
    records: Iterable[Mapping[str, Any]], *, market: Market = Market.US
) -> list[dict[str, Any]]:
    """한 세션의 시세 행 → 그 세션의 명단 행.

    ``name`` 은 티커 그대로 둔다. 회사명은 SEC 에서 **오늘 이름**만 받을 수
    있는데, 그걸 과거 행에 찍으면 사명 변경이 소급된다. 이름은 점수에
    들어가지 않으므로 정확한 티커가 부정확한 이름보다 낫다.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        entity = str(record["entity_id"])
        if entity in seen:
            continue
        seen.add(entity)
        rows.append(
            {
                "entity_id": entity,
                "valid_from": record["valid_from"],
                # 봉을 알 수 있었던 시각이 명단을 알 수 있었던 시각이다.
                "observed_at": record["observed_at"],
                "source": SOURCE,
                "market": str(market),
                "name": _ticker(entity),
                "is_listed": True,
                "is_tradable": True,
                "delisted_on": None,
            }
        )
    return rows


def delisting_rows(
    last_seen: Mapping[str, tuple[date, Any, Any]],
    sessions: list[date],
    *,
    market: Market = Market.US,
    dead_sessions: int = DEAD_SESSIONS,
) -> list[dict[str, Any]]:
    """마지막 봉 이후 소식이 끊긴 종목의 상폐 행.

    ``last_seen`` 은 ``entity_id → (마지막 세션, valid_from, observed_at)``.
    상폐 행의 시각은 **마지막으로 본 세션** 것을 쓴다. 오늘 시각으로 찍으면
    "오늘 상폐됐다" 가 되어 과거 리플레이가 그날의 사실과 어긋난다.
    """
    if len(sessions) <= dead_sessions:
        return []
    cutoff = sessions[-(dead_sessions + 1)]

    rows: list[dict[str, Any]] = []
    for entity, (day, valid_from, observed_at) in sorted(last_seen.items()):
        if day > cutoff:
            continue
        rows.append(
            {
                "entity_id": entity,
                "valid_from": valid_from,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": str(market),
                "name": _ticker(entity),
                "is_listed": False,
                "is_tradable": False,
                "delisted_on": valid_from,
            }
        )
    return rows


@dataclass
class BuildReport:
    sessions: int = 0
    rows: int = 0
    skipped: int = 0
    delisted: int = 0
    entities: set[str] = field(default_factory=set)

    def render(self) -> str:
        return (
            f"세션 {self.sessions}개 · 명단 {self.rows:,}행 · "
            f"종목 {len(self.entities):,}개 · 상폐 {self.delisted}행 "
            f"(건너뜀 {self.skipped})"
        )


def group_sessions(
    frame: Any, *, session_column: str = "valid_from"
) -> Iterator[tuple[date, list[dict[str, Any]]]]:
    """가격 프레임을 세션별로 쪼갠다. 세션 하나가 적재 단위다.

    한 번에 다 넣지 않는 이유는 파티션이 ``observed_at`` 날짜로 갈리기
    때문이다 — 세션 단위로 넣어야 파일 하나에 그 세션이 통째로 들어간다.
    종목 축으로 나눠 넣으면 파일이 종목 수만큼 생긴다 (실측: 247만개).
    """
    if frame.empty:
        return
    days = frame[session_column].dt.date
    for day in sorted(set(days)):
        yield day, frame[days == day].to_dict(orient="records")
