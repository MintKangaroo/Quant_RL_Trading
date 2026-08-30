"""지금 **AI 가 매매에 실제로 얼마나 관여하나** — 창고에서 읽어 한 표로.

    .venv/bin/python tools/ai_status.py [--store data/_paper]

사용자가 상시 확인하는 표(2026-08-30 요청). 값을 지어내지 않는다 — 설정·가중치·판정 기록에서만 온다.
'구현했다' 와 '매매에 쓰인다' 는 다른 사실이고, 이 표는 뒤쪽만 말한다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.errors import ConfigNotFound  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="data", help="설정·가중치를 읽을 창고")
    parser.add_argument("--journal", default="data/_paper", help="매매 장부(차단 판정을 읽는다)")
    args = parser.parse_args(argv)
    store = Store(root=Path(args.store))
    now = datetime.now(UTC)  # invariant-allow: wallclock

    baseline = str(store.config("allocator.baseline", as_of=now))
    try:
        checkpoint = str(store.config("allocator.rl.checkpoint", as_of=now) or "")
        modes = store.config("allocator.rl.modes", as_of=now)
    except ConfigNotFound:
        checkpoint, modes = "", []
    rl_on = bool(checkpoint)

    weights = store.get("analyst_weights", as_of=now, lookback=60)
    active: list[str] = []
    if not weights.empty:
        latest = weights.sort_values("valid_from").groupby("entity_id").tail(1)
        active = sorted(str(r["entity_id"]) for _, r in latest.iterrows() if float(r["weight"]) > 0)

    journal = Store(root=Path(args.journal))
    verdicts = journal.get("verdicts", as_of=now, lookback=30)
    blocks = 0
    if not verdicts.empty:
        live = verdicts[(verdicts["decision"] == "block") & (verdicts["expires_at"] > now)]
        blocks = len(live)

    reviews = store.get("reviews", as_of=now, lookback=10)
    review_note = "없음"
    if not reviews.empty:
        row = reviews.sort_values("valid_from").iloc[-1]
        review_note = f"{row['valid_from'].date()} 장 리뷰 기록됨(해설만, 불변식 8)"

    rows = [
        ("강화학습(RL)",
         "있음" if rl_on else "0%",
         (f"정책 {Path(checkpoint).name} · 모드 {modes}" if rl_on
          else f"정책 체크포인트 비어 있음 = 꺼짐. allocator.baseline: {baseline}(룰)")),
        ("지도학습", "0%", "LightGBM 랭커 2회 기각. 코드에 안 들어감"),
        ("비지도학습",
         "있음" if baseline == "risk_parity" else "0%",
         ("리스크 패리티(팩터 공분산)가 비중을 정한다" if baseline == "risk_parity"
          else "팩터 공분산/리스크 패리티 미채택 — §7 검증 대상")),
        ("LLM(Claude)",
         "있음" if blocks else "0%",
         f"뉴스 판정으로 {blocks}개 종목 매수 차단 중 · {review_note}"),
    ]
    width = max(len(r[0]) for r in rows)
    print(f"AI 매매 관여 — {now.astimezone().strftime('%Y-%m-%d %H:%M')} · 창고 {store.root}")
    print(f"{'종류'.ljust(width)}  {'지금 매매 영향':<10}  상태")
    for name, effect, note in rows:
        print(f"{name.ljust(width)}  {effect:<12}  {note}")
    print(f"\n종목 선정에 가중치를 받는 Analyst: {', '.join(active) if active else '없음'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
