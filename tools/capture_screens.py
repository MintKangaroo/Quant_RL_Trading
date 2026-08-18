#!/usr/bin/env python
"""README 용 화면 캡처 — **데모 창고로만 찍는다.**

    QUANT_RL_DATA_ROOT=data/_demo uv run python -m flask \
        --app quant_rl_trading.dashboard.app:create_app run --port 5099 &
    uv run python tools/capture_screens.py --base http://127.0.0.1:5099

저장소가 공개라서 실계좌 화면을 올리면 보유종목·주문번호가 영구히 남는다.
GitHub 은 커밋 히스토리와 CDN 에 사본을 두므로 나중에 지워도 회수가 어렵다.
그래서 **가리는 것이 아니라 애초에 다른 창고를 찍는다** — 마스킹은 한 군데만
빠뜨려도 그게 그대로 공개된다.

데모 창고는 "우리가 무엇을 샀나"(trades·orders·nav)만 지어내고 시세·유니버스는
실전과 같은 공개 시장 데이터를 읽는다. 그래서 화면 구성·색·레이아웃은 실물과
같고 계좌만 가짜다.

## 왜 네트워크가 멈출 때까지 기다리나

이 대시보드는 HTML 을 먼저 주고 ``/api/*`` 를 뒤이어 부른다. ``load`` 만 보고
찍으면 **패널이 전부 빈 채로** 찍힌다 — 화면은 멀쩡해 보이는데 내용이 없다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

#: (경로, 파일이름, 사람이 읽을 이름). README 에 나올 순서 그대로다.
PAGES = [
    ("/trading", "trading", "매매"),
    ("/market", "market", "시장"),
    ("/calendar", "calendar", "수익률 캘린더"),
    ("/learning", "learning", "학습"),
    ("/ai-review", "ai-review", "AI 리뷰"),
    ("/agent-health", "agent-health", "에이전트 상태"),
    ("/briefing", "briefing", "브리핑"),
    ("/headlines", "headlines", "뉴스"),
    ("/data-quality", "data-quality", "데이터 품질"),
    ("/system", "system", "시스템"),
    # 되감은 화면. README 가 "타임머신이 실제로 동작한다" 를 증명하는 자리다.
    #
    # **as_of 를 붙여 찍어야 한다.** 2026-08-18 에 헤더의 as_of 입력 폼을
    # 걷어내면서 이 그림이 없는 UI 를 보여주게 됐다 — 화면을 바꾸면 그 화면을
    # 찍은 문서도 같이 낡는다. 지금은 되감았을 때만 뜨는 노란 표지가 증거다.
    ("/data-quality?as_of=2023-06-15T07:01:00Z&lookback=90",
     "data-quality-timemachine", "타임머신"),
]

DESKTOP = {"width": 1680, "height": 1050}
#: iPhone 15 Pro 논리 해상도. 하단 탭바·safe-area 가 이 폭에서 검증된다.
MOBILE = {"width": 393, "height": 852}


def capture(base: str, out_dir: Path, *, mobile: bool, scale: int) -> list[str]:
    viewport = MOBILE if mobile else DESKTOP
    suffix = "-mobile" if mobile else ""
    written: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport=viewport,
            device_scale_factor=scale,
            is_mobile=mobile,
            has_touch=mobile,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        page = context.new_page()
        for route, name, label in PAGES:
            target = out_dir / f"{name}{suffix}.png"
            try:
                page.goto(f"{base}{route}", wait_until="load", timeout=30_000)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {label:<12} {route} — {type(exc).__name__}: {exc}")
                continue

            # 패널은 /api/* 응답이 온 뒤에 그려진다. 여기서 안 기다리면
            # 빈 화면이 찍힌다.
            #
            # 다만 **networkidle 을 필수로 두면 안 된다.** 브리핑처럼 주기적으로
            # 폴링하는 화면은 네트워크가 영원히 조용해지지 않아서, 멀쩡한
            # 페이지가 타임아웃으로 통째로 빠진다(실측: /briefing 은 2ms 에
            # 응답하는데 캡처만 실패했다). 조용해지면 그때 찍고, 안 조용해지면
            # 시간을 채우고 그냥 찍는다 — 기다림은 화질을 위한 것이지 정답이
            # 아니다.
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:  # noqa: BLE001
                page.wait_for_timeout(3_000)
            # echarts 는 응답 뒤 한 프레임 더 쓴다.
            page.wait_for_timeout(1_200)
            try:
                page.screenshot(path=str(target), full_page=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {label:<12} {route} — {type(exc).__name__}: {exc}")
                continue
            size = target.stat().st_size
            print(f"  ✔ {label:<12} {target.name}  {size/1024:.0f}KB")
            written.append(target.name)
        browser.close()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5099")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "images")
    parser.add_argument("--scale", type=int, default=2, help="레티나 배율")
    parser.add_argument("--skip-mobile", action="store_true")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"캡처 대상 {args.base} → {args.out}\n데스크톱 {DESKTOP['width']}px")
    written = capture(args.base, args.out, mobile=False, scale=args.scale)
    if not args.skip_mobile:
        print(f"\n모바일 {MOBILE['width']}px")
        written += capture(args.base, args.out, mobile=True, scale=args.scale)

    print(f"\n{len(written)}장 저장했다.")
    # 한 장도 못 찍었으면 실패다 — 0장을 성공으로 적으면 README 가 조용히 빈다.
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
