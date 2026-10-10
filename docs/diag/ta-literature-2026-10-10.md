# 가격·거래량 기반 종목선정 신호 — 문헌 조사, 한국 재현 여부 (2026-10-10)

목적: 학계에서 검증된 기술적/가격 기반 횡단면 신호 중 **국장(KOSPI·KOSDAQ)에서 재현된 것**과 그 **부호**를 정리한다.
방법: 웹 검색(SSRN·KCI·koreascience·KAIST/대학 리포지토리·IDEAS·Emerald). 초록·서지 수준에서 확인한 것만 적었다.
원문 표를 직접 확인하지 못한 수치는 "초록 기준"으로 표시. 찾지 못한 것은 **미확인** — 존재하지 않는다는 뜻이 아니라 이번 조사에서 확인 못 했다는 뜻이다.

부호 표기: **−** = 신호 값이 높을수록 이후 수익률 낮음(숏 다리형), **+** = 높을수록 높음.

## 1. 요약표

| # | 신호 | 원 논문 · 원 시장 부호 | 지평 | 한국 재현 (논문 · 표본 · 부호 · 유의성) | 소형주/숏 다리 집중 |
|---|---|---|---|---|---|
| 1 | 고유변동성 IVOL | Ang·Hodrick·Xing·Zhang (2006) 미국 **−** | 월 | **재현, −.** Kang·Lee·Sim (2014, APJFS 43(2)) — 개인 거래비중 높은 종목에서 더 강함, 개인 심리 높은 뒤 강함. Jung·Yoo (2015, 1999–2013) — 개인 순매수 상위에서 퍼즐. Kim 외 (2023, IJOEM, KOSPI+KOSDAQ 1997–2016) — **회전율 통제 시 소멸**. Hằng 외 (2020, 1990–2018) — 미실현 손실 구간에서만 음 | 개인 비중·고평가 종목(숏 다리). 규모·가치 통제 후에도 유지 보고(KAIST 연구) |
| 2 | 잔차 모멘텀 | Blitz·Huij·Martens (2011) 미국 **+** | 월(12-1) | **부분 재현(약함), +.** KAIST 석사논문 (2017) 잔차 모멘텀 실증 — 결과 수치 미확인. KAIST "Momentum strategies and salience" — 전통 모멘텀은 부진·장기 반전, **고유(idiosyncratic)·순위·부호 모멘텀은 안정적 이익** (초록 기준) | 미확인 |
| 3 | Frog-in-the-pan (정보 이산성) | Da·Gurun·Warachka (2014) 미국 — 연속 정보 모멘텀 **+**, 이산 정보 모멘텀 소멸 | 월 | **미확인.** 국내 직접 검증 논문 찾지 못함. Subrahmanyam 국제 표본 연구(2022 세미나)에서 FIP 가 모멘텀 설명 경쟁에서 이김 — 한국 국가별 결과 미확인 | 미확인 |
| 4 | 산업 모멘텀 | Moskowitz·Grinblatt (1999) 미국 **+** | 월 | **재현 실패(0).** Kang·Ryu·Webb (2025, Investment Analysts Journal 54(4), 1983–2023) — 산업 모멘텀 **유의하지 않음**, 개별주 모멘텀은 오히려 **반전(−)** 우세 | 해당 없음 |
| 5 | 추세 요인 (다중 이평) | Han·Zhou·Zhu (2016, JFE) 미국 **+**, 월 1.61% | 월 | **미확인 (한국 단독).** Lin·Liu·Zhang (2023, IRF) 49개 시장 중 39개에서 유의 — 한국 포함 여부·국가별 수치 미확인. 중국(Liu·Zhou·Zhu)은 유효. 국내: 옥기율·이민규, 이평 전략을 규모·B/M·발생액 포트에 적용해 무비용 포트 수익 양(+) (시계열 매매 성격, 횡단면 추세 요인 아님) | 미확인 |
| 6 | 이평 거리 MA21/MA200 | Avramov·Kaplanski·Subrahmanyam (2021, RFE) 미국 **+**, VW 연 ~9%, **롱 다리가 더 강함**, 국제에서도 예측력 | 월 | **미확인.** 국가별 결과·국내 재현 찾지 못함 | 원 논문은 롱 다리 강함(이 표에서 드문 경우) |
| 7a | 하방 베타 | Ang·Chen·Xing (2006) 미국 **+** (연 ~6% 프리미엄) | 월 | **재현되나 부호 반대, −.** 재무관리연구 34(4) (2021, KAIST 리포지토리) — 하방 베타·베타 비대칭이 시장 베타·특성 통제 후 **수익률과 음**, 약·중·강세장 모두, 롱숏 알파 유의. Byun (2004, 조선대) — 하방 베타만 유의, 부호 음 | 미확인 |
| 7b | 공왜도 | Harvey·Siddique (2000) 미국 **−** (공왜도 낮을수록 고수익) | 월 | **미확인.** 국내 직접 검증 찾지 못함 | 미확인 |
| 8 | 주가 지체(price delay) | Hou·Moskowitz (2005) 미국 **+** (지체 클수록 고수익) | 월~연 | **수익률 예측으로는 미확인.** 국내 연구는 지체의 결정요인만 다룸: Kang·Kwon·Park (2016, PBFJ, 1999–2012) 외국인 거래가 지체 축소; Kim·Choi (2020) 기업집단 소속이 지체 완화; Kim (2021) 개인 거래 영향 | 미확인 |
| 9a | 회전율 | Datar·Naik·Radcliffe (1998) 미국 **−** | 월 | **재현, −.** Kongahawatte (AFR, 2004–2015) — 견고한 음, **개인 거래비중 높은 종목에서 더 강함**(오가격). KOSPI 1991–2007 Fama-MacBeth — 규모·회전율만 유의, 회전율 요인 월 ~1.19%. 이평회전율 괴리(MATD) 전략도 음 | 개인 비중 높은 종목·고평가(숏 다리) |
| 9b | 거래량 변동성 | Chordia·Subrahmanyam·Anshuman (2001) 미국 **−** | 월 | **부분 재현, −(조건부).** 대한경영학회지 (2009) — 유동성 변동성 음, 그러나 수익률 변동성과 상호작용 통제 시 **부호 바뀌고 유의성 약화** | 미확인 |
| 9c | 고거래량 프리미엄 | Gervais·Kaniel·Mingelgrin (2001) 미국 **+** (일·주 거래량 충격 → 다음 달 상승) | 일·주 → 월 | **엇갈림.** An 외 (2006, KOSPI 2001–2003, 2차 인용) **+**, 대형주에서 강함. 어지원 (2019, KAIST) 월간 **+**, 유동성 변화로 설명. Chae·Kang (2019, PBFJ) — **저거래량 프리미엄(−)** "퍼즐". 비정상 거래량 5주까지 +, 기대 거래량은 장기 − (2016 국내 연구) | 대형주에서 + (An 외) |
| 9d | 거래량·과거수익 (Lee·Swaminathan 2000) | 미국: 고거래량 패자 → 저수익, 모멘텀 증폭 | 월 | **반대 방향 증거.** Investor attention 연구 (2020, 서울시립대) — **고회전율(고관심) 종목에서 모멘텀 이익이 유의하게 음** | 고관심 종목 |
| 10 | 수익률 계절성 | Heston·Sadka (2008) 미국 **+** (같은 달 과거 수익) | 월 | **미확인.** Li·Zhang·Zheng 42개 시장 — 선진국에서만 경제적 유의, **신흥국에선 미미**(한국 포함 여부 미확인) | 미확인 |
| 11a | MAX (복권) | Bali·Cakici·Whitelaw (2011) 미국 **−** | 월(전월 최대 일수익) | **재현, −.** 강장구·심명화 (2014, 재무연구) — 최저/최고 MAX 5분위 위험조정 차 **월 1.39%**, 규모·B/M·모멘텀·유동성·단기반전 통제 후 견고, IVOL 로 설명 안 됨. Byun·Jeon·Kim (2023, Applied Economics) — **투자심리 낮은 뒤에만** 강함. Goh·Kim (2024, JDQS) — **고평가 종목에서만** 유의 | 고평가·개인 매수 종목(숏 다리) |
| 11b | 기대 고유 왜도 · Kumar 복권지수 | Boyer·Mitton·Vorkink (2010), Kumar (2009) 미국 **−** | 월 | **직접 재현 미확인.** 인접: KAIST 연구 — IMAX **−**, IMIN 도 **−**(이론과 반대, "IMIN 이상현상"); 다른 KAIST 연구 — 왜도의 양의 위험프리미엄 보고(부호 혼재) | 미확인 |
| 11c | 일간 상·하위 랭킹 종목(관심) | Kumar·Ruenzi·Ungeheuer — 미국 **−** | 월 | **재현, −.** Kang·Yun (2020, 증권학회지) — 전월 일간 승자·패자 랭킹 종목이 다음 달 저수익, 개인이 매수·기관/외국인은 축소. **IVOL 퍼즐·MAX 효과의 주 원인**으로 제시. 랭킹에 안 오른 종목은 IVOL·MAX 효과 없음 | 개인 관심 종목(숏 다리) |
| 12a | 52주 고가 근접 | George·Hwang (2004) 미국 **+** | 월 | **약한 근거만.** 김보영 (2005, 이화여대 석사) 국장 검증 — 결과 수치 미확인. 신흥국 지수 수준에선 무익 보고. 동료심사 국내 재현 미확인 | 미확인 |
| 12b | 상한가/하한가 사후 수익 | (한국 고유) | 일~주 | **사후 수익 직접 검증 미확인.** 상한가 직전 **자석효과**는 재현(KRX 고빈도 연구). 가격제한폭 30% 확대(2015) 뒤 랜덤워크 종목 증가(효율 개선, AJEER 2018). 주간 모멘텀 이익은 **좁은 가격제한폭 시기에만** 양(JDQS). 큰 가격충격 뒤 **반전**(유통업 KOSPI 2004–2022, 공시 없는 충격일수록) | 미확인 |
| 13a | 차트 패턴 (Lo·Mamaysky·Wang 2000) | 미국 — 일부 패턴에 증분 정보 | 일~주 | **미확인.** 국내 학술 재현 찾지 못함 | 미확인 |
| 13b | CNN 차트 이미지 | Jiang·Kelly·Xiu (2023, JF) 미국 **+** (예측 상승 확률) | 주(5·20일) | **국가별 미확인.** 원 논문: 미국에서 학습한 패턴이 국제 시장에서도 유효(전이학습) — 한국 포함 여부·수치는 원문 표 확인 필요 | 미확인 |
| 14a | 단기 반전 | Jegadeesh (1990), Lehmann (1990) 미국 **−** (전월·전주 수익) | 주·월 | **재현, −. 그러나 비용 후 소멸 경향.** 윤·조 (2006, KRX 1995–2005) — 고회전·비유동 종목에서 반전 강함, **실효스프레드 차감 시 초과이익 소멸**. 1980–2009 연구 — 1997 위기 전엔 반전, 후엔 모멘텀. Kang·Ryu·Webb (2025, 1983–2023) — 개별주 중기 모멘텀도 **반전 우세**. Sim·Kim (2021, JDQS) — 최근 2개월 반전이 모멘텀 기간구조를 좌우 | 비유동·소형(비용 큰 곳) |
| 14b | 거래량 조건부 반전 | Avramov·Chordia·Goyal (2006), Nagel (2012) 미국 | 주·월 | **재현, 투자자 유형별.** Ülkü·Onishchenko (2019, J. Forecasting) — 미국·한국: 기관 매수 동반 상승은 덜 반전, **개인 매수 동반 상승(개인 서식지)은 더 반전**, 증강 역발상 전략 수익 **40–70% 증가**. Kim·Park (2015, EMFT) — 개인의 역발상 매매가 유동성 공급, 단기 소폭 초과수익. Goh·Jeong·Kang (2022) — 자본손실 오버행 큰 종목·기관지분 낮은 종목에서 반전 강함 | 소형·개인 서식지 |
| 15a | 야간/장중 수익 줄다리기 | Lou·Polk·Skouras (2019) 미국 | 일 | **재현.** Ham·Ryu·Webb·Yu (2023, FRL) — 국장 **야간–장중 음의 관계**, 개인은 야간 수익에 역방향, 기관·외국인은 양의 되먹임. Kongahawatte (2014–2017) — 개인 관심 매매가 야간, 국내기관 차익이 장중 패턴 구동. Park·Yi (2011) — 미국 수익 → 국장 야간 +, 장중 −(과잉반응-반전). KOSPI 장중: 첫 30분 모멘텀 없음, **마지막 1시간 반전**, 소형·비유동에서 강함 (KAIST 2011–2020) | 소형·비유동 |
| 15b | 외국인 매매 관련 | Choe·Kho·Stulz (1999) — 외국인 양의 되먹임, 불안정화 증거 없음 | 일 | **횡단면 예측 근거 약함.** 개인·외국인 모멘텀 매매, 기관 역발상 (Gultekin·Umutlu 2016). 외국인이 지체 축소 (#8). 외국인 순매수 → 미래 수익 횡단면 예측의 동료심사 논문은 이번 조사에서 미확인 | — |
| 15c | 전통 가격 모멘텀 (참고) | Jegadeesh·Titman (1993) 미국 **+** | 월 | **약하거나 반전.** 1990년대 없음, 2000년대 대형주 중심 존재(외국인 매매 가설). 1983–2023 전체로는 반전 우세(Kang·Ryu·Webb 2025). 동아시아 저개인주의 시장에서 모멘텀 약함(Chui·Titman·Wei 2010) | 2000년대엔 대형주 |

## 2. 메모

1. **한국에서 견고하게 재현된 것은 모두 "숏 다리형 음(−) 신호"다.** IVOL·MAX·회전율·일간 랭킹 관심·단기 반전·하방 베타. 공통 메커니즘은 **개인 투자자 쏠림 → 고평가 → 사후 저수익**이고, 효과는 개인 거래비중 높은 종목·고평가 종목·투자심리 낮은 국면에 집중된다. 이는 프로젝트 기존 결론("랭커 실력은 패자 가려내기", "이상현상은 숏 다리")과 일치한다.
2. **이 신호들은 서로 많이 겹친다.** Kim 외(2023): 회전율이 IVOL 을 흡수. Kang·Yun(2020): 일간 랭킹 관심이 IVOL·MAX 의 원인. 강·심(2014): MAX 는 IVOL 로 설명 안 됨. 즉 새 피처로 하나씩 더하면 기존 저변동성·거래량 피처와 중복될 가능성이 높다 (efcc725 의 "MAX·밴드폭은 저변동의 다른 이름" 과 같은 결).
3. **미국에서 양(+)인 추세형 신호는 한국에서 약하거나 뒤집힌다.** 산업 모멘텀 0, 개별 모멘텀 반전 우세, 하방 베타는 부호 반대(−), 고거래량 프리미엄은 엇갈림. 추세 요인·MAD·FIP·CNN 은 한국 단독 재현이 **미확인**이므로, 쓰려면 자체 측정이 먼저다.
4. **단기 반전은 비용에 약하다.** 윤·조(2006)에서 실효스프레드 차감 후 소멸. 반전은 비유동·소형에서 가장 크다. 거래량·투자자 유형으로 조건화하면 40–70% 개선(Ülkü·Onishchenko 2019) — 국장에선 투자자별 순매수 자료가 있으므로 이 변형이 현실적이다.
5. **상한가/하한가 사후 수익의 동료심사 이벤트 연구는 이번 조사에서 찾지 못했다.** 2015년 ±30% 확대 전후로 체제가 다르므로, 자체 측정 시 2015-06-15 를 경계로 나눠야 한다.
6. 확인하지 못한 수치(원문 표 미열람): 잔차 모멘텀 KAIST(2017) 결과, 김보영(2005) 52주 고가 결과, An 외(2006) 원문, JKX(2023)·Lin·Liu·Zhang(2023)·Li·Zhang·Zheng 의 한국 국가별 수치.

## 3. 출처

- IVOL·회전율: Kim·Lee·Lee·Ok·Truong (2023) https://ideas.repec.org/a/eme/ijoemp/ijoem-09-2021-1499.html
- Kang·Lee·Sim (2014) APJFS https://koasas.kaist.ac.kr/handle/10203/189204?mode=full
- Jung·Yoo (2015) https://koreascience.kr/article/JAKO201504641500627.do
- 개인 복권 선호·IVOL (KAIST) https://koasas.kaist.ac.kr/handle/10203/265773
- Hằng 외 (2020) https://ideas.repec.org/a/taf/oaefxx/v8y2020i1p1838686.html
- MAX 강장구·심명화 (2014) https://www.earticle.net/Article/A238246
- MAX·투자심리 Byun·Jeon·Kim (2023) https://ideas.repec.org/a/taf/applec/v55y2023i3p319-331.html , https://koasas.kaist.ac.kr/handle/10203/304298
- MAX·차익거래위험 Goh·Kim (2024) https://emerald.com/insight/content/doi/10.1108/JDQS-09-2023-0031
- IMAX·IMIN (KAIST) https://koasas.kaist.ac.kr/handle/10203/265636
- 일간 승자·패자 Kang·Yun (2020) https://research.knu.ac.kr/en/publications/daily-winners-and-losers-in-the-korean-stock-market/
- 잔차 모멘텀 (KAIST 2017) https://dspace.kaist.ac.kr/handle/10203/242768
- 모멘텀·현저성 (KAIST) https://koasas.kaist.ac.kr/handle/10203/297600
- 모멘텀·반전 1983–2023 Kang·Ryu·Webb (2025) https://www.nisc.co.za/products/abstracts/38749/momentum-and-reversal-effects-in-the-korean-stock-market
- 관심·모멘텀 (서울시립대) https://pure.uos.ac.kr/en/publications/investor-attention-market-dynamics-and-momentum-in-the-korean-sto/
- 단기반전→모멘텀 Sim·Kim (2021) https://emerald.com/insight/content/doi/10.1108/JDQS-02-2021-0005/full/html
- 주간 모멘텀·가격제한폭 https://www.emerald.com/jdqs/article/23/4/543/226938/Profit-Analysis-of-Short-Term-Weekly-Momentum
- 단기 반전·유동성 공급(1995–2005) https://s-space.snu.ac.kr/handle/10371/32298?mode=full
- 거래량 조건부 반전 Ülkü·Onishchenko (2019) https://ideas.repec.org/a/wly/jforec/v38y2019i6p582-599.html
- 개인 유동성 공급 Kim·Park (2015) https://ideas.repec.org/a/mes/emfitr/v51y2015is5ps1-s20.html
- 반전·준거의존 https://koasas.kaist.ac.kr/handle/10203/291538?mode=full
- 유통업 가격충격 반전 https://koreascience.or.kr/article/JAKO202308156805283.do
- 일중 반전 KOSPI (KAIST) https://koasas.kaist.ac.kr/handle/10203/294938
- 추세 요인 국제 49개 시장 https://international.vlex.com/vid/the-trend-premium-around-1042472750
- 추세 요인 중국 https://wrds-www.wharton.upenn.edu/documents/1119/WARSP_TrendChina.pdf
- 이평 전략·시장이상 (국내) https://www.koreascience.or.kr/article/JAKO201825758342883.page?lang=ko
- MAD Avramov·Kaplanski·Subrahmanyam https://anderson-review.ucla.edu/wp-content/uploads/2021/03/Avramov-Kaplanski-Subra_2018_SSRN-id3111334.pdf
- 하방 베타 (2021, 재무관리연구) https://koasas.kaist.ac.kr/handle/10203/290386
- 하방 베타 Byun (2004) https://oak.chosun.ac.kr/handle/2020.oak/302
- 주가 지체·외국인 Kang·Kwon·Park (2016) https://koasas.kaist.ac.kr/handle/10203/212280
- 주가 지체·기업집단 https://scholarworks.bwise.kr/sch/handle/2021.sw.sch/3381
- 주가 지체·개인 https://scholarworks.bwise.kr/sch/handle/2021.sw.sch/19140?mode=full
- 회전율 이상현상·개인 Kongahawatte https://doi.org/10.31357/afr.v1i2.6909
- 거래량 관련 국내 (회전율·비정상 거래량) https://www.earticle.net/Article/A274216 , https://www.earticle.net/Article/A238153
- 유동성 변동성 (2009) https://scholar.kyobobook.co.kr/article/detail/4010022841954
- 고거래량 프리미엄 KAIST (2019) https://dspace.kaist.ac.kr/handle/10203/265619
- 한국 거래량 프리미엄 재해석 (프리프린트, 비심사) https://arxiv.org/abs/2512.14134
- 계절성 국제 Li·Zhang·Zheng https://nottingham-repository.worktribe.com/OutputFile/7529759
- 52주 고가 김보영 (2005) https://dspace.ewha.ac.kr/handle/2015.oak/178931
- 가격제한폭 확대 효율성 https://ideas.repec.org/a/aoj/ajeaer/v5y2018i2p191-200id259.html
- JKX (2023) https://papers.ssrn.com/abstract=3756587
- 야간·장중 Ham·Ryu·Webb·Yu (2023) https://ideas.repec.org/a/eee/finlet/v54y2023ics1544612323001526.html
- 야간·장중 Kongahawatte https://journals.sjp.ac.lk/index.php/afr/article/view/7881
- 국장 ETF 야간·장중 반전 https://scholarworks.bwise.kr/ssu/handle/2018.sw.ssu/42155
- Park·Yi (2011) 인용 출처 https://durham-repository.worktribe.com/OutputFile/1402814
- Choe·Kho·Stulz (1999) https://www.nber.org/papers/w6661
- Chui·Titman·Wei (2010) https://ideas.repec.org/a/bla/jfinan/v65y2010i1p361-392.html
