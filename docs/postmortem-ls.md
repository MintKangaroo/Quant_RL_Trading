# LS_KR / LS_USA 부검

선행 프로젝트 두 개가 "강화학습 트레이딩 시스템"으로 출발해 사실상 룰 기반으로 귀결된 과정을,
코드와 설정에서 나온 근거만으로 재구성한 기록이다.

- 대상
  - `LS_KR` = `/home/mintkangaroo/Project/Invest_KOREA_Stock_Project/ls_kr_rl_trader` (247 파일 / 34,451줄)
  - `LS_USA` = `/home/mintkangaroo/Project/Invest_USA_Stock_Project/ls_us_rl_trader` (206 파일 / 27,753줄)
- 인용 경로는 각 레포 루트 기준 상대경로다.
- 직접 읽은 라인에서 나온 사실만 단정한다. 추론은 `[추측]` 로 표시한다.
- 조사 방법에 관한 단서: subagent 3개를 띄웠으나 세션 한도로 즉시 실패해, 아래 내용은
  grep 으로 경로를 좁힌 뒤 직접 읽어 작성했다. 그래서 커버리지는 **액션 경로·보상·상태·설정에 집중**돼 있고,
  I/O 계층(6번)은 파일 단위 인벤토리 수준이다. 6번은 4단계(Collector) 착수 전 보강이 필요하다.

---

## 요약 — 한 문장

두 프로젝트 모두 RL 액션이 실제 주문에 반영된 비율은 **0%** 이며,
그 사실이 코드가 아니라 **설정 파일 한 줄**로 결정돼 있었다.

| | LS_KR | LS_USA |
|---|---|---|
| 액션 반영률 | **0%** (`ensemble.weights.ppo: 0.0`) | **0%** (`ensemble.mode: equal_weight`) |
| 0이 된 시점 | 2026-08-02 감사 (0.40 → 0.10 → 0.0) | 2026-06 정직 OOS 이후 |
| 직접 원인 | action↔종목 매핑 파손 + 출력이 state에 무반응 | obs 차원 불일치 (42 vs 128) → 매 리밸런싱 실패 |
| 보상 | 조밀하나 MDD 벌점이 **선형** | 동일 (`0.1 * vol` 하드코딩) |
| 체결 되먹임 | 없음 (현금비율 1개만) | 없음 |
| 종목축 인코딩 | flatten (8종목 × 4~5피처 + 12) | flatten (차원 불일치로 파손) |

---

## 1. 액션 반영률 — 가장 유력한 원인 (확정)

### 1-1. 설계 단계에서 이미 RL을 배제하고 있었다

RL 래퍼의 docstring 이 직접 그렇게 말한다.

`agent/ppo_agent.py:5-8` (LS_KR)
```
- act() 는 raw action 만 반환. 실제 주문은 worker.tick() 의 가드 체인을 통과해야 한다.

⚠️ 안전 원칙
- PPO 결과를 직접 주문으로 변환하지 않는다.
```

`controller/ensemble_allocator.py:3-6` (LS_KR / LS_USA 동일 문구)
```
PPO 단독 사용 금지. 다음 전략을 가중합해 종목별 target weight 를 만든다::

    final_w = w_ppo * ppo + w_mom * momentum + w_mr * mean_reversion +
              w_rp * risk_parity + w_def * defensive
```

즉 RL은 처음부터 **의사결정자가 아니라 6개 신호 중 하나의 가중치**로 설계됐다.
안전장치가 나중에 RL을 덮어쓴 게 아니라, 구조 자체가 RL에 지분만 준 것이다.

### 1-2. 그 지분이 0으로 수렴했다

`config/settings.yaml:331-345` (LS_KR) — 최종 가중치
```yaml
    ppo: 0.0
    flow: 0.0
    momentum: 0.09
    mean_reversion: 0.0
    risk_parity: 0.47
    defensive: 0.25
```

`ppo: 0.0` — `ensemble_allocator.combine()` 의 가중합(`ensemble_allocator.py:73-80`)에서
PPO 항이 0으로 곱해지므로 **PPO 출력은 계산은 되지만 결과에 전혀 기여하지 않는다.**
`worker.py:446` 에서 `ctx.raw_action = self.agent.act(...)` 로 매 틱 추론은 계속 돈다. 완전한 dead output 이다.

가중치가 0이 된 경위가 같은 파일 주석에 날짜별로 남아 있다 — 이게 이 부검에서 가장 값진 자료다.

