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

1. 계좌 전체 예약 현금·노출 한도, 장중 조치의 영속 상태 전이, 위험 데이터가 없을 때의 score fallback 정책을 보강한다. 제약 투영이 실패한 값을 정상 배분으로 반환하는 경로는 차단했다.
2. 새 계약 데이터에서 공통 baseline·비용/지연 stress·WF/OOS를 실행할 재현 가능한 연구·평가 계약을 만든다. 이미 사용한 홀드아웃을 미사용 OOS로 부르지 않는다.
3. Champion 대비 net return·Sharpe·MDD·turnover·안정성·cost sensitivity를 모두 확인하는 승격 게이트를 연결한다. 기존 `promotion_gate.py`의 reward 중심 판정만으로 Production 자격을 부여하지 않는다.
4. 실제 연구 산출물로 Research/Models UI를 확장하고, clean environment와 측정된 병목을 개선한다.

비용·spread·slippage의 완전한 분해, realistic intrabar fill, 주문 예약 예산, 모든 리스크 항목, 전체 baseline 성과 비교, feature ablation, 새 OOS·paper·live shadow·limited capital 검증은 아직 완료되지 않았다. 이 패치는 과거 결함의 재발 조건을 줄인 것이며 수익성 개선 주장이 아니다.
