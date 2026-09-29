"""시행 D1 — 결정 중심 학습(DFL): **얼린 GBM 백본 + 잔차 머리**를 소프트 포트 층의 비용 후 수익으로 배운다.

    .venv/bin/python tools/trial_final_dfl.py smoke                                   # 합성 자료 배선 확인(아무 때나, 수치 안 찍음)
    .venv/bin/python tools/trial_final_dfl.py shuffle --i-registered                  # 실자료 라벨 섞기 → 채택 여백을 얼린다
    .venv/bin/python tools/trial_final_dfl.py canary --i-registered                   # 심은 신호(합성) — 얼린 여백을 건다
    .venv/bin/python tools/trial_final_dfl.py shuffle --synthetic                     # 합성 누설 점검(여백 안 얼림)
    .venv/bin/python tools/trial_final_dfl.py precheck                                # 지수 구성 커버리지만(수익 없음)
    .venv/bin/python tools/trial_final_dfl.py judge --i-registered [--variant D1a|D1b|both]
                                                    [--backbone C1|C2 --features FA|FA+] [--save]

    # v2 재설계(docs/protocols/decision-focused-v2-2026-10.md) — 같은 명령에 --track v2(기본 v1 = 해시 고정 판, 그대로 재현)
    .venv/bin/python tools/trial_final_dfl.py shuffle --track v2 --i-registered                 # 실자료 여백(재학습별 시드 평균 t)을 얼린다
    .venv/bin/python tools/trial_final_dfl.py canary --track v2 --i-registered                  # 합성 카나리 — 그 여백을 건다
    .venv/bin/python tools/trial_final_dfl.py shuffle --synthetic --track v2 --i-registered     # 합성 섞기 — 그 여백을 건다
    .venv/bin/python tools/trial_final_dfl.py judge --track v2 --i-registered --variant both --save

**v2 가 바꾸는 것은 머리 채택 규칙(`Accept`) 하나다** — 조기 종료·채택 지표를 연수익 차이에서 **일수익 차이의 t 값**으로,
채택 결정을 머리마다에서 **재학습마다 시드 5개 평균 한 번**으로(`walk_pooled`), 여백도 섞은 라벨 이득의 재학습별 시드 평균에서.
모델·손실·고정값·검증 창(20%)·판정 기준은 v1 과 같다(`Accept` 의 창·분위 칸은 합성 비교용으로 남긴다 — 등록 v2 §후보표).
v1(rc 7): 실자료 섞기 여백(연수익 최댓값 46.1%)이 척도가 다른 합성 카나리 이득(중앙값 16.5%)을 통째로 막았다.

설계는 `docs/design/ai-full-stack.md` §2(과적합 규율 20조)·§3.1~3.3, 사전등록은 `docs/protocols/decision-focused-2026-10.md`.
공통 틀은 `tools/final_round_kit` 이고 **이 파일은 kit 을 읽기만 한다**(패널·블록·내부 분할·대조군·규칙 포트·지표·판정).

**점수 = s_GBM + h_θ(FA).** s_GBM 은 대조군 C1(GBM·FA)의 **워크포워드 예측을 그대로 얼린 것**(kit 캐시, 시드 짝을 맞춘다),
h 는 작은 잔차 머리(FA → 16 → 1, 마지막 층 0 초기화 — 처음엔 정확히 C1 이다)다. 그래서 **D1 − C1 이 정확히 DFL 이 더한 몫**이고
(§3.1 b: MLP 를 처음부터 배우면 "DFL 이 나빠서" 와 "MLP 가 GBM 보다 약해서" 를 못 가른다), 파라미터가 1.2천이라 외우기도 어렵다.
백본 예측은 **판정 블록 행에만** 있다(워크포워드 예측이다) — 그래서 D1 의 학습 자료는 앞 블록들의 C1 표본 밖 예측이고,
첫 판정 블록은 `D1_FIRST_BLOCK = 10`(앞 10블록 = 200세션이 쌓인 뒤)이다. 대조군도 **같은 구간으로 잘라** 잰다.

**학습 = 소프트, 판정 = 하드(§3.1 c).** 판정은 C1 과 **같은 하드 규칙**(EMA5 · 상위 24 · 완충 72 · 10세션 재조정, 미장은 AT M1 합성 —
`kit.evaluate_all`)에 D1 점수만 넣는다. 소프트 층은 학습에만 쓰고, 그 입력도 하드 규칙과 **같은 선정 점수**(EMA5 · 미장 M1)의
미분 가능한 판이다. 조기 종료 지표도 하드 규칙 포트의 내부 검증 수익이다.

**경로(§3.1 d).** 결정을 같은 위상 10세션 간격으로 **순서대로** 굴린다. 비용 항의 직전 보유는 **하드 규칙이 실제로 든 보유**
(완충 포함 · 드리프트 · 기울기 멈춤)다 — 판정 장부와 같은 경로라서 회전 비용이 거짓이 아니다.

    손실 = α·L_pred/ℓ_pred + (1−α)·L_port/ℓ_port        (ℓ = 첫 학습창에서 h = 0 으로 잰 척도, 시드마다 얼린다 — §3.1 e)
    L_pred = 세션 안 rank-gauss y5 의 MSE(시행 L 과 같은 타깃)
    L_port = −평균(R) + η·분산(R),  R = w_soft·G − 비용 × Σ|w_soft − w_hard,prev,drift|
    G_i    = 결정일부터 10세션 보유 수익(t+1→t+2 규약 복리) — 드리프트해도 w 에 정확히 선형이다

**변형 둘(§3.1 f)** — D1a 소프트 상위-k(k 24, 온도는 첫 학습창에서 "유효 종목 수 ≈ 27" 로 정해 얼린다) ·
D1b 지수 기울이기(w ∝ 지수 원 비중 × exp(λ·rank-gauss(선정 점수)), λ = 0.5 = AX 채택 X3, 상한 국장 30%·미장 10% 반복 투영;
학습에선 rank-gauss 를 부드러운 순위로 바꾼다). D1b 의 판정 장부는 같은 기울이기 규칙이고, 대조는 C0·C1 점수를 **같은 층**에 넣은 T0·T1.

**누설(§3.1 a).** L_port 라벨은 10세션 보유(가격 t+11 까지)라 kit 의 간격 10(퍼지 5 + 엠바고 5)으로는 학습창 마지막 결정의 라벨이
판정 첫 세션에 닿는다. D1 의 학습 끝점은 **16세션 앞**(`D1_GAP` = 보유 10 + 체결 지연 1 + 퍼지 5)이고, 결정마다 "라벨의 마지막 가격일 <
판정 첫 세션" 을 따로 확인한다(테스트가 단언한다).

**자기 점검 둘(판정 전, 합성 자료)** — `canary`: IC 는 ~0 인데 상위 k 수익에만 있는 신호를 심고 D1 이 C1 보다 그것을 찾는지 ·
`shuffle --synthetic`: 학습 라벨을 섞으면 D1 − C1 이 ①의 문턱(2%p) 안인지(누설 점검). 러너는 실자료 `shuffle`(여백 굽기) → `canary` → 판정.
**채택 여백**: 실자료 `shuffle`(학습 라벨을 섞고 판정과 같은 재학습 — 학습 창 안 내부 검증만 본다)이 변형마다 "조기 종료가 주운
내부 검증 이득의 최댓값"(시드·재학습 전체) 하나를 얼리고(`MARGIN_PATH`, 등록 해시와 함께), 판정의 머리는 이득이 그 여백을
**넘을 때만** 채택된다(못 넘으면 h = 0 = 백본). canary(합성)는 그 여백을 건 채로 돈다.

**백본·피처 인자(§3.3 FA+).** `--backbone C1 --features FA`(기본) · `--backbone C2 --features FA+`(C2 = GBM·FA+ 가 통과했을 때만 —
등록의 조건부 절). 짝이 다르면 거부한다. C2 캐시나 FA+ 묶음(kit 의 G10~G13 정의)이 없으면 rc 3.

**계산량** — 등록 문서 §계산량(합성 실측). 머리가 작아 비싼 것은 FA 패널 조립과 내부 검증의 규칙 포트다. `torch.set_num_threads(2)`.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from scipy.stats import norm, rankdata  # type: ignore[import-untyped]  # noqa: E402
from torch import nn  # noqa: E402

from tools import final_round_kit as kit  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools.trial_overlay import ANN  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402
from tools.trial_us_missing_fundamental import CONF_FUND, CONF_RANK, W_FUND, W_RANK  # noqa: E402

PROTOCOL = Path("docs/protocols/decision-focused-2026-10.md")
SEED_CACHE = kit.CACHE / "D1"
VARIANTS = ("D1a", "D1b")
#: 백본 → 그 백본이 배운 피처 세트. 짝이 다르면 거부한다(C2 를 FA 위에 얹으면 머리가 못 보는 재료로 만든 점수다).
BACKBONES = {"C1": "FA", "C2": "FA+"}
#: FA+ = FA + 새 정보원 다섯(§3.3). kit 의 `blocks_of` 가 이 묶음을 정의해야 쓸 수 있다.
FA_PLUS_EXTRA = ("G8", "G10", "G11", "G12", "G13")

# ── 등록 때 고정하는 값 (판정 창에서 고르지 않는다 — 규율 8) ─────────────────────────────

HOLD = kit.REBALANCE_EVERY           # 보유·재조정 10세션(AO·AU C10)
K = kit.N                            # 상위 24 — 공통 틀
EXIT_MULT, SPAN = rkit.EXIT_MULT, rkit.SPAN   # 완충 3N · EMA5 — 판정의 하드 규칙과 같은 값(kit 이 쓰는 그 상수)
#: 학습 끝점 간격 = 보유 10 + 체결 지연 1(t+1→t+2 규약: 마지막 수익 행의 가격은 그 날 +2) + 퍼지 5. kit 의 GAP 10 으로는 누설이다(§3.1 a).
D1_GAP = HOLD + 1 + kit.PURGE
#: 첫 판정 블록. 백본(C1)의 표본 밖 예측이 200세션(10블록) 쌓여야 학습·내부 검증이 선다. 자료가 정한 값이다(판정 성적과 무관).
D1_FIRST_BLOCK = 10
#: 선정 점수 EMA 를 이 길이에서 자른다(EMA5 의 21번째 이후 가중치 합 (2/3)^20 ≈ 0.03%).
EMA_WINDOW = 20
#: 소프트 상위-k 의 목표 유효 종목 수(1/Σw²) — §3.1 c 의 "24~30" 가운데. 온도는 첫 학습창에서 이 값에 맞춰 얼린다.
TARGET_EFF, EFF_BAND = 27.0, (24.0, 30.0)
#: 기울이기 세기 — 시행 AX 채택 X3 과 같다(리드 승인 2026-09-28 — 새로 고르지 않는다).
LAMBDA = 0.5
#: 부드러운 순위의 폭(표준화 점수 단위). 이웃 종목 간격(n=200 이면 ~0.01σ)보다 넓고 순위를 뭉개지 않는 값 — 하드 순위와의 상관은 테스트가 본다.
RANK_TAU = 0.05
CAPS = {"KR": 0.30, "US": 0.10}
US_INDEX_WIDTH = 500
#: 예측 손실 비중 — 척도를 맞춘 뒤 반반(§3.2: 순수 포트 목적의 분산을 줄이는 최소 혼합).
ALPHA = 0.5
#: 분산 벌점 — 평균-분산 효용 E − (γ/2)·Var 의 로그효용(성장 최적) 2차 근사 γ = 1 → η = 0.5. 조정한 값이 아니다.
ETA = 0.5
HEAD_HIDDEN, DROPOUT = 16, 0.2
LR, WEIGHT_DECAY, CLIP = 1e-3, 1e-4, 1.0
GROUP_DROP_P, INPUT_NOISE = 0.15, 0.10         # BE 와 같은 억제
MAX_EPOCHS, PATIENCE = 10, 3
#: 에포크 = 고정 예산: 사슬 60개. 사슬 = 같은 위상의 결정 6개(첫 1개는 보유를 세우는 워밍업, 손실은 뒤 5개).
STEPS_PER_EPOCH, CHAIN_LEN, CHAIN_WARM = 60, 6, 1
RETRAIN_EVERY = 5
MIN_SET = 50
SEEDS = kit.SEEDS

JUDGE_KEYS: tuple[str, ...] = ("ann", "sharpe", "box_ann", "rally_ann", "mdd", "turn", "ic")
IR_GATE = 0.30
#: 자기 점검 문턱. 섞은 라벨로 ①(2%p)을 넘으면 누설이다. 카나리는 ⑥(1%p) 만큼은 찾아야 한다.
SHUFFLE_TOL, CANARY_MIN = kit.GATE_MEAN, kit.GATE_MODEL
INDEX_EXIT = 6
INDEX_MIN_COVERAGE = 0.90
#: 자기 점검 불통과·채택 여백 없음. 러너가 판정 전에 멈추는 자리다.
CHECK_EXIT = 7
#: 채택 여백 — 실자료 `shuffle` 이 변형마다 하나(시드·재학습 전체 이득의 최댓값)를 얼려 적는다. `canary`·`judge` 는 읽기만(없으면 rc 7).
MARGIN_PATH = SEED_CACHE / "shuffle-margin.json"

torch.set_num_threads(2)


@dataclass(frozen=True)
class Accept:
    """머리 채택 규칙 — 조기 종료 지표 · 내부 검증 창 · 여백의 분위. v1 은 기본값 그대로(등록 해시 24a52320cdcfc2c2).

    - ``metric``: ``"ann"`` = 하드 규칙 포트 내부 검증 **연수익** 차이(v1) · ``"t"`` = 같은 포트의 **일수익 차이(머리 − h 0)의 t 값**(v2).
      연수익 차이는 창 길이·변동성에 척도가 묶여 합성과 실자료가 다른 자로 잰다(실자료 섞기 최댓값 46% 대 합성 10%) — t 는 척도가 없다.
    - ``val_share``: 학습창에서 내부 검증으로 떼는 마지막 몫(v1 0.20 = kit 기본).
    - ``margin_q``: 여백 = 실자료 라벨 섞기 이득(시드·재학습 합동)의 이 분위. 1.0 = 최댓값(v1).
    - ``pool``: False = 머리마다 따로 채택(v1) · True = **재학습마다 시드 전부를 합친 한 번의 결정**(v2) — 같은 학습창·같은 내부 검증 창의
      시드 5개 이득의 **평균**이 여백을 넘으면 그 재학습의 머리를 시드 전부 쓰고, 못 넘으면 시드 전부 h = 0. 여백도 섞은 라벨 이득의
      재학습별 시드 평균(실자료 7개)에서 잰다. 한 창의 잡음 한 번이 머리 하나를 켜는 길을 막고, 시드 사이에 일관된 이득만 남긴다.
    """

    metric: str = "ann"
    val_share: float = kit.INNER_VAL_SHARE
    margin_q: float = 1.0
    pool: bool = False


ACCEPT_V1 = Accept()
#: v2(docs/protocols/decision-focused-v2-2026-10.md) — 합성 비교에서 고른 규칙(t 값 · 재학습별 시드 합동 결정). 후보표는 등록 문서.
ACCEPT_V2 = Accept(metric="t", val_share=0.20, margin_q=1.0, pool=True)


@dataclass(frozen=True)
class Track:
    """등록 한 벌 — 문서 · 여백 파일 · 채택 규칙 · 캐시 꼬리표 · 기록 이름. v1 경로는 그대로 남겨 재현할 수 있게 한다."""

    name: str
    protocol: Path
    margin_path: Path
    accept: Accept
    cache_tag: str             # 시드 캐시·여백 조각 이름에 붙는다(v1 은 빈 문자열 — 기존 파일 이름 그대로)
    entity: str                # research_trials 기록 이름


PROTOCOL_V2 = Path("docs/protocols/decision-focused-v2-2026-10.md")
TRACKS = {
    "v1": Track("v1", PROTOCOL, MARGIN_PATH, ACCEPT_V1, "", "decision-focused-2026-10"),
    "v2": Track("v2", PROTOCOL_V2, SEED_CACHE / "shuffle-margin-v2.json", ACCEPT_V2, "v2-",
                "decision-focused-v2-2026-10"),
}


def margin_from(gains: Sequence[float], q: float) -> float:
    """얼린 여백 — 섞은 라벨 이득(0 포함)의 분위 ``q``. 1.0 이면 최댓값(v1). 분위는 **관측값 하나**(method="higher" —
    두 값 사이를 보간해 여백을 낮추지 않는다)."""
    vals = [0.0, *[float(g) for g in gains]]
    if q >= 1.0:
        return max(vals)
    return float(np.quantile(np.asarray(gains if gains else [0.0], dtype=float), q, method="higher"))


@dataclass(frozen=True)
class Hyper:
    """고정값 묶음. 테스트·스모크만 작게 바꾼다 — 판정(`cmd_judge`)은 기본값 그대로다."""

    max_epochs: int = MAX_EPOCHS
    patience: int = PATIENCE
    steps_per_epoch: int = STEPS_PER_EPOCH
    chain_len: int = CHAIN_LEN
    alpha: float = ALPHA
    eta: float = ETA
    lam: float = LAMBDA
    k: int = K
    group_drop_p: float = GROUP_DROP_P
    noise: float = INPUT_NOISE
    retrain_every: int = RETRAIN_EVERY
    first_block: int = D1_FIRST_BLOCK


HYPER = Hyper()


# ── 포트 층 (미분 가능) ────────────────────────────────────────────────────────────────


def standardize(s: torch.Tensor) -> torch.Tensor:
    """같은 날 횡단면 통계만 쓴다."""
    return (s - s.mean()) / (s.std(unbiased=False) + 1e-6)


def soft_topk(z: torch.Tensor, k: int, temp: float, *, iters: int = 60) -> torch.Tensor:
    """소프트 상위-k 비중(합 1, 종목당 ≤ ~1/k). p_i = σ((z_i − θ)/T), Σp = k.

    θ 는 이분법(그래디언트 없음)으로 찾고 **뉴턴 한 걸음**으로 암묵 미분 dθ/dz_i = σ'_i/Σσ' 을 싣는다(gradcheck 로 대조).
    """
    n = int(z.shape[0])
    if n <= k:
        return torch.full_like(z, 1.0 / max(n, 1))
    with torch.no_grad():
        lo, hi = z.min() - 30.0 * temp, z.max() + 30.0 * temp
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if float(torch.sigmoid((z - mid) / temp).sum()) > k:
                lo = mid
            else:
                hi = mid
        theta0 = 0.5 * (lo + hi)
    p0 = torch.sigmoid((z - theta0) / temp)
    slope = p0 * (1.0 - p0) / temp
    theta = theta0 + (p0.sum() - k) / slope.sum().clamp_min(1e-12)
    p = torch.sigmoid((z - theta) / temp)
    return p / p.sum()


def effective_n(w: torch.Tensor | np.ndarray) -> float:
    arr = w.detach().numpy() if isinstance(w, torch.Tensor) else np.asarray(w)
    return float(1.0 / np.square(arr).sum())


def cap_project(w: torch.Tensor, cap: float, *, iters: int = 50) -> torch.Tensor:
    """종목 상한 투영 — 넘친 몫을 안 넘친 종목에 비례 분배하고 되풀이(`capped_cap_weights` 와 같은 규칙). 거의 모든 곳에서 미분 가능."""
    n = int(w.shape[0])
    cap = max(float(cap), 1.0 / max(n, 1))
    w = w / w.sum()
    for _ in range(iters):
        over = w > cap + 1e-12
        if not bool(over.any()):
            break
        excess = (w - cap).clamp_min(0.0).sum()
        free = torch.where(over, torch.zeros_like(w), w)
        mass = free.sum()
        if float(mass.detach()) <= 0.0:
            break
        w = torch.where(over, torch.full_like(w, cap), w + excess * free / mass)
    return w / w.sum()


def rank_gauss_exact(v: np.ndarray) -> np.ndarray:
    """AZ·AX 의 z — norm.ppf((순위 − 0.5)/n), 동률은 평균 순위(pandas rank 기본)."""
    n = len(v)
    return np.asarray(norm.ppf((rankdata(v, method="average") - 0.5) / n)) if n > 1 else np.zeros(n)


def rank_gauss_soft(v: torch.Tensor, tau: float = RANK_TAU) -> torch.Tensor:
    """부드러운 순위 → 정규 분위. 순위_i ≈ 0.5 + Σ_j σ((v_i − v_j)/τ)(자기 항 σ(0) = 0.5 포함), v 는 표준화한 값."""
    n = int(v.shape[0])
    if n < 2:
        return torch.zeros_like(v)
    z = standardize(v)
    r = 0.5 + torch.sigmoid((z[:, None] - z[None, :]) / tau).sum(dim=1)
    p = ((r - 0.5) / n).clamp(0.5 / n, 1.0 - 0.5 / n)
    out: torch.Tensor = np.sqrt(2.0) * torch.erfinv(2.0 * p - 1.0)
    return out


def tilt_weights(z: torch.Tensor, b: torch.Tensor, lam: float, cap: float) -> torch.Tensor:
    """D1b — 투영(b × exp(λ·z)). ``b`` 는 지수 구성 종목의 **원** 시총 비중(상한 전), z 는 점수 없는 종목에서 0(AX 규칙)."""
    w = b * torch.exp(lam * z)
    return cap_project(w / w.sum(), cap)


# ── 자료 ──────────────────────────────────────────────────────────────────────────────


@dataclass
class DaySet:
    """(시장, 세션) 하나 — 점수를 내는 종목(패널 행), D1b 면 지수 구성 종목."""

    day: date
    market: str
    pos: int                      # 이 시장 선정 축(세트가 있는 날)의 위치
    rows: np.ndarray              # 패널 행 번호
    code: np.ndarray              # 시장 내 종목 코드
    y: np.ndarray                 # y5 rank-gauss
    idx_code: np.ndarray | None = None
    idx_b: np.ndarray | None = None


@dataclass
class MarketData:
    """한 시장. 축은 **세트가 있는 날**(= 판정 규칙 포트의 피벗 행 = 예측이 있는 날 ∩ 수익표) — kit 포트가 도는 축과 같다."""

    market: str
    days: list[date]
    entities: list[str]
    R: np.ndarray                 # (날, 종목) 일수익, 결측 0
    G: np.ndarray                 # (날, 종목) 그 날부터 HOLD 날 보유 수익, 창 밖 NaN
    ret_days: list[date]          # 수익표 달력(라벨의 마지막 가격일을 세는 데 쓴다)
    cost: float
    bench: pd.Series
    trad: dict[date, set[str]] | None
    sets: dict[date, DaySet] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._pos = {d: i for i, d in enumerate(self.days)}
        self._rpos = {d: i for i, d in enumerate(self.ret_days)}
        self.code_of = {e: i for i, e in enumerate(self.entities)}
        self._trad_mask: dict[date, np.ndarray | None] = {}

    def pos_of(self, day: date) -> int:
        return self._pos[day]

    def label_end(self, day: date) -> date | None:
        """그 결정의 L_port 라벨이 읽는 **마지막 가격의 날짜**. 보유 마지막 수익 행 d 는 가격 d+2 를 본다(t+1→t+2 규약)."""
        p = self._pos[day] + HOLD - 1
        if p >= len(self.days):
            return None
        r = self._rpos[self.days[p]] + 2
        return self.ret_days[r] if r < len(self.ret_days) else None

    def trad_mask(self, day: date) -> np.ndarray | None:
        """국장 거래가능 명단(kit 포트가 행을 거르는 규칙). 미장은 None(거르지 않는다)."""
        if day not in self._trad_mask:
            ok = self.trad.get(day) if self.trad else None
            if ok:
                mask = np.zeros(len(self.entities), bool)
                mask[[self.code_of[e] for e in ok if e in self.code_of]] = True
                self._trad_mask[day] = mask
            else:
                self._trad_mask[day] = None
        return self._trad_mask[day]


def hold_returns(R: np.ndarray, hold: int = HOLD) -> np.ndarray:
    """G[p] = Π_{k<hold}(1+R[p+k]) − 1. 비중이 드리프트해도 보유 수익은 w·G 로 정확히 선형이다."""
    L = np.vstack([np.zeros((1, R.shape[1])), np.cumsum(np.log1p(np.maximum(R, -0.99)), axis=0)])
    out = np.full(R.shape, np.nan, dtype=np.float64)
    n = R.shape[0]
    if n >= hold:
        out[: n - hold + 1] = np.expm1(L[hold:] - L[: n - hold + 1])
    return out


def build_market(keys: pd.DataFrame, market: str, book: kit.MarketBook,
                 index_raw: dict[date, pd.Series] | None = None) -> MarketData:
    """키 프레임(시장·세션·종목 순 정렬, 인덱스 = 패널 행 번호) + 장부 → 한 시장."""
    part = keys.index[keys["market"].to_numpy() == market]
    sub = keys.loc[part, ["entity_id", "session", "y5"]]
    names = set(sub["entity_id"])
    for w in (index_raw or {}).values():
        names |= set(w.index)
    entities = sorted(set(book.ret.columns) | names)
    ret = book.ret.sort_index()
    ret_days = list(ret.index)
    groups = {d: idx for d, idx in sub.groupby("session").indices.items() if d in set(ret_days)}
    days = sorted(d for d, idx in groups.items() if len(idx) >= MIN_SET)
    R = ret.reindex(index=days, columns=entities).fillna(0.0).to_numpy(np.float64)
    md = MarketData(market, days, entities, R, hold_returns(R), ret_days, float(book.cost), book.bench, book.trad)
    ents_all = sub["entity_id"].to_numpy()
    ys = sub["y5"].to_numpy(np.float32)
    for day in days:
        idx = groups[day]
        ents = ents_all[idx]
        st = DaySet(day, market, md.pos_of(day), part[idx].to_numpy(),
                    np.array([md.code_of[e] for e in ents], dtype=np.int64), ys[idx])
        if index_raw is not None and day in index_raw:
            w = index_raw[day]
            if book.trad and book.trad.get(day):
                w = w[w.index.isin(book.trad[day])]
            w = w[w > 0]
            if len(w) >= 2:
                st.idx_code = np.array([md.code_of[e] for e in w.index], dtype=np.int64)
                st.idx_b = (w / w.sum()).to_numpy(np.float64)
        md.sets[day] = st
    return md


def feature_matrix(panel: pd.DataFrame, feats: Sequence[str]) -> np.ndarray:
    """모델 입력 — float16(rank-gauss ±4, BE 큐브와 같은 이유). 결측은 0(순위 중앙)."""
    X = np.zeros((len(panel), len(feats)), dtype=np.float16)
    for j, c in enumerate(feats):
        if c in panel.columns:
            X[:, j] = np.nan_to_num(panel[c].to_numpy(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return X


# ── 머리 ──────────────────────────────────────────────────────────────────────────────


def make_head(seed: int, n_features: int) -> nn.Module:
    """잔차 머리 h: FA → 16 → 1. **마지막 층 0 초기화** — 학습 전 점수는 정확히 백본(C1)이다(웜스타트, §3.2)."""
    torch.manual_seed(seed)
    head = nn.Sequential(nn.Linear(n_features, HEAD_HIDDEN), nn.GELU(), nn.Dropout(DROPOUT),
                         nn.Linear(HEAD_HIDDEN, 1), nn.Flatten(0))
    last = head[3]
    assert isinstance(last, nn.Linear)
    nn.init.zeros_(last.weight)
    nn.init.zeros_(last.bias)
    return head


@dataclass
class Aug:
    """묶음 드롭아웃 + 입력 잡음(`kit.drop_groups`, 표지는 1 로 켠다 — BE 와 같다)."""

    groups: dict[str, list[str]]
    index_of: dict[str, int]
    p: float = GROUP_DROP_P
    noise: float = INPUT_NOISE

    def apply(self, x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        if self.noise > 0:
            x = x + rng.normal(0.0, self.noise, size=x.shape).astype(np.float32)
        if self.p > 0:
            names = kit.drop_groups(self.groups, rng, self.p)
            zero = [self.index_of[c] for c in names if c in self.index_of and not c.startswith("miss_")]
            flags = [self.index_of[c] for c in names if c in self.index_of and c.startswith("miss_")]
            if zero:
                x[:, zero] = 0.0
            if flags:
                x[:, flags] = 1.0
        return x


@dataclass
class Inputs:
    """학습·예측이 같이 쓰는 행 단위 배열(패널 행 순서). ``bb`` = 백본 점수(없는 행 NaN)."""

    X: np.ndarray
    bb: np.ndarray
    fund: np.ndarray              # 미장 M1 재료(없으면 0)
    has_fund: np.ndarray


def scores(head: nn.Module, inp: Inputs, st: DaySet, *, aug: Aug | None = None,
           rng: np.random.Generator | None = None) -> torch.Tensor:
    """s = 백본 + h(FA). float64 로 올려 계산한다(포트 층의 이분법·순위가 float32 에선 동률을 만든다)."""
    x = inp.X[st.rows].astype(np.float32)
    if aug is not None and rng is not None:
        x = aug.apply(x, rng)
    h = head(torch.from_numpy(x)).double()
    out: torch.Tensor = torch.as_tensor(inp.bb[st.rows], dtype=torch.float64) + h
    return out


# ── 선정 점수 (하드 규칙과 같은 식 · 학습용 미분판) ─────────────────────────────────────


_M1_A, _M1_B = W_FUND * CONF_FUND, W_RANK * CONF_RANK


def day_values(md: MarketData, st: DaySet, s: torch.Tensor, inp: Inputs, *, soft: bool) -> torch.Tensor:
    """그 날의 선정 값 — 국장은 점수 그대로, 미장은 **AT M1 합성**(`trial_us_kit.m1_scores` 와 같은 식).

    M1 의 순위 백분위는 하드 경로에선 정확한 평균 순위, 소프트 경로에선 Φ(표준화 점수)(순위의 매끈한 대용).
    """
    if md.market != "US":
        return s
    if soft:
        pct = 0.5 * (1.0 + torch.erf(standardize(s) / np.sqrt(2.0)))
    else:
        v = s.detach().numpy()
        pct = torch.as_tensor((rankdata(v, method="average") - 0.5) / len(v), dtype=s.dtype)
    rank = torch.tanh((pct - 0.5) * np.sqrt(3.0))
    fund = torch.as_tensor(np.where(inp.has_fund[st.rows], inp.fund[st.rows], 0.0), dtype=s.dtype)
    return (_M1_A * fund + _M1_B * rank) / (_M1_A + _M1_B)


@dataclass
class Window:
    """선정 점수 EMA 를 위한 날별 값(시장 내 종목 전체 길이). 하드는 그래디언트 없음, 소프트는 그래디언트 있음."""

    hard: list[np.ndarray]
    soft: list[torch.Tensor]
    mask: list[np.ndarray]


def ema_select(values: Sequence[np.ndarray | torch.Tensor], masks: Sequence[np.ndarray]) -> tuple[torch.Tensor, np.ndarray]:
    """`pandas.ewm(span=5, adjust=True)` 의 마지막 행 — 없는 날은 분자·분모에서 빠진다(ignore_na=False 와 같은 가중).

    ``values`` 는 오래된 것부터. 반환: (선정 점수, 값이 있는 종목 마스크).
    """
    decay = 1.0 - 2.0 / (SPAN + 1.0)
    n = len(values)
    den = np.zeros(len(masks[0]))
    num: torch.Tensor | None = None
    for j, (v, m) in enumerate(zip(values, masks, strict=True)):
        w = decay ** (n - 1 - j)
        vt = v if isinstance(v, torch.Tensor) else torch.as_tensor(v)
        term = w * torch.where(torch.as_tensor(m), vt, torch.zeros_like(vt))
        num = term if num is None else num + term
        den = den + w * m
    have = den > 0
    assert num is not None
    return num / torch.as_tensor(np.where(have, den, 1.0)), have


def rule_pick(held: list[int], sel: np.ndarray, eligible: np.ndarray, n: int = K) -> list[int]:
    """하드 규칙의 한 재조정 — 완충 3N(`pick_mult`, kit 포트와 같은 함수)."""
    idx = np.flatnonzero(eligible)
    ranked = idx[np.argsort(-sel[idx], kind="stable")]
    return list(pick_mult(held, pd.Index(ranked), n, EXIT_MULT))  # type: ignore[arg-type]


def drift(md: MarketData, dense_w: np.ndarray, day: date) -> np.ndarray:
    """그 날 정한 비중을 HOLD 날 들고 난 뒤의 비중 — 다음 재조정의 비용 기준(판정 장부의 드리프트와 같다)."""
    g = np.nan_to_num(md.G[md.pos_of(day)], nan=0.0)
    d = dense_w * (1.0 + g)
    total = d.sum()
    return d / total if total > 0 else dense_w


def base_weights(st: DaySet) -> tuple[np.ndarray, np.ndarray]:
    """B0 — 신호 없는 같은 구성(λ = 0): 상한만 씌운 지수 비중. 복기 규칙 9 의 대조."""
    assert st.idx_code is not None and st.idx_b is not None
    return st.idx_code, cap_project(torch.as_tensor(st.idx_b), CAPS[st.market]).numpy()


def members_z(st: DaySet, sel: torch.Tensor, have: np.ndarray, *, soft: bool) -> torch.Tensor:
    """구성 종목의 z — 선정 점수가 있는 종목끼리 rank-gauss(소프트면 부드러운 순위), 없는 종목은 0."""
    assert st.idx_code is not None
    ok = have[st.idx_code]
    z = torch.zeros(len(st.idx_code), dtype=torch.float64)
    idx = np.flatnonzero(ok)
    if len(idx) >= 2:
        v = sel[torch.as_tensor(st.idx_code[idx])]
        zz = rank_gauss_soft(v) if soft else torch.as_tensor(rank_gauss_exact(v.detach().numpy()))
        z = z.index_put((torch.as_tensor(idx),), zz)
    return z


# ── 사슬 굴리기 (학습의 한 단위) ─────────────────────────────────────────────────────────


@dataclass
class Calib:
    """첫 학습창에서 정해 **얼리는** 값(시드마다). 온도(시장별) · 손실 척도 둘."""

    temp: dict[str, float]
    l_pred: float
    l_port: float


@dataclass
class TrainLog:
    seen: set[tuple[str, date]] = field(default_factory=set)         # 손실에 쓴 결정
    val_days: set[tuple[str, date]] = field(default_factory=set)     # 조기 종료에 쓴 날
    last_label_day: date | None = None                               # 학습이 읽은 가장 늦은 가격일
    train_losses: list[float] = field(default_factory=list)
    val_scores: list[float] = field(default_factory=list)            # −(하드 규칙 내부 검증 연수익), [0] = h 0(백본 그대로)
    epochs: int = 0
    best_epoch: int = 0
    stopped_early: bool = False
    margin: float = 0.0                                              # 채택 여백(shuffle 에서 얼린 값)
    accepted: bool = False                                           # 머리를 썼나(아니면 h = 0 = 백본)
    metric: str = "ann"                                              # 이득의 단위 — v1 연수익 · v2 t 값(`Accept.metric`)
    fit: list[tuple[str, date]] = field(default_factory=list)       # 적합 결정(v2 합동 결정의 되감기용)
    best_state: dict[str, torch.Tensor] | None = None               # 최고 에포크 머리(조기 종료가 고른 개선이 있을 때만)

    @property
    def dfl_improved(self) -> bool:
        """조기 종료가 h = 0 보다 나은 에포크를 **골랐나** — 잡음으로도 켜진다(shuffle 에서 4회 중 2회). 채택은 `accepted`."""
        return self.best_epoch > 0

    @property
    def gain(self) -> float:
        """내부 검증 이득 = 하드 규칙 포트 연수익(최고 에포크) − 연수익(h = 0). 0 이상."""
        return float(self.val_scores[0] - self.val_scores[self.best_epoch]) if self.val_scores else 0.0

    def touch(self, day: date | None) -> None:
        if day is not None and (self.last_label_day is None or day > self.last_label_day):
            self.last_label_day = day


def roll_chain(variant: str, head: nn.Module, inp: Inputs, md: MarketData, chain: list[date], calib: Calib,
               hyper: Hyper, *, aug: Aug | None = None, rng: np.random.Generator | None = None,
               log: TrainLog | None = None) -> tuple[list[torch.Tensor], list[torch.Tensor]]:
    """같은 위상의 결정 사슬을 **순서대로** 굴린다 — (L_port 값들, L_pred 값들).

    각 결정: 선정 점수(EMA · 미장 M1) → 소프트 층 → R = w·G − 비용 × Σ|w − 직전 하드 보유(드리프트)|.
    직전 보유는 같은 점수로 **하드 규칙이 실제로 든 것**(완충 포함, 그래디언트 없음)이다 — 판정 장부와 같은 경로.
    첫 `CHAIN_WARM` 결정은 보유만 세우고 손실에 넣지 않는다(그때 직전 보유가 "전량 매수" 라 비용 항이 상수다).
    """
    first = md.pos_of(chain[0])
    wdays = md.days[max(0, first - EMA_WINDOW + 1): md.pos_of(chain[-1]) + 1]
    wdays = [d for d in wdays if np.isfinite(inp.bb[md.sets[d].rows]).all()]
    where = {d: i for i, d in enumerate(wdays)}
    win = Window([], [], [])
    ss: dict[date, torch.Tensor] = {}
    E = len(md.entities)
    for d in wdays:
        st = md.sets[d]
        s = scores(head, inp, st, aug=aug, rng=rng)
        ss[d] = s
        hard = np.zeros(E)
        hard[st.code] = day_values(md, st, s, inp, soft=False).detach().numpy()
        soft = torch.zeros(E, dtype=torch.float64).index_put((torch.as_tensor(st.code),),
                                                             day_values(md, st, s, inp, soft=True))
        mask = np.zeros(E, bool)
        mask[st.code] = True
        win.hard.append(hard)
        win.soft.append(soft)
        win.mask.append(mask)
    held: list[int] = []
    prev: np.ndarray | None = None
    ports: list[torch.Tensor] = []
    preds: list[torch.Tensor] = []
    for i, day in enumerate(chain):
        if day not in where:
            break
        j = where[day]
        lo = max(0, j - EMA_WINDOW + 1)
        sel_h, have = ema_select(win.hard[lo: j + 1], win.mask[lo: j + 1])
        sel_s, _ = ema_select(win.soft[lo: j + 1], win.mask[lo: j + 1])
        st = md.sets[day]
        if variant == "D1a":
            trad = md.trad_mask(day)
            elig = have & trad if trad is not None else have
            idx = np.flatnonzero(elig)
            w_soft = soft_topk(standardize(sel_s[torch.as_tensor(idx)]), hyper.k, calib.temp[md.market])
            codes = idx
            held = rule_pick(held, sel_h.detach().numpy(), elig, hyper.k)
            dense_hard = np.zeros(E)
            dense_hard[held] = 1.0 / len(held)
        else:
            assert st.idx_code is not None and st.idx_b is not None
            b = torch.as_tensor(st.idx_b)
            w_soft = tilt_weights(members_z(st, sel_s, have, soft=True), b, hyper.lam, CAPS[md.market])
            codes = st.idx_code
            with torch.no_grad():
                w_hard = tilt_weights(members_z(st, sel_h, have, soft=False), b, hyper.lam, CAPS[md.market])
            dense_hard = np.zeros(E)
            dense_hard[codes] = w_hard.numpy()
        if i >= CHAIN_WARM and prev is not None:
            g = torch.as_tensor(np.nan_to_num(md.G[md.pos_of(day)][codes], nan=0.0))
            dense = torch.zeros(E, dtype=torch.float64).index_put((torch.as_tensor(codes),), w_soft)
            turn = (dense - torch.as_tensor(prev)).abs().sum()
            value = (w_soft * g).sum() - md.cost * turn
            if variant == "D1b":        # 지수(B0) 대비 초과 — 판정이 지수 대비 IR 이다(규율 2). 모델과 무관한 상수를 뺀다.
                bc, bw = base_weights(st)
                value = value - float((bw * np.nan_to_num(md.G[md.pos_of(day)][bc], nan=0.0)).sum())
            ports.append(value)
            have_y = np.isfinite(st.y)
            if have_y.sum() >= 2:
                preds.append(torch.mean((ss[day][torch.as_tensor(have_y)]
                                         - torch.as_tensor(st.y[have_y], dtype=torch.float64)) ** 2))
            if log is not None:
                log.seen.add((md.market, day))
                log.touch(md.label_end(day))
        prev = drift(md, dense_hard, day)
    return ports, preds


def port_loss(values: list[torch.Tensor], eta: float) -> torch.Tensor:
    v = torch.stack(values)
    return -v.mean() + eta * v.var(unbiased=False)


# ── 표본 ──────────────────────────────────────────────────────────────────────────────


def d1_train_end(axis: list[date], first: int) -> date:
    """D1 의 학습 끝점 — 블록 시작에서 `D1_GAP`(16) + 1 세션 앞(국장 축). kit 의 `train_end`(간격 10)는 라벨 10세션에 모자라다."""
    return axis[first - D1_GAP - 1]


def usable_day(variant: str, md: MarketData, inp: Inputs, day: date) -> bool:
    st = md.sets.get(day)
    if st is None or not np.isfinite(inp.bb[st.rows]).all():
        return False
    return variant == "D1a" or st.idx_code is not None


def training_pool(variant: str, data: dict[str, MarketData], inp: Inputs, cut: date,
                  judged_first: date) -> list[tuple[str, date]]:
    """학습 결정 — 날짜 ≤ 학습 끝점, **라벨의 마지막 가격일 < 판정 첫 세션**, 백본이 있는 날."""
    out = []
    for m, md in data.items():
        for day in md.days:
            if day > cut:
                break
            end = md.label_end(day)
            if end is not None and end < judged_first and usable_day(variant, md, inp, day):
                out.append((m, day))
    return out


def split_pool(data: dict[str, MarketData], pool: list[tuple[str, date]], *,
               share: float = kit.INNER_VAL_SHARE) -> tuple[list[tuple[str, date]], list[tuple[str, date]]]:
    """(적합, 내부 검증) — `kit.inner_split`(마지막 `share` — v1 20% · v2 40%, 퍼지 = D1_GAP). 적합 라벨의 마지막 가격일 < 검증 첫 날."""
    fit_days, val_days = kit.inner_split(sorted({d for _, d in pool}), share=share, purge=D1_GAP)
    if not val_days:
        return pool, []
    val_start = min(val_days)
    fit_set, val_set = set(fit_days), set(val_days)
    fit = [(m, d) for m, d in pool if d in fit_set and (data[m].label_end(d) or val_start) < val_start]
    return fit, [(m, d) for m, d in pool if d in val_set]


def chains_of(md: MarketData, days: list[date], length: int) -> list[list[date]]:
    """적합 결정 안에서 만들 수 있는 사슬 전부 — 같은 위상(선정 축 10칸 간격), 모두 적합 결정."""
    ok = set(days)
    out = []
    for d in days:
        p = md.pos_of(d)
        chain = [md.days[p + HOLD * i] for i in range(length) if p + HOLD * i < len(md.days)]
        if len(chain) == length and all(c in ok for c in chain):
            out.append(chain)
    return out


# ── 예측·규칙 포트 ────────────────────────────────────────────────────────────────────


def predict(head: nn.Module, inp: Inputs, data: dict[str, MarketData], keys: list[tuple[str, date]]) -> pd.DataFrame:
    """entity_id · session · market · pred(= 백본 + h). 평가 모드."""
    head.eval()
    frames = []
    with torch.no_grad():
        for m, day in keys:
            md = data[m]
            st = md.sets[day]
            s = scores(head, inp, st).numpy()
            frames.append(pd.DataFrame({"entity_id": np.asarray(md.entities, dtype=object)[st.code],
                                        "session": day, "market": m, "pred": s}))
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=["entity_id", "session", "market", "pred"]))


def selection_wide(pred: pd.DataFrame, book: kit.MarketBook) -> pd.DataFrame:
    """판정 규칙 포트의 선정 점수(세션 × 종목) — kit 과 같은 식: 국장 EMA5, 미장 M1 뒤 EMA5(`kit.us_m1_wide`)."""
    if book.market == "US":
        if book.fund is None:
            raise ValueError("미장 MarketBook 에 fund 가 없다 — M1 합성이 등록 규칙이다")
        return kit.us_m1_wide(pred[["entity_id", "session", "pred"]], book.fund)
    return pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()


Target = Callable[[DaySet], tuple[np.ndarray, np.ndarray]]
Held = dict[date, tuple[np.ndarray, np.ndarray]]


def rule_target(md: MarketData, wide: pd.DataFrame, n: int = K) -> Target:
    """하드 규칙 목표(상위 n · 완충 3N · 동일가중) — 호출 순서대로 완충 상태를 굴린다(판정 장부가 재조정일에만 부른다)."""
    held: list[int] = []

    def target(st: DaySet) -> tuple[np.ndarray, np.ndarray]:
        nonlocal held
        row = wide.loc[st.day].dropna() if st.day in wide.index else pd.Series(dtype=float)
        sel = np.full(len(md.entities), -np.inf)
        codes = np.array([md.code_of[e] for e in row.index if e in md.code_of], dtype=np.int64)
        sel[codes] = row[[e for e in row.index if e in md.code_of]].to_numpy()
        elig = np.isfinite(sel)
        trad = md.trad_mask(st.day)
        if trad is not None:
            elig &= trad
        held = rule_pick(held, sel, elig, n)
        return np.asarray(held, dtype=np.int64), np.full(len(held), 1.0 / len(held))
    return target


def tilt_target(md: MarketData, wide: pd.DataFrame, lam: float) -> Target:
    """D1b 판정 장부 목표 — 구성 종목의 선정 점수 rank-gauss 로 기울이기(AZ·AX 규칙), 상한 투영."""
    def target(st: DaySet) -> tuple[np.ndarray, np.ndarray]:
        assert st.idx_code is not None and st.idx_b is not None
        row = wide.loc[st.day] if st.day in wide.index else pd.Series(dtype=float)
        sel = np.full(len(md.entities), np.nan)
        keep = [e for e in row.index if e in md.code_of]
        sel[[md.code_of[e] for e in keep]] = row[keep].to_numpy()
        have = np.isfinite(sel)
        z = members_z(st, torch.as_tensor(np.nan_to_num(sel)), have, soft=False)
        with torch.no_grad():
            w = tilt_weights(z, torch.as_tensor(st.idx_b), lam, CAPS[md.market])
        return st.idx_code, w.numpy()
    return target


def run_book(md: MarketData, days: list[date], target: Target, *, every: int = HOLD,
             phase: int = 0) -> tuple[pd.Series, float, Held]:
    """장부 — `trial_ranker_kit.portfolio` 와 같은 규칙(첫날 회전 1, 재조정일에만 비용, 그 사이 드리프트). 위상 0 이 실제 장부."""
    prev: np.ndarray | None = None
    out: dict[date, float] = {}
    turns: list[float] = []
    held: Held = {}
    for step, day in enumerate(d for d in days if d in md.sets):
        if prev is None and step < phase:
            continue
        r = md.R[md.pos_of(day)]
        if prev is None or (step - phase) % every == 0:
            codes, w_t = target(md.sets[day])
            w = np.zeros(len(md.entities))
            w[codes] = w_t
            t = float(np.abs(w - prev).sum()) if prev is not None else float(w.sum())
            held[day] = (codes, w_t)
        else:
            w, t = prev, 0.0
        out[day] = float(w @ r - md.cost * t)
        turns.append(t)
        d = w * (1.0 + r)
        total = d.sum()
        prev = d / total if total > 0 else w
    return pd.Series(out, dtype=float).sort_index(), float(np.mean(turns) * ANN) if turns else 0.0, held


def book_metrics(daily: pd.Series, md: MarketData, turn: float, pred_y: pd.DataFrame | None) -> dict[str, float]:
    """대조군과 같은 요약(`trial_ranker_kit.summarize`) + 회전 + 지수 대비 국면 초과(v2 기록)."""
    m = rkit.summarize(daily, md.bench, pred_y)
    m["turn"] = turn
    b = md.bench.reindex(daily.index).fillna(0.0)
    for label, part in (("box", daily.index <= BOX_END), ("rally", daily.index > BOX_END)):
        m[f"{label}_excess"] = float((daily[part] - b[part]).mean() * ANN) if part.any() else float("nan")
    return m


def tilt_book(pred: pd.DataFrame, data: dict[str, MarketData], books: dict[str, kit.MarketBook],
              y: pd.DataFrame | None, *, lam: float = LAMBDA, phase: int = 0) -> tuple[dict[str, float], dict[str, Held]]:
    """D1b 판정 장부(합동 지표, 시장별 재조정 비중)."""
    per: dict[str, dict[str, float]] = {}
    helds: dict[str, Held] = {}
    for m, md in data.items():
        part = pred[pred["market"] == m]
        if part.empty:
            continue
        wide = selection_wide(part, books[m])
        days = sorted(d for d in set(part["session"]) if d in md.sets and md.sets[d].idx_code is not None)
        daily, turn, held = run_book(md, days, tilt_target(md, wide, lam), phase=phase)
        py = part[["entity_id", "session", "pred"]].merge(y, on=["entity_id", "session"], how="left") if y is not None else None
        per[m] = book_metrics(daily, md, turn, py)
        helds[m] = held
    return kit.pooled_metrics(per), helds


def rule_book(pred: pd.DataFrame, data: dict[str, MarketData], books: dict[str, kit.MarketBook], *,
              phase: int = 0) -> tuple[dict[str, float], dict[str, Held]]:
    """하드 규칙 포트를 **이 파일의 장부로** 다시 굴린다 — 위상 흩어짐·성향 노출 기록 전용. 판정 수치는 `kit.evaluate_all` 이다
    (둘이 같은지는 테스트가 대조한다)."""
    per: dict[str, dict[str, float]] = {}
    helds: dict[str, Held] = {}
    for m, md in data.items():
        part = pred[pred["market"] == m]
        if part.empty:
            continue
        wide = selection_wide(part, books[m])
        days = sorted(d for d in set(part["session"]) if d in md.sets)
        daily, turn, held = run_book(md, days, rule_target(md, wide), phase=phase)
        per[m] = book_metrics(daily, md, turn, None)
        helds[m] = held
    return kit.pooled_metrics(per), helds


def base_book(data: dict[str, MarketData], days_of: dict[str, list[date]]) -> dict[str, float]:
    """B0 — 신호 없는 같은 구성(λ = 0). IC 없음."""
    per = {}
    for m, md in data.items():
        days = [d for d in days_of.get(m, []) if d in md.sets and md.sets[d].idx_code is not None]
        if days:
            daily, turn, _held = run_book(md, days, base_weights)
            per[m] = book_metrics(daily, md, turn, None)
    return kit.pooled_metrics(per)


def hard_score(variant: str, pred: pd.DataFrame, data: dict[str, MarketData], books: dict[str, kit.MarketBook],
               hyper: Hyper = HYPER) -> float:
    """하드 규칙 포트의 연수익(합동) — 조기 종료 지표. D1a = kit 규칙 포트, D1b = 기울이기 장부."""
    if pred.empty:
        return float("nan")
    if variant == "D1a":
        return float(kit.evaluate_all(pred, books)[1].get("ann", np.nan))
    return float(tilt_book(pred, data, books, None, lam=hyper.lam)[0].get("ann", np.nan))


def hard_daily(variant: str, pred: pd.DataFrame, data: dict[str, MarketData], books: dict[str, kit.MarketBook],
               hyper: Hyper = HYPER) -> dict[str, pd.Series]:
    """하드 규칙 포트의 **일수익**(시장별) — v2 조기 종료 지표의 재료. D1a = 이 파일의 규칙 장부(kit 규칙 포트와 일수익이 같다 —
    테스트), D1b = 기울이기 장부. 둘 다 `hard_score` 가 연수익을 내는 바로 그 장부다."""
    out: dict[str, pd.Series] = {}
    for m, md in data.items():
        part = pred[pred["market"] == m]
        if part.empty:
            continue
        wide = selection_wide(part, books[m])
        if variant == "D1a":
            days = sorted(d for d in set(part["session"]) if d in md.sets)
            target = rule_target(md, wide)
        else:
            days = sorted(d for d in set(part["session"]) if d in md.sets and md.sets[d].idx_code is not None)
            target = tilt_target(md, wide, hyper.lam)
        out[m] = run_book(md, days, target)[0]
    return out


def diff_t(head: dict[str, pd.Series], base: dict[str, pd.Series]) -> float:
    """일수익 차이(머리 − h 0)의 t 값 — 시장별 차이를 한 줄로 이어 평균 / (표준편차 / √n). 차이가 없으면 0.

    척도가 없다: 창 길이·변동성이 달라도 "잡음이면 ~N(0,1)" 이다. 그래서 실자료로 얼린 여백을 합성 카나리에 걸 수 있다
    (v1 의 연수익 차이는 실자료 잡음 폭이 합성의 4배라 그 여백이 합성 신호를 통째로 막았다). 보유가 10세션 이어져도 일수익 차이는
    그 날의 수익이라 계열상관이 작다 — 평범한 t 를 쓴다(기록: 등록 문서)."""
    parts = []
    for m, h in head.items():
        b = base.get(m)
        if b is None:
            continue
        both = pd.concat([h, b], axis=1, join="inner").dropna()
        parts.append((both.iloc[:, 0] - both.iloc[:, 1]).to_numpy(np.float64))
    d = np.concatenate(parts) if parts else np.zeros(0)
    if len(d) < 2:
        return 0.0
    sd = float(d.std(ddof=1))
    return float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 1e-12 else 0.0


# ── 학습 ──────────────────────────────────────────────────────────────────────────────


def calibrate(variant: str, inp: Inputs, data: dict[str, MarketData], fit: list[tuple[str, date]],
              hyper: Hyper, seed: int, *, max_days: int = 40, max_chains: int = 24) -> Calib:
    """첫 학습창 안에서 온도(시장별)와 손실 척도를 정한다 — h = 0(백본 그대로). 판정 창을 보지 않는다(적합 결정만 읽는다)."""
    rng = np.random.default_rng(seed)
    head = make_head(seed, inp.X.shape[1])
    head.eval()
    temp: dict[str, float] = {}
    by_market = {m: [d for mm, d in fit if mm == m] for m in data}
    with torch.no_grad():
        for m, days in by_market.items():
            md = data[m]
            if variant == "D1b" or not days:
                temp[m] = 0.1
                continue
            pick = [days[int(i)] for i in rng.choice(len(days), size=min(max_days, len(days)), replace=False)]
            zs = []
            for day in pick:
                lo = max(0, md.pos_of(day) - EMA_WINDOW + 1)
                wd = [d for d in md.days[lo: md.pos_of(day) + 1] if np.isfinite(inp.bb[md.sets[d].rows]).all()]
                vals, masks = [], []
                for d in wd:
                    st = md.sets[d]
                    v = np.zeros(len(md.entities))
                    v[st.code] = day_values(md, st, scores(head, inp, st), inp, soft=True).numpy()
                    mk = np.zeros(len(md.entities), bool)
                    mk[st.code] = True
                    vals.append(v)
                    masks.append(mk)
                sel, have = ema_select(vals, masks)
                trad = md.trad_mask(day)
                elig = have & trad if trad is not None else have
                zs.append(standardize(sel[torch.as_tensor(np.flatnonzero(elig))]))

            def eff(t: float, zs: list[torch.Tensor] = zs) -> float:
                return float(np.median([effective_n(soft_topk(z, hyper.k, t)) for z in zs]))

            lo_t, hi_t = np.log(1e-3), np.log(5.0)
            for _ in range(40):                       # 유효 종목 수는 온도에 단조 증가 — 로그 이분법
                mid = 0.5 * (lo_t + hi_t)
                if eff(float(np.exp(mid))) < TARGET_EFF:
                    lo_t = mid
                else:
                    hi_t = mid
            temp[m] = float(np.exp(0.5 * (lo_t + hi_t)))
        calib0 = Calib(temp, 1.0, 1.0)
        ports, preds = [], []
        for m, days in by_market.items():
            chains = chains_of(data[m], days, hyper.chain_len)
            for c in (chains[int(i)] for i in rng.choice(len(chains), size=min(max_chains, len(chains)),
                                                          replace=False)) if chains else []:
                p, q = roll_chain(variant, head, inp, data[m], c, calib0, hyper)
                ports += [float(v) for v in p]
                preds += [float(v) for v in q]
    l_port = float(np.std(ports)) if len(ports) > 1 else 1.0
    return Calib(temp, float(np.mean(preds)) if preds else 1.0, max(l_port, 1e-4))


def train_head(variant: str, inp: Inputs, data: dict[str, MarketData], books: dict[str, kit.MarketBook],
               fit: list[tuple[str, date]], val: list[tuple[str, date]], seed: int, calib: Calib, *,
               aug: Aug | None = None, hyper: Hyper = HYPER, log: TrainLog | None = None,
               margin: float = 0.0, val_data: dict[str, MarketData] | None = None,
               accept: Accept = ACCEPT_V1) -> tuple[nn.Module, TrainLog]:
    """잔차 머리 학습. 에포크 0 = h 0 = 백본. 조기 종료 지표 = **하드 규칙 포트**의 내부 검증 연수익(높을수록 좋다, 부호 뒤집어 기록).
    ``accept.metric == "t"``(v2)면 지표는 같은 장부의 **일수익 차이(이 에포크 − h 0)의 t 값**이다(에포크 0 = 0).

    **채택 여백** — 최고 에포크의 내부 검증 이득이 `margin`(라벨을 섞은 학습에서 관측된 이득의 최댓값, 시드별로 얼린 값)을
    **넘을 때만** 머리를 쓴다. 못 넘으면 h = 0(백본 그대로)으로 되돌린다 — 조기 종료가 검증 창의 잡음을 줍는 것을 막는다.
    """
    log = log if log is not None else TrainLog(margin=margin)
    log.margin = margin
    log.metric = accept.metric
    head = make_head(seed, inp.X.shape[1])
    torch.manual_seed(seed)
    opt = torch.optim.AdamW(head.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    rng = np.random.default_rng(seed)
    chains = {m: chains_of(data[m], [d for mm, d in fit if mm == m], hyper.chain_len) for m in data}
    markets = [m for m, c in chains.items() if c]
    if not markets:
        raise ValueError("사슬을 만들 적합 결정이 없다 — 학습 끝점이 너무 이르다")

    base_daily: dict[str, pd.Series] | None = None

    def validate() -> float:
        nonlocal base_daily
        pred = predict(head, inp, data, val)
        log.val_days |= set(val)
        # 내부 검증 장부는 **실제 수익**으로 잰다(`val_data`) — 라벨 섞기에서 학습만 섞은 자료를 쓰고 검증은 진짜로 본다.
        book_data = val_data if val_data is not None else data
        if accept.metric == "t":
            daily = hard_daily(variant, pred, book_data, books, hyper)
            if base_daily is None:            # 에포크 0 = h 0 — 비교의 기준
                base_daily = daily
            head.train()
            return -diff_t(daily, base_daily)
        score = hard_score(variant, pred, book_data, books, hyper)
        head.train()
        return -score if np.isfinite(score) else float("inf")

    best = validate()
    log.val_scores.append(best)
    best_state = {k: v.detach().clone() for k, v in head.state_dict().items()}
    zero_state = {k: v.detach().clone() for k, v in best_state.items()}
    bad = 0
    head.train()
    for epoch in range(1, hyper.max_epochs + 1):
        losses = []
        for i in range(hyper.steps_per_epoch):
            m = markets[i % len(markets)]
            chain = chains[m][int(rng.integers(len(chains[m])))]
            ports, preds = roll_chain(variant, head, inp, data[m], chain, calib, hyper, aug=aug, rng=rng, log=log)
            if len(ports) < 2:
                continue
            loss = (1.0 - hyper.alpha) * port_loss(ports, hyper.eta) / calib.l_port
            if preds and hyper.alpha > 0:
                loss = loss + hyper.alpha * torch.stack(preds).mean() / calib.l_pred
            opt.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(head.parameters(), CLIP)
            opt.step()
            losses.append(float(loss.detach()))
        log.train_losses.append(float(np.mean(losses)) if losses else float("nan"))
        log.epochs = epoch
        score = validate()
        log.val_scores.append(score)
        if score < best - 1e-9:
            best, bad, log.best_epoch = score, 0, epoch
            best_state = {k: v.detach().clone() for k, v in head.state_dict().items()}
        else:
            bad += 1
            if bad >= hyper.patience:
                log.stopped_early = epoch < hyper.max_epochs
                break
    log.accepted = log.best_epoch > 0 and log.gain > margin
    log.fit = list(fit)
    log.best_state = best_state if log.best_epoch > 0 else None
    head.load_state_dict(best_state if log.accepted else zero_state)
    head.eval()
    return head, log


def fmt_gain(value: float, accept: Accept = ACCEPT_V1, *, sign: bool = True) -> str:
    """이득·여백 표기 — v1 은 연수익(%), v2 는 t 값."""
    if accept.metric == "t":
        return f"t {value:+.2f}" if sign else f"t {value:.2f}"
    return f"{value:+.2%}" if sign else f"{value:.2%}"


def judged_keys(data: dict[str, MarketData], axis: list[date], first: int, last: int) -> list[tuple[str, date]]:
    """블록의 판정 (시장, 세션) — 경계는 `kit.block_span`(반열림) 한 곳에서만."""
    lo, hi = kit.block_span(axis, first, last)
    return [(m, d) for m, md in data.items() for d in md.days if lo <= d and (hi is None or d < hi)]


@dataclass
class WalkResult:
    judge: pd.DataFrame
    train: pd.DataFrame
    logs: list[TrainLog]
    cuts: list[date]
    minutes: float
    calib: Calib | None = None


def walk(variant: str, inp: Inputs, data: dict[str, MarketData], books: dict[str, kit.MarketBook],
         axis: list[date], blocks: list[tuple[int, int]], seed: int, *, aug: Aug | None = None,
         hyper: Hyper = HYPER, store: object = None, clock: object = None, n_seeds: int = 0,
         insample: int = 100, margin: float = 0.0, val_data: dict[str, MarketData] | None = None,
         gains_only: bool = False, accept: Accept = ACCEPT_V1,
         replay: Sequence[TrainLog] | None = None, decisions: Sequence[bool] | None = None) -> WalkResult:
    """확장창 워크포워드 — 블록 `first_block` 부터 채점, 5블록마다 재학습(학습 끝점 `d1_train_end`).

    온도·손실 척도는 **첫 재학습의 적합 결정**으로 정하고 이 시드 안에서 얼린다.
    `store`·`clock` 이 있으면 블록마다 `kit.record_progress` — 학습 손실·내부 검증(하드 규칙 수익, 부호 뒤집음)뿐.

    ``gains_only`` — 여백 굽기(실자료 라벨 섞기) 전용: 재학습만 하고 **판정 블록·학습창 예측을 하나도 내지 않는다**
    (판정 블록 수익을 볼 길 자체를 없앤다). 로그의 내부 검증 이득만 남는다.

    ``replay`` + ``decisions`` — v2 합동 결정(`walk_pooled`)의 둘째 걸음: **다시 학습하지 않고** 첫 걸음이 남긴 머리(`TrainLog.best_state`)를
    재학습마다 정해진 결정대로 얹어(False 면 h = 0) 판정 블록·학습창 예측만 낸다. 블록·재학습 경계는 이 함수 하나가 정한다.
    """
    began = mark = monotonic()  # invariant-allow: wallclock — 소요 시간 기록
    head: nn.Module | None = None
    calib: Calib | None = None
    logs: list[TrainLog] = []
    cuts: list[date] = []
    out, train_out = [], []
    prev_cut: date | None = None
    markets = "+".join(sorted(data))
    for number, (first, last) in enumerate(blocks):
        if number < hyper.first_block:
            continue
        if head is None or (number - hyper.first_block) % hyper.retrain_every == 0:
            cut = d1_train_end(axis, first)
            judged_first = kit.block_span(axis, first, last)[0]
            if replay is not None and decisions is not None:
                log = copy.copy(replay[len(logs)])
                fit, val = log.fit, sorted(log.val_days)
                log.margin, log.accepted = margin, bool(decisions[len(logs)]) and log.best_state is not None
                head = make_head(seed, inp.X.shape[1])
                if log.accepted:
                    assert log.best_state is not None
                    head.load_state_dict(log.best_state)
                head.eval()
            else:
                fit, val = split_pool(data, training_pool(variant, data, inp, cut, judged_first),
                                      share=accept.val_share)
                if calib is None:
                    calib = calibrate(variant, inp, data, fit, hyper, seed)
                head, log = train_head(variant, inp, data, books, fit, val, seed, calib, aug=aug, hyper=hyper,
                                       margin=margin, val_data=val_data, accept=accept)
            logs.append(log)
            cuts.append(cut)
            if not gains_only:
                fit_days = sorted({d for _, d in fit})
                since = (prev_cut if prev_cut is not None
                         else fit_days[-min(insample, len(fit_days))] - timedelta(days=1))
                train_out.append(predict(head, inp, data, [k for k in fit if k[1] > since]))
            prev_cut = cut
            print(f"  {variant} seed {seed} · 재학습 {len(logs)} · 적합 {len(fit)} / 검증 {len(val)} 결정 (~{cut}) · "
                  f"에포크 {log.epochs}{' (조기종료)' if log.stopped_early else ''} · "
                  f"검증 이득 {fmt_gain(log.gain, accept)} (여백 {fmt_gain(margin, accept, sign=False)}) → {'머리 채택' if log.accepted else '백본 그대로'} · "
                  f"누적 {(monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock
        assert head is not None
        if gains_only:
            continue
        out.append(predict(head, inp, data, judged_keys(data, axis, first, last)))
        log = logs[-1]
        kit.record_progress(
            store, clock, variant, source="trial_final_dfl",
            market=markets, seed=int(seed), n_seeds=n_seeds or None, block=number, n_blocks=len(blocks),
            epoch=log.epochs, train_loss=(log.train_losses[-1] if log.train_losses else None),
            val_loss=(log.val_scores[log.best_epoch] if log.val_scores else None),
            metric="mixed / -hard rule ann(val)", stopped_early=log.stopped_early,
            elapsed_s=monotonic() - mark,  # invariant-allow: wallclock
            note=(f"재학습 {len(logs)}회 · {'머리 채택' if log.accepted else '백본 그대로'} · "
                  f"판정 {axis[first]}~{axis[last]}"))
        mark = monotonic()  # invariant-allow: wallclock
    empty = pd.DataFrame(columns=["entity_id", "session", "market", "pred"])
    return WalkResult(pd.concat(out, ignore_index=True) if out else empty,
                      pd.concat(train_out, ignore_index=True).drop_duplicates(["entity_id", "session", "market"],
                                                                             keep="last") if train_out else empty,
                      logs, cuts, (monotonic() - began) / 60, calib)  # invariant-allow: wallclock


def pooled_means(gains: Sequence[float], n_seeds: int) -> list[float]:
    """시드 순서로 이어 붙인 이득(시드 0 의 재학습 1..R, 시드 1 의 …) → 재학습별 시드 평균 R 개."""
    g = np.asarray(gains, dtype=float)
    if n_seeds <= 0 or len(g) == 0 or len(g) % n_seeds:
        raise ValueError(f"이득 {len(g)}개를 시드 {n_seeds}개로 나눌 수 없다")
    return [float(v) for v in g.reshape(n_seeds, -1).mean(axis=0)]


def walk_pooled(variant: str, inputs: dict[int, Inputs], data: dict[str, MarketData],
                books: dict[str, kit.MarketBook], axis: list[date], blocks: list[tuple[int, int]], *,
                margin: float, accept: Accept, aug: Aug | None = None, hyper: Hyper = HYPER,
                val_data: dict[str, MarketData] | None = None, store: object = None, clock: object = None,
                trained: dict[int, WalkResult] | None = None,
                data_by_seed: dict[int, dict[str, MarketData]] | None = None) -> dict[int, WalkResult]:
    """v2 합동 결정 — ① 시드마다 학습(판정 예측 없음, `gains_only`) ② 재학습마다 **시드 평균 이득 > 여백** 이면 그 재학습의 머리를 시드
    전부 채택 ③ 결정대로 머리를 얹어 예측(다시 학습하지 않는다). ``trained`` = ①을 이미 끝낸 시드(이어 쓰기).
    ``data_by_seed`` — 시드마다 다른 학습 자료(라벨 섞기 점검: 시드마다 다른 섞기, v1 과 같다)."""
    seeds = sorted(inputs)
    first: dict[int, WalkResult] = dict(trained or {})
    for s in seeds:
        if s not in first:
            first[s] = walk(variant, inputs[s], (data_by_seed or {}).get(s, data), books, axis, blocks, s, aug=aug, hyper=hyper, margin=float("inf"),
                            val_data=val_data, gains_only=True, accept=accept)
    n = {len(first[s].logs) for s in seeds}
    if len(n) != 1:
        raise ValueError(f"시드마다 재학습 수가 다르다 {n} — 합동 결정을 할 수 없다")
    means = pooled_means([lg.gain for s in seeds for lg in first[s].logs], len(seeds))
    decisions = [m > margin for m in means]
    print(f"  {variant} 합동 결정(재학습마다 시드 {len(seeds)}개 평균 > 여백 {fmt_gain(margin, accept, sign=False)}): "
          + " · ".join(f"{j + 1}:{fmt_gain(m, accept)}{'✓' if d else '·'}" for j, (m, d) in enumerate(zip(means, decisions,
                                                                                                      strict=True))),
          flush=True)
    out = {}
    for s in seeds:
        res = walk(variant, inputs[s], (data_by_seed or {}).get(s, data), books, axis, blocks, s, aug=aug, hyper=hyper,
                   margin=margin,
                   val_data=val_data, accept=accept, store=store, clock=clock, n_seeds=len(seeds),
                   replay=first[s].logs, decisions=decisions)
        out[s] = replace(res, calib=first[s].calib, minutes=first[s].minutes + res.minutes)
    return out


# ── 판정 ──────────────────────────────────────────────────────────────────────────────


def require_keys(where: str, metrics: dict[str, float], keys: Sequence[str] = JUDGE_KEYS) -> dict[str, float]:
    bad = [k for k in keys if k not in metrics or not np.isfinite(metrics[k])]
    if bad:
        raise SystemExit(f"{where}: 판정 키가 없거나 nan 이다 {bad} — 이대로 judge 에 넘기면 관문이 조용히 떨어진다")
    return metrics


def restrict(frame: pd.DataFrame, since: date) -> pd.DataFrame:
    """대조군을 D1 이 채점받는 구간으로 — 앞 `first_block` 블록은 D1 의 학습 자료라 채점하지 않는다(모든 군 같은 구간)."""
    part = frame.copy()
    part["session"] = pd.to_datetime(part["session"]).dt.date
    return part[part["session"] >= since].reset_index(drop=True)


def require_same_span(pred: pd.DataFrame, controls: dict[str, dict[int, pd.DataFrame]]) -> list[str]:
    """D1 이 채점받는 세션이 대조군이 채점받는 세션과 같은지. D1 이 더 많이 채점하면 멈춘다."""
    lines = []
    for m in sorted(pred["market"].unique()):
        mine = set(pd.to_datetime(pred.loc[pred["market"] == m, "session"]).dt.date)
        ctrl: set[date] = set()
        for table in controls.values():
            for frame in table.values():
                ctrl |= set(pd.to_datetime(frame.loc[frame["market"] == m, "session"]).dt.date)
        extra = sorted(mine - ctrl)
        if extra:
            raise SystemExit(f"{m}: D1 이 대조군이 채점받지 않는 {len(extra)}세션을 채점한다(예: {extra[:3]})")
        lines.append(f"{m} 채점 세션: D1 {len(mine)} · 대조군 {len(ctrl)} (차이 {len(ctrl - mine)} = 세트 최소 크기 미달)")
    return lines


def style_exposure(X: np.ndarray, data: dict[str, MarketData], helds: dict[str, Held],
                   index_of: dict[str, int]) -> dict[str, float]:
    """재조정일 비중으로 가중한 성향 노출(원피처 rank-gauss) — β·가치·배당(`final_round_controls.PROXY_COLS`)."""
    from tools.final_round_controls import PROXY_COLS

    out: dict[str, float] = {}
    for label, col in PROXY_COLS.items():
        if col not in index_of:
            continue
        vals = []
        for m, held in helds.items():
            md = data[m]
            for day, (codes, w) in held.items():
                st = md.sets[day]
                row_of = {c: r for c, r in zip(st.code, st.rows, strict=False)}
                rows = np.array([row_of.get(c, -1) for c in codes])
                have = rows >= 0
                if have.any():
                    vals.append(float((w[have] * X[rows[have], index_of[col]].astype(np.float64)).sum() / w[have].sum()))
        if vals:
            out[label] = float(np.mean(vals))
    return out


def _avg(table: dict[int, dict[str, float]], key: str) -> float:
    vals = [m[key] for m in table.values() if key in m and np.isfinite(m[key])]
    return float(np.mean(vals)) if vals else float("nan")


def _criteria(block: list[str]) -> list[str]:
    """kit.judge 의 "판정:" 을 "①~⑥:" 으로 — 이 도구의 판정 줄은 마지막 한 줄뿐(러너가 `^판정:` 으로 끝을 안다)."""
    return [line.replace("판정:", "①~⑥:", 1) if line.startswith("판정:") else line for line in block]


def _exposure_text(table: dict[str, float]) -> str:
    return " · ".join(f"{k} {v:+.2f}" for k, v in table.items()) or "없음(성향 대리 열이 FA 에 없다)"


@dataclass
class Verdict:
    variant: str
    verdict: str
    lines: list[str]
    results: dict[int, dict[str, float]]
    delta: dict[int, float] = field(default_factory=dict)       # 시드별 D1 − 백본(연수익) — 자기 점검이 읽는다


def judge_variant(variant: str, preds: dict[int, WalkResult], data: dict[str, MarketData],
                  controls: dict[str, dict[int, pd.DataFrame]], books: dict[str, kit.MarketBook],
                  y: pd.DataFrame, X: np.ndarray, index_of: dict[str, int], since: date, *,
                  backbone: str = "C1", hyper: Hyper = HYPER) -> Verdict:
    """등록 §채택 기준. D1a: kit.judge(D1a, C0, 백본) — 모두 kit 규칙 포트. D1b: kit.judge(D1b, T0, T백본) + v2 기준."""
    seeds = sorted(preds)
    ctrl = {arm: {s: restrict(f, since) for s, f in controls[arm].items() if s in seeds} for arm in ("C0", backbone)}
    lines = require_same_span(pd.concat([p.judge for p in preds.values()], ignore_index=True), ctrl)
    rule = {arm: {s: require_keys(f"{arm} 시드 {s}", kit.evaluate_all(f, books, y=y)[1]) for s, f in t.items()}
            for arm, t in ctrl.items()}
    if variant == "D1a":
        results = {s: require_keys(f"D1a 시드 {s}", kit.evaluate_all(preds[s].judge, books, y=y)[1]) for s in seeds}
        block, verdict = kit.judge(results, rule["C0"], rule[backbone], label=f"D1a 소프트 상위-k(대 C0·{backbone}): ")
        lines += _criteria(block)
        base = rule[backbone]
        helds = rule_book(preds[seeds[0]].judge, data, books)[1]
        other = rule_book(ctrl[backbone][seeds[0]], data, books)[1]
        spread = [rule_book(preds[seeds[0]].judge, data, books, phase=p)[0].get("ann", np.nan) for p in range(HOLD)]
        train_m = {s: kit.evaluate_all(preds[s].train, books, y=y)[1] for s in seeds if not preds[s].train.empty}
    else:
        results = {s: require_keys(f"D1b 시드 {s}", tilt_book(preds[s].judge, data, books, y, lam=hyper.lam)[0])
                   for s in seeds}
        tilt = {arm: {s: require_keys(f"T{arm} 시드 {s}", tilt_book(f, data, books, y, lam=hyper.lam)[0])
                      for s, f in t.items()} for arm, t in ctrl.items()}
        block, base_verdict = kit.judge(results, tilt["C0"], tilt[backbone],
                                        label=f"D1b 지수 기울이기(대 T0·T{backbone[1]}): ")
        lines += _criteria(block)
        base = tilt[backbone]
        days_of = {m: sorted(set(preds[seeds[0]].judge.loc[preds[seeds[0]].judge["market"] == m, "session"]))
                   for m in data}
        b0 = base_book(data, days_of)
        v2 = [_avg(results, "ir") >= IR_GATE,
              _avg(results, "box_excess") >= 0 and _avg(results, "rally_excess") >= 0,
              _avg(results, "ann") >= b0.get("ann", np.inf)]
        mk = rkit.mark
        lines.append(f"v2: IR(지수) {_avg(results, 'ir'):+.2f} (≥{IR_GATE}) {mk(v2[0])} · 두 국면 지수 대비 "
                     f"{_avg(results, 'box_excess'):+.1%}/{_avg(results, 'rally_excess'):+.1%} {mk(v2[1])} · "
                     f"B0(λ=0) 대비 {_avg(results, 'ann') - b0.get('ann', np.nan):+.1%}p {mk(v2[2])}")
        lines.append(f"기록: 규칙 상위 24 대비 — D1b {_avg(results, 'ann'):+.1%} · C0 {_avg(rule['C0'], 'ann'):+.1%} · "
                     f"{backbone} {_avg(rule[backbone], 'ann'):+.1%} · B0 {b0.get('ann', np.nan):+.1%} (IR {b0.get('ir', np.nan):+.2f})")
        if base_verdict.startswith("채택 —") and all(v2):
            verdict = "채택 — DFL 기울이기가 나아서(①~⑥ + v2)"
        elif base_verdict.startswith("채택") and all(v2):
            verdict = f"채택 후보 T{backbone[1]} — 정보가 늘어서(①~⑤ + v2 통과, ⑥ 미통과)"
        else:
            verdict = "기각"
        helds = tilt_book(preds[seeds[0]].judge, data, books, None, lam=hyper.lam)[1]
        other = tilt_book(ctrl[backbone][seeds[0]], data, books, None, lam=hyper.lam)[1]
        spread = [tilt_book(preds[seeds[0]].judge, data, books, None, lam=hyper.lam, phase=p)[0].get("ann", np.nan)
                  for p in range(HOLD)]
        train_m = {s: tilt_book(preds[s].train, data, books, y, lam=hyper.lam)[0] for s in seeds
                   if not preds[s].train.empty}
    delta = {s: results[s]["ann"] - base[s]["ann"] for s in seeds if s in base}
    gap = kit.overfit_gap(train_m, results) if train_m else {}
    logs = [log for p in preds.values() for log in p.logs]
    calibs = [p.calib for p in preds.values() if p.calib is not None]
    lines += [
        f"기록: D1 − {backbone}(같은 포트) 시드별 " + " · ".join(f"{s}:{v:+.1%}" for s, v in delta.items()),
        f"기록(성향 — 규율 15): β {_avg(results, 'beta'):.2f} (박스 {_avg(results, 'box_beta'):.2f} · 급등 "
        f"{_avg(results, 'rally_beta'):.2f}) · {backbone} β {_avg(base, 'beta'):.2f} · 가중 노출 "
        f"{_exposure_text(style_exposure(X, data, helds, index_of))} | {backbone} "
        f"{_exposure_text(style_exposure(X, data, other, index_of))}",
        f"기록: 위상 0~9 연수익 흩어짐 {np.nanstd(spread):.2%} (위상 0 {spread[0]:+.1%}) — 기준 아님",
        "기록: 과적합 격차(학습창 − 판정창) " + " · ".join(f"{k} {v:+.4f}" for k, v in gap.items()),
        "기록: 얼린 값 — 온도 " + " · ".join(f"{m} {np.mean([c.temp[m] for c in calibs]):.3f}" for m in data)
        + f" · 척도 ℓ_pred {np.mean([c.l_pred for c in calibs]) if calibs else float('nan'):.3f}"
        f" · ℓ_port {np.mean([c.l_port for c in calibs]) if calibs else float('nan'):.4f}",
        f"기록: 재학습 {len(logs)}회 · 머리 채택 {sum(lg.accepted for lg in logs)}/{len(logs)} "
        f"(조기 종료가 고른 개선 {sum(lg.dfl_improved for lg in logs)} · 여백 "
        f"{', '.join(sorted({fmt_gain(lg.margin, Accept(metric=lg.metric), sign=False) for lg in logs}))}) · "
        f"조기 종료 {sum(lg.stopped_early for lg in logs)} · 학습 {sum(p.minutes for p in preds.values()):.0f}분 · "
        f"최대 RSS {kit.rss_mb():.0f}MB",
        f"판정: {verdict}",
    ]
    return Verdict(variant, verdict, lines, results, delta)


# ── 준비 ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Prepared:
    inp: Inputs
    data: dict[str, MarketData]
    axis: list[date]
    blocks: list[tuple[int, int]]
    aug: Aug
    index_of: dict[str, int]
    y: pd.DataFrame
    keys: pd.DataFrame


def prepare(panel: pd.DataFrame, feats: list[str], groups: dict[str, list[str]], sessions: list[date],
            books: dict[str, kit.MarketBook], index_raw: dict[str, dict[date, pd.Series]] | None = None) -> Prepared:
    """패널 → 입력 행렬·시장 자료. X 를 뽑은 뒤 키·y5·재무 표지만 남긴다(FA 76열 프레임을 두 벌 들지 않는다)."""
    panel = panel.sort_values(["market", "session", "entity_id"]).reset_index(drop=True)
    columns = list(feats) if "is_us" in feats else [*feats, "is_us"]
    if "is_us" not in panel.columns:
        panel["is_us"] = (panel["market"] == "US").astype(np.float32)
    X = feature_matrix(panel, columns)
    fund = (panel["fund_raw"].to_numpy(np.float64) if "fund_raw" in panel.columns else np.zeros(len(panel)))
    has = (panel["has_fund"].fillna(False).to_numpy(bool) if "has_fund" in panel.columns
           else np.zeros(len(panel), bool))
    keys = panel[["entity_id", "session", "market", "y5"]].copy()
    data = {m: build_market(keys, m, books[m], (index_raw or {}).get(m)) for m in sorted(books)
            if (keys["market"] == m).any()}
    index_of = {c: i for i, c in enumerate(columns)}
    inp = Inputs(X, np.full(len(panel), np.nan), np.nan_to_num(fund), has)
    return Prepared(inp, data, list(sessions), list(kit.blocks(sessions)), Aug(dict(groups), index_of),
                    index_of, keys[["entity_id", "session", "y5"]], keys)


def with_backbone(prep: Prepared, frame: pd.DataFrame) -> Inputs:
    """백본 예측(한 시드)을 패널 행에 얹는다. 없는 행은 NaN — 그 날은 학습·채점에 못 쓴다."""
    key = prep.keys.reset_index()[["index", "entity_id", "session", "market"]]
    f = frame[["entity_id", "session", "market", "pred"]].copy()
    f["session"] = pd.to_datetime(f["session"]).dt.date
    hit = key.merge(f.drop_duplicates(["entity_id", "session", "market"]), on=["entity_id", "session", "market"])
    bb = np.full(len(prep.keys), np.nan)
    bb[hit["index"].to_numpy()] = hit["pred"].to_numpy(np.float64)
    return replace(prep.inp, bb=bb)


def d1_since(prep: Prepared, hyper: Hyper = HYPER) -> date:
    first, last = prep.blocks[hyper.first_block]
    return kit.block_span(prep.axis, first, last)[0]


# ── 실자료 ─────────────────────────────────────────────────────────────────────────────


def index_weights(store: object, books: dict[str, kit.MarketBook], sessions_of: dict[str, list[date]],
                  ) -> dict[str, dict[date, pd.Series]]:
    """세션별 지수 **원** 비중(상한 전, 합 1). 국장 = K200 유동시총(AZ 의 읽기), 미장 = 시총 상위 500(AX)."""
    out: dict[str, dict[date, pd.Series]] = {}
    if "KR" in books and sessions_of.get("KR"):
        from tools.trial_kr_index_tilt import caps_panel, float_ratios, members

        days = sessions_of["KR"]
        mem = members(store, days)  # type: ignore[arg-type]
        names = sorted(set().union(*mem.values())) if mem else []
        caps = caps_panel(store, days, names)  # type: ignore[arg-type]
        fr = float_ratios(store, names)  # type: ignore[arg-type]
        kr: dict[date, pd.Series] = {}
        for day, group in mem.items():
            if day in caps.index:
                cap = (caps.loc[day].reindex(sorted(group)) * fr.reindex(sorted(group))).dropna()
                cap = cap[cap > 0]
                if len(cap):
                    kr[day] = cap / cap.sum()
        out["KR"] = kr
    if "US" in books and sessions_of.get("US"):
        from tools.trial_us_index_minus_losers import top_caps

        days = sessions_of["US"]
        wide = top_caps(store, datetime.combine(days[-1], time(23), tzinfo=UTC),  # type: ignore[arg-type]
                        (days[-1] - days[0]).days + 30)
        us: dict[date, pd.Series] = {}
        for day in days:
            if day in wide.index:
                cap = wide.loc[day].dropna()
                cap = cap[cap > 0].nlargest(US_INDEX_WIDTH)
                if len(cap):
                    us[day] = cap / cap.sum()
        out["US"] = us
    return out


def require_index_coverage(data: dict[str, MarketData], keys: list[tuple[str, date]]) -> list[str]:
    """D1b 관문 — 판정 세션의 90% 이상에 지수 구성이 있어야 한다(AZ 규칙). 없으면 rc 6."""
    lines, ok = [], True
    for m in sorted({k for k, _ in keys}):
        days = [d for mm, d in keys if mm == m]
        have = sum(data[m].sets[d].idx_code is not None for d in days)
        share = have / max(1, len(days))
        lines.append(f"{m}: 지수 구성 있는 판정 세션 {have}/{len(days)} ({share:.0%})")
        ok = ok and share >= INDEX_MIN_COVERAGE
    if not ok:
        print("지수 구성 관문 — **미달**\n  " + "\n  ".join(lines), flush=True)
        raise SystemExit(INDEX_EXIT)
    return lines


def require_registered(args: argparse.Namespace, what: str, protocol: Path = PROTOCOL) -> str:
    """사전등록 전에는 결과를 보지 않는다 — 플래그·문서·초안 머리줄(`> **초안`)."""
    if not getattr(args, "i_registered", False):
        raise SystemExit(f"{what} 은 사전등록 뒤에만 돈다 — {protocol} 승인 후 --i-registered 를 명시하라")
    if not protocol.exists():
        raise SystemExit(f"등록 문서가 없다: {protocol}")
    if any(line.startswith("> **초안") for line in protocol.read_text().splitlines()[:5]):
        raise SystemExit(f"{protocol} 이 아직 초안이다(머리줄) — 사용자 승인·해시 고정 뒤에 돈다")
    return hashlib.sha256(protocol.read_bytes()).hexdigest()[:16]


def feature_include(features: str, backbone: str) -> tuple[str, ...]:
    """피처 세트 → kit `include`. 백본과 짝이 안 맞으면 거부, FA+ 묶음을 kit 이 정의하지 않았으면 rc 3."""
    if BACKBONES.get(backbone) != features:
        raise SystemExit(f"백본 {backbone} 은 피처 {BACKBONES.get(backbone)} 로 배웠다 — --features {features} 와 짝이 안 맞는다")
    if features == "FA":
        return tuple(kit.BLOCK_ORDER)
    defined = set(kit.blocks_of((), include=None))
    missing = [g for g in FA_PLUS_EXTRA if g not in defined]
    if missing:
        print(f"FA+ 묶음 {missing} 을 kit(`blocks_of`)이 정의하지 않았다 — FA+ 패널을 만들 수 없다(kit 확장 먼저)", flush=True)
        raise SystemExit(kit.CONTROLS_EXIT)
    return (*kit.BLOCK_ORDER, *FA_PLUS_EXTRA)


def _cache_path(backbone: str, variant: str, seed: int, kind: str, tag: str = "") -> Path:
    """시드 캐시. ``tag`` = 등록 벌의 꼬리표(v1 "" — 기존 이름 그대로 · v2 "v2-") — 두 등록의 예측이 섞이지 않는다."""
    return SEED_CACHE / f"{tag}{backbone}-{variant}-seed{seed}-{kind}.parquet"  # invariant-allow: data-access — 작업 파일


def _load_seed(backbone: str, variant: str, seed: int, tag: str = "") -> WalkResult | None:
    paths = [_cache_path(backbone, variant, seed, k, tag) for k in ("judge", "train")]
    if not all(p.exists() for p in paths):
        return None
    frames = []
    for path in paths:
        f = pd.read_parquet(path)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        f["session"] = pd.to_datetime(f["session"]).dt.date
        frames.append(f)
    return WalkResult(frames[0], frames[1], [], [], 0.0)


def _save_seed(backbone: str, variant: str, seed: int, res: WalkResult, tag: str = "") -> None:
    SEED_CACHE.mkdir(parents=True, exist_ok=True)
    res.judge.to_parquet(_cache_path(backbone, variant, seed, "judge", tag), index=False)  # invariant-allow: data-access — 작업 파일
    res.train.to_parquet(_cache_path(backbone, variant, seed, "train", tag), index=False)  # invariant-allow: data-access — 작업 파일


def _heads_path(backbone: str, variant: str, seed: int, tag: str) -> Path:
    return SEED_CACHE / f"{tag}{backbone}-{variant}-seed{seed}-heads.pkl"  # invariant-allow: data-access — 작업 파일


def _judge_pooled(variant: str, prep: Prepared, controls: dict[str, dict[int, pd.DataFrame]],
                  books: dict[str, kit.MarketBook], seeds: Sequence[int], backbone: str, track: Track, margin: float, *,
                  store: object = None, clock: object = None) -> dict[int, WalkResult]:
    """v2 합동 결정의 판정 경로 — 시드마다 첫 걸음(학습)을 조각으로 남기고(`-heads.pkl`, 내려가도 잇는다), 모두 끝나면 결정·예측."""
    import pickle

    done = {s: _load_seed(backbone, variant, s, track.cache_tag) for s in seeds}
    if all(v is not None for v in done.values()):
        print(f"  {variant} 시드 {len(seeds)}개 캐시 사용 — 다시 학습하지 않는다", flush=True)
        return {s: v for s, v in done.items() if v is not None}
    inputs = {s: with_backbone(prep, controls[backbone][s]) for s in seeds}
    trained: dict[int, WalkResult] = {}
    for s in seeds:
        path = _heads_path(backbone, variant, s, track.cache_tag)
        if path.exists():
            trained[s] = pickle.loads(path.read_bytes())  # 이 도구가 쓴 작업 파일
            print(f"  {variant} seed {s} · 학습 조각 캐시 사용", flush=True)
            continue
        trained[s] = walk(variant, inputs[s], prep.data, books, prep.axis, prep.blocks, s, aug=prep.aug,
                          margin=float("inf"), gains_only=True, accept=track.accept)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(trained[s]))
    preds = walk_pooled(variant, inputs, prep.data, books, prep.axis, prep.blocks, margin=margin, accept=track.accept,
                        aug=prep.aug, store=store, clock=clock, trained=trained)
    for s, res in preds.items():
        _save_seed(backbone, variant, s, res, track.cache_tag)
    return preds


def cmd_judge(args: argparse.Namespace) -> int:
    """본 학습 + 판정. 관문: 등록(거부) → 피처 세트(rc 3) → 굽기(rc 4) → 대조군·백본(rc 3) → 창(rc 5) → 지수 구성(D1b, rc 6).

    ``--track v2`` 는 v2 등록(문서·여백 파일·채택 규칙·캐시 꼬리표·기록 이름)으로 같은 경로를 돈다. 기본 v1 은 해시 고정 판 그대로다."""
    track = TRACKS[getattr(args, "track", "v1")]
    hashed = require_registered(args, "판정", track.protocol)
    from quant_rl_trading.replay.clock import LiveClock
    from quant_rl_trading.store import Store

    variants = VARIANTS if args.variant == "both" else (args.variant,)
    seeds = tuple(SEEDS[: args.seeds])
    include = feature_include(args.features, args.backbone)
    margins = load_margins(variants, track.margin_path, protocol_hash=hashed, backbone=args.backbone, seeds=seeds,
                           accept=_accept_check(track))   # 실자료 shuffle 이 얼린 여백 — 없으면 rc 7
    print(f"=== 시행 D1{'' if track.name == 'v1' else ' ' + track.name} — {track.protocol} (해시 {hashed}) · 변형 {','.join(variants)} · 백본 {args.backbone}·{args.features} "
          f"· 시드 {len(seeds)} ===", flush=True)
    store = Store(root=Path(args.root))
    panel, feats, groups, sessions = kit.load_full_panel(("KR", "US"), root=args.root, store=store, include=include)
    kit.require_full_coverage()
    controls = kit.require_controls(panel, seeds=seeds, arms=("C0", args.backbone))
    books = kit.market_books(store, sessions, panel=panel)
    index_raw = None
    if "D1b" in variants:
        index_raw = index_weights(store, books, {m: sorted(set(panel.loc[panel["market"] == m, "session"]))
                                                 for m in books})
    prep = prepare(panel, feats, groups, sessions, books, index_raw)
    del panel
    since = d1_since(prep)
    # 뒤돌아보는 것은 선정 점수 EMA(20세션)와 직전 재조정(10세션) — 채점 첫 세션 앞에 그만큼 백본 자료가 있어야 한다. rc 5.
    kit.require_full_window(sorted({d for md in prep.data.values() for d in md.days}), since, EMA_WINDOW,
                            label="선정 점수 EMA·직전 재조정")
    keys = [k for first, last in prep.blocks[HYPER.first_block:] for k in judged_keys(prep.data, prep.axis, first, last)]
    if "D1b" in variants:
        print("\n".join(require_index_coverage(prep.data, keys)), flush=True)
    progress = None if args.no_progress else store
    clock = LiveClock()
    verdicts = []
    for variant in variants:
        preds: dict[int, WalkResult] = {}
        if track.accept.pool:
            preds = _judge_pooled(variant, prep, controls, books, seeds, args.backbone, track, margins[variant],
                                  store=progress, clock=clock)
        for seed in seeds:
            if track.accept.pool:
                break
            done = _load_seed(args.backbone, variant, seed, track.cache_tag)
            if done is not None:
                preds[seed] = done
                print(f"  {variant} seed {seed} · 캐시 사용 — 다시 학습하지 않는다", flush=True)
                continue
            inp = with_backbone(prep, controls[args.backbone][seed])
            preds[seed] = walk(variant, inp, prep.data, books, prep.axis, prep.blocks, seed, aug=prep.aug,
                               store=progress, clock=clock, n_seeds=len(seeds), margin=margins[variant],
                               accept=track.accept)
            _save_seed(args.backbone, variant, seed, preds[seed], track.cache_tag)
        verdicts.append(judge_variant(variant, preds, prep.data, controls, books, prep.y, prep.inp.X,
                                      prep.index_of, since, backbone=args.backbone))
        print("\n" + "\n".join(verdicts[-1].lines), flush=True)
    if args.save:
        for v in verdicts:
            rkit.record(store, entity=f"{track.entity}:{v.variant}-{args.backbone}", source="trial_final_dfl",
                        family="selection", digest=hashed, verdict=v.verdict, lines=v.lines[-6:],
                        market="KR,US", run_tag=f"{v.variant}-{args.backbone}")
        print(f"research_trials 기록: selection/{','.join(v.variant for v in verdicts)} · protocol {hashed}")
    return 0


def cmd_precheck(args: argparse.Namespace) -> int:
    """등록 전 점검 — 지수 구성 커버리지. **수익을 보지 않는다.**"""
    from quant_rl_trading.store import Store

    store = Store(root=Path(args.root))
    panel, feats, groups, sessions = kit.load_full_panel(("KR", "US"), root=args.root, store=store)
    books = kit.market_books(store, sessions, panel=panel)
    sessions_of = {m: sorted(set(panel.loc[panel["market"] == m, "session"])) for m in books}
    prep = prepare(panel, feats, groups, sessions, books, index_weights(store, books, sessions_of))
    keys = [k for first, last in prep.blocks[HYPER.first_block:] for k in judged_keys(prep.data, prep.axis, first, last)]
    try:
        print("\n".join(require_index_coverage(prep.data, keys)), flush=True)
    except SystemExit:
        return INDEX_EXIT
    print(f"D1 채점 시작 {d1_since(prep)} · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
    return 0


# ── 합성 자료 · 자기 점검 ───────────────────────────────────────────────────────────────


@dataclass
class Synthetic:
    panel: pd.DataFrame
    feats: list[str]
    groups: dict[str, list[str]]
    sessions: list[date]
    books: dict[str, kit.MarketBook]
    index_raw: dict[str, dict[date, pd.Series]]


def synthetic(seed: int = 0, n_sessions: int = 300, n_entities: int = 90, *, alpha: float = 0.004,
              planted: float = 0.0, box_share: float = 0.7, noise: float = 0.02) -> Synthetic:
    """작은 합성 두 시장. 배선·계약·자기 점검용 — 수치는 뜻이 없다.

    - 국장·미장 달력이 어긋난다(국장 휴장일에 미장만 열린 날). 창은 BOX_END 를 `box_share` 지점에 걸쳐 두 국면이 다 있다.
    - f0 = 지속성 있는 알파(AR(1)) — 대조군(백본)이 이것을 본다.
    - ``planted`` > 0 이면 **심은 신호** f_top: f0 가 그날 상위 8% 인 종목에서만 수익을 가른다(나머지에선 무관).
      나머지 종목에선 반대 부호로 상쇄해 전 종목 IC 는 ~0 이고 상위 k 포트 수익에만 보인다 — 카나리가 찾게 하는 신호다(백본은 f_top 을 안 쓴다).
    - y5 는 수익표에서 만든다(자료끼리 모순이 없게).
    """
    from tools.trial_pooled_rank import rank_gauss

    rng = np.random.default_rng(seed)
    start = BOX_END - timedelta(days=int(n_sessions * box_share * 1.4))
    all_days = [d.date() for d in pd.bdate_range(start, periods=n_sessions + 12)]
    feats = [f"f{i}" for i in range(5)] + ["f_top", "miss_gB"]
    groups = {"score": ["f0", "f1"], "gB": ["f2", "f3", "miss_gB"], "gC": ["f4", "f_top"]}
    frames, books, index_raw = [], {}, {}
    for m, skip in (("KR", 37), ("US", 41)):
        days = [d for i, d in enumerate(all_days) if i % skip != skip - 1]
        ents = [f"{m}:{i:04d}" for i in range(n_entities)]
        T, n = len(days), n_entities

        def ar1(T: int = T, n: int = n, rho: float = 0.9) -> np.ndarray:
            x = np.zeros((T, n))
            x[0] = rng.normal(size=n)
            for t in range(1, T):
                x[t] = rho * x[t - 1] + np.sqrt(1 - rho ** 2) * rng.normal(size=n)
            return x

        x0, xt = ar1(), ar1()
        top = x0 >= np.quantile(x0, 0.92, axis=1, keepdims=True)
        beta = rng.uniform(0.5, 1.5, n)
        mkt = rng.normal(0.0004, 0.01, T)
        # 심은 신호: 상위 8% 에선 +, 나머지에선 −(0.08/0.92 배) — 전 종목 공분산이 정확히 0 이 되게 상쇄한다(IC ~0).
        share = float(top.mean())
        plant = planted * xt * np.where(top, 1.0, -share / (1.0 - share))
        R = beta[None, :] * mkt[:, None] + alpha * x0 + plant + rng.normal(0, noise, (T, n))
        ret = pd.DataFrame(R, index=days, columns=ents)
        X = rng.normal(size=(T, n, len(feats)))
        X[:, :, 0] = x0
        X[:, :, 5] = xt
        X[:, :, 6] = (rng.random((T, n)) < 0.2).astype(float)
        fwd = hold_returns(R, 5)
        for t, d in enumerate(days):
            f = pd.DataFrame(X[t], columns=feats)
            f["entity_id"], f["session"], f["market"] = ents, d, m
            f["y5"] = fwd[t]
            if m == "US":
                f["fund_raw"] = rng.normal(size=n)
                f["has_fund"] = rng.random(n) < 0.8
            frames.append(f)
        trad = {d: set(ents[: int(n * 0.9)]) for d in days} if m == "KR" else None
        books[m] = kit.MarketBook(m, ret, pd.Series(mkt, index=days), 0.0041 if m == "KR" else 0.0025, trad)
        cap = pd.Series(np.exp(rng.normal(0, 1.2, n)), index=ents)
        members = ents[: n // 2]
        index_raw[m] = {d: (cap[members] / cap[members].sum()) for d in days}
    panel = pd.concat(frames, ignore_index=True)
    panel = rank_gauss(panel, [f for f in feats if not f.startswith("miss_")] + ["y5"])
    panel["is_us"] = (panel["market"] == "US").astype(np.float32)
    us = panel["market"] == "US"
    books["US"].fund = panel.loc[us, ["entity_id", "session", "fund_raw", "has_fund"]].reset_index(drop=True)
    sessions = sorted(panel.loc[panel["market"] == "KR", "session"].unique())
    return Synthetic(panel, [*feats, "is_us"], groups, sessions, books, index_raw)


def synthetic_controls(syn: Synthetic, prep: Prepared, seeds: Sequence[int]) -> dict[str, dict[int, pd.DataFrame]]:
    """합성 대조군 — 모든 판정 블록 행에 f0(+f2) + 시드 잡음. **자료**일 뿐 규칙이 아니다(규칙은 kit 이 채점한다)."""
    rows = pd.concat([kit.block_rows(syn.panel, prep.axis, first, last) for first, last in prep.blocks])
    out: dict[str, dict[int, pd.DataFrame]] = {"C0": {}, "C1": {}}
    for s in seeds:
        rng = np.random.default_rng(100 + s)
        base = rows[["entity_id", "session", "market"]].reset_index(drop=True)
        f0 = rows["f0"].to_numpy()
        out["C0"][s] = base.assign(pred=0.1 * (f0 + rng.normal(0, 1.0, len(rows))))
        out["C1"][s] = base.assign(pred=0.1 * (f0 + 0.3 * rows["f2"].to_numpy() + rng.normal(0, 0.8, len(rows))))
    return out


def shuffled(data: dict[str, MarketData], seed: int) -> dict[str, MarketData]:
    """학습 라벨 섞기 — 날마다 **그 날 세트의 종목끼리** 수익을 섞고(보유 수익은 다시 계산), y5 도 세트 안에서 섞는다.
    피처·백본·판정 장부는 그대로다. 이것으로 배운 머리가 판정에서 백본을 이기면 라벨이 아닌 길로 정보가 새는 것이다."""
    rng = np.random.default_rng(seed)
    out = copy.deepcopy(data)
    for md in out.values():
        R = md.R.copy()
        for d, st in md.sets.items():
            p = md.pos_of(d)
            R[p, st.code] = R[p, rng.permutation(st.code)]
            st.y = st.y[rng.permutation(len(st.y))]
        md.R, md.G = R, hold_returns(R)
    return out


SMOKE_HYPER = Hyper(max_epochs=2, patience=1, steps_per_epoch=6, chain_len=3, first_block=6)
#: 자기 점검은 **등록 값 그대로** 돈다 — 첫 판정 블록만 합성 창 길이에 맞춘다(10블록 = 200세션을 앞에 둘 수 없다).
CHECK_HYPER = replace(HYPER, first_block=6)


def self_check(kind: str, seeds: Sequence[int], *, hyper: Hyper = CHECK_HYPER, n_sessions: int = 420,
               n_entities: int = 150, margin: float = 0.0, accept: Accept = ACCEPT_V1,
               shuffle_margin: bool = False, data_seed: int = 7,
               noise: float = 0.02) -> tuple[bool, list[str], list[float]]:
    """합성 자료 자기 점검 — ``canary``(심은 신호를 찾나) · ``shuffle``(섞은 라벨로는 못 이기나). D1a 만(선정 층의 점검).

    ``canary`` 는 **실자료 라벨 섞기로 얼린 채택 여백**을 건 채로 돈다 — 여백이 진짜 신호까지 막으면 카나리가 떨어진다.
    ``shuffle``(합성)은 누설 점검용이고 여백 0 으로 돈다(여백을 얼리는 것은 실자료 `bake_margin` 의 일이다).
    반환: (통과, 줄들, 재학습마다의 내부 검증 이득).

    v2(``accept`` = `ACCEPT_V2`, ``shuffle_margin=True``)는 합성 라벨 섞기에도 **같은 여백을 건다** — 거짓 채택 억제 점검이다.
    ``data_seed``·``noise`` 는 합성 자료의 뽑기·종목 잡음(v1 = 7 · 0.02). 여백을 다른 뽑기에서 굽는 합성 비교(등록 v2 §후보표)에만 바꾼다.
    """
    syn = synthetic(data_seed, n_sessions=n_sessions, n_entities=n_entities, planted=0.012, noise=noise)
    prep = prepare(syn.panel, syn.feats, syn.groups, syn.sessions, syn.books, syn.index_raw)
    controls = synthetic_controls(syn, prep, seeds)
    since = d1_since(prep, hyper)
    preds = {}
    used = margin if (kind != "shuffle" or shuffle_margin) else 0.0
    if accept.pool:
        per_seed = {s: shuffled(prep.data, 1000 + s) for s in seeds} if kind == "shuffle" else None
        preds = walk_pooled("D1a", {s: with_backbone(prep, controls["C1"][s]) for s in seeds}, prep.data, syn.books,
                            prep.axis, prep.blocks, margin=used, accept=accept, aug=prep.aug, hyper=hyper,
                            val_data=prep.data, data_by_seed=per_seed)
    for s in seeds:
        if accept.pool:
            break
        inp = with_backbone(prep, controls["C1"][s])
        train_data = shuffled(prep.data, 1000 + s) if kind == "shuffle" else prep.data
        preds[s] = walk("D1a", inp, train_data, syn.books, prep.axis, prep.blocks, s, aug=prep.aug, hyper=hyper,
                        margin=used, val_data=prep.data, accept=accept)
    v = judge_variant("D1a", preds, prep.data, controls, syn.books, prep.y, prep.inp.X, prep.index_of, since,
                      hyper=hyper)
    delta = float(np.mean(list(v.delta.values())))
    # 심은 신호의 전 종목 IC — ~0 이어야 "IC 로는 안 보이고 포트에만 보이는 신호" 다.
    judged = prep.keys[prep.keys["session"] >= since].copy()
    judged["f_top"] = prep.inp.X[judged.index, prep.index_of["f_top"]].astype(float)
    from quant_rl_trading.analysts.ic import daily_ic

    ic = float(daily_ic(judged.rename(columns={"f_top": "score", "y5": "target"})[["session", "score", "target"]]).mean())
    gains = [log.gain for p in preds.values() for log in p.logs]
    if kind == "canary":
        ok = delta >= CANARY_MIN
        line = (f"카나리(심은 신호, 여백 {fmt_gain(margin, accept, sign=False)}): f_top 전 종목 IC {ic:+.3f} · D1 − C1 {delta:+.1%}p (≥ {CANARY_MIN:.0%}p) "
                f"→ {'통과' if ok else '**불통과** — 배관이 포트 신호를 못 찾거나 여백이 막는다, 판정하지 않는다'}")
    else:
        ok = abs(delta) <= SHUFFLE_TOL
        line = (f"라벨 섞기(합성{', 여백 ' + fmt_gain(used, accept, sign=False) if shuffle_margin else ''}): "
                f"D1 − C1 {delta:+.1%}p (|·| ≤ {SHUFFLE_TOL:.0%}p) → "
                f"{'통과' if ok else '**불통과** — 라벨이 아닌 길로 정보가 샌다'} · "
                f"이득 최댓값 {fmt_gain(max([0.0, *gains]), accept, sign=False)}")
    return ok, [*v.lines[-4:-1], line], gains


def save_margins(margins: dict[str, float], gains: dict[str, list[float]], path: Path = MARGIN_PATH, *,
                 protocol_hash: str, backbone: str, seeds: Sequence[int], accept: Accept | None = None) -> None:
    """여백을 얼린다 — 변형마다 **하나**(시드 전체 · 재학습 전체 이득의 최댓값). 만든 조건(등록 해시·백본·시드)을 같이 적는다."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"margins": margins, "gains": gains, "protocol_hash": protocol_hash,
                                "backbone": backbone, "seeds": list(seeds),
                                "hyper": {k: getattr(HYPER, k) for k in HYPER.__dataclass_fields__},
                                **({"accept": {k: getattr(accept, k) for k in accept.__dataclass_fields__}}
                                   if accept is not None else {}),
                                "source": "실자료 라벨 섞기 — 학습 창 안 내부 검증만(판정 블록 수익 안 봄)"},
                               ensure_ascii=False, indent=1))


