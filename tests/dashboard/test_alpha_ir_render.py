"""장부별 IR 렌더러(static/alpha_ir.js)를 **진짜 창고에서 뜬 응답**으로 두 탭 템플릿에서 돌려 본다.

payloads/alpha_ir.json 은 2026-10-05 09:00 KST 의 실제 ``/api/trading/alpha-ir`` 응답이다(모의계좌 24세션 ·
shadow 트랙들 6~8세션 · 지수+V6 첫 NAV 전). 잡고 싶은 것:

- 템플릿에 렌더러가 만지는 요소(``alpha-ir``·``alpha-ir-stamp``)가 없는 탭 — 화면에서는 패널 하나가 조용히 빈다.
- 표본 부족 칸에 숫자가 새는 것, ``undefined``·``NaN`` 이 화면에 찍히는 것.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.dashboard.test_tab_render import HARNESS

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "quant_rl_trading" / "dashboard" / "static"
TEMPLATES = REPO_ROOT / "quant_rl_trading" / "dashboard" / "templates"
PAYLOAD = json.loads((Path(__file__).parent / "payloads" / "alpha_ir.json").read_text(encoding="utf-8"))

#: scope.js 의 fetchJson 을 응답으로 바꿔 끼우고, alpha_ir.js 가 스스로 부르는 loadAlphaIr() 가 끝나면 결과를 찍는다.
DRIVER = """
setTimeout(() => {
  const target = document.getElementById("alpha-ir");
  const stamp = document.getElementById("alpha-ir-stamp");
  console.log("RESULT " + JSON.stringify({ html: target ? target.innerHTML : null, stamp: stamp ? stamp.textContent : null }));
}, 50);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
@pytest.mark.parametrize("template", ["trading.html", "learning.html"])
def test_두_탭에서_실제_응답으로_표가_그려진다(template: str, tmp_path: Path) -> None:
    html = (TEMPLATES / template).read_text()
    assert "alpha_ir.js" in html and "alpha_ir.css" in html
    ids = sorted(set(re.findall(r'id="([^"]+)"', html))
                 | set(re.findall(r'id="([^"]+)"', (TEMPLATES / "_scope.html").read_text())))
    js = "\n".join([
        HARNESS.replace("IDS", json.dumps(ids)),
        (STATIC / "scope.js").read_text(),
        "fetchJson = async (path) => { const key = path.split('?')[0]; "
        f"const all = {json.dumps(PAYLOAD)}; if (!(key in all)) throw new Error('없는 경로 ' + key); return all[key]; }};",
        (STATIC / "alpha_ir.js").read_text(),
        DRIVER,
    ])
    path = tmp_path / "alpha_ir.js"
    path.write_text(js, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    line = next((x for x in result.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, f"{result.stdout}\n{result.stderr}"
    out = json.loads(line[len("RESULT "):])
    body = out["html"]

    assert "<table" in body and "못 읽었다" not in body
    assert "undefined" not in body and "NaN" not in body
    assert out["stamp"].startswith("기준 세션 ")
    books = PAYLOAD["trading/alpha-ir"]["data"]["books"]
    assert body.count("<tr") == len(books) + 1                       # 머리 한 줄 + 장부마다 한 줄
    assert "장부 아직 없음" in body
    for book in books:
        if book["status"] == "not_applicable":
            assert book["reason"] in body                             # 숫자 없이 이유 한 줄
    # 응답이 null 로 보낸 IR 은 화면에서도 숫자가 아니다. 전체 창이 모자란 장부는 비율 칸을 한 칸으로 접고(한 번),
    # 전체 창은 찼지만 롤링 창이 모자란 장부는 그 창마다 한 번.
    ok = [b for b in books if b["status"] == "ok"]
    short = sum(1 for b in ok if not b["windows"]["all"]["sufficient"])
    short += sum(1 for b in ok if b["windows"]["all"]["sufficient"]
                 for k in ("20", "60") if not b["windows"][k]["sufficient"])
    assert short > 0 and body.count("표본 부족 ") == short
