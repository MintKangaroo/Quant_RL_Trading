"""트레이딩 화면의 렌더러를 **실제 API 응답으로 한 번 돌려 본다.**

브라우저 없이 잡고 싶은 것은 하나다 — 렌더러가 만지는 DOM 요소가 템플릿에
없는 경우. 화면에서는 이렇게 보인다:

    loadTrading: Cannot set properties of null (setting 'textContent')

그리고 **그 뒤 렌더러가 전부 멈춘다.** 실제로 패널 머리글을 새로 쓰면서
`decision-engine` 을 빠뜨렸고, 워치리스트만 뜨고 나머지가 통째로 비었다.
파이썬 테스트는 200 만 확인하므로 이 부류를 못 잡는다.

node 가 없으면 건너뛴다 — 이 검사 하나 때문에 테스트가 못 도는 것이
더 나쁘다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "quant_rl_trading" / "dashboard" / "static"
TEMPLATE = REPO_ROOT / "quant_rl_trading" / "dashboard" / "templates" / "trading.html"
SCOPE_TEMPLATE = REPO_ROOT / "quant_rl_trading" / "dashboard" / "templates" / "_scope.html"

#: 렌더러가 부르는 DOM API 만 흉내 낸다. 요소가 템플릿에 있으면 객체를,
#: 없으면 **null 을** 돌려준다 — 브라우저와 같은 실패를 재현하기 위해서다.
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
    addEventListener() {},
  };
}
const cache = {};
global.document = {
  getElementById(id) {
    if (!ids.has(id) && !made.has(id)) return null;   // 브라우저와 같다
    return (cache[id] = cache[id] || element(id));
  },
  addEventListener() {},
};
global.window = { location: { search: "" }, addEventListener() {} };
global.echarts = { init: () => ({ setOption() {}, resize() {} }) };
global.fetch = async () => { throw new Error("fetch 는 스텁이 가로챈다"); };
""" + style_shim()

DRIVER = """
// scope.js 가 선언한 함수를 우리 것으로 갈아 끼운다. `global.` 로 얹으면
// 같은 이름의 함수 선언이 이겨서(호이스팅) 스텁이 안 걸린다.
fetchJson = async (path) => (path.startsWith("trading/chart") ? CHART : TRADING);
runAll = async (jobs) => { for (const job of jobs) await job(); };
loadTrading().then(
  () => {
    // 렌더 결과를 내보낸다. "끝까지 돌았다" 와 "옳은 값을 그렸다" 는 다른
    // 질문이고, 후자를 못 물으면 숫자가 조용히 틀려도 통과한다.
    const dump = {};
    for (const id of Object.keys(cache)) dump[id] = cache[id].innerHTML;
    console.log("DUMP " + JSON.stringify(dump));
    console.log("OK");
  },
  (error) => { console.log("FAIL " + error.message); process.exitCode = 1; }
);
"""


def _render(tmp_path: Path, trading: dict, chart: dict) -> dict[str, str]:
    """렌더러를 한 번 돌리고 요소별 innerHTML 을 돌려준다."""
    import re

    ids = sorted(
        set(re.findall(r'id="([^"]+)"', TEMPLATE.read_text()))
        | set(re.findall(r'id="([^"]+)"', SCOPE_TEMPLATE.read_text()))
    )
    trading_js = (STATIC / "trading.js").read_text().replace("runAll([loadTrading]);", "")
    script = "\n".join([
        HARNESS.replace("IDS", json.dumps(ids)),
        (STATIC / "scope.js").read_text(),
        (STATIC / "calendar.js").read_text(),
        (STATIC / "candles.js").read_text(),
        trading_js,
        DRIVER.replace("TRADING", json.dumps(trading)).replace("CHART", json.dumps(chart)),
    ])
    path = tmp_path / "render.js"
    path.write_text(script)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert "OK" in result.stdout, f"{result.stdout}\n{result.stderr}"
    line = next(row for row in result.stdout.splitlines() if row.startswith("DUMP "))
    return json.loads(line[len("DUMP "):])


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_트레이딩_렌더러가_실제_응답으로_끝까지_돈다(tmp_path: Path) -> None:
    payloads = Path(__file__).parent / "payloads"
    trading = json.loads((payloads / "trading.json").read_text())
    chart = json.loads((payloads / "chart.json").read_text())

    import re

    # 화면은 공통 헤더(_scope.html)와 함께 뜬다. 한쪽만 세면 헤더의 요소가
    # 없는 것으로 잡혀 엉뚱한 곳에서 실패한다.
    ids = sorted(
        set(re.findall(r'id="([^"]+)"', TEMPLATE.read_text()))
        | set(re.findall(r'id="([^"]+)"', SCOPE_TEMPLATE.read_text()))
    )
    scope = (STATIC / "scope.js").read_text()
    trading_js = (STATIC / "trading.js").read_text().replace(
        "runAll([loadTrading]);", ""
    )

    script = "\n".join([
        HARNESS.replace("IDS", json.dumps(ids)),
        scope,
        # trading.html 이 trading.js 보다 먼저 싣는다. 빠뜨리면 브라우저에서는
        # 되는데 테스트만 ReferenceError 로 죽는다.
        (STATIC / "calendar.js").read_text(),
        (STATIC / "candles.js").read_text(),
        trading_js,
        DRIVER.replace("TRADING", json.dumps(trading)).replace("CHART", json.dumps(chart)),
    ])
    path = tmp_path / "render.js"
    path.write_text(script)

    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, timeout=60
    )
    assert "OK" in result.stdout, f"{result.stdout}\n{result.stderr}"


# -- 장 마감 후 (2026-08-19) ---------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_장_마감_후에는_오늘_수익을_종가로_잡는다(tmp_path: Path) -> None:
    """**0.00% 는 "안 움직였다" 가 아니라 "아직 모른다" 였다.**

    일봉 수집은 장이 끝난 뒤에 돈다. 그 사이 창고 기준 `nav`·`today_pnl` 은
    아직 어제를 가리키고, 그때 실시간 값을 버리면 화면이 오늘 수익률을
    0.00% 로 보여준다. 실측 2026-08-19 16:22(shadow): 실제 -1.13% 였다.

    마감 후의 마지막 체결가는 곧 오늘 종가이므로, 그 값을 써야 한다.
    """
    payloads = Path(__file__).parent / "payloads"
    trading = json.loads((payloads / "trading.json").read_text())
    chart = json.loads((payloads / "chart.json").read_text())

    # 장이 닫힌 상태로 바꾼다. 회계는 아직 어제라 today_pnl 이 0 이다.
    kpis = trading["data"]["kpis"]
    kpis["live_session_open"] = False
    kpis["live_is_close"] = True
    kpis["live_today_pnl"] = -111690.0
    kpis["live_change"] = -0.0113
    kpis["today_pnl"] = 0.0
    kpis["daily_return"] = 0.0

    dump = _render(tmp_path, trading, chart)
    kpi_html = "".join(dump.values())

    # 오늘 수익금이 0 이 아니라 장중 마지막 값으로 찍혀야 한다.
    assert "111,690" in kpi_html, "마감 후 오늘 수익금이 종가로 안 잡힌다"
    # 종가라는 사실을 화면이 말해야 한다 — 안 적으면 어제 값과 구분이 안 된다.
    assert "종가" in kpi_html
