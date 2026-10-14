"""N3 주식선물 괴리(basis)를 랭커 재료로 — 평가 사다리 `--mode feature` 판정 + 공매도 금지기/재개 뒤 나눠 보기 (2026-10-14).

    flock data/_locks/research-slot-1.lock nice -n 10 .venv/bin/python tools/diag_n3_ladder.py feature   # 재료 파일 굽기
    flock data/_locks/research-slot-1.lock nice -n 10 .venv/bin/python tools/diag_n3_ladder.py ladder    # 재학습 짝 + 판정표

배경: docs/diag/n3-stock-futures-2026-10-11.md(47e72b5) — basis 하위 10% 5세션 −0.35%(t −4.9), 금지기 IC +0.039(t 5.5).
재료 = ``basis_raw``(대표 월물 정산가/현물가 − 1) **하루 늦춤**(결정 t 에 t−1 값 — 창고 observed_at 은 d 16:00 이나 실제 도착 d+1 아침,
data-contract §5-0). 선물 없는 종목은 결측(rank-gauss 가 중립 0 으로 둔다 — 사다리 재료 모드 관례).

판정: `tools/eval_ladder.py` 의 세 칸 그대로(전체·특수 구간 뺀 것·특수만). 그리고 같은 재학습 예측을 **구간별로 잘라** 다시 낸다 —
금지 전(~2023-11-03) · 금지기(2023-11-06~2025-03-30) · 재개 뒤(2025-03-31~2026-09-30). 재학습은 한 번(전 구간 워크포워드)이고 자르기는
평가만이다. 상한 2026-09-30(`eval_ladder.DATE_CAP`). 진단 도구 — 시행 아님, 창고 쓰기 없음.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from tools import eval_ladder as L  # noqa: E402

NAME = "N3"
WORK = L.OUT / "n3"
BAN = (date(2023, 11, 6), date(2025, 3, 30))
WINDOWS = {"금지 전": (None, date(2023, 11, 3)), "금지기": BAN, "재개 뒤": (date(2025, 3, 31), L.DATE_CAP)}


def bake_feature() -> Path:
    from quant_rl_trading.store import Store
    from tools import diag_n3_futures as N3
    from tools import diag_ta_features as T

    N3.END, N3.END_DAY = L.CAP_AS_OF, L.DATE_CAP          # 앞선 진단의 읽기·가드를 상한 9/30 으로
    store = Store(root=Path("data"))  # invariant-allow: data-access — 창고 루트(읽기는 store.get)
    d = T.load(store, end=L.CAP_AS_OF, start=N3.PANEL_START)
    f = N3.load_futures(store)
    shares = pd.DataFrame(index=d["close"].index, columns=d["close"].columns, dtype=float)   # basis 만 쓴다(시총 불필요)
    feats, stats = N3.build(f, d, shares)
    basis = feats["basis_raw"]                                   # 이미 shift(1) — 결정 t 에 t−1 값
    long = basis.stack().rename("pred").reset_index()
    long.columns = ["session", "entity_id", "pred"]
    long = long[(long["session"] >= L.CONTEXT_START) & (long["session"] <= L.DATE_CAP)]
    L.guard_cap(long["session"].unique(), "N3 재료")
    path = L.score_path(NAME)
    L.write_scores({0: long[["entity_id", "session", "pred"]]}, path)
    print(f"재료 {len(long):,}행 · 세션 {long['session'].nunique()} · 종목/일 {long.groupby('session').size().mean():.0f} → {path}", flush=True)
    print(json.dumps({k: v for k, v in stats.items() if not isinstance(v, dict)}, ensure_ascii=False, default=str), flush=True)
    return path


def run_ladder(n_tests: int) -> None:
    L.require_heavy(force=True)          # 사용자 10/14: 장중 무거운 계산 허용(연구 슬롯·메모리·금지 창은 호출 쪽 flock·시각으로 지킨다)
    ctx = L.load_context()
    feat = L.bundle(L.read_scores(NAME))
    # 범주 열 → 평범한 열(구간 자르기의 비교가 범주에서 안 된다)
    feat = pd.DataFrame({"entity_id": feat["entity_id"].astype(str),
                         "session": pd.to_datetime(feat["session"].astype(str)).dt.date, "pred": feat["pred"].astype(float)})
    WORK.mkdir(parents=True, exist_ok=True)
    cache = WORK / "plus-base.pkl"
    if cache.exists():
        plus_base = pd.read_pickle(cache)  # invariant-allow: data-access — 이 도구의 재학습 캐시
    else:
        plus_base = L.feature_marginal(feat, ctx)
        pd.to_pickle(plus_base, cache)  # invariant-allow: data-access — 이 도구의 재학습 캐시
    out = {}
    res = L.ladder({0: feat}, ctx, plus_base=plus_base, n_tests=n_tests)
    print(L.render(f"{NAME} 전체", res), flush=True)
    L.record(NAME, res)
    out["전체"] = res
    for label, (lo, hi) in WINDOWS.items():
        pb = {s: {k: L.restrict({0: v[k]}, lo, hi)[0] for k in ("base", "plus")} for s, v in plus_base.items()}
        fw = L.restrict({0: feat}, lo, hi)
        r = L.ladder(fw, ctx, plus_base=pb, n_tests=n_tests)
        print("\n" + L.render(f"{NAME} {label}", r), flush=True)
        out[label] = r
    (WORK / "report.json").write_text(json.dumps(L._jsonable(out), ensure_ascii=False, indent=1, default=str))
    print(f"→ {WORK / 'report.json'}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=("feature", "ladder"))
    ap.add_argument("--n-tests", type=int, default=1)
    a = ap.parse_args()
    if a.step == "feature":
        bake_feature()
    else:
        run_ladder(a.n_tests)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
