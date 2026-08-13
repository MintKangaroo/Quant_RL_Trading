# M4 — Allocator(RL) 착수 프롬프트 (상세)

`docs/design/rl-training.md` 를 먼저 읽힌다.
**M3 룰 베이스라인이 실전에서 돌고 있는 상태에서 시작한다.** RL이 실패해도 운용은 멈추지 않는다.

각 단계: plan mode(Shift+Tab) → 계획 검토 → 구현 → 테스트 출력 확인 → 커밋 → `/clear`
**한 단계씩만 던진다.** 여러 단계를 묶으면 어디서 깨졌는지 알 수 없게 된다.

---

## 4-1. 환경 스켈레톤

```
docs/design/rl-training.md §1 을 읽고 quant_rl_trading/allocator/env.py 구현 계획을 세워줘.
아직 구현하지 마.

Gymnasium 호환 LatticeEnv:

관측 (Dict space)
- portfolio: (24,) float32 — §1 표의 항목 그대로
- assets: (30, 28) float32
- mask: (30,) bool — 유효 후보 표시

액션 (Dict space)
- weights: (31,) — Dirichlet, 마지막 슬롯이 현금
- delay: (30,) — Categorical(4)
- fx_alloc: (1,) — Beta, 주간 스텝에서만 유효

info 에 매 스텝 반드시 담을 것:
realized_weights, target_weights, action_reflection_rate, cost, drawdown, turnover

에피소드: 250 거래일. 시작일은 학습 구간에서 무작위 샘플.
terminated = 낙폭 30% 초과, truncated = 250일 도달.

중요:
- 모든 데이터는 store.get(as_of=...) 경유. 직접 조회 금지
- 시간은 ReplayClock 주입. datetime.now() 금지
- 체결은 M1의 replay 시뮬레이터를 그대로 쓴다. 별도 구현 금지
- 후보가 30개 미만이면 패딩하고 mask 로 가린다

테스트도 같이 계획해줘:
- reset/step 이 스펙대로 동작하는지
- 같은 시드로 두 번 돌리면 궤적이 동일한지
- mask 된 슬롯에 비중이 배정되지 않는지
```

---

## 4-2. 보상 함수

```
rl-training.md §3 대로 보상 함수를 구현할 계획을 세워줘.

r_t = (r_port − r_bench) − w(d_t)·Δd_t − cost_t

- w(d): d<12% → 0 / 12~22% → 1.5 / 22~30% → 8.0 / ≥30% → 종료 + 큰 음수
- Δd 는 신저점 갱신 시에만 양수
- 벤치마크는 KOSPI 50 + S&P500(원화환산) 50 고정
- 모든 계수는 store.config("reward") 에서 읽어. 하드코딩 금지

리턴 정규화를 반드시 넣어줘 (§3).
일간 초과수익은 0.001 규모라 정규화 없이는 가치함수가 학습되지 않아.
- 할인 리턴의 running std 로 나누는 VecNormalize 방식
- 학습 중에만 통계 갱신, 평가 시 고정
- 보상에 임의의 상수를 곱하는 방식은 쓰지 마. 낙폭 페널티와의 비율이 깨져

단위 테스트:
- 낙폭 11.9% → 12.1% 이동 시 페널티 변화가 정확한지
- 신저점 미갱신일 페널티가 0인지
- 30% 초과 시 terminated 인지
- 왕복 비용이 국장 0.2~0.35% 범위인지
- 정규화 on/off 시 보상 스케일 차이를 수치로 보여줘
```

---

## 4-3. 정책 네트워크

```
rl-training.md §2 대로 quant_rl_trading/allocator/policy.py 를 구현할 계획을 세워줘.

구조:
- per-asset MLP (28→128)
- portfolio MLP (24→128) → CLS 토큰으로 삽입
- Transformer Encoder ×2 (d=128, heads=4, ff=256)
- **위치 인코딩 없음** ← 순열 불변성의 핵심
- 패딩 마스크를 attention 과 헤드 양쪽에 적용
- 인코더는 정책·가치가 공유, 헤드만 분리
- 직교 초기화, 정책 마지막 층 gain 0.01

헤드:
- weights: per-asset (128→1) + cash logit
  → concentration = softplus(logits) + 1e-3, clamp(max=1e3)
  → Dirichlet
- delay: per-asset (128→4) Categorical
- value: attention pooling → MLP → scalar

Dirichlet 을 쓰는 이유는 §1에 있어. softmax+Gaussian 으로 바꾸지 마.

반드시 포함할 테스트:
1. 순열 불변성 — 종목 순서를 섞어도 출력 동일 (오차 1e-5)
2. 마스킹 — 패딩 슬롯의 비중이 0에 수렴하는지
3. Dirichlet 샘플이 심플렉스 위에 있는지 (합 1, 모두 ≥0)
4. log_prob 이 유한한지 (concentration 극단값에서 NaN 안 나는지)
```