`config/settings.yaml:302-330` (LS_KR)
```
# 2026-06-07: 공시인지 PPO walk-forward OOS 69.3>룰53.7 → 0.40 상향했으나,
# 2026-06-09: 라이브에서 PPO가 학습 유니버스(quality-8)와 다른 동적 유니버스를 만나
#   퇴화 신호 [1,-1,-1...] 로 매수를 막음(현금 96%). → PPO 0.40→0.10 으로 낮추고
# 2026-08-02 감사 — ppo 0.10 → 0.0 (제거).
#   ① 매핑 파손 — ensemble_allocator._ppo_signal 은 action[i] 를 sorted(유니버스)[i] 에
#      붙인다. 유니버스는 390분마다 교체되므로 슬롯 의미가 매 리프레시마다 달라진다.
#   ② 길이 불일치 — action 은 8칸인데 유니버스는 코어 포함 최대 10종목 → 정렬 뒤쪽
#      2종목은 PPO 입력을 아예 못 받는다(코드순 임의 절단).
#   ③ 출력이 state 에 반응하지 않음 — 실제 스케일 state 200개에서 슬롯별
#      평균 [0.05,+0.43,-0.16,-0.23,-0.11,-0.03,+0.11,+0.20],
#      표준편차 [0.03,0.16,0.07,0.08,0.04,0.02,0.04,0.07]. 평균이 표준편차의 3~5배 =
#      사실상 슬롯별 고정 편향.
#   ①+③ 결합 = **종목코드 정렬 순서에 고정된 틸트**.
```

**핵심**: ③ 의 실측치(평균이 표준편차의 3~5배)는 정책이 state 를 거의 무시하고
슬롯별 상수를 출력했다는 뜻이다. 학습이 안 된 게 아니라 **학습된 것이 상수함수**였다.
그리고 ① 때문에 그 상수가 매번 다른 종목에 붙었다 — 노이즈보다 나쁜, 종목코드 순서에 대한 체계적 편향.

매핑 코드 자체:

`controller/ensemble_allocator.py:135-146` (LS_KR)
```python
# ndarray / list — state(StateBuilder) 가 sorted(quotes)[:N] 순서로 피처를 만들므로
# PPO action[i] 도 sorted(symbols)[i] 에 매핑해야 종목-가중치 정렬이 일치한다.
...
for i, s in enumerate(sorted(symbols)):
    if i >= arr.size:
        break                     # ← 유니버스가 action 보다 길면 뒤쪽은 조용히 버려진다
    out[s] = float(arr[i])
```

`break` 로 인한 무음 절단이 위 ②의 실체다. 예외도 경고도 없다.

### 1-3. 미장은 다른 경로로 같은 결과에 도달했다

LS_USA 는 가중치가 아니라 **모드 스위치**로 RL을 껐다.

`config/settings.yaml:220-238` (LS_USA)
```yaml
ensemble:
  enabled: true
  # ※ 정직 OOS(2026-06): 가격·라이브·감성 RL 3실험 모두 equal_weight 로 수렴(초과알파 없음).
  #   + 라이브 PPO 모델은 obs 차원 불일치(42 vs 128)로 매 리밸런싱 실패→equal_weight 폴백.
  mode: equal_weight
  weights:
    ppo: 0.30                          # PPO 메인 의사결정자 (중장기 일단위)
```

`ppo: 0.30` 과 "PPO 메인 의사결정자" 라는 주석이 남아 있지만 **이 값은 죽어 있다.**
`controller/ensemble_allocator.py:49-51, 121` (LS_USA) 에서 분기가 먼저 갈린다.
```python
if mode == "rl":
    ...
if mode in ("equal_weight", "inverse_vol"):
    return self._passive(symbols, state_builder, "equal_weight")   # weights 를 아예 보지 않음
```
`mode: equal_weight` 이므로 `_passive()` 로 즉시 빠져나가고, `weights.ppo` 는 참조조차 되지 않는다.

> **Lattice 가 물려받지 말아야 할 것**: 설정 파일에 살아 있는 것처럼 보이는 죽은 값.
> `ppo: 0.30` 과 "메인 의사결정자" 주석을 읽은 사람은 이 시스템이 RL로 돈다고 믿게 된다.
> 이것이 "RL 프로젝트라고 믿었지만 룰이었다"의 기계적 실체다.

### 1-4. 반영률을 재는 계측기가 없었다

두 레포 모두 **"RL 액션이 몇 % 집행됐는가"를 계산·기록하는 코드가 없다.**
그래서 ppo 가중치가 0.40 → 0.10 → 0.0 으로 내려가는 동안에도 시스템은 계속
"RL 트레이더"로 불렸다. 발견은 2026-08-02 사람이 직접 감사해서야 이뤄졌다.

**→ CLAUDE.md 의 "액션 반영률 30% 미만 경고" 지표가 정확히 이 공백을 겨냥한다.**
Lattice 에서는 이 값이 대시보드 상시 표시 항목이어야 하며, 0이 되면 즉시 눈에 띄어야 한다.

### 1-5. 가드 체인 — RL 이 살아 있었더라도 통과할 게 많았다

`worker.py:444-533` (LS_KR) 의 순서. 각 단계가 앞 단계 출력을 좁힌다.

