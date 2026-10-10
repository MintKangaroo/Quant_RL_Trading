"""과거 금고 백필 — 국장 2010-01-04 ~ 2021-08-10 일별매매를 **별도 창고 `data/_past_vault`** 에 받는다(사용자 10/10 "백필 + 봉인").

    .venv/bin/python tools/backfill_past_vault.py            # 이어받기(이미 적재한 세션은 건너뛴다)
    .venv/bin/python tools/backfill_past_vault.py --check    # 적재 세션 수만 센다(수익을 계산하지 않는다)

**봉인**: 본 창고(`data/`)에 안 넣는다 — 연구 도구는 전부 본 창고만 읽으므로 실수로 열 수 없다. 이 창고를 여는 것은 등록이 고정된 판정
도구(`docs/protocols/past-vault-2026-10.md`)뿐이다. 이 도구는 수익·라벨·IC 를 계산하거나 출력하지 않는다(행 수만).

- 원천: KRX Open API `stk_bydd_trd`·`ksq_bydd_trd`(본 수집기 `KrxOpenApi._call` 그대로). 그날 상장 전 종목 스냅샷이라 **나중에 상폐된 종목도 들어 있다**.
- prices: 원주가 OHLCV·거래대금. `adj_factor` = 그 세션 기업행위 배율 = (종가 − 전일대비) / 전일 종가 — 1 과 다를 때만(KRX 기준가가 권리락·분할을 반영한다).
- market_stats: 상장주식수·시가총액(`krx_openapi.normalize_shares`, 본 수집기와 같은 형식).
- observed_at = 세션 마감 + 공표 지연(`publication_policy`, 본 백필과 같은 규칙). ingest_run_id = `past-vault-krx-<날짜>` (이어받기).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_rl_trading.collectors.krx_openapi import (
    BOARDS,
    TRADE_FIELDS,
    KrxOpenApi,
    normalize_shares,
)
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.collectors.publication import publication_policy
from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

ROOT = Path("data/_past_vault")
START, END = date(2010, 1, 4), date(2021, 8, 10)
SOURCE = "krx_openapi_past_vault"


def num(x: object) -> float | None:
    try:
        s = str(x).replace(",", "").strip()
        return float(s) if s not in ("", "-") else None
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--max-days", type=int, default=0, help="이번 실행에서 받을 최대 세션 수(0 = 끝까지)")
    args = ap.parse_args(argv)
    ROOT.mkdir(parents=True, exist_ok=True)
    store = Store(root=ROOT)
    days = list(trading_days(Market.KR, START, END))
    done = [d for d in days if store.ingest_run_recorded("prices", f"past-vault-krx-{d}")]
    print(f"과거 금고 {START}~{END} · 달력 거래일 {len(days)} · 적재 {len(done)}", flush=True)
    if args.check:
        return 0
    api = KrxOpenApi(api_key=os.environ.get("KRX_OPENAPI_KEY", ""))
    clock = LiveClock()
    policy = publication_policy(Store(root=Path("data")), Market.KR, clock=clock)   # 공표 지연 설정은 본 창고 config(봉인 창고엔 config 가 없다)
    prev_close: dict[str, float] = {}
    todo = [d for d in days if d not in set(done)]
    if done:
        # 이어받기: 마지막 적재 세션의 종가를 기억해야 첫 세션의 조정계수를 낼 수 있다 — 창고에서 읽는다(봉인 창고 안, 수익 계산 아님).
        last = max(done)
        frame = read_prices(store, as_of=clock.now(), lookback=10, until=datetime(last.year, last.month, last.day, 23, tzinfo=UTC),
                            columns=["close"], market="KR")
        frame = frame[pd.to_datetime(frame["valid_from"]).dt.date == last]
        prev_close = dict(zip(frame["entity_id"], frame["close"].astype(float), strict=True))
    n_done, started = 0, clock.now()
    for d in todo:
        if args.max_days and n_done >= args.max_days:
            break
        raw = []
        for board, path in BOARDS.items():
            if board == "KONEX":
                continue
            raw += [(board, r) for r in api._call(path, d)]
        if not raw:
            print(f"{d}: KRX 0행 — 휴장으로 본다", flush=True)
            continue
        obs = policy.for_session(d)
        vf = datetime(d.year, d.month, d.day, tzinfo=UTC)
        prices, stats, closes = [], [], {}
        for board, r in raw:
            code = str(r.get("ISU_CD") or "").strip()
            close = num(r.get("TDD_CLSPRC"))
            if not code or close is None or close <= 0:
                continue
            ent = f"KR:{code}"
            factor = None
            chg = num(r.get("CMPPREVDD_PRC"))
            pc = prev_close.get(ent)
            if chg is not None and pc:
                f = (close - chg) / pc
                if abs(f - 1.0) > 1e-4 and f > 0:
                    factor = f
            prices.append({"entity_id": ent, "valid_from": vf, "observed_at": obs, "source": SOURCE, "market": "KR",
                           "open": num(r.get("TDD_OPNPRC")), "high": num(r.get("TDD_HGPRC")), "low": num(r.get("TDD_LWPRC")),
                           "close": close, "volume": num(r.get("ACC_TRDVOL")), "value": num(r.get("ACC_TRDVAL")), "adj_factor": factor})
            mapped = {name: r.get(field) for field, name in TRADE_FIELDS.items()}
            mapped["board"] = mapped.get("board") or board
            stats += normalize_shares([mapped], market="KR", valid_from=vf, observed_at=obs)
            closes[ent] = close
        store.append("prices", prices, ingest_run_id=f"past-vault-krx-{d}", source=SOURCE)
        if stats:
            store.append("market_stats", stats, ingest_run_id=f"past-vault-krx-stats-{d}", source=SOURCE)
        prev_close.update(closes)
        n_done += 1
        if n_done % 50 == 0:
            rate = n_done / max((clock.now() - started).total_seconds(), 1.0)
            print(f"{d} · 이번 실행 {n_done}세션 · {rate * 60:.1f}세션/분 · 남은 {len(todo) - n_done}", flush=True)
    api.close()
    print(f"끝 — 이번 실행 {n_done}세션", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
