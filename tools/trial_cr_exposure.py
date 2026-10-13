"""시행 CR — V6 국면 노출의 crisis(·bear) 배수를 실제 포트 위에서. docs/protocols/cr-crisis-exposure-2026-10.md.

    .venv/bin/python tools/trial_cr_exposure.py --plan                 # 순서만 인쇄(자료를 읽지 않는다)
    .venv/bin/python tools/trial_cr_exposure.py --power                # 등록 전 검정력(진단 대용 지수 ≤ 2026-06-30, 평균은 인쇄 안 함)
    .venv/bin/python tools/trial_cr_exposure.py --run [--save]         # 주 시험 — PROTOCOL_HASH 가 고정·일치할 때만

## 무엇을 바꾸나 — 노출 배수 하나

변형(등록 문서 '변형' 표): E1 = 현행 V6(bull·volatile 1.0 · bear 0.7 · crisis 0.5, 대조) · CR1 = crisis 1.0 · CR2 = V6 끔 · CR3 = bear 1.0.
배수는 **실전 함수** `selector.exposure.regime_scale`(확인 2세션 최솟값 · `ExposureParams` 는 창고 설정 오늘 값)에 배수 표만 바꿔 넣어 얻는다.
국면은 실전 순수 함수 `analysts.regime.classify`(실전 첫 대용 지수 KRX 300 · 400일 창 · crisis 하한 = 창고 설정) — 결정 세션 **전날 종가까지**
(`v2_hmm_exposure.v6_path`·`diag_bear_2022.exposure_scales` 와 같은 지연). 백테스트 전용 분기는 없다(불변식 5): 실전과 다른 것은 배수 표뿐이다.

## 포트 — 실전 선택 규칙 그대로, 노출만 바꾼다

점수 = C0 이음 예측(시드 0~4): 하락장 검진 워크포워드(2021-12-29~2023-02-14, `diag_hedged_sleeve.kr_preds` 의 이음) + 회차 C0 OOS(~2026-06-29)
+ 얼린 C0(`vault_judge._c0_inputs` 와 같은 `p1b_predictions`, 2026-06-30~). EMA5 → 상위 24 · 완충 3N(72) · 10세션 재조정 → 비중
(주: 실전 `allocator.risk_parity_baseline.allocate_risk_parity`, 기록: 동일가중) → `tools/v2_sim.simulate`(실전 보유일 규칙 —
보유일엔 명단·상대 비중을 드리프트로 두고 노출만 매일 따른다, 줄인 몫은 현금, 비용 = 편도 0.41% × Σ|Δw|).

## 봉인 — 2026-10-01 이후는 한 칸도 쓰지 않는다

`LAST_CLOSE = 2026-09-30` 을 코드에 못 박았다: 모든 창고 읽기의 as_of 는 그날 장 마감 뒤(16:00 UTC)를 넘지 않고, 읽은 표의 날짜를 다시 검사해
넘으면 멈춘다(`guard`). 결정 세션은 t+2 종가가 9/30 안에 있는 날만 쓴다. 날짜 인자는 받지 않는다. 금고 second(10/1~11/13)의 확인은
이 도구가 아니라 `tools/vault_judge.py --window second`(해시 고정 시 `CR` 이 붙는다)가 11/23 에 `vault_run` 으로 한다.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts.regime import (  # noqa: E402
    CRISIS_FLOOR_KEY,
    LOOKBACK_DAYS,
    MARKET_PROXIES,
    classify,
)
from quant_rl_trading.selector.exposure import FLOOR, ExposureParams, regime_scale  # noqa: E402
from tools.trial_overlay import ANN, ONE_WAY_COST  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402
from tools.v2_sim import simulate  # noqa: E402

PROTOCOL = Path("docs/protocols/cr-crisis-exposure-2026-10.md")
#: 등록 문서 sha256 앞 16자 — **사용자 확인 뒤 리드가 고정한다.** None 이면 `--run` 을 거부한다(초안).
PROTOCOL_HASH: str | None = None

#: 봉인 — second 금고(10/1~11/13)는 이 도구가 읽지 않는다. 마지막으로 쓸 수 있는 종가.
LAST_CLOSE = date(2026, 9, 30)
SEALED_FROM = date(2026, 10, 1)
#: 대용 지수 진단(15년)의 상한 — 검정력 계산은 이 날짜까지만 읽는다(진단 문서 §1 과 같은 상한).
POWER_UNTIL = date(2026, 6, 30)

#: 주 시험 창 — 첫 결정 세션(하락장 검진 예측의 첫 블록), 이음 지점 둘.
MAIN_FIRST = date(2021, 12, 29)
SPLICE = date(2023, 2, 15)          # 이 날부터 회차 C0 (diag_hedged_sleeve.SPLICE 와 같다)
FROZEN_FROM = date(2026, 6, 30)     # 이 날부터 얼린 C0(학습 끝 2026-06-16 · usable_from 07-01 — 6/30 도 학습 밖)
PRICE_START = date(2021, 11, 10)    # market_data 가격 창 시작(diag_hedged_sleeve.PRICE_START)

SEEDS = (0, 1, 2, 3, 4)
EVERY = 10
N_TOP, EXIT_MULT, SPAN = 24, 3, 5
NW_LAG = 10
REGIME_INDEX = MARKET_PROXIES[0]    # 실전 RegimeAnalyst 의 첫 대용 — KRX 300

V6: dict[str, float] = {"bull": 1.0, "volatile": 1.0, "bear": 0.7, "crisis": 0.5, "unknown": 1.0}
VARIANTS: dict[str, dict[str, float]] = {
    "E1": dict(V6),
    "CR1": {**V6, "crisis": 1.0},
    "CR2": {s: 1.0 for s in V6},
    "CR3": {**V6, "bear": 1.0},
}
CONTROL = "E1"
TREATMENTS = ("CR1", "CR2", "CR3")

#: 국면 구간(실현 구간 t+1→t+2 가 통째로 드는 날, `diag_bear_2022.in_window` 규칙). 날짜는 등록 문서 표와 같다.
PHASES: dict[str, tuple[date, date]] = {
    "하락": (date(2022, 1, 3), date(2022, 9, 30)),
    "반등": (date(2022, 9, 30), date(2023, 1, 31)),
    "박스": (date(2023, 1, 31), date(2024, 12, 30)),
    "급등": (date(2024, 12, 30), LAST_CLOSE),
}

# ---- 주 시험 기준(등록 문서 '주 시험' 표와 같다 — 테스트가 대조한다)
GATE_T = 3.0             # A1 일 초과 차(시드 평균) 평균 > 0 그리고 NW t(lag 10) ≥ 3
GATE_SEEDS = 5           # A2 시드별 연 차 > 0 인 시드 수(5 중)
GATE_YEARS = 4           # A3 달력 연도 블록(2022~2026) 중 연 차 ≥ 0 인 블록 수(5 중), 블록 ≥ 60세션만 센다
GATE_YEAR_MIN = 60
GATE_PHASE_UP = ("박스", "급등")   # A4 이 국면들의 연 차 > 0
GATE_BEAR_RET = -0.02    # A5 하락 구간 누적수익 차(처리 − 대조) ≥ −2%p
GATE_BEAR_MDD = -0.02    # A5 하락 구간 MDD 차 ≥ −2%p
GATE_MDD = -0.02         # A6 전 구간 MDD 차 ≥ −2%p
#: 비중 경로 — 리스크 패리티 물러섬(스코어 비례)이 재조정일의 이 비율을 넘으면 주 비중을 동일가중으로 바꾼다(등록 때 정함).
RP_FALLBACK_MAX = 0.20

# ---- 금고 확인(11/23, second 30세션) — 여유는 주 시험 창의 30세션 블록 분포에서 `--run` 이 유도해 결과 파일에 적는다
VAULT_BLOCK, VAULT_STEP, VAULT_Z = 30, 5, 1.645

ENTITY = "cr-crisis-exposure-2026-10:CR"
KST = timezone(timedelta(hours=9))
MIN_AVAILABLE_GB = 5.0


# --------------------------------------------------------------------------- 봉인


def sealed_as_of(day: date = LAST_CLOSE) -> datetime:
    """창고 읽기의 as_of 상한 — 그날 장 마감 뒤(16:00 UTC = 다음날 01:00 KST). 10/1 행은 아직 관측되지 않은 시각이다."""
    if day > LAST_CLOSE:
        raise SystemExit(f"봉인: {day} > {LAST_CLOSE} — second 금고(10/1~)는 이 도구가 읽지 않는다")
    return datetime.combine(day, time(16, 0), tzinfo=UTC)


def guard(name: str, index: Any, *, until: date = LAST_CLOSE) -> None:
    """표·계열의 날짜가 상한을 넘으면 멈춘다(rc 3)."""
    days = pd.to_datetime(pd.Series(list(index))).dt.date if len(index) else pd.Series([], dtype=object)
    if len(days) and max(days) > until:
        raise SystemExit(f"봉인 위반: {name} 의 마지막 날 {max(days)} > {until} — 멈춘다")


# --------------------------------------------------------------------------- 국면·배수 (실전 함수)


def variant_params(base: ExposureParams, variant: str) -> ExposureParams:
    """실전 노출 설정에서 **국면 배수 표만** 변형 값으로 바꾼다(확인 세션·데드밴드 등은 그대로)."""
    return dataclasses.replace(base, regime_scale=dict(VARIANTS[variant]))


def regime_states(closes: pd.Series, days: list[date], floor: float) -> pd.Series:
    """결정 세션마다 국면 — **전날 종가까지**(400일 창), `classify` 그대로."""
    closes = closes.sort_index()
    out = {}
    for d in days:
        hist = closes[(closes.index < d) & (closes.index > d - timedelta(days=LOOKBACK_DAYS))]
        out[d] = classify(hist, crisis_floor=floor)
    return pd.Series(out, dtype=object)


def scale_path(states: pd.Series, params: ExposureParams) -> pd.Series:
    """국면 → 배수. `regime_scale`(최근 N 세션 최솟값) 그대로, 바닥 FLOOR. 직전 세션 국면은 이 계열의 앞 칸이다."""
    window = max(1, int(params.regime_confirm_sessions))
    recent: list[str] = []
    out = {}
    for d, state in states.items():
        scale, _ = regime_scale(str(state), params, recent_states=tuple(recent))
        out[d] = max(FLOOR, float(scale))
        recent = [*recent, str(state)][-(window - 1):] if window > 1 else []
    return pd.Series(out, dtype=float)


# --------------------------------------------------------------------------- 날짜


def usable_days(days: list[date], calendar: list[date], last_close: date = LAST_CLOSE) -> list[date]:
    """결정 세션 t 중 실현 구간 끝(t+2 종가)이 ``last_close`` 안에 있는 날만."""
    pos = {d: i for i, d in enumerate(calendar)}
    out = []
    for d in days:
        i = pos.get(d)
        if i is not None and i + 2 < len(calendar) and calendar[i + 2] <= last_close:
            out.append(d)
    return out


def realized(days: list[date], calendar: list[date]) -> dict[date, tuple[date, date]]:
    pos = {d: i for i, d in enumerate(calendar)}
    return {d: (calendar[pos[d] + 1], calendar[pos[d] + 2]) for d in days if d in pos and pos[d] + 2 < len(calendar)}


def phase_days(index: pd.Index, spans: dict[date, tuple[date, date]], window: tuple[date, date]) -> list[date]:
    lo, hi = window
    return [d for d in index if d in spans and spans[d][0] >= lo and spans[d][1] <= hi]


# --------------------------------------------------------------------------- 포트


Weigh = Callable[[date, list[str], pd.Series], pd.Series]


def equal_weigh(_day: date, held: list[str], _row: pd.Series) -> pd.Series:
    return pd.Series(1.0 / len(held), index=held)


def targets_from_scores(pred: pd.DataFrame, ret: pd.DataFrame, trad: dict[date, set[str]], days: list[date],
                        weigh: Weigh, *, every: int = EVERY) -> tuple[pd.DataFrame, list[date]]:
    """세션 × 종목 목표 비중(주식만, 합 1). 재조정일(step % every == 0)에만 새로 고르고 그 사이엔 직전 목표를 그대로 둔다.

    고르기는 `trial_ranker_kit.portfolio` 와 같다(EMA5 → 거래가능 명단 → `pick_mult` 상위 24·완충 3N). 비중만 ``weigh``.
    반환 (목표, 재조정일). `v2_sim.simulate` 는 같은 step 규칙으로 재조정한다(policy 의 b) — 둘의 step 은 이 목표의 행 순서다.
    """
    wide = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    held: list[str] = []
    rows: dict[date, pd.Series] = {}
    rebal: list[date] = []
    last: pd.Series | None = None
    step = -1
    for day in days:
        if day not in ret.index or day not in wide.index:
            continue
        step += 1
        if last is None or step % every == 0:
            row = wide.loc[day].dropna()
            ok = trad.get(day)
            if ok:
                row = row[row.index.isin(ok)]
            if row.empty:
                if last is None:
                    step -= 1
                    continue
                rows[day] = last
                continue
            held = pick_mult(held, row.sort_values(ascending=False).index, N_TOP, EXIT_MULT)
            w = weigh(day, held, row).reindex(held).fillna(0.0)
            w = w[w > 0]
            last = w / w.sum()
            rebal.append(day)
        rows[day] = last
    frame = pd.DataFrame(rows).T.sort_index() if rows else pd.DataFrame()
    return frame.fillna(0.0), rebal


def books(targets: pd.DataFrame, ret: pd.DataFrame, bench: pd.Series, paths: dict[str, pd.Series],
          *, every: int = EVERY) -> dict[str, pd.Series]:
    """변형마다 비용 후 일수익 — 같은 목표 비중, 노출 경로만 다르다(`v2_sim.simulate`)."""
    out = {}
    for name, path in paths.items():
        policy = (lambda pth: (lambda s: (float(pth.get(s.day, 1.0)), s.step % every == 0)))(path)
        out[name] = simulate(targets, ret, bench, ONE_WAY_COST, policy, k_min=FLOOR).daily
    return out


# --------------------------------------------------------------------------- 지표·판정 (순수)


def mdd(r: pd.Series) -> float:
    nav = (1.0 + r.fillna(0.0)).cumprod()
    return float((nav / nav.cummax() - 1.0).min()) if len(nav) else float("nan")


def judge_main(daily: dict[str, dict[int, pd.Series]], spans: dict[date, tuple[date, date]]) -> tuple[list[str], str, dict]:
    """주 시험. ``daily[변형][시드]`` = 비용 후 일수익(결정 세션 인덱스). 처리마다 A1~A6, 통과한 것 중 NW t 가 가장 큰 하나.

    지표가 비거나 nan 이면 멈춘다(조용히 기각하지 않는다).
    """
    if CONTROL not in daily or any(t not in daily for t in TREATMENTS):
        raise ValueError(f"judge_main: 변형이 빠졌다 — {sorted(daily)}")
    seeds = sorted(daily[CONTROL])
    days = sorted(set().union(*(s.index for s in daily[CONTROL].values())))
    ctrl = pd.DataFrame(daily[CONTROL]).reindex(days)
    if ctrl.isna().any().any():
        raise ValueError("judge_main: 대조 일수익에 빈칸 — 시드마다 같은 날을 채점해야 한다")
    lines = ["| 변형 | 연 차(시드 평균) | NW t | 시드 + | 연도 ≥0 | 하락 누적 차 · MDD 차 | 반등 | 박스 | 급등 | 전 구간 MDD 차 | 판정 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    stats: dict[str, dict[str, Any]] = {}
    passed: list[str] = []
    for arm in TREATMENTS:
        treat = pd.DataFrame(daily[arm]).reindex(days)
        if treat.isna().any().any() or list(treat.columns) != list(ctrl.columns):
            raise ValueError(f"judge_main: {arm} 일수익이 대조와 같은 날·같은 시드가 아니다")
        diff = (treat - ctrl).mean(axis=1)
        ann = float(diff.mean() * ANN)
        t = float(ic_module.newey_west_t(diff, lag=NW_LAG))
        seeds_up = int(sum((treat[s] - ctrl[s]).mean() > 0 for s in seeds))
        years = {}
        for y, part in diff.groupby(pd.Index([d.year for d in diff.index])):
            if len(part) >= GATE_YEAR_MIN:
                years[int(y)] = float(part.mean() * ANN)
        years_ok = sum(v >= 0 for v in years.values())
        ph: dict[str, dict[str, float]] = {}
        for name, window in PHASES.items():
            sel = phase_days(diff.index, spans, window)
            if not sel:
                raise ValueError(f"judge_main: 국면 {name} 에 세션이 없다")
            tr, cr = treat.loc[sel].mean(axis=1), ctrl.loc[sel].mean(axis=1)
            ph[name] = {"ann": float(diff.loc[sel].mean() * ANN), "n": len(sel),
                        "cum": float((1 + tr).prod() - (1 + cr).prod()),
                        "mdd": float(np.mean([mdd(treat.loc[sel, s]) - mdd(ctrl.loc[sel, s]) for s in seeds]))}
        full_mdd = float(np.mean([mdd(treat[s]) - mdd(ctrl[s]) for s in seeds]))
        values = [ann, t, full_mdd, *(v for p in ph.values() for v in p.values())]
        if not all(np.isfinite(v) for v in values):
            raise ValueError(f"judge_main: {arm} 지표에 nan — 판정 불가")
        c = {"A1": ann > 0 and t >= GATE_T, "A2": seeds_up >= GATE_SEEDS, "A3": years_ok >= GATE_YEARS and len(years) >= GATE_YEARS,
             "A4": all(ph[p]["ann"] > 0 for p in GATE_PHASE_UP),
             "A5": ph["하락"]["cum"] >= GATE_BEAR_RET and ph["하락"]["mdd"] >= GATE_BEAR_MDD,
             "A6": full_mdd >= GATE_MDD}
        ok = all(c.values())
        if ok:
            passed.append(arm)
        stats[arm] = {"ann": ann, "t": t, "seeds_up": seeds_up, "years": years, "phases": ph, "mdd": full_mdd, "gates": c}
        mk = {key: "○" if ok_ else "×" for key, ok_ in c.items()}
        lines.append(
            f"| {arm} | {ann:+.2%}p {mk['A1']} | {t:+.2f} | {seeds_up}/{len(seeds)} {mk['A2']} | {years_ok}/{len(years)} {mk['A3']} | "
            f"{ph['하락']['cum']:+.1%}p · {ph['하락']['mdd']:+.1%}p {mk['A5']} | {ph['반등']['ann']:+.1%}p | {ph['박스']['ann']:+.1%}p | "
            f"{ph['급등']['ann']:+.1%}p {mk['A4']} | {full_mdd:+.1%}p {mk['A6']} | {'통과' if ok else '기각'} |")
    if not passed:
        return lines, "기각 — 주 시험(긴 과거)에서 통과한 변형 없음, V6 유지", stats
    best = max(passed, key=lambda a: stats[a]["t"])
    return lines, f"주 시험 통과 {best} — 금고 second(11/23) 확인 대기, 실전 반영은 사용자 결정", stats


def block_margins(treat: pd.Series, ctrl: pd.Series, *, length: int = VAULT_BLOCK, step: int = VAULT_STEP) -> dict[str, float]:
    """주 시험 창의 ``length`` 세션 블록(``step`` 씩 밀며)에서 연 차·MDD 차의 표준편차 → 금고 확인 여유(−1.645 × 표준편차)."""
    d = (treat - ctrl).dropna()
    ann, dd = [], []
    for a in range(0, len(d) - length + 1, step):
        idx = d.index[a:a + length]
        ann.append(float(d.loc[idx].mean() * ANN))
        dd.append(mdd(treat.loc[idx]) - mdd(ctrl.loc[idx]))
    if len(ann) < 10:
        raise ValueError("block_margins: 블록이 10개 미만 — 여유를 못 정한다")
    sd_ann, sd_mdd = float(np.std(ann, ddof=1)), float(np.std(dd, ddof=1))
    return {"blocks": len(ann), "sd_ann": sd_ann, "sd_mdd": sd_mdd,
            "gate_ann": -VAULT_Z * sd_ann, "gate_mdd": -VAULT_Z * sd_mdd}


def judge_vault(treat: dict[int, pd.Series], ctrl: dict[int, pd.Series], margins: dict[str, float], arm: str) -> tuple[list[str], str]:
    """금고 확인 — V1 연 차(시드 평균) ≥ gate_ann · V2 MDD 차 ≥ gate_mdd. "이겼다" 가 아니라 "뒤집히지 않았고 해가 없다"."""
    seeds = sorted(ctrl)
    if not seeds or sorted(treat) != seeds:
        raise ValueError(f"judge_vault: 시드가 비었거나 어긋난다(처리 {sorted(treat)} · 대조 {seeds}) — 판정 불가")
    t, c = pd.DataFrame(treat)[seeds], pd.DataFrame(ctrl)[seeds]
    if t.empty or t.isna().any().any() or c.isna().any().any():
        raise ValueError("judge_vault: 일수익이 비었거나 빈칸 — 판정 불가")
    ann = float((t - c).mean(axis=1).mean() * ANN)
    dmdd = float(np.mean([mdd(t[s]) - mdd(c[s]) for s in seeds]))
    v1, v2 = ann >= margins["gate_ann"], dmdd >= margins["gate_mdd"]
    mk = lambda ok: "○" if ok else "×"  # noqa: E731
    lines = [f"CR 금고 확인({arm} 대 E1, {len(t)}세션, 시드 {len(seeds)}) V1 연 차 {ann:+.1%}p (≥ {margins['gate_ann']:+.1%}p) {mk(v1)} · "
             f"V2 MDD 차 {dmdd:+.1%}p (≥ {margins['gate_mdd']:+.1%}p) {mk(v2)}"]
    if v1 and v2:
        return lines, f"확인 — {arm} 금고에서 뒤집히지 않음·해 없음, 실전 반영은 사용자 결정(전방 순차 검정 병행)"
    return lines, f"기각 — {arm} 금고에서 해({'V1' if not v1 else ''}{'V2' if not v2 else ''})"


# --------------------------------------------------------------------------- 검정력 (등록 전, 진단 대용 지수)


def nw_se_annual(x: np.ndarray, n: int, lag: int = NW_LAG) -> float:
    """일 계열 x 의 NW 장기분산으로 길이 n 창 평균의 연율 표준오차."""
    dev = x - x.mean()
    var = float((dev * dev).mean())
    for k in range(1, lag + 1):
        var += 2.0 * (1.0 - k / (lag + 1)) * float((dev[k:] * dev[:-k]).mean())
    return float(np.sqrt(var / n) * ANN)


def power_table(diff: np.ndarray, *, length: int, deltas: list[float], thresholds: list[float], step: int = 5) -> pd.DataFrame:
    """귀무 = 평균을 뺀 계열, 대립 = 거기에 연 δ 를 더한 것. 길이 ``length`` 블록(``step`` 씩)마다 NW t ≥ 문턱의 비율. **평균은 내보내지 않는다.**"""
    x0 = diff - diff.mean()
    rows = []
    for delta in [0.0, *deltas]:
        ts = np.array([ic_module.newey_west_t(pd.Series(x0[a:a + length] + delta / ANN), lag=NW_LAG)
                       for a in range(0, len(x0) - length + 1, step)])
        rows.append({"δ(연)": delta, **{f"t≥{th:g}": float(np.mean(ts >= th)) for th in thresholds}})
    return pd.DataFrame(rows)


def proxy_diffs(level: pd.Series, v6: pd.Series, base: ExposureParams) -> dict[str, np.ndarray]:
    """진단 대용 지수(시총 상위 200) 위의 변형 − E1 일 차. 결정 d 의 배수는 d−1 까지의 국면, 수익은 d+1→d+2 (주 시험과 같은 정렬).

    노출 전환 비용 = 편도 × |Δk|. 검정력 계산 전용 — 평균은 호출부가 인쇄하지 않는다.
    """
    level = level.sort_index()
    r = (level.shift(-2) / level.shift(-1) - 1.0)
    states = v6.shift(1).reindex(level.index).fillna("unknown")
    keep = r.notna() & (states != "unknown")
    paths = {v: scale_path(states, variant_params(base, v)) for v in VARIANTS}
    net = {}
    for v, k in paths.items():
        cost = k.diff().abs().fillna(0.0) * ONE_WAY_COST
        net[v] = (k * r - cost)[keep]
    return {v: (net[v] - net[CONTROL]).to_numpy(float) for v in TREATMENTS}


def run_power(data: Path, *, length: int, vol_mults: tuple[float, ...] = (1.0, 1.5)) -> int:
    """진단 `data/_diag/regime-strategy-map/regimes.pkl`(≤ 2026-06-30) 로 등록 전 검정력. 평균 차는 인쇄하지 않는다."""
    path = data / "_diag/regime-strategy-map/regimes.pkl"
    reg = pd.read_pickle(path)  # invariant-allow: data-access — 진단 캐시(대용 지수·V6 국면, 이미 본 자료)
    level, v6 = reg["level"], reg["v6"]
    guard("regimes.level", level.index, until=POWER_UNTIL)
    guard("regimes.v6", v6.index, until=POWER_UNTIL)
    base = ExposureParams(below_trend=1.0, regime_scale=dict(V6), squeezed=1.0, squeeze_quantile=0.2,
                          regime_confirm_sessions=2, deadband=0.10, deadband_asymmetric=False)
    diffs = proxy_diffs(level, v6, base)
    print(f"=== 시행 CR 검정력 — 진단 대용 지수 {level.index[0]}~{level.index[-1]} · 판정 창 길이 {length}세션 · NW lag {NW_LAG} "
          f"(평균 차는 인쇄하지 않는다 — 귀무는 평균을 뺀 계열) ===", flush=True)
    for arm, d in diffs.items():
        active = float(np.mean(np.abs(d) > 1e-12))
        print(f"\n[{arm}] 일 차 표준편차 {d.std():.5f} · 차가 0 아닌 날 {active:.0%} · 관측 {len(d)}", flush=True)
        for vm in vol_mults:
            x = d * vm
            se = nw_se_annual(x, length)
            print(f"  변동성 ×{vm:g}: 연 차의 표준오차(창 {length}) {se:.2%}p → 최소 탐지 효과(80%·t≥3 단측) ≈ {(3.0 + 0.84) * se:.2%}p "
                  f"· (t≥2) ≈ {(2.0 + 0.84) * se:.2%}p", flush=True)
            tab = power_table(x, length=length, deltas=[0.005, 0.01, 0.02, 0.03, 0.05], thresholds=[2.0, 2.5, 3.0])
            print(tab.to_string(index=False, float_format=lambda v: f"{v:.3f}"), flush=True)
        blocks = [d[a:a + VAULT_BLOCK] for a in range(0, len(d) - VAULT_BLOCK + 1, VAULT_STEP)]
        sd30 = float(np.std([b.mean() * ANN for b in blocks], ddof=1))
        print(f"  참고 — 30세션 블록 연 차 표준편차(대용 지수, 변동성 ×1) {sd30:.1%}p → 금고 V1 여유 대략 {-VAULT_Z * sd30:+.1%}p "
              "(실제 여유는 --run 이 실제 포트로 다시 잰다)", flush=True)
    return 0


# --------------------------------------------------------------------------- 자료 읽기 (--run)


def kr_preds_long(data: Path, seed: int) -> pd.DataFrame:
    """C0 이음 예측(~2026-06-29) — `diag_hedged_sleeve.kr_preds` 그대로(하락장 검진 + 회차 C0), 경로만 ``data`` 아래."""
    from tools import final_round_kit as fkit
    from tools.diag_hedged_sleeve import C0_TAG

    bear = pd.read_pickle(data / "_diag/bear-2022" / f"pred-seed{seed}.pkl")  # invariant-allow: data-access — 진단 캐시
    c0 = pd.read_pickle(fkit.control_path("C0", seed, C0_TAG, cache_dir=data / "_diag/final-round"))  # invariant-allow: data-access — 대조군 캐시
    c0 = c0[c0["market"] == "KR"]
    out = pd.concat([bear[bear["session"] < SPLICE], c0[c0["session"] >= SPLICE]], ignore_index=True)
    out = out[out["session"] < FROZEN_FROM]
    return out[["entity_id", "session", "pred"]]


def frozen_c0_preds(store: Any, sessions: list[date]) -> dict[int, pd.DataFrame]:
    """얼린 C0(앞당김 문서 해시 대조)로 ``sessions`` 를 채점 — `vault_judge.p1b_predictions` 그대로(실전 경로 fa_features)."""
    from quant_rl_trading.collectors.market_hours import Market
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import ReplayClock
    from tools import vault_judge as vj

    if sessions and max(sessions) > LAST_CLOSE:
        raise SystemExit(f"봉인: 얼린 C0 채점 세션 {max(sessions)} > {LAST_CLOSE}")
    _, c0 = vj.frozen_be2(vj.frozen_hashes("BE2"))
    policy = publication_policy(store, Market.KR, clock=ReplayClock(sealed_as_of()))  # 공표 지연 = 오늘 설정
    return vj.p1b_predictions(store, sessions, c0, as_of_of=policy.for_session)


def regime_closes(store: Any, *, as_of: datetime, start: date) -> pd.Series:
    frame = store.get("indices", as_of=as_of, entity=[REGIME_INDEX], market="KR",
                      lookback=(as_of.date() - start).days + LOOKBACK_DAYS + 40,
                      columns=["entity_id", "valid_from", "close", "revision"])
    closes = (frame.sort_values(["valid_from", "revision"]).assign(day=lambda f: f["valid_from"].dt.date)
              .groupby("day")["close"].last().astype(float).sort_index())
    return closes[closes > 0]


def rp_weigher(store: Any, log: dict[str, int], *, until: date = LAST_CLOSE) -> Weigh:
    """실전 리스크 패리티(`allocate_risk_parity`) — 점수 = 그날 EMA 점수의 단면 z(보유 명단). 물러섬·제약 실패는 세고, 실패는 동일가중.

    손잡이(점수 기울기·RC 상한·비중 상한 …)는 **오늘 설정**(``until`` 장 마감 뒤 as_of)을 한 번 읽어 소급한다 — 노출 설정과 같은 규칙
    (`diag_bear_2022` "지금 전략을 그때 썼다면"). 공분산·섹터·하방 베타는 그 재조정일 as_of 의 창고 자료다. as_of 는 ``until`` 을 넘지 않는다.
    """
    from quant_rl_trading.allocator.baseline import AllocatorParams, Baseline
    from quant_rl_trading.allocator.risk_parity_baseline import (
        RiskParityParams,
        allocate_risk_parity,
    )
    from quant_rl_trading.portfolio.constraints import ProjectionError

    cap = datetime.combine(until, time(16, 0), tzinfo=UTC)
    base = AllocatorParams.from_store(store, as_of=cap)
    params = RiskParityParams.from_store(store, as_of=cap)
    fallback = AllocatorParams(baseline=Baseline.SCORE, max_position_weight=base.max_position_weight, cash_buffer=base.cash_buffer)

    def weigh(day: date, held: list[str], row: pd.Series) -> pd.Series:
        as_of = datetime.combine(min(day, until), time(16, 0), tzinfo=UTC)
        z = ((row - row.mean()) / row.std()).reindex(held).fillna(0.0)
        try:
            w, path = allocate_risk_parity(store, as_of=as_of, market="KR", scores=z.to_dict(), entities=list(held),
                                           params=params, fallback=fallback)
        except ProjectionError:
            log["error"] = log.get("error", 0) + 1
            return equal_weigh(day, held, row)
        log["total"] = log.get("total", 0) + 1
        if path.endswith("fallback"):
            log["fallback"] = log.get("fallback", 0) + 1
        s = pd.Series(w, dtype=float).reindex(held).fillna(0.0)
        return s if s.sum() > 0 else equal_weigh(day, held, row)

    return weigh


def tradable_from(cache: Path) -> dict[date, set[str]]:
    """거래가능 명단 캐시 — `trial_ranker_kit.market_data` 의 명단 부분과 같다(가격은 다시 읽지 않는다)."""
    frame = pd.read_pickle(cache / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    return {day: set(part["entity_id"]) for day, part in frame.groupby("session")}


def available_gb() -> float:
    with open("/proc/meminfo") as handle:  # invariant-allow: data-access — /proc, 창고 아님
        for line in handle:
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024 / 1024
    return float("nan")


def heavy_ok() -> bool:
    now = datetime.now(UTC).astimezone(KST)  # invariant-allow: wallclock — 무거운 계산 시작 시각 관문
    quiet = now.time() >= time(22, 20) or now.time() <= time(0, 45)
    if quiet:
        print("22:20~00:45 KST 에는 무거운 계산을 시작하지 않는다(밤 크론 구간).", flush=True)
    gb = available_gb()
    if gb < MIN_AVAILABLE_GB:
        print(f"가용 메모리 {gb:.1f}GB < {MIN_AVAILABLE_GB}GB — 시작하지 않는다.", flush=True)
    return not quiet and gb >= MIN_AVAILABLE_GB


def protocol_problem() -> str | None:
    if PROTOCOL_HASH is None:
        return f"{PROTOCOL} 는 초안이다(PROTOCOL_HASH = None) — 사용자 확인 뒤 해시를 고정해야 --run 이 돈다"
    if not PROTOCOL.exists():
        return f"{PROTOCOL} 가 없다"
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    if digest != PROTOCOL_HASH:
        return f"{PROTOCOL} 해시 {digest} ≠ 고정값 {PROTOCOL_HASH} — 고정 뒤 문서가 바뀌었다"
    return None


def run_main(data: Path, *, save: bool) -> int:
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.store import Store
    from tools import trial_ranker_kit as rkit

    store = Store(root=data)
    as_of = sealed_as_of()
    base = ExposureParams.from_store(store, as_of=as_of)
    live = {k: float(v) for k, v in base.regime_scale.items()}
    if any(abs(live.get(s, 1.0) - VARIANTS[CONTROL][s]) > 1e-12 for s in VARIANTS[CONTROL]):
        raise SystemExit(f"실전 국면 배수 {live} 가 등록 대조 E1 {VARIANTS[CONTROL]} 와 다르다 — 멈춘다(대조가 실전이 아니다)")
    floor = float(store.config(CRISIS_FLOOR_KEY, as_of=as_of))
    calendar = list(trading_days(Market.KR, PRICE_START, LAST_CLOSE))
    sessions = [d for d in calendar if d >= MAIN_FIRST]
    days = usable_days(sessions, calendar)
    print(f"주 시험 결정 세션 {days[0]}~{days[-1]} · {len(days)}세션 (t+2 ≤ {LAST_CLOSE})", flush=True)

    long_ret, bench, trad = rkit.market_data(store, [PRICE_START, *sessions], cache=data / "_diag-long")
    early = tradable_from(data / "_diag/vault-early")             # 금고 early(7/1~9/30) — 소진돼 탐색 자료
    guard("tradable early", list(early))
    trad = {**{d: s for d, s in trad.items() if d < date(2026, 7, 1)}, **early}
    guard("ret", long_ret.index)
    guard("bench", bench.index)
    ret = long_ret.reindex(days)
    bench = bench.reindex(days)

    closes = regime_closes(store, as_of=as_of, start=MAIN_FIRST)
    guard("regime closes", closes.index)
    states = regime_states(closes, days, floor)
    unknown = int((states == "unknown").sum())
    if unknown:
        raise SystemExit(f"국면 unknown {unknown}세션 — {REGIME_INDEX} 이력 부족, 변형 차가 묻힌다. 멈춘다")
    paths = {v: scale_path(states, variant_params(base, v)) for v in VARIANTS}
    print("국면 분포 " + " · ".join(f"{k} {v}" for k, v in states.value_counts().items()), flush=True)

    # 얼린 C0 조각(6/30~). EMA 는 앞 조각(회차 C0) 점수에서 그대로 이어진다 — 따로 데우는 세션을 넣지 않는다.
    frozen = frozen_c0_preds(store, [d for d in calendar if d >= FROZEN_FROM])
    daily: dict[str, dict[str, dict[int, pd.Series]]] = {"RP": {v: {} for v in VARIANTS}, "EW": {v: {} for v in VARIANTS}}
    rp_log: dict[str, int] = {}
    for seed in SEEDS:
        pred = pd.concat([kr_preds_long(data, seed), frozen[seed]], ignore_index=True)
        guard(f"pred seed{seed}", pred["session"])
        for wname, weigh in (("RP", rp_weigher(store, rp_log)), ("EW", equal_weigh)):
            tgt, _ = targets_from_scores(pred, ret, trad, days, weigh)
            for v, r in books(tgt, ret, bench, paths).items():
                daily[wname][v][seed] = r
        print(f"  시드 {seed} 완료 · RP 물러섬 {rp_log.get('fallback', 0)}/{rp_log.get('total', 0)} · 제약 실패 {rp_log.get('error', 0)}",
              flush=True)
    share = (rp_log.get("fallback", 0) + rp_log.get("error", 0)) / max(1, rp_log.get("total", 0) + rp_log.get("error", 0))
    primary = "RP" if share <= RP_FALLBACK_MAX else "EW"
    print(f"주 비중 = {primary} (리스크 패리티 물러섬 비율 {share:.0%}, 문턱 {RP_FALLBACK_MAX:.0%})", flush=True)
    spans = realized(days, calendar)
    lines, verdict, _ = judge_main(daily[primary], spans)
    other = "EW" if primary == "RP" else "RP"
    rec_lines, rec_verdict, _ = judge_main(daily[other], spans)
    print("\n".join(lines) + f"\n판정: {verdict}\n\n기록(기준 아님) {other} 비중:\n" + "\n".join(rec_lines)
          + f"\n({other} 이었다면: {rec_verdict})", flush=True)
    result: dict[str, Any] = {"protocol_hash": PROTOCOL_HASH, "verdict": verdict, "primary": primary, "rp_fallback_share": share,
                              "first": str(days[0]), "last": str(days[-1]), "sessions": len(days)}
    if verdict.startswith("주 시험 통과"):
        arm = verdict.split()[3]
        ctrl = pd.DataFrame(daily[primary][CONTROL]).mean(axis=1)
        treat = pd.DataFrame(daily[primary][arm]).mean(axis=1)
        result |= {"winner": arm, "margins": block_margins(treat, ctrl)}
        print(f"금고 확인 여유(30세션 블록) {result['margins']}", flush=True)
    if save:
        (data / "_diag/cr-exposure").mkdir(parents=True, exist_ok=True)
        (data / "_diag/cr-exposure/main.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        rkit.record(store, entity=ENTITY, source="trial_cr_exposure", family="selection", digest=str(PROTOCOL_HASH),
                    verdict=verdict, lines=[f"주 비중 {primary}", *lines[-4:]])
        print(f"기록 완료 — research_trials 1행 · {data / '_diag/cr-exposure/main.json'}", flush=True)
    else:
        print("\n--save 가 없다 — 창고에 아무것도 적지 않았다(시행 미소진).", flush=True)
    return 0


# --------------------------------------------------------------------------- 금고 second 확인 (vault_judge 가 부른다)


def vault_prereq(data: Path, expected_hash: str | None) -> str | None:
    """금고에 붙기 전 조건 — 주 시험 결과 파일이 있고, 같은 등록 해시이고, 통과였다. 아니면 건너뛸 이유."""
    path = data / "_diag/cr-exposure/main.json"
    if not path.exists():
        return f"주 시험 결과 {path} 가 없다(--run --save 전)"
    res = json.loads(path.read_text())
    if res.get("protocol_hash") != expected_hash:
        return f"주 시험 결과의 등록 해시 {res.get('protocol_hash')} ≠ {expected_hash}"
    if not str(res.get("verdict", "")).startswith("주 시험 통과"):
        return f"주 시험이 통과가 아니다({res.get('verdict')!r}) — 금고 확인 없음"
    return None


def vault_run(store: Any, data: Path) -> tuple[list[str], str]:
    """second 창 — 얼린 C0 × 실전 경로 FA(`vault_judge._c0_inputs`, P1B·TF 와 같은 입력) × 주 시험과 같은 포트·비중 규칙, E1 대 우승 변형."""
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from tools import vault_judge as vj

    res = json.loads((data / "_diag/cr-exposure/main.json").read_text())
    arm, margins, primary = res["winner"], res["margins"], res["primary"]
    preds, sessions, ret, bench, trad = vj._c0_inputs(store)
    end = vj.VAULT_END
    as_of = datetime.combine(end, time(16, 0), tzinfo=UTC)
    base = ExposureParams.from_store(store, as_of=as_of)
    floor = float(store.config(CRISIS_FLOOR_KEY, as_of=as_of))
    calendar = list(trading_days(Market.KR, vj.VAULT_START - timedelta(days=60), end + timedelta(days=10)))
    days = usable_days(sessions, calendar, end)                     # t+2 종가가 창 끝 안 — 창 밖 가격을 쓰지 않는다
    warm = [d for d in calendar if d < vj.VAULT_START][-5:]         # 확인 2세션 — 창 앞 국면
    closes = regime_closes(store, as_of=as_of, start=warm[0])
    states = regime_states(closes, [*warm, *days], floor)
    paths = {v: scale_path(states, variant_params(base, v)).reindex(days) for v in (CONTROL, arm)}
    log: dict[str, int] = {}
    weigh = rp_weigher(store, log, until=end) if primary == "RP" else equal_weigh
    out: dict[str, dict[int, pd.Series]] = {CONTROL: {}, arm: {}}
    for seed, pred in preds.items():
        tgt, _ = targets_from_scores(pred, ret, trad, days, weigh)
        for v, r in books(tgt, ret, bench, paths).items():
            out[v][int(seed)] = r
    lines, verdict = judge_vault(out[arm], out[CONTROL], margins, arm)
    lines.append(f"기록(기준 아님) 비중 {primary} · 국면 " + " · ".join(f"{k} {v}" for k, v in states.reindex(days).value_counts().items())
                 + f" · 주 시험 {res['first']}~{res['last']} {res['sessions']}세션")
    return lines, verdict


# --------------------------------------------------------------------------- CLI


PLAN = f"""시행 CR — 순서 (자료를 읽지 않는다)
0. 등록 문서 {PROTOCOL} 해시 고정(사용자 확인 뒤 리드) → 이 파일 PROTOCOL_HASH · vault_judge.CR_PROTOCOL_HASH 에 같은 값
1. 검정력(등록 전, 이미 함): .venv/bin/python tools/trial_cr_exposure.py --power
2. 주 시험(긴 과거 {MAIN_FIRST}~, 종가 ≤ {LAST_CLOSE}): nice -n 10 env QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB MALLOC_ARENA_MAX=2 OMP_NUM_THREADS=2 \\
     .venv/bin/python tools/trial_cr_exposure.py --run --save      # 가용 ≥ {MIN_AVAILABLE_GB:g}GB · 22:20~00:45 밖
   → research_trials 1행(entity {ENTITY}) · data/_diag/cr-exposure/main.json(우승 변형·금고 여유)
3. 통과면 11/23 금고 second 확인: .venv/bin/python tools/vault_judge.py --judge --window second --trials …,CR --save
   (주 시험 미통과면 판정부가 CR 을 건너뛴다)
4. 확인이면 실전 반영은 사용자 결정 — 반영하면 대조 장부(V6 그대로)를 두고 전방 순차 검정(modelops/sequential.py)으로 감시
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--power", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--data", default="data", help="창고 루트(기본 data)")
    parser.add_argument("--length", type=int, default=1180, help="--power 의 판정 창 길이(세션)")
    args = parser.parse_args(argv)
    if args.save and not args.run:
        parser.error("--save 는 --run 과 함께만")
    if args.plan:
        print(PLAN, flush=True)
        return 0
    if args.power:
        return run_power(Path(args.data), length=args.length)
    problem = protocol_problem()
    if problem:
        print(f"--run 거부 — {problem}", flush=True)
        return 2
    if not heavy_ok():
        return 2
    return run_main(Path(args.data), save=args.save)


if __name__ == "__main__":
    raise SystemExit(main())
