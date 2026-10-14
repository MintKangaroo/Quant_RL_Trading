#!/usr/bin/env python
"""새 정보 일곱(N1~N7)을 **랭커 재료 파일**로 굽는다 — 공통 평가 사다리(`eval_ladder.py --mode feature`)의 입력.
사용자 10/13 "다 돌려" · 리드 지시 2026-10-14. 진단 도구다 — 창고에 쓰지 않는다(읽기는 전부 `store.get`).

    # 0) 시장 바탕(시총·상장주식수·거래대금·수정종가·명단 축)을 굽는다 — 가볍다(분기별 읽기)
    .venv/bin/python tools/build_n_materials.py base
    # 1) 공시 원문 → 사건 표(파서 `collectors/dart_capital_events.py`·`dart_supply_contract.py`)
    .venv/bin/python tools/build_n_materials.py events
    # 2) 재료 파일 — 사다리 점수 폴더의 N1~N7 (+ 스타일 잔차 부분 IC 보고)
    .venv/bin/python tools/build_n_materials.py features --n N2,N4,N5,N7
    .venv/bin/python tools/build_n_materials.py features --n N6        # 대차잔고·보호예수 반환(창고 stock_lending·lockup_returns)
    .venv/bin/python tools/build_n_materials.py style --n N2      # FW 부분 IC(재료·정답 둘 다 스타일 잔차) — 가볍다

## 규칙(10/13 확정 교훈 — 계산 전에 고정)

- **크기로 정규화**(금액/시총·주식수/상장주식수), **수준보다 변화·지속**, 사건은 **경과 세션 감쇠**(반감기 H, `_decay`),
  **결측은 0 이 아니라 `m_*` 표지 열**(사건 부재는 사실이라 0, 자료 부재는 표지), 순위 정규화는 사다리가 한다(세션 안 rank-gauss).
- 대표 열 `pred` 의 부호는 **사전 가설 방향**(클수록 좋다)으로 여기서 고정한다 — 결과를 보고 뒤집지 않는다(`SIGNS`).
- **시각**: 사건은 관측 시각(공시 목록 첫 관측 = 접수일 18:00 KST 안팎)이 세션 t 의 결정 시각(t 16:00 UTC = t+1 01:00 KST,
  `trial_ranker_kit` 관례) 이하일 때 t 부터 보인다. 원문을 나중에 받은 것은 새 사실이 아니다(`build_supply_contracts` 규칙).
- **봉인선**: 세션 ≤ 2026-09-30(`eval_ladder.DATE_CAP`), 시장 자료 as_of = `CAP_AS_OF`. 원문(raw_path)은 그 뒤에 받았어도
  공시 시각이 봉인선 안이면 쓴다(내용은 접수 시각에 공개된 것이다).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import eval_ladder as el  # noqa: E402

OUT = Path("data/_diag/n-materials")  # invariant-allow: data-access — 연구 캐시(창고 아님)
BASE = OUT / "base"
EVENTS = OUT / "events.parquet"  # invariant-allow: data-access — 연구 캐시(사건 표)
SCORES = el.SCORES
#: 바탕 자료 시작 — 공시 목록(2021-08-17~)과 같게. 창 250세션이 판정 시작(2021-11-10)에 반쯤 찬다(한계로 적는다).
START = date(2021, 8, 1)
KST = "Asia/Seoul"

#: 대표 열 부호(사전 가설, 2026-10-14 고정). +1 = 값이 클수록 좋다.
SIGNS = {
    "N1": "−(잠재+실제 희석, 반감기 120세션) — 희석 물량은 숏 다리(Pontiff·Woodgate 2008, Yoon·Son 2014)",
    "N2": "+(연기금 60세션 순매수/시총) — 지속 기관 매수(외국인 60일의 확장)",
    "N3": "−(최대주주 담보 주식 / 발행주식총수, 최근 공시 상태) — 반대매매·지배권 위험",
    "N4": "+(K200 편입 압력 − 편출 압력, 정기변경 전 40세션 가중) — 기계적 수요(롱)",
    "N5": "+(공급계약 매출액 대비 %, 반감기 40세션 합) — G13 크기형",
    "N7": "+(실제 자사주 취득 금액/시총, 반감기 60세션) — 이행된 매입만",
    "N6": "−(대차잔고/상장주식수의 20세션 변화) — 숏 수요 증가는 부진(Desai 외 2002, Cohen·Diether·Malloy 2007) · 2026-10-14 결과 전 고정",
}


# --------------------------------------------------------------------------- 공통


def _store():  # type: ignore[no-untyped-def]
    from quant_rl_trading.store import Store

    return Store(root=Path("data"))


def decision_cutoffs(sessions: Sequence[date]) -> np.ndarray:
    """세션 t 의 결정 시각 = t 16:00 UTC(= t+1 01:00 KST). ns 정수 배열."""
    return np.array([pd.Timestamp(datetime.combine(d, time(16, 0), tzinfo=UTC)).value for d in sessions], dtype=np.int64)


def arrival_index(observed: pd.Series, sessions: Sequence[date]) -> np.ndarray:
    """관측 시각 → 처음 보이는 세션 인덱스(len(sessions) 면 창 밖)."""
    # 단위를 ns 로 맞춘다 — 창고(`store.get`)는 datetime64[us] 로 돌려줘 int64 가 µs 가 되고, 결정 시각(ns)과 견주면 전부 세션 0 이 됐다
    # (2026-10-14 N6 첫 굽기: 대차 값 0%·반환 0 아님 0%). 사건 표(events.parquet)는 ns 라 N1·N3·N5·N7 은 닿지 않았다.
    obs = pd.to_datetime(observed, utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    return np.searchsorted(decision_cutoffs(sessions), obs, side="left")


def event_wide(ev: pd.DataFrame, value: str, sessions: list[date], entities: pd.Index) -> pd.DataFrame:
    """사건 → 도착 세션 × 종목 넓은 표(같은 칸은 합). 창 밖·명단 밖은 버린다."""
    pos = arrival_index(ev["observed_at"], sessions)
    m = (pos < len(sessions)) & ev["entity_id"].isin(entities).to_numpy() & ev[value].notna().to_numpy()
    w = np.zeros((len(sessions), len(entities)), dtype=np.float64)
    col = entities.get_indexer(ev.loc[m, "entity_id"])
    np.add.at(w, (pos[m], col), ev.loc[m, value].to_numpy(dtype=np.float64))
    return pd.DataFrame(w, index=sessions, columns=entities)


def _decay(w: pd.DataFrame, half_life: float) -> pd.DataFrame:
    """경과 세션 감쇠 합 s_t = x_t + λ s_{t-1}, λ = 2^(−1/H)."""
    from scipy.signal import lfilter

    lam = 2.0 ** (-1.0 / half_life)
    return pd.DataFrame(lfilter([1.0], [1.0, -lam], w.to_numpy(), axis=0), index=w.index, columns=w.columns)


def _roll(w: pd.DataFrame, n: int) -> pd.DataFrame:
    return w.rolling(n, min_periods=1).sum()


def _last_state(ev: pd.DataFrame, value: str, sessions: list[date], entities: pd.Index, *, max_age: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """사건의 **마지막 값**(상태)과 경과 세션 — max_age 넘으면 결측. 같은 세션 여러 건은 마지막 관측."""
    pos = arrival_index(ev["observed_at"], sessions)
    e = ev.assign(_pos=pos)
    e = e[(e["_pos"] < len(sessions)) & e["entity_id"].isin(entities) & e[value].notna()]
    e = e.sort_values(["_pos", "observed_at"]).groupby(["_pos", "entity_id"])[value].last().reset_index()
    val = pd.DataFrame(np.nan, index=range(len(sessions)), columns=entities)
    age = pd.DataFrame(np.nan, index=range(len(sessions)), columns=entities)
    if not e.empty:
        p = e.pivot(index="_pos", columns="entity_id", values=value).reindex(columns=entities)
        val.loc[p.index] = p.to_numpy()
        stamp = pd.DataFrame(np.where(p.notna(), np.asarray(p.index)[:, None], np.nan), index=p.index, columns=entities)
        age.loc[p.index] = stamp.to_numpy()
    val = val.ffill()
    last = age.ffill()
    ages = np.arange(len(sessions))[:, None] - last.to_numpy()
    ok = ages <= max_age
    val = val.where(ok)
    out_age = pd.DataFrame(np.where(ok, ages, np.nan), index=sessions, columns=entities)
    val.index = sessions
    return val, out_age


# --------------------------------------------------------------------------- 바탕 자료


def _quarters(start: date, end: date) -> list[tuple[date, date]]:
    out, d = [], start
    while d <= end:
        nxt = (pd.Timestamp(d) + pd.offsets.QuarterBegin(startingMonth=1)).date()
        out.append((d, min(nxt - timedelta(days=1), end)))
        d = nxt
    return out


def build_base(store) -> None:  # type: ignore[no-untyped-def]
    """시총·상장주식수(market_stats)·거래대금·수정종가(prices) 넓은 표 — 세션 축은 국장 가격 세션."""
    from quant_rl_trading.store.prices import read_prices

    BASE.mkdir(parents=True, exist_ok=True)
    as_of = el.CAP_AS_OF
    caps, shares, value, close = [], [], [], []
    for lo, hi in _quarters(START, el.DATE_CAP):
        lo_t = datetime.combine(lo, time(0), tzinfo=UTC) - timedelta(hours=9)
        hi_t = datetime.combine(hi, time(23, 59), tzinfo=UTC)
        lb = (as_of - lo_t).days + 1
        ms = store.get("market_stats", as_of=as_of, lookback=lb, until=hi_t, market="KR",
                       columns=["entity_id", "valid_from", "metric", "value"])
        ms["session"] = pd.to_datetime(ms["valid_from"]).dt.tz_convert(KST).dt.date
        ms = ms[(ms["session"] >= lo) & (ms["session"] <= hi)]
        for metric, bucket in (("market_cap", caps), ("shares", shares)):
            f = ms[ms["metric"] == metric]
            bucket.append(f.pivot_table(index="session", columns="entity_id", values="value", aggfunc="last").astype("float64"))
        px = read_prices(store, as_of=as_of, lookback=lb, until=hi_t, columns=["close", "value"], adjusted=False, market="KR")
        px["session"] = pd.to_datetime(px["valid_from"]).dt.tz_convert(KST).dt.date
        px = px[(px["session"] >= lo) & (px["session"] <= hi) & px["entity_id"].astype(str).str.startswith("KR:")]
        value.append(px.pivot_table(index="session", columns="entity_id", values="value", aggfunc="last").astype("float64"))
        print(f"  바탕 {lo}~{hi}: 시총 {len(caps[-1])}세션 · 가격 {len(value[-1])}세션", flush=True)
    # 수정 종가는 한 번에(보정은 as_of 기준 접기 — 창을 나누면 같은 as_of 라 같다)
    span = (el.DATE_CAP - START).days + 10
    pa = read_prices(store, as_of=as_of, lookback=span, columns=["close"], adjusted=True, market="KR")
    pa["session"] = pd.to_datetime(pa["valid_from"]).dt.tz_convert(KST).dt.date
    pa = pa[(pa["session"] >= START) & (pa["session"] <= el.DATE_CAP) & pa["entity_id"].astype(str).str.startswith("KR:")]
    close = pa.pivot_table(index="session", columns="entity_id", values="close", aggfunc="last").astype("float64")
    del pa
    frames = {"cap": pd.concat(caps).sort_index(), "shares": pd.concat(shares).sort_index(),
              "value": pd.concat(value).sort_index(), "close_adj": close.sort_index()}
    sessions = sorted(frames["close_adj"].index)
    el.guard_cap(sessions, "바탕 세션")
    ents = sorted(set().union(*[set(f.columns) for f in frames.values()]))
    for name, f in frames.items():
        f = f[~f.index.duplicated(keep="last")].reindex(index=sessions, columns=ents).astype("float32")
        f.to_pickle(BASE / f"{name}.pkl")  # invariant-allow: data-access — 연구 캐시
        print(f"  {name}: {f.shape} · 결측 {float(f.isna().to_numpy().mean()):.1%}", flush=True)


def load_base() -> dict[str, pd.DataFrame]:
    return {n: pd.read_pickle(BASE / f"{n}.pkl").astype("float64") for n in ("cap", "shares", "value", "close_adj")}  # invariant-allow: data-access — 연구 캐시


# --------------------------------------------------------------------------- 사건 표


def _first_observed(store, start: date, end: date) -> dict[str, pd.Timestamp]:  # type: ignore[no-untyped-def]
    """접수번호 → 공시 목록 **첫** 관측 시각. 달마다 '그 달 말 + 3일' 시점 목록을 읽는다(원문 정정본이 그 뒤라 첫 것이 남는다)."""
    out: dict[str, pd.Timestamp] = {}
    for p in pd.period_range(start, end, freq="M"):
        lo = p.start_time.tz_localize(KST).to_pydatetime()
        hi = p.end_time.tz_localize(KST).ceil("s").to_pydatetime()
        then = hi + timedelta(days=3)
        f = store.get("documents", as_of=then, lookback=(then - lo).days + 1, until=hi, market="KR",
                      columns=["entity_id", "valid_from", "doc_id", "observed_at"])
        f = f[f["valid_from"] >= lo]
        out.update(dict(zip(f["doc_id"].astype(str), f["observed_at"], strict=False)))
    return out


def build_events(store, now: datetime) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """원문이 있는 자본·지배·자사주·공급계약 공시 → 사건 표(파싱 실패도 행으로 남긴다: parsed=False)."""
    from quant_rl_trading.collectors import dart_capital_events as ce
    from quant_rl_trading.collectors import dart_documents as docs
    from quant_rl_trading.collectors import dart_supply_contract as sc

    lo = datetime.combine(START, time(0), tzinfo=UTC)
    hi = datetime.combine(el.DATE_CAP, time(23, 59), tzinfo=UTC)
    d = store.get("documents", as_of=now, lookback=(now - lo).days + 1, until=hi, market="KR",
                  columns=["entity_id", "valid_from", "doc_id", "title", "raw_path"])
    d = d[d["entity_id"].astype(str).str.startswith("KR:")]
    d["kind"] = d["title"].astype(str).map(ce.kind_of)
    sk = d["title"].astype(str).map(sc.kind_of)
    d.loc[d["kind"].isna() & sk.notna(), "kind"] = "sc_" + sk[d["kind"].isna() & sk.notna()]
    d = d[d["kind"].notna()].copy()
    first = _first_observed(store, START, el.DATE_CAP)
    fallback = (pd.to_datetime(d["valid_from"]).dt.tz_convert(KST).dt.normalize() + pd.Timedelta(hours=18))
    d["observed_at"] = [first.get(str(x)) for x in d["doc_id"]]
    d["observed_at"] = pd.to_datetime(d["observed_at"], utc=True).fillna(fallback.dt.tz_convert("UTC"))
    d["amended"] = d["title"].astype(str).str.contains(r"^\[", regex=True)
    rows = []
    for r in d.itertuples():
        path = str(r.raw_path or "")
        has = len(path) > 1 and path not in ("None", docs.NO_TEXT)
        base = {"entity_id": r.entity_id, "doc_id": str(r.doc_id), "kind": r.kind, "valid_from": r.valid_from,
                "observed_at": r.observed_at, "amended": bool(r.amended), "has_text": has}
        if not has:
            rows.append({**base, "parsed": False})
            continue
        try:
            text = docs.read_text(Path(path))
        except (OSError, EOFError):
            rows.append({**base, "parsed": False})
            continue
        if str(r.kind).startswith("sc_"):
            c = sc.parse(text, kind=str(r.kind)[3:])
            rows.append({**base, "parsed": c.usable, "amount": c.amount, "ratio": c.sales_ratio, "base_shares": None})
            continue
        p = ce.parse(text, str(r.kind))
        rows.append({**base, "parsed": p.usable, "amount": p.amount, "shares": p.shares, "base_shares": p.base_shares,
                     "ratio": p.ratio, "price": p.price, "price2": p.price2, "ratio2": p.ratio2, "method": p.method,
                     "extra": json.dumps(p.extra, ensure_ascii=False) if p.extra else ""})
    ev = pd.DataFrame(rows)
    return amend_deltas(ev)


#: 흐름(합)으로 쓰는 종류 — 정정본은 **차이만** 더한다. 상태(마지막 값)로 쓰는 종류(담보·결과보고·지분 변동)는 정정본 값 그대로.
FLOW_KINDS = ("cb", "bw", "eb", "rights", "bb_decision", "bb_trust", "sc_contract", "sc_termination", "refix", "bwrefix")
AMEND_MATCH_DAYS = 180


def amend_deltas(ev: pd.DataFrame) -> pd.DataFrame:
    """정정공시(제목 '[…]' 머리, 국장 수시공시의 약 60%)가 원본과 따로 합산되면 같은 사건이 두 번 세인다.
    흐름 종류는 정정본 값을 **(정정값 − 같은 종목·종류의 직전 공시 값)** 으로 바꾼다 — 원본 관측 시각부터는 원본 값,
    정정 관측 시각부터는 정정 값이 합에 남는다(미래를 보지 않는다). 180일 안에 짝이 없으면 정정값 그대로(원본이 창 밖)."""
    ev = ev.sort_values(["entity_id", "kind", "observed_at", "doc_id"]).reset_index(drop=True)
    cols = [c for c in ("amount", "shares", "ratio") if c in ev.columns]
    for (_ent, kind), g in ev.groupby(["entity_id", "kind"], sort=False):
        if kind not in FLOW_KINDS or not g["amended"].any():
            continue
        prev: dict[str, float] = {}
        prev_t = None
        for idx, row in g.iterrows():
            if row["amended"] and prev_t is not None and (row["observed_at"] - prev_t).days <= AMEND_MATCH_DAYS:
                for c in cols:
                    v, pv = row[c], prev.get(c)
                    if pd.notna(v) and pv is not None and pd.notna(pv):
                        ev.at[idx, c] = v - pv
                    # 정정본 값을 못 읽었으면 원본 값이 남는다(0 이 아니라 결측 — 합에 안 들어간다)
            if row["parsed"]:
                prev = {c: row[c] for c in cols}
                prev_t = row["observed_at"]
    return ev


# --------------------------------------------------------------------------- 재료


def _axis(base: dict[str, pd.DataFrame]) -> tuple[list[date], pd.Index]:
    close = base["close_adj"]
    return list(close.index), pd.Index(close.columns, name="entity_id")


def _per_cap(w: pd.DataFrame, cap: pd.DataFrame) -> pd.DataFrame:
    """도착 세션 금액 ÷ **전날** 시총(공시 반응이 든 당일 시총을 피한다)."""
    c = cap.reindex_like(w).ffill().shift(1)
    return w / c.where(c > 0)


def _per_shares(w: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    s = shares.reindex_like(w).ffill().shift(1)
    return w / s.where(s > 0)


def _to_long(cols: dict[str, pd.DataFrame], sessions: list[date], lo: date) -> pd.DataFrame:
    """넓은 표 여럿 → (entity_id, session, 열들) 긴 표. 판정 창(lo~DATE_CAP)·거래가능 명단 안만."""
    ctx_trad = pd.read_pickle(el.CONTEXT_FILE / "trad.pkl")  # invariant-allow: data-access — 연구 캐시
    ctx_trad["session"] = pd.to_datetime(ctx_trad["session"]).dt.date
    keep = [i for i, d in enumerate(sessions) if lo <= d <= el.DATE_CAP]
    parts = []
    for name, w in cols.items():
        s = w.iloc[keep].astype("float32").stack(future_stack=True).rename(name)
        parts.append(s)
    long = pd.concat(parts, axis=1).reset_index()
    long.columns = ["session", "entity_id", *cols.keys()]
    long = long.merge(ctx_trad[["session", "entity_id"]].astype({"entity_id": str}), on=["session", "entity_id"], how="inner")
    return long


def _write(name: str, long: pd.DataFrame) -> Path:
    el.guard_cap(long["session"].unique(), name)
    long = long.drop_duplicates(["entity_id", "session"]).assign(seed=0)
    path = SCORES / f"{name}{el.SCORE_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    long.to_parquet(path, index=False)  # invariant-allow: data-access — 재료 캐시
    cov = {c: float(long[c].notna().mean()) for c in long.columns if c.startswith(("pred", "x_"))}
    nz = {c: float((long[c].fillna(0) != 0).mean()) for c in long.columns if c.startswith(("pred", "x_"))}
    print(f"{name}: {len(long):,}행 · 세션 {long['session'].nunique()} · 종목 {long['entity_id'].nunique()}", flush=True)
    for c in cov:
        print(f"   {c:22s} 값있음 {cov[c]:.1%} · 0 아님 {nz[c]:.1%}", flush=True)
    for c in [c for c in long.columns if c.startswith("m_")]:
        print(f"   {c:22s} 표지=1 {float(long[c].mean()):.1%}", flush=True)
    return path


def n1(base, ev) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N1 순발행 + CB/BW 리픽싱 + 유상증자 배정 방식."""
    sessions, ents = _axis(base)
    e = ev[ev["parsed"]].copy()
    bond = e[e["kind"].isin(["cb", "bw"])].copy()
    rights = e[e["kind"] == "rights"].copy()
    bond["dil"] = bond["ratio"].clip(0, 100)                      # 주식총수 대비 %(공시가 스스로 분모를 든다)
    rights["dil"] = rights["ratio"].clip(0, 100)
    pot = event_wide(pd.concat([bond, rights]), "dil", sessions, ents)
    third = event_wide(rights[rights["method"] == "third"], "dil", sessions, ents)
    holders = event_wide(rights[rights["method"].isin(["holders", "public"])], "dil", sessions, ents)
    out = {"pred": -_decay(pot, 120), "x_dil_h20": -_decay(pot, 20), "x_third_h60": _decay(third, 60),
           "x_public_h60": -_decay(holders, 60)}
    # 리픽싱 여력: 최근 2년 CB·BW 의 (전환가/ max(현재가, 최저가) − 1) × 주식총수 대비 % — 가격이 내려가면 늘어나는 희석.
    close = base["close_adj"]
    b = bond[bond["price"].notna() & bond["dil"].notna()].copy()
    b["apos"] = arrival_index(b["observed_at"], sessions)
    b = b[(b["apos"] < len(sessions)) & b["entity_id"].isin(ents)]
    room = np.zeros((len(sessions), len(ents)))
    col_of = {e_: i for i, e_ in enumerate(ents)}
    px = close.reindex(columns=ents).ffill().to_numpy()
    for r in b.itertuples():
        j = col_of[r.entity_id]
        lo_, hi_ = int(r.apos), min(len(sessions), int(r.apos) + 500)
        floor = r.price2 if (r.price2 and r.price2 > 0) else 0.7 * r.price
        p = np.maximum(px[lo_:hi_, j], floor)
        extra = np.clip(r.price / np.where(p > 0, p, np.nan) - 1.0, 0.0, 1.0)
        room[lo_:hi_, j] += np.nan_to_num(extra) * r.dil
    out["x_refix_room"] = -pd.DataFrame(room, index=sessions, columns=ents)
    # 실제 리픽싱(조정 공시 원문이 있을 때만): 늘어난 전환가능 주식 / 상장주식수
    rf = e[e["kind"].isin(["refix", "bwrefix"])]
    n_refix_text = int(ev[ev["kind"].isin(["refix", "bwrefix"])]["has_text"].sum())
    n_refix_all = int(ev["kind"].isin(["refix", "bwrefix"]).sum())
    if n_refix_text >= 0.8 * max(n_refix_all, 1):
        added = event_wide(rf, "shares", sessions, ents)
        out["x_refix_h60"] = -_decay(_per_shares(added, base["shares"]).fillna(0.0) * 100, 60)
    else:
        print(f"  N1 리픽싱 원문 {n_refix_text}/{n_refix_all} — x_refix_h60 생략(백필 뒤 다시)", flush=True)
    # 실현 순발행(12개월): log(시총비) − log(수정종가비) = 분할 보정된 log 주식수 변화. 신주 상장 시차는 감내(±1 로 자른다).
    cap = base["cap"].reindex(index=sessions, columns=ents).ffill(limit=5)
    ca = close.reindex(index=sessions, columns=ents)
    nsi = (np.log(cap) - np.log(cap.shift(250))) - (np.log(ca) - np.log(ca.shift(250)))
    out["x_nsi_12m"] = -nsi.clip(-1, 1)
    out["m_nsi"] = nsi.isna().astype("float32")
    return out


