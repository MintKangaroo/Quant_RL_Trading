# Postmortem follow-up — 2026-09-09

[최초 감사](quant-platform-audit-2026-09-09.md)는 수정 전 증거다. 이 문서는 이후 구현과 검증을 구분한다. 목표 아키텍처는 잠정 Hybrid(E): 신호 모델 → 목표 비중 → 포트폴리오 → 결정론적 위험검사 → 집행 → 브로커다. RL은 계속 비활성이며 우월성이 검증된 모델로 취급하지 않는다.

## 구현한 회귀 방지

| Failure | Root cause | Code location | 적용한 제약·수정 | Regression test | 상태 |
|---|---|---|---|---|---|
| Kill 이후 후속 매수 전송 | 세션 시각에 검사하고 지연 전송 시 재검사하지 않음 | `executor/guards.py`, `executor/pipeline.py` | 실제 전송 시계로 kill·시세 신선도·circuit 재검사, 캐시 우회 | `tests/executor/test_safety_regressions.py` | 구현 |
| 동시 호출·재시도 중 중복 주문 | 메모리 확인/전송 경쟁, 영속 claim 경쟁 | `broker/submission.py`, `store/locking.py` | 어댑터 직렬화, 미확정 재전송 차단, POSIX Store claim 잠금 | `tests/broker/test_submission_safety.py`, `tests/store/test_concurrent_append.py` | 구현, 다중 호스트 보장 제외 |
| 취소·정정 미확정을 완료로 취급 | ACK와 최종 상태 혼동 | `executor/lifecycle.py`, `executor/supervise.py`, `tools/reconcile_fills.py` | UNKNOWN을 열린 상태로 유지, 조회 기간 경과로 제외하지 않음 | `tests/executor/test_safety_regressions.py` | 일부 구현; 조치별 영속 claim·최종 취소 대사 남음 |
| 과거 미국 종목 선정에 미래 거래 단절 반영 | 전체 패널 끝에서 발견한 단절을 과거 상폐로 소급 | `collectors/us_universe_panel.py`, `store/quality.py` | 추론 시점부터 비거래 상태로만 기록. 상폐일 생성 금지. 알려진 오염 source의 새 학습·백테스트 차단 | `tests/collectors/test_us_universe_panel.py` | 코드 수정; 기존 데이터 재구축 별도 |
| 뒤늦은 업종·유동주식 정보 노출 | 표시명과 같은 참조 데이터 예외 | `store/tables.py` | 이름 표시 외 피처 데이터는 observed_at 게이트 통과 필수 | `tests/invariants/test_reference_data.py` | 구현; 없는 과거 정보는 결측 |
| 학습·운용 입력 불일치 | 미래 라벨을 가진 종목만 남긴 뒤 횡단면 변환 | `analysts/ranker.py`, `tools/train_ranker.py` | 시점별 공통 입력 생성 후 라벨 결합. 미래 정정은 과거 X 불변 | `tests/analysts/test_ranker_feature_contract.py` | 구현; 기존 모델 재학습·재평가 미실행 |
| 첫 평가일 손실 누락 | 평가 구간 첫 종가를 기초값으로 사용, 첫 수익 무조건 제거 | `backtest/loop.py`, `backtest/stats.py` | 회계와 같은 직전 스냅샷을 기초값으로 사용 | `tests/backtest/test_evaluation_boundary.py` | 실제 Store→회계→loop 검증 포함 |
| PPO 보상에서 실비 두 번 차감 | 비용 후 NAV 수익에서 cost를 다시 차감 | `allocator/reward.py`, `allocator/env.py` | `net-cost-once-v2`: cost는 별도 기록. 기존 계약 checkpoint 재개 차단 | `tests/rl/test_reward.py`, `tests/rl/test_reward_net_contract.py` | 구현; 별도 DRL 시뮬레이터 성과 설명 아님 |
| 체결 0건에서 액션 반영 100% | 사이징 계획을 실제 비중으로 기록 | `executor/pipeline.py`, `accounting/weights.py` | 장부 보유만 평가. 부분체결 대사 후 revision. 같은 상태 재대사 멱등 | `tests/accounting/test_realized_weights.py`, `tests/executor/test_safety_regressions.py` | 구현; 계좌 대사 완료 여부를 대신하지 않음 |
| 불가능한 위험 제약을 정상 비중으로 반환 | 근사 투영 후 모든 제약을 재검사하지 않음 | `portfolio/constraints.py`, `session/daily.py` | 입력·최종 RC/베타 검사. 실패 시 리밸런싱 중단, 기존 보유를 청산 목표로 만들지 않음 | `tests/portfolio/test_projection_safety.py`, `tests/session/test_daily.py` | 구현; 기존 score fallback은 RC 인증 아님 |

