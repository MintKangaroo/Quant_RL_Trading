# BE2 shadow — 얼린 BE2 를 매일 돌리는 병행 장부 (2026-09-29 설계)

사용자 결정(2026-09-29): "BE2 를 실전에 투입 — shadow 모드로". 10월 첫 주 가동 목표. 모의계좌 전환은 11/26 그대로
(11/25 60세션 종료 판정을 섞지 않는다). 금고는 7/1~9/30 로 앞당겨 연다 — 그 심사와 이 shadow 가 **같은 코드**로
피처를 만든다.

## 무엇인가

**BE2** = 마지막 모델 회차(docs/protocols/final-model-round-2026-10.md, 해시 `34abffde1d5e6bed`)의 채택 후보.
BE1(세트 트랜스포머 — 종목마다 60세션 × FA 76열, 같은 날 종목끼리 주의) + C1(GBM·FA, 시행 L 규격)의 세션 안
순위 평균. 판정 대 C1 +2.1%p · 대 C0 +2.2%p. 판정 때는 예측만 저장했으므로 **얼리기**가 먼저다.

**BE2 shadow** = 현행 모의계좌와 **같은 규칙**(상위 24 · 완충 72 · C10 · 리스크 패리티 · 노출 V6 · 섹터 상한)에
알파 자리만 `ranker` → `be2` 로 바꾼 모의 체결 장부(`data/_be2_shadow`).

## 흐름

```
22:55 run_daily (운영, 그대로)  ── signals: chart·event·flow_kr·fundamental·regime·risk·volume·ranker
23:05~23:55 다른 KR shadow 들
00:10 scripts/run_shadow_be2.sh (제안)
   ① tools/score_be2.py --market KR
        analysts/fa_features.build_session(store, KR, 세션, as_of)   → 창고 fa_features (FA 76열, 정규화 끝)
        analysts/be2.Be2Analyst.run(as_of)  ← fa_features 60세션 창 + models/be2 얼린 모델
                                            → 창고 signals (analyst=be2, observed_at=as_of, 관찰 모드)
   ② rc 0 이면 tools/run_session.py --market KR --sandbox data/_be2_shadow
        selector.weights_override = {be2: 1, risk: 1}  (샌드박스 config-overrides.yaml 에서만)
```

## 부품

| 부품 | 파일 | 하는 일 |
|---|---|---|
| FA 열 정의 | `quant_rl_trading/schemas/fa.py` | 76열 이름·순서 상수(kit `feature_names` 와 같다 — 테스트·얼리기 도구가 맞춘다) |
| FA 매일 계산 | `quant_rl_trading/analysts/fa_features.py` | 세션 하나를 그 as_of 로 — 판정 캐시가 부른 **같은 패키지 함수**를 부른다 |
| 밸류업 5 | `quant_rl_trading/analysts/valueup.py` | 시행 BA 함수를 패키지로 옮김(도구는 다시 내보낸다, 규칙 불변) |
| 창고 표 | `fa_features` (store/tables.py) | 세션마다 정규화 끝난 76열. valid_from = observed_at = 세션 공표 시각 |
| 얼리기 | `tools/freeze_be2.py` | 판정 패널 캐시로 BE1·C1 시드 5 를 한 번 학습 → `data/models/be2/` |
| Analyst | `quant_rl_trading/analysts/be2.py` | 얼린 모델로 채점. 평활은 ranker 와 같은 키(`ranker.smoothing_span`) |
| 매일 도구 | `tools/score_be2.py` | ①의 전부. 금고 창 굽기·판정 패널 대조도 같은 도구 |
| 가중치 고정 | `selector/weights.py` `selector.weights_override` | 샌드박스 전용. 체크인 기본값 `{}` = 끔 |
| 러너 | `scripts/run_shadow_be2.sh` | ①→② , 첫 실행만 자본 5.03억 |
| 화면 | 학습 탭 ② 병행 트랙 | 첫 NAV 가 생기면 한 줄 — 모의계좌와 같은 창 수익(TWR 지수끼리) |

## 판정 때와 같게 한 것 (불변식 5)