#: KRX 투자자 이름 → LS t1717 이름(같은 주체). KRX 에 사모펀드·보험 따로 없음(그 칸은 결측으로 남는다).
KRX_NAMES = {"연기금": "기금", "투신": "투신", "금융투자": "증권", "개인": "개인"}


def n2(base, store) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N2 투자자 유형 세분 — LS t1717 수급(ls_api: 기금·투신·증권·사모펀드·보험), 2021-08~ 약 800~990종목."""
    sessions, ents = _axis(base)
    as_of = el.CAP_AS_OF
    wide: dict[str, list[pd.DataFrame]] = {}
    seen: set[str] = set()
    for lo, hi in _quarters(START, el.DATE_CAP):
        lo_t = datetime.combine(lo, time(0), tzinfo=UTC) - timedelta(hours=9)
        hi_t = datetime.combine(hi, time(23, 59), tzinfo=UTC)
        f = store.get("flows", as_of=as_of, lookback=(as_of - lo_t).days + 1, until=hi_t, market="KR",
                      columns=["entity_id", "valid_from", "investor", "net_value", "source"])
        f["session"] = pd.to_datetime(f["valid_from"]).dt.tz_convert(KST).dt.date
        f = f[(f["session"] >= lo) & (f["session"] <= hi)]
        # ls_api(t1717) 가 2026-08 중순에 끊기고 KRX(pykrx) 가 이어받았다 — 이름을 맞춰 **ls 가 없는 (세션, 종목)만** KRX 로 메운다.
        ls = f[f["source"] == "ls_api"]
        kx = f[f["source"] == "krx"].assign(investor=lambda g: g["investor"].map(KRX_NAMES))
        kx = kx[kx["investor"].notna()]
        have = pd.MultiIndex.from_frame(ls[["session", "entity_id"]].drop_duplicates())
        kx = kx[~pd.MultiIndex.from_frame(kx[["session", "entity_id"]]).isin(have)]
        seen |= set(ls["entity_id"])
        kx = kx[kx["entity_id"].isin(seen)]       # 덮는 종목 집합은 LS 의 것 그대로(커버리지가 마지막 30세션에 갑자기 넓어지지 않게)
        f = pd.concat([ls, kx], ignore_index=True)
        for inv in ("기금", "투신", "증권", "사모펀드", "보험", "개인"):
            g = f[f["investor"] == inv]
            wide.setdefault(inv, []).append(g.pivot_table(index="session", columns="entity_id", values="net_value", aggfunc="last"))
        print(f"  수급 {lo}~{hi}: {len(f):,}행", flush=True)
    W = {k: pd.concat(v).sort_index().reindex(index=sessions, columns=ents) for k, v in wide.items()}
    covered = W["개인"].notna()
    cap = base["cap"].reindex(index=sessions, columns=ents).ffill(limit=5)
    adv = base["value"].reindex(index=sessions, columns=ents).rolling(20, min_periods=10).mean()

    def flow(inv: str, n: int, denom: pd.DataFrame) -> pd.DataFrame:
        x = W[inv].where(covered, np.nan).fillna(0.0).where(covered)
        s = x.rolling(n, min_periods=max(5, n // 2)).sum()
        return s / denom.where(denom > 0)

    out = {"pred": flow("기금", 60, cap)}
    out["x_pension_20"] = flow("기금", 20, cap)
    out["x_pension_adv20"] = flow("기금", 20, adv * 20)
    out["x_trust_60"] = flow("투신", 60, cap)
    out["x_trust_20"] = flow("투신", 20, cap)
    out["x_finv_20"] = flow("증권", 20, cap)
    out["x_pe_60"] = flow("사모펀드", 60, cap)
    out["x_ins_60"] = flow("보험", 60, cap)
    sgn = np.sign(W["기금"].where(covered))
    out["x_pension_persist"] = sgn.rolling(60, min_periods=30).mean()
    out["x_pension_accel"] = out["x_pension_20"] - out["pred"] / 3.0
    out["m_flow"] = (~covered.rolling(20, min_periods=1).max().astype(bool)).astype("float32")
    return out


def _second_thursday(y: int, m: int) -> date:
    d = date(y, m, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(days=7)


def n4(base, store) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N4 K200 정기변경 편입·편출 예측 — 코스피 보통주 시총(120세션 평균) 순위 대 현재 구성. 구성은 2022-11-04~."""
    sessions, ents = _axis(base)
    as_of = el.CAP_AS_OF
    im = store.get("index_members", as_of=as_of, lookback=(as_of - datetime(2021, 1, 1, tzinfo=UTC)).days, market="KR",
                   columns=["entity_id", "valid_from", "index_id"])
    im = im[im["index_id"] == "KOSPI200"]
    im["session"] = pd.to_datetime(im["valid_from"]).dt.tz_convert(KST).dt.date
    snap_days = sorted(im["session"].unique())
    mem = pd.DataFrame(0.0, index=snap_days, columns=ents)
    for d, g in im.groupby("session"):
        mem.loc[d, mem.columns.intersection(g["entity_id"])] = 1.0
    # 스냅샷 관측: 그 세션 16:00 → 그 세션 결정 시각에 보인다. 주 1회 스냅샷 구간은 ffill(최대 10세션), 첫 스냅샷 앞은 모름.
    member = mem.reindex(sessions).ffill(limit=10)
    unknown = member.isna().all(axis=1)
    # 코스닥 표지: KRX 소속부 행이 있으면 코스닥(코스피는 소속부가 빈칸이라 행이 없다 — krx_openapi.normalize_sectors).
    sec = store.get("sectors", as_of=as_of, lookback=2000, market="KR", columns=["entity_id", "source"])
    kosdaq = set(sec.loc[sec["source"] == "krx_openapi", "entity_id"])
    common = [e for e in ents if e not in kosdaq and str(e).endswith("0")]
    cap = base["cap"].reindex(index=sessions, columns=ents).ffill(limit=5)
    avg = cap[common].rolling(120, min_periods=60).mean()
    rank = avg.rank(axis=1, ascending=False)

    def cap_at(k: int) -> pd.Series:
        return avg.where(rank.le(k)).min(axis=1)

    z_in = np.log(avg.div(cap_at(150), axis=0))
    z_out = np.log(avg.div(cap_at(250), axis=0))
    mm = member[common]
    press = (1 - mm) * z_in.clip(lower=0) - mm * (-z_out).clip(lower=0)
    # 정기변경 효력: 6·12월 둘째 목요일 다음 날. 그 전 40세션 창에 가중 1, 효력 뒤 0.
    eff = sorted({_second_thursday(y, m) + timedelta(days=1) for y in range(2021, 2027) for m in (6, 12)})
    sidx = pd.Index(sessions)
    w = np.zeros(len(sessions))
    for d in eff:
        k = sidx.searchsorted(d)
        w[max(0, k - 40):min(k, len(sessions))] = 1.0
    season = pd.Series(w, index=sessions)
    out = {"pred": press.mul(season, axis=0).reindex(columns=ents),
           "x_press_raw": press.reindex(columns=ents),
           "x_z_cut200": np.log(avg.div(cap_at(200), axis=0)).reindex(columns=ents),
           "x_member": mm.reindex(columns=ents)}
    m = pd.DataFrame(1.0, index=sessions, columns=ents)
    m.loc[~unknown, common] = 0.0
    out["m_k200"] = m
    return out


