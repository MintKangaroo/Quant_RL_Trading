"""시딩이 실계좌 지문을 지우지 않는다.

**2026-08-15 에 실제로 한 번 지웠다.** `seed_config_defaults()` 가
`execution.live_account_fingerprint` 를 "바뀐 값" 으로 보고 멈췄고, 그것을
"오늘 아침 모의→실전 계좌를 바꿨으니 정정본이 맞다" 로 오독해 `effective_at`
을 줘서 밀어붙였다. yaml 은 `""` 이고 창고에 값이 있었으므로, 그 한 번으로
창고 값이 빈 문자열이 됐다.

그 지문은 `tools/verify_live_order.py` 가 "모의인 줄 알았다" 를 막는 유일한
장치다. 비면 계좌 확인이 통째로 무력해진다 — 8/17~18 실계좌 주문 이틀 전에
벌어진 일이다.

같은 날 다른 작업이 또 밟을 뻔했다(`seed_config_defaults` 는 backfill·
dashboard 가 부른다). **사람이 매번 조심하는 것으로는 못 막으므로** 규칙을
코드에 둔다: yaml 의 빈 문자열은 "여기서 정하지 않는다" 는 뜻이고, 그런 키는
창고가 정본이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import importlib

#: **`from quant_rl_trading.store import config` 는 서브모듈이 아니라 동명의
#: 함수를 준다** — 패키지가 그 이름을 재수출한다. `import ... as` 도 같은 것을
#: 집는다. 모듈이 필요하므로 `import_module` 로 가져온다.
config_module = importlib.import_module("quant_rl_trading.store.config")

LIVE = "execution.live_account_fingerprint"
FINGERPRINT = "aa303b375a64"


def _yaml_with_empty_fingerprint(tmp_path: Path) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(
        "execution:\n"
        f'  live_account_fingerprint: ""\n'
        "  max_slippage: 0.005\n",
        encoding="utf-8",
    )
    return path


def _warehouse_has_fingerprint() -> dict[str, tuple[str, int]]:
    return {
        LIVE: (json.dumps(FINGERPRINT), 0),
        "execution.max_slippage": (json.dumps(0.005), 0),
    }


def test_빈_yaml_값은_바뀐_것으로_안_센다(tmp_path: Path) -> None:
    """`changed_names` 가 이 키를 세면 `seed_config_defaults` 가 멈추고,
    사람이 그 멈춤을 오독해 `effective_at` 으로 밀어붙이게 된다."""
    changed = config_module.changed_names(
        _yaml_with_empty_fingerprint(tmp_path), _warehouse_has_fingerprint()
    )
    assert LIVE not in changed, "빈 yaml 값이 '정정' 으로 보이면 지문이 지워진다"


def test_시딩이_지문을_안_덮는다(tmp_path: Path) -> None:
    """`effective_at` 을 줘도 이 키에는 행이 안 생긴다 — 그게 방어의 요점이다.

    `effective_at` 이 있으면 정정본이 만들어지는 것이 정상 동작이라,
    "발효 시점을 줬으니 괜찮다" 는 판단이 여기서는 틀린다.
    """
    rows = config_module.defaults_rows(
        _yaml_with_empty_fingerprint(tmp_path),
        current=_warehouse_has_fingerprint(),
        effective_at=datetime(2026, 8, 15, 13, 0, tzinfo=UTC),
    )
    assert not [row for row in rows if row["entity_id"] == LIVE], (
        "시딩이 실계좌 지문을 빈 값으로 덮었다"
    )


def test_창고에_없으면_빈_값이라도_처음엔_심는다(tmp_path: Path) -> None:
    """**아직 안 정한 것과 지우는 것은 다르다.** 빈 창고에는 키가 있어야
    `store.config` 가 `ConfigNotFound` 대신 빈 값을 돌려준다."""
    rows = config_module.defaults_rows(_yaml_with_empty_fingerprint(tmp_path), current={})
    assert [row for row in rows if row["entity_id"] == LIVE], (
        "창고에 값이 없으면 빈 값이라도 키는 심어야 한다"
    )


def test_창고도_비어_있으면_안_건드린다(tmp_path: Path) -> None:
    """양쪽 다 `""` 면 바뀐 게 없다. 같은 사실을 두 번 적지 않는다."""
    current = {LIVE: (json.dumps(""), 0)}
    rows = config_module.defaults_rows(
        _yaml_with_empty_fingerprint(tmp_path), current=current
    )
    assert not [row for row in rows if row["entity_id"] == LIVE]


def test_빈_값이_아닌_키는_평소대로_정정된다(tmp_path: Path) -> None:
    """방어가 너무 넓으면 진짜 정정까지 막는다. `max_slippage` 는 그대로 돈다."""
    path = tmp_path / "cfg.yaml"
    path.write_text(
        'execution:\n  live_account_fingerprint: ""\n  max_slippage: 0.009\n',
        encoding="utf-8",
    )
    changed = config_module.changed_names(path, _warehouse_has_fingerprint())
    assert changed == {"execution.max_slippage"}
