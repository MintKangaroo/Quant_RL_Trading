"""원달러 환율 일봉 — LS `t3518` kind=R `USDKRWSMBS`(서울외국환중개). Yahoo `KRW=X` 대체 (2026-10-04).

    .venv/bin/python tools/collect_fx_ls.py [--dry-run]

FRED(DEXKOUS)는 주간 묶음이라 창고 환율이 며칠씩 밀린다(`collect_fx`). 그 자리를 Yahoo 가
메웠는데 Yahoo 는 robots.txt `Disallow: /` 라 걷어냈다(`docs/design/data-contract.md` §4-2).

## 규약 — FRED 행과 같은 칸

`entity_id` = `FX:USDKRW`, `valid_from` = d 09:00 KST(= FRED 의 d 00:00 UTC), `observed_at` = **실제 받은 시각**.
d 라벨 = **d 의 서울 종가**. FRED 와 같은 날짜끼리 가장 가깝다(105일 평균 |10.4|bp). 옛 Yahoo 봉은 d 라벨이
d−1 마감에 가까웠다 — 정의가 하루 당겨진다.

## 언제 적나

SMBS 일봉은 연장 거래(익일 02:00) 뒤 정산 틱이 d+1 05:4x 에 찍힌 뒤 확정이다. 그래서 **d+1 06:00 KST
전에는 d 를 적지 않는다.** 06:00 브리핑 보충과 08:40 미장 수집이 전날 값을 잡는다 — 예전 09:12 보충
(Yahoo 봉이 09:00 에 닫혀서 있던 것)은 필요 없다.

이미 있는 날은 건너뛴다. 단 **이 원천이 적은 행의 값이 지금 응답과 다르면** `revision+1` 정정본을 붙인다
(06:00 직후 값이 아직 움직였던 날). 다른 원천(FRED·옛 Yahoo) 행이 있는 날은 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.ls_investinfo import KIND_FX, DailyBar, fetch_daily  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TABLE = "fx"
ENTITY = "FX:USDKRW"
SOURCE = "ls_t3518"
SYMBOL = "USDKRWSMBS"
SEOUL = ZoneInfo("Asia/Seoul")
#: d 봉이 확정되는 시각(d+1 의 KST). 정산 틱이 05:4x 에 찍힌다(2026-10-02 봉 실측 05:42:31).
FINAL_AFTER = time(6, 0)
#: 한 번에 받는 봉 수. 연휴·결손을 메울 만큼(거래일 약 3주).
COUNT = 15
#: 같은 값으로 볼 차이. 원 단위 소수 둘째 자리 아래는 표기 차이다.
SAME_RATE = 0.005


def ls_client_for_queries(store: Any, *, as_of: datetime) -> Any:
    """조회 TR 전용 LS 클라이언트 — `collect_indices_ls` 와 같은 프로파일 규약(모의면 모의 키)."""
    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
    from tools.verify_live_order import resolve_profile

    profile = resolve_profile(store, market="KR", as_of=as_of)
    return LSClient(
        credentials=LSCredentials.from_env(prefix=profile.env_prefix),
        live_trading=False,  # t3518·t3521 은 조회 TR(PAPER_ALLOWED_TR) — 주문 경로가 없다
        min_interval_sec=max(profile.min_interval_sec, 1.05),  # 두 TR 모두 초당 1건
    )


def final_through(now: datetime) -> date:
    """지금 확정돼 있는 마지막 날짜(포함). 06:00 KST 전이면 그저께, 뒤면 어제."""
    here = now.astimezone(SEOUL)
    back = 1 if here.time() >= FINAL_AFTER else 2
    return here.date() - timedelta(days=back)


def plan_rows(
    bars: list[DailyBar], have: Any, *, now: datetime
) -> list[dict[str, Any]]:
    """적을 행. ``have`` 는 창고의 최신 fx 행(entity·valid_from·source·rate·revision)."""
    last = final_through(now)
    existing: dict[date, dict[str, Any]] = {}
    if have is not None and not have.empty:
        mine = have[have["entity_id"] == ENTITY]
        for row in mine.itertuples(index=False):
            existing[row.valid_from.astimezone(SEOUL).date()] = {
                "source": row.source, "rate": float(row.rate), "revision": int(row.revision),
            }
    rows: list[dict[str, Any]] = []
    for bar in bars:
        if bar.day > last:
            continue  # 아직 정산 전 — 미완성 값을 적지 않는다
        prior = existing.get(bar.day)
        revision = 0
        if prior is not None:
            if prior["source"] != SOURCE or abs(prior["rate"] - bar.close) < SAME_RATE:
                continue
            revision = prior["revision"] + 1  # 같은 원천이 값을 고쳤다 — 정정본
        rows.append({
            "entity_id": ENTITY,
            "valid_from": datetime.combine(bar.day, time(9, 0), tzinfo=SEOUL),
            "observed_at": now,
            "source": SOURCE,
            "revision": revision,
            "rate": float(bar.close),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    have = store.get(TABLE, as_of=now, lookback=30)
    client = ls_client_for_queries(store, as_of=now)
    try:
        bars = fetch_daily(client, KIND_FX, SYMBOL, count=COUNT)
    finally:
        client.close()
    if not bars:
        # 빈 응답은 "새 값 없음" 이 아니라 수집 경로 고장이다 — 15봉 창에 한 줄도 없을 수 없다.
        print(f"t3518 {SYMBOL} 빈 응답 — 수집 실패", file=sys.stderr)
        return 1
    rows = plan_rows(bars, have, now=now)
    for r in rows:
        tag = f" (정정 rev {r['revision']})" if r["revision"] else ""
        print(f"  {ENTITY} {r['valid_from'].date()} {r['rate']:,.2f}{tag}")
    if not rows:
        print(f"새 환율 없음 (원천 최신 {bars[-1].day} · 확정 기준 {final_through(now)})")
        return 0
    if args.dry_run:
        print(f"드라이런 — {len(rows)}행")
        return 0
    written = store.append(TABLE, rows, ingest_run_id=f"fx-ls-{now:%Y%m%dT%H%M%S}", source=SOURCE)
    print(f"fx 적재: {written}행 ({SOURCE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
