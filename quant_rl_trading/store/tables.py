"""테이블 레지스트리.

등록되지 않은 이름으로는 저장도 조회도 되지 않는다. 오타 하나로 새 테이블이
조용히 생기면, 그 테이블은 아무도 검증하지 않는 데이터가 된다.
"""

from __future__ import annotations

import pyarrow as pa

from quant_rl_trading.store.errors import UnknownTable
from quant_rl_trading.store.schema import TableSpec

CONFIG_TABLE = "config"

_SPECS: dict[str, TableSpec] = {
    "prices": TableSpec(
        name="prices",
        columns={
            "market": pa.string(),
            "open": pa.float64(),
            "high": pa.float64(),
            "low": pa.float64(),
            "close": pa.float64(),
            "volume": pa.float64(),
            "value": pa.float64(),
            # 수정주가는 저장하지 않는다. 원주가 + 조정계수로 시점별 재계산한다.
            # 수정주가에는 미래의 분할·증자가 이미 반영돼 있기 때문이다.
            "adj_factor": pa.float64(),
        },
        # 그날의 사실을 그날보다 먼저 관측할 수는 없다. 늦게 오는 쪽(백필·
        # 정정본)은 하한 **위**에 있으므로 안 잘린다. 선언이 없으면 하한
        # 프루닝이 통째로 꺼져 창을 좁혀도 파티션을 전부 연다 (flows 참조).
        observation_lag_days=3,
        doc="일봉. 원주가 기준.",
    ),
    "security_ids": TableSpec(
        name="security_ids",
        columns={
            "market": pa.string(),
            # "CUSIP" 또는 "CINS". 외국 발행사는 CINS(G1151C101)를 쓰고
            # 13F 는 그것도 cusip 칸에 담아 보낸다 — 물어보는 쪽이 두 종류를
            # 구분하지 않으면 8.7% 가 "없는 종목" 이 된다
            # (collectors/security_ids.py 실측 1).
            "id_type": pa.string(),
            "id_value": pa.string(),
            "figi": pa.string(),
            "composite_figi": pa.string(),
            # "Common Stock"·"ETP"·"REIT" 등. 13F 에는 ETF 도 섞여 들어오므로
            # 읽는 쪽이 종목만 고를 수 있어야 한다.
            "security_type": pa.string(),
            "name": pa.string(),
            # **우리가 실제로 물어본 시각.** valid_from·observed_at 은 아래
            # 이유로 기준시점(2015-01-01)에 박혀 있어서, 이 열이 없으면
            # "이 매핑은 언제 찍은 스냅샷인가" 를 창고에 물을 수 없다.
            "mapped_at": pa.timestamp("us", tz="UTC"),
        },
        # 한 티커에 여러 식별자가 붙는다(옛 CUSIP·재편 전 CINS). 식별자를
        # 키에서 빼면 그것들이 서로를 덮어 마지막 하나만 남는다.
        natural_key=("entity_id", "valid_from", "id_type", "id_value"),
        # valid_from == observed_at 이라 지연이 0 이지만, **선언 자체를
        # 빠뜨리면 하한 프루닝이 통째로 꺼진다.** 다른 테이블과 같은 3일.
        observation_lag_days=3,
        doc=(
            "식별자 매핑. entity_id = US:AAPL, id_value = 037833100. "
            "출처는 OpenFIGI. valid_from·observed_at 이 둘 다 2015-01-01 인 "
            "것은 실수가 아니다 — 식별자는 그 시점에도 공개된 사실이었고, "
            "조회한 날로 찍으면 과거 as_of 조회가 이 표를 한 행도 못 본다. "
            "근거와 남는 위험은 collectors/security_ids.py 모듈 docstring."
        ),
    ),
    "filings_13f": TableSpec(
        name="filings_13f",
        columns={
            "filer_cik": pa.string(),
            "filer_name": pa.string(),
            "issuer": pa.string(),
            "cusip": pa.string(),
            "value_usd": pa.float64(),
            "shares": pa.float64(),
            # 그 기관 포트폴리오 안에서의 비중. 수집기가 한 번만 계산한다 —
            # 화면에서 다시 나누면 분모를 무엇으로 잡았는지가 갈린다.
            "weight": pa.float64(),
            # 같은 CUSIP 이 몇 줄로 쪼개져 있었나. 13F 는 자회사 운용역별로
            # 줄을 나눠서 내므로(버크셔 2026Q2: 89줄 → 실제 29종목),
            # **합산했다는 사실 자체가 검증거리**라 남긴다.
            "folded_rows": pa.float64(),
            # 분기말 → 공개까지 걸린 날. 최대 45일이다. 이 값을 화면이
            # 안 보여주면 낡은 보유를 지금 보유로 읽는다.
            "lag_days": pa.float64(),
        },
        # 한 분기·한 기관·한 종목이 한 행이다. 기관이 빠지면 여러 기관이
        # 같은 종목을 들 때 서로를 덮는다.
        natural_key=("entity_id", "valid_from", "filer_cik"),
        # valid_from(분기말)과 observed_at(접수일)이 최대 45일 벌어진다 —
        # 이 표의 성격 자체가 그 간격이라 좁게 잡으면 정상 데이터가 막힌다.
        observation_lag_days=60,
    ),
    "prices_intraday": TableSpec(
        name="prices_intraday",
        columns={
            "market": pa.string(),
            # "1m"·"5m"·"15m"·"1H"·"4H" — LS ncnt(분)를 화면 버튼 이름으로
            # 옮긴 값. collectors/intraday_collector.py 참고.
            "interval": pa.string(),
            "open": pa.float64(),
            "high": pa.float64(),
            "low": pa.float64(),
            "close": pa.float64(),
            "volume": pa.float64(),
            "value": pa.float64(),
        },
        # 같은 종목·같은 시각에 여러 해상도(1m/5m/...)가 공존한다 — 마감
        # 시각이 정각에 겹치는 두 봉(예: 09:05 의 1분봉과 09:00~09:05 5분봉을
        # 마감시각으로 찍으면 둘 다 valid_from=09:05)은 interval 없이는
        # 같은 자연키로 충돌해 한쪽이 정정본으로 밀려난다.
        natural_key=("entity_id", "valid_from", "interval"),
        # 이 표는 "보유·워치리스트 종목의 최근 며칠" 만 하루 한 번 받는다
        # (수집 범위를 좁힌 이유는 intraday_collector.py 모듈독스트링 참고).
        # 그 말은 valid_from(며칠 전 봉)과 observed_at(오늘 수집 시각) 사이
        # 간격이 며칠까지 벌어질 수 있다는 뜻이다 — prices 의 3일보다 넉넉히
        # 잡는다. 이 값보다 좁히면 대시보드가 lookback 을 줘도 하한 프루닝이
        # 방금 수집한 옛 봉이 든 파티션을 조용히 건너뛴다.
        observation_lag_days=20,
        doc=(
            "분봉. prices(일봉)와 절대 섞지 않는다 — prices 를 읽는 코드 "
            "전부(회계·백테스트·store/prices.py 의 0-세션 제거)가 '하루에 "
            "한 행'을 전제로 짜여 있다. 대시보드의 분봉 캔들 전용 입력이고, "
            "전 종목·전 구간을 받지 않는다."
        ),
    ),
    "flows": TableSpec(
        name="flows",
        columns={
            "market": pa.string(),
            "investor": pa.string(),
            "net_value": pa.float64(),
            "net_volume": pa.float64(),
            # 잠정치/확정치 구분. **우리 소스는 이것을 주지 않는다** —
            # KRX 투자자별 순매수 응답에도 LS t1717 응답에도 없다. 수집기는
            # null 을 쓴다(모르는 것을 True 로 적어 두었던 것을 2026-08-22 에
            # 걷어냈다). 확정치 대체는 이 칸이 아니라 revision 으로 성립한다.
            "is_final": pa.bool_(),
        },
        natural_key=("entity_id", "valid_from", "investor"),
        # 그날의 순매수를 그날보다 먼저 관측할 수는 없다. 늦게 도착하는 쪽
        # (백필·정정본)은 하한 위에 있으므로 안 잘린다. 3일은 KST/UTC 경계와
        # 연휴용 여유다. **이 선언이 없으면 하한 프루닝이 통째로 꺼져서**
        # 45일을 달라고 해도 1,200여 개 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "투자자별 순매수. 주체가 자연키에 들어간다. 그날 그 주체가 손대지 "
            "않은 종목은 행 자체가 없다 — 없는 것을 0으로 채우지 않는다. "
            "'순매수 0' 과 '거래 없음' 은 다른 사실이다."
        ),
    ),
    "shorting": TableSpec(
        name="shorting",
        columns={
            "market": pa.string(),
            "board": pa.string(),
            "short_volume": pa.float64(),
            "total_volume": pa.float64(),
            "short_ratio": pa.float64(),
        },
        # 공매도 잔고는 사후 공시다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "공매도. **T+1~2 에 공표된다** — observed_at 이 세션 당일이면 "
            "flow_kr 이 통째로 미래를 본다. backfill.shorting_lag_days 로 "
            "거래일 단위로 늦춰 찍는다."
        ),
    ),
    "indices": TableSpec(
        name="indices",
        columns={
            "market": pa.string(),
            "board": pa.string(),
            "open": pa.float64(),
            "high": pa.float64(),
            "low": pa.float64(),
            "close": pa.float64(),
            "volume": pa.float64(),
            "value": pa.float64(),
        },
        # 지수 종가도 종가다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "지수 일봉. regime Analyst 의 입력. prices 와 섞지 않는다 — "
            "지수가 종목 유니버스에 끼면 커버리지 통계와 횡단면 z 가 오염된다."
        ),
    ),
    "fundamentals": TableSpec(
        name="fundamentals",
        columns={
            "market": pa.string(),
            "metric": pa.string(),
            "value": pa.float64(),
            "fiscal_period": pa.string(),
            "report_type": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "metric"),
        doc="재무. 회계기간 종료일이 아니라 공시일 기준.",
    ),
    "fx": TableSpec(
        name="fx",
        columns={"rate": pa.float64()},
        # 환율도 그날 값이다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc="환율. entity_id 예: FX:USDKRW",
    ),
    "universe": TableSpec(
        name="universe",
        columns={
            "market": pa.string(),
            "name": pa.string(),
            "is_listed": pa.bool_(),
            "is_tradable": pa.bool_(),
            "delisted_on": pa.timestamp("us", tz="UTC"),
        },
        # 명단 스냅샷은 그날 찍는다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc="매 거래일 상장종목 명단 스냅샷. 상폐 종목을 지우지 않는다.",
    ),
    "documents": TableSpec(
        name="documents",
        columns={
            "doc_id": pa.string(),
            "doc_type": pa.string(),
            "title": pa.string(),
            "filer": pa.string(),
            "url": pa.string(),
            "raw_path": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "doc_id"),
        doc=(
            "공시·뉴스. valid_from 은 공시 접수일, observed_at 은 우리가 받아온 시각. "
            "기사 발행시각은 사후 수정되므로 신뢰하지 않는다 (data-contract §4)."
        ),
    ),
    "ingest_latency": TableSpec(
        name="ingest_latency",
        columns={
            "stage": pa.string(),
            "elapsed_ms": pa.float64(),
            "ok": pa.bool_(),
            "detail": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "stage"),
        # 수집한 뒤에 재는 값이다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "파이프라인 단계별 실측 지연. entity_id = 수집 소스. "
            "백테스트 지연은 이 실측의 p90 을 쓴다 — 가정한 지연과 실제 지연의 "
            "차이가 백테스트를 거짓말로 만드는 대표적 원인이다 (data-contract §5)."
        ),
    ),
    "events": TableSpec(
        name="events",
        columns={
            "seq": pa.int64(),
            "stage": pa.string(),
            "actor": pa.string(),
            "payload_hash": pa.string(),
            "payload": pa.string(),
        },
        natural_key=("entity_id", "seq"),
        doc=(
            "이벤트 로그. entity_id = run_id, valid_from = ts_sim, "
            "observed_at = ts_wall. 같은 뜻의 필드를 두 벌 들지 않는다."
        ),
    ),
    "macro_releases": TableSpec(
        name="macro_releases",
        columns={
            "market": pa.string(),
            "indicator": pa.string(),
            "release_name": pa.string(),
            # 발표가 일어나는(또는 일어날) 시각. **valid_from 과 다르다.**
            "scheduled_at": pa.timestamp("us", tz="UTC"),
            "actual": pa.float64(),
            "previous": pa.float64(),
            "unit": pa.string(),
            # scheduled | released
            "status": pa.string(),
        },
        # 같은 발표는 한 행이다. 일정이 먼저 들어오고(actual 없음), 발표 후
        # 실측값이 같은 자연키로 덮는다 — append-only 규칙대로 새 행이 쌓이고
        # 게이트가 최신 것을 고른다.
        natural_key=("entity_id", "scheduled_at"),
        observation_lag_days=3,
        doc=(
            "CPI·PPI 등 거시지표 발표 일정과 실측값. **valid_from 은 우리가 그 "
            "사실을 안 시각이고, 발표 시각은 scheduled_at 이다.** 미래 일정을 "
            "valid_from 에 넣으면 게이트의 lookback 창이 미래를 못 보고, 그러면 "
            "'다가오는 일정'을 영영 못 찾는다."
        ),
    ),
    "market_stats": TableSpec(
        name="market_stats",
        columns={
            "market": pa.string(),
            "metric": pa.string(),
            "value": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "metric"),
        # 그날 통계를 그날보다 먼저 알 수 없다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "일별 시장 관측 — 상장주식수·시가총액. **fundamentals 와 나눈다.** "
            "분기에 4번 바뀌는 공시와 매일 바뀌는 관측을 한 테이블에 두면, "
            "재무를 읽을 때마다 일별 데이터가 딸려 온다. 실제로 그렇게 만들었다가 "
            "fundamentals 가 460만 행(2.2GB)이 되어 Analyst 가 OOM 으로 죽었다."
        ),
    ),
    "sectors": TableSpec(
        name="sectors",
        columns={
            "market": pa.string(),
            "sector": pa.string(),
        },
        # 일별매매(KRX Open API)에만 있다. LS 유니버스(t8436)에는 섹터가 없다
        # (krx_openapi.TRADE_FIELDS 의 SECT_TP_NM). 종목이 업종을 옮기면 그날
        # 부터 새 관측이 새 행으로 쌓인다 — 옛 행을 고치지 않는다(append-only).
        # 과거 시점 조회가 그때의 섹터를 보게 하는 것이 이 테이블의 존재
        # 이유다. 모르는 종목은 행 자체가 없다 — "기타" 로 채우면 그 종목들이
        # 전부 한 섹터가 되어 섹터 상한이 엉뚱하게 걸린다.
        #
        # **자연키에 source 가 들어간다.** 한 종목의 같은 날에 서로 다른
        # 분류체계가 공존한다 — KRX 소속부(`krx_openapi`)와 DART 표준산업분류
        # (`dart_company`)는 경쟁하는 정정본이 아니라 **다른 사실**이다.
        # source 를 빼면 게이트가 둘 중 하나를 정정본으로 보고 읽기에서
        # 지우는데, 파일에는 둘 다 남아 있어서 창고를 봐도 사라진 줄 모른다.
        natural_key=("entity_id", "valid_from", "source"),
        observation_lag_days=3,
        doc=(
            "일별 섹터 관측. selector 의 섹터 상한(selector.md §5-5)이 이걸 "
            "읽는다. entity 마다 매 세션 한 행 — 값이 안 바뀌어도 다시 쌓인다. "
            "\n\n**주의(실측 2026-08-13):** ``SECT_TP_NM`` 은 업종(GICS 류) "
            "분류가 아니라 **소속부**(시장 세부 구분)다. KOSPI 는 빈 문자열만 "
            "주고(942 종목 전부 커버리지 0), KOSDAQ 만 우량기업부·벤처기업부· "
            "중견기업부·기술성장기업부 등으로 갈린다. 즉 지금 섹터 상한은 "
            "**업종 분산이 아니라 KOSDAQ 시장 구분에만** 걸린다 — KOSPI 대형주는 "
            "섹터 미상으로 남아 상한 자체가 적용되지 않는다."
            "\n\n**2026-08-15 갱신:** 진짜 업종 분류를 KRX 에서 받을 수 있는지 "
            "실제 호출로 확인했다 — KRX 정식 Open API(data-dbg.krx.co.kr) 에는 "
            "업종분류 상품 자체가 없다(종목기본정보 3개 엔드포인트 전부 "
            "``SECT_TP_NM`` 뿐). 대신 DART ``company.json`` 의 ``induty_code``"
            "(표준산업분류)로 받았다 — ``source=dart_company`` 로 KR "
            "2,761/2,874종목(96.1%) 적재 완료 (``collectors/dart_sectors.py``). "
            "\n\n**2026-08-22 갱신:** 섹터 상한을 켰다. ``selector/pipeline.py`` 가 "
            "``source=dart_company`` 만 골라 읽고 ``selector/ksic.py`` 로 접는다 — "
            "KSIC 세세분류 그대로면 2,761종목이 535개 코드로 갈려 상한이 영원히 "
            "안 걸린다(중분류군으로 접으면 24개, 최대 군집 19%). ``krx_openapi`` "
            "행은 여전히 상한에 쓰지 않는다."
        ),
    ),
    "signals": TableSpec(
        name="signals",
        columns={
            "analyst": pa.string(),
            "analyst_version": pa.string(),
            "score": pa.float64(),
            "confidence": pa.float64(),
            "horizon_days": pa.int32(),
            "features_hash": pa.string(),
            "evidence_json": pa.string(),
            "latency_ms": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "analyst", "analyst_version"),
        # as_of 시점에 계산한다. 계산이 사실보다 앞설 수 없다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "Analyst 점수. valid_from = as_of(신호가 유효해진 시점), "
            "observed_at = 계산해서 알게 된 시점. analyst_version 이 자연키에 "
            "들어가는 이유는 나쁜 모델의 성적이 좋은 모델에 섞이면 안 되기 "
            "때문이다 — 버전별로 IC 를 따로 잰다."
        ),
    ),
    "verdicts": TableSpec(
        name="verdicts",
        columns={
            "analyst": pa.string(),
            "analyst_version": pa.string(),
            "decision": pa.string(),
            "severity": pa.float64(),
            "category": pa.string(),
            "reason": pa.string(),
            "expires_at": pa.timestamp("us", tz="UTC"),
        },
        natural_key=("entity_id", "valid_from", "analyst", "analyst_version"),
        doc=(
            "News·SNS 판정. 매수 금지만 가능하고 매도 권한은 없다. "
            "expires_at 없는 차단은 스키마가 거부한다 — 영구 차단은 존재할 수 "
            "없다. 차단된 종목의 이후 수익률로 성적표를 만든다."
        ),
    ),
    "short_flow": TableSpec(
        name="short_flow",
        columns={
            "market": pa.string(),
            # **주기가 다른 두 계열을 자연키로 가른다.**
            #   volume    매일. FINRA 통합 공매도 거래량(CNMS)
            #   interest  월 2회. 공매도 잔고
            # 한 계열로 섞으면 반월 값이 발표 사이 구간에서 매일 반복되는데,
            # 그게 피처에서는 "변화 없음" 이 아니라 "관측됨" 으로 읽힌다.
            "kind": pa.string(),
            # kind = volume
            "short_volume": pa.float64(),
            "short_exempt_volume": pa.float64(),
            "total_volume": pa.float64(),
            # kind = interest
            "short_position": pa.float64(),
            "previous_short_position": pa.float64(),
            "days_to_cover": pa.float64(),
            "average_daily_volume": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "kind"),
        # 그날의 공매도 거래량을 그날보다 먼저 알 수는 없다. 늦게 오는 정정본은
        # 하한 위라 안 잘린다. 선언이 없으면 프루닝이 통째로 꺼진다(`flows` 와
        # 같은 사정 — 그때 45일 요청이 1,200개 파티션을 열었다).
        observation_lag_days=3,
        doc=(
            "FINRA 공매도. flow_us Analyst 의 입력. **flows 와 별도 표다** — "
            "거기에 컬럼을 더하면 union_by_name 이 없는 컬럼을 만들어주지 "
            "않아 오래된 구간이 죽는다. 비율(short_volume/total_volume)은 "
            "저장하지 않는다. 분자·분모를 두면 나중에 다르게 물을 수 있다."
        ),
    ),
    # -- RL 학습 (M4) ---------------------------------------------------------
    "rl_updates": TableSpec(
        name="rl_updates",
        columns={
            "update": pa.int32(),
            "step": pa.int64(),
            "seed": pa.int32(),
            "market": pa.string(),
            "curriculum": pa.string(),
            # rl-training.md §10 의 로깅 지표. 이름을 그 표와 맞춘다 —
            # 화면·문서·창고가 같은 말을 써야 "경고선 위/아래" 를 옮겨
            # 적는 곳이 생기지 않는다.
            "explained_variance": pa.float64(),
            "approx_kl": pa.float64(),
            "entropy": pa.float64(),
            "grad_norm": pa.float64(),
            "action_reflection": pa.float64(),
            "policy_churn": pa.float64(),
            "concentration_sum": pa.float64(),
            "episode_reward": pa.float64(),
            "cash_weight": pa.float64(),
            # 재현성 (§11). 이게 없으면 좋은 성적이 나와도 다시 못 만든다.
            "git_commit": pa.string(),
            "config_fingerprint": pa.string(),
        },
        # 같은 run 의 같은 업데이트는 한 번만. 학습을 이어받아 다시 돌리면
        # 같은 번호가 또 나오는데, 그때 두 벌이 쌓이면 곡선이 겹쳐 그려진다.
        natural_key=("entity_id", "update"),
        # entity_id 가 종목이 아니라 **run_id** 다. analyst_weights 와 같은
        # 사정이라 시장 접두어 규칙이 안 맞는다 — 시장은 별도 컬럼이다.
        market_prefixed_entity=False,
        doc=(
            "PPO 학습 업데이트 1회 = 1행. entity_id = run_id. "
            "학습 탭의 explained_variance·학습곡선·approx KL 이 여기서 온다. "
            "**표가 없어서 화면이 비어 있었다** — 학습 코드가 없어서가 "
            "아니었다(2026-08-19). 값을 지어내지 않으므로 학습을 안 돌리면 "
            "0행이고, 0행과 '쟀는데 0' 은 화면에서 다르게 보여야 한다."
        ),
    ),
    "analyst_failures": TableSpec(
        name="analyst_failures",
        columns={
            "market": pa.string(),
            "stage": pa.string(),
            "error_type": pa.string(),
            "detail": pa.string(),
        },
        # entity_id 가 종목이 아니라 **Analyst 이름**이다. rl_updates 와 같은
        # 사정이라 시장 접두어 규칙이 안 맞는다 — 시장은 별도 컬럼이다.
        natural_key=("entity_id", "valid_from", "stage"),
        market_prefixed_entity=False,
        doc=(
            "Analyst 가 그 세션에 예외로 죽었다는 기록. 1행 = 한 Analyst · 한 세션. "
            "**나중에 창고를 읽어 추론할 수 없어서 만든 표다.** 2026-08-20 에 "
            "event·fundamental·regime 이 죽었는데, 같은 날 정정본을 넣자 "
            "signals 는 그날을 6종으로 보여줬다 — 정정본이 사고의 흔적을 지웠다. "
            "verify_m3 가 그 날을 잡을 방법이 없어 상수에 손으로 적어야 했다. "
            "**고쳐도 이 행은 지우지 않는다.** 그날 세션이 반쪽 판단으로 돈 것은 "
            "나중에 고쳐도 달라지지 않는 사실이다."
        ),
    ),
    "analyst_weights": TableSpec(
        name="analyst_weights",
        columns={
            "analyst_version": pa.string(),
            "weight": pa.float64(),
            "ic": pa.float64(),
            "ic_threshold": pa.float64(),
            "sample_days": pa.int32(),
            "passed": pa.bool_(),
            "market": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "analyst_version"),
        # entity_id 가 종목이 아니라 analyst 이름("risk"·"chart"…)이라 시장
        # 접두어 규칙(TableSpec.market_prefixed_entity)이 안 맞는다. market
        # 은 그 analyst 가 어느 시장을 대상으로 측정됐는지를 말하는 별도
        # 값이다 — entity_id 를 파생시킬 수 있는 값이 아니다.
        market_prefixed_entity=False,
        doc=(
            "IC 검증 결과와 그에 따른 가중치. entity_id = analyst 이름. "
            "가중치를 손으로 켜고 끄면 언젠가 검증 안 된 것이 켜져 있다. "
            "통과 여부는 측정 결과에서만 나온다 — 통과 못 하면 weight 0."
        ),
    ),
    # -- 회계 (M3) ------------------------------------------------------------
    #
    # NAV 산출은 ``quant_rl_trading/accounting/`` 한 곳에서만 한다 (accounting.md §8).
    # 이 테이블들은 그 모듈의 입력과 출력이다.
    "trades": TableSpec(
        name="trades",
        columns={
            "market": pa.string(),
            "side": pa.string(),
            "quantity": pa.float64(),
            "price": pa.float64(),
            "currency": pa.string(),
            "fee": pa.float64(),
            "tax": pa.float64(),
            # 체결 하나가 주문 하나가 아니다. 분할 집행이면 여러 행이 같은
            # 주문에서 나온다 — 되돌아가 볼 수 있게 주문 식별자를 남긴다.
            "order_id": pa.string(),
        },
        natural_key=("entity_id", "valid_from", "order_id"),
        # 우리 체결이다. 체결 전에 기록될 수 없다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "체결. **발생주의(trade-date)** — 결제일이 아니라 체결 시점에 "
            "손익·수수료를 인식한다 (accounting.md §1). 수수료와 세금은 "
            "체결가에 녹이지 않고 따로 든다. 녹이면 나중에 비용만 따로 볼 수 없다."
        ),
    ),
    "capital_flows": TableSpec(
        name="capital_flows",
        columns={
            "currency": pa.string(),
            "amount": pa.float64(),
            "kind": pa.string(),
        },
        doc=(
            "입출금. **TWR 계산의 핵심 입력이다** — 이걸 모르면 입금일에 "
            "수익이 난 것으로 오인한다 (accounting.md §6). entity_id 는 계좌."
        ),
    ),
    "dividends": TableSpec(
        name="dividends",
        columns={
            "market": pa.string(),
            "currency": pa.string(),
            "per_share": pa.float64(),
            "tax_rate": pa.float64(),
            # 배당락일 = valid_from. 입금일은 따로 든다 — 인식은 배당락일에
            # 하되(§4) 실제 현금 이동은 이 날짜에 일어난다.
            "pay_date": pa.timestamp("us", tz="UTC"),
        },
        doc=(
            "배당. **valid_from 은 배당락일이다.** 입금일 인식으로 바꾸면 "
            "배당락 하락이 그대로 낙폭이 되어 보상 함수가 손실로 오인한다."
        ),
    ),
    "nav_daily": TableSpec(
        name="nav_daily",
        columns={
            "nav": pa.float64(),
            "inflow": pa.float64(),
            "twr_return": pa.float64(),
            "index_value": pa.float64(),
            "drawdown": pa.float64(),
            "cash_krw": pa.float64(),
            "cash_usd": pa.float64(),
            "equity_kr": pa.float64(),
            "equity_us": pa.float64(),
            "accrued_dividend": pa.float64(),
            "payable": pa.float64(),
            "fx_rate": pa.float64(),
            # 양도세는 일간 NAV 에 넣지 않는다 (§5). 충당금으로만 추적하고
            # 세후 성과를 따로 보고한다.
            "tax_provision": pa.float64(),
            "nav_after_tax": pa.float64(),
            # ⚠️ **가격지수(PR)다, 총수익지수(TR)가 아니다.** TR 은 지금 키로
            # 못 받는다(KRX·LS·FRED 실측, accounting.md §7.1). 우리는 배당을
            # 받고 벤치마크는 못 받으므로 이 컬럼으로 잰 초과수익은 배당수익률
            # 만큼(국내 대형주 연 2~3%p) **우리에게 유리하게 부풀어 있다.**
            # 계산은 accounting/benchmark.py 한 곳에서만 한다.
            "benchmark_index": pa.float64(),
            # 위가 null 인 날의 이유. 없으면 화면이 "데이터가 없었다" 와
            # "벤치마크가 0% 였다" 를 구분하지 못한다.
            "benchmark_note": pa.string(),
        },
        # 그날 장부를 그날 접는다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "일간 회계 스냅샷. 한국시간 15:40 하루 한 번, 미장은 직전 종가 "
            "(accounting.md §2). **낙폭은 NAV 원금액이 아니라 TWR 누적지수로 "
            "계산한다** — 원금액으로 하면 입금이 낙폭을 지운다."
        ),
    ),
    # -- 실행 (M3) ------------------------------------------------------------
    "orders": TableSpec(
        name="orders",
        columns={
            "market": pa.string(),
            "session_id": pa.string(),
            "slice_seq": pa.int32(),
            "side": pa.string(),
            "quantity": pa.float64(),
            "limit_price": pa.float64(),
            "target_weight": pa.float64(),
            "status": pa.string(),
            "reason": pa.string(),
        },
        # **주문 식별자가 자연키다.** (session_id, entity_id, slice_seq) 로
        # 결정론적으로 만들므로, 재시작 후 같은 주문을 다시 만들어도 같은 키가
        # 나오고 창고가 중복을 거부한다 — 그것이 멱등성의 마지막 방어선이다.
        natural_key=("entity_id", "session_id", "slice_seq"),
        # 우리 주문이다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "집행한 주문. 프로세스는 반드시 죽었다 살아난다 — 그때 중복 주문이 "
            "나가지 않게 하는 것이 이 테이블의 존재 이유다."
        ),
    ),
    "killswitch": TableSpec(
        name="killswitch",
        columns={
            "state": pa.string(),
            "reason": pa.string(),
            "triggered_by": pa.string(),
        },
        doc=(
            "킬스위치 latch. **한번 걸리면 사람이 풀기 전까지 유지된다.** "
            "자동 해제를 두면 발동 원인이 남아 있는 채로 다시 매매한다."
        ),
    ),
    "realized_weights": TableSpec(
        name="realized_weights",
        columns={
            "market": pa.string(),
            "session_id": pa.string(),
            "target_weight": pa.float64(),
            "realized_weight": pa.float64(),
        },
        natural_key=("entity_id", "valid_from", "session_id"),
        # 집행 결과다. 늦게 오는 쪽(백필·정정본)은 하한 **위**라 안 잘린다.
        # 선언이 없으면 하한 프루닝이 꺼져 창을 좁혀도 파티션을 전부 연다.
        observation_lag_days=3,
        doc=(
            "목표 비중과 **실제 체결된 비중** (불변식 7). 빠지면 RL 은 자기가 "
            "하지 않은 행동으로 보상받고 학습이 망가진다. 소액 구간에서는 정수 "
            "라운딩 때문에 둘이 크게 벌어진다."
        ),
    ),
    "agent_cache": TableSpec(
        name="agent_cache",
        columns={
            "agent": pa.string(),
            "agent_version": pa.string(),
            "features_hash": pa.string(),
            "output": pa.string(),
            "computed_at": pa.timestamp("us", tz="UTC"),
        },
        natural_key=("entity_id", "valid_from", "agent", "agent_version", "features_hash"),
        doc=(
            "에이전트·LLM 출력 캐시. observed_at 은 계산한 벽시계 시각이 아니라 "
            "as_of 다 — 출력이 as_of 이전 데이터만의 함수이므로 그 시점에 알 수 "
            "있었던 것이 맞고, 벽시계를 찍으면 과거 리플레이에서 영영 안 보인다. "
            "실제 계산 시각은 computed_at 에 따로 남긴다."
        ),
    ),
    "llm_usage": TableSpec(
        name="llm_usage",
        columns={
            "agent": pa.string(),
            "agent_version": pa.string(),
            "model": pa.string(),
            # anthropic 응답의 request_id. 실제 HTTP 왕복 하나를 식별하는
            # 유일한 값이라 자연키에 넣는다 — 없으면 재시도가 같은 행으로 뭉갠다.
            "request_id": pa.string(),
            # 한 번의 호출이 여러 항목(뉴스 여러 건, 지표 여러 건)을 함께
            # 묻는다. 몇 건을 같이 물었는지 남겨야 "호출당 평균 비용" 을 잰다.
            "items": pa.int32(),
            "input_tokens": pa.int64(),
            "output_tokens": pa.int64(),
            "cache_creation_input_tokens": pa.int64(),
            "cache_read_input_tokens": pa.int64(),
            "computed_at": pa.timestamp("us", tz="UTC"),
        },
        natural_key=("entity_id", "valid_from", "agent", "agent_version", "request_id"),
        doc=(
            "LLM 호출 하나(HTTP 왕복)당 실제 토큰 사용량 — 비용을 재기 위한 표다. "
            "``agent_cache`` 는 판정·해설을 **항목별로** 캐싱하는 표라, 한 번의 "
            "호출이 뉴스 여러 건·지표 여러 건을 함께 묻고도 항목 수만큼 행으로 "
            "갈라진다. 토큰을 그 표에 붙이면 배치 크기만큼 비용이 부풀려진다 "
            "— 그래서 별도 표로 뺐다. entity_id = agent 이름. valid_from· "
            "observed_at 은 agent_cache 와 같은 이유로 as_of 다(호출 결과가 "
            "그 시점 데이터만의 함수이므로). 실제 계산 시각(벽시계)은 "
            "computed_at 에 남긴다. 단가는 여기 없다 — store.config 에서 읽는다 "
            "(불변식 10). 이 표가 생기기 전 호출은 기록이 없다. 그건 '0원' 이 "
            "아니라 '몰랐다' 다 — 대시보드가 그 경계를 명시해야 한다."
        ),
    ),
    CONFIG_TABLE: TableSpec(
        name=CONFIG_TABLE,
        columns={"value_json": pa.string()},
        doc=(
            "임계치. 이중시간으로 둔다 — 오늘 임계치를 바꿨다고 작년 백테스트 "
            "결과가 소급해 바뀌면 재현이 불가능해진다."
        ),
    ),
}


def get_spec(table: str) -> TableSpec:
    try:
        return _SPECS[table]
    except KeyError:
        raise UnknownTable(
            f"등록되지 않은 테이블: {table!r}. 등록된 것: {sorted(_SPECS)}"
        ) from None


def table_names() -> list[str]:
    return sorted(_SPECS)
