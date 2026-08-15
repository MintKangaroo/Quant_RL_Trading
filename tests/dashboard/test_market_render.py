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

import html
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
#:
#: 지수·ETF 패널은 여기 없다. 2026-08-15 에 **가로 한 줄**(`index-strip`)로
#: 옮겨 갔기 때문이다 — 좌우 두 칸이 아니라서 짝 검사가 안 맞는다. 대신
#: 시장 경계가 보이는지·양쪽 시장이 다 찼는지를 아래에서 따로 본다.
PAIRED = ("indices", "breadth", "movers", "leaders", "macro")

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


def _group(rendered: dict[str, str], suffix: str) -> str:
    """가로 한 줄에서 그 시장의 묶음만 잘라 낸다.

    묶음 div 는 ``index-strip`` 의 innerHTML 안에서만 생기므로 하니스의
    ``getElementById`` 로는 안 잡힌다(아무도 그 id 로 찾지 않는다). 그래서
    문자열에서 자른다 — 검사가 보려는 것은 "그 시장 자리에 무엇이 찍혔나" 다.
    """
    strip = rendered["index-strip"]
    start = strip.index(f'id="index-group-{suffix}"')
    nxt = strip.find('id="index-group-', start + 1)
    return strip[start : nxt if nxt != -1 else len(strip)]


def _panels(suffix: str) -> dict:
    return _payload()["data"]["markets"][suffix.upper()]["instrument_panels"]


#: 미장 패널 제목이 **절대** 되어서는 안 되는 이름들. 지수 이름을 달고 ETF 를
#: 그리는 것이 이 저장소가 금지하는 대용치 바꿔치기다.
INDEX_NAMES = ("S&P 500", "SP500", "S&P500", "NASDAQ", "나스닥", "DJIA", "다우",
               "SOX", "다우존스", "필라델피아")


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_패널마다_차트가_하나씩_붙는다(rendered: dict[str, str], suffix: str) -> None:
    """창고에 없는 것도 자리를 지킨다 — 통째로 빼면 수집이 안 된 것인지
    애초에 안 그리는 것인지 화면에서 구분이 안 된다."""
    data = _panels(suffix)
    dump = _group(rendered, suffix)
    for panel in data["panels"]:
        assert panel["label"] in dump, panel["entity_id"]
        assert dec2(panel["close"]) in dump, f"{panel['label']} 종가가 안 적혔다"
    for gone in data["missing"]:
        assert gone["entity_id"] in dump, "없는 것의 자리가 통째로 사라졌다"
        assert "저절로 찬다" in dump, "수집되면 찬다는 사실을 안 적었다"

    drawn = [key for key in rendered if key.startswith(f"chart:chart-index-{suffix}-")]
    assert len(drawn) == len(data["panels"]), "패널 수와 그린 차트 수가 다르다"


def test_미장_패널_제목은_지수_이름이_아니다(rendered: dict[str, str]) -> None:
    """**이번 작업에서 제일 되돌아가기 쉬운 실수다.**

    미장 지수는 창고에 종가만 있어(FRED) 봉을 못 그리므로 ETF 를 그린다.
    그러면 제목도 ETF 티커여야 한다 — "S&P 500" 이라 쓰고 SPY 를 그리는
    순간 화면이 거짓말을 시작한다. 추종 지수는 **부제(``tracks``)에만** 적는다.
    """
    data = _panels("us")
    assert data["kind"] == "etf"
    for spec in data["panels"] + data["missing"]:
        assert spec["kind"] == "etf"
        # 제목은 티커다. entity_id 의 시장 접두어만 뗀 것.
        assert spec["label"] == spec["entity_id"].split(":", 1)[1]
        for name in INDEX_NAMES:
            assert name.lower() != spec["label"].lower(), f"제목이 지수 이름이다: {spec['label']}"
        assert spec["tracks"], "무엇을 좇는지는 적어야 한다"

    # 화면에서도 티커가 제목 자리에 있고, 추종 지수는 '추종' 이라는 말과 함께만 나온다.
    dump = _group(rendered, "us")
    for spec in data["panels"] + data["missing"]:
        assert f'class="index-panel-name">{spec["label"]}<' in dump
        # 화면은 esc() 를 지난다 — "S&P 500" 은 "S&amp;P 500" 으로 찍힌다.
        assert f'{html.escape(spec["tracks"])} 추종' in dump


