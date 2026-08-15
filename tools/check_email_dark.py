"""이메일 HTML 에 배경 없는 칸이 있는지 검사한다.

    uv run python tools/check_email_dark.py logs/briefing/briefing_*.html

## 이 검사기가 잡는 것과 안 잡는 것

**이건 Gmail 다크모드 대응 도구가 아니다.** 2026-08-15 실기기 확인으로
"배경이 흰색으로 보인다" 는 신고의 실제 원인이 밝혀졌다 — Gmail 앱이 이
메일을 "라이트로 디자인됐다" 고 가정하고 자기 다크모드 변환(명도 반전)을
적용한 것이었다. 아이폰 기본 메일에서는 처음부터 의도대로 나왔다. Gmail 의
그 변환은 인라인 스타일로 못 막는다 — 이 저장소는 더 이상 그걸 이기려
하지 않는다(``quant_rl_trading/reporting/render.py`` 모듈 독스트링 참고).

그렇다고 이 검사기가 쓸모없어진 건 아니다. **배경 선언이 통째로 빠진 칸**은
Gmail 과 무관하게 어느 클라이언트에서든 문제다 — 실제로 이 메일에도 한 번
있었다(카드를 감싸는 바깥 여백 ``<td>``, 지금은 조상 테이블의 배경이 자연히
비쳐서 문제없지만 그런 우연에 기대는 코드는 다음에도 안전하다는 보장이
없다). 그래서 남긴다. 두 가지를 본다.

1. **정적 검사** — CSS 에도 배경 선언이 전혀 없는 ``<table>``/``<td>`` 를
   소스에서 찾는다. 조상이 배경을 갖고 있어 지금 당장은 안전할 수 있지만,
   그 칸만 떼어 다른 맥락에 옮겨 써도 안전한지는 보장 못 한다는 신호다
2. **body 제거 시뮬레이션** — ``<body ...>`` 의 속성을 지운 사본을 만들어
   같이 렌더하고, 뷰포트를 격자로 훑어 "배경을 상속받을 조상이 하나도
   없는" 지점(=아무 클라이언트 기본색이든 비칠 수 있는 자리)이 있는지 잰다.
   이건 Gmail 이 배경을 뒤집는 것과는 다른 이야기다 — 일부 클라이언트가
   ``<body>`` 태그 자체를 통째로 들어내는 것(속성 반전이 아니라 제거)에
   대한 방어선이다

**판정 기준은 "배경 선언이 구조적으로 있는가" 다. "Gmail 에서 어떻게
보이는가" 가 아니다.** 후자는 이 도구로 검증할 수 없다 — Gmail 의 변환은
소스가 아니라 클라이언트 쪽 알고리즘이라, HTML 을 렌더해서는 안 잡힌다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# check_email_width.py 의 Chromium 탐색기를 그대로 쓴다 — 두 벌로 두면
# "실행되는 Chromium 판" 목록이 갈릴 수 있다 (tools/__init__.py 로 패키지다).
from tools.check_email_width import LAUNCH_FLAGS, find_chromium  # noqa: E402

IPHONE_13_PRO = 390

_OPEN_TAG = re.compile(r"<(table|td)\b([^>]*)>", re.IGNORECASE)
_STYLE_BG = re.compile(r"background-color\s*:", re.IGNORECASE)


def static_findings(html: str) -> list[str]:
    """소스 텍스트만으로 잡을 수 있는 것 — 렌더가 필요 없다.

    CSS ``background-color`` 조차 없는 ``<table>``/``<td>`` 만 문제로 본다.
    ``bgcolor`` HTML 속성 유무는 더 이상 보지 않는다 — 그건 Gmail 을 이기려던
    시도의 일부였고 되돌렸다 (모듈 독스트링 참고).
    """
    problems: list[str] = []
    for match in _OPEN_TAG.finditer(html):
        tag, attrs = match.group(1).lower(), match.group(2)
        if _STYLE_BG.search(attrs) is None:
            snippet = match.group(0)[:90]
            problems.append(f"배경 선언이 아예 없다: <{tag}> {snippet}")
    return problems


def strip_body_style(html: str) -> str:
    """``<body ...>`` 의 속성을 전부 지운다 — 일부 클라이언트가 body 태그를
    통째로 들어내는 것을 흉내낸다."""
    return re.sub(r"<body\b[^>]*>", "<body>", html, count=1, flags=re.IGNORECASE)


#: 측정용 틀. check_email_width.py 의 iframe 트릭을 그대로 쓴다 — 이유는
#: 그 파일의 HOST 독스트링 참고(윈도우 크기가 아니라 iframe 폭이 뷰포트다).
HOST = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:#888}}</style></head><body>
<iframe id="frame" src="{src}"
 style="width:{width}px;height:{height}px;border:0;display:block"></iframe>
<script>
function isTransparent(color) {{
  if (!color) return true;
  var m = color.match(/rgba?\\(([^)]+)\\)/);
  if (!m) return color === 'transparent';
  var parts = m[1].split(',').map(function(s) {{ return parseFloat(s); }});
  return parts.length === 4 && parts[3] === 0;
}}
function measure() {{
  var frame = document.getElementById('frame');
  var win = frame.contentWindow, doc = frame.contentDocument;
  var out = {{ nodes: doc.querySelectorAll('*').length,
    text: (doc.body ? (doc.body.textContent || '').trim().length : 0),
    holes: [], sampled: 0 }};
  var kids = doc.body ? doc.body.children : [];
  var bottom = 0;
  for (var k = 0; k < kids.length; k++) {{
    var edge = kids[k].getBoundingClientRect().bottom;
    if (edge > bottom) bottom = edge;
  }}
  bottom = Math.min(bottom, {max_height});
  var step = 28;
  for (var y = 4; y < bottom; y += step) {{
    for (var x = 4; x < win.innerWidth; x += step) {{
      out.sampled++;
      var el = doc.elementFromPoint(x, y);
      if (!el) continue;
      var protected_ = false;
      var node = el;
      var depth = 0;
      while (node && depth < 30) {{
        var bg = win.getComputedStyle(node).backgroundColor;
        if (!isTransparent(bg)) {{ protected_ = true; break; }}
        node = node.parentElement;
        depth++;
      }}
      if (!protected_) {{
        out.holes.push({{
          x: x, y: y, tag: el.tagName,
          text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 40)
        }});
      }}
    }}
  }}
  document.documentElement.setAttribute('data-report', JSON.stringify(out));
}}
document.getElementById('frame').onload = measure;
setTimeout(measure, 1500);
</script></body></html>
"""


