# 용어집과 구조

이름은 격자(quant_rl_trading)에서 왔다. 옵션 가격결정의 이항 격자에서 온 말이자,
이 시스템의 다층 에이전트 구조 그 자체다.

> Analysts score, the Selector nominates, the Allocator sizes, the Executor acts.

---

## 에이전트 15

| 층 | 이름 | 역할 | 패키지 |
|---|---|---|---|
| 수집 | **Collector** ×2 | 시장 / 문서 수집. 점수를 내지 않는다 | `collectors/` |
| 분석 | **Analyst** ×10 | chart, volume, flow_kr, flow_us, fundamental, news, sns, regime, event, risk | `analysts/` |
| 결정 | **Selector** | 표를 모아 후보 선정 + Analyst 가중치 진화 | `selector/` |
| 결정 | **Allocator** | RL 제어기 — 비중·타이밍·현금·KR/US 배분 | `allocator/` |
| 결정 | **Executor** | 주문 변환·상한·리스크 가드·킬스위치 (AI 없음) | `executor/` |
| 결정 | **Auditor** | 사후 귀속 분석 — "왜 돈을 벌었나" | `auditor/` |
| 결정 | **ModelOps** | 모델 관리·학습 진단 — "왜 모델이 잘 되나" | `modelops/` |

**Signal** — Analyst의 출력 스키마 (score, confidence, horizon, evidence)
**Verdict** — News·SNS의 차단 판정 스키마
**Session** — 하루의 의사결정 사이클

---

## 패키지 구조

```
quant_rl_trading/
  store/        데이터 게이트 (Parquet + DuckDB), 이중시간 조회
  replay/       Clock, 이벤트 로그, 체결 시뮬레이터
  collectors/
  analysts/
  selector/
  allocator/
  executor/
  auditor/
  modelops/
  schemas/      Signal, Verdict, Order, Review
  dashboard/    Flask + ECharts
docs/
tests/
  invariants/   불변식 검증 — 커밋 전 필수 통과
```
