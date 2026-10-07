"""P1-b′ shadow 가 다른 장부를 건드리지 않는다 (docs/protocols/p1b-prime-2026-10.md "shadow 장부").

새 길은 설정 키 하나(`selector.swap_min_z`)와 덮어쓰기 템플릿 하나다. 지키는 것:
 ① 체크인 기본값은 끔(0) — 모의계좌·다른 shadow 의 선정이 그대로다.
 ② 템플릿은 B1′ 구성 넷과 배관 둘만 바꾼다 — 배분·노출·위험 하한은 현행 그대로.
 ③ 템플릿이 켜면 그 샌드박스에서만 켜진다(빈 창고는 끔).
 ④ 러너는 샌드박스에만 쓰고 실주문을 내지 않는다.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from quant_rl_trading.selector.candidates import SelectionParams
from quant_rl_trading.store import OVERRIDES_FILE, Store

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run_shadow_p1b.sh"
TEMPLATE = REPO / "config" / "shadow" / "p1b.config-overrides.yaml"
NOW = datetime(2026, 10, 8, 7, 0, tzinfo=UTC)


def test_checked_in_swap_is_off() -> None:
    config = yaml.safe_load((REPO / "config" / "quant_rl_trading.yaml").read_text(encoding="utf-8"))
    assert config["selector"]["swap_min_z"] == 0.0
    assert not any(key.startswith("swap_min_z_") for key in config["selector"])


def test_template_changes_only_b1_prime_and_plumbing() -> None:
    values = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert values == {
        "selector.n_candidates": 100,
        "selector.exit_rank": 500,
        "selector.rebalance_every": 20,
        "selector.swap_min_z": 1.0,
        "selector.rebalance_anchor": "2026-10-08",
        "risk.max_positions": 110,
    }
    # 덮어쓰는 이름은 전부 체크인 설정에 있는 키다 — 오타면 조용히 안 걸린다.
    config = yaml.safe_load((REPO / "config" / "quant_rl_trading.yaml").read_text(encoding="utf-8"))
    for name in values:
        section, key = name.split(".")
        assert key in config[section], name


def test_template_turns_it_on_only_in_its_sandbox(tmp_path: Path) -> None:
    live = Store(root=tmp_path / "live")
    live.seed_config_defaults()
    assert SelectionParams.from_store(live, as_of=NOW, market="KR").swap_min_z == 0.0
    sandbox = Store(root=tmp_path / "_p1b_shadow")
    sandbox.seed_config_defaults()
    (tmp_path / "_p1b_shadow" / OVERRIDES_FILE).write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    params = SelectionParams.from_store(sandbox, as_of=NOW, market="KR")
    assert (params.n_candidates, params.exit_rank, params.swap_min_z) == (100, 500, 1.0)
    assert SelectionParams.from_store(live, as_of=NOW, market="KR").swap_min_z == 0.0


def test_runner_writes_only_to_a_shadow_sandbox() -> None:
    from quant_rl_trading.store import mode

    text = RUNNER.read_text(encoding="utf-8")
    book = re.search(r"^BOOK=(\S+)$", text, flags=re.M)
    assert book is not None and book.group(1) == "data/_p1b_shadow"
    assert mode.of(book.group(1)).code == mode.SHADOW
    sessions = [line for line in text.splitlines() if "tools/run_session.py" in line and not line.lstrip().startswith("#")
                and "pgrep" not in line]
    assert sessions and all('--sandbox "${BOOK}"' in line for line in sessions)
    assert "--live-store" not in text and "--live-broker" not in text