실제 비중 갱신은 백테스트 체결 및 브로커 체결 대사 양쪽에서 회계 모듈을 사용한다. 평가에 필요한 가격·환율이 없으면 성공한 수치로 위장하지 않는다. 기록된 체결만 알려진 포지션이며, UNKNOWN 주문의 브로커 잔고 확정을 뜻하지 않는다.

## 데이터·모델 호환성

- 새 미국 비활성 추론 source는 `ls_us_inactive_inferred_v2`. 거래 단절은 법적 상장폐지의 증거가 아니다. 기존 append-only 자료를 삭제하거나 시점을 다시 찍지 않았다.
- `ls_us_derived` 상폐 추정 행이 보이는 창고는 새 ranker 학습·backtest에서 거절한다. 당시 관측할 수 있던 근거로 별도 데이터셋을 재구축해야 한다. 이 가드는 모든 형태의 생존편향을 탐지하는 인증기가 아니다.
- 새 ranker sidecar에는 `pit-crosssection-before-label-v2`를 기록한다. 기존 모델은 새 계약 통과 모델이 아니며, 바뀐 피처와 기존 모델의 수익성은 새 검증 대상이다.
- 새 PPO checkpoint에는 보상 계약을 기록한다. 구 계약의 optimizer·normalizer를 그대로 resume하지 않는다. RL 재훈련·정책 승격은 실행하지 않았다.

## 검증 증거

- 주문 안전성 첫 패치: 선택 테스트 409개, 추가 경계 21개, 새 격리 환경 164개 통과.
- 데이터 PIT·ranker·portfolio·Store·backtest 선택 테스트: 279개 통과(577.22초). 금융 경계 수정 전 시작한 실행이므로 최신 회계 변경의 증거와 구분한다.
- 최신 회계·집행·브로커·PIT·불변식 선택 테스트: 386개 통과(42.23초).
- 새 격리 환경의 금융 CI 대상: 100개 통과(25.04초). 평가 loop 통합 사례 추가 전 실행이다.
- 평가 경계 테스트 3개 통과(6.28초): 첫 평가일 -10% 보유 손실이 loop의 Total Return과 MDD에 모두 포함된다.
- 기존 backtest loop 10개 통과(160.32초), 최종 커밋 전 불변식 112개 통과(10.42초).
- 포트폴리오 실패 사례 8개는 수정 전 모두 실패. 수정 후 제약·배분·세션 선택 테스트 31개 통과(51.85초).
- RL 선택 의존성을 분리한 새 격리 환경: 룰 세션·회계·평가 경계·포트폴리오 100개 통과(81.41초).
- 수정한 일반 CI의 주문·PIT 대상: 격리 환경 176개 통과, 실데이터 검사 3개 명시적 제외(35.84초). 실데이터 검사는 로컬에서 별도로 3개 통과(1.83초).
- 최종 격리 환경 불변식 112개 통과(22.01초). RL 패키지를 설치하지 않은 환경이다.
- 기존 RL 운용 연결을 포함한 불변식·포트폴리오·allocator·세션 검사 266개 통과(440.30초). 로컬 torch CPU 환경에서 수행했다.

최초 두 GitHub `execution-safety` 실행은 로컬 실제 시세 파일을 찾는 호가 검사 3개 때문에 실패했다. 로컬 격리 Python 환경은 같은 머신의 `data/`를 볼 수 있었으므로 이 의존성을 드러내지 못했다. 이 3개를 `warehouse` 대상으로 명시하고 일반 CI에서는 제외했다. 데이터가 있는 로컬 실데이터 검사는 유지하며, 호가 경계·방향성·반올림 검사는 계속 CI에서 실행한다. 원격 CI 결과는 마지막 push 이후 별도로 확인한다.

