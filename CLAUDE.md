# CLAUDE.md

Quant_RL_Trading — 멀티에이전트 AI 사모펀드.
목표는 **시장보다 덜 잃고 시장보다 더 버는 것**. 한 숫자로 말하면 **정보비율(IR)** 이다.

---

## 불변식 — 위반하면 프로젝트 전체가 거짓이 된다

1. **모든 데이터 접근은 `store.get(table, as_of=...)` 를 경유한다.**
   Parquet 직접 읽기, DuckDB 직접 쿼리 금지.

2. **`datetime.now()` 직접 호출 금지.** 시간은 `Clock` 주입으로만 얻는다.

3. **모든 저장 레코드는 `observed_at` 을 갖는다.** 없으면 저장 거부.
   `valid_from`(사실이 유효해진 시각) 과 `observed_at`(내가 알 수 있었던 시각) 은 다른 필드다.

4. **데이터는 append-only.** UPDATE/DELETE 금지. 정정은 `revision` 을 올린 새 행으로.

5. **백테스트와 라이브는 같은 코드를 쓴다.** Clock만 바꿔 낀다.
   `if backtest:` 같은 분기를 만드는 순간 두 코드는 갈라지고 백테스트는 거짓말이 된다.

6. **Executor 안에는 AI가 없다.** 순수 코드만. 마지막 안전장치는 예측 가능해야 한다.

7. **실현 비중을 Allocator에 되먹인다.** 다음 상태값은 목표 비중이 아니라 **실제 체결된 비중**이다.
   빠지면 RL은 자기가 하지 않은 행동으로 보상받고 학습이 망가진다.

8. **LLM 출력은 보상 함수에 들어가지 않는다.** Claude는 심판이 아니라 해설자다.

9. **모든 대시보드 API는 `as_of` 파라미터를 받는다.** 예외 없음.

10. **임계치는 `store.config` 에서 읽는다.** 하드코딩 금지.
    화면이 12/22/30을 따로 들면 학습 설정과 어긋난다.

---

## 선행 프로젝트 참고 규칙

`LS_KR`(국장) / `LS_USA`(미장) 는 **강화학습이 학습되지 않아 사실상 룰 기반으로 동작한 실패 사례**다.
그냥 "참고해"라고 하면 실패 구조를 그대로 물려받는다.

**배관은 재사용, 두뇌는 새로.**

| ✅ 가져올 것 | ❌ 가져오지 말 것 |
|---|---|
| LS API 인증·토큰 갱신·재시도 | 학습 루프 |
| 주문 전송, 체결·잔고 조회 | 상태값 설계 |
| 응답 파싱·정규화 | 보상 함수 |
| 장 운영시간·휴장일·서머타임 | 액션 공간 |
| 리스크 가드·킬스위치 | 에이전트 구조 |

이식할 때는 커밋 메시지에 출처를 남긴다: `port(collectors): LS_KR 토큰 갱신 이식`

### 재발 방지 지표

> **액션 반영률 = RL이 낸 결정 중 실제로 집행된 비율**

선행 프로젝트가 룰로 전락한 유력 원인은 안전장치가 RL 출력을 덮어쓴 것이다.
매 Session마다 계산해 저장하고, Fund 화면에 상시 표시한다.
**30% 미만이면 경고.** 그건 RL이 아니라 룰 시스템이다.

---

## 금지 사항

- `localStorage` / `sessionStorage` (대시보드)
- 하드코딩된 임계치
- 백테스트 전용 분기
- Analyst가 데이터를 직접 수집하는 것 (Collector만 수집한다)
- News·SNS Analyst에 매도 권한 부여 (매수 금지만 가능)
- 과거 데이터가 없는 신호를 RL 상태값에 넣는 것
- ECharts 외 차트 라이브러리 추가
- 시크릿을 코드에 하드코딩하는 것 (`.env` + Secret Manager)
- 장 중 배포

---

## 개발 원칙

- **M3까지는 RL 없이 돌아가야 한다.** RL은 작동하는 시스템 위에 얹는다
- **NAV 산출은 `quant_rl_trading/accounting/` 한 곳에서만 한다.** 각 모듈이 따로 계산하면 반드시 어긋난다
- 새 Analyst는 IC 0.03을 통과해야 가중치를 받는다. 통과 전에는 관찰 모드(가중치 0)
- 설계를 바꾸면 `docs/design/` 을 먼저 고치고 코드를 고친다
- 커밋 전 `pytest tests/invariants/` 통과 필수

---

## 문서

| 문서 | 내용 |
|---|---|
| `docs/glossary.md` | 에이전트 용어, 패키지 구조 |
| `docs/design/reward-and-risk.md` | 보상 함수, MDD 밴드, 자본 단계 |
| `docs/design/data-contract.md` | 이중시간 저장, 데이터 게이트, 검증 테스트 |
| `docs/design/agents.md` | 에이전트 명세, Signal 스키마 |
| `docs/design/accounting.md` | NAV·TWR·배당·세금 — 보상 함수의 r_port 정의 |
| `docs/design/selector.md` | Analyst 가중치 진화, 후보 선정 |
| `docs/design/portfolio-construction.md` | 비중 산출 — 섹터 하방베타, 팩터 공분산, 리스크 기여 균등, RL 과의 충돌 처리 |
| `docs/runbook.md` | 배포, 장애 등급, 킬스위치, 복구 |
| `docs/design/rl-training.md` | RL 학습 절차, 진단, 하이퍼파라미터 |
| `docs/design/reporting.md` | 리포트 3종, 이메일 제약 |
| `docs/design/dashboard.md` | 3탭 화면 명세, 밀도 규칙, API 규약 |
| `docs/design/config.md` | 모든 임계치의 단일 소스 (config/quant_rl_trading.yaml) |
| `docs/design/self-improvement.md` | 자기개선 루프 — 홀드아웃 금고, 사전등록, 시행 예산, DSR |
| `docs/design/ls-api.md` | LS API 제약 확인 목록 |
| `docs/milestones.md` | M1~M5, 완료 기준, 중단 기준 |
| `START-HERE.md` | 전체 실행 순서, 부트스트랩 프롬프트 |
