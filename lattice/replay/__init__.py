"""Clock, 이벤트 로그, 체결 시뮬레이터.

Clock 프로토콜과 LiveClock / ReplayClock 이 여기 산다.
벽시계를 읽는 지점은 ``clock.py`` 한 곳뿐이며, 그 라인에는
``# invariant-allow: wallclock`` 주석이 붙는다.

백테스트와 라이브는 같은 코드를 쓴다. Clock 만 바꿔 낀다.
"""
