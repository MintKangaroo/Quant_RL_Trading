# Hugging Face 재조사 — 랭커에 넣을 재료를 만드는 모델 (2026-10-13 밤)

> **조사 문서다(시행·사전등록 아님).** 모델을 받거나 돌리지 않았다. 수익·IC 는 하나도 재지 않았다.
> 근거는 HF 모델 카드·HF API(`/api/models`, robots `Allow: /`)·arXiv 초록/본문·저장소 문서·10/9 CPU 벤치 로그(`logs/bench-cpu-20261009-1011.log`)뿐이다.
> 사용자 방향(10/13): "데이터가 많아지면 신경망을 키우고, 지금은 LightGBM 랭커에 더 좋은 재료를 넣자" → 찾은 것은 **랭커 피처를 만드는 모델**이다. 랭커 대체 모델은 뒤로 뺐다.
> 조건 정정(사용자, 10/13 밤): 하루 추론은 **밤 01:00~07:30(약 6시간) 안**이면 된다. 메모리 12GB·GPU 없음은 그대로다. 그래서 큰 후보도 다시 넣었고, 시간은 '몇 시간' 으로 적었다. 과거 백필 시간은 따로 적었다.
>
> 이미 한 것(중복 추천 안 함): TTM r2(main·r2.1 가지), Chronos-Bolt, Chronos-2(단변량), Moirai 2.0, TimesFM 2.5, Qwen3-8B, finbert(영어), KR-SBERT, ko-sroberta·e5-small(TX·X), TabPFN v2/2.5(벤치만).
> 10/14 낮 벤치 예정: TTM-r3 · TiRex-2 · t0-beta. 제외됨: TimesFM 3.0(비상업) · KiT(가중치 미공개).

---

## 0. 먼저 알아 둘 것 — 시간 추정의 근거와 판정 규칙

**CPU 시간 추정의 근거**는 10/9 실측(10스레드, 2,800계열, 문맥 512) 하나다.
TTM 0.65초 · Moirai-small 12초 · Chronos-Bolt-small 23초 · Chronos-2(120M) 86초 · TimesFM-2.5(200M) 193초.
LLM(llama.cpp GGUF, 프롬프트 1,580 토큰): Qwen3-1.7B Q8 프롬프트 54 tok/s · 생성 14 tok/s(문서당 36초) / Qwen3-8B Q4 프롬프트 7.2 tok/s · 생성 3.5 tok/s(문서당 246초, RSS 7.0GB).
인코더(TX 실측, 12스레드): ko-sroberta 111M 512토큰 조각 2.9개/초 · e5-small 6.7개/초. Kronos(10/7 배선 확인, 4스레드, 표본 5개): small 계열당 약 2.8초 · base 약 10초.
아래 표에서 이 숫자에 없는 모델은 **크기 비례 추정**이고 "추정" 이라고 적었다. 실측 전에는 등록 근거로 쓰지 않는다.

**백필 규모(국장, 매일·전 종목)**: 2011-01~2026-06 약 3,800세션 × 평균 약 2,600종목 ≈ **990만 창**(TTM 굽기 2021-08~ 는 약 290만 창이었다).
시계열 모델의 백필 시간 = 990만 ÷ 2,800 × (2,800계열 하루 시간). 매주 1세션만 굽으면 1/5, 거래대금 상위 300만이면 약 1/9 이다.

**누수 판정 규칙**은 저장소 고정본을 그대로 따른다([model-cutoffs-2026-10.md](../protocols/model-cutoffs-2026-10.md) `6454eae26c53cd01` + [부록](../protocols/model-cutoffs-2026-10-addendum.md)).
- 개별 주가가 사전학습에 없는 시계열 모델 = **규칙 6**(종목 기억 누수 낮음, D₀ 없음, 전 구간 사용 + 공개일 뒤 구간을 따로 기록).
- 주가를 학습한 모델 = **학습 자료 끝 + 3개월(D₀)** 뒤만. 끝 날짜를 모르면 **공개일 + 3개월**.
- 연도별 판이 있는 모델 = ChronoGPT 규칙과 같게 **결정 연도 Y 에는 판 Y−1, 1~3월은 판 Y−2**.
- **합성 자료만으로 사전학습한 모델 = 시장 자료 누수 없음, 전 구간**(TabPFN 줄과 같은 판단).