| 단계 | 위치 | 동작 |
|---|---|---|
| 5. PPO 추론 | `worker.py:446` | 실패 시 `raw_action = None` (`:447-449`) |
| 6. 앙상블 | `worker.py:452` | PPO를 6신호 중 하나로 가중합 (가중치 0) |
| 7. 메타 컨트롤러 | `worker.py:462` | regime·이벤트·서킷으로 재조정 |
| 8. 리스크 | `worker.py:473` | `blocked_symbols` 종목 제외 (`:481-486`) |
| 8-c. DQ | `worker.py:490-505` | 데이터품질 결함 종목 매수 제외 |
| 9. 킬스위치 | `worker.py:508-510` | 발동 시 전량 차단 |
| 9. 유동성 | `worker.py:513-525` | 미달 종목 제외, 전부 미달이면 차단 |
| 9. 드리프트 | `worker.py:527-529` | `reduce_exposure()` 로 비중 일괄 축소 |
| 10. 사람 승인 | `worker.py:536-539` | live + 대규모면 승인 대기 |

추가로 `ensemble_allocator.py:106` 에서 `abs(tw) < 0.005` 인 종목은 조용히 탈락한다.

이 체인 자체는 합리적이다 — 문제는 **RL이 이 체인을 통과한 뒤 무엇이 남는지 아무도 측정하지 않았다**는 것.

---

## 2. 보상 함수 — 조밀하지만 형태가 틀렸다 (확정)

**조밀하다.** 에피소드 끝 정산이 아니라 매 스텝 계산된다 (`env/reward.py:69` `compute()`,
`env/trading_env.py:161-166` 에서 매 step `info` 구성).

문제는 **MDD 벌점의 형태**다.

`env/reward.py:75-80` (LS_KR)
```python
reward = (
    self.absolute_pnl_weight * pnl
    - self.mdd_penalty * dd          # ← dd = 현재 낙폭 수준(level), 증분이 아니다
    - self.turnover_penalty * turnover
    - self.vol_penalty * vol
)
```
`env/trading_env.py:161-162`
```python
peak = max(self._equity_history + [equity_after])
drawdown = (peak - equity_after) / peak if peak > 0 else 0.0
```

`dd` 는 고점 대비 현재 낙폭의 **절대 수준**이고, 여기에 상수를 곱해 매 스텝 뺀다.
즉 `docs/design/reward-and-risk.md` 가 명시적으로 금지한 **선형 페널티 `−λ·MDD`** 다.
LS_USA 도 동일하며 vol 계수가 하드코딩돼 있다 — `env/reward.py:50` (LS_USA):
```python
reward = pnl - self.mdd_penalty * dd - self.turnover_penalty * turnover - 0.1 * vol
```

### 결과: 현금 편향으로 붕괴

이 형태가 무엇을 만들었는지 레포 자신이 실측으로 기록해 두었다.

`env/reward.py:54-63` (LS_KR)
```
# 변동성 벌점 — 하드코딩 0.1 이었으나 설정 가능하게(2026-08-03).
#   mdd 벌점과 같은 이유로 현금 편향을 만든다: 현금이면 vol=0 이라 벌점 0.
# 절대 pnl 항 가중(2026-08-03). 컨벡시티 학습에서는 0 으로 끈다.
#   이유: 절대 pnl 이 남아 있으면 **하락 구간에서 현금이 항상 이긴다**.
#   실측(고정 노출 스윕): 벌점을 다 제거해도 누적보상이
#     노출 0.01 → +147 / 0.50 → -49 / 0.99 → -94 로 현금이 압도적이었다.
```

`env/reward.py:87-92`
```
#   1차 식(절대 pnl 기준)은 현금으로 붕괴했다:
#     상승장 +1.0*pnl / 하락장 -2.0*손실  →  노출 0 이면 양쪽 다 보상 0 인데
#     하락 벌점이 2배라 '참여의 기대보상'이 음수 → 아무것도 안 하기가 최적.
#     (실측: 참여율 상방 0.000 / 하방 -0.002, MDD 2.1%, cum +0.32% = 사실상 현금)
```

**보상 설계가 "아무것도 하지 않기"를 최적해로 만들었다.** 정책은 그걸 정확히 학습했다.

`env/reward.py:41-44` 에는 학습 실패의 자기진단도 있다.
```
#   왜: 2026-08-02 감사에서 이 시스템에 **검증된 알파가 없다**는 것이 확인됐다.
#   그런데 기존 보상은 benchmark_relative(=초과수익=알파)를 겨냥했다.
#   없는 것을 보상하니 배울 게 없었고, 메타-배분 RL 학습 로그의
#   `explained_variance ≈ 0` 이 정확히 그 증상이다
```

> **Lattice 대조**: `reward-and-risk.md` 의 `w(d)·Δd` 는 이 실패의 직접적 해독제다.
> ① 신저점 갱신 시에만 벌점(증분) → 이미 난 낙폭을 매 스텝 재차 벌하지 않는다.
> ② 12% 자유구간 → 정상 영업 범위의 낙폭에 벌점 0이라 현금 편향이 생기지 않는다.
> ③ 벤치 상대 → "없는 알파" 대신 상대 성과를 겨냥.
> 다만 ③은 LS 의 실패가 재현될 수 있는 지점이기도 하다. **알파가 없으면 벤치 상대 보상도 배울 게 없다.**
> 그래서 M2 의 IC 0.03 게이트가 M4 RL 보다 먼저 와야 한다는 순서가 옳다.

