"""미장 알파 진단 — docs/diag/us-alpha.md §1 대로 한 번 잰다.

    nice -n 10 .venv/bin/python tools/diag_us_alpha.py fetch-etf      # 스타일 ETF 일봉 → 연구 캐시 (LS g3204 조회 TR, sujung=Y)
    nice -n 10 .venv/bin/python tools/diag_us_alpha.py shadow         # ⑤ shadow 장부 점검(읽기만)

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/us-alpha/` 에 둔다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

OUT = Path("data/_diag/us-alpha")
#: 금고(research.holdout.start = 2026-07-01) 앞날까지만 받는다.
ETF_END = date(2026, 6, 30)
#: 스타일 ETF — SPY·MDY 는 창고에도 있다(검산용으로 같이 받는다). 섹터 11 은 `data/_diag/sector-rotation/us-spdr.parquet` 를 읽는다.
STYLE_ETFS = ("SPY", "RSP", "MDY", "IJR", "IWM", "QQQ")


def fetch_closes(symbols: tuple[str, ...], *, start: date = date(2021, 1, 1), end: date = ETF_END) -> pd.DataFrame:
    """g3204 일봉 종가(수정주가 sujung=Y) — `diag_sector_rotation.fetch_us` 와 같은 호출을 종목 목록만 바꿔 쓴다."""
    from quant_rl_trading.collectors import ls_us_source as ls
    from quant_rl_trading.settings import load_env

    load_env()
    src = ls.LsUsSource.from_env()
    rows = []
    for symbol in symbols:
        exchange = src.resolve_exchange(symbol)
        if exchange is None:
            raise SystemExit(f"{symbol}: 거래소 못 찾음")
        got: dict[str, float] = {}
        cursor = end
        while cursor >= start:
            payload = src.client.request_tr(ls.PATH_CHART, ls.TR_CHART, {f"{ls.TR_CHART}InBlock": {
                "sujung": "Y", "delaygb": "R", "comp_yn": "N", "keysymbol": f"{exchange}{symbol}", "exchcd": exchange,
                "symbol": symbol, "gubun": "2", "qrycnt": ls.MAX_ROWS_PER_CALL, "sdate": start.strftime("%Y%m%d"),
                "edate": cursor.strftime("%Y%m%d"), "cts_date": "", "cts_info": ""}})
            days = [r for r in payload.get(f"{ls.TR_CHART}OutBlock1") or [] if r.get("date")]
            if not days:
                break
            for r in days:
                got[r["date"]] = float(r["close"])
            oldest = datetime.strptime(min(r["date"] for r in days), "%Y%m%d").date()
            nxt = oldest - timedelta(days=1)
            if nxt >= cursor:
                break
            cursor = nxt
        rows += [{"day": datetime.strptime(k, "%Y%m%d").date(), "symbol": symbol, "close": v} for k, v in got.items()]
        print(f"{symbol}({exchange}) {len(got)}행 {min(got)}~{max(got)}", flush=True)
    return pd.DataFrame(rows)


def fetch_etf(_args: argparse.Namespace) -> int:
    frame = fetch_closes(STYLE_ETFS)
    frame = frame[frame["day"] <= ETF_END]
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT / "etf.parquet")  # invariant-allow: data-access — 진단 원본(LS g3204 연구 캐시)
    levels = frame.pivot(index="day", columns="symbol", values="close").sort_index()
    jumps = levels.pct_change().abs().stack()
    print(f"|일수익| > 15% 칸: {int((jumps > 0.15).sum())} · 빈 칸: {int(levels.isna().sum().sum())}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch-etf").set_defaults(func=fetch_etf)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