def render_scan(
    html: str, *, width: int, chrome: Path, height: int = 4200
) -> dict[str, object]:
    """``width`` 뷰포트로 렌더하고 배경 없는 지점을 격자로 찾는다."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        page = Path(work) / "page.html"
        page.write_text(html, encoding="utf-8")
        host = Path(work) / "host.html"
        host.write_text(
            HOST.format(src=page.name, width=width, height=height, max_height=height),
            encoding="utf-8",
        )
        command = [
            str(chrome), *LAUNCH_FLAGS, "--hide-scrollbars",
            f"--window-size={width + 40},{height + 40}",
            "--allow-file-access-from-files",
            f"--user-data-dir={work}/profile",
        ]
        result = subprocess.run(
            [*command, "--dump-dom", host.as_uri()],
            capture_output=True, text=True, timeout=120, check=False,
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
    if int(report.get("nodes", 0)) < 5 or int(report.get("text", 0)) < 50:
        raise RuntimeError(
            f"iframe 이 비어 있다 (노드 {report.get('nodes')}개, 글자 "
            f"{report.get('text')}자). 이 상태의 '정상' 은 아무 뜻이 없다"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="이메일 HTML 배경 선언 검사")
    parser.add_argument("files", nargs="+", help="검사할 HTML 파일")
    parser.add_argument("--width", type=int, default=IPHONE_13_PRO)
    parser.add_argument("--max-holes", type=int, default=8, help="점당 자세히 찍을 상한")
    args = parser.parse_args(argv)

    chrome = find_chromium()
    if chrome is None:
        print("Chromium 을 못 찾았다", file=sys.stderr)
        return 2

    failed = False
    for name in args.files:
        path = Path(name)
        html = path.read_text(encoding="utf-8")
        print(f"== {path.name} ==")

        statics = static_findings(html)
        if statics:
            failed = True
            print(f"  [정적 검사] {len(statics)}건")
            for problem in statics[:20]:
                print(f"    - {problem}")
        else:
            print("  [정적 검사] 배경 없는 table/td 없음")

        original = render_scan(html, width=args.width, chrome=chrome)
        stripped = render_scan(strip_body_style(html), width=args.width, chrome=chrome)

        orig_holes = original["holes"]  # type: ignore[index]
        strip_holes = stripped["holes"]  # type: ignore[index]
        print(
            f"  [렌더 — 원본] 표본 {original['sampled']}점 중 배경 없는 지점 "
            f"{len(orig_holes)}개"  # type: ignore[arg-type]
        )
        if orig_holes:
            failed = True
            for hole in orig_holes[: args.max_holes]:  # type: ignore[index]
                print(f"      ({hole['x']},{hole['y']}) <{hole['tag']}> {hole['text']!r}")

        print(
            f"  [렌더 — body 벗김] 표본 {stripped['sampled']}점 중 배경 없는 지점 "
            f"{len(strip_holes)}개"  # type: ignore[arg-type]
        )
        if strip_holes:
            new_holes = [
                h for h in strip_holes  # type: ignore[union-attr]
                if not any(o["x"] == h["x"] and o["y"] == h["y"] for o in orig_holes)  # type: ignore[union-attr]
            ]
            if new_holes:
                failed = True
                print(f"    body 배경에만 기대던 자리 {len(new_holes)}개:")
                for hole in new_holes[: args.max_holes]:
                    print(f"      ({hole['x']},{hole['y']}) <{hole['tag']}> {hole['text']!r}")

        if not statics and not orig_holes and not strip_holes:
            print("  [판정] 정상 — body 를 지워도 배경이 남는다")
        else:
            print("  [판정] 결함 있음")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