---

## 3. 체결 되먹임 — 전혀 없다 (확정)

state 에 들어가는 계좌 정보는 **현금비율 스칼라 하나뿐**이다.

`env/state_builder.py:151-157` (LS_KR)
```python
# cash ratio
if account:
    eq = float(account.get("equity_krw", 0)) or 1.0
    cash = float(account.get("cash_krw", 0))
    features.append(min(1.0, max(0.0, cash / eq)))
else:
    features.append(1.0)
```

`build()` 전체(`env/state_builder.py:71-159`)를 읽었을 때 state 구성은:

| 블록 | 라인 | 내용 |
|---|---|---|
| 종목별 피처 | `:92-118` | ret_1, ret_5, vol_5 (+ 공시점수, 수급) — **전부 가격/외부 데이터** |
| 매크로 z-score | `:132-139` | VKOSPI, USDKRW, KR_RATE |
| 벤치마크 변화율 | `:141-144` | KOSPI, KOSDAQ, KRX300 |
| regime one-hot | `:146-149` | 5종 |
| 현금비율 | `:151-157` | 1개 |

**종목별 보유 비중이 state 에 없다.** 목표 비중도, 실현 비중도 없다.
정책은 자기가 현재 무엇을 얼마나 들고 있는지 모르는 채로 매 스텝 목표 비중을 출력했다.

이건 CLAUDE.md 불변식 7번이 말하는 것보다 더 나쁜 상태다. 불변식 7번은
"목표 비중이 아니라 실현 비중을 넣어라"인데, LS는 **둘 다 안 넣었다.**

결과적으로 정책 입장에서 환경은 사실상 무상태(stateless)였다 —
행동이 다음 관측에 영향을 주지 않으니 순차적 의사결정 문제가 성립하지 않는다.
`explained_variance ≈ 0` 은 이것만으로도 설명된다. [추측] 이지만 근거는 강하다:
가치함수가 예측할 대상인 미래 보상이 현재 관측과 인과적으로 연결돼 있지 않다.

참고로 LS_USA 에는 `broker/fill_reconciler.py` (139줄) 가 있어 체결 대사 자체는 존재한다.
다만 그 결과가 state 로 흘러가지는 않는다.

---

## 4. 종목 축 인코딩 — flatten (확정)

**flatten 이다. set encoder 아니다.**

`config/settings.yaml:290` (LS_KR)
```yaml
  include_disclosure: true  # 종목당 공시점수 1피처 추가 (dim = 8*4+12 = 44)
```

고정 슬롯 8개 × 종목당 4피처 + 매크로/벤치/regime/현금 12개 = 44차원 벡터.
`env/state_builder.py:87-118` 이 그대로 구현한다. 슬롯 배정 방식:

`env/state_builder.py:88-89`
```python
# 고정차원: 현재 universe(snapshot quotes) 상위 N개를 코드 정렬해 안정적 순서로.
syms = sorted((snapshot.get("quotes") or {}).keys())[: self.symbols_n]
```

주석은 "안정적 순서"라고 주장하지만, **종목코드 정렬은 유니버스가 바뀌면 안정적이지 않다.**
유니버스에 새 종목이 들어오면 그 뒤 슬롯이 전부 한 칸씩 밀린다.
1-2 절의 감사 기록 ①("유니버스는 390분마다 교체되므로 슬롯 의미가 매 리프레시마다 달라진다")이
바로 이 라인의 귀결이다.

문제 정리:

1. **슬롯 = 종목코드 순위**. 의미론적으로 아무것도 아니다. 슬롯 3번은 어제와 오늘 다른 회사다.
2. **순열 불변성 없음**. 같은 포트폴리오라도 종목코드 순서가 바뀌면 다른 state 가 된다.
3. **크기 불일치를 조용히 자른다**. state 는 8칸 고정인데 실제 유니버스는 최대 10종목
   (`ensemble_allocator.py:143-144` 의 `break`).
4. **차원 불일치가 런타임에 터졌다**. LS_USA 는 obs 42 vs 모델 128 로 매 리밸런싱이 실패했다
   (`config/settings.yaml:227`). 그런데도 시스템은 멈추지 않고 equal_weight 로 폴백해 계속 돌았다.

> **Lattice 대조**: `milestones.md` M4 의 "상태 인코더(set encoder)" 가 1·2번을 직접 해결한다.
> 추가로 4번이 주는 교훈 — **차원 불일치는 조용한 폴백이 아니라 실패여야 한다.**
> 폴백이 있었기 때문에 두 달 동안 아무도 RL이 죽은 걸 몰랐다.

---

## 5. Look-ahead 누수 — 학습 파이프라인은 의외로 깨끗, 위험은 다른 데 있다

찾아본 전형적 누수 패턴은 **대부분 발견되지 않았다.**

| 패턴 | 결과 |
|---|---|
| 전체 구간 scaler `.fit()` | 미발견 |
| `train_test_split` / `shuffle=True` | 미발견 |
| `center=True` rolling | 미발견 |
| `.shift()` 오용 | 미발견 — 오히려 올바르게 쓰였다 |