---

## 4-4. 오라클 카나리 ⭐ 최우선 관문

```
rl-training.md §0 을 읽어. 실제 학습 전에 배선이 살아 있는지 증명하는 단계야.

env config 에 oracle_leak 플래그를 추가해줘:
- 기본값 False
- True 면 assets 피처 한 칸에 "5일 후 실제 초과수익"을 그대로 넣어
- True 로 켜지면 로그에 크게 경고 출력 (실수로 실제 학습에 켜지면 안 되니까)

이 상태로 PPO 를 200k 스텝 짧게 돌리고 결과를 보여줘.

합격 기준:
- explained_variance > 0.5
- 정책이 오라클 피처를 실제로 이용하는지 (그래디언트 기여도 상위)

합격하면 → 배선 정상. 다음 단계로
불합격하면 → 시장이 아니라 코드 문제야. rl-training.md §5 의 6가지 원인을
①부터 순서대로 배제해줘. 각 배제 결과를 docs/rl-diagnosis.md 에 기록해.

**불합격 상태로 다음 단계로 넘어가지 마.**
tests/rl/test_oracle_canary.py 로 영구 보존해줘.
```

---

## 4-5. PPO 학습 루프

```
quant_rl_trading/allocator/train.py 를 구현할 계획을 세워줘.

rl-training.md §4 의 하이퍼파라미터를 config 기본값으로:
num_envs 32, n_steps 512, minibatch 2048, n_epochs 10,
gamma 0.997, gae_lambda 0.95, clip 0.2, clip_vf 0.2,
ent_coef 3e-3(선형감쇠), vf_coef 0.5, max_grad_norm 0.5,
lr_policy 1e-4(선형감쇠), lr_value 3e-4, target_kl 0.02

주의:
- gamma 를 0.99 로 낮추지 마. 유효 지평 100스텝이면 250일 낙폭을 못 봐
- Dict observation 을 다루는 rollout buffer 가 필요해
- target_kl 초과 시 해당 업데이트 조기 종료
- 체크포인트에 옵티마이저 상태와 정규화 통계까지 포함

재현성 (§11):
torch.use_deterministic_algorithms(True)
CUBLAS_WORKSPACE_CONFIG=":4096:8"
run_id, seed, git commit hash, config 스냅샷 저장

로깅은 로컬 JSONL + Parquet 로. 외부 서비스 의존 없이.
§10 표의 지표를 전부 기록해줘.
```

---

## 4-6. 커리큘럼 C1~C2

```
rl-training.md §6 의 C1, C2 를 실행해줘.

C1: 국장만, 후보 10종목, 비중 액션만, 비용 0
    통과 기준 — explained_variance > 0.1, 동일가중 초과
C2: 비용·라운딩 추가
    통과 기준 — EV 유지, 회전율 안정

각 단계 5개 시드로 돌리고, §10 지표를 전부 보여줘.
학습 곡선과 EV 추이를 그래프로.

C1 에서 깨지면 멈춰. 비용이 0인데도 학습이 안 되면
비용 모델 문제가 아니라 더 근본적인 문제야.
C2 에서 깨지면 비용 모델을 의심해.

어느 단계에서 깨졌는지를 명확히 보고해줘. 다음으로 넘어가지 마.
```

---

## 4-7. 커리큘럼 C3~C5

```
C3, C4, C5 를 순서대로 실행해줘.

C3: 후보 30종목, 진입 지연 액션 추가
C4: 미장 추가, 현금 KRW/USD 분리
    ← 환율 피처가 실제로 쓰이는지 확인해줘. 안 쓰이면 원화 기준 평가의
      자연 헤지 효과를 정책이 학습하지 못한 거야
C5: KR/US 주간 배분 추가
    통과 기준 — 스코어 비례 대비 IR 우위

표본 증강도 켜줘 (§7):
겹치는 윈도우(5거래일씩), 종목 부트스트랩, 병렬 환경 32개,
피처 드롭아웃(Analyst 점수 확률적 마스킹).

각 단계마다 액션 반영률을 확인해줘. 30% 미만이면 학습이 아니라
Executor 쪽 배선을 고쳐야 해.
```

---

## 4-8. walk-forward 평가

```
rl-training.md §8 의 walk-forward 프로토콜을 구현해줘.

[학습 2년] [purge 5일] [검증 6개월] [embargo 5일] [테스트 6개월]
6개월씩 전진, 최소 4폴드.

규칙:
- 하이퍼파라미터는 검증 폴드에서만 결정
- 테스트 구간은 최종 보고 때 딱 한 번만 본다
- 모든 지표는 3~5시드 중앙값. 단일 시드 성과는 보고하지 마

폴드별 결과와 이어붙인 전체 결과를 표로 보여줘.
폴드 간 성과 편차가 크면 그것도 보고해줘 — 레짐 의존성이 크다는 뜻이야.
```

