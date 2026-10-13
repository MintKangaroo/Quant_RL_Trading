# C1·AR shadow — 금고가 고른 두 랭커 변형을 따로 병주하는 장부 (2026-10-13 설계)

사용자 결정(2026-10-13 설문, 금고 early 판정 뒤):
1. **C1**(대조군이었던 GBM·FA) → 새 시행 **C1G** 로 등록 고정 + shadow. 판정은 두 번째 금고(second 창 10/1~11/13, 11/23 개봉) —
   등록 초안 `docs/protocols/c1-gbm-fa-second-2026-10.md`.
2. **AR**(내부자 G4+G7, 금고 early 두 시장 통과)과 C1 은 **따로 두 개**의 랭커 변형으로 각각 shadow. 실전(모의계좌 `data/_paper`) 반영은
   나중에 사용자 재승인.

본보기는 BE2 shadow(`docs/design/be2-shadow.md`) 그대로다 — 다른 것은 알파 자리에 끼우는 점수 하나뿐이다.

## 무엇인가

| | C1 shadow | AR shadow |
|---|---|---|
| 알파 Analyst(새 이름, 관찰 전용) | `c1fa` | `ar_insider` |
| 모델 | 얼린 C1 = `data/models/be2/be2-v1.0.0-20260630-gbm-seed{0..4}.txt`(BE2 사이드카 안 GBM, 사이드카 e3c347d129cc1c33) | 얼린 AR 처리 = `AR-treat-s{0,1,2}`(등록 문서 해시), `data/models/ar_insider/` 에 복사 |
| 입력 | 창고 `fa_features`(FA 76열, BE2 와 같은 표) | 창고 `signals` 점수 6 + 내부자 원자료(G4·G7, 세션 개장 as_of) |
| 채점 | 시드 0~4 GBM 의 세션 안 백분위 평균 | 시드 0·1·2 GBM 의 세션 안 백분위 평균 |
| 장부 | `data/_c1_shadow` | `data/_ar_shadow` |
| 설정 | `config/shadow/c1.config-overrides.yaml` → `selector.weights_override = {c1fa: 1, risk: 1}` | `config/shadow/ar.config-overrides.yaml` → `{ar_insider: 1, risk: 1}` |
| 러너 | `scripts/run_shadow_c1.sh` | `scripts/run_shadow_ar.sh` |
| 판정 | 금고 second(11/23) — `vault_judge` 의 C1G(이 장부는 판정에 안 쓴다) | 금고 early 로 끝났다(채택 후보). 다음 관문은 재학습 게이트(11월 개봉 뒤) |

두 장부 모두 나머지는 **현행 모의계좌 규칙 그대로**(상위 24 · 완충 72 · C10 · 리스크 패리티 · 노출 V6 · 섹터 상한 · 위험 하한). 비교 상대는
KR shadow(`data/_shadow`, 현행 ranker)와 모의계좌.

## 흐름

```
22:55 run_daily (운영, 그대로)        ── signals: chart·event·flow_kr·fundamental·regime·risk·volume·ranker
22:56 run_score_be2.sh (운영, 그대로)  ── fa_features(그 세션) · signals.be2
23:05~00:45 다른 shadow 들 (그대로)
00:55 scripts/run_shadow_c1.sh (제안)
   ① tools/score_shadow_alpha.py --analyst c1fa   [잠금 data/_locks/score-be2.lock]
        FA 가 없으면 score_be2.score_session(features_only) 로 먼저 적는다(같은 함수 · 같은 run id)
        analysts/c1fa.C1FaAnalyst.run(as_of) → signals (analyst=c1fa, valid_from = observed_at = 세션 공표 시각)
   ② rc 0 이면 tools/run_session.py --market KR --sandbox data/_c1_shadow
01:05 scripts/run_shadow_ar.sh (제안)
   ① tools/score_shadow_alpha.py --analyst ar_insider
        analysts/ar_insider.ArInsiderAnalyst.run(as_of) → signals (analyst=ar_insider, 같은 규약)
   ② rc 0 이면 tools/run_session.py --market KR --sandbox data/_ar_shadow
```

시각: 00:05~00:45 shadow 들(ta·be2·tsfm·ix·ixt·p1b·tg)과 겹치지 않고 22:20~00:00 정규 작업을 피한다. 00:50 `collect_n2_periodic`(수집, 가볍다)·
01:20 `collect_filing_texts` 사이. 러너는 다른 KR `run_session` 이 떠 있으면 끝날 때까지 기다린다(be2 와 같은 대기 패턴).