판단 방식(10/13 사용자 설문): **"긴 과거(2011~2026)가 주인공, 금고는 확인"**. 그래서 이번 정리에서 가장 크게 본 것은 "2011~ 백테스트가 정직하게 가능한가" 다.
11/23 금고에 붙이려면 등록·해시 고정이 ~11/16 이고, 랭커 피처 시행(TR 류)은 10/22 고정이 기준이다.

---

## 1. 추천 상위 5개

| # | 무엇 | 만드는 재료 | 라이선스 | CPU — 하루 / 백필 | 누수 판정 | 검증 방법 한 줄 |
|---|---|---|---|---|---|---|
| **1** | **FinText 금융 사전학습 TSFM 연도별 판**(`FinText/TimesFM_20M_<YYYY>_Global` 주력, `Chronos_Small/Tiny_<YYYY>_Global` 대조). 94개국 일별 초과수익률(1990~)로 **처음부터** 사전학습했다. 연도마다 따로 학습한 판이 2000~2023 판 24개 있다. 한국이 학습 자료(Compustat Global)와 시험 시장에 들어 있다 | `fintext_pct` = 다음 1일(또는 5일) 예측 초과수익의 횡단면 백분위(하루 늦춤). TR 의 `ttm_pct` 와 같은 꼴이고 TTM 과의 차이도 피처로 쓸 수 있다 | Apache-2.0 | TimesFM-20M: 하루 **약 20초(추정)** / 2011~ 백필 **약 20시간(추정, 6시간 밤 3~4번)**. Chronos-Small(T5, 자기회귀·표본 20): 하루 5~10분(추정), 백필은 매주·상위 300 으로만 약 10시간 | **연도별 판이라 2001~ 전 구간이 정직하다.** 판 Y 는 Y 년 말까지의 자료로 학습했다(카드: "up to the year 2023"). 결정 연도 Y 에 판 Y−1(1~3월 판 Y−2). 2024~2026 은 2023 판 하나 — 학습 끝 뒤라 깨끗하다 | 먼저 10/9 하네스(창 A·B, 상위 300)로 제로샷 IC 를 TTM 과 나란히 잰다. 그다음 2011~2026 국면별 IC(연도 맞춤 판)를 재고, 통과하면 TR 꼴로 등록(C1 = 뺀 것) |
| **2** | **합성 자료로만 사전학습한 TSFM 둘** — `LG-AI-Research/EXAONE-Forecast-for-Finance-1.0`(202M, 금융 모양 합성 말뭉치: 두꺼운 꼬리·변동성 군집·점프·국면 전환·자산 간 의존) · `autogluon/chronos-2-synth`(Chronos-2 구조, 합성만) | `exf_pct` · `c2s_pct` = 5일 예측 수익 백분위. 분위수 21개라 **예측 하방 꼬리(q05/q50)** 도 피처 후보 | EXAONE: **EXAONE AI Model License 1.2-NC(비상업)** — 연구·shadow 만, 실자금 전 교체 필요. chronos-2-synth: **Apache-2.0** | EXAONE: 하루 3~5분(추정, TimesFM-2.5 급) / 백필 매주 30~50시간(추정). chronos-2-synth: 하루 약 1.5분(Chronos-2 실측 86초) / 백필 매일 약 85시간·매주 약 17시간 | **누수 없음(합성 전용).** 2011~ 전 구간. 기존 모델(규칙 6)과 달리 "실제 자료 섞임" 반론이 원천적으로 없다 | EXAONE 카드가 FinVerse 벤치에서 **횡단면 IC 1위**를 주장한다(자체 벤치 — 독립 검증 아님). 10/14 벤치에 끼워 같은 창에서 TTM·Bolt 와 대조. chronos-2-synth 대 Chronos-2(10/9 실측 IC +0.122)의 차이가 "실제 자료 효과"를 재는 누수 대조가 된다 |
| **3** | **IBM TSPulse**(`ibm-granite/granite-timeseries-tspulse-r1`, **1M 매개변수**, ICLR 2026). 예측기가 아니라 **이상 탐지·결측 보간·유사 구간 검색**용 사전학습 모델이다 | ① `tsp_anom` = 최근 OHLCV·거래대금 경로의 이상 점수(가지 `main`) — "무너질 종목" 재료(랭커 실력이 패자 가려내기라는 발견과 맞다). ② `tsp_analog5` = 임베딩이 가장 비슷한 **과거** 창 k개(다른 종목 포함, 결정일 이전만)의 실현 5일 수익 평균 | Apache-2.0 | 하루 **수 초**(TTM 급, 추정) / 2011~ 백필 이상 점수 **2~3시간(추정)**. 유사 검색은 색인 크기에 달림(종목×월 1창 색인이면 수 시간 추정) | 사전학습 자료에 Bitcoin 이 있지만 개별 주가는 없다 → **규칙 6, 전 구간**. 유사 검색은 색인을 결정일 이전으로 잘라야 한다(코드 단언 필요) | 이상 점수의 국면별 하위 10분위 수익(2011~)을 먼저 본다. 랭커 C0 하위 10% 와 겹침이 낮고 t≥3 이면 6차 꼴 묶음으로 등록 |
| **4** | **Chronos-2 공변량 판**(`amazon/chronos-2`, 120M — 10/9 엔 **단변량으로만** 쟀다). 과거 공변량으로 거래량·거래대금·외국인 순매수·공매도 비중 등을 같이 넣는다. 대조로 공변량 없는 같은 모델과 #2 의 합성판 | `c2cov_pct` = 공변량 넣은 5일 예측 백분위. **공변량 판 − 단변량 판**의 차이 자체가 "수급이 가격 예측을 바꾸는 정도" 라는 새 재료 | Apache-2.0 | 하루 약 1.5~5분(공변량 수에 비례, 추정) / 백필 매주 약 20~60시간(추정). 10/9 에 D-c 로 지웠으니 다시 받는다(약 0.5GB) | 규칙 6(카드: Chronos 자료 + GIFT-Eval pretrain + 합성 — 개별 주가 명시 없음) → 전 구간. 공변량은 우리 창고 자료라 observed_at 규칙만 지키면 된다 | 같은 하네스·같은 창에서 단변량 대비 IC 차이(짝 t). 효과가 없으면 닫는다 |
| **5** | **Kronos 재측정**(`NeoQuasar/Kronos-small` 25M · `Kronos-mini` 4M, MIT) — **OHLCV 캔들로 사전학습한 유일한 공개 금융 모델**. 10/9 진단에서 "미측정" 이었던 이유는 모델 불량이 아니라 **외장 D: 디스크 점검 대기**였다(`logs/tsfm-zeroshot-20261009-1546.log` 86~93행). 아직 한 번도 안 쟀다 | `kronos_pct` = 5일 예측 종가 백분위 · 예측 고저 폭(Parkinson) — 거래량을 입력으로 받는다 | MIT | small: 하루 약 **1~2시간**(4스레드 2.8초/계열 × 2,600, 10스레드면 줄어듦 추정) · 표본 1개로 줄이면 약 25분(추정). mini 는 그 몇 분의 1(추정). base: 하루 3~7시간 — 경계 | **KRX(XKRX) 일봉을 학습했다**(논문: 자료 2024-06 까지). 공개 가중치가 논문 판인지 카드에 없어 보수 D₀ **2025-09-30**. → 백테스트는 **2025-10~ 와 금고·전방만**. 긴 과거 주인공 규칙에는 못 낀다 | SSD 로 옮겨 10/14 벤치 창 A(2026-02~06, D₀ 뒤)에서 small·mini 를 TTM 과 대조. 좋아도 등록은 전방 shadow 만. 참고: KiT 논문(arXiv 2609.34507)은 Kronos-base 횡단면 IC 0.009·RankIC 0.015 로 약하다고 보고 |