def test_ETF_가_지수와_어떻게_다른지_화면이_적는다(rendered: dict[str, str]) -> None:
    """이 교체는 공짜가 아니다. 분배금·추적오차·시장가 괴리 셋을 화면이
    말하지 않으면 사람이 ETF 등락률을 지수 등락률로 읽는다."""
    dump = _group(rendered, "us")
    for word in ("분배", "추적오차", "NAV"):
        assert word in dump, f"{word} 를 안 적었다"
    # 벤치마크는 화면 사정으로 바뀌지 않는다는 것도 적어야 한다.
    assert "config.benchmark" in dump
    # 국장 칸은 지수라 이 문단이 붙지 않는다.
    assert "분배" not in _group(rendered, "kr")


def test_벤치마크와_화면_대표는_다른_배지다(rendered: dict[str, str]) -> None:
    """SPY 는 미장 칸의 첫 자리지만 **우리가 견줘 평가받는 대상이 아니다.**
    벤치마크는 여전히 `config.benchmark` 가 정한 지수이고, 이 화면은 그걸
    바꾸지 않는다. 한 배지로 뭉치면 언젠가 화면 사정으로 벤치마크가 갈린다.
    """
    kr = _panels("kr")
    us = _panels("us")
    assert [row["entity_id"] for row in kr["panels"] if row["benchmark"]] == ["KR:IDX:KOSPI"]
    assert not [row for row in us["panels"] + us["missing"] if row["benchmark"]], (
        "미장 ETF 가 벤치마크로 찍혔다 — config.benchmark 는 여전히 지수다"
    )
    # 배지 **텍스트**만 본다 — "벤치마크가 아니다" 라고 설명하는 툴팁·주의문에도
    # 같은 낱말이 들어 있어서, 그것까지 잡으면 문구를 못 고치게 된다.
    assert ">벤치마크</span>" in _group(rendered, "kr")
    assert ">벤치마크</span>" not in _group(rendered, "us")
    assert ">화면 대표</span>" in _group(rendered, "us")


def test_어느_시장도_패널이_비지_않는다(rendered: dict[str, str]) -> None:
    """이 검사는 두 번 규칙이 바뀌었다. "두 칸의 패널 수가 같다" → (미장이
    넷이 되며) "양쪽 다 채워져 있다" → (좌우 분할이 가로 한 줄이 되며) 칸이
    아니라 **시장** 기준. **막으려는 것은 처음부터 하나다** — 한쪽 시장이
    통째로 비어 "볼 게 없다" 처럼 보이는 것.
    """
    for suffix in ("kr", "us"):
        data = _panels(suffix)
        cards = data["panels"] + data["missing"]
        assert cards, f"{suffix} 시장에 패널이 하나도 없다"
        primary = [row for row in cards if row["role"] == "primary"]
        assert len(primary) == 1, f"{suffix} 첫 자리가 하나가 아니다: {primary}"
        assert _group(rendered, suffix).strip()


def test_한_줄이지만_시장_경계가_보인다(rendered: dict[str, str]) -> None:
    """가로로 펴면 **위치가 시장을 말해주지 않는다.** 좌우로 갈랐을 때는
    왼쪽/오른쪽이 그 일을 했는데, 여섯을 한 줄에 늘어놓으면 코스닥과 SPY
    사이의 경계가 사라진다 — 그 둘은 다른 시장이고 성격도 다르다(지수 vs ETF).
    """
    strip = rendered["index-strip"]
    # 시장마다 묶음이 하나씩. 묶음 사이의 선은 CSS 가 긋는다(.index-group + .index-group).
    assert strip.count('class="index-group"') == 2
    for code, suffix in (("KR", "kr"), ("US", "us")):
        head = _group(rendered, suffix)
        assert f'>{code}</span>' in head, f"{code} 머리글이 없다"
    assert "국장 · 지수" in strip
    assert "미장 · ETF" in strip


