"""시행 BG — 잔차 RL. docs/protocols/final-model-round-2026-10.md 의 공통 틀 + BG 절대로 잰다.

    .venv/bin/python tools/trial_final_residual_rl.py smoke                    # 합성 자료 배관 점검(아무 때나)
    .venv/bin/python tools/trial_final_residual_rl.py canary --i-registered    # 정답 흘림 → 필요 스텝 산수
    .venv/bin/python tools/trial_final_residual_rl.py pilot  --i-registered    # 파일럿 관문(검증 우위 > 0)
    .venv/bin/python tools/trial_final_residual_rl.py judge  --i-registered    # 본 학습 + 판정 (관문 통과 뒤에만)

**정책이 하는 일은 하나다.** GBM(FA)(=C1) 상위 24 동일가중 **주위로 종목별 기울기 δ 를 낸다.**

    w_i = 1/n + δ_i,   Σδ = 0,   δ_i ≤ +5%p,   w_i ≥ 0   (따라서 δ_i ≥ −1/n)

현금도 종목 선택도 지연도 없다. 이것이 이 회차의 핵심 설계다 — `docs/rl-postmortem.md` 의 다섯 판이 전부
**외울 수 있는 자유도**(현금·지연·유니버스 전체 softmax)에서 죽었고, 교훈 3 은 "규칙이 더 잘하는 것은 규칙에 둔다" 다.
종목 선정은 감독학습(C1), 평활·완충·재조정 주기는 규칙(EMA5·3N·AO C10)이 그대로 한다. 정책에는 **그 24 안의 기울기만** 남긴다.

**기울기를 내는 날도 규칙이 정한다** — 재조정일(10세션마다, AO C10)뿐이다. 보유일에 매일 기울이면 규칙이 일부러 없앤
거래일을 되살리는 셈이고, 교훈 5("회전 비용은 알파의 대부분이다")대로 비용이 먼저 먹는다. 한 결정의 결과는 그 10세션이다.

**관측**(전부 그날 개장 전 · 전부 O(1) · 스케일은 **고정 상수** `SLOT_SCALE`·`CTX_SCALE` + 마지막 ±5 잠금):
그 24종목의 C1 예측(슬롯 안 순위와 rank-gauss 값),
FA 묶음별 평균(rank-gauss 라 이미 ~N(0,1)), **실현 비중 편차**(불변식 7 — 다음 상태는 목표가 아니라 체결된 비중이다),
보유 경과일, 그리고 포트 수준 문맥(낙폭 깊이·지수 20세션 수익·실현변동성·에피소드 진행률). 자료에서 추정한 평균·표준편차를
쓰지 않으니 누수가 원천적으로 없고, 2회차 r5 를 죽인 "환율 원값 1,478" 이 구조적으로 못 들어온다(테스트가 강제).

**보상**(`docs/design/reward-and-risk.md §2` 를 **잔차로** 읽는다):

    r = (r_policy,net − r_control,net) − [ w(d_pol)·Δd_pol − w(d_ctrl)·Δd_ctrl ]

`r_*,net` 은 **각 장부가 자기 회전 × 편도비용을 이미 뺀** 값이다 — 비용을 다시 빼지 않는다(§2 표의 `cost_t` 규약).
기준선은 지수가 아니라 **같은 24 동일가중**이다(시행 L 의 교훈: 배우는 목적 = 채점받는 지표). 수익 항이 잔차이므로
낙폭 항도 잔차다 — 왜 그래야 하는지는 `step_reward` 에 실측과 함께 적었다(공통 낙폭 벌점이 초과수익을 3,500배 덮었다).
낙폭 밴드·가중치는 `store.config` 의 `reward` 섹션에서 읽는다(불변식 10) — 합성 자료 경로만 문서 기본값을 쓴다.
LLM 출력은 들어가지 않는다(불변식 8).

**미래를 안 보는 장치.** 수익은 t+1→t+2 규약(`tools/v2_sim` 과 같다)이고, 결정은 그날 개장 전 정보만 본다. 재조정일 t 의
행동은 그 뒤 10세션의 수익으로만 채점되며, 관측에 들어가는 어떤 칸도 t 이후 자료를 읽지 않는다(테스트가 강제).

**표본이 몇 개인지 먼저 세라.** 10세션 재조정이면 확장 패널(2021-08~2026-06, ~1,230세션)에서 결정은 **123회뿐**이고,
카나리 산수가 요구하는 스텝(수만)이 그 123개를 수만 번 되돌려 본다 — 교훈 4("표본 300세션에서 신경망은 규칙보다
우연을 먼저 찾는다")를 제곱한 자리다. 그래서 **위상(phase) 10벌**을 쓴다: 재조정 시작 요일이 10가지이므로 규칙을
그대로 지키면서 겹치는 결정표를 10벌 만든다(`reward-and-risk.md §2` 의 "겹치는 윈도우"). 위상 0 이 실제 장부이고,
판정은 위상 전부에서 재고 **위상 간 흩어짐**을 운의 크기로 같이 적는다.

**과적합 억제** — 에피소드 시작점 무작위 · 위상 무작위 · 도메인 무작위화(비용 ×U(0.5,2) · 수익 잡음 · 종목 70% 부분표본) ·
0 기울기로의 KL 벌점 · 작은 망(은닉 16) · 엔트로피 보너스 · 내부 검증 조기 종료 · 시드 3 · **파일럿 관문** ·
학습창 대비 판정창 격차 기록 · 기울기가 한계에 붙는 비율 기록.

**알고리즘 — 왜 PPO 를 새로 쓰나.** `quant_rl_trading/allocator/` 의 PPO 는 `LatticeEnv`(Dict 관측 · Dirichlet 비중 ·
지연 머리 · gym)에 묶여 있고, `tools/v2_sim` 머리글이 적은 대로 그 환경은 완충·재조정 주기·노출이 빠져 실전과 다른 게임이라
쓰지 않는다. Dirichlet 은 **합 0 인 기울기**를 표현할 수 없고 지연 머리는 BG 설계에서 빼는 자유도다. 그래서 행동 분포만
작은 가우시안으로 새로 쓰고, **순수 계산 부품은 그대로 가져온다** — `allocator.train.gae` · `explained_variance`(카나리와 같은 식
이어야 비교가 된다), `allocator.reward.RewardParams` · `penalty_weight`(낙폭 밴드의 단일 소스).
그래디언트 클리핑은 **정책·가치를 따로** 한다 — 2회차 r5 에서 전역 클리핑 0.5 가 가치 손실(노름 1,659)에 잘려 정책의 실효
학습률이 3e-9 였다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import monotonic

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

PROTOCOL = Path("docs/protocols/final-model-round-2026-10.md")
#: C1(GBM·FA) 워크포워드 예측 캐시 — 공통 틀 제작자가 쓴다(entity_id · session · pred). BG 는 읽기만 한다.
C1_CACHE = Path("data/_diag/final-round")
#: 판정·학습은 이 날짜부터. 공통 틀의 "측정 10/5 이후" 이고, 사전등록 승인과 **같이** 필요한 조건이다.
# 원래 10/5(BC·G8 과 겹치지 않게 — 일정 잠금). 사용자 9/27 "주말이라 낮에 돌려도 돼" 로 앞당겼다. 진짜 잠금은 등록 문서의 '초안' 머리줄이다.
JUDGE_FROM = date(2026, 9, 28)

TOP_N = 24
#: 재조정 주기(세션) = 채택된 규칙 AO C10. 기울기도 이 날에만 낸다.
EVERY = 10
#: 한 결정이 붙잡는 세션 수 = EVERY. 에피소드는 결정 25개 = 250세션(reward-and-risk §2 "최소 250거래일").
EPISODE_DECISIONS = 25
#: 기울기 상한. 하한은 `−min(TILT_MAX, 1/n)` — 비중 하한 0 때문에 1/24 = 4.17%p 에서 먼저 걸린다.
TILT_MAX = 0.05
ONE_WAY_COST_KR, ONE_WAY_COST_US = 0.0041, 0.0025
ANN = 252

#: 관측이 **뒤돌아보는 최대 세션 수**. 선정 점수의 EMA5 와 지수 문맥 20세션 중 큰 쪽이다.
#: `kit.require_full_window` 에 이 값을 넘긴다 — 0 으로 채워진 창은 아무 경고도 내지 않는다(rank-gauss 뒤 0 은
#: "자료 없음" 이 아니라 "순위 중앙" 으로 보인다). BG 는 관측이 묶음별 평균이라 그 함정이 더 조용하다.
WARMUP_SESSIONS = 20

#: 도메인 무작위화 — 등록 때 고정한 값이다(판정 창에서 고르지 않는다).
COST_JITTER = (0.5, 2.0)
RETURN_NOISE = 0.003
SUBSAMPLE = 0.70

#: 낙폭 벌점 기본값 — **합성 자료 경로에서만** 쓴다. 실자료는 `store.config("reward")` 를 읽는다(불변식 10).
DD_DEFAULT = (0.12, 0.22, 0.30, 0.0, 1.5, 8.0)

#: 관측 칸과 그 **고정** 스케일. 곱한 뒤 전부 |값| ≲ 5 여야 한다(테스트가 강제).
#: 자료에서 추정한 통계를 쓰지 않는 것이 요점이다 — 그래야 누수가 없고 실전과 같은 코드가 된다(불변식 5).
SLOT_NAMES: tuple[str, ...] = ("slot_rank", "pred_z", "weight_dev", "held_days")
SLOT_SCALE = np.array([1.0, 1.0, 1.0, 1.0])
CTX_NAMES: tuple[str, ...] = ("depth", "idx_ret20", "idx_vol20", "progress")
CTX_SCALE = np.array([5.0, 10.0, 5.0, 1.0])
#: 묶음 평균은 rank-gauss 평균이라 이미 ~N(0, 1/√k) — 스케일 1.
BUNDLE_SCALE = 1.0

#: 망이 작아(은닉 16 · 슬롯 24) 스레드를 늘려도 이득이 없고, **머신을 나누지 않는다**(memory `training-shares-no-machine`).
torch.set_num_threads(2)


# --------------------------------------------------------------------------- 보상


@dataclass(frozen=True)
class Bands:
    """낙폭 벌점 계단 한 벌. 실자료는 `store.config`, 합성은 문서 기본값."""

    free: float
    warn: float
    hard: float
    w_free: float
    w_mid: float
    w_hot: float

    @classmethod
    def default(cls) -> Bands:
        return cls(*DD_DEFAULT)

    @classmethod
    def from_store(cls, store, *, as_of) -> Bands:  # type: ignore[no-untyped-def]
        """불변식 10 — 임계치는 `store.config` 에서 읽는다. 화면·학습이 12/22/30 을 따로 들면 어긋난다."""
        from quant_rl_trading.allocator.reward import RewardParams

        p = RewardParams.from_store(store, as_of=as_of)
        return cls(p.drawdown_free, p.drawdown_warn, p.drawdown_hard, p.w_free, p.w_mid, p.w_hot)

    def weight(self, depth: float) -> float:
        """깊이별 한계 벌점. `allocator.reward.penalty_weight` 와 **같은 계단**이어야 한다(테스트가 대조한다)."""
        if depth < self.free:
            return self.w_free
        if depth < self.warn:
            return self.w_mid
        return self.w_hot


def step_reward(port_net: float, ctrl_net: float, pol_prev: float, pol_now: float,
                ctrl_prev: float, ctrl_now: float, bands: Bands) -> float:
    """하루치 보상. `port_net`·`ctrl_net` 은 **이미 비용을 뺀** 순수익이다 — 비용을 여기서 다시 빼지 않는다.

    벌점은 **새로 깊어진 낙폭**에만 붙는다(신저점이 아니면 Δd = 0) — reward-and-risk §2 "조밀 보상" 그대로.

    **§2 를 잔차로 읽는 한 곳.** 수익 항이 "동일가중 대비" 이므로 낙폭 항도 **동일가중이 어차피 낼 벌점을 뺀다**:

        r = (r_pol,net − r_ctrl,net) − [ w(d_pol)·Δd_pol − w(d_ctrl)·Δd_ctrl ]

    안 빼면 안 된다는 것을 실측으로 봤다(합성 스모크 2026-09-27): 장부 공통의 낙폭 벌점이 결정당 −0.07 로
    초과수익 1e-5 를 3,500배 덮어써 advantage 가 전부 시장 낙폭 잡음이 됐다. 1회차의 "보상이 평평했다" 와 같은
    모양이고, 원인은 신호가 없는 것이 아니라 **행동과 무관한 큰 항**이었다. 이 형태는 δ = 0 에서 보상이
    **정확히 0** 이라 그 함정이 구조적으로 없다(테스트가 강제). 밴드·가중치는 그대로 `store.config` 를 읽는다.
    """
    return (port_net - ctrl_net) - (bands.weight(pol_now) * max(0.0, pol_now - pol_prev)
                                    - bands.weight(ctrl_now) * max(0.0, ctrl_now - ctrl_prev))


# --------------------------------------------------------------------------- 행동(기울기) 투영


def tilt_bounds(base: np.ndarray, *, tilt_max: float = TILT_MAX) -> tuple[np.ndarray, float]:
    """슬롯별 하한과 상한. 하한은 **비중 하한 0** 에서 나온다 — 1/24 동일가중이면 −4.17%p 가 먼저 걸린다."""
    return np.maximum(-tilt_max, -base), tilt_max


def project_tilt(raw: np.ndarray, base: np.ndarray, *, tilt_max: float = TILT_MAX, iters: int = 80) -> np.ndarray:
    """`raw ∈ (−1, 1)^n`(tanh 출력) → 제약을 **정확히** 만족하는 기울기 δ. **보유 슬롯만** 넘긴다.

    제약 셋: Σδ = 0 · δ_i ≤ +tilt_max · base_i + δ_i ≥ 0. 투영은 **환경의 일부**다 — 정책의 로그확률은
    투영 전 가우시안 표본으로 계산하므로 야코비안을 걱정하지 않는다.

    방법은 유클리드 투영(공통 이동량 λ 에 대한 이분법)이다. "남은 여유에 비례해 잔차를 나눈다" 는 쉬운 방법은
    **이미 한계에 걸린 슬롯을 되밀어 올린다** — −5%p 를 내라고 한 슬롯이 −3.9%p 로 돌아와, 한계붙음 비율
    (등록 전 점검 ⑤)이 영원히 0 으로 찍힌다. 그 지표가 거짓이 되면 "정책이 뭘 하고 있나" 를 못 본다.
    """
    lo, hi = tilt_bounds(base, tilt_max=tilt_max)
    d0 = tilt_max * np.asarray(raw, dtype=float)
    lam_lo = float(d0.min() - tilt_max) - 1.0          # λ 가 작으면 전부 상한 → 합 > 0
    lam_hi = float(d0.max() + tilt_max) + 1.0          # λ 가 크면 전부 하한 → 합 ≤ 0
    for _ in range(iters):
        mid = 0.5 * (lam_lo + lam_hi)
        if float(np.clip(d0 - mid, lo, hi).sum()) > 0.0:
            lam_lo = mid
        else:
            lam_hi = mid
    d = np.clip(d0 - 0.5 * (lam_lo + lam_hi), lo, hi)
    if abs(float(d.sum())) > 1e-9:                     # 마지막 안전장치 — 못 맞추면 동일가중으로 떨어진다
        d = np.zeros_like(d)
    return d


def saturated(d: np.ndarray, base: np.ndarray, *, tilt_max: float = TILT_MAX, tol: float = 1e-9) -> int:
    """한계에 붙은 슬롯 수 — 등록 전 점검 ⑤("기울기가 ±5%p 에 몇 % 붙는지")가 요구하는 계수."""
    lo, hi = tilt_bounds(base, tilt_max=tilt_max)
    return int(((np.abs(d - lo) < tol) | (np.abs(d - hi) < tol)).sum())


# --------------------------------------------------------------------------- 자료(결정 단위로 미리 굽는다)


@dataclass(frozen=True)
class Prepared:
    """재조정일 D 개로 만든 결정 표. 한 번 구워 두면 스텝이 싸다(2회차의 "스텝 비용 함정" 을 피한다)."""

    days: list[date]
    slots: np.ndarray        # (D, TOP_N) int — 그날 보유 슬롯의 종목 인덱스, 패딩은 −1
    mask: np.ndarray         # (D, TOP_N) bool
    static: np.ndarray       # (D, TOP_N, F) float32 — 예측·묶음 평균 등 그날 개장 전 값
    rets: np.ndarray         # (D, EVERY, TOP_N) float32 — t+1→t+2 규약의 보유 구간 일수익
    ctx: np.ndarray          # (D, 3) float32 — 지수 ret20·vol20 (낙폭·진행률은 굴리며 채운다)
    feat_names: tuple[str, ...]
    entities: list[str]
    #: 결정마다 그 비중으로 드는 EVERY 세션의 날짜. 판정 지표를 **일수익 시계열**로 내려면 필요하다
    #: (`trial_ranker_kit.summarize` 가 일수익을 받는다 — 결정 단위 수익으로는 샤프·MDD·β 가 다른 뜻이 된다).
    hold_days: list[list[object]] = field(default_factory=list)

    @property
    def n_dec(self) -> int:
        return len(self.days)

    @property
    def n_static(self) -> int:
        return self.static.shape[2]


def obs_dim(n_static: int) -> int:
    return n_static + len(SLOT_NAMES) + len(CTX_NAMES)


def build_obs(prep: Prepared, i: int, *, weights: np.ndarray, held_days: np.ndarray, depth: float,
              progress: float, mask: np.ndarray) -> np.ndarray:
    """슬롯 하나당 관측 한 줄. **고정 상수**로만 스케일한다 — 자료에서 추정한 통계는 쓰지 않는다."""
    n = int(mask.sum())
    static = prep.static[i] * BUNDLE_SCALE
    order = np.full(TOP_N, 0.0)
    if n:
        vals = np.where(mask, static[:, 0], -np.inf)
        rank = np.argsort(np.argsort(-vals))[: TOP_N].astype(float)
        order = np.where(mask, (rank / max(1, n - 1) - 0.5) * 2.0, 0.0)
    share = weights.sum()
    dev = np.where(mask & (share > 0), weights / max(share, 1e-12) * max(n, 1) - 1.0, 0.0)
    slot = np.stack([order, np.clip(static[:, 0], -3.0, 3.0), np.clip(dev, -3.0, 3.0),
                     np.clip(held_days / 60.0, 0.0, 3.0)], axis=1) * SLOT_SCALE
    ctx = np.clip(np.array([-abs(depth), prep.ctx[i][0], prep.ctx[i][1], progress]) * CTX_SCALE, -3.0, 3.0)
    out = np.concatenate([np.clip(static, -5.0, 5.0), slot, np.repeat(ctx[None, :], TOP_N, axis=0)], axis=1)
    # **마지막 잠금.** 어떤 칸이 들어와도 O(1) 을 벗어나지 못한다 — 2026-08-27 의 환율 1,478 은 "그 칸이 큰 줄
    # 몰랐다" 가 아니라 **큰 값이 들어올 길이 열려 있었다** 는 사건이다. 새 피처를 넣는 사람이 스케일을 잊어도
    # 가치 헤드 그래디언트가 1,000 대로 튀지 않는다. 자료에서 추정한 통계는 여전히 하나도 안 쓴다.
    out = np.clip(np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0), -5.0, 5.0)
    return np.where(mask[:, None], out, 0.0).astype(np.float32)


# --------------------------------------------------------------------------- 정책망


@dataclass(frozen=True)
class Hyper:
    """**등록 때 고정한다.** 판정 창에서 고르지 않는다(공통 틀 과적합 억제 2번)."""

    hidden: int = 16
    lr: float = 3e-4
    wd: float = 1e-4
    clip: float = 0.2
    epochs: int = 4
    gamma: float = 0.97
    lam: float = 0.95
    ent_coef: float = 0.003
    kl_coef: float = 0.02
    vf_coef: float = 0.5
    grad_policy: float = 0.5
    grad_value: float = 5.0
    init_std: float = 0.25          # tanh 입력의 표준편차 — δ 초기 흩어짐 ≈ ±1.2%p
    episodes_per_update: int = 4


#: 등록 때 고정한 한 벌. 기본 인자로 새 객체를 만들지 않는다(같은 값을 여러 곳이 들면 갈라진다).
HYPER = Hyper()


class TiltPolicy(nn.Module):
    """슬롯 공유 MLP + 횡단면 평균 풀링 → 슬롯별 평균 μ_i, 그리고 상태와 무관한 log σ 하나.

    마지막 층은 **0 초기화** — 출발점이 정확히 "동일가중"(δ = 0)이다. 잔차 정책의 요점이고,
    5회차에서 실제로 작동한 장치다(유효종목 21~27).
    """

    def __init__(self, n_obs: int, hyper: Hyper = HYPER) -> None:
        super().__init__()
        h = hyper.hidden
        self.local = nn.Sequential(nn.Linear(n_obs, h), nn.Tanh(), nn.Linear(h, h), nn.Tanh())
        self.mu = nn.Linear(2 * h, 1)
        self.value = nn.Sequential(nn.Linear(h, h), nn.Tanh(), nn.Linear(h, 1))
        nn.init.zeros_(self.mu.weight)
        nn.init.zeros_(self.mu.bias)
        self.log_std = nn.Parameter(torch.full((1,), float(np.log(hyper.init_std))))
        self.ref_log_std = float(np.log(hyper.init_std))

    def forward(self, obs: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """obs (B, TOP_N, F) · mask (B, TOP_N) → μ (B, TOP_N) · log σ (1,) · V (B,)."""
        h = self.local(obs)
        m = mask.unsqueeze(-1).float()
        pooled = (h * m).sum(1) / m.sum(1).clamp(min=1.0)
        mu = self.mu(torch.cat([h, pooled.unsqueeze(1).expand_as(h)], dim=-1)).squeeze(-1)
        # **가치 머리는 인코더를 detach 한 표현 위에 얹는다.** 공유하면 가치 손실의 그래디언트가 `local` 로 흘러
        # 정책 파라미터 군에 섞이고, 그러면 분리 클리핑이 이름뿐이 된다 — 2회차 r5 가 죽은 자리(가치 노름 1,659
        # 대 정책 19.5 가 전역 클리핑 0.5 에 같이 잘려 정책 실효 학습률 3e-9)가 그대로 돌아온다.
        return mu.masked_fill(~mask, 0.0), self.log_std, self.value(pooled.detach()).squeeze(-1)

    def kl_to_flat(self, mu: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """0 기울기 정책 N(0, σ_ref) 로의 KL. **μ = 0 이고 σ = σ_ref 면 정확히 0** 이다(테스트가 강제)."""
        log_std, ref = self.log_std, self.ref_log_std
        per = ref - log_std + (log_std.exp().pow(2) + mu.pow(2)) / (2.0 * float(np.exp(2 * ref))) - 0.5
        m = mask.float()
        return (per * m).sum() / m.sum().clamp(min=1.0)


def log_prob(mu: torch.Tensor, log_std: torch.Tensor, u: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """가려진 슬롯을 뺀 대각 가우시안 로그확률. **투영 전 표본 u 로 잰다** — 투영은 환경의 일부다."""
    var = (2.0 * log_std).exp()
    lp = -0.5 * ((u - mu) ** 2 / var + 2.0 * log_std + float(np.log(2 * np.pi)))
    return (lp * mask.float()).sum(-1)


def entropy(log_std: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    per = log_std + 0.5 * float(np.log(2 * np.pi * np.e))
    return (per * mask.float()).sum(-1)


# --------------------------------------------------------------------------- 굴리기(장부 둘을 나란히)


@dataclass
class Book:
    """장부 하나. 재조정일에 목표로 맞추고 보유일엔 드리프트만 한다(`v2_sim.simulate` 와 같은 규칙).

    비중은 **종목으로** 들고 있다(슬롯 번호가 아니다). 슬롯 → 종목 대응은 재조정마다 바뀌므로 슬롯 축에 비중을
    남겨 두면 |w − prev| 가 엉뚱한 두 종목을 견주고, **회전이 거짓이 된다** — 회전은 이 프로젝트에서 알파의
    대부분을 먹는 항이라(교훈 5) 거기서 틀리면 판정 전체가 거짓이다.
    """

    prev: dict[int, float] = field(default_factory=dict)
    nav: float = 1.0
    peak: float = 1.0
    turnover: float = 0.0

    def aligned(self, names: np.ndarray) -> np.ndarray:
        """이번 슬롯 배치에 맞춘 현재(체결된) 비중 — 관측의 `weight_dev` 가 읽는 값이다(불변식 7)."""
        return np.array([self.prev.get(int(e), 0.0) if e >= 0 else 0.0 for e in names])

    def rebalance(self, names: np.ndarray, w: np.ndarray, rets: np.ndarray,
                  cost: float) -> tuple[np.ndarray, np.ndarray]:
        """w 로 맞춘 뒤 `len(rets)` 세션 든다. 회전 비용은 재조정일 하루에 문다.

        반환: (세션별 **순수익**, 세션별 낙폭 깊이). 순수익에 비용이 이미 들어 있다 — 보상에서 다시 빼지 않는다.
        """
        keep = {int(e) for e in names if e >= 0}
        self.turnover = float(np.abs(w - self.aligned(names)).sum())
        self.turnover += sum(v for e, v in self.prev.items() if e not in keep)   # 명단에서 빠진 것은 전량 매도
        out, depths = np.empty(len(rets)), np.empty(len(rets))
        cur = w.copy()
        for j, r in enumerate(rets):
            gross = float((cur * r).sum())
            out[j] = gross - (cost * self.turnover if j == 0 else 0.0)
            self.nav *= 1.0 + out[j]
            self.peak = max(self.peak, self.nav)
            depths[j] = self.depth
            drift = cur * (1.0 + r)
            total = float(drift.sum()) + (1.0 - float(cur.sum()))
            cur = drift / total if total > 0 else cur
        self.prev = {int(e): float(cur[k]) for k, e in enumerate(names) if e >= 0 and cur[k] > 0.0}
        return out, depths

    @property
    def depth(self) -> float:
        return max(0.0, 1.0 - self.nav / self.peak)


@dataclass
class Rollout:
    obs: list[np.ndarray] = field(default_factory=list)
    mask: list[np.ndarray] = field(default_factory=list)
    u: list[np.ndarray] = field(default_factory=list)
    logp: list[float] = field(default_factory=list)
    value: list[float] = field(default_factory=list)
    reward: list[float] = field(default_factory=list)
    done: list[float] = field(default_factory=list)
    policy_net: list[float] = field(default_factory=list)
    ctrl_net: list[float] = field(default_factory=list)
    turnover: list[float] = field(default_factory=list)
    sat: list[int] = field(default_factory=list)
    tilt_abs: list[float] = field(default_factory=list)
    abs_turnover: list[float] = field(default_factory=list)
    align: list[float] = field(default_factory=list)   # 카나리용 — 행동과 정답의 정렬도
    #: 세션 단위 순수익 — 판정 지표(샤프·MDD·β·국면)는 결정 단위가 아니라 일수익으로 잰다.
    daily: list[tuple[object, float]] = field(default_factory=list)
    ctrl_dailyseries: list[tuple[object, float]] = field(default_factory=list)

    def stack(self) -> dict[str, np.ndarray]:
        return {"obs": np.stack(self.obs), "mask": np.stack(self.mask), "u": np.stack(self.u),
                "logp": np.array(self.logp), "value": np.array(self.value),
                "reward": np.array(self.reward), "done": np.array(self.done)}


def episode(net: TiltPolicy, prep: Prepared, start: int, length: int, *, rng: np.random.Generator,
            gen: torch.Generator, bands: Bands, cost: float, randomize: bool, deterministic: bool,
            out: Rollout, oracle: np.ndarray | None = None) -> None:
    """에피소드 하나. 결정마다 (관측 → 기울기 → 10세션 보유 → 보상) 를 `out` 에 쌓는다.

    **도메인 무작위화**는 `randomize` 일 때만: 비용 배수 ×U(0.5,2) 는 호출자가 이미 `cost` 에 넣었고,
    여기서는 수익 잡음과 종목 70% 부분표본을 건다. 판정·검증 경로는 원자료를 그대로 쓴다.
    """
    stop = min(start + length, prep.n_dec)
    pol, ctrl = Book(), Book()
    held_by_entity: dict[int, float] = {}          # 종목별 보유 경과 세션 — 슬롯 번호는 재조정마다 바뀐다
    for i in range(start, stop):
        names = prep.slots[i]
        mask = prep.mask[i].copy()
        if randomize and mask.sum() > 4:
            keep = rng.random(TOP_N) < SUBSAMPLE
            if keep[mask].sum() >= 4:
                mask &= keep
        n = int(mask.sum())
        if n < 2:
            continue
        base = np.where(mask, 1.0 / n, 0.0)
        rets = prep.rets[i].astype(float)
        if randomize:
            rets = rets + rng.normal(0.0, RETURN_NOISE, rets.shape)
        rets = np.where(mask[None, :], rets, 0.0)
        held = np.array([held_by_entity.get(int(e), 0.0) if e >= 0 else 0.0 for e in names])
        obs = build_obs(prep, i, weights=pol.aligned(names), held_days=held, depth=pol.depth,
                        progress=(i - start) / max(1, length - 1), mask=mask)
        if oracle is not None:
            obs = np.concatenate([obs, np.where(mask, oracle[i], 0.0)[:, None].astype(np.float32)], axis=1)
        with torch.no_grad():
            t_obs = torch.from_numpy(obs).unsqueeze(0)
            t_mask = torch.from_numpy(mask).unsqueeze(0)
            mu, log_std, value = net(t_obs, t_mask)
            u = mu if deterministic else mu + log_std.exp() * torch.randn(mu.shape, generator=gen)
            u = u * t_mask.float()
            lp = log_prob(mu, log_std, u, t_mask)
        u_np = u.squeeze(0).numpy()
        # **보유 슬롯만** 투영한다 — 가려진 슬롯을 끼워 넣고 나중에 0 으로 지우면 합 0 이 깨진다(= 현금·레버리지).
        active = np.flatnonzero(mask)
        delta = np.zeros(TOP_N)
        delta[active] = project_tilt(np.tanh(u_np[active]), base[active])
        pol_before, ctrl_before = pol.depth, ctrl.depth
        p_daily, p_depths = pol.rebalance(names, base + delta, rets, cost)
        c_daily, c_depths = ctrl.rebalance(names, base, rets, cost)
        # 보상은 하루 단위로 쌓고 결정 하나에 합친다 — 벌점은 **새로 깊어진 낙폭**에만 붙는다(조밀 보상).
        r = 0.0
        for j in range(len(p_daily)):
            pp = pol_before if j == 0 else float(p_depths[j - 1])
            cp = ctrl_before if j == 0 else float(c_depths[j - 1])
            r += step_reward(float(p_daily[j]), float(c_daily[j]), pp, float(p_depths[j]), cp, float(c_depths[j]), bands)
        out.obs.append(obs)
        out.mask.append(mask)
        out.u.append(u_np)
        out.logp.append(float(lp))
        out.value.append(float(value))
        out.reward.append(r)
        out.done.append(1.0 if i == stop - 1 else 0.0)
        out.policy_net.append(float(np.prod(1.0 + p_daily) - 1.0))
        out.ctrl_net.append(float(np.prod(1.0 + c_daily) - 1.0))
        out.turnover.append(pol.turnover - ctrl.turnover)     # 정책이 **더** 낸 회전 — 기울기의 값이 여기서 갈린다
        out.abs_turnover.append(pol.turnover)
        if i < len(prep.hold_days):
            for d, pv, cv in zip(prep.hold_days[i], p_daily, c_daily, strict=True):
                out.daily.append((d, float(pv)))
                out.ctrl_dailyseries.append((d, float(cv)))
        out.sat.append(saturated(delta[active], base[active]))
        out.tilt_abs.append(float(np.abs(delta).sum()))
        if oracle is not None:
            out.align.append(float((delta * np.where(mask, oracle[i], 0.0)).sum()))
        held_by_entity = {int(e): held[k] + EVERY for k, e in enumerate(names) if e >= 0 and mask[k]}


def collect(net: TiltPolicy, prep: Prepared, span: range, *, rng: np.random.Generator, gen: torch.Generator,
            bands: Bands, cost: float, episodes: int, randomize: bool, oracle: np.ndarray | None = None,
            out: Rollout | None = None) -> Rollout:
    """에피소드 몇 개를 모은다. **시작점은 매번 무작위** — 1회차가 같은 시작점을 714회 반복한 실패를 막는다."""
    out = Rollout() if out is None else out
    lo, hi = span.start, span.stop
    for _ in range(episodes):
        length = min(EPISODE_DECISIONS, max(2, hi - lo))
        start = int(rng.integers(lo, max(lo + 1, hi - length + 1)))
        c = cost * float(rng.uniform(*COST_JITTER)) if randomize else cost
        episode(net, prep, start, length, rng=rng, gen=gen, bands=bands, cost=c, randomize=randomize,
                deterministic=False, out=out, oracle=oracle)
    return out


def _phases(prep: Prepared | list[Prepared], span: range | list[range]) -> tuple[list[Prepared], list[range]]:
    """**위상(phase) 이 여러 개일 수 있다.** 10세션 재조정은 시작 요일이 10가지라, 같은 규칙을 지키면서도
    결정표를 10벌 만들 수 있다 — 겹치는 윈도우로 표본을 늘리라는 `reward-and-risk.md §2` 그대로다.
    이게 없으면 확장 패널(1,230세션)에서 결정이 **123회뿐**이고, 필요 스텝(카나리 산수)이 그 123개를 수만 번 되돌려 본다.
    """
    preps = prep if isinstance(prep, list) else [prep]
    spans = span if isinstance(span, list) else [span] * len(preps)
    return preps, spans


def evaluate_span(net: TiltPolicy, prep: Prepared, span: range, *, bands: Bands, cost: float,
                  oracle: np.ndarray | None = None) -> tuple[Rollout, dict[str, float]]:
    """검증·판정 경로 — 무작위화 없음 · 평균 행동(deterministic) · 구간 전체를 한 에피소드로."""
    out = Rollout()
    episode(net, prep, span.start, span.stop - span.start, rng=np.random.default_rng(0),
            gen=torch.Generator().manual_seed(0), bands=bands, cost=cost, randomize=False,
            deterministic=True, out=out, oracle=oracle)
    if not out.reward:
        return out, {"edge": 0.0, "n": 0.0}
    pol = np.array(out.policy_net)
    ctrl = np.array(out.ctrl_net)
    stats = {
        "edge": float(np.mean(pol - ctrl)),                       # 결정당 초과(비용 후) — 파일럿 관문이 보는 값
        "reward": float(np.mean(out.reward)),
        "n": float(len(pol)),
        "policy_ann": float((np.prod(1.0 + pol) ** (ANN / max(1, len(pol) * EVERY))) - 1.0),
        "ctrl_ann": float((np.prod(1.0 + ctrl) ** (ANN / max(1, len(ctrl) * EVERY))) - 1.0),
        "sat_share": float(np.mean(out.sat) / TOP_N),
        "tilt_abs": float(np.mean(out.tilt_abs)),
    }
    return out, stats


def evaluate_phases(net: TiltPolicy, preps: list[Prepared], spans: list[range], *, bands: Bands, cost: float,
                    oracles: list[np.ndarray | None] | None = None) -> dict[str, float]:
    """위상 전부에서 재고 **결정 수로 가중평균**한다. 한 위상만 보면 재조정 요일 운을 성과로 읽는다."""
    rows = []
    for i, (prep, span) in enumerate(zip(preps, spans, strict=True)):
        _r, s = evaluate_span(net, prep, span, bands=bands, cost=cost,
                              oracle=None if oracles is None else oracles[i])
        if s["n"] > 0:
            rows.append(s)
    if not rows:
        return {"edge": 0.0, "n": 0.0, "sat_share": 0.0, "tilt_abs": 0.0}
    n = np.array([r["n"] for r in rows])
    out = {k: float(np.average([r[k] for r in rows], weights=n)) for k in rows[0] if k != "n"}
    out["n"] = float(n.sum())
    out["phase_spread"] = float(np.std([r["edge"] for r in rows]))    # 위상 간 흩어짐 — 운의 크기
    return out


# --------------------------------------------------------------------------- PPO


def ppo_update(net: TiltPolicy, opt: torch.optim.Optimizer, roll: Rollout, hyper: Hyper) -> dict[str, float]:
    """한 번의 PPO 업데이트. **정책·가치 그래디언트를 따로 클리핑**한다(2회차 r5 의 뿌리)."""
    from quant_rl_trading.allocator.train import explained_variance, gae

    data = roll.stack()
    adv, ret = gae(rewards=data["reward"][:, None], values=data["value"][:, None], dones=data["done"][:, None],
                   bootstrap=np.zeros((len(data["reward"]), 1)), last_value=np.zeros(1),
                   gamma=hyper.gamma, lam=hyper.lam)
    adv, ret = adv[:, 0], ret[:, 0]
    adv_n = (adv - adv.mean()) / (adv.std() + 1e-8)
    obs = torch.from_numpy(data["obs"])
    mask = torch.from_numpy(data["mask"])
    u = torch.from_numpy(data["u"].astype(np.float32))
    old = torch.from_numpy(data["logp"].astype(np.float32))
    t_adv = torch.from_numpy(adv_n.astype(np.float32))
    t_ret = torch.from_numpy(ret.astype(np.float32))
    log: dict[str, float] = {}
    for _ in range(hyper.epochs):
        mu, log_std, value = net(obs, mask)
        lp = log_prob(mu, log_std, u, mask)
        ratio = (lp - old).exp()
        pg = -torch.min(ratio * t_adv, ratio.clamp(1 - hyper.clip, 1 + hyper.clip) * t_adv).mean()
        ent = entropy(log_std, mask).mean()
        kl_flat = net.kl_to_flat(mu, mask)
        vloss = (value - t_ret).pow(2).mean()
        loss = pg - hyper.ent_coef * ent + hyper.kl_coef * kl_flat + hyper.vf_coef * vloss
        opt.zero_grad()
        loss.backward()
        pol_params = [p for n_, p in net.named_parameters() if not n_.startswith("value.")]
        val_params = [p for n_, p in net.named_parameters() if n_.startswith("value.")]
        gp = float(nn.utils.clip_grad_norm_(pol_params, hyper.grad_policy))
        gv = float(nn.utils.clip_grad_norm_(val_params, hyper.grad_value))
        opt.step()
        log = {"pg": float(pg.detach()), "vloss": float(vloss.detach()), "ent": float(ent.detach()),
               "kl_flat": float(kl_flat.detach()), "grad_policy": gp, "grad_value": gv,
               "log_std": float(net.log_std.detach())}
    log["ev"] = explained_variance(data["value"], ret)
    log["reward"] = float(data["reward"].mean())
    return log


@dataclass
class Trained:
    net: TiltPolicy
    best_step: int
    best_edge: float
    log: list[dict[str, float]]
    state: dict[str, torch.Tensor] | None = None
    updates: int = 0

    @property
    def budget_exhausted(self) -> bool:
        """**검증이 끝까지 안 꺾였다** = 예산을 다 썼다. BG 의 조기 종료는 break 가 아니라 최고 체크포인트
        선택이라 "조기 종료 N회" 카운터가 없다 — 대신 이것을 적는다. `best_step == updates` 면 마지막 평가가
        최고였다는 뜻이고, 그건 "조기 종료가 잘 걸린다" 가 아니라 **예산이 모자랐다** 는 신호다.
        틀리게 읽으면 과적합 진단이 **반대로** 읽힌다(공통 틀 담당의 off-by-one 지적과 같은 자리, 2026-09-27).
        """
        return self.updates > 0 and self.best_step >= self.updates


def train_one(prep: Prepared | list[Prepared], fit: range | list[range], valid: range | list[range], *,
              seed: int, updates: int, bands: Bands, cost: float,
              hyper: Hyper = HYPER, eval_every: int = 10, n_oracle: int = 0,
              oracle: np.ndarray | list[np.ndarray] | None = None, verbose: bool = True) -> Trained:
    """시드 하나. 조기 종료는 **학습창 안의 내부 검증**으로만 한다(판정 창을 보지 않는다)."""
    preps, fits = _phases(prep, fit)
    _p, valids = _phases(prep, valid)
    oracles = oracle if isinstance(oracle, list) else [oracle] * len(preps)
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    gen = torch.Generator().manual_seed(seed)
    net = TiltPolicy(obs_dim(preps[0].n_static) + n_oracle, hyper)
    opt = torch.optim.Adam(net.parameters(), lr=hyper.lr, weight_decay=hyper.wd)
    best = Trained(net, 0, -1e9, [], None, updates)
    for step in range(1, updates + 1):
        roll = Rollout()
        for _ in range(hyper.episodes_per_update):
            ph = int(rng.integers(len(preps)))          # 위상도 무작위 — 같은 결정표를 반복해 외우는 것을 막는다
            collect(net, preps[ph], fits[ph], rng=rng, gen=gen, bands=bands, cost=cost,
                    episodes=1, randomize=True, oracle=oracles[ph], out=roll)
        if not roll.reward:
            continue
        info = ppo_update(net, opt, roll, hyper)
        info["step"] = float(step)
        if step % eval_every == 0 or step == updates:
            vstats = evaluate_phases(net, preps, valids, bands=bands, cost=cost, oracles=oracles)
            info["valid_edge"] = vstats["edge"]
            info["valid_sat"] = vstats["sat_share"]
            if vstats["edge"] > best.best_edge:
                best = Trained(net, step, vstats["edge"], best.log,
                               {k: v.detach().clone() for k, v in net.state_dict().items()}, updates)
            if verbose:
                print(f"    시드 {seed} 업데이트 {step}: 보상 {info['reward']:+.5f} · 검증 우위 {vstats['edge']:+.6f} "
                      f"· 한계붙음 {vstats['sat_share']:.0%} · KL0 {info['kl_flat']:.4f} · |g|정책 {info['grad_policy']:.2f} "
                      f"가치 {info['grad_value']:.2f} · EV {info['ev']:+.2f}", flush=True)
        best.log.append(info)
    if best.state is not None:
        net.load_state_dict(best.state)
    best.net = net
    return best


# --------------------------------------------------------------------------- 파일럿 관문 · 필요 스텝 산수


#: 판정을 앞으로 걸어가며 몇 번 다시 적합하는가. 2 면 적합 둘 · 채점 구간 둘(둘 다 안 본 구간)이다.
#: 블록마다(41회) 다시 적합하면 예산이 41배가 된다 — 그건 이 자리에서 불가능하다(0.2초/업데이트).
FOLDS = 2
#: 채점하는 결정의 비율(뒤에서부터). 0.60 이면 앞 40%는 첫 적합에만 쓰이고 채점받지 않는다.
JUDGE_SHARE = 0.60
#: 적합 끝과 채점 시작 사이에 비우는 **결정** 수. 결정 하나가 EVERY 세션이므로 1 이면 GAP(10세션)과 같다.
GAP_DECISIONS = 1


def walk_folds(n_dec: int, *, folds: int = FOLDS, judge_share: float = JUDGE_SHARE,
               gap: int = GAP_DECISIONS) -> list[tuple[range, range]]:
    """앞으로 걸어가는 (적합, 채점) 짝. **채점되는 결정은 전부 그 적합이 보지 못한 구간이다.**

    왜 필요한가: C0·C1 은 블록마다 앞만 보고 예측하는 워크포워드다. BG 가 앞 80%로 배우고 100%를 채점받으면
    **자기 학습 구간을 같이 채점받는다** — 등록의 "학습 구간 성과는 판정에 쓰지 않는다" 가 깨지고, 비교가
    같은 것을 재지 않는다(공통 틀 담당 지적, 2026-09-27).

    적합은 **확장창**이다(fold 2 의 적합은 fold 1 의 채점 구간까지 포함한다 — 그때는 이미 지나간 자료다).
    적합 끝과 채점 시작 사이에 `gap` 결정(= GAP 10세션)을 비운다.
    """
    if n_dec < 4:
        return []
    first = max(gap + 1, round(n_dec * (1.0 - judge_share)))
    if first >= n_dec:
        return []
    edges = np.linspace(first, n_dec, folds + 1).round().astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        if b - a < 1 or a - gap < 1:
            continue
        out.append((range(0, int(a) - gap), range(int(a), int(b))))
    return out


def pilot_gate(prep: Prepared | list[Prepared], fit: range | list[range], valid: range | list[range], *,
               bands: Bands, cost: float, updates: int,
               seeds: tuple[int, ...] = (0, 1, 2), hyper: Hyper = HYPER) -> tuple[bool, list[Trained]]:
    """**검증 우위 > 0 이 아니면 본 학습을 하지 않는다.** 3·4회차가 이 관문으로 수십 시간을 아꼈다."""
    runs = [train_one(prep, fit, valid, seed=s, updates=updates, bands=bands, cost=cost, hyper=hyper) for s in seeds]
    edges = [r.best_edge for r in runs]
    ok = max(edges) > 0.0
    used = sum(r.budget_exhausted for r in runs)
    if used:
        print(f"  주의 — 시드 {used}/{len(runs)} 이 마지막 평가에서 최고였다(예산 소진). "
              "조기 종료가 걸린 것이 아니라 더 돌 여지가 있다는 뜻이다", flush=True)
    print("\n파일럿 관문 — 시드별 최고 검증 우위 " + " · ".join(f"{e:+.6f}" for e in edges)
          + f" → {'통과(본 학습으로)' if ok else '불통과 — 본 학습 안 한다(재료 문제)'}", flush=True)
    # **통과의 크기를 같이 적는다.** 기준은 등록된 대로 "> 0" 이지만, 시드 흩어짐만한 우위는 잡음이다.
    # 3·4회차의 실패는 −0.00005 였고, 그 자릿수의 +부호는 "배웠다" 가 아니다(교훈 6: 판정 지표를 먼저 의심한다).
    spread = float(np.std(edges)) if len(edges) > 1 else float("nan")
    if ok and (not np.isnan(spread)) and max(edges) < spread:
        print(f"  경고 — 우위 {max(edges):+.6f} 가 시드 흩어짐 {spread:.6f} 보다 작다. 통과는 잡음일 수 있다", flush=True)
    return ok, runs


#: 필요 스텝 상수. 카나리 실측(2026-08-23)으로 눈금을 맞췄다: 정렬도 r=0.043 에 필요 스텝 ≈ 110,000 ⇒ c = 110,000 × r².
#: "못 배운다" 를 말하기 전에 **필요한 스텝 수를 계산한다"(rl-postmortem §1).
STEP_CONST = 110_000 * 0.043 ** 2


def needed_steps(r: float) -> float:
    """신호 세기 r(정렬도)에서 필요한 그래디언트 스텝 수 ≈ c / r²."""
    return float("inf") if abs(r) < 1e-9 else STEP_CONST / r ** 2


def alignment(roll: Rollout) -> tuple[float, float]:
    """**중심화한 보상** ↔ 행동의 정답 정렬도 r 과 그 t 값. 카나리에서만 잰다(정답 칸이 있어야 한다).

    사후분석 §1 은 advantage 로 쟀다. 여기서는 그 대리로 중심화한 보상을 쓴다 — 결정 하나가 10세션을 붙잡고
    γ=0.97 이라 advantage 의 대부분이 그 스텝의 보상이다. **대리를 쓴다는 사실을 적어 둔다**: 필요 스텝 수는
    자릿수 판단용이고, 소수점 두 자리를 믿는 값이 아니다.
    """
    if len(roll.align) < 10:
        return 0.0, 0.0
    a = np.array(roll.reward) - np.mean(roll.reward)
    g = np.array(roll.align)
    if a.std() < 1e-12 or g.std() < 1e-12:
        return 0.0, 0.0
    r = float(np.corrcoef(a, g)[0, 1])
    n = len(a)
    return r, float(r * np.sqrt(max(1, n - 2)) / max(1e-9, np.sqrt(1 - r ** 2)))


# --------------------------------------------------------------------------- 합성 자료(스모크·테스트)


def synthetic(seed: int = 0, n_dec: int = 120, n_bundles: int = 4, *, alpha: float = 0.0) -> Prepared:
    """결정 n_dec 개의 합성 결정표. `alpha > 0` 이면 첫 묶음이 실제로 수익을 예측한다 —
    **배관 점검용**이고 여기서 이겼다는 사실은 판정에 쓰지 않는다(`constant-feature-eats-weight` 교훈)."""
    rng = np.random.default_rng(seed)
    days = [d.date() for d in pd.bdate_range("2022-01-03", periods=n_dec * EVERY, freq="B")[::EVERY]]
    entities = [f"S{i}" for i in range(80)]
    # 명단은 **대부분 유지되고 몇 개만 바뀐다**(완충 3N 의 모양). 매번 새 명단으로 만들면 회전이 항상 2.0 이라
    # 비용이 모든 것을 덮고, 회전을 보는 테스트가 아무것도 못 잡는다.
    cur = list(rng.permutation(len(entities))[:TOP_N])
    rows = []
    for _ in range(n_dec):
        rows.append(list(cur))
        outs = rng.choice(TOP_N, size=3, replace=False)
        pool = [e for e in range(len(entities)) if e not in cur]
        for k, e in zip(outs, rng.choice(pool, size=3, replace=False), strict=True):
            cur[int(k)] = int(e)
    slots = np.array(rows)
    mask = np.ones((n_dec, TOP_N), dtype=bool)
    static = rng.normal(0.0, 1.0, (n_dec, TOP_N, 1 + n_bundles)).astype(np.float32)
    signal = static[:, :, 1] if n_bundles else np.zeros((n_dec, TOP_N), np.float32)
    market = rng.normal(0.0004, 0.010, (n_dec, EVERY, 1))
    rets = (market + rng.normal(0.0, 0.012, (n_dec, EVERY, TOP_N))
            + alpha * signal[:, None, :] / EVERY).astype(np.float32)
    ctx = np.stack([rng.normal(0.0, 0.05, n_dec), np.abs(rng.normal(0.18, 0.04, n_dec))], axis=1).astype(np.float32)
    names = ("pred", *(f"bundle{i}" for i in range(n_bundles)))
    all_days = [d.date() for d in pd.bdate_range("2022-01-03", periods=n_dec * EVERY, freq="B")]
    holds = [all_days[i * EVERY: (i + 1) * EVERY] for i in range(n_dec)]
    return Prepared(days, slots, mask, static, rets, ctx, names, entities, holds)


def oracle_column(prep: Prepared) -> np.ndarray:
    """정답 칸 — 그 결정의 보유 구간 수익 부호. **카나리 전용**이고 판정 경로엔 존재하지 않는다."""
    fwd = np.prod(1.0 + prep.rets, axis=1) - 1.0
    return np.sign(fwd - fwd.mean(axis=1, keepdims=True)).astype(np.float32)


# --------------------------------------------------------------------------- 실자료 경로(공통 틀에 붙는다)


def _kit():  # type: ignore[no-untyped-def]
    """공통 틀 `tools/final_round_kit.py`. **고치지 않는다** — 없으면 여기서 멈춘다."""
    try:
        from tools import final_round_kit as kit
    except ImportError as exc:                      # pragma: no cover — 실자료 경로
        raise SystemExit("공통 틀 tools/final_round_kit.py 가 없다 — BG 는 그것을 읽기만 한다") from exc
    return kit


def c1_average(frames: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """C1(GBM·FA) 워크포워드 예측을 **시드 평균** 한 벌로 만든다.

    BG 의 장부는 "C1 상위 24 동일가중" 이므로 기준선은 한 벌이어야 한다. C1 의 시드 흩어짐까지 BG 의 성과로
    섞이면 무엇을 재는지 알 수 없다(`seed-noise-3pp` 교훈) — 그래서 시드 평균을 기준선으로 고정한다.
    BG 자신의 시드 3개는 정책 시드다. (등록 문서에 적어야 하는 선택이다.)
    """
    parts = []
    for frame in frames.values():
        one = frame[["entity_id", "session", "market", "pred"]].copy()
        one["session"] = pd.to_datetime(one["session"]).dt.date
        # **원값이 아니라 세션별 백분위를 평균한다.** GBM pred 는 시드마다 스케일이 달라 원값 평균은 분산 큰
        # 시드가 더 끌어당긴다(시행 AB 가 순위 평균을 쓴 이유). 상위 24 를 고르는 데는 순위만 필요하다.
        one["pred"] = one.groupby(["market", "session"])["pred"].rank(pct=True)
        parts.append(one)
    if not parts:
        raise SystemExit("C1 예측이 비었다 — kit.require_controls 가 먼저다")
    return pd.concat(parts, ignore_index=True).groupby(["entity_id", "session", "market"], as_index=False)["pred"].mean()


def axis_report(market: str, usable: list, panel: pd.DataFrame, wide: pd.DataFrame, book) -> str:  # type: ignore[no-untyped-def]
    """결정·장부 축이 **그 시장 패널의 세션을 다 덮는지** 한 줄로. 덮지 못하면 어디서 잃었는지 말한다.

    잃는 자리는 셋뿐이다: 대조군 예측에 없는 세션 · 수익표에 없는 세션 · 둘 다 있는데 창 끝이라 못 쓰는 세션.
    셋을 갈라 적어야 "국장 축으로 미장을 끊었다" 를 발견할 수 있다 — 합계만 보면 정상처럼 보인다.
    """
    have = sorted(set(panel["session"])) if "session" in panel.columns else []
    if not have:
        return f"{market} 축: 패널 세션 없음 — 확인 불가"
    no_pred = [d for d in have if d not in set(wide.index)]
    no_ret = [d for d in have if d in set(wide.index) and d not in set(book.ret.index)]
    tail = len(usable) - max(0, len(usable) - EVERY + 1)
    return (f"{market} 축: 패널 {len(have)}세션 → 쓸 수 있는 {len(usable)}세션 "
            f"(예측 없음 {len(no_pred)} · 수익 없음 {len(no_ret)} · 창 끝 {tail}) "
            f"{'✓' if not no_ret else '⚠ 수익표에 없는 세션이 있다 — 달력이 어긋났는지 본다'}")


def prepare_market(market: str, wide: pd.DataFrame, book, panel: pd.DataFrame,  # type: ignore[no-untyped-def]
                   groups: dict[str, list[str]]) -> list[Prepared]:
    """선정 점수 + 규칙(완충 3N · 10세션 재조정)으로 한 시장의 결정표를 **위상 10벌** 굽는다.

    `wide` 는 **그 시장의 선정 점수**(세션 × 종목, 평활까지 끝난 것)다 — 국장은 C1 pred 의 EMA5, 미장은
    `kit.us_m1_wide`(시행 AT 채택 M1 합성 뒤 EMA5). **합성을 군마다 달리 쓰면 그 차이가 BG 의 우위로 잡힌다** —
    미장에서 C1 pred 를 바로 정렬하면 BG 의 기준 포트만 다른 규칙이 된다(공통 틀 담당 지적, 2026-09-27).

    위상이란 10세션 재조정의 **시작 요일**이다. 위상 p 의 장부는 세션 p, p+10, p+20 … 에 재조정한다 — 규칙은
    똑같고 결정표만 10벌이 된다. 위상 0 이 실제 장부이고, 나머지 아홉은 학습 표본을 늘리는 겹치는 윈도우다
    (`reward-and-risk.md §2` 의 "겹치는 윈도우 + 다중 시드"). 판정은 위상 전부에서 재고 흩어짐을 같이 적는다.

    피처는 FA 전부가 아니라 **묶음별 평균**이다 — 결정이 1,200개뿐인 자리에 80열을 넣으면 정책이 먼저 외운다.
    묶음 목록은 `kit.load_full_panel` 이 준 `groups` 를 그대로 쓴다 — 표지(`miss_*`)는 평균에서 빼고 `cover` 한 칸으로 따로 넣는다.
    """
    from tools.trial_selection_smoothing import pick_mult

    stat = panel.set_index(["session", "entity_id"])
    entities = sorted(set(wide.columns))
    eidx = {e: i for i, e in enumerate(entities)}
    # **시간 축은 그 시장의 달력이다.** `kit.load_full_panel` 의 `sessions` 는 국장 세션이라(블록 경계를 AA 와
    # 맞추려고) 그걸 미장 결정·장부 축으로 쓰면 **국장 휴장일에만 열린 미장 세션이 조용히 빠진다** — 그러면
    # 미장 슬리브의 보유일이 실제보다 짧아지고 비용·드리프트가 과소 계상된다(공통 틀 담당·BE 지적, 2026-09-27).
    # BG 는 시장마다 따로 굽고 축은 `wide.index ∩ book.ret.index`(둘 다 그 시장 달력)이다. 여기서 그것을 **재 본다**.
    usable = [d for d in wide.index if d in book.ret.index]
    axis = axis_report(market, usable, panel, wide, book)
    print("    " + axis, flush=True)
    # **표지(`miss_*`)는 평균에 넣지 않는다** — 0/1 이고 피처가 아니다(공통 틀 담당 정정, 2026-09-27).
    # 넣으면 묶음 평균이 "값" 과 "자료가 있나" 를 섞은 한 칸이 되어 무엇을 읽었는지 말할 수 없다.
    names_of_group = {k: [c for c in cols if c in panel.columns and not c.startswith("miss_")]
                      for k, cols in groups.items()}
    # 표지는 평균에 섞지 않되 **버리지도 않는다.** 국장 원피처·G 묶음은 판정 창의 28% 만 덮는다(2025-05-21~) —
    # 박스 국면 614세션에는 FA 의 새 재료가 없고, 그때 묶음 평균은 전부 0(순위 중앙)이다. 정책이 "오늘은 자료가
    # 없다" 를 모르면 0 을 **신호로** 읽는다. 그래서 표지 평균을 **칸 하나**(`cover`)로 따로 넣는다.
    flag_cols = sorted({c for cols in groups.values() for c in cols
                        if c.startswith("miss_") and c in panel.columns})
    out: list[Prepared] = []
    for phase in range(EVERY):
        held: list[str] = []            # 완충(3N) 상태는 **위상마다 따로** 굴린다 — 섞으면 명단이 거짓이 된다
        days, holds, slots, masks, statics, rets, ctxs = [], [], [], [], [], [], []
        for step, day in enumerate(usable):
            if (step - phase) % EVERY or step + EVERY > len(usable):
                continue
            row = wide.loc[day].dropna()
            if book.trad and book.trad.get(day):
                row = row[row.index.isin(book.trad[day])]
            if row.empty:
                continue
            held = pick_mult(held, row.sort_values(ascending=False).index, TOP_N, 3)
            names = [e for e in held if e in eidx][:TOP_N]
            if len(names) < 4:
                continue
            pad = TOP_N - len(names)
            feats = np.zeros((TOP_N, 2 + len(names_of_group)), np.float32)
            feats[: len(names), 0] = row.reindex(names).to_numpy(np.float32)
            try:
                block = stat.loc[day].reindex(names)
            except KeyError:
                block = None
            if block is not None and flag_cols:
                feats[: len(names), 1] = 1.0 - block[flag_cols].mean(axis=1).to_numpy(np.float32)
            for bi, cols in enumerate(names_of_group.values(), start=2):
                if block is None or not cols:
                    continue
                feats[: len(names), bi] = block[cols].mean(axis=1).to_numpy(np.float32)
            window = usable[step: step + EVERY]
            r = np.zeros((EVERY, TOP_N), np.float32)
            r[:, : len(names)] = book.ret.loc[window].reindex(columns=names).fillna(0.0).to_numpy(np.float32)
            days.append(day)
            holds.append(list(window))
            slots.append(np.array([eidx[e] for e in names] + [-1] * pad))
            masks.append(np.array([True] * len(names) + [False] * pad))
            statics.append(feats)
            rets.append(r)
            ctxs.append(_index_ctx(book.bench, usable, step))
        if not days:
            continue
        out.append(Prepared(days, np.stack(slots), np.stack(masks), np.stack(statics), np.stack(rets),
                            np.stack(ctxs), ("pred", "cover", *names_of_group), entities, holds))
    if not out:
        raise SystemExit("결정표가 0행이다 — C1 예측·거래가능 명단·수익 창 중 하나가 비었다")
    print(f"  결정표: 위상 {len(out)}벌 · 결정 합 {sum(p.n_dec for p in out)}회(위상 0 은 {out[0].n_dec}회) · "
          f"피처 {out[0].n_static} · 비용 {book.cost:.2%}", flush=True)
    return out


def _index_ctx(bench: pd.Series, usable: list, step: int) -> np.ndarray:
    """지수 20세션 수익·실현변동성 — **그 결정일 전** 벤치 수익만 본다(`v2_bandit.index_features` 와 같은 뜻)."""
    prior = [float(bench.get(d, 0.0) or 0.0) for d in usable[max(0, step - 20): step]]
    if len(prior) < 5:
        return np.zeros(2, np.float32)
    arr = np.array(prior)
    return np.array([float(np.prod(1.0 + arr) - 1.0), float(arr.std() * np.sqrt(ANN))], np.float32)

# --------------------------------------------------------------------------- 관문


def require_registered(args: argparse.Namespace, what: str) -> str:
    """사전등록 전에는 아무 결과도 보지 않는다 — 플래그·초안 여부·날짜 셋 다 본다(v2_bandit 과 같은 규약)."""
    if not getattr(args, "i_registered", False):
        raise SystemExit(f"{what} 은 사전등록 뒤에만 돈다 — {PROTOCOL} 승인 후 --i-registered 를 명시하라")
    if not PROTOCOL.exists():
        raise SystemExit(f"등록 문서가 없다: {PROTOCOL}")
    if "초안" in PROTOCOL.read_text().splitlines()[0]:
        raise SystemExit(f"{PROTOCOL} 이 아직 초안이다(머리줄) — 사용자 승인·해시 고정 뒤에 돈다")
    from quant_rl_trading.replay.clock import LiveClock

    today = LiveClock().now().date()
    if today < JUDGE_FROM:
        raise SystemExit(f"{what} 은 {JUDGE_FROM} 부터다(공통 틀: 측정 10/5 이후) — 오늘 {today}")
    return hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------- 자료 준비(명령들이 함께 쓰는 자리)


@dataclass(frozen=True)
class Inputs:
    """한 시장의 준비물 — 위상별 결정표 · 학습창/내부검증 · 비용 · 낙폭 밴드."""

    market: str
    preps: list[Prepared]
    fits: list[range]
    valids: list[range]
    cost: float
    bands: Bands


def _spans(prep: Prepared, args: argparse.Namespace) -> tuple[range, range]:
    """학습창 / 내부 검증. 공통 틀의 `inner_split`(마지막 20% · 퍼지 5)을 **세션 날짜로** 받아 결정 인덱스로 옮긴다."""
    if args.synthetic:
        cut = int(prep.n_dec * 0.8)
        return range(0, cut), range(cut, prep.n_dec)
    fit_days, val_days = _kit().inner_split(list(prep.days))      # type: ignore[attr-defined]
    fit_set, val_set = set(fit_days), set(val_days)
    fit_idx = [i for i, d in enumerate(prep.days) if d in fit_set]
    val_idx = [i for i, d in enumerate(prep.days) if d in val_set]
    if not fit_idx or not val_idx:
        raise SystemExit(f"내부 검증 분할이 비었다(결정 {prep.n_dec}회) — 창이 너무 짧다")
    return range(fit_idx[0], fit_idx[-1] + 1), range(val_idx[0], val_idx[-1] + 1)


def _selection_score(market: str, pred: pd.DataFrame, book, kit) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """그 시장의 선정 점수(세션 × 종목, 평활 끝). **모든 군이 같은 합성을 써야 한다**(공통 틀 `evaluate` 와 같다).

    국장은 pred 의 EMA5. 미장은 **시행 AT 채택 M1 합성 뒤** EMA5 — 결측 재무를 0 으로 두고 분모에는 남긴다.
    미장에서 이걸 빼면 후보 24/24 가 재무 없는 외국 발행사로 채워지고, 그 차이가 BG 의 우위로 잡힌다.
    """
    if market == "US":
        if book.fund is None:
            raise SystemExit("미장 MarketBook 에 fund 가 없다 — market_books(..., panel=panel) 로 불러야 한다")
        return kit.us_m1_wide(pred, book.fund)
    from tools.trial_ranker_kit import SPAN

    return pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()


@dataclass(frozen=True)
class Real:
    """실자료 한 벌 — 시장별 준비물 · 공통 틀 · 대조군 예측 · 시장 장부 · FA 패널."""

    inputs: list[Inputs]
    kit: object = None
    controls: dict = field(default_factory=dict)      # {"C0": {seed: df}, "C1": {seed: df}}
    books: dict = field(default_factory=dict)
    panel: pd.DataFrame | None = None


def build_inputs(args: argparse.Namespace) -> Real:
    """실자료(또는 합성)를 한 번에 준비한다. **대조군 예측은 공통 틀이 준다** — BG 는 자기 GBM 을 짜지 않는다."""
    if args.synthetic:
        prep = synthetic(args.seed, n_dec=args.decisions, alpha=args.alpha)
        fit, valid = _spans(prep, args)
        return Real([Inputs("KR", [prep], [fit], [valid], ONE_WAY_COST_KR, Bands.default())])
    from datetime import UTC, datetime, time

    from quant_rl_trading.store import Store

    kit = _kit()
    markets = tuple(m for m in args.markets.split(",") if m)
    store = Store(root=Path(args.root))
    panel, _feats, groups, sessions = kit.load_full_panel(markets, (kit.JUDGE_START, kit.JUDGE_END),
                                                         root=args.root, store=store)
    # 미장이 포함되면 `panel` 이 **필수**다 — 없으면 evaluate 가 ValueError. M1 합성에 재무 결측 표지가 필요하다.
    # **굽기 관문**(rc 4) — 원피처 캐시가 판정 창을 못 덮으면 시작하지 않는다. 대조군 관문(rc 3)과 코드가 다르다.
    kit.require_full_coverage((kit.JUDGE_START, kit.JUDGE_END))
    books = kit.market_books(store, sessions, markets, panel=panel)
    # 없으면 어느 파일이 없는지 찍고 SystemExit(3) — 대조군 없이 도는 것보다 멈추는 게 낫다.
    ctrls = kit.require_controls(panel)
    c1 = c1_average(ctrls["C1"])
    bands = Bands.from_store(store, as_of=datetime.combine(sessions[-1], time(16), tzinfo=UTC))
    out = []
    for market in markets:
        part = c1[c1["market"] == market][["entity_id", "session", "pred"]]
        if part.empty or market not in books:
            print(f"{market}: C1 예측이 비었다 — 건너뛴다", flush=True)
            continue
        print(f"{market}: C1 예측(시드 {len(ctrls['C1'])}개 순위평균) {len(part):,}행", flush=True)
        wide = _selection_score(market, part, books[market], kit)
        preps = prepare_market(market, wide, books[market], panel[panel["market"] == market], groups)
        # **창 관문**(rc 5) — 채점되는 첫 결정 앞에 워밍업 세션이 있는지. 축은 그 시장 달력이다(국장 축을 주면
        # 미장 단독 세션이 안 세어져 조용히 통과한다). BG 는 시장마다 따로 굽으므로 이 시장의 축을 준다.
        kit.require_full_window(sorted(books[market].ret.index), preps[0].days[0], WARMUP_SESSIONS,
                                label=f"{market} 에피소드 워밍업")
        spans = [_spans(p, args) for p in preps]
        out.append(Inputs(market, preps, [s[0] for s in spans], [s[1] for s in spans], books[market].cost, bands))
    if not out:
        raise SystemExit("어느 시장도 결정표를 못 만들었다")
    return Real(out, kit, ctrls, books, panel)


# --------------------------------------------------------------------------- 명령


def cmd_smoke(args: argparse.Namespace) -> int:
    """합성 자료 배관 점검. 판정이 아니다 — 행동 제약·결정론·학습 루프가 도는지만 본다."""
    inp = build_inputs(args).inputs[0]
    prep, fit, valid = inp.preps[0], inp.fits[0], inp.valids[0]
    print(f"=== BG 스모크(합성 · seed {args.seed} · 결정 {prep.n_dec} · alpha {args.alpha}) — 판정 아님 ===", flush=True)
    _r, flat = evaluate_span(TiltPolicy(obs_dim(prep.n_static)), prep, valid, bands=inp.bands, cost=inp.cost)
    print(f"  0 기울기(초기 정책 = 동일가중): 결정당 우위 {flat['edge']:+.6f} · |δ|합 {flat['tilt_abs']:.4f}", flush=True)
    run = train_one(prep, fit, valid, seed=args.seed, updates=args.updates, bands=inp.bands, cost=inp.cost,
                    eval_every=max(1, args.updates // 5))
    _r, best = evaluate_span(run.net, prep, valid, bands=inp.bands, cost=inp.cost)
    print(f"  학습 뒤: 검증 우위 {best['edge']:+.6f}(최고 {run.best_edge:+.6f} @업데이트 {run.best_step}) · "
          f"한계붙음 {best['sat_share']:.0%} · |δ|합 {best['tilt_abs']:.4f}", flush=True)
    return 0


def cmd_canary(args: argparse.Namespace) -> int:
    """정답 칸을 흘려 **배울 수 있는 배관인가**를 먼저 본다. 통과는 "학습을 시작할 자격" 까지만 뜻한다.

    그리고 정렬도 r 을 재서 **필요 스텝 수**를 찍는다 — 예산을 안 찍고 "안 배운다" 를 말하지 않는다(rl-postmortem §1).
    """
    hashed = require_registered(args, "카나리") if not args.synthetic else "synthetic"
    print(f"=== BG 카나리(정답 흘림 — 판정 아님) 해시 {hashed} ===", flush=True)
    worst = float("inf")
    for inp in build_inputs(args).inputs:
        oracles = [oracle_column(p) for p in inp.preps]
        run = train_one(inp.preps, inp.fits, inp.valids, seed=args.seed, updates=args.updates, bands=inp.bands,
                        cost=inp.cost, n_oracle=1, oracle=oracles, eval_every=max(1, args.updates // 5))
        roll, stats = evaluate_span(run.net, inp.preps[0], inp.valids[0], bands=inp.bands, cost=inp.cost,
                                    oracle=oracles[0])
        r, t = alignment(roll)
        need = needed_steps(r)
        worst = min(worst, r)
        print(f"\n{inp.market}: 정렬도 r {r:+.4f} (t {t:+.2f}) → 필요 그래디언트 스텝 ≈ {need:,.0f} "
              f"(업데이트 ≈ {need / HYPER.epochs:,.0f}) · 검증 우위 {stats['edge']:+.6f} · "
              f"한계붙음 {stats['sat_share']:.0%}", flush=True)
    print(f"\n예산 기준 — 가장 약한 시장의 r {worst:+.4f} 로 업데이트 {needed_steps(worst) / HYPER.epochs:,.0f} 이 필요하다. "
          "판정의 --updates 가 이보다 훨씬 작으면 결과를 '기각' 으로 읽지 않는다(카나리 144배 오판).", flush=True)
    print("카나리는 판정이 아니다 — 통과는 '배관이 살아 있다' 까지만 뜻한다", flush=True)
    return 0


def cmd_pilot(args: argparse.Namespace) -> int:
    hashed = require_registered(args, "파일럿") if not args.synthetic else "synthetic"
    print(f"=== BG 파일럿 관문 — 해시 {hashed} ===", flush=True)
    passed = []
    for inp in build_inputs(args).inputs:
        print(f"\n[{inp.market}]", flush=True)
        ok, _runs = pilot_gate(inp.preps, inp.fits, inp.valids, bands=inp.bands, cost=inp.cost, updates=args.updates)
        passed.append(ok)
    return 0 if any(passed) else 3


def _series(pairs: list[tuple[object, float]]) -> pd.Series:
    """(세션, 수익) 목록 → 세션 오름차순 일수익. 위상 0 만 쓰므로 같은 날이 두 번 오지 않는다."""
    if not pairs:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(pairs, columns=["session", "ret"]).groupby("session")["ret"].mean()
    return frame.sort_index()


#: `judge`·`overfit_gap` 이 반드시 보는 키. 하나라도 없거나 nan 이면 `_mean` 이 nan 을 내고 관문이 **조용히**
#: 떨어진다 — 교훈 6("판정 지표를 먼저 의심한다"). 넘기기 전에 여기서 막는다.
JUDGE_KEYS: tuple[str, ...] = ("ann", "sharpe", "box_ann", "rally_ann", "mdd", "turn", "ic")


def _metrics(daily: pd.Series, book, turn: float, ic: float) -> dict[str, float]:  # type: ignore[no-untyped-def]
    """판정 지표 — 대조군과 **같은 함수**(`trial_ranker_kit.summarize`)로 낸다. 두 곳이 다르면 비교가 아니다.

    `ic` 에는 **C0 의 ic** 를 그대로 넣어 ΔIC = 0 으로 만든다. `judge` 의 관문 ④는 `results.ic − control0.ic` 이고
    **control0 은 C0** 다 — 여기에 C1 의 ic 를 넣으면 ④가 재는 것은 "C1 − C0" 이 되어, C1 이 C0 보다 IC 가 높으면
    BG 가 공짜로 통과하고 낮으면 자기 잘못이 아닌 이유로 떨어진다. BG 는 **종목 순위를 바꾸지 않으므로**(합 0
    기울기만) ④는 애초에 BG 에 해당하지 않는 기준이다. 등록 문서 한계 절에 이 처리를 적는다 — 사후에 고른 것으로
    보이면 안 된다. (공통 틀 담당 지적, 2026-09-27.)
    """
    from tools.trial_ranker_kit import summarize

    m = summarize(daily, book.bench)
    m["turn"] = turn
    m["ic"] = ic
    return m


def _inner(fit: range, *, share: float = 0.20) -> int:
    """적합창 뒤쪽 몇 결정을 내부 검증으로 뗄까 — 공통 틀 `inner_split` 과 같은 비율(20%), 사이에 퍼지."""
    return max(1 + GAP_DECISIONS, round((fit.stop - fit.start) * share))


def _restrict(kit, real, books, judged_first: dict, c0: dict, c1: dict):  # type: ignore[no-untyped-def]
    """대조군을 **BG 가 채점받는 구간으로** 다시 잰다. 구간이 다르면 ①③⑤ 가 전부 뜻을 잃는다.

    BG 는 워크포워드 폴드 때문에 앞쪽 일부를 적합에만 쓰고 채점하지 않는다. C0·C1 을 전 구간으로 두면
    국면 구성(박스/급등 비율)이 달라 "연수익 ≥ C0 + 2%p" 가 다른 시장을 견주는 문장이 된다.
    """
    if not judged_first:
        return c0, c1
    out = []
    for table in (c0, c1):
        redone: dict[int, dict[str, float]] = {}
        arm = "C0" if table is c0 else "C1"
        for seed, pred in real.controls.get(arm, {}).items():
            part = pred.copy()
            part["session"] = pd.to_datetime(part["session"]).dt.date
            keep = pd.Series(True, index=part.index)
            for market, first in judged_first.items():
                keep &= ~((part["market"] == market) & (part["session"] < first))
            part = part[keep]
            if part.empty:
                continue
            y = real.panel[["entity_id", "session", "y5"]] if real.panel is not None else None
            _by, pooled = kit.evaluate_all(part, books, y=y)
            redone[int(seed)] = require_keys(f"{arm}(구간 제한) 시드 {seed}", pooled)
        out.append(redone or table)
    first_txt = " · ".join(f"{m} {d}" for m, d in sorted(judged_first.items()))
    print(f"\n대조군을 BG 채점 구간으로 다시 쟀다 — 시작 {first_txt}", flush=True)
    return out[0], out[1]


def require_same_span(bg_days: list, controls: dict[str, dict], market: str) -> str:  # type: ignore[type-arg]
    """BG 가 채점받는 구간이 **C0·C1 과 같은지** 본다. 다르면 `judge` 의 비교가 같은 것을 재지 않는다.

    BG 의 결정표는 C1 예측 캐시에서 나오고, 그 캐시는 판정 블록 위에만 있다(`walk_gbm` 이 블록마다 예측한다).
    그래서 보통 저절로 맞지만, **맞는다고 가정하면 안 된다**: 블록 경계가 반열림으로 바뀌었고(공통 틀 담당,
    2026-09-27) 블록에 못 든 꼬리 구간은 채점하지 않는다. 그 구간까지 에피소드를 늘리면 **판정에 안 쓰는
    구간으로 보상을 받는다** — 학습창 성과를 판정에 쓰지 않는다는 등록 규칙이 조용히 깨지는 자리다.
    """
    ctrl_days: set = set()
    for table in controls.values():
        for frame in table.values():
            part = frame[frame["market"] == market] if "market" in frame.columns else frame
            ctrl_days |= set(pd.to_datetime(part["session"]).dt.date)
    if not ctrl_days or not bg_days:
        return f"{market} 구간 확인 불가(대조군 {len(ctrl_days)} · BG {len(bg_days)})"
    extra = sorted(set(bg_days) - ctrl_days)
    if extra:
        raise SystemExit(f"{market}: BG 가 대조군이 채점받지 않는 {len(extra)}세션을 채점한다 "
                         f"(예: {extra[:3]}) — judge 의 비교가 같은 것을 재지 않는다")
    missing = len(ctrl_days - set(bg_days))
    return (f"{market} 채점 구간: BG {min(bg_days)}~{max(bg_days)} {len(bg_days)}세션 · "
            f"대조군 {len(ctrl_days)}세션 (BG 가 못 쓴 {missing} = 재조정 창 끝)")


def require_keys(where: str, metrics: dict[str, float], *, keys: tuple[str, ...] | None = None) -> dict[str, float]:
    """`judge` 에 넘기기 전 마지막 점검. 조용히 떨어지는 관문보다 시끄럽게 멈추는 것이 낫다.
    ``keys`` 를 주면 그 키만 본다(판정에 안 들어가는 학습창 지표 — 과적합 격차에 쓰는 키만)."""
    bad = [k for k in (keys or JUDGE_KEYS) if k not in metrics or not np.isfinite(metrics[k])]
    if bad:
        raise SystemExit(f"{where}: 판정 키가 없거나 nan 이다 {bad} — 이대로 judge 에 넘기면 관문이 조용히 떨어진다")
    return metrics


def cmd_judge(args: argparse.Namespace) -> int:
    """본 학습 + 판정. **파일럿 관문을 통과한 뒤에만** 돈다(같은 실행에서 관문을 먼저 건다)."""
    hashed = require_registered(args, "판정")
    real = build_inputs(args)
    inputs, kit, books = real.inputs, real.kit, real.books
    from quant_rl_trading.replay.clock import LiveClock
    from quant_rl_trading.store import Store
    from tools.trial_ranker_kit import record

    store = Store(root=Path(args.root))
    print(f"=== 시행 BG — {PROTOCOL} (해시 {hashed}) ===", flush=True)

    # 대조군 — C0·C1 을 **공통 틀의 evaluate_all 로** 시드마다 잰다. BG 는 이 표를 만들지 않고 읽는다.
    # `y` 로 y5 를 넘겨야 IC 가 채워진다(관문 ④의 기준선이 된다).
    y = real.panel[["entity_id", "session", "y5"]] if real.panel is not None else None
    c0: dict[int, dict[str, float]] = {}
    c1: dict[int, dict[str, float]] = {}
    for arm, into in (("C0", c0), ("C1", c1)):
        for seed, pred in real.controls.get(arm, {}).items():
            _by, pooled = kit.evaluate_all(pred, books, y=y)          # type: ignore[attr-defined]
            into[int(seed)] = pooled
    if not c0 or not c1:
        raise SystemExit("C0·C1 대조 지표를 못 만들었다 — scripts/final_round_bake.sh 가 먼저다")
    for arm, table in (("C0", c0), ("C1", c1)):
        for seed, m in table.items():
            require_keys(f"{arm} 시드 {seed}", m)

    for inp in inputs:
        ok, _p = pilot_gate(inp.preps, inp.fits, inp.valids, bands=inp.bands, cost=inp.cost, updates=args.pilot_updates)
        if not ok:
            print(f"{inp.market}: 파일럿 불통과 — 본 학습을 하지 않는다", flush=True)
            return 3

    # ΔIC = 0 을 **시드마다** 맞춘다 — C0 의 그 시드 ic 를 그대로 쓴다(위 `_metrics` 주석).
    ic_of = {s: c0[s]["ic"] for s in (0, 1, 2) if s in c0}
    if len(ic_of) < 3:
        raise SystemExit(f"C0 에 시드 0·1·2 의 ic 가 없다 {sorted(c0)} — ΔIC 를 0 으로 맞출 수 없다")
    results: dict[int, dict[str, float]] = {}
    train_m: dict[int, dict[str, float]] = {}
    # 진행 기록은 **본 판정에서만**. `--no-progress` 로 끈다(합성 스모크·카나리는 애초에 여길 안 지난다).
    progress_store = None if (args.no_progress or args.synthetic) else store
    clock = LiveClock()
    mark = monotonic()  # invariant-allow: wallclock — 폴드 하나에 걸린 시간
    sats, spreads, exhausted = [], [], []
    judged_first: dict[str, object] = {}
    for seed in (0, 1, 2):
        per_market, per_train = {}, {}
        for inp in inputs:
            folds = walk_folds(inp.preps[0].n_dec, folds=args.folds)
            if not folds:
                raise SystemExit(f"{inp.market}: 결정 {inp.preps[0].n_dec}회로는 워크포워드 폴드를 못 만든다")
            judged, trained = Rollout(), Rollout()
            for k_fold, (fit_all, judge_span) in enumerate(folds, 1):
                # **적합은 확장창, 채점은 그 뒤 구간.** 조기 종료는 적합창 안의 내부 검증으로만 한다.
                fits = [range(fit_all.start, max(fit_all.start + 1, fit_all.stop - _inner(fit_all))) for _ in inp.preps]
                valids = [range(f.stop + GAP_DECISIONS, fit_all.stop) for f in fits]
                if any(v.stop - v.start < 1 for v in valids):
                    raise SystemExit(f"{inp.market} 폴드 {k_fold}: 내부 검증이 비었다(적합 {fit_all})")
                run = train_one(inp.preps, fits, valids, seed=seed, updates=args.updates, bands=inp.bands,
                                cost=inp.cost, eval_every=max(10, args.updates // 100), verbose=False)
                jstats = evaluate_phases(run.net, inp.preps, [judge_span] * len(inp.preps),
                                         bands=inp.bands, cost=inp.cost)
                # 판정 일수익은 **위상 0**(실제 장부)에서 낸다. 나머지 아홉은 흩어짐(운의 크기)으로만 적는다.
                _r, _s = evaluate_span(run.net, inp.preps[0], judge_span, bands=inp.bands, cost=inp.cost)
                judged.daily += _r.daily
                judged.abs_turnover += _r.abs_turnover
                _t, _ts = evaluate_span(run.net, inp.preps[0], fits[0], bands=inp.bands, cost=inp.cost)
                trained.daily += _t.daily
                trained.abs_turnover += _t.abs_turnover
                sats.append(jstats["sat_share"])
                spreads.append(jstats.get("phase_spread", 0.0))
                exhausted.append(run.budget_exhausted)
                print(f"  시드 {seed} {inp.market} 폴드 {k_fold}/{len(folds)}: 적합 결정 {fit_all.stop} → "
                      f"채점 {judge_span.start}~{judge_span.stop - 1} · 채점창 우위 {jstats['edge']:+.6f} "
                      f"· 학습창 {_ts['edge']:+.6f} · 한계붙음 {jstats['sat_share']:.0%} "
                      f"· 최고 @{run.best_step}/{args.updates}"
                      f"{' (예산 소진)' if run.budget_exhausted else ''}", flush=True)
                # **진행 기록.** 학습 쪽 숫자만 적는다 — 위에 찍은 `jstats['edge']`(채점창)는
                # 판정 창 지표라서 표에 넣지 않는다(사전등록: 학습 중에 판정 창을 보지 않는다).
                # 담는 것은 학습 보상·내부 검증 우위(부호 뒤집음)·최고 체크포인트 위치·경과다.
                # BG 는 break 로 끊지 않고 최고 체크포인트를 고르므로, **예산을 다 쓰지 않은 것**이
                # 조기 종료가 걸린 것과 같은 뜻이다(`budget_exhausted` 의 반대).
                kit.record_progress(                                                   # type: ignore[attr-defined]
                    progress_store, clock, "BG", source="trial_final_residual_rl",
                    # 시드 셋 — 바로 위 `for seed in (0, 1, 2)` 와 같은 수다(진행률의 분모).
                    market=inp.market, seed=int(seed), n_seeds=3, fold=k_fold, n_folds=len(folds),
                    step=int(run.best_step), rounds=int(args.updates),
                    train_loss=(-float(run.log[-1]["reward"]) if run.log and "reward" in run.log[-1] else None),
                    val_loss=-float(run.best_edge), metric="reward(−) / valid edge(−)",
                    stopped_early=not run.budget_exhausted,
                    elapsed_s=monotonic() - mark,  # invariant-allow: wallclock
                    # 한계붙음도 **학습창(`_ts`)의 값**을 적는다 — `jstats` 는 채점창이라 진행 기록에 못 넣는다.
                    # `evaluate_span` 은 에피소드가 비면 edge·n 만 돌려준다 — 없는 칸을 꺼내다 죽지 않게 get 이다.
                    note=(f"적합 결정 ~{fit_all.stop} · 학습창 기울기 한계붙음 "
                          f"{_ts.get('sat_share', float('nan')):.0%}"))
                mark = monotonic()  # invariant-allow: wallclock
            daily = _series(judged.daily)
            judged_first.setdefault(inp.market, min(daily.index))
            if seed == 0:
                print("    " + require_same_span(list(daily.index), real.controls, inp.market), flush=True)
            turn = float(np.mean(judged.abs_turnover) * ANN / EVERY) if judged.abs_turnover else 0.0
            per_market[inp.market] = _metrics(daily, books[inp.market], turn, ic_of[seed])
            per_train[inp.market] = _metrics(_series(trained.daily), books[inp.market],
                                             float(np.mean(trained.abs_turnover) * ANN / EVERY) if trained.abs_turnover else 0.0,
                                             ic_of[seed])
        results[seed] = require_keys(f"BG 시드 {seed}", kit.pooled_metrics(per_market))   # type: ignore[attr-defined]
        # 학습창 지표는 **판정에 안 쓰고** 과적합 격차(gap_ann·gap_sharpe·gap_ic)에만 쓴다. 학습창은 판정 구간 앞(2023~2024
        # 박스장)이라 급등 국면 세션이 없어 rally_ann 이 nan 인 것이 정상이다 — 9/29 16:26 판정 전체 키를 요구하다
        # 시드 0 뒤 rc=1 로 멈췄고, 대기열이 처음부터 다시 돌렸다. 격차에 쓰는 키만 요구한다(판정 규칙 변경 아님).
        train_m[seed] = require_keys(f"BG 학습창 시드 {seed}", kit.pooled_metrics(per_train),  # type: ignore[attr-defined]
                                     keys=("ann", "sharpe", "ic"))

    # **대조군을 같은 구간으로 자른다.** BG 는 채점 구간이 뒤쪽 일부이므로, C0·C1 을 전 구간으로 두면
    # 서로 다른 구간을 견주게 된다 — 국면 구성이 달라 ①③⑤ 가 전부 뜻을 잃는다.
    c0, c1 = _restrict(kit, real, books, judged_first, c0, c1)

    gaps = kit.overfit_gap(train_m, results)                           # type: ignore[attr-defined]
    lines, verdict = kit.judge(results, c0, c1, label="BG 잔차 RL")    # type: ignore[attr-defined]
    extra = [f"과적합 지표(학습창 − 판정창) {gaps}",
             f"기울기 한계붙음 {np.mean(sats):.1%} · 위상 간 흩어짐 {np.mean(spreads):.6f}",
             "ΔIC 는 정의상 0 — BG 는 종목 순위를 바꾸지 않는다(기울기만)"]
    print("\n" + "\n".join([*lines, *extra]), flush=True)
    print(f"판정: {verdict}", flush=True)
    if args.save:
        record(store, entity="final-model-round-2026-10:BG", source="trial_final_residual_rl", family="rl",
               digest=hashed, verdict=verdict, lines=[*lines[-3:], *extra], market=",".join(i.market for i in inputs))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("smoke", "canary", "pilot", "judge"):
        p = sub.add_parser(name)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--updates", type=int, default=30 if name == "smoke" else 60)
        p.add_argument("--decisions", type=int, default=120)
        p.add_argument("--alpha", type=float, default=0.0, help="합성 자료의 진짜 알파(배관 점검용)")
        if name != "smoke":
            p.add_argument("--i-registered", action="store_true")
            p.add_argument("--synthetic", action="store_true", help="합성 자료로 같은 경로를 돈다(판정 아님)")
            p.add_argument("--markets", default="KR,US", help="합동 판정이 기본이다(공통 틀 §자료)")
            p.add_argument("--root", default="data")
        if name == "judge":
            p.add_argument("--pilot-updates", type=int, default=60)
            p.add_argument("--folds", type=int, default=FOLDS,
                           help="앞으로 걸어가며 다시 적합하는 횟수. 채점되는 결정은 전부 안 본 구간이다")
            p.add_argument("--save", action="store_true")
            p.add_argument("--no-progress", action="store_true",
                           help="trial_progress 기록을 끈다(기본은 적는다 — 학습 탭의 진행률이 여기서 온다)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "smoke":
        args.synthetic, args.markets, args.root = True, "KR", "data"
    return {"smoke": cmd_smoke, "canary": cmd_canary, "pilot": cmd_pilot, "judge": cmd_judge}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
