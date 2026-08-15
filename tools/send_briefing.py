"""시황 브리핑 — 만들고, 파일로 떨구고, (허락하면) 보낸다.

    uv run python tools/send_briefing.py --out logs/briefing        # 파일만
    uv run python tools/send_briefing.py --as-of 2026-08-15T06:00:00+09:00 --out /tmp/b
    uv run python tools/send_briefing.py --send                     # 실제 발송

**기본값은 발송이 아니다.** ``--send`` 를 주지 않으면 한 통도 나가지 않는다.
메일은 되돌릴 수 없고, 되돌릴 수 없는 것의 기본값은 "안 함" 이다.

## 언제 도는가

미장 마감 후다. 마감은 16:00 ET 고정이지만 **KST 로는 서머타임에 따라 움직인다**
— 3~11월 05:00, 겨울 06:00. 그래서 크론을 KST 시각에 고정하면 반년은 마감
전에 돈다. 두 가지 중 하나로 푼다.

1. 크론 자체를 ``America/New_York`` 로 걸고 16:40 ET 에 돌린다 (권장)
2. KST 06:40 에 걸되 이 도구가 알아서 판단하게 둔다 — 기대 세션은
   ``publication_policy`` 로 구하므로, 아직 공표 전이면 그 시장은 "오늘 세션
   없음" 으로 정직하게 나온다. 여름에는 두 시간 늦은 메일이 될 뿐이다

어느 쪽이든 **날짜를 손으로 계산하지 않는다.** 계절로 시각을 고정하는 것만
피하면 된다.

## 실패는 여기서 끝난다

리포트는 비필수 경로다 (reporting.md §2). 생성 실패도 발송 실패도 종료코드로
드러나되, 이 도구는 매매 경로에서 호출되지 않는다 — 크론이 따로 부른다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import Clock, LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.reporting import (  # noqa: E402
    build_briefing,
    emailer,
    render,
)
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시황 브리핑 생성·발송")
    parser.add_argument(
        "--as-of",
        help="ISO8601, 타임존 필수. 없으면 지금. 과거를 주면 그 시점의 메일이 재현된다",
    )
    parser.add_argument(
        "--out", help="HTML·텍스트·JSON 을 떨굴 디렉터리. 발송 없이 눈으로 볼 때 쓴다"
    )
    parser.add_argument(
        "--send", action="store_true", help="실제로 메일을 보낸다. 기본은 보내지 않는다"
    )
    parser.add_argument("--store", help="창고 경로. 기본은 레포의 data/")
    parser.add_argument(
        "--config-overlay",
        metavar="DIR",
        help=(
            "설정만 임시 루트에 심고 나머지는 실제 창고를 링크로 읽는다. "
            "config/quant_rl_trading.yaml 에 새로 넣은 키가 아직 창고에 안 심겼을 때, "
            "창고에 한 바이트도 쓰지 않고 돌려보는 길이다"
        ),
    )
    return parser.parse_args(argv)


def open_store(args: argparse.Namespace) -> Store:
    """창고를 연다. ``--config-overlay`` 면 **설정만** 사본에 둔다.

    ``reporting`` 섹션처럼 방금 yaml 에 추가한 키는 창고의 config 테이블에
    아직 없다. 정상적인 길은 ``store.seed_config_defaults()`` 로 심는 것이지만,
    그건 창고에 쓰는 일이다 — 백테스트가 도는 동안에는 하고 싶지 않을 수 있다.

    오버레이는 config 만 빈 디렉터리로 새로 만들고 나머지 테이블은 실제 창고로
    심볼릭 링크한다 (``store/overlay.py``). 4.3GB 를 복사하지도, 원본에 쓰지도
    않는다.
    """
    source = Path(args.store) if args.store else Store().root
    if not args.config_overlay:
        return Store(root=source)

    root = Path(args.config_overlay)
    overlay.build(root=root, source=source, writable={"config"})
    store = Store(root=root)
    store.seed_config_defaults()
    return store


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env()

    clock: Clock
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            print("--as-of 에 타임존이 없다. 국장·미장을 한 시스템에서 돌리는 이상 "
                  "어느 쪽 자정인지 추측하지 않는다", file=sys.stderr)
            return 2
        clock = ReplayClock(as_of.astimezone(UTC))
    else:
        clock = LiveClock()
    as_of = clock.now()

    store = open_store(args)
    briefing = build_briefing(store, as_of=as_of, clock=clock)
    parts = render.render(briefing)

    print(parts["text"])
    if briefing.notes:
        print("\n[들어오지 않은 것]", file=sys.stderr)
        for note in briefing.notes:
            print(f"  - {note}", file=sys.stderr)

    if args.out:
        target = Path(args.out)
        target.mkdir(parents=True, exist_ok=True)
        stamp = as_of.strftime("%Y%m%dT%H%M%SZ")
        html_path = target / f"briefing_{stamp}.html"
        text_path = target / f"briefing_{stamp}.txt"
        json_path = target / f"briefing_{stamp}.json"
        html_path.write_text(parts["html"], encoding="utf-8")
        text_path.write_text(parts["text"], encoding="utf-8")
        json_path.write_text(
            json.dumps(briefing.as_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n파일: {html_path}\n      {text_path}\n      {json_path}", file=sys.stderr)

    if not args.send:
        print("\n(--send 없음 — 메일은 보내지 않았다)", file=sys.stderr)
        return 0

    result = emailer.send(
        subject=parts["subject"], html=parts["html"], text=parts["text"]
    )
    print(f"\n발송: {result.status} — {result.detail}", file=sys.stderr)
    # 발송 실패도 종료코드 0 이다. 크론이 이걸 실패로 세면 알림이 쌓이고,
    # 쌓인 알림은 안 보게 된다. 상태는 위 한 줄이 말한다.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
