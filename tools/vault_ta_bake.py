"""시행 TG 금고 굽기 — second 창(10/1~11/13) 기술적 합성 점수. 가격만 읽는다(수익·라벨은 판정부가 계산).

    .venv/bin/python tools/vault_ta_bake.py         # 창 끝(11/13) 뒤. 산출 data/_diag/vault-second/ta-KR.pkl (session · entity_id · r_hat=합성 점수)

실전 신호(`tools/ta_signal.py`)와 같은 함수(`tools.ta_composite.composite_panel`). 하루 늦춤은 판정부(`vault_judge.tsfm_lagged`)가 건다.
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_rl_trading.store import Store
from tools.ta_composite import composite_panel

START, END = date(2026, 10, 1), date(2026, 11, 13)
OUT = Path("data/_diag/vault-second/ta-KR.pkl")


def main() -> int:
    if OUT.exists():
        print(f"{OUT} 있음 — 건너뛴다")
        return 0
    comp, _ = composite_panel(Store(root=Path("data")), end=datetime.combine(END, time(16, 0), tzinfo=UTC))
    part = comp.loc[[d for d in comp.index if d >= date(2026, 9, 25)]]          # 첫 세션의 하루 늦춤용 앞 세션 포함
    long = part.stack().rename("r_hat").reset_index()
    long.columns = ["session", "entity_id", "r_hat"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    long.to_pickle(OUT)
    print(f"{OUT} · {long['session'].nunique()}세션 · {len(long):,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
