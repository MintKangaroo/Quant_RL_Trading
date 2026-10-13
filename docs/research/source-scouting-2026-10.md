# 새 정보원(재료) 후보 정찰 — 2026-10-13

> **조사 문서다(시행·사전등록 아님).** 수익·IC 는 하나도 계산하지 않았다. 웹(API 가이드·robots.txt 실측·논문 초록)과 저장소 문서·수집기 코드만 봤다.
> 코드·크론·창고·config 는 건드리지 않았다.
> 앞선 스캔 [source-scan-2026-10-10](../diag/source-scan-2026-10-10.md)(N1~N10)과 대장(memory `source-candidates-backlog`)에 이미 있는 후보는
> 새 근거가 없으면 다시 올리지 않았다. 컨센서스·추정치(FnSpace 연 70만원)는 사용자 결정으로 닫혀 있어 다루지 않는다.
>
> 배경: 10/13 금고에서 13건 중 **AR(내부자 G4 국장 + G7 미장)만 통과**했다. "병목은 모델이 아니라 정보"
> ([[headroom-bottleneck-is-information]])가 다시 확인됐다. 이번 정찰의 우선 가설은 **사람의 행동·의도가 드러나는 재료**다.
> 다만 거기에 갇히지 않으려고 수출·조달·특허·대차도 함께 봤다.

---

## 추천 상위 5개

