#!/usr/bin/env python
"""AR early 통과가 '본 적 없는 0 입력' 효과였나 — **기록용 진단**(판정을 바꾸지 않는다, research_trials 에 안 적는다).

    .venv/bin/python tools/diag_ar_filled.py --data /path/to/data

10/13 금고 early(7/1~9/30)에서 AR 이 두 시장 다 통과했다. 그런데 그때 내부자 원자료가 멈춰 있었다 —
`insider_trades`(DART) 마지막 행 8/28, `form4_trades` 마지막 행 7/1. 그래서 금고 창 G4·G7 열의 상당 부분이 0(순위 중앙)이었다.
얼린 AR 처리 모델은 학습 때 그런 "전부 0" 구간을 거의 못 봤다(국장 G4 2024-08~, 미장 G7 2024q1~ 로 학습).

이 도구는 같은 얼린 모델(`AR-{control,treat}-s{0,1,2}`, 해시 대조)과 같은 판정 틀(`vault_judge._ar_books`·`judge_ar`)로
**두 번** 잰다:

- `original` — 판정 때와 같은 월 조각(`data/_diag/ranker-sources/G4-KR-2026{07,08,09}` · `G7-US-…`)
- `filled`   — 10/13 에 따라잡아 넣은 원자료로 G4·G7 을 다시 굽는다(`data/_diag/insider-deep/ranker-sources-filled/`).
  따라잡은 행은 observed_at 이 **10/13(실제 받은 시각)** 이라 그 세션 시점의 게이트로는 안 보인다. 그래서 이 두 표만
  ``as_of = 10/13`` 로 읽고 **valid_from(접수일 18:00) ≤ 그 세션 개장**으로 거른다 — "제때 받았더라면" 의 반사실이다.
  공시는 그 날짜에 공개된 사실이라 미래 정보는 아니지만, **실시간으로 재현된 입력은 아니다**(기록에 그렇게 적는다).

날짜 상한: 패널·수익은 2026-09-30 까지. IC 는 h5 라벨(t+1→t+6)이 9/30 안에 끝나는 세션까지만(그 뒤 타깃은 NaN 으로 지운다)
— 원래 판정은 이 자르기 없이 쟀으므로 `original` 숫자가 기록된 판정과 조금 다를 수 있다. 둘 다 같은 자르기로 나란히 본다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import tools.trial_ranker_sources as trs  # noqa: E402
import tools.vault_judge as vj  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

SEAL_END = date(2026, 9, 30)
#: 따라잡기 적재가 끝난 뒤의 고정 시각(반사실 읽기의 as_of). 이 뒤에 들어온 행은 쓰지 않는다.
FILLED_AS_OF = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
INSIDER_TABLES = ("insider_trades", "form4_trades")


class FilledStore:
    """내부자 두 표만 ``FILLED_AS_OF`` 로 읽고 valid_from ≤ 원래 as_of 로 거른다. 나머지는 그대로 넘긴다."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def get(self, table: str, *, as_of: datetime, lookback=None, **kwargs):  # noqa: ANN001
        if table not in INSIDER_TABLES:
            return self._store.get(table, as_of=as_of, lookback=lookback, **kwargs)
        extra = (FILLED_AS_OF.date() - as_of.astimezone(UTC).date()).days
        lb = None if lookback is None else (lookback + extra if isinstance(lookback, int) else lookback + timedelta(days=extra))
        frame = self._store.get(table, as_of=FILLED_AS_OF, lookback=lb, **kwargs)
        if frame.empty:
            return frame
        return frame[frame["valid_from"] <= pd.Timestamp(as_of)]

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._store, name)


def label_cut(market: str) -> date:
    days = [d for d in trading_days(Market(market), SEAL_END - timedelta(days=30), SEAL_END)]
    return days[-7]


def coverage(kr: pd.DataFrame, us: pd.DataFrame) -> dict[str, float]:
    out = {}
    for market, frame, cols in (("KR", kr, trs.GROUPS["G4"]), ("US", us, trs.GROUPS["G7"])):
        nz = (frame[list(cols)].abs() > 1e-9).any(axis=1)
        out[market] = float(nz.mean())
        by_month = nz.groupby(pd.to_datetime(frame["session"]).dt.strftime("%Y-%m")).mean().round(4).to_dict()
        out[f"{market}_by_month"] = by_month
    return out


def run(mode: str, store: Store, data: Path) -> dict:
    trs.CACHE = data / "_diag" / ("ranker-sources" if mode == "original" else "insider-deep/ranker-sources-filled")
    use = store if mode == "original" else FilledStore(store)
    if mode == "original":
        for g, m in (("G4", "KR"), ("G7", "US")):
            for month in ("202607", "202608", "202609"):
                if not (trs.CACHE / f"{g}-{m}-{month}.parquet").exists():  # invariant-allow: data-access — 작업 캐시 존재 확인
                    raise SystemExit(f"원래 판정의 월 조각이 없다: {g}-{m}-{month} — 다시 굽지 않는다(원본 비교가 깨진다)")
    kr, us, _ = vj.pooled_panels(use, insider=True)  # type: ignore[arg-type]
    for frame, market in ((kr, "KR"), (us, "US")):
        if pd.to_datetime(frame["session"]).max().date() > SEAL_END:
            raise SystemExit(f"{market} 패널에 9/30 뒤 세션이 있다 — 봉인 위반, 멈춘다")
        cut = label_cut(market)
        frame.loc[frame["session"] > cut, "target"] = np.nan  # h5 라벨이 10/1 이후 가격에 닿는 세션은 IC 에서 뺀다
    cov = coverage(kr, us)
    expected = vj.frozen_hashes("AR")
    control = vj.FEATS + ["is_us"]
    treat = control + [c for g in vj.INSIDER for c in vj.GROUPS[g]]
    res = vj._ar_books(store, kr, us, {"control": control, "treat": treat},
                       {"control": "AR-control", "treat": "AR-treat"}, expected)
    lines, verdict = vj.judge_ar(res)
    return {"mode": mode, "coverage": cov, "lines": lines, "verdict": verdict,
            "res": {m: {a: {"ann": vj._mean(v["seeds"], "ann"), "seeds": [r["ann"] for r in v["seeds"]], "ic": v["ic"]}
                        for a, v in arms.items()} for m, arms in res.items()}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="data")
    parser.add_argument("--modes", default="original,filled")
    args = parser.parse_args(argv)
    data = Path(args.data)
    win = vj.windows()["early"]
    if win.end != SEAL_END:
        raise SystemExit(f"early 창 끝 {win.end} ≠ {SEAL_END}")
    vj.use_window(replace(win, cache=data / "_diag" / "vault-early"))
    vj.MODELS = data / "_diag" / "vault-reviews"
    store = Store(root=data)
    out = data / "_diag" / "insider-deep"
    results = []
    for mode in args.modes.split(","):
        print(f"\n=== AR 재채점 — {mode} ===", flush=True)
        r = run(mode, store, data)
        results.append(r)
        print(json.dumps(r["coverage"], ensure_ascii=False), flush=True)
        print("\n".join(r["lines"]), flush=True)
        print(f"판정(기록용): {r['verdict']}", flush=True)
    (out / "ar-filled.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