**한 줄 요약**: 1·2·3은 **2011~ 를 정직하게 돌 수 있는** 재료라서 "긴 과거 주인공" 규칙에 바로 들어간다. 1번(FinText)은 금융으로 사전학습했는데도 연도별 판이 있어서 누수 문제가 없다. 이번 조사에서 가장 큰 발견이다.
4는 이미 가진 모델의 안 쓴 기능이다. 5는 전방 전용이다.
한국어 텍스트 쪽에서는 **CPU 시간 조건이 풀려도 TX(ko-sroberta)를 바꿀 근거가 생기지 않았다.** 큰 한국어 인코더는 전부 2024년 이후 공개라 긴 과거에서 누수가 걸린다(§3).

**10/14 벤치에 더할 것(한 줄)**: `FinText/TimesFM_20M_2023_Global`·`FinText/Chronos_Small_2023_Global`(창 A·B 모두 학습 끝 뒤) + `autogluon/chronos-2-synth` + `LG-AI-Research/EXAONE-Forecast-for-Finance-1.0`(NC, 연구용) + Kronos-small·mini(D: 에서 SSD 로) — 전부 같은 창·같은 하네스, 금고 7/1 이후 금지. 여유가 있으면 FlowState r1.1(일봉 scale 0.0656)·TempoPFN(합성 전용)도 넣는다.