| # | 무엇 | 왜 | 과거 이력 | 수집 난이도 | 검증 방법 한 줄 |
|---|---|---|---|---|---|
| **1** | **국장 내부자 원문 파싱(AR 심화)**. DART 「임원ㆍ주요주주특정증권등소유상황보고서」 원문에서 **보고사유(장내매수·장내매도·신규선임·스톡옵션·증여…)·단가·변동일·보고자 유형**을 뽑는다. 이걸로 기회적/일상적 구분·군집 매수·보고자 유형별 피처를 만든다 | 지금 G4 는 `elestock.json` 의 **증감 주수만** 쓴다. 보고사유가 없어서 장내 순매수와 스톡옵션·신규선임 같은 기계적 변동이 섞여 있다. 문헌을 보면 정보는 **기회적 거래**(CMP 2012, 월 +82bp)와 **군집**(Alldredge·Blank 2019)에 몰린다. 한국에서는 **직급이 높을수록**(Mazza·Ruh 2022), **매도 군집이 폭락 직전 짧게**(Park·Lim 2023) 신호를 낸다 | 원문 `document.xml` 은 1999 접수분부터 나온다(N2 실측 2026-10-11). 창고 `documents` 의 ownership 제목은 2021-08~ 이다. **elestock 의 2년 창(G4 가 2024-08~ 인 이유로 추정)을 넘어 확장 패널 전체를 덮는다** | **중**. 수집기(`collect_filing_texts.py`)는 있다. `ownership` 유형만 대상에 더하고 파서 하나를 새로 만든다(G12·G13 파서와 같은 틀). 콜 수는 미실측이고, 연 수만 건이면 한도 2만/일로 며칠이다 | AR 과 같은 틀(대조 C0 = 현행 + G4 원형, 처리 = + 변형 피처 ≤3, 시드 3, 상위 24 z-수익 + ΔIC). 판정은 11/23 second 창. 기회적/일상적 분류는 **3년 선행 이력이 필요**해 원문 백필이 2018 전후까지 가야 한다 |
| **2** | **미장 Form 4 심화 + 백필 2006~**. CMP 기회적/일상적 구분, 서로 다른 내부자의 **군집 매수**, 매수(P)만 쓰는 피처, 직책(CEO/CFO) 구분을 한다 | G7 은 **2024q1~ 만 백필**돼 있다(`backfill_form4.py --start 2024q1`). 기회적/일상적 구분에는 3년 선행 이력이 있어야 한다. 분기 벌크(Insider Transactions Data Sets)는 2006~ 이 무료이고 수집기도 이미 있어 **인자만 바꾸면 된다**. `title`·`relationship`·`trans_code`·`plan_10b5_1` 이 이미 저장된다. 문헌상 매수가 매도보다 강하다(Lakonishok·Lee 2001, Jeng 외 2003: 매수 초과수익의 약 1/4 이 **첫 5일** = h5 와 맞음) | 2006~ (벌크) | **저**. 수집기·표가 있다(`collectors/sec_form4.py`, `form4_trades`). 백필 2~3일, 피처 계산만 새로 한다 | AR 과 같은 틀(미장 주 시장). 처리 = G7 + {opp_buy_60, cluster_buy_20, ceo_cfo_net_60} 중 ≤3 |
| **3** | **사전공시 거래계획보고서(국장) + Form 144(미장)**. "앞으로 팔겠다/사겠다" 는 **의도 공시**와 **계획 대비 미이행** | 2024-07-24 시행(8/23 결제분부터). 6개월 합산 발행주식 1% 이상 또는 50억원 이상 거래는 **30~90일 전에** 「임원ㆍ주요주주특정증권등거래계획보고서」로 공시해야 한다. 대형 내부자 매도를 **미리** 보는 유일한 공개 경로다. 미장 Form 144 는 예고 뒤 36.2% 가 다 안 팔았고, 그 미이행이 이후 양(+)의 성과를 예측한다(Voelcker 2024, 학위논문) | 국장 2024-08~(G4 와 같은 깊이). 미장 Form 144 는 전자 제출 의무가 2023-04-13~ | **중**. 국장은 DART 원문(1번과 같은 배관, 제목 `거래계획보고서`), 미장은 EDGAR 일별 색인에서 form 144 XML 을 직접 읽는다(DERA 데이터셋엔 없음) | 이력이 짧아 **11/23 에는 커버리지 실측만** 하고 등록은 그다음 금고로 미룬다. 사건 연구는 판정 창 밖 구간에서만 한다(계획 공시일 기준 h5/h20, 계획 규모/시총) |
| **4** | **국장 종목별 대차잔고 + 의무보호예수 반환(오버행)**. data.go.kr 금융위원회 `주식대차정보`(15059612) · `주식발행정보`(15043423, 반환일·반환주식수·사유) · 예탁원 `증권대차서비스`(15157426) | 대장 #3(신용·대차)은 "공개 API 없음" 으로 보류돼 있었다. **이번에 종목별 일별 대차 API 를 찾았다** → 보류 근거가 바뀐다. 숏 수요는 우리 패자 알파와 같은 축이다([[ranker-skill-is-losers]], 공매도 재개 2025-03 뒤 대차가 다시 정보가 된다). 보호예수는 N8 의 "물량 원천 미확인" 을 메운다 | **미확인**(페이지에 기재 없음, 키를 넣고 가장 이른 `basDt` 를 찔러야 안다). 갱신은 "기준일 다음 영업일 13시 이후" 로 명시돼 있어 observed_at 을 지을 수 있다 | **중**(data.go.kr 키 신청, 개발 트래픽 1만/일). 이력이 3년 미만이면 하로 내린다 | 먼저 이력 시작일만 실측(수익은 보지 않는다). 2021-08 이전까지 있으면 대차잔고 변화/유동주식 피처 ≤2 로 등록한다. **공매도 금지기(2023-11~2025-03)와 재개 뒤를 나눠 보고**한다 |
| **5** | **DART 주요사항보고서 구조화 API(DS005) — 자사주 취득(직접 vs 신탁)·CB/BW(리픽싱 조건)·유상증자(배정 방식)·소송 제기** | 지금 이 사건들은 제목 분류(건수)와 L1 LLM 추출(원문, 최근분)로만 들어온다. DS005 는 **2015~ 의 정형 필드**(취득 예정 금액·방법·목적, 전환가액·최저 조정가액, 배정 방식, 소송 청구금액)를 준다. 확장 패널 전체를 결정론적으로 덮는다. 문헌: 자사주 취득 전 내부자 순매수와 겹치면 **코스닥에서** 장기성과가 높다(임병권·박순홍·윤평식 2015). 재벌 계열 사모 증자는 터널링 신호(Baek·Kang·Lee 2006 JF) | 2015~ (36종 전부 가이드 명시). 접수일 필드 → 기존 FilingPolicy(그날 장 마감 뒤) | **저~중**. DART 키·클라이언트가 있다. 날짜 축으로 받으면 엔드포인트 × 기간 콜이 수천 건 수준(추정) | G 묶음 틀. 처리 = 1번 내부자 피처와의 **교차**(자사주 취득 + 내부자 순매수) 하나 + 리픽싱 최저가 비율 하나. 단독 건수 피처는 BA 교훈(스타일 대리) 때문에 넣지 않는다 |

