# START HERE

Quant_RL_Trading의 현재 인수인계와 초기 개발 순서. 기존 시스템을 이어서 작업할 때는
아래 인수인계를 먼저 확인한다. 초기 kickoff를 처음부터 다시 실행하지 않는다.

## 현재 인수인계 — 2026-09-09

### 작업 위치와 반영 상태

- 대상 저장소는 `MintKangaroo/Quant_RL_Trading`이다. 세션 환경에 보였던
  `Short_Big_Money`는 다른 저장소다.
- 이번 변경의 격리 worktree: `/home/mintkangaroo/Project/Quant_RL_Trading_mission_control`.
  브랜치: `fix/mission-control-safety-audit`.
- 구현 커밋 `9275273`, 통합 검증 기록 `8db7ac6`, 구형 승격 차단·셸 테스트 격리
  `69f6713`, 인수인계 `36c255d`까지 GitHub 푸시를 확인했다.
  후속 디자인 변경은 아래 UI 상태와 전용 감사 문서에 기록했다. 이 세션에서 main 병합이나 변경 코드의
  계획된 배포를 수행하지 않았다. 아래 테스트 사고에 따른 운영 영향은 별도로 발생했다.
- 원래 작업 폴더 `Quant_RL_Trading`에는 병행 변경이 있다. 인수인계 작성 시
  `docs/quant-platform-audit-20260909`, HEAD `1470fa0` 및 주문 이벤트/action journal 등의
  추가 미커밋 변경을 확인했다.
  그 변경은 이번 브랜치에 통합·검증하지 않았다. 다음 작업 전에 두 브랜치의 최신 상태와
  겹치는 수정부터 비교하고, 다른 작업의 미커밋 파일을 덮어쓰거나 되돌리지 않는다.

### 읽을 문서와 유지할 판단 기준

