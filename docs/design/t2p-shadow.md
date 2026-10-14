# T2P 전방 shadow — 잔차 정답(T2) GBM × P1-b′ 구성 (2026-10-14 설계, 미가동)

사용자 결정(10/14): **T2P 는 등록하지 않고 전방 shadow 만.** 기준 틀은 `docs/protocols/t2-target-p1b-2026-10.md`(등록 안 함 · 전방 기록)를 읽는 틀로 쓴다.
크론은 걸지 않았다. 화면에도 넣지 않는다(켤 때 사용자 결정).

## 무엇인가

| 장부 | 알파 | 구성 | 상태 |
|---|---|---|---|
| `data/_p1b_shadow` | ranker | B1′(N100 · 완충 500 · R20 · θ1.0) | 돈다(10/8~) — **짝 상대** |
| `data/_blt_shadow` | be2 | B1′ | 10/15 첫 세션 |
| **`data/_t2p_shadow`** | **t2fa**(얼린 T2) | B1′ | 이 설계 |

같은 구성에 알파만 셋 — T2P − P1B 가 "정답을 바꾼 몫", 질문은 등록 초안 그대로 **회전이 더 줄고 비용 후로 P1-b′ 보다 나쁘지 않은가**.

## 켜기 전에 필요한 것 (아직 없다)

1. **얼린 T2**: `tools/trial_sprint_1123.py freeze --trial T2P`(자료 끝 2026-06-30 · 학습 끝 = 그 앞 10세션, 시드 0~4, 6점수 국장 단독, 20~40분 · RSS ≈ 3GB) →
   산출 `data/_diag/eval-ladder/sprint-1123/T2P/models/T2P-T2-s*.txt` 를 `data/models/t2fa/` 로 복사하고 지문 목록(sidecar json)을 둔다.
2. **`t2fa` Analyst**(`quant_rl_trading/analysts/t2fa.py`, c1fa 본보기): 입력 = 창고 `fa_features` 의 6점수(`be2.session_batch` — 얼린 C0 와 같은 입력),
   시드 0~4 GBM 의 세션 안 백분위 평균 → 순위 z → `ranker.smoothing_span` EMA. 지문 고정(사이드카 해시 상수), 관찰 모드(SCORERS·주간 IC 목록에 없음).
3. `tools/score_shadow_alpha.ANALYSTS` 에 `t2fa` 추가(국장만, `SHADOW_START` 규칙 그대로).
4. 격리 테스트(`test_c1_ar_shadow_isolation` 의 CASES 에 t2p 한 줄)와 템플릿 anchor 를 첫 세션 날짜로.

러너는 위가 갖춰지기 전엔 **아무것도 하지 않는다**: anchor 가 `2099-01-01` 이거나 `data/models/t2fa/` 가 없으면 로그 한 줄 · rc 0.

## 부품

| 부품 | 파일 |
|---|---|
| 템플릿 | `config/shadow/t2p.config-overrides.yaml` — `{t2fa: 1, risk: 1}` + P1-b′ 구성 넷 + `max_positions` 110(P1-b′ 템플릿과 같은 값) |
| 러너 | `scripts/run_shadow_t2p.sh` — ① `score_shadow_alpha --analyst t2fa` ② rc 0 이면 `run_session --sandbox data/_t2p_shadow` |
| 크론 초안 | `25 1 * * 2-6 /home/mintkangaroo/Project/Quant_RL_Trading/scripts/run_shadow_t2p.sh` (BLT 01:15 뒤) |

## 전방 기록 — 읽는 법 (판정 아님)

- 순차 검정(`modelops/sequential.confidence_sequence`, α 0.10, t* 250): T2P − P1B 일 초과수익. 하한 > 0 "우세 확정" · 상한 < 0 "열위 확정" · 그 사이 "모름".
- 회전: 60세션마다 연 회전 비(T2P / P1B). 비가 1 을 넘으면 질문(회전 감소)이 깨진 것이다.
- 내리는 조건(사용자에게 올림): 상한 < 0 · MDD 가 P1B 보다 3%p 넘게 깊다 · 회전 비 > 1 이 두 번 연속.

## 되돌리기

크론 줄을 지우면 끝. 운영 장부·가중치에 남는 것 없음(`t2fa` 신호는 append-only 기록).