**한 줄 요약**: 1·2번은 **이미 통과한 재료를 같은 원천에서 더 잘게 쓰는 것**이라 사전확률이 가장 높다. 2번은 2주 안에 백필까지 끝난다. 1번은 파서 하나가 관건이다.
3번은 "의도" 를 미리 보는 새 정보지만 이력이 짧아 이번 금고엔 못 쓴다. 지금 수집을 시작해 이력을 쌓는 데 의미가 있다.

---

## 11/23 일정 메모

- 등록·해시 고정 마감 **~11/16**(오늘부터 약 5주). 굽기·검산 여유를 빼면 **수집기 + 과거 이력 백필은 10/27 경까지** 끝나야 안전하다.
- **2주 안에 되는 것(가산점)**: 2번 Form 4 백필(수집기 그대로, 분기 ZIP 약 80개) · 5번 DS005(키·클라이언트 있음) · 1번(파서가 G13 처럼 표본 수백 건으로 1~2일에 굳으면).
- **2주 안에 안 될 수 있는 것**: 4번(이력 미확인 + 키 신청 승인 대기) · 3번(이력 자체가 짧아 이번엔 커버리지만).
- 11/23 second 창은 국장 30세션이다. 상위 24 z-수익의 MDE 가 크다(early 창 기준 28%p 이상, [vault-early-open](../protocols/vault-early-open-2026-10.md)).
  변형 피처의 기대 효과가 작으므로 **시드 3/3 같은 방향 + ΔIC 해 방지**를 주 기준으로 두는 AR 형식이 맞다.
- 1·2번은 **같은 family(내부자)** 이므로 다중 비교 카운터를 같이 올린다. 변형은 ≤3 피처 한 묶음으로 등록한다(피처마다 따로 시행하지 않는다).

---

## 후보 표

