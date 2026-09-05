"""0행의 판정 — "원본이 안 냈다" 와 "우리가 못 받았다" 를 가른다.

수집이 0행으로 끝났을 때 그것이 무엇인지 지금까지는 구분되지 않았다.

    (가) 원본이 그날 값을 안 냈다        휴장·미공표·그 종목엔 그 사실이 없음
    (나) 원본은 냈는데 우리가 못 받았다   API 오류·인증 실패·타임아웃·파싱 실패
    (다) 원본이 냈고 우리도 받았는데 0건  조건에 맞는 행이 진짜로 없음

셋이 전부 "0행" 으로 끝나고 rc=0 으로 나갔다. 그래서 **(나)가 며칠씩 조용히
이어져도 아무도 몰랐다** — 2026-08-19 KRX 지수·시총(``market_stats=0`` ·
``indices=0`` 을 "완료 — 실패 0건" 으로 적었다), 2026-08-22 FRED 지수.

## 세 원칙

1. **셋을 구별해 창고에 남긴다.** 로그 문구만 바꾸면 사람이 로그를 안 볼 때
   판정할 방법이 없다. ``ingest_outcomes`` 표에 남는다.
2. **(나)는 rc≠0 으로 나간다.** (가)와 (다)는 정상이다
   (선례: 커밋 ``7ad5680`` — Analyst 가 죽으면 rc 로 나간다).
3. **모르면 (나)로 본다.** "휴장이겠거니" 하고 넘어가는 쪽이 조용한 실패를
   만든다. 휴장은 달력에 물어 **확인**할 수 있고(``market_hours``), 확인
   못 하면 그건 아는 것이 아니다.

## 빈 응답을 (가)로 부를 수 있는 조건

200 을 받고 0건이라는 사실만으로는 (가)가 증명되지 않는다. 죽은 키가 빈
블록을 돌려주는 일이 실제로 있다(ECOS 는 무효한 키에 HTML 503 을 냈고
반나절을 오판했다). 그래서 **원본이 이미 냈어야 할 시각인지**를 먼저 묻는다.

- 아직 공표 시각 전 → ``NOT_PUBLISHED``. ``publication.NotYetPublished`` 가
  이미 쓰던 어휘다. 새로 만들지 않는다.
- 원본이 그 세션 값을 **낸다고 실측으로 확인한** 시각 전 → ``TOO_EARLY``.
  22:40 수집이 KRX 시총·지수에 0행을 받고 같은 호출이 아침에 5,744행을
  받은 것이 이 경우다. 실패가 아니라 "나중에 다시" 다.
- 그 시각이 지났는데 0건, 또는 **그 시각을 우리가 모름** →
  ``EMPTY_UNCONFIRMED``. 원칙 3 에 따라 (나)로 본다.

달력이 모르는 유령 휴장일(2026-07-17)이 여기 걸릴 수 있다. 그건 사람이
``market_hours._OVERRIDES`` 에 적어 **확인**하는 자리다 — 확인하면 그날은
애초에 호출하지 않게 된다. 코드가 알아서 휴장으로 넘겨주는 편의는 두지
않는다. 그 편의가 수집 실패를 휴장으로 세탁한다.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from quant_rl_trading.store import ConfigNotFound, DuplicateIngestRun

logger = logging.getLogger(__name__)

OUTCOMES_TABLE = "ingest_outcomes"

#: 원본이 그 세션 값을 낸다고 **실측으로 확인한** 시각(세션 마감 뒤 시간).
#: 키는 데이터셋 이름의 ``:`` 를 ``.`` 로 바꾼 것이다.
#: 값이 없으면 "확인 못 했다" 이고, 그러면 빈 응답은 (나)가 된다 (원칙 3).
AVAILABILITY_SECTION = "collectors.available_after_hours"


class Verdict(StrEnum):
    """0행 하나에 대한 판정. 값이 창고에 그대로 들어간다."""

    #: (가) 아직 공표 시각이 아니다. 네트워크를 건드리기도 전에 갈린다.
    NOT_PUBLISHED = "not_published"
    #: (가) 공표는 됐다지만 원본이 실제로 내는 시각은 아직이다. 실측 근거가 있다.
    TOO_EARLY = "too_early"
    #: (다) 받았는데 조건에 맞는 행이 없다. 정상이다.
    FILTERED_EMPTY = "filtered_empty"
    #: (나) 호출이 예외로 끝났다.
    FETCH_FAILED = "fetch_failed"
    #: (나) 0건인데 그것이 (가)라는 근거가 없다. 모르면 (나)다.
    EMPTY_UNCONFIRMED = "empty_unconfirmed"

    @property
    def ours(self) -> bool:
        """우리 실패인가. 이것만 rc≠0 으로 나간다."""
        return self in (Verdict.FETCH_FAILED, Verdict.EMPTY_UNCONFIRMED)


def availability_key(dataset: str) -> str:
    """데이터셋 이름 → config 키. ``krx_openapi:indices`` 는 점으로 갈린다."""
    return f"{AVAILABILITY_SECTION}.{dataset.replace(':', '.')}"


def available_after(store: Any, dataset: str, *, as_of: datetime) -> timedelta | None:
    """원본이 낸다고 확인한 시각까지의 간격. **모르면 None** (불변식 10).

    설정에 없는 데이터셋은 "확인하지 못했다" 는 뜻이고, 그 상태에서 0건이면
    (나)로 간다. 상수를 코드에 박아 두면 그 상수가 확인한 사실인 척한다 —
    ``publication.UnverifiedSchedulePolicy`` 가 같은 이유로 존재한다.
    """
    try:
        hours = store.config(availability_key(dataset), as_of=as_of)
    except ConfigNotFound:
        return None
    if hours is None:
        return None
    return timedelta(hours=float(hours))


def judge_empty(
    *,
    published_at: datetime,
    now: datetime,
    window: timedelta | None,
) -> Verdict:
    """빈 응답 하나를 판정한다. ``window`` 가 None 이면 (나)다.

    ``published_at`` 은 그 세션의 공표 추정시각이다. 원본이 실제로 내는
    시각은 그보다 늦을 수 있고(KRX 시총은 마감 +7시간에도 없었다), 그 차이를
    실측으로 확인한 것만이 ``window`` 다.
    """
    if window is None:
        return Verdict.EMPTY_UNCONFIRMED
    return Verdict.TOO_EARLY if now < published_at + window else Verdict.EMPTY_UNCONFIRMED


def record(
    store: Any,
    *,
    dataset: str,
    table: str,
    market: str,
    day: date | datetime,
    verdict: Verdict,
    stage: str,
    observed_at: datetime,
    error: BaseException | None = None,
    detail: str = "",
) -> None:
    """0행 하나를 창고에 남긴다.

    ``valid_from`` 은 그 세션, ``observed_at`` 은 **우리가 그 사실을 겪은
    시각**이다 (불변식 3). 나중에 데이터가 채워져도 이 행은 지우지 않는다 —
    그날 그 시각에 우리가 0행을 받았다는 것은 나중에 고쳐도 달라지지 않는
    사실이고, 정정본이 흔적을 지우는 것을 막으려고 만든 표다.

    **여기서 죽지 않는다.** 사고를 적다가 사고를 키우면 안 된다.
    """
    session = day if isinstance(day, datetime) else _midnight(day)
    run_id = f"ingest-outcome-{dataset}-{session.date().isoformat()}-{verdict}"
    row = {
        "entity_id": dataset,
        "valid_from": session,
        "observed_at": observed_at,
        "source": dataset.split(":", 1)[0],
        "market": market,
        "table_name": table,
        "verdict": str(verdict),
        "stage": stage,
        "error_type": type(error).__name__ if error is not None else "",
        # 원문을 자르지 않는다. 숫자 하나가 원인을 특정하는 경우가 있다.
        "detail": detail or (str(error) if error is not None else ""),
    }
    try:
        with contextlib.suppress(DuplicateIngestRun):
            store.append(OUTCOMES_TABLE, [row], ingest_run_id=run_id)
    except Exception as write_error:
        logger.warning("수집 판정을 창고에 못 적었다: %s", write_error)


def _midnight(day: date) -> datetime:
    from datetime import UTC

    return datetime(day.year, day.month, day.day, tzinfo=UTC)