## 신선도 (selector/freshness.py 규약)

두 Analyst 모두 **일일 규약**으로 적는다 — `valid_from = observed_at = 세션 공표 시각`(score_be2 와 같다). 그래서 세션 S 의 선정은
`valid_from ≥ S 정규장 마감` 인 신호만 신선하다고 본다. 채점이 실패한 날은 러너가 세션을 안 돌리고(rc ≠ 0), 손으로 세션을 돌리더라도
선정이 fault `alpha_stale`(rc 3)로 멈춘다 — 묵은 점수로 조용히 고르지 않는다. confidence 는 `ic.rolling_confidence`(자기 신호의 롤링 IC)이고
신호가 쌓이기 전엔 `NO_EVIDENCE_CONFIDENCE`(1.0)다 — 롤링 IC 가 0 이하가 되면 0 이 되어 `alpha_no_confidence` 동결이 걸린다(be2 와 같다).

## 왜 AR 은 얼린 모델인가 (다시 학습하지 않는다)

등록 문서(insider-forward-2026-09.md) 문구는 "채택 후보 → 랭커 피처 추가 → **재학습 게이트** → shadow" 다. 그 게이트(modelops-ranker.md ③)는
"2026-11 하순 개봉 뒤부터 매월 `train_ranker --through <직전 월말>`" 이고 "개봉 전엔 재학습 없음" 이다. 지금 같은 규격으로 다시 학습하면
- 2026-06-30 까지로는 금고가 판정한 파일과 **같은 것**을 다시 만들 뿐이고(시드·자료·하이퍼파라미터 같음),
- 2026-09-30 까지로 늘리면 h5 라벨이 10/1 이후 가격에 닿아 **second 창(11/23 금고)을 학습이 본다** — 퍼지로 9/23 까지 자르더라도 그 모델은
  금고가 판정한 모델이 아니고, 재학습 게이트(새 열의 직전 60세션 IC ≥ 옛 열 − 0.01)를 거치지 않은 열이다.

그래서 shadow 는 **금고가 판정한 바로 그 파일**(`data/_diag/vault-reviews/AR-treat-s*.txt`, 등록 해시 대조)을 쓰고, 재학습은 원래 일정(11/23 개봉 뒤
재학습 게이트)대로 간다. 게이트를 통과한 새 열이 생기면 그때 이 shadow 의 모델을 바꿀지 사용자에게 묻는다.

## 판정 때와 같게 한 것 (불변식 5)

- **C1**: 입력·창·행 집합은 `be2.session_batch`, GBM 은 `Be2Model.predict_gbm` — 금고 판정부 `vault_judge.be2_predictions` 와 같은 함수다.
  60세션 창이 찬 세션만 채점(판정이 그랬다). 사이드카 지문이 `c1fa.SIDECAR_HASH` 와 다르면 점수를 안 낸다.
- **AR**: 판정 패널(`trial_pooled.load_kr` → `rank_gauss` → `trial_ranker_sources.attach`)과 같은 규칙 — `ar_insider` 독스트링의 표.
  점수 6 = `fa_features._scores`(그 세션 as_of), 내부자 = `fa_features._group`(= `ranker_sources.build`, 세션 개장 as_of — 월 조각과 같은 호출),
  rank-gauss 는 `ranker.rank_gauss`(연구 `trial_pooled_rank.rank_gauss` 와 같은 값 — 테스트), 결측 0, 열 순서 = `freeze_ar` 의 treat.
  국장 G7 은 판정 때도 0행이라 전부 0 이다.

## 판정과 다른 것 (알고 받아들인 것)

1. **시드 평균**: 판정은 시드마다 포트를 만들어 지표를 평균했고, 장부는 하나라 **점수를** 시드 평균한다(be2 와 같다).
2. **실전 포트 규칙**: 판정 포트는 EMA5·상위 24·완충 3N·10세션. 실전은 리스크 패리티·V6·섹터 상한·위험 하한이 얹힌다(비교 상대가 모의계좌라서).
3. **AR 행 집합**: 판정 패널은 "점수 종목 ∩ h5 타깃이 있는 종목", 매일은 "점수 종목 ∩ 그 세션 시세가 있는 종목" — 타깃은 미래라 모른다
   (곧 상장폐지될 종목이 남는다, be2-shadow.md 4 와 같은 차이). rank-gauss 는 열마다 값이 있는 종목 안에서만 매기므로 그 몇 종목만큼만 다르다.