| # | 재료 | 시장 | 소스/엔드포인트 | 이력 시작 | point-in-time | 비용/제한 | robots·약관 | 가설(어떤 종목을 가르나) | 근거 문헌 | 우선순위 |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | 내부자 보고사유·단가·보고자 유형(원문 파싱) | KR | OpenDART `document.xml`(rcept_no) — 「임원ㆍ주요주주특정증권등소유상황보고서」 | 원문 1999~(N2 실측 근거), 창고 제목 2021-08~ | 접수일 → FilingPolicy(그날 18시). 변동일은 접수일보다 앞이라 **접수일만 쓴다** | 일 2만 콜. 원문 크기 작음(표 1~2개) | opendart 는 robots 없음. **dart.fss.or.kr 은 `/dsaf001/main.do`·`/report/viewer.do`·`/report/download.do` Disallow** — API 경로(opendart)만 쓸 것 | 장내 순매수(기계적 변동 제외)가 큰 종목 ↑, **여러 내부자 매도 군집** ↓(h5), 최대주주·특수관계인 vs 기타 주요주주 **부호가 다를 수 있음** | Cohen·Malloy·Pomorski 2012 JF · Alldredge·Blank 2019 JFR · Mazza·Ruh 2022 IRLE(한국, 직급↑ 성과↑) · Park·Lim 2023 APJAE(한국 매도 군집 → 폭락 직전) · earticle A274189(보고자 유형별 부호 반대, 저자 미확인) | **상** |
| A2 | Form 4 기회적/일상적·군집 매수·직책 | US | SEC Insider Transactions Data Sets 분기 벌크(기존 수집기) | 2006~ | 접수 시각(Form 4 는 22:00 이후 접수분이 다음 영업일 공개) | 초당 10건, UA 필수 | sec.gov `/Archives` Allow | 기회적 매수·3인 이상 군집 매수 ↑, 기회적 매도 ↓, 일상적 거래 = 0 | CMP 2012 · Lakonishok·Lee 2001 RFS(매수만 정보, 소형주) · Jeng·Metrick·Zeckhauser 2003 REStat(매수 연 6%+, 1/4 이 첫 5일) · Kim·Kim·Rajgopal SSRN 5362431(10b5-1 개정 뒤 계획매도 효과 소멸, **계획 해지 뒤 양**) | **상** |
| A3 | 사전공시 거래계획보고서 · 계획 미이행 | KR | DART 원문(제목 `거래계획보고서`) — 구조화 API 여부 미확인 | 2024-08~ | 접수일 | 일 2만 콜 | 위와 같음 | 대규모 매도 계획 공시 종목 ↓(30~90일 앞), 계획 대비 덜 판 종목 ↑ | 제도: 금융위 보도자료(2024). 효과 연구 **없음(미확인)** — 첫 재현이 된다 | **중**(이력 짧음 → 관찰 수집) |
| A4 | Form 144 매도 예고 · 미이행 | US | EDGAR 일별 색인 form 144 XML | 2023-04-13~ | 접수 시각 | 초당 10건 | Allow | 예고 뒤 실제 Form 4 매도가 없거나 적은 종목 ↑ | Voelcker 2024(MIT 학위논문, 2008~2019 10만 건, 36.2% 미이행) | 중 |
| A5 | 13D(행동주의) 구조화 XML | US | EDGAR SC 13D/13G XML | 서류는 1990년대~, XML 의무화 2024-12-18~ | 접수 시각 | 초당 10건 | Allow | 13D 신규 제출 종목 ↑(행동주의 개입) | (이번에 확인한 13D 수익 문헌 없음 — 미확인) | 중-하(XML 이력 짧고 HTML 은 파싱 필요. `edgar_filings` 가 이미 ownership 으로 제목 분류) |
| B1 | DS005 자사주 취득(직접/신탁·목적·금액) | KR | `tsstkAqDecsn`·`tsstkAqTrctrCnsDecsn`·`tsstkAqTrctrCcDecsn`·`tsstkDpDecsn` | 2015~ | 접수일 | 일 2만 | robots 없음 | 직접취득·금액/시총 큰 종목 ↑, 임직원 보상 목적 처분 ↓. **내부자 순매수와 겹칠 때만** 유의(코스닥) | 임병권·박순홍·윤평식 2015 재무연구. 거래소 분석 당일 +0.93%p·20일 +6.06%p(언론, 2007~09). 단 **우리 실측 C 자사주 소각 h20 −0.6%(t −1.3)** 로 단독 효과는 이미 약했다 | **중상**(교차 피처로만) |
| B2 | DS005 CB/BW/EB 발행(리픽싱 최저가·전환가) | KR | `cvbdIsDecsn`·`bdwtIsDecsn`·`exbdIsDecsn` | 2015~ | 접수일 | 일 2만 | robots 없음 | 리픽싱 하한 낮은(현재가 대비) CB 를 낸 종목 ↓ — 국장 패자의 전형 | 한국 학술 **미확인**. 대만 Wang·Lin·Lee 2015 APJFS. N1(순발행) 실측 부분 t −4.6 이 같은 방향 | 중상(롱온리라 **제외 필터**) |
| B3 | DS005 유상증자(배정 방식·할인율) | KR | `piicDecsn`·`pifricDecsn` | 2015~ | 접수일 | 일 2만 | robots 없음 | 제3자배정·대폭 할인 ↓, 재벌 계열 사모 ↓(터널링) | Baek·Kang·Lee 2006 JF(1989~2000) | 중 |
| B4 | DS005 소송 제기·부도·영업정지·회생 | KR | `lwstLg`·`dfOcr`·`bsnSp`·`ctrcvsBgrq` | 2015~ | 접수일 | 일 2만 | robots 없음 | 소송 청구금액/자기자본 큰 종목 ↓ | (국내 정량 근거 미확인). distress 제목은 이미 G2(기각)가 씀 → 중복 큼 | 하 |
| B5 | DS002 배당(alotMatter)·자기주식 현황 | KR | `alotMatter`·`tesstkAcqsDspsSttus` | 사업연도 2015~ | **응답에 접수일 없음** → rcept_no 로 조인해야 함 | 일 2만 | robots 없음 | 배당 증가 ↑ | 결과 엇갈림(earticle A192349 ↑ / 홍춘욱 외 2012 유의하지 않음) | 하 |
| C1 | 종목별 대차잔고(일별) | KR | data.go.kr 금융위 `GetStocLendBorrInfoService_V2`(15059612) · 예탁원 `B552481/SlbSvc` `getSlbDealingByIsin`(15157426) | **미확인** | 금융위: "기준일 다음 영업일 13시 이후" 갱신 명시 → 깨끗. 과거 정정 여부 미확인 | 개발 1만/일(금융위), 100/일(예탁원 GW) | data.go.kr Googlebot 일부 경로만 Disallow. **공공누리 2유형 = 상업적 이용 금지** | 대차잔고 급증(숏 수요) ↓, 금지기 뒤 효과 회복 여부 | 국내: 공매도 강도 → 이후 음(KoreaScience 2016, 2005~2016) · Wang 2023 PBFJ(투자등급 기업만) · Lee·Wang 2022 JDQS(국민연금 대여 중단 뒤 수익 상승 없음). 해외: Muravyev 외 2025 JFE | **중상**(이력 확인 뒤 결정) |
| C2 | 의무보호예수 반환(일·주식수·사유) | KR | data.go.kr 금융위 `GetStocIssuInfoService_V3`(15043423) · 예탁원 주식정보서비스 `B552481/StockSvc`(15157413) | **미확인** | 반환일은 미리 알 수 있음(누수 없음). 공표 시점 확인 필요 | 위와 같음 | 2유형(상업 금지) | 반환 주식수/유동주식 큰 종목 ↓(해제 전 약세) | 증권사 분석(2012~ 189건, 해제 50영업일 전부터 하락) · Bradley 외 2001(미국, VC 기업). 국내 학술은 Kim 2010 **양(+)** 으로 부호가 갈림 | 중(신규상장 종목만이라 커버리지 작음, 제외 필터) |
| C3 | 월별 전환청구 현황·잠재주식수 | KR | 예탁원 증권예탁통계(15157347)·기업정보서비스(15157348) | 미확인 | 미확인 | 개발 100/일 | 2유형 | 전환 물량 출회 ↓ | N1 과 같은 축(실현 주식수가 이미 잡음) | 하(N1 과 중복) |
| D1 | 관세청 품목별 수출(HS) | KR(업종) | data.go.kr 관세청 `nitemtrade/getNitemtradeList`(15100475) · ECOS 901Y118 등(국가·성질별, 2000-01~) | 관세청 미확인, ECOS 2000~ | **덮어쓰기**(매월 15일경 정정 반영) → 우리가 날짜별로 쌓아야 PIT. 과거 빈티지 없음 | 개발 1만 | 이용 제한 없음(관세청). **unipass `/csp/`·bandtrass 4경로 robots 금지 → 스크래핑 불가** | 반도체 수출 서프라이즈 → 반도체 업종 h20 ↑ (국면·업종 기울이기 재료, 랭커 횡단면 아님) | 수출→업종 수익 선행 **학술 근거 없음(미확인)**. 1992 SJE(한국·대만 인과 없음). 업종 정보 확산 Hong·Torous·Valkanov(게재지 미확인) | 중-하(업종 하나 = 독립 표본 작음, 빈티지 없음) |
| D2 | 관세청 10일·20일 잠정 수출 | KR(업종) | **API 없음** — 보도자료만(보통 11일·21일) | — | 발표 시각 미확인 | — | 보도자료 사이트 robots 미확인 | 위와 같음 | 위와 같음 | 하(원천 없음 → 닫는 표로) |
| D3 | 나라장터 계약(업체별 조달 수주) | KR | data.go.kr 조달청 계약정보서비스(15129427) | 2004-07~(메타데이터, 최신 반영 미확인) | 계약일·변경 이력 조회 기능 있음 | 개발 1,000 | 이용 제한 없음 | 대형 수주(매출 대비) ↑ | Pyun 2025 Economics Letters(대형 계약 → 장기 수익, 미국) · Ferris 외 2025(수의계약 반응 큼) | 하-중(사업자번호 → 상장사 매핑 필요, G13 공급계약과 중복 가능, 지평이 김) |
| D4 | KIPRIS 특허(출원인 법인) | KR | data.go.kr KIPRISPlus(15057851·15059277·15065437) | 미확인 | 공개일 기준 가능 | **월 1,000회 무료** → 2,600종목 백필 불가, 벌크는 유료 | www.kipris.or.kr `Disallow: /` (웹 금지), plus 는 robots 없음 | 혁신 효율(특허/R&D) ↑ | Hirshleifer·Hsu·Li 2013 JFE(월~분기 지평) | 하 |
| D5 | KRX 주식옵션·신주인수권증권 일별 | KR | KRX Open API 파생·증권상품 | 유가 2010-01~, 코스닥 옵션 2017-06~, 신주인수권 2010~ | 일별 정산 | 이미 키 있음 | robots 없음 | (N9 와 같음) | Woo·Kim 2021(36종목) | 하(N9 기록 유지) |
| E1 | USAspending 정부 계약 | US | `api.usaspending.gov` v2 | FY2008~(2차 출처) | 계약 3영업일 보고, **DoD 는 90일 지연** | 키 불필요 | api 호스트 robots 없음 | 대형 수주 ↑ | Pyun 2025 | 하 |
| E2 | SEC Financial Statement and Notes | US | DERA 데이터셋 | 2009Q1~ | "as filed"이나 재처리 이력 있음(2024-09) | — | Allow | (N4 현금흐름 발생액 재료) | Sloan 1996 | 하(G5 계열 기각) |
| E3 | FINRA ATS 주간 거래량 | US | `weeklySummaryHistoric` | **굴러가는 4년(지금 2021-10-13~)** | Tier1 2주·나머지 4주 지연 | — | **FINRA 약관: 비상업 내부 이용만** | (다크풀 비중 → 정보 거래) | 미확인 | 하 |
| E4 | openFDA 신약 승인 | US | `api.fda.gov/drug/drugsfda.json` | 미확인 | 승인일 필드 | 키로 일 12만 | robots 없음 | 승인 이벤트(바이오만) | 미확인 | 하 |

