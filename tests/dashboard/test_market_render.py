"""마켓 화면 — **좌우 두 칸이 각자 자기 시장으로 찬다.**

`test_tab_render.py` 가 잡는 것은 "렌더러가 끝까지 도는가" 다. 이 화면에는
그것만으로 안 잡히는 고장이 하나 더 있다 — **두 칸이 같은 시장을 그리는
것.** 좌우로 나눈 화면은 id 접미사(`-kr` / `-us`)를 손으로 두 벌 적으므로,
복사한 줄에서 접미사 하나를 안 고치면 미장 칸에 국장 표가 뜬다. API 는
200 이고 렌더러도 안 죽는다. 화면만 조용히 거짓말한다.

그래서 여기서는 렌더러를 돌린 **뒤 DOM 을 들여다본다**:

1. 두 칸의 패널이 전부 뭔가로 찼는가 (빈 칸이면 접미사를 놓친 것이다)
2. 두 칸이 **서로 다른** 내용인가
3. 미장처럼 데이터가 없는 칸은 **없는 이유**를 적었는가 (빈 채로 두지 않는다)

node 가 없으면 건너뛴다 — 이 검사 하나 때문에 테스트가 못 도는 것이 더 나쁘다.
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

#: 좌우 두 칸에 짝으로 존재해야 하는 패널들. 한쪽에만 있는 패널을 만들면
#: 화면의 좌우 밀도가 갈라진다 — 그 자체가 이 작업이 고치려던 문제다.
PAIRED = ("index-head", "indices", "breadth", "movers", "leaders", "macro")

HARNESS = """
const ids = new Set(IDS);
const made = new Set();
const cache = {};
function element(id) {
  return {
    id, _html: "", _text: "", dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    style: {}, hidden: false,
    set innerHTML(value) {
      this._html = String(value);
      for (const m of this._html.matchAll(/id="([^"]+)"/g)) made.add(m[1]);
    },
    get innerHTML() { return this._html; },
    set textContent(value) { this._text = String(value); },
    get textContent() { return this._text || ""; },
    querySelectorAll: () => [],
    querySelector: () => null,
    appendChild() {}, addEventListener() {},
  };
}
global.document = {
  getElementById(id) {
    if (!ids.has(id) && !made.has(id)) return null;   // 브라우저와 같다
    return (cache[id] = cache[id] || element(id));
  },
  querySelectorAll: () => [], querySelector: () => null,
  createElement: () => element("made"), addEventListener() {},
};
global.window = {
  location: { search: "" }, addEventListener() {},
  setInterval() { return 0; }, clearInterval() {}, open() { return null; },
};
global.setInterval = () => 0;
global.clearInterval = () => {};
/* 트리맵은 캔버스 안에 그려져 innerHTML 로는 안 보인다. 무엇을 받았는지만
   붙잡아 둔다 — 두 칸이 같은 데이터를 그리는 사고를 여기서도 잡는다. */
