"""BE2 shadow 가 운영 장부(모의계좌 data/_paper · 실전 창고 data/)를 건드리지 않는다 — 정적 검사.

BE2 는 금고 심사 전 모델이다(docs/design/be2-shadow.md). 운영 장부로 새는 길은 셋뿐이고 셋 다 여기서 막는다:
 ① 가중치 — be2 가 일일 실행기(SCORERS)·주간 IC 측정 목록에 들어가면 측정표가 be2 에 가중치를 준다.
 ② 설정 — 체크인 기본값의 `selector.weights_override` 가 비어 있지 않으면 모든 창고가 그 값을 쓴다.
 ③ 장부 — 러너가 샌드박스가 아닌 창고에 쓰거나(--live-store) 실주문을 내면(--live-broker) 모의계좌와 섞인다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run_shadow_be2.sh"


def test_be2_gets_no_operational_weight() -> None:
    from quant_rl_trading.session.signals import SCORERS
    from tools.measure_ic import ANALYSTS

    assert all("be2" not in names for names in SCORERS.values()), "be2 가 일일 실행기에 들어가면 운영 장부 신호가 된다"
    assert "be2" not in ANALYSTS, "주간 IC --save 가 be2 에 운영 가중치를 준다"


def test_checked_in_override_is_off() -> None:
    config = yaml.safe_load((REPO / "config" / "quant_rl_trading.yaml").read_text(encoding="utf-8"))
    assert config["selector"].get("weights_override") in ({}, None, "")
    assert "weights_override_us" not in config["selector"] and "weights_override_kr" not in config["selector"]


def test_runner_writes_only_to_a_shadow_sandbox() -> None:
    from quant_rl_trading.store import mode

    text = RUNNER.read_text(encoding="utf-8")
    book = re.search(r"^BOOK=(\S+)$", text, flags=re.M)
    assert book is not None and book.group(1) == "data/_be2_shadow"
    assert mode.of(book.group(1)).code == mode.SHADOW
    sessions = [line for line in text.splitlines() if "tools/run_session.py" in line and not line.lstrip().startswith("#")
                and "pgrep" not in line]
    assert sessions and all('--sandbox "${BOOK}"' in line for line in sessions)
    assert "--live-store" not in text and "--live-broker" not in text
