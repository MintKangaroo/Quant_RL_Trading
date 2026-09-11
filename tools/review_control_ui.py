"""저장된 테스트 응답으로 UI를 검수한다. 실제 앱·창고·브로커를 열지 않는다.

uv tool run --from playwright --with flask python tools/review_control_ui.py
첫 실행 전: uv tool run --from playwright playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, render_template
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "quant_rl_trading/dashboard"
PAYLOADS = ROOT / "tests/dashboard/payloads"


async def review(output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    app = Flask("ui-review", template_folder=str(DASHBOARD / "templates"))
    with app.test_request_context():
        pages = {name: render_template(f"{name}.html") for name in ("trading", "learning")}
    data = {
        "trading": json.loads((PAYLOADS / "trading.json").read_text()),
        "trading/chart": json.loads((PAYLOADS / "chart.json").read_text()),
    }
    for name in ("learning", "system"):
        data.update(json.loads((PAYLOADS / f"{name}.json").read_text()))
    report = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        for name in pages:
            for width, height in ((1440, 1000), (390, 844)):
                page = await browser.new_page(viewport={"width": width, "height": height})
                errors, requests, mutations = [], [], []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))

                async def route(request, *, name=name, mutations=mutations, requests=requests):
                    path = urlparse(request.request.url).path
                    if request.request.method != "GET":
                        mutations.append(path)
                        await request.abort()
                    elif path.startswith("/api/"):
                        key = path[5:]
                        requests.append(key)
                        if key in data:
                            payload = {**data[key], "live": False}
                            await request.fulfill(json=payload)
                        else:
                            await request.fulfill(
                                status=503, json={"error": "저장된 검수 응답 없음"}
                            )
                    elif path.startswith("/static/"):
                        static = (DASHBOARD / "static").resolve()
                        file = (static / path[8:]).resolve()
                        if file.is_relative_to(static) and file.is_file():
                            await request.fulfill(
                                body=file.read_bytes(),
                                content_type=mimetypes.guess_type(file)[0]
                                or "application/octet-stream",
                            )
                        else:
                            await request.abort()
                    elif path == f"/{name}":
                        await request.fulfill(body=pages[name], content_type="text/html")
                    else:
                        await request.abort()

                await page.route("**/*", route)
                await page.goto(
                    f"http://quant-ui.test/{name}?as_of=2026-08-14T10:45:01Z&market=KR&ledger=paper"
                )
                await page.wait_for_function("document.querySelector('#kpis').children.length > 0")
                await page.evaluate("document.fonts.ready")
                await page.wait_for_timeout(200)
                assert await page.evaluate("document.documentElement.scrollWidth === innerWidth"), (
                    name,
                    width,
                )
                links = await page.locator(
                    "header nav a, .bottom-tabs a, .control-link"
                ).evaluate_all("els => els.every(e => new URL(e.href).searchParams.has('as_of'))")
                assert links, "탭 이동에서 과거 조회 시점이 사라졌다"
                metrics = await page.evaluate("""() => ({
                    risk_top: document.querySelector('#risk')?.getBoundingClientRect().top,
                    budget_top: document.querySelector('#control-budget')
                        ?.getBoundingClientRect().top,
                    stop_top: document.querySelector('#emergency-stop')
                        ?.getBoundingClientRect().top,
                    page_width: document.documentElement.scrollWidth})""")
                if name == "trading":
                    assert await page.locator("#emergency-stop").is_disabled()
                    assert "미측정" in await page.locator("#control-reconciliation").inner_text()
                    assert "trading/chart" not in requests and "trading/review" not in requests
                    assert metrics["budget_top"] < height and metrics["stop_top"] < height
                    if width == 1440:
                        assert metrics["risk_top"] < height
                else:
                    assert "learning/training-runs" not in requests
                    assert "learning/walk-forward" not in requests
                await page.evaluate("""() => {
                    const label = document.createElement('div');
                    label.textContent = 'UI 검수 · 저장된 테스트 응답 · 실시간 아님';
                    Object.assign(label.style, {position:'fixed', bottom:'0', left:'0', right:'0',
                        zIndex:'10000', background:'var(--panel)', color:'var(--text)',
                        fontSize:'11px', textAlign:'center', padding:'3px'});
                    document.body.append(label);
                }""")
                await page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)
                if name == "trading":
                    await page.locator("#candidate-details > summary").click()
                    await page.wait_for_function(
                        "document.querySelector('#chart-candle canvas') !== null"
                    )
                    assert "trading/chart" in requests
                    await page.evaluate("""async () => {
                        fetchJson = async () => {throw new Error('검수용 조회 장애');};
                        try { await loadTrading(); } catch (_) {}
                    }""")
                    assert "조회 실패" in await page.locator("#control-overview").inner_text()
                    assert await page.locator("#emergency-stop").is_disabled()
                else:
                    await page.locator("#rl-diagnostics > summary").click()
                    await page.wait_for_function(
                        "document.querySelector('#training-live').textContent.length > 0"
                    )
                    assert "learning/training-runs" in requests
                    await page.evaluate("""async () => {
                        fetchJson = async () => {throw new Error('검수용 모델 조회 장애');};
                        try { await renderKpis(); } catch (_) {}
                    }""")
                    assert "미측정" in await page.locator("#champion-evidence").inner_text()
                    assert "조회 실패" in await page.locator("#gate").inner_text()
                assert not errors, errors
                assert not mutations, mutations
                report.append(
                    {
                        "page": name,
                        "viewport": [width, height],
                        "metrics": metrics,
                        "javascript_errors": len(errors),
                        "write_requests": len(mutations),
                        "passed": True,
                    }
                )
                await page.close()
        await browser.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/quant-control-review"))
    args = parser.parse_args()
    result = asyncio.run(review(args.output))
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    (args.output / "report.json").write_text(serialized)
    print(serialized)
