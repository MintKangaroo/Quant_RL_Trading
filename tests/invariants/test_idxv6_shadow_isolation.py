"""지수+V6 shadow 가 운영 장부(모의계좌 data/_paper · 실전 창고 data/)를 건드리지 않는다 — 정적 검사.

새 길은 셋이고 셋 다 기본값이 옛 동작이어야 한다(portfolio-construction.md "지수+V6 트랙"):
 ① 설정 — 체크인 기본값의 `selector.fixed_basket` 이 비어 있지 않으면 모든 창고가 선정을 건너뛴다.
 ② 시세 — 펀드 ID 가 시장 전체 조회에 끼면 유니버스·횡단면 z 가 오염된다(tests/store/test_fund_prices.py 가 동작을 지킨다).
 ③ 장부 — 러너가 샌드박스가 아닌 창고에 쓰거나(--live-store) 실주문을 내면(--live-broker) 모의계좌와 섞인다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run_shadow_idxv6.sh"


def test_checked_in_fixed_basket_is_off() -> None:
    config = yaml.safe_load((REPO / "config" / "quant_rl_trading.yaml").read_text(encoding="utf-8"))
    assert config["selector"].get("fixed_basket") in ([], None, "")
    assert not any(key.startswith("fixed_basket_") for key in config["selector"])


def test_fund_rows_join_only_by_name() -> None:
    from quant_rl_trading.store.prices import _split_funds

    assert _split_funds(None) == (None, [])
    assert _split_funds(["KR:005930"]) == (["KR:005930"], [])
    assert _split_funds(["KR:005930", "KR:ETF:069500"]) == (["KR:005930"], ["KR:ETF:069500"])


def test_runner_writes_only_to_a_shadow_sandbox() -> None:
    from quant_rl_trading.store import mode

    text = RUNNER.read_text(encoding="utf-8")
    book = re.search(r"^BOOK=(\S+)$", text, flags=re.M)
    assert book is not None and book.group(1) == "data/_idxv6_shadow"
    assert mode.of(book.group(1)).code == mode.SHADOW
    sessions = [line for line in text.splitlines() if "tools/run_session.py" in line and not line.lstrip().startswith("#")
                and "pgrep" not in line]
    assert sessions and all('--sandbox "${BOOK}"' in line for line in sessions)
    assert "--live-store" not in text and "--live-broker" not in text
