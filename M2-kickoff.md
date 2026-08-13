# M2 — Analyst 착수 프롬프트

`docs/design/agents.md` 를 먼저 읽힌다. **알파가 실제로 나오는 층이다.**
M1(store + replay + 백필)이 완료된 뒤 시작한다.

각 단계: plan mode → 계획 검토 → 구현 → 테스트 출력 → 커밋 → `/clear`

---

## 2-1. Signal 스키마와 기반 클래스

```
docs/design/agents.md §1 을 읽고 quant_rl_trading/schemas/ 와 Analyst 기반 클래스를
구현할 계획을 세워줘. 아직 개별 Analyst 는 만들지 마.

Signal (pydantic):
signal_id, session_id, analyst, analyst_version, entity_id, as_of,
score(-1~1), confidence(0~1), horizon_days, features_hash, evidence[], latency_ms

score 정의는 하나로 통일해:
  score = tanh(예측 초과수익의 횡단면 z-score / 2)
  → horizon_days 뒤, 같은 시장 종목들 대비 얼마나 더 오를 것인가

confidence 는 Analyst 가 스스로 매기지 않아.
최근 60일 롤링 IC 로 자동 계산해. 스스로 매기게 하면 과신해.

BaseAnalyst:
- 입력은 store.get(as_of=...) 경유만. 직접 수집 금지 (Collector만 수집한다)
- features_hash 계산 (리플레이 캐시 키)
- latency_ms 기록
- 실패 시 예외를 삼키지 말고 Signal 을 내지 않는다 (죽은 Analyst = 중립 0 처리)

Verdict (News/SNS 전용):
entity_id, verdict("block"|"pass"), severity, category, reason, expires_at
```

---

## 2-2. IC 측정 파이프라인 ⭐ Analyst보다 먼저

```
Analyst 를 만들기 전에 평가 도구를 먼저 만들어줘.
평가할 수 없으면 만들어도 쓸 수 없어.

quant_rl_trading/analysts/evaluation.py:

- 타깃: 5일 후 횡단면 초과수익 z-score
- IC = Spearman rank correlation (score, 실제 초과수익)
- 60일 롤링 IC, 누적 IC, IC IR (평균/표준편차)
- purged K-fold + embargo 5일
- 국장/미장 별도 측정
- 표본 200거래일 이상에서만 유효 판정
- 합격선 IC 0.03 (OOS)

검증 테스트:
1. 랜덤 점수를 넣으면 IC 가 0 근처인지
2. 정답(미래 수익률)을 그대로 넣으면 IC 가 1에 가까운지
   ← 이게 안 나오면 측정 코드가 틀린 거야
3. 5일 미래를 미리 보는 피처를 넣었을 때 purge/embargo 가 이를 걸러내는지

주의: 백테스트 IC 가 0.15 처럼 나오면 재능이 아니라 버그야.
먼저 데이터 누수를 의심하도록 경고 로직을 넣어줘.
```

---

## 2-3. Chart Analyst

```
agents.md §2 의 Chart Analyst 를 구현할 계획을 세워줘.

모델: LightGBM 회귀. 갱신 5분.

피처 (30개 이하로 제한):
- 추세: MA 이격도(5/20/60/120), 정배열 여부, ADX
- 모멘텀: 수익률(1/5/20/60/120일), RSI14, MACD 히스토그램
- 변동성: ATR/가격, 실현변동성(20/60), 볼린저 %B·밴드폭
- 거래량: 거래량 z-score, OBV 기울기, 거래대금 회전율
- 위치: 52주 고저 대비 위치, 신고가 갱신

주의:
- 기술지표는 상호 상관이 0.9 를 넘어. 100개 넣으면 트리가 같은 정보를
  100번 쪼개면서 과적합해. 30개 이하로 자르고 SHAP 으로 정리해줘
- 모든 피처는 같은 시장 내 횡단면 z-score. 국장과 미장을 섞지 마
- 학습은 국장/미장 별도

완료 후 IC 를 측정하고 결과를 보여줘. SHAP 상위 피처도 함께.
```

---

## 2-4. Flow Analyst (국장/미장 별도 모델)

```
agents.md 의 Flow Analyst 를 구현할 계획을 세워줘.
flow_kr 과 flow_us 는 별도 모델이야. 데이터가 아예 달라서 같은 모델을 못 써.

flow_kr (데이터 풍부 — 알파의 주요 원천):
- 투자자별 순매수: 외인/기관/개인/연기금/사모/투신/보험
- 시총 대비 비율로 정규화, 1/5/20일 누적
- 외인 지분율 변화, 공매도 비중, 대차잔고 증감, 신용잔고율
- 주체 일치도 (외인+기관 동반 매수)

flow_us (데이터 희박 — 투자자별 수급 공시가 없음):
- short interest (격주), 13F (분기·지연 큼)
- ETF 자금흐름, 옵션 put/call·미결제약정

주의:
- 장중 잠정치와 마감 확정치는 observed_at 이 달라. 별도 행으로 저장돼 있어야 해
- 잠정치를 확정치로 오인하면 look-ahead 야

IC 를 각각 측정해줘. flow_us 가 0.03 을 못 넘을 가능성이 높아.
못 넘으면 미장에서만 가중치 0 으로 두고 국장에서는 사용해.
억지로 통과시키려고 하지 마.
```

