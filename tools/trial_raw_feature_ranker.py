"""시행 W — 원피처 랭커: 점수 대신 원피처를 주면 랭커가 더 배우나. docs/protocols/raw-feature-ranker-2026-09.md.

    .venv/bin/python tools/trial_raw_feature_ranker.py [--save]
    .venv/bin/python tools/trial_raw_feature_ranker.py --smoke        # 패널·원피처 병합만 확인, 판정 없음

대조 = 점수 여섯 + is_us (+ 6차 채택 묶음) · 처리 = 같은 여섯 Analyst 의 원피처 + is_us (+ 같은 채택 묶음).
모델·판정 블록·기준 형식은 6차(`tools/trial_ranker_sources.judge`)를 그대로 쓰고, 기준값만 W 등록값(② −0.01 · ④ −0.005)이다.
대조·처리 모두 시드 0·1·2 평균(6차와 같은 측정 전 정정). 판정은 **2026-10-11 이후**이고 **6차 G1~G7 이 다 기록된 뒤**에만 돈다.

원피처 캐시: 국장 `data/_diag/features-*-KR.pkl`(2025-05-21~, 점수 패널과 같은 시작), 미장 `data/_diag/w-us/features-*-US.pkl`
(점수 패널과 같은 2024-06-11~ — `scripts/bake_w_us_wide.sh`). 미장 옛 캐시(2025-07~)는 쓰지 않는다(정정 2).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_pooled import FEATS, load_kr, load_us  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_sources import (  # noqa: E402
    GROUPS,
    TRIAL_PREFIX,
    attach,
    build_panel,
    judge,
)

PROTOCOL = Path("docs/protocols/raw-feature-ranker-2026-09.md")
MEASURE_FROM = date(2026, 9, 24)  # 당김(사용자 지시 9/24, 추석 휴장 — 예산 잠금이었지 자료 잠금이 아니다)
ROUND6 = ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
RAW_DIRS = {"KR": Path("data/_diag"), "US": Path("data/_diag/w-us")}
ANALYSTS = {"KR": ("chart", "event", "flow_kr", "fundamental", "regime", "risk"),
            "US": ("chart", "event", "flow_us", "fundamental", "regime", "risk")}
#: 등록값 — 개선 주장(①)은 유의성, 해 방지(②④)는 의미 있는 여유(2026-09-18 교훈).
TOP_MARGIN, OTHER_FLOOR = 0.01, -0.005


def round6_verdicts(store: Store) -> dict[str, str]:
    """창고에 기록된 6차 판정 — 묶음마다 마지막 기록. 없으면 그 묶음은 빠진다."""
    now = datetime.now(UTC)  # invariant-allow: wallclock — 기록 조회 시점
    frame = store.get("research_trials", as_of=now, lookback=400)
    out = {}
    for g in ROUND6:
        rows = frame[frame["entity_id"].astype(str) == f"{TRIAL_PREFIX}:{g}"] if not frame.empty else frame
        if not rows.empty:
            detail = str(rows.sort_values("valid_from").iloc[-1]["detail"])
            out[g] = "채택" if "판정: 채택" in detail else "기각"
    return out


def raw_features(market: str, *, smoke: bool = False, keys: pd.DataFrame | None = None) -> tuple[pd.DataFrame, list[str]]:
    """여섯 Analyst 의 원피처를 한 표로. 열 이름 앞에 Analyst 를 붙여 시장 간 같은 이름이 섞이지 않게 한다.

    smoke 에서만, 넓은 미장 캐시가 아직 없으면 옛 캐시(data/_diag)로 **배선만** 본다. 판정은 넓은 캐시가 없으면 멈춘다."""
    base = RAW_DIRS[market]
    if not (base / f"features-{ANALYSTS[market][0]}-{market}.pkl").exists():
        if not smoke:
            raise SystemExit(f"{base} 에 원피처 캐시가 없다 — scripts/bake_w_us_wide.sh 가 먼저다")
        base = Path("data/_diag")
        print(f"  smoke: {market} 넓은 캐시가 아직 없어 {base} 의 옛 캐시로 배선만 본다", flush=True)
    merged, cols = None, []
    for a in ANALYSTS[market]:
        f = pd.read_pickle(base / f"features-{a}-{market}.pkl")  # invariant-allow: data-access — 진단 캐시(창고 아님)
        f["session"] = pd.to_datetime(f["session"]).dt.date
        feats = [c for c in f.columns if c not in ("entity_id", "session")]
        # **판정 패널에 있는 행만 남기고 float32 로.** 넓은 미장 캐시는 Analyst 마다 1천만 행이라 여섯을 바깥 병합하면
        # RSS 6.7GB 로 커널 OOM 에 죽었다(2026-09-24 21:55). 어차피 attach 가 패널 기준 왼쪽 병합이라 결과는 같다.
        if keys is not None:
            f = f.merge(keys, on=["entity_id", "session"], how="inner")
        f[feats] = f[feats].astype("float32")
        f = f.rename(columns={c: f"raw_{a}_{c}" for c in feats})
        cols += [f"raw_{a}_{c}" for c in feats]
        merged = f if merged is None else merged.merge(f, on=["entity_id", "session"], how="outer")
    return merged, cols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="병합·붙는 비율만 — 판정 없음")
    args = parser.parse_args(argv)
    store = Store(root=Path("data"))
    now = datetime.now(UTC)  # invariant-allow: wallclock — 사전등록 시점 잠금·시행 기록 시각
    verdicts = round6_verdicts(store)
    if not args.smoke:
        if now.date() < MEASURE_FROM:
            print(f"판정은 {MEASURE_FROM} 이후에만 돈다(사전등록). 지금은 --smoke 만.", flush=True)
            return 2
        missing = [g for g in ROUND6 if g not in verdicts]
        if missing:
            print(f"6차가 아직 안 끝났다({','.join(missing)} 미기록) — 대조가 정해지지 않아 기다린다.", flush=True)
            return 2
    adopted = [g for g in ROUND6 if verdicts.get(g) == "채택"]
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 W — {PROTOCOL} (해시 {digest}) · 6차 채택 {adopted or '없음'} ===", flush=True)

    kr, _ = load_kr(); us = load_us()
    kr = rank_gauss(kr, FEATS + ["target"]); us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"] = 0.0; us["is_us"] = 1.0
    for g in adopted:
        cols = list(GROUPS[g])
        kr = attach(kr, build_panel(store, g, "KR", sorted(kr["session"].unique())), cols)
        us = attach(us, build_panel(store, g, "US", sorted(us["session"].unique())), cols)
    raw_cols: list[str] = []
    frames = {}
    for market, frame in (("KR", kr), ("US", us)):
        feats, cols = raw_features(market, smoke=args.smoke, keys=frame[["entity_id", "session"]].drop_duplicates())
        raw_cols += [c for c in cols if c not in raw_cols]
        hit = frame.merge(feats[["entity_id", "session"]], on=["entity_id", "session"], how="inner")
        print(f"{market}: 원피처 {len(cols)}개 · {feats['session'].min()}~{feats['session'].max()} · "
              f"패널 {len(frame):,}행 중 붙은 행 {len(hit):,} ({len(hit) / max(1, len(frame)):.0%})", flush=True)
        frames[market] = attach(frame, feats, cols)
    kr, us = frames["KR"], frames["US"]
    for c in raw_cols:  # 그 시장에 없는 원피처는 전부 0(순위 중앙) — 등록 규칙
        for frame in (kr, us):
            if c not in frame.columns:
                frame[c] = 0.0
    if args.smoke:
        print(f"smoke 끝 — 원피처 합계 {len(raw_cols)}개, 판정은 하지 않았다.", flush=True)
        return 0

    extra = [c for g in adopted for c in GROUPS[g]]
    control = FEATS + ["is_us"] + extra
    treat = raw_cols + ["is_us"] + extra
    lines, verdict = judge("W", kr, us, control, treat, top_margin=TOP_MARGIN, other_floor=OTHER_FLOOR)
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        store.append("research_trials", [{
            "entity_id": "raw-feature-ranker-2026-09:W", "valid_from": now, "observed_at": now,
            "source": "trial_raw_feature_ranker", "market": "KR", "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"판정: {verdict} | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-raw-feature-ranker-W-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/W · protocol {digest} · {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