def n5(base, ev) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N5 단일판매·공급계약 — 매출액 대비 %(공시 스스로 분모) + 금액/시총, 감쇠·창 합·해지."""
    sessions, ents = _axis(base)
    e = ev[ev["parsed"] & ev["kind"].isin(["sc_contract", "sc_termination"])]
    con, can = e[e["kind"] == "sc_contract"], e[e["kind"] == "sc_termination"]
    r = event_wide(con.assign(r=con["ratio"].clip(0, 1000)), "r", sessions, ents)
    a = event_wide(con, "amount", sessions, ents)
    t = event_wide(can.assign(r=can["ratio"].clip(0, 1000)), "r", sessions, ents)
    last, age = _last_state(con.assign(r=con["ratio"].clip(0, 1000)), "r", sessions, ents, max_age=120)
    return {"pred": _decay(r, 40), "x_sum60": _roll(r, 60), "x_amt_cap_h40": _decay(_per_cap(a, base["cap"]).fillna(0.0), 40),
            "x_last": last, "x_age": age, "x_cancel60": -_roll(t, 60), "m_sc_last": last.isna().astype("float32")}


def n7(base, ev) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N7 자사주 실제 취득 — 결과보고서(직접)·신탁 해지결과의 실제 금액, 결정 대비 이행률.

    **원문 관문**(2026-10-14 리드 점검): 신탁 해지결과는 원문을 파싱해야 금액이 나온다. 원문 0/1,612 로 구웠을 때 신탁형 매입이
    '이행 0' 으로 읽혀 `pred` 가 덜 세이고 `x_unfilled_h60` 이 부풀었다(그 판정은 보류). 그래서 N3 담보처럼 원문 80% 미만이면 굽지 않고,
    넘어도 **파싱 못 한 해지결과**가 최근 60세션 안에 도착한 (종목, 세션)은 금액 열을 결측으로 두고 `m_trust_gap`=1 로 표지한다.
    """
    sessions, ents = _axis(base)
    te_all = ev[ev["kind"] == "bb_trust_end"]
    n_te_text, n_te = int(te_all["has_text"].sum()), len(te_all)
    print(f"  N7 신탁 해지결과 원문 {n_te_text}/{n_te}", flush=True)
    if n_te_text < 0.8 * max(n_te, 1):
        raise SystemExit(f"N7 신탁 해지결과 원문이 {n_te_text}/{n_te} — 백필 뒤에 굽는다(원문 없음을 '이행 0' 으로 읽으면 안 된다)")
    e = ev[ev["parsed"]]
    res = e[e["kind"].isin(["bb_result", "bb_trust_end"])]
    plan = e[e["kind"].isin(["bb_decision", "bb_trust"])]
    gap = _roll(event_wide(te_all[~te_all["parsed"]].assign(one=1.0), "one", sessions, ents), 60) > 0
    exe = _per_cap(event_wide(res, "amount", sessions, ents), base["cap"]).fillna(0.0)
    pl = _per_cap(event_wide(plan, "amount", sessions, ents), base["cap"]).fillna(0.0)
    rate, rage = _last_state(res.assign(r=res["ratio"].clip(0, 150)), "r", sessions, ents, max_age=250)
    return {"pred": (_decay(exe, 60) * 100).where(~gap), "x_exec_250": (_roll(exe, 250) * 100).where(~gap),
            "x_plan_h20": _decay(pl, 20) * 100, "x_rate_last": rate,
            "x_unfilled_h60": ((_decay(pl, 60) - _decay(exe, 60)) * 100).where(~gap),
            "m_rate": rate.isna().astype("float32"), "m_trust_gap": gap.astype("float32")}


