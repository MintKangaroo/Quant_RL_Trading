"""이메일 HTML 이 좁은 화면에서 가로로 넘치는지 **실제로 렌더해서** 잰다.

    uv run python tools/check_email_width.py logs/briefing/briefing_*.html
    uv run python tools/check_email_width.py --width 390 --shot out.png <파일>

## 왜 눈으로 보면 안 되나

"아마 될 것" 으로 넘긴 레이아웃은 대개 안 된다. 폭이 넘치는지는 **글자가
실제로 그 폰트로 그려져 봐야** 알 수 있다 — 종목명 한 개가 길어서 표가
밀리는 종류의 사고는 소스를 읽어서는 안 보인다.

그래서 진짜 Chromium 을 390px 뷰포트로 띄우고 ``scrollWidth`` 를 잰다.
``documentElement.scrollWidth > innerWidth`` 이면 가로 스크롤이 생긴 것이고,
그건 아이폰에서 읽는 메일로는 실패다.

넘친 원소를 하나씩 되짚어 **무엇이 밀었는지**까지 돌려준다. 총평만 주면
고칠 수가 없기 때문이다.

## Chromium 은 어디서 오나

playwright 패키지는 이 venv 에 없지만 브라우저 바이너리는 캐시에 있다.
바이너리만 직접 쓴다 — 의존성을 늘리지 않으려고.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

#: 아이폰 13 Pro 의 CSS 폭. 기기 픽셀은 1170 이지만 레이아웃이 보는 폭은 이것이다.
IPHONE_13_PRO = 390

CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"

#: 측정용 **틀**. 메일을 정확히 ``width`` 픽셀짜리 iframe 에 넣어 띄운다.
#:
#: ``--window-size`` 를 쓰지 않는 이유: 이 headless 빌드가 그 값을 무시한다.
#: 390 을 주든 320 을 주든 ``innerWidth`` 가 500 으로 나왔다(실측). 그 위에서
#: "안 넘쳤다" 는 판정은 500px 짜리 판정이지 아이폰 판정이 아니다 — 넘침을
#: 못 잡고 통과시키는 쪽이라 더 나쁘다.
#:
#: iframe 은 자기 폭이 곧 뷰포트라 이 문제가 없다. 같은 file:// 출처라
#: ``contentDocument`` 로 안을 잴 수 있다(``--allow-file-access-from-files``).
HOST = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:#888}}</style></head><body>
<iframe id="frame" src="{src}"
 style="width:{width}px;height:{height}px;border:0;display:block"></iframe>
<script>
function measure() {{
  var frame = document.getElementById('frame');
  var win = frame.contentWindow, doc = frame.contentDocument;
  var out = {{
    viewport: win.innerWidth,
    scrollWidth: doc.documentElement.scrollWidth,
    bodyHeight: 0,
    nodes: doc.querySelectorAll('*').length,
    text: (doc.body ? (doc.body.textContent || '').trim().length : 0),
    offenders: []
  }};
  // **본문 높이는 scrollHeight 로 재면 안 된다.** iframe 을 크게 잡을수록
  // 뷰포트가 바닥을 밀어 올려서 iframe 높이가 그대로 나온다. 실제 내용이
  // 어디서 끝나는지는 자식들의 아래끝 중 가장 아래다.
  var kids = doc.body ? doc.body.children : [];
  for (var k = 0; k < kids.length; k++) {{
    var edge = kids[k].getBoundingClientRect().bottom;
    if (edge > out.bodyHeight) {{ out.bodyHeight = Math.round(edge); }}
  }}
  var nodes = doc.querySelectorAll('*');
  for (var i = 0; i < nodes.length; i++) {{
    var node = nodes[i];
    var box = node.getBoundingClientRect();
    if (box.right > win.innerWidth + 0.5 || box.width > win.innerWidth + 0.5) {{
      out.offenders.push({{
        tag: node.tagName,
        right: Math.round(box.right),
        width: Math.round(box.width),
        text: (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50)
      }});
    }}
  }}
  // 가장 바깥 것부터 몇 개만. 부모가 넘치면 자식이 전부 딸려 나온다.
  out.offenders = out.offenders.slice(0, 12);
  document.documentElement.setAttribute('data-report', JSON.stringify(out));
}}
// **readyState 를 믿으면 안 된다.** 갓 만들어진 iframe 의 contentDocument 는
// about:blank 이고 그 readyState 는 이미 'complete' 다. 그래서 "다 됐네" 하고
// 빈 문서를 재게 된다 — 빈 문서는 절대 가로로 안 넘치므로 검사가 언제나
// 통과한다. 실측으로 그렇게 통과했다(노드 3개, 글자 0자).
// load 를 기다리고, 그마저 놓쳤을 때를 대비해 한 번 더 잰다.
document.getElementById('frame').onload = measure;
setTimeout(measure, 1500);
</script></body></html>
"""


#: 브라우저를 띄울 때 늘 붙이는 것. ``--virtual-time-budget`` 이 없으면
#: 렌더가 끝나도 프로세스가 안 죽고 매달린다.
LAUNCH_FLAGS = (
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--no-first-run",
    "--disable-dev-shm-usage",
    "--virtual-time-budget=4000",
)


