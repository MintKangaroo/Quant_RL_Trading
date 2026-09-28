"""AI v2 R0 — 맥락적 밴딧으로 노출 k 를 정한다 (docs/design/ai-architecture-v2.md §2 L3, 로드맵 10/5~9).

    .venv/bin/python tools/v2_bandit.py smoke                       # 합성 자료 스모크(창고 안 씀)
    .venv/bin/python tools/v2_bandit.py canary --i-registered       # 배관 점검(정답을 흘린다 — 판정 아님)
    .venv/bin/python tools/v2_bandit.py judge  --i-registered       # 판정(사전등록·승인 뒤에만)
    .venv/bin/python tools/v2_bandit.py train  --i-registered       # 모델 적합 → data/_diag/v2/bandit-*.pkl
    .venv/bin/python tools/v2_bandit.py act --market KR [--dry-run] # 그날 행동을 exposure_actions 에 적는다

**행동은 노출 하나다.** k ∈ {0.50, 0.80, 1.00}(BB 와 같은 세 배수, 2026-09-28 사용자 결정). 종목 비중은 건드리지 않고(L0·L1 이 정한다),
재조정 주기도 채택된 규칙(10세션 = AO C10)을 그대로 쓴다 — RL 사후분석의 교훈 3("규칙이 더 잘하는 것은 규칙에 둔다").
팔을 고를 때는 **바꾸는 값을 먼저 뺀다**: 노출을 한 칸 옮기면 그만큼이 왕복하므로 점수에서 |Δk| × 편도비용 을 감한다.
이것이 규칙의 데드밴드 자리다 — 팔 간격(0.15~0.20)이 실전 밴드 0.10 보다 커서 밴드로는 아무것도 못 막는다.

**문맥**(전부 그날 개장 전에 알 수 있고 과거 이력이 있다 — CLAUDE.md 금지사항):
HMM 국면 확률 p0·p1·p2(U1, 전방 필터) · 지수 20세션 수익 · 지수 20세션 실현변동성 · 지수 낙폭 · 직전 노출 k.
포트폴리오 NAV 낙폭이 아니라 **지수 낙폭**을 쓴다 — 실전에서도 같은 값을 같은 코드로 얻을 수 있어야 한다(불변식 5).
스케일은 **고정 상수**(`CTX_SCALE`)로 O(1) 로 맞춘다. 자료에서 추정한 평균·표준편차를 쓰지 않으니 누수가 원천적으로 없고,
2회차 r5 를 죽인 "관측 원값(환율 1,478)" 도 구조적으로 못 들어온다(테스트가 강제).

**보상**(docs/design/reward-and-risk.md §2 그대로): r = (r_port,net − r_bench) − w(d)·Δd.
비용은 이미 r_port,net 에 들어 있고(회전 × 편도), 낙폭 벌점 밴드는 12/22/30 · w = 0/1.5/8.0. LLM 출력은 들어가지 않는다(불변식 8).

**미래를 안 보는 장치.** 시뮬레이터의 하루 수익은 t+1→t+2 구간이라 t+2 세션 전에는 알 수 없다. 그래서 t 의 선택은
`FEEDBACK_LAG`(2세션) 이전 보상만 학습한 모델로 한다 — 밴딧이 받는 큐는 지연을 통과한 것만 비운다.

**실전 경로**는 HMM(hmm-v1)과 같다: `act` 가 세션 전에 k 를 창고 `exposure_actions`(source `bandit-v1`)에 적고,
`session/daily.run` 은 `exposure.source: bandit-v1` 일 때 그 행을 읽기만 한다(불변식 6 — 집행 안에 AI 없음).

판정·학습·행동 적기는 **사전등록 뒤에만** 돈다(`--i-registered` + 등록 문서가 초안이 아님 + 날짜 ≥ JUDGE_FROM).
`smoke` 만 아무 때나 돈다 — 합성 자료뿐이고 결과를 판정에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools.v2_sim import Result, State, simulate  # noqa: E402

PROTOCOL = Path("docs/protocols/v2-bandit-exposure-2026-10.md")
MODELS = Path("data/_diag/v2")
SOURCE = "bandit-v1"

#: 행동 집합. v2 가 정한 [0.30, 1.00] 안이고 BB 의 세 배수(0.5·0.8·1.0)를 덮는다. 이웃 간 간격이 실전 데드밴드 0.10 보다
#: 커서 밴드로는 왕복을 못 막는다 — 대신 전환 비용을 점수에서 뺀다(`Runner.policy`).
ARMS: tuple[float, ...] = (0.50, 0.80, 1.00)  # 2026-09-28 사용자 결정 — 팔마다 표본 ~270
#: 재조정 주기(세션). 채택된 규칙 AO C10 과 같다.
EVERY = 10
#: 보상이 도착하기까지 기다리는 세션 수. 시뮬 하루 수익이 t+1→t+2 라 2 가 최소다.
FEEDBACK_LAG = 2

#: LinUCB/Thompson 상수. 자료를 보기 전에 고정했다 — 보상 크기(일 초과수익 ~1%p)로 정한 값이고 성과로 고르지 않았다.
RIDGE, ALPHA, REWARD_SCALE = 1.0, 0.30, 0.01
#: 문맥 칸과 그 고정 스케일. 곱한 뒤 전부 |값| ≲ 3 이어야 한다(테스트가 강제).
CTX_NAMES: tuple[str, ...] = ("bias", "p0", "p1", "p2", "ret20", "vol20", "index_dd", "prev_k")
CTX_SCALE = np.array([1.0, 1.0, 1.0, 1.0, 10.0, 5.0, 5.0, 1.0])
#: 낙폭 벌점 — docs/design/reward-and-risk.md §2. (깊이 상한, 가중치)
DD_BANDS: tuple[tuple[float, float], ...] = ((0.12, 0.0), (0.22, 1.5), (0.30, 8.0))
DD_ABOVE = 8.0
#: 판정·학습은 이 날짜부터(v2 로드맵 10/5~9). 사전등록 승인과 **같이** 필요한 조건이다.
JUDGE_FROM = date(2026, 10, 20)  # 2026-09-28 사용자 결정 — 10/19 결과 정리 뒤


# --------------------------------------------------------------------------- 보상


def dd_weight(depth: float) -> float:
    """낙폭 깊이별 한계 벌점 가중치(깊이는 양수). 임계치는 reward-and-risk.md 표와 같다."""
    for edge, w in DD_BANDS:
        if depth < edge:
            return w
    return DD_ABOVE


def reward(r_port: float, r_bench: float, depth_prev: float, depth_now: float) -> float:
    """r = (초과수익) − w(d)·Δd. **새로 깊어진 낙폭에만** 벌점 — 신저점이 아니면 Δd = 0."""
    delta = max(0.0, depth_now - depth_prev)
    return (r_port - r_bench) - dd_weight(depth_now) * delta


# --------------------------------------------------------------------------- 문맥


def raw_context(p: np.ndarray, ret20: float, vol20: float, index_dd: float, prev_k: float) -> np.ndarray:
    """문맥 벡터. p 는 HMM 상태 확률(길이 3, 상태 0 = 가장 나쁜 국면). index_dd 는 지수 낙폭(음수)."""
    v = np.array([1.0, float(p[0]), float(p[1]), float(p[2]), float(ret20), float(vol20), float(index_dd), float(prev_k)])
    return np.asarray(np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0) * CTX_SCALE, dtype=float)


def index_features(closes: pd.Series, day: object) -> tuple[float, float, float]:
    """지수 20세션 수익·실현변동성·낙폭 — **그날 전 종가까지만** 본다(개장 전 결정)."""
    prior = closes[closes.index < day]
    if len(prior) < 21:
        return 0.0, 0.0, 0.0
    r = np.log(prior).diff().dropna()
    ret20 = float(np.exp(r.tail(20).sum()) - 1.0)
    vol20 = float(r.tail(20).std() * np.sqrt(252))
    dd = float(prior.iloc[-1] / prior.cummax().iloc[-1] - 1.0)
    return ret20, vol20, dd


# --------------------------------------------------------------------------- 학습기


def pick(scores: np.ndarray) -> int:
    """가장 높은 점수. **동점이면 가장 낮은 팔** — 순서·부동소수에 안 흔들리게 한 곳에서만 고른다."""
    return int(np.flatnonzero(scores == scores.max())[0])


class LinUCB:
    """분리형 LinUCB — 팔마다 릿지 회귀 하나. 같은 자료·같은 순서면 **완전히 결정론적**이다(난수 없음)."""

    kind = "linucb"

    def __init__(self, dim: int, n_arms: int, *, alpha: float = ALPHA, ridge: float = RIDGE, seed: int = 0) -> None:
        self.dim, self.n_arms, self.alpha, self.seed = dim, n_arms, alpha, seed
        self.A = np.stack([np.eye(dim) * ridge for _ in range(n_arms)])
        self.b = np.zeros((n_arms, dim))
        self.pulls = np.zeros(n_arms, dtype=int)

    def scores(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(self.n_arms)
        for a in range(self.n_arms):
            inv = np.linalg.inv(self.A[a])
            theta = inv @ self.b[a]
            out[a] = float(theta @ x + self.alpha * np.sqrt(max(0.0, x @ inv @ x)))
        return out

    def choose(self, x: np.ndarray) -> int:
        return pick(self.scores(x))

    def update(self, arm: int, x: np.ndarray, r: float) -> None:
        self.A[arm] += np.outer(x, x)
        self.b[arm] += (r / REWARD_SCALE) * x
        self.pulls[arm] += 1


class ThompsonLinear(LinUCB):
    """선형 톰슨 샘플링. 시드가 같으면 같은 행동을 낸다(rng 를 학습기 안에 들고 있다)."""

    kind = "thompson"

    def __init__(self, dim: int, n_arms: int, *, alpha: float = ALPHA, ridge: float = RIDGE, seed: int = 0) -> None:
        super().__init__(dim, n_arms, alpha=alpha, ridge=ridge, seed=seed)
        self.rng = np.random.default_rng(seed)

    def scores(self, x: np.ndarray) -> np.ndarray:
        draws = np.empty(self.n_arms)
        for a in range(self.n_arms):
            inv = np.linalg.inv(self.A[a])
            draws[a] = float(self.rng.multivariate_normal(inv @ self.b[a], (self.alpha ** 2) * inv) @ x)
        return draws


class ContextFree(LinUCB):
    """**문맥 없는** 같은 밴딧(절편만). 복기 규칙 9 의 "신호 없는 같은 구성" 대조 — 문맥이 값을 하는지 이걸로 가른다."""

    kind = "contextfree"

    def __init__(self, dim: int, n_arms: int, *, alpha: float = ALPHA, ridge: float = RIDGE, seed: int = 0) -> None:
        super().__init__(1, n_arms, alpha=alpha, ridge=ridge, seed=seed)
        self.full_dim = dim

    def scores(self, x: np.ndarray) -> np.ndarray:
        return super().scores(x[:1])

    def update(self, arm: int, x: np.ndarray, r: float) -> None:
        super().update(arm, x[:1], r)


LEARNERS = {"linucb": LinUCB, "thompson": ThompsonLinear, "contextfree": ContextFree}


# --------------------------------------------------------------------------- 실행기(시뮬레이터에 끼우는 정책)


@dataclass(frozen=True)
class Choice:
    """그날의 선택 한 건 — 무엇을 보고 어느 팔을 골랐나, 그때까지 **몇 번째 스텝의 보상까지** 배웠나."""

    day: object
    step: int
    arm: int
    k: float
    ctx: np.ndarray
    trained_through: int


@dataclass
class Runner:
    """온라인 밴딧을 `v2_sim.simulate` 의 정책·관찰자로 만든다.

    선택 → (지연 뒤) 보상. `pending` 은 아직 지연을 통과하지 못한 (스텝, 문맥, 팔, 보상) 이고,
    `policy` 가 매번 **지연을 통과한 것만** 학습기에 흘린다. 이 큐가 미래를 막는 장치다.
    """

    learner: LinUCB
    bench: pd.Series
    cost: float = 0.0          # 편도 비용 — 팔을 바꿀 때 내는 값(선택에 미리 반영한다)
    arms: tuple[float, ...] = ARMS
    every: int = EVERY
    lag: int = FEEDBACK_LAG
    oracle: bool = False
    nav: float = 1.0
    peak: float = 1.0
    depth: float = 0.0
    prev_k: float = 1.0
    pending: list[tuple[int, np.ndarray, int, float]] = field(default_factory=list)
    log: list[Choice] = field(default_factory=list)
    trained_through: int = -1          # 학습에 반영된 마지막 스텝 — 테스트가 이걸로 누수를 본다

    def context(self, state: State) -> np.ndarray:
        e: dict = state.extra   # type: ignore[type-arg]
        p = np.asarray(e.get("p", (0.0, 0.0, 1.0)), dtype=float)
        x = raw_context(p, e.get("ret20", state.bench_ret20), e.get("vol20", state.bench_vol20),
                        e.get("index_dd", 0.0), self.prev_k)
        if self.oracle:
            x = np.append(x, float(e.get("oracle", 0.0)))   # 배관 점검용 정답 칸 — 판정 경로엔 없다
        return x

    def policy(self, state: State) -> tuple[float, bool]:
        ready = [row for row in self.pending if row[0] <= state.step - self.lag]
        self.pending = [row for row in self.pending if row[0] > state.step - self.lag]
        for step, x, arm, r in ready:
            self.learner.update(arm, x, r)
            self.trained_through = max(self.trained_through, step)
        x = self.context(state)
        # **바꾸는 값은 아는 값이다.** 노출을 한 칸 옮기면 그만큼이 왕복하고 편도 비용을 낸다 — 팔 점수에서 그 비용을 먼저 뺀다.
        # 이것이 규칙의 데드밴드 자리다(팔 간격 0.15~0.20 이 밴드 0.10 보다 커서 밴드로는 아무것도 못 막는다).
        # 미래 정보가 아니라 이미 정해진 수수료율이고, 없으면 밴딧은 잡음만큼 매일 노출을 왕복시킨다(스모크 회전 9~19배).
        penalty = np.abs(np.asarray(self.arms) - self.prev_k) * self.cost / REWARD_SCALE
        arm = pick(self.learner.scores(x) - penalty)
        self.log.append(Choice(day=state.day, step=state.step, arm=arm, k=self.arms[arm], ctx=x.copy(),
                               trained_through=self.trained_through))
        self.prev_k = self.arms[arm]
        return self.arms[arm], state.step % self.every == 0

    def observe(self, state: State, k: float, b: bool, r: float) -> None:
        self.nav *= 1.0 + r
        self.peak = max(self.peak, self.nav)
        depth_prev, self.depth = self.depth, max(0.0, 1.0 - self.nav / self.peak)
        rb = float(self.bench.get(state.day, 0.0) or 0.0)
        rec = self.log[-1]
        self.pending.append((rec.step, rec.ctx, rec.arm, reward(r, rb, depth_prev, self.depth)))

    def summary(self) -> str:
        pulls = np.bincount([r.arm for r in self.log], minlength=len(self.arms))
        return " · ".join(f"k={k:.2f} {n}회" for k, n in zip(self.arms, pulls, strict=True))


def run_bandit(targets: pd.DataFrame, ret: pd.DataFrame, bench: pd.Series, cost: float, learner: LinUCB, *,
               extra: Callable[[object], dict[str, object]], arms: tuple[float, ...] = ARMS, every: int = EVERY,
               oracle: bool = False, k_min: float = 0.30) -> tuple[Result, Runner]:
    """밴딧 하나를 워크포워드로 한 번 굴린다(에폭 없음 — 같은 날을 두 번 배우지 않는다)."""
    runner = Runner(learner=learner, bench=bench, cost=cost, arms=arms, every=every, oracle=oracle)
    res = simulate(targets, ret, bench, cost, runner.policy, k_min=k_min, extra=extra, observe=runner.observe)
    return res, runner


# --------------------------------------------------------------------------- 창고에 적는 행동(실전 경로)


def action_row(market: str, moment: datetime, k: float, detail: dict[str, object]) -> dict[str, object]:
    """`exposure_actions` 한 행 — `selector.exposure.learned_decision` 이 읽는 모양. hmm-v1 과 같은 계약이다."""
    return {"entity_id": market, "valid_from": moment, "observed_at": moment, "source": SOURCE, "market": market,
            "scale": float(min(1.0, max(0.30, k))), "detail": json.dumps(detail, default=str)}


# --------------------------------------------------------------------------- 관문


def _today() -> date:
    from quant_rl_trading.replay.clock import LiveClock

    return LiveClock().now().date()


def require_registered(args: argparse.Namespace, what: str) -> str:
    """사전등록 전에는 아무 결과도 보지 않는다 — 명시 플래그·초안 여부·날짜 셋 다 본다. 해시를 돌려준다."""
    if not getattr(args, "i_registered", False):
        raise SystemExit(f"{what} 은 사전등록 뒤에만 돈다 — {PROTOCOL} 승인 후 --i-registered 를 명시하라")
    if not PROTOCOL.exists():
        raise SystemExit(f"등록 문서가 없다: {PROTOCOL}")
    text = PROTOCOL.read_text()
    if "초안" in text.splitlines()[0]:
        raise SystemExit(f"{PROTOCOL} 이 아직 초안이다(머리줄) — 사용자 승인·해시 고정 뒤에 돈다")
    if _today() < JUDGE_FROM:
        raise SystemExit(f"{what} 은 {JUDGE_FROM} 부터다(v2 로드맵) — 오늘 {_today()}")
    return hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------- 자료(판정 경로 — 무거운 것은 늦게 들여온다)


def _judge_inputs(store, market: str = "KR"):  # type: ignore[no-untyped-def]
    """시행 BB 와 **같은** 자료·같은 구성(기본 포트 K0, 점수 원천 넷, 10세션 재조정)."""
    from tools import v2_hmm_exposure as bb
    from tools.trial_kr_index_tilt import caps_panel, float_ratios, members
    from tools.trial_portfolio_variance import CACHE, SEEDS
    from tools.trial_ranker_kit import SPAN, market_data, scores_chunked

    sessions = list(pd.read_pickle(CACHE / "sessions.pkl"))  # invariant-allow: data-access — AM loop 캐시
    sources = {f"loop{s}": pd.read_pickle(CACHE / f"loop-seed{s}.pkl") for s in SEEDS}  # invariant-allow: data-access — AM loop 캐시
    start = min(p["session"].min() for p in sources.values())
    sessions = [s for s in sessions if s >= start]
    live = scores_chunked(store, "ranker", sessions).stack().rename("pred").reset_index()
    live.columns = ["session", "entity_id", "pred"]
    sources = {"live": live[["entity_id", "session", "pred"]], **sources}
    ret, bench, trad = market_data(store, sessions)
    mem = members(store, sessions)
    names = sorted(set().union(*mem.values()))
    caps, fr = caps_panel(store, sessions, names), float_ratios(store, names)
    days = [d for d in sessions if d in mem and d in ret.index]
    return bb, sources, ret, bench, trad, mem, caps, fr, days, SPAN


def _extra_of(hmm: pd.DataFrame, closes: pd.Series):  # type: ignore[no-untyped-def]
    """문맥 재료를 날짜로 주는 함수 — 전부 **그날 전** 자료만 본다."""
    cols = [c for c in hmm.columns if c.startswith("p")]

    def extra(day: object) -> dict:   # type: ignore[type-arg]
        prev = hmm[hmm.index < day]
        p = prev.iloc[-1][cols].to_numpy(dtype=float) if len(prev) else np.array([0.0, 0.0, 1.0])
        ret20, vol20, dd = index_features(closes, day)
        return {"p": p, "ret20": ret20, "vol20": vol20, "index_dd": dd}

    return extra


# --------------------------------------------------------------------------- 스모크(합성 자료)


def _synthetic(seed: int = 0, n: int = 400):  # type: ignore[no-untyped-def]
    """국면이 번갈아 오는 합성 시장. 나쁜 국면에서 노출을 줄이면 이득이 있게 만든 자료다 —
    **배관 점검용**이고 여기서 이겼다는 사실은 판정에 쓰지 않는다(합성 자료로 통과하는 테스트는 현실을 말해주지 않는다)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2022-01-03", periods=n).date
    bad = (np.arange(n) // 40) % 2 == 1
    mu = np.where(bad, -0.0012, 0.0012)
    sd = np.where(bad, 0.022, 0.008)
    names = [f"S{i}" for i in range(6)]
    mkt = rng.normal(mu, sd)
    ret = pd.DataFrame({e: mkt + rng.normal(0, 0.004, n) for e in names}, index=days)
    bench = ret.mean(axis=1)
    targets = pd.DataFrame(1 / len(names), index=days, columns=names)
    closes = pd.Series((1 + bench).cumprod().to_numpy(), index=days)
    p = np.stack([np.where(bad, 0.7, 0.1), np.full(n, 0.2), np.where(bad, 0.1, 0.7)], axis=1)
    hmm = pd.DataFrame({f"p{i}": p[:, i] for i in range(3)}, index=days)
    return targets, ret, bench, closes, hmm


def cmd_smoke(args: argparse.Namespace) -> int:
    from tools.trial_overlay import ONE_WAY_COST, metrics

    targets, ret, bench, closes, hmm = _synthetic(args.seed)
    extra = _extra_of(hmm, closes)
    if args.oracle:
        base = extra
        nxt = bench.shift(-1).fillna(0.0)
        extra = lambda d: {**base(d), "oracle": float(np.sign(nxt.get(d, 0.0)))}  # noqa: E731
    print(f"=== R0 스모크(합성 자료 · seed {args.seed} · {len(ret)}세션) — 판정 아님 ===", flush=True)
    rows = {"C0 노출 끔": simulate(targets, ret, bench, ONE_WAY_COST, lambda s: (1.0, s.step % EVERY == 0), extra=extra)}
    for kind in ("contextfree", "linucb", "thompson"):
        dim = len(CTX_NAMES) + (1 if args.oracle else 0)
        res, runner = run_bandit(targets, ret, bench, ONE_WAY_COST, LEARNERS[kind](dim, len(ARMS), seed=args.seed),
                                 extra=extra, oracle=args.oracle)
        rows[f"B {kind}"] = res
        print(f"  {kind}: 평균 노출 {res.exposure.mean():.2f} · 팔 {runner.summary()}", flush=True)
    for name, res in rows.items():
        m = metrics(res.daily, bench.reindex(res.daily.index).fillna(0.0))
        print(f"  {name:<16} 연수익 {m['ann']:+.1%} · 샤프 {m['sharpe']:+.2f} · MDD {m['mdd']:.1%} · 회전 {res.turnover:.1f}", flush=True)
    return 0


# --------------------------------------------------------------------------- 판정 · 배관 점검 · 적합


def _variants(store, args, oracle: bool):  # type: ignore[no-untyped-def]
    """C0(끔)·C1(규칙 V6)·C2(HMM 매핑)·B0(문맥 없는 밴딧)·B1(LinUCB)·B2(Thompson) 를 같은 포트 위에서 굴린다."""
    from tools.trial_overlay import ANN, ONE_WAY_COST, metrics
    from tools.trial_ranker_ensemble import BOX_END

    bb, sources, ret, bench, trad, mem, caps, fr, days, span = _judge_inputs(store, args.market)
    base = args.base or bb.base_variant()
    hmm = pd.read_pickle(bb.HMM)  # invariant-allow: data-access — v2 연구 캐시
    hmm.index = pd.to_datetime(pd.Series(hmm.index)).dt.date.values
    closes = pd.Series((1 + bench).cumprod())
    extra = _extra_of(hmm, closes)
    if oracle:
        nxt = bench.shift(-1).fillna(0.0)
        plain, extra = extra, (lambda d: {**plain(d), "oracle": float(np.sign(nxt.get(d, 0.0)))})
    rule = {"C0": pd.Series(1.0, index=days), "C1": bb.v6_path(store, days), "C2": bb.hmm_path(days)}
    out: dict[str, dict[str, dict[str, float]]] = {}
    for src, frame in sources.items():
        score = frame.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=span).mean()
        tgt = bb.targets(base, score.reindex([d for d in days if d in score.index]), mem, caps, fr, trad)
        runs = {v: simulate(tgt, ret, bench, ONE_WAY_COST, (lambda p: (lambda s: (float(p.get(s.day, 1.0)), s.step % EVERY == 0)))(path),
                            k_min=0.30, extra=extra) for v, path in rule.items()}
        for name, kind in (("B0", "contextfree"), ("B1", "linucb"), ("B2", "thompson")):
            per_seed = []
            for seed in (0, 1, 2):
                dim = len(CTX_NAMES) + (1 if oracle else 0)
                res, runner = run_bandit(tgt, ret, bench, ONE_WAY_COST, LEARNERS[kind](dim, len(ARMS), seed=seed),
                                         extra=extra, oracle=oracle)
                per_seed.append((res, runner))
                print(f"  {src} {name} seed{seed}: 평균 노출 {res.exposure.mean():.2f} · {runner.summary()}", flush=True)
            runs[name] = per_seed[0][0]
            for i, (res, _) in enumerate(per_seed[1:], start=1):
                runs[f"{name}s{i}"] = res
        for v, res in runs.items():
            b = bench.reindex(res.daily.index).fillna(0.0)
            m = metrics(res.daily, b)
            for label, part in (("box", res.daily.index <= BOX_END), ("rally", res.daily.index > BOX_END)):
                m[f"{label}_ann"] = float(res.daily[part].mean() * ANN)
            out.setdefault(v, {})[src] = {**m, "turn": res.turnover, "exp": float(res.exposure.mean())}
    return out, sources, base


