"""대시보드 금지 사항 — 정적 검사.

CLAUDE.md 의 금지 목록은 지시다. 이 파일은 강제다.

두 가지를 막는다.

1. ``localStorage`` / ``sessionStorage`` — 브라우저에 숨은 상태가 있으면 같은
   링크를 열어도 사람마다 다른 화면을 보게 되고, "그때 그 화면" 을 재현할 수
   없다. 이 프로젝트의 전제(as_of 로 모든 것을 되감는다)와 정면으로 충돌한다.
2. ECharts 외 차트 라이브러리 — 두 벌이 되면 색·축·툴팁이 화면마다 달라진다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "quant_rl_trading" / "dashboard"

BANNED_STORAGE = ("localStorage", "sessionStorage")

#: 허용된 외부 출처. 늘리려면 CLAUDE.md 부터 고쳐야 한다.
ALLOWED_CDN_HOSTS = ("cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com")

OTHER_CHART_LIBRARIES = (
    "chart.js", "chartjs", "d3.js", "d3.min", "plotly", "highcharts",
    "apexcharts", "recharts", "lightweight-charts", "amcharts",
)


def asset_files() -> list[Path]:
    return sorted(
        path
        for pattern in ("*.js", "*.css", "*.html")
        for path in DASHBOARD.rglob(pattern)
    )


@pytest.mark.invariant
def test_assets_exist() -> None:
    """검사 대상이 사라지면 이 테스트가 조용히 통과한다. 그것부터 막는다."""
    assert asset_files(), "대시보드 자산이 하나도 없다"


@pytest.mark.invariant
def test_no_browser_storage() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {banned}"
        for path in asset_files()
        for banned in BANNED_STORAGE
        if banned in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "localStorage / sessionStorage 금지 (CLAUDE.md). "
        "상태는 URL 쿼리스트링에만 둔다:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.invariant
def test_only_echarts() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {library}"
        for path in asset_files()
        for library in OTHER_CHART_LIBRARIES
        if library in path.read_text(encoding="utf-8").lower()
    ]
    assert not offenders, (
        "ECharts 외 차트 라이브러리 추가 금지 (CLAUDE.md):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.invariant
def test_external_sources_are_allowlisted() -> None:
    """예상 못 한 외부 출처가 화면에 끼어드는 것을 잡는다."""
    import re

    offenders: list[str] = []
    for path in asset_files():
        for url in re.findall(r"https://([\w.-]+)", path.read_text(encoding="utf-8")):
            if url not in ALLOWED_CDN_HOSTS:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {url}")
    assert not offenders, "허용되지 않은 외부 출처:\n  " + "\n  ".join(offenders)
