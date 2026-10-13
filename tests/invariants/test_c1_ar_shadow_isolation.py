"""C1·AR shadow 가 운영 장부(모의계좌 data/_paper · 실전 창고 data/)를 건드리지 않는다 — 정적 검사(be2 shadow 와 같은 셋).

 ① 가중치 — c1fa·ar_insider 가 일일 실행기(SCORERS)·주간 IC 측정 목록에 들어가면 측정표가 가중치를 준다.
 ② 설정 — 덮어쓰기 템플릿은 그 Analyst 하나 + risk 만 켠다(체크인 기본값이 꺼져 있는 것은 test_be2_shadow_isolation 이 본다).
 ③ 장부 — 러너가 샌드박스가 아닌 창고에 쓰거나(--live-store) 실주문을 내면(--live-broker) 모의계좌와 섞인다.
 ④ 금고 second 창 — 채점 도구는 시작일 전 세션을 소급 채점하지 않는다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CASES = {
    "c1fa": (REPO / "scripts" / "run_shadow_c1.sh", "data/_c1_shadow", REPO / "config" / "shadow" / "c1.config-overrides.yaml"),
    "ar_insider": (REPO / "scripts" / "run_shadow_ar.sh", "data/_ar_shadow", REPO / "config" / "shadow" / "ar.config-overrides.yaml"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_shadow_alpha_gets_no_operational_weight(name: str) -> None:
    from quant_rl_trading.session.signals import SCORERS
    from tools.measure_ic import ANALYSTS

    assert all(name not in names for names in SCORERS.values()), f"{name} 가 일일 실행기에 들어가면 운영 장부 신호가 된다"
    assert name not in ANALYSTS, f"주간 IC --save 가 {name} 에 운영 가중치를 준다"


@pytest.mark.parametrize("name", sorted(CASES))
def test_override_template_switches_only_this_alpha(name: str) -> None:
    _, _, template = CASES[name]
    config = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert config == {"selector.weights_override": {name: 1.0, "risk": 1.0}}


@pytest.mark.parametrize("name", sorted(CASES))
def test_runner_writes_only_to_its_shadow_sandbox(name: str) -> None:
    from quant_rl_trading.store import mode

    runner, book, template = CASES[name]
    text = runner.read_text(encoding="utf-8")
    found = re.search(r"^BOOK=(\S+)$", text, flags=re.M)
    assert found is not None and found.group(1) == book
    assert mode.of(book).code == mode.SHADOW
    tmpl = re.search(r"^TEMPLATE=(\S+)$", text, flags=re.M)
    assert tmpl is not None and REPO / tmpl.group(1) == template
    sessions = [line for line in text.splitlines() if "tools/run_session.py" in line and not line.lstrip().startswith("#")
                and "pgrep" not in line]
    assert sessions and all('--sandbox "${BOOK}"' in line for line in sessions)
    assert "--live-store" not in text and "--live-broker" not in text
    assert f"--analyst {name}" in text
    assert re.search(r'^exit "\$\{RC:-1\}"$', text, flags=re.M), "크론이 보는 rc 를 echo 가 삼키지 않게"


def test_scoring_never_backfills_the_second_window() -> None:
    from tools import score_shadow_alpha as tool

    assert date(2026, 10, 13) <= tool.SHADOW_START
    assert set(tool.ANALYSTS) == set(CASES)
    assert tool.main(["--analyst", "c1fa", "--day", "2026-10-02", "--root", "/nonexistent-never-opened"]) == tool.RC_BAD_ARGS
    assert tool.score_session(None, "ar_insider", None, date(2026, 10, 12), None) == tool.RC_BAD_ARGS  # type: ignore[arg-type]
