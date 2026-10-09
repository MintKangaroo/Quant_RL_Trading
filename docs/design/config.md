# 설정 스키마

불변식 10번: **임계치는 `store.config` 에서 읽는다. 하드코딩 금지.**

같은 숫자가 학습·Executor·대시보드·리포트 네 곳에 흩어지면 반드시 어긋난다.
12%를 13%로 바꿨는데 화면만 12%를 보여주는 상황이 실제로 일어난다.

`config/quant_rl_trading.yaml` 하나가 유일한 출처다.

---

## config/quant_rl_trading.yaml

```yaml
reward:
  drawdown_free: 0.12        # 자유구간 — 낙폭 페널티 0
  drawdown_warn: 0.22        # 페널티 급증 시작
  drawdown_hard: 0.30        # 에피소드 종료 / 킬스위치
  w_free: 0.0
  w_mid: 1.5
  w_hot: 8.0
  terminal_penalty: -10.0
  normalize_returns: return_std   # none | return_std | popart

benchmark:
  kr_weight: 0.5
  us_weight: 0.5
  # ⚠️ 가격지수(PR)다. TR 은 지금 키로 못 받는다 (accounting.md §7.1).
  # 이름은 창고에 실제로 있는 entity_id 그대로 — 대용치로 바꿔치기하지 않는다.
  kr_index: "KR:IDX:KRX 300"
  us_index: US:IDX:SP500
  total_return: false
  base_value: 100.0
  max_staleness_days: 10     # 이보다 오래된 종가는 휴장이 아니라 구멍이라 null

accounting:
  base_currency: KRW
  snapshot_time: "15:40"     # 한국시간, 하루 1회
  dividend_recognition: ex_date    # ex_date | payment_date (ex_date 고정)
  dividend_tax_kr: 0.154
  dividend_tax_us: 0.15
  capital_gains_us: 0.22     # 충당금으로만. 일간 NAV 미반영
  capital_gains_allowance_krw: 2_500_000   # 해외 양도세 기본공제(연간)
  return_method: TWR
  fee_kr: 0.000_15           # 위탁수수료(편도)
  fee_us: 0.002_5            # 해외주식 위탁수수료(편도)
  transaction_tax_kr: 0.001_8  # 증권거래세 — **매도에만** 붙는다
  transaction_tax_kr_etf: 0.0  # 국내 상장 ETF(KR:ETF:*) 매도세 — 거래세 면제·농특세 없음 (2026-10-04, 지수+V6 shadow)

universe:
  min_turnover_20d_kr: 500_000_000   # 원
  min_turnover_20d_us: 1_000_000     # 달러
  min_listed_days: 180
  max_price_ratio: 0.15      # 1주 가격 / 자본
  exclude_flags: [관리종목, 거래정지, 정리매매]

execution:
  max_adv_ratio: 1.0         # 일 거래대금 대비 매수 상한. 2026-08-28 0.03→1.0 (모의 단계, 후보 43% 절단 해소). 실전 전환 전 재조임
  max_liquidation_days: 3
  defer_minutes: 30          # 개장 후 신규매수 보류
  order_type: limit          # 시장가는 청산·킬스위치에만
  max_slippage: 0.005
  slice_count: 4
  slice_interval_sec: 60
  plan_source: rule          # E1 집행 계획을 읽을 부품. rule 이면 표를 안 읽는다. 샌드박스에서만 켠다 (execution-safety.md E1)
  retry_after_sec: 300
  max_retries: 3
  # 매도 대금이 예수금이 되기까지의 거래일. 국내 주식은 D+2.
  # **0 으로 두면 오늘 판 돈으로 오늘 사게 된다.** 이게 없던 동안 백테스트가
  # 레버리지 3.2배까지 갔다 — 가용 현금을 보는 코드가 아예 없었다 (2026-08-15).
  settlement_days: 2
  # 시장별 키가 있으면 그것을 쓴다(ledger.settlement_days_for). 국장 0(2026-09-21, 상계 — accounting.md §1),
  # 미장 0(2026-10-07, T+1 매도대금은 다음 날 매수 체결일에 이미 결제돼 있다 — accounting.md §1). 과거 as_of 는 옛 값(2·1)으로 재현된다.
  settlement_days_kr: 0
  settlement_days_us: 0

analyst:
  ic_threshold: 0.03
  ic_min_samples: 200
  ic_rolling_window: 60
  horizon_days: 5
  retrain_ic_floor: 0.01     # 이하로 떨어지면 재학습
  block_ratio_cap: 0.30      # 뉴스·SNS 하루 거부 상한
  verdict_ttl_days: 5

selector:
  n_candidates: 24
  corr_threshold: 0.7
  corr_penalty: 0.3
  sector_cap: 0.35
  population: 64
  generations: 40
  l1_penalty: 0.01
  turnover_penalty: 0.05
  checkpoint_dir: logs/evolution   # 세대 체크포인트 JSONL 이 쌓이는 곳
  checkpoint_every: 1              # 몇 세대마다 한 줄 남길지
  min_fold_gap_days: 21            # 한 세대 두 폴드의 최소 시작일 간격(달력일)

allocator:
  action_reflection_floor: 0.30   # 미만이면 경고 — RL이 아니라 룰 시스템
  episode_days: 250
  n_max_candidates: 30
  gamma: 0.997
  gae_lambda: 0.95
  rl:
    checkpoint: ""                # 정책 체크포인트. 비면 어디서도 정책을 쓰지 않는다
    modes: ["paper"]              # 정책이 결정하는 장부 모드 — paper 만. shadow·live 는 룰 (rl-training.md §13)

fx:
  rebalance_deadband: 0.10   # 10%p 넘을 때만 환전
  rebalance_weekday: FRI

killswitch:
  drawdown_trigger: 0.30
  order_fail_rate: 0.10
  liquidate_on_trigger: false     # 기본은 신규매수만 차단

capital:
  gate_min_trading_days: 60
  gate_max_order_fail_rate: 0.01
  gate_max_missing_rate: 0.005
  gate_slippage_tolerance: 0.30
  step_multiplier: 2.5

llm:
  monthly_budget_usd: 50
  news_screen_model: haiku
  news_deep_model: sonnet
  review_model: sonnet
  filing_events_monthly_budget_usd: 20   # 시행 L1 LLM 공시 추출만의 달 예산(2026-09-28) — 전체 예산 안에서 따로 멈춘다
```