def load_margins(variants: Sequence[str], path: Path = MARGIN_PATH, *, protocol_hash: str,
                 backbone: str, seeds: Sequence[int] = (), accept: Accept | None = None) -> dict[str, float]:
    """얼린 여백 — 판정이 읽기만 한다. 없거나, 다른 등록 해시·다른 백본으로 만든 것이면 rc 7(러너가 `shuffle` 을 먼저 돌린다)."""
    import json

    if not path.exists():
        print(f"채택 여백이 없다({path}) — `shuffle --i-registered` 로 실자료에서 먼저 얼려라", flush=True)
        raise SystemExit(CHECK_EXIT)
    got = json.loads(path.read_text())
    stale = [k for k, want in (("protocol_hash", protocol_hash), ("backbone", backbone)) if got.get(k) != want]
    if accept is not None and got.get("accept") != {k: getattr(accept, k) for k in accept.__dataclass_fields__}:
        stale.append("accept")            # v2: 다른 채택 규칙(지표·창·분위·합동)으로 구운 여백은 쓰지 않는다
    missing = [v for v in variants if v not in got.get("margins", {})]
    missing += [f"시드 {s}" for s in seeds if s not in set(got.get("seeds", []))]
    if stale or missing:
        print(f"채택 여백이 이 판정과 안 맞는다({path}) — 어긋난 칸 {stale} · 없는 변형 {missing}. 다시 얼려라", flush=True)
        raise SystemExit(CHECK_EXIT)
    return {v: float(got["margins"][v]) for v in variants}


