#!/usr/bin/env python
"""재심 2 판정표 — 사다리 결과(`result-RX-*.json`)를 한 가족 BH q0.05 + t ≥ 3 + 특수 뺀 쪽 t ≥ 2·부호 같음으로 묶는다
(`docs/protocols/reexam-sparse-gauss-2026-10.md` 규칙). 계산 없음, 결과 파일만 읽는다.

    .venv/bin/python tools/summarize_reexam_sparse.py [RX-G2 RX-G5 RX-BA RX-G4 RX-G1]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scipy.stats import norm

OUT = Path("data/_diag/eval-ladder")  # invariant-allow: data-access — 결과 캐시
FAMILY = ("RX-G2", "RX-G5", "RX-BA", "RX-G4", "RX-G1")
Q, T_STAR, EX_T = 0.05, 3.0, 2.0


def main(argv: list[str] | None = None) -> int:
    names = tuple(argv or sys.argv[1:]) or FAMILY
    rows = []
    for n in names:
        p = OUT / f"result-{n}.json"
        if not p.exists():
            rows.append((n, None))
            continue
        r = json.loads(p.read_text())
        rows.append((n, r))
    have = [(n, r) for n, r in rows if r is not None]
    pv = {n: 2 * (1 - norm.cdf(abs(r["marginal"]["dic"]["t"]))) for n, r in have}
    order = sorted(pv, key=pv.get)
    m = len(rows)                       # 가족 크기 = 목록 전체(못 잰 것도 센다 — 보수적)
    bh_ok, cut = set(), 0
    for i, n in enumerate(order, 1):
        if pv[n] <= Q * i / m:
            cut = i
    bh_ok = set(order[:cut])
    print("| 재료 | ΔIC | t | 특수 뺀 쪽 t | 시드 | p | BH | 판정 |")
    print("|---|---|---|---|---|---|---|---|")
    for n, r in rows:
        if r is None:
            print(f"| {n} | — | — | — | — | — | — | 결과 없음 |")
            continue
        d = r["marginal"]["dic"]
        ex = d.get("ex_special", {})
        ok = (n in bh_ok and d["ann"] > 0 and d["t"] >= T_STAR and ex.get("t", 0) >= EX_T and ex.get("ann", 0) > 0)
        print(f"| {n} | {d['ann']:+.4f} | {d['t']:+.2f} | {ex.get('t', float('nan')):+.2f} | {r['marginal']['seed_pos']}/5 "
              f"| {pv[n]:.2g} | {'O' if n in bh_ok else 'X'} | {'살아남음' if ok else '기각 유지'} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
