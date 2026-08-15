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
PAIRED = ("index-panels", "indices", "breadth", "movers", "leaders", "macro")

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


def indexLabel(entity_id: str) -> str:
    """market.js 의 indexLabel 과 같은 규칙. "KR:IDX:KOSPI" → "KOSPI"."""
    head, _, tail = entity_id.partition("IDX:")
    return tail or entity_id


def dec2(value: float) -> str:
    """market.js 의 dec(v, 2). 자릿수가 다르면 "안 적혔다" 로 오판한다."""
    return f"{value:.2f}"


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


def _panels(suffix: str) -> dict:
    return _payload()["data"]["markets"][suffix.upper()]["index_panels"]


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_지수마다_자기_패널을_갖는다(rendered: dict[str, str], suffix: str) -> None:
    """한 차트에 겹치지 않는다 — 지수 하나당 패널 하나, 차트 하나다.

    창고에 없는 지수도 **자리를 지킨다.** 통째로 빼면 좌우 두 칸의 패널 수가
    갈리고, 그러면 "미장은 원래 볼 게 없다" 처럼 보인다.
    """
    data = _panels(suffix)
    dump = rendered[f"index-panels-{suffix}"]
    for panel in data["panels"]:
        assert indexLabel(panel["entity_id"]) in dump, panel["entity_id"]
        assert dec2(panel["close"]) in dump, f"{panel['entity_id']} 종가가 안 적혔다"
    for gone in data["missing"]:
        assert gone["entity_id"] in dump, "없는 지수의 자리가 통째로 사라졌다"

    drawn = [key for key in rendered if key.startswith(f"chart:chart-index-{suffix}-")]
    assert len(drawn) == len(data["panels"]), "패널 수와 그린 차트 수가 다르다"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_차트는_원_종가다_정규화하지_않는다(rendered: dict[str, str], suffix: str) -> None:
    """정규화는 축을 공유해야 할 때만 필요했던 트릭이다. 패널이 갈리면서
    사라졌다 — 100 에서 출발하는 선이 있으면 그건 옛 동작이 남은 것이다."""
    for i, panel in enumerate(_panels(suffix)["panels"]):
        series = json.loads(rendered[f"chart:chart-index-{suffix}-{i}"])
        assert series == [panel["closes"]], f"{panel['entity_id']} 가 원 종가가 아니다"


def test_대표_지수는_config_가_정한_것이다(rendered: dict[str, str]) -> None:
    """화면이 대표를 고르지 않는다. 창고에 없으면 대용치로 바꿔치기하지 않고
    "없다" 고 적는다 — KRX 300 을 코스피라 부르는 순간 화면이 거짓말을 시작한다."""
    for suffix in ("kr", "us"):
        data = _panels(suffix)
        heads = [row for row in data["panels"] + data["missing"] if row["role"] == "headline"]
        assert len(heads) == 1, f"{suffix} 대표가 하나가 아니다"
        assert "대표" in rendered[f"index-panels-{suffix}"]


def test_두_칸의_패널_수가_같다(rendered: dict[str, str]) -> None:
    """미장에만 다우·나스닥100·SOX 를 더하면 좌우 눈높이가 어긋나 나란히
    비교하는 배치 자체가 무너진다."""
    kr = _panels("kr")
    us = _panels("us")
    assert len(kr["panels"]) + len(kr["missing"]) == len(us["panels"]) + len(us["missing"])


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_가격지수_배지를_계속_단다(rendered: dict[str, str], suffix: str) -> None:
    """배당이 빠진 만큼 우리가 이긴 것처럼 보인다. config 가 total_return 을
    true 로 바꾸기 전까지 배지는 화면에 남아야 한다."""
    if _payload()["data"]["total_return"]:
        assert "배당 미반영" not in rendered[f"index-panels-{suffix}"]
    else:
        assert "배당 미반영" in rendered[f"index-panels-{suffix}"]


def test_변동성_지수는_패널로_세우지_않는다(rendered: dict[str, str]) -> None:
    """VIX 는 가격지수가 아니다. 20 → 24 는 +20% 수익이 아니라 공포다 —
    목록에는 있되 **가격지수와 갈라서** 있어야 한다."""
    for suffix in ("kr", "us"):
        data = _panels(suffix)
        named = {row["entity_id"] for row in data["panels"] + data["missing"]}
        assert not (named & {"US:IDX:VIX", "US:IDX:VXN", "US:IDX:RVX"})

    listed = _payload()["data"]["markets"]["US"]["indices"]["others"]
    if [row for row in listed if row["kind"] == "volatility"]:
        assert "변동성 지수" in rendered["indices-us"], "가격지수와 구분되지 않는다"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_패널로_옮긴_지수는_목록에서_빠지고_그_사실을_적는다(
    rendered: dict[str, str], suffix: str
) -> None:
    """같은 지수를 한 칸에 두 번 적으면 목록의 "N종" 이 무엇을 세는지 흐려진다.
    다만 빠진 개수를 안 적으면 총 개수와 줄 수가 안 맞아 버그처럼 보인다."""
    market = _payload()["data"]["markets"][suffix.upper()]["indices"]
    listed = {row["entity_id"] for row in market["others"]}
    assert not (listed & set(market["excluded"]))
    if market["excluded"]:
        assert "위 패널" in rendered[f"indices-count-{suffix}"]


def test_환율_차트는_사라졌고_지수는_칸_안에_있다(rendered: dict[str, str]) -> None:
    """환율 자체는 KPI 카드와 스파크라인으로 남는다. 그리고 지수는 두 칸 위에
    걸친 공통 차트가 아니라 **각 칸 안**에 산다 — 옛 id 가 살아 있으면 새 JS +
    옛 HTML 조합에서 죽는다(Flask 가 템플릿을 캐싱한다)."""
    markup = (TEMPLATES / "market.html").read_text(encoding="utf-8")
    assert "chart-fx" not in markup
    assert "chart-indices" not in markup
    assert "chart:chart-fx" not in rendered
    assert "chart:chart-indices" not in rendered
