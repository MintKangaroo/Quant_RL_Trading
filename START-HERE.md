# START HERE

Quant_RL_Trading 개발 전체 순서. 위에서부터 차례로 실행한다.

---

## 0. 한 번에 다 던지지 않는 이유

실제 돈이 걸린 시스템이다. 한 세션에 여러 단계를 몰아넣으면
**어디서 깨졌는지 특정할 수 없게 된다.** 선행 프로젝트가 9차 재정식화까지 간 이유가
정확히 이것이었다.

대신 아래 **부트스트랩 프롬프트**를 쓰면, Claude Code 가 순서를 스스로 관리하면서
단계마다 멈춰 승인을 받는다. 붙여넣는 것은 한 번이고, 진행은 단계적이다.

### 부트스트랩 프롬프트 (첫 세션에 한 번)

```
이 레포는 Quant_RL_Trading — 멀티에이전트 AI 사모펀드야.

먼저 이 순서로 읽어줘:
1. CLAUDE.md          ← 불변식 10개. 이 프로젝트의 헌법
2. docs/glossary.md   ← 용어, 패키지 구조
3. docs/milestones.md ← M1~M5 순서와 완료 기준
4. START-HERE.md      ← 실행 순서

그 다음 M1-kickoff.md 를 읽고, 0단계부터 시작해줘.

작업 규칙:
- 한 번에 한 단계만. 단계가 끝나면 멈추고 결과를 보고해
- 각 단계는 계획을 먼저 제시하고, 내 승인 후 구현해
- "테스트 통과했다"고 말하지 말고 실행 명령과 출력을 그대로 보여줘
- 다음 단계로 넘어가기 전에 항상 물어봐
- 단계가 끝나면 커밋 메시지를 제안해줘

지금부터 M1-kickoff.md 0단계를 시작해줘.
```

이후 세션에서는 `/clear` 후 이렇게 시작한다:

```
CLAUDE.md 와 docs/milestones.md 를 읽고,
[해당 kickoff 파일]의 [N단계]를 진행해줘. 계획을 먼저 보여줘.
```

---

## 1. 실행 순서

| 순서 | 파일 | 내용 | 산출물 |
|---|---|---|---|
| **0** | `M1-kickoff.md` 0단계 | LS_KR/LS_USA 부검 | `docs/postmortem-ls.md` |
| **0.5** | `docs/design/ls-api.md` | LS API 제약 실측 | 문서 채우기 |
| **1** | `M1-kickoff.md` 1~6 | store · replay · Collector · 백필 · 데이터 탭 | M1 완료 |
| **2** | `M2-kickoff.md` | Analyst 9명 + IC 검증 | M2 완료 |
| **3** | `M3-kickoff.md` | 회계 · Selector · Executor · 룰 베이스라인 | **실전 가능** |
| **3.5** | `reporting-kickoff.md` | Gmail 리포트 | M3.5 완료 |
| **4** | `M4-kickoff.md` | RL Allocator (13단계) | M4 완료 |
| **5** | `M5-kickoff.md` | Auditor · ModelOps · Claude 리뷰 | M5 완료 |

`dashboard-kickoff.md` 는 독립 파일이다. D-1(셸)은 1단계 직후,
D-2는 M1 직후, D-3은 M3, D-4는 M4 시점에 실행한다.

**M3 시점에 이미 돈을 벌 수 있어야 한다.** RL 없이도 시스템이 돌아가는 게 설계 목표다.

---

## 2. 착수 전 확인

- [ ] `CLAUDE.md`, `docs/` 를 레포에 커밋했다
- [ ] `.gitignore` 에 `data/`, `.env` 가 있다
- [ ] LS API 모의투자 계정을 발급받았다
- [ ] `docs/design/ls-api.md` §1 의 실측 항목 중 **5년치 백필 가능 여부**를 확인했다
      (불가능하면 M1 일정과 데이터 소스가 달라진다)

---

## 3. 문서 지도

### 설계 (진실의 원천)
| 문서 | 내용 |
|---|---|
| `docs/design/accounting.md` | NAV·TWR·배당·세금 — **보상 함수의 r_port 정의** |
| `docs/design/reward-and-risk.md` | 보상 함수, MDD 밴드, 자본 단계 |
| `docs/design/data-contract.md` | 이중시간 저장, 데이터 게이트, 검증 테스트 |
| `docs/design/agents.md` | 에이전트 명세, Signal 스키마 |
| `docs/design/selector.md` | Analyst 가중치 진화 |
| `docs/design/rl-training.md` | RL 학습 절차, 진단, 하이퍼파라미터 |
| `docs/design/dashboard.md` | 3탭 화면 명세 |
| `docs/design/reporting.md` | 리포트 4섹션, Gmail |
| `docs/design/config.md` | **모든 임계치의 단일 소스** |
| `docs/design/ls-api.md` | LS API 제약 확인 목록 |

### 운영
| 문서 | 내용 |
|---|---|
| `docs/runbook.md` | 배포, 장애 등급, 킬스위치, 복구 |
| `docs/milestones.md` | M1~M5, 완료 기준, **중단 기준** |

---

## 4. 세션 운영 규칙

- **작업이 바뀌면 `/clear`.** 컨텍스트가 이전 시도로 오염되면 판단이 흐려진다
- **같은 문제로 두 번 교정했으면 `/clear`** 하고 더 구체적으로 다시 시작
- 레포 전체를 읽는 작업(부검, 완료 검토)은 **subagent** 로
- 중요 단계(store, 회계, Allocator)는 **새 컨텍스트의 subagent 로 리뷰**
- 긴 세션 중간에 주기적으로:
  > CLAUDE.md 불변식 10개를 다시 확인하고 위반이 있는지 점검해줘

---

## 5. 잊지 말 것

이 프로젝트가 선행 프로젝트와 다른 지점 세 가지다.

1. **액션 반영률을 상시 측정한다.** 30% 미만이면 그건 RL이 아니라 룰 시스템이다
2. **오라클 카나리를 통과하기 전엔 실제 학습을 시작하지 않는다.**
   "신호가 약한 것"과 "코드가 끊어진 것"을 구분하기 위해서다
3. **중단 기준이 있다.** 재정식화 3회 실패 시 룰 베이스라인으로 되돌린다.
   보상 함수를 바꿔가며 될 때까지 시도하지 않는다