올바른 사용례 — `env/etf_exposure_env.py:132-135` (LS_KR)
```python
self._vol20 = self._core.rolling(20).std().shift(1).fillna(0.0)
self._mom20 = self._core.rolling(20).mean().shift(1).fillna(0.0)
```
`rolling(20)` 뒤에 `.shift(1)` 을 붙여 당일 바를 배제했다. `scripts/train_meta_allocator.py:161`
(`df.pct_change(fill_method=None).shift(1)`) 도 동일하게 처리돼 있다.

정규화도 전 구간 통계가 아니라 롤링 윈도 안에서만 계산된다 — `env/state_builder.py:136`
```python
z = (arr[-1] - arr.mean()) / (arr.std() + 1e-9)   # arr = 최근 history_len 개 deque
```

### 다만 세 가지가 남는다

**(a) purge / embargo 가 사실상 없다.** 전체 레포에서 `embargo|purge` 를 포함한 파일은
`scripts/selection_evolve_run.py` 하나뿐이다. walk-forward 검증은 여러 곳에서 하지만
(`backtest/walk_forward.py`, 16-fold 언급이 `settings.yaml:300, 309` 에 있음)
fold 경계에서 라벨 구간이 겹치는 것을 막는 장치는 확인되지 않았다.

**(b) 생존편향.** 유니버스가 `sorted(snapshot["quotes"].keys())` — 즉 **현재 조회되는 종목**에서
나온다 (`env/state_builder.py:89`). 과거 시점의 유니버스를 그 시점 기준으로 복원하는 구조가 아니다.
상장폐지 종목이 과거 백테스트 유니버스에 들어갈 방법이 없다. [추측] 이지만 구조상 불가피하다 —
`store.get(as_of=)` 같은 시점 조회 계층 자체가 없기 때문이다.

**(c) `observed_at` 개념이 없다.** 공시 점수는 `disc.score(code, ts)` (`env/state_builder.py:107`)
로 조회되는데 `ts` 는 스냅샷 시각이다. 공시의 **공표 시각**과 **내가 알 수 있었던 시각**을
구분하는 필드가 스키마에 없으므로, 백필 데이터에 대해서는 공시를 실제보다 일찍 본다.
검증할 수 없었다 — `data/schema.py` 를 읽지 않았다. **4단계 착수 전 확인 필요.**

> **Lattice 대조**: (b)와 (c)는 불변식 1·3번(이중시간 + `store.get(as_of=)`)이 겨냥하는 바로 그 구멍이다.
> LS 의 누수는 코딩 실수가 아니라 **시점 조회 계층의 부재**에서 왔다. 개별 코드를 조심해서 막을 수 있는 게 아니다.

---

## 6. 재사용 가치가 있는 I/O 계층

> 출처: 1차 작성은 파일 단위 인벤토리였고, 이후 별도 조사 패스에서 모듈 내부까지 채웠다.
> 아래 표시된 항목 중 **킬스위치 영속화 / US `cancel_order` 부재 / 레이트리밋 상수**는
> 직접 코드로 재확인했다. 나머지는 2차 패스의 라인 인용을 신뢰하되 이식 시점에 재검증할 것.
>
> **오염 여부는 전 항목 "없음"으로 조사됐다** — `broker/`, `risk/`, `data/`, `news/` 어느 모듈도
> `env/`(RL 환경)나 `agent/` 를 import 하지 않는다. 배관과 두뇌가 실제로 분리돼 있어 이식이 가능하다.

### LS_KR

| 모듈 | 줄 | 용도 |
|---|---|---|
| `broker/ls_client.py` | 566 | LS API 클라이언트 — 인증·토큰·요청 래퍼 |
| `broker/order.py` | 715 | 주문 전송/취소/정정 |
| `broker/account.py` | 512 | 잔고·포지션 조회 |
| `broker/market_data.py` | 405 | 시세·호가·수급 조회 |
| `broker/ls_websocket.py` | 269 | 실시간 스트림 |
| `broker/settlement_nav.py` | 108 | 결제 기준 NAV |
| `broker/live_sync.py` | 101 | 브로커–로컬 동기화 |
| `risk/risk_manager.py` | 281 | 사전 리스크 체크 |
| `risk/kill_switch.py` | 134 | 킬스위치 |
| `risk/position_sizer.py` | 40 | 사이징 |
| `liquidity/liquidity_guard.py` | 192 | 유동성 필터 |
| `liquidity/slippage_estimator.py` | 26 | 슬리피지 추정 |
| `scheduling/market_hours.py` | — | 장 운영시간 |
| `config/krx_holidays.txt` | 27 | **휴장일 하드코딩 텍스트 파일** |
| `data/collector.py`, `ecos_collector.py`, `storage.py`, `schema.py`, `data_quality_guard.py` | — | 수집·저장·DQ |

### LS_USA

