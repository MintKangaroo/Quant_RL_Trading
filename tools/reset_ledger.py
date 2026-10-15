"""장부 리셋 — 계좌가 초기화됐을 때 **옛 장부를 통째로 보관하고 새 장부를 연다** (accounting.md §9, runbook §4.1).

    .venv/bin/python tools/reset_ledger.py --sandbox data/_paper --flow KRW:500000000 \\
        --valid-from 2026-10-15T08:00+09:00 --known-at 2026-10-15T12:45+09:00 \\
        --reason "LS 모의계좌 3개월 만료 초기화(t0424 추정순자산 5억·보유 0)"            # 미리보기
    ... --apply                                                                       # 실행

하는 일:
1. ``--sandbox`` 를 ``data/_archive/<이름>-<첫 입금일>_<리셋일>`` 로 **옮긴다**. 지우지도 고치지도 않는다 — 옛 장부의 어떤
   as_of 조회도 그대로 재현된다(불변식 4). 보관 위치는 ``data/_*_shadow`` 묶음(apply_switch·압축 크론) 밖이다.
2. 같은 경로에 빈 오버레이를 새로 만들고 ``capital_flows`` 에 입금 행을 적는다(source ``account_reset``,
   ingest_run_id 에 보관 위치). 이 행이 새 장부의 원금·TWR 기준점이다 — 원금 = 입금 누계(§8.1), 지수 100 은 첫 스냅샷.

왜 같은 장부에 '리셋 거래' 를 얹지 않나: 원금이 입출금 누계라 옛 입금(5.07억)과 정산 입금이 쌓여 새 측정의 수익금이 첫날부터
옛 손익을 들고 시작하고, 지수·낙폭·반영률·주문표도 옛 기록을 끌고 온다. 사용자가 원한 것은 '대시보드도 리셋' 이다.

``--known-at`` 은 그 잔고를 **알 수 있었던 시각**(observed_at)이다 — 증권사 조회 시각 같은. 세션은 as_of = 그날 16:00 으로
장부를 접으므로(backtest.loop.snapshot_moment), 그날 세션이 이 입금을 보려면 16:00 전이어야 한다. 지금보다 늦으면 거부한다.

장 중(평일 08:20~15:50 KST)에는 --apply 를 거부한다. 장부를 쓰는 크론(대사·주문감시·조각 배포)을 먼저 멈춘다 — 이 도구는
명령줄에 그 장부 경로가 든 프로세스가 있으면 거부한다(``_locks`` 의 잠금 파일은 쓰고 나서도 남아 있어 증거가 못 된다).
대시보드는 환경변수로 장부를 잡아 명령줄에 안 보인다 — 옮긴 뒤 scripts/restart_dashboards.sh 로 다시 띄운다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.accounting import ledger as L  # noqa: E402
from quant_rl_trading.accounting.rates import Rates  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store  # noqa: E402

SEOUL = ZoneInfo("Asia/Seoul")
SOURCE = "account_reset"
ARCHIVE_DIR = "_archive"
#: 새 장부에 남기는 리셋 기록(사람이 읽는 것). 창고 기록은 capital_flows 행이다.
NOTE = "RESET.json"


def parse_flow(text: str) -> tuple[str, float]:
    currency, _, amount = text.partition(":")
    if currency not in (L.KRW, L.USD) or not amount:
        raise SystemExit(f"--flow 는 KRW:금액 또는 USD:금액 — {text!r}")
    value = float(amount.replace(",", "").replace("_", ""))
    if value <= 0:
        raise SystemExit(f"입금은 양수여야 한다 — {text!r}")
    return currency, value


def first_flow_day(store: Store, *, as_of: datetime) -> str:
    flows = store.get(L.CAPITAL_FLOWS, as_of=as_of, lookback=None)
    if flows.empty:
        return "empty"
    return flows["valid_from"].min().tz_convert(SEOUL).date().isoformat()


def busy(sandbox: Path) -> list[str]:
    """명령줄에 장부 경로가 든 다른 프로세스(pid: 명령 앞부분). 자기와 조상(timeout·셸)은 뺀다."""
    needle = sandbox.as_posix().rstrip("/")
    mine = set()
    pid = os.getpid()
    while pid > 1 and pid not in mine:
        mine.add(pid)
        try:
            pid = int((Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    out = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) in mine:
            continue
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if needle in cmd:
            out.append(f"{proc.name}: {cmd[:120]}")
    return out


def in_market_hours(now: datetime) -> bool:
    local = now.astimezone(SEOUL)
    return local.weekday() < 5 and 820 <= local.hour * 100 + local.minute <= 1550


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sandbox", required=True, help="리셋할 장부(data/_paper · data/_shadow)")
    ap.add_argument("--flow", action="append", required=True, help="새 장부 입금 — KRW:500000000 · USD:370000 (여러 번)")
    ap.add_argument("--valid-from", required=True, help="새 잔고가 유효해진 시각(ISO, 시간대 포함)")
    ap.add_argument("--known-at", required=True, help="그 잔고를 알 수 있었던 시각 = observed_at (ISO, 시간대 포함)")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    now = LiveClock().now()
    valid_from = datetime.fromisoformat(args.valid_from)
    known_at = datetime.fromisoformat(args.known_at)
    if valid_from.tzinfo is None or known_at.tzinfo is None:
        raise SystemExit("--valid-from · --known-at 에 시간대를 붙여라(+09:00)")
    if known_at > now:
        raise SystemExit(f"--known-at {known_at.isoformat()} 이 지금보다 늦다 — 아직 모르는 것을 안다고 적지 않는다")
    flows = [parse_flow(f) for f in args.flow]

    sandbox = Path(args.sandbox)
    if not (sandbox / "curated").is_dir():
        raise SystemExit(f"{sandbox} 는 장부(오버레이)가 아니다")
    if (sandbox / "config-overrides.yaml").exists():
        # 실험 장부의 덮어쓰기 설정은 새 장부로 옮겨야 한다 — 이 도구는 실전 장부(_paper·_shadow)용이다.
        raise SystemExit(f"{sandbox} 에 config-overrides.yaml 이 있다 — 실험 장부 리셋은 이 도구로 하지 않는다")
    source = build_store(None)
    old = Store(root=overlay.build(root=sandbox, source=source.root, writable=JOURNAL).root)
    reset_day = valid_from.astimezone(SEOUL).date().isoformat()
    archive = sandbox.parent / ARCHIVE_DIR / f"{sandbox.name.lstrip('_')}-{first_flow_day(old, as_of=now)}_{reset_day}"
    if archive.exists():
        raise SystemExit(f"보관 위치 {archive} 가 이미 있다 — 같은 리셋을 두 번 하지 않는다")

    old_book = L.build_book(old, as_of=now, rates=Rates.from_store(old, as_of=now))
    held = {k: v for k, v in old_book.positions.items() if abs(v.quantity) > 0}
    print(f"== 옛 장부 {sandbox} → {archive}")
    print(f"  보유 {len(held)}종목 · 현금 { {k: round(v) for k, v in old_book.cash.items()} } · 원금 {L.principal(old, as_of=now):,.0f}")
    running = busy(sandbox)
    print(f"  이 장부를 쥔 프로세스: {running or '(없음)'}")
    print(f"== 새 장부 {sandbox} — capital_flows (valid_from {valid_from.isoformat()} · observed_at {known_at.isoformat()})")
    for currency, amount in flows:
        print(f"  deposit {currency} {amount:,.2f}")
    print(f"  사유: {args.reason}")
    if not args.apply:
        print("\n미리보기 — --apply 로 실행(장 마감 뒤, 장부 쓰는 크론을 멈춘 뒤)")
        return 0
    if in_market_hours(now):
        print("장 중(평일 08:20~15:50)에는 실행하지 않는다")
        return 2
    if running:
        print(f"이 장부를 쥔 프로세스가 있다 — 끝난 뒤 다시: {running}")
        return 3

    archive.parent.mkdir(parents=True, exist_ok=True)
    sandbox.rename(archive)
    fresh = Store(root=overlay.build(root=sandbox, source=source.root, writable=JOURNAL).root)
    run_id = f"reset-{reset_day}-from-{archive.name}"
    # 자연키가 (계좌, valid_from) 라 같은 시각의 원화·달러 두 행은 한 행으로 접힌다(최신만 남음 — 리허설에서 원화 5억이 사라졌다).
    # 9/2 달러 시딩처럼 1초씩 띄운다.
    rows = [{"entity_id": L.ACCOUNT, "valid_from": valid_from + timedelta(seconds=i), "observed_at": known_at,
             "source": SOURCE, "currency": currency, "amount": amount, "kind": "deposit"}
            for i, (currency, amount) in enumerate(flows)]
    written = fresh.append(L.CAPITAL_FLOWS, rows, ingest_run_id=run_id, source=SOURCE)
    (sandbox / NOTE).write_text(json.dumps({
        "reset_at": now.isoformat(), "archive": str(archive), "valid_from": valid_from.isoformat(),
        "known_at": known_at.isoformat(), "flows": [{"currency": c, "amount": a} for c, a in flows],
        "reason": args.reason, "old_positions": len(held),
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"보관 완료 {archive} · 새 장부 입금 {written}행 ({run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
