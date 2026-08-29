"""사전등록 시행 A·B — docs/protocols/analyst-features-2026-09.md 대로 잰다.

    .venv/bin/python tools/trial_analyst_features.py --trial A [--save]
    .venv/bin/python tools/trial_analyst_features.py --trial B [--save]

기준을 여기서 바꾸지 않는다. 채택 기준은 프로토콜 문서에 있고, 이 도구는 숫자만 낸다.
캐시: data/_diag (KR 300세션 · 점수 · h1/h5/h20 타깃). 판정은 2026-06-30 이전 세션만.
--save 는 research_trials 에 family `analyst` 1행을 적는다(시행 1회 소진).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts.base import rank_score  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.report_feature_ic import benjamini_hochberg, two_sided_p  # noqa: E402

CACHE = Path("data/_diag")
MARKET = "KR"
HOLDOUT_START = date(2026, 7, 1)
TOP_N = 24
ONE_WAY_COST = 0.0041          # 편도 비용 (선정 시행 3 과 같은 값)
T_GATE = 2.0
FDR = 0.10
SEOUL = ZoneInfo("Asia/Seoul")
PROTOCOL = Path("docs/protocols/analyst-features-2026-09.md")

#: 시행 A 후보 피처와 사전 고정 부호.
CHART_SIGNS = {"momentum_12_1": +1, "reversal_21": -1, "high_52w": +1, "idio_momentum_120": +1}


def _sessions() -> list[date]:
    cal = pd.read_pickle(CACHE / f"calendar-{MARKET}.pkl")
    days = sorted(pd.to_datetime(cal["session"]).dt.date)
    return [d for d in days if d < HOLDOUT_START]


def _targets(h: int) -> pd.DataFrame:
    t = pd.read_pickle(CACHE / f"targets-{MARKET}-h{h}.pkl")
    t["session"] = pd.to_datetime(t["session"]).dt.date
    return t[t["session"] < HOLDOUT_START]


def _scores(name: str) -> pd.DataFrame:
    s = pd.read_pickle(CACHE / f"scores-{name}-{MARKET}.pkl")
    s["session"] = pd.to_datetime(s["session"]).dt.date
    return s[s["session"] < HOLDOUT_START]


def _tradable() -> set[str] | None:
    path = CACHE / f"tradable-{MARKET}.pkl"
    if not path.exists():
        return None
    obj = pd.read_pickle(path)
    if isinstance(obj, pd.DataFrame):
        return set(obj["entity_id"].astype(str))
    return set(map(str, obj))


def _nw(series: pd.Series, lag: int) -> float:
    return float(ic_module.newey_west_t(series.dropna(), lag=lag)) if series.notna().sum() > 3 else float("nan")


def _feature_ic(frame: pd.DataFrame, column: str, targets: pd.DataFrame, h: int) -> tuple[float, float, int]:
    merged = frame[["entity_id", "session", column]].rename(columns={column: "score"}).merge(
        targets, on=["entity_id", "session"], how="inner"
    ).dropna()
    daily = ic_module.daily_ic(merged)
    if daily.empty:
        return float("nan"), float("nan"), 0
    return float(daily.mean()), _nw(daily, h - 1), int(daily.size)


def _top_n_excess(scored: pd.DataFrame, targets: pd.DataFrame) -> pd.Series:
    """세션별 상위 N 동일가중 타깃 − 세션 전체 평균 타깃."""
    merged = scored.merge(targets, on=["entity_id", "session"], how="inner").dropna(subset=["score", "target"])
    out = {}
    for session, group in merged.groupby("session"):
        top = group.nlargest(TOP_N, "score")
        out[session] = float(top["target"].mean() - group["target"].mean())
    return pd.Series(out).sort_index()


def _net_ir(scored: pd.DataFrame, targets_h1: pd.DataFrame) -> dict[str, float]:
    """상위 24 매일 재선정 · h1 동일가중 수익 · 교체 비용 차감 → 연율 IR."""
    merged = scored.merge(targets_h1, on=["entity_id", "session"], how="inner").dropna(subset=["score", "target"])
    prev: set[str] = set()
    daily, turnover = [], []
    for _session, group in merged.groupby("session"):
        held = set(group.nlargest(TOP_N, "score")["entity_id"])
        swapped = len(held - prev) if prev else 0
        cost = swapped * ONE_WAY_COST * 2 / TOP_N
        daily.append(float(group[group["entity_id"].isin(held)]["target"].mean()) - cost)
        turnover.append(swapped / TOP_N)
        prev = held
    r = pd.Series(daily)
    return {
        "net_annual_return": float(r.mean() * 252), "net_ir": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else float("nan"),
        "annual_turnover": float(np.mean(turnover) * 252), "days": int(r.size),
    }


def _combined(parts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for frame in parts.values():
        f = frame[["entity_id", "session", "score"]].copy()
        f["z"] = ic_module.cross_sectional_z(f, "score")
        frames.append(f.dropna(subset=["z"]))
    stacked = pd.concat(frames, ignore_index=True)
    return stacked.groupby(["entity_id", "session"], as_index=False)["z"].mean().rename(columns={"z": "score"})


def _record_trial(store: Store, *, trial: str, detail: str) -> None:
    now = datetime.now(UTC)  # invariant-allow: wallclock
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else ""
    store.append("research_trials", [{
        "entity_id": f"analyst-features-2026-09:{trial}", "valid_from": now, "observed_at": now,
        "source": "trial_analyst_features", "market": MARKET, "family": "analyst", "n_trials": 1,
        "protocol_hash": digest, "detail": detail[:900],
    }], ingest_run_id=f"trial-analyst-{trial}-{now:%Y%m%dT%H%M%S}")
    print(f"\nresearch_trials 기록: analyst/{trial} · protocol {digest}")


# --------------------------------------------------------------------------- 시행 A

def _price_panels(store: Store, sessions: list[date]) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    last = datetime.combine(sessions[-1], time(15, 40), tzinfo=SEOUL)
    frame = read_prices(
        store, as_of=last, lookback=900, market=MARKET,  # 252일 창 피처가 첫 세션부터 서게 (560 은 171일만 남겼다)
        columns=["entity_id", "valid_from", "close", "volume"], adjusted=True,
    )
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.tz_convert(SEOUL).dt.date
    close = frame.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    volume = frame.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last").sort_index()
    index_id = str(store.config("benchmark.kr_index", as_of=last))
    idx = store.get("indices", as_of=last, lookback=900, entity=index_id, columns=["valid_from", "close"])
    idx["day"] = pd.to_datetime(idx["valid_from"]).dt.tz_convert(SEOUL).dt.date
    index = idx.groupby("day")["close"].last().sort_index()
    return close, volume, index


def _chart_features(close: pd.DataFrame, index: pd.Series, sessions: list[date]) -> pd.DataFrame:
    close = close.where(close > 0)
    ret = close.pct_change()
    index = index.reindex(close.index).ffill()
    iret = index.pct_change()
    mom_12_1 = close.shift(21) / close.shift(252) - 1.0
    rev_21 = close / close.shift(21) - 1.0
    high_52 = close / close.rolling(252, min_periods=200).max()
    ret120 = close / close.shift(120) - 1.0
    iret120 = index / index.shift(120) - 1.0
    cov = ret.rolling(120, min_periods=90).cov(iret)
    var = iret.rolling(120, min_periods=90).var()
    beta = cov.div(var, axis=0)
    idio = ret120.sub(beta.mul(iret120, axis=0))
    rows = []
    for session in sessions:
        if session not in close.index:
            continue
        rows.append(pd.DataFrame({
            "entity_id": close.columns, "session": session,
            "momentum_12_1": mom_12_1.loc[session].to_numpy(), "reversal_21": rev_21.loc[session].to_numpy(),
            "high_52w": high_52.loc[session].to_numpy(), "idio_momentum_120": idio.loc[session].to_numpy(),
        }))
    return pd.concat(rows, ignore_index=True)


def _volume_variants(volume: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    ratio = volume.rolling(5).mean() / volume.rolling(60, min_periods=40).mean() - 1.0
    log_ratio = np.log1p(ratio.clip(lower=-0.99))
    z60 = (ratio - ratio.rolling(60, min_periods=40).mean()) / ratio.rolling(60, min_periods=40).std()
    rows = []
    for session in sessions:
        if session not in volume.index:
            continue
        rows.append(pd.DataFrame({
            "entity_id": volume.columns, "session": session,
            "volume_surge": ratio.loc[session].to_numpy(), "volume_surge_log": log_ratio.loc[session].to_numpy(),
            "volume_surge_z60": z60.loc[session].to_numpy(),
        }))
    return pd.concat(rows, ignore_index=True)


def trial_a(store: Store, *, save: bool) -> None:
    sessions = _sessions()
    tradable = _tradable()
    t5, t20 = _targets(5), _targets(20)
    close, volume, index = _price_panels(store, sessions)
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 종목 {close.shape[1]} · 유동성 통과 {len(tradable) if tradable else '전체'}")

    chart = _chart_features(close, index, sessions)
    vol = _volume_variants(volume, sessions)
    for frame in (chart, vol):
        if tradable is not None:
            frame.drop(frame[~frame["entity_id"].isin(tradable)].index, inplace=True)
    # 세션별 순위 정규화 — Analyst 가 실제로 쓰는 변환(base.rank_score)
    for col in CHART_SIGNS:
        chart[col] = chart.groupby("session")[col].transform(rank_score)
    for col in ("volume_surge", "volume_surge_log", "volume_surge_z60"):
        vol[col] = vol.groupby("session")[col].transform(rank_score)

    print("\nA1 chart 후보 4피처 — 개별 IC (h5 · h20), 부호는 사전 고정")
    rows = []
    for col, sign in CHART_SIGNS.items():
        ic5, t5v, n5 = _feature_ic(chart, col, t5, 5)
        ic20, t20v, _ = _feature_ic(chart, col, t20, 20)
        rows.append({"feature": col, "부호": "+" if sign > 0 else "−", "IC5": ic5, "t5": t5v, "IC20": ic20, "t20": t20v,
                     "일수": n5, "_p": two_sided_p(t5v, n5) if n5 else 1.0, "_sign_ok": np.sign(ic5) == sign})
    table = pd.DataFrame(rows)
    table["BH"] = benjamini_hochberg(table["_p"], fdr=FDR)
    table["통과"] = table["_sign_ok"] & (table["t5"].abs() >= T_GATE) & table["BH"]
    print(table[["feature", "부호", "IC5", "t5", "IC20", "t20", "일수", "BH", "통과"]].round(4).to_string(index=False))
    passed = [c for c, ok in zip(table["feature"], table["통과"], strict=True) if ok]

    print("\nA2 volume 변형 — h5")
    vrows = []
    for col in ("volume_surge", "volume_surge_log", "volume_surge_z60"):
        ic5, t5v, n5 = _feature_ic(vol, col, t5, 5)
        vrows.append({"variant": col, "IC5": ic5, "t5": t5v, "일수": n5})
    vtable = pd.DataFrame(vrows)
    print(vtable.round(4).to_string(index=False))
    best_vol = vtable.loc[vtable["t5"].idxmax()] if not vtable.empty else None
    vol_pass = best_vol is not None and best_vol["t5"] >= T_GATE

    fundamental = _scores("fundamental")
    result: dict[str, object] = {"chart_passed": passed, "volume_best": None if best_vol is None else str(best_vol["variant"]), "volume_pass": bool(vol_pass)}
    if passed:
        chart["score"] = sum(CHART_SIGNS[c] * chart[c] for c in passed) / len(passed)
        chart_new = chart[["entity_id", "session", "score"]]
        shares, details = ic_module.marginal_shares({"fundamental": fundamental, "chart_new": chart_new}, t5, horizon=5, t_min=T_GATE)
        delta, t_value = details["chart_new"]
        base_ex = _top_n_excess(fundamental, t5)
        comb_ex = _top_n_excess(_combined({"fundamental": fundamental, "chart_new": chart_new}), t5)
        diff = (comb_ex - base_ex).dropna()
        print(f"\n결합 — chart_new 의 fundamental 위 한계기여 ΔIC(h5) {delta:+.4f} · NW t {t_value:+.2f} · 가중치 share {shares['chart_new']:.2f}")
        print(f"상위 {TOP_N} h5 초과수익: 기준 {base_ex.mean():+.5f} · 결합 {comb_ex.mean():+.5f} · Δ {diff.mean():+.5f} (NW t {_nw(diff, 4):+.2f})")
        adopt = delta > 0 and t_value >= T_GATE and diff.mean() >= 0
        result.update({"delta_ic": delta, "delta_t": t_value, "top_excess_base": float(base_ex.mean()), "top_excess_combined": float(comb_ex.mean()), "adopt_chart": bool(adopt)})
        print(f"\n판정 A1: {'채택' if adopt else '기각'} (피처 t≥2·BH 통과 {len(passed)}개, 한계기여 t {t_value:+.2f}, 상위24 Δ {diff.mean():+.5f})")
    else:
        result["adopt_chart"] = False
        print("\n판정 A1: 기각 — 개별 IC 를 통과한 chart 피처가 없다")
    print(f"판정 A2: {'채택 후보 ' + str(result['volume_best']) if vol_pass else '기각 — volume 은 관찰 유지'}")
    if save:
        _record_trial(store, trial="A", detail=json.dumps(result, ensure_ascii=False, default=float))


# --------------------------------------------------------------------------- 시행 B

def _residualize(event: pd.DataFrame, fundamental: pd.DataFrame) -> pd.DataFrame:
    e = event[["entity_id", "session", "score"]].copy()
    e["ez"] = ic_module.cross_sectional_z(e, "score")
    f = fundamental[["entity_id", "session", "score"]].copy()
    f["fz"] = ic_module.cross_sectional_z(f, "score")
    m = e.merge(f[["entity_id", "session", "fz"]], on=["entity_id", "session"], how="inner").dropna(subset=["ez", "fz"])
    out = []
    for session, g in m.groupby("session"):
        x, y = g["fz"].to_numpy(), g["ez"].to_numpy()
        if len(g) < 30 or np.var(x) == 0:
            continue
        beta = np.cov(x, y)[0, 1] / np.var(x)
        resid = y - beta * x
        out.append(pd.DataFrame({"entity_id": g["entity_id"].to_numpy(), "session": session, "score": resid}))
    return pd.concat(out, ignore_index=True)


def _event_window(event_features: pd.DataFrame, event_scores: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """피처 벡터가 최근 20세션 안에 바뀐 종목만 점수를 남긴다 — 사건 창의 대용이다.
    (사건 날짜 표가 따로 없어 피처 변화를 사건 발생으로 읽는다. 프로토콜과의 차이로 적는다.)"""
    cols = [c for c in event_features.columns if c not in ("entity_id", "session")]
    f = event_features.sort_values(["entity_id", "session"]).copy()
    changed = f.groupby("entity_id")[cols].diff().abs().sum(axis=1) > 1e-9
    f["changed"] = changed.to_numpy()
    f["recent"] = f.groupby("entity_id")["changed"].transform(lambda s: s.rolling(window, min_periods=1).max())
    keep = f.loc[f["recent"] > 0, ["entity_id", "session"]]
    return event_scores.merge(keep, on=["entity_id", "session"], how="inner")


def trial_b(store: Store, *, save: bool) -> None:
    t5, t20, t1 = _targets(5), _targets(20), _targets(1)
    fundamental, event = _scores("fundamental"), _scores("event")
    ef = pd.read_pickle(CACHE / f"features-event-{MARKET}.pkl")
    ef["session"] = pd.to_datetime(ef["session"]).dt.date
    ef = ef[ef["session"] < HOLDOUT_START]
    variants = {"B1 직교화 잔차": _residualize(event, fundamental), "B2 사건창 20세션": _event_window(ef, event)}
    base_ir = _net_ir(fundamental, t1)
    base_ex = _top_n_excess(fundamental, t5)
    print(f"기준(fundamental): 상위{TOP_N} h5 초과 {base_ex.mean():+.5f} · 순IR {base_ir['net_ir']:+.2f} · 연회전 {base_ir['annual_turnover']:.2f}")
    result: dict[str, object] = {}
    for label, frame in variants.items():
        ic5, t5v, n5 = _feature_ic(frame.rename(columns={"score": "s"}), "s", t5, 5)
        ic20, t20v, _ = _feature_ic(frame.rename(columns={"score": "s"}), "s", t20, 20)
        _shares, details = ic_module.marginal_shares({"fundamental": fundamental, "event_v": frame}, t5, horizon=5, t_min=T_GATE)
        delta, dt = details["event_v"]
        combined = _combined({"fundamental": fundamental, "event_v": frame})
        comb_ex = _top_n_excess(combined, t5)
        comb_ir = _net_ir(combined, t1)
        adopt = delta > 0 and dt >= T_GATE and t20v > -T_GATE and comb_ir["net_ir"] >= base_ir["net_ir"]
        print(f"\n{label}: IC5 {ic5:+.4f} (t {t5v:+.2f}, {n5}일) · IC20 {ic20:+.4f} (t {t20v:+.2f}) · "
              f"한계기여 ΔIC {delta:+.4f} (t {dt:+.2f}) · 상위24 h5 초과 {comb_ex.mean():+.5f} · 순IR {comb_ir['net_ir']:+.2f} · 연회전 {comb_ir['annual_turnover']:.2f}")
        print(f"  판정: {'채택' if adopt else '기각'}")
        result[label] = {"ic5": ic5, "t5": t5v, "ic20": ic20, "t20": t20v, "delta_ic": delta, "delta_t": dt, "net_ir": comb_ir["net_ir"], "adopt": bool(adopt)}
    if save:
        _record_trial(store, trial="B", detail=json.dumps(result, ensure_ascii=False, default=float))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", choices=["A", "B"], required=True)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    store = Store(root=Path(args.root))
    print(f"=== 시행 {args.trial} — {PROTOCOL} (판정은 {HOLDOUT_START} 이전 세션만) ===")
    (trial_a if args.trial == "A" else trial_b)(store, save=args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
