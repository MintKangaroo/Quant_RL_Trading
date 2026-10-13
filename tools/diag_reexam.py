"""기각 시행 재심 — `docs/protocols/reexam-rejected-2026-10.md`(main 고정, 결과 보기 전 목록) 를 평가 사다리로 한 번에.

    .venv/bin/python tools/diag_reexam.py scores     # 캐시에서 처리·대조 점수 파일을 만든다(가벼움, 새 학습 없음)
    .venv/bin/python tools/diag_reexam.py report     # 사다리 세 칸 × 전체/특수 뺀 쪽/특수만 · 한 가족 BH(q 0.05)

**진단이다** — 통과해도 '새 등록 후보' 표시만(규칙 5). 창고·실전에 쓰지 않는다. 상한 2026-09-30(`eval_ladder.DATE_CAP`).

이번 회차가 **캐시만으로** 잴 수 있는 것(새 학습 없음 — 원 시행의 OOS 예측을 그대로):
- WL(W 머리) 대 같은 회차 C1 — `data/_diag/wl-heads`(국장, 2022-06-28~2026-06-02, 시드 5)
- D1 v2(D1b) 대 C1 — `data/_diag/final-round/D1`(2023-12-07~, 시드 5) · 국장 + 미장 다리
- BF(BF1 · BF2 = BF1·C1 순위 평균) 대 C1 — 회차 캐시(2023-02-15~2026-06-29, 시드 5) · 국장 + 미장 다리
- 섹터 중립 — fundamental 점수를 KSIC 중분류 안 z 로 바꾼 것 대 원 점수(국장). 원 시행은 KSIC 24군이었다 — 분류가 다르다(기록)
- G4 · G7 — AR 대 현행 진단(`diag_ar_vs_current`)의 국장(G4)·미장(G7) 다리를 그대로 가져온다(겹치는 이름은 한 번만 센다)
나머지(AM·Q·P·AD·BB·AW·AY·BG·J·DF·TB·RE·AS·AV·N1 의 최근 창)는 포트·노출 규칙이거나 재학습·재구축이 필요해 이번 회차에 **재지 않았다** —
BH 가족 수에는 넣고(p = 1 로 보수적으로) 사유를 적는다.

주 기준(BH 에 넣는 p) = 칸 ② 짝 ΔIC(처리 − 자기 대조)의 NW t 양측 p. 통과 = BH q 0.05 · t ≥ 3 · 특수 뺀 쪽 t ≥ 2·부호 같음.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import eval_ladder as el  # noqa: E402

OUT = el.OUT / "reexam"
WL = Path("data/_diag/wl-heads")  # invariant-allow: data-access — 시행 캐시
FINAL = Path("data/_diag/final-round")  # invariant-allow: data-access — 회차 캐시
FUND = Path("data/_diag-long/scores-fundamental-KR.pkl")  # invariant-allow: data-access — 연구 캐시
TAG = "KR+US-20220701-20260630"
CTX_US = el.OUT / "context-US"
AR_DIR = el.OUT / "ar"
#: 고정 목록(중복 제외) — 재심 문서 그대로. 값 = 이번 회차에서 잰 군 이름(없으면 안 잰 사유).
FAMILY: dict[str, str] = {
    "AM": "안 잼 — 포트 폭 규칙(72종목), 점수 비교 아님",
    "BB": "안 잼 — HMM 노출 규칙, 종목 점수 아님",
    "WL": "WL-W-KR",
    "Q": "안 잼 — K200 안 후보 규칙, K200 명단(pykrx) 필요",
    "P": "안 잼 — 유동시총 가중 규칙",
    "J": "안 잼 — 미장 SUE 재료 재구축 필요",
    "G4": "AR-KR",
    "G7": "AR-US",
    "N1": "안 잼 — 랭커 재료로는 C1+nsi 재학습 필요(과거 금고 원리 t −4.05 는 기존 기록)",
    "D1b": "D1b-KR",
    "DF": "안 잼 — HMM 위기 섞기 규칙, 섞기 재현 필요",
    "AD": "안 잼 — 지수−패자 규칙, K200 명단 필요",
    "BG": "안 잼 — 잔차 RL 재학습(41블록 × 5시드)",
    "AS": "안 잼 — 얼린 모델은 금고 창 원피처만, 긴 창 예측 없음",
    "섹터 중립": "SECT-KR",
    "AV": "안 잼 — 미장 72종목 규칙(오염 명단 캐시)",
    "AW": "안 잼 — 미장 국면 노출 규칙",
    "AY": "안 잼 — 미장 기울인 지수 + 노출 규칙",
    "BF": "BF2-KR",
    "TB": "안 잼 — 지수 코어 기울이기 규칙",
    "RE": "안 잼 — HMM 가중 섞기 재현 필요",
}
#: 같은 이름의 추가 다리(기록 — BH 의 대표 다리는 FAMILY 값)
EXTRA = {"BF": ["BF1-KR", "BF1-US", "BF2-US"], "D1b": ["D1b-US"]}


def log(msg: str) -> None:
    print(msg, flush=True)


def _kr_us(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    f = frame[frame["market"] == market] if "market" in frame.columns else frame
    f = f[["entity_id", "session", "pred"]].copy()
    f["session"] = pd.to_datetime(f["session"]).dt.date
    return f


def _write(name: str, gen: Any) -> None:
    el.write_scores(gen, el.score_path(name, OUT / "scores"))
    log(f"  {name} 저장")


def cmd_scores() -> int:
    from tools.trial_final_transformer import rank_average

    seeds = el.SEEDS
    _write("WL-W-KR", ((s, _kr_us(pd.read_pickle(WL / f"pred-W-seed{s}.pkl"), "KR")) for s in seeds))  # invariant-allow: data-access — 시행 캐시
    _write("WL-C1-KR", ((s, _kr_us(pd.read_pickle(WL / f"pred-C1-seed{s}.pkl"), "KR")) for s in seeds))  # invariant-allow: data-access — 시행 캐시
    for market in ("KR", "US"):
        _write(f"D1b-{market}", ((s, _kr_us(pd.read_parquet(FINAL / "D1" / f"v2-C1-D1b-seed{s}-judge.parquet"), market))  # invariant-allow: data-access — 회차 캐시
                                 for s in seeds))
        _write(f"C1-{market}", ((s, _kr_us(pd.read_pickle(FINAL / f"pred-C1-seed{s}-{TAG}.pkl"), market)) for s in seeds))  # invariant-allow: data-access — 회차 캐시
        _write(f"BF1-{market}", ((s, _kr_us(pd.read_pickle(FINAL / f"pred-BF1-seed{s}-{TAG}.pkl"), market)) for s in seeds))  # invariant-allow: data-access — 회차 캐시

        def bf2(market: str = market):  # type: ignore[no-untyped-def]
            for s in seeds:
                b = pd.read_pickle(FINAL / f"pred-BF1-seed{s}-{TAG}.pkl")  # invariant-allow: data-access — 회차 캐시
                c = pd.read_pickle(FINAL / f"pred-C1-seed{s}-{TAG}.pkl")  # invariant-allow: data-access — 회차 캐시
                b, c = b[b["market"] == market], c[c["market"] == market]
                out = _kr_us(rank_average(b[["entity_id", "session", "market", "pred"]], c), market)
                del b, c
                yield s, out
        _write(f"BF2-{market}", bf2())
        gc.collect()
    # 섹터 중립 — fundamental 원 점수 대 KSIC 중분류 안 z(시드 없음)
    ctx = el.load_context()
    f = pd.read_pickle(FUND)  # invariant-allow: data-access — 연구 캐시
    f["session"] = pd.to_datetime(f["session"]).dt.date
    f = f[f["session"] <= el.DATE_CAP].rename(columns={"score": "pred"})[["entity_id", "session", "pred"]]
    f["sector"] = f["entity_id"].map(ctx.sectors).fillna("NA")
    g = f.groupby(["session", "sector"])["pred"]
    z = (f["pred"] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
    _write("SECT-KR", [(0, f.assign(pred=z.fillna(0.0))[["entity_id", "session", "pred"]])])
    _write("FUND-KR", [(0, f[["entity_id", "session", "pred"]])])
    return 0


#: 군 → (처리, 대조, 시장)
ARMS = {
    "WL-W-KR": ("WL-W-KR", "WL-C1-KR", "KR"),
    "D1b-KR": ("D1b-KR", "C1-KR", "KR"), "D1b-US": ("D1b-US", "C1-US", "US"),
    "BF1-KR": ("BF1-KR", "C1-KR", "KR"), "BF2-KR": ("BF2-KR", "C1-KR", "KR"),
    "BF1-US": ("BF1-US", "C1-US", "US"), "BF2-US": ("BF2-US", "C1-US", "US"),
    "SECT-KR": ("SECT-KR", "FUND-KR", "KR"),
    "AR-KR": ("AR-KR", "ARc-KR", "KR"), "AR-US": ("AR-US", "ARc-US", "US"),
}


def _path(name: str) -> Path:
    return el.score_path(name, AR_DIR) if name.startswith("AR") else el.score_path(name, OUT / "scores")


def bh(pvals: dict[str, float], q: float = 0.05) -> dict[str, bool]:
    """Benjamini–Hochberg — 가족 전체(안 잰 것은 p = 1)."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    cut = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / m:
            cut = i
    passed = {k for k, _ in items[:cut]}
    return {k: k in passed for k in pvals}


