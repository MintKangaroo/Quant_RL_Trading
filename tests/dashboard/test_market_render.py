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
import os
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
PAIRED = ("indices", "breadth", "rankings", "macro")

from tests.dashboard._browser import style_shim

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
  documentElement: element("html"),
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
""" + style_shim()

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


def num_ko(value: float) -> str:
    """market.js 의 num(). 천단위 구분이 들어가므로 문자열 비교에 필요하다."""
    return f"{int(value):,}"


def dec2(value: float) -> str:
    """market.js 의 dec(v, 2). 자릿수가 다르면 "안 적혔다" 로 오판한다."""
    return f"{value:.2f}"


def _payload() -> dict:
    """렌더러에 먹인 것과 **같은** 응답. 화면의 빈 칸이 "데이터가 없어서" 인지
    "렌더러가 놓쳐서" 인지는 이걸 같이 봐야 갈린다."""
    return json.loads((PAYLOADS / "market.json").read_text(encoding="utf-8"))["market"]


def _render(payload: dict | None = None) -> dict[str, str]:
    payload = payload if payload is not None else _payload()
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
        # market.html 이 market.js 보다 먼저 싣는다. 빠뜨리면 브라우저에서는
        # 멀쩡한 화면이 여기서만 죽는다 — 반대로, **여기서 죽으면 브라우저에서도
        # 죽는다**. 봉 그리기는 트레이딩 탭과 공유한다(candles.js).
        (STATIC / "candles.js").read_text(encoding="utf-8"),
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
    조인 버그처럼 보인다 — 화면이 이유를 말해야 엉뚱한 데를 파지 않는다.

    옛 "시가총액 상위" 패널이 순위표로 바뀌면서 이 사실을 드는 자리는 **트리맵**
    이 됐다. 검사를 지우지 않고 그쪽으로 옮긴다."""
    market = _payload()["data"]["markets"][suffix.upper()]
    if not market["treemap"]["rows"]:
        assert "상장주식수" in rendered[f"chart-treemap-{suffix}"]


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


# -- 빈 자리에서 죽지 않는다 ---------------------------------------------------


def test_국장_시총이_비어도_미장_칸이_끝까지_그려진다() -> None:
    """**한 줄에서 죽으면 그 아래가 통째로 안 돈다.**

    2026-08-18 실측: 국장 시총이 창 밖으로 밀려 트리맵이 빈 경로로 들어갔고,
    그 경로가 없는 이름(`noCapReason`)을 불러 ReferenceError 로 죽었다. 죽은
    자리는 국장 트리맵인데 **사라진 것은 미장 칸 전부**였다 — 지수 목록도,
    시장 폭도, 순위표도, 거시지표도. 사용자에게는 "미장이 안 뜬다" 로 보인다.

    그래서 고정한다: **어느 칸이 비어도 나머지 칸은 끝까지 찬다.** 기본 판은
    양쪽 트리맵이 다 차 있어 이 경로를 한 번도 안 밟았고, 그래서 이 고장이
    테스트를 통과했다.
    """
    if shutil.which("node") is None:
        pytest.skip("node 가 없다")
    payload = _payload()
    payload["data"]["markets"]["KR"]["treemap"]["rows"] = []
    rendered = _render(payload)

    assert "상장주식수" in rendered["chart-treemap-kr"]
    # 죽었으면 이 뒤가 통째로 없다 — 미장 칸이 실제로 찼는지 본다.
    for panel in PAIRED:
        assert rendered[f"{panel}-us"].strip("\x00").strip(), f"{panel}-us 가 비었다"
    assert rendered.get("chart:chart-treemap-us"), "미장 트리맵이 안 그려졌다"


def test_시총_세션을_트리맵_옆에_적는다() -> None:
    """시총 세션은 시세 세션과 다를 수 있다(수집기가 다르다). 날짜를 안 적으면
    며칠 지난 시총이 오늘 것으로 읽힌다."""
    if shutil.which("node") is None:
        pytest.skip("node 가 없다")
    payload = _payload()
    payload["data"]["markets"]["KR"]["treemap"]["session"] = "2026-08-11"
    rendered = _render(payload)
    assert "2026-08-11" in rendered["treemap-note-kr"]