---

## 닫는 후보(안 되는 이유)

| 후보 | 안 되는 이유 |
|---|---|
| 미 의회 의원 거래(STOCK Act) | **법으로 상업 이용 금지**(5 U.S.C. §13107(c), 민사 벌금). 상원 eFD 는 robots.txt 조차 403. 퀀트 회사 목표와 충돌 |
| Google Trends | 공식 API 는 alpha 신청제. 웹 `/explore?` 는 robots Disallow |
| PatentsView(USPTO) | 2026-03-20 부터 API 중지, 벌크는 2025-12-31 까지. 출원인 통합을 사후 재계산해 **매핑에 미래 정보가 섞임** |
| KIPRIS 웹 검색 스크래핑 | www.kipris.or.kr `User-Agent: * Disallow: /`. 무료 API 는 월 1,000회라 백필 불가 |
| unipass·bandtrass 수출 스크래핑 | robots 금지(`/csp/`, `/customs/` 등). 관세청 10일·20일 잠정치는 API 가 없다 |
| 금융위 KRX상장종목정보(15094775) | 공공누리 **4유형** — 재배포·변경 금지. 상업용은 data.krx 유료. 이미 KRX Open API 로 같은 정보를 받고 있다 |
| KOFIA 신용공여·예탁금 | data.go.kr 15094809 는 **시장 전체**만 준다. 종목별 신용잔고가 freesis 에 있는지는 미확인. 대장 #3 의 신용 쪽 보류 유지(대차 쪽만 C1 로 재개) |
| Cboe put/call CSV | 2019-10-04 에서 끊김. 종목별 일별은 유료(DataShop) |
| Reddit·StockTwits | 대규모 상업 이용 유료, StockTwits 는 API 제거 보도(미확인). SNS 는 사용자가 2026-08-13 보류 |
| 미 의원·H-1B LCA·Form 5500 | 연·분기 단위이고 기업 매핑이 어렵다. h5 랭커에 맞지 않는다 |
| SEC FTD·의견서 | 앞선 스캔(N5·N6)에서 이미 하. 새 근거 없음 |
| Lazy Prices · 순발행 · 주식선물 괴리 | 이미 N2·N1·N3 로 진행 중 — 중복 추천하지 않는다 |
| 컨센서스·목표가 | 유료(FnSpace) 또는 robots 금지(한경·네이버·FnGuide). 사용자 결정으로 닫힘 |