헌법은 [CLAUDE.md](CLAUDE.md)다. 최초 감사에서는 사용자 지정 17개 문서를 순서대로
읽고 실제 코드를 대조했다. 이어받는 작업자는 [PRODUCT.md](PRODUCT.md),
[RL 실패 기록과 재개 조건](docs/rl-postmortem.md),
[Repository Audit](docs/audits/2026-09-09-mission-control.md),
[RL 승격의 현재 적용 규칙](docs/design/rl-training.md#13-승격),
[운영 런북](docs/runbook.md)을 함께 확인한다.

Champion은 **Supervised Ranking + deterministic portfolio/trading rules**다.
rank-gauss 기반 GBM ranker → Selector → smoothing/buffer → risk-parity/rules →
Risk/Executor를 유지한다. 이번 변경은 새 alpha나 수익률 개선을 증명한 작업이 아니다.
Challenger의 승격 판단은 OOS 위험조정 순성과·견고성·운영 안전성으로 한다.

### 완료한 범위

| 영역 | 이 브랜치에서 구현·검증한 내용 |
|---|---|
| 주문 안전 | 제출 직전·조각 사이 킬스위치 재확인, 같은 날 해제 후 재발동, 청산 매도 허용, 지정가·비용을 포함한 매수 현금 예약 |
| 체결·회계 | 누적 체결 수량/대금 차분, 배당락 이전 보유 기준 권리 계산, 실제 체결에 따른 실현 비중 정정 |
| 데이터·표시 | 공표 기대 세션 기준 stale 확인, 비유한/미래 발효 가격 제외, KR 지수 ID 수정, 과거 계좌의 현재 브로커 조회와 시장 간 freshness 대체 차단 |
| 구형 RL 승격 | 좋은 reward/균등가중 기록만으로 활성화하던 경로 차단. CLI와 쓰기 함수 양쪽에서 거부. `--off`와 설정 변경 없는 `--dry-run` 유지 |
| 구형 실험 체인 | `chain_20260830.sh`, `chain_r7_full.sh`, `chain_drl_r4.sh`는 실행 전 종료. supervisor의 3회차 재시작 제거 |
| 테스트 격리 | 수집·복구 셸 테스트의 실제 cd 명령을 임시 경로로 치환. 경로가 없거나 여러 개면 프로세스 실행 전에 실패 |

구형 RL 활성화 경로 차단은 **공통 승격 gate의 완성이 아니다**. 원시 config 쓰기 권한,
다른 연구 도구의 직접 실행, 이미 활성화된 정책을 통제하는 장치는 아직 아니다.

### 디자인 / UI 상태 — 두 화면 개편 완료, 운영 배포 전

승인된 트레이딩·학습 화면 개편을 구현했다. [디자인 감사와 검증](docs/audits/2026-09-09-dashboard.md)을
먼저 읽는다. 새 화면의 대사·총비용·Challenger 검증 자료 부재를 완료 상태로 오인하지 않는다.

- 운용 관제: 독립 상태 4개, 리스크 예산 요약과 정지 버튼을 상단 배치. 리스크 패널 상시 표시.
- 손익: Gross / 명시 비용 / Net 분리. fee/tax는 통화별 전수 관측일 때만 표시하며,
  체결 목록이 잘리면 미측정. Net에서 비용을 다시 빼지 않는다.
- 모델 검증: Champion 기준과 관측 IC·가중치·측정 시점을 먼저 표시. Challenger의 없는
  검증 자료는 미측정. 과거 RL 진단·AI 해설·후보 차트는 필요할 때 펼친다.
- learning API의 KR/US 덮어쓰기와 IC 이력 혼합을 수정. 과거 조회와 장부 범위를 링크에
  유지하고, 과거/종합 화면에서는 킬스위치 조작을 비활성화한다.
- 1440×1000 / 390×844 Chromium 검수: 가로 넘침·JavaScript 오류·쓰기 요청 0.
  실제 paper 관측도 쓰기 금지 Store로 조회했다. 실제 계좌 캡처는 GitHub에 올리지 않는다.

[dashboard 설계](docs/design/dashboard.md)와 [app.css의 :root](quant_rl_trading/dashboard/static/app.css)를
유지했다. 배경·손익·상태 색, Pretendard / IBM Plex Mono, 1px divider, 숫자 정렬을 보존했다.
이미지나 가짜 금융 지표를 새 UI에 넣지 않았다.

**진행 방식:** 이번 승인 범위는 디자인 개편까지다. 사용자가 “다음 작업을 추천하고,
승인하면 진행”하도록 요청했다. 다음 추천은 병행 안전 코드와의 계약 대조·통합 검증 및
대사 상태 연결이다. 승인 전 해당 작업이나 운영 배포를 시작하지 않는다.

### 기다릴 연구와 지금 가능한 작업

| 구분 | 다음 작업 / 판정 조건 |
|---|---|
| G1~G6 연구 판정 대기 | [사전등록](docs/protocols/ranker-sources-round6-2026-09.md)에 따라 **2026-10-01 이후**. 그 전에 평가창 수치·marginal IC를 열지 않는다. 사전 점검은 등록된 coverage 범위로 제한 |
| TWAP 관측 대기 | [집행 연구 등록](docs/protocols/execution-rl-2026-09.md)의 **유효 20거래일** 필요. 9/1·9/2는 제외하며 달력상 날짜 도래만으로 PASS하지 않는다. 룰 1차 관문 전 Execution RL에 착수하지 않는다 |
| 지금 가능한 correctness / safety | 병행 구현과 대조한 뒤 남은 주문 전 reconciliation 계약, 미결 주문 예약금, 기업행위 수량, writer 장애·동시성 경계 검증 |
| 지금 가능한 data / observability | 독립 missing/ghost gate, 저장된 broker snapshot, 총비용 분해와 대사 증거 연결(화면 배치는 완료) |
| 공통 연구·승격 gate | 사전등록·예산·구간·Champion 순성과·seed 분산·artifact 동일성·사람 승인 연결이 남음. 새 평가 없이 계약·회귀를 먼저 준비 가능 |

연구 판정을 기다리는 동안에도 수집 실패·데이터 오염·장부 불일치 점검은 계속한다.
이 브랜치만으로 실자본 운용 준비 완료라고 판단하지 않는다. 배포 전에는 새
`execution.circuit_breaker_drop` 설정을 명시적 발효 시각으로 등록하고 과거 replay의
설정 유효 구간을 검토해야 한다. 운영 창고를 소급 수정하거나 숨은 fallback을 넣지 않는다.

### 검증 근거와 주의할 운영 기록

- 디자인 배치: dashboard 전체 + performance + invariants **370개 통과**.
  후속 실패 표시 수정 뒤 관련 렌더/API/회계/불변식 **174 passed, 1 warning in 19.39s**.
  마지막 시장 표시 수정 뒤 관련 API/렌더/불변식 **152 passed, 7 warnings in 22.26s**.
  재현 가능한 Chromium 검수 도구 `tools/review_control_ui.py`를 추가했다.
  변경 Python 파일의 Ruff 추가 진단은 0건이며 기존 32건은 남아 있다.

- 첫 배치의 폭넓은 경계 검증: **459 passed, 3 skipped**. 통합 검증: **183 passed**.
  범위가 다른 실행이며 통과 건수를 합산하지 않는다. 명령·warning·skip 사유는 감사 문서에 있다.
- `69f6713`의 최종 승격/셸/불변식 검증은 아래 명령으로 **143 passed, 1 warning in 26.72s**.
  변경 Python 파일 Ruff, 셸 `bash -n`, `git diff --check`도 통과했다.
- 저장소 전체 Ruff/mypy는 기존 실패가 남아 있다. 전체 정적 검사를 PASS로 표시하지 않는다.
  당시 동일 기준선 대비 Ruff 1,632개, mypy 430개였으며 상세 비교는 감사 문서를 따른다.
- 앞선 `36c255d` 인수인계 문서 갱신에서는 `python -m pytest tests/invariants --disable-warnings --tb=short`:
  **111 passed, 1 warning in 16.42s**. 로컬 문서 링크 11개와 `git diff --check`도 확인했다.

```bash
# 격리 worktree에서 실행. 운영 셸을 직접 실행하지 않는다.
PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 QUANT_RL_DUCKDB_THREADS=1 \
  /home/mintkangaroo/Project/Quant_RL_Trading/.venv/bin/python -m pytest \
  tests/tools/test_policy_promotion_safety.py \
  tests/tools/test_reboot_recover_script.py tests/tools/test_collect_daily_script.py \
  tests/invariants --disable-warnings --tb=short
```

**테스트 사고를 반드시 인계한다.** 수정 전 셸 테스트가 격리 폴더를 벗어나 원래 저장소의
복구 스크립트를 실행했다. 대시보드 재기동 호출 3회, 회계 완료 2회, shadow NAV 정정 1건을
로그에서 확인했다. 테스트와 자식 프로세스를 중단했고, 대시보드 5059를 복구해 HTML
HTTP 200을 확인했다. 복구 로그상 추가 수집/주문 세션은 없었다. NAV 정정과 사고 로그는
append-only 원칙에 따라 보존했다. 정정의 경제적 정확성을 새로 인증한 것은 아니다.
이후 두 셸 테스트의 격리를 고치고 위 최종 검증을 통과했다. 운영 영향의 상세는
[감사 문서](docs/audits/2026-09-09-mission-control.md)에 남아 있다.

---

아래 0~5절은 초기 개발 절차와 참고 이력이다. 현재 작업 범위·연구 재개 조건은 위 인수인계와
최신 설계/프로토콜을 따르며, 기존 문서의 초기 RL 일정으로 자동 복귀하지 않는다.

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
| **3** | `M3-kickoff.md` | 회계 · Selector · Executor · 룰 베이스라인 | 룰 경로 구축 — 실자본 준비는 별도 검증 |
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
3. **재개·기각 기준을 따른다.** 2026-09-08 개정으로 실험 횟수 상한은 없다.
   `docs/rl-postmortem.md` §10의 재개 조건과 사전등록·예산·홀드아웃·카나리·반영률
   관문은 유지한다. 보상 함수를 바꿔가며 될 때까지 시도하지 않는다.