---

## 2-5. Fundamental Analyst

```
agents.md 의 Fundamental Analyst 를 구현할 계획을 세워줘.

피처:
- 밸류: PER, PBR, PSR, EV/EBITDA, FCF yield
- 퀄리티: ROE, ROIC, 영업이익률, 부채비율, 발생액
- 성장: 매출·영업이익 YoY/QoQ, 3년 CAGR
- 서프라이즈: 컨센서스 대비 괴리, 추정치 리비전

주의:
- 밸류 지표는 절대값이 무의미해. 반도체 PER 15 와 은행 PER 15 는 다른 얘기야.
  섹터 내 백분위로 변환해줘
- DART 정정공시 처리 확인. 공시일 기준이지 회계기간 종료일 기준이 아니야
- 추정치 리비전은 가장 강한 팩터 중 하나야. 컨센서스 데이터를 구할 수 있으면
  반드시 넣어줘. 없으면 그렇다고 보고해줘

타깃은 5일로 통일해. 재무는 느린 신호라 IC 가 낮게 나오는 게 정상이야.
5일에서 0.03 을 못 넘으면 기준을 낮추지 말고 horizon 20일 버전을
별도 모델로 추가해줘.
```

---

## 2-6. News Analyst (필터)

```
agents.md §3 의 News Analyst 를 구현할 계획을 세워줘.
점수를 내지 않아. Verdict 만 낸다.

차단할 것 (사실 기반 구조적 악재만):
회계부정·감사의견거절 / 횡령·배임 / 상폐 실질심사 / 거래정지 /
대규모 소송 패소 / 리콜 / 유상증자·CB 발행(희석) / 최대주주 매도 / 실적 쇼크

차단하지 않을 것:
단순 주가 하락 기사 / 목표가 하향 / 일반적 부정 논조

전부 차단하면 살 종목이 안 남아. 이 구분이 이 Analyst 의 전부야.

제약:
- 매수 금지만 가능. 매도 권한 없음
  (오작동해도 기회를 놓칠 뿐 손실이 확정되지 않아)
- expires_at 필수, 기본 3~5거래일. 영구 차단 금지
- 하루 거부 상한: 후보의 30%
- 2단계 호출: 저비용 모델 스크리닝 → 의심 건만 고성능 모델
- 결과를 agent_cache 에 저장. 리플레이 시 재호출 금지

차단한 종목의 이후 5일 수익률을 추적하는 성적표 테이블도 만들어줘.
이게 이 Analyst 의 유일한 평가 수단이야 (IC 로는 평가할 수 없어).
```

---

## 2-7. SNS Analyst (펌핑 탐지)

```
agents.md 의 SNS Analyst 를 구현할 계획을 세워줘.

긍정 신호는 절대 쓰지 마. 언급량 폭증은 대부분 이미 오른 뒤이거나 작전이야.
유일한 용도는 펌핑 탐지야:

- 언급량 z-score 급등
- + 신규·저품질 계정 비율 급증
- + 다채널 동시 게시 패턴
- + 감성 극단성 (반박 없는 일방적 찬양)

위 조건이 겹칠 때만 verdict="block", category="pump_suspected".

News Analyst 와 동일한 제약 (매수 금지만, expires_at, 거부 상한, 캐시).
성적표도 동일하게.
```

---

## 2-8. 지원 Analyst 3종

```
Regime, Event, Risk 를 구현할 계획을 세워줘.

Event 부터 만들어줘. 모델이 없어서 제일 쉽고 효과가 확실해:
- 실적발표 D-day, 배당락, 옵션만기, FOMC·금통위, 락업해제, 지수 리밸런싱
- 각 종목의 D-day 를 상태값으로 제공

Regime:
- HMM 또는 룰. 상태 4~6개 (강세/약세/횡보/고변동/위기)
- 입력: 지수 수익·변동성(VKOSPI, VIX), 금리, 신용스프레드, 시장 폭(등락 종목 비율)
- 레짐 전환이 너무 잦으면 노이즈야. 최소 지속 기간을 두는 걸 검토해줘

Risk:
- 60일 EWMA 상관행렬
- 섹터·팩터(시총/밸류/모멘텀/변동성) 노출
- 유동성 (청산 소요일수)
```

---

## 2-9. 통합과 M2 완료 확인

```
9개 Analyst 를 한 Session 파이프라인으로 묶어줘.

- 병렬 실행, 개별 타임아웃
- 죽은 Analyst 는 중립 0 처리하고 계속 진행 (전체 정지 아님)
- 전 Analyst 의 Signal 을 저장 (IC 0.03 미통과분도 관찰 모드로 계속 기록)
- 실행 시간을 단계별로 기록 → 지연 실측에 반영

그 다음 docs/milestones.md 의 M2 완료 기준을 하나씩 검증하고,
실행 명령과 출력을 증거로 보여줘.

마지막에 subagent 로 전체 Analyst 코드를 검토해줘:
- store.get 을 거치지 않는 데이터 접근이 있는지
- 국장/미장 피처가 섞인 곳이 있는지
- 미래 데이터를 참조하는 지점이 있는지
요구사항 위반만 지적하고 스타일은 빼줘.
```