| 모듈 | 줄 | 비고 |
|---|---|---|
| `broker/ls_client.py` | 439 | KR 판(566줄)보다 작다 — 분기 여부 미확인 |
| `broker/order.py` | 331 | |
| `broker/account.py` | 334 | |
| `broker/market_data.py` | 276 | |
| `broker/fill_reconciler.py` | 139 | **KR 에 없다.** 체결 대사 — Lattice 불변식 7번(실현 비중)에 직접 유용 |
| `broker/ls_websocket.py` | 93 | |
| `scheduling/market_hours.py`, `market_hooks.py` | — | 미장 시간·서머타임 [추측] |

### 즉시 확인된 주의사항

**시크릿은 누출되지 않았다.** `config/secrets.env` 는 `.gitignore:2-3` 에서
`config/secrets.env*` + `!config/secrets.env.example` 로 차단돼 있고,
`git ls-files` 결과 추적되는 건 `.example` 뿐이다.

Lattice `.env.example` 에 필요한 키 이름 (LS_KR `config/secrets.env` 의 키 목록, 값 제외):
```
LS_APPKEY, LS_APPSECRET, LS_REST_BASE_URL, LS_WS_BASE_URL,
LS_ACCOUNT_NO, LS_ACCOUNT_PRODUCT_CODE,
OPENDART_API_KEY, NEWS_API_KEY, ECOS_API_KEY,
DATABASE_URL, FLASK_SECRET_KEY, DASHBOARD_HOST, DASHBOARD_PORT,
ANTHROPIC_API_KEY,
SMTP_SERVER, SMTP_SENDER_EMAIL, SMTP_SENDER_PASSWORD, RECIPIENT_EMAIL
```

**`.gitignore` 에서 배운 함정** — `.gitignore:18-21` (LS_KR)
```
# ⚠️ `env/` 패턴 금지 — 이 프로젝트의 env/ 는 **RL 환경 패키지**다
#    (state_builder / trading_env / meta_allocation_env / reward).
#    저장소에서 빠져 있었다(2026-08-03 발견). 가상환경은 .venv/ 만 쓴다.
```
`env/` 를 gitignore 했다가 RL 환경 패키지 전체가 두 달간 버전관리에서 빠져 있었다.
Lattice 는 `.venv/` 만 제외한다.

**휴장일이 27줄짜리 텍스트 파일이다.** `config/krx_holidays.txt` — 수동 관리이며
갱신되지 않으면 조용히 틀린다. Lattice 에서는 이식하되 이중시간 저장으로 옮기고,
만료 감시를 두는 것이 낫다.

### 6-1. 인증 · 토큰 — 이식 난이도 낮음, 최우선

표준 OAuth `client_credentials` 다. `broker/ls_client.py:137-186` (KR) / `:110-148` (US).
토큰은 `expires_in - 60` 여유로 캐시한다 (KR `:183`).

**주의할 자산이 하나 있다** — KR과 US가 **같은 appkey 를 공유해서** 한쪽이 토큰을 재발급하면
다른 쪽 토큰이 서버에서 무효화된다(`IGW00121`). 그 감지 → 강제 재발급 → 재시도 로직이
KR `ls_client.py:231-296` 에 있고, 주석(`:232-236`)에 경위가 남아 있다.

> Lattice 는 **KR/US 별도 appkey 를 발급**해서 이 문제를 원천 제거하는 게 낫다.
> US `ls_client.py:32` 주석도 같은 권고를 한다.

자격증명은 `config/loader.py:85-114` 에서 `settings.yaml`(구조) + `secrets.env`(민감정보) 병합.
`secrets.env` 가 없어도 paper 모드는 동작한다 — 안전한 설계이며 이식 가치가 있다.

### 6-2. HTTP · 재시도 · 성공판정 — 진짜 자산은 예외코드 지식

| | KR | US |
|---|---|---|
| 레이트리밋 | `MIN_INTERVAL_SEC = 3.1` (`ls_client.py:55`) | `1.05` (`ls_client.py:57`) |
| TR 호출 | `_request_tr()` `:199-309` | `_call_tr()` `:160-228` |
| 성공 판정 | `("00000","00039","00040","00463")` + `rsp_msg` 에 "완료되었습니다" 포함 (`:283-287`) | `rsp_cd.startswith("0")` (`:222`) |
| 에러율 집계 | 롤링 윈도 (`:110-126`) | 누적 카운터 (`:230-235`) |

**KR 의 성공코드 리스트가 이 레포에서 가장 값진 코드일 수 있다.** `00039`/`00040`/`00463` 은
문서가 아니라 **실거래 사고로 발견된 코드**다. 이식할 때 이 리스트를 잃으면 같은 사고를 다시 겪는다.

반대로 US 의 `startswith("0")` 는 지나치게 포괄적이라 실패를 성공으로 읽을 수 있다.
**통일한다면 KR 쪽 판정을 기준으로 삼아야 한다.**

### 6-3. 주문 — 두 레포가 서로 다른 중복방지 전략을 쓴다

- **KR**: 2-phase 상태기계. `submitting` → `submitted`/`unconfirmed` (`order.py:609-710`).
  제출 **전** 선기록이라 프로세스가 죽거나 타임아웃이 나도 재제출이 차단된다 (`:622-634` 주석).
  중복방지는 종목 집합 기준 (`store.submitted_symbols()` `:162-168`).