const drawn = {};
global.echarts = {
  init: (el) => ({
    setOption(option) { drawn[el.id] = option; },
    resize() {}, on() {},
  }),
};
global.fetch = async () => { throw new Error("fetch 는 스텁이 가로챈다"); };
"""

DRIVER = """
// 괄호가 필요하다 — 화살표 함수 뒤의 중괄호는 객체가 아니라 본문으로 읽힌다.
fetchJson = async () => (PAYLOAD);
runAll = async (jobs) => { for (const job of jobs) await job(); };
(async () => { await runAll([JOBS]); })().then(
  () => {
    const dump = {};
    for (const [id, el] of Object.entries(cache))
      dump[id] = el.innerHTML + "\\u0000" + el.textContent;
    for (const [id, option] of Object.entries(drawn)) {
      dump["chart:" + id] = JSON.stringify((option.series || []).map((s) => s.data || []));
      // 이름·선 모양은 data 에 안 담긴다 — 상단 지수 차트는 "무엇을 그렸나" 와
      // "어느 선이 대표인가" 를 이걸로 잡는다.
      dump["series:" + id] = JSON.stringify(
        (option.series || []).map((s) => ({ name: s.name, lineStyle: s.lineStyle || {} }))
      );
    }
    console.log("DUMP " + JSON.stringify(dump));
  },
  (error) => { console.log("FAIL " + error.message); process.exitCode = 1; }
);
"""


def _payload() -> dict:
    """렌더러에 먹인 것과 **같은** 응답. 화면의 빈 칸이 "데이터가 없어서" 인지
    "렌더러가 놓쳐서" 인지는 이걸 같이 봐야 갈린다."""
    return json.loads((PAYLOADS / "market.json").read_text(encoding="utf-8"))["market"]


def _render() -> dict[str, str]:
    payload = _payload()
    ids = sorted(
        set(re.findall(r'id="([^"]+)"', (TEMPLATES / "market.html").read_text(encoding="utf-8")))
        | set(re.findall(r'id="([^"]+)"', (TEMPLATES / "_scope.html").read_text(encoding="utf-8")))
    )
    source = (STATIC / "market.js").read_text(encoding="utf-8")
    match = re.search(r"runAll\(\[([^\]]*)\]\)\s*;", source)
    assert match, "market.js 에 runAll([...]) 진입점이 없다"
    jobs = match.group(1).strip()
    source = source[: match.start()] + source[match.end() :]

    js = "\n".join([
        HARNESS.replace("IDS", json.dumps(ids)),
        (STATIC / "scope.js").read_text(encoding="utf-8"),
        source,
        DRIVER.replace("PAYLOAD", json.dumps(payload)).replace("JOBS", jobs),
    ])
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "market_render.js"
        path.write_text(js, encoding="utf-8")
        result = subprocess.run(
            ["node", str(path)], capture_output=True, text=True, timeout=60
        )
    line = next(
        (row for row in result.stdout.splitlines() if row.startswith("DUMP ")), None
    )
    assert line, f"{result.stdout}\n{result.stderr}"
    return json.loads(line[len("DUMP "):])


@pytest.fixture(scope="module")
def rendered() -> dict[str, str]:
    if shutil.which("node") is None:
        pytest.skip("node 가 없다")
    return _render()


@pytest.mark.parametrize("panel", PAIRED)
def test_두_칸이_짝으로_찬다(rendered: dict[str, str], panel: str) -> None:
    """접미사(-kr/-us)를 하나 놓치면 그 칸이 통째로 빈다."""
    for suffix in ("kr", "us"):
        key = f"{panel}-{suffix}"
        assert key in rendered, f"{key} 에 아무것도 안 그렸다 — 템플릿의 id 를 확인할 것"
        assert rendered[key].strip("\x00").strip(), f"{key} 가 비었다"


@pytest.mark.parametrize("panel", PAIRED)
def test_두_칸이_서로_다른_시장을_그린다(rendered: dict[str, str], panel: str) -> None:
    """복사한 줄에서 접미사를 안 고치면 미장 칸에 국장 표가 뜬다.
    API 는 200 이고 렌더러도 안 죽는다 — 화면만 조용히 거짓말한다."""
    assert rendered[f"{panel}-kr"] != rendered[f"{panel}-us"]


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_트리맵은_시장마다_따로_그린다(rendered: dict[str, str], suffix: str) -> None:
    """트리맵은 캔버스라 innerHTML 로는 안 보인다 — setOption 을 붙잡아 본다.

    원·달러 시총을 한 맵에 섞지 않는다(맵 둘). 시총이 아직 없는 시장은 그림
    대신 **이유**를 적는다 — 빈 사각형으로 두면 조인 버그처럼 보인다.
    """
    caps = _payload()["data"]["markets"][suffix.upper()]["treemap"]["rows"]
    if caps:
        assert rendered.get(f"chart:chart-treemap-{suffix}"), "맵이 안 그려졌다"
    else:
        assert f"chart:chart-treemap-{suffix}" not in rendered
        assert "상장주식수" in rendered[f"chart-treemap-{suffix}"]


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_시가총액이_없으면_이유를_적는다(rendered: dict[str, str], suffix: str) -> None:
    """미장 시가총액은 오래 0행이었다(상장주식수 수집기가 없어서). 빈 표로 두면
    조인 버그처럼 보인다 — 화면이 이유를 말해야 엉뚱한 데를 파지 않는다."""
    market = _payload()["data"]["markets"][suffix.upper()]
    if market["leaders"]:
        assert "상장주식수" not in rendered[f"leaders-{suffix}"]
    else:
        assert "상장주식수" in rendered[f"leaders-{suffix}"]


def test_상단_지수_차트는_한_번만_그린다(rendered: dict[str, str]) -> None:
    """대표 지수 넷은 좌우로 갈리기 전의 공통 줄이다 — 칸 안에 넣으면 "어느
    시장이 앞서 가나" 를 두 그림을 번갈아 보며 풀어야 한다."""
    assert rendered.get("chart:chart-indices"), "상단 지수 차트가 안 그려졌다"
    assert "chart:chart-indices-kr" not in rendered
    assert "chart:chart-indices-us" not in rendered


