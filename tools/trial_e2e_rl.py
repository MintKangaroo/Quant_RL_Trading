"""매매 전반 RL 베타 — docs/protocols/e2e-rl-2026-09.md 대로 잰다.

종목 선정·사이징·회전을 **포트폴리오 손익 하나로** 배우게 하고, 그 정책의 점수가
fundamental 위에 정보를 더하는지를 랭커 시행과 같은 기준으로 판정한다. 회차를 세지
않는 베타이므로 창고에 시행 기록을 남기지 않는다(`research_trials` 미기록).

    .venv/bin/python tools/trial_e2e_rl.py            # 측정만
    .venv/bin/python tools/trial_e2e_rl.py --steps 20 # 배관 확인용(판정 아님)

시뮬레이터가 미분가능하므로 에피소드 누적 보상을 파라미터로 직접 미분한다 — 분산 0 의
정책경사. 표본 기반 PPO 는 같은 그래디언트의 잡음 있는 추정이라, 여기서 지면 PPO 도 진다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time as time_module
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

PROTOCOL = Path("docs/protocols/e2e-rl-2026-09.md")
CACHE = Path("data/_diag")
MARKET = "KR"
ANALYSTS = ("chart", "event", "flow_kr", "fundamental", "regime", "risk", "volume")
HOLDOUT_START = date(2026, 7, 1)
TOP_N = 24
ONE_WAY_COST = 0.0041          # 편도 비용 — 선정 시행 3·시행 A 와 같은 값
T_GATE = 2.0
WORST_BLOCK_GATE = -0.03
MAX_MOVE = 0.5                 # 일수익 ±50% 초과는 데이터 사고로 본다
# 워크포워드 (랭커 변형 B 와 동일)
MIN_TRAIN = 150
BLOCK = 20
PURGE = 5
# 학습 (고정)
HIDDEN = 64
LR = 1e-3
WEIGHT_DECAY = 1e-4
STEPS = 200
CLIP = 1.0
SEEDS = (0, 1, 2)
Z_CLIP = 5.0


# ---------------------------------------------------------------- 데이터

def _sessions() -> list[date]:
    cal = pd.read_pickle(CACHE / f"calendar-{MARKET}.pkl")
    return sorted(pd.to_datetime(cal["session"]).dt.date)


def _features() -> pd.DataFrame:
    """7개 pkl 을 (entity_id, session) 로 합친다. 세션별 횡단면 z, 결측 0."""
    merged: pd.DataFrame | None = None
    for name in ANALYSTS:
        frame = pd.read_pickle(CACHE / f"features-{name}-{MARKET}.pkl")
        frame["session"] = pd.to_datetime(frame["session"]).dt.date
        merged = frame if merged is None else merged.merge(frame, on=["entity_id", "session"], how="outer")
    assert merged is not None
    cols = [c for c in merged.columns if c not in ("entity_id", "session")]
    grouped = merged.groupby("session")[cols]
    z = (merged[cols] - grouped.transform("mean")) / grouped.transform("std").replace(0.0, np.nan)
    merged[cols] = z.clip(-Z_CLIP, Z_CLIP).fillna(0.0)
    return merged


def _tradable() -> pd.DataFrame:
    frame = pd.read_pickle(CACHE / f"tradable-{MARKET}.pkl")
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    return frame


def _targets(h: int) -> pd.DataFrame:
    frame = pd.read_pickle(CACHE / f"targets-{MARKET}-h{h}.pkl")
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    return frame


def _scores(name: str) -> pd.DataFrame:
    frame = pd.read_pickle(CACHE / f"scores-{name}-{MARKET}.pkl")
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    return frame


def _forward_returns(store: Store, sessions: list[date]) -> pd.DataFrame:
    """세션 t 의 결정 → t+1 종가 진입 → t+2 종가 평가. 행 = 세션, 열 = 종목."""
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 40
    prices = read_prices(
        store, as_of=now, lookback=span, columns=["close"], adjusted=True, market=MARKET
    )
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    wide = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    entry = wide.shift(-1)
    exit_ = wide.shift(-2)
    ret = exit_ / entry - 1.0
    ret = ret.where(ret.abs() <= MAX_MOVE)
    return ret.reindex(sessions)


class Panel:
    """세션 × 종목 텐서. 종목 축은 전 기간 합집합, 세션마다 tradable·수익 결측 마스크."""

    def __init__(self, store: Store, sessions: list[date]) -> None:
        feats = _features()
        trad = _tradable()
        fwd = _forward_returns(store, sessions)
        keep_feats = feats[feats["session"].isin(sessions)]
        entities = sorted(set(keep_feats["entity_id"]) & set(fwd.columns))
        self.entities = entities
        self.sessions = sessions
        eidx = {e: i for i, e in enumerate(entities)}
        sidx = {s: i for i, s in enumerate(sessions)}
        cols = [c for c in feats.columns if c not in ("entity_id", "session")]
        self.feature_names = cols
        S, N, F = len(sessions), len(entities), len(cols)
        x = np.zeros((S, N, F), dtype=np.float32)
        present = np.zeros((S, N), dtype=bool)
        sub = keep_feats[keep_feats["entity_id"].isin(eidx)]
        si = sub["session"].map(sidx).to_numpy()
        ei = sub["entity_id"].map(eidx).to_numpy()
        x[si, ei] = sub[cols].to_numpy(dtype=np.float32)
        present[si, ei] = True
        tsub = trad[trad["session"].isin(sidx) & trad["entity_id"].isin(eidx)]
        tradable = np.zeros((S, N), dtype=bool)
        tradable[tsub["session"].map(sidx).to_numpy(), tsub["entity_id"].map(eidx).to_numpy()] = True
        r = fwd[entities].to_numpy(dtype=np.float32)
        has_ret = np.isfinite(r)
        self.mask = torch.tensor(present & tradable & has_ret)
        self.x = torch.tensor(x)
        self.r = torch.tensor(np.where(has_ret, r, 0.0).astype(np.float32))
        print(
            f"패널: {S}세션 × {N}종목 × {F}피처 · 세션당 거래가능 평균 {self.mask.sum(1).float().mean():.0f}종목"
        )


# ---------------------------------------------------------------- 정책

class Policy(nn.Module):
    """종목 축 공유 MLP + 횡단면 평균 풀링 → 점수. 종목 순서·개수에 불변."""

    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.local = nn.Sequential(
            nn.Linear(n_features + 1, HIDDEN), nn.ReLU(), nn.Linear(HIDDEN, HIDDEN), nn.ReLU()
        )
        self.head = nn.Sequential(nn.Linear(2 * HIDDEN, HIDDEN), nn.ReLU(), nn.Linear(HIDDEN, 1))

    def forward(self, x: torch.Tensor, held: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        h = self.local(torch.cat([x, held.unsqueeze(-1)], dim=-1))
        m = mask.unsqueeze(-1).float()
        context = (h * m).sum(0, keepdim=True) / m.sum().clamp(min=1.0)
        s = self.head(torch.cat([h, context.expand_as(h)], dim=-1)).squeeze(-1)
        return s.masked_fill(~mask, -1e9)


def simulate(
    policy: Policy, panel: Panel, idx: range, held0: torch.Tensor | None = None
) -> tuple[torch.Tensor, list[dict[str, float]], list[torch.Tensor], torch.Tensor]:
    """연속 시뮬레이션. (누적 보상, 세션별 지표, 세션별 점수, 마지막 보유비중)"""
    N = panel.x.shape[1]
    held = torch.zeros(N) if held0 is None else held0
    total = torch.zeros(())
    rows: list[dict[str, float]] = []
    scores: list[torch.Tensor] = []
    for t in idx:
        mask = panel.mask[t]
        s = policy(panel.x[t], held, mask)
        w = torch.softmax(s, dim=0)
        r = panel.r[t]
        port = (w * r).sum()
        bench = r[mask].mean() if mask.any() else torch.zeros(())
        turnover = (w - held).abs().sum()
        reward = port - ONE_WAY_COST * turnover - bench
        total = total + reward
        rows.append({
            "excess": float(reward), "gross": float(port - bench), "turnover": float(turnover),
            "eff_n": float(1.0 / (w * w).sum()),
        })
        scores.append(s.detach())
        held = (w * (1.0 + r)) / (1.0 + port).clamp(min=1e-6)
    return total, rows, scores, held.detach()


def train(panel: Panel, idx: range, seed: int, steps: int) -> tuple[Policy, list[float]]:
    torch.manual_seed(seed)
    policy = Policy(panel.x.shape[2])
    opt = torch.optim.Adam(policy.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    curve: list[float] = []
    for _ in range(steps):
        total, _rows, _s, _h = simulate(policy, panel, idx)
        loss = -total
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(policy.parameters(), CLIP)
        opt.step()
        curve.append(float(total))
    return policy, curve


# ---------------------------------------------------------------- 판정

def _rank_norm(s: torch.Tensor, mask: torch.Tensor) -> np.ndarray:
    out = np.full(s.shape[0], np.nan)
    valid = mask.numpy()
    vals = s.numpy()[valid]
    if vals.size:
        out[valid] = pd.Series(vals).rank(pct=True).to_numpy() - 0.5
    return out


def _nw(series: pd.Series, lag: int) -> float:
    return float(ic_module.newey_west_t(series.dropna(), lag=lag)) if series.notna().sum() > 3 else float("nan")


def _daily_ic(scored: pd.DataFrame, targets: pd.DataFrame) -> pd.Series:
    merged = scored.merge(targets, on=["entity_id", "session"], how="inner").dropna(subset=["score", "target"])
    return ic_module.daily_ic(merged)


def _top_excess(scored: pd.DataFrame, targets: pd.DataFrame) -> pd.Series:
    merged = scored.merge(targets, on=["entity_id", "session"], how="inner").dropna(subset=["score", "target"])
    top = merged.sort_values("score", ascending=False).groupby("session").head(TOP_N)
    return top.groupby("session")["target"].mean()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--steps", type=int, default=STEPS, help="고정값 200 이 아니면 판정이 아니다")
    parser.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    args = parser.parse_args(argv)
    seeds = tuple(int(s) for s in args.seeds.split(","))
    official = args.steps == STEPS and seeds == SEEDS
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 매매 전반 RL 베타 — {PROTOCOL} (해시 {digest}) · {'판정' if official else '배관 확인, 판정 아님'} ===")
    torch.set_num_threads(max(1, torch.get_num_threads() // 2))

    store = Store(root=Path(args.root))
    sessions = [s for s in _sessions() if s < HOLDOUT_START]
    print(f"세션 {len(sessions)}개: {sessions[0]} ~ {sessions[-1]} (홀드아웃 {HOLDOUT_START} 이전)")
    panel = Panel(store, sessions)
    t5 = _targets(5)
    fundamental = _scores("fundamental")

    # 워크포워드 블록: 학습 [0, end) → 퍼지 5 → 예측 [end+5, end+25)
    blocks: list[tuple[int, int, int]] = []
    end = MIN_TRAIN
    while end + PURGE + BLOCK <= len(sessions):
        blocks.append((end, end + PURGE, end + PURGE + BLOCK))
        end += BLOCK
    if not blocks:
        print("판정 블록이 없다.", file=sys.stderr)
        return 2
    print(f"블록 {len(blocks)}개 · 판정 {len(blocks) * BLOCK}세션")

    rows: list[dict[str, object]] = []
    sim_rows: list[dict[str, float]] = []
    for b, (train_end, pred_start, pred_end) in enumerate(blocks, 1):
        started = time_module.perf_counter()
        ranks = np.zeros((BLOCK, panel.x.shape[1]))
        curve_first, curve_last = [], []
        for seed in seeds:
            policy, curve = train(panel, range(0, train_end), seed, args.steps)
            curve_first.append(curve[0])
            curve_last.append(curve[-1])
            with torch.no_grad():
                # 학습창 끝의 보유비중에서 이어서 퍼지 → 예측 구간을 돈다
                _t, _r, _s, held = simulate(policy, panel, range(0, train_end))
                _t, srows, scores, _h = simulate(policy, panel, range(train_end, pred_end), held)
            for j, t in enumerate(range(pred_start, pred_end)):
                ranks[j] += np.nan_to_num(_rank_norm(scores[t - train_end], panel.mask[t]), nan=0.0)
            if seed == seeds[-1]:
                sim_rows.extend(srows[PURGE:])
        for j, t in enumerate(range(pred_start, pred_end)):
            valid = panel.mask[t].numpy()
            for i in np.flatnonzero(valid):
                rows.append({"entity_id": panel.entities[i], "session": sessions[t], "score": ranks[j, i] / len(seeds)})
        print(
            f"블록 {b}/{len(blocks)} 학습 {sessions[0]}~{sessions[train_end - 1]} → 판정 {sessions[pred_start]}~{sessions[pred_end - 1]}"
            f" · 학습창 누적초과 시작 {np.mean(curve_first):+.3f} → 끝 {np.mean(curve_last):+.3f}"
            f" · {time_module.perf_counter() - started:.0f}s"
        )

    scored = pd.DataFrame(rows)
    judged = sorted(scored["session"].unique())
    base = fundamental[fundamental["session"].isin(judged)]
    ic_p = _daily_ic(scored, t5)
    ic_f = _daily_ic(base, t5)
    common = ic_p.index.intersection(ic_f.index)
    delta = (ic_p.loc[common] - ic_f.loc[common])
    top_p = _top_excess(scored, t5)
    top_f = _top_excess(base, t5)
    block_delta = []
    for b, (_e, ps, pe) in enumerate(blocks, 1):
        days = [d for d in sessions[ps:pe] if d in delta.index]
        block_delta.append(float(delta.loc[days].mean()) if days else float("nan"))
    sim = pd.DataFrame(sim_rows)

    print()
    print(f"판정 {len(common)}세션 · 정책 IC(h5) {ic_p.mean():+.4f} (t {_nw(ic_p, 4):+.2f})"
          f" 대 fundamental {ic_f.mean():+.4f} (t {_nw(ic_f, 4):+.2f})")
    print(f"  1. ΔIC {delta.mean():+.4f} · NW t {_nw(delta, 4):+.2f}  (기준 ≥ {T_GATE})")
    print(f"  2. 상위{TOP_N} h5 z-수익 정책 {top_p.mean():+.4f} 대 대조 {top_f.mean():+.4f}  (기준 정책 ≥ 대조)")
    print(f"  3. 블록별 ΔIC {' '.join(f'{d:+.3f}' for d in block_delta)} · 최악 {np.nanmin(block_delta):+.3f}  (기준 ≥ {WORST_BLOCK_GATE})")
    passed = (
        _nw(delta, 4) >= T_GATE and top_p.mean() >= top_f.mean() and np.nanmin(block_delta) >= WORST_BLOCK_GATE
    )
    print(f"  정책 비중 그대로(마지막 시드): 순초과 연 {sim['excess'].mean() * 252:+.3f} · 비용 전 {sim['gross'].mean() * 252:+.3f}"
          f" · 일회전 {sim['turnover'].mean():.2f} · 유효종목 {sim['eff_n'].mean():.0f}")
    print(f"판정: {'채택 후보' if passed else '기각(기록만)'}{'' if official else ' — 배관 확인 실행이라 판정이 아니다'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
