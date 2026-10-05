# 고정 — 모델 지식 컷오프 표: 어느 모델을 어느 판정 창에 쓸 수 있나 (2026-10-05)

> **고정 문서.** 알파 탐색 프로그램 Phase 0([alpha-research-program.md](../design/alpha-research-program.md) §4.3·§5.2).
> 이 파일의 sha256 앞 16자를 프로그램 문서에 적는다. 고정한 뒤에는 고치지 않는다. 고쳐야 하면 새 판(`-v2`)을 만든다.
> 근거는 2026-10-05 에 huggingface.co 모델 카드와 공개 API(`/api/models/<id>` 의 `createdAt`)에서 읽었다. robots 는 `Allow: /` 다.
> 이 표는 **Phase 2 의 LLM·텍스트·시계열 모델 등록 전부의 전제**다. 등록 문서는 모델을 고를 때 이 표의 행을 인용한다. 표에 없는 모델은 새 판에 넣은 뒤에만 쓴다.

## 왜

LLM 은 학습 시점까지의 세상을 안다. 컷오프가 판정 창 안에 있는 모델로 과거를 채점하면, 모델이 이미 아는 결과를 맞히는 것을 실력으로 읽게 된다
(Glasserman·Lin 2023 arXiv 2309.17322 · Gao·Jiang·Yan 2025 arXiv 2512.23847 · Look-Ahead-Bench arXiv 2601.13770).
그래서 모델마다 **"이 날짜 이후의 결정에만 쓸 수 있다"** 를 등록 전에 못 박는다. 결과를 본 뒤에 고르면 그 선택 자체가 누수다.

## 규칙

1. **컷오프 C** 는 다음 순서로 정한다.
   ① 카드에 날짜가 적혀 있으면 그 날짜다. ② 학습 자료가 날짜로 잘려 있으면(연도별 판) 그 날짜다.
   ③ 둘 다 없으면 **공개일(HF `createdAt` 또는 카드의 공개 표기)** 을 상한으로 쓴다. 공개 전 자료로 학습했을 테니 실제 컷오프는 그 앞이다.
   이 경우는 보수적이다. 판정 창 쪽으로 보면 컷오프를 늦게 잡는 셈이다.
2. **사용 가능 첫 결정일 D₀ = C + 3개월 여유.** 카드의 날짜는 대략이고, 웹 수집본에는 날짜가 늦게 붙은 문서가 섞인다.
3. 판정 창은 셋이다.
   - **W-main** 2023-02-15 ~ 2026-06-30: 회차 kit 의 첫 채점 세션부터.
   - **W-fa2021** 2021-11-10 ~ 2026-06-30: 확장 패널. 첫 채점은 학습창 뒤라 2022 중이다.
   - **W-vault** 2026-07-01 ~: 금고와 전방.
4. 어떤 창에 쓰든 **그 창의 결정 세션 중 D₀ 앞의 것은 채점하지 않는다**(잘라 낸다). 자르고 남은 구간이 60세션 미만이면 그 창에는 쓰지 않는다.
5. **임베딩·분류 인코더**(수익을 내지 않는 모델)도 같은 규칙이다. 사전학습 말뭉치에 사후 뉴스가 있으면 같은 문맥의 벡터가 미래를 품을 수 있다.
6. **시계열 파운데이션 모델**은 개별 주가를 학습 자료에 명시하지 않았으면 "종목 기억 누수 낮음" 으로 분류한다. 이 경우 규칙 2·4 를 적용하지 않는다.
   단 공개 시계열 묶음에 금융 계열이 섞였을 가능성은 카드로 배제할 수 없다. 그래서 등록 때 **판정 창 안팎 성능을 나란히 적는다**(누수 탐지 기록, 기준 아님).

## 표