# -- 순위표 3종 -----------------------------------------------------------------


def _rankings(suffix: str) -> dict:
    return _payload()["data"]["markets"][suffix.upper()]["rankings"]


def test_거래량_상위는_없다_그리고_다시_생기지_않는다() -> None:
    """2026-08-12 미장 거래량 상위 1·5위가 $1.52(+126%) · $1.36(+90.7%) 였다.

    **하한을 안 걸어서가 아니다** — 하한($1·$5M)은 거래량 순위에도 걸려 있었고
    둘 다 통과했다. 주식 수로 세는 한 싼 쪽이 유리한 것은 **척도 자체의
    성질**이라 하한은 바닥만 자를 뿐 순위의 기울기를 못 바꾼다. 그래서 표를
    없앴다 — 하한을 올려서 되살리려는 시도를 이 테스트가 막는다.
    """
    from quant_rl_trading.dashboard.services import market as service

    keys = [key for key, _, _ in service.RANKINGS]
    assert "volume" not in keys
    assert keys == ["value", "market_cap", "gainers", "losers"]


def test_화면과_메일이_같은_순위표를_든다() -> None:
    """두 곳이 다른 순위표를 들면 사용자가 어느 쪽을 믿을지 모른다.

    메일(`reporting/briefing.py`)은 거래대금·시가총액 둘이고, 화면은 거기에
    상승률을 더한 셋이다. **화면에만 있는 것은 괜찮지만, 메일에 있는데 화면에
    없는 것은 안 된다** — 그건 화면이 뒤처졌다는 뜻이다.

    (import 방향에 주의: `briefing` 이 `dashboard.services.market` 을 부르므로
    그 반대 방향 import 는 순환이다. 테스트에서만 둘을 함께 본다.)
    """
    from quant_rl_trading.dashboard.services import market as service
    from quant_rl_trading.reporting import briefing

    mail = {key for key, _, _ in briefing.RANKINGS}
    screen = {key for key, _, _ in service.RANKINGS}
    assert mail <= screen, f"메일에만 있는 순위표: {sorted(mail - screen)}"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_하한을_화면이_적는다(rendered: dict[str, str], suffix: str) -> None:
    """안 보이면 사용자는 이게 전체 순위인 줄 안다."""
    data = _rankings(suffix)
    dump = rendered[f"rankings-{suffix}"]
    if data["floor"] is None:
        # 하한을 모르면 순위를 안 매긴다 — 기본값으로 때우지 않는다.
        assert not any(table["rows"] for table in data["tables"])
        assert "config.reporting" in dump
        return
    assert "하한" in dump
    assert num_ko(data["floor"]["pool"]) in dump, "상승률 모집단 크기를 안 적었다"


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_순위표는_표마다_자기_세션을_적는다(rendered: dict[str, str], suffix: str) -> None:
    """시세와 시총은 다른 수집기가 넣는다 — 실측으로 국장 시세 08-14 ·
    시총 08-11 이었다. 나란히 놓으면 같은 날로 읽힌다."""
    dump = rendered[f"rankings-{suffix}"]
    for table in _rankings(suffix)["tables"]:
        assert table["label"] in dump
        if table["session"]:
            assert table["session"] in dump, f"{table['label']} 세션이 안 적혔다"


def test_국장과_미장의_세션이_다르면_화면이_그걸_말한다(rendered: dict[str, str]) -> None:
    """국장이 미장보다 앞선다(미장 prices 는 하루 늦다). 두 칸을 나란히 놓으면
    같은 날로 읽히므로 각 칸이 자기 날짜를 들어야 한다."""
    sessions = {}
    for suffix in ("kr", "us"):
        tables = _rankings(suffix)["tables"]
        sessions[suffix] = {t["session"] for t in tables if t["session"]}
    if sessions["kr"] and sessions["us"] and sessions["kr"] != sessions["us"]:
        for suffix in ("kr", "us"):
            for session in sessions[suffix]:
                assert session in rendered[f"rankings-{suffix}"]


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_순위표_줄마다_이름_가격_등락_시총이_있다(rendered: dict[str, str], suffix: str) -> None:
    """**등락은 장중 값으로 그린다.** 순위 자체는 종가로 서지만(그래야 새로
    고칠 때마다 순위가 흔들리지 않는다) 표에 찍히는 숫자는 지금 값이다.

    그래서 여기서 확인하는 것은 ``change``(종가 기준)가 아니라
    ``live_change`` 다. 장이 닫혀 장중 값이 없으면 종가로 되돌아간다 —
    두 경우를 다 밟는다.
    """
    dump = rendered[f"rankings-{suffix}"]
    for table in _rankings(suffix)["tables"]:
        for row in table["rows"]:
            assert html.escape(row["name"]) in dump
            shown = row.get("live_change")
            if shown is None:
                shown = row["change"]
            if shown is not None:
                assert dec2(shown * 100) + "%" in dump


