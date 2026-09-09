"""6차 학습 — 랭커 정보원 묶음 G1~G4 시행. docs/protocols/ranker-sources-round6-2026-09.md 대로 묶음당 한 번 잰다.

    .venv/bin/python tools/trial_ranker_sources.py --group G1 [--adopted G1,G2] [--save]
    .venv/bin/python tools/trial_ranker_sources.py --group G1 --smoke 3      # 피처 조각 3세션만 굽고 병합 확인, 판정 없음

루프·설정은 시행 L(tools/trial_rank_ranker.py)과 같다. 다른 것은 셋뿐이다 — 처리 피처가 묶음 원자료로 늘고, 대조가
fundamental 이 아니라 **같은 루프로 학습한 기본 7피처 랭커(+채택된 묶음)** 이며, 주 시장이 묶음마다 다르다.

피처는 `analysts/ranker_sources.build` 로 세션마다 개장 직전 as_of 에서 만들고, `data/_diag/ranker-sources/` 에 월 단위
조각으로 적재한다(WSL 감시 밖 장기 작업 규칙 — 죽어도 그 달만 다시 굽는다). 이 파일은 창고가 아니다.

**판정은 2026-10-01 전엔 돌지 않는다**(MEASURE_FROM). 그 전엔 --smoke 만 허용된다 — 사전등록의 "중간 들여다보기 금지".
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.ranker_sources import GROUPS, build  # noqa: E402
from quant_rl_trading.analysts.risk import RiskAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_pooled import FEATS, TOP_N, _nw, daily_ic, fit_gbm, load_kr, load_us, top_excess  # noqa: E402
from tools.trial_pooled_deep import blocks_for  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402

PROTOCOL = Path("docs/protocols/ranker-sources-round6-2026-09.md")
TRIAL_PREFIX = "ranker-sources-round6-2026-09"
MEASURE_FROM = date(2026, 10, 1)
T_GATE = 2.0
WORST_GATE = -0.03
#: 주 시장 — 자료가 있는 쪽. 다른 시장은 ④(지지 않을 것)만 본다.
PRIMARY = {"G1": "KR", "G2": "KR", "G3": "US", "G4": "KR", "G5": "KR", "G6": "US"}
#: 세션 개장 직전(UTC). 국장 09:00 KST, 미장 09:30 ET(서머타임 무시 — 13:30 UTC 는 어느 쪽이든 개장 전이다).
OPEN_UTC = {"KR": timedelta(hours=0), "US": timedelta(hours=13, minutes=30)}
CACHE = Path("data/_diag/ranker-sources")
BOTTOM_SHARE = 0.10


# --------------------------------------------------------------------------- 피처 조각 적재


def build_panel(store: Store, group: str, market: str, sessions: list, *, limit: int | None = None) -> pd.DataFrame:
    """(entity_id, session, 묶음 피처) — 세션마다 개장 직전 as_of. 월 조각이 있으면 다시 굽지 않는다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    columns = list(GROUPS[group])
    todo = sessions[:limit] if limit else sessions
    by_month: dict[str, list] = {}
    for s in todo:
        by_month.setdefault(f"{s:%Y%m}", []).append(s)
    parts = []
    for month, days in by_month.items():
        path = CACHE / f"{group}-{market}-{month}.parquet"  # invariant-allow: data-access — 창고가 아닌 작업 파일
        if path.exists() and limit is None:
            parts.append(pd.read_parquet(path))  # invariant-allow: data-access — 창고가 아닌 작업 파일
            continue
        rows = []
        for s in days:
            as_of = datetime(s.year, s.month, s.day, tzinfo=UTC) + OPEN_UTC[market]
            analyst = RiskAnalyst(store, ReplayClock(as_of), market=Market(market))
            raw = build(group, analyst, as_of)
            if raw.empty:
                continue
            raw = raw.reset_index().rename(columns={"index": "entity_id"})
            raw["session"] = s
            rows.append(raw[["entity_id", "session", *columns]])
        chunk = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["entity_id", "session", *columns])
        if limit is None:
            chunk.to_parquet(path, index=False)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        parts.append(chunk)
        print(f"  {group} {market} {month}: {len(chunk):,}행 ({len(days)}세션)", flush=True)
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["entity_id", "session", *columns])
    out["session"] = pd.to_datetime(out["session"]).dt.date
    return out