def n3(base, ev) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N3 최대주주 주식담보(코스닥 '최대주주 변경을 수반하는' 담보 공시)·지분 변동·지배권 변경."""
    sessions, ents = _axis(base)
    e = ev[ev["parsed"]]
    pl = e[e["kind"].isin(["pledge", "pledge_off"])]
    state, _age = _last_state(pl.assign(r=pl["ratio"].clip(0, 100)), "r", sessions, ents, max_age=500)
    drop = pl.assign(d=(pl["extra"].map(lambda s: json.loads(s).get("holder_pct") if s else np.nan) - pl["ratio2"]).clip(0, 100))
    dstate, _ = _last_state(drop, "d", sessions, ents, max_age=500)
    out = {"pred": -state.fillna(0.0), "x_exec_drop": -dstate.fillna(0.0)}
    chg = pl.assign(r=pl["ratio"].clip(0, 100))
    out["x_pledge_new_h20"] = -_decay(event_wide(chg[chg["kind"] == "pledge"], "r", sessions, ents), 20)
    hc = e[e["kind"] == "holder_change"]
    n_hc_text = int(ev[ev["kind"] == "holder_change"]["has_text"].sum())
    n_hc = int((ev["kind"] == "holder_change").sum())
    if n_hc_text >= 0.8 * max(n_hc, 1):
        out["x_holder_chg_h40"] = _decay(event_wide(hc.assign(r=hc["ratio"].clip(-50, 50)), "r", sessions, ents), 40)
    else:
        print(f"  N3 최대주주등소유주식변동 원문 {n_hc_text}/{n_hc} — x_holder_chg_h40 생략(백필 뒤 다시)", flush=True)
    cc = e[e["kind"] == "control_change"]
    out["x_ctrl_new_h60"] = _decay(event_wide(cc.assign(r=cc["ratio2"].clip(0, 100)), "r", sessions, ents), 60)
    n_pl_text = int(ev[ev["kind"].isin(["pledge", "pledge_off"])]["has_text"].sum())
    n_pl = int(ev["kind"].isin(["pledge", "pledge_off"]).sum())
    print(f"  N3 담보 원문 {n_pl_text}/{n_pl}", flush=True)
    if n_pl_text < 0.8 * max(n_pl, 1):
        raise SystemExit(f"N3 담보 원문이 {n_pl_text}/{n_pl} — 백필 뒤에 굽는다(빈 상태를 '담보 없음' 으로 읽으면 안 된다)")
    return out


# --------------------------------------------------------------------------- 스타일 잔차 부분 IC


def style_report(name: str, base: dict[str, pd.DataFrame]) -> dict[str, object]:
    """FW 부분 IC — 재료(pred)와 h5 정답을 **둘 다** 세션 안 순위에서 log 시총·log ADV20·60일 변동성에 잔차화한 상관.
    (N1 진단 10/11: 재료만 잔차화하면 0 몰림 동점 무리에 통제의 IC 가 샌다.) 전체·특수 뺀 것·특수만."""
    path = SCORES / f"{name}{el.SCORE_SUFFIX}"
    f = pd.read_parquet(path, columns=["entity_id", "session", "pred"])  # invariant-allow: data-access — 재료 캐시
    f["session"] = pd.to_datetime(f["session"]).dt.date
    close = base["close_adj"]
    sessions = list(close.index)
    y5 = (close.shift(-6) / close.shift(-1) - 1.0).where(lambda x: x.abs() <= 1.0)
    cap = base["cap"].ffill(limit=5)
    adv = base["value"].rolling(20, min_periods=10).mean()
    vol = close.pct_change(fill_method=None).rolling(60, min_periods=30).std()
    ctrl = {"lcap": np.log(cap.where(cap > 0)), "ladv": np.log(adv.where(adv > 0)), "vol": vol}
    wide = f.pivot_table(index="session", columns="entity_id", values="pred", aggfunc="last")
    ics, raw = {}, {}
    for d in wide.index:
        if d not in close.index:
            continue
        x = wide.loc[d]
        df = pd.DataFrame({"x": x, "y": y5.loc[d].reindex(x.index)})
        for k, v in ctrl.items():
            df[k] = v.loc[d].reindex(x.index)
        df = df.dropna()
        if len(df) < 200 or df["x"].nunique() < 5:
            continue
        r = df.rank(pct=True) - 0.5
        X = np.column_stack([np.ones(len(r)), r[["lcap", "ladv", "vol"]].to_numpy()])
        bx = np.linalg.lstsq(X, r["x"].to_numpy(), rcond=None)[0]
        by = np.linalg.lstsq(X, r["y"].to_numpy(), rcond=None)[0]
        ex, ey = r["x"].to_numpy() - X @ bx, r["y"].to_numpy() - X @ by
        ics[d] = float(np.corrcoef(ex, ey)[0, 1]) if ex.std() > 0 else np.nan
        raw[d] = float(np.corrcoef(r["x"], r["y"])[0, 1])
    out: dict[str, object] = {}
    for label, series in (("partial", pd.Series(ics).dropna()), ("raw", pd.Series(raw).dropna())):
        spec = el.special_sessions(list(series.index), sessions, label_days=6)
        ex = series[[d not in spec for d in series.index]]
        sp = series[[d in spec for d in series.index]]
        out[label] = {k: {"ic": float(s.mean()), "t": el.nw_t(s), "n": int(len(s))} for k, s in
                      (("전체", series), ("특수뺀", ex), ("특수만", sp)) if len(s) > 10}
    return out


# --------------------------------------------------------------------------- CLI

def _lending_wide(store, value: str, table: str, sessions: list[date], ents: pd.Index, *, how: str) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """창고 표 → **도착 세션**(observed_at ≤ 결정 시각) × 종목 넓은 표. how="last" 는 상태(잔고), "sum" 은 흐름(반환 물량)."""
    as_of = el.CAP_AS_OF
    parts = []
    for lo, hi in _quarters(START, el.DATE_CAP):
        lo_t = datetime.combine(lo, time(0), tzinfo=UTC) - timedelta(hours=9)
        hi_t = datetime.combine(hi, time(23, 59), tzinfo=UTC)
        f = store.get(table, as_of=as_of, lookback=(as_of - lo_t).days + 1, until=hi_t, market="KR",
                      columns=["entity_id", "valid_from", "observed_at", value])
        f = f[(pd.to_datetime(f["valid_from"]).dt.tz_convert(KST).dt.date >= lo)]
        parts.append(f)
        print(f"  {table} {lo}~{hi}: {len(f):,}행", flush=True)
    ev = pd.concat(parts, ignore_index=True)
    if how == "sum":
        return event_wide(ev, value, sessions, ents)
    val, _age = _last_state(ev, value, sessions, ents, max_age=5)
    return val


def n6(base, store) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    """N6 주식대차잔고·의무보호예수 반환(금융위, `collectors/fsc_lending.py`). 기준일 d 값은 d+1 13:00 KST 에 공표 → 도착 세션 d+1
    (`fsc_lending.lending_features` 의 '하루 늦춤' 과 같다). 정의는 그 피처 그대로 + 결측 표지, 결과 보기 전 고정(2026-10-14).

    - 대차: 잔고 행이 없는 (종목, 세션)은 **모름**(`m_lend`=1, 값 결측) — 0 으로 메우지 않는다. 상태라 5세션까지만 이어 쓴다.
    - 반환: 반환이 없던 날은 0(사건 부재는 사실). 보호예수는 반환 **뒤에** 나온다 — 해제 전 약세가 아니라 반환 직후 오버행이다.
    """
    sessions, ents = _axis(base)
    bal = _lending_wide(store, "lend_balance", "stock_lending", sessions, ents, how="last")
    ret = _lending_wide(store, "returned_shares", "lockup_returns", sessions, ents, how="sum")
    sh = base["shares"].reindex(index=sessions, columns=ents).ffill(limit=5).shift(1)   # 잔고 기준일(전날)의 주식수
    ratio = bal / sh.where(sh > 0)
    lock20 = _per_shares(_roll(ret, 20), base["shares"])
    return {"pred": -(ratio - ratio.shift(20)), "x_lend_ratio": ratio, "x_dlend5": ratio - ratio.shift(5),
            "x_lockup20": lock20, "m_lend": ratio.isna().astype("float32")}


BUILDERS = {"N1": ("ev", n1), "N2": ("store", n2), "N3": ("ev", n3), "N4": ("store", n4), "N5": ("ev", n5), "N7": ("ev", n7), "N6": ("store", n6)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("base")
    sub.add_parser("events")
    ft = sub.add_parser("features")
    ft.add_argument("--n", default="N2,N4,N5,N7")
    sy = sub.add_parser("style")
    sy.add_argument("--n", default="N2,N4,N5,N7")
    args = ap.parse_args(argv)
    if args.cmd == "base":
        build_base(_store())
        return 0
    if args.cmd == "events":
        from quant_rl_trading.replay.clock import LiveClock

        ev = build_events(_store(), LiveClock().now())
        OUT.mkdir(parents=True, exist_ok=True)
        ev.to_parquet(EVENTS, index=False)  # invariant-allow: data-access — 연구 캐시
        summ = ev.groupby("kind").agg(n=("doc_id", "size"), text=("has_text", "mean"), parsed=("parsed", "mean"),
                                      ents=("entity_id", "nunique"))
        print(summ.to_string(), flush=True)
        return 0
    base = load_base()
    names = [x.strip().upper() for x in args.n.split(",") if x.strip()]
    if args.cmd == "style":
        rep = {n: style_report(n, base) for n in names}
        (OUT / "style.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        for n, r in rep.items():
            print(n, json.dumps(r, ensure_ascii=False), flush=True)
        return 0
    ev = pd.read_parquet(EVENTS) if any(BUILDERS[n][0] == "ev" for n in names) else None  # invariant-allow: data-access — 연구 캐시
    store = _store() if any(BUILDERS[n][0] == "store" for n in names) else None
    for n in names:
        kind, fn = BUILDERS[n]
        cols = fn(base, ev if kind == "ev" else store)
        sessions = list(base["close_adj"].index)
        _write(n, _to_long(cols, sessions, el.CONTEXT_START))
        print(f"  부호: {SIGNS[n]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