| 모델(HF id) | 근거 문구(카드·API) | C | 근거 종류 | D₀ | W-fa2021 | W-main | W-vault/전방 |
|---|---|---|---|---|---|---|---|
| `jhgan/ko-sroberta-multitask` | KLUE-RoBERTa + KorNLI/KorSTS(2020). 가중치 2022-03 이후 형식 변환뿐(TX 초안 실측) | 2020-12 이전 | ② 말뭉치 | 2021-03 | ○ | ○ | ○ |
| `snunlp/KR-FinBert-SC` | "corporate related economic news articles from 72 media sources", "analyst reports from 16 securities companies". 공개 2022-03-02 | ≤ 2022-03-02 | ③ 공개일 | 2022-06-02 | 2022-06 뒤만 ○ | ○ | ○ |
| `snunlp/KR-SBERT-V40K-klueNLI-augSTS` | KLUE NLI 기반, 공개 2022-05(TX 초안) | ≤ 2022-05 | ③ | 2022-08 | 2022-08 뒤만 ○ | ○ | ○ |
| `EleutherAI/polyglot-ko-5.8b` · `-12.8b` | "863 GB of Korean language data" — 블로그·뉴스 87.0GB 등. 수집 날짜 미표기. 공개 2022-09-22 / 2022-10-14 | ≤ 2022-09-22 / ≤ 2022-10-14 | ③ | 2022-12-22 / 2023-01-14 | 2022-12 뒤만 ○ | ○ | ○ |
| `intfloat/multilingual-e5-small` | 공개 2023-06-30. 대조학습 자료는 웹 크롤 쌍(TX 초안) | ≤ 2023-06-30 | ③ | 2023-09-30 | ✕(자르면 앞 7개월 손실 — 규칙 4 로 남는 구간만) | 2023-10 뒤만 | ○ |
| `manelalab/chrono-gpt-instruct-v1-<YYYY>1231` · `chrono-bert-v1-<YYYY>1231` | "available before a fixed knowledge-cutoff date τ". 지시 조정 자료도 "pre-2000 content" 만. 영어 | 각 판 YYYY-12-31 | ② 말뭉치 | 각 판 다음 해 04-01 | **연도별 판**(결정 연도 Y 에는 판 Y−1 — 단 1~3월은 판 Y−2) | 같다 | 최신 판(2024) 2025-04 뒤 ○ |
| `meta-llama/Llama-3.1-8B-Instruct` | "Knowledge Cutoff: December 2023" | 2023-12-31 | ① 카드 | **2024-04-01** | 2024-04 뒤만 | 2024-04 뒤만(약 2.2년) | ○ |
| `Qwen/Qwen3-1.7B` · `-4B` · `-8B` | 컷오프 미표기. 공개 2025-04-27 | ≤ 2025-04-27 | ③ | 2025-07-27 | ✕(남는 구간 < 1년 — 급등장 하나뿐) | ✕ | ○ |
| `LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct` | 미표기. 공개 2024-12-01 | ≤ 2024-12-01 | ③ | 2025-03-01 | ✕ | ✕(급등장만 남는다) | ○ (NC — 연구·shadow 만) |
| `LGAI-EXAONE/EXAONE-4.0-1.2B` · `-32B` | 미표기. 공개 2025-07-11 | ≤ 2025-07-11 | ③ | 2025-10-11 | ✕ | ✕ | ○ (NC — 연구·shadow 만) |
| `SUFE-AIFLM-Lab/Fin-R1` | 미표기. 공개 2025-03-17 | ≤ 2025-03-17 | ③ | 2025-06-17 | ✕ | ✕ | ○(쓰지 않기로 함) |
| Claude(Haiku 4.5 · Sonnet 5.5 · Opus 5.5) | 최신 상용 모델 | 2025 이후 | ③ | — | ✕ | ✕ | **전방만** |
| `ibm-granite/granite-timeseries-ttm-r2` | 학습 자료: 전기·날씨·교통·**Bitcoin** 등. 개별 주가 명시 없음 | 해당 없음 | 규칙 6 | — | ○(안팎 기록) | ○(안팎 기록) | ○ |
| `amazon/chronos-bolt-small` · `amazon/chronos-2` | "trained on nearly 100 billion time series observations" / Chronos Datasets 일부 + GIFT-Eval Pretrain 일부 + 합성. 개별 주가 명시 없음 | 해당 없음 | 규칙 6 | — | ○(안팎 기록) | ○(안팎 기록) | ○ |
| `google/timesfm-2.5-200m-pytorch` | "GiftEvalPretrain", "Wikimedia Pageviews, cutoff Nov 2023", "Google Trends top queries, cutoff EoY 2022", 합성 | 해당 없음 | 규칙 6 | — | ○(안팎 기록) | ○(안팎 기록) | ○ |
| `Salesforce/moirai-2.0-R-small` | GIFT-Eval pretrain/train 일부, Chronos 자료 mixup, KernelSynth, 사내 운영 자료 | 해당 없음 | 규칙 6 | — | ○(안팎 기록) | ○(안팎 기록) | ○ (NC — 연구·shadow 만) |
| `Prior-Labs/TabPFN-v2-*` · `tabpfn_2_5` | "trained purely on synthetic tabular tasks"(2.5). v2 도 합성 사전학습 | 해당 없음(시장 자료 없음) | 합성 | — | ○ | ○ | ○ (2.5 는 NC) |
| `Prior-Labs/Real-TabPFN-2.5` | "continued pre-training on real-world datasets" — 무엇인지 미표기 | 미상 | — | — | **쓰지 않는다** | 쓰지 않는다 | 쓰지 않는다 |

○ 는 쓸 수 있다는 뜻이다. "X 뒤만" 은 규칙 4 로 그 앞 결정을 자른다는 뜻이다. ✕ 는 그 창에 쓰지 않는다는 뜻이다.
NC 표시는 사용자 결정(2026-10-05)을 따른다. 연구·모의계좌·shadow 에서는 쓰고, 실자금·회사 전에 상업 라이선스를 확보하거나 모델을 교체한다(교체하면 재검증).

## 등록 문서가 지킬 것

- 모델 ID 와 **리비전 해시**(HF 커밋)를 적는다. 양자화 파일(GGUF)이면 파일명과 sha256 도 적는다. 리비전이 바뀌면 다른 모델이다.
- 판정 도구는 결정 세션 < D₀ 인 행을 **코드로 거른다.** 거른 행 수를 출력한다. 거르지 않았으면 rc ≠ 0 으로 끝낸다.
- LLM 을 쓰면 **LAP 탐지 기록**(날짜만 주는 질의로 사후 사건 회상률)을 판정 창 안·밖으로 적는다. 기준이 아니라 기록이다.
- 회사명·종목코드 가림은 보조 수단으로만 적용한다. 가렸다고 규칙 4 를 면제하지 않는다.
