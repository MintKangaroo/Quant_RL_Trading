"""성과 카드 차트 — 메일에 싣는 PNG 를 **ECharts 로** 굽는다.

## 왜 이미지이고 왜 ECharts 인가

메일 클라이언트는 JS 도 SVG 도 못 그린다. 그래서 그림은 PNG 여야 하고, data URI 는
Gmail 이 막으니 인라인 첨부(``cid:``)로 싣는다(``emailer.build_message``).

차트 라이브러리는 **ECharts 하나다**(CLAUDE.md 금지 사항 — "ECharts 외 차트 라이브러리
추가"). 대시보드가 쓰는 같은 ``dashboard/static/echarts.min.js`` 를 헤드리스
Chromium(Playwright)에 올려 캔버스로 그리고 그 요소를 찍는다. 화면과 메일이 같은
엔진이라 색·모양이 어긋날 자리도 없다.

## 비필수 경로다

Chromium 이 없거나 굽다가 죽으면 **빈 dict** 를 돌려준다. 렌더러는 실제로 구워진 cid
만 참조하므로 깨진 이미지 아이콘은 생기지 않는다 — 메일은 차트 없이 나간다
(reporting.md §2 "리포트는 비필수 경로").

## 무엇을 그리나

카드 한 장에 한 그림: 위는 누적 곡선(펀드 지수 vs 벤치마크 지수, 첫날 = 0%), 아래는
일간 수익률 막대. 재료는 ``accounting.performance.Curve`` — 회계 장부를 읽기만 한 것이다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from quant_rl_trading.accounting.performance import Curve

ECHARTS = Path(__file__).resolve().parents[1] / "dashboard" / "static" / "echarts.min.js"

#: **폰 카드 폭**(390 화면 − 바깥·카드 여백 ≈ 340~360px)에 맞춘 CSS 픽셀. 520 으로 그렸더니
#: 폰에서 2/3 로 줄어 축 글씨가 7px 가 됐다(2026-09-19 실측). 폰에서 거의 1:1 로 보이게 그리고,
#: 3배로 찍어 데스크톱에서 늘어나도 흐리지 않게 한다 — PNG 는 1080×630, 한 장 수십 KB.
WIDTH = 360
HEIGHT = 210
SCALE = 3

#: 메일 안 cid 이름. 렌더러가 이 이름으로 ``<img src="cid:…">`` 를 건다.
CID = {"KR": "perf-kr", "US": "perf-us"}

_SCRIPT = """
(payload) => {
  const { data, palette, width, height } = payload;
  const el = document.getElementById("c");
  el.style.width = width + "px"; el.style.height = height + "px";
  const chart = echarts.init(el, null, { renderer: "canvas" });
  const days = data.sessions.map((d) => d.slice(5));
  const pct = (v) => (v === null || v === undefined ? null : +(v - 100).toFixed(3));
  const hasBench = (data.benchmark || []).some((v) => v !== null && v !== undefined);
  const series = [{
    name: "펀드", type: "line", data: data.index.map(pct), showSymbol: false,
    lineStyle: { width: 2.2, color: palette.ink }, itemStyle: { color: palette.ink },
    areaStyle: { color: palette.fill }, z: 3,
  }];
  if (hasBench) series.push({
    name: "벤치마크", type: "line", data: data.benchmark.map(pct), showSymbol: false,
    lineStyle: { width: 1.6, type: "dashed", color: palette.soft }, itemStyle: { color: palette.soft },
  });
  series.push({
    name: "일간", type: "bar", xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 9,
    data: data.daily.map((v) => v === null || v === undefined ? null : {
      value: +(v * 100).toFixed(3), itemStyle: { color: v >= 0 ? palette.up : palette.down },
    }),
  });
  // 막대 축은 0 을 가운데 두고 대칭 — 위 누적 축과 한 축처럼 읽히지 않게 이름표도 단다.
  const peak = Math.max(0.1, ...data.daily.filter((v) => v !== null && v !== undefined).map((v) => Math.abs(v * 100)));
  const bound = Math.ceil(peak * 10) / 10;
  const axisLine = { lineStyle: { color: palette.line } };
  const label = { color: palette.soft, fontSize: 11 };
  chart.setOption({
    animation: false,
    backgroundColor: palette.paper,
    textStyle: { fontFamily: palette.font },
    legend: hasBench ? {
      top: 2, left: 44, itemWidth: 16, itemHeight: 2, icon: "rect",
      data: ["펀드", "벤치마크"], textStyle: { color: palette.soft, fontSize: 12 },
    } : undefined,
    title: hasBench ? undefined : {
      text: "누적", left: 44, top: 2, textStyle: { color: palette.soft, fontSize: 12, fontWeight: 400 },
    },
    graphic: [{ type: "text", left: 44, top: 132,
      style: { text: "일간 수익률", fill: palette.soft, fontSize: 12, fontFamily: palette.font } }],
    grid: [
      { left: 44, right: 8, top: 24, height: 98 },
      { left: 44, right: 8, top: 150, bottom: 20 },
    ],
    xAxis: [
      { type: "category", data: days, gridIndex: 0, boundaryGap: false,
        axisLine, axisTick: { show: false }, axisLabel: { show: false } },
      { type: "category", data: days, gridIndex: 1, axisLine, axisTick: { show: false },
        axisLabel: { ...label, hideOverlap: true } },
    ],
    yAxis: [
      { type: "value", gridIndex: 0, scale: true, splitNumber: 3,
        axisLabel: { ...label, formatter: (v) => (v > 0 ? "+" : "") + v.toFixed(1) + "%" },
        splitLine: { lineStyle: { color: palette.line } } },
      { type: "value", gridIndex: 1, min: -bound, max: bound, interval: bound,
        axisLabel: { ...label, formatter: (v) => (v > 0 ? "+" : "") + v.toFixed(1) + "%" },
        splitLine: { lineStyle: { color: palette.line } } },
    ],
    series,
  });
  return true;
}
"""


def _palette() -> dict[str, str]:
    # 메일 본문과 **같은 색**이다 — 카드 바탕·상승·하락이 그림 안팎에서 달라지면 안 된다.
    from quant_rl_trading.reporting import render

    return {
        "paper": render.PAPER,
        "ink": render.INK,
        "soft": render.SOFT,
        "line": render.LINE,
        "up": render.UP,
        "down": render.DOWN,
        "fill": "rgba(232,234,237,0.08)",
        "font": "-apple-system,'Apple SD Gothic Neo','Noto Sans CJK KR','Malgun Gothic',sans-serif",
    }


def render_pngs(curves: dict[str, Curve]) -> dict[str, bytes]:
    """``{"KR": Curve, "US": Curve}`` → ``{cid: png}``. **실패하면 빈 dict** — 메일은 나간다."""
    wanted = {code: curve for code, curve in curves.items() if code in CID and len(curve.sessions) >= 2}
    if not wanted or not ECHARTS.exists():
        return {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("차트 생략 — playwright 가 없다", file=sys.stderr)
        return {}

    out: dict[str, bytes] = {}
    palette = _palette()
    try:
        with sync_playwright() as runner:
            browser = runner.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": WIDTH + 20, "height": HEIGHT + 20}, device_scale_factor=SCALE
                )
                page.set_content(
                    f'<html><body style="margin:0;background:{palette["paper"]}">'
                    '<div id="c"></div></body></html>'
                )
                page.add_script_tag(path=str(ECHARTS))
                for code, curve in wanted.items():
                    payload: dict[str, Any] = {
                        "data": json.loads(json.dumps(curve.as_dict())),
                        "palette": palette,
                        "width": WIDTH,
                        "height": HEIGHT,
                    }
                    page.evaluate(_SCRIPT, payload)
                    out[CID[code]] = page.locator("#c").screenshot(type="png")
                    page.evaluate("() => echarts.dispose(document.getElementById('c'))")
            finally:
                browser.close()
    except Exception as error:  # noqa: BLE001 - 차트는 비필수다
        print(f"차트 생략 — 굽다가 실패했다: {error}", file=sys.stderr)
        return {}
    return out
