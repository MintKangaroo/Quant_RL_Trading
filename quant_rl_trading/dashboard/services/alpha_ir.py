"""장부별 KODEX200 대비 IR — 트레이딩·학습 탭의 같은 표 (dashboard.md §4 "지수 대비 IR").

**여기서 수식을 들지 않는다.** 초과수익·IR·β·α·추적오차는 ``accounting/relative.py`` 가 낸다 — 종료 판정
도구(``tools/verify_exit_criterion.py``)와 실자금 투입 관문 1 이 부르는 바로 그 함수다(accounting.md §8.2). 이 모듈은
장부 목록을 돌며 창을 자르고, 표본이 모자란 칸의 숫자를 감출 뿐이다.

- 창 길이·표본 하한·리셋일은 ``dashboard.ir_*`` 설정(불변식 10). 없으면 숫자를 지어내지 않고 이유를 적는다.
- 장부는 회계가 적은 ``nav_daily.index_value`` 만 읽는다 — NAV 는 accounting 한 곳에서만.
- 결손일(장부에 없는 거래일)은 판정 도구와 같게 건너뛴 구간을 한 걸음으로 잇고, 날짜를 그대로 싣는다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quant_rl_trading.accounting import relative
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import ConfigNotFound

KST = ZoneInfo("Asia/Seoul")

WINDOWS_KEY = "dashboard.ir_windows_sessions"
MIN_SESSIONS_KEY = "dashboard.ir_min_sessions"
RESET_KEY = "dashboard.ir_reset_date"

#: 장부 목록 — 연구 창고(``data``) 아래 디렉터리 이름. 국장 장부만 KODEX200 대비가 뜻이 있다.
#: ``since`` 를 적은 장부는 그날부터가 '전체' 창이다 — 모의계좌는 종료 기준 창 시작(verify_exit_criterion.START)과 같게.
#: 나머지는 장부 첫 NAV 가 시작이다. 시작 전 장부(첫 NAV 없음)는 "장부 아직 없음" 한 줄로 나간다.
BOOKS: list[dict[str, str]] = [
    {"key": "paper", "name": "모의계좌", "ledger": "_paper", "market": "KR", "since": "2026-08-28"},
    {"key": "idxv6", "name": "지수+V6", "ledger": "_idxv6_shadow", "market": "KR"},
    {"key": "z2", "name": "Z2", "ledger": "_z2_shadow", "market": "KR"},
    {"key": "k200v6", "name": "K200 V6", "ledger": "_k200v6_shadow", "market": "KR"},
    {"key": "k200hmm", "name": "K200 HMM", "ledger": "_k200hmm_shadow", "market": "KR"},
    {"key": "be2", "name": "BE2", "ledger": "_be2_shadow", "market": "KR"},
    {"key": "w72", "name": "W72", "ledger": "_w72_shadow", "market": "KR"},
    {"key": "n24", "name": "N24", "ledger": "_n24_shadow", "market": "KR"},
    {"key": "p1b", "name": "P1-b′", "ledger": "_p1b_shadow", "market": "KR"},
    {"key": "shadow", "name": "shadow (국장+미장)", "ledger": "_shadow", "market": "MIXED"},
    {"key": "g1us", "name": "G1 미장", "ledger": "_g1us_shadow", "market": "US"},
]

NOT_APPLICABLE = {
    "US": "미장 장부 — KODEX200 대비는 뜻이 없다(관문은 국장만)",
    "MIXED": "국장·미장 슬리브가 한 장부에 섞여 있다 — KODEX200 대비로 재면 미장 수익이 초과로 섞인다",
}


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _stats(result: relative.Relative | None, *, need: int, sessions_hint: int) -> dict[str, Any]:
    """한 창의 숫자. 세션이 ``need`` 보다 적으면 IR·β·α·추적오차를 **내지 않는다**(표본 부족)."""
    if result is None:
        return {"sessions": sessions_hint, "need": need, "sufficient": False, "missing": [],
                "first": None, "last": None, "ours_total": None, "etf_total": None, "excess": None,
                "ir": None, "tracking_error": None, "beta": None, "alpha": None}
    sufficient = len(result.sessions) >= need
    return {
        "sessions": len(result.sessions),
        "need": need,
        "sufficient": sufficient,
        "missing": [d.isoformat() for d in result.missing],
        "first": _iso(result.sessions[0]),
        "last": _iso(result.sessions[-1]),
        # 누적 수익·초과는 사실이라 표본과 무관하게 싣는다. 비율 추정치만 감춘다.
        "ours_total": result.ours_total,
        "etf_total": result.etf_total,
        "excess": result.excess,
        "ir": result.ir if sufficient else None,
        "tracking_error": result.tracking_error if sufficient else None,
        "beta": result.beta if sufficient else None,
        "alpha": result.alpha if sufficient else None,
    }


def _settings(config: Store, as_of: datetime) -> tuple[list[int], int, date]:
    windows = [int(n) for n in config.config(WINDOWS_KEY, as_of=as_of)]
    minimum = int(config.config(MIN_SESSIONS_KEY, as_of=as_of))
    reset = date.fromisoformat(str(config.config(RESET_KEY, as_of=as_of)))
    return windows, minimum, reset


def alpha_ir(root: Path, *, config: Store, source: Store, as_of: datetime) -> dict[str, Any]:
    """``root``(연구 창고) 아래 장부마다 KODEX200 총수익 대비 IR. ``config`` 는 화면 임계치, ``source`` 는 ETF·분배금 가정."""
    try:
        windows, minimum, reset = _settings(config, as_of)
    except (ConfigNotFound, LookupError, ValueError, TypeError):
        return {"available": False, "books": [],
                "note": f"설정 {WINDOWS_KEY}·{MIN_SESSIONS_KEY}·{RESET_KEY} 가 창고에 없다 — 창을 지어내지 않는다"}
    try:
        annual = relative.distribution_yield(source, as_of=as_of)
    except (ConfigNotFound, LookupError, ValueError):
        return {"available": False, "books": [],
                "note": f"{relative.YIELD_KEY} 가 창고에 없다 — 분배금 보정 없이는 총수익 대비를 못 잰다"}

    indexes: dict[str, Any] = {}
    for book in BOOKS:
        path = root / book["ledger"]
        if book["market"] != "KR" or not (path / "curated" / "nav_daily").is_dir():
            continue
        try:
            series = relative.book_index(Store(root=path), as_of=as_of, lookback=None)
        except Exception:  # noqa: BLE001 — 장부 하나가 깨져도 표 전체가 죽지 않는다
            series = None
        indexes[book["key"]] = series

    starts = [date.fromisoformat(b["since"]) for b in BOOKS if b.get("since")]
    starts += [s.index[0] for s in indexes.values() if s is not None and not s.empty]
    today = as_of.astimezone(KST).date()
    lookback = (today - min(starts)).days + 10 if starts else 30
    etf = relative.etf_close(source, as_of=as_of, lookback=lookback)
    if etf.empty:
        return {"available": False, "books": [],
                "note": f"{BENCHMARK_ETF} 종가가 창고에 없다 — 대조군 없이는 IR 을 못 잰다"}
    through = max(etf.index)  # as_of 로 이미 걸렀다 — 마지막 KODEX200 종가 세션이 창의 끝이다

    columns = [{"key": str(n), "label": f"최근 {n}세션", "sessions": n} for n in windows]
    columns += [{"key": "all", "label": "전체", "sessions": None},
                {"key": "reset", "label": "리셋 뒤", "sessions": None, "since": reset.isoformat()}]
    rows = []
    for book in BOOKS:
        row: dict[str, Any] = {"key": book["key"], "name": book["name"], "ledger": f"data/{book['ledger']}",
                               "market": book["market"], "status": "ok", "reason": None,
                               "since": None, "windows": {}, "curve": []}
        if book["market"] != "KR":
            rows.append({**row, "status": "not_applicable", "reason": NOT_APPLICABLE[book["market"]]})
            continue
        ours = indexes.get(book["key"])
        if ours is None or ours.empty:
            rows.append({**row, "status": "no_book", "reason": "장부 아직 없음 — 첫 NAV 가 생기면 줄이 찬다"})
            continue
        since = date.fromisoformat(book["since"]) if book.get("since") else ours.index[0]
        window = trading_days(Market.KR, since, through) if since <= through else []
        overlap = relative.overlap(ours, etf, window)

        def measure(days: list[date], *, last: int | None = None, need: int) -> dict[str, Any]:
            result = relative.compare(ours, etf, window=days, annual_yield=annual, last=last)
            hint = min(len(relative.overlap(ours, etf, days)), last or 10**9)
            return _stats(result, need=need, sessions_hint=hint)

        # 롤링 창은 N 세션이 다 차야 그 창이다 — 22세션으로 '최근 60세션 IR' 을 내면 그건 전체 IR 이다.
        for n in windows:
            row["windows"][str(n)] = measure(window, last=n, need=max(n, minimum))
        row["windows"]["all"] = measure(window, need=minimum)
        if reset <= through:
            row["windows"]["reset"] = measure(trading_days(Market.KR, max(reset, since), through), need=minimum)
        else:
            row["windows"]["reset"] = None
        full = relative.compare(ours, etf, window=window, annual_yield=annual)
        row["since"] = _iso(overlap[0]) if overlap else _iso(since)
        row["curve"] = [[d.isoformat(), v] for d, v in full.curve] if full else []
        rows.append(row)

    return {
        "available": True,
        "note": None,
        "benchmark": BENCHMARK_ETF,
        "annual_yield": annual,
        "trading_days_per_year": relative.TRADING_DAYS_PER_YEAR,
        "min_sessions": minimum,
        "reset_date": reset.isoformat(),
        "reset_open": reset <= through,
        "through": through.isoformat(),
        "columns": columns,
        "books": rows,
    }