---

## 2. 후보 표 — 시계열(범주 1·4)

CPU '하루' = 국장 약 2,600종목 1회 추론. '백필' = 2011-01~2026-06 매일 전 종목(약 990만 창) 기준, 다르면 적었다.

| 모델 | 범주 | HF id | 라이선스 | 크기 | CPU 하루 / 백필 | 사전학습 자료 끝·누수 | 백테스트 가능 구간 | 재료 아이디어 | 우선순위 |
|---|---|---|---|---|---|---|---|---|---|
| FinText TimesFM-20M(연도별, Global) | 금융 TSFM | `FinText/TimesFM_20M_<2000..2023>_Global` | Apache-2.0 | 20M(79MB) | 약 20초(추정) / 약 20시간(추정) | 판 Y = 1990~Y 년 말 일별 초과수익, 94개국(CRSP + Compustat Global), 한국 포함. 확장 창이라 판마다 끝이 분명하다. 연도 안 정확한 끝 날짜는 미표기 → 1~3월 판 Y−2 | **2001~ 전 구간**(연도 맞춤 판) | 1·5일 예측 초과수익 백분위, TTM 과의 차이 | **1** |
| FinText Chronos-Small/Mini/Tiny(연도별) | 금융 TSFM | `FinText/Chronos_{Small,Mini,Tiny}_<YYYY>_Global` | Apache-2.0 | 46M / 20M / 8M | Small 5~10분(추정, T5 자기회귀·표본) / 매주·상위 300 약 10시간 | 같다 | 2001~ | TimesFM 판의 대조, 분위 폭 | 1(대조) |
| FinText ··· _US · _Augmented | 금융 TSFM | `FinText/*_<YYYY>_US` · `*_Augmented` | Apache-2.0 | 같다 | 같다 | US = CRSP 만. Augmented = 합성 증강판(구성 미확인) | 2001~ | **미장**: 범용 TSFM 은 미장 IC ≤0 이었다 → US 판으로 미장 재측정 | 2 |
| EXAONE Forecast for Finance 1.0 | 금융 TSFM(합성) | `LG-AI-Research/EXAONE-Forecast-for-Finance-1.0` | **EXAONE 1.2-NC**(추론 코드는 BSD-3-LG, 가중치는 NC) | 202M, 문맥 512, 분위 21개, 어텐션 없음(CNN) | 3~5분(추정) / 매주 30~50시간(추정) | **합성 금융 말뭉치만**(카드·초록). 2026-09-03 공개 | 전 구간 — 단 NC 라 실자금 전 교체 | 5일 예측 백분위, 하방 꼬리 | **2** |
| Chronos-2-Synth | TSFM(합성) | `autogluon/chronos-2-synth` | Apache-2.0 | ≈120M(Chronos-2 와 같은 구조 — 크기 미표기) | 약 1.5분 / 약 85시간(매주 약 17시간) | **합성만**(카드) | 전 구간 | Chronos-2 와 짝 → 누수 대조 | **2** |
| TSPulse r1 | 이상 탐지·임베딩 | `ibm-granite/granite-timeseries-tspulse-r1`(가지: main=이상탐지 · dualhead=보간·검색 · block=분류) | Apache-2.0 | 1.08M | 수 초(추정) / 2~3시간(추정) | 전기·날씨·교통·**Bitcoin** 등 — 개별 주가 없음 → 규칙 6 | 전 구간 | 이상 점수, 아날로그 5일 수익, 결측 보간 | **3** |
| Chronos-2(공변량) | TSFM | `amazon/chronos-2` | Apache-2.0 | 120M | 1.5~5분(추정) / 매주 20~60시간 | 규칙 6(10/9 표) | 전 구간 | 거래량·수급 공변량 판, 공변량 효과 차이 | **4** |
| Kronos-small · mini | 금융 캔들 TSFM | `NeoQuasar/Kronos-small` · `Kronos-mini` | MIT | 25M · 4M(+토크나이저 4M) | small 1~2시간(4스레드 실측 기반) / 2025-10~ 만 의미 — 금고 30세션 약 30~60시간, 상위 300 이면 4~7시간 | **KRX·NASDAQ 일봉 포함**, 논문 2024-06 까지, HF 공개 2025-06-30(마지막 수정 2025-09-09) | **2025-10~(보수)** · 논문 기준이면 2024-10~ | 캔들 예측 백분위, 예측 범위 | **5** |
| Kronos-base | 금융 캔들 TSFM | `NeoQuasar/Kronos-base` | MIT | 102M | 하루 3~7시간(추정) — 밤 창 경계 | 같다 | 같다 | — | 6(small 이 나으면 그때) |
| FlowState r1.1 | TSFM(SSM) | `ibm-granite/granite-timeseries-flowstate-r1`(revision `r1.1`) | Apache-2.0(연구판 `ibm-research/flowstate` 는 따로) | 9M(r1.0)·18.5M(r1.1) | 수십 초(추정) / 약 10시간(추정) | GIFT-Eval pretrain + Chronos 자료 일부 + CauKer 합성 → 규칙 6. 일봉은 scale 0.0656(주기 없을 때) | 전 구간 | 5일 예측 백분위(작은 판의 다양성) | 6 |
| TempoPFN | TSFM(합성) | `AutoML-org/TempoPFN` | Apache-2.0 | 38M(선형 RNN) | 30~60초(추정) / 30~60시간(추정) | **합성만**(카드) | 전 구간 | #2 의 셋째 합성 대조 | 6 |
| PatchTST-FM-r2 | TSFM | `ibm-granite/granite-timeseries-patchtst-fm-r2` | OpenMDW-1.0 또는 Apache-2.0(이중) | 385M, 문맥 8,192 | 5~10분(추정) / 매주 약 80시간(추정) | GIFT-Eval pretrain 일부 + KernelSynth·TSMixup·CauKer 합성 → 규칙 6 | 전 구간 | 큰 범용 판 하나 — 범용 TSFM 이 이미 넷이라 기대 낮음 | 7 |
| Toto 2.0(4m·22m·313m) | TSFM(관측성) | `Datadog/Toto-2.0-22m` 등 | Apache-2.0 | 4M~313M | 22m 은 1분 안(추정) | 관측성 지표 + 공개 자료(학습 자료 세부는 카드에서 미확인) | 미확인 → 규칙 6 가정 금지 | — | 8 |
| FinCast | 금융 TSFM | `Vincent05R/FinCast`(v1.pth 4.0GB) | Apache-2.0 | 약 1B(파일 크기 추정) | 15~30분(추정) | "20B financial time points"(주식·FX·선물·암호화폐), **끝 날짜 미확인**. 공개 2025-08-20 | D₀ ≈ 2025-11-20(공개일 규칙) → 금고·전방만 | — | 8(끝 날짜가 확인되면 재검토) |

