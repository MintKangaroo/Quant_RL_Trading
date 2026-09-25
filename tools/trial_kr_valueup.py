"""시행 BA — 국장 밸류업·주주환원 공시 묶음을 랭커 입력에. docs/protocols/kr-valueup-2026-09.md.

    .venv/bin/python tools/trial_kr_valueup.py --coverage     # 피처 커버리지만(수익·IC 없음)
    .venv/bin/python tools/trial_kr_valueup.py [--save]

확장 패널(2022-07~2026-06, 판정 블록 41) · 루프 GBM(시행 L 규격) 대조 6피처 vs 처리 6+5피처 · 시드 0·1·2 · 상위 24 C10 포트.
피처는 창고 `documents`(DART) 한 번 읽고 세션마다 **관측 시각 < 그 세션 개장** 인 공시만 센다(미래를 안 본다).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_ensemble import FEATS, JUDGE_END  # noqa: E402
from tools.trial_ranker_kit import blocks, judge_panel, market_data, portfolio, record, summarize, walk  # noqa: E402

PROTOCOL = Path("docs/protocols/kr-valueup-2026-09.md")
SEEDS = (0, 1, 2)
WINDOW_DAYS = 365
#: (피처, 제목 규칙) — 자회사 공시·정정·첨부추가는 뺀다(같은 결정을 두 번 세지 않는다).
KINDS = {
    "vu_plan": r"^기업가치제고계획\(자율공시\)",
    "vu_cancel": r"^주식소각결정$",
    "vu_buyback": r"^주요사항보고서\(자기주식취득(?:신탁계약체결)?결정\)$",
    "vu_dividend": r"^현금ㆍ현물배당결정$",
}
NEW = [*KINDS, "vu_plan_age"]
GATE_MEAN, GATE_REGIME, GATE_IC, GATE_MDD, GATE_TURN = 0.02, -0.01, -0.01, 0.02, 1.2


def features(store: Store, sessions: list, entities: set[str]) -> pd.DataFrame:
    end = datetime.combine(sessions[-1], time(23), tzinfo=UTC)
    docs = store.get("documents", as_of=end, lookback=(sessions[-1] - sessions[0]).days + WINDOW_DAYS + 30,
                     columns=["entity_id", "valid_from", "observed_at", "title"])
    docs = docs[docs["entity_id"].isin(entities)]
    title = docs["title"].astype(str).str.strip()
    opens = np.array([np.datetime64(datetime.combine(s, time(0), tzinfo=UTC).replace(tzinfo=None), "ns") for s in sessions])  # 09:00 KST
    per_kind = []
    for kind, pattern in KINDS.items():
        hit = docs[title.str.match(pattern)]
        # 같은 종목·같은 날 여러 건(정정 원본 등)은 한 번 — 결정 한 번이 한 사건이다.
        hit = hit.assign(day=pd.to_datetime(hit["valid_from"]).dt.date).sort_values("observed_at").drop_duplicates(["entity_id", "day"])
        rows = []
        for entity, g in hit.groupby("entity_id"):
            seen = np.sort(pd.to_datetime(g["observed_at"]).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]"))
            upto = np.searchsorted(seen, opens, side="left")                               # 개장 전 관측만
            since = np.searchsorted(seen, opens - np.timedelta64(WINDOW_DAYS, "D"), side="left")
            f = pd.DataFrame({"entity_id": entity, "session": sessions, kind: (upto - since).astype(np.float32)})
            if kind == "vu_plan":
                last = seen[np.maximum(upto - 1, 0)]
                age = ((opens - last) / np.timedelta64(1, "D")).astype(float)
                f["vu_plan_age"] = np.where(upto > 0, np.minimum(age, WINDOW_DAYS), np.nan).astype(np.float32)
            rows.append(f)
        if rows:
            per_kind.append(pd.concat(rows, ignore_index=True).set_index(["entity_id", "session"]))
    if not per_kind:
        return pd.DataFrame(columns=["entity_id", "session", *NEW])
    return pd.concat(per_kind, axis=1).reset_index()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 BA — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    panel, sessions = judge_panel()
    feats = features(store, sessions, set(panel["entity_id"].unique()))
    panel = panel.merge(feats, on=["entity_id", "session"], how="left")
    for c in NEW:
        if c not in panel.columns:
            panel[c] = np.nan
    cover = {c: float(panel[c].fillna(0).gt(0).mean()) for c in KINDS}
    by_year = panel.assign(y=pd.to_datetime(pd.Series(panel["session"])).dt.year.values).groupby("y")["vu_plan"].apply(lambda s: float(s.fillna(0).gt(0).mean()))
    print("커버리지(0 아닌 행 비율): " + " · ".join(f"{k} {v:.1%}" for k, v in cover.items())
          + " · vu_plan 연도별 " + " ".join(f"{y} {v:.1%}" for y, v in by_year.items()), flush=True)
    if args.coverage:
        return 0
    # 개수는 0 이 "사건 없음" 이다 — 0 으로 둔 채 rank-gauss(순위 중앙이 아니라 아래쪽). 경과일 결측은 중앙.
    panel[list(KINDS)] = panel[list(KINDS)].fillna(0.0)
    panel = rank_gauss(panel, NEW)
    bl = blocks(sessions)
    ret, bench, trad = market_data(store, sessions)
    res: dict[str, dict[int, dict]] = {"control": {}, "treat": {}}
    for s in SEEDS:
        for arm, cols in (("control", FEATS), ("treat", [*FEATS, *NEW])):
            pred = walk(panel, sessions, cols, "y5", bl, seeds=(s,), label=f"{arm} s{s}")
            daily, extra = portfolio(pred, ret, trad, every=10)
            pred = pred.merge(panel[["entity_id", "session", "y5"]], on=["entity_id", "session"], how="left")
            res[arm][s] = {**summarize(daily, bench, pred), **extra}

    def avg(a: str, k: str) -> float:
        return float(np.mean([res[a][s][k] for s in SEEDS]))

    lines = ["| 군 | 시드 | 연수익 | 박스 | 급등 | 샤프 | MDD | IC | 회전 |", "|---|---|---|---|---|---|---|---|---|"]
    for a in ("control", "treat"):
        for s in SEEDS:
            m = res[a][s]
            lines.append(f"| {a} | {s} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['ic']:+.4f} | {m['turn']:.1f} |")
    wins = sum(res["treat"][s]["ann"] > res["control"][s]["ann"] for s in SEEDS)
    c = (avg("treat", "ann") >= avg("control", "ann") + GATE_MEAN, wins == len(SEEDS),
         avg("treat", "box_ann") >= avg("control", "box_ann") + GATE_REGIME and avg("treat", "rally_ann") >= avg("control", "rally_ann") + GATE_REGIME,
         avg("treat", "ic") - avg("control", "ic") >= GATE_IC,
         avg("treat", "mdd") >= avg("control", "mdd") - GATE_MDD and avg("treat", "turn") <= avg("control", "turn") * GATE_TURN)
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    lines += ["", f"①평균 {avg('treat', 'ann') - avg('control', 'ann'):+.1%}p {mark(c[0])} · ②{wins}/3 {mark(c[1])} · "
              f"③국면 {avg('treat', 'box_ann') - avg('control', 'box_ann'):+.1%}p/{avg('treat', 'rally_ann') - avg('control', 'rally_ann'):+.1%}p {mark(c[2])} · "
              f"④ΔIC {avg('treat', 'ic') - avg('control', 'ic'):+.4f} {mark(c[3])} · ⑤MDD·회전 {mark(c[4])}"]
    shadow = wins == len(SEEDS) and avg("treat", "ann") >= avg("control", "ann") + 0.01
    verdict = "채택" if all(c) else ("기각 — shadow 승격 후보(3/3·+1%p, v2 §3 ②′)" if shadow else "기각")
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="kr-valueup-2026-09:BA", source="trial_kr_valueup", family="ranker",
               digest=digest, verdict=verdict, lines=lines[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
