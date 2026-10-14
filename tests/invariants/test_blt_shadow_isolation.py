"""BLT shadow 가 다른 장부를 건드리지 않는다 (docs/design/blt-shadow.md, 등록 초안 be2-low-turnover-2026-10.md §6).

새 코드는 없다 — 이미 도는 두 장부의 설정(BE2 shadow 의 알파 · P1-b′ shadow 의 구성)을 합친 템플릿 하나와 러너 하나다. 지키는 것:
 ① 템플릿 = be2 알파 + B1′ 구성 넷 + 배관 둘 — 다른 장부 템플릿과 값이 같다(갈라지면 2×2 짝 비교가 깨진다).
 ② 덮어쓰는 이름은 모두 체크인 설정에 있는 키다(오타면 조용히 안 걸린다).
 ③ 러너는 샌드박스에만 쓰고 실주문을 내지 않으며, 채점하지 않는다(be2 점수는 run_score_be2 가 낸다).
 ④ anchor 자리표시(2099-01-01)면 러너가 아무것도 안 한다 — 크론을 먼저 걸어도 장부가 서지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run_shadow_blt.sh"
TEMPLATE = REPO / "config" / "shadow" / "blt.config-overrides.yaml"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_template_is_be2_alpha_plus_p1b_construction() -> None:
    blt = _yaml(TEMPLATE)
    be2 = _yaml(REPO / "config" / "shadow" / "be2.config-overrides.yaml")
    p1b = _yaml(REPO / "config" / "shadow" / "p1b.config-overrides.yaml")
    assert blt["selector.weights_override"] == be2["selector.weights_override"] == {"be2": 1.0, "risk": 1.0}
    for key in ("selector.n_candidates", "selector.exit_rank", "selector.rebalance_every", "selector.swap_min_z", "risk.max_positions"):
        assert blt[key] == p1b[key], key
    assert set(blt) == {"selector.weights_override", "selector.n_candidates", "selector.exit_rank", "selector.rebalance_every",
                        "selector.swap_min_z", "selector.rebalance_anchor", "risk.max_positions"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(blt["selector.rebalance_anchor"]))


def test_override_keys_exist_in_checked_in_config() -> None:
    config = _yaml(REPO / "config" / "quant_rl_trading.yaml")
    for name in _yaml(TEMPLATE):
        section, key = name.split(".")
        assert key in config[section], name


def test_runner_writes_only_to_its_shadow_sandbox() -> None:
    from quant_rl_trading.store import mode

    text = RUNNER.read_text(encoding="utf-8")
    book = re.search(r"^BOOK=(\S+)$", text, flags=re.M)
    assert book is not None and book.group(1) == "data/_blt_shadow"
    assert mode.of(book.group(1)).code == mode.SHADOW
    tmpl = re.search(r"^TEMPLATE=(\S+)$", text, flags=re.M)
    assert tmpl is not None and REPO / tmpl.group(1) == TEMPLATE
    sessions = [line for line in text.splitlines() if "tools/run_session.py" in line and not line.lstrip().startswith("#")
                and "pgrep" not in line]
    assert sessions and all('--sandbox "${BOOK}"' in line and "--market KR" in line for line in sessions)
    assert "--live-store" not in text and "--live-broker" not in text
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("tools/score_be2.py --market" in line for line in code), "채점은 run_score_be2 의 몫 — 여기서 실전 창고에 쓰지 않는다"
    assert re.search(r'^exit "\$\{RC:-1\}"$', text, flags=re.M), "크론이 보는 rc 를 echo 가 삼키지 않게"


def test_placeholder_anchor_makes_runner_a_no_op(tmp_path: Path) -> None:
    """러너의 anchor 추출 sed 가 템플릿 줄을 읽고, 자리표시(2099)는 '첫 세션 전' 으로 건너뛴다."""
    import subprocess

    text = RUNNER.read_text(encoding="utf-8")
    sed = re.search(r"^ANCHOR=\$\(sed -n '([^']+)'", text, flags=re.M)
    assert sed is not None
    out = subprocess.run(["sed", "-n", sed.group(1), str(TEMPLATE)], capture_output=True, text=True, check=True).stdout.strip()
    assert out == str(_yaml(TEMPLATE)["selector.rebalance_anchor"])
    assert '[[ ! "$(date +%F)" > "${ANCHOR}" ]]' in text


def test_template_turns_on_only_in_its_sandbox(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from quant_rl_trading.selector.candidates import SelectionParams
    from quant_rl_trading.store import OVERRIDES_FILE, Store

    now = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    live = Store(root=tmp_path / "live")
    live.seed_config_defaults()
    sandbox = Store(root=tmp_path / "_blt_shadow")
    sandbox.seed_config_defaults()
    (tmp_path / "_blt_shadow" / OVERRIDES_FILE).write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    params = SelectionParams.from_store(sandbox, as_of=now, market="KR")
    assert (params.n_candidates, params.exit_rank, params.swap_min_z) == (100, 500, 1.0)
    assert sandbox.config("selector.weights_override", as_of=now) == {"be2": 1.0, "risk": 1.0}
    assert SelectionParams.from_store(live, as_of=now, market="KR").n_candidates == 24
    assert not live.config("selector.weights_override", as_of=now)
