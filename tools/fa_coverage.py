"""FA 패널 조각의 묶음별 채움률 — 시장 × 월. **라벨·수익을 읽지 않는다**(y5 열은 아예 안 올린다).

채움 = 표지 있는 묶음은 `표지 = 0` 비율, score 는 열마다 `값 ≠ 0` 비율의 평균(BE3 등록 문서의 실측 규칙과 같다).
rank-gauss 뒤라 score 의 0 은 "순위 중앙" 이거나 "자료 없음" 이다 — 표지가 없어 이 근사를 쓴다.

    .venv/bin/python tools/fa_coverage.py <패널 조각 경로…> --start 2021-11-10 --end 2022-06-30
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402  # invariant-allow: data-access — 창고가 아닌 작업 캐시(패널 조각 스키마)

from tools.trial_ranker_ensemble import FEATS as SCORE_FEATS  # noqa: E402

FLAGS = ("miss_raw", "miss_g1", "miss_g2", "miss_g3", "miss_g4", "miss_g5", "miss_g6", "miss_g7", "miss_ba")


def coverage(paths: list[Path], start: date | None = None, end: date | None = None, *,
             by: str = "month") -> pd.DataFrame:
    """행 = (시장, 기간), 열 = 묶음, 값 = 채움률(0~1). `by` 는 "month" 또는 "all"."""
    out = []
    for path in paths:
        names = set(pq.read_schema(path).names)
        cols = ["session", "market", *[c for c in SCORE_FEATS if c in names], *[f for f in FLAGS if f in names]]
        frame = pd.read_parquet(path, columns=cols)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
        frame["session"] = pd.to_datetime(frame["session"]).dt.date
        if start:
            frame = frame[frame["session"] >= start]
        if end:
            frame = frame[frame["session"] <= end]
        if frame.empty:
            continue
        period = frame["session"].map(lambda d: f"{d:%Y-%m}") if by == "month" else pd.Series("all", index=frame.index)
        keys = [frame["market"], period]
        table = pd.DataFrame({"rows": frame.groupby(keys).size()})
        table["score"] = sum((frame[c] != 0).groupby(keys).mean() for c in SCORE_FEATS if c in frame) / len(SCORE_FEATS)
        for f in FLAGS:
            if f in frame:
                table[f.removeprefix("miss_")] = (frame[f] == 0).groupby(keys).mean()
        out.append(table)
        del frame
    return pd.concat(out).sort_index() if out else pd.DataFrame()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--by", choices=("month", "all"), default="month")
    args = parser.parse_args(argv)
    table = coverage(args.paths, args.start, args.end, by=args.by)
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        print((table.drop(columns="rows") * 100).round(1).join(table["rows"]).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
