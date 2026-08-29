"""학습한 정책을 **실제 장부 위에서** 하루의 목표 비중으로 푼다.

## 무엇을 하는가

    장부(book) + 오늘 후보 → 관측(env.observe_live) → 정책 forward
    → 평균 행동 → env.decide_live → {종목: 목표 비중}

세션(`session/daily.py`)이 이 모듈의 `decide` 를 룰 베이스라인 자리에 끼운다.
그 뒤 — 사이징·집행·실현 비중 기록 — 는 룰과 같은 길이다. 갈리는 것은
"목표 비중을 누가 냈나" 하나다 (불변식 5).

## 관측은 환경의 것이다

여기서 피처를 다시 짜지 않는다. `LatticeEnv.observe_live` 가 학습이 쓰던
`_asset_features`·`_portfolio_features` 를 그대로 부른다. 학습과 라이브가
다른 관측을 보면 정책은 학습 때 본 적 없는 입력에 답하게 되고, 그 오류는
"정책이 나쁘다" 로만 보인다. 라이브라서 다를 수밖에 없는 세 칸(남은 스텝
비율·보유 경과일 상한·직전 회전율/반영률)은 그 함수의 독스트링에 적었다.

## 평균 행동

Dirichlet 의 평균(alpha / sum(alpha)), 지연은 argmax. 표본을 뽑지 않는다 — 같은 장부에
같은 정책이 매일 다른 답을 내면 액션 반영률이 집행기가 아니라 주사위 얘기가
된다. `tools/evaluate_policy.py` 가 OOS 를 잰 방식과 같다.

## 노출 제어를 거치지 않는다

룰 베이스라인은 `selector/exposure.py` 가 레짐·변동성으로 노출을 줄인다.
정책은 그 단계를 **모르고 배웠다** — 학습 환경에 노출 배수가 없고, 현금
비중은 정책의 액션 한 칸이다. 그 위에 배수를 또 곱하면 정책이 내린 현금
결정을 룰이 덮어쓰는 것이고, 그게 선행 프로젝트가 룰로 전락한 길이다
(CLAUDE.md 액션 반영률). 킬스위치는 집행기 안에 그대로 있다 — 그건
정책의 결정을 다듬는 것이 아니라 시스템을 멈추는 마지막 장치다.

## 진입 지연

정책의 지연 액션(0~N 거래일 뒤 매수)은 학습 환경에서 대기열로 굴렀다.
라이브 세션에는 대기열이 없다 — 매일 정책이 다시 본다. 그래서 지연 > 0 인
**매수**는 "오늘은 안 산다" 로 푼다: 그 종목의 목표를 오늘 실현 비중에 둔다.
내일 정책이 다시 결정한다. 매도는 지연이 없다(환경과 같다).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.accounting.book import Book
from quant_rl_trading.allocator.env import (
    FEATURE_REALIZED_WEIGHT,
    N_ASSET_FEATURES,
    N_PORTFOLIO_FEATURES,
    EnvParams,
    LatticeEnv,
)
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig
from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import ConfigNotFound

logger = logging.getLogger(__name__)

TRADES = "trades"
REALIZED_WEIGHTS = "realized_weights"
#: 관측 창을 앞뒤로 펼칠 달력 여유. 에피소드 250거래일 ≈ 365일 × 1.6.
_CALENDAR_STRETCH = 1.6


@dataclass(frozen=True)
class LiveParams:
    """`allocator.rl.*` — 어느 장부에서 정책이 결정하나.

    ``checkpoint`` 가 비면 어디서도 안 쓴다. ``modes`` 는 `store/mode.py` 의
    코드(소문자)다 — 모의계좌(paper)만 켜고 shadow 는 룰로 두면 둘이 같은 날
    같은 후보로 병주해 RL 과 룰을 나란히 볼 수 있다 (rl-training.md §13).
    """

    checkpoint: str
    modes: tuple[str, ...]

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> LiveParams:
        try:
            checkpoint = str(store.config("allocator.rl.checkpoint", as_of=as_of) or "")
            raw = store.config("allocator.rl.modes", as_of=as_of)
        except ConfigNotFound:
            # 설정이 창고에 아직 안 심긴 시점(과거 as_of)이다. 그때는 RL 이 없었다.
            return cls(checkpoint="", modes=())
        modes = raw if isinstance(raw, list | tuple) else str(raw).split(",")
        return cls(
            checkpoint=checkpoint.strip(),
            modes=tuple(str(m).strip().lower() for m in modes if str(m).strip()),
        )

    def active_for(self, mode_code: str) -> bool:
        return bool(self.checkpoint) and mode_code.lower() in self.modes


@dataclass(frozen=True)
class PolicyDecision:
    weights: dict[str, float]
    delays: dict[str, int]
    deferred: tuple[str, ...]
    cash_weight: float
    slots: tuple[str, ...]
    slots_dropped: int
    checkpoint: str
    update: int | None
    concentration_total: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "weights": {k: round(v, 6) for k, v in self.weights.items()},
            "delays": dict(self.delays),
            "deferred": list(self.deferred),
            "cash_weight": round(self.cash_weight, 6),
            "slots": list(self.slots),
            "slots_dropped": self.slots_dropped,
            "checkpoint": self.checkpoint,
            "update": self.update,
            "concentration_total": round(self.concentration_total, 4),
            "notes": list(self.notes),
        }


_POLICIES: dict[tuple[str, float, int], tuple[AllocatorPolicy, int | None]] = {}


def load_policy(checkpoint: Path, params: EnvParams) -> tuple[AllocatorPolicy, int | None]:
    """체크포인트 → 평가 모드 정책. 같은 파일은 한 번만 연다.

    지연 선택지 수는 **체크포인트가 말한다**(`delay_head` 출력 폭). 설정의
    `max_entry_delay_days` 와 다를 수 있다 — 학습기(`tools/train_rl.py`)가
    3 을 박아 두었고, 여기서 설정값으로 만들면 load_state_dict 가 모양 불일치로
    죽는다. 학습이 쓴 것을 그대로 쓰는 것이 옳다.
    """
    import torch

    path = Path(checkpoint)
    if not path.exists():
        raise FileNotFoundError(f"정책 체크포인트가 없다: {path}")
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime, stat.st_size)
    cached = _POLICIES.get(key)
    if cached is not None:
        return cached
    state = torch.load(path, map_location="cpu", weights_only=False)
    weights = state["policy"]
    choices = int(weights["delay_head.weight"].shape[0])
    policy = AllocatorPolicy(
        PolicyConfig(
            n_max=params.n_max,
            n_asset_features=N_ASSET_FEATURES,
            n_portfolio_features=N_PORTFOLIO_FEATURES,
            n_delay_choices=choices,
        )
    )
    policy.load_state_dict(weights)
    policy.eval()
    update = state.get("update")
    _POLICIES.clear()
    _POLICIES[key] = (policy, int(update) if update is not None else None)
    return _POLICIES[key]


def entry_dates(store: Store, *, as_of: datetime) -> dict[str, date]:
    """보유 종목별 **최초 매수일** — 보유 경과일 피처의 기준.

    체결을 처음부터 되짚어 수량이 0 에서 양수가 된 날을 잡는다. 수량이 0 으로
    돌아가면 그 종목의 기준일은 지운다. 장부(`ledger.build_book`)와 같은 표를
    같은 게이트로 읽는다.
    """
    frame = store.get(TRADES, as_of=as_of)
    if frame.empty:
        return {}
    frame = frame[frame["valid_from"] <= pd.Timestamp(as_of)]
    # 정정본(revision)은 store.get 이 자연키마다 최신 하나로 이미 접었다.
    frame = frame.sort_values(["valid_from", "observed_at"])
    quantity: dict[str, float] = {}
    entered: dict[str, date] = {}
    for row in frame.itertuples(index=False):
        entity = str(row.entity_id)
        signed = float(row.quantity) * (1.0 if str(row.side).upper() == "BUY" else -1.0)
        before = quantity.get(entity, 0.0)
        after = before + signed
        quantity[entity] = after
        if before <= 0 < after:
            entered[entity] = pd.Timestamp(row.valid_from).date()
        elif after <= 0:
            entered.pop(entity, None)
    return entered


def last_turnover(store: Store, *, as_of: datetime, nav: float) -> float:
    """직전 세션의 회전율 = 그날 체결 금액 / NAV. 환경이 스텝마다 재던 값."""
    if nav <= 0:
        return 0.0
    frame = store.get(TRADES, as_of=as_of, lookback=10)
    if frame.empty:
        return 0.0
    frame = frame[frame["valid_from"] <= pd.Timestamp(as_of)]
    if frame.empty:
        return 0.0
    latest = frame["valid_from"].max()
    day = frame[frame["valid_from"].dt.date == pd.Timestamp(latest).date()]
    gross = float((day["quantity"].abs() * day["price"].abs()).sum())
    return gross / nav


def last_reflection(store: Store, *, as_of: datetime) -> float:
    """직전 세션의 액션 반영률. 없으면 1.0 — 환경의 초기값과 같다."""
    frame = store.get(REALIZED_WEIGHTS, as_of=as_of, lookback=10)
    if frame.empty:
        return 1.0
    frame = frame[frame["valid_from"] <= pd.Timestamp(as_of)]
    if frame.empty:
        return 1.0
    latest = frame["valid_from"].max()
    day = frame[frame["valid_from"] == latest]
    target = float(day["target_weight"].abs().sum())
    if target <= 0:
        return 1.0
    matched = 1.0 - float((day["target_weight"] - day["realized_weight"]).abs().sum()) / target
    return max(0.0, min(1.0, matched))


def build_env(store: Store, *, as_of: datetime, market: str, params: EnvParams) -> LatticeEnv:
    """관측 전용 환경. 캐시를 안 탄다 — 오늘 세션은 캐시에 없고, 있어도 장부가
    다르다. 거래일 창은 오늘까지 에피소드 길이만큼이다(앞쪽은 `observe_live` 가 자리로 채운다)."""
    stretch = timedelta(days=int(params.episode_days * _CALENDAR_STRETCH) + 14)
    day = as_of.date()
    return LatticeEnv(
        store,
        train_start=day - stretch,
        train_end=day,
        market=market,
        params=params,
        use_cache=False,
    )


def decide(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    book: Book,
    nav: float,
    drawdown: float,
    candidates: Sequence[tuple[str, float]],
    params: LiveParams,
    hyper_as_of: datetime | None = None,
) -> PolicyDecision:
    """오늘의 목표 비중을 정책에게 묻는다.

    ``drawdown`` 은 `accounting.snapshot` 의 값(≤ 0) 이든 양수 깊이든 받는다 —
    절댓값을 깊이로 쓴다.
    """
    import torch

    hyper = hyper_as_of or as_of
    env_params = EnvParams.from_store(store, as_of=as_of, hyper_as_of=hyper)
    env = build_env(store, as_of=as_of, market=market, params=env_params)
    policy, update = load_policy(Path(params.checkpoint), env_params)

    obs, info = env.observe_live(
        session=as_of.date(),
        book=book,
        entered=entry_dates(store, as_of=as_of),
        nav=nav,
        drawdown_depth=abs(float(drawdown)),
        candidates=candidates,
        last_turnover=last_turnover(store, as_of=as_of, nav=nav),
        last_reflection=last_reflection(store, as_of=as_of),
    )
    with torch.no_grad():
        out = policy(
            torch.as_tensor(obs["portfolio"], dtype=torch.float32).unsqueeze(0),
            torch.as_tensor(obs["assets"], dtype=torch.float32).unsqueeze(0),
            torch.as_tensor(obs["mask"], dtype=torch.bool).unsqueeze(0),
        )
        alpha = out.concentration[0]
        mean = (alpha / alpha.sum()).cpu().numpy().astype(np.float64)
        delay = out.delay_logits[0].argmax(-1).cpu().numpy().astype(np.int64)
        total = float(alpha.sum().item())

    weights, delays = env.decide_live({"weights": mean, "delay": delay})
    realized = {
        slot: float(obs["assets"][i, FEATURE_REALIZED_WEIGHT])
        for i, slot in enumerate(info["candidates"])
    }
    deferred: list[str] = []
    for entity, wait in delays.items():
        if wait > 0 and weights.get(entity, 0.0) > realized.get(entity, 0.0) + 1e-9:
            weights[entity] = realized.get(entity, 0.0)
            deferred.append(entity)
    cash = max(0.0, 1.0 - sum(weights.values()))
    notes: list[str] = []
    if info.get("slots_dropped"):
        notes.append(f"슬롯 부족으로 후보 {info['slots_dropped']}종목이 관측에서 빠졌다")
    if deferred:
        notes.append(f"진입 지연으로 오늘 매수를 미룬 종목 {len(deferred)}")
    return PolicyDecision(
        weights=weights,
        delays=delays,
        deferred=tuple(deferred),
        cash_weight=cash,
        slots=tuple(info["candidates"]),
        slots_dropped=int(info.get("slots_dropped", 0)),
        checkpoint=str(params.checkpoint),
        update=update,
        concentration_total=total,
        notes=notes,
    )


__all__ = [
    "LiveParams",
    "PolicyDecision",
    "build_env",
    "decide",
    "entry_dates",
    "last_reflection",
    "last_turnover",
    "load_policy",
]
