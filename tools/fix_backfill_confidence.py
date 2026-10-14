"""백필 경계의 가짜 confidence 를 정정본(revision+1)으로 바로잡는다 — 미리보기가 기본 (2026-10-14).

    .venv/bin/python tools/fix_backfill_confidence.py --analyst ranker risk            # 미리보기(쓰기 없음)
    .venv/bin/python tools/fix_backfill_confidence.py --analyst ranker risk --apply    # 적재(장 마감 뒤, 사람이)

## 무엇이 틀렸나 (docs/design/data-contract.md "백필 경계 confidence")

창고 `signals` 의 저장 `confidence` 에 **가짜 0** 이 있다 — ranker 2024-02-15~23(7세션), risk 2024-09-25~10-15(12세션)
(`docs/diag/confidence-regime-2026-10-11.md` §0). 같은 날 실전 정의(`ic.rolling_confidence`)로 다시 재면 0.08~0.12 다.

원인은 **백필 순서**다. 백필 도구는 confidence 를 "이미 창고에 있는 과거 signals" 로 잰다(`tools/backfill_signals.py`
docstring: 오름차순으로 처리해야 한다). 그런데 구간을 **뒤 조각부터** 넣었다 — ranker 는 2024-02-01~ 조각이 2022-07~2024-01
조각보다, risk 는 2024-09-03~ 조각이 그 앞 조각보다 먼저 적재됐다. 뒤 조각의 첫 세션은 앞 이력이 없어 1.0(증거 없음)으로
시작했고, 라벨이 붙은 세션이 2~7개뿐인 며칠 동안 그 몇 날의 IC 평균이 0 이하로 떨어져 0 이 찍혔다. 나중에 앞 조각이
들어와도 이미 적힌 confidence 는 다시 계산되지 않는다.

이 0 은 백테스트에서 ranker 단독 알파일 때 **동결**(`selector/freshness.zero_confidence`)을 부른다.

## 정정 규칙

1. 저장 confidence 를 세션별로 모은다(한 세션은 한 값이어야 한다).
2. 의심 세션 = 저장값이 0 이거나, **증거 없음(1.0) 재시작** 뒤 60세션(롤링 창) 안. 재시작 = 저장값이 1.0 인데 그 앞 세션에
   같은 Analyst 신호가 있는 날(지금 창고 기준). `--all` 이면 범위 전부.
3. 의심 세션마다 그 세션의 `valid_from`(= 신호의 as_of) 으로 `ic.rolling_confidence` 를 다시 부른다 — 실전·백필과 같은 함수,
   같은 as_of. |재계산 − 저장| > `--tol` 이면 정정 대상.
4. 정정본 = 그 세션·그 Analyst 의 행 전부(자연키 entity_id·valid_from·analyst·analyst_version)를 값 그대로 옮기고
   `confidence` 만 바꾸고 `revision` 을 1 올린다. **`observed_at` 은 원래 행 그대로다.**

## observed_at 을 그대로 두는 이유 (`tools/reclassify_424b2.py` 와 같은 판단)

입력이 늦게 온 것이 아니라 **계산이 틀렸다.** 앞 조각 신호의 `observed_at` 은 그 세션 공표 시각이라, 백필 규약상 그 세션
as_of 에 이미 보이는 이력이다(재계산이 바로 그 as_of 로 읽어 같은 값을 낸다). 지금 시각을 넣으면 과거 as_of 로 도는
백테스트가 계속 가짜 0 을 본다 — 고치는 목적이 사라진다. 실전(모의계좌)은 이 구간에 없었으므로 "그때 실제로 쓴 값" 이
바뀌는 일도 없다.

## 두 번 돌려도 안전하다

정정 뒤엔 저장값 = 재계산값이라 대상이 0 이다. run id 는 `fix-confidence-<analyst>-<시장>-<날짜>` 로 결정론적이다.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402

SIGNALS = "signals"
#: 롤링 창(세션) — `ic.rolling_confidence` 기본값과 같다. 재시작 뒤 이만큼이 오염될 수 있다.
WINDOW = 60
#: 저장값과 재계산값이 이만큼 넘게 다르면 정정. 정상 세션의 중앙 절대차는 0.0013~0.0018 이었다(진단 §0).
DEFAULT_TOL = 0.01
CHUNK_DAYS = 90


@dataclass
class Correction:
    analyst: str
    session: date
    as_of: datetime
    stored: float
    recomputed: float
    rows: int
    reason: str


@dataclass
class Plan:
    corrections: list[Correction] = field(default_factory=list)
    checked: int = 0
    restarts: dict[str, list[date]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def stored_by_session(store: Store, *, analyst: str, market: str, start: date, end: date, now: datetime) -> pd.DataFrame:
    """세션별 저장 confidence(지금 창고의 최신 revision). 열: session · as_of · confidence · n · spread."""
    parts: list[pd.DataFrame] = []
    cur = datetime(start.year, start.month, start.day, tzinfo=UTC)
    stop = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
    while cur < stop:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), stop)
        frame = store.get(SIGNALS, as_of=now, lookback=now - cur, until=nxt, market=market,
                          columns=["entity_id", "valid_from", "analyst", "confidence"])
        if not frame.empty:
            frame = frame[(frame["analyst"].astype(str) == analyst)
                          & (frame["valid_from"] >= pd.Timestamp(cur)) & (frame["valid_from"] < pd.Timestamp(nxt))]
            if not frame.empty:
                parts.append(frame.groupby("valid_from")["confidence"].agg(["median", "min", "max", "size"]).reset_index())
        cur = nxt
    if not parts:
        return pd.DataFrame(columns=["session", "as_of", "confidence", "n", "spread"])
    agg = pd.concat(parts, ignore_index=True).sort_values("valid_from")
    return pd.DataFrame({
        "session": agg["valid_from"].dt.date.to_numpy(),
        "as_of": agg["valid_from"].to_numpy(),
        "confidence": agg["median"].astype(float).to_numpy(),
        "n": agg["size"].astype(int).to_numpy(),
        "spread": (agg["max"] - agg["min"]).astype(float).to_numpy(),
    })


def suspects(stored: pd.DataFrame, *, check_all: bool) -> tuple[list[int], list[date]]:
    """의심 세션의 위치와 재시작 날짜. 순수 함수 — `stored` 는 session 오름차순."""
    if stored.empty:
        return [], []
    conf = stored["confidence"].to_numpy(dtype=float)
    if check_all:
        return list(range(len(stored))), []
    picked: set[int] = set()
    restarts: list[date] = []
    for i, value in enumerate(conf):
        if value <= 1e-12:
            picked.add(i)
        # 재시작: 증거 없음(1.0) 인데 앞에 같은 Analyst 신호 세션이 있다 → 앞 이력 없이 계산됐다.
        if i > 0 and abs(value - ic.NO_EVIDENCE_CONFIDENCE) < 1e-12 and abs(conf[i - 1] - ic.NO_EVIDENCE_CONFIDENCE) > 1e-12:
            restarts.append(stored["session"].iloc[i])
            picked.update(range(i, min(len(stored), i + WINDOW + 1)))
    return sorted(picked), restarts


def plan(store: Store, *, analysts: list[str], market: str, start: date, end: date, now: datetime,
         tol: float = DEFAULT_TOL, check_all: bool = False, recompute: Any = None) -> Plan:
    """정정 계획. ``recompute(analyst, as_of)`` 는 시험용 주입점 — 기본은 ``ic.rolling_confidence``."""
    recompute = recompute or (lambda analyst, as_of: ic.rolling_confidence(
        store, analyst=analyst, as_of=as_of, market=market))
    out = Plan()
    for analyst in analysts:
        stored = stored_by_session(store, analyst=analyst, market=market, start=start, end=end, now=now)
        uneven = stored[stored["spread"] > 1e-12]
        if not uneven.empty:
            out.warnings.append(f"{analyst}: 한 세션 안 confidence 가 여럿인 세션 {len(uneven)}개 — 건너뛴다")
            stored = stored[stored["spread"] <= 1e-12].reset_index(drop=True)
        positions, restarts = suspects(stored, check_all=check_all)
        out.restarts[analyst] = restarts
        for i in positions:
            row = stored.iloc[i]
            as_of = pd.Timestamp(row["as_of"]).to_pydatetime()
            value = float(recompute(analyst, as_of))
            out.checked += 1
            old = float(row["confidence"])
            if not math.isfinite(value) or abs(value - old) <= tol:
                continue
            reason = "저장 0" if old <= 1e-12 else ("증거 없음 재시작" if abs(old - 1.0) < 1e-12 else "재시작 창")
            out.corrections.append(Correction(analyst=analyst, session=row["session"], as_of=as_of, stored=old,
                                              recomputed=value, rows=int(row["n"]), reason=reason))
    return out


def correction_rows(store: Store, item: Correction, *, market: str, now: datetime) -> list[dict[str, Any]]:
    """그 세션·Analyst 의 행 전부를 값 그대로, confidence 만 바꿔 revision+1. observed_at 은 원래 행 그대로."""
    frame = store.get(SIGNALS, as_of=now, lookback=now - item.as_of + timedelta(days=1),
                      until=item.as_of + timedelta(seconds=1), market=market)
    frame = frame[(frame["analyst"].astype(str) == item.analyst) & (frame["valid_from"] == pd.Timestamp(item.as_of))]
    keep = ["entity_id", "valid_from", "observed_at", "source", "revision", "analyst", "analyst_version", "score",
            "confidence", "horizon_days", "features_hash", "evidence_json", "latency_ms"]
    rows: list[dict[str, Any]] = []
    for record in frame[[c for c in keep if c in frame.columns]].to_dict("records"):
        record["revision"] = int(record["revision"]) + 1
        record["confidence"] = float(item.recomputed)
        for key in ("valid_from", "observed_at"):
            record[key] = pd.Timestamp(record[key]).to_pydatetime()
        rows.append(record)
    return rows


def run_id(item: Correction, market: str) -> str:
    return f"fix-confidence-{item.analyst}-{market}-{item.session.isoformat()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--analyst", nargs="+", default=["ranker", "risk"])
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 11, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="기본 오늘")
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL)
    parser.add_argument("--all", action="store_true", help="재시작 창이 아니라 범위 전부 재계산(느림)")
    parser.add_argument("--json", type=Path, help="미리보기 결과를 이 파일에")
    parser.add_argument("--apply", action="store_true", help="정정본을 적재한다(장 마감 뒤, 사람이)")
    args = parser.parse_args(argv)
    from quant_rl_trading.replay.clock import LiveClock

    now = LiveClock().now()
    store = Store(root=Path(args.root))
    end = args.end or now.date()
    result = plan(store, analysts=list(args.analyst), market=args.market, start=args.start, end=end, now=now,
                  tol=args.tol, check_all=args.all)
    for line in result.warnings:
        print("경고:", line)
    for analyst, days in result.restarts.items():
        print(f"{analyst}: 증거 없음 재시작 {len(days)}회 — {', '.join(d.isoformat() for d in days) or '없음'}")
    print(f"재계산 {result.checked}세션 · 정정 대상 {len(result.corrections)}세션")
    for item in result.corrections:
        print(f"  {item.analyst:7s} {item.session}  저장 {item.stored:.4f} → 재계산 {item.recomputed:.4f}  "
              f"({item.rows}행, {item.reason})")
    if args.json:
        args.json.write_text(json.dumps([{**item.__dict__, "session": item.session.isoformat(),
                                          "as_of": item.as_of.isoformat()} for item in result.corrections],
                                        ensure_ascii=False, indent=1))
    if not args.apply:
        print("미리보기 — 쓰지 않았다(--apply 로 적재)")
        return 0
    written = 0
    for item in result.corrections:
        rows = correction_rows(store, item, market=args.market, now=now)
        with contextlib.suppress(DuplicateIngestRun):
            written += int(store.append(SIGNALS, rows, ingest_run_id=run_id(item, args.market)))
    print(f"적재 {written:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