def test_여섯이_지정한_순서로_선다(rendered: dict[str, str]) -> None:
    """코스피 · 코스닥 · SPY · QQQ · DIA · SOXX. 순서가 섞이면 시장 경계가
    묶음 머리글과 어긋난다."""
    strip = rendered["index-strip"]
    expected = [
        spec["label"]
        for suffix in ("kr", "us")
        for spec in _panels(suffix)["panels"] + _panels(suffix)["missing"]
    ]
    positions = [strip.index(f'class="index-panel-name">{name}<') for name in expected]
    assert positions == sorted(positions), f"순서가 어긋났다: {expected}"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_봉은_넷이_다_있을_때만_그린다(rendered: dict[str, str], suffix: str) -> None:
    """없는 시가·고가·저가를 종가로 채우면 모든 봉이 십자가가 되는데, 그건
    "그날 변동이 없었다" 는 **다른 사실**이다. 못 그리면 선으로 긋고 화면이
    이유를 적는다."""
    for i, panel in enumerate(_panels(suffix)["panels"]):
        series = json.loads(rendered[f"chart:chart-index-{suffix}-{i}"])
        if panel["has_ohlc"]:
            assert series == [panel["ohlc"]], f"{panel['label']} 봉이 원 OHLC 가 아니다"
            assert all(len(bar) == 4 for bar in panel["ohlc"])
        else:
            assert series == [panel["closes"]]
            assert "봉을 못 그린다" in _group(rendered, suffix)


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_차트는_원값이다_정규화하지_않는다(rendered: dict[str, str], suffix: str) -> None:
    """정규화는 축을 공유해야 할 때만 필요했던 트릭이다. 패널이 갈리면서
    사라졌다 — 100 에서 출발하는 선이 있으면 그건 옛 동작이 남은 것이다."""
    for i, panel in enumerate(_panels(suffix)["panels"]):
        drawn = json.loads(rendered[f"chart:chart-index-{suffix}-{i}"])[0]
        # 캔들이면 첫 원소가 봉 하나([시,종,저,고]), 선이면 종가 하나다.
        raw = panel["ohlc"][0] if panel["has_ohlc"] else panel["closes"][0]
        assert drawn[0] == raw, f"{panel['label']} 이 원값이 아니다"
        assert drawn[0] != 100.0, "기준일 100 으로 정규화된 흔적이 남았다"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_가격지수_배지는_지수_패널에만_단다(rendered: dict[str, str], suffix: str) -> None:
    """배당이 빠진 만큼 우리가 이긴 것처럼 보인다 — 지수에는 그 배지가 계속
    붙어야 한다. 반대로 ETF 에 붙이면 틀린 말이다(ETF 는 분배금을 준다)."""
    dump = _group(rendered, suffix)
    if _panels(suffix)["kind"] == "index" and not _payload()["data"]["total_return"]:
        assert "배당 미반영" in dump
    else:
        assert "배당 미반영" not in dump


def test_변동성_지수는_패널이_아니다(rendered: dict[str, str]) -> None:
    """VIX 는 가격지수가 아니다 — 20 → 24 는 +20% 수익이 아니라 공포다.
    패널에서 빠지되 **목록에는 남는다**(US:IDX:* 는 전부 목록의 식구다)."""
    for suffix in ("kr", "us"):
        data = _panels(suffix)
        named = {row["entity_id"] for row in data["panels"] + data["missing"]}
        assert not (named & {"US:IDX:VIX", "US:IDX:VXN", "US:IDX:RVX"})

    listed = _payload()["data"]["markets"]["US"]["indices"]["others"]
    volatile = [row for row in listed if row["kind"] == "volatility"]
    assert volatile, "US:IDX:VIX 계열이 목록에서도 사라졌다"
    assert "변동성 지수" in rendered["indices-us"], "가격지수와 구분되지 않는다"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_패널로_세운_지수만_목록에서_빠진다(rendered: dict[str, str], suffix: str) -> None:
    """국장 패널은 지수라 목록에서 빠지고, 미장 패널은 ETF 라 애초에 이 목록의
    식구가 아니다 — 그래서 `US:IDX:*` 는 하나도 안 빠진다."""
    market = _payload()["data"]["markets"][suffix.upper()]["indices"]
    listed = {row["entity_id"] for row in market["others"]}
    assert not (listed & set(market["excluded"]))
    if _panels(suffix)["kind"] == "etf":
        assert market["excluded"] == [], "ETF 패널이 지수 목록을 깎았다"
    if market["excluded"]:
        assert "위 패널" in rendered[f"indices-count-{suffix}"]


def test_환율_차트는_사라졌고_패널은_칸_안에_있다(rendered: dict[str, str]) -> None:
    """환율 자체는 KPI 카드와 스파크라인으로 남는다. 옛 id 가 살아 있으면 새
    JS + 옛 HTML 조합에서 죽는다(Flask 가 템플릿을 캐싱한다)."""
    markup = (TEMPLATES / "market.html").read_text(encoding="utf-8")
    assert "chart-fx" not in markup
    assert "chart-indices" not in markup
    assert "chart:chart-fx" not in rendered
    assert "chart:chart-indices" not in rendered