---

## 부수 발견 — 리드 확인 필요(코드는 안 고쳤다)

1. **`tools/collect_float_ratio.py`(크론 일 18:00 매주)가 robots 금지 호스트를 긁는다.** `navercomp.wisereport.co.kr/robots.txt` = `User-agent: * / Disallow: /` (2026-10-13 실측).
   사용자 규칙([[yahoo-replaced-by-ls-t3518]], "robots 금지 호스트는 쓰지 않는다")과 충돌한다. 대체 후보는 예탁원 기업정보서비스의 주식분포(15157348, 2유형)인데,
   유동비율을 직접 주는지는 미확인이다. 크론을 끌지 여부는 사용자 결정 사항이다.
2. **FINRA 공매도(`collectors/finra_short.py`)는 FINRA 약관상 비상업 내부 이용만 허용된다.** data.go.kr 2유형 API(C1·C2·C3)도 상업 이용 금지라
   예탁원(portal@ksd.or.kr)과 따로 계약해야 한다. 지금은 모의·연구라 문제없지만, **퀀트 회사 설립**([[user-goal-quant-firm]]) 때 재계약 목록에 올려야 한다.
3. dart.fss.or.kr robots 는 뷰어·다운로드 경로를 막는다. 공시 원문 수집기가 `opendart.fss.or.kr/api/document.xml`(robots 없음)만 쓰는지 리드가 한 번 확인할 것.
   N2 실측 문서에는 opendart 경로로 적혀 있다.
