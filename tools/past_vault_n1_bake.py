"""과거 금고 N1 점수 굽기 — 순주식발행 `nsi_12m_c`. **상장주식수·기업행위 배율만 읽는다**(수익·라벨을 계산·출력하지 않는다).
등록 `docs/protocols/past-vault-n1-2026-10.md`(초안).

    .venv/bin/python tools/past_vault_n1_bake.py            # → data/_past_vault/_bake/n1.pkl (session · entity_id · r_hat = nsi_12m_c)
    .venv/bin/python tools/past_vault_n1_bake.py --check    # 봉인 창고 market_stats 의 metric 별 행 수만 센다

- 피처 정의는 `nsi_panel` 하나 — 판정 리허설(`tools/past_vault_judge_n1.py --rehearse`, 본 창고)도 같은 함수를 부른다.
- 점수 = 그 세션 **마감까지**의 값(늦춤 없음). 하루 늦춤은 판정 쪽 `vault_judge.tsfm_lagged`(결정 d 에 d 앞 세션 점수)가 한다 — TG·TF 와 같다.
- 종가는 **세션 축**(시세 0 인 날 제거)을 얻으려고만 읽는다. 수익을 만들지 않는다.
- 산출은 봉인 창고 안(`data/_past_vault/_bake`)에만 둔다. 출력은 행 수·세션 수뿐.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

ROOT = Path("data/_past_vault")
OUT = ROOT / "_bake"
START, END = date(2009, 12, 1), date(2021, 8, 10)
FIRST_SCORE = date(2010, 12, 1)
#: 피처 상수 — 등록 문서 '피처 상수' 표와 같은 값. 바꾸면 새 등록이다. 탐색 진단과는 EVENT_PAD(40→120)·급변 경계(>→≥)가 다르다.
WINDOW = 252          # 12개월 세션
#: 사건 창 여유 — 가격 배율(권리락·배당락) 뒤 원 주식수가 바뀌기까지(신주 상장)의 지연. 본 창고 2021-08~2026-06 사건 1,035건을
#: ±150세션까지 다시 잰 값(`tools/diag_n1_issuance.py --delays`, 날짜·주식수만): 뒤(+) 532건의 99% 분위 113 · 최대 150 → 120(뒤 지연의 99.2%).
#: 처음 40 은 ±40 에서 잘린 측정이었다(독립 검토 10/11).
EVENT_PAD = 120
EVENT_LOG = 0.03      # |log f| > 3% = 기업행위 사건
#: 하루 **2배 이상**(≥) 원 주식수 급변. '초과' 로 두면 1:1 무상증자(정확히 2배)가 부동소수 오차에 따라 걸리거나 빠진다(독립 검토 10/11).
JUMP_LOG = math.log(2) - 1e-9
FFILL = 5             # 주식수 결측을 직전 값으로 채우는 최대 세션


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    out, y = [], start.year
    while date(y, 1, 1) <= end:
        a, b = max(start, date(y, 1, 1)), min(end, date(y, 12, 31))
        out.append((a, b))
        y += 1
    return out


def read_inputs(store: Store, start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(상장주식수 S, 기업행위 배율 f) 넓은 표, 행 = 시세가 있는 세션. 연 단위 조각으로 읽는다(메모리)."""
    as_of = datetime.combine(end, time(23, 59), tzinfo=UTC)
    s_parts, f_parts, days = [], [], []
    for a, b in _year_chunks(start, end):
        until = datetime.combine(b, time(23, 59), tzinfo=UTC)
        lb = (end - a).days + 1   # lookback 은 as_of 기준 valid_from 하한이다(until 기준이 아니다) — store/reader._valid_from_floor
        p = read_prices(store, as_of=as_of, until=until, lookback=lb, columns=["close", "adj_factor"], adjusted=False, market="KR")
        p = p[p["entity_id"].str.match(r"^KR:\d{6}$")]
        p["day"] = pd.to_datetime(p["valid_from"]).dt.date
        days += sorted(p["day"].unique())
        ev = p[p["adj_factor"].notna()]
        if len(ev):
            f_parts.append(ev.pivot_table(index="day", columns="entity_id", values="adj_factor", aggfunc="last"))
        m = store.get("market_stats", as_of=as_of, until=until, lookback=lb, market="KR", columns=["entity_id", "valid_from", "metric", "value"])
        m = m[(m["metric"] == "shares") & m["entity_id"].str.match(r"^KR:\d{6}$")]
        m["day"] = pd.to_datetime(m["valid_from"]).dt.date
        s_parts.append(m.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").astype("float64"))
    idx = sorted(set(days))
    S = pd.concat(s_parts).groupby(level=0).last()
    cols = S.columns
    S = S.reindex(index=idx)
    F = pd.concat(f_parts).groupby(level=0).last().reindex(index=idx, columns=cols) if f_parts else pd.DataFrame(np.nan, index=idx, columns=cols)
    return S, F


def nsi_panel(S: pd.DataFrame, F: pd.DataFrame, stats: dict | None = None) -> pd.DataFrame:
    """nsi_12m_c — 행 k 의 값은 세션 k 마감까지의 자료(늦춤 없음). 등록 문서 '피처' 절의 식 그대로.

    ``stats`` 를 주면 사건 창·급변 제외 개수를 적는다(값·수익이 아니라 개수만).
    """
    logS = np.log(S.where(S > 0)).ffill(limit=FFILL)
    logf = np.log(F.where(F > 0))
    L = logS + logf.fillna(0.0).cumsum()
    E = (logf.abs() > EVENT_LOG).astype(float)
    J = (logS.diff().abs() >= JUMP_LOG).astype(float)
    dirty = (E.rolling(WINDOW + EVENT_PAD, min_periods=1).sum() > 0) | (J.rolling(WINDOW, min_periods=1).sum() > 0)
    raw = L - L.shift(WINDOW)
    if stats is not None:
        has = raw.notna()
        stats["raw"] = int(has.sum().sum())
        stats["event"] = int((has & (E.rolling(WINDOW + EVENT_PAD, min_periods=1).sum() > 0)).sum().sum())
        stats["jump"] = int((has & (J.rolling(WINDOW, min_periods=1).sum() > 0)).sum().sum())
        stats["dirty"] = int((has & dirty).sum().sum())
    return raw.where(~dirty)


def to_long(panel: pd.DataFrame, first: date) -> pd.DataFrame:
    panel = panel.loc[[d for d in panel.index if d >= first]]
    long = panel.stack().rename("r_hat").reset_index()
    long.columns = ["session", "entity_id", "r_hat"]
    return long


def file_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(store: Store) -> int:
    """봉인 창고 market_stats 의 metric 별 행 수(2015 한 해 · 2020 한 해) — 값·수익은 안 본다."""
    for y in (2010, 2015, 2020):
        until = datetime(y, 12, 31, 23, 59, tzinfo=UTC)
        m = store.get("market_stats", as_of=datetime.combine(END, time(23, 59), tzinfo=UTC), until=until, lookback=(END - date(y, 1, 1)).days + 1,
                      market="KR", columns=["entity_id", "metric"])
        print(f"{y}: " + " · ".join(f"{k} {v:,}행" for k, v in m["metric"].value_counts().items()), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    store = Store(root=ROOT)
    if args.check:
        return check(store)
    out = OUT / "n1.pkl"
    if out.exists():
        print(f"{out} 있음 — 건너뛴다")
        return 0
    S, F = read_inputs(store, START, END)
    print(f"입력: 세션 {len(S)} · 종목 {S.shape[1]} · 주식수 값 {int(S.notna().sum().sum()):,} · 배율 사건 {int(F.notna().sum().sum()):,}", flush=True)
    st: dict = {}
    frame = to_long(nsi_panel(S, F, st), FIRST_SCORE)
    print(f"제외(전 기간·전 종목, 계산 가능한 종목-세션 {st['raw']:,} 중): 사건 창 {st['event']:,} · 급변 {st['jump']:,} · "
          f"합 {st['dirty']:,}({st['dirty'] / max(st['raw'], 1):.1%})", flush=True)
    if frame.empty:
        print("점수 0행 — 쓰지 않는다", file=sys.stderr)
        return 4
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(out)
    per = frame.groupby("session").size()
    print(f"→ {out} · {len(frame):,}행 · 세션 {frame['session'].nunique()}({frame['session'].min()}~{frame['session'].max()})"
          f" · 세션당 종목 중앙값 {int(per.median())} · sha256 {file_sha256(out)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
