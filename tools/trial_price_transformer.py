"""시행 AC — 가격 계열 트랜스포머, 앙상블의 구성원으로. docs/protocols/price-transformer-2026-09.md.

    .venv/bin/python tools/trial_price_transformer.py [--save] [--smoke 2] [--no-extra-seeds]

C0 = 현행 GBM(h5, 시행 AB 의 B0 과 같은 워크포워드) · C1 = 트랜스포머 단독 · C2 = 순위 평균.
판정 블록은 시행 AB 와 **같다**(`trial_ranker_ensemble.blocks_for`). 트랜스포머는 5블록마다 재학습한다.
입력은 (종목, 60세션, 4채널) — 창고의 수정 종가·거래대금·K200 에서 직접 만든다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time as time_module
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_bench_construct import _caps  # noqa: E402
from tools.trial_overlay import ANN, INDEX, MAX_MOVE  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_ensemble import (  # noqa: E402
    BOX_END,
    FEATS,
    JUDGE_END,
    JUDGE_START,
    blocks_for,
    load_panel,
    portfolio,
    summarize,
    walk_forward,
)

PROTOCOL = Path("docs/protocols/price-transformer-2026-09.md")
CACHE = Path("data/_diag-long")
WINDOW, MIN_VALID, CHANNELS = 60, 40, 4
D_MODEL, HEADS, LAYERS, FFN, DROPOUT = 32, 4, 2, 64, 0.1
EPOCHS, LR, WEIGHT_DECAY, CLIP = 3, 1e-3, 0.01, 1.0
RETRAIN_EVERY, PURGE = 5, 5
N = 24
GATE_ANN, GATE_T, GATE_MDD, GATE_ASYM = 0.01, 2.0, 0.03, -0.02


def build_channels(store: Store, sessions: list[date]) -> tuple[np.ndarray, list[date], list[str]]:
    """(세션, 종목, 채널) 배열. 창 앞쪽 자료가 필요해 판정 시작보다 일찍부터 읽는다."""
    first = sessions[0] - timedelta(days=int(WINDOW * 7 / 5) + 30)
    days = list(trading_days(Market.KR, first, sessions[-1]))
    now = datetime.combine(days[-1], time(16), tzinfo=UTC)
    span = (days[-1] - days[0]).days + 10
    frame = read_prices(store, as_of=now, lookback=span, columns=["close", "value", "volume"],
                        adjusted=True, market="KR")
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.date
    close = frame.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").reindex(days)
    value = frame.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").reindex(days)
    volume = frame.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last").reindex(days)
    del frame
    value = value.where(value.notna(), close * volume)
    ret = close.pct_change(fill_method=None)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    # ① 일수익 — 세션 안 rank-gauss
    pct = ret.rank(axis=1, pct=True)
    count = ret.notna().sum(axis=1).clip(lower=1)
    ch_ret = pd.DataFrame(norm.ppf(((pct.mul(count, axis=0)) - 0.5).div(count, axis=0).clip(1e-4, 1 - 1e-4)),
                          index=ret.index, columns=ret.columns)
    # ② K200 일수익 원값(×10, ±1 클립) — 모든 종목에 같은 값
    idx = store.get("indices", as_of=now, lookback=span, market="KR", columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    mkt = idx.groupby("day")["close"].last().reindex(days).pct_change()
    ch_mkt = np.clip(mkt.fillna(0.0).to_numpy() * 10.0, -1.0, 1.0)
    # ③ log 거래대금 — 창 안 z 는 배치 때 만든다(여기선 log 만)
    log_value = np.log(value.where(value > 0))
    missing = ret.isna()
    cube = np.zeros((len(days), close.shape[1], CHANNELS), dtype=np.float32)
    cube[:, :, 0] = ch_ret.fillna(0.0).to_numpy(np.float32)
    cube[:, :, 1] = ch_mkt[:, None].astype(np.float32)
    cube[:, :, 2] = log_value.to_numpy(np.float32)  # NaN 포함 — 배치에서 z 로 바꾸며 0 으로 채운다
    cube[:, :, 3] = missing.to_numpy(np.float32)
    return cube, days, list(close.columns)


def window_batch(cube: np.ndarray, day_index: int) -> tuple[np.ndarray, np.ndarray]:
    """세션 하나의 (종목, 60, 4) 입력과 유효 마스크. 창은 t−59…t (등록 정정 2)."""
    x = cube[day_index - WINDOW + 1: day_index + 1].transpose(1, 0, 2).copy()  # (종목, 60, 4)
    valid = (x[:, :, 3] < 0.5).sum(axis=1) >= MIN_VALID
    lv = x[:, :, 2]
    mean = np.nanmean(np.where(np.isfinite(lv), lv, np.nan), axis=1, keepdims=True)
    std = np.nanstd(np.where(np.isfinite(lv), lv, np.nan), axis=1, keepdims=True)
    z = (lv - mean) / np.where(std > 1e-6, std, 1.0)
    x[:, :, 2] = np.nan_to_num(np.clip(z, -5, 5), nan=0.0)
    return x, valid


def make_model(seed: int):
    import torch
    from torch import nn

    torch.manual_seed(seed)

    class Ranker(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.inp = nn.Linear(CHANNELS, D_MODEL)
            self.pos = nn.Parameter(torch.zeros(1, WINDOW, D_MODEL))
            layer = nn.TransformerEncoderLayer(D_MODEL, HEADS, FFN, DROPOUT, batch_first=True)
            self.encoder = nn.TransformerEncoder(layer, LAYERS)
            self.out = nn.Linear(D_MODEL, 1)

        def forward(self, x):  # type: ignore[no-untyped-def]
            h = self.encoder(self.inp(x) + self.pos)
            return self.out(h.mean(dim=1)).squeeze(-1)

    return Ranker()


def train(cube: np.ndarray, col_of: dict[str, int], targets: dict[int, pd.Series], train_days: list[int], seed: int):
    import torch

    model = make_model(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    rng = np.random.default_rng(seed)
    model.train()
    for _ in range(EPOCHS):
        for day_index in rng.permutation(train_days):
            y = targets.get(int(day_index))
            if y is None or y.empty:
                continue
            x, valid = window_batch(cube, int(day_index))
            cols = np.array([col_of[e] for e in y.index if e in col_of])
            names = [e for e in y.index if e in col_of]
            keep = valid[cols]
            if keep.sum() < 50:
                continue
            xb = torch.from_numpy(x[cols[keep]])
            yb = torch.from_numpy(y.loc[names].to_numpy(np.float32)[keep])
            opt.zero_grad()
            loss = torch.nn.functional.mse_loss(model(xb), yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), CLIP)
            opt.step()
    model.eval()
    return model


def predict(model, cube: np.ndarray, entities: list[str], day_index: int, session: date) -> pd.DataFrame:
    import torch

    x, valid = window_batch(cube, day_index)
    with torch.inference_mode():
        pred = model(torch.from_numpy(x[valid])).numpy()
    names = [e for e, ok in zip(entities, valid, strict=True) if ok]
    return pd.DataFrame({"entity_id": names, "session": session, "pred": pred})


def run_transformer(cube, days, entities, panel, sessions, blocks, seed: int) -> tuple[pd.DataFrame, float]:
    col_of = {e: i for i, e in enumerate(entities)}
    index_of = {d: i for i, d in enumerate(days)}
    targets = {index_of[s]: part.set_index("entity_id")["y5"].dropna()
               for s, part in panel.groupby("session") if s in index_of}
    out, model, began = [], None, time_module.monotonic()  # invariant-allow: wallclock — 소요 시간 기록
    for number, (first, last) in enumerate(blocks):
        if number % RETRAIN_EVERY == 0:
            train_end = sessions[first - PURGE - 1]
            train_days = [index_of[s] for s in sessions if s <= train_end and index_of[s] >= WINDOW]
            model = train(cube, col_of, targets, train_days, seed)
            print(f"  seed {seed} · 재학습 {number // RETRAIN_EVERY + 1} · 학습 세션 {len(train_days)} (~{train_end}) · "
                  f"누적 {(time_module.monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock
        for s in sessions[first: last + 1]:
            out.append(predict(model, cube, entities, index_of[s], s))
    return pd.concat(out, ignore_index=True), (time_module.monotonic() - began) / 60  # invariant-allow: wallclock


def rank_corr(a: pd.DataFrame, b: pd.DataFrame) -> pd.Series:
    m = a.merge(b, on=["entity_id", "session"], suffixes=("_a", "_b"))
    return m.groupby("session").apply(lambda g: g["pred_a"].rank().corr(g["pred_b"].rank()), include_groups=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선·시간 확인용, 수치는 안 찍는다")
    parser.add_argument("--no-extra-seeds", action="store_true", help="시드 1·2(기록 항목)를 건너뛴다")
    parser.add_argument("--threads", type=int, default=12)
    args = parser.parse_args(argv)
    import torch

    torch.set_num_threads(args.threads)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AC — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    panel = load_panel(CACHE)
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    panel = rank_gauss(panel, [*FEATS, "y5"])
    sessions = sorted(panel["session"].unique())
    blocks = blocks_for(sessions, 60)  # 시행 AB 와 같은 판정 블록
    if args.smoke:
        blocks = blocks[: args.smoke]
    cube, days, entities = build_channels(store, sessions)
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} · 블록 {len(blocks)} · 입력 {cube.shape} "
          f"({cube.nbytes / 1e6:.0f}MB)", flush=True)

    tf_pred, minutes = run_transformer(cube, days, entities, panel, sessions, blocks, seed=0)
    if args.smoke:
        print(f"트랜스포머 예측 {len(tf_pred):,}행 · {minutes:.1f}분 — 배선 확인만(수치 안 찍음)", flush=True)
        return 0
    gbm_pred = walk_forward(panel, sessions, 5, blocks)

    def pct(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.assign(pred=frame.groupby("session")["pred"].rank(pct=True))

    both = pct(gbm_pred).merge(pct(tf_pred), on=["entity_id", "session"], suffixes=("_g", "_t"))
    scores = {
        "C0": gbm_pred, "C1": tf_pred,
        "C2": both.assign(pred=(both["pred_g"] + both["pred_t"]) / 2)[["entity_id", "session", "pred"]],
    }
    from tools.trial_overlay import _index, _prices
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    caps = _caps(store, sessions)

    rows, series = {}, {}
    for name, score in scores.items():
        daily = portfolio(score, ret, trad)
        b = bench.reindex(daily.index).fillna(0.0)
        m = summarize(daily, b)
        m["box_ann"] = float(daily[daily.index <= BOX_END].mean() * ANN)
        m["rally_ann"] = float(daily[daily.index > BOX_END].mean() * ANN)
        top = score.sort_values("pred", ascending=False).groupby("session").head(N)
        mega = 0
        for s, part in top.groupby("session"):
            if s in caps.index:
                big = set(caps.loc[s].dropna().sort_values(ascending=False).index[:2])
                mega += int(bool(big & set(part["entity_id"])))
        m["mega"] = mega
        rows[name], series[name] = m, daily

    corr = rank_corr(gbm_pred, tf_pred)
    lines = [f"기록: GBM·트랜스포머 세션별 순위 상관 평균 {corr.mean():+.3f} (5%~95%: {corr.quantile(.05):+.3f}~{corr.quantile(.95):+.3f}) · "
             f"학습+추론 {minutes:.0f}분(seed 0)"]
    if not args.no_extra_seeds:
        extra = [run_transformer(cube, days, entities, panel, sessions, blocks, seed=k)[0] for k in (1, 2)]
        pairs = [rank_corr(tf_pred, extra[0]).mean(), rank_corr(tf_pred, extra[1]).mean(), rank_corr(extra[0], extra[1]).mean()]
        lines.append(f"기록: 시드 0·1·2 예측 순위 상관 {pairs[0]:+.3f} · {pairs[1]:+.3f} · {pairs[2]:+.3f}")
    lines += ["", "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR(K200) | 상승 | 하락 | 비대칭 | 시총2 상위24 세션 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in rows.items():
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                     f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['ir']:+.2f} | {m['up']:.2f} | {m['down']:.2f} | "
                     f"{m['asym']:+.3f} | {m['mega']} |")
    lines.append("")
    passed = []
    for name in ("C1", "C2"):
        m, c0 = rows[name], rows["C0"]
        t = float(ic_module.newey_west_t((series[name] - series["C0"]).dropna(), lag=4))
        c = (m["box_ann"] >= c0["box_ann"] + GATE_ANN and m["rally_ann"] >= c0["rally_ann"] + GATE_ANN,
             t >= GATE_T, m["mdd"] >= c0["mdd"] - GATE_MDD, m["asym"] >= c0["asym"] + GATE_ASYM)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①두 국면 +1%p {mark(c[0])} · ②NW t {t:+.2f} {mark(c[1])} · ③MDD {m['mdd']:.1%} {mark(c[2])} · "
                     f"④비대칭 {m['asym']:+.3f} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["sharpe"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행 GBM 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "price-transformer-2026-09:AC", "valid_from": now, "observed_at": now,
            "source": "trial_price_transformer", "market": "KR", "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-price-transformer-AC-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/AC · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
