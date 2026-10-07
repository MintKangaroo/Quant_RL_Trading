"""Z2 트랙 주간 비교 — docs/design/portfolio-construction.md "Z2 트랙". 시행이 아니다(판정 기준 없음, 기록만).

    .venv/bin/python tools/compare_z2_track.py

Z2 shadow(data/_z2_shadow, 국장만, 모의 체결)와 모의계좌(data/_paper, 국장만, 실제 모의 체결)를 **Z2 첫 세션부터 같은 창**으로 나란히 놓는다.
둘 다 국장 원화 장부라 TWR(nav_daily.index_value)을 그대로 비교할 수 있다 — 기존 KR shadow(data/_shadow)는 미장 달러 슬리브가 섞여
있어 같은 줄에 못 놓는다. 체결 방식이 다르다는 차이(시뮬 vs 실제 모의 체결)는 그대로 남는다 — 비교표 아래에 적는다.
KODEX200(069500)도 같은 창으로 적는다(판정 벤치마크와 같은 수집).

**지수+V6 shadow**(data/_idxv6_shadow, portfolio-construction.md "지수+V6 트랙", 2026-10-04)도 같은 모양으로 적는다 — 실자금 투입
관문 ② 의 "KODEX200 + V6" 대용을 매매 기록으로 남긴 장부다. 그 장부의 창에는 계산 대용(`modelops.exposure_effect`, 모의계좌의 노출
기록 × 지수 일수익 − 비용)과 분배금 보정(장부는 분배금을 안 받는다 — `benchmark.kodex200_distribution_yield_annual`)을 함께 적는다.

**P1-b′ shadow**(data/_p1b_shadow, docs/protocols/p1b-prime-2026-10.md, 2026-10-08)는 N24 shadow(현행 규칙)와 같은 창 수익 차와
매도 회전을 함께 적는다 — 확인 지표는 회전이다(판정은 금고 second 창).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402

PAPER = ("모의계좌", "data/_paper")
#: 비교할 shadow 장부 — 장부마다 자기 첫 세션부터의 창으로 모의계좌·KODEX200 과 나란히 놓는다.
TRACKS = {"Z2 shadow": "data/_z2_shadow", "지수+V6 shadow": "data/_idxv6_shadow", "P1-b′ shadow": "data/_p1b_shadow"}
#: P1-b′ 의 비교 상대 — 현행 규칙 24종목 장부(B0 의 실전형, 같은 정보 시점). docs/protocols/p1b-prime-2026-10.md
N24 = ("N24 shadow", "data/_n24_shadow")


def _book(sandbox: str) -> Store:
    return Store(root=overlay.build(root=Path(sandbox), source=build_store(None).root, writable=JOURNAL).root)


def book_series(sandbox: str, now) -> pd.Series:
    store = _book(sandbox)
    nav = store.get("nav_daily", as_of=now, lookback=120, columns=["valid_from", "index_value", "nav"])
    if nav.empty:
        return pd.Series(dtype=float)
    nav["day"] = pd.to_datetime(nav["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    return nav.sort_values("valid_from").groupby("day")["index_value"].last()


def etf_series(now) -> pd.Series:
    # 판정 도구(verify_exit_criterion)와 같은 자리 — ETF 가격은 indices 표에 BENCHMARK_ETF 로 들어 있다.
    prices = build_store(None).get("indices", as_of=now, lookback=120, entity=BENCHMARK_ETF, columns=["valid_from", "close"])
    if prices.empty:
        return pd.Series(dtype=float)
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    return prices.sort_values("valid_from").groupby("day")["close"].last()


def stats(s: pd.Series) -> tuple[float, float]:
    if len(s) < 2:
        return float("nan"), float("nan")
    rel = s / s.iloc[0]
    return float(rel.iloc[-1] - 1.0), float((rel / rel.cummax() - 1.0).min())


def report(name: str, track: pd.Series, others: dict[str, pd.Series], now: datetime) -> None:
    start = track.index[0]
    print(f"=== {now.astimezone(ZoneInfo('Asia/Seoul')):%Y-%m-%d %H:%M} {name} 비교 · 창 {start}~{track.index[-1]} ({len(track)}세션) ===")
    for label, s in {name: track, **others}.items():
        window = s[s.index >= start]
        ret, mdd = stats(window)
        print(f"  {label:14s} 수익 {ret:+.2%} · MDD {mdd:.2%} · 세션 {len(window)}")
    tr, _ = stats(track)
    paper = others[PAPER[0]]
    pr, _ = stats(paper[paper.index >= start])
    print(f"  {name} − 모의계좌 {tr - pr:+.2%}p (체결 방식이 다르다: shadow 는 시뮬, 모의계좌는 LS 모의투자 실제 체결 — 체결 비용 차이가 섞인다)")


def sell_turnover(sandbox: str, track: pd.Series, now: datetime) -> tuple[float, float]:
    """창 안 매도 체결 금액 합 / 평균 NAV — (창 누적, 연환산). 매도만 세므로 첫 구성(매수뿐)은 안 들어간다."""
    store = _book(sandbox)
    trades = store.get("trades", as_of=now, lookback=120, columns=["valid_from", "side", "quantity", "price"])
    nav = store.get("nav_daily", as_of=now, lookback=120, columns=["valid_from", "nav"])
    if nav.empty or len(track) < 2:
        return float("nan"), float("nan")
    start = pd.Timestamp(track.index[0], tz="Asia/Seoul")
    sells = trades[(trades["side"] == "sell") & (pd.to_datetime(trades["valid_from"]) >= start)] if not trades.empty else trades
    sold = float((sells["quantity"] * sells["price"]).sum()) if not sells.empty else 0.0
    cumulative = sold / float(nav["nav"].mean())
    return cumulative, cumulative * 252 / max(len(track) - 1, 1)


def p1b_lines(track: pd.Series, now: datetime) -> None:
    """P1-b′ 장부를 N24 장부와 같은 창으로 — 수익 차와 회전(확인 지표: N24 의 절반 이하인가)."""
    n24 = book_series(N24[1], now) if Path(N24[1], "curated").is_dir() else pd.Series(dtype=float)
    n24 = n24[n24.index >= track.index[0]]
    tr, _ = stats(track)
    nr, _ = stats(n24)
    print(f"  P1-b′ − N24 {tr - nr:+.2%}p (같은 시뮬 체결·같은 정보 시점 — 차이는 구성: N100·R20·θ1.0 대 N24·R10)")
    for label, path, series in (("P1-b′", TRACKS["P1-b′ shadow"], track), ("N24", N24[1], n24)):
        cumulative, annual = sell_turnover(path, series, now) if len(series) >= 2 else (float("nan"), float("nan"))
        print(f"  {label:6s} 매도 회전 창 {cumulative:.1%} · 연환산 {annual:.1f}회 (첫 구성 제외 — 짧은 창의 연환산은 거칠다)")


def proxy_lines(track: pd.Series, now: datetime) -> None:
    """지수+V6 장부 옆에 계산 대용과 분배금 보정을 적는다. 계산은 관문 문서가 가리키는 그 함수 그대로다."""
    from quant_rl_trading.modelops.exposure_effect import exposure_effect

    warehouse = build_store(None)
    paper = Store(root=overlay.build(root=Path(PAPER[1]), source=warehouse.root, writable=JOURNAL).root)
    result = exposure_effect(warehouse, as_of=now, ledger=paper, sessions=max(len(track) - 1, 1))
    if result.get("sessions"):
        print(f"  계산 대용(모의계좌 노출 기록 × {result['index'].split(':')[-1]} 일수익 − 비용) {result['cum_applied']:+.2%} · "
              f"MDD {result['mdd_applied']:+.2%} · {result['start']}~{result['end']} {result['sessions']}세션 "
              f"(정렬: 세션 d 결정 → d 종가→d+1 종가 수익 — 장부와 하루 어긋날 수 있다)")
    else:
        print(f"  계산 대용: 못 냄 ({result.get('reason', '자료 없음')})")
    yield_annual = float(warehouse.config("benchmark.kodex200_distribution_yield_annual", as_of=now))
    days = (track.index[-1] - track.index[0]).days
    print(f"  분배금 보정: 장부는 KODEX200 분배금을 안 받는다 — 연 {yield_annual:.1%} 가정이면 이 창 {yield_annual * days / 365:+.2%}p 를 더해 읽는다")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    now = LiveClock().now()
    others = {PAPER[0]: book_series(PAPER[1], now), "KODEX200": etf_series(now)}
    for name, path in TRACKS.items():
        track = book_series(path, now) if Path(path, "curated").is_dir() else pd.Series(dtype=float)
        if len(track) < 2:
            print(f"{now:%Y-%m-%d} {name} 세션 {len(track)}개 — 아직 비교할 창이 없다")
            continue
        report(name, track, others, now)
        if path.endswith("_idxv6_shadow"):
            proxy_lines(track, now)
        if path.endswith("_p1b_shadow"):
            p1b_lines(track, now)
    print("  기록만 — 판정 기준이 없다. Z2 는 20세션 뒤 사용자와 모의계좌 전환을 논의한다(portfolio-construction.md). "
          "지수+V6 는 관문 ② 를 매매 기록으로 읽는 보조다(live-capital-entry-2026-11.md). "
          "P1-b′ 는 판정이 금고 second 창(11/23)이라 이 장부로 판정하지 않는다(p1b-prime-2026-10.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