def _report(out: dict[str, dict[str, dict[str, float]]], sources: dict[str, object], base: str, hashed: str) -> list[str]:
    def avg(v: str, k: str) -> float:
        return float(np.mean([out[v][s][k] for s in sources]))

    def seeds(v: str, k: str) -> list[float]:
        return [avg(name, k) for name in (v, f"{v}s1", f"{v}s2") if name in out]

    lines = [f"기본 포트 {base} · 등록 해시 {hashed}", "",
             "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR(지수) | 회전 | 평균 노출 |", "|---|---|---|---|---|---|---|---|---|---|"]
    label = {"C0": "C0 노출 끔", "C1": "C1 규칙 V6(대조)", "C2": "C2 HMM 매핑", "B0": "B0 문맥 없는 밴딧", "B1": "B1 LinUCB", "B2": "B2 Thompson"}
    for v, name in label.items():
        if v not in out:
            continue
        lines.append(f"| {name} | {avg(v, 'ann'):+.1%} | {avg(v, 'box_ann'):+.1%} | {avg(v, 'rally_ann'):+.1%} | {avg(v, 'sharpe'):+.2f} | "
                     f"{avg(v, 'mdd'):.1%} | {avg(v, 'beta'):.2f} | {avg(v, 'ir'):+.2f} | {avg(v, 'turn'):.1f} | {avg(v, 'exp'):.2f} |")
    ctrl = max(avg("C1", "sharpe"), avg("C2", "sharpe"))
    ctrl_mdd, ctrl_ann = max(avg("C1", "mdd"), avg("C2", "mdd")), max(avg("C1", "ann"), avg("C2", "ann"))
    wins = sum(out["B1"][s]["sharpe"] > max(out["C1"][s]["sharpe"], out["C2"][s]["sharpe"]) for s in sources)
    sd = np.std(seeds("B1", "sharpe")) if len(seeds("B1", "sharpe")) > 1 else float("nan")
    c = (avg("B1", "sharpe") >= ctrl + 0.10, avg("B1", "mdd") >= ctrl_mdd - 0.01, avg("B1", "ann") >= ctrl_ann - 0.01,
         wins == len(sources), avg("B1", "sharpe") >= avg("C0", "sharpe"), avg("B1", "sharpe") >= avg("B0", "sharpe") + 0.05)
    def mark(ok: bool) -> str:
        return "○" if ok else "×"

    lines += ["", f"시드 흩어짐(B1 샤프 표준편차) {sd:.3f}",
              f"B1: ①샤프 {avg('B1', 'sharpe'):+.2f} vs 대조 {ctrl:+.2f} (+0.10) {mark(c[0])} · ②MDD {avg('B1', 'mdd'):.1%} vs {ctrl_mdd:.1%} {mark(c[1])} · "
              f"③연수익 {avg('B1', 'ann') - ctrl_ann:+.1%}p {mark(c[2])} · ④{wins}/{len(sources)} {mark(c[3])} · "
              f"⑤vs 노출 끔 {avg('C0', 'sharpe'):+.2f} {mark(c[4])} · ⑥vs 문맥 없는 밴딧 {avg('B0', 'sharpe'):+.2f} {mark(c[5])}"]
    shadow = wins == len(sources) and avg("B1", "sharpe") >= ctrl + 0.05 and c[5]
    lines.append("판정: " + ("채택 B1" if all(c) else ("기각 — shadow 승격 후보(4/4 · 샤프 +0.05 · 문맥 값 있음)" if shadow else "기각 — 규칙·HMM 유지")))
    return lines


