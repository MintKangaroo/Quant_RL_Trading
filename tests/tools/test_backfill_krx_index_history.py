"""KRX 통합지수 구멍 메우기 — run key 가 실전 패널과 갈라져 있는지(겹치면 다시 건너뛴다)."""
from quant_rl_trading.collectors.panels import OPENAPI_PANELS, PANELS
from tools import backfill_krx_index_history as tool


def test_run_key_differs_from_live_panels():
    keys = {p.key for p in [*PANELS.values(), *OPENAPI_PANELS.values()]}
    assert tool.RUN_KEY not in keys


def test_live_panel_keys_untouched():
    # 실전 패널의 run key 를 바꾸면 매일 실행이 최근 세션을 새 run id 로 다시 쌓는다 — 이 도구는 그걸 피하려고 있다.
    assert OPENAPI_PANELS["indices-krx"].key == "indices"
    assert OPENAPI_PANELS["indices-board"].key == "indices-board"
