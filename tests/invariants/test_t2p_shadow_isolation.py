"""T2P 전방 shadow(미가동)가 다른 장부를 건드리지 않는다 (docs/design/t2p-shadow.md).

 ① 템플릿 = t2fa 알파 + P1-b′ 구성 넷 + 배관 — P1-b′ 템플릿과 같은 값.
 ② 러너는 샌드박스에만 쓰고 실주문이 없으며, 모델 폴더·anchor 가 갖춰지기 전엔 무동작.
 ③ t2fa 는 운영 가중치를 받는 목록에 없다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run_shadow_t2p.sh"
TEMPLATE = REPO / "config" / "shadow" / "t2p.config-overrides.yaml"


def test_template_is_t2fa_alpha_plus_p1b_construction() -> None:
    t2p = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    p1b = yaml.safe_load((REPO / "config" / "shadow" / "p1b.config-overrides.yaml").read_text(encoding="utf-8"))
    assert t2p["selector.weights_override"] == {"t2fa": 1.0, "risk": 1.0}
    for key in ("selector.n_candidates", "selector.exit_rank", "selector.rebalance_every", "selector.swap_min_z", "risk.max_positions"):
        assert t2p[key] == p1b[key], key


def test_runner_is_sandboxed_and_gated() -> None:
    from quant_rl_trading.store import mode

    text = RUNNER.read_text(encoding="utf-8")
    book = re.search(r"^BOOK=(\S+)$", text, flags=re.M)
    assert book is not None and book.group(1) == "data/_t2p_shadow" and mode.of(book.group(1)).code == mode.SHADOW
    sessions = [line for line in text.splitlines() if "tools/run_session.py" in line and not line.lstrip().startswith("#")
                and "pgrep" not in line]
    assert sessions and all('--sandbox "${BOOK}"' in line for line in sessions)
    assert "--live-store" not in text and "--live-broker" not in text
    assert 'if [ ! -d "${MODELS}" ]' in text and '[[ ! "$(date +%F)" > "${ANCHOR}" ]]' in text
    assert re.search(r'^exit "\$\{RC:-1\}"$', text, flags=re.M)


def test_t2fa_gets_no_operational_weight() -> None:
    from quant_rl_trading.session.signals import SCORERS
    from tools.measure_ic import ANALYSTS

    assert all("t2fa" not in names for names in SCORERS.values()) and "t2fa" not in ANALYSTS
