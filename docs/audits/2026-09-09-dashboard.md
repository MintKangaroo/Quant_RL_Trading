# 2026-09-09 운용 관제 / 모델 검증 화면 개편

대상: `fix/mission-control-safety-audit`, 변경 전 `36c255d`.
사용자가 승인한 디자인 개편 범위다. 거래 전략·학습·연구 평가일·운영 설정을 바꾸지 않는다.
기존 [Repository Audit](2026-09-09-mission-control.md)의 후속이며 실자본 준비 완료 판정은 아니다.

## Hypothesis → Evidence → Change → Test → Result

| 가설 | 확인한 근거 | 변경 | 검증 / 결과 |
|---|---|---|---|
| 위험을 숨긴 배치가 이상 발견을 늦춘다 | 리스크가 닫힌 details 안에 있고 데스크톱 약 1,767px, 모바일 약 2,885px 아래에 위치 | 주문·데이터·모델·대사 4개 상태와 낙폭 한도 사용량을 상단 배치, 리스크 패널 상시 표시, 정지 버튼을 제목 옆으로 이동 | 저장 응답 재생: 리스크 위치 약 589px / 1,357px. 모바일 예산 요약 403px, 정지 버튼 138px로 첫 화면 안에 표시 |
| 해제/미관측을 정상으로 읽을 수 있다 | 킬스위치 해제에 OPERATIONAL, null 거부율에 0, 보유 10종목 상한과 70% 경고를 UI가 임의 적용 | 킬스위치와 주문 안전을 분리. 대사 미측정·조건 미확인 표시. 보유 수는 관측값, 밴드는 API 설정 사용 | 렌더 회귀: 24종목을 임의 상한 초과로 표시하지 않음, null 거부율은 UNKNOWN. 조회 실패 시 경보와 조작 비활성화 |
| 비용과 Net을 한 숫자로 읽으면 비용 기여를 오인한다 | 회계 체결에는 fee/tax/통화가 있지만 총비용 분해는 없고 체결 목록에 상한이 있음 | Gross / Trading Cost / Net 별도 표시. 회계에서 명시 비용만 통화별 집계. 목록이 잘리면 비용 미측정 | 회계 회귀: KRW/USD를 합치지 않음, 수수료를 기존 PnL에서 재차감하지 않음. 미관측과 체결 0건 구분 |
| 모델 상태를 다른 시장과 혼동한다 | 기존 roster가 시장별 최신 행을 만든 뒤 analyst 이름 하나로 다시 덮어씀. 실제 KR 조회에서 US ranker만 반환 | learning gate/IC API가 요청 시장을 집계 전에 적용. ALL은 시장별 행·이력 분리 | KR +0.081, US -0.021 테스트 관측을 각각 보존. 늦게 관측된 +0.9는 as_of에서 배제. 실제 KR 읽기에서도 KR ranker 반환 확인 |
| 학습 진단이 운용 증거로 오독된다 | 기존 학습 페이지가 PPO 진단·고정 과거 WF 수치를 앞세우고 passed만으로 “매매에 쓰임” 표시 | 모델 검증 페이지: Champion 기준, 관측 IC·가중치·측정 시점, Challenger 증거 부재. 과거 RL 진단은 펼칠 때 조회, 고정 WF 패널은 제거 | 양쪽 뷰포트에서 렌더 성공. 닫힌 RL/후보 차트/AI 해설은 해당 API를 호출하지 않음. IC 통과라도 가중치 0이면 관찰 |
| 탭 이동이 과거 조회를 현재로 바꾼다 | 기존 정적 링크가 as_of/ledger/market을 유지하지 않음 | 공통 탭과 새 상세 링크에 범위 전달. 장부 전환은 선택 목적지와 기존 시점을 유지. 킬스위치 요청도 장부 범위 전달 | 브라우저에서 상단/모바일/복귀 링크의 as_of 보존과 과거 조회 조작 비활성화 확인 |

위 픽셀 값은 동일한 저장 테스트 응답과 뷰포트에서 잰 **배치 검수**다. 운용자 반응 시간이나
backtest 실행 속도의 측정치가 아니다. 첫 화면에는 위험 요약이 보이고, 모바일 상세 표에는
여전히 스크롤이 필요하다. 기존 토큰·Pretendard·IBM Plex Mono·1px 구분선은 유지했다.

## 실제 실행한 검증

격리 worktree에서 기존 Python 환경을 사용했다.

```bash
PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 QUANT_RL_DUCKDB_THREADS=1 \
  /home/mintkangaroo/Project/Quant_RL_Trading/.venv/bin/python -m pytest \
  tests/dashboard tests/accounting/test_performance.py tests/invariants -q
```

**370개 통과, 7개 deprecation warning**. 경고는 기존 exchange_calendars/NumPy timedelta다.
후속 UI 실패 상태 수정 후 관련 렌더/API/회계/불변식도 다시 실행했다:

```bash
PYTHONPATH=. OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 QUANT_RL_DUCKDB_THREADS=1 \
  /home/mintkangaroo/Project/Quant_RL_Trading/.venv/bin/python -m pytest \
  tests/dashboard/test_trading_render.py tests/dashboard/test_tab_render.py \
  tests/dashboard/test_mobile_layout.py tests/dashboard/test_learning_api.py \
  tests/accounting/test_performance.py tests/invariants --disable-warnings --tb=short
# 174 passed, 1 warning in 19.39s
```

시장 이력 이름과 대소문자 표시의 마지막 수정 뒤에는 위 명령의 대상만
`tests/dashboard/test_agent_health_api.py tests/dashboard/test_learning_api.py
tests/dashboard/test_tab_render.py tests/dashboard/test_trading_render.py tests/invariants`로
바꿔 재실행했다: **152 passed, 7 warnings in 22.26s**. 서로 다른 검증 실행의 건수를 합산하지 않는다.

```bash
uv tool run --from playwright --with flask python tools/review_control_ui.py
```

첫 실행에서 Chromium이 없으면 `uv tool run --from playwright playwright install chromium`.
이 도구는 Flask 템플릿만 렌더하고 모든 브라우저 요청을 가로챈다. 실제 앱 factory·창고·
브로커를 열지 않는다. 기존 `tests/dashboard/payloads`를 사용하며 없는 응답은 503이다.
**1440×1000 / 390×844 × 2페이지 PASS, JavaScript 오류 0, 쓰기 요청 0, 페이지 가로 넘침 0**.
과거 조회의 정지 버튼, 링크 시점 보존, 패널을 펼칠 때 조회, 기존 응답 후 장애 시 표시를 검사한다.
결과와 검수 표식이 있는 이미지는 `/tmp/quant-control-review`에 생성한다.
테스트 응답의 계좌·날짜는 운용 성과 증거로 사용하지 않는다.

추가로 원래 paper 창고의 `append`/설정 초기화를 거부하는 읽기 전용 Store와 명시적
ReplayClock/as_of로 trading, learning/gate, learning/ic-history, learning/research-ledger,
system/freshness를 조회했다. **모두 HTTP 200**. 저장된 관측을 읽었으며 새 IC 계산·OOS
평가·홀드아웃 개봉을 수행하지 않았다. 실제 계좌 응답과 이미지는 `/tmp/quant-ui-review`에만
보관하고 GitHub에는 포함하지 않는다. 이번 디자인 검수에서 운영 서비스 재시작은 없었다.
앞선 셸 테스트 사고 기록은 기존 감사/인수인계에 그대로 보존한다.

`node --check`(trading/learning/scope), `git diff --check` 통과.
변경 Python 파일 7개의 Ruff 진단은 기준선과 같은 **32건**, 추가 진단 0건.
새 `tools/review_control_ui.py`의 Ruff는 통과. 전체 저장소 정적 검사 PASS를 주장하지 않는다.

## 남은 위험 / 다음 작업의 경계

- 대사 증거를 연결하지 않았으므로 상단 대사는 미측정이다. 킬스위치 해제나 NAV 차이 0을
  주문 가능 인증으로 바꾸지 않는다. 주문 가능 여부의 종합 판정도 아직 없다.
- 명시 비용은 **해당 회계 세션의 장부 전체 수수료·세금**이다. US sleeve의 Net과 범위가
  다를 수 있어 장부 전체로 명시한다. ALL 합산 명시 비용은 미측정이다. spread/slippage,
  turnover, 순IR, gross PnL의 공통 분해 계약은 아직 없다.
- Ranker IC는 저장된 관측값이다. Training/Validation/OOS 분류·marginal IC·Challenger 순성과·
  seed 안정성·재학습 시각·feature drift는 증거 연결 전이다. 이를 승격 성적표로 읽지 않는다.
- learning API는 시장 경계를 수정했으나 다른 legacy 서비스 소비자의 시장 혼합 위험까지
  전수 해소했다고 주장하지 않는다. 연구 대장의 고정 설명·과거 진단도 현재 승격 근거가 아니다.
- 새 화면은 두 페이지에 한정한다. 전 기기·전 탭 인증이나 배포 완료가 아니다.
- 원래 worktree의 안전성 구현과 미커밋 변경은 통합하지 않았다. **다음 추천은 두 브랜치의
  주문/대사/예약금 계약 대조·통합 검증 후, 근거가 있는 상태를 새 관제에 연결하는 것**이다.
  사용자가 다음 작업을 승인하기 전 착수하지 않는다.
- G1~G6는 2026-10-01 이후, TWAP은 사전등록된 유효 20거래일과 기존 관문을 기다린다.