def attach(panel: pd.DataFrame, features: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """점수 패널에 묶음 피처를 붙이고 세션 안 rank-gauss. 없는 종목·결측은 0(순위 중앙) — 사전등록 규칙."""
    merged = panel.merge(features, on=["entity_id", "session"], how="left")
    merged = rank_gauss(merged, columns)
    merged[columns] = merged[columns].fillna(0.0)
    return merged


# --------------------------------------------------------------------------- 판정


def bottom_excess(df: pd.DataFrame, col: str) -> pd.Series:
    def one(g: pd.DataFrame) -> float:
        n = max(1, int(len(g) * BOTTOM_SHARE))
        return float(g.nsmallest(n, col)["target"].mean())
    return df.groupby("session").apply(one)


def judge(group: str, kr: pd.DataFrame, us: pd.DataFrame, control: list[str], treat: list[str]) -> tuple[list[str], str]:
    sessions = sorted(kr["session"].unique()); blocks = blocks_for(sessions)
    print(f"국장 {len(kr):,}행 · 미장 {len(us):,}행 · 판정 블록 {len(blocks)} · 대조 {len(control)}피처 · 처리 {len(treat)}피처", flush=True)
    preds = {"KR": [], "US": []}; gains = []
    for i, (train_end, ps, pe) in enumerate(blocks, 1):
        train = pd.concat([kr[kr["session"] <= train_end], us[us["session"] <= train_end]], ignore_index=True)
        y = train["target"].to_numpy(np.float32)
        m_c = fit_gbm(train[control].to_numpy(np.float32), y)
        m_t = fit_gbm(train[treat].to_numpy(np.float32), y)
        gains.append(dict(zip(treat, m_t.feature_importance(importance_type="gain"))))
        for market, src in (("KR", kr), ("US", us)):
            te = src[(src["session"] >= ps) & (src["session"] <= pe)].copy()
            te["control"] = m_c.predict(te[control].to_numpy(np.float32))
            te["treat"] = m_t.predict(te[treat].to_numpy(np.float32))
            preds[market].append(te[["entity_id", "session", "target", "control", "treat"]])
        print(f"블록 {i}/{len(blocks)} 학습 ~{train_end} ({len(train):,}행) → 판정 {ps}~{pe}", flush=True)

    primary = PRIMARY[group]; other = "US" if primary == "KR" else "KR"
    d = pd.concat(preds[primary], ignore_index=True); do = pd.concat(preds[other], ignore_index=True)
    ic_t, ic_c = daily_ic(d, "treat"), daily_ic(d, "control"); c = ic_t.index.intersection(ic_c.index)
    delta = ic_t.loc[c] - ic_c.loc[c]
    bd = []
    for (_e, ps, pe) in blocks:
        days = [x for x in delta.index if ps <= x <= pe]; bd.append(float(delta.loc[days].mean()) if days else np.nan)
    tp, tc = float(top_excess(d, "treat").mean()), float(top_excess(d, "control").mean())
    bp, bc = float(bottom_excess(d, "treat").mean()), float(bottom_excess(d, "control").mean())
    # 시행 S(폭 80)가 채택되면 실전 폭은 80 — 기준은 아니고 기록만.
    w80 = {k: float(d.sort_values(k, ascending=False).groupby("session").head(80).groupby("session")["target"].mean().mean()) for k in ("treat", "control")}
    ico_t, ico_c = daily_ic(do, "treat"), daily_ic(do, "control"); co = ico_t.index.intersection(ico_c.index)
    delta_o = ico_t.loc[co] - ico_c.loc[co]
    gain = pd.DataFrame(gains).mean().sort_values(ascending=False)
    c1 = _nw(delta, 4) >= T_GATE; c2 = tp >= tc; c3 = float(np.nanmin(bd)) >= WORST_GATE; c4 = float(delta_o.mean()) >= 0
    verdict = "채택" if (c1 and c2 and c3 and c4) else "기각"
    lines = [
        f"{group} 주 시장 {primary} 판정 {len(c)}세션 · 처리 IC {ic_t.mean():+.4f} (t {_nw(ic_t,4):+.2f}) 대 대조 {ic_c.mean():+.4f} (t {_nw(ic_c,4):+.2f})",
        f"① ΔIC {delta.mean():+.4f} · NW t {_nw(delta,4):+.2f} {'○' if c1 else '×'}",
        f"② 상위{TOP_N} h5 z-수익 처리 {tp:+.4f} 대 대조 {tc:+.4f} {'○' if c2 else '×'}",
        f"③ 블록별 ΔIC {' '.join(f'{x:+.3f}' for x in bd)} · 최악 {np.nanmin(bd):+.3f} {'○' if c3 else '×'}",
        f"④ {other} 판정 {len(co)}세션 · ΔIC {delta_o.mean():+.4f} (NW t {_nw(delta_o,4):+.2f}) {'○' if c4 else '×'}",
        f"기록(기준 아님) 하위 {BOTTOM_SHARE:.0%} z-수익 처리 {bp:+.4f} 대 대조 {bc:+.4f} · 상위80 처리 {w80['treat']:+.4f} 대 대조 {w80['control']:+.4f}",
        "피처 gain: " + " · ".join(f"{k} {v/gain.sum():.0%}" for k, v in gain.items()),
        f"판정: {verdict}",
    ]
    return lines, verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--group", required=True, choices=sorted(GROUPS))
    parser.add_argument("--adopted", default="", help="이미 채택된 묶음(쉼표) — 대조에 누적")
    parser.add_argument("--smoke", type=int, default=0, help="세션 N개만 피처를 굽고 병합 확인, 판정 없음")
    parser.add_argument("--save", action="store_true", help="research_trials 에 기록(시행 소진)")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)  # invariant-allow: wallclock — 사전등록 시점 잠금·시행 기록 시각
    if not args.smoke and now.date() < MEASURE_FROM:
        print(f"판정은 {MEASURE_FROM} 이후에만 돈다(사전등록 '중간 들여다보기 금지'). 지금은 --smoke 만.", flush=True)
        return 2
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    adopted = [g for g in args.adopted.split(",") if g]
    print(f"=== 시행 {args.group} — {PROTOCOL} (해시 {digest}) · 채택 누적 {adopted or '없음'} ===", flush=True)
    store = Store(root=Path(args.root))
    kr, _ = load_kr(); us = load_us()
    kr = rank_gauss(kr, FEATS + ["target"]); us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"] = 0.0; us["is_us"] = 1.0
    base = FEATS + ["is_us"]
    limit = args.smoke or None
    for g in [*adopted, args.group]:
        cols = list(GROUPS[g])
        for market, frame in (("KR", kr), ("US", us)):
            sessions = sorted(frame["session"].unique())
            feats = build_panel(store, g, market, sessions, limit=limit)
            merged = attach(frame, feats, cols)
            if market == "KR":
                kr = merged
            else:
                us = merged
            hit = merged.merge(feats[["entity_id", "session"]].drop_duplicates(), on=["entity_id", "session"], how="inner")
            print(f"{g} {market}: 피처 {len(feats):,}행 → 패널 {len(merged):,}행 중 붙은 행 {len(hit):,}", flush=True)
    if args.smoke:
        print("smoke 끝 — 판정은 하지 않았다.", flush=True)
        return 0
    control = base + [c for g in adopted for c in GROUPS[g]]
    treat = control + list(GROUPS[args.group])
    lines, verdict = judge(args.group, kr, us, control, treat)
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        store.append("research_trials", [{
            "entity_id": f"{TRIAL_PREFIX}:{args.group}", "valid_from": now, "observed_at": now,
            "source": "trial_ranker_sources", "market": PRIMARY[args.group], "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": " | ".join(lines)[:900],
        }], ingest_run_id=f"trial-ranker-{args.group}-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/{args.group} · protocol {digest} · {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