---

## 3. 후보 표 — 한국어 텍스트 → 재료(범주 2)

공시량: 수시공시(TX 대상) 2021-08~2025-03 약 4.9만 건, 평균 2.2조각(512토큰). L1 대상(유상증자·사채·자사주 등)은 달 400~520건(약 20건/일), 사채 원문 평균 약 1만 자.

| 모델 | 범주 | HF id | 라이선스 | 크기 | CPU 하루 / 백필 | 사전학습 자료 끝·누수 | 백테스트 가능 구간 | 재료 아이디어 | 우선순위 |
|---|---|---|---|---|---|---|---|---|---|
| KURE-v1 | 한국어 임베딩 | `nlpai-lab/KURE-v1`(bge-m3 미세조정) | MIT | 568M, 1024차원, 8,192토큰 | 약 0.5조각/초(ko-sroberta 실측 × 크기비, 추정) → 하루 수시공시 약 200조각 **약 7분** / 4.9만 건 **약 60시간(6시간 밤 10번)** | bge-m3(2024-01 공개) + 한국어 쌍(공개 2024-12) | D₀ 2025-03 → 판정 창 대부분 ✕ | TX 의 대형 인코더 변형(긴 문서 8k — 앞 4조각 제한이 풀림) | 9 — 금고·전방 변형으로만 |
| KoE5 | 한국어 임베딩 | `nlpai-lab/KoE5`(multilingual-e5-large 미세조정) | MIT | 560M, 512토큰 | KURE 와 비슷(추정) | e5-large(≤2023-06) + ko-triplet(공개 2024-09) | D₀ 2024-12 | 같다 | 9 |
| A.X Encoder base | 한국어 인코더(ModernBERT) | `skt/A.X-Encoder-base` | Apache-2.0 | 149M, 16,384토큰 | 약 2조각/초(추정) / 약 15시간(추정) | 자체 말뭉치, 끝 날짜 미확인, 공개 2025-07-26 | D₀ 2025-10 | 문장 임베딩이 아니라 분류용 — 미세조정 필요 | 10 |
| KF-DeBERTa | 한국어 **금융** 언어모델 | `kakaobank/kf-deberta-base` | MIT | base(약 0.2B 추정) | 수 시간(추정) | 범용 + 금융 말뭉치(카카오뱅크·에프엔가이드), **수집 기간 미표기**, 공개 2023-12-13 | D₀ 2024-03 | 마스크 LM 이라 그대로는 재료가 안 된다 — 미세조정 머리 필요(TX 리지와 겹침) | 10 |
| KorFinASC-XLM-RoBERTa | 한국어 금융 **대상별 감성** | `amphora/KorFinASC-XLM-RoBERTa`(arXiv 2301.03136) | Apache-2.0 | XLM-R large(약 560M) | 뉴스 제목 기준 수 분/일(추정) | KorFin-ASC 등 금융 뉴스 감성 자료, 공개 2023-01 | D₀ 2023-04 | 한국어 **뉴스 제목**이 있어야 쓴다 — 지금 창고엔 한국어 뉴스 원천이 없다(미확인) | 11(뉴스 원천이 생기면) |
| KR-FinBert-SC | 한국어 금융 감성 | `snunlp/KR-FinBert-SC` | **카드에 라이선스 없음** | base | 수 분 | 뉴스 72곳·증권사 리포트 16곳, 공개 2022-03 | D₀ 2022-06 | 같다 | 11 |
| Qwen3.5-2B · 4B | 작은 다국어 LLM(추출) | `Qwen/Qwen3.5-2B` · `-4B` | Apache-2.0 | 2.3B · 4.7B | 4B Q4: 문서당 2~4분(추정) → 하루 L1 대상 20건 **약 1시간** / **백필 2021-08~ 약 2.7만 건 ≈ 1,000~1,800시간 — 불가** | 공개 2026-02, 끝 날짜 미표기 | 사실 추출은 원문 안 값만 꺼내므로 누수 경로가 좁다. 그래도 고정본 규칙 4 로는 D₀ 2026-05 | Haiku L1 의 **로컬 대체**(월 $20 → 0) — 단 과거 백필은 CPU 로 불가 | 12 |
| NuExtract3 | 구조화 추출 특화 LLM | `numind/NuExtract3`(Qwen3.5-4B 기반) | Apache-2.0 | 4.5B | Qwen3.5-4B 와 같다 | 같다 | 같다 | JSON 틀 → 값. 공급계약·리픽싱 표 추출 | 12 |
| kanana-1.5-2.1b-instruct | 한국어 LLM | `kakaocorp/kanana-1.5-2.1b-instruct-2505` | Apache-2.0 | 2.3B | 문서당 약 1분(Qwen3-1.7B 실측 기반 추정) | 공개 2025-05 | D₀ 2025-08 | 위와 같은 쓰임, 한국어 품질은 미확인 | 12 |

