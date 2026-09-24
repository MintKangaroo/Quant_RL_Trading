"""시행 AQ·AR — 홀드아웃 11월 개봉 때 심사할 모델을 **지금 얼린다**.

    .venv/bin/python tools/trial_insider_forward.py --freeze

- AR(docs/protocols/insider-forward-2026-09.md): 6차 패널(국장+미장 한 모델, is_us)로 {대조, 대조+G4+G7} × 시드 0·1·2.
- AQ(docs/protocols/breadth72-forward-2026-09.md): 확장 패널 국장 루프 GBM(시행 L 규격) × 시드 0·1·2 — 원천 ②③④.
- AS(docs/protocols/raw-feature-vault-2026-09.md): 시행 W 의 처리(원피처 35 + is_us) × 시드 0·1·2. 대조는 AR 대조 모델과 같은 규격이라 다시 굽지 않는다.

학습 자료는 금고 전(2026-06-30)까지, 라벨이 금고 가격에 닿는 마지막 5세션은 퍼지한다. 모델 문자열의 sha256 을 두 등록 문서
"모델 해시" 절에 적는다 — 판정 때 다시 학습하지 않고 이 파일을 읽는다. 판정부(--judge)는 금고 개봉(2026-11-23) 전에
따로 커밋한다: 금고 창(7/1~11/13)의 Analyst 점수 패널을 개봉 때 굽는 일이 먼저다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_pooled import FEATS, load_kr, load_us  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_kit import FEATS as LOOP_FEATS  # noqa: E402
from tools.trial_ranker_kit import PURGE, fit, judge_panel  # noqa: E402
from tools.trial_ranker_sources import GROUPS, attach, build_panel  # noqa: E402

OUT = Path("data/_diag/vault-reviews")
PROTOCOLS = {"AR": Path("docs/protocols/insider-forward-2026-09.md"), "AQ": Path("docs/protocols/breadth72-forward-2026-09.md"),
             "AS": Path("docs/protocols/raw-feature-vault-2026-09.md")}
SEEDS = (0, 1, 2)
INSIDER = ("G4", "G7")


def _save(model, name: str) -> str:
    text = model.model_to_string()
    (OUT / f"{name}.txt").write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _record(trial: str, rows: list[str]) -> None:
    path = PROTOCOLS[trial]
    body = path.read_text()
    marker = "## 모델 해시 (--freeze 뒤 적는다)\n\n(비어 있음)\n"
    if marker not in body:
        raise SystemExit(f"{path} 의 해시 절이 이미 채워졌다 — 다시 얼리지 않는다(등록 뒤 모델 교체 금지).")
    path.write_text(body.replace(marker, "## 모델 해시 (--freeze 뒤 적는다)\n\n" + "\n".join(rows) + "\n"))


def freeze_ar(store: Store) -> list[str]:
    kr, _ = load_kr()
    us = load_us()
    kr = rank_gauss(kr, FEATS + ["target"])
    us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"], us["is_us"] = 0.0, 1.0
    for g in INSIDER:
        cols = list(GROUPS[g])
        kr = attach(kr, build_panel(store, g, "KR", sorted(kr["session"].unique())), cols)
        us = attach(us, build_panel(store, g, "US", sorted(us["session"].unique())), cols)
    control = FEATS + ["is_us"]
    treat = control + [c for g in INSIDER for c in GROUPS[g]]
    sessions = sorted(set(kr["session"]) | set(us["session"]))
    train_end = sessions[-PURGE - 1]
    train = [f[(f["session"] <= train_end) & f["target"].notna()] for f in (kr, us)]
    import pandas as pd

    data = pd.concat(train, ignore_index=True)
    y = data["target"].to_numpy(np.float32)
    rows = [f"학습 ~{train_end} · {len(data):,}행 · 대조 {control} · 처리 +{[c for g in INSIDER for c in GROUPS[g]]}"]
    for arm, cols in (("control", control), ("treat", treat)):
        for s in SEEDS:
            digest = _save(fit(data[cols].to_numpy(np.float32), y, seed=s), f"AR-{arm}-s{s}")
            rows.append(f"- `AR-{arm}-s{s}` {digest}")
            print(rows[-1], flush=True)
    return rows


def freeze_as() -> list[str]:
    from tools.trial_raw_feature_ranker import raw_features

    import pandas as pd

    kr, _ = load_kr()
    us = load_us()
    kr = rank_gauss(kr, FEATS + ["target"])
    us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"], us["is_us"] = 0.0, 1.0
    raw_cols: list[str] = []
    frames = []
    for market, frame in (("KR", kr), ("US", us)):
        feats, cols = raw_features(market, keys=frame[["entity_id", "session"]].drop_duplicates())
        raw_cols += [c for c in cols if c not in raw_cols]
        frames.append(attach(frame, feats, cols))
        del feats
    for frame in frames:  # 그 시장에 없는 원피처는 0(순위 중앙) — W 등록 규칙
        for c in raw_cols:
            if c not in frame.columns:
                frame[c] = 0.0
    treat = raw_cols + ["is_us"]
    sessions = sorted(set(frames[0]["session"]) | set(frames[1]["session"]))
    train_end = sessions[-PURGE - 1]
    data = pd.concat([f[(f["session"] <= train_end) & f["target"].notna()][[*treat, "target"]] for f in frames], ignore_index=True)
    del frames
    y = data["target"].to_numpy(np.float32)
    X = data[treat].to_numpy(np.float32)
    rows = [f"학습 ~{train_end} · {len(data):,}행 · 처리 원피처 {len(raw_cols)}개 + is_us · 대조 = AR-control-s0~2(같은 규격)"]
    for s in SEEDS:
        digest = _save(fit(X, y, seed=s), f"AS-treat-s{s}")
        rows.append(f"- `AS-treat-s{s}` {digest}")
        print(rows[-1], flush=True)
    return rows


def freeze_aq() -> list[str]:
    panel, sessions = judge_panel()
    train_end = sessions[-PURGE - 1]
    train = panel[(panel["session"] <= train_end) & panel["y5"].notna()]
    X, y = train[LOOP_FEATS].to_numpy(np.float32), train["y5"].to_numpy(np.float32)
    rows = [f"학습 ~{train_end} · {len(train):,}행 · 피처 {list(LOOP_FEATS)} · 타깃 y5 rank-gauss"]
    for s in SEEDS:
        digest = _save(fit(X, y, seed=s), f"AQ-loop-s{s}")
        rows.append(f"- `AQ-loop-s{s}` {digest}")
        print(rows[-1], flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--trials", default="AQ,AR", help="얼릴 시행(쉼표). 이미 해시가 적힌 시행은 거부한다")
    args = parser.parse_args(argv)
    if not args.freeze:
        parser.error("--freeze 만 있다 (판정부는 금고 개봉 전에 따로 커밋)")
    OUT.mkdir(parents=True, exist_ok=True)
    store = Store(root=Path("data"))
    trials = [t for t in args.trials.split(",") if t]
    if "AQ" in trials:
        _record("AQ", freeze_aq())
    if "AR" in trials:
        _record("AR", freeze_ar(store))
    if "AS" in trials:
        _record("AS", freeze_as())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
