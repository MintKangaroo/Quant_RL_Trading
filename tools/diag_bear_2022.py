"""2022 하락장 건강검진 — 지금 전략이 하락장에서 어떻게 했을까. docs/diag/bear-2022-checkup.md §1 대로 한 번 잰다.

    nice -n 10 taskset -c 0-3 .venv/bin/python tools/diag_bear_2022.py

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
결과 원본은 `data/_diag/bear-2022/` 에 둔다.

규칙은 베끼지 않는다: 패널·rank-gauss 는 `trial_ranker_kit.judge_panel`, 모델은 `final_round_kit.walk_gbm`
(퍼지 5 + 엠바고 5 · 시행 L 하이퍼파라미터), 포트는 `trial_ranker_kit.portfolio`(EMA5 · 24 · 완충 3N · 비용 0.41%,
`every=10`), 국면은 실전 순수 함수 `analysts.regime.classify`, 배수는 `selector.exposure.regime_scale`, 노출 적용 식은
시행 V(`trial_exposure_axes.evaluate`)와 같다. 이 진단이 바꾸는 것은 **최소 학습창 하나**(첫 블록 시작) — 문서 §1.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts.regime import CRISIS_FLOOR_KEY, LOOKBACK_DAYS, MARKET_PROXIES, classify  # noqa: E402
from quant_rl_trading.selector.exposure import FLOOR, ExposureParams, regime_scale  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools import final_round_kit as fkit  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, ONE_WAY_COST, metrics  # noqa: E402

OUT = Path("data/_diag/bear-2022")
#: 점수 패널(chart)의 첫 날. 그 앞은 6점수가 다 안 찬다.
PANEL_START = date(2021, 11, 10)
#: 패널 끝 — 마지막 구간(~2023-01-31) 의 t+2 수익과 y5 라벨이 찰 만큼만.
PANEL_END = date(2023, 2, 28)
#: 첫 블록 시작(결정 세션). 첫 하락일 2022-01-03 이 t+1 인 세션의 한 세션 앞 — 문서 §1.
FIRST_BLOCK = date(2021, 12, 29)
#: 문서 §1 의 구간. 실현 구간(t+1 종가 → t+2 종가)이 (시작, 끝] 안에 통째로 드는 날만 넣는다.
PEAK, TROUGH, REBOUND_END = date(2022, 1, 3), date(2022, 9, 30), date(2023, 1, 31)
WINDOWS = {"하락장": (PEAK, TROUGH), "반등": (TROUGH, REBOUND_END), "전체": (PEAK, REBOUND_END)}
REGIME_INDEX = MARKET_PROXIES[0]          # 실전 RegimeAnalyst 의 첫 대용 — KRX 300
SEEDS = fkit.SEEDS


def blocks_from(sessions: list[date], first_day: date) -> list[tuple[int, int]]:
    """`trial_ranker_kit.blocks` 와 같은 20세션 블록이되 첫 블록 시작만 `first_day` 로 당긴다(문서 §1)."""
    out, start = [], sessions.index(first_day)
    while start + rkit.BLOCK <= len(sessions):
        out.append((start, start + rkit.BLOCK - 1))
        start += rkit.BLOCK
    if start < len(sessions):                 # 남은 꼬리도 판정한다 — 반등 구간 끝까지 덮어야 한다
        out.append((start, len(sessions) - 1))
    return out


def exposure_scales(store: Store, days: list[date], as_of: datetime,
                    index_id: str = REGIME_INDEX) -> tuple[pd.Series, pd.Series]:
    """결정 세션마다 V6 배수와 국면. 국면은 **그 세션 전날 종가까지**(라이브 08:40 세션과 같은 지연).

    창고의 KRX 300(·KRX 100·TMI) 은 2022-04-21 부터라 실전 대용으로는 2022 하락장 내내 120세션 이력이 안 찬다 —
    국면이 unknown(배수 1.0)으로 남는다. `--supplement` 는 이력이 있는 K200 으로 같은 규칙을 다시 잰다(사후 보충, 문서 §2).
    """
    params = ExposureParams.from_store(store, as_of=as_of)
    floor = float(store.config(CRISIS_FLOOR_KEY, as_of=as_of))
    frame = store.get("indices", as_of=as_of, entity=[index_id], market="KR",
                      lookback=(as_of.date() - days[0]).days + LOOKBACK_DAYS + 40,
                      columns=["entity_id", "valid_from", "close", "revision"])
    closes = (frame.sort_values(["valid_from", "revision"]).assign(day=lambda f: f["valid_from"].dt.date)
              .groupby("day")["close"].last().astype(float).sort_index())
    print(f"노출 설정(오늘 값): 국면 배수 {params.regime_scale} · 확인 {params.regime_confirm_sessions}세션 · "
          f"crisis 하한 {floor:+.0%} · 바닥 {FLOOR:.0%} · 지수 {index_id} {closes.index[0]}~", flush=True)
    recent: list[str] = []
    scales, states = {}, {}
    for day in days:
        hist = closes[(closes.index < day) & (closes.index > day - timedelta(days=LOOKBACK_DAYS))]
        state = classify(hist, crisis_floor=floor)
        scale, _ = regime_scale(state, params, recent_states=tuple(recent))
        recent = (recent + [state])[-max(1, params.regime_confirm_sessions):]
        scales[day], states[day] = max(FLOOR, float(scale)), state
    return pd.Series(scales), pd.Series(states)


def apply_exposure(net: pd.Series, scale: pd.Series) -> tuple[pd.Series, pd.Series]:
    """시행 V 의 식 — r = s·net − |Δs|·비용. 반환은 (수익, 노출 전환 비용)."""
    s = scale.reindex(net.index).fillna(1.0)
    prev = s.shift(1).fillna(1.0)
    switch = (s - prev).abs() * ONE_WAY_COST
    return s * net - switch, switch


def realized_end(days: list[date], calendar: list[date]) -> dict[date, tuple[date, date]]:
    """결정 세션 t → (t+1, t+2). 포트 수익이 실현되는 종가 구간이다."""
    pos = {d: i for i, d in enumerate(calendar)}
    out = {}
    for d in days:
        i = pos.get(d)
        if i is not None and i + 2 < len(calendar):
            out[d] = (calendar[i + 1], calendar[i + 2])
    return out


def in_window(index: pd.Index, spans: dict[date, tuple[date, date]], window: tuple[date, date]) -> list[date]:
    lo, hi = window
    return [d for d in index if d in spans and spans[d][0] >= lo and spans[d][1] <= hi]


def side_beta(r: pd.Series, b: pd.Series, *, down: bool) -> float:
    mask = b < 0 if down else b > 0
    x, y = b[mask], r[mask]
    return float(np.cov(y, x)[0, 1] / x.var()) if len(x) > 2 and x.var() > 0 else float("nan")


def describe(r: pd.Series, d1: pd.Series, d2: pd.Series, days: list[date], *,
             scale: pd.Series | None = None, turn: float | None = None, cost: float | None = None,
             ic: float | None = None) -> dict[str, float]:
    r, b1, b2 = r.reindex(days).fillna(0.0), d1.reindex(days).fillna(0.0), d2.reindex(days).fillna(0.0)
    m = metrics(r, b1)
    up, dn = capture(r, b1)
    cum = float((1 + r).prod() - 1)
    out = {
        "n": len(days), "cum": cum, "ann": m["ann"], "mdd": m["mdd"], "vol": m["vol"],
        "ex_d1": cum - float((1 + b1).prod() - 1), "ex_d2": cum - float((1 + b2).prod() - 1), "ir_d1": m["ir"],
        "beta": m["beta"], "beta_down": side_beta(r, b1, down=True), "beta_up": side_beta(r, b1, down=False),
        "cap_down": dn, "cap_up": up,
        "weight": float(scale.reindex(days).fillna(1.0).mean()) if scale is not None else 1.0,
    }
    if turn is not None:
        out["turn"] = turn
    if cost is not None:
        out["cost"] = cost
    if ic is not None:
        out["ic"] = ic
    return out


def supplement(index_id: str) -> int:
    """**사후 보충**(문서 §2) — 본 측정의 B 일수익(daily.pkl)에 V6 규칙을 ``index_id`` 국면으로 다시 씌운다. 모델·포트는 다시 안 돈다."""
    store = Store(root=Path("data"))
    now = datetime.now(UTC)  # invariant-allow: wallclock — 오늘의 설정을 읽는 시각(본 측정과 같다)
    daily = pd.read_pickle(OUT / "daily.pkl")  # invariant-allow: data-access — 진단 원본
    days = list(daily.index)
    scale, state = exposure_scales(store, days, now, index_id=index_id)
    a, _ = apply_exposure(daily["B"], scale)
    spans = {d: (days[i + 1], days[i + 2]) for i, d in enumerate(days[:-2])}
    print(f"\n=== 사후 보충 A′ — V6 국면 지수 {index_id} ===")
    print("| 구간 | A′ 누적 | B 누적 | A′−B | A′ MDD | B MDD | A′ 하방β | A′ 하락포착 | 평균 주식비중 | 배수<1 세션 | 국면 분포 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for wname, window in WINDOWS.items():
        wd = in_window(daily.index, spans, window)
        ra = describe(a, daily["D1"], daily["D2"], wd, scale=scale)
        rb = describe(daily["B"], daily["D1"], daily["D2"], wd)
        share = state.reindex(wd).value_counts(normalize=True)
        print(f"| {wname} | {ra['cum']:+.1%} | {rb['cum']:+.1%} | {ra['cum'] - rb['cum']:+.1%} | {ra['mdd']:+.1%} | "
              f"{rb['mdd']:+.1%} | {ra['beta_down']:.2f} | {ra['cap_down']:.2f} | {ra['weight']:.0%} | "
              f"{float((scale.reindex(wd) < 1.0).mean()):.0%} | " + " ".join(f"{k} {v:.0%}" for k, v in share.items()) + " |")
    months = pd.Series({spans[d][1]: scale[d] for d in days if d in spans})
    months.index = pd.to_datetime(months.index)
    print("월별 평균 주식 비중(A′, 실현월):",
          " ".join(f"{k}:{v:.0%}" for k, v in months.groupby(months.index.to_period("M")).mean().items()))
    pd.DataFrame({"A_prime": a, "scale": scale, "state": state}).to_pickle(OUT / f"supplement-{index_id.split(':')[-1]}.pkl")  # invariant-allow: data-access — 진단 원본
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--supplement":
        return supplement(sys.argv[2])
    OUT.mkdir(parents=True, exist_ok=True)
    store = Store(root=Path("data"))
    now = datetime.now(UTC)  # invariant-allow: wallclock — "오늘의 설정" 을 읽는 시각(문서 §1: 오늘 규칙을 2022 에 소급)
    panel, sessions = rkit.judge_panel(start=PANEL_START, end=PANEL_END)
    bl = blocks_from(sessions, FIRST_BLOCK)
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 블록 {len(bl)}개 · "
          f"첫 블록 {sessions[bl[0][0]]} 학습 끝 {fkit.train_end(sessions, bl[0][0])}", flush=True)

    preds = fkit.walk_gbm(panel, sessions, list(rkit.FEATS), bl, SEEDS, label="bear-2022")
    y = panel[["entity_id", "session", "y5"]]
    del panel
    for s, p in preds.items():
        p.to_pickle(OUT / f"pred-seed{s}.pkl")  # invariant-allow: data-access — 진단 원본

    ret, bench, trad = rkit.market_data(store, sessions)
    calendar = list(ret.index)
    nets, turns, ics, block_ic = {}, {}, {}, []
    for s, p in preds.items():
        daily, extra = rkit.portfolio(p, ret, trad, every=fkit.REBALANCE_EVERY)
        nets[s], turns[s] = daily, extra["turn"]
        ic = ic_module.daily_ic(p.merge(y, on=["entity_id", "session"]).rename(columns={"pred": "score", "y5": "target"})
                                [["session", "score", "target"]].dropna())
        ic.index = pd.to_datetime(pd.Series(ic.index)).dt.date.values
        ics[s] = ic
    net_frame = pd.DataFrame(nets).sort_index()
    net = net_frame.mean(axis=1)                       # 시드 평균 포트 = 다섯 장부에 1/5 씩
    ic_mean = pd.DataFrame(ics).mean(axis=1)
    for first, last in bl:
        part = ic_mean[[sessions[first] <= d <= sessions[last] for d in ic_mean.index]]
        block_ic.append((sessions[first], sessions[last], fkit.train_end(sessions, first), float(part.mean())))

    days = list(net.index)
    scale, state = exposure_scales(store, days, now)
    a, switch = apply_exposure(net, scale)
    b = net
    d1 = bench.reindex(days)
    dist = float(store.config("benchmark.kodex200_distribution_yield_annual", as_of=now))
    d2 = d1 + dist / ANN
    uni = pd.Series({d: float(ret.loc[d].reindex(list(trad.get(d, ()))).mean()) for d in days if d in ret.index})
    spans = realized_end(days, calendar)

    # 종목 회전은 시드 평균(연환산), 노출 쪽 회전은 배수 전환의 합. 비용은 둘 다 편도 0.41%.
    stock_turn = float(np.mean(list(turns.values())))
    results: dict[str, dict[str, dict[str, float]]] = {}
    for wname, window in WINDOWS.items():
        wd = in_window(net.index, spans, window)
        n = len(wd)
        sc = scale.reindex(wd)
        a_turn = stock_turn * float(sc.mean()) + float((sc - sc.shift(1).fillna(sc.iloc[0])).abs().sum()) * ANN / n
        ic_w = float(ic_mean.reindex(wd).mean())
        results[wname] = {
            "A": describe(a, d1, d2, wd, scale=scale, turn=a_turn, cost=a_turn * ONE_WAY_COST, ic=ic_w),
            "B": describe(b, d1, d2, wd, turn=stock_turn, cost=stock_turn * ONE_WAY_COST, ic=ic_w),
            "D1": describe(d1, d1, d2, wd),
            "D2": describe(d2, d1, d2, wd),
            "E": describe(uni, d1, d2, wd),
        }
        seed_cum = [float((1 + apply_exposure(net_frame[c], scale)[0].reindex(wd).fillna(0.0)).prod() - 1)
                    for c in net_frame.columns]
        results[wname]["A"]["seed_min"], results[wname]["A"]["seed_max"] = min(seed_cum), max(seed_cum)
        seed_cum_b = [float((1 + net_frame[s].reindex(wd).fillna(0.0)).prod() - 1) for s in net_frame.columns]
        results[wname]["B"]["seed_min"], results[wname]["B"]["seed_max"] = min(seed_cum_b), max(seed_cum_b)
        print(f"{wname}: {n}세션 {wd[0]}~{wd[-1]} (실현 {spans[wd[0]][0]}→{spans[wd[-1]][1]})", flush=True)

    bear_days = in_window(net.index, spans, WINDOWS["하락장"])
    months = pd.Series({spans[d][1]: scale[d] for d in days if d in spans})
    months.index = pd.to_datetime(months.index)
    monthly = months.groupby(months.index.to_period("M")).mean()
    state_share = state.reindex(bear_days).value_counts(normalize=True).to_dict()
    below = float((scale.reindex(bear_days) < 1.0).mean())

    pd.DataFrame({"A": a, "B": b, "D1": d1, "D2": d2, "E": uni, "scale": scale, "state": state}).to_pickle(OUT / "daily.pkl")  # invariant-allow: data-access — 진단 원본
    (OUT / "results.json").write_text(json.dumps({
        "windows": results, "monthly_weight": {str(k): float(v) for k, v in monthly.items()},
        "bear_state_share": state_share, "bear_below_1": below,
        "block_ic": [[str(x), str(yv), str(t), v] for x, yv, t, v in block_ic],
        "exposure_cost_ann": float(switch.mean() * ANN), "dist_yield": dist,
    }, ensure_ascii=False, indent=1))

    print("\n=== 결과 ===")
    keys = [("cum", "누적", "%"), ("ann", "연환산", "%"), ("mdd", "MDD", "%"), ("ex_d1", "초과 D1", "%"),
            ("ex_d2", "초과 D2", "%"), ("ir_d1", "IR(D1)", "f"), ("beta_down", "하방β", "f"), ("beta_up", "상방β", "f"),
            ("cap_down", "하락포착", "f"), ("cap_up", "상승포착", "f"), ("weight", "평균 주식비중", "%"),
            ("turn", "회전(연)", "f"), ("cost", "비용(연)", "%"), ("ic", "IC h5", "f")]
    for wname in WINDOWS:
        print(f"\n### {wname} ({results[wname]['A']['n']}세션)\n")
        print("| 지표 | A 현행(V6) | B 노출 끔 | D1 K200 | D2 KODEX200 TR대용 | E 유니버스 EW |")
        print("|---|---|---|---|---|---|")
        for k, label, fmt in keys:
            cells = []
            for arm in ("A", "B", "D1", "D2", "E"):
                v = results[wname][arm].get(k)
                if k == "ir_d1" and arm in ("D1", "D2"):
                    v = None                   # 벤치끼리는 추적오차가 0(또는 상수 차)이라 IR 이 뜻이 없다
                cells.append("—" if v is None or (isinstance(v, float) and np.isnan(v))
                             else (f"{v:+.1%}" if fmt == "%" else f"{v:.2f}"))
            print(f"| {label} | " + " | ".join(cells) + " |")
        ra, rb = results[wname]["A"], results[wname]["B"]
        print(f"| 시드 범위(누적) | {ra['seed_min']:+.1%}~{ra['seed_max']:+.1%} | {rb['seed_min']:+.1%}~{rb['seed_max']:+.1%} | | | |")
    print("\n월별 평균 주식 비중(A, 실현월):", " ".join(f"{k}:{v:.0%}" for k, v in monthly.items()))
    print(f"하락장 국면 분포: { {k: f'{v:.0%}' for k, v in state_share.items()} } · 배수 <1 세션 {below:.0%}")
    print("블록 IC:")
    for x, yv, t, v in block_ic:
        print(f"  {x}~{yv} 학습~{t} IC {v:+.3f}")
    print(f"최대 RSS {fkit.rss_mb():.0f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