4. 대장 #3 "대차잔고 — 공개 API 없음(확인)" 은 **틀렸다.** 금융위 `주식대차정보`(15059612)가 종목별 일별 대차를 준다(이력은 미확인). 대장을 고칠 때 반영할 것.
5. KR 조사 에이전트는 KRX Open API 공식 서비스 목록에 "투자자별 수급" 이 없다고 보고했다. 우리 수급은 LS t1717 백필과 KRX 경로로 들어오므로([[foreign-flow-macro-2026-10-06]]) 문제는 아니다. 기록만 남긴다.

---

## 미확인 목록(등록 전에 확인할 것)

- elestock(DS004)이 majorstock 처럼 2년 롤링인지 — G4 시작 2024-08 이 그 흔적으로 보이지만 가이드에는 기재가 없다.
- `documents` 의 ownership 제목 건수(연도별)와 소유상황보고서 원문의 표 서식 변형 수 — 1번의 콜·파서 비용.
- 거래계획보고서에 구조화 API 가 있는지, 제목 패턴.
- data.go.kr 대차(15059612·15157426)·보호예수(15043423) 이력 시작일, 과거 정정 여부.
- CMP 의 일상적 거래 정의(원문은 "직전 3년 연속 같은 달" 로 알려져 있으나 이번엔 요약으로만 확인).
- 국문 논문 효과 크기(KCI·DBpia 원문 접근 불가로 초록만 확인).