4. **점수 정정본**: 연구 캐시는 굽는 날의 정정본, 매일은 그 세션 as_of 에 보였던 값.
5. **C1 의 `miss_ba`**: 학습은 연구 패널의 미래참조 표지로, 채점은 as_of 표지로(금고 앞당김 문서 '명시해 두는 차이' 4 — be2 와 같다).
6. **미장은 안 한다(지금)**: c1fa 는 be2 와 같은 이유(미장 FA 명단이 실전과 다르다). AR 은 두 시장 한 모델이지만 미장 shadow 장부의
   배선(미장 점수 6 의 실전 명단·M1 합성·달러 슬리브)은 따로 정한다 — 사용자 지시 "국장 장부부터".

## 운영 장부로 새지 않는 이유

- `c1fa`·`ar_insider` 는 `session/signals.SCORERS`(일일 실행기)·`tools/measure_ic.ANALYSTS`(주간 IC --save)에 없다 → 측정표에 가중치가 안 생긴다.
- `selector.weights_override` 체크인 기본값은 `{}`(끔). 켜는 곳은 두 샌드박스의 `config-overrides.yaml` 뿐이고 실전 창고는 덮어쓰기 파일을 거부한다.
- 러너는 `--sandbox data/_c1_shadow`·`data/_ar_shadow` 에만 쓴다(`--live-store`·`--live-broker` 없음). 정적 검사: `tests/invariants/test_c1_ar_shadow_isolation.py`.
- 실전 창고에 새로 쓰는 것은 `signals` 의 `c1fa`·`ar_insider` 행과(be2 채점이 실패한 날만) `fa_features` 한 세션 — 운영 선정이 안 읽는다.

## 금고 second 창 봉인 — 소급하지 않는다

- `tools/score_shadow_alpha.py` 는 `SHADOW_START`(2026-10-13) 전 세션을 채점하지 않는다(rc 2). 범위 채점(--start/--end)이 없다.
  second 창(10/1~11/13)은 11/23 에 한 번 여는 금고다 — 10/1~10/12 를 소급 채점하면 그 IC·장부가 창고·화면에 생긴다.
- 장부는 첫 세션부터 앞으로만 쌓인다. 장부 성적은 C1G 판정 기준에 들어가지 않고, **장부 성적을 보고 등록 문서·기준을 고치지 않는다.**
- 화면(학습 탭 ② 병행 트랙·알파 IR 표)에 두 장부를 넣는 것은 이 배선에 **포함하지 않았다** — 넣으면 second 창 성적이 매일 보인다. 사용자 결정 사항.

## 리드가 할 일 (순서)

1. **AR 모델 설치**(한 번, 수 초): `.venv/bin/python tools/install_ar_shadow_models.py` 로 점검 → `--apply`.
   금고 판정 파일을 `data/models/ar_insider/` 로 복사하고 지문을 등록 해시와 대조한다(다르면 rc 2/3 으로 멈춘다).
2. **크론 두 줄**(장 마감 뒤에 건다 — 아래 '크론 줄 초안').
3. 일요일 압축 줄의 샌드박스 목록에 `data/_c1_shadow data/_ar_shadow` 추가.
4. 첫 세션 뒤 `logs/shadow-c1-YYYYMM.log`·`logs/shadow-ar-YYYYMM.log` — score rc · 후보 24 · 주문 수.
5. C1G 등록 해시 고정(사용자 확인 뒤): 문서 머리말 "초안" → "고정" 커밋 → sha256[:16] 을 `vault_judge.C1G_PROTOCOL_HASH` 에 → `--plan --window second` 가 시행 목록에 C1G 를 찍는지 확인.

## 크론 줄 초안 (걸지 않았다)

```
55 0 * * 2-6 /home/mintkangaroo/Project/Quant_RL_Trading/scripts/run_shadow_c1.sh
5 1 * * 2-6 /home/mintkangaroo/Project/Quant_RL_Trading/scripts/run_shadow_ar.sh
```

## 되돌리기

크론 줄을 지우면 끝이다. 운영 장부·가중치에는 아무것도 안 남는다(`c1fa`·`ar_insider` 신호는 append-only 기록으로 남는다).