def cmd_judge(args: argparse.Namespace) -> int:
    hashed = require_registered(args, "판정")          # 관문이 먼저 — 무거운 것은 통과한 뒤에 들여온다
    from quant_rl_trading.store import Store
    from tools.trial_ranker_kit import record

    print(f"=== 시행 R0 — {PROTOCOL} (해시 {hashed}) ===", flush=True)
    store = Store(root=Path("data"))
    out, sources, base = _variants(store, args, oracle=False)
    lines = _report(out, sources, base, hashed)
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="v2-bandit-exposure-2026-10:R0", source="v2_bandit", family="selection",
               digest=hashed, verdict=lines[-1], lines=lines[-3:], market=args.market)
    return 0


def cmd_canary(args: argparse.Namespace) -> int:
    """정답(다음날 벤치 부호)을 문맥에 흘려 **배울 수 있는 배관인가**를 먼저 본다(2026-08-20 카나리의 교훈).
    여기서도 대조를 못 이기면 본 판정은 돌리지 않는다 — 신호가 아니라 배관을 고칠 차례다."""
    hashed = require_registered(args, "카나리")
    from quant_rl_trading.store import Store

    print(f"=== R0 카나리(정답 흘림 — 판정 아님) — 해시 {hashed} ===", flush=True)
    out, sources, base = _variants(Store(root=Path("data")), args, oracle=True)
    print("\n" + "\n".join(_report(out, sources, base, hashed)[:-1]), flush=True)
    print("카나리는 판정이 아니다 — 통과는 '배관이 살아 있다' 까지만 뜻한다", flush=True)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """판정 창 전체를 한 번 굴린 뒤 학습기 상태를 얼린다 — `act` 가 그 모델로 그날 행동을 낸다."""
    hashed = require_registered(args, "적합")
    from quant_rl_trading.store import Store
    from tools.trial_overlay import ONE_WAY_COST

    store = Store(root=Path("data"))
    bb, sources, ret, bench, trad, mem, caps, fr, days, span = _judge_inputs(store, args.market)
    base = args.base or bb.base_variant()
    hmm = pd.read_pickle(bb.HMM)  # invariant-allow: data-access — v2 연구 캐시
    hmm.index = pd.to_datetime(pd.Series(hmm.index)).dt.date.values
    extra = _extra_of(hmm, pd.Series((1 + bench).cumprod()))
    frame = sources["live"]
    score = frame.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=span).mean()
    tgt = bb.targets(base, score.reindex([d for d in days if d in score.index]), mem, caps, fr, trad)
    MODELS.mkdir(parents=True, exist_ok=True)
    for seed in (0, 1, 2):
        _, runner = run_bandit(tgt, ret, bench, ONE_WAY_COST, LinUCB(len(CTX_NAMES), len(ARMS), seed=seed), extra=extra)
        path = MODELS / f"bandit-{args.market}-seed{seed}.pkl"
        path.write_bytes(pickle.dumps({"learner": runner.learner, "arms": ARMS, "ctx": CTX_NAMES, "scale": CTX_SCALE,
                                       "protocol_hash": hashed, "through": runner.log[-1].day}))
        print(f"  seed{seed} → {path} · {runner.summary()}", flush=True)
    return 0


