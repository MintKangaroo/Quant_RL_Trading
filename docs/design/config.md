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
  kr_index: KOSPI_TR         # 총수익지수. 가격지수 쓰면 가짜 알파 발생
  us_index: SPX_TR
  base_value: 100.0

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

universe:
  min_turnover_20d_kr: 500_000_000   # 원
  min_turnover_20d_us: 1_000_000     # 달러
  min_listed_days: 180
  max_price_ratio: 0.15      # 1주 가격 / 자본
  exclude_flags: [관리종목, 거래정지, 정리매매]

execution:
  max_adv_ratio: 0.03        # 일 거래대금 대비 매수 상한
  max_liquidation_days: 3
  defer_minutes: 30          # 개장 후 신규매수 보류
  order_type: limit          # 시장가는 청산·킬스위치에만
  max_slippage: 0.005
  slice_count: 4
  slice_interval_sec: 60
  retry_after_sec: 300
  max_retries: 3

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

allocator:
  action_reflection_floor: 0.30   # 미만이면 경고 — RL이 아니라 룰 시스템
  episode_days: 250
  n_max_candidates: 30
  gamma: 0.997
  gae_lambda: 0.95

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

execution:                     # 체결 시뮬레이터
  impact_k: 0.1                # 충격비용 = k × 변동성 × √(주문량/ADV)
  min_order_value: 100000.0    # 이보다 작으면 수수료가 잡아먹는다
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
