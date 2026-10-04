"""확장 FA 패널(`final_round_kit.FA2021`)을 **시장별 · 기간 조각별로** 굽는다 — 메모리 4GB 안쪽에서.

`kit.load_full_panel(**FA2021)` 은 두 시장을 한 프로세스에서 굽는다. 기존 창(2022-07~)에서도 최대 RSS 가 5.4GB 였고,
확장 창은 행이 늘고 미장 원피처가 2024-06 전에도 차서 더 크다. 그래서 이 도구가 같은 부품
(`blocks_of` → `_build_market` → `finalize`)을 **기간 조각마다 따로** 부르고, 조각을 이어 `load_full_panel` 이 찾는
캐시 파일(`panel-{시장}-KR+US-20211110-20260630` 조각)을 만든다. 그 뒤엔 `load_full_panel(**FA2021)` 이 그 파일을 읽기만 한다.

조각으로 나눠도 값이 같은 이유: rank-gauss 는 (시장, 세션)마다이고, 묶음 붙이기는 (종목, 세션) 키의 왼쪽 병합이다.
미장 점수 패널(`us_panel`)도 세션마다 상위 1,000 을 고르고 세션마다 정규화한다(거래대금 20일 평균은 창 앞 100일을 더 읽는다).
예외 하나는 밸류업(ba)이다. 값(개장 전 관측 건수)은 창과 무관하지만 `miss_ba` 는 "창 안에 그 종목 공시가 하나라도 있었나" 라서
창에 따라 달라진다(회차 패널의 `miss_ba` 누수와 같은 규칙). 그래서 ba 는 **전 창으로 한 번** 계산해 조각마다 잘라 쓴다 —
`load_full_panel(**FA2021)` 을 한 번에 돌린 것과 같은 규칙이다.
`--verify` 는 기존 회차 패널과 겹치는 세션에서 국장 열을 비교해, 같은 입력이면 같은 값이 나오는지 확인한다(y5 는 비교하지 않는다).

    .venv/bin/python tools/fa2021_build_panel.py --market KR          # 조각 굽기 + 잇기 (이어 돌 수 있다)
    .venv/bin/python tools/fa2021_build_panel.py --market US
    .venv/bin/python tools/fa2021_build_panel.py --verify             # 회차 패널과 겹치는 세션 비교
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools import final_round_kit as kit  # noqa: E402

MARKETS = ("KR", "US")
#: 기간 조각 — 반년 남짓. 조각 하나가 기존 창 전체의 1/8 쯤이다.
CHUNKS = (
    (date(2021, 11, 10), date(2022, 6, 30)), (date(2022, 7, 1), date(2022, 12, 31)),
    (date(2023, 1, 1), date(2023, 6, 30)), (date(2023, 7, 1), date(2023, 12, 31)),
    (date(2024, 1, 1), date(2024, 6, 30)), (date(2024, 7, 1), date(2024, 12, 31)),
    (date(2025, 1, 1), date(2025, 6, 30)), (date(2025, 7, 1), date(2025, 12, 31)),
    (date(2026, 1, 1), date(2026, 6, 30)),
)


def tag() -> str:
    lo, hi = kit.FA2021["window"]
    return f"{'+'.join(MARKETS)}-{lo:%Y%m%d}-{hi:%Y%m%d}"


def final_path(market: str) -> Path:
    return Path(kit.FA2021["cache_dir"]) / f"panel-{market}-{tag()}.parquet"  # invariant-allow: data-access — 창고가 아닌 작업 파일


def part_path(market: str, lo: date, hi: date) -> Path:
    name = f"panel-{market}-{tag()}-{lo:%Y%m%d}-{hi:%Y%m%d}.parquet"  # invariant-allow: data-access — 창고가 아닌 작업 파일
    return Path(kit.FA2021["cache_dir"]) / "parts" / name


def whole_window_ba(store: Store) -> pd.DataFrame:
    """ba 를 전 창으로 한 번 — `load_full_panel` 이 한 번에 구울 때와 같은 창(첫 세션~끝 세션 23:00 UTC)·같은 규칙.

    세션·종목은 국장 점수 조각의 창 안 전부다. 값은 세션마다 독립이라 상위집합을 줘도 붙는 행의 값은 같다.
    """
    lo, hi = kit.FA2021["window"]
    s = pd.read_pickle(kit.LONG_CACHE / "scores-risk-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    s["session"] = pd.to_datetime(s["session"]).dt.date
    s = s[(s["session"] >= lo) & (s["session"] <= hi)]
    sessions, entities = sorted(s["session"].unique()), set(s["entity_id"].unique())
    del s
    frame = kit._ba_block(store, sessions, entities)
    frame["session"] = kit._as_dates(frame["session"])
    kit._log(f"ba 전 창 {sessions[0]}~{sessions[-1]} · {len(frame):,}행")
    return frame


def build(market: str) -> int:
    fa = kit.FA2021
    assert kit.check_window(fa["window"]) == (CHUNKS[0][0], CHUNKS[-1][1])
    out = final_path(market)
    if out.exists():
        print(f"{market}: 이미 있다 {out}", flush=True)
        return 0
    # 원피처 열 이름은 **두 시장 캐시의 헤더 합집합**이다. 한쪽 잇기가 아직이면 그 시장 전용 열이 빠진 조각이 생긴다.
    absent = [m for m in MARKETS if kit.raw_span(m, fa["raw_dirs"]) is None]
    if absent:
        print(f"{market}: 이어 붙인 원피처가 아직 없다({absent}) — tools/fa2021_merge.py 가 먼저다", flush=True)
        return 2
    store = Store(root=Path("data"))
    # 묶음 표는 **두 시장 묶음**으로 만든다 — load_full_panel(("KR","US"), **FA2021) 과 같은 원피처 열 합집합.
    groups = kit.blocks_of(MARKETS, raw_dirs=fa["raw_dirs"])
    ba: pd.DataFrame | None = None
    for lo, hi in CHUNKS:
        path = part_path(market, lo, hi)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if market == "KR" and ba is None:
            ba = whole_window_ba(store)
            whole = ba
            kit._ba_block = lambda _store, days, _ents: whole[whole["session"].isin(set(days))]  # type: ignore[assignment]
        one = kit._build_market(store, market, (lo, hi), groups,
                                raw_dirs=fa["raw_dirs"], sources_dirs=fa["sources_dirs"])
        one, _feats = kit.finalize(one, groups)
        one.to_parquet(path, index=False)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        kit._log(f"{market} 조각 {lo}~{hi}: {len(one):,}행 × {one.shape[1]}열 → {path.name}")
        del one
        kit.release_memory()
    parts = [kit._read_part(part_path(market, lo, hi)) for lo, hi in CHUNKS]
    cols = list(parts[0].columns)
    for p in parts[1:]:
        if list(p.columns) != cols:
            raise SystemExit(f"{market}: 조각의 열이 다르다 — {sorted(set(p.columns) ^ set(cols))[:6]}")
    panel = pd.concat(parts, ignore_index=True)
    del parts
    panel["session"] = kit._as_dates(panel["session"])
    dup = int(panel.duplicated(["entity_id", "session"]).sum())
    if dup:
        raise SystemExit(f"{market}: 조각 경계에서 겹친 키 {dup}개")
    panel.to_parquet(out, index=False)  # invariant-allow: data-access — 창고가 아닌 작업 파일
    kit._log(f"{market} 패널 캐시 {out} · {len(panel):,}행 × {panel.shape[1]}열")
    return 0


def verify() -> int:
    """회차 패널(2022-07-01~)과 겹치는 국장 세션 — 같은 입력을 쓰는 열은 같은 값이어야 한다.

    열을 열 개씩 나눠 읽는다(두 패널을 통째로 올리면 4GB 를 넘는다). 키는 한 번만 맞춘다.
    """
    import pyarrow.parquet as pq  # invariant-allow: data-access — 창고가 아닌 작업 캐시(패널 조각 스키마)

    old = kit.CACHE / f"panel-KR-KR+US-{kit.JUDGE_START:%Y%m%d}-{kit.JUDGE_END:%Y%m%d}.parquet"  # invariant-allow: data-access — 작업 캐시
    new = final_path("KR")
    keys = ["entity_id", "session"]
    names_new = set(pq.read_schema(new).names)
    cols = [c for c in pq.read_schema(old).names if c not in ("y5", "market", *keys) and c in names_new]

    def _keys(path: Path) -> pd.DataFrame:
        k = pd.read_parquet(path, columns=keys)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
        k["session"] = kit._as_dates(k["session"])
        return k.reset_index(names="row")

    ka, kb = _keys(old), _keys(new)
    kb = kb[kb["session"] >= ka["session"].min()]
    m = ka.merge(kb, on=keys, how="outer", suffixes=("_old", "_new"), indicator=True)
    print(f"키: 둘 다 {int((m['_merge'] == 'both').sum()):,} · 옛것만 {int((m['_merge'] == 'left_only').sum()):,} · "
          f"새것만 {int((m['_merge'] == 'right_only').sum()):,}", flush=True)
    both = m[m["_merge"] == "both"]
    ia, ib = both["row_old"].to_numpy(np.int64), both["row_new"].to_numpy(np.int64)
    del ka, kb, m, both
    bad = 0
    for i in range(0, len(cols), 10):
        batch = cols[i:i + 10]
        a = pd.read_parquet(old, columns=batch)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
        b = pd.read_parquet(new, columns=batch)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
        for c in batch:
            x, y = a[c].to_numpy(np.float64)[ia], b[c].to_numpy(np.float64)[ib]
            diff = ~((x == y) | (np.isnan(x) & np.isnan(y)))
            if diff.any():
                bad += 1
                print(f"  다름 {c}: {int(diff.sum()):,}행 ({diff.mean():.2%}) · 최대 |차| {np.nanmax(np.abs(x - y)):.4g}", flush=True)
        del a, b
    print(f"열 {len(cols)}개 중 다른 열 {bad}개", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", nargs="+", choices=MARKETS, default=[])
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    rc = sum(build(m) for m in args.market)
    if args.verify:
        rc += verify()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
