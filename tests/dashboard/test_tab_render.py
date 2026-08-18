"""새 탭들의 렌더러를 **실제 API 응답으로 한 번씩 돌려 본다.**

`test_trading_render.py` 와 같은 목적이고, 대상만 여러 탭이다. 잡고 싶은 것은
렌더러가 만지는 DOM 요소가 템플릿에 없는 경우다. 화면에서는 이렇게 보인다:

    loadMarket: Cannot set properties of null (setting 'textContent')

그리고 **그 뒤 렌더러가 전부 멈춘다.** API 는 200 을 내므로 파이썬 테스트로는
안 잡힌다. 실제로 이 프로젝트는 패널 머리글을 새로 쓰다가 요소 하나를 빠뜨려
화면 절반이 빈 적이 있다.

payloads/<탭>.json 은 **진짜 창고에서 뜬 응답**이다. 손으로 지어내면 화면이
못 받는 모양을 테스트만 통과시킨다. 다시 뜨려면:
    python tools/capture_tab_payloads.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "quant_rl_trading" / "dashboard" / "static"
TEMPLATES = REPO_ROOT / "quant_rl_trading" / "dashboard" / "templates"
PAYLOADS = Path(__file__).parent / "payloads"

#: (탭, 템플릿, 스크립트, 먼저 실을 스크립트). 탭을 늘리면 여기 한 줄을 더한다.
#: 네 번째 칸은 템플릿이 그 스크립트보다 **앞에서** 부르는 것들이다 — 빠뜨리면
#: 브라우저에서는 되는데 테스트만 ReferenceError 로 죽는다.
TABS = [
    ("market", "market.html", "market.js", ()),
    ("headlines", "headlines.html", "headlines.js", ()),
    ("system", "system.html", "system.js", ()),
    ("learning", "learning.html", "learning.js", ()),
    ("ai_review", "ai_review.html", "ai_review.js", ()),
    ("calendar_page", "calendar.html", "calendar_page.js", ("calendar.js",)),
]

from tests.dashboard._browser import style_shim

HARNESS = """
const ids = new Set(IDS);
const made = new Set();          // JS 가 innerHTML 로 만들어 넣는 요소
function element(id) {
  return {
    id,
    _html: "",
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    style: {},
    hidden: false,
    set innerHTML(value) {
      this._html = String(value);
      for (const m of this._html.matchAll(/id="([^"]+)"/g)) made.add(m[1]);
    },
    get innerHTML() { return this._html; },
    set textContent(value) { this._text = String(value); },
    get textContent() { return this._text || ""; },
    querySelectorAll: () => [],
    querySelector: () => null,
    appendChild() {},
    addEventListener() {},
  };
}
const cache = {};
global.document = {
  getElementById(id) {
    if (!ids.has(id) && !made.has(id)) return null;   // 브라우저와 같다
    return (cache[id] = cache[id] || element(id));
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: () => element("made"),
  addEventListener() {},
};
global.window = {
  location: { search: "" },
  addEventListener() {},
  setInterval() { return 0; },      // 자동 갱신은 여기서 돌 이유가 없다
  clearInterval() {},
  open() { return null; },
};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.echarts = { init: () => ({ setOption() {}, resize() {}, on() {} }) };
global.fetch = async () => { throw new Error("fetch 는 스텁이 가로챈다"); };
""" + style_shim()

#: 마지막 ``runAll([...]);`` 를 걷어내고 그 목록을 우리가 직접 돌린다.
#: `global.` 로 얹으면 같은 이름의 함수 선언이 호이스팅으로 이겨서 스텁이 안 걸린다.
DRIVER = """
fetchJson = async (path) => {
  const key = path.split("?")[0];
  if (!(key in PAYLOADS)) throw new Error("페이로드에 없는 경로: " + key);
  return PAYLOADS[key];
};
runAll = async (jobs) => { for (const job of jobs) await job(); };
(async () => { await runAll([JOBS]); })().then(
  () => console.log("OK"),
  (error) => { console.log("FAIL " + error.message); process.exitCode = 1; }
);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
@pytest.mark.parametrize(("tab", "template", "script", "preload"), TABS)
def test_탭_렌더러가_실제_응답으로_끝까지_돈다(
    tab: str, template: str, script: str, preload: tuple[str, ...], tmp_path: Path
) -> None:
    payloads = json.loads((PAYLOADS / f"{tab}.json").read_text(encoding="utf-8"))

    # 화면은 공통 헤더(_scope.html)와 함께 뜬다. 한쪽만 세면 헤더의 요소가
    # 없는 것으로 잡혀 엉뚱한 곳에서 실패한다.
    ids = sorted(
        set(re.findall(r'id="([^"]+)"', (TEMPLATES / template).read_text()))
        | set(re.findall(r'id="([^"]+)"', (TEMPLATES / "_scope.html").read_text()))
    )

    source = (STATIC / script).read_text()
    match = re.search(r"runAll\(\[([^\]]*)\]\)\s*;", source)
    assert match, f"{script} 에 runAll([...]) 진입점이 없다"
    jobs = match.group(1).strip()
    source = source[: match.start()] + source[match.end() :]

    js = "\n".join([
        HARNESS.replace("IDS", json.dumps(ids)),
        (STATIC / "scope.js").read_text(),
        *[(STATIC / name).read_text() for name in preload],
        source,
        DRIVER.replace("PAYLOADS", json.dumps(payloads)).replace("JOBS", jobs),
    ])
    path = tmp_path / f"{tab}.js"
    path.write_text(js, encoding="utf-8")

    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert "OK" in result.stdout, f"{result.stdout}\n{result.stderr}"