---

## 4-9. 하이퍼파라미터 튜닝

```
rl-training.md §9 대로 Optuna 파이프라인을 만들어줘.

탐색 (중요도 순):
1. 리턴 정규화 방식 (none / return_std / popart)
2. lr_policy 3e-5~3e-4 로그
3. ent_coef 1e-4~1e-2 로그
4. gae_lambda 0.9~0.99
5. gamma 0.995~0.999

절대 튜닝하지 말 것:
- 보상 함수 계수(12/22/30, w) — 투자철학이지 하이퍼파라미터가 아님
- 비용 모델 — 실비
- 에피소드 길이 — MDD 정의에 묶여 있음

규칙:
- MedianPruner, 예산 50~100 trial
- 목적함수 = 검증 폴드 IR 의 3시드 중앙값
- 테스트 구간 절대 참조 금지

튜닝 후 반드시 보고해줘:
시드 간 성과 분산이 하이퍼파라미터 간 차이보다 큰지.
크면 그 결과는 노이즈니까 채택하면 안 돼. 솔직하게 그렇게 말해줘.
```

---

## 4-10. 어블레이션

```
어느 설계 결정이 실제로 기여했는지 확인해줘. 각각 끄고 재학습:

1. 리턴 정규화 off
2. 위치 인코딩 추가 (순열 불변성 파괴)
3. 실현 비중 되먹임 off → 목표 비중만 되먹임
4. Transformer → 단순 flatten MLP
5. Dirichlet → softmax + Gaussian + 클리핑
6. 낙폭 자유구간 12% → 0% (선형 페널티)

각각의 EV 와 IR 변화를 표로.

기여가 없는 요소는 제거를 검토해. 다만 3번(되먹임)과 6번(자유구간)은
차이가 작아 보여도 유지해줘 — 각각 불변식과 투자철학이야.
```

---

## 4-11. 베이스라인 대결

```
학습된 Allocator 를 네 베이스라인과 비교해줘 (§12).

1. 혼합 벤치마크
2. 동일가중
3. 스코어 비례 (M3 룰) ← 진짜 경쟁자
4. 랜덤 정책 (하한 확인용)

항목: IR, CAGR, MDD, 회전율, 승률, 액션 반영률.
전부 테스트 폴드에서, 3~5시드 중앙값으로.

스코어 비례를 IR 에서 이기지 못하면 RL 을 쓸 이유가 없어.
그 경우 솔직하게 그렇게 보고해줘.
억지로 이기게 만들려고 보상 함수나 평가 방식을 바꾸지 마.
```

---

## 4-12. shadow 운용

```
승격 전 shadow 를 붙여줘 (§13).

- 20거래일간 실제 주문 없이 결정만 기록
- 룰 베이스라인과 병주. 같은 신호에 대해 두 정책의 결정을 나란히 저장
- 매일 차이를 집계: 종목 선택 차이, 비중 차이, 예상 손익 차이
- 액션 반영률을 shadow 에서도 측정

승격 체크리스트를 만들어줘:
□ 검증 폴드 IR 이 스코어 비례 대비 우위
□ OOS MDD 20% 이내
□ 액션 반영률 30% 이상
□ shadow 20거래일 무사고
□ 사람 승인

승격 후에도 룰 베이스라인은 계속 돌아가야 해. 언제든 되돌릴 수 있게.
```

---

## 4-13. 학습 탭

```
docs/design/dashboard.md §5 대로 학습 탭을 만들어줘.
dashboard-kickoff.md 의 D-4 를 참고해.

이 탭의 결론 패널은 "베이스라인 대비 IR" 이야.
스코어 비례를 못 이기면 RL 을 쓸 이유가 없다는 걸 항상 보여줘.
```

---

## 실패 시

```
rl-training.md §14 의 중단 규칙을 적용해줘.

재정식화 3회를 시도했는데도 EV 가 0 근처라면 더 시도하지 말고:
1. M3 스코어 비례 베이스라인이 정상 운용 중인지 확인
2. §5 의 6가지 원인 중 어디까지 배제했는지 정리
3. 남은 후보를 좁힌 보고서를 docs/rl-postmortem.md 로 작성

보상 함수를 바꿔가며 될 때까지 시도하는 건 금지야.
그게 선행 프로젝트가 9차까지 간 방식이야.
```

---

## 긴 세션 중 주기적으로

```
지금까지의 RL 코드가 CLAUDE.md 불변식과 rl-training.md 규칙을
위반하지 않는지 점검해줘. 특히:
- 불변식 7 (실현 비중 되먹임)
- 순열 불변성 테스트가 여전히 통과하는지
- 보상 계수가 store.config 에서 읽히는지
- oracle_leak 플래그가 실수로 켜져 있지 않은지
```