실행별 테스트는 중복되므로 합산하지 않는다. 전체 저장소 lint/type/test 통과 또는 투자성과 증거가 아니다. 과거 전역 Ruff·mypy 오류는 별도 부채다. 추가 검증 결과는 해당 커밋 README에 기록한다.

## 남은 개선 순서

1. 장중 조치의 영속 상태 전이·최종 취소/과거 주문일 대사, 위험 데이터가 없을 때의 score fallback 정책을 보강한다. 계좌 예약 현금·노출 한도는 아래 후속 패치로 구현했다. 제약 투영이 실패한 값을 정상 배분으로 반환하는 경로는 차단했다.
2. 새 계약 데이터에서 공통 baseline·비용/지연 stress·WF/OOS를 실행할 재현 가능한 연구·평가 계약을 만든다. 이미 사용한 홀드아웃을 미사용 OOS로 부르지 않는다.
3. Champion 대비 net return·Sharpe·MDD·turnover·안정성·cost sensitivity를 모두 확인하는 승격 게이트를 연결한다. 기존 `promotion_gate.py`의 reward 중심 판정만으로 Production 자격을 부여하지 않는다.
4. 실제 연구 산출물로 Research/Models UI를 확장하고, clean environment와 측정된 병목을 개선한다.

비용·spread·slippage의 완전한 분해, realistic intrabar fill, 다중 호스트 계좌 예산, 모든 리스크 항목, 전체 baseline 성과 비교, feature ablation, 새 OOS·paper·live shadow·limited capital 검증은 아직 완료되지 않았다. 이 패치는 과거 결함의 재발 조건을 줄인 것이며 수익성 개선 주장이 아니다.


## 계좌 예산 후속 패치

| Failure | Root cause | Code location | Constraint / fix | Regression test | Priority |
|---|---|---|---|---|---|
| 다른 주문이 같은 현금을 사용 | 주문 ID별 중복 검사만 존재, 미체결 금액 미예약 | `risk/account.py`, `risk/budget.py`, `executor/pipeline.py` | 통화별 현금에서 수수료·재호가 상한 포함 잔량 예약, 계좌 잠금 | `tests/risk/test_account_budget.py` 동시 스레드·프로세스·재시작 사례 | Critical |
| 보유량 초과 매도, 예약된 매도대금 재사용 | 목표/현재 주문만 검사 | `risk/budget.py` | 실제 수량에서 기존 매도 잔량 차감; 예상 매도는 매수 여력으로 계산 금지 | `tests/risk/test_budget.py` | Critical |
| 오래된 미확정 주문을 완료로 표시 | 경과 일수를 취소 증거로 사용 | `tools/reconcile_fills.py`, `broker/fills.py` | 자동 expired 제거, 주문번호 미확정·미확인 잔량 실패 보고, 과거 주문은 날짜별 대사 필요 | `tests/risk/test_account_budget.py`, `tests/broker/test_fills.py` | Critical |
| 과거 부분체결을 다시 계산할 위험 | 주문 조회 창으로 장부 체결 이력까지 제한 | `broker/fills.py` | 누적 체결 차감에 전체 PIT 체결 이력 사용; 대사·전송·재호가 같은 계좌 잠금 | `test_old_partial_fill_is_not_forgotten_by_polling_window` | High |
| 미승인 주문을 백테스트에서 체결 | planned를 paper와 같은 승인 상태로 취급 | `backtest/execution.py`, `executor/pipeline.py` | planned → reserved → 전송; PaperBroker용 승인 조각만 D+1 시도, simulated로 잔량 해제 | `test_simulator_consumes_authorized_slices_and_releases_after_attempt` | High |
| UTC 자정에 미국 매도대금을 조기 해제 | 결제일 경계와 체결일 시간대 불일치 | `accounting/ledger.py` | 시장 현지 날짜·거래일 캘린더, 기존 settlement_days 적용 | `tests/accounting/test_settlement_timezone.py` | High |
| 장중 snapshot 이후 평가 시작 지수 불일치 | loop와 일일 TWR의 전일 선택 기준 차이 | `backtest/loop.py` | `previous_session_snapshot` 공유 | `tests/backtest/test_evaluation_boundary.py` 장중 snapshot 유무 사례 | High |

