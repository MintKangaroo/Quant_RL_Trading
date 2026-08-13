# M5 — Auditor + ModelOps 착수 프롬프트

시스템이 스스로를 평가하는 층이다.
**Auditor 는 "왜 돈을 벌었나", ModelOps 는 "왜 모델이 잘 되나"를 본다.**

---

## 5-1. Auditor — 성과 귀속

```
quant_rl_trading/auditor/ 를 구현할 계획을 세워줘.

일간·주간 성과를 아래로 분해해:

1. 종목별 기여도
2. 섹터별 기여도
3. **Analyst별 기여도** ← 가장 중요
   각 Analyst 의 점수가 실제 수익에 얼마나 기여했는가
4. 환율 기여도 (원화 기준 평가이므로 분리 필수)
5. 비용 (수수료·거래세·슬리피지·환전)
6. 타이밍 기여 (진입 지연이 도움이 됐는가)

합계가 실제 TWR 수익률과 일치해야 해. 잔차가 남으면 귀속 로직에 버그가 있는 거야.
잔차를 항상 함께 보고해줘.

Analyst별 기여도는 Selector 의 가중치 진화에 피드백돼야 해.
어느 Analyst가 실제로 돈을 벌어줬는지가 여기서 나와.

주의: 귀속은 근사야. 정확한 분해가 불가능한 부분은 "미분류"로 남기고
억지로 배분하지 마.
```

---

## 5-2. ModelOps — 모델 감시

```
quant_rl_trading/modelops/ 를 구현할 계획을 세워줘.

감시 항목:

1. Analyst IC 감쇠
   - 60일 롤링 IC 추이
   - config 의 retrain_ic_floor 아래로 떨어지면 재학습 트리거
   - 알파 소멸 경고

2. 학습 상태 판정
   - explained_variance, approx KL, entropy 추이
   - 0.1 아래 고착 시 rl-training.md §5 원인 목록과 대조

3. 액션 반영률
   - config 의 action_reflection_floor 미만이면 경고
   - 이게 낮으면 RL이 아니라 룰 시스템이야

4. 입력 분포 드리프트
   - 학습 시점 피처 분포 vs 현재 분포 (PSI 또는 KS 통계)
   - 드리프트가 크면 재학습 권고

5. 재학습 스케줄
   - 분기 정기 + 트리거 기반
   - 재학습 후 자동 승격 금지. shadow 를 거쳐 사람이 승인

ModelOps 는 판정만 하고 자동으로 모델을 교체하지 않아.
승격은 항상 사람 승인을 거쳐.
```

---

## 5-3. Claude 리뷰 파이프라인

```
docs/design/reporting.md §4 대로 Claude 리뷰를 붙여줘.
reporting-kickoff.md 의 R-3 와 같은 계층이야. 중복 구현하지 마.

ModelOps 가 오케스트레이션하고, 리뷰 3종을 생성해:
1. 일일 매매 리뷰 — 왜 이 거래가 좋았나/나빴나
2. 학습 진단 — explained_variance·IC·손실곡선을 보고 학습 상태 판정
3. 이상 징후 서술 — 숫자로 안 잡히는 패턴

불변식 8번: **LLM 출력은 보상 함수에 들어가지 않아.**
Claude 는 심판이 아니라 해설자야. 보상에 넣으면 RL이 "돈 버는 법"이 아니라
"Claude 를 설득하는 법"을 배워.

- 입력은 구조화 데이터만. 원본 뉴스 본문 금지
- 출력은 {headline, severity, tags, body} 구조화 형식
- body 전문 + 입력 스냅샷 + 모델 버전 + 토큰 비용을 reviews 테이블에 저장
- 대시보드·리포트에는 headline 한 줄만
- agent_cache 에 저장. 같은 입력이면 캐시 반환, 재호출 금지
- 월 비용이 config 의 monthly_budget_usd 를 넘으면 자동으로 리뷰 중단
```

---

## 5-4. 나머지 화면

```
dashboard.md 규약대로 Attribution, Risk, Regime 화면을 만들어줘.
트레이딩 탭 하위 또는 별도 라우트로. 3탭 구조는 유지해.

Attribution: 종목/섹터/Analyst/환율 기여도 워터폴 + 잔차
Risk: 상관 히트맵, 섹터·팩터 노출, 청산 소요일수 분포
Regime: 현재 레짐, 과거 레짐 타임라인, 레짐별 성과
```

---

## 5-5. M5 완료 확인

```
docs/milestones.md 의 M5 완료 기준을 검증하고 증거를 보여줘.

추가 확인:
- 귀속 합계가 실제 TWR 수익률과 일치하는지 (잔차 크기)
- 리뷰가 agent_cache 에 저장돼 리플레이 시 재호출이 없는지
- LLM 월 비용이 예산 내인지
- Analyst 기여도가 실제로 Selector 진화에 전달되는지
```
