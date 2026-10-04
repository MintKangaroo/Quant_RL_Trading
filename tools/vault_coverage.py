"""금고 창 캐시가 **창의 세션을 다 덮는지** — 판정 전에 한 번 본다. 값(점수·라벨·수익)은 읽지 않고 세션 열만 센다.

    .venv/bin/python tools/vault_coverage.py --window early

2026-10-04 리허설에서 찾은 빈칸 둘이 이 도구의 이유다:
- 국장 점수 7종이 61세션(9/30 없음) — 10/1 17:01 굽기 때 9/30 signals 가 아직 창고에 없었다(9/30~10/1 호스트 정지,
  따라잡기가 그 뒤에 적었다). 굽기 도구는 파일이 있으면 다시 굽지 않으므로 그대로 굳는다.
- 미장 점수·원피처·내부자 9월 조각이 57세션(9/22~9/30 없음) — `backfill_ic_history` 는 **라벨이 있는 세션만** 채점하고
  측정 시점이 달 말일(9/30 공표)이라 9/22 뒤 세션의 h5 라벨이 그 시점엔 없었다.
판정부(`vault_judge`)는 창을 캐시에서 읽으므로 이 빈칸이 있으면 **창이 조용히 짧아진다.** 빠진 세션이 있으면 rc 1.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from tools import vault_judge as vj  # noqa: E402

#: 판정부가 읽는 국장 점수(AQ 원천 ① ranker 포함)와 미장 점수 조각 이름 — `trial_pooled.load_kr/load_us` 와 같은 목록.
KR_SCORES = vj.KR_ANALYSTS
US_SCORES = ("chart", "event", "flow_us", "fundamental", "regime", "risk")
INSIDER_HOME = {"G4": "KR", "G7": "US"}


def _sessions(frame: pd.DataFrame) -> set[date]:
    return set(pd.to_datetime(frame["session"]).dt.date)


def _pickle_sessions(path: Path) -> set[date] | None:
    if not path.exists():
        return None
    return _sessions(pd.read_pickle(path)[["session"]])  # invariant-allow: data-access — 작업 캐시(세션 열만)


def _parquet_sessions(paths: list[Path]) -> set[date] | None:
    if not paths:
        return None
    return set().union(*(_sessions(pd.read_parquet(p, columns=["session"])) for p in paths))  # invariant-allow: data-access — 작업 캐시(세션 열만)


def check(win: vj.Window, *, insider_dir: Path = Path("data/_diag/ranker-sources")) -> list[str]:
    """(무엇, 빠진 세션) 문제 목록. 빈 목록이면 판정부가 창 전체를 본다."""
    vj.use_window(win)
    want = {"KR": set(trading_days(Market.KR, win.start, win.end)),
            "US": set(trading_days(Market.US, win.start, win.end))}
    items: list[tuple[str, str, set[date] | None]] = []
    for name in KR_SCORES:
        items.append((f"국장 점수 {name}", "KR", _pickle_sessions(vj.VAULT / f"scores-{name}-KR.pkl")))
    items.append(("국장 타깃 h5", "KR", _pickle_sessions(vj.VAULT / "targets-KR-h5.pkl")))
    items.append(("국장 거래가능 명단", "KR", _pickle_sessions(vj.VAULT / "tradable-KR.pkl")))
    for name in US_SCORES:
        items.append((f"미장 점수 {name}", "US", _parquet_sessions(sorted(vj.US_WORK.glob(f"scores-{name}-0*.parquet")))))  # invariant-allow: data-access — 작업 캐시 목록
    items.append(("미장 타깃", "US", _parquet_sessions(sorted(vj.US_WORK.glob("targets-*.parquet")))))  # invariant-allow: data-access — 작업 캐시 목록
    for market in ("KR", "US"):
        items.append((f"원피처 달력 {market}", market, _pickle_sessions(vj.RAW_DIRS[market] / f"calendar-{market}.pkl")))
        for name in vj.RAW_ANALYSTS[market]:
            items.append((f"원피처 {name} {market}", market,
                          _pickle_sessions(vj.RAW_DIRS[market] / f"features-{name}-{market}.pkl")))
    months = sorted({f"{d:%Y%m}" for d in want["KR"] | want["US"]})
    # 내부자 묶음은 제 시장만 센다(G4 = DART 국장, G7 = Form 4 미장) — 다른 시장 조각은 원래 0행이다(6차 규칙: 없는 시장은 0).
    for group, market in INSIDER_HOME.items():
        paths = [insider_dir / f"{group}-{market}-{m}.parquet" for m in months]  # invariant-allow: data-access — 작업 캐시 경로
        got = None if any(not p.exists() for p in paths) else _parquet_sessions(paths)
        items.append((f"내부자 {group} {market}", market, got))
    problems: list[str] = []
    for label, market, got in items:
        if got is None:
            problems.append(f"{label}: 파일 없음")
            continue
        lack = sorted(want[market] - got)
        if lack:
            problems.append(f"{label}: {len(lack)}세션 없음 ({lack[0]}~{lack[-1]})")
        else:
            print(f"  {label}: {len(want[market])}세션 ○", flush=True)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--window", default="early")
    args = parser.parse_args(argv)
    win = vj.windows()[args.window]
    print(f"=== 금고 {win.name} 캐시 세션 점검 · {win.start}~{win.end} ===", flush=True)
    problems = check(win)
    if problems:
        print("빠진 것:\n  " + "\n  ".join(problems), flush=True)
        return 1
    print("전부 덮는다 — 판정부가 창 전체를 본다.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