### 구현이 추가한 섹션

명세 초안에 없던 값들이다. 구현하면서 실제로 필요했고, 하드코딩할 뻔한 것들이라
설정으로 끌어올렸다.

```yaml
collector:                     # 킬스위치가 보는 수집 오류율
  error_rate_window_sec: 120.0
  error_rate_min_samples: 8    # 표본이 적을 때 성급히 끄면 잡음 하나로 멈춘다
  call_history_sec: 600.0

backfill:
  years: 5
  kr_publication_lag_seconds: 1800   # 세션 종료 + 이 지연 = observed_at
  us_publication_lag_seconds: 1200
  us_on_time_until_kst: "13:30"  # 미장 증분 봉이 공표일(KST) 이 시각 뒤에 받히면 observed_at = 받은 시각(data-contract §5-0b). wait_us_prices 마감과 같다. 새 키(2026-10-07)
  shorting_lag_days: 2         # 공매도는 T+2. 0이면 flow_kr 이 미래를 본다
  session_pause_ms: 200

data:
  assumed_latency_seconds: 300 # 실측 p90 으로 갱신하기 전의 보수적 초기값

data_quality:                  # 데이터 화면 경고선
  coverage_warn: 0.98
  missing_warn: 0.01
  latency_p90_warn_ms: 300000
  default_lookback_days: 90
  max_lookback_days: 400       # 화면 하나가 창고를 통째로 올리지 않게
  failure_rows: 50
  ready_min_coverage: 0.9      # 수집 완료 판정 — 기대 세션의 시세 종목 수가 직전 세션들 최대치의 이 비율 미만이면 "부분 수집" = 아직
                               # (tools/plan_recovery, wait_us_prices·reboot_recover 가 읽는다). 2026-09-29 재부팅이 미장 수집을 b000(A~D)에서
                               # 끊었는데 "봉이 하나라도 있으면 준비됨" 이라 세션이 1/4 배치로 돌았다(G1 후보 335/450 결측). 새 키(2026-10-07)
  index_divergence_warn: 0.015 # 지수 짝(KRX300↔K200·KRX100↔K200·TMI↔코스피) 같은 날 일수익 차가 이보다 크면 경고만(data-contract §3-1).
                               # 근거: 2020-08~2026-10 약 1,500세션 최대 1.20%p·99.9% 분위 ≤0.92%p → 0건. 새 키(2026-10-03) — seed_config --apply

execution:                     # 체결 시뮬레이터
  impact_k: 0.1                # 충격비용 = k × 변동성 × √(주문량/ADV)
  min_order_value: 100000.0    # 이보다 작으면 수수료가 잡아먹는다

exposure:                      # 노출 제어 (selector/exposure.py)
  regime_confirm_sessions: 2   # 국면 배수 확인 기간 — 낮추기 즉시, 올리기 N 세션 연속 확인.
                               # crisis↔volatile 이 하루걸러 뒤집혀 절반을 팔았다 사던 왕복을 막는다 (2026-08-28)
                               # **세는 축은 관측된 종가 세션이다**(2026-10-04 결함 수정) — 직전 N 세션 = as_of 창고에 실제로 있는
                               # 마지막 지수 세션들. portfolio-construction.md "노출 국면 확인 창"

selector:
  exit_rank: 48                # 완충 구간 — 보유 종목은 이 순위 안이면 남긴다 (진입 24). selector.md §5
  swap_min_z: 0.0              # 비용 인지 교체 문턱(단면 z) — 완충 밖으로 떨어진 보유도 z 차이 ≥ 이 값인 진입이 있을 때만 판다.
  floor_analyst: risk          # 위험 하한(risk_floor_percentile)을 재는 Analyst. 실전 risk, 시행 IX-T 샌드박스만 tsfm(2026-10-09). 키가 없으면 risk.
  extra_floor_analyst: ''       # 두 번째 하한 Analyst(기본 끔) — 첫 하한 뒤 남은 후보에서 한 번 더 자른다. TF 전환 후보는 tsfm.
  extra_floor_percentile: 0.0  # 두 번째 하한 비율(0 = 끔).
  ceiling_analyst: ''           # 상한(칼날 빼기) Analyst — 점수 상위 비율을 뺀다(기본 끔). TB 전환 후보는 tsfm.
  ceiling_percentile: 0.0      # 상한 비율(0 = 끔). 개수 ⌊n×q⌋ 로 고른다.
  combo_floor_analyst: ''       # 결합 하한 Analyst — (합성 점수 백분위 + 이 Analyst 백분위)/2 하위를 뺀다(기본 끔). TC 전환 후보는 tsfm.
  combo_floor_percentile: 0.0  # 결합 하한 비율(0 = 끔). 개수 ⌊n×q⌋.
                               # 0 = 끔(옛 동작). 샌드박스 전용 — P1-b′ shadow(data/_p1b_shadow)가 1.0. selector.md §5 6번 (2026-10-08)
  hold_fill_min_weight: 0.002  # 보유일 잔여 채움 하한 — 직전 재조정 목표 주식 수와 보유의 차가 자본의 이 비율 이상인 종목만 (selector.md §5 7번, 2026-10-04)
  weights_override: {}         # 샌드박스 전용 {analyst: weight} — 비면 측정표(analyst_weights). BE2 shadow 가
                               # {be2: 1.0, risk: 1.0} 로 켠다(be2-shadow.md). 실전 창고는 덮어쓰기 파일을 거부한다
  fixed_basket: []             # 샌드박스 전용 — 선정을 건너뛰고 이 종목들을 같은 점수로. 지수+V6 shadow 가
                               # [KR:ETF:069500] 로 켠다(portfolio-construction.md "지수+V6 트랙")

collectors:                    # 수집기가 "조용한 실패" 를 rc 로 내보내는 문턱
  consensus_max_fail_ratio: 0.10   # 국장 컨센서스(tools/collect_consensus_naver.py) 종목 실패 비율.
                                   # 넘으면 사유를 적고 rc=1. 평소 실패는 ~5%(2,800 중 ~135 —
                                   # 없는 코드·신규 종목). 2026-09-11 원본 주소가 바뀌어 100% 가
                                   # 실패했는데 rc 가 0 이라 2주를 몰랐다
  dart_daily_limit: 20000          # OpenDART 키 하나의 일 한도(KST 자정에 다시 찬다)
  dart_text_backfill_daily_cap: 15000  # 그중 공시 원문 과거 백필(tools/backfill_filing_texts.py, 시행 TX 재료)의 몫.
                                   # 날짜별 장부 data/_dart_quota/ 로 센다. 나머지 5,000 은 정규 수집들 몫.
                                   # 몫 ≥ 한도면 도구가 rc=2 로 멈춘다. 2026-10-02 심음(seed_config --apply)
  intraday:                        # 국장 분봉 수집 확대(ls-api.md §0-14, 2026-10-05 사용자 승인)
    kr_top_n: 300                  # 20세션 평균 거래대금 상위 N(보유·후보는 별도로 늘 포함). 예산 초과가 이어지면 줄인다
    adv_sessions: 20
    wide_intervals: [1m, 5m]       # 넓게 받는 구간. 15m·1H·4H 는 보유·후보만
    kr_min_interval_sec: 1.1       # t8412 카탈로그 한도 초당 1(TR 별)
    run_budget_sec: 780            # 도구 한 번(한 구간)의 예산. 넘으면 남은 종목은 안 받고 rc=1
    max_fail_ratio: 0.10           # 종목 실패 비율 — 넘으면 rc=1(적재는 한다)
    max_consecutive_failures: 10   # 연속 실패면 API 장애로 보고 그 구간을 멈춘다
    qrycnt_live_1m: 120            # 장중 회차의 호출당 봉 수. 개장 뒤 첫 회차·마감 회차는 500
    qrycnt_live_5m: 30
    full_fetch_minutes_after_open: 20
    kr_wide_when_orders_share_key: false  # 주문이 같은 appkey(LS_)를 쓰는 실전 모드에서도 넓힐지 — 실측 전엔 끈다

modelops:                      # 랭커 ModelOps (modelops-ranker.md ①)
  ranker:
    fail_streak: 2             # 랭커 IC 가 합격선 아래로 연속 이 횟수면 "랭커 감쇠"
    input_decay_ratio: 0.5     # 랭커 입력 IC 가 직전 측정의 이 비율 아래면 "입력 감쇠"
  exposure_eval_sessions: 120  # regime(노출 지표) 평가 창 — 최근 N세션 노출 적용 지수 대 지수 100%. 표시만, 임계 없음.
                               # 새 키(2026-10-04) — seed_config --apply

dashboard:                     # 학습 탭 "마지막 모델 회차" 카드의 상태 배지 (dashboard.md §5)
  training_stall_factor: 3     # 마지막 진행 기록이 평균 단위(블록·폴드) 시간 × 이 배수보다 오래되면 "느림/멈춤 의심"
  training_trend_window: 5     # 최근 이 단위 수에서 학습 손실↓ · 학습창 안쪽 검증↓(나빠짐) 이면 "과적합 의심".
                               # 판정 창은 보지 않는다 — 사전등록. 새 키라 기존 창고엔 tools/seed_config.py --apply 로 심는다
  fill_rate_window_sessions: 20  # 트레이딩 탭 "체결율" 칸의 두 번째 기간 — 전송 조각이 있는 최근 N 세션 (dashboard.md §4).
                               # 새 키(2026-09-29) — 창고에 없으면 화면은 당일만 재고 창 칸에 이유를 적는다
  ir_windows_sessions: [20, 60]  # 장부별 지수 대비 IR 패널(dashboard.md §4)의 롤링 창 — 장부 세션 N개. '전체'·'리셋 뒤' 는 항상 붙는다
  ir_min_sessions: 20          # 창의 세션이 이보다 적으면 IR·β·α·추적오차 대신 '표본 부족'
  ir_reset_date: "2026-11-26"  # 모의계좌 측정 리셋일(실자금 관문 측정 창 시작). 이날부터의 창을 따로 싣는다 — 전에는 빈 칸.
                               # 새 키(2026-10-05) — seed_config --apply. 창고에 없으면 패널은 숫자 없이 이유를 적는다

allocator:                     # 유동시총 가중(float_cap — Z2·미장 G1 트랙). portfolio-construction.md "Z2 트랙"
  float_cap_limit: 0.10        # 한 종목 상한. 샌드박스 덮어쓰기로 켠다
  float_cap_min_coverage: 0.8  # 후보 중 시총을 아는 비율이 이보다 낮으면 동일가중으로 물러선다.
                               # 아는 몇 종목에만 예산을 다 실으면 나머지가 목표 0 = 이유 없는 전량 매도다 (2026-09-26)
  env:
    kr_policy_rate_series: "KR:RATE:BASE_DAILY"   # 한미 정책금리차 칸이 읽는 `indices` 이름. ECOS 722Y001 **일별**(결정일에 바뀐다),
                               # 관측 = 그 세션 마감 + 국장 공표 지연(tools/backfill_ecos_daily_rates.py). 정정 2026-10-06 — 옛 값
                               # "KR:RATE:BASE"(월별을 그 달 1일·관측 2일로 찍음)는 달 중간 금리 결정을 **23~27일 미리 보여준다**
                               # (2024-11-28 인하가 11/02 에 보임). 옛 행은 append-only 라 남아 있고 2026-10-06 이후 사용 중지.
                               # seed_config 정정본은 지금 시각 발효라 과거 as_of 조회는 여전히 옛 이름을 읽는다.
                               # **과거 RL 결과(M4 1~4회차)는 이 누수 아래에서 나왔다** — RL 은 종료 상태라 재평가하지 않는다.
    us_policy_rate_series: "US:RATE:FED_FUNDS"
```

---

## 규약

- `store.config("reward")` 는 섹션을 dict 로, `store.config("reward.w_free")` 는
  값 하나를 돌려준다. **저장은 평평하게** 한다 — 섹션째 한 행에 넣으면 값 하나를
  바꿔도 섹션 전체가 새 revision 이 되고, 무엇이 바뀌었는지 이력에서 읽을 수 없다
- 값 변경은 **커밋으로 기록**한다. 런타임 수정 금지
- 변경 시 `config_version` 을 올리고, 이벤트 로그와 리포트에 함께 남긴다.
  "이 성과가 어느 설정에서 나왔나"를 나중에 추적할 수 있어야 한다
- 학습 체크포인트에 config 스냅샷을 포함한다
- **대시보드는 이 값을 API로 받아 표시한다.** 프런트에 숫자를 적지 않는다

### 튜닝 금지 항목

`reward` · `accounting` · `benchmark` 섹션은 하이퍼파라미터가 아니다.
투자철학과 회계 규칙이므로 Optuna 탐색 공간에 넣지 않는다.
