#!/usr/bin/env python
"""재심 2(`docs/protocols/reexam-sparse-gauss-2026-10.md`) 재료 파일 — 같은 재료 정의를 공통 사다리 재료 모드에 넣는다.

    .venv/bin/python tools/build_reexam_sparse.py          # RX-G1·RX-G2·RX-G4·RX-G5·RX-BA 점수 파일
    flock -n data/_locks/research-slot-1.lock nice -n 10 .venv/bin/python tools/eval_ladder.py run --mode feature --name RX-G2 --score RX-G2 --n-tests 5

- 6차 묶음(G1·G2·G4·G5)은 6차 작업 캐시 `data/_diag/ranker-sources/G?-KR-*.parquet` 의 **원값 그대로**(그때 `ranker_sources.build` 가 만든 것).
  BA 는 `analysts.valueup.features`(시행 BA·BE2 shadow 와 같은 함수, 건수 결측 → 0 도 같다).
- 사다리가 0 이 30% 넘는 열을 `sparse_gauss` 로 바꾼다 — **바뀌는 것은 변환뿐**이다(재심 규칙).
- 대표 열 `pred` = 묶음의 첫 열(원값, 부호를 뒤집지 않는다 — 판정은 칸 ② ΔIC). 나머지는 `x_*`, 캐시에 행이 없으면 `m_miss = 1`
  (6차 `attach`·마지막 회차 `miss_g*` 표지와 같은 뜻).
- **국장만**(사다리가 국장 전용). 미장 G7·미장 G1/G2/G5 는 이 사다리로 못 잰다 — 결과 문서에 적는다. AR 의 국장 몫은 G4 와 같다.
"""

from __future__ import annotations

import glob
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.ranker_sources import GROUPS  # noqa: E402
from tools import eval_ladder as el  # noqa: E402

CACHE = "data/_diag/ranker-sources"


def _axis() -> pd.DataFrame:
    trad = pd.read_pickle(el.CONTEXT_FILE / "trad.pkl")  # invariant-allow: data-access — 연구 캐시
    trad["session"] = pd.to_datetime(trad["session"]).dt.date
    trad["entity_id"] = trad["entity_id"].astype(str)
    return trad[(trad["session"] >= el.CONTEXT_START) & (trad["session"] <= el.DATE_CAP)][["entity_id", "session"]]


def _write(name: str, frame: pd.DataFrame, cols: list[str]) -> None:
    out = frame.rename(columns={cols[0]: "pred", **{c: f"x_{c}" for c in cols[1:]}})
    out = out.drop_duplicates(["entity_id", "session"]).assign(seed=0)
    el.guard_cap(out["session"].unique(), name)
    out.to_parquet(el.SCORES / f"{name}{el.SCORE_SUFFIX}", index=False)  # invariant-allow: data-access — 재료 캐시
    zero = {c: round(float((frame[c] == 0).sum() / max(frame[c].notna().sum(), 1)), 3) for c in cols}
    print(f"{name}: {len(out):,}행 · 결측 표지 {float(out['m_miss'].mean()):.1%} · 0 비율 {zero}", flush=True)


def group_file(g: str, axis: pd.DataFrame) -> None:
    cols = list(GROUPS[g])
    parts = [pd.read_parquet(f) for f in sorted(glob.glob(f"{CACHE}/{g}-KR-*.parquet"))]  # invariant-allow: data-access — 6차 작업 캐시
    raw = pd.concat(parts, ignore_index=True)
    raw["session"] = pd.to_datetime(raw["session"]).dt.date
    raw["entity_id"] = raw["entity_id"].astype(str)
    raw = raw.drop_duplicates(["entity_id", "session"], keep="last")
    f = axis.merge(raw[["entity_id", "session", *cols]].assign(_hit=1.0), on=["entity_id", "session"], how="left")
    f["m_miss"] = 1.0 - f.pop("_hit").fillna(0.0)
    _write(f"RX-{g}", f, cols)


def ba_file(axis: pd.DataFrame) -> None:
    from quant_rl_trading.analysts.valueup import KINDS, NEW, features
    from quant_rl_trading.store import Store

    sessions: list[date] = sorted(axis["session"].unique())
    feats = features(Store(root=Path("data")), sessions, set(axis["entity_id"]), as_of=el.CAP_AS_OF)
    feats["session"] = pd.to_datetime(feats["session"]).dt.date
    f = axis.merge(feats, on=["entity_id", "session"], how="left")
    f[list(KINDS)] = f[list(KINDS)].fillna(0.0)                     # 시행 BA 규칙 그대로(건수 결측 = 사건 없음)
    f["m_miss"] = 0.0
    _write("RX-BA", f, list(NEW))


def main() -> int:
    axis = _axis()
    for g in ("G1", "G2", "G4", "G5"):
        group_file(g, axis)
    ba_file(axis)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