예약은 주문 저널에서 재구축한다. 별도 메모리 잔액이나 새로운 NAV 계산을 만들지 않았다.
`reserved/paper/submitting/sent/cancel_unknown/modify_unknown` 잔량과 브로커 번호가 있는
미검증 종결 행은 체결 장부가 입증한 수량만 차감한다. 미전송 조각 철회는 전송 claim이
없을 때만 허용한다. partial fill과 예약 해제를 같은 계좌 잠금 안에서 처리하며,
중간 장애는 예약을 과하게 유지할 수 있어도 확인 없이 예산을 풀지는 않는다.

새 설정은 config 판번호 2이며 신규 연구 창고의 기본 가정이다. 기존 운영 창고의 값과
계좌는 수정하지 않았다. 기존 창고에 도입할 때는 `Store.seed_config_defaults`에 명시적
`effective_at`을 제공해 변경값을 먼저 검토해야 한다. 과거 설정·모델 결과를 새 위험계층을
통과한 결과로 재표시하지 않는다. RL 캐시 설정 지문에도 risk 키를 포함한다.

같은 Store 루트의 FUND만 직렬화한다. 다른 루트·호스트·외부 수동 주문, 입출금과
브로커 최종 취소 확정까지 원자적으로 보장하는 계좌 원장은 아직 없다. 기존
`reconcile_snapshot`의 추정 단가 정정도 실제 체결 증거를 대체하지 않으며, 이번에는
경합을 막는 잠금만 연결했다. 모든 라이브 주문의 완전한 대사를 주장하지 않는다.

예약 승인 과정에서 종목별 중복 Store 조회를 일괄 조회로 줄였다. 별도 성능 벤치마크나
전략 수익성 개선을 측정했다는 의미는 아니다. 전체 baseline·비용/지연 stress·WF/OOS는
다음 연구 단계로 남아 있다.

모의체결 권한은 예약 승인과 별개다. `simulation_only` 표시가 있는 PaperBroker용
예약만 봉으로 체결하며, 해당 예약의 실브로커 전송도 차단한다. 기존 미승인 planned
행을 승인된 과거 주문으로 소급 변경하지 않았다. 비교 연구는 새 버전·새 저널에서
재실행해야 한다. 실제 broker용 reserved를 모의체결하지 않는 회귀 테스트를 포함했다.


### 계좌 예산 검증 기록

수정 전 새 실패 사례 6개(다른 주문/동시 주문의 중복 현금 사용, 수수료 예산,
상한 없는 시장가 매수, 계좌 평가 누락, 보유량 초과 매도)가 모두 실패했다.

- 계좌·브로커·집행·회계·평가 경계·설정·시스템 API·불변식: **417 passed**, 로컬 시세가 필요한 3개 제외, 129.29초. 모의체결 권한 분리 전 실행이다.
- 최종 모의/실제 브로커 예약 분리 후 계좌·집행·브로커·불변식·룰 세션: **325 passed**, 실데이터 3개 제외, 112.98초.
- 설정 지문·config·커밋 전 불변식: **128 passed**, 24.28초.
- 대사·추격·대시보드 CLI의 `--help` 진입점을 확인했다. 실제 주문 전송이나 운영 계좌 설정 변경은 실행하지 않았다.
- 신규 risk 코드·pipeline·계좌 잠금·신규 재무 테스트의 Ruff 검사와 `git diff --check`가 통과했다. 기존 전체 저장소 lint 부채의 해소를 주장하지 않는다.

위 실행은 서로 중복되므로 테스트 수를 합산하지 않는다. 실현 수익·Sharpe·alpha 개선
검증이 아니라 장부·집행 계약 검증이다. 최종 재생 검사는 아래에 별도로 기록한다.

- 모의체결 권한 분리 후 `tests/backtest/test_loop.py`·`tests/session/test_shadow_execution.py`: **12 passed**, 245.75초. 동일 세션 재실행·중복 체결 방지와 하루씩 나눈 shadow 연결을 검증했다.

## 취소·정정 확인과 부분체결 대사 후속 패치

