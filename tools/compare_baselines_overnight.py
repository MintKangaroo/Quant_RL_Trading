"""야간 검증 — 리스크 패리티가 스코어 비례보다 MDD 를 낮추나 (§7).

    .venv/bin/python tools/compare_baselines_overnight.py \
        --start 2025-08-01 --end 2026-08-15

포트폴리오 구성(§1~§5)의 값어치를 실증하는 자리다. 같은 구간·같은 후보에
**baseline 만 갈아** 두 번 백테스트하고 성적을 나란히 적는다:

    score        스코어 비례 (현행 기본)
    risk_parity  섹터 리스크 기여 균등 + 스코어 틸트 + 제약 투영 (§3~§5)

§7 이 묻는 것: "리스크 패리티 비중이 스코어 비례 대비 MDD 를 낮췄는지."
세션마다 팩터 모델을 세우므로 risk_parity 쪽이 느리다 — **야간용**이다.

각 baseline 은 **자기 샌드박스**에서 돈다. baseline 값은 그 샌드박스의 config
테이블에 정정본으로 심어 갈아 낀다(실제 창고는 안 건드린다). 결과는
``logs/compare_baselines_<날짜>.md`` 로 남긴다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.backtest import loop  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from quant_rl_trading.store.tables import CONFIG_TABLE  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import WRITABLE  # noqa: E402

BASELINES = ("score", "risk_parity")

#: baseline 정정본의 발효 시점. 에포크(rev 0 기본값)보다 뒤라 valid_from 으로
#: 이긴다. 어떤 백테스트 as_of(2025+)보다 앞이라 구간 전체에서 유효하다.
OVERRIDE_FROM = datetime(2020, 1, 1, tzinfo=UTC)


#: 소스 config 를 통째로 읽을 때 쓰는 먼 미래 시점. 모든 행이 이 시점엔
#: 발효돼 있다(스케줄된 변경 포함).
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)

#: config 스키마 컬럼 — append 가 row_hash·ingest_run_id 는 따로 붙인다.
_CONFIG_COLS = ["entity_id", "valid_from", "observed_at", "source", "revision", "value_json"]


def _seed_config(store: Store, source: Path, baseline: str) -> None:
    """샌드박스 config 를 채운다 — **소스 전체를 복사한 뒤 baseline 만 덮는다.**

    config 를 writable 로 두면 소스와 링크가 끊겨 빈 테이블이 된다. 그대로
    두면 fee_kr 같은 나머지 설정이 통째로 사라진다. 그래서 소스 config 를
    다 읽어 옮기고, 그 위에 baseline 정정본(rev+1, 2020 발효)을 얹어 이긴다.
    """
    source_store = Store(root=source)
    existing = source_store.get(CONFIG_TABLE, as_of=FAR_FUTURE)
    rows = existing[_CONFIG_COLS].to_dict(orient="records")
    if rows:
        store.append(CONFIG_TABLE, rows, ingest_run_id="copy-source-config")

    # baseline 정정본. 소스의 baseline 최대 revision 보다 크게 잡아 이긴다.
    base_rows = existing[existing["entity_id"] == "allocator.baseline"]
    next_rev = int(base_rows["revision"].max()) + 1 if not base_rows.empty else 1
    store.append(
        CONFIG_TABLE,
        [{
            "entity_id": "allocator.baseline",
            "valid_from": OVERRIDE_FROM,
            "observed_at": OVERRIDE_FROM,
            "source": "backtest-override",
            "revision": next_rev,
            "value_json": f'"{baseline}"',
        }],
        ingest_run_id=f"override-baseline-{baseline}",
    )


#: 워크포워드 가중치를 가져올 기존 샌드박스. **실전 창고에서 가져오면 안 된다** —
#: 실전 가중치는 오늘 관측이라 과거 백테스트가 보면 미래를 훔친다
#: (`run_backtest.WRITABLE` 주석). 여기 것은 과거 시점으로 측정해 심은 것이다.
WEIGHTS_SOURCE = REPO_ROOT / "data" / "_backtest"

_WEIGHT_COLS = [
    "entity_id", "valid_from", "observed_at", "source", "revision",
    "analyst_version", "weight", "ic", "ic_threshold", "sample_days",
    "passed", "market",
]


def _seed_weights(store: Store, *, weights_from: Path) -> int:
    """Analyst 가중치를 샌드박스에 심는다. **없으면 매매가 0건이 된다.**

    `analyst_weights` 는 writable 이라 `layer.clear()` 가 비운다. 비워 두면
    합성 점수가 0 이 되고 후보가 0 건이 되어 **NAV 가 초기값에 고정된 채
    "MDD 0%" 라는 거짓 성적표**가 나온다(verify_m3 가 경고하는 함정).
    2026-08-23 실측: 신호 344만 행을 만들고도 매매 0건이었다.

    두 baseline 에 **같은 가중치**를 심는다 — 그래야 차이가 비중 산출에서만 온다.
    """
    source = Store(root=weights_from)
    frame = source.get("analyst_weights", as_of=FAR_FUTURE)
    if frame.empty:
        return 0
    rows = frame[_WEIGHT_COLS].to_dict(orient="records")
    return store.append("analyst_weights", rows, ingest_run_id="copy-walkforward-weights")


def _run_one(
    baseline: str, *, start: date, end: date, market: str, capital: float,
    warmup: int | None, sandbox_root: Path,
) -> loop.stats_module.Performance | None:
    source = build_store(None).root
    layer = overlay.build(
        root=sandbox_root, source=source, writable=WRITABLE | {CONFIG_TABLE}
    )
    layer.clear()
    store = Store(root=layer.root)
    _seed_config(store, source, baseline)
    seeded = _seed_weights(store, weights_from=WEIGHTS_SOURCE)
    if seeded == 0:
        # **여기서 멈춘다.** 가중치 0 이면 후보가 0 건이라 NAV 가 안 움직이고,
        # 그 성적표는 "완벽" 처럼 보인다. 조용히 도는 것이 제일 나쁘다.
        raise RuntimeError(
            f"Analyst 가중치를 못 심었다({WEIGHTS_SOURCE}). 이대로 돌리면 "
            "매매 0건에 MDD 0% 라는 거짓 성적표가 나온다"
        )
    print(f"[{baseline}] 가중치 {seeded}행 심음 ({WEIGHTS_SOURCE.name})", flush=True)

    probe = loop.snapshot_moment(
        store, start,
        as_of=datetime.combine(start, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=loop.SEOUL),
    )
    warm = warmup if warmup is not None else int(
        store.config("backtest.warmup_days", as_of=probe)
    )
    read_baseline = str(store.config("allocator.baseline", as_of=probe))
    print(f"[{baseline}] 시작 · config 확인 {read_baseline} · 워밍업 {warm} · {sandbox_root}",
          flush=True)
    if read_baseline != baseline:
        print(f"[{baseline}] ⚠️ config 가 {read_baseline} 로 읽힌다 — 정정본이 안 이겼다",
              flush=True)

    def show(day: loop.DayResult) -> None:
        if not day.warmup and day.as_of.day == 1:  # 매월 1일만 찍어 로그를 줄인다
            print(f"  [{baseline}] {day.as_of.date()} NAV {day.nav:,.0f} "
                  f"낙폭 {day.drawdown:.2%}", flush=True)

    result = loop.run(
        store, start=start, end=end, market=market, capital=capital,
        warmup_days=warm, on_day=show,
    )
    # **체결 수를 같이 본다.** 0 건이면 MDD 는 언제나 0% 라 성적표가 완벽해
    # 보인다 — 그건 성적이 아니라 아무것도 안 한 것이다(verify_m3 규약).
    trades = store.get("trades", as_of=FAR_FUTURE)
    n_trades = int(len(trades))
    print(f"[{baseline}] 끝 · 체결 {n_trades}건", flush=True)
    if n_trades == 0:
        print(f"[{baseline}] ⚠️ 매매 0건 — 이 성적표는 읽지 마라", flush=True)
    return result.performance, n_trades


def _report(results: dict[str, object], *, start: date, end: date,
            trades: dict[str, int], path: Path) -> None:
    lines = [
        f"# baseline 비교 — {start} ~ {end}",
        "",
        "§7 검증: 리스크 패리티가 스코어 비례 대비 MDD 를 낮췄나.",
        "",
        "| baseline | 체결 | 수익 | MDD | 변동성 | 수익/변동성 | 회전율 | 액션반영 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for baseline in BASELINES:
        perf = results.get(baseline)
        if perf is None:
            lines.append(f"| {baseline} | — 백테스트 실패 — |")
            continue
        n = trades.get(baseline, 0)
        lines.append(
            f"| {baseline} | {n} | {perf.total_return:+.2%} | {perf.max_drawdown:.2%} | "
            f"{perf.volatility:.2%} | {perf.return_over_vol:.2f} | {perf.turnover:.2f} | "
            f"{perf.action_reflection:.2%} |"
        )
    if any(trades.get(b, 0) == 0 for b in BASELINES):
        lines += ["", "> **매매 0건인 팔이 있다 — 이 표를 성적으로 읽지 마라.** "
                      "MDD 0% 는 완벽이 아니라 아무것도 안 한 것이다."]
    score, rp = results.get("score"), results.get("risk_parity")
    if score is not None and rp is not None:
        dd = rp.max_drawdown - score.max_drawdown
        lines += [
            "",
            f"**MDD 차이: {dd:+.2%}** "
            f"({'리스크 패리티가 낮췄다 ✓' if dd > 0 else '못 낮췄다'} "
            "— MDD 는 음수라 클수록(0 에 가까울수록) 낫다).",
            f"수익/변동성: score {score.return_over_vol:.2f} → "
            f"risk_parity {rp.return_over_vol:.2f}.",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트: {path}")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-08-01")
    parser.add_argument("--end", default="2026-08-15")
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--capital", type=float, default=500_000_000.0)
    parser.add_argument("--warmup", type=int, default=None)
    args = parser.parse_args(argv)

    load_env()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    results: dict[str, object] = {}
    trade_counts: dict[str, int] = {}
    for baseline in BASELINES:
        sandbox = REPO_ROOT / "data" / f"_backtest_{baseline}"
        try:
            perf, n_trades = _run_one(
                baseline, start=start, end=end, market=args.market,
                capital=args.capital, warmup=args.warmup, sandbox_root=sandbox,
            )
            results[baseline] = perf
            trade_counts[baseline] = n_trades
        except Exception as exc:  # 야간 무인 실행 — 한쪽이 죽어도 다른 쪽은 남긴다
            print(f"[{baseline}] 실패: {type(exc).__name__}: {exc}", flush=True)
            results[baseline] = None

    stamp = LiveClock().now().strftime("%Y%m%d")
    _report(results, start=start, end=end, trades=trade_counts,
            path=REPO_ROOT / "logs" / f"compare_baselines_{stamp}.md")
    return 0 if all(results.get(b) is not None for b in BASELINES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