- **US**: 멱등성 키 방식. `client_ref` = decision_id 기반 (`order.py:321-328`) +
  `has_recent_client_ref()` 30분 창 (`:109-122`).

**⚠️ 기능 격차**: US `ls_client.py` 에는 `place_order` 만 있고 `cancel_order`/`modify_order` 가
**아예 없다** (직접 확인: KR 은 `:487`, `:536`, `:557` 에 셋 다 존재. US 는 `:328` 에 `place_order` 뿐).
미장은 주문 취소·정정 기능이 미구현 상태로 운영됐다.

> 이식 방향: **KR 의 2-phase 상태기계 + US 의 client_ref 멱등성을 합친다.**
> 이중체결은 실거래에서 가장 위험한 실패모드이므로 어느 한쪽만 가져가면 안 된다.

### 6-4. 킬스위치 — 어느 쪽도 그대로 가져가면 안 된다 (직접 확인)

| | KR (`risk/kill_switch.py`) | US (`risk/kill_switch.py`) |
|---|---|---|
| 영속화 | **없음 (인메모리)** | `storage/.kill_switch_state.json`, atomic write (`:61-79`) |
| 프로세스 간 동기화 | 없음 | mtime 감지 재로드 (`:74-79`) |
| hard/soft 구분 | **있음** (`:34-35, 49-63`) — hard=latch·수동해제, soft=자동해제(90s 최소유지), soft→hard 승격 | **없음** — 전부 latch, 수동 reset 만 |
| 일일손실 임계 | 0.03 | 0.05 |
| 데이터갭 | 120s | 300s |

US 파일 상단 주석(`:12-18`)이 결정적이다 — **"이전엔 인메모리라 재시작시 자동해제되는 버그가 있었다"**.
US 는 그 버그를 고쳤고, **KR 은 고치지 않은 채로 남아 있다.**
같은 코드베이스에서 갈라진 두 레포 사이에 버그 수정이 전파되지 않은 것이다.

> 이식 방향: **KR 의 hard/soft 이원화 + US 의 파일 영속화**를 합쳐 새로 쓴다.
> 원본 어느 쪽도 완전하지 않다.

같은 격차가 `risk/risk_manager.py` 에도 있다 — KR 은 쿨다운·일일카운터를
`storage/.risk_order_state.json` 에 영속화하지만 (`:214-252`), US 는 하지 않는다.
US 는 프로세스를 재시작하면 당일 주문 카운터가 초기화된다.

### 6-5. 장 운영시간 — US 방식이 낫다

- **KR**: 정규장 09:00–15:30 (`scheduling/market_hours.py:23-24`), 휴장일은
  **수작업 텍스트 파일** 27줄, 2026년만 커버.
- **US**: 09:30–16:00 ET (`:24-25`), 휴장일은 **`pandas_market_calendars`(XNYS) 조회** (`:48-68`).
  DST 는 `pytz` 의 `America/New_York` 이 자동 처리.

> Lattice 는 US 패턴(라이브러리 조회)을 기본으로 하고, KR 은 `exchange_calendars` 의 `XKRX`
> 로 대체 검토할 가치가 있다. 수작업 리스트는 갱신을 잊으면 조용히 틀린다.

### 6-6. 응답 정규화 — KR 은 검증됨, US 는 미검증

- **KR** `account.py:292-377` — t0424 필드맵이 주석에 문서화돼 있다
  (`sunamt`=추정순자산, `janqty`=잔고수량 등, `:301-321`). 이식 가치 높음.
- **US** `account.py:236-279` — 자체 주석(`:239`)에 **"⚠️ 필드명은 LS 해외주식 TR 스펙에 맞춰
  검증 필요"** 라고 적혀 있다. 다중 후보키로 방어 파싱하는 것은 곧 필드명을 모른다는 뜻이다.

> US 계좌 정규화는 **포팅하지 말고 LS 공식 스펙 기준으로 재작성**하고 스키마 테스트를 붙인다.

### 6-7. 데이터 수집 — 소스 목록

| 데이터 | KR | US |
|---|---|---|
| 가격/차트 | LS API t1102/t8410/t8407 | LS API g3104/g3204 |
| 환율·거시 | 한국은행 ECOS (`data/ecos_collector.py:27`) | FRED (`data/fred_collector.py:26`) |
| 공시 | OpenDART (`news/dart_collector.py:27`) | SEC EDGAR (`news/edgar_collector.py:21-22`, 무료·User-Agent만) |
| 뉴스 | newsapi → DART → 네이버금융 폴백체인 | newsapi |
| 수급 | `MarketSnapshot.investor_flow` (수집 경로 미확인) | Fintel (공매도·기관) |
| 기타 | — | CEO SNS (X API), COT |

ECOS/FRED 시리즈코드 매핑 딕셔너리(`ecos_collector.py:31-37`, `fred_collector.py:29-40`)는
하드코딩이지만 **도메인 지식이라 이식 가치가 있다.**

