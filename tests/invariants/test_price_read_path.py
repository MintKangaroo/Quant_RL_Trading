"""시세는 ``store.prices.read_prices`` 를 경유한다.

창고의 ``prices`` 에는 전 종목 종가가 0 인 휴장일 세션이 있다(2026-06-03·
2026-07-17). 그 행이 한 줄만 남아도 ``pct_change`` 가 전 종목을 같은 날
-100% 로 만들고, 그 공통 하루가 60일 상관행렬을 지배한다 — 실측으로 상관
상한을 넘는 쌍이 3.5% → 94.0% 로 뛰었고 후보 절반이 음수 알파로 뒤집혔다.

**헬퍼를 두는 것만으로는 안 막힌다.** 다음 사람이 ``store.get("prices", ...)``
를 한 번 더 쓰면 그 경로만 조용히 오염되고, 조용하기 때문에 아무도 모른다.
실제로 이 규칙을 켜자 ``analysts/event.py`` 가 ``price_panel`` 을 안 타고
직접 읽고 있던 것이 나왔다 — 사람 눈으로 훑어서는 못 찾은 곳이다.

여기서도 **가드 자체를 먼저 시험한다.** 잡아야 할 것을 잡는지, 멀쩡한 것을
잡지 않는지 둘 다 확인하지 않으면 가드를 믿을 근거가 없다.
"""

import pytest

from tools.invariant_guard import RULE_PRICE_READ, scan_repo, scan_source

pytestmark = pytest.mark.invariant

MODULE = "quant_rl_trading/analysts/chart.py"
GATE = "quant_rl_trading/store/prices.py"


VIOLATIONS = {
    "문자열 리터럴": (
        "def features(store, as_of):\n"
        "    return store.get('prices', as_of=as_of, lookback=60)\n"
    ),
    "PRICES 상수": (
        "PRICES = 'prices'\n"
        "def features(store, as_of):\n"
        "    return store.get(PRICES, as_of=as_of, lookback=60)\n"
    ),
    "self.store 경유": (
        "class A:\n"
        "    def features(self, as_of):\n"
        "        return self.store.get('prices', as_of=as_of, lookback=60)\n"
    ),
    "latest_by_entity": (
        "def latest(store, as_of):\n"
        "    return store.latest_by_entity('prices', as_of=as_of)\n"
    ),
}

CLEAN = {
    "헬퍼 경유": (
        "from quant_rl_trading.store.prices import read_prices\n"
        "def features(store, as_of):\n"
        "    return read_prices(store, as_of=as_of, lookback=60)\n"
    ),
    "다른 테이블": (
        "def features(store, as_of):\n"
        "    return store.get('universe', as_of=as_of, lookback=60)\n"
    ),
    "as_of 없는 평범한 dict 조회": (
        "def count(report):\n"
        "    return report.counts.get('prices', 0)\n"
    ),
    "docstring 언급": '"""prices 는 read_prices 로 읽는다."""\nx = 1\n',
}


@pytest.mark.parametrize("source", VIOLATIONS.values(), ids=list(VIOLATIONS))
def test_direct_price_read_is_detected(source: str) -> None:
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_PRICE_READ]
    assert found, f"가드가 직접 시세 읽기를 놓쳤다:\n{source}"


@pytest.mark.parametrize("source", CLEAN.values(), ids=list(CLEAN))
def test_clean_source_is_not_flagged(source: str) -> None:
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_PRICE_READ]
    assert not found, f"오탐:\n{source}\n{found}"


def test_price_gate_itself_is_exempt() -> None:
    """``store/`` 는 게이트 그 자체다. 헬퍼가 자기 규칙에 걸리면 안 된다."""
    source = "def read_prices(store, *, as_of):\n    return store.get('prices', as_of=as_of)\n"
    found = [v for v in scan_source(source, GATE) if v.rule == RULE_PRICE_READ]
    assert not found, found


def test_line_exemption_is_honoured() -> None:
    """읽는 목적이 다른 자리는 줄 면제로 연다. 이유는 주석으로 남는다."""
    source = (
        "def sessions(store, as_of):\n"
        "    return store.get(  # invariant-allow: price-read\n"
        "        'prices', as_of=as_of, columns=['observed_at']\n"
        "    )\n"
    )
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_PRICE_READ]
    assert not found, found


def test_repository_is_clean() -> None:
    violations = [v for v in scan_repo() if v.rule == RULE_PRICE_READ]
    assert not violations, "\n".join(str(v) for v in violations)
