"""사전등록 시행 C·D·E — docs/protocols/new-sources-2026-09.md 대로 잰다.

    .venv/bin/python tools/trial_new_sources.py --trial C [--save]   # PEAD
    .venv/bin/python tools/trial_new_sources.py --trial D [--save]   # 내부자 순매수
    .venv/bin/python tools/trial_new_sources.py --trial E [--save]   # 월 리밸런스
    .venv/bin/python tools/trial_new_sources.py --trial H [--save]   # 국장 공매도

기준을 여기서 바꾸지 않는다. 채택 기준은 프로토콜 문서에 있고, 이 도구는 숫자만 낸다.
`--save` 는 research_trials 에 family `sources` 1행을 적는다(시행 1회 소진).

## 프로토콜을 코드로 옮기며 정한 것 (문서에 안 적힌 세부)

- **홀드아웃은 끝까지 닫는다.** 시세·지수 조회의 as_of 를 판정 마지막 세션(2026-06-30 이전)에
  맞추고, 이벤트 연구는 **CAR 창 [t+2, t+21] 이 그 세션 안에서 닫히는 사건만** 쓴다. 6월 말
  사건의 20일 창은 7월로 넘어가는데, 그걸 재려면 금고를 여는 것이 된다.
- **NW t 를 무엇에 붙이나.** 프로토콜은 "상위/하위 3분위 CAR 차이의 NW t" 라고만 적었다.
  겹치는 창(20일) 때문에 사건들이 독립이 아니므로, **사건을 날짜순으로 세워 한 줄짜리 시계열**
  (상위 3분위는 +CAR, 하위 3분위는 −CAR, 가운데는 제외)을 만들고 거기에 NW(lag 20) 를 붙인다.
  그 평균의 부호·유의성은 "상위−하위 > 0" 과 같은 검정이다.
- **신호가 언제부터 보이나.** 반응은 [t, t+1] 을 다 봐야 알 수 있으므로 `pead_60` 은 **t+2**
  부터 60세션. 내부자 보고는 18:00 KST 라 당일 장에는 못 쓰므로 `insider_net_60` 은 **t+1**
  부터 60세션. 이 한 칸을 안 밀면 미래를 보는 신호가 된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts.base import rank_score  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_analyst_features import (  # noqa: E402
    HOLDOUT_START,
    MARKET,
    ONE_WAY_COST,
    SEOUL,
    T_GATE,
    TOP_N,
    _combined,
    _feature_ic,
    _net_ir,
    _nw,
    _scores,
    _sessions,
    _targets,
    _top_n_excess,
    _tradable,
)

PROTOCOL = Path("docs/protocols/new-sources-2026-09.md")
FAMILY = "sources"
#: 이벤트 연구 창. 반응 [t, t+1] · 드리프트 [t+2, t+21].
REACTION_DAYS = 2
DRIFT_FROM, DRIFT_TO = 2, 21
#: 횡단면 신호가 살아 있는 창(세션).
SIGNAL_WINDOW = 60
#: 시행 E 변형의 리밸런스 주기(세션)와 현행 완충 구간.
REBALANCE_INTERVAL = 20
EXIT_RANK = 48


def _record_trial(store: Store, *, trial: str, detail: str) -> None:
    now = datetime.now(UTC)  # invariant-allow: wallclock
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else ""
    store.append("research_trials", [{
        "entity_id": f"new-sources-2026-09:{trial}", "valid_from": now, "observed_at": now,
        "source": "trial_new_sources", "market": MARKET, "family": FAMILY, "n_trials": 1,
        "protocol_hash": digest, "detail": detail[:900],
    }], ingest_run_id=f"trial-sources-{trial}-{now:%Y%m%dT%H%M%S}")
    print(f"\nresearch_trials 기록: {FAMILY}/{trial} · protocol {digest}")


# --------------------------------------------------------------------------- 공통: 시세·비정상수익

def _last_moment(sessions: list[date]) -> datetime:
    """조회 상한. 이 시각 뒤 데이터는 금고(2026-07-01~) 다."""
    return datetime.combine(sessions[-1], time(15, 40), tzinfo=SEOUL)


def _panels(store: Store, sessions: list[date]) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """(종가, 거래대금, 지수) — 전부 판정 마지막 세션까지만."""
    last = _last_moment(sessions)
    frame = read_prices(
        store, as_of=last, lookback=900, market=MARKET,
        columns=["entity_id", "valid_from", "close", "value"], adjusted=True,
    )
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.tz_convert(SEOUL).dt.date
    close = frame.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    value = frame.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index()
    index_id = str(store.config("benchmark.kr_index", as_of=last))
    idx = store.get("indices", as_of=last, lookback=900, entity=index_id, columns=["valid_from", "close"])
    idx["day"] = pd.to_datetime(idx["valid_from"]).dt.tz_convert(SEOUL).dt.date
    index = idx.groupby("day")["close"].last().sort_index()
    return close.where(close > 0), value, index


def _abnormal(close: pd.DataFrame, index: pd.Series) -> pd.DataFrame:
    """일별 비정상수익 AR = 종목 수익 − 지수 수익."""
    stock = close.pct_change()
    market = index.reindex(close.index).ffill().pct_change()
    return stock.sub(market, axis=0)


def _windows(ar: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """사건마다 반응 AR[t, t+1] 과 드리프트 CAR[t+2, t+21].

    창이 판정 구간 안에서 닫히는 사건만 남긴다 — 6월 말 사건의 20일 창을 재려면
    금고(2026-07-01~)를 열어야 하므로 그 사건은 **버린다**.
    """
    days = list(ar.index)
    position = {day: i for i, day in enumerate(days)}
    cum = ar.fillna(0.0).cumsum()
    rows = []
    for entity, day in events[["entity_id", "session"]].itertuples(index=False):
        i = position.get(day)
        if i is None or entity not in ar.columns:
            continue
        if i + DRIFT_TO >= len(days):
            continue  # 드리프트 창이 판정 구간을 넘는다
        base = cum[entity]
        reaction = base.iloc[i + REACTION_DAYS - 1] - (base.iloc[i - 1] if i > 0 else 0.0)
        drift = base.iloc[i + DRIFT_TO] - base.iloc[i + DRIFT_FROM - 1]
        if not np.isfinite(reaction) or not np.isfinite(drift):
            continue
        rows.append({"entity_id": entity, "session": day, "reaction": float(reaction), "car": float(drift)})
    return pd.DataFrame(rows)


def _tercile_test(windows: pd.DataFrame) -> dict[str, float]:
    """반응 3분위 상위−하위의 CAR 차이와 NW t (겹치는 창 → lag 20)."""
    if len(windows) < 30:
        return {"n": len(windows), "top": float("nan"), "bottom": float("nan"), "diff": float("nan"), "t": float("nan")}
    ranked = windows.sort_values("session").copy()
    edges = ranked["reaction"].quantile([1 / 3, 2 / 3]).to_numpy()
    ranked["bucket"] = np.select(
        [ranked["reaction"] <= edges[0], ranked["reaction"] >= edges[1]], [-1, 1], default=0
    )
    tails = ranked[ranked["bucket"] != 0]
    signed = pd.Series((tails["bucket"] * tails["car"]).to_numpy())
    top = float(ranked.loc[ranked["bucket"] == 1, "car"].mean())
    bottom = float(ranked.loc[ranked["bucket"] == -1, "car"].mean())
    return {
        "n": len(ranked), "top": top, "bottom": bottom, "diff": top - bottom,
        "t": _nw(signed, DRIFT_TO),
    }


def _signal_panel(
    events: pd.DataFrame, value_column: str, sessions: list[date], *, delay: int, window: int
) -> pd.DataFrame:
    """사건값을 ``delay`` 세션 뒤부터 ``window`` 세션 동안 들고 간다 (가장 최근 사건).

    ``events`` 는 (entity_id, session, <value_column>). 같은 날 같은 종목이 둘이면 마지막.
    """
    if events.empty:
        return pd.DataFrame(columns=["entity_id", "session", "signal"])
    wide = events.pivot_table(index="session", columns="entity_id", values=value_column, aggfunc="last")
    wide = wide.reindex(sessions).shift(delay).ffill(limit=window - 1)
    long = wide.stack().rename("signal").reset_index()
    long.columns = ["session", "entity_id", "signal"]
    return long[["entity_id", "session", "signal"]]


def _flow_panel(
    filings: pd.DataFrame, value: pd.DataFrame, close: pd.DataFrame, sessions: list[date],
    *, delay: int, window: int,
) -> pd.DataFrame:
    """금액 합을 창 안에서 누적하고 20일 평균 거래대금으로 나눈다 (시행 D)."""
    if filings.empty:
        return pd.DataFrame(columns=["entity_id", "session", "signal"])
    krw = filings.pivot_table(index="session", columns="entity_id", values="krw", aggfunc="sum")
    krw = krw.reindex(close.index).fillna(0.0)
    rolled = krw.rolling(window, min_periods=1).sum().shift(delay)
    turnover = value.reindex(close.index).rolling(20, min_periods=10).mean().shift(delay)
    ratio = rolled.div(turnover.replace(0.0, np.nan))
    ratio = ratio.reindex(sessions)
    long = ratio.replace([np.inf, -np.inf], np.nan).stack().rename("signal").reset_index()
    long.columns = ["session", "entity_id", "signal"]
    return long[["entity_id", "session", "signal"]]


def _judge_signal(
    store: Store, name: str, signal: pd.DataFrame, tradable: set[str] | None,
    t5: pd.DataFrame, t20: pd.DataFrame,
) -> dict[str, float | bool]:
    """횡단면 신호 하나를 공통 기준으로 잰다: IC(h5·h20) → fundamental 위 한계기여 → 상위 24."""
    frame = signal.dropna(subset=["signal"]).copy()
    if tradable is not None:
        frame = frame[frame["entity_id"].isin(tradable)]
    if frame.empty:
        print(f"  {name}: 신호가 0행이다 — 잴 것이 없다")
        return {"rows": 0, "adopt": False}
    frame["score"] = frame.groupby("session")["signal"].transform(rank_score)
    scored = frame[["entity_id", "session", "score"]].dropna()
    ic5, t5v, n5 = _feature_ic(scored.rename(columns={"score": "s"}), "s", t5, 5)
    ic20, t20v, _ = _feature_ic(scored.rename(columns={"score": "s"}), "s", t20, 20)
    fundamental = _scores("fundamental")
    _shares, details = ic_module.marginal_shares(
        {"fundamental": fundamental, name: scored}, t5, horizon=5, t_min=T_GATE
    )
    delta, delta_t = details[name]
    base_ex = _top_n_excess(fundamental, t5)
    comb_ex = _top_n_excess(_combined({"fundamental": fundamental, name: scored}), t5)
    diff = (comb_ex - base_ex).dropna()
    adopt = bool(np.isfinite(delta_t) and delta > 0 and delta_t >= T_GATE and diff.mean() >= 0)
    print(f"  IC(h5) {ic5:+.4f} (NW t {t5v:+.2f}, {n5}일) · IC(h20) {ic20:+.4f} (NW t {t20v:+.2f})")
    print(f"  fundamental 위 한계기여 ΔIC(h5) {delta:+.4f} · NW t {delta_t:+.2f}")
    print(f"  상위 {TOP_N} h5 초과수익: 기준 {base_ex.mean():+.5f} · 결합 {comb_ex.mean():+.5f} "
          f"· Δ {diff.mean():+.5f} (NW t {_nw(diff, 4):+.2f})")
    return {
        "rows": len(scored), "ic5": ic5, "t5": t5v, "ic20": ic20, "t20": t20v,
        "delta_ic": delta, "delta_t": delta_t, "top_excess_delta": float(diff.mean()), "adopt": adopt,
    }


# --------------------------------------------------------------------------- 시행 C — PEAD

def _earnings_events(store: Store, sessions: list[date]) -> pd.DataFrame:
    """`documents` 의 잠정실적 공시. 정정본은 제외(원본이 이미 사건이다)."""
    last = _last_moment(sessions)
    docs = store.get(
        "documents", as_of=last, lookback=900,
        columns=["entity_id", "valid_from", "doc_type", "title"],
    )
    if docs.empty:
        return pd.DataFrame(columns=["entity_id", "session"])
    docs = docs[
        docs["entity_id"].str.startswith("KR:")
        & (docs["doc_type"] == "earnings")
        & docs["title"].str.contains("잠정", na=False)
        & ~docs["title"].str.contains("기재정정", na=False)
    ]
    docs = docs.assign(session=pd.to_datetime(docs["valid_from"]).dt.tz_convert(SEOUL).dt.date)
    return docs[["entity_id", "session"]].drop_duplicates()


def trial_c(store: Store, *, save: bool) -> None:
    sessions = _sessions()
    tradable = _tradable()
    t5, t20 = _targets(5), _targets(20)
    close, _value, index = _panels(store, sessions)
    ar = _abnormal(close, index)
    events = _earnings_events(store, sessions)
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 잠정실적 공시 {len(events)}건")

    windows = _windows(ar, events)
    test = _tercile_test(windows)
    print(f"\nC1 이벤트 연구 — 반응 AR[t, t+1] 3분위별 드리프트 CAR[t+2, t+21] ({test['n']}건, 창이 닫힌 것만)")
    print(f"  상위 3분위 {test['top']:+.4f} · 하위 3분위 {test['bottom']:+.4f} · "
          f"차이 {test['diff']:+.4f} · NW t {test['t']:+.2f}")
    event_pass = bool(np.isfinite(test["t"]) and test["diff"] > 0 and test["t"] >= T_GATE)
    print(f"  판정: {'통과' if event_pass else '미달'} (차이 > 0 이고 NW t ≥ {T_GATE})")

    print(f"\nC2 횡단면 신호 pead_60 — 반응이 알려진 t+{DRIFT_FROM} 부터 {SIGNAL_WINDOW}세션, 부호 +")
    signal = _signal_panel(
        windows[["entity_id", "session", "reaction"]], "reaction", sessions,
        delay=DRIFT_FROM, window=SIGNAL_WINDOW,
    )
    result = _judge_signal(store, "pead", signal, tradable, t5, t20)
    result["event_study"] = test
    result["event_pass"] = event_pass
    print(f"\n판정 C: {'채택' if result['adopt'] else '기각'} — "
          f"한계기여 NW t {result.get('delta_t', float('nan')):+.2f} (기준 ≥ {T_GATE}), "
          f"상위 {TOP_N} Δ {result.get('top_excess_delta', float('nan')):+.5f}")
    if save:
        _record_trial(store, trial="C", detail=json.dumps(result, ensure_ascii=False, default=float))


# --------------------------------------------------------------------------- 시행 D — 내부자 순매수

def _insider_filings(store: Store, sessions: list[date], close: pd.DataFrame) -> pd.DataFrame:
    """`insider_trades` → (종목, 세션, 증감주수, 금액). 증감 0·결측은 뺀다."""
    last = _last_moment(sessions)
    frame = store.get(
        "insider_trades", as_of=last, lookback=900,
        columns=["entity_id", "valid_from", "change"],
    )
    if frame.empty:
        return pd.DataFrame(columns=["entity_id", "session", "change", "krw"])
    frame = frame.dropna(subset=["change"])
    frame = frame[frame["change"] != 0.0]
    # 보고일은 18:00 KST 다 — 그날 장에는 못 쓴다. 다음 세션에 붙인다.
    filed = pd.to_datetime(frame["valid_from"]).dt.tz_convert(SEOUL).dt.date
    frame = frame.assign(session=filed)
    days = pd.Index(close.index)
    frame = frame[frame["session"].isin(set(days))]
    if frame.empty:
        return pd.DataFrame(columns=["entity_id", "session", "change", "krw"])
    # **보고가 있는 (종목, 날) 만 종가를 꺼낸다.** 시세 판을 통째로 stack 하면 900일 ×
    # 3천 종목이 긴 표로 펴져 수백 MB 를 쓴다 — 여기 필요한 것은 보고 건수만큼의 값이다.
    wanted = close.reindex(index=sorted(set(frame["session"])), columns=sorted(set(frame["entity_id"])))
    prices = wanted.stack().rename("close").reset_index()
    prices.columns = ["session", "entity_id", "close"]
    merged = frame.merge(prices, on=["entity_id", "session"], how="inner")
    merged["krw"] = merged["change"] * merged["close"]
    return merged[["entity_id", "session", "change", "krw"]]


def trial_d(store: Store, *, save: bool) -> None:
    sessions = _sessions()
    tradable = _tradable()
    t5, t20 = _targets(5), _targets(20)
    close, value, index = _panels(store, sessions)
    ar = _abnormal(close, index)
    filings = _insider_filings(store, sessions, close)
    if filings.empty:
        print("\ninsider_trades 가 비어 있다(또는 판정 구간에 행이 없다).")
        print("`tools/backfill_insider_dart.py` 로 채운 뒤 다시 잰다 — 시행은 소진하지 않는다.")
        return
    buys = filings[filings["change"] > 0]
    sells = filings[filings["change"] < 0]
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 보고 {len(filings)}건 "
          f"(순매수 {len(buys)} · 순매도 {len(sells)}) · 종목 {filings['entity_id'].nunique()}")

    buy_windows = _windows(ar, buys[["entity_id", "session"]])
    sell_windows = _windows(ar, sells[["entity_id", "session"]])
    print(f"\nD1 이벤트 연구 — 보고 뒤 CAR[t+{DRIFT_FROM}, t+{DRIFT_TO}]")
    events_result: dict[str, dict[str, float]] = {}
    for label, frame in (("순매수", buy_windows), ("순매도(대조)", sell_windows)):
        if frame.empty:
            print(f"  {label}: 창이 닫힌 사건이 없다")
            events_result[label] = {"n": 0}
            continue
        ordered = frame.sort_values("session")
        series = pd.Series(ordered["car"].to_numpy())
        mean, t_value = float(series.mean()), _nw(series, DRIFT_TO)
        print(f"  {label}: {len(frame)}건 · 평균 CAR {mean:+.4f} · NW t {t_value:+.2f}")
        events_result[label] = {"n": len(frame), "car": mean, "t": t_value}
    buy_stat = events_result.get("순매수", {})
    event_pass = bool(
        buy_stat.get("n", 0) > 0
        and np.isfinite(buy_stat.get("t", float("nan")))
        and buy_stat.get("car", 0.0) > 0
        and buy_stat["t"] >= T_GATE
    )
    print(f"  판정: {'통과' if event_pass else '미달'} (순매수 CAR > 0 이고 NW t ≥ {T_GATE})")

    print(f"\nD2 횡단면 신호 insider_net_60 — 보고 다음 세션부터 {SIGNAL_WINDOW}세션 누적 / 20일 평균 거래대금, 부호 +")
    signal = _flow_panel(filings, value, close, sessions, delay=1, window=SIGNAL_WINDOW)
    result = _judge_signal(store, "insider", signal, tradable, t5, t20)
    result["event_study"] = events_result
    result["event_pass"] = event_pass
    print(f"\n판정 D: {'채택' if result['adopt'] else '기각'} — "
          f"한계기여 NW t {result.get('delta_t', float('nan')):+.2f} (기준 ≥ {T_GATE}), "
          f"상위 {TOP_N} Δ {result.get('top_excess_delta', float('nan')):+.5f}")
    if save:
        _record_trial(store, trial="D", detail=json.dumps(result, ensure_ascii=False, default=float))


# --------------------------------------------------------------------------- 시행 E — 월 리밸런스

def _hold(group: pd.DataFrame, held: set[str], *, exit_rank: int | None) -> set[str]:
    """그날의 보유 집합. ``exit_rank`` 가 있으면 완충 구간(그 순위 안이면 유지)."""
    ranked = group.sort_values("score", ascending=False).reset_index(drop=True)
    order = list(ranked["entity_id"])
    rank = {entity: i + 1 for i, entity in enumerate(order)}
    if exit_rank is None:
        return set(order[:TOP_N])
    keep = sorted((e for e in held if rank.get(e, len(order) + 1) <= exit_rank), key=lambda e: rank[e])
    out = keep[:TOP_N]
    for entity in order[:TOP_N]:
        if len(out) >= TOP_N:
            break
        if entity not in out:
            out.append(entity)
    return set(out)


def _policy_net_ir(
    scored: pd.DataFrame, targets_h1: pd.DataFrame, *, interval: int, exit_rank: int | None
) -> dict[str, float]:
    """정책 하나의 순IR·연회전. 비용은 `_net_ir` 과 같은 규약(편도 0.41% × 왕복 / 24)."""
    merged = scored.merge(targets_h1, on=["entity_id", "session"], how="inner").dropna(subset=["score", "target"])
    held: set[str] = set()
    daily, turnover = [], []
    for step, (_session, group) in enumerate(merged.groupby("session")):
        if step % interval == 0 or not held:
            new_held = _hold(group, held, exit_rank=exit_rank)
        else:
            new_held = held  # 리밸런스 날이 아니면 그대로 든다
        swapped = len(new_held - held) if held else 0
        cost = swapped * ONE_WAY_COST * 2 / TOP_N
        picked = group[group["entity_id"].isin(new_held)]["target"]
        daily.append((float(picked.mean()) if len(picked) else 0.0) - cost)
        turnover.append(swapped / TOP_N)
        held = new_held
    returns = pd.Series(daily)
    return {
        "net_annual_return": float(returns.mean() * 252),
        "net_ir": float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else float("nan"),
        "annual_turnover": float(np.mean(turnover) * 252),
        "days": int(returns.size),
    }


def trial_e(store: Store, *, save: bool) -> None:
    t1 = _targets(1)
    fundamental = _scores("fundamental")
    sessions = sorted(set(fundamental["session"]))
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 점수 {len(fundamental)}행 · "
          f"비용 편도 {ONE_WAY_COST:.2%}")

    plain = _net_ir(fundamental, t1)
    current = _policy_net_ir(fundamental, t1, interval=1, exit_rank=EXIT_RANK)
    variant = _policy_net_ir(fundamental, t1, interval=REBALANCE_INTERVAL, exit_rank=None)
    table = pd.DataFrame(
        [
            {"정책": f"참고: 매일 상위{TOP_N}(완충 없음)", **plain},
            {"정책": f"현행: 매일 상위{TOP_N} · 퇴출 {EXIT_RANK}위", **current},
            {"정책": f"변형: {REBALANCE_INTERVAL}세션마다 상위{TOP_N}", **variant},
        ]
    )
    print()
    print(
        table[["정책", "net_annual_return", "net_ir", "annual_turnover", "days"]]
        .rename(columns={"net_annual_return": "순수익(연율)", "net_ir": "순IR", "annual_turnover": "연회전", "days": "일수"})
        .round(4).to_string(index=False)
    )

    adopt = bool(
        np.isfinite(variant["net_ir"]) and np.isfinite(current["net_ir"])
        and variant["net_ir"] >= current["net_ir"]
        and variant["annual_turnover"] <= current["annual_turnover"] / 2
    )
    print(f"\n판정 E: {'채택' if adopt else '기각'} — 변형 순IR {variant['net_ir']:+.2f} vs 현행 "
          f"{current['net_ir']:+.2f} (≥ 필요), 연회전 {variant['annual_turnover']:.2f} vs "
          f"{current['annual_turnover'] / 2:.2f} (이하 필요)")
    result = {"plain": plain, "current": current, "variant": variant, "adopt": adopt}
    if save:
        _record_trial(store, trial="E", detail=json.dumps(result, ensure_ascii=False, default=float))


# --------------------------------------------------------------------------- 시행 H — 국장 공매도

#: 시행 H 표본 규칙 — 판정 세션이 이보다 적으면 결과는 채택도 기각도 아닌 **보류**다.
MIN_SESSIONS_H = 120
SHORT_LONG_WINDOW = 20
SHORT_SHORT_WINDOW = 5


def _shorting_panel(store: Store, sessions: list[date]) -> pd.DataFrame:
    """`shorting.short_ratio` 를 **알 수 있게 된 세션** 축으로 편 판(세션 × 종목).

    행의 valid_from 은 거래일이지만 공표(observed_at)는 T+2~4 16:00 이다. valid_from 축으로
    펴면 공표 전 값을 그날 쓰는 것이 되어 미래를 본다. 그래서 각 행을 observed_at 뒤 **첫
    세션**에 붙인다 — 같은 종목의 두 행이 같은 세션에 붙으면 valid_from 이 늦은 쪽이 남는다.
    """
    last = _last_moment(sessions)
    frame = store.get(
        "shorting", as_of=last, lookback=900, market=MARKET,
        columns=["entity_id", "valid_from", "observed_at", "short_ratio"],
    )
    if frame.empty:
        return pd.DataFrame()
    frame = frame.dropna(subset=["short_ratio"])
    observed = pd.to_datetime(frame["observed_at"]).dt.tz_convert(SEOUL).dt.date
    days = np.array(sessions)
    # observed_at 이 16:00 이므로 그날은 못 쓴다 — 그 날짜보다 **뒤** 첫 세션.
    pos = np.searchsorted(days, observed.to_numpy(), side="right")
    frame = frame.assign(available=[days[i] if i < len(days) else None for i in pos])
    frame = frame.dropna(subset=["available"]).sort_values("valid_from")
    return frame.pivot_table(
        index="available", columns="entity_id", values="short_ratio", aggfunc="last"
    ).reindex(sessions)


def trial_h(store: Store, *, save: bool) -> None:
    sessions = _sessions()
    tradable = _tradable()
    t5, t20 = _targets(5), _targets(20)
    panel = _shorting_panel(store, sessions)
    if panel.empty or panel.notna().any(axis=1).sum() == 0:
        print("\nshorting 이 비어 있다(또는 판정 구간에 행이 없다). 시행은 소진하지 않는다.")
        return
    covered = panel.index[panel.notna().any(axis=1)]
    # 창은 **관측이 있는 세션**끼리 굴린다. 공백(수집 중단) 뒤 첫 세션이 옛 값을 끌고 오지
    # 않도록 min_periods 를 두고, 값이 없는 세션은 신호도 없다.
    long_mean = panel.rolling(SHORT_LONG_WINDOW, min_periods=SHORT_LONG_WINDOW // 2).mean()
    short_mean = panel.rolling(SHORT_SHORT_WINDOW, min_periods=SHORT_SHORT_WINDOW).mean()
    n_judge = int(long_mean.notna().any(axis=1).sum())
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 공매도 관측 세션 {len(covered)} "
          f"({covered[0]}~{covered[-1]}) · 종목 {panel.shape[1]} · 신호 있는 세션 {n_judge}")

    def _long(wide: pd.DataFrame) -> pd.DataFrame:
        out = wide.stack().rename("signal").reset_index()
        out.columns = ["session", "entity_id", "signal"]
        return out[["entity_id", "session", "signal"]]

    # 부호 − : 공매도가 많을수록 점수가 낮아야 한다. 순위 정규화 앞에서 뒤집는다.
    print(f"\nH1 short_ratio_20 — 최근 {SHORT_LONG_WINDOW}세션 공매도 비중 평균, 부호 −")
    primary = _judge_signal(store, "shorting", _long(-long_mean), tradable, t5, t20)
    print(f"\nH2 (탐색·기록만) short_ratio_chg — {SHORT_SHORT_WINDOW}세션 평균 − {SHORT_LONG_WINDOW}세션 평균, 부호 −")
    secondary = _judge_signal(store, "shorting_chg", _long(-(short_mean - long_mean)), tradable, t5, t20)

    thin = n_judge < MIN_SESSIONS_H
    if thin:
        verdict = "보류"
    else:
        verdict = "채택" if primary["adopt"] else "기각"
    print(f"\n판정 H: {verdict} — 판정 세션 {n_judge} (기준 ≥ {MIN_SESSIONS_H}), "
          f"한계기여 NW t {primary.get('delta_t', float('nan')):+.2f} (기준 ≥ {T_GATE})")
    if thin:
        print("  표본 규칙: 120세션 미만은 채택도 기각도 아니다. 백필이 되살아나 표본이 차면 다시 잰다.")
    result = {"sessions": n_judge, "primary": primary, "secondary": secondary, "verdict": verdict}
    if save and not thin:
        _record_trial(store, trial="H", detail=json.dumps(result, ensure_ascii=False, default=float))
    elif save:
        print("  --save 무시 — 표본이 얇아 시행을 소진하지 않는다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", choices=["C", "D", "E", "H"], required=True)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    store = Store(root=Path(args.root))
    print(f"=== 시행 {args.trial} — {PROTOCOL} (판정은 {HOLDOUT_START} 이전 세션만) ===")
    {"C": trial_c, "D": trial_d, "E": trial_e, "H": trial_h}[args.trial](store, save=args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