- **피처**: 점수 6 = 창고 `signals`(연구 캐시 `bake_long_panel` 과 같은 출처) · 원피처 = `Analyst.features(as_of)`
  (`diagnose_ic cache-extra` 와 같은 호출) · G1~G7 = `ranker_sources.build`(세션 **개장** as_of, 월 조각과 같다) ·
  BA = 시행 BA 함수. 정규화는 kit `attach_block`·`finalize` 규칙 그대로(정확 비교 테스트).
  **실측(2026-09-29, 읽기 전용)**: 2026-06-30 세션을 매일 경로로 만들어 판정 패널과 견주니 2,876행 **76열 전부 비트 동일**.
- **행 집합**: 점수 종목 ∪ 그 세션 시세 종목(연구 패널의 타깃 행).
- **판정 때 자료 없던 묶음**: 국장 G3·G6·G7 은 월 조각이 0행이었다 → 빌더를 안 부르고 표지 1(`LIVE_GROUPS`).
- **시간 축**: 판정 큐브 축 = 국장 ∪ 미장 세션. 국장 휴장일(미장만 연 날)은 국장 종목에 0 벡터 한 칸. 실전 창도 같다.
- **큐브 정밀도**: float16 으로 한 번 내렸다 올린다(판정 큐브가 float16).
- **구조·하이퍼파라미터**: 실전 `build_set_ranker` 는 판정 `make_model` 과 같은 파라미터 이름·같은 출력(테스트).

## 판정과 다른 것 (알고 받아들인 것)

1. **시드 평균**: 판정은 시드마다 BE2 포트를 만들어 **지표를** 평균했다. 장부는 하나라 **점수를** 시드 평균한다
   (시드 s 마다 (백분위 BE1_s + 백분위 C1_s)/2 → 다섯의 평균). 앙상블이라 잡음이 준다 — 판정 숫자와 같은 대상은 아니다.
2. **실전 포트 규칙**: 판정 포트는 EMA5·상위 24 동일가중·완충 3N·C10. 실전은 거기에 리스크 패리티·노출 V6·섹터 상한·
   위험 하한이 얹힌다(현행 모의계좌와 같게 — 비교 상대가 모의계좌라서).
3. **`miss_ba` 표지의 미래 참조(연구 패널 쪽 결함)**: 연구 패널은 공시를 판정 창 끝(2026-06-30)까지 한 번에 읽어
   "그 종목에 해당 공시가 한 번이라도 있었나" 로 표지를 세웠다 — 2023 년 세션이 2025 년 첫 배당 공시를 안다.
   실전은 같은 시작점부터 **그 세션 as_of 까지**만 본다. 실측: 2026-06-30 은 동일, 2023-03-02 는 `miss_ba` 11% 가 다르다.
   개수·경과일 피처 자체는 개장 전 관측만 세므로 같다. 모델이 가장 최근에 배운 구간에선 둘이 같아 영향은 작을 것으로
   보지만 **금고 심사 때 적어 둘 것**(표지 하나가 미래를 조금 봤다).
4. **타깃 행의 생존 편향(연구 쪽)**: 연구 국장 행은 5일 뒤 가격이 있는 종목이었다 — 곧 상장폐지될 종목이 빠져 있었다.
   실전은 그날 시세가 있으면 넣는다(2023-03-02 에 2종목 차이).
5. **점수 정정본**: 연구 캐시는 굽는 날(as_of=지금)의 정정본을, 실전은 그 세션 as_of 에 보였던 값을 쓴다.
6. **미장은 안 한다**: 미장 FA 점수 패널은 시행 AT 의 거래대금 상위 1,000 명단이었다 — 실전 명단과 달라 국장만.

## 운영 장부로 새지 않는 이유

- `be2` 는 `session/signals.SCORERS`(일일 실행기)·`tools/measure_ic.ANALYSTS`(주간 IC --save)에 없다 → 측정표에 가중치가 안 생긴다.
- `selector.weights_override` 체크인 기본값은 `{}`(끔). 켜는 곳은 `data/_be2_shadow/config-overrides.yaml` 하나이고,
  실전 창고는 덮어쓰기 파일을 거부한다(`StoreError`).