def find_chromium() -> Path | None:
    """**돌아가는** Chromium 을 고른다. 최신이 아니라.

    캐시에 여러 판이 쌓여 있고 그중 일부는 이 환경에서 뜨지 않는다 —
    실측으로 chromium-1234 는 아무 응답 없이 매달렸고 1187 은 멀쩡했다.
    가장 높은 번호를 그냥 집으면 검사기가 통째로 안 돈다. 그래서 새 것부터
    한 번씩 깨워 보고, 대답하는 첫 번째를 쓴다.
    """
    probe = "<html><body>ok</body></html>"
    for candidate in sorted(CHROMIUM_CACHE.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            page = Path(work) / "probe.html"
            page.write_text(probe, encoding="utf-8")
            try:
                result = subprocess.run(
                    [str(candidate), *LAUNCH_FLAGS,
                     f"--user-data-dir={work}/profile", "--dump-dom", page.as_uri()],
                    capture_output=True, text=True, timeout=30, check=False,
                )
            except subprocess.TimeoutExpired:
                continue
        if "ok" in result.stdout:
            return candidate
    return None


def measure(
    html: str, *, width: int, chrome: Path, shot: Path | None, height: int = 2600
) -> dict[str, object]:
    """``width`` 폭 iframe 안에서 렌더하고 넘침을 잰다."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        page = Path(work) / "page.html"
        page.write_text(html, encoding="utf-8")
        host = Path(work) / "host.html"
        host.write_text(
            HOST.format(src=page.name, width=width, height=height), encoding="utf-8"
        )
        command = [
            str(chrome),
            *LAUNCH_FLAGS,
            "--hide-scrollbars",
            f"--window-size={width + 40},{height + 40}",
            "--allow-file-access-from-files",
            f"--user-data-dir={work}/profile",
        ]
        if shot is not None:
            subprocess.run(
                [*command, f"--screenshot={shot}", host.as_uri()],
                capture_output=True,
                timeout=120,
                check=False,
            )
        result = subprocess.run(
            [*command, "--dump-dom", host.as_uri()],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    marker = 'data-report="'
    start = result.stdout.find(marker)
    if start < 0:
        raise RuntimeError(f"측정기가 결과를 안 남겼다. chromium stderr: {result.stderr[-400:]}")
    start += len(marker)
    end = result.stdout.index('"', start)
    raw = result.stdout[start:end]
    for entity, plain in (("&quot;", '"'), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        raw = raw.replace(entity, plain)
    report = json.loads(raw)
    # **빈 문서는 절대 안 넘친다.** 그래서 아무것도 안 실린 채로 "정상" 이
    # 나오는 것이 이 검사기의 가장 위험한 고장이다 — 조용히 통과시킨다.
    # 실제로 한 번 그랬다(iframe 이 about:blank 인 채로 측정).
    # about:blank 는 노드 3개(html·head·body)에 글자 0자다. 그 언저리만 막는다 —
    # 문턱을 높이면 짧지만 멀쩡한 페이지까지 거부한다.
    if int(report.get("nodes", 0)) < 5 or int(report.get("text", 0)) < 50:
        raise RuntimeError(
            f"iframe 이 비어 있다 (노드 {report.get('nodes')}개, 글자 "
            f"{report.get('text')}자). 이 상태의 '정상' 은 아무 뜻이 없다"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="이메일 HTML 가로 넘침 검사")
    parser.add_argument("files", nargs="+", help="검사할 HTML 파일")
    parser.add_argument("--width", type=int, default=IPHONE_13_PRO)
    parser.add_argument("--shot", help="스크린샷 저장 경로 (첫 파일만)")
    # 세로를 짧게 잡으면 iframe 높이가 그대로 "본문 높이" 로 잡혀 길이를
    # 실제보다 짧게 잰다. 재는 쪽은 넉넉히, 찍는 쪽만 화면만큼.
    parser.add_argument("--height", type=int, default=20000, help="측정용 iframe 높이")
    args = parser.parse_args(argv)

    chrome = find_chromium()
    if chrome is None:
        print(f"Chromium 을 못 찾았다: {CHROMIUM_CACHE}", file=sys.stderr)
        return 2

    failed = False
    for index, name in enumerate(args.files):
        path = Path(name)
        shot = Path(args.shot) if args.shot and index == 0 else None
        report = measure(
            path.read_text(encoding="utf-8"),
            width=args.width,
            chrome=chrome,
            shot=shot,
            height=2600 if shot is not None else args.height,
        )
        viewport = int(report["viewport"])  # type: ignore[call-overload]
        scroll = int(report["scrollWidth"])  # type: ignore[call-overload]
        offenders = report["offenders"]  # type: ignore[index]
        overflow = scroll > viewport
        mark = "넘침" if overflow else "정상"
        height = int(report.get("bodyHeight") or 0)  # type: ignore[union-attr]
        # 아이폰 13 Pro 의 세로 여백은 약 750pt. 몇 번 쓸어내려야 하는지가
        # "길다/짧다" 의 실제 단위다.
        swipes = height / 750 if height else 0
        print(
            f"[{mark}] {path.name}  뷰포트 {viewport}px · 문서 폭 {scroll}px"
            f" · 세로 {height}px (약 {swipes:.1f} 화면)"
        )
        if offenders:
            for item in offenders:  # type: ignore[union-attr]
                print(
                    f"    {item['tag']} width={item['width']} right={item['right']}"
                    f"  {item['text']!r}"
                )
        if overflow:
            failed = True
        if shot is not None:
            print(f"    스크린샷 {shot}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