def bake_margin(variant: str, prep: Prepared, controls: dict[str, dict[int, pd.DataFrame]],
                books: dict[str, kit.MarketBook], seeds: Sequence[int], *, backbone: str = "C1",
                hyper: Hyper = HYPER, parts: Path | None = None, tag: str = "",
                accept: Accept = ACCEPT_V1) -> list[float]:
    """실자료 라벨 섞기 — 학습 라벨(수익·y5)을 세션 안에서 섞어 **판정과 같은 재학습**을 돌리고, 내부 검증 이득만 모은다.

    내부 검증은 학습 창 안이고 **진짜 수익**으로 잰다(`val_data`) — 섞은 라벨로 배운 머리가 우연히 얻는 이득의 크기다.
    `gains_only` 라 판정 블록 예측을 하나도 만들지 않는다(판정 블록 수익을 볼 길이 없다). 반환: 시드 × 재학습의 이득.
    """
    import json

    gains: list[float] = []
    for seed in seeds:
        # 몇 시간짜리라 (변형, 시드)마다 조각을 남긴다 — 내려가도(메모리 가드·재부팅) 끝난 조각은 다시 돌지 않는다.
        # 조각 이름에 등록 해시·백본(`tag`)을 넣어, 다른 등록으로 만든 조각을 섞어 쓰지 않는다.
        part = parts / f"{tag}-{variant}-seed{seed}.json" if parts is not None else None
        if part is not None and part.exists():
            gains += [float(g) for g in json.loads(part.read_text())]
            print(f"  {variant} seed {seed} · 여백 조각 캐시 사용", flush=True)
            continue
        inp = with_backbone(prep, controls[backbone][seed])
        res = walk(variant, inp, shuffled(prep.data, 1000 + seed), books, prep.axis, prep.blocks, seed,
                   aug=prep.aug, hyper=hyper, margin=0.0, val_data=prep.data, gains_only=True, accept=accept)
        got = [log.gain for log in res.logs]
        if part is not None:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_text(json.dumps(got))
        gains += got
    return gains