def test_상승률과_하락률은_같은_모집단을_쓴다(rendered: dict[str, str]) -> None:
    """config 키가 ``gainer_pool`` 이라 상승 전용처럼 읽히는데 **아니다.**
    한쪽에만 하한을 걸면 두 표가 서로 다른 세계를 보게 되고, "상위 5개는 다
    급등주인데 하위 5개는 듣도 보도 못한 종목" 같은 화면이 된다.
    """
    for suffix in ("kr", "us"):
        data = _rankings(suffix)
        pooled = {t["key"]: t["pooled"] for t in data["tables"]}
        assert pooled["gainers"] == pooled["losers"], "두 표의 모집단이 갈렸다"
        # 시총·거래대금 표는 모집단 개념이 없다 — pooled 가 없어야 한다.
        assert pooled["value"] is None and pooled["market_cap"] is None
        if data["floor"]:
            assert "상승률·하락률" in rendered[f"rankings-{suffix}"]


def test_하락률_표는_아래에서부터_고른다(rendered: dict[str, str]) -> None:
    """같은 모집단을 반대 방향으로 자른다. 정렬이 뒤집히지 않으면 상승률
    표와 같은 종목이 뜬다 — 화면은 멀쩡해 보이고 값만 거짓이 된다."""
    for suffix in ("kr", "us"):
        tables = {t["key"]: t for t in _rankings(suffix)["tables"]}
        losers = [row["change"] for row in tables["losers"]["rows"] if row["change"] is not None]
        gainers = [row["change"] for row in tables["gainers"]["rows"] if row["change"] is not None]
        if not losers or not gainers:
            continue
        assert losers == sorted(losers), "하락률이 오름차순이 아니다"
        assert gainers == sorted(gainers, reverse=True), "상승률이 내림차순이 아니다"
        assert min(losers) <= max(gainers)


def test_줄_수는_config_가_정한다(rendered: dict[str, str]) -> None:
    """한때 모듈 상수였다. 같은 값을 두 곳에 두면 언젠가 화면과 메일이 다른
    줄 수를 든다 — `MOVER_POOL` 을 없앤 것과 같은 이유다 (불변식 10)."""
    from quant_rl_trading.dashboard.services import market as service

    assert not hasattr(service, "RANK_ROWS"), "줄 수가 다시 코드에 박혔다"
    for suffix in ("kr", "us"):
        data = _rankings(suffix)
        if data["floor"] is None:
            continue
        assert data["rows"] == data["floor"]["rows"]
        for table in data["tables"]:
            assert len(table["rows"]) <= data["rows"]


