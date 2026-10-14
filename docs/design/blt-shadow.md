# BLT shadow — 얼린 BE2 × P1-b′ 구성 병행 장부 (2026-10-14 설계, 첫 세션 2026-10-15)

등록 초안 `docs/protocols/be2-low-turnover-2026-10.md`(시행 BLT, 미고정) §6 의 전방 장부. 사용자 결정(10/14 아침): 1번(BE2 + 저회전)은 shadow 장부 설계까지.
사용자 결정(10/14): **shadow 는 바로**, 첫 세션 2026-10-15. 크론은 리드가 장 마감 뒤에 건다. **화면에는 해시 고정 뒤에** 넣는다(C1·AR 와 같은 이유).

## 무엇인가 — 새 코드 없이 두 장부를 합친 것

| | 알파 | 구성 | 장부 |
|---|---|---|---|
| 현행(N24 shadow) | ranker | 상위 24 · 완충 72 · R10 | `data/_n24_shadow` (이미 돈다) |
| BE2 shadow | **be2** | 상위 24 · 완충 72 · R10 | `data/_be2_shadow` (이미 돈다) |
| P1-b′ shadow | ranker | **N100 · 완충 500 · R20 · θ1.0** | `data/_p1b_shadow` (이미 돈다) |
| **BLT shadow** | **be2** | **N100 · 완충 500 · R20 · θ1.0** | `data/_blt_shadow` (이 설계) |

2×2 가 완성된다 — 알파 효과(BLT − P1B, BE2 − N24)와 구성 효과(BLT − BE2, P1B − N24)를 같은 시점·같은 실전 배관에서 따로 읽는다.
나머지(리스크 패리티 · 노출 V6 · 종목 상한 · 위험 하한 · 섹터 상한 · 보유일 잔여 채움)는 모의계좌 규칙 그대로. 비중이 실전형이라 주 시험(동일가중)과 다르다 — 그 차이를 이 장부가 메운다.

## 흐름

```
22:55 run_daily (운영, 그대로)
22:56 run_score_be2.sh (운영, 그대로)   ── fa_features(그 세션) · signals.be2   ← BLT 가 읽는 점수
00:10 BE2 shadow · 00:40 P1-b′ shadow · 00:55 C1 · 01:05 AR (그대로)
01:15 scripts/run_shadow_blt.sh (초안)
   anchor 전이면 건너뜀(로그 한 줄, rc 0)
   다른 KR run_session · score_be2 가 떠 있으면 기다림 → score-be2 잠금을 한 번 잡았다 놓음(채점이 끝났는지)
   tools/run_session.py --market KR --sandbox data/_blt_shadow   (첫 실행만 --capital 503000000)
```

- 채점은 하지 않는다 — be2 신호는 `run_score_be2.sh` 가 이미 실전 창고 `signals` 에 적는다(관찰 모드, 운영 가중치 0). 신호가 없거나 묵었으면 선정이 fault `alpha_stale`(rc 3)로 멈춘다.
- rc 는 `exit "${RC:-1}"` 로 크론에 그대로(2026-09-26 점검 규칙).

## 부품

| 부품 | 파일 | 비고 |
|---|---|---|
| 덮어쓰기 템플릿 | `config/shadow/blt.config-overrides.yaml` | be2 알파 = BE2 템플릿, 구성 넷 + `risk.max_positions` 110 = P1-b′ 템플릿과 **같은 값**(테스트가 맞춘다) |
| anchor | 같은 파일 `selector.rebalance_anchor` | `2026-10-15` = 첫 세션(빈 장부가 보유일에 시작하지 않게). 그 다음날 01:15 부터 러너가 돈다 |
| 러너 | `scripts/run_shadow_blt.sh` | anchor 다음날부터 돈다. 샌드박스만 · 실주문 없음 |
| 정적 검사 | `tests/invariants/test_blt_shadow_isolation.py` | 템플릿 = BE2 알파 + P1-b′ 구성 · 키 실재 · 샌드박스 한정 · 채점 안 함 · 자리표시면 무동작 |

## 켜는 순서 (리드, 장 마감 뒤)

1. (끝남) 변형 하나 V1 · 첫 세션 2026-10-15 — 사용자 결정 10/14.
3. 크론 한 줄(초안):
   ```
   15 1 * * 2-6 /home/mintkangaroo/Project/Quant_RL_Trading/scripts/run_shadow_blt.sh
   ```
4. 일요일 압축 줄의 샌드박스 목록에 `data/_blt_shadow` 추가.
5. 첫 세션 뒤 `logs/shadow-blt-YYYYMM.log` — rc · 후보 100 · 주문 수 · 첫 구성 회전.
6. 순차 검정 짝(등록 §6): BLT − N24 · BLT − P1B · BLT − BE2 일 초과수익, 원리 짝(be2 − ranker 세션별 ΔIC). Fund 탭 짝 표(`dashboard.seq_*`)에는 **사용자 결정 뒤**에만.

## 운영 장부로 새지 않는 이유

- be2 는 일일 실행기(`SCORERS`)·주간 IC `--save` 목록에 없다(BE2 shadow 와 같은 근거, `test_be2_shadow_isolation`).
- 덮어쓰기 키는 샌드박스 `config-overrides.yaml` 에서만 읽히고 실전 창고는 그 파일을 거부한다. 체크인 기본값(`weights_override {}` · `swap_min_z 0` · `n_candidates 24`)은 그대로.
- 러너는 `--sandbox data/_blt_shadow` 에만 쓴다. 실전 창고에 새로 쓰는 것 없음(채점 안 함).

## 되돌리기

크론 줄을 지우면 끝. 운영 장부·가중치에 남는 것 없음.
