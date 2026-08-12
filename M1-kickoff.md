# Claude Code 착수 프롬프트

`CLAUDE.md` 와 `docs/` 를 먼저 레포에 커밋한 뒤 시작한다.

## 사용 규칙

- **각 단계를 하나씩** 던진다. 여러 단계를 한 번에 시키지 않는다
- 단계마다 **plan mode(Shift+Tab)로 시작**하고, 계획을 검토한 뒤 구현으로 넘어간다
- 단계가 끝나면 **커밋하고 `/clear`** 로 컨텍스트를 비운다
- 같은 문제로 두 번 이상 교정했다면 `/clear` 하고 더 구체적인 프롬프트로 다시 시작한다

---

## 0단계 — 선행 프로젝트 부검 (코드 작성 없음)

> plan mode로 시작할 것.

```
LS_KR 과 LS_USA 레포를 부검해줘. 파일을 많이 읽어야 하니 subagent를 써서
조사하고 요약만 돌려줘. 코드는 절대 쓰지 마.

이 두 프로젝트는 강화학습 기반으로 만들었지만 학습이 되지 않아 사실상
룰 기반으로 동작한 실패 사례야. 새 프로젝트에서 같은 실패를 반복하지
않으려면 원인을 먼저 알아야 해.

조사할 것:

1. RL 출력이 실제 주문에 반영되는 비율은 얼마인가?
   룰 가드나 안전장치가 RL 액션을 덮어쓰는 지점을 코드에서 찾아줘.
   (가장 유력한 원인이다)

2. 보상 함수는 조밀한가 희소한가? 에피소드 끝에 한 번만 정산하나?

3. 상태값에 실제 체결 결과가 되먹여지나, 목표값만 들어가나?

4. 종목 축을 어떻게 인코딩했나? flatten인가 set encoder인가?

5. 학습 데이터에 look-ahead 누수 가능성이 있는 지점

6. 재사용 가치가 있는 I/O 계층 목록
   (LS API 인증·토큰 갱신, 주문 전송, 응답 파싱, 휴장일 처리,
    리스크 가드, 킬스위치) — 파일 경로와 함께

결과를 docs/postmortem-ls.md 로 저장해줘.
각 항목마다 근거가 된 파일과 라인을 명시해줘. 추측이면 추측이라고 써줘.
```

---

## 1단계 — 레포 초기화

```
CLAUDE.md 와 docs/glossary.md 를 읽어. 거기 불변식이 이 프로젝트의 헌법이야.

Lattice 레포 뼈대를 만들 계획을 세워줘. 아직 구현하지 마, 계획만.

- docs/glossary.md 의 패키지 구조 그대로
- Python 3.12, uv
- 의존성: duckdb, pyarrow, pandas, polars, flask, lightgbm, pydantic, httpx
- pytest, ruff, mypy
- .gitignore — data/ 와 시크릿 제외
- .env.example — LS API 키, Claude API 키 자리

tests/invariants/ 에 CI 가드를 먼저 넣어줘:
- datetime.now() 직접 호출 검출 (Clock 주입만 허용)
- Parquet/DuckDB 직접 접근 검출 (store.get 경유만 허용)

그리고 이 두 검사를 파일 수정 후 자동 실행되는 hook으로도 만들어줘.
CLAUDE.md 지시는 권고지만 hook은 강제되니까.
```

계획 승인 후:

```
계획대로 구현하고, pytest tests/invariants/ 를 실행해서 출력 그대로 보여줘.
```

---

## 2단계 — store 계층

```
docs/design/data-contract.md 를 읽고 lattice/store/ 구현 계획을 세워줘.
아직 구현하지 마.

핵심:
- 이중시간 스키마 (valid_from / observed_at / revision / source / ingest_run_id)
- observed_at 없는 레코드는 저장 거부
- append-only. UPDATE/DELETE 없음
- Parquet 날짜 파티션, DuckDB 읽기 전용 연결
- 유일한 조회 API: store.get(table, as_of, entity=None, lookback=None)
  내부에서 observed_at <= as_of 강제, as_of 이전 최신 revision 선택
- store.config(name) — 임계치를 설정에서 읽게

테스트를 먼저 쓰는 순서로 계획해줘:
1. 미래 훔쳐보기 — 가짜 미래 행을 심고 과거 조회에서 안 나오는지
2. 정정공시 — 정정 전/후 시점 조회가 다른 값을 주는지
3. 생존편향 — 상장폐지 종목이 과거 유니버스 조회에 남아 있는지
4. observed_at 누락 시 저장 거부
```