def _accept_check(track: Track) -> Accept | None:
    """여백 파일의 채택 규칙 대조 — v1 여백 파일은 규칙 칸이 없다(해시 고정 판 그대로 읽는다)."""
    return None if track.name == "v1" else track.accept


def cmd_shuffle(args: argparse.Namespace) -> int:
    """``--synthetic``: 합성 누설 점검(통과/불통과). 기본: **실자료로 채택 여백을 얼린다**(등록 뒤, 판정 블록 수익 안 봄).

    v2 의 ``--synthetic`` 은 **실자료로 얼린 여백을 건 채로** 합성 라벨을 섞는다(거짓 채택 억제 점검 — 등록 뒤에만)."""
    track = TRACKS[getattr(args, "track", "v1")]
    if args.synthetic:
        if track.name == "v1":
            ok, lines, _gains = self_check("shuffle", tuple(range(args.seeds)))
        else:
            hashed = require_registered(args, "합성 라벨 섞기(얼린 여백을 건다)", track.protocol)
            margin = load_margins(("D1a",), track.margin_path, protocol_hash=hashed, backbone=args.backbone,
                                  accept=track.accept)["D1a"]
            ok, lines, _gains = self_check("shuffle", tuple(range(args.seeds)), margin=margin, accept=track.accept,
                                           shuffle_margin=True)
        print("\n".join(lines), flush=True)
        return 0 if ok else 1
    hashed = require_registered(args, "여백 굽기(실자료 라벨 섞기)", track.protocol)
    from quant_rl_trading.store import Store

    seeds = tuple(SEEDS[: args.seeds])
    include = feature_include(args.features, args.backbone)
    store = Store(root=Path(args.root))
    panel, feats, groups, sessions = kit.load_full_panel(("KR", "US"), root=args.root, store=store, include=include)
    kit.require_full_coverage()
    controls = kit.require_controls(panel, seeds=seeds, arms=("C0", args.backbone))
    books = kit.market_books(store, sessions, panel=panel)
    sessions_of = {m: sorted(set(panel.loc[panel["market"] == m, "session"])) for m in books}
    prep = prepare(panel, feats, groups, sessions, books, index_weights(store, books, sessions_of))
    del panel
    margins, gains = {}, {}
    for variant in VARIANTS:
        gains[variant] = bake_margin(variant, prep, controls, books, seeds, backbone=args.backbone,
                                     parts=SEED_CACHE / "margin-parts", tag=f"{track.cache_tag}{hashed}-{args.backbone}",
                                     accept=track.accept)
        null = pooled_means(gains[variant], len(seeds)) if track.accept.pool else gains[variant]
        margins[variant] = margin_from(null, track.accept.margin_q)
        what = ("최댓값" if track.accept.margin_q >= 1.0 else f"상위 {track.accept.margin_q:.0%} 분위") + \
            (f"(재학습별 시드 평균 {len(null)}개)" if track.accept.pool else "")
        print(f"{variant}: 섞은 라벨 이득 {len(gains[variant])}개 · {what} = 채택 여백 "
              f"{fmt_gain(margins[variant], track.accept, sign=False)}", flush=True)
    save_margins(margins, gains, track.margin_path, protocol_hash=hashed, backbone=args.backbone, seeds=seeds,
                 accept=_accept_check(track))
    print(f"채택 여백을 얼렸다 → {track.margin_path} (해시 {hashed})", flush=True)
    return 0