| Failure | Root Cause | Code Location | Proposed Fix / 적용한 제약 | Regression Test | Priority |
|---|---|---|---|---|---|
| 접수 응답을 취소 완료·가격 변경으로 오인 | API ACK와 거래소 확인을 같은 상태로 취급 | `executor/supervise.py`, `broker/order_confirmations.py` | ACK는 UNKNOWN, 인증된 SC2/SC3와 전송·조치 기록을 대조한 뒤 확정 | `test_receipt_keeps_reservation_and_confirmed_cancellation_releases_it` | Critical |
| API 호출 뒤 프로세스 중단 시 같은 조치 재전송 | 응답 이후에만 상태 적재 | `executor/action_journal.py`, `tools/chase_orders.py` | 계좌 잠금 아래 intent → API → receipt; intent 존재 시 재전송 금지 | `test_crash_never_resends_durable_intent`, `test_separate_processes_share_one_action_claim` | Critical |
| 재시작마다 retry가 0, 정정 번호·가격 유실 | 원 orders 행만으로 상태 재구성 | `executor/action_journal.py` | 확인된 정정 번호, 원 기준가, 재시도 횟수·시각 복원 | `test_reprice_confirmation_restores_child_number_timer_and_retry_budget` | High |
| 부분 취소로 예약 전액 해제 | 잔량 0/취소 접수만으로 종결 | `risk/account.py`, `broker/order_confirmations.py` | SC3 취소 확인 수량만 차감; 체결+취소가 원수량을 채울 때만 종결 | `test_partial_cancel_preserves_unrecorded_fill_reservation` | Critical |
| 마감 취소에서 이미 체결된 수량까지 취소 요청 | 최초 수량으로 복원한 뒤 close 경로가 누적 체결을 생략 | `executor/action_journal.py`, `tools/chase_orders.py` | 첫 조치 전에도 실제 체결 장부로 잔량 복원 | `test_chase_cli_restarts_without_repeating_cancel_and_uses_actual_remainder` | High |
| 다른 계좌·주문일의 같은 번호를 대사할 위험 | 주문번호에 계좌·거래일 증거가 연결되지 않음 | `executor/pipeline.py`, `broker/fills.py`, `tools/reconcile_fills.py` | 새 전송의 지문·현지 주문일 기록, 수정된 orders 관측일로 원 주문일 대체 금지 | `test_original_order_date_survives_later_status_revision`, `test_fill_reconciliation_cannot_use_another_account_or_overfill_cancelled_remainder` | Critical |
| 누적 평균가가 바뀌면 현금·손익 왜곡 | 신규 체결 수량에 누적 평균가를 그대로 곱함 | `broker/fills.py` | 누적 거래대금에서 기록된 거래대금 차감, human/hash 주문 ID를 같은 장부로 연결 | `test_partial_fill_cash_uses_change_in_cumulative_notional` | High |
| 수량 초과·역행·비정수 응답을 정상 체결로 처리 | 수량 보존 검사 없이 장부 적재/잔량 클램프 | `broker/fills.py`, `executor/supervise.py` | 원수량 = 실제 체결 + 확인 취소 + 예약 잔량; 모순이면 UNKNOWN/조치 차단 | `test_invalid_broker_quantity_never_enters_book` | Critical |
| 확인 파서가 있어도 운영 경로에서 사용되지 않을 위험 | 수신 도구·상태 복구 호출 누락 | `collectors/order_events.py`, `tools/watch_order_events.py` | 인증 구독 → 확인 대조 → append-only 증거 → 상태 투영, chase/대사에서 복구 | `tests/collectors/test_order_events.py`, chase CLI 회귀 | High |

