"""시행 BE3 — BE1 세트 트랜스포머 + 자기지도 사전학습(가림 묶음 복원 + 대조 학습).

    .venv/bin/python tools/trial_be3_pretrain.py coverage                         # 컷오프 이전 FA 묶음 채움률(수익·라벨 안 봄)
    .venv/bin/python tools/trial_be3_pretrain.py pretrain --i-registered          # 사전학습 한 번 → data/_diag/final-round/BE3/pretrain.pt
    .venv/bin/python tools/trial_be3_pretrain.py train --i-registered --seeds 0   # 시드별 미세조정(BE1 과 같은 워크포워드) → 시드 캐시
    .venv/bin/python tools/trial_be3_pretrain.py judge --i-registered [--save]    # 기준 ①~⑧ · research_trials `be3-pretrain-2026-10:BE3`
    .venv/bin/python tools/trial_be3_pretrain.py pretrain --synthetic             # 합성 스모크(창고를 안 읽는다)
    .venv/bin/python tools/trial_be3_pretrain.py train --synthetic --smoke 2 [--no-pretrain]

사전등록 `docs/protocols/be3-pretrain-2026-10.md`. 머리줄이 `> **초안` 이면 실자료 pretrain·train·judge 를 모두 거부한다
(`--i-registered` 도 필요). coverage 는 등록 전 점검이라 거부하지 않는다 — 판정 창 행도 라벨도 읽지 않는다.

**한 축만 바꾼다.** 구조·하이퍼·미세조정 절차·시드·블록·포트는 BE1(`tools/trial_final_transformer.py`)과 같고,
이 파일은 BE1 의 함수(`make_model`·`train_model`·`run_seed`·큐브·억제 장치)를 **import 해서 부른다** — 베끼지 않는다.
다른 것은 첫 재학습의 시작 가중치 하나다: BE1 은 `make_model(seed)` 무작위, BE3 은 **사전학습한 인코더 + 그 시드의 무작위 머리**
(`transfer_state`). `--no-pretrain` 이면 `run_seed(init_state=None)` 로 BE1 과 같은 경로가 된다(테스트가 비트 동일을 지킨다).

**누수 차단이 최우선이다.** 사전학습 자료는 `pretrain_cutoff(sessions)` = 판정 창 첫 채점 세션의 학습 끝점
(`kit.train_end` — 퍼지 5 + 엠바고 5) **이하**만이다. 판정 창 자료는 라벨이 없어도 쓰지 않는다.
  · 로더(`load_pretrain_panel`)는 창 끝을 컷오프로 넘기고(창고 조회는 as_of = 컷오프 23:00 UTC — kit 의 미장 패널·밸류업),
    돌아온 표에 컷오프 뒤 행이 **하나라도** 있으면 `PretrainLeak`(rc 7)으로 멈춘다 — 조용히 잘라 쓰지 않는다.
  · 라벨 열(`y5`)은 읽자마자 버린다 — 사전학습은 라벨을 한 번도 보지 않는다.
  · `pretrain` 은 큐브 세션 축의 끝이 컷오프를 넘으면 멈추고, 본 세션(창 포함)을 `PretrainLog.seen` 에 남긴다.
  · 체크포인트에 컷오프·본 세션의 끝·열 목록을 적고, `train` 이 판정 축에서 컷오프를 다시 계산해 **같지 않으면** rc 7.
사전학습 가중은 첫 재학습의 학습 끝점(= 컷오프) 이전만 봤으므로, 그 뒤 모든 재학습의 학습 끝점보다도 앞이다.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
import time as time_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools import final_round_kit as kit  # noqa: E402
from tools import trial_final_transformer as be  # noqa: E402

PROTOCOL = Path("docs/protocols/be3-pretrain-2026-10.md")
ENTITY = "be3-pretrain-2026-10:BE3"
FAMILY = "ranker"
SOURCE = "trial_be3_pretrain"
#: 사전학습 체크포인트·시드별 예측. BE1 캐시(`kit.CACHE / "BE"`)는 **읽기만** 한다.
BE3_DIR = kit.CACHE / "BE3"
CKPT_NAME = "pretrain.pt"
#: 회차 BE1 의 시드별 판정 예측 — 재계산하지 않는다(대조 BE1·BE2 의 재료).
BE1_DIR = kit.CACHE / "BE"

#: 종료 코드. 1 등록 거부 · 3 입력(대조군·BE1·BE3 예측·체크포인트) 없음 · 7 사전학습 누수·체크포인트 불일치 · 9 채점 세션 불일치.
#: kit 의 4(굽기)·5(창)는 그대로 올라온다.
REGISTER_EXIT, INPUT_EXIT, LEAK_EXIT, SPAN_EXIT = 1, 3, 7, 9

# ── 등록 때 고정하는 사전학습 하이퍼파라미터 (판정 창에서 고르지 않는다 · 격자 없음) ─────────────
#: 가림 확률 — (종목, 묶음) 쌍마다. BERT 의 15% 를 따르고, BE1 의 묶음 드롭아웃 확률(0.15)과 같은 값이다.
MASK_P = 0.15
#: 채움률 관문 — 컷오프 이전 채움이 이보다 낮은 (시장, 묶음)은 사전학습에서 **없는 것으로 가린다**
#: (값 0 · 표지 1 · 복원 대상 아님). 6차 G8 커버리지 관문(10%)과 같은 값이다.
MIN_FILL = 0.10
#: 대조 학습의 양성 쌍 = 같은 종목의 창 둘, 끝이 이만큼 떨어진다. 재조정 주기(10세션)와 같다 —
#: "한 보유 기간 동안 같은 종목의 표현은 가까워야 한다" 는 뜻이고, 판정 지표(10세션 재조정 포트)의 시간 척도다.
CONTRAST_SHIFT = kit.REBALANCE_EVERY
#: InfoNCE 온도 — SimCLR 기본값. 코사인 유사도 위에서.
TAU = 0.10
#: 손실 가중. 두 손실을 **우연 수준 = 1** 로 맞춘 뒤 1:1 로 더한다: 복원 MSE 는 rank-gauss 목표(분산 ≈ 1)라 0 을 내면 ≈ 1,
#: InfoNCE 는 우연이면 ln(N) 이라 ln(N) 으로 나눈다. 어느 과제를 더 믿을 근거가 등록 시점에 없으므로 같게 둔다.
W_REC, W_NCE = 1.0, 1.0
#: 사전학습은 **한 번**, 이 시드로. 미세조정 시드 0~4 가 모두 이 인코더에서 출발하고 머리(`out`)는 시드마다 BE1 과 같은 무작위다.
PRETRAIN_SEED = 0
#: 에포크 = 사전학습 앵커 세션 전부를 한 번(두 시장 집합). 조기 종료는 앵커 세션의 마지막 20%(퍼지 5) 검증 손실로만.
PRETRAIN_EPOCHS, PRETRAIN_PATIENCE = 6, 2
#: 검증 손실용 가림·잡음 난수는 고정한다 — 에포크마다 같은 문제를 풀어야 비교가 된다.
VAL_RNG_SEED = 12345

# ── 채택 기준 ⑦⑧ (①~⑥ 은 kit.judge) ────────────────────────────────────────────────
#: ⑦ BE3+C1 순위 평균이 BE2(BE1+C1) 보다 시드 평균 연 +1%p 이상이고, 시드의 80% 이상에서 높다.
GATE_ENS_MEAN, GATE_ENS_SHARE = 0.01, 0.80
#: ⑧ BE3 단독의 박스 국면 연수익이 BE1 단독보다 높다(시드 평균, 0 초과). BE1 은 박스에서 C0 에 −1.3%p 로 졌다.
GATE_BOX = 0.0
JUDGE_KEYS = ("ann", "box_ann", "rally_ann", "mdd", "turn", "ic")
KEYS = ["entity_id", "session", "market"]


class PretrainLeak(RuntimeError):
    """사전학습 자료에 컷오프 뒤 행이 섞였거나 체크포인트가 지금 판정 축과 안 맞는다 — rc 7."""


# ── 컷오프 · 로더 ──────────────────────────────────────────────────────────────────

def pretrain_cutoff(sessions: Sequence[date]) -> date:
    """사전학습이 볼 수 있는 마지막 세션 = 판정 창 **첫 채점 블록의 학습 끝점**(`kit.train_end`, 퍼지 5 + 엠바고 5).

    BE1 의 첫 재학습이 라벨로 보는 마지막 날과 같다. 사전학습은 그 날까지를 라벨 **없이** 본다 — 그래서 그 뒤
    어떤 재학습보다도 더 많이 알지 않는다.
    """
    bl = kit.blocks(list(sessions))
    if not bl:
        raise ValueError("판정 블록이 없다 — 세션 축이 너무 짧다")
    return kit.train_end(list(sessions), bl[0][0])


def enforce_cutoff(panel: pd.DataFrame, cutoff: date, *, where: str = "사전학습 패널") -> None:
    """컷오프 뒤 행이 **하나라도** 있으면 멈춘다. 잘라 쓰지 않는다 — 로더가 창을 어겼다는 뜻이고 그것은 결함이다."""
    if len(panel) == 0:
        raise PretrainLeak(f"{where}: 행이 없다 — 컷오프 {cutoff} 이전 자료를 못 읽었다")
    last = max(panel["session"])
    if last > cutoff:
        n = int((panel["session"] > cutoff).sum())
        raise PretrainLeak(f"{where}: 컷오프 {cutoff} 뒤 행 {n:,}개(마지막 {last}) — 사전학습은 판정 창을 보지 않는다")


def load_pretrain_panel(cutoff: date, *, root: str | Path = "data",
                        loader: Callable[..., tuple[pd.DataFrame, list[str], dict[str, list[str]], list[date]]] | None = None,
                        start: date = kit.PANEL_FIRST) -> tuple[pd.DataFrame, list[str], dict[str, list[str]]]:
    """사전학습 패널 — 창 [start, cutoff], FA 76열(판정 패널과 같은 묶음·같은 규칙), **라벨 없음**.

    ``loader`` 는 기본이 `kit.load_full_panel` 이다(테스트가 바꿔 낀다). 창 끝을 컷오프로 넘기므로 kit 의 창고 조회
    (미장 가격·밸류업 공시)는 as_of = 컷오프 23:00 UTC 이고, 진단 캐시는 세션 ≤ 컷오프로 잘린다.
    돌아온 표를 `enforce_cutoff` 로 한 번 더 검사한다 — 로더가 창을 어기면 여기서 멈춘다.
    """
    load = loader or kit.load_full_panel
    panel, feats, groups, _sessions = load(("KR", "US"), (start, cutoff), root=root,
                                           cache_dir=kit.CACHE / "BE3" / "panel")
    # 라벨은 읽자마자 버린다 — y5 는 세션 뒤 5일 수익이라 컷오프 뒤 가격을 품고 있다. 재무 기록(M1 합성 재료)도 쓰지 않는다.
    panel = panel.drop(columns=[c for c in ("y5", "fund_raw", "has_fund") if c in panel.columns])
    enforce_cutoff(panel, cutoff)
    return panel, list(feats), dict(groups)


def judge_sessions(cache: Path = kit.CACHE, window: tuple[date, date] = (kit.JUDGE_START, kit.JUDGE_END)) -> list[date]:
    """판정 패널의 국장 세션 축 — 조각 캐시에서 `session` 열만 읽는다(라벨·피처 안 읽음). 컷오프 계산용."""
    import pyarrow.parquet as pq  # invariant-allow: data-access — 창고가 아닌 작업 캐시(kit 과 같은 조각)

    tag = f"KR+US-{window[0]:%Y%m%d}-{window[1]:%Y%m%d}"
    path = cache / f"panel-KR-{tag}.parquet"  # invariant-allow: data-access — 작업 캐시(kit 과 같은 조각)
    if not path.exists():
        print(f"판정 패널 캐시가 없다: {path} — scripts/final_round_bake.sh 가 먼저다", flush=True)
        raise SystemExit(INPUT_EXIT)
    days = pq.read_table(path, columns=["session"]).column(0).to_pandas()  # invariant-allow: data-access — 작업 캐시
    return sorted(set(pd.to_datetime(days).dt.date))


# ── 채움률 ──────────────────────────────────────────────────────────────────────────

def group_fill(panel: pd.DataFrame, groups: dict[str, list[str]]) -> pd.DataFrame:
    """(시장, 묶음) 채움률 — 표지가 있는 묶음은 `표지 == 0` 비율, 표지가 없는 묶음(score)은 열마다 `값 ≠ 0` 비율의 평균.

    kit 패널은 결측을 rank-gauss 0 으로 채웠으므로 표지 없는 묶음은 0 을 결측으로 센다(중앙값 정확히 0 인 행은 N 분의 1 이하).
    """
    rows = []
    for market, part in panel.groupby("market"):
        for name, cols in groups.items():
            flags = [c for c in cols if c.startswith("miss_")]
            values = [c for c in cols if not c.startswith("miss_") and c in part.columns]
            if flags and flags[0] in part.columns:
                fill = float((part[flags[0]] < 0.5).mean())
            elif values:
                fill = float(np.mean([(part[c] != 0).mean() for c in values]))
            else:
                fill = 0.0
            rows.append({"market": market, "group": name, "n_cols": len(values), "rows": len(part), "fill": fill})
    return pd.DataFrame(rows)


def fill_by_period(panel: pd.DataFrame, groups: dict[str, list[str]],
                   edges: Sequence[date]) -> pd.DataFrame:
    """`group_fill` 을 구간별로 — 열 이름 `YYYY-MM~` (구간 시작). 국장 원피처·G 가 2022-04 부터라는 것을 보이려는 표."""
    out = None
    bounds = [*edges, date.max]
    for lo, hi in zip(bounds[:-1], bounds[1:], strict=False):
        part = panel[(panel["session"] >= lo) & (panel["session"] < hi)]
        if part.empty:
            continue
        f = group_fill(part, groups).set_index(["market", "group"])["fill"].rename(f"{lo:%Y-%m}~")
        out = f.to_frame() if out is None else out.join(f, how="outer")
    return out.reset_index() if out is not None else pd.DataFrame()


def pretrain_channels(fill: pd.DataFrame, min_fill: float = MIN_FILL) -> dict[str, set[str]]:
    """시장 → 사전학습에 쓰는 묶음 이름. 채움률이 `min_fill` 미만인 묶음은 빠진다(그 시장에서 없는 것으로 가린다)."""
    out: dict[str, set[str]] = {}
    for market, part in fill.groupby("market"):
        out[str(market)] = {str(g) for g, f in zip(part["group"], part["fill"], strict=False) if f >= min_fill}
    return out


def channel_masks(groups: dict[str, list[str]], index_of: dict[str, int],
                  channels: dict[str, set[str]]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """시장 코드(0 국장 · 1 미장) → (0 으로 둘 열, 1 로 둘 표지) — 빠진 묶음을 실제 결측과 같은 모양으로 만든다."""
    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for code, name in be.MARKET_NAME.items():
        keep = channels.get(name, set())
        zero, flags = [], []
        for g, cols in groups.items():
            if g in keep:
                continue
            for c in cols:
                if c not in index_of:
                    continue
                (flags if c.startswith("miss_") else zero).append(index_of[c])
        out[code] = (np.asarray(zero, np.int64), np.asarray(flags, np.int64))
    return out


# ── 사전학습 ────────────────────────────────────────────────────────────────────────

@dataclass
class PretrainLog:
    """사전학습이 본 것. **seen** 은 창까지 포함해 입력으로 읽은 세션 전부 — 누수 테스트가 이것의 최댓값을 컷오프와 견준다."""
    seen: set[date] = field(default_factory=set)
    anchors: set[date] = field(default_factory=set)
    val_anchors: set[date] = field(default_factory=set)
    train_losses: list[float] = field(default_factory=list)
    val_losses: list[float] = field(default_factory=list)
    rec_losses: list[float] = field(default_factory=list)
    nce_losses: list[float] = field(default_factory=list)
    epochs: int = 0
    stopped_early: bool = False
    minutes: float = 0.0


def encode(model, x):  # type: ignore[no-untyped-def]
    """BE1 `SetRanker.forward` 에서 마지막 `out` 만 뺀 것 — (종목, d) 표현. `model.out(encode(x)) == model(x)` 를 테스트가 지킨다."""
    h = model.time_encoder(model.drop(model.inp(x)) + model.pos)
    z = model.norm(h.mean(dim=1))
    return model.set_encoder(z.unsqueeze(0)).squeeze(0)


def make_heads(n_features: int, seed: int = PRETRAIN_SEED):  # type: ignore[no-untyped-def]
    """사전학습 전용 머리 — 복원(d → 피처 수)과 대조 투영(d → d → d). **미세조정으로 넘어가지 않는다.**"""
    import torch
    from torch import nn

    torch.manual_seed(seed + 1)

    class Heads(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.rec = nn.Linear(be.D_MODEL, n_features)
            self.proj = nn.Sequential(nn.Linear(be.D_MODEL, be.D_MODEL), nn.ReLU(), nn.Linear(be.D_MODEL, be.D_MODEL))

    return Heads()


def group_index(groups: dict[str, list[str]], index_of: dict[str, int]) -> dict[str, tuple[np.ndarray, int | None]]:
    """묶음 → (값 열 인덱스, 표지 열 인덱스 또는 None)."""
    out: dict[str, tuple[np.ndarray, int | None]] = {}
    for g, cols in groups.items():
        vals = [index_of[c] for c in cols if c in index_of and not c.startswith("miss_")]
        flag = next((index_of[c] for c in cols if c in index_of and c.startswith("miss_")), None)
        out[g] = (np.asarray(vals, np.int64), flag)
    return out


def apply_channel_mask(x: np.ndarray, mask: tuple[np.ndarray, np.ndarray] | None) -> np.ndarray:
    """채움률 관문에서 빠진 묶음을 결측 모양(값 0 · 표지 1)으로. 제자리에서 바꾼다."""
    if mask is None:
        return x
    zero, flags = mask
    if len(zero):
        x[:, :, zero] = 0.0
    if len(flags):
        x[:, :, flags] = 1.0
    return x


def mask_groups(x: np.ndarray, gidx: dict[str, tuple[np.ndarray, int | None]], keep: set[str],
                rng: np.random.Generator, p: float = MASK_P,
                ref: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """가림 묶음 복원의 입력·목표 표지. (종목, 묶음) 쌍마다 확률 p 로 **창 전체**에서 그 묶음을 가린다.

    · 결측 표지를 존중한다 — 그 날(오프셋 0) 표지가 1 인 묶음(자료 없음)은 가리지 않고 복원 대상도 아니다.
      표지 없는 묶음(score)은 그 날 값이 하나라도 0 이 아니면 있는 것으로 본다.
    · 가린 자리는 실제 결측과 같은 모양(값 0 · 표지 1)으로 보인다 — BE1 드롭아웃과 같은 규칙.
    · 창 전체를 가리는 이유: 한 날만 가리면 느린 피처(재무·내부자)는 전날 값을 베끼면 풀린다.
    ``ref`` 는 "그 날 있었나" 를 판단할 **잡음 전** 입력이다(잡음이 0 을 0 아닌 값으로 바꾸므로). 없으면 x 로 본다.
    돌려주는 것: (가린 입력, (종목, 피처) 목표 표지 — 복원 손실을 셀 칸).
    """
    target = np.zeros((x.shape[0], x.shape[2]), dtype=bool)
    out = x.copy()
    for g, (vals, flag) in gidx.items():
        if g not in keep or not len(vals):
            continue
        now = (ref if ref is not None else x)[:, 0, :]
        present = (now[:, flag] < 0.5) if flag is not None else (np.abs(now[:, vals]) > 0).any(axis=1)
        pick = present & (rng.random(x.shape[0]) < p)
        if not pick.any():
            continue
        rows = np.flatnonzero(pick)
        out[np.ix_(rows, np.arange(x.shape[1]), vals)] = 0.0
        if flag is not None:
            out[rows, :, flag] = 1.0
        target[np.ix_(rows, vals)] = True
    return out, target


def info_nce(za, zb, tau: float = TAU):  # type: ignore[no-untyped-def]
    """대칭 InfoNCE — 같은 행이 양성, 같은 집합(같은 세션·시장)의 다른 종목이 음성. **ln(N) 으로 나눠** 우연 수준을 1 로."""
    import torch
    import torch.nn.functional as F

    a, b = F.normalize(za, dim=-1), F.normalize(zb, dim=-1)
    logits = a @ b.T / tau
    labels = torch.arange(len(a))
    loss = 0.5 * (F.cross_entropy(logits, labels) + F.cross_entropy(logits.T, labels))
    return loss / math.log(max(len(a), 2))


def _pair_rows(observed: np.ndarray, market_id: np.ndarray, day: int, shift: int) -> dict[int, np.ndarray]:
    """시장 → 두 창(끝 day, day − shift) 모두에 행이 있는 종목. MIN_SET 미만 집합은 뺀다(BE1 규칙)."""
    have = observed[day] & observed[day - shift]
    out = {}
    for m in np.unique(market_id):
        rows = np.flatnonzero(have & (market_id == m))
        if len(rows) >= be.MIN_SET:
            out[int(m)] = rows
    return out


def pretrain_step_loss(model, heads, cube: np.ndarray, day: int, rows: np.ndarray, market: int,
                       gidx, keep: set[str], cmask, aug: be.Aug | None, rng: np.random.Generator,
                       shift: int = CONTRAST_SHIFT):  # type: ignore[no-untyped-def]
    """집합 하나의 (전체, 복원, 대조) 손실. 두 창 모두 `be.window_batch` — 오프셋은 과거뿐이다."""
    import torch

    xa = apply_channel_mask(be.window_batch(cube, day, rows), cmask)
    xb = apply_channel_mask(be.window_batch(cube, day - shift, rows), cmask)
    ref = xa.copy()
    truth = ref[:, 0, :]
    if aug is not None:
        xa, xb = aug.apply(xa, rng), aug.apply(xb, rng)
    xa, target = mask_groups(xa, gidx, keep, rng, ref=ref)
    ha = encode(model, torch.from_numpy(xa))
    hb = encode(model, torch.from_numpy(xb))
    if target.any():
        pred = heads.rec(ha)
        t = torch.from_numpy(target)
        rec = ((pred - torch.from_numpy(truth)) ** 2)[t].mean()
    else:
        rec = ha.sum() * 0.0
    nce = info_nce(heads.proj(ha), heads.proj(hb))
    return W_REC * rec + W_NCE * nce, rec, nce


def anchor_days(cube_sessions: Sequence[date], shift: int = CONTRAST_SHIFT) -> list[int]:
    """앵커(늦은 창의 끝) 인덱스 — 두 창이 모두 큐브 안에서 꽉 차는 날만(앞이 0 으로 채워진 창으로 배우지 않는다)."""
    first = be.WINDOW - 1 + shift
    return list(range(first, len(cube_sessions)))


def pretrain(cube: np.ndarray, observed: np.ndarray, market_id: np.ndarray, cube_sessions: Sequence[date],
             cutoff: date, gidx, channels: dict[int, set[str]], cmasks: dict[int, tuple[np.ndarray, np.ndarray]],
             aug: be.Aug | None, *, seed: int = PRETRAIN_SEED, epochs: int = PRETRAIN_EPOCHS,
             patience: int = PRETRAIN_PATIENCE, steps_per_epoch: int | None = None,
             log: PretrainLog | None = None, progress: Callable[..., Any] | None = None):  # type: ignore[no-untyped-def]
    """사전학습 한 번. 돌려주는 것: (BE1 구조 모델의 state_dict, PretrainLog). 머리는 버린다.

    **누수 관문**: 큐브 세션 축의 끝이 컷오프를 넘으면 시작하지 않는다(`PretrainLeak`). 라벨 인자가 아예 없다.
    조기 종료는 앵커 세션의 마지막 20%(`kit.inner_split`, 퍼지 5)에서 고정 난수로 잰 검증 손실만 본다.
    최적화는 BE1 과 같은 AdamW·학습률·weight decay·클리핑(`be.LR`·`be.WEIGHT_DECAY`·`be.CLIP`).
    """
    import torch

    log = log if log is not None else PretrainLog()
    if max(cube_sessions) > cutoff:
        raise PretrainLeak(f"사전학습 큐브의 마지막 세션 {max(cube_sessions)} 이 컷오프 {cutoff} 뒤다")
    began = time_module.monotonic()  # invariant-allow: wallclock — 소요 시간 기록
    model = be.make_model(seed, cube.shape[2])
    heads = make_heads(cube.shape[2], seed)
    params = [*model.parameters(), *heads.parameters()]
    opt = torch.optim.AdamW(params, lr=be.LR, weight_decay=be.WEIGHT_DECAY)
    rng = np.random.default_rng(seed)
    days = anchor_days(cube_sessions)
    fit_s, val_s = kit.inner_split([cube_sessions[d] for d in days])
    index_of = {s: i for i, s in enumerate(cube_sessions)}
    fit, val = [index_of[s] for s in fit_s], [index_of[s] for s in val_s]
    best, best_state, bad = np.inf, None, 0

    def _note(day: int) -> None:
        for back in (*be.TIME_OFFSETS, *(o + CONTRAST_SHIFT for o in be.TIME_OFFSETS)):
            if day - back >= 0:
                log.seen.add(cube_sessions[day - back])

    for epoch in range(epochs):
        model.train()
        heads.train()
        order = rng.permutation(np.asarray(fit, np.int64))
        if steps_per_epoch and steps_per_epoch < len(order):
            order = order[:steps_per_epoch]
        sums = np.zeros(3)
        n = 0
        for day in order:
            day = int(day)
            for m, rows in _pair_rows(observed, market_id, day, CONTRAST_SHIFT).items():
                if be.ENTITY_SUBSAMPLE < 1.0:
                    take = rng.random(len(rows)) < be.ENTITY_SUBSAMPLE
                    if take.sum() >= be.MIN_SET:
                        rows = rows[take]
                total, rec, nce = pretrain_step_loss(model, heads, cube, day, rows, m, gidx,
                                                     channels.get(m, set()), cmasks.get(m), aug, rng)
                opt.zero_grad()
                total.backward()
                torch.nn.utils.clip_grad_norm_(params, be.CLIP)
                opt.step()
                sums += (float(total.detach()), float(rec.detach()), float(nce.detach()))
                n += 1
                log.anchors.add(cube_sessions[day])
                _note(day)
        log.epochs = epoch + 1
        mean = sums / max(n, 1)
        log.train_losses.append(float(mean[0]))
        log.rec_losses.append(float(mean[1]))
        log.nce_losses.append(float(mean[2]))
        vloss = _val_loss(model, heads, cube, observed, market_id, cube_sessions, val, gidx, channels, cmasks, log, _note)
        log.val_losses.append(vloss)
        if progress is not None:
            progress(epoch=epoch + 1, train_loss=float(mean[0]), val_loss=vloss,
                     elapsed_s=time_module.monotonic() - began)  # invariant-allow: wallclock
        print(f"  사전학습 epoch {epoch + 1}/{epochs} · 학습 {mean[0]:.4f}(복원 {mean[1]:.4f} · 대조 {mean[2]:.4f}) · "
              f"검증 {vloss:.4f} · {(time_module.monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock
        if vloss < best - 1e-6:
            best, bad = vloss, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                log.stopped_early = epoch + 1 < epochs
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    log.minutes = (time_module.monotonic() - began) / 60  # invariant-allow: wallclock
    return {k: v.detach().clone() for k, v in model.state_dict().items()}, log


def _val_loss(model, heads, cube, observed, market_id, cube_sessions, val, gidx, channels, cmasks,
              log: PretrainLog, note) -> float:  # type: ignore[no-untyped-def]
    """검증 손실 — 드롭아웃·잡음 없이, 가림 난수는 고정(`VAL_RNG_SEED`). 판정 창이 아니라 컷오프 이전 앵커의 끝 20% 다."""
    import torch

    model.eval()
    heads.eval()
    rng = np.random.default_rng(VAL_RNG_SEED)
    vals = []
    with torch.inference_mode():
        for day in val:
            for m, rows in _pair_rows(observed, market_id, int(day), CONTRAST_SHIFT).items():
                total, _rec, _nce = pretrain_step_loss(model, heads, cube, int(day), rows, m, gidx,
                                                       channels.get(m, set()), cmasks.get(m), None, rng)
                vals.append(float(total))
            log.val_anchors.add(cube_sessions[int(day)])
            note(int(day))
    return float(np.mean(vals)) if vals else float("inf")


def transfer_state(pretrained: dict, seed: int, n_features: int) -> dict:
    """미세조정 시작 가중치 — **인코더는 사전학습 것, `out` 머리는 그 시드의 BE1 무작위 초기화 그대로.**

    구조가 같아야 한다: 키·모양이 `be.make_model(seed, n)` 과 하나라도 다르면 멈춘다(사전학습 전후 구조 동일).
    """
    model = be.make_model(seed, n_features)
    own = model.state_dict()
    if set(own) != set(pretrained):
        raise PretrainLeak(f"사전학습 가중의 키가 BE1 구조와 다르다: {sorted(set(own) ^ set(pretrained))[:6]}")
    out = {}
    for k, v in own.items():
        if tuple(pretrained[k].shape) != tuple(v.shape):
            raise PretrainLeak(f"사전학습 가중 {k} 모양 {tuple(pretrained[k].shape)} ≠ BE1 {tuple(v.shape)}")
        out[k] = v.detach().clone() if k.startswith("out.") else pretrained[k].detach().clone()
    return out


# ── 체크포인트 ──────────────────────────────────────────────────────────────────────

def save_checkpoint(path: Path, state: dict, *, columns: list[str], cutoff: date, log: PretrainLog,
                    channels: dict[str, set[str]], fill: pd.DataFrame | None, protocol_hash: str) -> None:
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state": state, "columns": list(columns), "cutoff": cutoff.isoformat(),
        "max_seen": max(log.seen).isoformat() if log.seen else None,
        "channels": {k: sorted(v) for k, v in channels.items()},
        "fill": fill.to_dict("records") if fill is not None else [],
        "epochs": log.epochs, "val_losses": log.val_losses, "train_losses": log.train_losses,
        "minutes": log.minutes, "seed": PRETRAIN_SEED, "protocol_hash": protocol_hash,
    }, path)


def load_checkpoint(path: Path, *, columns: list[str], cutoff: date) -> dict:
    """체크포인트를 읽고 **지금 판정 축과 맞는지** 본다 — 컷오프·본 세션의 끝·열 목록. 어긋나면 `PretrainLeak`(rc 7)."""
    import torch

    if not path.exists():
        print(f"사전학습 체크포인트가 없다: {path} — `pretrain` 이 먼저다", flush=True)
        raise SystemExit(INPUT_EXIT)
    ck = torch.load(path, map_location="cpu", weights_only=False)
    if ck.get("cutoff") != cutoff.isoformat():
        raise PretrainLeak(f"체크포인트 컷오프 {ck.get('cutoff')} ≠ 지금 판정 축의 컷오프 {cutoff}")
    if ck.get("max_seen") is None or date.fromisoformat(ck["max_seen"]) > cutoff:
        raise PretrainLeak(f"체크포인트가 본 마지막 세션 {ck.get('max_seen')} 이 컷오프 {cutoff} 뒤다(또는 기록 없음)")
    if list(ck.get("columns", [])) != list(columns):
        raise PretrainLeak("체크포인트 열 목록이 판정 큐브와 다르다 — 사전학습 패널과 판정 패널의 FA 가 어긋났다")
    return ck


# ── 판정 ⑦⑧ ────────────────────────────────────────────────────────────────────────

def _mean(table: dict[int, dict[str, float]], key: str) -> float:
    vals = [m[key] for m in table.values() if key in m and np.isfinite(m[key])]
    return float(np.mean(vals)) if vals else float("nan")


def require_keys(tables: dict[str, dict[int, dict[str, float]]]) -> None:
    """지표 키가 빠지면 크게 멈춘다 — nan 비교가 조용히 기각을 만드는 것을 막는다(kit.judge 와 같은 규칙)."""
    for name, table in tables.items():
        for seed, metrics in table.items():
            missing = [k for k in JUDGE_KEYS if k not in metrics or not np.isfinite(metrics[k])]
            if missing:
                raise ValueError(f"judge: {name} 시드 {seed} 에 지표 {missing} 가 없다 — 조용히 기각하지 않는다")


def criterion_ensemble(be3c1: dict[int, dict[str, float]], be2: dict[int, dict[str, float]]) -> tuple[bool, str]:
    """⑦ BE3+C1 이 BE2 보다 시드 평균 연 ≥ +1%p **이고** 시드의 80% 이상에서 높다(같은 시드끼리)."""
    seeds = sorted(set(be3c1) & set(be2))
    if not seeds:
        raise ValueError("⑦: BE3+C1 과 BE2 가 겹치는 시드가 없다")
    diff = _mean({s: be3c1[s] for s in seeds}, "ann") - _mean({s: be2[s] for s in seeds}, "ann")
    wins = sum(be3c1[s]["ann"] > be2[s]["ann"] for s in seeds)
    share = wins / len(seeds)
    ok = diff >= GATE_ENS_MEAN - 1e-12 and share >= GATE_ENS_SHARE - 1e-12
    return ok, f"⑦ BE3+C1 대 BE2 {diff:+.1%}p · {wins}/{len(seeds)} ({share:.0%}) {'✓' if ok else '✗'}"


def criterion_box(be3: dict[int, dict[str, float]], be1: dict[int, dict[str, float]]) -> tuple[bool, str]:
    """⑧ BE3 단독의 박스 국면(~2024-12) 연수익이 BE1 단독보다 높다(시드 평균, 0 초과 — 같으면 개선이 아니다)."""
    seeds = sorted(set(be3) & set(be1))
    if not seeds:
        raise ValueError("⑧: BE3 와 BE1 이 겹치는 시드가 없다")
    diff = _mean({s: be3[s] for s in seeds}, "box_ann") - _mean({s: be1[s] for s in seeds}, "box_ann")
    ok = diff > GATE_BOX
    return ok, f"⑧ 박스 BE3 대 BE1 {diff:+.1%}p {'✓' if ok else '✗'}"


def verdict(be3_kit: str, ens_kit: str, c7: bool, c8: bool) -> str:
    """최종 판정 — 채택은 **후보**다(확정은 두 번째 금고).

    · BE3 단독이 ①~⑥ 을 모두 넘고(kit: "채택 — 모델이 나아서") ⑧ 도 넘으면 → 채택 후보 BE3 단독.
    · 아니면, BE3+C1 이 ①~⑥ 을 모두 넘고 ⑦·⑧ 을 넘으면 → 채택 후보 BE3+C1(BE2 를 대체).
    · 그 밖은 기각 — 사전학습이 BE1·BE2 에 더한 것이 없다.
    """
    model_ok = be3_kit.startswith("채택 — 모델")
    ens_ok = ens_kit.startswith("채택 — 모델")
    if model_ok and c8:
        return "채택 후보 — BE3 단독(①~⑥·⑧ 통과)" + (" · BE3+C1 도 ⑦ 통과" if c7 and ens_ok else "")
    if ens_ok and c7 and c8:
        return "채택 후보 — BE3+C1(BE2 대체, ①~⑥·⑦·⑧ 통과)"
    return "기각 — 사전학습이 BE1·BE2 를 넘지 못했다"


# ── 등록·입력 관문 ──────────────────────────────────────────────────────────────────

def require_registered(args: argparse.Namespace, what: str, protocol: Path = PROTOCOL) -> str:
    """사전등록 전에는 실자료로 돌지 않는다 — 플래그·문서·초안 머리줄(`> **초안`). next-four 와 같은 규칙."""
    if not getattr(args, "i_registered", False):
        raise SystemExit(f"{what} 은 사전등록 뒤에만 돈다 — {protocol} 승인 후 --i-registered 를 명시하라")
    if not protocol.exists():
        raise SystemExit(f"등록 문서가 없다: {protocol}")
    if any(line.startswith("> **초안") for line in protocol.read_text().splitlines()[:5]):
        raise SystemExit(f"{protocol} 이 아직 초안이다(머리줄) — 사용자 승인·해시 고정 뒤에 돈다")
    return hashlib.sha256(protocol.read_bytes()).hexdigest()[:16]


def seed_done(cache: Path, seed: int) -> bool:
    return (cache / f"seed{seed}-judge.parquet").exists() and (cache / f"seed{seed}-train.parquet").exists()  # invariant-allow: data-access — 작업 파일


# ── 합성 자료 ──────────────────────────────────────────────────────────────────────

def synthetic_fa(n_sessions: int = 260, n_entities: int = 200, seed: int = 0):
    """BE1 합성 패널 + 결측 표지 둘 — gB 는 30% 결측(표지 miss_gb), gC 는 5% 만 채워짐(표지 miss_gc, 채움률 관문에 걸린다)."""
    panel, feats, groups, sessions = be.synthetic(n_sessions=n_sessions, n_entities=n_entities, seed=seed)
    rng = np.random.default_rng(seed + 99)
    miss_b = rng.random(len(panel)) < 0.30
    miss_c = rng.random(len(panel)) > 0.05
    panel.loc[miss_b, groups["gB"]] = 0.0
    panel.loc[miss_c, groups["gC"]] = 0.0
    panel["miss_gb"] = miss_b.astype(np.float32)
    panel["miss_gc"] = miss_c.astype(np.float32)
    panel["is_us"] = (panel["market"] == "US").astype(np.float32)
    groups = {"gA": list(groups["gA"]), "gB": [*groups["gB"], "miss_gb"], "gC": [*groups["gC"], "miss_gc"]}
    feats = [*feats, "miss_gb", "miss_gc", "is_us"]
    return panel, feats, groups, sessions


# ── 공통 조립 ──────────────────────────────────────────────────────────────────────

@dataclass
class Cube:
    cube: np.ndarray
    observed: np.ndarray
    market_id: np.ndarray
    entities: list[str]
    sessions: list[date]
    columns: list[str]
    index_of: dict[str, int]


def make_cube(panel: pd.DataFrame, feats: list[str]) -> Cube:
    """BE1 `main` 과 같은 순서로 큐브를 만든다 — 열 순서는 `be._feature_layout`, 축은 두 시장 세션의 합집합."""
    entities = sorted(panel["entity_id"].unique())
    columns, index_of = be._feature_layout(list(feats))
    if "is_us" not in panel.columns:
        panel = panel.assign(is_us=(panel["market"] == "US").astype(np.float32))
    sessions = sorted(panel["session"].unique())
    cube = be.build_cube(panel, columns, sessions, entities)
    observed = be.observed_mask(panel, sessions, entities)
    market_of = panel.drop_duplicates("entity_id").set_index("entity_id")["market"]
    market_id = (market_of.reindex(entities).fillna("KR") == "US").to_numpy(np.int8)
    return Cube(cube, observed, market_id, entities, sessions, columns, index_of)


def run_pretrain(panel: pd.DataFrame, feats: list[str], groups: dict[str, list[str]], cutoff: date, *,
                 epochs: int = PRETRAIN_EPOCHS, steps_per_epoch: int | None = None,
                 progress: Callable[..., Any] | None = None) -> tuple[dict, PretrainLog, Cube, pd.DataFrame, dict[str, set[str]]]:
    """패널 → 채움률 → 채널 → 큐브 → 사전학습. 합성·실자료가 같은 길을 쓴다."""
    enforce_cutoff(panel, cutoff)
    fill = group_fill(panel, groups)
    channels = pretrain_channels(fill)
    c = make_cube(panel, feats)
    by_code = {code: channels.get(name, set()) for code, name in be.MARKET_NAME.items()}
    cmasks = channel_masks(groups, c.index_of, channels)
    aug = be.Aug(kit.drop_groups, dict(groups), c.index_of)
    state, log = pretrain(c.cube, c.observed, c.market_id, c.sessions, cutoff, group_index(groups, c.index_of),
                          by_code, cmasks, aug, epochs=epochs, steps_per_epoch=steps_per_epoch, progress=progress)
    return state, log, c, fill, channels


# ── 명령 ──────────────────────────────────────────────────────────────────────────

def cmd_coverage(args: argparse.Namespace) -> int:
    """등록 전 점검 — 컷오프 이전 FA 묶음 채움률(국장·미장 따로)과 구간별 표. **라벨·수익을 보지 않는다.**"""
    sessions = judge_sessions()
    cutoff = pretrain_cutoff(sessions)
    first = sessions[kit.blocks(sessions)[0][0]]
    print(f"판정 첫 채점 세션 {first} · 사전학습 컷오프(퍼지 5 + 엠바고 5) {cutoff} · 창 {kit.PANEL_FIRST}~{cutoff}", flush=True)
    try:
        panel, feats, groups = load_pretrain_panel(cutoff, root=args.root)
    except PretrainLeak as error:
        print(f"누수 관문: {error}", flush=True)
        return LEAK_EXIT
    fill = group_fill(panel, groups)
    per = fill_by_period(panel, groups, [kit.PANEL_FIRST, date(2022, 4, 1), date(2022, 7, 1), date(2023, 1, 1)])
    ch = pretrain_channels(fill)
    n_sess = panel.groupby("market")["session"].nunique().to_dict()
    n_ent = panel.groupby("market")["entity_id"].nunique().to_dict()
    print(f"사전학습 패널 {len(panel):,}행 · 피처 {len(feats)} · 세션 {n_sess} · 종목 {n_ent} · 최대 RSS {kit.rss_mb():.0f}MB")
    print("\n| 묶음 | 열 | 국장 채움 | 미장 채움 |\n|---|---|---|---|")
    wide = fill.pivot(index="group", columns="market", values="fill")
    ncols = fill.drop_duplicates("group").set_index("group")["n_cols"]
    for g in groups:
        kr = wide.loc[g].get("KR", float("nan")) if g in wide.index else float("nan")
        us = wide.loc[g].get("US", float("nan")) if g in wide.index else float("nan")
        print(f"| {g} | {int(ncols.get(g, 0))} | {kr:.1%} | {us:.1%} |")
    print("\n구간별(시장·묶음 × 구간 시작):")
    print(per.to_string(index=False, float_format=lambda v: f"{v:.1%}"))
    print(f"\n사전학습 채널(채움 ≥ {MIN_FILL:.0%}): " + " · ".join(f"{m} {sorted(v)}" for m, v in sorted(ch.items())))
    print(f"최대 RSS {kit.rss_mb():.0f}MB", flush=True)
    return 0


def cmd_pretrain(args: argparse.Namespace) -> int:
    import torch

    if args.synthetic:
        torch.set_num_threads(args.threads)
        panel, feats, groups, sessions = synthetic_fa()
        cutoff = pretrain_cutoff(sessions)
        panel = panel[panel["session"] <= cutoff].drop(columns=["y5"])
        state, log, c, fill, channels = run_pretrain(panel, feats, groups, cutoff, epochs=args.epochs or 2,
                                                     steps_per_epoch=args.steps)
        print(f"[합성] 사전학습 epoch {log.epochs} · 본 세션 끝 {max(log.seen)} ≤ 컷오프 {cutoff} · "
              f"채널 {dict((k, sorted(v)) for k, v in channels.items())} · {log.minutes:.1f}분 — 배선 확인만", flush=True)
        if args.out:
            save_checkpoint(Path(args.out) / CKPT_NAME, state, columns=c.columns, cutoff=cutoff, log=log,
                            channels=channels, fill=fill, protocol_hash="synthetic")
        return 0
    digest = require_registered(args, "사전학습")
    torch.set_num_threads(args.threads)
    print(f"=== 시행 BE3 사전학습 — {PROTOCOL} (해시 {digest}) ===", flush=True)
    sessions = judge_sessions()
    cutoff = pretrain_cutoff(sessions)
    path = BE3_DIR / CKPT_NAME
    if path.exists():
        print(f"사전학습 체크포인트가 이미 있다: {path} — 사전학습은 한 번뿐이다(다시 하려면 사람이 지운다)", flush=True)
        return 0
    try:
        panel, feats, groups = load_pretrain_panel(cutoff, root=args.root)
        store = None if args.no_progress else kit.Store(root=Path(args.root))
        clock = LiveClock()

        def progress(**kw: Any) -> None:
            kit.record_progress(store, clock, "BE3-pretrain", source=SOURCE, market="KR+US", n_seeds=1, seed=PRETRAIN_SEED,
                                metric="rec mse + infonce/ln N", note=f"사전학습 컷오프 {cutoff}", **kw)

        state, log, c, fill, channels = run_pretrain(panel, feats, groups, cutoff, progress=progress)
    except PretrainLeak as error:
        print(f"누수 관문: {error}", flush=True)
        return LEAK_EXIT
    save_checkpoint(path, state, columns=c.columns, cutoff=cutoff, log=log, channels=channels, fill=fill,
                    protocol_hash=digest)
    print(f"사전학습 끝: epoch {log.epochs}{' (조기종료)' if log.stopped_early else ''} · 검증 {min(log.val_losses):.4f} · "
          f"앵커 {len(log.anchors)} · 본 세션 {min(log.seen)}~{max(log.seen)} ≤ 컷오프 {cutoff} · {log.minutes:.0f}분 · "
          f"최대 RSS {kit.rss_mb():.0f}MB → {path}", flush=True)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    import torch

    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else list(be.SEEDS)
    if args.synthetic:
        torch.set_num_threads(args.threads)
        panel, feats, groups, sessions = synthetic_fa()
        cutoff = pretrain_cutoff(sessions)
        blocks = list(kit.blocks(sessions))[: args.smoke or 2]
        init = None
        c = make_cube(panel, feats)
        if not args.no_pretrain:
            pre = panel[panel["session"] <= cutoff].drop(columns=["y5"])
            state, _log, _c, _fill, _ch = run_pretrain(pre, feats, groups, cutoff, epochs=1, steps_per_epoch=args.steps or 8)
            init = state
        targets = be.target_columns(panel, c.sessions, c.entities)
        aug = be.Aug(kit.drop_groups, dict(groups), c.index_of)
        for seed in seeds:
            start = transfer_state(init, seed, c.cube.shape[2]) if init is not None else None
            judge, _train, logs, minutes = be.run_seed(kit, c.cube, c.observed, c.market_id, c.entities, c.sessions,
                                                       sessions, blocks, targets, aug, seed,
                                                       max_epochs=args.max_epochs, init_state=start, trial="BE3",
                                                       source=SOURCE)
            print(f"[합성] seed {seed} · {'사전학습 없음(BE1 경로)' if init is None else '사전학습 초기화'} · "
                  f"예측 {len(judge):,}행 · 재학습 {len(logs)} · {minutes:.1f}분 — 배선 확인만", flush=True)
        return 0
    if args.no_pretrain:
        print("--no-pretrain 은 합성에서만 — 실자료 BE1 은 회차 캐시(data/_diag/final-round/BE)를 쓴다", file=sys.stderr)
        return 2
    digest = require_registered(args, "미세조정")
    torch.set_num_threads(args.threads)
    print(f"=== 시행 BE3 미세조정 — {PROTOCOL} (해시 {digest}) · 시드 {seeds} ===", flush=True)
    todo = [s for s in seeds if not seed_done(BE3_DIR, s)]
    if not todo:
        print("요청한 시드가 모두 캐시에 있다 — 다시 학습하지 않는다", flush=True)
        return 0
    panel, feats, groups, sessions = kit.load_full_panel(("KR", "US"), root=args.root)
    kit.require_full_coverage()
    cutoff = pretrain_cutoff(sessions)
    blocks = list(kit.blocks(sessions))
    c = make_cube(panel, feats)
    try:
        ck = load_checkpoint(BE3_DIR / CKPT_NAME, columns=c.columns, cutoff=cutoff)
    except PretrainLeak as error:
        print(f"누수 관문: {error}", flush=True)
        return LEAK_EXIT
    targets = be.target_columns(panel, c.sessions, c.entities)
    aug = be.Aug(kit.drop_groups, dict(groups), c.index_of)
    del panel
    kit.release_memory()
    store = None if args.no_progress else kit.Store(root=Path(args.root))
    clock = LiveClock()
    BE3_DIR.mkdir(parents=True, exist_ok=True)
    print(f"큐브 {c.cube.nbytes / 1e6:.0f}MB · 세션 {len(c.sessions)} · 종목 {len(c.entities):,} · 블록 {len(blocks)} · "
          f"사전학습 컷오프 {cutoff}(본 끝 {ck['max_seen']})", flush=True)
    for seed in todo:
        start = transfer_state(ck["state"], seed, c.cube.shape[2])
        judge, train, logs, minutes = be.run_seed(kit, c.cube, c.observed, c.market_id, c.entities, c.sessions,
                                                  sessions, blocks, targets, aug, seed, max_epochs=args.max_epochs,
                                                  store=store, clock=clock, n_seeds=len(be.SEEDS),
                                                  init_state=start, trial="BE3", source=SOURCE)
        be._save_seed(BE3_DIR, seed, judge, train)
        early = sum(log.stopped_early for log in logs)
        print(f"  seed {seed} 끝 · 재학습 {len(logs)}(조기 종료 {early}) · {minutes:.0f}분 · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
    return 0


def _read_preds(cache: Path, seed: int) -> pd.DataFrame:
    got = be._load_seed(cache, seed)
    if got is None:
        print(f"예측이 없다: {cache}/seed{seed}-judge.parquet", flush=True)  # invariant-allow: data-access — 작업 파일
        raise SystemExit(INPUT_EXIT)
    return kit.share_objects(got[0][[*KEYS, "pred"]])


def same_sessions(treated: pd.DataFrame, control: pd.DataFrame, *, label: str) -> None:
    """처리·대조가 같은 (세션 × 시장) 을 채점하는지 — 회차 캐시의 꼬리표 확인이 이것이다. 다르면 rc 9."""
    from tools.trial_final_lambdarank import session_sets_match
    drift = session_sets_match(treated, control)
    if drift:
        print(f"{label}: 처리와 대조의 채점 세션이 다르다 — 판정을 멈춘다:\n  " + "\n  ".join(drift), flush=True)
        raise SystemExit(SPAN_EXIT)


def cmd_judge(args: argparse.Namespace) -> int:
    digest = require_registered(args, "판정")
    from tools import trial_ranker_kit as rkit
    from tools.trial_next_four import light_panel

    seeds = list(be.SEEDS[: args.seeds])
    store = kit.Store(root=Path(args.root))
    print(f"=== 시행 BE3 판정 — {PROTOCOL} (해시 {digest}) · 시드 {len(seeds)} ===", flush=True)
    panel, sessions = light_panel()
    ctrl = kit.require_controls(panel, seeds=tuple(seeds))
    missing = [s for s in seeds if not seed_done(BE3_DIR, s)]
    if missing:
        print(f"BE3 시드 {missing} 의 예측이 없다 — `train` 이 먼저다", flush=True)
        return INPUT_EXIT
    be1 = {s: _read_preds(BE1_DIR, s) for s in seeds}
    be3 = {s: _read_preds(BE3_DIR, s) for s in seeds}
    for s in seeds:  # 회차 캐시 꼬리표 확인 — BE1·BE3·C1 이 같은 (세션 × 시장) 을 채점하는가
        same_sessions(be1[s], ctrl["C1"][s], label=f"BE1 seed{s}")
        same_sessions(be3[s], ctrl["C1"][s], label=f"BE3 seed{s}")
    books = kit.market_books(store, sessions, panel=panel)
    y = panel[["entity_id", "session", "y5"]]
    score = be.score_seed
    c0 = {s: score(kit, ctrl["C0"][s], books, y) for s in seeds}
    c1 = {s: score(kit, ctrl["C1"][s], books, y) for s in seeds}
    r_be1 = {s: score(kit, be1[s], books, y) for s in seeds}
    r_be2 = {s: score(kit, be.rank_average(be1[s], ctrl["C1"][s]), books, y) for s in seeds}
    r_be3 = {s: score(kit, be3[s], books, y, control=ctrl["C0"][s]) for s in seeds}
    r_ens = {s: score(kit, be.rank_average(be3[s], ctrl["C1"][s]), books, y) for s in seeds}
    require_keys({"C0": c0, "C1": c1, "BE1": r_be1, "BE2": r_be2, "BE3": r_be3, "BE3+C1": r_ens})
    lines_be3, v_be3 = kit.judge(r_be3, c0, c1, label="BE3 단독: ")
    lines_ens, v_ens = kit.judge(r_ens, c0, c1, label="BE3+C1: ")
    ok7, line7 = criterion_ensemble(r_ens, r_be2)
    ok8, line8 = criterion_box(r_be3, r_be1)
    final = verdict(v_be3, v_ens, ok7, ok8)
    lines = [*lines_be3, *lines_ens, line7, line8,
             "기록: 연수익 " + " · ".join(f"{k} {_mean(t, 'ann'):+.1%}" for k, t in
                                     (("C0", c0), ("C1", c1), ("BE1", r_be1), ("BE2", r_be2), ("BE3", r_be3), ("BE3+C1", r_ens))),
             "기록: 박스/급등 " + " · ".join(f"{k} {_mean(t, 'box_ann'):+.1%}/{_mean(t, 'rally_ann'):+.1%}" for k, t in
                                       (("C0", c0), ("BE1", r_be1), ("BE3", r_be3))),
             f"기록: 상위 24 겹침(BE3 대 C0) {np.nanmean([m.get('overlap', np.nan) for m in r_be3.values()]):.0%} · "
             f"최대 RSS {kit.rss_mb():.0f}MB",
             f"판정: {final}"]
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        rkit.record(store, entity=ENTITY, source=SOURCE, family=FAMILY, digest=digest, verdict=final,
                    lines=[line7, line8, *lines_be3[-1:], *lines_ens[-1:]], market="KR,US", run_tag="BE3")
        print(f"research_trials 기록: {FAMILY}/{ENTITY} · protocol {digest}", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("coverage", "pretrain", "train", "judge"):
        p = sub.add_parser(name)
        p.add_argument("--root", default="data")
        p.add_argument("--threads", type=int, default=12)
        p.add_argument("--i-registered", action="store_true", help="등록 문서 승인·해시 고정 뒤에만")
        p.add_argument("--no-progress", action="store_true", help="trial_progress 기록을 끈다")
        if name in ("pretrain", "train"):
            p.add_argument("--synthetic", action="store_true", help="창고를 읽지 않고 합성 패널로 배선 확인")
            p.add_argument("--steps", type=int, default=None, help="[합성] 에포크당 앵커 수 상한")
        if name == "pretrain":
            p.add_argument("--epochs", type=int, default=0, help="[합성] 에포크 수(실자료는 등록값 고정)")
            p.add_argument("--out", default="", help="[합성] 체크포인트를 남길 디렉터리")
        if name == "train":
            p.add_argument("--seeds", default="", help="예: 0,1 — 비우면 0~4. 끝난 시드는 캐시로 건너뛴다")
            p.add_argument("--smoke", type=int, default=0, help="[합성] 블록 수")
            p.add_argument("--max-epochs", type=int, default=be.MAX_EPOCHS)
            p.add_argument("--no-pretrain", action="store_true", help="[합성] BE1 과 같은 초기화 경로")
        if name == "judge":
            p.add_argument("--seeds", type=int, default=len(be.SEEDS))
            p.add_argument("--save", action="store_true", help="research_trials 에 1행(시행 예산 소진)")
    args = parser.parse_args(argv)
    if args.cmd == "pretrain" and not args.synthetic and args.epochs:
        print("--epochs 는 합성에서만 — 실자료 사전학습 에포크는 등록값(PRETRAIN_EPOCHS)이다", file=sys.stderr)
        return 2
    return {"coverage": cmd_coverage, "pretrain": cmd_pretrain, "train": cmd_train, "judge": cmd_judge}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