- 러너는 `--sandbox data/_be2_shadow` 에만 쓴다(`--live-store`·`--live-broker` 없음). 정적 검사: `tests/invariants/test_be2_shadow_isolation.py`.
- 실전 창고에 새로 쓰는 것은 `fa_features`(새 표)와 `signals` 의 `be2` 행뿐 — 둘 다 운영 선정이 안 읽는다.
  (RL 환경의 `analyst_slots` 는 signals 에 있는 Analyst 이름을 관측 칸으로 늘린다 — RL 정책은 지금 꺼져 있다. 켤 때 확인.)

## 멈춤 규칙 (조용히 틀리지 않는다)

- 얼린 모델이 없거나(usable_from 전 포함) 사이드카 해시·구조·피처·파일 지문이 다르면 점수를 안 낸다.
- 60세션 창의 국장 세션 중 하나라도 `fa_features` 가 없으면 점수를 안 낸다(0 으로 채운 창 금지).
- 그 세션 점수 6 열 중 하나라도 비었으면(일일 실행기 전·Analyst 사망) FA 를 **적지 않는다** — append-only 라 빈 열이 굳는다.
- 위 셋이면 `score_be2` rc 3/4 → 러너가 shadow 세션을 돌리지 않는다(be2 점수 없이 돌면 후보가 비어 보유를 판다).

## 리드가 할 일 (순서)

1. **얼리기** (D1 끝난 뒤, 머신 혼자):
   `setsid nohup .venv/bin/python -u tools/freeze_be2.py --verify >> logs/freeze-be2.log 2>&1 &`
   먼저 `--plan` 으로 자르는 날(2026-06-16 — 7/1 앞 11 국장 세션, `--plan` 실측)·재학습 지점을 확인. 추정: chain 8~8.5시간,
   최대 RSS 5.5~6.5GB(패널 4.8GB + GBM). 급하면 `--schedule cold`(1.5~3시간, 판정 모델의 학습 경로와 다르다).
   `--verify` 결과(판정 마지막 블록 재현: 트랜스포머 최대 차·순위상관, GBM 최대 차)를 사이드카에서 확인.
2. **금고 창 피처 굽기**: `tools/score_be2.py --start 2026-04-01 --end <어제> --features-only`
   (7/1 첫 채점에 60세션 창이 필요 — 4월부터). 세션당 40~110초·최대 RSS ≈ 2.5GB → 약 125세션 2~4시간.
   6월 세션은 `--compare-panel` 로 판정 패널과 대조(2026-06-30 은 이미 비트 동일 확인).
3. **금고 창 신호**는 금고 등록(해시 고정) 뒤에만: `--signals-from 2026-07-01`. 그 전에 적으면 be2 의 금고 성적이
   창고·화면(IC)에 생긴다. `score_be2` 가 앞당김 등록(`docs/protocols/vault-early-open-2026-10.md`)이 고정되기 전에는
   금고 창(~9/30) `--signals-from` 을 거부한다(rc 2). 금고 **판정**은 이 신호를 읽지 않는다 — `tools/vault_judge.py --window early`
   가 같은 `fa_features` 와 같은 함수(`be2.session_batch`·`Be2Model.seed_predictions`)로 시드별 예측을 직접 낸다.
   대조 C0 는 `tools/freeze_be2.py --arm C0` 로 따로 얼린다(15~25분, RSS ≈ 5GB).
4. **크론** (제안, 걸지 않았다): `10 0 * * 2-6 /home/mintkangaroo/Project/Quant_RL_Trading/scripts/run_shadow_be2.sh`
   · 일요일 압축 줄의 샌드박스 목록에 `data/_be2_shadow` 추가.
5. 첫 세션 뒤 `logs/shadow-be2-YYYYMM.log` — score rc·후보 24·주문 수. 학습 탭 ② 병행 트랙에 BE2 줄이 뜬다.

## 되돌리기

크론 줄을 지우면 끝이다. 운영 장부·가중치에는 아무것도 안 남는다(`fa_features`·`be2` 신호는 append-only 기록으로 남는다).