새 계좌 수신기는 **KR에 한정**한다. 공식 LS 계좌 실시간 필드와 예제를 확인해
SC2 정정 확인 수량·가격, SC3 `canccnfqty`를 사용했다. 실제 계좌에 접속해 검증한
결과는 아니다. 공식 링크와 수신 계약은 [execution safety](design/execution-safety.md#취소정정-조치-저널과-확인-증거)에 기록했다.

`execution_events`는 신규 append-only 테이블이며 paper/backtest overlay의 독립
저널에 포함된다. 토큰·계좌번호·원본 계좌 잔고는 저장하지 않는다. 이벤트의 원 식별자와
관측시각을 보존하며, 접수 ACK나 주문 조회 누락을 취소 증거로 소급 변환하지 않는다.
증거 적재와 orders 상태 투영 사이의 중단은 다음 수신·chase·체결 대사에서 복원한다.

수신 도구를 주문 추격 **전에** 실행해야 한다. 운영 배포·자동 기동은 이번 작업에서
실행하지 않았다. 수신 설정 확인용 `--help`는 계좌에 접속하지 않는다.

```bash
.venv/bin/python tools/watch_order_events.py --help
# 계좌 모드와 지문이 Store에 고정된 환경에서 운용할 때의 수신 명령:
.venv/bin/python tools/watch_order_events.py --sandbox data/_paper --seconds 21600
```

연결이 끊기거나 확인 이벤트를 놓치면 계속 예약한다. 완전하지 않은 정정 확인,
미기록 체결이 남은 부분 취소, 지문·주문일 증거 없는 기존 주문은 자동 재시도하지 않는다.
SC3의 `unercqty=0`이 원주문 전체 취소를 뜻한다고 가정하지 않는다. 과거 날짜를 지정한
REST 대사, 놓친 이벤트 복구, US 취소 확인과 외부 수동 주문의 전체 계좌 대사는 남아 있다.

금융 오류 재현: 30주를 100에 먼저 기록하고 총 100주의 누적 평균가가 107이면,
추가 70주의 단가는 110, 총 대금은 10,700이어야 한다. 수정 전 코드는 70주를 107에
기록해 총 대금을 10,490으로 만들었다. 새 테스트가 수정 전에 실패하는 것을 확인했다.
누적 가격의 반올림·정밀도와 설정 수수료/세금 모델은 그대로 사용한다. 브로커 개별
체결 ID와 실제 비용 명세의 완전한 대사를 구현했다고 주장하지 않는다.

### 검증 기록

- 최초 ACK 경계 테스트 2개와 새 금융 계산·수량 검사 4개가 수정 전 실패했다.
- 주문·리스크·브로커·수신·동시 적재 검사: **234 passed**, 실데이터 의존 3개 제외, 94.59초. 이후 CLI·별도 프로세스·추가 계좌 대사 사례를 보강했다.
- 회계·평가 경계·불변식: **190 passed**, 52.08초.
- overlay·일일 shadow 연결: **8 passed**, 24.67초.
- 별도 프로세스 경쟁과 CLI 재시작을 포함한 조치 저널 검사: **25 passed**, 15.63초. 추가 계좌/취소 잔량 대사 검사 1개도 통과했다.
- 신규 저널·확인·수신 모듈과 새 테스트의 Ruff, `uv lock --check --offline`, 수신 CLI `--help`를 확인했다.

각 실행은 중복되며 합산하지 않는다.

최종 통합 검사: **429 passed, 3 deselected**, 106.75초. 제외 3개는 기존 실데이터 호가 검사다.

```bash
.venv/bin/pytest tests/risk/ tests/executor/ tests/broker/ \
  tests/collectors/test_order_events.py tests/store/test_concurrent_append.py \
  tests/invariants/ tests/accounting/ tests/backtest/test_stats.py \
  tests/backtest/test_evaluation_boundary.py -m 'not warehouse' -q -o addopts=''
```

기존 `trades`의 잘못된 과거 단가를 소급 수정하지 않았다. 기존 전체 Ruff/mypy 부채도
이번 패치에서 해소한 것은 아니다. `execution-safety` CI에 계좌 알림 수신 검사를 추가했다.

### 다음 개선 순서

1. 검증 이력이 고정된 데이터·전략 명세와 공통 baseline 평가 경로를 만든다. 비용 1/1.5/2/3배와 진입 1/2 bar 지연은 같은 portfolio/risk/execution 경로에서 비교한다.
2. Walk-forward와 사용 이력을 기록한 OOS를 평가한다. 기존에 열어본 holdout은 독립 OOS로 부르지 않는다.
3. reward 중심 `promotion_gate.py`와 별도 승격 도구를 artifact hash·net KPI·cost sensitivity·seed 안정성·단계별 증거로 연결한다. 현재 도구의 통과를 Production 자격으로 해석하지 않는다.
4. 검증 산출물에 연결된 Research/Models UI를 개선한다.

이 실행 안전성 변경만으로 전체 baseline 비교·새 OOS·paper/live shadow·limited capital
검증이 완료된 것은 아니다. RL 비활성 상태와 보수적인 아키텍처 판단을 유지한다.