# -- 봉 전환 버튼 ---------------------------------------------------------------
#
# **꺼진 버튼도 화면에 남아야 한다.** 지수·ETF 에는 분봉이 한 봉도 없는데
# (수집이 보유·워치리스트·shadow 보유 종목만 받는다) 버튼을 지우면 "이 화면은
# 일봉 전용" 이 되고, 켜 두면 눌렀을 때 빈 화면이 뜬다. 남기고 끄고 **이유를
# title 에 적는** 것이 셋 중 유일하게 정직한 길이다.


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_패널마다_봉_전환_버튼이_선다(rendered: dict[str, str], suffix: str) -> None:
    dump = _group(rendered, suffix)
    for interval in ("1m", "5m", "15m", "1H", "4H", "1D", "1W"):
        assert f'data-interval="{interval}"' in dump, f"{interval} 버튼이 없다"
    # 지금 그린 것은 일봉이다 — **그 버튼에만** 불이 켜진다. 두 개가 켜져
    # 있으면 화면이 무엇을 보고 있는지 말하지 않는 것과 같다.
    lit = [
        interval
        for interval in ("1m", "5m", "15m", "1H", "4H", "1D", "1W")
        for chunk in [dump.partition(f'data-interval="{interval}"')[2]]
        if 'class="on"' in chunk[: chunk.index("</button>")]
    ]
    assert lit == ["1D"], lit
    # 버튼은 패널마다 하나씩이고, 어느 패널의 것인지 자기가 안다.
    for panel in _panels(suffix)["panels"]:
        assert f'data-entity="{html.escape(panel["entity_id"])}"' in dump


@pytest.mark.parametrize("suffix", ["kr", "us"])
def test_분봉이_없으면_버튼이_꺼지고_이유를_적는다(rendered, suffix: str) -> None:
    """**왜 꺼졌는지 화면이 말해야 한다.** 이유가 없으면 사용자는 고장으로
    읽고, 실제 원인(수집 대상이 아니다)에서 멀어진다."""
    data = _panels(suffix)
    dump = _group(rendered, suffix)
    for panel in data["panels"]:
        # 실측: 지수·ETF 는 분봉이 하나도 없다.
        assert panel["intervals"] == ["1D", "1W"], panel["entity_id"]
    assert "disabled" in dump
    assert "보유·워치리스트" in dump, "왜 꺼졌는지 화면이 말하지 않는다"


def test_창고에_분봉이_있으면_그_버튼만_켜진다() -> None:
    """민감도 — 버튼을 끄는 판단이 **응답의 사실**을 따라가는지 본다.

    실창고의 지수·ETF 에는 분봉이 없어 위 검사는 "전부 꺼짐" 만 본다. 그것만
    보면 "무조건 끈다" 로 짜여 있어도 통과한다. 그래서 응답을 한 줄 바꿔 넣고
    그 봉만 켜지는지 확인한다.
    """
    payload = _payload()
    panel = payload["data"]["markets"]["KR"]["instrument_panels"]["panels"][0]
    panel["intervals"] = ["5m", "1D", "1W"]
    dump = _group(_render(payload), "kr")

    assert '<button type="button" data-interval="5m"' in dump
    tail = dump.partition('data-interval="5m"')[2]
    assert "disabled" not in tail[: tail.index("</button>")], "있는 봉인데 꺼져 있다"
    # 나머지 분봉은 여전히 꺼져 있다 — 하나가 켜졌다고 다 켜지면 안 된다.
    for interval in ("1m", "15m", "1H", "4H"):
        after = dump.partition(f'data-interval="{interval}"')[2]
        assert "disabled" in after[: after.index("</button>")], f"{interval} 이 켜져 있다"


def test_템플릿이_공용_봉_렌더러를_싣는다() -> None:
    """**노드 하니스는 이걸 못 잡는다.** 하니스는 스크립트 목록을 자기가 들고
    있어서, 템플릿에서 ``candles.js`` 를 빼도 여기 검사들은 전부 통과한다 —
    그리고 브라우저에서만 ``candleOption is not defined`` 로 죽는다. 오늘 이
    화면이 그 방식으로 두 번 깨졌다.

    두 탭 다 본다. 봉 그리는 코드가 한 벌이라는 것은 곧 **두 화면이 같은
    파일에 의존한다**는 뜻이고, 한쪽 템플릿만 고치면 다른 쪽이 죽는다.
    """
    for name, main in (("market.html", "market.js"), ("trading.html", "trading.js")):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        # 주석에도 파일 이름이 나오므로 **script 태그만** 본다.
        loaded = re.findall(r"filename='([^']+\.js)'", source)
        assert "candles.js" in loaded, f"{name} 이 candles.js 를 안 싣는다"
        # 순서도 본다 — 뒤에 실으면 함수가 없는 채로 실행된다.
        assert loaded.index("candles.js") < loaded.index(main), f"{name} 의 로드 순서"