def cmd_canary(args: argparse.Namespace) -> int:
    """합성 카나리 — 실자료로 얼린 D1a 여백을 건 채로(여백이 없으면 rc 7)."""
    track = TRACKS[getattr(args, "track", "v1")]
    hashed = require_registered(args, "카나리(얼린 여백을 건다)", track.protocol)
    margin = load_margins(("D1a",), track.margin_path, protocol_hash=hashed, backbone=args.backbone,
                          accept=_accept_check(track))["D1a"]
    ok, lines, _gains = self_check("canary", tuple(range(args.seeds)), margin=margin, accept=track.accept)
    print("\n".join(lines), flush=True)
    return 0 if ok else 1


def cmd_smoke(args: argparse.Namespace) -> int:
    """합성 자료로 학습·판정 경로 전체. **판정이 아니다** — 수치는 찍지 않고 모양·시간·RSS 만."""
    syn = synthetic(args.seed, n_sessions=420)
    prep = prepare(syn.panel, syn.feats, syn.groups, syn.sessions, syn.books, syn.index_raw)
    seeds = tuple(range(args.seeds))
    controls = synthetic_controls(syn, prep, seeds)
    since = d1_since(prep, SMOKE_HYPER)
    began = monotonic()  # invariant-allow: wallclock
    for variant in VARIANTS:
        preds = {s: walk(variant, with_backbone(prep, controls["C1"][s]), prep.data, syn.books, prep.axis,
                         prep.blocks, s, aug=prep.aug, hyper=SMOKE_HYPER) for s in seeds}
        v = judge_variant(variant, preds, prep.data, controls, syn.books, prep.y, prep.inp.X, prep.index_of,
                          since, hyper=SMOKE_HYPER)
        print(f"{variant}: 예측 {sum(len(p.judge) for p in preds.values()):,}행 · 판정 줄 {len(v.lines)} · "
              f"결론 칸 {'있음' if v.verdict else '없음'} — 배선 확인만(수치 안 찍음)", flush=True)
    print(f"스모크 {(monotonic() - began):.1f}초 · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)  # invariant-allow: wallclock
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--seed", type=int, default=0)
    smoke.add_argument("--seeds", type=int, default=1)
    for name in ("canary", "shuffle"):
        p = sub.add_parser(name)
        p.add_argument("--seeds", type=int, default=len(SEEDS), help="판정 시드 전부")
        p.add_argument("--i-registered", action="store_true")
        p.add_argument("--backbone", choices=sorted(BACKBONES), default="C1")
        p.add_argument("--track", choices=sorted(TRACKS), default="v1", help="등록 벌(v1 해시 고정 · v2 재설계)")
        if name == "shuffle":
            p.add_argument("--synthetic", action="store_true", help="합성 누설 점검만(여백을 얼리지 않는다)")
            p.add_argument("--features", choices=sorted(set(BACKBONES.values())), default="FA")
            p.add_argument("--root", default="data")
    pre = sub.add_parser("precheck")
    pre.add_argument("--root", default="data")
    judge = sub.add_parser("judge")
    judge.add_argument("--i-registered", action="store_true")
    judge.add_argument("--variant", choices=[*VARIANTS, "both"], default="both")
    judge.add_argument("--backbone", choices=sorted(BACKBONES), default="C1")
    judge.add_argument("--features", choices=sorted(set(BACKBONES.values())), default="FA")
    judge.add_argument("--seeds", type=int, default=len(SEEDS))
    judge.add_argument("--root", default="data")
    judge.add_argument("--save", action="store_true")
    judge.add_argument("--track", choices=sorted(TRACKS), default="v1", help="등록 벌(v1 해시 고정 · v2 재설계)")
    judge.add_argument("--no-progress", action="store_true", help="trial_progress 기록을 끈다(기본은 적는다)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"smoke": cmd_smoke, "canary": cmd_canary, "shuffle": cmd_shuffle, "precheck": cmd_precheck,
            "judge": cmd_judge}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
