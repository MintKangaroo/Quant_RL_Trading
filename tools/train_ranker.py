"""ranker 모델 학습 — 창고 `signals` 표 + 전방수익 라벨로, 시행 L 설정 그대로.

    .venv/bin/python tools/train_ranker.py --through 2026-06-30
    .venv/bin/python tools/train_ranker.py --schedule 2025-06-30 2026-06-30   # 월말마다(워크포워드 산출물)

산출물: `<root>/models/ranker/ranker-vX-YYYYMMDD.txt` (LightGBM) + `.json` (사이드카).
`--through` 는 **라벨을 아는 마지막 날** 이다 — 그 날까지의 전방수익으로 학습하므로
`usable_from` = 다음 날. 홀드아웃(2026-07-01~)을 넘는 날짜는 거부한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.analysts.ranker import (  # noqa: E402
    BASE_ANALYSTS, FEATURES, GBM_PARAMS, GBM_ROUNDS, SCORE_FEATURES, VERSION, model_dir, rank_gauss,
)
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
HOLDOUT = date(2026, 7, 1)
PROTOCOL = Path("docs/protocols/rank-objective-ranker-2026-09.md")
#: 신호 역사 전부. 국장 signals 는 2024-06-25 부터다.
HISTORY_DAYS = 900
CHUNK_DAYS = 90


def _moment(day: date) -> datetime:
    # 하루의 끝(KST). 미장 세션 신호(익일 05:20 KST)도 그날 라벨과 같이 들어온다.
    return datetime(day.year, day.month, day.day, 23, 59, tzinfo=KST)


def load_signals(store: Store, *, as_of: datetime, market: Market) -> pd.DataFrame:
    """(entity_id, session, chart…risk) — as_of 시점에 알던 기초 Analyst 점수."""
    prefix = f"{market}:"
    parts: list[pd.DataFrame] = []
    floor = as_of - timedelta(days=HISTORY_DAYS)
    start = floor
    while start < as_of:
        end = min(start + timedelta(days=CHUNK_DAYS), as_of)
        chunk = store.get(
            "signals", as_of=as_of, lookback=as_of - start, until=end,
            columns=["entity_id", "valid_from", "observed_at", "analyst", "score"],
        )
        if not chunk.empty:
            chunk = chunk[
                chunk["entity_id"].astype(str).str.startswith(prefix)
                & chunk["analyst"].astype(str).isin(BASE_ANALYSTS)
            ]
        if not chunk.empty:
            chunk = chunk.sort_values("observed_at").groupby(["entity_id", "valid_from", "analyst"], as_index=False).tail(1)
            chunk["session"] = chunk["valid_from"].dt.date
            chunk["feature"] = chunk["analyst"].map(BASE_ANALYSTS)
            parts.append(chunk.loc[:, ["entity_id", "session", "feature", "score"]])
        start = end
    if not parts:
        return pd.DataFrame(columns=["entity_id", "session", *SCORE_FEATURES])
    stacked = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["entity_id", "session", "feature"], keep="last")
    wide = stacked.pivot_table(index=["entity_id", "session"], columns="feature", values="score", aggfunc="last").reset_index()
    for column in SCORE_FEATURES:
        if column not in wide.columns:
            wide[column] = np.nan
    return wide.loc[:, ["entity_id", "session", *SCORE_FEATURES]]


def build_frame(store: Store, *, through: date) -> pd.DataFrame:
    as_of = _moment(through)
    frames = []
    for market in (Market.KR, Market.US):
        signals = load_signals(store, as_of=as_of, market=market)
        if signals.empty:
            print(f"  {market}: 신호 0행", flush=True); continue
        targets = ic.build_targets(store, as_of=as_of, lookback=HISTORY_DAYS, market=str(market))
        merged = signals.merge(targets, on=["entity_id", "session"], how="inner")
        merged["market"] = str(market)
        print(f"  {market}: 신호 {len(signals):,}행 · 라벨 {len(targets):,}행 → 학습 {len(merged):,}행 · "
              f"{merged['session'].nunique()}세션 ({merged['session'].min()}~{merged['session'].max()})", flush=True)
        frames.append(merged)
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    frame = rank_gauss(frame, [*SCORE_FEATURES, "target"], by=["market", "session"])
    frame["is_us"] = (frame["market"] == "US").astype(float)
    return frame


def train_one(store: Store, *, through: date, threads: int) -> Path:
    import lightgbm as lgb

    if through >= HOLDOUT:
        raise SystemExit(f"{through} 는 홀드아웃(≥{HOLDOUT}) 이다. 학습에 안 쓴다.")
    print(f"=== 학습 through={through} ===", flush=True)
    frame = build_frame(store, through=through)
    if frame.empty:
        raise SystemExit("학습 행이 없다.")
    X = frame.loc[:, list(FEATURES)].to_numpy(np.float32)
    y = frame["target"].to_numpy(np.float32)
    params = dict(GBM_PARAMS, num_threads=threads)
    booster = lgb.train(params, lgb.Dataset(X, y, feature_name=list(FEATURES)), num_boost_round=GBM_ROUNDS)
    folder = model_dir(store.root); folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f"{VERSION}-{through:%Y%m%d}"
    model_path, sidecar = Path(f"{stem}.txt"), Path(f"{stem}.json")
    booster.save_model(str(model_path))
    gain = dict(zip(FEATURES, booster.feature_importance(importance_type="gain")))
    total = sum(gain.values()) or 1.0
    meta = {
        "version": VERSION, "trained_through": through.isoformat(),
        "usable_from": (through + timedelta(days=1)).isoformat(),
        "features": list(FEATURES), "rows": int(len(frame)),
        "sessions": {m: int(frame.loc[frame["market"] == m, "session"].nunique()) for m in ("KR", "US")},
        "params": {k: v for k, v in GBM_PARAMS.items() if k != "verbose"}, "rounds": GBM_ROUNDS,
        "gain": {k: round(float(v) / total, 4) for k, v in gain.items()},
        "protocol_hash": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else "",
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest()[:16],
    }
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {model_path} · {len(frame):,}행 · gain " +
          " ".join(f"{k} {v:.0%}" for k, v in meta["gain"].items()), flush=True)
    return model_path


def month_ends(start: date, end: date) -> list[date]:
    days, cursor = [], start
    while cursor <= end:
        nxt = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        days.append(nxt - timedelta(days=1)); cursor = nxt
    return [d for d in days if start <= d <= end]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--through", type=date.fromisoformat, help="라벨을 아는 마지막 날")
    parser.add_argument("--schedule", nargs=2, type=date.fromisoformat, metavar=("START", "END"), help="월말마다 하나씩")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--force", action="store_true", help="이미 있는 산출물도 다시 만든다")
    args = parser.parse_args(argv)
    if not args.through and not args.schedule:
        parser.error("--through 또는 --schedule")
    store = Store(root=Path(args.root))
    days = month_ends(*args.schedule) if args.schedule else [args.through]
    for day in days:
        target = model_dir(store.root) / f"{VERSION}-{day:%Y%m%d}.json"
        if target.exists() and not args.force:
            print(f"건너뜀 {target.name} (있음)"); continue
        train_one(store, through=day, threads=args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
