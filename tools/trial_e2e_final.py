"""M4 5회차 — 매매 전반 DRL 마지막 시도. docs/protocols/e2e-drl-final-2026-09.md 대로 잰다.

    .venv/bin/python tools/trial_e2e_final.py [--quick] [--save]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402
from scipy.stats import norm  # noqa: E402
from torch import nn  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_pooled_deep import blocks_for  # noqa: E402
from tools.trial_selection_ranker import _scores  # noqa: E402
from tools.trial_selection_smoothing import _tradable, pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/e2e-drl-final-2026-09.md")
START, END = date(2024, 2, 1), date(2026, 6, 30)
FEATS = ("chart", "event", "flow_kr", "fundamental", "regime", "risk", "ranker")
ONE_WAY_COST, MAX_MOVE, TOP_N, ANN = 0.0041, 0.5, 24, 252
CAP, EFF_N_FLOOR, EFF_N_LAMBDA = 1.0 / 16, 16.0, 0.01
HIDDEN, LR, WD, STEPS, CLIP, EPISODE, NOISE, DROPOUT, TAU0 = 32, 5e-4, 1e-3, 400, 1.0, 120, 0.1, 0.1, 0.3
VALID, PURGE, EVAL_EVERY, SEEDS = 60, 5, 20, (0, 1, 2)
T_GATE, MDD_SLACK, EFFN_GATE, MAXW_GATE, IC_SLACK, WORST_GATE = 2.0, 0.03, 12.0, 0.10, 0.01, -0.03
torch.set_num_threads(6)


def rank_gauss_rows(wide: pd.DataFrame) -> pd.DataFrame:
    r = wide.rank(axis=1, method="average"); n = wide.notna().sum(axis=1)
    q = (r.sub(0.5, axis=0)).div(n, axis=0)
    return pd.DataFrame(norm.ppf(q.clip(1e-6, 1 - 1e-6)), index=wide.index, columns=wide.columns).where(wide.notna(), 0.0)


class Panel:
    def __init__(self, store: Store, sessions: list[date]) -> None:
        self.sessions = sessions
        raw = {name: _scores(store, name, sessions).reindex(sessions) for name in FEATS}
        trad = _tradable(store, sessions)
        now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
        prices = read_prices(store, as_of=now, lookback=(sessions[-1] - sessions[0]).days + 40, columns=["close"], adjusted=True, market="KR")
        prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
        wide = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
        fwd = (wide.shift(-2) / wide.shift(-1) - 1.0); fwd = fwd.where(fwd.abs() <= MAX_MOVE).reindex(sessions)
        entities = sorted(set(raw["ranker"].columns) & set(fwd.columns))
        self.entities = entities
        S, N = len(sessions), len(entities)
        feats = [rank_gauss_rows(raw[n].reindex(columns=entities)) for n in FEATS]
        ema = feats[-1].ewm(span=5).mean().where(raw["ranker"].reindex(columns=entities).notna(), 0.0)
        feats.append(ema)
        x = np.stack([f.to_numpy(np.float32) for f in feats], axis=-1)   # S × N × 8
        present = raw["ranker"].reindex(columns=entities).notna().to_numpy()
        tradable = np.zeros((S, N), dtype=bool)
        eidx = {e: i for i, e in enumerate(entities)}
        for si, day in enumerate(sessions):
            ok = [eidx[e] for e in trad.get(day, set()) if e in eidx]
            tradable[si, ok] = True
        r = fwd[entities].to_numpy(np.float32); has = np.isfinite(r)
        self.x = torch.tensor(x); self.z_ranker = torch.tensor(feats[6].to_numpy(np.float32))
        self.mask = torch.tensor(present & tradable & has); self.r = torch.tensor(np.where(has, r, 0.0).astype(np.float32))
        self.risk = raw["risk"].reindex(columns=entities); self.ranker_raw = raw["ranker"].reindex(columns=entities)
        print(f"패널: {S}세션 × {N}종목 × {x.shape[2]}피처 · 세션당 거래가능 평균 {self.mask.sum(1).float().mean():.0f}", flush=True)


class Policy(nn.Module):
    """s = z_ranker + f(x, 풀링). f 의 마지막 층은 0 초기화 — 출발점이 ranker 다."""

    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.local = nn.Sequential(nn.Linear(n_features + 2, HIDDEN), nn.ReLU(), nn.Linear(HIDDEN, HIDDEN), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(2 * HIDDEN, HIDDEN), nn.ReLU(), nn.Linear(HIDDEN, 1))
        nn.init.zeros_(self.head[-1].weight); nn.init.zeros_(self.head[-1].bias)
        # 온도 — 배관 점검(9/4)에서 온도 없이 softmax(z) 를 쓰니 유효종목 1,050 = 시장 그 자체였다.
        # log τ 를 학습한다. 초기 τ=0.3 이면 z 상위 24 안팎이 비중을 가져간다.
        self.log_tau = nn.Parameter(torch.tensor(float(np.log(TAU0))))

    def forward(self, x, z_ranker, held, days, mask):
        h = self.local(torch.cat([x, held.unsqueeze(-1), days.unsqueeze(-1)], dim=-1))
        m = mask.unsqueeze(-1).float()
        ctx = (h * m).sum(0, keepdim=True) / m.sum().clamp(min=1.0)
        s = (z_ranker + self.head(torch.cat([h, ctx.expand_as(h)], dim=-1)).squeeze(-1)) / self.log_tau.exp()
        return s.masked_fill(~mask, -1e9)


def capped_softmax(s: torch.Tensor, cap: float = CAP, iters: int = 5) -> torch.Tensor:
    w = torch.softmax(s, dim=0)
    for _ in range(iters):
        over = w > cap
        if not bool(over.any()):
            break
        excess = (w[over] - cap).sum()
        under = (~over).float() * w
        w = torch.where(over, torch.full_like(w, cap), w + excess * under / under.sum().clamp(min=1e-9))
    return w / w.sum()


def warm_start(panel: Panel, t: int) -> torch.Tensor:
    s = panel.z_ranker[t].masked_fill(~panel.mask[t], -1e9)
    top = torch.topk(s, TOP_N).indices; w = torch.zeros(panel.x.shape[1]); w[top] = 1.0 / TOP_N
    return w


def simulate(policy: Policy, panel: Panel, idx: range, held0: torch.Tensor, *, train: bool):
    N = panel.x.shape[1]; held = held0; days = torch.zeros(N)
    xs, rows, scores, ws = [], [], [], []
    for t in idx:
        mask = panel.mask[t]; x = panel.x[t]
        if train:
            x = x + NOISE * torch.randn_like(x)
            x = x * (torch.rand_like(x) > DROPOUT).float() / (1 - DROPOUT)
        s = policy(x, panel.z_ranker[t], held, days, mask)
        w = capped_softmax(s); r = panel.r[t]
        port = (w * r).sum(); bench = r[mask].mean() if bool(mask.any()) else torch.zeros(())
        turnover = (w - held).abs().sum()
        xs.append(port - ONE_WAY_COST * turnover - bench)
        eff = 1.0 / (w * w).sum()
        rows.append({"net": float((port - ONE_WAY_COST * turnover).detach()), "excess": float(xs[-1].detach()), "turnover": float(turnover.detach()),
                     "eff_n": float(eff.detach()), "max_w": float(w.max().detach())})
        scores.append(s.detach()); ws.append(w.detach())
        held = (w * (1.0 + r)) / (1.0 + port).clamp(min=1e-6)
        days = torch.where(w.detach() > 1e-4, (days * 60 + 1) / 60, torch.zeros(N)).clamp(max=1.0)
        if train:
            held = held  # 그래디언트는 보유 경로로도 흐른다 — "붙잡기" 를 배울 수 있게
        if not train:
            held = held.detach()
    x = torch.stack(xs)
    ir = x.mean() / x.std().clamp(min=1e-6)
    pen = torch.stack([torch.relu(EFF_N_FLOOR - 1.0 / (w * w).sum()) for w in ws]).mean() if train else torch.zeros(())
    return ir, pen, rows, scores, held.detach()


def train_one(panel: Panel, train_idx: range, valid_idx: range, seed: int, steps: int) -> tuple[Policy, dict]:
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    policy = Policy(panel.x.shape[2]); opt = torch.optim.Adam(policy.parameters(), lr=LR, weight_decay=WD)
    best = (-1e9, None, 0); log = []
    lo, hi = train_idx.start, train_idx.stop
    for step in range(1, steps + 1):
        start = int(rng.integers(lo, max(lo + 1, hi - EPISODE)))
        ep = range(start, min(start + EPISODE, hi))
        ir, pen, _r, _s, _h = simulate(policy, panel, ep, warm_start(panel, start), train=True)
        loss = -ir + EFF_N_LAMBDA * pen
        opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(policy.parameters(), CLIP); opt.step()
        if step % EVAL_EVERY == 0 or step == steps:
            with torch.no_grad():
                v_ir, _p, vrows, _s, _h = simulate(policy, panel, valid_idx, warm_start(panel, valid_idx.start), train=False)
            log.append((step, float(ir), float(v_ir), float(np.mean([r["eff_n"] for r in vrows]))))
            if float(v_ir) > best[0]:
                best = (float(v_ir), {k: v.clone() for k, v in policy.state_dict().items()}, step)
    if best[1] is not None:
        policy.load_state_dict(best[1])
    return policy, {"best_step": best[2], "best_valid_ir": best[0], "log": log}


class Ensemble:
    def __init__(self, policies: list[Policy]) -> None:
        self.policies = policies

    def __call__(self, x, z, held, days, mask):
        return torch.stack([p(x, z, held, days, mask) for p in self.policies]).mean(0)


def baseline_path(panel: Panel, floor: float = 0.0):
    """대조: EMA5·3N ranker EW24 (오늘 채택된 현행). 전 구간 연속 시뮬 → 세션별 순수익·보유비중."""
    scores = panel.ranker_raw.ewm(span=5).mean(); held: list[int] = []; prev = None; out = {}; weights = {}
    N = len(panel.entities)
    for t, day in enumerate(panel.sessions):
        m = panel.mask[t].numpy(); f = scores.iloc[t].to_numpy(); f = np.where(m, f, np.nan)
        order = [int(i) for i in np.argsort(-np.nan_to_num(f, nan=-1e9)) if m[i]]
        if not order:
            continue
        held = pick_mult(held, pd.Index(order), TOP_N, 3)
        w = np.zeros(N, np.float32); w[held] = 1.0 / len(held)
        r = panel.r[t].numpy(); port = float((w * r).sum())
        turnover = float(np.abs(w - (prev if prev is not None else 0)).sum())
        out[t] = port - ONE_WAY_COST * turnover; weights[t] = torch.tensor(w)
        drift = w * (1 + r); prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out), weights


def mdd(x: pd.Series) -> float:
    nav = (1 + x).cumprod(); return float((nav / nav.cummax() - 1).min())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    parser.add_argument("--quick", action="store_true", help="배관 점검: 스텝 20·시드 1·블록 1")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== M4 5회차 — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    sessions = trading_days(Market.KR, START, END)
    panel = Panel(store, sessions)
    steps, seeds = (20, (0,)) if args.quick else (STEPS, SEEDS)
    blocks = [b for b in blocks_for(sessions) if b[1] >= date(2026, 1, 1)]   # 판정 블록 = 2026-01~06 (시행 L 과 동일)
    if args.quick:
        blocks = blocks[-1:]
    targets = ic_module.build_targets(store, as_of=datetime.combine(END, time(16), tzinfo=UTC), lookback=(END - START).days + 60, market="KR")
    base_net, base_w = baseline_path(panel)
    sidx = {s: i for i, s in enumerate(sessions)}
    pol_rows, pol_scores, block_diff = [], [], []
    for b, (train_end, ps, pe) in enumerate(blocks, 1):
        tr_end = sidx[train_end] + 1; tr = range(0, tr_end - VALID - PURGE); va = range(tr_end - VALID, tr_end)
        pi = range(sidx[ps], sidx[pe] + 1)
        policies = []
        for seed in seeds:
            p, info = train_one(panel, tr, va, seed, steps); policies.append(p)
            print(f"블록 {b}/{len(blocks)} 시드 {seed}: 최고 검증 IR {info['best_valid_ir']:+.3f} @스텝 {info['best_step']} · "
                  + " ".join(f"[{s}: 학 {a:+.2f} 검 {v:+.2f} N {n:.0f}]" for s, a, v, n in info["log"][-3:]), flush=True)
        ens = Ensemble(policies)
        with torch.no_grad():
            _ir, _p, rows, scores, _h = simulate(ens, panel, pi, base_w.get(pi.start - 1, warm_start(panel, pi.start)), train=False)
        for t, row, s in zip(pi, rows, scores):
            row["t"] = t; pol_rows.append(row)
            m = panel.mask[t].numpy(); vals = s.numpy()
            pol_scores.append(pd.DataFrame({"entity_id": np.array(panel.entities)[m], "session": sessions[t], "score": vals[m]}))
        pnet = pd.Series({r["t"]: r["net"] for r in rows}); bnet = base_net.reindex(pnet.index)
        block_diff.append(float(((1 + pnet).prod() - 1) - ((1 + bnet).prod() - 1)))
        print(f"블록 {b} 판정 {ps}~{pe}: 정책 순 {((1+pnet).prod()-1):+.2%} 대조 {((1+bnet).prod()-1):+.2%} · 유효N {np.mean([r['eff_n'] for r in rows]):.1f} · 회전/일 {np.mean([r['turnover'] for r in rows]):.2f}", flush=True)
    pol = pd.DataFrame(pol_rows).set_index("t"); pnet = pol["net"]; bnet = base_net.reindex(pnet.index)
    diff = (pnet - bnet).dropna(); t_stat = float(ic_module.newey_west_t(diff, lag=4))
    scored = pd.concat(pol_scores, ignore_index=True)
    ic_p = ic_module.daily_ic(scored.merge(targets, on=["entity_id", "session"]))
    rank_scored = pd.concat([pd.DataFrame({"entity_id": panel.entities, "session": sessions[t], "score": panel.ranker_raw.iloc[t].to_numpy()}).dropna() for t in pnet.index], ignore_index=True)
    ic_r = ic_module.daily_ic(rank_scored.merge(targets, on=["entity_id", "session"]))
    c1 = t_stat >= T_GATE; c2 = mdd(pnet) >= mdd(bnet) - MDD_SLACK
    c3 = pol["eff_n"].mean() >= EFFN_GATE and pol["max_w"].max() <= MAXW_GATE
    c4 = ic_p.mean() >= ic_r.mean() - IC_SLACK; c5 = min(block_diff) >= WORST_GATE
    lines = [
        f"판정 {len(pnet)}세션 · 정책 순수익 연 {pnet.mean()*ANN:+.1%} (IR {pnet.mean()/pnet.std()*np.sqrt(ANN):+.2f}) 대 대조 {bnet.mean()*ANN:+.1%} (IR {bnet.mean()/bnet.std()*np.sqrt(ANN):+.2f})",
        f"① Δ순일수익 {diff.mean()*ANN:+.1%}/년 · NW t {t_stat:+.2f} {'○' if c1 else '×'}",
        f"② MDD 정책 {mdd(pnet):.1%} 대 대조 {mdd(bnet):.1%} {'○' if c2 else '×'}",
        f"③ 유효종목 평균 {pol['eff_n'].mean():.1f} · 최대 비중 {pol['max_w'].max():.1%} · 회전/일 {pol['turnover'].mean():.2f} {'○' if c3 else '×'}",
        f"④ IC(h5) 정책 {ic_p.mean():+.4f} 대 ranker {ic_r.mean():+.4f} {'○' if c4 else '×'}",
        f"⑤ 블록별 Δ누적 {' '.join(f'{d:+.1%}' for d in block_diff)} · 최악 {min(block_diff):+.1%} {'○' if c5 else '×'}",
        f"판정: {'채택 후보' if all((c1, c2, c3, c4, c5)) else '기각'}",
    ]
    print("\n" + "\n".join(lines), flush=True)
    if args.save and not args.quick:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "e2e-drl-final-2026-09:M4-5", "valid_from": now, "observed_at": now, "source": "trial_e2e_final",
            "market": "KR", "family": "rl", "n_trials": 1, "protocol_hash": digest, "detail": " | ".join(lines)[:900],
        }], ingest_run_id=f"trial-e2e-final-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: rl/M4-5 · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
