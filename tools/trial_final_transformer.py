"""시행 BE — 세트 트랜스포머 v2: 전 피처(FA) 60세션 계열 + 같은 날 종목 사이 주의.

    .venv/bin/python tools/trial_final_transformer.py [--save] [--smoke 2] [--seeds 5]
    .venv/bin/python tools/trial_final_transformer.py --synthetic --smoke 2   # 합성 자료 배선 확인(수치 안 찍음)

사전등록 `docs/protocols/final-model-round-2026-10.md` 의 공통 틀 + BE 절. 공통 틀은 `tools/final_round_kit` 에 있고
**이 파일은 kit 을 읽기만 한다**(자료·블록·내부 분할·채점·판정은 전부 kit).

지난 트랜스포머(시행 AC)와 무엇이 다른가 — 바뀐 축은 둘뿐이다:
  ① **재료**: 가격 4채널 → FA 전 피처(6점수 + 원피처 + G1~G7 + BA + 묶음별 결측 표지 + is_us).
     열 수는 `len(kit.feature_names(kit.blocks_of()))` 로 읽는다 — 하드코딩하지 않는다(2026-09-27 현재 76).
     **G8·G10·G11 은 FA 에서 빠졌다**(리드 결정) — `kit.BLOCK_ORDER` 가 이 회차의 FA 를 고정한다.
     AC 의 실패 이유가 "가격 이력엔 알파가 없다" 였으므로 여기서 재료를 바꾼다.
  ② **구조**: 종목을 따로 보던 것 → 시간 인코더 뒤 **같은 (세션, 시장) 종목끼리 attention**(세트 트랜스포머).
     상대 순위를 맞히는 과제이므로 모델도 이웃을 봐야 한다.
나머지(타깃·퍼지·블록·비용·포트)는 공통 틀 그대로다 — 한 번에 한 축(복기 규칙, 2026-09-22 교훈 1).

**손실은 세션 내 rank-gauss y5 의 MSE** 다. 순위 손실을 쓰지 않은 이유:
  · 공통 틀이 타깃을 "y5 rank-gauss(목적 = 판정 지표)" 로 못 박았다. 세션 안에서 rank-gauss 한 타깃의 MSE 는
    이미 세션 내 순위 회귀이고, 세션마다 배치를 끊으므로 세션 간 수준 차이가 손실에 안 섞인다.
  · 순위 목적(NDCG) 축은 **같은 회차의 시행 BF** 가 맡는다. 여기서 같이 바꾸면 기각됐을 때 구조 탓인지
    목적함수 탓인지 못 가른다 — AC·AJ·AK 가 그렇게 해석 불가로 끝났다.

**예상 계산량**(2026-09-27 실측 기준: 종목 900 · 20스텝 · 72피처 배치가 학습 0.96초 / 추론 0.18초, 12스레드):
  · 판정 41블록(첫 채점 세션은 `kit.FIRST_JUDGED_OFFSET = 155` 뒤) / 재학습 9회(5블록마다) /
    세션마다 (국장, 미장) 두 집합 / 종목 70% 부분표본 → 배치 ~0.65초
  · 에포크 = 160세션 × 2집합 × 0.65초 ≈ 3.5분 + 내부 검증 ≈ 1.2분 → 4.7분
  · 콜드 재학습 1회(최대 8에포크, 조기 종료로 보통 5) ≈ 24분 · 웜 재학습 8회(2에포크) ≈ 75분 · 추론 ≈ 7분
  · **시드 하나 ≈ 1.8시간 → 시드 5 ≈ 9시간**. 큐브는 float16 으로 약 310MB(세션 1,070 × 종목 1,900 × 76열),
    패널 프레임과 합쳐 파이썬 전체 RSS 2.5~3GB 예상.
  · 시드별 예측을 `SEED_CACHE` 에 남기므로 중간에 죽거나 이틀로 쪼개도 끝난 시드는 다시 돌지 않는다.

**엠바고 때문에 C0 은 시행 L 과 직접 비교할 수 없다.** `kit.train_end` 가 학습 끝점을 10세션 당긴다
(퍼지 5 + 엠바고 5, 모든 군 동일). 그래서 C0 의 절대 수치는 시행 L 기록보다 간격이 5세션 넓다 —
**군 사이 비교(BE 대 C0·C1)는 온전하고, C0 을 L 의 숫자와 견주는 것만 하면 안 된다.**

**미장 포트는 M1 합성을 거친다**(시행 AT 채택, 연 +6.0%p). 모든 군(C0·C1·BE1·BE2)에 똑같이 적용된다 —
`kit.evaluate` 안에서 돌고, 그래서 `market_books(..., panel=panel)` 로 재료를 넘겨야 한다.
BE2 의 순위 평균은 **pred 단계**에서 하고 M1 합성은 그 뒤 `evaluate` 가 한다(순서를 바꾸면 안 된다).

**워밍업을 따로 안 받는 이유.** `kit.blocks` 는 첫 블록을 `kit.FIRST_JUDGED_OFFSET = MIN_TRAIN + PURGE
= 155` 세션 뒤에서 시작한다(**엠바고는 블록을 늦추지 않는다** — `train_end` 로 학습 끝점만 당긴다.
처음에 `MIN_TRAIN + GAP = 160` 으로 적었던 것은 틀렸고 결론만 같았다).
등록 창이 2022-07-01 부터이므로 **채점되는 첫 세션 앞에 이미 155세션이 있다** — 60세션 창이 언제나 꽉 찬다.
창이 짧아 0 으로 채워지는 것은 학습창 맨 앞 60세션뿐이고 그 세션은 채점되지 않는다. 그래서 패널 창을
앞으로 늘리지 않는다(늘리면 `control_tag` 가 달라져 대조군 캐시와 어긋난다).

**아직 못 믿는 것.** 국장 원피처·G 묶음은 판정 창의 28%(2025-05-21~)만 덮는다 — 박스 국면 614세션에는
FA 의 새 재료가 한 칸도 없다. 전 창 굽기(`scripts/final_round_bake_features.sh`) 전에 나온 실자료 숫자는
"정보가 늘어서" 를 잴 수 없다(C1 도 같이 굶으므로 모델 − C1 비교는 성립하지만 ①~⑤ 는 아니다).

과적합 억제(공통 틀 4·5): 하이퍼는 아래 상수로 **등록 때 고정**(판정 창에서 고르지 않는다) · dropout 0.2 ·
weight decay 1e-4 · **묶음 단위 피처 드롭아웃**(결측 표지도 같이 켠다) · 입력 잡음 · 종목 부분표본 ·
조기 종료는 `kit.inner_split` 의 내부 검증만 본다(판정 창은 학습 중에 단 한 번도 안 읽는다) · 시드 5 · 5블록마다 재학습.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402

PROTOCOL = Path("docs/protocols/final-model-round-2026-10.md")
#: 시드별 예측 캐시. 10시간짜리 실행이라 중간에 죽으면(메모리 가드·WSL2 재부팅) 끝난 시드는 다시 돌리지 않는다.
SEED_CACHE = Path("data/_diag/final-round/BE")

# ── 등록 때 고정하는 하이퍼파라미터 (판정 창에서 고르지 않는다) ─────────────────────────
WINDOW = 60           # 창 길이(세션) — 공통 틀
D_MODEL, HEADS, FFN, DROPOUT = 32, 4, 64, 0.2
TIME_LAYERS, SET_LAYERS = 2, 2
LR, WEIGHT_DECAY, CLIP = 1e-3, 1e-4, 1.0
#: **에포크 = 고정 스텝 예산**(전체 통과가 아니다). 확장창이라 뒤 재학습일수록 학습 세션이 늘어
#: 전체 통과로 잡으면 시드 5 × 재학습 9 가 49시간이 된다(실측: 종목 900·20스텝 배치 0.96초).
#: 예산을 고정하면 재학습마다 비용이 같고, 자료가 늘어난 몫은 "표본을 뽑는 풀이 넓어지는" 형태로 들어온다.
#: 세션 하나가 (국장, 미장) 두 집합이므로 실제 배치 수는 이 값의 2배다.
STEPS_PER_EPOCH = 160
#: 웜 재학습은 2에포크. 여기가 전체 시간의 70% 라 3 으로 두면 시드 5 가 12시간이 된다(2 면 9시간).
#: PATIENCE 2 와 겹쳐 웜 회차에서는 조기 종료가 사실상 안 걸리지만, **최고 에포크 가중치 되돌리기**는 그대로 돈다.
MAX_EPOCHS, MAX_EPOCHS_WARM, PATIENCE = 8, 2, 2
#: 두 번째 재학습부터는 **앞 모델 가중치에서 이어 학습**한다(옵티마이저 상태는 새로).
#: 앞 모델은 그 시점까지의 과거만 봤으므로 누설이 아니다. 없으면 같은 성적에 4배 시간이 든다.
WARM_START = True
GROUP_DROP_P = 0.15   # 묶음 단위 피처 드롭아웃 확률
INPUT_NOISE = 0.10    # rank-gauss 입력에 더하는 가우시안 잡음 표준편차
ENTITY_SUBSAMPLE = 0.7
MIN_SET = 50          # 이보다 적은 (세션, 시장) 집합은 건너뛴다 — 횡단면 attention 이 뜻을 잃는다
RETRAIN_EVERY = 5     # 블록 — 시행 AC 와 같게
SEEDS = (0, 1, 2, 3, 4)

#: **시간 격자** — 60세션을 20스텝으로 읽는다. 최근 10세션은 매일, 그 앞은 5세션마다.
#: 이유 둘: (ㄱ) FA 의 대부분(재무·내부자 60일·잠정실적)은 느리게 움직여 일별 해상도가 정보를 더 주지 않는다.
#: (ㄴ) 60스텝 전체는 국장+미장 합동에서 계산량이 하룻밤에 안 들어간다(아래 예상 계산량 주석).
#: **이 격자는 계산량을 보고 정한 것이고 판정 창 성적을 보고 정한 것이 아니다** — 등록 때 고정한다.
TIME_OFFSETS: tuple[int, ...] = tuple(range(0, 10)) + tuple(range(14, WINDOW, 5))
N_STEPS = len(TIME_OFFSETS)


@dataclass
class TrainLog:
    """학습 중 무엇을 봤는지 남긴다 — 미래 누설·조기 종료 감사를 테스트가 이걸로 한다."""
    seen_days: set[int] = field(default_factory=set)     # 손실·그래디언트에 쓴 세션 인덱스
    val_days: set[int] = field(default_factory=set)      # 조기 종료 판단에 쓴 세션 인덱스
    val_scores: list[float] = field(default_factory=list)
    #: 에포크마다의 **학습 손실 평균**(MSE). 진행 기록(`trial_progress`)의 train_loss 가 이 마지막 값이다 —
    #: 검증만 적으면 "학습이 내려가는데 검증이 안 내려간다"(과적합)와 "둘 다 안 내려간다"(학습 실패)를 못 가른다.
    train_losses: list[float] = field(default_factory=list)
    epochs: int = 0
    stopped_early: bool = False


# ── 자료 → 큐브 ────────────────────────────────────────────────────────────────────

def build_cube(panel: pd.DataFrame, columns: list[str], sessions: list[date],
               entities: list[str]) -> np.ndarray:
    """롱 패널 → (세션, 종목, 피처) float16 큐브. 결측은 0(rank-gauss 0 = 중앙값, 공통 틀).

    float16 인 이유: 국장+미장 1,250세션 × 2,000종목 × 70여 피처는 float32 로 700MB 다.
    입력이 rank-gauss(대략 ±4)라 반정밀도로도 유효숫자가 남고, 배치에서 float32 로 올린다.
    열 하나씩 채우고 바로 버린다 — 2.5백만 행 × 70열 프레임을 한꺼번에 피벗하면 머신이 스왑으로 간다.
    """
    row_of = {s: i for i, s in enumerate(sessions)}
    col_of = {e: i for i, e in enumerate(entities)}
    rows = panel["session"].map(row_of).to_numpy()
    cols = panel["entity_id"].map(col_of).to_numpy()
    keep = ~(pd.isna(rows) | pd.isna(cols))
    rows, cols = rows[keep].astype(np.int32), cols[keep].astype(np.int32)
    cube = np.zeros((len(sessions), len(entities), len(columns)), dtype=np.float16)
    for k, name in enumerate(columns):
        values = panel[name].to_numpy(np.float32)[keep] if name in panel.columns \
            else np.zeros(int(keep.sum()), np.float32)
        cube[rows, cols, k] = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float16)
    return cube


def observed_mask(panel: pd.DataFrame, sessions: list[date], entities: list[str]) -> np.ndarray:
    """(세션, 종목) 그 날 패널에 행이 있었나. 창 안에 이 값이 모자라면 예측하지 않는다."""
    row_of = {s: i for i, s in enumerate(sessions)}
    col_of = {e: i for i, e in enumerate(entities)}
    rows = panel["session"].map(row_of).to_numpy()
    cols = panel["entity_id"].map(col_of).to_numpy()
    keep = ~(pd.isna(rows) | pd.isna(cols))
    mask = np.zeros((len(sessions), len(entities)), dtype=bool)
    mask[rows[keep].astype(np.int32), cols[keep].astype(np.int32)] = True
    return mask


def window_batch(cube: np.ndarray, day_index: int, rows: np.ndarray) -> np.ndarray:
    """(종목, N_STEPS, 피처) — 오프셋은 **모두 과거**(0 = 그 날, 양수 = 며칠 전). 미래는 손대지 않는다.

    창 앞이 큐브 밖으로 나가면 0 으로 둔다(결측 = rank-gauss 0). 잘라 쓰지 않는 이유는
    판정 블록이 워밍업 뒤에서 시작하므로 실제로는 거의 일어나지 않고, 일어나도 조용히 망가지지 않게 하려는 것.
    """
    if day_index < 0 or day_index >= cube.shape[0]:
        raise IndexError(f"세션 인덱스 {day_index} 가 큐브 밖이다 (0..{cube.shape[0] - 1})")
    out = np.zeros((len(rows), N_STEPS, cube.shape[2]), dtype=np.float32)
    for k, back in enumerate(TIME_OFFSETS):
        src = day_index - back
        if src >= 0:
            out[:, k, :] = cube[src][rows].astype(np.float32)
    return out


def drop_indices(dropper, groups: dict[str, list[str]], index_of: dict[str, int],
                 rng: np.random.Generator, p: float = GROUP_DROP_P) -> tuple[np.ndarray, np.ndarray]:
    """이번 스텝에 덮을 열 인덱스 — (값을 0 으로, 표지를 1 로). 묶음 고르기는 `kit.drop_groups` 가 한다.

    묶음을 떨어뜨릴 때 **그 묶음의 결측 표지를 1 로 켠다** — 실제 결측과 같은 모양으로 보여야
    모델이 "표지가 0 이면 값이 있다" 는 지름길을 외우지 않는다. kit 은 표지도 열 목록에 넣어 주므로
    여기서 `miss_` 로 갈라 쓴다.
    """
    if p <= 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    names = dropper(groups, rng, p)
    zero = [index_of[c] for c in names if c in index_of and not c.startswith("miss_")]
    flags = [index_of[c] for c in names if c in index_of and c.startswith("miss_")]
    return np.asarray(zero, np.int64), np.asarray(flags, np.int64)


def augment(x: np.ndarray, rng: np.random.Generator, zero_cols: np.ndarray,
            flag_cols: np.ndarray, noise: float = INPUT_NOISE) -> np.ndarray:
    """묶음 단위 피처 드롭아웃(열 목록은 `drop_indices` 가 고른다) + 입력 잡음. 학습에서만 부른다."""
    if noise > 0:
        x = x + rng.normal(0.0, noise, size=x.shape).astype(np.float32)
    if len(zero_cols):
        x[:, :, zero_cols] = 0.0
    if len(flag_cols):
        x[:, :, flag_cols] = 1.0
    return x


@dataclass
class Aug:
    """학습 배치에 걸 억제 장치 묶음. `dropper` 는 `kit.drop_groups` 다 — 규칙을 베끼지 않는다."""

    dropper: Callable[..., list[str]]
    groups: dict[str, list[str]]
    index_of: dict[str, int]
    p: float = GROUP_DROP_P
    noise: float = INPUT_NOISE

    def apply(self, x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        zero, flags = drop_indices(self.dropper, self.groups, self.index_of, rng, self.p)
        return augment(x, rng, zero, flags, self.noise)


# ── 모델 ──────────────────────────────────────────────────────────────────────────

def make_model(seed: int, n_features: int):
    """시간 인코더 → 종목 임베딩 → 같은 (세션, 시장) 종목끼리 attention → 점수. 파라미터 3~4만.

    횡단면 층에는 **위치 인코딩을 넣지 않는다** — 종목 순서는 뜻이 없어야 한다(집합). 그래서 세트 트랜스포머.
    """
    import torch
    from torch import nn

    torch.manual_seed(seed)

    class SetRanker(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.inp = nn.Linear(n_features, D_MODEL)
            self.pos = nn.Parameter(torch.zeros(1, N_STEPS, D_MODEL))
            self.drop = nn.Dropout(DROPOUT)
            time_layer = nn.TransformerEncoderLayer(D_MODEL, HEADS, FFN, DROPOUT, batch_first=True)
            self.time_encoder = nn.TransformerEncoder(time_layer, TIME_LAYERS)
            set_layer = nn.TransformerEncoderLayer(D_MODEL, HEADS, FFN, DROPOUT, batch_first=True)
            self.set_encoder = nn.TransformerEncoder(set_layer, SET_LAYERS)
            self.norm = nn.LayerNorm(D_MODEL)
            self.out = nn.Linear(D_MODEL, 1)

        def forward(self, x):  # type: ignore[no-untyped-def]  # x: (종목, 스텝, 피처)
            h = self.time_encoder(self.drop(self.inp(x)) + self.pos)   # (종목, 스텝, d)
            z = self.norm(h.mean(dim=1))                               # (종목, d) — 종목 임베딩
            z = self.set_encoder(z.unsqueeze(0)).squeeze(0)             # 같은 날 종목끼리 attention
            return self.out(z).squeeze(-1)

    return SetRanker()


def _sets_for_day(day_index: int, observed: np.ndarray, market_id: np.ndarray,
                  targets: dict[int, np.ndarray]) -> list[tuple[np.ndarray, np.ndarray]]:
    """세션 하나를 (시장별) 집합들로 쪼갠다. 시장이 다르면 같은 횡단면이 아니다 — attention 도 나눈다."""
    y = targets.get(day_index)
    if y is None:
        return []
    out = []
    have = observed[day_index] & np.isfinite(y)
    for m in np.unique(market_id):
        rows = np.flatnonzero(have & (market_id == m))
        if len(rows) >= MIN_SET:
            out.append((rows, y[rows]))
    return out


def train_model(cube: np.ndarray, observed: np.ndarray, market_id: np.ndarray,
                targets: dict[int, np.ndarray], fit_days: list[int], val_days: list[int],
                seed: int, aug: Aug, log: TrainLog | None = None, max_epochs: int = MAX_EPOCHS,
                steps_per_epoch: int = STEPS_PER_EPOCH, init_state=None):
    """조기 종료는 **val_days 만** 본다. fit_days·val_days 는 kit.inner_split 이 정한다.

    `init_state` 가 있으면 그 가중치에서 이어 학습한다(웜스타트). 옵티마이저는 항상 새로 만든다 —
    Adam 모멘트를 이어받으면 앞 구간의 그래디언트 크기가 새 구간의 첫 스텝을 지배한다.
    """
    import torch

    log = log if log is not None else TrainLog()
    model = make_model(seed, cube.shape[2])
    if init_state is not None:
        model.load_state_dict(init_state)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    rng = np.random.default_rng(seed)
    best, best_state, bad = -np.inf, None, 0
    pool = np.asarray(fit_days, dtype=np.int64)

    for epoch in range(max_epochs):
        model.train()
        loss_sum, loss_n = 0.0, 0
        if steps_per_epoch and steps_per_epoch < len(pool):
            days = rng.choice(pool, size=steps_per_epoch, replace=False)
        else:
            days = rng.permutation(pool)
        for day_index in days:
            for rows, y in _sets_for_day(int(day_index), observed, market_id, targets):
                if ENTITY_SUBSAMPLE < 1.0:
                    take = rng.random(len(rows)) < ENTITY_SUBSAMPLE
                    if take.sum() < MIN_SET:
                        take[:] = True
                    rows, y = rows[take], y[take]
                x = aug.apply(window_batch(cube, int(day_index), rows), rng)
                opt.zero_grad()
                loss = torch.nn.functional.mse_loss(
                    model(torch.from_numpy(x)), torch.from_numpy(y.astype(np.float32)))
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), CLIP)
                opt.step()
                loss_sum += float(loss.detach())
                loss_n += 1
                log.seen_days.add(int(day_index))
        log.epochs = epoch + 1
        log.train_losses.append(loss_sum / loss_n if loss_n else float("nan"))
        score = _val_score(model, cube, observed, market_id, targets, val_days, log)
        log.val_scores.append(score)
        if score > best + 1e-6:
            best, bad = score, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= PATIENCE:
                # 마지막 허용 에포크에서 걸린 break 는 **조기 종료가 아니다** — 어차피 끝날 차례였다.
                # 이걸 True 로 두면 기록 줄의 "조기 종료 N회" 가 예산을 다 쓴 회차까지 세어 부풀려진다.
                log.stopped_early = epoch + 1 < max_epochs
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return model, log


def _val_score(model, cube: np.ndarray, observed: np.ndarray, market_id: np.ndarray,
               targets: dict[int, np.ndarray], val_days: list[int], log: TrainLog) -> float:
    """내부 검증 점수 = 세션·시장별 순위상관(Spearman)의 평균. 판정 지표와 같은 방향이다."""
    import torch

    model.eval()
    vals: list[float] = []
    for day_index in val_days:
        for rows, y in _sets_for_day(int(day_index), observed, market_id, targets):
            x = window_batch(cube, int(day_index), rows)
            with torch.inference_mode():
                pred = model(torch.from_numpy(x)).numpy()
            if np.std(pred) > 1e-9 and np.std(y) > 1e-9:
                vals.append(float(pd.Series(pred).corr(pd.Series(y), method="spearman")))
            log.val_days.add(int(day_index))
    return float(np.mean(vals)) if vals else -np.inf


#: 시장 코드 → 이름. 예측 프레임은 `market` 열을 **들고 나와야 한다**(kit.split_markets 가 요구한다).
MARKET_NAME = {0: "KR", 1: "US"}


def predict_days(model, cube: np.ndarray, observed: np.ndarray, market_id: np.ndarray,
                 entities: list[str], sessions: list[date], day_indices: list[int]) -> pd.DataFrame:
    import torch

    model.eval()  # dropout 이 켜진 채로 점수를 내면 같은 입력이 세션마다 다른 순위를 낸다
    names = np.asarray(entities, dtype=object)
    frames = []
    for day_index in day_indices:
        have = observed[day_index]
        for m in np.unique(market_id):
            rows = np.flatnonzero(have & (market_id == m))
            if len(rows) < MIN_SET:
                continue
            with torch.inference_mode():
                pred = model(torch.from_numpy(window_batch(cube, day_index, rows))).numpy()
            frames.append(pd.DataFrame({"entity_id": names[rows], "session": sessions[day_index],
                                        "market": MARKET_NAME.get(int(m), "KR"), "pred": pred}))
    return pd.concat(frames, ignore_index=True) if frames else \
        pd.DataFrame(columns=["entity_id", "session", "market", "pred"])


def rank_average(*frames: pd.DataFrame) -> pd.DataFrame:
    """세션 안 백분위로 바꿔 평균 — 변형 BE2(트랜스포머 + GBM(FA))."""
    if not frames:
        return pd.DataFrame(columns=["entity_id", "session", "market", "pred"])
    out = pd.DataFrame()
    for k, frame in enumerate(frames):
        # 백분위는 **(세션, 시장) 안에서** 낸다 — 두 시장을 한 줄로 세우면 시장 수준 차이가 순위에 섞인다.
        part = frame.assign(pred=frame.groupby(["session", "market"])["pred"].rank(pct=True))
        keep = ["entity_id", "session", "market", f"p{k}"]
        part = part.rename(columns={"pred": f"p{k}"})[keep]
        out = part if k == 0 else out.merge(part, on=["entity_id", "session", "market"])
    cols = [c for c in out.columns if c.startswith("p")]
    return out.assign(pred=out[cols].mean(axis=1))[["entity_id", "session", "market", "pred"]]


# ── 워크포워드 ─────────────────────────────────────────────────────────────────────

def run_seed(kit, cube, observed, market_id, entities, cube_sessions, axis_sessions, blocks, targets,
             aug: Aug, seed: int, purge: int = 5,
             max_epochs: int = MAX_EPOCHS, store=None, clock=None, n_seeds: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, list[TrainLog], float]:
    """시드 하나의 워크포워드. **5블록마다 재학습**, 학습은 `kit.train_end`(퍼지+엠바고) 까지만 본다.

    축이 둘이다 — 섞으면 미장이 조용히 사라진다:
      · ``axis_sessions`` — kit 이 블록·학습 끝점을 세는 축(국장 세션). 블록 인덱스는 이쪽 것이다.
      · ``cube_sessions`` — 큐브의 세션 축(**국장 ∪ 미장** 합집합). 60세션 창과 예측은 이쪽에서 돈다.
    국장 축만 쓰면 국장이 쉬는 날의 미장 행이 큐브에서 빠져 미장 창이 뒤틀린다.

    반환: (판정창 예측, 학습창 예측, 재학습 로그, 분). 학습창 예측은 과적합 격차 지표에만 쓴다 —
    **판정에는 절대 들어가지 않는다**(공통 틀 5).

    `store`·`clock` 을 주면 블록마다 `kit.record_progress` 로 진행을 적는다(학습 탭이 이걸 읽는다).
    적는 것은 **학습 손실·내부 검증(부호 뒤집은 순위상관)·조기 종료·에포크·경과**뿐이다 —
    판정 창 성적은 여기서 한 번도 계산하지 않고, 판정이 끝난 뒤 research_trials 에만 적힌다.

    `kit.inner_split` 은 **세션 날짜**를 돌려준다(인덱스가 아니다) — 여기서 큐브 인덱스로 옮긴다.
    """
    began = time_module.monotonic()  # invariant-allow: wallclock — 소요 시간 기록
    index_of = {s: i for i, s in enumerate(cube_sessions)}
    if blocks:
        # **근거를 주석이 아니라 검사로.** 0 으로 채운 창은 아무 경고도 내지 않는다(rank-gauss 뒤 0 은
        # "자료 없음" 이 아니라 "순위 중앙" 이다). 규칙은 kit 한 곳에 있다 — BF·BG 와 같은 함수다. rc=5.
        kit.require_full_window(cube_sessions, axis_sessions[blocks[0][0]], WINDOW, label="60세션 창")
    model, logs, out, train_out = None, [], [], []
    #: 블록 **하나**에 걸린 시간을 적는다(누적이 아니다) — 화면의 예상 완료가 평균 블록 시간 × 남은 블록이다.
    mark = began
    markets = "KR+US" if int(market_id.sum()) and int((market_id == 0).sum()) else ("US" if int(market_id.sum()) else "KR")
    for number, (first, last) in enumerate(blocks):
        if number % RETRAIN_EVERY == 0:
            cut = (kit.train_end(axis_sessions, first) if hasattr(kit, "train_end")
                   else axis_sessions[first - purge - 1])
            fit, val = kit.inner_split([s for s in cube_sessions if s <= cut])
            fit_days = [index_of[s] for s in fit if s in index_of]
            val_days = [index_of[s] for s in val if s in index_of]
            warm = WARM_START and model is not None
            init = ({k: v.detach().clone() for k, v in model.state_dict().items()}
                    if model is not None and warm else None)
            model, log = train_model(cube, observed, market_id, targets, fit_days, val_days,
                                     seed, aug, init_state=init,
                                     max_epochs=min(max_epochs, MAX_EPOCHS_WARM) if warm else max_epochs)
            logs.append(log)
            print(f"  seed {seed} · 재학습 {len(logs)} · 적합 {len(fit_days)} / 검증 {len(val_days)} 세션 "
                  f"(~{cut}) · epoch {log.epochs}"
                  f"{' (조기종료)' if log.stopped_early else ''} · "
                  f"검증 {log.val_scores[-1] if log.val_scores else float('nan'):+.4f} · "
                  f"누적 {(time_module.monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock
            train_out.append(train_window_predictions(model, cube, observed, market_id,
                                                      entities, cube_sessions, log))
        out.append(predict_days(model, cube, observed, market_id, entities, cube_sessions,
                                block_indices(kit, cube_sessions, axis_sessions, first, last)))
        log = logs[-1] if logs else None
        kit.record_progress(
            store, clock, "BE", source="trial_final_transformer",
            market=markets, seed=int(seed), n_seeds=n_seeds or None, block=number, n_blocks=len(blocks),
            epoch=(log.epochs if log else None),
            train_loss=(log.train_losses[-1] if log and log.train_losses else None),
            # 순위상관은 **높을수록** 좋다 — 표 규약대로 부호를 뒤집어 넣는다(metric 에 이름을 적는다).
            val_loss=(-log.val_scores[-1] if log and log.val_scores else None),
            metric="mse / spearman(−)",
            stopped_early=(bool(log.stopped_early) if log else None),
            elapsed_s=time_module.monotonic() - mark,  # invariant-allow: wallclock
            note=f"재학습 {len(logs)}회 · 판정 {axis_sessions[first]}~{axis_sessions[last]}")
        mark = time_module.monotonic()  # invariant-allow: wallclock
    minutes = (time_module.monotonic() - began) / 60  # invariant-allow: wallclock
    return (pd.concat(out, ignore_index=True),
            pd.concat(train_out, ignore_index=True) if train_out else pd.DataFrame(),
            logs, minutes)


def block_indices(kit, cube_sessions: list[date], axis_sessions: list[date],
                  first: int, last: int) -> list[int]:
    """블록이 덮는 **큐브 세션 인덱스**. 경계 규칙은 `kit.block_span` 한 곳에서만 온다.

    `block_span` 은 **반열림** ``[이 블록 시작일, 다음 블록 시작일)`` 을 준다(끝이 None 이면 위쪽 한계 없음).
    닫힌 구간으로 고르면 국장 휴장일이 **한 블록의 끝과 다음 블록의 시작 사이**에 놓일 때 그날 미장 행이
    어느 블록에도 안 든다(BF 담당이 찾았다, 2026-09-27). kit 은 패널 행을 고르는 `block_rows` 를 주지만
    여기서 골라야 하는 것은 큐브의 세션 인덱스라 같은 경계를 `block_span` 에서 받아 쓴다.
    """
    lo, hi = kit.block_span(axis_sessions, first, last)
    return [i for i, s in enumerate(cube_sessions) if lo <= s and (hi is None or s < hi)]


def train_window_predictions(model, cube, observed, market_id, entities, sessions,
                             log: TrainLog, sample: int = 40) -> pd.DataFrame:
    """학습창 대비 판정창 격차(과적합 지표)를 위해 **학습에 쓴 세션**에서도 예측을 낸다.

    판정 블록(20세션)과 견줄 수 있게 표본을 작게 잡는다 — 이 예측은 채점표에 오르지 않는다.
    """
    days = sorted(log.seen_days)
    if not days:
        return pd.DataFrame(columns=["entity_id", "session", "pred"])
    step = max(1, len(days) // sample)
    return predict_days(model, cube, observed, market_id, entities, sessions, days[::step])


# ── 합성 자료(스모크·테스트) ────────────────────────────────────────────────────────

def synthetic(n_sessions: int = 260, n_entities: int = 240, seed: int = 0):
    """알파가 있는 작은 합성 패널. 배선·모양 확인용 — 수치는 뜻이 없다."""
    rng = np.random.default_rng(seed)
    sessions = [(date(2022, 1, 1) + timedelta(days=i)) for i in range(n_sessions)]
    entities = [f"E{i:04d}" for i in range(n_entities)]
    feats = [f"f{i}" for i in range(8)]
    groups = {"gA": feats[:3], "gB": feats[3:6], "gC": feats[6:]}
    rows = []
    for s in sessions:
        x = rng.normal(size=(n_entities, len(feats))).astype(np.float32)
        y = 0.4 * x[:, 0] + 0.2 * x[:, 3] + rng.normal(0, 0.9, n_entities)
        frame = pd.DataFrame(x, columns=feats)
        frame["entity_id"], frame["session"] = entities, s
        frame["market"] = ["US" if i % 2 else "KR" for i in range(n_entities)]
        frame["y5"] = (pd.Series(y).rank(pct=True) - 0.5).to_numpy(np.float32)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True), feats, groups, sessions


# ── 본 실행 ────────────────────────────────────────────────────────────────────────

def _feature_layout(feats: list[str]) -> tuple[list[str], dict[str, int]]:
    """큐브 열 순서(= kit.feature_names 순서, is_us 포함)와 열 이름 → 인덱스."""
    columns = list(feats)
    if "is_us" not in columns:
        columns.append("is_us")
    return columns, {c: i for i, c in enumerate(columns)}


def target_columns(panel: pd.DataFrame, sessions: list[date],
                   entities: list[str]) -> dict[int, np.ndarray]:
    """세션 인덱스 → (종목,) y5 벡터. 없는 종목은 NaN 이고 `_sets_for_day` 가 걸러낸다."""
    row_of = {s: i for i, s in enumerate(sessions)}
    col_of = {e: i for i, e in enumerate(entities)}
    out: dict[int, np.ndarray] = {}
    have = panel[panel["y5"].notna()]
    for s, part in have.groupby("session"):
        if s not in row_of:
            continue
        idx = part["entity_id"].map(col_of)
        keep = idx.notna()
        column = np.full(len(entities), np.nan, dtype=np.float32)
        column[idx[keep].to_numpy(np.int64)] = part.loc[keep[keep].index, "y5"].to_numpy(np.float32)
        out[row_of[s]] = column
    return out


def score_seed(kit, pred: pd.DataFrame, books: dict, y: pd.DataFrame,
               control: pd.DataFrame | None = None) -> dict[str, float]:
    """시드 하나의 예측 → `kit.evaluate_all`(시장별 + 합동). 시장별 주요 지표도 같이 남긴다.

    시장을 섞어 한 포트로 만들지 않는다 — 비용·벤치·거래가능 명단이 시장마다 다르다(kit.MarketBook).
    """
    if pred.empty:
        return {}
    by_market, pooled = kit.evaluate_all(pred, books, y=y, control=control)
    for market, m in by_market.items():
        for k in ("ann", "mdd", "turn", "ic"):
            if k in m:
                pooled[f"{market}_{k}"] = m[k]
    return pooled


def _load_seed(cache: Path | None, seed: int) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """끝난 시드의 예측을 캐시에서. 없으면 None. 10시간 실행이 중간에 죽어도 이어 간다."""
    if cache is None:
        return None
    judge_path = cache / f"seed{seed}-judge.parquet"   # invariant-allow: data-access — 창고가 아닌 작업 파일
    train_path = cache / f"seed{seed}-train.parquet"   # invariant-allow: data-access — 창고가 아닌 작업 파일
    if not (judge_path.exists() and train_path.exists()):
        return None
    out = []
    for path in (judge_path, train_path):
        frame = pd.read_parquet(path)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        frame["session"] = pd.to_datetime(frame["session"]).dt.date
        out.append(frame)
    return out[0], out[1]


def _save_seed(cache: Path | None, seed: int, judge_pred: pd.DataFrame, train_pred: pd.DataFrame) -> None:
    if cache is None:
        return
    judge_pred.to_parquet(cache / f"seed{seed}-judge.parquet", index=False)  # invariant-allow: data-access — 작업 파일
    train_pred.to_parquet(cache / f"seed{seed}-train.parquet", index=False)  # invariant-allow: data-access — 작업 파일


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선·시간 확인용, 수치는 안 찍는다")
    parser.add_argument("--synthetic", action="store_true", help="창고를 읽지 않고 합성 패널로 배선 확인")
    parser.add_argument("--seeds", type=int, default=len(SEEDS))
    parser.add_argument("--max-epochs", type=int, default=MAX_EPOCHS)
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--root", default="data")
    parser.add_argument("--no-progress", action="store_true", help="trial_progress 기록을 끈다(기본은 적는다)")
    args = parser.parse_args(argv)
    if args.save and (args.smoke or args.synthetic):
        print("--save 는 본 판정에서만. --smoke/--synthetic 과 같이 못 쓴다.", file=sys.stderr)
        return 2
    import torch

    torch.set_num_threads(args.threads)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else "미고정"
    print(f"=== 시행 BE — {PROTOCOL} (해시 {digest}) ===", flush=True)

    # **합성이든 실자료든 같은 kit 을 쓴다.** 최소 대역을 따로 두면 그것이 진짜 kit 과 갈리는 날
    # 스모크가 거짓으로 통과한다(kit 담당 지적). 바꿔 끼우는 것은 **패널 하나**뿐이다.
    from tools import final_round_kit as kit
    if args.synthetic:
        panel, feats, groups, sessions = synthetic()
    else:
        panel, feats, groups, sessions = kit.load_full_panel(("KR", "US"), root=args.root)
    if not (args.smoke or args.synthetic):
        # **굽기 관문이 학습 앞이다.** 원피처 캐시가 판정 창을 못 덮으면 기준 ①~⑤(대 C0)가 사실상 C0 대 C0 이
        # 된다 — 9시간을 태우고 나서 알면 하룻밤을 버린다. rc=4 로 멈춘다(kit.COVERAGE_EXIT).
        kit.require_full_coverage()
    blocks = list(kit.blocks(sessions))
    if args.smoke:
        blocks = blocks[: args.smoke]

    entities = sorted(panel["entity_id"].unique())
    columns, index_of = _feature_layout(list(feats))
    if "is_us" not in panel.columns:
        panel = panel.assign(is_us=(panel["market"] == "US").astype(np.float32))
    # 큐브 축은 **두 시장 세션의 합집합**. kit 의 `sessions`(국장)는 블록·학습 끝점을 세는 데만 쓴다.
    cube_sessions = sorted(panel["session"].unique())
    cube = build_cube(panel, columns, cube_sessions, entities)
    observed = observed_mask(panel, cube_sessions, entities)
    market_of = panel.drop_duplicates("entity_id").set_index("entity_id")["market"]
    market_id = (market_of.reindex(entities).fillna("KR") == "US").to_numpy(np.int8)
    del market_of
    targets = target_columns(panel, cube_sessions, entities)
    aug = Aug(kit.drop_groups, dict(groups), index_of)
    print(f"패널 {len(panel):,}행 · 블록 축 세션 {len(sessions)} · 큐브 축 세션 {len(cube_sessions)} · "
          f"종목 {len(entities):,} · 피처 {len(columns)} · 묶음 {len(groups)} · 블록 {len(blocks)} · "
          f"큐브 {cube.nbytes / 1e6:.0f}MB · 시간격자 {N_STEPS}스텝 {TIME_OFFSETS}", flush=True)

    seeds = SEEDS[: args.seeds]
    cache = None if (args.synthetic or args.smoke) else SEED_CACHE
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
    # 진행 기록은 **본 판정에서만** 적는다 — 합성·스모크 행이 섞이면 화면의 진행률이 거짓이 된다.
    progress_store = None if (args.synthetic or args.smoke or args.no_progress) else kit.Store(root=Path(args.root))
    clock = LiveClock()
    preds, train_preds, all_logs, minutes = {}, {}, {}, {}
    for seed in seeds:
        done = _load_seed(cache, seed)
        if done is not None:
            preds[seed], train_preds[seed] = done
            all_logs[seed], minutes[seed] = [], 0.0
            print(f"  seed {seed} · 캐시 사용({len(preds[seed]):,}행) — 다시 학습하지 않는다", flush=True)
            continue
        preds[seed], train_preds[seed], all_logs[seed], minutes[seed] = run_seed(
            kit, cube, observed, market_id, entities, cube_sessions, sessions, blocks, targets,
            aug, seed, max_epochs=args.max_epochs, store=progress_store, clock=clock,
            n_seeds=len(seeds))
        _save_seed(cache, seed, preds[seed], train_preds[seed])
    del cube
    if args.smoke or args.synthetic:
        print(f"예측 {sum(len(p) for p in preds.values()):,}행 · 학습창 표본 "
              f"{sum(len(p) for p in train_preds.values()):,}행 · {sum(minutes.values()):.1f}분 — "
              "배선 확인만(수치 안 찍음)", flush=True)
        return 0

    # C0(현행 6점수 GBM) · C1(같은 GBM 을 FA 로) — **`tools/final_round_controls.py --bake` 가 미리 굽는다.**
    # 여기서 다시 적합하지 않고 그 캐시를 읽는다: 대조군을 도구마다 따로 만들면 9/22 의
    # "같은 산출물이라던 T0·B0·C0 이 1.6~1.7%p 달랐다" 가 되풀이된다.
    # `require_controls` 는 캐시가 없으면 어느 파일이 없는지 찍고 SystemExit(3) — 자기 GBM 을 짜지 않는다.
    ctrl = kit.require_controls(panel, seeds=seeds)
    store = kit.Store(root=Path(args.root))
    # **panel 을 반드시 준다.** 미장 포트는 시행 AT 채택 M1 합성을 거치고, 그 재료(fund_raw·has_fund)가
    # 패널에 있다. 빼면 kit.evaluate 가 미장에서 ValueError 로 멈춘다(조용히 다른 규칙으로 도는 것보다 낫다).
    books = kit.market_books(store, sessions, panel=panel)
    y = panel[["entity_id", "session", "y5"]]
    c0 = {s: score_seed(kit, ctrl["C0"][s], books, y) for s in seeds}
    c1 = {s: score_seed(kit, ctrl["C1"][s], books, y) for s in seeds}

    be1 = {s: score_seed(kit, preds[s], books, y, control=ctrl["C0"][s]) for s in seeds}
    be2 = {s: score_seed(kit, rank_average(preds[s], ctrl["C1"][s]), books, y) for s in seeds}
    # 과적합 지표: 같은 모델이 학습창에서 낸 성적과 판정창 성적의 차. 학습창 성적은 **판정에 쓰지 않는다**.
    train_scored = {s: score_seed(kit, train_preds[s], books, y) for s in seeds}
    gap = kit.overfit_gap(train_scored, be1)

    early = sum(log.stopped_early for logs in all_logs.values() for log in logs)
    epochs = [log.epochs for logs in all_logs.values() for log in logs]
    lines = [f"기록: 시드 {len(seeds)} · 재학습 {sum(len(v) for v in all_logs.values())}회"
             f"(조기 종료 {early} · 에포크 중앙값 {int(np.median(epochs)) if epochs else 0}) · "
             f"학습+추론 {sum(minutes.values()):.0f}분 · 최대 RSS {kit.rss_mb():.0f}MB",
             "기록: 과적합 격차(학습창 − 판정창) " + " · ".join(f"{k} {v:+.4f}" for k, v in gap.items()),
             f"기록: 상위 24 겹침(C0 대비) {np.mean([m.get('overlap', np.nan) for m in be1.values()]):.0%}"]
    verdicts = {}
    for label, results in (("BE1 단독", be1), ("BE2 = BE1+C1 순위평균", be2)):
        block, verdict = kit.judge(results, c0, c1, label=f"{label}: ")
        lines += block
        verdicts[label] = verdict
    verdict = " | ".join(f"{k} → {v}" for k, v in verdicts.items())
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "final-model-round-2026-10:BE", "valid_from": now, "observed_at": now,
            "source": "trial_final_transformer", "market": "KR", "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-final-transformer-BE-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/BE · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
