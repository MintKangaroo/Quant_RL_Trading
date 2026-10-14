"""11/23 등록 넷 — BLT · UIR · T2P · TK 의 주 시험(긴 과거 사다리)·검정력·얼리기·금고 확인(2026-10-14, 사용자 결정 10/14 아침).

    .venv/bin/python tools/trial_sprint_1123.py plan                         # 시행별 순서·필요 파일·해시 상태
    .venv/bin/python tools/trial_sprint_1123.py power --trial BLT            # 검정력(긴 과거 t ≥ 3 · 30세션 블록) — 가벼움
    .venv/bin/python tools/trial_sprint_1123.py bake --trial UIR|TK          # 재료 패널(무거움, 연구 슬롯 안)
    .venv/bin/python tools/trial_sprint_1123.py walk --trial UIR|TK          # 재료 워크포워드 처리·대조(무거움)
    .venv/bin/python tools/trial_sprint_1123.py freeze --trial T2P|TK|UIR    # 금고 확인용 얼린 모델(자료 끝 6/30, 학습 끝 = 6/30 − 10세션)
    .venv/bin/python tools/trial_sprint_1123.py run --trial BLT [--save]     # 주 시험 — 등록 해시가 고정·일치할 때만

| 시행 | 등록 문서 | 처리 | 대조 |
|---|---|---|---|
| BLT | `docs/protocols/be2-low-turnover-2026-10.md` | 얼린 설계 BE2 × 저회전(V1 = P1-b′ B1′ · V2 = B2′) | C0 × 현행(B0) · C0 × 같은 저회전 |
| UIR | `docs/protocols/us-insider-style-neutral-2026-10.md` | 현행 미장 랭커 규격 + Form 4 세 열의 스타일 잔차 | 현행 미장 랭커 규격(같은 폴드·시드 재학습) |
| T2P | `docs/protocols/t2-target-p1b-2026-10.md` | 정답 T2(업종·β·시총 잔차) GBM × P1-b′ B1′ | P1-b′(C0 × B1′) · 현행(C0 × B0) · 기록 T0 × B1′ |
| TK | `docs/protocols/ta-ranker-features-2026-10.md` | C0 6점수 + 기술 지표 여섯 재학습 | C0 6점수 재학습(같은 블록·시드) |

**봉인**: 모든 계산은 `eval_ladder.DATE_CAP`(2026-09-30) 안 — 점수·가격·라벨 어느 것도 10/1 이후를 쓰지 않는다(금고 second 봉인).
**해시 잠금**: `run` 은 `PROTOCOL_HASH[trial]` 이 None 이면(초안) 자료를 읽기 전에 거부하고, 문서 해시가 다르면 거부한다.
금고 second 창 판정은 `tools/vault_judge.py` 가 이 모듈의 `vault_prereq`·`vault_run` 을 부른다(해시가 고정됐을 때만 창에 붙는다 — C1G·CR 와 같은 규칙).
진단·시행 산출물은 `data/_diag/eval-ladder/sprint-1123/<시행>/`(창고 쓰기 0, `--save` 는 main.json 만).
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import eval_ladder as el  # noqa: E402

# --------------------------------------------------------------------------- 등록·고정값

TRIALS = ("BLT", "UIR", "T2P", "TK")
DOCS: dict[str, Path] = {
    "BLT": Path("docs/protocols/be2-low-turnover-2026-10.md"),
    "UIR": Path("docs/protocols/us-insider-style-neutral-2026-10.md"),
    "T2P": Path("docs/protocols/t2-target-p1b-2026-10.md"),
    "TK": Path("docs/protocols/ta-ranker-features-2026-10.md"),
}
#: 등록 문서 sha256 앞 16자 — **사용자 확인 뒤 리드가 고정할 때** 적는다(`vault_judge.<시행>_PROTOCOL_HASH` 와 같은 값, 테스트가 맞춘다).
PROTOCOL_HASH: dict[str, str | None] = {"BLT": None, "UIR": None, "T2P": None, "TK": None}
ENTITY = {"BLT": "be2-low-turnover-2026-10:BLT", "UIR": "us-insider-style-neutral-2026-10:UIR",
          "T2P": "t2-target-p1b-2026-10:T2P", "TK": "ta-ranker-features-2026-10:TK"}
FAMILY = {"BLT": "selection", "UIR": "ranker", "T2P": "selection", "TK": "ranker"}
OUT = el.OUT / "sprint-1123"

#: 국장 구성 — (N, 완충 배수, 재조정 R, 비용 인지 θ). P1-b′ 등록(`vault_judge.P1B_ARMS`)과 같은 값(테스트가 맞춘다).
ARMS: dict[str, tuple[int, int, int, float | None]] = {"B0": (24, 3, 10, None), "B1p": (100, 5, 20, 1.0), "B2p": (24, 3, 20, None)}
#: 미장 현행 구성 — 상위 24 · 완충 48(2N, `selector.exit_rank_us`) · 10세션(`rebalance_every_us`) · 평활 없음(`smoothing_span_us` 0).
US_ARM = (24, 2, 10, None)
#: 재조정 위상(§10 ② "위상 고르기 금지") — 시작 세션을 0·5·10·15 만큼 밀어 돌린 장부의 일수익 평균(R10 은 0·5 두 벌이 된다).
PHASES = (0, 5, 10, 15)

#: 주 시험 문턱 — 사용자 확정 "주장하는 칸 t ≥ 3, 특수 뺀 쪽 t ≥ 2·부호 같음".
T_STAR, EX_T, SEEDS_NEED = 3.0, 2.0, 4
#: ③ 해 방지 — MDD(처리) ≥ 현행 − 2%p 이고 ≥ 벤치 − 5%p. 회전: 저회전 시행 ≤ 현행 × 0.75(주장), 재료 시행 ≤ 대조 × 1.2(해 방지).
MDD_VS_CONTROL, MDD_VS_BENCH = 0.02, 0.05
TURN_LOW, TURN_HARM = 0.75, 1.2
#: T2P ② — 비열등(정답을 바꿔 순위 정보가 크게 줄지 않았다): ΔIC(T2 − T0) ≥ −0.002 를 t ≥ 2 로(평균 + 0.002 의 NW t).
T2P_NI, T2P_NI_T = 0.002, 2.0
#: T2P 주장 칸 — 재조정일 회전 짝 차(T2 × B1′ − C0 × B1′)의 NW t ≤ −3(회전이 줄었다).
T2P_TURN_T = -3.0
#: 금고 확인 여유 — 주 시험 창의 30세션 블록(5세션씩 밀며) 표준편차 × 1.645.
BLOCK, BLOCK_STEP, Z_MARGIN = 30, 5, 1.645
#: 다중검정 — 11/23 가족의 변형(BLT 1 · UIR 1 · TK 1, T2P 는 등록 안 함 — 본 것이라 그대로 센다 1) + 여유 2 + 같은 창에서 이미 본 질문 13
#: (레버 9 · 정답 2 · BE2 사다리 2). t ≥ 3 의 양측 p 0.0027 × 19 = 0.051 — Bonferroni 5% 와 거의 같다(필요 t 3.01).
N_TESTS = 19

#: 금고 확인 모델 자르는 날 — 얼린 C0·BE2 와 같다(자료 끝 2026-06-30, 학습 끝 = 그 앞 10세션 → 2026-06-16).
FREEZE_DATA_END = date(2026, 6, 30)

#: 미장 내부자 원 열(진단 `diag_ar_vs_current.G7`)과 잔차 열 이름.
G7 = ("form4_sell_60", "form4_sellers_20", "form4_buy_60")
UIR_COLS = tuple(f"{c}_sres" for c in G7)
#: 기술 지표 여섯(`ta_composite.composite_panel` 이 같은 함수로 만든다).
TK_COLS = ("shadow_asym_20", "pv_corr_20", "skew_60", "fip", "mom_12_1", "vol_cv_60")
TK_SIGN = {"shadow_asym_20": -1, "pv_corr_20": -1, "skew_60": -1, "fip": 1, "mom_12_1": 1, "vol_cv_60": -1}
UIR_SIGN = {"form4_sell_60_sres": -1, "form4_sellers_20_sres": -1, "form4_buy_60_sres": 1}


@dataclass(frozen=True)
class Spec:
    trial: str
    market: str
    kind: str                                   # "model"(점수 파일 비교) | "feature"(재료 재학습 짝)
    variants: dict[str, str]                    # 변형 → 구성(ARMS 키, 미장은 "U0")
    start: date | None = None                   # 주 시험 창 시작(점수 공통 창)
    low_turnover: bool = False
    note: str = ""
    signs: dict[str, int] = field(default_factory=dict)


SPECS: dict[str, Spec] = {
    "BLT": Spec("BLT", "KR", "model", {"V1": "B1p"}, start=date(2023, 2, 15), low_turnover=True,
                note="처리 BE2 · 원리 대조 C0 · 포트 대조 C0×B0(현행)·C0×같은 구성"),
    "T2P": Spec("T2P", "KR", "model", {"V1": "B1p"}, start=date(2022, 6, 28), low_turnover=True,
                note="처리 T2 · 원리 대조 T0(비열등) · 포트 대조 C0×B1′(P1-b′)·C0×B0(현행) · 기록 T0×B1′"),
    "TK": Spec("TK", "KR", "feature", {"V1": "B0"}, start=date(2022, 9, 1), note="C0 6점수 + 여섯 재학습 대 C0 6점수 재학습", signs=TK_SIGN),
    "UIR": Spec("UIR", "US", "feature", {"V1": "U0"}, note="미장 랭커 규격 + 잔차 세 열 대 같은 규격", signs=UIR_SIGN),
}


def log(msg: str) -> None:
    print(msg, flush=True)


def out_dir(trial: str, root: Path | None = None) -> Path:
    base = (root / "_diag/eval-ladder/sprint-1123") if root is not None else OUT
    return base / trial


# --------------------------------------------------------------------------- 해시 잠금


def doc_hash(trial: str, *, cwd: Path = REPO_ROOT) -> str:
    return hashlib.sha256((cwd / DOCS[trial]).read_bytes()).hexdigest()[:16]


def require_pinned(trial: str, *, cwd: Path = REPO_ROOT) -> str:
    """주 시험은 등록 해시가 고정되고 문서가 그대로일 때만 — 초안이면 자료를 읽기 전에 멈춘다(rc 2)."""
    expected = PROTOCOL_HASH[trial]
    if expected is None:
        raise SystemExit(f"{trial}: 등록 문서 {DOCS[trial]} 가 초안이다(PROTOCOL_HASH None) — 사용자 확인 뒤 고정한 다음 돈다")
    digest = doc_hash(trial, cwd=cwd)
    if digest != expected:
        raise SystemExit(f"{trial}: 문서 해시 {digest} ≠ 고정값 {expected} — 고정 뒤 문서가 바뀌었다(바꾸면 새 등록)")
    return digest


# --------------------------------------------------------------------------- 포트(위상 평균)


def arm_book(wide: pd.DataFrame, ctx: el.Context, arm: str, *, smooth: bool = True,
             phases: Sequence[int] = PHASES) -> tuple[pd.Series, pd.Series]:
    """넓은 점수(세션 × 종목) → 구성 `arm` 의 비용 후 일수익·일 회전(재조정 위상 평균).

    `eval_ladder.book` 과 같은 롱 다리(`diag_style_hedge_cost.long_leg` — EMA5 → 상위 N · 완충 · R · θ · 동일가중 · 드리프트)를
    위상마다 돌려 **공통 세션**에서 평균한다. 평활은 위상을 자르기 전에 한 번(데움이 같다).
    """
    from tools.diag_style_hedge_cost import long_leg

    n, mult, every, theta = US_ARM if arm == "U0" else ARMS[arm]
    w = wide.dropna(axis=1, how="all")
    if smooth:
        w = w.ewm(span=el.SPAN).mean()
    offs = sorted({int(k) % every for k in phases})
    nets, turns = [], []
    for k in offs:
        daily, turn, _ = long_leg(w.iloc[k:], ctx.ret, ctx.trad, n, every=every, mult=mult, theta=theta)
        turn = turn.reindex(daily.index).fillna(0.0)
        if ctx.cost != el.ONE_WAY_COST:                 # long_leg 는 국장 0.41% 를 박아 둔다
            daily = daily + turn * (el.ONE_WAY_COST - ctx.cost)
        nets.append(daily)
        turns.append(turn)
    common = nets[-1].index
    for s in nets[:-1]:
        common = common.intersection(s.index)
    net = pd.concat([s.reindex(common) for s in nets], axis=1).mean(axis=1)
    tn = pd.concat([s.reindex(common) for s in turns], axis=1).mean(axis=1)
    return net, tn


def _wide(p: pd.DataFrame, ctx: el.Context) -> pd.DataFrame:
    return el.as_wide(p, ctx)


def seed_books(preds: Mapping[int, pd.DataFrame], ctx: el.Context, arm: str, *, smooth: bool = True) -> dict[Any, tuple[pd.Series, pd.Series]]:
    """시드별 장부 + 묶음(시드 평균 점수 하나, 키 "bundle")."""
    out: dict[Any, tuple[pd.Series, pd.Series]] = {}
    for s, p in preds.items():
        out[int(s)] = arm_book(_wide(p, ctx), ctx, arm, smooth=smooth)
        gc.collect()
    out["bundle"] = arm_book(el.bundle(preds, ctx), ctx, arm, smooth=smooth) if len(preds) > 1 else out[next(iter(out))]
    return out


def summarize(net: pd.Series, turn: pd.Series, ctx: el.Context) -> dict[str, Any]:
    days = list(net.index)
    b = ctx.bench.reindex(days).fillna(0.0)
    u = ctx.uni.reindex(days)
    return {"ann": float(net.mean() * el.ANN), "vs_bench": el.describe(net - b, ctx), "vs_uni": el.describe(net - u, ctx),
            "mdd": el.mdd(net), "bench_mdd": el.mdd(b),
            "turn": float(turn.sum() * el.ANN / max(1, len(days))), "n": len(days)}


def paired(a: pd.Series, b: pd.Series, ctx: el.Context) -> dict[str, Any]:
    idx = a.index.intersection(b.index)
    return el.describe(a.reindex(idx) - b.reindex(idx), ctx)


# --------------------------------------------------------------------------- 블록 여유(금고 확인)


def block_margins(treat: pd.Series, ctrl: pd.Series, treat_turn: pd.Series, ctrl_turn: pd.Series,
                  dic: pd.Series | None, *, length: int = BLOCK, step: int = BLOCK_STEP) -> dict[str, float]:
    """주 시험 창의 30세션 블록(5세션씩 밀며) — 연수익 차·MDD 차·ΔIC 평균의 표준편차, 회전 비의 95% 분위.
    금고 확인 기준(Q1~Q4)의 여유가 여기서 나온다(금고 개봉 전에 정해진다)."""
    idx = treat.index.intersection(ctrl.index)
    t, c = treat.reindex(idx).fillna(0.0), ctrl.reindex(idx).fillna(0.0)
    tt, ct = treat_turn.reindex(idx).fillna(0.0), ctrl_turn.reindex(idx).fillna(0.0)
    ann, mdd, ratio = [], [], []
    for lo in range(0, len(idx) - length + 1, step):
        sl = slice(lo, lo + length)
        ann.append(float((t.iloc[sl] - c.iloc[sl]).mean() * el.ANN))
        mdd.append(el.mdd(t.iloc[sl]) - el.mdd(c.iloc[sl]))
        den = float(ct.iloc[sl].sum())
        if den > 0:
            ratio.append(float(tt.iloc[sl].sum()) / den)
    out = {"blocks": len(ann), "sd_ann": float(np.std(ann, ddof=1)) if len(ann) > 1 else float("nan"),
           "sd_mdd": float(np.std(mdd, ddof=1)) if len(mdd) > 1 else float("nan"),
           "turn_q95": float(np.quantile(ratio, 0.95)) if ratio else float("nan")}
    if dic is not None:
        d = dic.dropna()
        means = [float(d.iloc[lo:lo + length].mean()) for lo in range(0, len(d) - length + 1, step)]
        out["sd_dic"] = float(np.std(means, ddof=1)) if len(means) > 1 else float("nan")
    return out


# --------------------------------------------------------------------------- 판정(순수 함수 — 합성 자료로 테스트)


def _robust(d: dict[str, Any], *, t_min: float | None) -> bool:
    """전체 > 0 (· NW t ≥ t_min) 그리고 특수 뺀 쪽 > 0 (· t ≥ EX_T 는 t_min 이 있을 때만)."""
    ex = d["ex_special"]
    if not (np.isfinite(d["ann"]) and np.isfinite(ex["ann"])):
        raise ValueError("판정 지표가 nan — 조용히 기각하지 않는다")
    if t_min is None:
        return bool(d["ann"] > 0 and ex["ann"] > 0)
    return bool(d["ann"] > 0 and d["t"] >= t_min and ex["ann"] > 0 and ex["t"] >= EX_T)


def gates(trial: str, res: dict[str, Any]) -> dict[str, Any]:
    """주 시험 칸별 O/X. ``res`` 는 `run_main` 이 만든 결과(합성 자료 테스트가 같은 모양으로 만든다).

    ① 원리: IC > 0·t ≥ 3(특수 뺀 쪽 t ≥ 2) · D10−D1 같은 규칙. 재료 시행은 재료 열마다 등록 부호와 같은 방향 IC(|t| ≥ 3) 가 열의 2/3 이상.
    ② 한계기여: ΔIC > 0·t ≥ 3(특수 뺀 쪽 t ≥ 2)·시드 ≥ 4/5. T2P 는 비열등(ΔIC + 0.002 의 t ≥ 2, 특수 뺀 쪽 평균 ≥ −0.002).
    ③ 실제 포트(변형마다): a 대 현행 짝 차 > 0(전체·특수 뺀 쪽 부호)·시드 ≥ 4/5 · b 대 같은 구성 대조 > 0(두 쪽) ·
       c 대 유니버스 EW > 0(두 쪽) · d 대 벤치 특수 뺀 쪽 > 0(전체는 기록) · e MDD · f 회전.
    """
    spec = SPECS[trial]
    g: dict[str, Any] = {"principle": {}, "marginal": {}, "variants": {}}
    p = res["principle"]
    if spec.kind == "model":
        g["principle"]["①a IC"] = _robust(p["ic"], t_min=T_STAR)
        g["principle"]["①b D10−D1"] = _robust(p["d10_d1"], t_min=T_STAR)
    else:
        hits = 0
        for col, sign in spec.signs.items():
            d = p["features"][col]
            hits += int(np.sign(d["ann"]) == sign and abs(d["t"]) >= T_STAR and np.sign(d["ex_special"]["ann"]) == sign)
        g["principle"]["①c 재료 부호"] = hits >= int(np.ceil(len(spec.signs) * 2 / 3))
        g["principle"]["①a IC(처리 모델)"] = _robust(p["ic"], t_min=T_STAR)
    m = res["marginal"]
    if trial == "T2P":
        dic = m["dic"]
        shifted_t = (dic["ann"] + T2P_NI) / (dic["ann"] / dic["t"]) if dic["t"] not in (0, None) and np.isfinite(dic["t"]) and dic["ann"] != 0 else float("nan")
        if not np.isfinite(shifted_t):
            raise ValueError("T2P 비열등 t 를 못 잰다")
        g["marginal"]["②a 비열등 ΔIC"] = bool(shifted_t >= T2P_NI_T and dic["ex_special"]["ann"] >= -T2P_NI)
        g["marginal"]["②b 시드"] = True       # 비열등 시험엔 시드 방향을 묻지 않는다(기록)
    else:
        g["marginal"]["②a ΔIC"] = _robust(m["dic"], t_min=T_STAR)
        g["marginal"]["②b 시드"] = bool(m["seed_pos"] >= SEEDS_NEED)
    for v, r in res["variants"].items():
        c: dict[str, bool] = {}
        c["③a 대 현행"] = _robust(r["vs_current"], t_min=None) and r["seed_pos_current"] >= SEEDS_NEED
        if "vs_same" in r:
            c["③b 대 같은 구성"] = _robust(r["vs_same"], t_min=None)
        c["③c 대 U EW"] = _robust(r["vs_uni"], t_min=None)
        bench_ex = r["vs_bench"]["ex_special"]["ann"]
        if not np.isfinite(bench_ex):
            raise ValueError("대 벤치 특수 뺀 쪽이 nan")
        c["③d 대 벤치(특수 뺀 쪽)"] = bool(bench_ex > 0)
        c["③e MDD"] = bool(r["mdd"] >= r["mdd_current"] - MDD_VS_CONTROL and r["mdd"] >= r["bench_mdd"] - MDD_VS_BENCH)
        if spec.low_turnover:
            c["③f 회전(주장)"] = bool(r["turn"] <= r["turn_current"] * TURN_LOW)
        else:
            c["③f 회전(해 방지)"] = bool(r["turn"] <= r["turn_current"] * TURN_HARM)
        if trial == "T2P":
            c["③g 회전 짝 차 t"] = bool(r["turn_pair_t"] <= T2P_TURN_T)
        g["variants"][v] = c
    ok_pm = all(g["principle"].values()) and all(g["marginal"].values())
    passed = [v for v, c in g["variants"].items() if ok_pm and all(c.values())]
    g["passed"] = passed
    if passed:
        g["winner"] = max(passed, key=lambda v: res["variants"][v]["vs_current"]["t"])
        g["verdict"] = f"주 시험 통과 {g['winner']} — 금고 second 확인으로"
    else:
        first = next((k for k in ("principle", "marginal") if not all(g[k].values())), "variants")
        g["verdict"] = {"principle": "기각 — ① 원리", "marginal": "기각 — ② 한계기여", "variants": "기각 — ③ 실제 포트"}[first]
    return g


def judge_confirm(trial: str, dic: pd.Series | None, treat: dict[str, float], ctrl: dict[str, float],
                  margins: dict[str, float]) -> tuple[list[str], str]:
    """금고 second 확인(해 방지) — 방향이 운으로 설명 안 되게 뒤집히지 않았나. 주장은 하지 않는다.

    Q1 ΔIC 창 평균 ≥ −1.645 × sd_dic(원리 짝이 있는 시행) · Q2 연수익 차(처리 − 현행, 시드 평균) ≥ −1.645 × sd_ann ·
    Q3 MDD 차 ≥ −1.645 × sd_mdd · Q4 회전 비 ≤ 블록 95% 분위. 지표가 nan 이면 멈춘다.
    """
    need = [margins.get("sd_ann"), margins.get("sd_mdd"), margins.get("turn_q95")]
    if any(x is None or not np.isfinite(x) for x in need):
        raise ValueError(f"{trial}: 금고 여유가 비었다(main.json margins) — 판정 불가")
    for k in ("ann", "mdd", "turn"):
        if not (np.isfinite(treat.get(k, np.nan)) and np.isfinite(ctrl.get(k, np.nan))):
            raise ValueError(f"{trial}: 포트 지표 {k} 가 nan — 조용히 기각하지 않는다")
    lines, ok = [], []
    if dic is not None:
        d = dic.dropna()
        if d.empty or not np.isfinite(margins.get("sd_dic", np.nan)):
            raise ValueError(f"{trial}: ΔIC 또는 sd_dic 가 비었다")
        q1 = float(d.mean()) >= -Z_MARGIN * margins["sd_dic"]
        ok.append(q1)
        lines.append(f"Q1 ΔIC 창 평균 {d.mean():+.4f} (≥ {-Z_MARGIN * margins['sd_dic']:+.4f}, 세션 {len(d)}) {el.mark(q1)}")
    ann = treat["ann"] - ctrl["ann"]
    mdd = treat["mdd"] - ctrl["mdd"]
    ratio = treat["turn"] / max(ctrl["turn"], 1e-12)
    q2, q3, q4 = ann >= -Z_MARGIN * margins["sd_ann"], mdd >= -Z_MARGIN * margins["sd_mdd"], ratio <= margins["turn_q95"]
    ok += [q2, q3, q4]
    lines.append(f"Q2 연수익 차 {ann:+.1%}p (≥ {-Z_MARGIN * margins['sd_ann']:+.1%}p) {el.mark(q2)} · Q3 MDD 차 {mdd:+.1%}p "
                 f"(≥ {-Z_MARGIN * margins['sd_mdd']:+.1%}p) {el.mark(q3)} · Q4 회전 비 ×{ratio:.2f} (≤ ×{margins['turn_q95']:.2f}) {el.mark(q4)}")
    verdict = ("확인 — 금고에서 뒤집히지 않았다, 실전 반영은 사용자 결정(전방 순차 검정 병행)" if all(ok)
               else "기각 — 금고에서 해(방향이 여유 밖으로 뒤집혔다)")
    return lines, verdict


# --------------------------------------------------------------------------- 주 시험 — 모델 시행(BLT·T2P)


def _scores(name: str, lo: date | None) -> Mapping[int, pd.DataFrame]:
    return el.restrict(el.read_scores(name), lo, None)


def principle_block(treat: Mapping[int, pd.DataFrame], control: Mapping[int, pd.DataFrame], ctx: el.Context) -> dict[str, Any]:
    ics = el.ic_frame(treat, ctx)
    cics = el.ic_frame(control, ctx)
    common = sorted(set(ics.columns) & set(cics.columns))
    dic = pd.DataFrame({s: ics[s] - cics[s] for s in common})
    dec = pd.DataFrame({s: (lambda d: d["d10"] - d["d1"])(el.decile_frame(p, ctx)) for s, p in treat.items()}).mean(axis=1)
    return {"principle": {"ic": el.describe(ics.mean(axis=1).dropna(), ctx, scale=1), "d10_d1": el.describe(dec, ctx)},
            "marginal": {"dic": el.describe(dic.mean(axis=1).dropna(), ctx, scale=1),
                         "seed_pos": int((dic.mean(axis=0) > 0).sum()), "dic_seed": {int(s): float(dic[s].mean()) for s in dic}},
            "_dic_daily": dic.mean(axis=1).dropna()}


def variant_block(treat_books: dict[Any, tuple[pd.Series, pd.Series]], cur_books: dict[Any, tuple[pd.Series, pd.Series]],
                  same_books: dict[Any, tuple[pd.Series, pd.Series]] | None, ctx: el.Context) -> dict[str, Any]:
    bt, bc = treat_books["bundle"], cur_books["bundle"]
    st, sc = summarize(*bt, ctx), summarize(*bc, ctx)
    seeds = [k for k in treat_books if k != "bundle" and k in cur_books]
    pos = sum(1 for s in seeds if paired(treat_books[s][0], cur_books[s][0], ctx)["ann"] > 0)
    out = {"vs_current": paired(bt[0], bc[0], ctx), "seed_pos_current": pos, "vs_uni": st["vs_uni"], "vs_bench": st["vs_bench"],
           "ann": st["ann"], "mdd": st["mdd"], "mdd_current": sc["mdd"], "bench_mdd": st["bench_mdd"],
           "turn": st["turn"], "turn_current": sc["turn"],
           "seeds": {int(s): summarize(*treat_books[s], ctx) | {"vs_current": paired(treat_books[s][0], cur_books[s][0], ctx)["ann"]}
                     for s in seeds}}
    if same_books is not None:
        bs = same_books["bundle"]
        out["vs_same"] = paired(bt[0], bs[0], ctx)
        out["turn_same"] = summarize(*bs, ctx)["turn"]
        idx = bt[1].index.intersection(bs[1].index)
        rb = (bt[1].reindex(idx) + bs[1].reindex(idx)) > 0             # 재조정일(어느 쪽이든 회전이 난 날)
        out["turn_pair_t"] = el.nw_t((bt[1].reindex(idx) - bs[1].reindex(idx))[rb])
    return out


def run_model_trial(trial: str) -> dict[str, Any]:
    spec = SPECS[trial]
    ctx = el.load_context()
    treat_name, principle_ctrl = {"BLT": ("BE2", "C0"), "T2P": ("T2", "T0")}[trial]
    treat, pc = _scores(treat_name, spec.start), _scores(principle_ctrl, spec.start)
    c0 = _scores("C0", spec.start)
    res = principle_block(treat, pc, ctx)
    dic_daily = res.pop("_dic_daily")
    cur = seed_books(c0, ctx, "B0")
    res["variants"] = {}
    res["records"] = {}
    for v, arm in spec.variants.items():
        tb = seed_books(treat, ctx, arm)
        same = seed_books(c0, ctx, arm)
        res["variants"][v] = variant_block(tb, cur, same, ctx) | {"arm": arm}
        if trial == "T2P":
            t0b = seed_books(pc, ctx, arm)
            res["records"][f"{v} 대 T0×{arm}"] = paired(tb["bundle"][0], t0b["bundle"][0], ctx)
        res["variants"][v]["_books"] = (tb["bundle"], cur["bundle"])
        gc.collect()
    res["_dic_daily"] = dic_daily
    res["window"] = [str(spec.start), str(el.DATE_CAP)]
    return res


# --------------------------------------------------------------------------- 재료 시행(TK·UIR) — 패널·워크포워드


def tk_bake(root: str) -> Path:
    """기술 지표 여섯 패널(entity_id·session·여섯) — `ta_composite.composite_panel` 과 같은 함수·같은 대상(거래대금 ≥ 10억·이력 252),
    가격은 DATE_CAP 장 마감 as_of 한 번(지표는 결정일 d 종가까지만 쓴다 — 미래 라벨을 쓰는 trend_factor 는 여섯에 없다)."""
    el.require_heavy(False)
    from quant_rl_trading.store import Store
    from tools import diag_ta_features as T
    from tools import diag_ta_round2 as R2

    store = Store(root=Path(root))
    d = T.load(store, end=el.CAP_AS_OF, start=date(2020, 9, 1))
    c, v = d["close"], d["value"]
    el.guard_cap(c.index, "TK 가격")
    label = (c.shift(-6) / c.shift(-1) - 1.0)
    label = label.where(label.abs() <= 0.5)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    f1 = T.features(d)
    sig = R2.signals(store, d, uni, label.where(uni))
    cols = {"shadow_asym_20": f1["upper_shadow_20"] - f1["lower_shadow_20"], "pv_corr_20": f1["pv_corr_20"],
            "skew_60": f1["skew_60"], "fip": sig["fip"], "mom_12_1": sig["mom_12_1"], "vol_cv_60": sig["vol_cv_60"]}
    parts = []
    for k in TK_COLS:
        s = cols[k].where(uni).stack().rename(k)
        parts.append(s)
    panel = pd.concat(parts, axis=1).reset_index()
    panel.columns = ["session", "entity_id", *TK_COLS]
    panel["session"] = pd.to_datetime(panel["session"]).dt.date
    panel = panel[panel["session"] >= el.CONTEXT_START]
    path = out_dir("TK") / "features.parquet"  # invariant-allow: data-access — 시행 캐시
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(path, index=False)  # invariant-allow: data-access — 시행 캐시
    log(f"TK 재료 패널 {len(panel):,}행 · {panel['session'].min()}~{panel['session'].max()} → {path}")
    return path


def tk_panel(ctx: el.Context) -> tuple[pd.DataFrame, list[date]]:
    """C0 6점수(rank-gauss) + 여섯(rank-gauss, 결측 0) + 정답 y(h5 rank-gauss, 결측 그대로) — `eval_ladder.feature_marginal` 의 여러 열 판."""
    from tools.trial_pooled_rank import rank_gauss

    panel, sessions = el.score_panel(ctx)
    y5 = ctx.y5()
    panel = panel.merge(y5.rename(columns={"target": "r5"}), on=["entity_id", "session"], how="left")
    panel["y"] = panel["r5"]
    panel = rank_gauss(panel, ["y"])
    panel["y"] = panel["y"].where(panel["r5"].notna())
    feat = pd.read_parquet(out_dir("TK") / "features.parquet")  # invariant-allow: data-access — 시행 캐시
    feat["session"] = pd.to_datetime(feat["session"]).dt.date
    panel = panel.merge(feat, on=["entity_id", "session"], how="left")
    panel = rank_gauss(panel, list(TK_COLS))
    return panel, sessions


def tk_walk() -> None:
    el.require_heavy(False)
    ctx = el.load_context()
    panel, sessions = tk_panel(ctx)
    for name, feats in (("base", list(el.SCORE_FEATS)), ("plus", [*el.SCORE_FEATS, *TK_COLS])):
        preds = el.walk_kr(panel, sessions, feats, "y", el.SEEDS, label=f"TK {name}", progress=log)
        el.write_scores(preds, out_dir("TK") / f"{name}.parquet")  # invariant-allow: data-access — 시행 캐시
        del preds
        gc.collect()


def style_residual(x: np.ndarray, styles: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """세션(그룹)마다 피처 백분위를 [1, z(스타일 열들)] 에 최소제곱한 잔차. 피처·스타일 중 하나라도 결측이면 결측.
    진단 `diag_ar_vs_current.style_residual_ic` 의 피처 잔차와 같은 식(백분위 → 잔차)."""
    out = np.full(len(x), np.nan, dtype=np.float64)
    order = np.argsort(groups, kind="stable")
    g = groups[order]
    cuts = np.flatnonzero(np.r_[True, g[1:] != g[:-1], True])
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        rows = order[a:b]
        ok = ~np.isnan(x[rows]) & ~np.isnan(styles[rows]).any(axis=1)
        idx = rows[ok]
        if len(idx) < 50:
            continue
        xr = pd.Series(x[idx]).rank(pct=True).to_numpy()
        Z = styles[idx]
        sd = Z.std(0)
        Z = (Z - Z.mean(0)) / np.where(sd > 0, sd, 1.0)
        Z = np.column_stack([np.ones(len(Z)), Z])
        out[idx] = xr - Z @ np.linalg.lstsq(Z, xr, rcond=None)[0]
    return out


def uir_bake() -> Path:
    """미장 Form 4 세 열의 스타일 잔차 — 내부자 심화 압축 패널(`ar/panel-US`)과 **같은 행 순서**로 세 열만 쓴다.
    스타일 = log ADV20(원 종가 × 원 거래량 20일 평균 — 분할 보정 가격 × 원 거래량을 쓰지 않는다, 10/7 명단 오염 교훈) ·
    log 원 종가 · 60일 일수익 변동성(보정 종가). 모두 결정일 d 종가까지."""
    el.require_heavy(False)
    import pyarrow.parquet as pq  # invariant-allow: data-access — 진단 캐시

    from tools import diag_ar_vs_current as ar

    ctx = el.load_context(ar.CTX_US)
    t = pq.read_table(el.score_path("panel-US", ar.OUT), columns=["entity_id", "ord", *G7])  # invariant-allow: data-access — 진단 캐시
    ords = t.column("ord").to_numpy()
    # 행마다 문자열·date 객체를 만들지 않는다(660만 행 — 4GB 상한) — 고유 세션·고유 종목에서 찾아 펼친다.
    uord, inv_d = np.unique(ords, return_inverse=True)
    ecol = t.column("entity_id").dictionary_encode().combine_chunks()
    edict = pd.Index(ecol.dictionary.to_pylist())
    inv_e = ecol.indices.to_numpy(zero_copy_only=False)
    udays = pd.Index([date.fromordinal(int(o)) for o in uord])
    adv = pd.read_pickle(ar.OUT / "us-adv20.pkl")  # invariant-allow: data-access — 진단 캐시
    px = pd.read_pickle(ar.OUT / "us-rawclose.pkl")  # invariant-allow: data-access — 진단 캐시
    vol = ctx.close.pct_change(fill_method=None).rolling(60, min_periods=30).std()

    def look(w: pd.DataFrame) -> np.ndarray:
        ri = w.index.get_indexer(udays)[inv_d]
        ci = w.columns.get_indexer(edict)[inv_e]
        vals = np.full(len(ords), np.nan)
        ok = (ri >= 0) & (ci >= 0)
        vals[ok] = w.to_numpy(np.float64)[ri[ok], ci[ok]]
        return vals

    la = look(adv)
    lp = look(px)
    styles = np.column_stack([np.log(np.where(la > 0, la, np.nan)), np.log(np.where(lp > 0, lp, np.nan)), look(vol)])
    del adv, px, vol
    gc.collect()
    res = {}
    for c, name in zip(G7, UIR_COLS, strict=True):
        res[name] = style_residual(t.column(c).to_numpy(zero_copy_only=False).astype(np.float64), styles, ords).astype(np.float32)
        log(f"  {name}: 값 있는 행 {int(np.isfinite(res[name]).sum()):,}")
    frame = pd.DataFrame({"ord": ords.astype(np.int32), **res})
    path = out_dir("UIR") / "resid-US.parquet"  # invariant-allow: data-access — 시행 캐시
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)  # invariant-allow: data-access — 시행 캐시
    log(f"UIR 잔차 열 {len(frame):,}행 → {path}")
    return path


def uir_walk() -> None:
    """합동(국장+미장) 연 단위 워크포워드 — 진단 AR 과 같은 폴드·시드·하이퍼파라미터. base = 6점수 + is_us(현행 미장 랭커 규격),
    plus = base + 잔차 세 열(국장 행은 결측 → rank-gauss 0). 같은 실행에서 둘 다 배운다(같은 폴드·같은 시드)."""
    el.require_heavy(False)
    import lightgbm as lgb

    from tools import diag_ar_vs_current as ar

    X, y, sidx, market, ent, axis, names = ar.load_pooled()
    resid = pd.read_parquet(out_dir("UIR") / "resid-US.parquet")  # invariant-allow: data-access — 시행 캐시
    us = market == 1
    if int(us.sum()) != len(resid):
        raise SystemExit(f"잔차 행 수 {len(resid):,} ≠ 미장 패널 {int(us.sum()):,} — 굽기와 패널이 다르다")
    key = market.astype(np.int64) * 100_000 + sidx
    extra = np.zeros((len(y), len(UIR_COLS)), dtype=np.float32)
    for j, c in enumerate(UIR_COLS):
        col = np.full(len(y), np.nan, dtype=np.float64)
        col[us] = resid[c].to_numpy(np.float64)
        extra[:, j] = ar.rank_gauss_array(col, key)
    del resid
    base_idx = [ar.NUM.index(c) for c in ar.CONTROL]
    seeds = el.SEEDS
    ax = np.array(axis, dtype=object)
    for arm in ("base", "plus"):
        preds = {s: np.full(len(y), np.nan, dtype=np.float32) for s in seeds}
        xs = X[:, base_idx] if arm == "base" else np.hstack([X[:, base_idx], extra])   # 팔마다 한 번(폴드마다 복사하지 않는다)
        for year in ar.YEARS:
            lo = next(i for i, d in enumerate(axis) if d >= date(year, 1, 1))
            hi = max(i for i, d in enumerate(axis) if d <= min(date(year, 12, 31), el.DATE_CAP))
            te = lo - ar.GAP - 1
            idx_tr = np.flatnonzero((sidx <= te) & ~np.isnan(y))
            idx_te = np.flatnonzero((sidx >= lo) & (sidx <= hi))
            ds = lgb.Dataset(xs[idx_tr], y[idx_tr], free_raw_data=True)
            ds.construct()
            xt = xs[idx_te]
            for s in seeds:
                preds[s][idx_te] = ar.fit(ds, s).predict(xt).astype(np.float32)
            log(f"  UIR {arm} {year}: 학습 ~{axis[te]} · 채점 {axis[lo]}~{axis[hi]}")
            del ds, xt
            gc.collect()
        del xs

        def gen(preds: dict = preds):  # type: ignore[no-untyped-def]
            for s in seeds:
                ok = us & ~np.isnan(preds[s])
                yield s, pd.DataFrame({"entity_id": names[ent[ok]], "session": ax[sidx[ok]], "pred": preds[s][ok].astype(float)})
        el.write_scores(gen(), out_dir("UIR") / f"{arm}.parquet")  # invariant-allow: data-access — 시행 캐시
        del preds
        gc.collect()


def feature_principle(trial: str, ctx: el.Context) -> dict[str, Any]:
    """① 재료 열마다 거래가능 전 종목 h5 IC(원값, 등록 부호와 비교). 이미 본 숫자(진단)와 같은 질문 — 기록에 가깝다."""
    out: dict[str, Any] = {}
    if trial == "TK":
        feat = pd.read_parquet(out_dir("TK") / "features.parquet")  # invariant-allow: data-access — 시행 캐시
        feat["session"] = pd.to_datetime(feat["session"]).dt.date
        for c in TK_COLS:
            out[c] = el.describe(el.ic_frame({0: feat[["entity_id", "session", c]].rename(columns={c: "pred"})}, ctx)[0], ctx, scale=1)
    else:
        import pyarrow.parquet as pq  # invariant-allow: data-access — 진단 캐시

        from tools import diag_ar_vs_current as ar

        t = pq.read_table(el.score_path("panel-US", ar.OUT), columns=["entity_id", "ord"])  # invariant-allow: data-access — 진단 캐시
        resid = pd.read_parquet(out_dir("UIR") / "resid-US.parquet")  # invariant-allow: data-access — 시행 캐시
        base = pd.DataFrame({"entity_id": t.column("entity_id").to_pylist(),
                             "session": [date.fromordinal(int(o)) for o in t.column("ord").to_numpy()]})
        for c in UIR_COLS:
            out[c] = el.describe(el.ic_frame({0: base.assign(pred=resid[c].to_numpy())}, ctx)[0], ctx, scale=1)
    return out


def run_feature_trial(trial: str) -> dict[str, Any]:
    from tools import diag_ar_vs_current as ar

    ctx = el.load_context() if trial == "TK" else el.load_context(ar.CTX_US)
    lo = SPECS[trial].start                     # TK: 여섯이 이력 252세션을 채운 뒤(창고 가격 2021-08~ → 2022-09~)
    plus = el.restrict(el.read_scores(out_dir(trial) / "plus.parquet"), lo, None)  # invariant-allow: data-access — 시행 캐시
    base = el.restrict(el.read_scores(out_dir(trial) / "base.parquet"), lo, None)  # invariant-allow: data-access — 시행 캐시
    pb = principle_block(plus, base, ctx)
    res: dict[str, Any] = {"principle": pb["principle"] | {"features": feature_principle(trial, ctx)},
                           "marginal": pb["marginal"]}
    dic_daily = pb["_dic_daily"]
    arm = SPECS[trial].variants["V1"]
    smooth = trial == "TK"                      # 미장 현행 규칙은 평활 없음
    tb = seed_books(plus, ctx, arm, smooth=smooth)
    cb = seed_books(base, ctx, arm, smooth=smooth)
    res["variants"] = {"V1": variant_block(tb, cb, None, ctx) | {"arm": arm, "_books": (tb["bundle"], cb["bundle"])}}
    if trial == "TK":                            # 기록: 현행 랭커 이음(C0) 대비
        c0 = seed_books(el.restrict(el.read_scores("C0"), lo, None), ctx, "B0")
        res["records"] = {"V1 대 C0 이음(현행 랭커)": paired(tb["bundle"][0], c0["bundle"][0], ctx)}
    res["_dic_daily"] = dic_daily
    return res


# --------------------------------------------------------------------------- 실행·저장


def _strip(res: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in res.items() if not k.startswith("_")}
    out["variants"] = {v: {k: x for k, x in r.items() if not k.startswith("_")} for v, r in res["variants"].items()}
    return out


def run_main(trial: str, *, save: bool, root: Path) -> int:
    digest = require_pinned(trial)
    el.require_heavy(False)
    res = run_model_trial(trial) if SPECS[trial].kind == "model" else run_feature_trial(trial)
    g = gates(trial, res)
    out = {"protocol_hash": digest, "trial": trial, "verdict": g["verdict"], "gates": g, "n_tests": N_TESTS,
           "result": el._jsonable(_strip(res))}
    if g.get("winner"):
        w = res["variants"][g["winner"]]
        (tn, tt), (cn, ct) = w["_books"]
        out["winner"] = g["winner"]
        out["arm"] = w["arm"]
        out["margins"] = block_margins(tn, cn, tt, ct, res["_dic_daily"])
        log(f"금고 확인 여유(주 시험 창 30세션 블록) {out['margins']}")
    log(json.dumps({"verdict": g["verdict"], "gates": g}, ensure_ascii=False, indent=1, default=str))
    if save:
        path = out_dir(trial, root) / "main.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        log(f"저장 {path}")
    return 0


# --------------------------------------------------------------------------- 검정력(가벼움 — 이미 있는 점수만)


def power(trial: str) -> int:
    """긴 과거 주 시험과 30세션 금고의 원리 짝 검정력.

    BLT(BE2 − C0)·T2P(T2 − T0) 는 이미 본 짝 계열 그대로(평균은 10/14 사다리가 이미 공개했다), TK·UIR 은 처리 예측이 아직 없어
    **같은 꼴의 재료 짝**(AR − AR 대조, 국장·미장 — 6점수 위에 열을 더한 재학습 짝)의 계열을 대리로 쓴다(평균은 빼고 분산만).
    """
    from tools import diag_ar_vs_current as ar

    if trial in ("BLT", "T2P"):
        ctx = el.load_context()
        a, b = {"BLT": ("BE2", "C0"), "T2P": ("T2", "T0")}[trial]
        lo = SPECS[trial].start
        ta, tb = _scores(a, lo), _scores(b, lo)
        deltas = [0.0025, 0.005, 0.0087, 0.012] if trial == "BLT" else [0.001, 0.002, 0.003]
    else:
        ctx = el.load_context() if trial == "TK" else el.load_context(ar.CTX_US)
        m = "KR" if trial == "TK" else "US"
        ta, tb = el.read_scores(el.score_path(f"AR-{m}", ar.OUT)), el.read_scores(el.score_path(f"ARc-{m}", ar.OUT))
        deltas = [0.002, 0.004, 0.006, 0.008]
    ia, ib = el.ic_frame(ta, ctx), el.ic_frame(tb, ctx)
    d = pd.DataFrame({s: ia[s] - ib[s] for s in sorted(set(ia) & set(ib))}).mean(axis=1).dropna()
    centered = d - d.mean()
    se_full = float(centered.std(ddof=1)) / np.sqrt(len(d)) * _nw_inflation(centered)
    lines = [f"{trial} 원리 짝 계열 {len(d)}세션 · 일 표준편차 {centered.std():.4f} · 긴 과거 평균의 NW 표준오차 {se_full:.5f}"]
    from scipy.stats import norm

    for t_min, label in ((T_STAR, "긴 과거 t ≥ 3"),):
        mde = (t_min + 0.84) * se_full
        lines.append(f"  {label}: 최소 탐지 효과(80%) {mde:+.4f} · " + " · ".join(
            f"δ {x:+.4f} → {1 - norm.cdf(t_min - x / se_full):.0%}" for x in deltas))
    blocks = [float(centered.iloc[i:i + BLOCK].mean()) for i in range(0, len(centered) - BLOCK + 1, BLOCK_STEP)]
    sd30 = float(np.std(blocks, ddof=1))
    lines.append(f"  30세션 블록 평균 표준편차 {sd30:.4f} → 금고 확인 Q1 여유 −1.645×sd = {-Z_MARGIN * sd30:+.4f} · "
                 f"최소 탐지(80%·양측 5%) ≈ {2.80 * sd30:.4f}")
    for x in deltas:
        lines.append(f"  δ {x:+.4f}: 30세션 평균 > 0 확률 {norm.cdf(x / sd30):.0%} · Q1 통과 확률 {norm.cdf((x + Z_MARGIN * sd30) / sd30):.0%}")
    log("\n".join(lines))
    return 0


def _nw_inflation(x: pd.Series, lag: int = el.LAG) -> float:
    """NW 장기분산 / 표본분산 의 제곱근(자기상관 보정 배수)."""
    v = x.to_numpy(float) - float(x.mean())
    n = len(v)
    g0 = float(v @ v) / n
    lr = g0 + 2 * sum((1 - k / (lag + 1)) * float(v[k:] @ v[:-k]) / n for k in range(1, lag + 1))
    return float(np.sqrt(max(lr, 1e-18) / g0))


# --------------------------------------------------------------------------- 얼리기(금고 확인용)


def freeze_dir(trial: str, root: Path | None = None) -> Path:
    return out_dir(trial, root) / "models"


def freeze(trial: str) -> int:
    """금고 확인용 모델 — 주 시험 패널을 FREEZE_DATA_END 까지로 자르고 학습 끝 = 그 앞 10세션(얼린 C0·BE2 와 같은 규칙), 시드 0~4.
    파일 sha256 을 출력한다 — 등록 문서 '모델 해시' 절에 적은 뒤 문서 해시를 고정한다. BLT 는 이미 얼린 BE2·C0 를 쓴다(새 학습 없음)."""
    if trial == "BLT":
        raise SystemExit("BLT 는 이미 얼린 BE2·C0(data/models/be2, 해시 e3c347d129cc1c33 · 2f856986d7e668a8)를 쓴다 — 새로 얼리지 않는다")
    el.require_heavy(False)
    folder = freeze_dir(trial)
    folder.mkdir(parents=True, exist_ok=True)
    ctx = el.load_context() if trial != "UIR" else None
    if trial == "T2P":
        from tools.diag_translation_loss import targets
        from tools.trial_pooled_rank import rank_gauss

        panel, sessions = el.score_panel(ctx)
        tg = targets(ctx, sessions)
        panel = panel.merge(tg, on=["entity_id", "session"], how="left")
        have = panel["r5"].notna()
        panel["y_r5"], panel["y_t2"] = panel["r5"], panel["t2"]
        panel = rank_gauss(panel, ["y_r5", "y_t2"])
        arms = {"T0": ("y_r5", list(el.SCORE_FEATS)), "T2": ("y_t2", list(el.SCORE_FEATS))}
        for k in ("y_r5", "y_t2"):
            panel[k] = panel[k].where(have)
    elif trial == "TK":
        panel, sessions = tk_panel(ctx)
        arms = {"base": ("y", list(el.SCORE_FEATS)), "plus": ("y", [*el.SCORE_FEATS, *TK_COLS])}
    else:
        return _freeze_uir(folder)
    kr_sessions = [d for d in sessions if d <= FREEZE_DATA_END]
    end = kr_sessions[-(10 + 1)]
    for arm, (target, feats) in arms.items():
        train = panel[(panel["session"] <= end) & panel[target].notna()]
        import lightgbm as lgb
        ds = lgb.Dataset(train[feats].to_numpy(np.float32), train[target].to_numpy(np.float32), free_raw_data=False)
        for s in el.SEEDS:
            path = folder / f"{trial}-{arm}-s{s}.txt"
            el.fit(None, None, seed=int(s), dataset=ds).save_model(str(path))
            log(f"{path.name} {hashlib.sha256(path.read_bytes()).hexdigest()[:16]} · 학습 끝 {end} · 열 {feats}")
        del ds, train
        gc.collect()
    return 0


def _freeze_uir(folder: Path) -> int:
    import lightgbm as lgb

    from tools import diag_ar_vs_current as ar

    X, y, sidx, market, ent, axis, names = ar.load_pooled()
    resid = pd.read_parquet(out_dir("UIR") / "resid-US.parquet")  # invariant-allow: data-access — 시행 캐시
    us = market == 1
    key = market.astype(np.int64) * 100_000 + sidx
    extra = np.zeros((len(y), len(UIR_COLS)), dtype=np.float32)
    for j, c in enumerate(UIR_COLS):
        col = np.full(len(y), np.nan)
        col[us] = resid[c].to_numpy(np.float64)
        extra[:, j] = ar.rank_gauss_array(col, key)
    last = max(i for i, d in enumerate(axis) if d <= FREEZE_DATA_END)
    te = last - ar.GAP
    idx = np.flatnonzero((sidx <= te) & ~np.isnan(y))
    base_idx = [ar.NUM.index(c) for c in ar.CONTROL]
    for arm in ("base", "plus"):
        xs = X[idx][:, base_idx] if arm == "base" else np.hstack([X[idx][:, base_idx], extra[idx]])
        ds = lgb.Dataset(xs, y[idx], free_raw_data=True)
        ds.construct()
        for s in el.SEEDS:
            path = folder / f"UIR-{arm}-s{s}.txt"
            ar.fit(ds, s).save_model(str(path))
            log(f"{path.name} {hashlib.sha256(path.read_bytes()).hexdigest()[:16]} · 학습 끝 {axis[te]}")
        del ds, xs
        gc.collect()
    return 0


# --------------------------------------------------------------------------- 금고 second 확인 (vault_judge 가 부른다)


def vault_prereq(trial: str, data: Path, expected_hash: str | None) -> str | None:
    """금고에 붙기 전 조건 — 주 시험 결과 파일이 같은 등록 해시로 '주 시험 통과' 였다. 아니면 건너뛸 이유(CR 과 같은 규칙)."""
    path = out_dir(trial, data) / "main.json"
    if not path.exists():
        return f"주 시험 결과 {path} 가 없다(run --save 전)"
    res = json.loads(path.read_text())
    if res.get("protocol_hash") != expected_hash:
        return f"주 시험 결과의 등록 해시 {res.get('protocol_hash')} ≠ {expected_hash}"
    if not str(res.get("verdict", "")).startswith("주 시험 통과"):
        return f"주 시험이 통과가 아니다({res.get('verdict')!r}) — 금고 확인 없음"
    return None


def _main_json(trial: str, data: Path) -> dict[str, Any]:
    return json.loads((out_dir(trial, data) / "main.json").read_text())


def _model_hashes(trial: str, doc: Path | None = None) -> dict[str, str]:
    """등록 문서 '모델 해시' 절 — `이름.txt` 16자 줄. 없으면 판정 거부(얼리기 → 해시 기입 → 고정 순서)."""
    import re

    body = (doc or (REPO_ROOT / DOCS[trial])).read_text()
    head = body.find("## 모델 해시")
    if head < 0:
        raise SystemExit(f"{DOCS[trial]} 에 '모델 해시' 절이 없다 — 판정 거부")
    found = dict(re.findall(r"`([A-Za-z0-9_.-]+\.txt)` ([0-9a-f]{16})", body[head:]))
    if not found:
        raise SystemExit(f"{DOCS[trial]} '모델 해시' 절이 비었다 — 얼리기 뒤 해시를 적고 고정해야 한다")
    return found


def _load_frozen(trial: str, arm: str, data: Path) -> dict[int, Any]:
    import lightgbm as lgb

    expected = _model_hashes(trial)
    out = {}
    for s in el.SEEDS:
        name = f"{trial}-{arm}-s{s}.txt"
        path = freeze_dir(trial, data) / name
        if not path.exists():
            raise SystemExit(f"{path} 가 없다 — 다시 학습하지 않는다(등록 위반)")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        if expected.get(name) != digest:
            raise SystemExit(f"{name} 지문 {digest} ≠ 문서 {expected.get(name)} — 판정 거부")
        out[int(s)] = lgb.Booster(model_file=str(path))
    return out


def _vault_ports(preds: dict[int, pd.DataFrame], ctrl: dict[int, pd.DataFrame], ret: pd.DataFrame, trad: dict[date, set[str]],
                 sessions: list[date], arm: str, ctrl_arm: str, *, smooth: bool = True,
                 cost: float = el.ONE_WAY_COST) -> tuple[dict[str, float], dict[str, float]]:
    """금고 창 장부 — 시드별(처리 × arm · 대조 × ctrl_arm) 연수익·MDD·회전의 시드 평균. 데운 세션은 EMA 만 데운다."""
    first = sessions[0]
    ctx = el.Context(close=ret, ret=ret, bench=pd.Series(0.0, index=ret.index), k200_close=pd.Series(1.0, index=ret.index),
                     trad=trad, uni=pd.Series(0.0, index=ret.index), scale=pd.Series(1.0, index=ret.index), cost=cost)
    rows: dict[str, list[dict[str, float]]] = {"t": [], "c": []}
    for key, src, a in (("t", preds, arm), ("c", ctrl, ctrl_arm)):
        for s in sorted(src):
            wide = src[s].pivot_table(index="session", columns="entity_id", values="pred").sort_index()
            if smooth:
                wide = wide.ewm(span=el.SPAN).mean()
            wide = wide[wide.index >= first]
            net, turn = arm_book(wide, ctx, a, smooth=False, phases=(0,))
            net = net.reindex([d for d in sessions if d in net.index]).fillna(0.0)
            rows[key].append({"ann": float(net.mean() * el.ANN), "mdd": el.mdd(net),
                              "turn": float(turn.reindex(net.index).fillna(0.0).sum() * el.ANN / max(1, len(net)))})
    mean = lambda rs: {k: float(np.mean([r[k] for r in rs])) for k in ("ann", "mdd", "turn")}  # noqa: E731
    return mean(rows["t"]), mean(rows["c"])


def vault_run(trial: str, store: Any) -> tuple[list[str], str]:
    """second 창 확인 — 처리(우승 변형) 대 **현행 구성** 대조, 원리 짝 ΔIC. 판정은 `judge_confirm`.

    BLT: 얼린 BE2·C0(`vault_judge.be2_predictions`, 실전 경로 fa_features) · T2P: 얼린 T2·T0(이 모듈 `freeze`) × 같은 fa_features 6점수 ·
    TK: 얼린 base·plus × fa_features 6점수 + 여섯(같은 함수, 창 끝 as_of 가격) · UIR: 얼린 base·plus × 미장 금고 패널(AR 과 같은 경로) + 잔차.
    """
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import LiveClock
    from tools import vault_judge as vj
    from tools.power_principle import session_stats

    data = Path(store.root)
    main = _main_json(trial, data)
    margins, arm = main["margins"], main["arm"]
    if trial == "UIR":
        return _vault_uir(store, main)
    sessions = list(trading_days(Market.KR, vj.VAULT_START, vj.VAULT_END))
    warm = list(trading_days(Market.KR, vj.VAULT_START - timedelta(days=60), vj.VAULT_START - timedelta(days=1)))[-vj.P1B_WARM:]
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    model, c0 = vj.frozen_be2(vj.frozen_hashes("BE2"))
    if trial == "BLT":
        preds = vj.be2_predictions(store, [*warm, *sessions], model, c0, as_of_of=policy.for_session)
        treat, base, cur = preds["BE2"], preds["C0"], preds["C0"]
    else:
        cur = vj.p1b_predictions(store, [*warm, *sessions], c0, as_of_of=policy.for_session)
        arms = ("T2", "T0") if trial == "T2P" else ("plus", "base")
        boosters = {a: _load_frozen(trial, a, data) for a in arms}
        frames = _kr_frames(store, [*warm, *sessions], policy.for_session, with_ta=trial == "TK")
        cols = list(el.SCORE_FEATS) + (list(TK_COLS) if trial == "TK" else [])
        out = {a: {s: pd.concat([f[["entity_id", "session"]].assign(pred=b.predict(f[cols if a in ("plus",) else list(el.SCORE_FEATS)].to_numpy(np.float32)))
                                 for f in frames], ignore_index=True) for s, b in boosters[a].items()} for a in arms}
        treat, base = out[arms[0]], out[arms[1]]
    panel, _ = vj.kr_loop_panel()
    y = panel[["entity_id", "session", "y5"]].rename(columns={"y5": "target"})
    del panel
    ret, bench, trad = vj.market_data(store, sessions, cache=vj.VAULT)
    dic = pd.DataFrame({s: session_stats(treat[s][treat[s]["session"].isin(sessions)], y, trad)["ic"]
                        - session_stats(base[s][base[s]["session"].isin(sessions)], y, trad)["ic"] for s in sorted(treat)}).mean(axis=1)
    t_port, c_port = _vault_ports(treat, cur, ret, trad, sessions, arm, "B0")
    lines, verdict = judge_confirm(trial, dic, t_port, c_port, margins)
    lines.append(f"기록(기준 아님) 처리 {arm} 연 {t_port['ann']:+.1%} · 현행(C0×B0) {c_port['ann']:+.1%} · 주 시험 {main['verdict']}")
    return lines, verdict


def _kr_frames(store: Any, days: list[date], as_of_of: Any, *, with_ta: bool) -> list[pd.DataFrame]:
    """세션마다 fa_features 의 6점수(이미 세션 안 rank-gauss) — 얼린 C0 와 같은 입력(`be2.session_batch`). TK 는 여섯을 붙인다
    (창 끝 as_of 가격으로 `ta_composite` 와 같은 함수, 결정일 종가까지만 쓰는 지표), 여섯은 세션 안 rank-gauss·결측 0."""
    from quant_rl_trading.analysts import be2 as be2_module
    from tools.trial_pooled_rank import rank_gauss

    ta = None
    if with_ta:
        from tools import vault_judge as vj
        ta = _ta_window(store, datetime.combine(vj.VAULT_END + timedelta(days=1), time(0), tzinfo=UTC))
    out, failed = [], []
    for day in days:
        batch = be2_module.session_batch(store, "KR", as_of_of(day))
        if isinstance(batch, str) or batch.session != day:
            failed.append(f"{day}: {batch if isinstance(batch, str) else batch.session}")
            continue
        f = batch.today[["entity_id", *el.SCORE_FEATS]].copy()
        f["session"] = day
        if ta is not None:
            f = f.merge(ta[ta["session"] == day], on=["entity_id", "session"], how="left").assign(market="KR")
            f = rank_gauss(f, list(TK_COLS)).drop(columns="market")
        out.append(f)
    if failed:
        raise SystemExit(f"금고 창 {len(failed)}세션을 채점하지 못했다 — 창을 조용히 줄이지 않는다:\n  " + "\n  ".join(failed[:10]))
    return out


def _ta_window(store: Any, end: datetime) -> pd.DataFrame:
    from tools import diag_ta_features as T
    from tools import diag_ta_round2 as R2

    d = T.load(store, end=end, start=date(2024, 6, 1))     # 이력 252세션 + 60일 창이 창 첫 세션에 차도록
    c, v = d["close"], d["value"]
    label = (c.shift(-6) / c.shift(-1) - 1.0)
    label = label.where(label.abs() <= 0.5)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    f1 = T.features(d)
    sig = R2.signals(store, d, uni, label.where(uni))
    cols = {"shadow_asym_20": f1["upper_shadow_20"] - f1["lower_shadow_20"], "pv_corr_20": f1["pv_corr_20"],
            "skew_60": f1["skew_60"], "fip": sig["fip"], "mom_12_1": sig["mom_12_1"], "vol_cv_60": sig["vol_cv_60"]}
    panel = pd.concat([cols[k].where(uni).stack().rename(k) for k in TK_COLS], axis=1).reset_index()
    panel.columns = ["session", "entity_id", *TK_COLS]
    panel["session"] = pd.to_datetime(panel["session"]).dt.date
    return panel


def _vault_uir(store: Any, main: dict[str, Any]) -> tuple[list[str], str]:
    """미장 확인 — 금고 미장 패널(`vault_judge.pooled_panels(insider=True)`, AR 판정과 같은 G7 경로) + 잔차(같은 `style_residual`).
    스타일(ADV20·원 종가·변동성)은 창고 원 가격을 창 끝 as_of 로 한 번 읽어 결정일 종가까지로 만든다."""
    from tools import vault_judge as vj

    data = Path(store.root)
    kr, us, _ = vj.pooled_panels(store, insider=True)
    del kr
    us = us.sort_values(["session", "entity_id"]).reset_index(drop=True)
    styles = _us_styles(store, us)
    groups = np.array([d.toordinal() for d in us["session"]])
    for c, name in zip(G7, UIR_COLS, strict=True):
        us[name] = style_residual(us[c].to_numpy(np.float64), styles, groups)
    us = us.assign(market="US")
    from tools.trial_pooled_rank import rank_gauss
    us = rank_gauss(us, list(UIR_COLS))
    base_cols = [*vj.FEATS, "is_us"]
    boosters = {a: _load_frozen("UIR", a, data) for a in ("plus", "base")}
    preds = {a: {s: us[["entity_id", "session"]].assign(pred=b.predict(us[base_cols + (list(UIR_COLS) if a == "plus" else [])].to_numpy(np.float32)))
                 for s, b in boosters[a].items()} for a in boosters}
    _, _, close, bench_close = vj.us_panel(store, start=vj.VAULT_START, end=vj.VAULT_END, work_dirs=(vj.US_WORK,))
    ret = vj.us_returns(close)
    trad = {d: set(g["entity_id"]) for d, g in us.groupby("session")}
    sessions = sorted(trad)
    y = us[["entity_id", "session", "target"]]
    from tools.power_principle import session_stats
    dic = pd.DataFrame({s: session_stats(preds["plus"][s], y, trad)["ic"] - session_stats(preds["base"][s], y, trad)["ic"]
                        for s in sorted(preds["plus"])}).mean(axis=1)
    fee = float(store.config("accounting.fee_us", as_of=datetime.combine(vj.VAULT_END, time(16), tzinfo=UTC)))
    t_port, c_port = _vault_ports(preds["plus"], preds["base"], ret, trad, sessions, "U0", "U0", smooth=False, cost=fee)
    lines, verdict = judge_confirm("UIR", dic, t_port, c_port, main["margins"])
    lines.append(f"기록(기준 아님) 미장 처리 연 {t_port['ann']:+.1%} · 대조 {c_port['ann']:+.1%} · 주 시험 {main['verdict']}")
    return lines, verdict


def _us_styles(store: Any, us: pd.DataFrame) -> np.ndarray:
    from quant_rl_trading.store.prices import read_prices
    from tools import vault_judge as vj

    end = datetime.combine(vj.VAULT_END + timedelta(days=1), time(0), tzinfo=UTC)
    names = sorted(set(us["entity_id"]))
    back = (end.date() - (vj.VAULT_START - timedelta(days=150))).days

    def wide(adjusted: bool, cols: list[str]) -> dict[str, pd.DataFrame]:
        p = read_prices(store, as_of=end, until=end, entity=names, lookback=back, columns=cols, adjusted=adjusted, market="US")
        p["day"] = pd.to_datetime(p["valid_from"]).dt.date
        return {c: p.pivot_table(index="day", columns="entity_id", values=c, aggfunc="last").sort_index() for c in cols}

    raw = wide(False, ["close", "volume"])
    px = raw["close"]
    adv = (raw["close"] * raw["volume"]).rolling(20, min_periods=10).mean()      # 원 종가 × 원 거래량(10/7 교훈)
    vol = wide(True, ["close"])["close"].pct_change(fill_method=None).rolling(60, min_periods=30).std()

    def look(w: pd.DataFrame) -> np.ndarray:
        ri = pd.Index(w.index).get_indexer(pd.Index(us["session"]))
        ci = w.columns.get_indexer(pd.Index(us["entity_id"]))
        vals = np.full(len(us), np.nan)
        ok = (ri >= 0) & (ci >= 0)
        vals[ok] = w.to_numpy(np.float64)[ri[ok], ci[ok]]
        return vals

    la, lp = look(adv), look(px)
    return np.column_stack([np.log(np.where(la > 0, la, np.nan)), np.log(np.where(lp > 0, lp, np.nan)), look(vol)])


# --------------------------------------------------------------------------- CLI


def plan() -> int:
    for t in TRIALS:
        h = PROTOCOL_HASH[t]
        doc = REPO_ROOT / DOCS[t]
        state = "초안(고정 대기)" if h is None else ("고정 ○" if doc.exists() and doc_hash(t) == h else "고정값과 문서 불일치 ×")
        steps = {"BLT": "run --save", "T2P": "freeze → 해시 기입 → 고정 → run --save",
                 "TK": "bake → walk → freeze → 해시 기입 → 고정 → run --save",
                 "UIR": "bake → walk → freeze → 해시 기입 → 고정 → run --save"}[t]
        log(f"{t}: {DOCS[t]} · {state} · 순서 {steps} · {SPECS[t].note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("plan", "power", "bake", "walk", "freeze", "run"))
    ap.add_argument("--trial", choices=TRIALS)
    ap.add_argument("--root", default="data")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--cwd", default=None, help="상대 경로(data/)의 기준 디렉터리 — worktree 에서 실전 트리의 연구 캐시를 읽을 때(읽기만)")
    args = ap.parse_args(argv)
    if args.cwd:
        import os

        os.chdir(args.cwd)
    if args.cmd == "plan":
        return plan()
    if args.trial is None:
        ap.error("--trial 이 필요하다")
    if args.cmd == "power":
        return power(args.trial)
    if args.cmd == "bake":
        if args.trial not in ("TK", "UIR"):
            ap.error("bake 는 재료 시행(TK·UIR)만")
        (tk_bake(args.root) if args.trial == "TK" else uir_bake())
        return 0
    if args.cmd == "walk":
        if args.trial not in ("TK", "UIR"):
            ap.error("walk 는 재료 시행(TK·UIR)만")
        (tk_walk() if args.trial == "TK" else uir_walk())
        return 0
    if args.cmd == "freeze":
        return freeze(args.trial)
    return run_main(args.trial, save=args.save, root=Path(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