뉴스·SNS 수집기는 성격이 다르다 — 네이버 HTML 스크래핑, X API, Nitter 의존은
브로커 배관과 달리 외부 변경에 취약하다. **필요한 소스만 선별해 재구축**하는 편이 낫다.

### 6-8. 이식 우선순위

1. **LSClient 코어** — OAuth·TR호출·레이트리밋·토큰충돌 복구. 실거래로 검증된 예외코드 지식 포함.
2. **주문 2-phase 상태기계** — KR 상태기계 + US 멱등성 키를 통합.
3. **킬스위치** — KR hard/soft + US 파일영속화를 합쳐 새로 작성.
4. **RiskManager 영속화 패턴** — KR 것을 기준으로.
5. **시장시간 라이브러리 조회** — US 패턴을 KR 에도 적용.

**배관이어도 새로 짤 것**: US 계좌 필드 매핑(미검증), 뉴스·SNS 수집기(외부 의존 취약),
KR 킬스위치 원형(고쳐지지 않은 버그 보유).

---

## Lattice 로 가져갈 결론

| # | LS 에서 실제로 일어난 일 | Lattice 의 대응 | 상태 |
|---|---|---|---|
| 1 | RL 기여도 0%를 아무도 측정하지 않아 두 달간 몰랐다 | 액션 반영률 상시 표시, 30% 미만 경고 | CLAUDE.md 에 명시됨 |
| 2 | 설정에 살아 있는 척하는 죽은 값 (`ppo: 0.30` + mode 스위치) | 임계치는 `store.config` 단일 출처 | 불변식 10 |
| 3 | 선형 MDD 벌점 → 현금 편향, "아무것도 안 하기"가 최적해 | `w(d)·Δd` 증분 벌점 + 12% 자유구간 | reward-and-risk.md |
| 4 | state 에 보유 비중이 아예 없어 순차 결정 문제가 성립 안 함 | 실현 비중 되먹임 | 불변식 7 |
| 5 | 종목축 flatten + 코드정렬 슬롯 → 종목코드에 고정된 틸트 | set encoder | M4 산출물 |
| 6 | 차원 불일치가 조용한 폴백으로 흡수돼 실패가 은폐됨 | — | **미대응. 아래 참조** |
| 7 | 시점 조회 계층 부재 → 생존편향·공시 시점 누수 | `store.get(as_of=)` + 이중시간 | 불변식 1·3 |
| 8 | 중단 기준이 없어 9차까지 재정식화를 끌고 감 | M4 3회 실패 시 M3 복귀 | milestones.md |

### 문서에 아직 없는 것 — 6번

LS 의 실패에서 가장 반복적으로 나타난 메커니즘은 **조용한 폴백**이다.

- `ppo_agent.py:49-51` — 모델 로드 실패 → `logger.warning` 후 `model = None`, 계속 진행
- `ppo_agent.py:68-71` — `model is None` → zeros 반환("safe fallback"), 호출자는 구분 못 함
- `worker.py:447-449` — `act()` 예외 → `raw_action = None`, 계속 진행
- `ensemble_allocator.py:143-144` — 길이 불일치 → `break`, 조용히 절단
- LS_USA — obs 차원 불일치 → equal_weight 폴백, 매 리밸런싱 실패하면서도 계속 운영

전부 "안전하게" 설계됐고, 전부 **실패를 성공처럼 보이게 만들었다.**
개별적으로는 합리적이지만 합치면 시스템이 죽었는데도 계속 도는 상태가 된다.

제안: Lattice 에 **"조용한 폴백 금지"** 규칙을 추가할 것.
모델 미로딩·차원 불일치·매핑 실패는 경고가 아니라 **Session 실패**여야 하고,
`degraded` 상태가 대시보드에 표시돼야 한다. 이것을 CLAUDE.md 불변식이나
`docs/design/agents.md` 에 넣을지는 결정이 필요하다.

---

## 미검증 항목 (다음 단계에서 확인)

1. ~~I/O 모듈별 오염 여부~~ — **해결.** `broker/`·`risk/`·`data/`·`news/` 어느 모듈도 `env/`·`agent/` 를
   import 하지 않는다. 배관과 두뇌가 실제로 분리돼 있다.
2. ~~LS_KR / LS_USA `ls_client.py` 차이~~ — **해결.** 6-2·6-3 참조. 핵심 차이는 성공판정 로직,
   레이트리밋 상수, US 의 취소·정정 TR 부재.
3. **`data/schema.py`** — 저장 스키마에 시각 필드가 어떻게 들어 있는지. 5(c)(`observed_at` 부재) 근거 보강.
4. **LS_USA worker 가드 체인** — KR 과 동일한지, 미장 특수사항(서머타임, PDT)이 어디 있는지.
5. **`backtest/walk_forward.py`** — fold 분할에 purge 가 실제로 없는지 확인.
6. **KR 수급(`investor_flow`) 수집 경로** — 어느 소스에서 오는지. `settings.yaml:307` 은
   "네이버 대체소스, KRX 차단 우회"라고 적고 있어 안정성 확인이 필요하다.