def cmd_report(force: bool) -> int:
    el.require_heavy(force)
    from tools.diag_translation_loss import _seed_lines

    res: dict[str, Any] = {}
    md = [f"# 기각 시행 재심 — 사다리 원본(상한 {el.DATE_CAP})", ""]
    ctxs = {"KR": el.load_context()}
    if CTX_US.exists():
        ctxs["US"] = el.load_context(CTX_US)
    for arm, (treat, ctrl, market) in ARMS.items():
        if market not in ctxs or not _path(treat).exists() or not _path(ctrl).exists():
            log(f"{arm}: 자료 없음 — 건너뜀")
            continue
        ctx = ctxs[market]
        a, b = el.read_scores(_path(treat)), el.read_scores(_path(ctrl))
        r = el.ladder(a, ctx, control=b, n_tests=len(FAMILY))
        res[arm] = r
        md += [el.render(f"{arm} — {treat} 대 {ctrl}", r), "", *_seed_lines(r)]
        log(f"{arm} 끝 — 첫 탈락 {r['first_fail']}")
        gc.collect()
    pvals, rows = {}, []
    for name, arm in FAMILY.items():
        r = res.get(arm)
        if r is None or "dic" not in r.get("marginal", {}):
            pvals[name] = 1.0
            rows.append((name, arm, None))
            continue
        d = r["marginal"]["dic"]
        pvals[name] = el._p_two(d["t"]) if np.isfinite(d["t"]) else 1.0
        rows.append((name, arm, r))
    passed_bh = bh(pvals)
    md += ["## 한 가족 BH(q 0.05) — 가족 수 " + str(len(FAMILY)), "",
           "| 시행 | 군 | ΔIC (t) | 특수 뺀 쪽 (t) | 특수만 (t) | p | BH | t≥3 | 특수 견고 | 결과 | 첫 탈락 칸 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    out_rows = []
    for name, arm, r in rows:
        if r is None:
            md.append(f"| {name} | — | — | — | — | 1 | — | — | — | 재지 않음: {arm} | — |")
            out_rows.append({"trial": name, "measured": False, "why": arm})
            continue
        d = r["marginal"]["dic"]
        ex, sp = d["ex_special"], d["special_only"]
        t3 = d["ann"] > 0 and d["t"] >= 3.0
        robust = ex["ann"] > 0 and ex["t"] >= 2.0
        ok = passed_bh[name] and t3 and robust
        verdict = "새 등록 후보" if ok else ("특수(능력이지만 특수)" if (t3 and not robust and sp["ann"] > 0) else "재심 탈락")
        md.append(f"| {name} | {arm} | {d['ann']:+.4f} ({d['t']:+.2f}) | {ex['ann']:+.4f} ({ex['t']:+.2f}) | {sp['ann']:+.4f} ({sp['t']:+.2f}) | "
                  f"{pvals[name]:.2g} | {'O' if passed_bh[name] else 'X'} | {'O' if t3 else 'X'} | {'O' if robust else 'X'} | {verdict} | {r['first_fail'] or '없음'} |")
        out_rows.append({"trial": name, "arm": arm, "p": pvals[name], "bh": passed_bh[name], "t3": t3, "robust": robust,
                         "verdict": verdict, "first_fail": r["first_fail"]})
    md.append("")
    for name, extras in EXTRA.items():
        for arm in extras:
            r = res.get(arm)
            if r is not None:
                d = r["marginal"]["dic"]
                md.append(f"- 기록 다리 {name} · {arm}: ΔIC {d['ann']:+.4f}(t {d['t']:+.2f}) · 첫 탈락 {r['first_fail'] or '없음'}")
    res["family"] = out_rows
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(el._jsonable(res), ensure_ascii=False, indent=1))
    (OUT / "report.md").write_text("\n".join(md))
    log("\n".join(md))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("scores", "report"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    return cmd_scores() if args.cmd == "scores" else cmd_report(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
