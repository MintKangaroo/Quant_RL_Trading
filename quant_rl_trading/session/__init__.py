"""Session — 하루의 의사결정 사이클.

    07:00 Collector    미장 마감·환율·야간 뉴스
    08:00 store        universe 스냅샷 + 데이터 품질 게이트
    08:30 Analyst      Signal 생성
    08:50 Selector     후보 선정
    08:55 Allocator    목표 비중 (M3 는 룰 베이스라인)
    09:30 Executor     주문 집행
    15:40 accounting   NAV·TWR·낙폭 스냅샷

``daily.run`` 이 08:50~09:30 구간(선정→배분→집행)을 한 번에 돈다. 수집과
Signal 생성은 이미 `tools/run_daily.py` 가 하고, 회계 스냅샷은
`accounting.snapshot` 이 한다.

**전 단계를 이벤트 로그에 append-only 로 남긴다.** 같은 as_of 로 리플레이하면
같은 주문이 나와야 하고, 안 나오면 백테스트가 거짓말이 된다 (불변식 5).
"""

from quant_rl_trading.session.daily import DailySession, market_stats, run

__all__ = ["DailySession", "market_stats", "run"]
