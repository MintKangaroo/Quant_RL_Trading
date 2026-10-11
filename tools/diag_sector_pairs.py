"""진단 — 섹터 회전 세 갈래 (docs/diag/sector-rotation-2026-10-11.md, 사용자 질문 10/11 "어디 섹터가 빠지면 어디가 오르나").

    (A) 섹터 쌍 지도 — A 가 빠진 날(같은 날 / d+1 진입 1·3·5세션) B 의 초과수익
    (B) 업종 안 선행(Hou 2007) — 섹터 대형(시총 상위 5)의 지난 1·5일 수익이 같은 섹터 중소형의 다음 5세션을 맞히나 + 삼전·닉스 → 반도체 중소형
    (C) 업종 모멘텀(Moskowitz–Grinblatt 1999) — 섹터 6·12개월(직전 1개월 제외) → 다음 1개월, 월말 리밸런싱, 상위3−하위3, 비용 후

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고·config·크론·장부를 바꾸지 않는다.
이 파일은 `tools/diag_rotation.py`(시소 진단, 같은 날)의 연장이다 — 유니버스·섹터(ksic.roll_up 24군집)·초대형·대형/중소형 구분·
매매 타이밍(d 종가 정보 → d+1 종가 진입)·NW 회귀·BH/본페로니·편도 비용 0.41% 를 **import 해서 그대로** 쓴다.
(`tools/diag_sector_rotation.py` 는 10/4 섹터 순환 진단이라 덮지 않고 이 이름으로 둔다.)

    ulimit -v 16777216; MALLOC_ARENA_MAX=2 QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 \
        nice -n 10 .venv/bin/python tools/diag_sector_pairs.py

**금고** — 수익·라벨은 2026-06-30 까지만(`R.END`, `R.guard`). data/_past_vault 는 열지 않는다. 섹터 분류만 2026-08 스냅샷(생존 편향, 문서에 적는다).

## 정의 (결과를 보기 전에 고정 — 이 뒤에 더한 것은 문서의 '사후' 절로, 다중검정 가족 밖)

공통: 결정일 2022-09-01~ (유니버스 이력 252 규칙), h=5 라벨 끝 ≤ 2026-06-30. 국면 박스 2022-09~2024-12 · 급등 2025-01~2026-06.

(A) 섹터 일수익 = 초대형 제외 유니버스(전일 구성) 동일가중, 그날 ≥10종목, 결정일 90% 이상 값이 있는 섹터만. 원천 A 에는
    '초대형(삼전+닉스 시총가중)' 을 하나 더 둔다(목적 B 로는 안 쓴다).
    **초과수익은 A 를 뺀 시장 EW 대비**(B_ex|A = B − 시장EW(A 제외)) — A 가 빠지면 시장 EW 가 A 의 몫만큼 같이 빠져서 B 의 '시장 대비'
    가 기계적으로 오르는 것을 막는다.
    사건 E1 = A 원수익이 결정 구간 하위 10% · E2 = A 초과수익(A 제외 시장 대비가 아니라 A − 전체 시장 EW) 하위 10%.
    ('≤ −2% 초과' 는 섹터마다 빈도가 수 배 달라 쓰지 않는다 — 대형 섹터 EW 는 초과 −2% 가 거의 없다.) 문턱은 결정 구간 전체 분포(사후 문턱, 진단용).
    ① 같은 날: B_ex|A 를 사건 더미에 회귀(차이 = 사건일 − 평일, NW lag 1). 상관행렬(원수익·시장 EW 대비 초과) 은 서술.
    ② 예측: B 의 d+1 종가 → d+1+h 종가(h 1·3·5) 섹터 EW 수익 − 같은 창 시장 EW(A 제외). 사건 더미 회귀, NW lag h, 전체·국면.
       가족 = A≠B 전 쌍 × E1·E2 × h (BH 10%·본페로니 5%). 자기 자신(A=B, 섹터 자기 반전)은 따로 작은 가족.
       '일관 쌍' = 전체 가족 BH 10% 생존 ∧ 박스·급등 차이가 전체와 같은 부호 ∧ 두 국면 각각 |t| ≥ 1.5.
    ③ 규칙(비용 후): 차이 > 0 인 줄 중 일관 쌍이 있으면 그중 전체 t 최대, 없으면 차이 > 0 전체 중 t 최대 한 줄.
       사건 문턱은 **직전 250세션 하위 10%(최소 120)** — 미래 분포를 안 쓴다. d+1 종가에 'B EW 매수 · 시장 EW(A 제외) 매도' 단위 1, h 세션 보유.
       진입·청산마다 두 다리 2 × 0.41%. 고른 줄 자체가 수천 줄의 최대값이라 낙관 편향이다(그래도 지면 확실히 없는 것).
(B) 섹터 대형 = 그날 유니버스 안 섹터 시총 상위 5(초대형 포함), 대형 수익 LR_L = 그 5종목 시총가중 지난 L(1·5)일 수익(≥3종목).
    대상 = 중소형(초대형 제외 유니버스 시총 순위 > 200, 섹터 대형 아님). 피처 gap_L = LR_L(자기 섹터) − 자기 지난 L일 수익, lead_L = LR_L.
    라벨 c(d+6)/c(d+1)−1 (d+1 진입 5세션). IC NW lag 4, 부분 IC 통제 6(rev5·mom60·lowvol·liq·로그시총·베타, N1 방식 둘 다 잔차화),
    랭커 잔차 IC(signals ranker). 가족 = 피처 4 원 IC + 반도체 시계열 8.
    반도체: 대상 = KSIC 261*(반도체) · 2927*(반도체·디스플레이 제조용 기계) 중소형. 그날 이 무리는 같은 섹터 신호를 공유하므로 횡단면 IC 가
    아니라 **시계열**: y = 반도체 중소형 EW 전방 h(1·5) − 중소형 전체 EW 전방 h, x = 초대형 지난 L(1·5)일 수익 (또는 gap = 초대형 − 반도체 중소형 자기 L일). NW lag h.
(C) 섹터 일수익 = **완화 유니버스**(20일 거래대금 ≥ 10억만, 이력 252 규칙 없음 — 12개월 신호가 2022-08 부터 서게), 전일 구성, ≥10종목,
    EW·전일 시총가중 둘 다. 월말(그달 마지막 세션) d 에 MOM6-1 = 로그수익 합 (d−125…d−21], MOM12-1 = (d−251…d−21] (창 90% 이상 값),
    전방 = d+2…d+22 (d+1 종가 진입 21세션). 섹터 ≥ 8 인 달만. IC Spearman, NW lag 1. 상위3−하위3, 상위3−전 섹터 EW.
    비용: 바뀐 칸마다 1/3 매도 + 1/3 매수 → 편도 0.41% × 2/3 (다리마다), 첫 달은 다리마다 1 × 0.41%. 섹터 안 비중 표류는 무시.
    가족 = 신호 2 × 묶음 2 × {IC, 상위3−하위3 총} = 8.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.store import Store
from tools import diag_n1_issuance as N1
from tools import diag_rotation as R
from tools import diag_ta_features as T

OUT = R.OUT
TAG = R.TAG
PREFIX = "diag-sector-rotation"
COST = R.COST
REGIMES = R.REGIMES
HORIZONS = R.HORIZONS
Q_EVENT = 0.10
SECTOR_COVER = 0.90
CONSIST_T = 1.5
RULE_WIN, RULE_MINP = 250, 120
LEADERS, LEADERS_MIN = 5, 3
SEMI_PREFIX = ("KSIC:261", "KSIC:2927")
MEGA_NAME = "초대형"
MONTH = 21


def fname(part: str, ext: str = "csv") -> Path:
    return OUT / f"{PREFIX}-{part}-{TAG}.{ext}"


def regime_cols(row: dict, y: pd.Series, x: pd.Series, lag: int, key: str = "차이") -> None:
    for rn, a, b in REGIMES:
        m = R.in_regime(y.index, a, b)
        bb, tt = R.nw_ols(y[m], x[m].to_frame(), lag)
        row[f"{key}_{rn}"], row[f"t_{rn}"] = bb[1], tt[1]
        row[f"n_{rn}"] = int((x[m] > 0).sum())


# --------------------------------------------------------------------------- 공통


def decision_days(P: dict) -> list:
    idx = P["idx"]
    sess = list(idx)
    pos = {x: i for i, x in enumerate(sess)}
    days = [x for x in idx if R.START_DECISION <= x <= R.END_DAY and pos[x] + 1 + max(HORIZONS) < len(sess)]
    R.guard([sess[pos[days[-1]] + 1 + max(HORIZONS)]])
    return days


def group_sum_n(x: pd.DataFrame, sec: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = [c for c in x.columns if c in sec.index]
    g = sec.reindex(cols)
    return x[cols].T.groupby(g).sum(min_count=1).T, x[cols].notna().T.groupby(g).sum().T


# --------------------------------------------------------------------------- (A) 섹터 쌍


def part_a_panels(P: dict, F: dict[int, pd.DataFrame], days: list) -> dict:
    sec = P["sec"]
    rest_prev, _, _ = R.size_masks(P["uni_prev"], P["prev_cap"], P["is_mega"])
    r = P["r"].where(rest_prev)
    gs, gn = group_sum_n(r, sec)
    S = (gs / gn).where(gn >= R.MIN_SECTOR)
    cover = S.loc[days].notna().mean()
    secs = [s for s in S.columns if cover[s] >= SECTOR_COVER]
    tot_s, tot_n = r.sum(axis=1, min_count=1), r.notna().sum(axis=1)
    mkt = tot_s / tot_n
    D = R.daily_series(P)
    # 앞으로(d 구성 = d 정보)
    rest, _, _ = R.size_masks(P["uni"], P["cap"], P["is_mega"])
    fw = {}
    for h in HORIZONS:
        fr = F[h].where(rest)
        fs, fn = group_sum_n(fr, sec)
        fw[h] = {"S": (fs / fn).where(fn >= R.MIN_SECTOR), "s": fs, "n": fn,
                 "tot_s": fr.sum(axis=1, min_count=1), "tot_n": fr.notna().sum(axis=1)}
    return {"S": S, "gs": gs, "gn": gn, "secs": secs, "mkt": mkt, "tot_s": tot_s, "tot_n": tot_n, "mega": D["mega"], "fw": fw,
            "cover": cover}


def mkt_ex(tot_s: pd.Series, tot_n: pd.Series, gs: pd.DataFrame, gn: pd.DataFrame, a: str) -> pd.Series:
    """시장 EW(A 제외). A 가 초대형이면 이미 빠져 있다."""
    if a == MEGA_NAME:
        return tot_s / tot_n
    return (tot_s - gs[a].fillna(0.0)) / (tot_n - gn[a].fillna(0))


def events_a(A: dict, days: list) -> dict[tuple[str, str], pd.Series]:
    S, mkt = A["S"], A["mkt"]
    src = {s: S[s] for s in A["secs"]} | {MEGA_NAME: A["mega"]}
    ev = {}
    for a, x in src.items():
        xr = x.loc[days]
        xe = (x - mkt).loc[days]
        ev[("E1", a)] = (xr <= xr.quantile(Q_EVENT)).where(xr.notna(), False).astype(bool)
        ev[("E2", a)] = (xe <= xe.quantile(Q_EVENT)).where(xe.notna(), False).astype(bool)
    return ev


def same_day_pairs(A: dict, ev: dict, days: list) -> pd.DataFrame:
    rows = []
    for (en, a), e in ev.items():
        mk = mkt_ex(A["tot_s"], A["tot_n"], A["gs"], A["gn"], a).loc[days]
        dummy = e.astype(float).rename("ev")
        for b in A["secs"]:
            if b == a:
                continue
            y = A["S"][b].loc[days] - mk
            bb, tt = R.nw_ols(y, dummy.to_frame(), 1)
            row = {"사건": en, "A": a, "B": b, "n_ev": int(e.sum()), "B 사건일 초과": y[e.to_numpy()].mean(),
                   "B 평일 초과": bb[0], "차이": bb[1], "t": tt[1], "B 사건일 원수익": A["S"][b].loc[days][e.to_numpy()].mean()}
            regime_cols(row, y, dummy, 1)
            row["p"] = R.p_of(tt[1])
            rows.append(row)
    return pd.DataFrame(rows)


def corr_tables(A: dict, days: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    S = A["S"][A["secs"]].loc[days]
    S[MEGA_NAME] = A["mega"].loc[days]
    raw = S.corr()
    ex = S.sub(A["mkt"].loc[days], axis=0).corr()
    return raw, ex


def forward_pairs(A: dict, ev: dict, days: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, self_rows = [], []
    for (en, a), e in ev.items():
        dummy = e.astype(float).rename("ev")
        for h in HORIZONS:
            fw = A["fw"][h]
            mk = mkt_ex(fw["tot_s"], fw["tot_n"], fw["s"], fw["n"], a).loc[days]
            for b in A["secs"]:
                y = fw["S"][b].loc[days] - mk
                bb, tt = R.nw_ols(y, dummy.to_frame(), h)
                row = {"사건": en, "A": a, "B": b, "h": h, "n_ev": int(e.sum()), "사건 후 평균": y[e.to_numpy()].mean(), "평일 평균": bb[0],
                       "차이": bb[1], "t": tt[1]}
                regime_cols(row, y, dummy, h)
                row["p"] = R.p_of(tt[1])
                (self_rows if b == a else rows).append(row)
    return pd.DataFrame(rows), pd.DataFrame(self_rows)


def consistent(df: pd.DataFrame, passed: set[str], key) -> pd.Series:
    s = np.sign(df["차이"])
    return (df.apply(key, axis=1).isin(passed) & (np.sign(df["차이_박스"]) == s) & (np.sign(df["차이_급등"]) == s)
            & (df["t_박스"].abs() >= CONSIST_T) & (df["t_급등"].abs() >= CONSIST_T))


def pair_key(r) -> str:
    return f"{r['사건']}|{r['A']}→{r['B']}|h{int(r['h'])}"


def rule_backtest(P: dict, A: dict, row: pd.Series, days: list) -> pd.DataFrame:
    """고른 한 줄을 규칙으로: 사건 문턱 = 직전 250세션 하위 10%(미래 분포 안 씀)."""
    a, b, h, en = row["A"], row["B"], int(row["h"]), row["사건"]
    x = A["mega"] if a == MEGA_NAME else A["S"][a]
    if en == "E2":
        x = x - A["mkt"]
    thr = x.shift(1).rolling(RULE_WIN, min_periods=RULE_MINP).quantile(Q_EVENT)
    e = (x <= thr).fillna(False)
    spread = A["S"][b] - mkt_ex(A["tot_s"], A["tot_n"], A["gs"], A["gn"], a)  # 그날 일수익(전일 구성)
    idx = list(P["idx"])
    pos = pd.Series(0.0, index=P["idx"])
    for i, d in enumerate(idx):
        if d >= days[0] and d <= days[-1] and bool(e.iloc[i]):
            pos.iloc[i + 2: i + 2 + h] = 1.0
    pos = pos.loc[days[0]:R.END_DAY]
    R.guard(list(pos.index))
    sp = spread.reindex(pos.index).fillna(0.0)
    gross = pos * sp
    chg = pos.diff().abs().fillna(pos.iloc[0])
    net = gross - chg * 2 * COST
    out = []
    for rn, lo, hi in (("전체", days[0], R.END_DAY), *REGIMES):
        m = R.in_regime(pos.index, lo, hi)
        yrs = m.sum() / 252
        g_, n_ = gross[m], net[m]
        out.append({"규칙": f"{en} {a}↓ → {b} 매수·시장(A 제외) 매도, h{h}", "구간": rn, "보유일 비율": pos[m].mean(),
                    "진입": int((pos[m].diff() > 0).sum()), "총 연환산": g_.sum() / yrs, "비용후 연환산": n_.sum() / yrs,
                    "비용후 IR": n_.mean() / n_.std() * math.sqrt(252) if n_.std() > 0 else np.nan,
                    "보유일 평균(bp)": g_[pos[m] > 0].mean() * 1e4, "보유일 t": R.nw_t(g_[pos[m] > 0], h)})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- (B) 업종 안 선행


def load_raw_ksic(store: Store) -> pd.Series:
    f = store.get("sectors", as_of=R.SECTOR_AS_OF, market="KR")
    f = f[f["source"] == "dart_company"].sort_values(["valid_from", "revision"])
    return f.groupby("entity_id")["sector"].last()


def part_b(P: dict, store: Store, ranker: pd.DataFrame, F: dict[int, pd.DataFrame], days_a: list) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    c, cap, uni, sec = P["c"], P["cap"], P["uni"], P["sec"]
    cols = [x for x in P["cols"] if x in sec.index]
    g = sec.reindex(cols)
    capu = cap.where(uni)[cols]
    rk = capu.T.groupby(g).rank(ascending=False).T
    leaders = (rk <= LEADERS).reindex(columns=P["cols"], fill_value=False)
    nsec = uni[cols].T.groupby(g).sum().T  # 그날 섹터 유니버스 종목 수
    _, _, small = R.size_masks(uni, cap, P["is_mega"])
    small = small & ~leaders & P["cols"].isin(cols)[None, :]
    rL = {1: P["r"], 5: (c / c.shift(5) - 1.0).where(lambda z: z.abs() <= 0.5)}
    feats = {}
    info = {}
    for L, rr in rL.items():
        lr = R.group_wmean(rr.where(leaders), cap, sec, LEADERS_MIN).where(nsec >= R.MIN_SECTOR)
        stock_lr = lr.reindex(columns=g.to_numpy())
        stock_lr.columns = cols
        stock_lr = stock_lr.reindex(columns=P["cols"])
        feats[f"lead_{L}"] = stock_lr.where(small)
        feats[f"gap_{L}"] = (stock_lr - rr).where(small)
    label = c.shift(-6) / c.shift(-1) - 1.0
    label = label.where(label.abs() <= 0.5).where(small)
    days = [x for x in P["idx"] if x >= R.START_DECISION]
    label = label.loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    sess = list(P["idx"])
    R.guard([sess[sess.index(days[-1]) + 6]])
    info["대상 중소형 평균 종목"] = float(label.notna().sum(axis=1).mean())
    info["결정일"] = f"{days[0]}~{days[-1]} {len(days)}일"
    ctl = {k: x.where(small).loc[days] for k, x in R.controls(P).items()}
    cl = [ctl[k] for k in ("_rev5", "_mom60", "_lowvol", "_liq", "_logcap", "_beta")]
    rkr = ranker.where(small).loc[days]
    rk_ic = T.rowwise_ic(rkr, label)
    info["랭커 IC(같은 대상·라벨)"] = f"{rk_ic.mean():.4f} t {R.nw_t(rk_ic, 4):.2f} n {len(rk_ic)}"
    rows = []
    for name, x in feats.items():
        X = x.loc[days]
        ic = T.rowwise_ic(X, label)
        row = {"피처": name, "n일": len(ic), "커버리지": float((X.notna() & label.notna()).sum(axis=1).sum() / label.notna().sum(axis=1).sum()),
               "IC": ic.mean(), "t": R.nw_t(ic, 4)}
        for rn, a, b in REGIMES:
            part = ic[R.in_regime(ic.index, a, b)]
            row[f"IC_{rn}"], row[f"t_{rn}"] = part.mean(), R.nw_t(part, 4)
        pic = N1.partial_ic(X, label, cl)
        row["편IC6"], row["편t6"] = pic.mean(), R.nw_t(pic, 4)
        for rn, a, b in REGIMES:
            row[f"편IC6_{rn}"] = pic[R.in_regime(pic.index, a, b)].mean()
        ric = N1.partial_ic(X, label, [rkr])
        row["랭커잔차IC"], row["랭커잔차t"], row["랭커잔차 n일"] = ric.mean(), R.nw_t(ric, 4), len(ric)
        row["ρ랭커"] = T.rowwise_ic(X, rkr).mean()
        row["ρ_rev5"] = T.rowwise_ic(X, ctl["_rev5"]).mean()
        dec = N1.deciles(X, label)
        row.update({f"D{i + 1}": v for i, v in enumerate(dec)})
        row["p"] = R.p_of(row["t"])
        rows.append(row)
        print(f"  (B) {name}: IC {row['IC']:.4f} t {row['t']:.2f} · 편IC6 {row['편IC6']:.4f} · 랭커잔차 {row['랭커잔차IC']:.4f} t {row['랭커잔차t']:.2f}", flush=True)
    fi = pd.DataFrame(rows).set_index("피처")
    # 반도체 시계열
    raw = load_raw_ksic(store)
    semi_ids = [x for x, k in raw.items() if isinstance(k, str) and k.startswith(SEMI_PREFIX) and x not in R.MEGA]
    semi = pd.Series(P["cols"].isin(semi_ids), index=P["cols"])
    _, _, small_all = R.size_masks(uni, cap, P["is_mega"])
    semi_small = small_all & semi.to_numpy()[None, :]
    info["반도체 중소형 평균 종목(결정일)"] = float(semi_small.loc[days_a].sum(axis=1).mean())
    info["반도체 분류 종목"] = len(semi_ids)
    mega_L = {}
    for L, rr in rL.items():
        w = cap[R.MEGA].where(rr[R.MEGA].notna())
        mega_L[L] = (rr[R.MEGA] * w).sum(axis=1, min_count=1) / w.sum(axis=1, min_count=1)
    srows = []
    for h in (1, 5):
        y = (F[h].where(semi_small).mean(axis=1) - F[h].where(small_all).mean(axis=1)).loc[days_a]
        for L, rr in rL.items():
            own = rr.where(semi_small).mean(axis=1)
            for form, x in (("초대형 L일", mega_L[L]), ("gap = 초대형 − 반도체중소형 L일", mega_L[L] - own)):
                xx = x.loc[days_a].rename("x")
                bb, tt = R.nw_ols(y, xx.to_frame(), h)
                row = {"형태": form, "L": L, "h": h, "기울기": bb[1], "t": tt[1], "상관": y.corr(xx), "y 평균": y.mean()}
                for rn, a, b in REGIMES:
                    m = R.in_regime(y.index, a, b)
                    b2, t2 = R.nw_ols(y[m], xx[m].to_frame(), h)
                    row[f"기울기_{rn}"], row[f"t_{rn}"] = b2[1], t2[1]
                # 초대형 L일 하위 10% 날(급락 다음) 의 y — 해석용
                q = xx.quantile(0.10)
                row["x 하위10% 날 y 평균"] = y[xx <= q].mean()
                row["p"] = R.p_of(tt[1])
                srows.append(row)
    return fi, pd.DataFrame(srows), info


# --------------------------------------------------------------------------- (C) 업종 모멘텀


def part_c(P: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    sec, r, v = P["sec"], P["r"], P["v"]
    liq = (T.rmean(v, 20) >= T.MIN_VALUE).shift(1, fill_value=False).astype(bool)
    rr = r.where(liq)
    daily = {"EW": R.group_mean(rr, sec), "CW": R.group_wmean(rr, P["prev_cap"], sec)}
    idx = list(P["idx"])
    pos = {x: i for i, x in enumerate(idx)}
    last: dict = {}
    for x in idx:
        last[(x.year, x.month)] = x
    month_end = list(last.values())
    rows_ic, rows_ls, picks = [], [], []
    info = {}
    for bn, S in daily.items():
        lg = np.log1p(S)
        ok = lg.notna().astype(float)
        cs, cn = lg.fillna(0.0).cumsum(), ok.cumsum()

        def window(i0: int, i1: int) -> pd.Series:  # 로그수익 합 (i0, i1] → 행 i0+1..i1
            s = cs.iloc[i1] - cs.iloc[i0]
            n = cn.iloc[i1] - cn.iloc[i0]
            return s.where(n >= 0.9 * (i1 - i0))

        for sn, back in (("MOM6-1", 126), ("MOM12-1", 252)):
            recs = []
            for d in month_end:
                i = pos[d]
                if i - back < 0 or i + 1 + MONTH >= len(idx) or d < date(2021, 9, 1):
                    continue
                sig = window(i - back, i - MONTH)
                fwd = np.expm1(window(i + 1, i + 1 + MONTH))  # d+2..d+22 = d+1 종가 → d+22 종가
                R.guard([idx[i + 1 + MONTH]])
                df = pd.concat([sig.rename("s"), fwd.rename("f")], axis=1).dropna()
                if len(df) < 8:
                    continue
                rk = df["s"].rank(ascending=False, method="first")
                top, bot = set(df.index[rk <= 3]), set(df.index[rk > len(df) - 3])
                recs.append({"d": d, "n": len(df), "IC": df["s"].corr(df["f"], method="spearman"),
                             "top": df.loc[list(top), "f"].mean(), "bot": df.loc[list(bot), "f"].mean(), "all": df["f"].mean(),
                             "top_set": top, "bot_set": bot})
            m = pd.DataFrame(recs).set_index("d")
            prev_t, prev_b = None, None
            ct, cb = [], []
            for d, x in m.iterrows():
                kt = 3 if prev_t is None else len(x["top_set"] - prev_t)
                kb = 3 if prev_b is None else len(x["bot_set"] - prev_b)
                ct.append(COST * (kt / 3) * (2 if prev_t is not None else 1))
                cb.append(COST * (kb / 3) * (2 if prev_b is not None else 1))
                prev_t, prev_b = x["top_set"], x["bot_set"]
                for s_ in x["top_set"]:
                    picks.append({"묶음": bn, "신호": sn, "d": d, "섹터": s_, "쪽": "상위3"})
                for s_ in x["bot_set"]:
                    picks.append({"묶음": bn, "신호": sn, "d": d, "섹터": s_, "쪽": "하위3"})
            m["ls"] = m["top"] - m["bot"]
            m["ls_net"] = m["ls"] - np.array(ct) - np.array(cb)
            m["tilt"] = m["top"] - m["all"]
            m["tilt_net"] = m["tilt"] - np.array(ct)
            m["회전(상위)"] = [np.nan] + [len(a - b) / 3 for a, b in zip(m["top_set"].iloc[1:], m["top_set"].iloc[:-1], strict=True)]
            segs = (("전체", date(2021, 9, 1), R.END_DAY), ("2022 하락(~2022-08)", date(2021, 9, 1), date(2022, 8, 31)), *REGIMES)
            for rn, a, b in segs:
                mm = m[R.in_regime(m.index, a, b)]
                if len(mm) < 3:
                    continue
                rows_ic.append({"묶음": bn, "신호": sn, "구간": rn, "달": len(mm), "섹터 평균": mm["n"].mean(),
                                "IC": mm["IC"].mean(), "t": R.nw_t(mm["IC"], 1), "IC>0 비율": (mm["IC"] > 0).mean()})
                rows_ls.append({"묶음": bn, "신호": sn, "구간": rn, "달": len(mm),
                                "상위3−하위3 월평균": mm["ls"].mean(), "t": R.nw_t(mm["ls"], 1),
                                "비용후 월평균": mm["ls_net"].mean(), "비용후 t": R.nw_t(mm["ls_net"], 1), "비용후 연환산": mm["ls_net"].mean() * 12,
                                "상위3−전체 월평균": mm["tilt"].mean(), "상위3−전체 t": R.nw_t(mm["tilt"], 1), "상위3−전체 비용후 연환산": mm["tilt_net"].mean() * 12,
                                "상위 회전 평균": mm["회전(상위)"].mean()})
            info[f"{bn}|{sn}"] = f"{m.index[0]}~{m.index[-1]} {len(m)}달"
            print(f"  (C) {bn} {sn}: {info[f'{bn}|{sn}']} · IC {m['IC'].mean():.3f} t {R.nw_t(m['IC'], 1):.2f}", flush=True)
    return pd.DataFrame(rows_ic), pd.DataFrame(rows_ls), pd.DataFrame(picks), info


# --------------------------------------------------------------------------- 사후


def main_posthoc() -> int:
    """사후 점검(결과를 본 뒤 추가 — 다중검정 가족 밖, 탐색으로만): (B) gap_L 의 BH 생존(gap_5 t 5.0, 랭커 잔차 t 4.1)이
    '대형 선행' 인지 '자기 L일 반전' 인지. gap_L = lead_L − own_L 이고 ρ(gap_5, rev5) = −0.88 이다.
    (i) −own_L 만의 원 IC·랭커 잔차 IC (ii) gap_L 의 부분 IC — 통제 [랭커, own_L] (iii) lead_L 의 부분 IC — 통제 [랭커, own_L]."""
    pd.set_option("display.width", 260)
    store = Store(root=Path("data"))
    P = R.build(store)
    c, cap, uni, sec = P["c"], P["cap"], P["uni"], P["sec"]
    cols = [x for x in P["cols"] if x in sec.index]
    g = sec.reindex(cols)
    rk = cap.where(uni)[cols].T.groupby(g).rank(ascending=False).T
    leaders = (rk <= LEADERS).reindex(columns=P["cols"], fill_value=False)
    nsec = uni[cols].T.groupby(g).sum().T
    _, _, small = R.size_masks(uni, cap, P["is_mega"])
    small = small & ~leaders & P["cols"].isin(cols)[None, :]
    label = c.shift(-6) / c.shift(-1) - 1.0
    label = label.where(label.abs() <= 0.5).where(small)
    days = [x for x in P["idx"] if x >= R.START_DECISION]
    label = label.loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    sess = list(P["idx"])
    R.guard([sess[sess.index(days[-1]) + 6]])
    rkr = R.load_ranker(store, P["idx"], P["cols"]).where(small).loc[days]
    rows = []
    for L, rr in ((1, P["r"]), (5, (c / c.shift(5) - 1.0).where(lambda z: z.abs() <= 0.5))):
        lr = R.group_wmean(rr.where(leaders), cap, sec, LEADERS_MIN).where(nsec >= R.MIN_SECTOR)
        slr = lr.reindex(columns=g.to_numpy())
        slr.columns = cols
        slr = slr.reindex(columns=P["cols"])
        own = rr.where(small).loc[days]
        X = {"−own": -own, "gap": (slr - rr).where(small).loc[days], "lead": slr.where(small).loc[days]}
        for name, x in X.items():
            ic = T.rowwise_ic(x, label)
            ric = N1.partial_ic(x, label, [rkr])
            row = {"L": L, "피처": f"{name}_{L}", "IC": ic.mean(), "t": R.nw_t(ic, 4), "랭커잔차IC": ric.mean(), "랭커잔차t": R.nw_t(ric, 4)}
            if name != "−own":
                p2 = N1.partial_ic(x, label, [rkr, own])
                row["편IC|랭커+own"], row["편t|랭커+own"] = p2.mean(), R.nw_t(p2, 4)
                for rn, a, b in REGIMES:
                    row[f"편IC|랭커+own_{rn}"] = p2[R.in_regime(p2.index, a, b)].mean()
                    row[f"편t|랭커+own_{rn}"] = R.nw_t(p2[R.in_regime(p2.index, a, b)], 4)
            rows.append(row)
            print(row, flush=True)
    out = pd.DataFrame(rows)
    print(out.round(4).to_string())
    out.to_csv(fname("posthoc-leadlag"), index=False)
    return 0


# --------------------------------------------------------------------------- main


def main() -> int:
    if "--posthoc" in sys.argv:
        return main_posthoc()
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 60)
    pd.set_option("display.max_rows", 500)
    OUT.mkdir(exist_ok=True)
    store = Store(root=Path("data"))
    P = R.build(store)
    F = R.fwd_panels(P)
    days = decision_days(P)
    print(f"패널 {P['idx'][0]}~{P['idx'][-1]} · 결정일 {days[0]}~{days[-1]} {len(days)}일", flush=True)
    summary: dict = {"결정일": f"{days[0]}~{days[-1]} {len(days)}"}

    print("\n=== (A) 섹터 쌍 ===", flush=True)
    A = part_a_panels(P, F, days)
    print(f"섹터 {len(A['secs'])}개(커버리지 ≥ {SECTOR_COVER:.0%}) · 빠진 섹터: "
          f"{ {s: round(float(c_), 2) for s, c_ in A['cover'].items() if s not in A['secs']} }", flush=True)
    summary["(A) 섹터"] = A["secs"]
    ev = events_a(A, days)
    summary["(A) 사건 수"] = {f"{k[0]}|{k[1]}": int(v.sum()) for k, v in ev.items()}
    sd = same_day_pairs(A, ev, days)
    raw_c, ex_c = corr_tables(A, days)
    fp, selfp = forward_pairs(A, ev, days)
    print(f"같은 날 {len(sd)}줄 · 예측 {len(fp)}줄 · 자기 {len(selfp)}줄", flush=True)

    fam = {
        "(A)② 쌍 예측": {pair_key(r): r["p"] for _, r in fp.iterrows()},
        "(A)② 자기 반전": {pair_key(r): r["p"] for _, r in selfp.iterrows()},
    }
    fam_same = {f"{r['사건']}|{r['A']}→{r['B']}": r["p"] for _, r in sd.iterrows()}
    mt_same = R.multi(fam_same)
    sd["BH10"] = [f"{r['사건']}|{r['A']}→{r['B']}" in set(mt_same["BH10"]) for _, r in sd.iterrows()]
    passed = set(T.bh(fam["(A)② 쌍 예측"]))
    fp["BH10"] = fp.apply(pair_key, axis=1).isin(passed)
    fp["본페로니5"] = fp["p"] <= 0.05 / len(fp)
    fp["일관"] = consistent(fp, passed, pair_key)
    fp["두국면 같은부호 |t|≥1.5"] = ((np.sign(fp["차이_박스"]) == np.sign(fp["차이_급등"])) & (fp["t_박스"].abs() >= CONSIST_T)
                                & (fp["t_급등"].abs() >= CONSIST_T))
    sp = set(T.bh(fam["(A)② 자기 반전"]))
    selfp["BH10"] = selfp.apply(pair_key, axis=1).isin(sp)

    print("\n같은 날 — 차이 상위/하위 15 (E2)")
    s2 = sd[sd["사건"] == "E2"].sort_values("t")
    print(pd.concat([s2.tail(15), s2.head(15)]).round(4).to_string())
    print(f"같은 날 가족 m={mt_same['m']} BH10 {len(mt_same['BH10'])} 본페로니 {len(mt_same['본페로니5'])}")
    lo = ex_c.where(~np.eye(len(ex_c), dtype=bool)).stack().sort_values()
    print("\n초과수익 상관 음수 하위 15:", lo.iloc[::2].head(15).round(3).to_dict())
    print(f"원수익 상관 최소 {raw_c.where(~np.eye(len(raw_c), dtype=bool)).min().min():.3f} · 음수 쌍 {(raw_c < 0).sum().sum() // 2}")

    print("\n예측 — 차이 t 상위/하위 20")
    fs = fp.sort_values("t")
    print(pd.concat([fs.tail(20), fs.head(20)]).round(4).to_string())
    print(f"BH10 {int(fp['BH10'].sum())} · 본페로니 {int(fp['본페로니5'].sum())} · 일관 {int(fp['일관'].sum())} · 두국면 같은부호 |t|≥1.5 {int(fp['두국면 같은부호 |t|≥1.5'].sum())}"
          f" (그중 차이>0 {int((fp['두국면 같은부호 |t|≥1.5'] & (fp['차이'] > 0)).sum())})")
    print("\n자기 반전 BH10:", int(selfp["BH10"].sum()))
    print(selfp.sort_values("t").round(4).head(8).to_string())

    cand = fp[(fp["차이"] > 0) & fp["일관"]]
    best_from = "일관 쌍" if len(cand) else "차이>0 전체"
    if not len(cand):
        cand = fp[fp["차이"] > 0]
    best = cand.dropna(subset=["t"]).sort_values("t").iloc[-1]
    print(f"\n규칙 점검 대상({best_from} 중 t 최대): {pair_key(best)} 차이 {best['차이']:.4f} t {best['t']:.2f}")
    rule = rule_backtest(P, A, best, days)
    print(rule.round(4).to_string())
    summary["(A) 규칙 대상"] = {"출처": best_from, "줄": pair_key(best), "차이": float(best["차이"]), "t": float(best["t"])}

    print("\n=== (B) 업종 안 선행 ===", flush=True)
    ranker = R.load_ranker(store, P["idx"], P["cols"])
    fi, semi, binfo = part_b(P, store, ranker, F, days)
    print(binfo)
    print(fi.round(4).T.to_string())
    print(semi.round(4).to_string())
    fam["(B) 업종 안 선행"] = {f"IC|{k}": fi.loc[k, "p"] for k in fi.index} | {
        f"반도체|{r['형태']}|L{r['L']}|h{r['h']}": r["p"] for _, r in semi.iterrows()}
    summary["(B)"] = binfo

    print("\n=== (C) 업종 모멘텀 ===", flush=True)
    cic, cls, picks, cinfo = part_c(P)
    print(cic.round(4).to_string())
    print(cls.round(4).to_string())
    fq = picks.groupby(["묶음", "신호", "쪽"])["섹터"].value_counts(normalize=False).groupby(level=[0, 1, 2]).head(3)
    print(fq.to_string())
    full_ic = cic[cic["구간"] == "전체"]
    full_ls = cls[cls["구간"] == "전체"]
    fam["(C) 업종 모멘텀"] = {f"IC|{r['묶음']}|{r['신호']}": R.p_of(r["t"]) for _, r in full_ic.iterrows()} | {
        f"LS|{r['묶음']}|{r['신호']}": R.p_of(r["t"]) for _, r in full_ls.iterrows()}
    summary["(C)"] = cinfo

    allp = {f"{fn}|{k}": p for fn, d_ in fam.items() for k, p in d_.items()}
    mt = {fn: R.multi(d_) for fn, d_ in fam.items()} | {"(A)① 같은 날": mt_same, "전체(예측만)": R.multi(allp)}
    print("\n=== 다중검정 ===")
    for fn, m in mt.items():
        print(f"{fn}: m={m['m']} · BH10% {len(m['BH10'])} · 본페로니5% {len(m['본페로니5'])}")
        if fn != "(A)① 같은 날":
            print("  BH10:", m["BH10"][:40])

    sd.to_csv(fname("sameday"), index=False)
    raw_c.to_csv(fname("corr-raw"))
    ex_c.to_csv(fname("corr-excess"))
    fp.to_csv(fname("pairs"), index=False)
    selfp.to_csv(fname("self"), index=False)
    for en in ("E1", "E2"):
        for h in HORIZONS:
            sub = fp[(fp["사건"] == en) & (fp["h"] == h)]
            sub.pivot(index="A", columns="B", values="t").to_csv(fname(f"tmatrix-{en}-h{h}"))
    rule.to_csv(fname("rule"), index=False)
    fi.to_csv(fname("leadlag"))
    semi.to_csv(fname("semi"), index=False)
    cic.to_csv(fname("indmom-ic"), index=False)
    cls.to_csv(fname("indmom-ls"), index=False)
    picks.to_csv(fname("indmom-picks"), index=False)
    fname("multi", "json").write_text(json.dumps({"검정": mt, "요약": summary}, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
