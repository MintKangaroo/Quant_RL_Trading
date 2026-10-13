"""TTM 일봉판(r2.1) 대 현행 main — score(docs/diag/tsfm-ttm-daily-2026-10-13.md). 실전 `.venv`.

진단이지 시행이 아니다. 창고를 다시 읽지 않는다 — 10/9 진단 extract(`store.get(as_of=2026-06-30 23:59)` 경유)가 쓴
keys·labels 를 그대로 쓴다(금고 2026-07-01~ 은 그 파일에 없다). IC·NW t 는 `tools/diag_tsfm_zeroshot.py` 의 함수를 그대로 부른다.

    .venv-bench/bin/python bench/tsfm_ttm_variants.py --threads 4
    .venv/bin/python tools/diag_tsfm_ttm_variants.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "tools"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from diag_tsfm_zeroshot import H, _ic_stats, _spearman_by_session  # noqa: E402

ZS = ROOT_DIR / "data" / "_diag" / "tsfm-zeroshot"
OUT = ROOT_DIR / "data" / "_diag" / "tsfm-ttm-daily"
VAULT_START = pd.Timestamp("2026-07-01")
N_DAILY = 2600          # 매일 신호 규모(국장 약 2,600 종목) — 추론 시간 환산


def _decile_excess(df: pd.DataFrame) -> tuple[float, float]:
    """세션마다 r_hat 10분위 → D1·D10 평균 r − 세션 평균 r, 세션 평균."""
    d1, d10 = [], []
    for _, g in df.dropna(subset=["r", "r_hat"]).groupby("session"):
        if len(g) < 30:
            continue
        q = pd.qcut(g["r_hat"].rank(method="first"), 10, labels=False)
        mu = g["r"].mean()
        d1.append(g.loc[q == 0, "r"].mean() - mu)
        d10.append(g.loc[q == 9, "r"].mean() - mu)
    return float(np.mean(d1)), float(np.mean(d10))


def main() -> int:
    run = json.loads((OUT / "preds" / "run.json").read_text())["cells"]
    rows = []
    for w in ("A", "B"):
        keys = pd.read_pickle(ZS / "inputs" / f"keys-KR-{w}.pkl")
        lab = pd.read_pickle(ZS / "labels" / f"labels-KR-{w}.pkl")
        base = keys.merge(lab, on=["session", "entity_id"], how="left", validate="1:1")
        assert len(base) == len(keys)
        assert pd.to_datetime(base["session"]).max() < VAULT_START, "금고 구간 세션"
        preds = {}
        for f in sorted((OUT / "preds").glob(f"*-KR-{w}.npz")):
            p = np.load(f)["price"]
            assert len(p) == len(base), f.name
            preds[f.stem.rsplit("-KR-", 1)[0]] = p[:, H - 1] / p[:, 0] - 1.0     # 10/9 정의: p̂(t+6)/p̂(t+1) − 1
        old = np.load(ZS / "preds" / f"ttm-KR-{w}-L256.npz")["price"]             # 10/9 결과와 같은지(재현 확인)
        v0_old = old[:, H - 1] / old[:, 0] - 1.0
        reprod = float(np.nanmax(np.abs(preds["V0_main"] - v0_old)))
        for name, rh in [("반전5", base["rev5"].to_numpy()), ("모멘텀20", base["mom20"].to_numpy())] + list(preds.items()):
            df = base[["session", "r"]].copy()
            df["r_hat"] = rh
            df["v0"] = preds["V0_main"]
            st = _ic_stats(_spearman_by_session(df, "r_hat", "r"))
            rc = _spearman_by_session(df, "r_hat", "v0").mean()
            d1, d10 = _decile_excess(df)
            cell = run.get(f"{name}-KR-{w}", {})
            rows.append({"창": w, "변형": name, "IC": st["mean"], "t": st["t"], "양수": st["pos"], "세션": st["n"],
                         "D1": d1, "D10": d10, "V0순위상관": float(rc),
                         "ms/계열": cell.get("ms_per_series"), f"{N_DAILY}종목초": (cell.get("ms_per_series") or np.nan) * N_DAILY / 1000,
                         "리비전": cell.get("snapshot", "")[:8] if cell else "", "V0재현오차": reprod if name == "V0_main" else None})
    table = pd.DataFrame(rows)
    (OUT / "score.json").write_text(table.to_json(orient="records", force_ascii=False, indent=1))
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(table.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