def cmd_act(args: argparse.Namespace) -> int:
    """그날 행동 하나를 창고에 적는다(hmm-v1 과 같은 계약). --dry-run 은 관문 없이 돌아 계산만 보여 준다."""
    if not args.dry_run:
        require_registered(args, "행동 적기")
    from quant_rl_trading.backtest import loop
    from quant_rl_trading.collectors.market_hours import (
        Market,
        is_trading_day,
        previous_trading_day,
    )
    from quant_rl_trading.replay.clock import LiveClock
    from quant_rl_trading.selector.exposure import ACTIONS
    from quant_rl_trading.store import Store
    from tools.v2_regime_hmm import INDEX, latest

    path = MODELS / f"bandit-{args.market}-seed{args.seed}.pkl"
    if not path.exists():
        raise SystemExit(f"모델이 없다: {path} — 먼저 train")
    model = pickle.loads(path.read_bytes())
    market = Market(args.market)
    now = LiveClock().now()
    day = date.fromisoformat(args.day) if args.day else now.astimezone(_tz(market)).date()
    if not is_trading_day(market, day):
        day = previous_trading_day(market, day)
    store = Store(root=Path("data"))
    moment = loop.snapshot_moment(store, day, as_of=now, market=market)
    end = datetime.combine(day, time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=max(end, now), lookback=(day - date(2020, 1, 1)).days, market=args.market,
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX[args.market]].assign(d=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("d")["close"].last().sort_index()
    closes = closes[(closes > 0) & (closes.index <= day)]
    if closes.empty or closes.index[-1] != day:
        print(f"{args.market} {day}: 지수 종가가 아직 없다 — 적지 않는다", file=sys.stderr)
        return 1
    p = latest(closes, 3)
    ret20, vol20, dd = index_features(closes, day + timedelta(days=1))
    prev = store.get(ACTIONS, as_of=now, lookback=20, market=args.market, columns=["entity_id", "valid_from", "source", "scale"])
    prev = prev[(prev["source"] == SOURCE) & (prev["valid_from"] < moment)] if not prev.empty else prev
    prev_k = float(prev.sort_values("valid_from").iloc[-1]["scale"]) if len(prev) else 1.0
    x = raw_context(p, ret20, vol20, dd, prev_k)
    arm = model["learner"].choose(x)
    k = model["arms"][arm]
    detail = {"arm": int(arm), "ctx": {n: round(float(v), 4) for n, v in zip(CTX_NAMES, x, strict=True)},
              "p": [round(float(v), 4) for v in p], "prev_k": prev_k, "model": path.name, "protocol_hash": model.get("protocol_hash")}
    print(f"{args.market} {day}: p={np.round(p, 3).tolist()} · ret20 {ret20:+.1%} · vol20 {vol20:.1%} · dd {dd:.1%} → k {k:.2f}", flush=True)
    if not args.dry_run:
        store.append(ACTIONS, [action_row(args.market, moment, k, detail)],
                     ingest_run_id=f"{SOURCE}-{args.market}-{day:%Y%m%d}", source=SOURCE)
    return 0


def _tz(market) -> ZoneInfo:  # type: ignore[no-untyped-def]
    from quant_rl_trading.collectors.market_hours import Market

    return ZoneInfo("Asia/Seoul" if market is Market.KR else "America/New_York")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("smoke", "canary", "judge", "train", "act"):
        p = sub.add_parser(name)
        p.add_argument("--market", default="KR")
        p.add_argument("--seed", type=int, default=0)
        if name != "smoke":
            p.add_argument("--i-registered", action="store_true", help="사전등록·승인을 확인했다는 명시 플래그")
            p.add_argument("--base", default="", help="기본 포트(비우면 AZ 판정 로그에서 읽는다)")
        if name == "smoke":
            p.add_argument("--oracle", action="store_true")
        if name in ("judge",):
            p.add_argument("--save", action="store_true")
        if name == "act":
            p.add_argument("--day", default="")
            p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return {"smoke": cmd_smoke, "canary": cmd_canary, "judge": cmd_judge, "train": cmd_train, "act": cmd_act}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
