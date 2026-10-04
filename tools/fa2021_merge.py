"""2022 하락장 확장 원피처 — 앞 조각(fa2021/raw-*-seg*) + 기존 캐시를 **새 디렉터리**에 이어 붙인다.

기존 캐시(data/_diag/kr-long · data/_diag/w-us)는 읽기만 한다 — 등록된 시행이 그 파일을 읽는다.
결과는 `final_round_kit.FA2021["raw_dirs"]`(data/_diag/fa2021/raw-KR · raw-US)이고, 같은 자리에 `manifest-{시장}.json`
(입력 파일 크기·수정 시각·행 수)을 남겨 어떤 조각으로 만들었는지 되짚을 수 있게 한다.

한 시장의 조각이 하나라도 덜 구워졌으면 그 시장은 **만들지 않는다** — 반쯤 이은 캐시는 구멍을 0(순위 중앙)으로 숨긴다.
"덜 구워짐" 과 "원천이 그 기간에 없어 비었다" 는 파일만으로 구별되지 않는다(`diagnose_ic` 는 빈 피처면 파일을 안 만든다).
그래서 후자는 사람이 로그("피처가 비었다")를 확인하고 `features-{a}-{시장}.empty`(사유 한 줄)를 남겨야 빈 조각으로 친다.

    .venv/bin/python tools/fa2021_merge.py [--market KR US]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from tools.final_round_kit import FA2021, FA2021_DIR, RAW_DIRS  # noqa: E402
from tools.trial_raw_feature_ranker import ANALYSTS  # noqa: E402

#: 앞 조각 디렉터리 — `scripts/fa2021_bake.sh` 와 같은 이름. 순서는 시간순이고 기존 캐시가 맨 뒤다.
SEGMENTS = {
    "KR": [FA2021_DIR / "raw-KR-seg"],
    "US": [FA2021_DIR / f"raw-US-seg-{t}" for t in ("2021H2", "2022H1", "2022H2", "2023H1", "2023H2", "2024H1")],
}


def _sessions(path: Path) -> pd.Series:
    return pd.to_datetime(pd.read_pickle(path)["session"]).dt.date  # invariant-allow: data-access — 진단 캐시(창고 아님)


def _empty_marker(seg: Path, analyst: str, market: str) -> Path:
    return seg / f"features-{analyst}-{market}.empty"


def missing_parts(market: str) -> list[str]:
    """덜 구워진 조각 — 달력은 있는데 Analyst 피처 파일이 없는 자리. 달력이 비었으면(세션 0) 빈 조각으로 친다."""
    out = []
    for seg in SEGMENTS[market]:
        if not (seg / f"calendar-{market}.pkl").exists():
            out.append(str(seg / f"calendar-{market}.pkl"))
            continue
        out += [str(seg / f"features-{a}-{market}.pkl") for a in ANALYSTS[market]
                if not (seg / f"features-{a}-{market}.pkl").exists() and not _empty_marker(seg, a, market).exists()]
    return out


def merge_market(market: str) -> int:
    out = Path(FA2021["raw_dirs"][market])
    todo = missing_parts(market)
    if todo:
        print(f"{market}: 덜 구워진 조각 {len(todo)}개 — 이번엔 만들지 않는다: {todo[:4]}", flush=True)
        return 1
    sources = [*SEGMENTS[market], RAW_DIRS[market]]
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"market": market, "sources": [str(s) for s in sources], "files": {}}
    days = sorted({d for s in sources for d in _sessions(s / f"calendar-{market}.pkl")})
    for a in ANALYSTS[market]:
        name = f"features-{a}-{market}.pkl"
        frames = []
        for s in sources:
            path = s / name
            if not path.exists() and _empty_marker(s, a, market).exists():
                manifest["files"][str(path)] = {"empty": _empty_marker(s, a, market).read_text().strip()}  # type: ignore[index]
                continue
            stat = path.stat()
            f = pd.read_pickle(path)  # invariant-allow: data-access — 진단 캐시(창고 아님)
            manifest["files"][str(path)] = {"bytes": stat.st_size, "mtime": stat.st_mtime, "rows": len(f)}  # type: ignore[index]
            frames.append(f)
        cols = list(dict.fromkeys(c for f in frames for c in f.columns))
        merged = pd.concat(frames, ignore_index=True)[cols]
        del frames
        merged["session"] = pd.to_datetime(merged["session"]).dt.date
        # 기존 캐시가 뒤에 있다 — 겹치는 (종목, 세션)이 생기면 기존 값을 남긴다(겹침은 0 이어야 정상).
        dup = int(merged.duplicated(["entity_id", "session"], keep="last").sum())
        merged = merged.drop_duplicates(["entity_id", "session"], keep="last").sort_values(["session", "entity_id"])
        merged.reset_index(drop=True).to_pickle(out / name)  # invariant-allow: data-access — 진단 캐시(창고 아님)
        print(f"{market} {a}: {len(merged):,}행 · 세션 {merged['session'].min()}~{merged['session'].max()} · 겹침 {dup}", flush=True)
        manifest.setdefault("dups", {})[a] = dup  # type: ignore[union-attr]
        del merged
    pd.DataFrame({"session": days}).to_pickle(out / f"calendar-{market}.pkl")  # invariant-allow: data-access — 진단 캐시(창고 아님)
    manifest["calendar"] = {"sessions": len(days), "first": str(days[0]), "last": str(days[-1])}
    (out / f"manifest-{market}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))  # invariant-allow: data-access — 작업 기록
    print(f"{market}: 달력 {len(days)}세션 {days[0]}~{days[-1]} → {out}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", nargs="+", default=["KR", "US"], choices=["KR", "US"])
    args = parser.parse_args(argv)
    return sum(merge_market(m) for m in args.market)


if __name__ == "__main__":
    raise SystemExit(main())
