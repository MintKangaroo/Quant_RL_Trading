# 사전등록 — Analyst 재료 2건: chart/volume 피처 교체 · event 변형 (2026-08-29 등록)

측정 전에 고정한다. 결과를 보고 기준을 바꾸지 않는다 (self-improvement.md §1②).
등록 계기: 2026-08-29 학습 탭 "매매에 쓰이는 애널리스트 2 / 잰 것 8". 후보에 알파가 없으면
RL 이 배울 것이 "무엇을" 이 아니라 "언제" 뿐이고, 그것은 구간에 묶인다(rl-training.md 2회차 판정).

## 공통
- 데이터: `data/_diag` 피처 캐시(KR 300세션) 또는 같은 규격으로 새로 굽는다. **판정은
  2026-06-30 이전 세션만**(홀드아웃 금고 2026-07-01~ 은 열지 않는다).
- 유니버스: `tradable-KR.pkl`. IC: 일별 Spearman, Newey-West t (lag = horizon−1).
- 기준선: 현행 결합 점수(fundamental 1.0 · event 0.0 · risk 제약). 한계기여는
  `ic.marginal_shares` 규칙 그대로(측정 도구 `tools/measure_ic.py` 의 것).
- 시행 수: 아래 2건 = research_trials 2회 (family `analyst`). 실행 시점: 2026-09-01 이후,
  8/29 IC 재측정 결과가 적재된 뒤(그 결과가 기준선이다).
- 부호는 여기 적은 대로 고정. 재고 나서 뒤집어 쓰지 않는다.

## 시행 A — chart/volume 피처 교체
현행 chart 5개(momentum_20/60 · reversal_5 · ma_gap · range_position)는 전수 측정에서 전부
유의하지 않았고, 한때의 IC 는 섞여 있던 low_volatility 에서 빌린 것이었다
(docs/ic-diagnosis.md). volume 은 `volume_surge` 하나가 t 2.18 로 살아남았다.

- A1 chart 후보 피처 (부호 사전 고정):
  | 피처 | 정의 | 부호 |
  |---|---|---|
  | `momentum_12_1` | 252일 수익률에서 최근 21일을 뺀 것 (t−252 → t−21) | **+** |
  | `reversal_21` | 21일 수익률 | **−** |
  | `high_52w` | 종가 / 252일 최고가 | **+** |
  | `idio_momentum_120` | 120일 수익률에서 KOSPI 120일 수익률 × 베타를 뺀 것 | **+** |
  각각 h5·h20 IC 와 NW t 를 잰다. BH FDR 10%(4개).
- A2 volume: 죽은 피처를 빼고 `volume_surge` 단독 + 정규화 변형(로그, 60일 z) 셋 중 h5 t 가
  가장 큰 것 하나. 셋 다 t < 2 면 volume 은 관찰 유지.
- 결합: A1 에서 통과한 피처만 부호 가중으로 chart 점수를 만들고, fundamental 위 **한계기여**
  ΔIC(h5) 를 잰다.
- **채택 기준**: 피처 t(NW) ≥ 2.0 이고 BH 통과 **그리고** 결합 한계기여 ΔIC(h5) NW t ≥ 2.0,
  상위 24 동일가중 h5 초과수익이 기준선 이상. 개별 IC 만 좋고 한계기여 0 이면 기각(event 의 선례).
- 채택 시 코드 반영: `analysts/chart.py` 피처 교체 + `docs/feature-registry.md` 갱신. 가중치는
  관찰 모드(0)에서 시작해 다음 IC 측정에서 통과해야 받는다(CLAUDE.md 개발 원칙).

## 시행 B — event 변형: fundamental 과 겹치지 않는 부분만 남긴다
event 는 IC 0.039 로 합격선을 넘지만 fundamental 위 한계기여가 0 이다(겹친다).

- B1 직교화: 세션별 횡단면에서 event 점수를 fundamental 점수에 회귀한 **잔차**를 event' 로.
  잔차의 IC(h5·h20) 와 fundamental 위 한계기여 ΔIC(h5).
- B2 사건 창 한정: 사건(발행·자사주·배당·만기) 뒤 20세션 안에서만 점수를 내고 그 밖은 0.
  같은 지표.
- **채택 기준**: B1 또는 B2 의 한계기여 ΔIC(h5) NW t ≥ 2.0 이고 h20 이 나빠지지 않을 것(t > −2),
  상위 24 순IR(회전 비용 0.41%/편도 차감)이 기준선 이상. 둘 다 미달이면 event 는 관찰 유지
  (지금과 같음) — 코드에 넣지 않는다.

## 하지 않는 것
결합 방식(가중 합) 변경, 비선형 랭커(2회 기각), flow_kr 재시도(구현 결함 수정 전엔 재지 않는다),
regime 을 랭커로 쓰는 것(노출 조절 자리에 있다), 홀드아웃 개봉.

## 결과 기록
`research_trials` 에 family `analyst` 로 2행(시행 A·B 각 1회). 결과 표는 이 문서 아래에 붙인다.
