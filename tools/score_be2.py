"""BE2 매일 경로 — 그 세션 FA 76열을 창고에 적고(`fa_features`), 얼린 BE2 로 채점해 `signals`(analyst=be2)에 적는다.

    .venv/bin/python tools/score_be2.py --market KR                       # 마지막으로 공표된 세션 하나(크론)
    .venv/bin/python tools/score_be2.py --market KR --day 2026-10-02
    .venv/bin/python tools/score_be2.py --start 2026-04-01 --end 2026-09-30 --features-only   # 금고 창 피처 굽기
    .venv/bin/python tools/score_be2.py --compare-panel --start 2026-06-01 --end 2026-06-30 --features-only
                                                                         # 연구 패널과 값 대조(판정 창 끝 한 달)

## 같은 코드 (불변식 5)

- 피처: `analysts/fa_features.build_session` — 매일 경로·금고 창 굽기·백필이 전부 이 한 함수다.
- 점수: `analysts/be2.Be2Analyst.run` — 일일 실행기(`session/signals.produce`)와 같은 규약으로 적는다:
  `observed_at = as_of`(세션 공표 시각), run id `daily-signals-<시장>-<날짜>-be2`(같은 세션을 두 번 안 쓴다).
- as_of: 크론은 `tools/run_daily.last_published`(일일 실행기와 같은 함수), 날짜를 주면 `policy.for_session`.

## 운영 장부에 영향이 없는 이유

`be2` 는 `analyst_weights`(측정표)에 없고, 주간 IC 측정 목록에도 없다 — 운영 장부의 합성에서 가중치 0 이다.
이 점수를 쓰는 것은 `data/_be2_shadow/config-overrides.yaml` 의 `selector.weights_override` 뿐이다.

## 금고 창 신호

`--start/--end` 로 금고 창(2026-07-01~)을 채점하면 be2 의 금고 성적이 창고에 생긴다 — **금고 등록(해시 고정) 전에는
`--features-only` 로 피처만 굽는다.** 신호까지 적으려면 `--signals-from 2026-07-01` 을 명시한다(기본은 오늘 세션만).

## 종료 코드

0 정상(점수를 적었거나 이미 있었다) · 1 세션이 없다 · 3 FA 를 못 만들었다(행 0) · 4 점수를 안 냈다(모델·창 미달 —
`skip_reason` 을 찍는다) · 2 인자 오류. shadow 러너는 0 이 아니면 그날 shadow 세션을 돌리지 않는다 —
be2 점수 없이 돌면 후보가 비어 보유를 판다.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import fa_features, ic  # noqa: E402
from quant_rl_trading.analysts.be2 import Be2Analyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.schemas.fa import FA_FEATURES  # noqa: E402
from quant_rl_trading.session.signals import SIGNALS, run_id_for  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402

PANEL = Path("data/_diag/final-round/panel-KR-KR+US-20220701-20260630.parquet")  # invariant-allow: data-access — 연구 캐시 대조
RC_OK, RC_NO_SESSION, RC_BAD_ARGS, RC_NO_FEATURES, RC_NO_SCORE = 0, 1, 2, 3, 4


def score_session(store: Store, market: Market, session: date, as_of, *,  # type: ignore[no-untyped-def]
                  features_only: bool, write_signals: bool, dry_run: bool = False) -> int:
    """세션 하나: FA → (신호). rc 를 돌려준다."""
    ident = fa_features.run_id(str(market), session)
    if store.ingest_run_recorded(fa_features.TABLE, ident):
        print(f"  {session} FA: 이미 있다 ({ident})", flush=True)
    else:
        counts = fa_features.score_counts(store, str(market), session, as_of)
        empty = [name for name, n in counts.items() if n == 0]
        if empty:
            # 빈 점수 열로 FA 를 적으면 "순위 중앙" 으로 굳어 창고에 영영 남는다 — 적지 않고 멈춘다(나중에 --day 로 다시).
            print(f"  {session} FA: 점수 열 {empty} 이 비었다 — 일일 실행기가 아직 안 돌았거나 Analyst 가 죽었다. 적지 않는다",
                  flush=True)
            return RC_NO_FEATURES
        frame = fa_features.build_session(store, str(market), session, as_of)
        if frame.empty:
            print(f"  {session} FA: 행 0 — 점수·시세가 창고에 없다", flush=True)
            return RC_NO_FEATURES
        flags = {c: float(frame[c].mean()) for c in FA_FEATURES if c.startswith("miss_")}
        print(f"  {session} FA: {len(frame):,}종목 · 결측 표지 평균 "
              + " ".join(f"{k[5:]} {v:.0%}" for k, v in flags.items()), flush=True)
        if not dry_run:
            fa_features.write_session(store, frame, market=str(market), session=session, as_of=as_of)
    if features_only or not write_signals:
        return RC_OK
    run_id = run_id_for(SIGNALS, market, as_of, "be2")
    if store.ingest_run_recorded(SIGNALS, run_id):
        print(f"  {session} be2: 이미 있다 ({run_id})", flush=True)
        return RC_OK
    analyst = Be2Analyst(store, ReplayClock(as_of), market=market)
    confidence = ic.rolling_confidence(store, analyst="be2", as_of=as_of, market=str(market))
    signals = analyst.run(as_of, confidence=confidence)
    if not signals:
        print(f"  {session} be2: 점수 없음 — {analyst.skip_reason or '신호 0건'}", flush=True)
        return RC_NO_SCORE
    scores = pd.Series({s.entity_id: s.score for s in signals})
    print(f"  {session} be2: {len(signals):,}종목 · 점수 범위 {scores.min():+.2f}~{scores.max():+.2f} · "
          f"confidence {confidence:.2f}", flush=True)
    if not dry_run:
        rows = [s.row(observed_at=as_of, source="daily") for s in signals]
        with contextlib.suppress(DuplicateIngestRun):
            store.append(SIGNALS, rows, ingest_run_id=run_id)
    return RC_OK


def compare_panel(store: Store, sessions: list[date], *, panel_path: Path = PANEL) -> None:
    """실전 경로로 만든 FA 와 판정 패널(연구 캐시)을 같은 (종목, 세션) 에서 견준다 — 판정 창 안 세션만 뜻이 있다.

    열마다 최대 절대 차이와 순위상관을 찍는다. 같은 함수를 같은 as_of 로 불렀으니 대부분 0 이어야 하고,
    차이가 나는 열은 창고 정정본(연구 굽기는 as_of=굽는 날)이나 `miss_ba`(모듈 독스트링)로 설명돼야 한다.
    """
    import pyarrow.parquet as pq  # invariant-allow: data-access — 창고가 아닌 연구 캐시 대조(읽기만)

    wanted = [f"{s:%Y-%m-%d}" for s in sessions]
    table = pq.read_table(panel_path, columns=["entity_id", "session", *FA_FEATURES],  # invariant-allow: data-access — 연구 캐시
                          filters=[("session", ">=", min(sessions)), ("session", "<=", max(sessions))])
    research = table.to_pandas()
    research["session"] = pd.to_datetime(research["session"]).dt.date
    research = research[research["session"].isin(set(sessions))]
    now = LiveClock().now()
    live = fa_features.read_window(store, as_of=now, market="KR", lookback_days=(now.date() - min(sessions)).days + 5)
    live = live[live["session"].isin(set(sessions))]
    both = research.merge(live, on=["entity_id", "session"], suffixes=("_r", "_l"))
    print(f"대조 — 세션 {len(wanted)} · 연구 {len(research):,}행 · 실전 {len(live):,}행 · 겹침 {len(both):,}행", flush=True)
    if both.empty:
        return
    for c in FA_FEATURES:
        a, b = both[f"{c}_r"].to_numpy(np.float64), both[f"{c}_l"].to_numpy(np.float64)
        diff = float(np.nanmax(np.abs(a - b)))
        rho = float(pd.Series(a).corr(pd.Series(b), method="spearman")) if np.std(a) > 0 and np.std(b) > 0 else float("nan")
        mark = "" if diff < 1e-5 else ("  ← 다르다" if not np.isfinite(rho) or rho < 0.99 else "  (근소)")
        print(f"  {c:40s} 최대차 {diff:9.5f} · 순위상관 {rho:+.4f}{mark}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--market", default="KR", choices=["KR"])
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--features-only", action="store_true", help="FA 만 굽고 점수는 안 적는다(금고 창 굽기)")
    parser.add_argument("--signals-from", type=date.fromisoformat, default=None,
                        help="이 날짜 이후 세션만 be2 신호를 적는다(기본: 마지막 세션만)")
    parser.add_argument("--compare-panel", action="store_true", help="굽고 나서 판정 패널과 값을 견준다")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    store = Store(root=Path(args.root))
    market = Market(args.market)
    clock = LiveClock()
    policy = publication_policy(store, market, clock=clock)

    if args.start or args.end:
        if not (args.start and args.end) or args.day:
            print("--start 와 --end 를 같이 준다(--day 와는 같이 못 쓴다)", file=sys.stderr)
            return RC_BAD_ARGS
        sessions = list(trading_days(market, args.start, args.end))
        moments = {s: policy.for_session(s) for s in sessions}
    elif args.day:
        sessions = [args.day]
        moments = {args.day: policy.for_session(args.day)}
    else:
        from tools.run_daily import last_published

        moment = last_published(store, market, clock.now())
        if moment is None:
            print(f"{market} 공표된 세션을 찾지 못했다", file=sys.stderr)
            return RC_NO_SESSION
        day = fa_features.session_of(str(market), moment)
        sessions, moments = [day], {day: moment}
    if not sessions:
        print("세션이 없다", file=sys.stderr)
        return RC_NO_SESSION
    signals_from = args.signals_from or sessions[-1]
    print(f"=== BE2 {market} · 세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 신호 {signals_from}~ "
          f"{'(피처만)' if args.features_only else ''}{' (dry-run)' if args.dry_run else ''} ===", flush=True)
    worst = RC_OK
    for session in sessions:
        rc = score_session(store, market, session, moments[session], features_only=args.features_only,
                           write_signals=session >= signals_from, dry_run=args.dry_run)
        worst = max(worst, rc)
    if args.compare_panel:
        compare_panel(store, sessions)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
