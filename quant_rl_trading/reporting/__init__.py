"""시황 브리핑 — 하루 1통.

**매매 리포트가 아니다.** 실매매가 시작되기 전까지 이 메일에는 성과·수익률·
보유 섹션이 없다. 빈 표를 내보내면 읽는 사람이 그것을 "손실 0" 으로 읽기
때문이다 — 없는 것을 0 으로 그리는 것은 이 저장소가 가장 경계하는 실패다.

여기 있는 것은 시황이다. 지수, 오른 종목, 환율, 거시지표, 공시.

``docs/design/reporting.md`` 의 마감 리포트(4개 섹션)와는 **다른 리포트다.**
그쪽은 매매·학습이 돌기 시작한 뒤의 것이고, 이쪽은 그 전에도 매일 나갈 수
있는 것이다. 나중에 매매 섹션이 붙으면 같은 메일 안에서 위에 얹는다.
"""

from quant_rl_trading.reporting.briefing import Briefing, build_briefing
from quant_rl_trading.reporting.sessions import SessionRef, resolve_sessions

__all__ = ["Briefing", "SessionRef", "build_briefing", "resolve_sessions"]
