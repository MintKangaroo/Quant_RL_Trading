# 사전등록 — 시행 L: 순위 목적 GBM 랭커 (2026-09-03, 정식 시행 · family `ranker` · 1회)

진단 D1~D7(pooled-ranker-2026-09.md)이 말한 것: 랭커가 진 원인은 **목적함수**였다(수익률 MSE ≠ 일별 순위 IC). D7 은
진단이라 채택 근거가 아니다 — 여기서 **같은 설정을 고정하고 한 번** 잰다. 통과하면 `ranker` Analyst(관찰 모드)로 배선.

## 고정
- 데이터: pooled-ranker 와 동일 — Analyst 점수 6개(chart·event·flow·fundamental·regime·risk) + 시장 원핫. 국장 271세션 +
  미장 508세션. **타깃·피처 전부 세션×시장 안 순위 정규분위(rank-gauss)**, 결측은 0. 홀드아웃(2026-07~) 안 연다.
- 모델: LightGBM 잎 7 · min_data 2,000 · lr 0.03 · 300 라운드 · bagging 0.8 · feature_fraction 1.0 · L2 1.0 · seed 0.
  학습 = 국장+미장 합침(GBM-POOL). 시드 앙상블 없음(하나로 잰다).
- 워크포워드: 국장 판정 블록 = 랭커 B 와 동일(확장창 ≥150, 20세션 예측, 퍼지 5 → 5블록 100세션). 미장은 같은 날짜 블록.

## 채택 기준 (국장 판정 블록) — 넷 다 통과해야 채택
① ΔIC(h5) vs fundamental 의 일별 차이 NW t ≥ 2.0 ② 상위 24 동일가중 h5 z-수익 ≥ 대조 ③ 최악 20세션 블록 ΔIC ≥ −0.03
④ 미장 판정 블록 ΔIC vs fundamental ≥ 0 (다른 시장에서도 안 지는가).
함께 적되 기준 아님: 피처 gain 순위, 시장별 IC, 상위24 (③②는 꼬리 지표라는 것을 안다 — 그래도 등록된 기준이므로 그대로 잰다).

## 채택 시
`analysts/ranker.py`(관찰 모드, 가중치 0)로 배선 → `analyst_weights` 한계기여 규칙이 가중을 정한다 → shadow → 모의계좌.
랭커는 fundamental 을 **대체**하는 알파다(risk 제약은 그대로). 기각이면 이 분기엔 랭커를 다시 열지 않는다.