def test_상단_차트가_창고에_있는_대표_지수를_다_그린다(rendered: dict[str, str]) -> None:
    """서비스가 실은 시계열 수와 그린 선의 수가 같아야 한다. 하나를 흘리면
    화면은 멀쩡해 보이는데 지수 하나가 조용히 사라진다."""
    expected = _payload()["data"]["index_chart"]["series"]
    drawn = json.loads(rendered["series:chart-indices"])
    assert len(drawn) == len(expected)
    for series, spec in zip(drawn, expected):
        assert spec["entity_id"].endswith(series["name"]), (series["name"], spec["entity_id"])


def test_대표는_실선_곁_지수는_점선(rendered: dict[str, str]) -> None:
    """어느 선이 `config.benchmark` 가 정한 지수인지는 선 모양이 말한다 —
    색은 시장(국장·미장)에 이미 쓰였다."""
    expected = _payload()["data"]["index_chart"]["series"]
    drawn = json.loads(rendered["series:chart-indices"])
    for series, spec in zip(drawn, expected):
        dashed = series["lineStyle"].get("type") == "dashed"
        assert dashed == (spec["role"] != "headline"), series["name"]


def test_정규화했다는_사실을_화면이_말한다(rendered: dict[str, str]) -> None:
    """기준일 100 정규화라 120 은 지수 포인트가 아니다. 화면이 그 말을 안 하면
    사람이 코스피가 120 이라고 읽는다."""
    chart = _payload()["data"]["index_chart"]
    note = rendered["index-chart-note"]
    assert "정규화" in note
    assert chart["base_session"] in note

    # 정규화가 지운 절대 수준은 범례가 되돌려 준다.
    legend = rendered["index-chart-legend"]
    for series in chart["series"]:
        assert f"{series['close']:.2f}" in legend, series["entity_id"]

    # 첫 값이 아니라 첫 **관측**이다 — 공통 기준일에 그 시장이 쉬었으면 그
    # 지수의 출발점은 하루 뒤다(국장·미장은 휴장일이 다르다).
    values = json.loads(rendered["chart:chart-indices"])
    for row in values:
        first = next((v for v in row if v is not None), None)
        assert first == chart["base_value"], "기준일에서 100 으로 출발하지 않는다"


def test_변동성_지수는_상단_차트에_없다(rendered: dict[str, str]) -> None:
    """VIX 는 가격지수가 아니다. 기준일 100 으로 정규화해 나스닥 옆에 두면
    급등이 수익률로 읽힌다 — 목록에는 있되 **가격지수와 갈라서** 있어야 한다."""
    drawn = {series["name"] for series in json.loads(rendered["series:chart-indices"])}
    assert not (drawn & {"VIX", "VXN", "RVX"})

    listed = _payload()["data"]["markets"]["US"]["indices"]["others"]
    volatile = [row for row in listed if row["kind"] == "volatility"]
    if volatile:
        assert "변동성 지수" in rendered["indices-us"], "가격지수와 구분되지 않는다"


def test_환율_차트는_지수_차트에_자리를_내줬다(rendered: dict[str, str]) -> None:
    """환율 자체는 KPI 카드와 스파크라인으로 남는다 — **차트만** 교체다.
    옛 id 가 살아 있으면 새 JS + 옛 HTML 조합에서 두 차트가 겹친다."""
    assert "chart:chart-fx" not in rendered
    assert "chart-fx" not in (TEMPLATES / "market.html").read_text(encoding="utf-8")