**텍스트 쪽 결론**:
1. **임베딩은 TX(ko-sroberta, 말뭉치 2020 이전)를 유지한다.** 시간 조건이 풀려 KURE-v1·KoE5 백필(약 60시간)은 이제 가능하다. 그래도 둘 다 2024년 이후 공개라 판정 창(2023-02~)에서 누수 반론을 못 지운다. 쓰려면 "TX 대형 인코더 변형" 으로 **금고·전방에서만** 잰다.
2. **정형 사건 추출은 모델보다 DART DS005(주요사항보고서 구조화 API)가 먼저다.** 자사주·CB/BW 리픽싱·유상증자를 2015~ 정형 필드로 준다(정찰 문서 [source-scouting-2026-10.md](source-scouting-2026-10.md) #5·B2). L1 범위 대부분이 여기 들어가고 결정론적이라 누수가 없다.
   로컬 LLM 은 과거 백필이 CPU 로 1,000시간이 넘어 **긴 과거 검증에 못 쓴다.** 남는 쓰임은 Haiku L1 의 매일 비용 대체(전방)뿐이다.
   공급계약(G13)·손익구조(G12)는 이미 정규식 파서가 있다.

---

## 4. 후보 표 — 표 형식 파운데이션 모델(범주 3)

TabPFN v2 실측(10/9): 문맥 3,000행 · 12피처로 200행 예측 43초 → 2,600종목 하루 **약 9분**. 매일 백필(3,800세션)은 **약 590시간이라 불가**, 월 1회면 약 28시간.

| 모델 | HF id | 라이선스 | 크기 | 사전학습 자료 | 쓸 만한 자리 | 우선순위 |
|---|---|---|---|---|---|---|
| TabICLv2 | `jingang/TabICL`(원본) · `general-forecast/tabicl-v2-regressor`(같은 가중치 변환) | BSD-3 | 미표기 | v1 논문은 합성 사전학습. v2 의 사전학습 자료는 **미확인** | 미장 fundamental 결측 보간(합성 상위 24 가 전부 fundamental 결측이던 문제) — 다만 GBM 은 결측을 그대로 다룬다. 보간이 새 정보를 만들지는 않는다 | 13 |
| TabPFN v2 | `Prior-Labs/TabPFN-v2-reg` | Prior Labs License 1.1(Apache-2.0 + 표기) | — | 합성 | 같다. 이미 받아 둠 | 13 |
| Mitra | `autogluon/mitra-regressor` | Apache-2.0 | 76M | 합성 사전분포 혼합(카드 기준, 세부 미확인) | 같다 | 14 |
| LimiX-2 | `stable-ai/LimiX-2` | **StableAI 비상업 v1.0** | 12.5M~406M 계열 | 합성 | — | 닫음 |

**결론**: 표 모델은 **재료를 새로 만들지 못한다.** 같은 입력으로 또 하나의 예측기를 만드는 것이다. 사용자 방향("지금 모델에 더 좋은 재료")과 맞지 않아 우선순위를 낮게 뒀다.

---

## 5. 닫는 후보

| 모델 | 이유 |
|---|---|
| `google/timesfm-3.0-pytorch` | 비상업(이미 제외) |
| KiT(arXiv 2609.34507) | 가중치 미공개(이미 제외). 논문만 참고 |
| `ibm-research/ttm-r3` | CC BY-NC-SA. 상업판 `ibm-granite/granite-timeseries-ttm-r3` 가 10/14 벤치에 있다 |
| `pfnet/timesfm-1.0-200m-fin` | CC BY-NC-SA 4.0 + 금융 미세조정(누수 끝 날짜 미확인) |
| `Salesforce/moirai-*`(추가 판) | CC BY-NC(이미 쟀다) |
| `NX-AI/TiRex`(1.x) | NXAI 공동체 라이선스(연매출 €1억 넘으면 별도 조건). TiRex-2(Apache)가 10/14 벤치에 있어 1.x 는 필요 없다 |
| `MahmoodLab/KRONOS2` | 이름만 같은 병리 영상 모델(CC BY-NC-ND) |
| `lc2004/kronos_*_BTCUSDT_*`, `Shadowell/Kairos-*-crypto` 등 | 개인 미세조정본, 암호화폐, 근거 없음 |
| `mldi-lab/Kairos_*`, `Maple728/TimeMoE-50M`, `thuml/sundial-base-128m`, Lag-Llama | 범용 TSFM 이 이미 다섯이다(TTM·Bolt·Moirai·TimesFM·Chronos-2). 추가 범용 판은 서로 순위상관이 높아(TTM 변형 간 0.45~0.83) 새 재료가 아니다. 합성 판(#2)과 금융 판(#1)이 낫다 |
| `AutonLab/MOMENT-1-*` | MIT. Time-series Pile 의 금융 포함 여부 **미확인**. 임베딩 차원이 커서 X 의 교훈(고차원 임베딩 → GBM 실패)에 걸린다. TSPulse 가 같은 일을 1M 으로 한다 |
| `jinaai/jina-embeddings-v3` | CC BY-NC |
| `google/embeddinggemma-300m` | Gemma 라이선스, 2025-07 공개 → D₀ 2025-10 |
| `Qwen/Qwen3-Embedding-0.6B` | Apache 지만 2025-06 공개 → D₀ 2025-09. 금고·전방 변형으로는 KURE 와 겹친다 |
| KRX 금융 LLM 대회 모델들(`*krx*qwen*` GGUF 다수) | 라이선스 미표기가 많고, 금융 문답 학습이라 누수 경로가 넓다 |
| 영어 금융 LLM(Finance-Llama·FinanceGemma·Fin-R1 등) · `FinLang/finance-embeddings-investopedia` | 영어 전용이거나 NC. 국장 공시에 맞지 않는다 |
| `naver-hyperclovax/HyperCLOVAX-SEED-*` · `kakaocorp/kanana-2-*` | 라이선스 'other' — 상업 조건 **미확인**. Apache 대안(kanana-1.5, Qwen3.5)이 있다 |
| `LGAI-EXAONE/EXAONE-4.x` | NC(10/5 표에 이미 있음) |
| `stable-ai/LimiX-2` · `Prior-Labs/tabpfn_2_5` · `Real-TabPFN-2.5` | 비상업, 또는 실제 자료 미표기(이미 제외) |
| 로컬 LLM 과거 백필(어떤 모델이든) | CPU 로 2.7만 건 × 수 분 = 1,000시간 이상. DS005 가 대신한다 |

---

## 6. 검증 순서 제안(실행 아님)

1. **10/14 낮 벤치에 끼우기**(같은 창 A·B, 같은 하네스, 금고 금지): FinText TimesFM-20M·Chronos-Small 2023 Global · chronos-2-synth · EXAONE-Forecast(NC 연구용) · Kronos-small·mini. 모두 받기만 하면 되고, 크기는 합쳐 2GB 안팎이다(EXAONE 202M fp32 약 0.8GB, Kronos 합계 약 0.1GB, FinText 둘 약 0.3GB, chronos-2-synth 약 0.5GB — 추정).
   새 의존성: `exaone-forecast` 패키지(코드는 BSD-3-LG)와 Kronos 저장소 `model/`(이미 D: 에 있음 → SSD 로 복사)이다. `.venv-bench` 는 밤 00:15 신호가 쓰므로 **장 중에 건드리지 않는다**(TTM-r3 때와 같은 이유).
2. FinText 가 TTM 과 비슷하거나 낫고 순위상관이 낮으면 → **2011~2026 연도 맞춤 굽기**(TimesFM-20M, 약 20시간 = 밤 3~4번). 국면별 IC·D1 을 엄격 기준(t≥3)으로 본 뒤 TR 꼴 등록 초안을 쓴다. 11/23 금고에 붙이려면 ~11/16 고정이다.
3. TSPulse 이상 점수는 굽기가 싸다(2~3시간). 2번과 같은 밤에 붙여 원리 시험(하위 10분위·NW t)부터 본다.
4. 합성 판(#2)은 "같은 구조에서 실제 자료를 뺐더니 IC 가 어떻게 되나" 를 재는 **누수 대조**로도 쓴다. 기존 TF·IX-T·TR 의 누수 반론에 답이 된다.

## 출처

- HF 모델 카드·API(2026-10-13 조회): 위 표의 각 id. 라이선스·생성일·마지막 수정일·매개변수 수는 `huggingface.co/api/models/<id>` 의 `tags`·`createdAt`·`lastModified`·`safetensors.total` 이다.
- FinText: Rahimikia·Ni·Wang, *Re(Visiting) Time Series Foundation Models in Finance*, arXiv 2511.18578(본문 HTML v1 — 자료 CRSP + Compustat Global 94개국, 1990~ 확장 창, 연도별 판 2000~2022 로 2001~2023 표본 밖 평가, 문맥 5·21·252·512, 다음날 초과수익 목표, 미국 결과: 금융 사전학습 Chronos-small 연 36.8%·샤프 5.42 대 CatBoost 47.3%·6.46 — 비용 언급 없음, 한국 개별 결과는 본문에서 못 찾음). 코드 github.com/DeepIntoStreams/TSFM_Finance.
- EXAONE Forecast for Finance: 모델 카드 + arXiv 2609.04239(초록: 합성 금융 말뭉치 사전학습, FinVerse 벤치 — 한국 포함 여부·평가 기간 미확인).
- Chronos-2: arXiv 2510.15821 · `autogluon/chronos-2-synth` 카드("only been trained on synthetic").
- TSPulse: arXiv 2505.13033(ICLR 2026) · 카드 Training Data 절. FlowState: arXiv 2508.05287. TempoPFN: arXiv 2510.25502. TabICL: arXiv 2502.05564 · v2 2602.11139. LimiX: arXiv 2509.03505.
- Kronos: arXiv 2508.02739(저장소 부록 표에 이미 있음) · KiT: arXiv 2609.34507(Kronos-base IC 0.0090/RankIC 0.0148 — 그 논문의 자체 보고).
- KorFinASC: arXiv 2301.03136.
- 저장소: `docs/diag/tsfm-zeroshot.md` · `docs/diag/tsfm-ttm-daily-2026-10-13.md` · `docs/protocols/tx-filing-text-2026-10.md` · `docs/protocols/tsfm-ranker-and-finetune-2026-10.md` · `docs/protocols/llm-filing-events-2026-10.md` · `docs/protocols/model-cutoffs-2026-10*.md` · `logs/bench-cpu-20261009-1011.log` · `logs/tsfm-zeroshot-20261009-1546.log`.