---

## 출처

**문헌**
- Cohen·Malloy·Pomorski 2012 JF "Decoding Inside Information" — https://www.nber.org/digest/apr11/w16454.html
- Alldredge·Blank 2019 JFR 42(2) — https://ideas.repec.org/a/bla/jfnres/v42y2019i2p331-360.html
- Lakonishok·Lee 2001 RFS 14(1) — https://ideas.repec.org/a/oup/rfinst/v14y2001i1p79-111.html
- Jeng·Metrick·Zeckhauser 2003 REStat — https://scholar.harvard.edu/sites/scholar.harvard.edu/files/rzeckhauser/files/insider_trading.pdf
- Kim·Kim·Rajgopal (10b5-1 개정, SSRN 5362431) — https://clsbluesky.law.columbia.edu/?p=67723
- Voelcker 2024 (Form 144) — https://clsbluesky.law.columbia.edu/?p=69135 · https://dspace.mit.edu/handle/1721.1/155852
- Mazza·Ruh 2022 IRLE 71 (한국 내부자) — https://ideas.repec.org/a/eee/irlaec/v71y2022ics0144818822000321.html
- Park·Lim 2023 APJAE 30(5) (한국 내부자 군집·폭락) — https://ideas.repec.org/a/taf/raaexx/v30y2023i5p1368-1389.html
- 임병권·박순홍·윤평식 2015 재무연구 28(3) — https://www.earticle.net/Article/A252012
- 보고자 유형별 순매수·폭락(저자 미확인) — https://www.earticle.net/Article/A274189
- 내부자거래 사전공시제도(금융위) — https://www.fsc.go.kr/no010101/82635?curPage=97
- Baek·Kang·Lee 2006 JF — https://ideas.repec.org/p/hit/hitcei/2004-6.html
- Hirshleifer·Hsu·Li 2013 JFE — https://ideas.repec.org/a/eee/jfinec/v107y2013i3p632-654.html
- Pyun 2025 Economics Letters 252 (정부조달) — https://ideas.repec.org/a/eee/ecolet/v252y2025ics0165176525001727.html
- 공매도 강도(한국 2005~2016) — https://koreascience.kr/article/JAKO201623562837921.do
- Cohen·Frazzini 2008 JF / 감쇠 재현 — https://www.aqr.com/Insights/Research/Journal-Article/Economic-Links-and-Predictable-Returns · https://ar5iv.labs.arxiv.org/html/2301.11394

**데이터 원천**
- OpenDART DS005/DS002/DS004 — https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS005 · https://dart.fss.or.kr/robots.txt
- KRX Open API 서비스 목록 — https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
- data.go.kr: 15059612(주식대차) · 15043423(주식발행·보호예수) · 15157413/15157426/15157347/15157348(예탁원) · 15094809(KOFIA) · 15100475(관세청) · 15129427(조달청) · 15057851/15059277/15065437(KIPRIS) · 15094775(KRX상장종목, 4유형) — `https://www.data.go.kr/data/<번호>/openapi.do`
- SEC — https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data · https://www.sec.gov/edgar/filer/how-do-i/file-form-144-electronically · https://www.toppanmerrill.com/blog/sec-modernizes-beneficial-ownership-reporting-on-schedule-13d-and-13g-with-xml-format/
- FINRA 약관 — https://developer.finra.org/sites/default/files/2022-12/Developer%20API%20-%20Specific%20Terms%20-%20Equity%20Data%20%2812-2022%29%5B10%5D.pdf
- USPTO PatentsView 이전 — https://www.uspto.gov/subscription-center/2026/patentsview-migrating-uspto-open-data-portal-march-20
- USAspending / FPDS DoD 지연 — https://api.usaspending.gov/ · https://www.fpds.gov/common/html/dodDataAvailability.html
- 의원 공시 — https://disclosures-clerk.house.gov/FinancialDisclosure · https://efdsearch.senate.gov/
- Google Trends API — https://developers.google.com/search/blog/2025/07/trends-api
