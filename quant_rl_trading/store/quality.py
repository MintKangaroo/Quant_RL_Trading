"""Reject known contaminated inputs before producing new research evidence."""

import re
from datetime import UTC, datetime, timedelta

from quant_rl_trading.store import Store

#: 구버전 상폐 추정 행의 ingest_run_id — 날짜가 곧 **그 사실을 알게 된 날**이다.
_LEGACY_RUN = re.compile(r"US-universe-delisted-(\d{4}-\d{2}-\d{2})")


def require_causal_universe(
    store: Store, *, as_of: datetime, market: str, window_start: datetime | None = None
) -> None:
    """구버전(`ls_us_derived`) 상폐 추정 행은 observed_at 이 마지막 봉으로 소급돼 있다 —
    그 사실을 실제로 안 날은 ingest_run_id 의 날짜다. 평가 구간이 그 날짜보다 **앞에서
    시작하면** 미래 정보가 새므로 거절한다. 구간이 전부 그 뒤면(오늘 하루짜리 shadow
    세션처럼) 그 행은 이미 알려진 사실이라 누수가 없다 — 2026-09-11 12:21·15:17 미장
    shadow 가 이 검사로 통째로 죽었고, 그날 결정에는 8/28·9/4 에 안 사실만 들어간다.
    ``window_start`` 를 안 주면(학습처럼 과거 전체를 쓰는 경우) 오염 행이 하나라도
    보이면 거절한다."""
    if market != "US":
        return
    frame = store.get(
        "universe",
        as_of=as_of,
        market=market,
        columns=["source", "is_listed", "delisted_on", "ingest_run_id"],
    )
    if frame.empty:
        return
    contaminated = frame["source"].eq("ls_us_derived") & (
        ~frame["is_listed"].fillna(True).astype(bool) | frame["delisted_on"].notna()
    )
    if not contaminated.any():
        return
    if window_start is not None:
        known_by: list[datetime] = []
        for run_id in frame.loc[contaminated, "ingest_run_id"].astype(str).unique():
            found = _LEGACY_RUN.search(run_id)
            if found is None:
                known_by = []
                break
            # 그날 안에 알게 됐다 — 다음 날 0시(UTC)부터는 확실히 안다.
            known_by.append(
                datetime.fromisoformat(found.group(1)).replace(tzinfo=UTC) + timedelta(days=1)
            )
        if known_by and window_start >= max(known_by):
            return
    raise ValueError(
        "Legacy US inferred delistings contain backdated future information. "
        "Rebuild a separate universe dataset from point-in-time evidence before "
        "training or backtesting; existing artifacts are not repaired automatically."
    )