승인 후:

```
계획대로 구현해줘. 테스트 4개를 먼저 쓰고, 그 다음 통과시켜.
마지막에 pytest 출력을 그대로 보여줘.
```

---

## 3단계 — replay 계층

```
lattice/replay/ 구현 계획을 세워줘. 구현은 아직.

- Clock 프로토콜 + LiveClock / ReplayClock
- 이벤트 로그: {run_id, seq, ts_wall, ts_sim, stage, actor, payload_hash, payload}
  append-only
- 에이전트 출력 캐시: (agent, agent_version, entity_id, as_of, features_hash) → output
- 체결 시뮬레이터
  · 충격비용 = k × 변동성 × √(주문량 / 일평균거래량)
  · 부분체결, 갭, 거래정지, 상하한가
  · 3% 거래대금 상한, 청산 3일 제약
  · 정수 라운딩, 최소 주문금액

결정론 테스트를 포함해줘: 같은 as_of 로 두 번 실행 → 주문이 바이트 단위로 동일.
이 테스트가 M1의 핵심 산출물이야.
```

---

## 4단계 — Collector

```
lattice/collectors/ 구현 계획을 세워줘. 구현은 아직.

docs/postmortem-ls.md 의 "재사용 가치가 있는 I/O 계층" 목록을 참고해서
LS_KR / LS_USA 코드를 이식하되, CLAUDE.md 의 참고 규칙을 지켜.
학습·상태설계·보상 관련 코드는 절대 가져오지 마.

- market_collector: LS증권 REST API — 가격, 호가, 수급, 환율
- document_collector: DART 공시, 뉴스

공통:
- 원본 응답을 data/raw/ 에 그대로 저장 (삭제 금지)
- 정규화 후 curated 에 적재
- 각 단계 타임스탬프 기록 → 지연 실측 (p50/p90 집계)
- 매 거래일 universe 스냅샷 (상장폐지 종목 포함)

이식하는 코드마다 출처 파일 경로를 주석과 커밋 메시지에 남겨줘.
```

---

## 5단계 — 백필

```
과거 5년치 전종목 백필 스크립트를 만들어줘.

- 국장 전종목 + 미장 전종목, 상장폐지 종목 포함
- 재개 가능해야 함 (중단 후 이어받기)
- 진행률과 실패 건 로깅
- 백필된 데이터의 observed_at 은 원 공표 시각으로 설정
  ← 백필 시각이 아니다. 이걸 틀리면 전체가 미래를 보게 된다

먼저 종목 10개로 시험 실행하고 결과를 보여줘.
문제 없으면 전체를 돌리고, 완료 후 검증 리포트를 내줘:
커버리지, 결측률, 종목 수 추이, 상장폐지 종목 수.
```

---

## 6단계 — Data Quality 화면

```
docs/design/dashboard.md 를 읽고 Data Quality 화면을 만들어줘.
Flask + ECharts, CDN 사용, 빌드 도구 없음.

- 모든 API 는 as_of 파라미터를 받는다 (예외 없음)
- 임계치는 store.config 에서 읽는다
- 색·타이포는 dashboard.md 토큰 그대로
- localStorage / sessionStorage 금지

표시: 소스별 커버리지, 결측률 추이, 지연 실측 분포(p50/p90),
universe 종목 수 추이, 최근 수집 실패 건.

docs/design/fund-reference.html 을 열어보고 밀도와 색감을 맞춰줘.
그건 구현물이 아니라 스타일 기준이야.
```

---

## M1 완료 확인

```
docs/milestones.md 의 M1 완료 기준을 하나씩 검증하고,
각 항목마다 실행한 명령과 그 출력을 증거로 보여줘.

그 다음 subagent를 써서 지금까지의 코드를 CLAUDE.md 불변식 10개와
대조 검토해줘. 요구사항 위반만 지적하고 스타일은 빼.

전부 통과하면 M2 착수 계획을 세워줘. 하나라도 실패하면 M2로 넘어가지 마.
```

---

## 긴 세션 중 주기적으로

```
CLAUDE.md 의 불변식 10개를 다시 확인하고, 지금까지 작성한 코드에
위반이 있는지 점검해줘. 특히 3번(observed_at), 5번(백테스트 전용 분기),
7번(실현 비중 되먹임).
```
