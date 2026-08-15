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
"""

DRIVER = """
// scope.js 가 선언한 함수를 우리 것으로 갈아 끼운다. `global.` 로 얹으면
// 같은 이름의 함수 선언이 이겨서(호이스팅) 스텁이 안 걸린다.
fetchJson = async (path) => (path.startsWith("trading/chart") ? CHART : TRADING);
runAll = async (jobs) => { for (const job of jobs) await job(); };
loadTrading().then(
  () => console.log("OK"),
  (error) => { console.log("FAIL " + error.message); process.exitCode = 1; }
);
"""


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
        trading_js,
        DRIVER.replace("TRADING", json.dumps(trading)).replace("CHART", json.dumps(chart)),
    ])
    path = tmp_path / "render.js"
    path.write_text(script)

    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, timeout=60
    )
    assert "OK" in result.stdout, f"{result.stdout}\n{result.stderr}"


# -- 지수 대비: 누적 초과 곡선 ---------------------------------------------------


def _render_with(payload: dict, tmp_path: Path) -> dict[str, str]:
    """주어진 응답으로 트레이딩 렌더러를 돌리고 각 요소의 innerHTML 을 돌려준다.

    기본 페이로드(`trading.json`)는 창고가 얕아 **벤치마크가 전부
    ``available: False``** 다 — 그것만으로는 곡선 경로가 한 줄도 안 돈다.
    그래서 벤치마크가 실제로 찬 응답을 따로 물려 이 검사에만 쓴다.
    """
    import re

    ids = sorted(
        set(re.findall(r'id="([^"]+)"', TEMPLATE.read_text()))
        | set(re.findall(r'id="([^"]+)"', SCOPE_TEMPLATE.read_text()))
    )
    chart = json.loads((Path(__file__).parent / "payloads" / "chart.json").read_text())
    trading_js = (STATIC / "trading.js").read_text().replace("runAll([loadTrading]);", "")
    driver = """
fetchJson = async (path) => (path.startsWith("trading/chart") ? CHART : TRADING);
runAll = async (jobs) => { for (const job of jobs) await job(); };
loadTrading().then(
  () => {
    const dump = {};
    for (const [id, el] of Object.entries(cache)) dump[id] = el.innerHTML;
    console.log("DUMP " + JSON.stringify(dump));
  },
  (error) => { console.log("FAIL " + error.message); process.exitCode = 1; }
);
"""
    script = "\n".join([
        HARNESS.replace("IDS", json.dumps(ids)),
        (STATIC / "scope.js").read_text(),
        (STATIC / "calendar.js").read_text(),
        trading_js,
        driver.replace("TRADING", json.dumps(payload)).replace("CHART", json.dumps(chart)),
    ])
    path = tmp_path / "bench.js"
    path.write_text(script)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    line = next((row for row in result.stdout.splitlines() if row.startswith("DUMP ")), None)
    assert line, f"{result.stdout}\n{result.stderr}"
    return json.loads(line[len("DUMP "):])


def _bench_payload() -> dict:
    return json.loads(
        (Path(__file__).parent / "payloads" / "trading_benchmark.json").read_text()
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_지수_대비는_일별_막대가_아니라_누적_곡선이다(tmp_path: Path) -> None:
    """이 전략은 저베타라(베타 0.131 · 상승일 포착률 14%) 상승장에서 일별
    초과가 음수인 날이 줄줄이 나오는 것이 **정상 동작**이다. 막대로 그리면
    화면이 매일 "졌다" 고 스무 번 외치고, 그 빨강은 같은 패널 캘린더의
    빨강(**진짜 손실**)과 같은 색이라 한 패널에서 두 뜻이 된다.
    """
    dump = _render_with(_bench_payload(), tmp_path)["bench-compare"]
    assert "bench-curve" in dump, "곡선이 안 그려졌다"
    assert "bench-daily" not in dump and 'class="bar' not in dump, "일별 막대가 남아 있다"
    # 0선이 있어야 "앞섰나 뒤졌나" 를 곡선 모양만으로 읽을 수 있다.
    assert 'class="zero"' in dump


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_곡선의_끝점이_머리의_누적_초과와_같다(tmp_path: Path) -> None:
    """**다르면 그게 결함이다.** 실제로 어긋나 있었다 — 서비스가
    ``cumulative_index`` 는 지수 자기 거래일로, ``daily`` 는 우리 거래일로
    만들어서 국장에서 3.6~4.4%p 벌어졌다. 축을 두 달력의 합집합으로 바꿔
    맞췄다.
    """
    for row in _bench_payload()["data"]["benchmark_compare"]["benchmarks"]:
        if not row.get("available"):
            continue
        assert row["daily"], row["label"]
        assert row["daily"][-1]["cumulative_excess"] == pytest.approx(
            row["cumulative_excess"], abs=1e-12
        ), f"{row['label']} 곡선 끝점이 머리 숫자와 다르다"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_호버가_우리_지수_누적초과_셋을_다_보여준다(tmp_path: Path) -> None:
    """일별 초과가 아니라 **누적**이다."""
    dump = _render_with(_bench_payload(), tmp_path)["bench-compare"]
    assert "누적 초과" in dump
    assert "우리 " in dump and "지수 " in dump


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_양쪽이_다_관측되지_않은_날을_지우지_않는다(tmp_path: Path) -> None:
    """한쪽이 없는 날은 그쪽 누적이 안 움직인다 — 즉 "그날 그쪽 수익률이 0"
    이라고 가정한 셈이다. 휴장이면 맞고 미수집이면 틀린데 둘을 못 가르므로,
    **가정했다는 사실 자체**를 화면이 말해야 한다."""
    payload = _bench_payload()
    missing = [
        sum(1 for d in row["daily"] if not d["paired"])
        for row in payload["data"]["benchmark_compare"]["benchmarks"]
        if row.get("available")
    ]
    assert any(missing), "이 페이로드에는 미대응 날이 없다 — 검사가 무의미하다"
    dump = _render_with(payload, tmp_path)["bench-compare"]
    assert 'class="gap"' in dump, "미대응 날이 점으로 안 찍혔다"
    assert "한쪽이 안 움직인 것으로 잰다" in dump, "가정을 화면이 안 말한다"
