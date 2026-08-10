"""Analyst ×9 — chart, flow_kr, flow_us, fundamental, news, sns, regime, event, risk.

Signal(score, confidence, horizon, evidence) 을 낸다.
데이터를 직접 수집하지 않는다 — Collector 가 적재한 것을 store 로 읽는다.
새 Analyst 는 IC 0.03 을 통과하기 전까지 가중치 0(관찰 모드)이다.
"""
