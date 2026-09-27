"""마지막 모델 회차의 **대조군 둘** — C0(현행 GBM · 점수 6) · C1(같은 GBM · FA 전 피처).
등록 문서 `docs/protocols/final-model-round-2026-10.md` §대조.

    .venv/bin/python tools/final_round_controls.py --precheck            # 커버리지·성향 상관·겹침만 (수익·IC 없음)
    .venv/bin/python tools/final_round_controls.py --bake --smoke 2      # 배선 확인 (2블록, 시드 1개)
    .venv/bin/python tools/final_round_controls.py --bake                # 본 굽기 — scripts/final_round_bake.sh 로만

**판정 수치를 찍지 않는다.** 이 도구는 예측을 `data/_diag/final-round/` 에 캐시하는 것까지만 한다. 수익·IC·판정은
10/5 이후 시행 BE·BF·BG 도구가 같은 캐시를 읽어 계산한다. 사전등록의 "중간 들여다보기 금지" 를 코드로 지키는 자리다
(시행 AT 의 교훈: 등록 전 점검은 **수익을 보지 않고도** 실패를 잡는다 — 척도·겹침·커버리지로).

`--precheck` 가 찍는 것 넷, 전부 수익과 무관하다:
① 묶음별 결측률(전체·연도별·시장별) ② 묶음 피처 대 성향 대리(시총·β·가치·배당) 상관 ③ C0 대비 C1 상위 24 겹침
④ 최대 RSS. BA 의 교훈("개수 피처는 사건이 아니라 회사 성격을 말한다")을 FA 의 묶음마다 미리 본다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools import final_round_kit as kit  # noqa: E402
from tools.trial_lambdarank import top_overlap  # noqa: E402

#: 성향 대리 — 시총은 창고에서, 나머지는 원피처 묶음에서 그대로 쓴다(따로 계산하면 정의가 갈린다).
PROXY_COLS = {"beta": "raw_regime_beta", "value": "raw_fundamental_book_to_market",
              "dividend": "raw_event_dividend"}
#: 상관은 세션을 건너뛰며 잰다 — 전 세션을 melt 하면 5.5백만 행 × 69열이 메모리를 먹고, 성향 상관은 그렇게 정밀할 필요가 없다.
CORR_STRIDE = 20
PRECHECK_BLOCKS = 5
ARMS = {"C0": "score", "C1": "FA"}


# --------------------------------------------------------------------------- 등록 전 점검


def coverage(panel: pd.DataFrame, groups: dict[str, list[str]]) -> list[str]:
    """묶음별 결측률 — 표지(miss_*)가 1 인 행의 비율. 전체·시장별·연도별."""
    year = pd.to_datetime(pd.Series(panel["session"])).dt.year.to_numpy()
    years = sorted(set(year))
    lines = ["| 묶음 | 피처 | 전체 결측 | KR | US | " + " | ".join(str(y) for y in years) + " |",
             "|---" * (5 + len(years)) + "|"]
    for name, cols in groups.items():
        flag = next((c for c in cols if c.startswith("miss_")), None)
        if flag is None:
            lines.append(f"| {name} | {len(cols)} | — 패널을 정의하는 묶음 | — | — | "
                         + " | ".join("—" for _ in years) + " |")
            continue
        miss = panel[flag].to_numpy(dtype=float)
        per_market = []
        for m in ("KR", "US"):
            sel = (panel["market"] == m).to_numpy()
            per_market.append(f"{miss[sel].mean():.0%}" if sel.any() else "—")
        per_year = [f"{miss[year == y].mean():.0%}" for y in years]
        lines.append(f"| {name} | {len(cols) - 1} | {miss.mean():.0%} | " + " | ".join(per_market)
                     + " | " + " | ".join(per_year) + " |")
    return lines


def _caps_long(store: Store, panel: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    """(entity_id, session, log 시총) — 국장·미장 각자의 기존 읽기를 그대로 쓴다."""
    from tools.trial_bench_construct import _caps as kr_caps
    from tools.trial_us_index_minus_losers import top_caps as us_caps
    parts = []
    for market in sorted(panel["market"].unique()):
        days = [d for d in sessions if d in set(panel.loc[panel["market"] == market, "session"])]
        if not days:
            continue
        if market == "KR":
            wide = kr_caps(store, days)
        else:
            now = datetime.combine(days[-1], time(23), tzinfo=UTC)
            wide = us_caps(store, now, (days[-1] - days[0]).days + 60)
        wide = wide[wide.index.isin(days)]
        long = wide.stack().rename("cap").reset_index()
        long.columns = ["session", "entity_id", "cap"]
        long["cap"] = np.log(long["cap"].where(long["cap"] > 0)).astype(np.float32)
        parts.append(long)
        del wide, long
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["session", "entity_id", "cap"])


def style_correlation(store: Store, panel: pd.DataFrame, groups: dict[str, list[str]],
                      sessions: list[date]) -> list[str]:
    """묶음 피처가 성향(시총·β·가치·배당) 대리변수인지 — 세션 안 순위상관, 세션 평균.

    BA 의 교훈 그대로다: 배당결정 **건수** 는 새 사건이 아니라 "배당하는 회사" 표지였고, 그 스타일이 급등장에서 뒤졌다.
    원피처가 없는 행(miss_raw = 1)에서는 β·가치·배당 대리가 0 이라 뜻이 없다 — 그 행은 뺀다(표에 남은 행 수를 적는다).
    """
    picked = sessions[::CORR_STRIDE]
    sub = panel[panel["session"].isin(set(picked))]
    if "miss_raw" in sub.columns:
        sub = sub[sub["miss_raw"] < 0.5]
    caps = _caps_long(store, panel, picked)
    sub = sub.merge(caps, on=["entity_id", "session"], how="left")
    del caps
    proxies = {"cap": "cap", **{k: v for k, v in PROXY_COLS.items() if v in sub.columns}}
    lines = [f"세션 {len(picked)}개 표본(매 {CORR_STRIDE}세션) · 원피처 있는 행 {len(sub):,}",
             "| 묶음 | 최대 절대상관 (피처·대리) | 시총 | β | 가치 | 배당 |", "|---|---|---|---|---|---|"]
    for name, cols in groups.items():
        feats = [c for c in cols if not c.startswith("miss_") and c in sub.columns]
        if not feats:
            continue
        best, table = ("", 0.0), {}
        for label, col in proxies.items():
            per_session = []
            for _day, g in sub.groupby("session"):
                base = g[col]
                if base.notna().sum() < 30:
                    continue
                per_session.append(g[feats].corrwith(base, method="spearman"))
            if not per_session:
                continue
            mean = pd.concat(per_session, axis=1).mean(axis=1)
            table[label] = mean
            top = mean.abs().idxmax()
            if abs(mean[top]) > abs(best[1]):
                best = (f"{top}({label})", float(mean[top]))
        cells = [f"{table[k].abs().max():+.2f}" if k in table else "—" for k in ("cap", "beta", "value", "dividend")]
        lines.append(f"| {name} | {best[1]:+.2f} {best[0]} | " + " | ".join(cells) + " |")
    return lines


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--precheck", action="store_true", help="커버리지·성향 상관·겹침만 — 수익·IC 없음")
    parser.add_argument("--bake", action="store_true", help="C0·C1 워크포워드 예측을 캐시한다 — 수익·IC 없음")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만, 시드 1개 — 배선 확인")
    parser.add_argument("--markets", default="KR,US")
    parser.add_argument("--window", default="", help="YYYY-MM-DD:YYYY-MM-DD — **배선 확인용 짧은 창**. 본 판정은 기본값(등록 창)이다")
    parser.add_argument("--rebuild", action="store_true", help="FA 패널 조각을 다시 굽는다")
    args = parser.parse_args(argv)
    if not (args.precheck or args.bake):
        parser.error("--precheck 또는 --bake 중 하나는 있어야 한다")
    markets = tuple(m for m in args.markets.split(",") if m)
    store = Store(root=Path(args.root))
    window = (kit.JUDGE_START, kit.JUDGE_END)
    if args.window:
        a, b = args.window.split(":")
        window = (date.fromisoformat(a), date.fromisoformat(b))
        print(f"** 짧은 창 {window[0]}~{window[1]} — 배선 확인용이다. 판정 창이 아니다. **", flush=True)
    panel, feats, groups, sessions = kit.load_full_panel(markets, window, root=args.root, store=store,
                                                        rebuild=args.rebuild)
    bl = kit.blocks(sessions)
    score_feats = [*kit.SCORE_FEATS, "is_us"]
    print(f"FA {len(feats)}피처 · 묶음 {len(groups)} · 판정 블록 {len(bl)} · C0 {len(score_feats)}피처", flush=True)

    if args.precheck:
        print("\n== ① 커버리지(묶음별 결측 표지 비율) ==")
        print("\n".join(coverage(panel, groups)))
        print("\n== ② 성향 대리 상관 ==")
        print("\n".join(style_correlation(store, panel, groups, sessions)))
        print("\n== ③ C0 대비 C1 상위 24 겹침 (첫 5블록 · 시드 0 · 수익 계산 없음) ==")
        head = bl[:PRECHECK_BLOCKS]
        # 점검용 예측은 **캐시하지 않는다** — 블록 5개뿐이라 본 굽기의 예측과 다르고, 같은 이름으로 남으면 판정이 그걸 읽는다.
        c0 = kit.walk_gbm(panel, sessions, score_feats, head, (0,), label="C0")[0]
        c1 = kit.walk_gbm(panel, sessions, feats, head, (0,), label="C1")[0]
        for market in markets:
            a = c1[c1["market"] == market][["entity_id", "session", "pred"]]
            b = c0[c0["market"] == market][["entity_id", "session", "pred"]]
            if a.empty or b.empty:
                continue
            print(f"{market}: 상위 {kit.N} 겹침 {top_overlap(a, b):.0%}", flush=True)
        print(f"\n== ④ 최대 RSS {kit.rss_mb():.0f}MB ==")
        print("등록 전 점검 끝 — 수익·IC 는 계산하지 않았다.", flush=True)
        return 0

    # 굽기·캐시 규칙은 **kit 한 곳**에 있다 — 시행 도구(BE·BF·BG)가 `kit.require_controls` 로 읽는 것과 같은 파일이다.
    seeds = (0,) if args.smoke else kit.SEEDS
    if args.smoke:
        bl = bl[: args.smoke]
    out = kit.controls(panel, feats, sessions, bl, seeds=seeds, smoke=args.smoke)
    for arm, per_seed in out.items():
        for s, frame in per_seed.items():
            print(f"{arm} seed{s}: 예측 {len(frame):,}행 · 세션 {frame['session'].nunique()}", flush=True)
    print(f"꼬리표 {kit.control_tag(panel, smoke=args.smoke)} · 예측 캐시만 만들었다 — 수익·IC·판정은 "
          f"10/5 이후 시행 도구가 읽는다. 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
