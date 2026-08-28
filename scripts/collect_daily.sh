#!/usr/bin/env bash
# 일일 수집 — 시세·유니버스·수급·공시·지수·거시지표·환율을 창고에 채운다.
#
#   scripts/collect_daily.sh KR
#
# **이 스크립트가 없어서 창고가 이틀 낡아 있었다.** run_daily.sh 는 점수만
# 내고 수집하지 않는다. 수집은 백필 도구를 사람이 돌릴 때만 들어왔고, 그래서
# 2026-08-12 이후 시세가 멈춘 채 shadow 가 낡은 값으로 돌았다.
#
# 순서가 있다. 수집 → run_daily(16:10) → run_shadow(16:30) 이다. 게이트가
# observed_at <= as_of 로 거르므로 수집이 늦으면 그날 데이터가 아예 안 보인다
# — 버그가 아니라 규칙대로인데, 순서를 틀리면 조용히 0건이 된다.
#
# 최근 3거래일을 다시 받는다. 하루만 받으면 연휴·장애로 하루를 놓쳤을 때
# 영영 빈칸으로 남는다. 이미 받은 세션은 매니페스트가 건너뛴다(멱등).
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1

MARKET="${1:-KR}"
SESSIONS="${SESSIONS:-3}"
LOG="logs/collect-$(date +%Y%m).log"

export QUANT_RL_DUCKDB_MEMORY_LIMIT="${QUANT_RL_DUCKDB_MEMORY_LIMIT:-2GB}"
export QUANT_RL_DUCKDB_THREADS="${QUANT_RL_DUCKDB_THREADS:-2}"

{
    echo "=== $(date '+%F %T') market=${MARKET} sessions=${SESSIONS} ==="

    # 1. 시세 + 유니버스. --table 을 안 주면 이 둘이다.
    #
    #    **국장만 이 도구를 쓴다.** 미장 시세는 날짜 축이 아니라 **종목 축**
    #    이라(LS g3204 는 심볼 하나의 기간을 준다) `--sessions` 가 먹지 않고,
    #    `--market US` 는 6,648종목 × 8배치를 통째로 다시 도는 전체 백필이
    #    된다 — 하루 한 번 도는 스크립트에서 부를 수 있는 물건이 아니다.
    #    미장은 바로 아래 1-1 의 전용 증분 도구가 받는다.
    if [ "${MARKET}" = "KR" ]; then
        .venv/bin/python tools/backfill.py --market "${MARKET}" --sessions "${SESSIONS}"
        echo "  시세·유니버스 rc=$?"

        # 상장주식수·시가총액. **위 한 줄에 안 딸려 온다** — `--table` 을 안 주면
        # 시세와 유니버스 둘뿐이고 `shares` 패널(OPENAPI_PANELS)은 따로 불러야
        # 한다. 그래서 시세는 매일 들어오는데 시총만 조용히 멈춰 있었다:
        # 실측 2026-08-18 시세 08-14 · 시총 08-11 (3세션 결손).
        #
        # 마켓 탭의 시총 순위표·트리맵이 이걸 읽는다. 없으면 표가 통째로 비고,
        # 화면은 "상장주식수가 없다"(= 수집기가 없다)고 말한다 — 있는 것을
        # 없다고 말하는 문구라 원인을 엉뚱한 데서 찾게 된다.
        .venv/bin/python tools/backfill.py \
            --market "${MARKET}" --table shares --sessions "${SESSIONS}"
        echo "  시가총액 rc=$?"
    fi

    # 1-1. 미장 시세. **여기 없어서 매번 며칠씩 밀려 있었다** — 실측 2026-08-18
    #      국장 08-14 / 미장 08-12. 국장은 위 한 줄이 매일 받아 주는데 미장은
    #      받는 경로가 전체 백필뿐이라 사람이 기억할 때만 들어왔다.
    #
    #      **오래 걸린다 — 종목당 1호출, 6,647종목에 약 111분이다.** 미장에는
    #      국장 `t8407`(복수종목 현재가) 같은 다중조회 TR 이 없다. 실측으로
    #      확인했다(2026-08-18): g3104·g3106·g3102·g3202·g3204 전부 종목 단건,
    #      g3190 은 InBlock 조합 5가지 모두 `00009 해당 자료가 없습니다`.
    #      그래서 줄일 수 있는 것은 호출 수가 아니라 **구간**이고, 최근 며칠만
    #      받으면 종목당 1호출로 끝난다 (전체 백필은 종목당 4~8호출).
    #
    #      **첫 실행만 1.4배 든다.** 거래소(나스닥/뉴욕)를 모르면 한 번 치고
    #      0행이면 다른 쪽으로 다시 친다 — LS 는 거래소가 틀려도 오류가 아니라
    #      0행을 준다(실측). 맞힌 값은 data/backfill/us_exchanges.json 에 남아
    #      이튿날부터 항상 1호출이다. 이 파일을 지우면 그날 45분이 더 든다.
    #      `--source sec` 으로 한 번 돌리면 SEC 명단이 거래소를 같이 줘서
    #      캐시가 한 번에 차고, **신규 상장 종목도 그때 들어온다** (기본값
    #      `--source store` 는 이미 창고에 있는 종목만 최신으로 유지한다).
    #
    #      **이 스크립트가 08:40 에 도는 것이 전제다.** 미장 마감은 05:20 KST
    #      (공표 정책), run_daily 는 16:10 이라 그 사이 두 시간은 여유가 있다.
    #      급할 때는 `US_TOP=500` 으로 거래대금 상위만 먼저 받을 수 있지만,
    #      그건 **부분 수집이라 나머지 종목의 그날 세션이 비게 된다** — 창고에
    #      남는 표지도 다르다(run_id 에 `top500`). 임시 조치로만 쓴다.
    #
    #      종료코드는 행 수가 아니라 **창고가 몇 세션 밀렸나**로 정해진다.
    #      0행은 "이미 다 받았다" 와 "고장났다" 를 구분하지 못한다 (환율이
    #      그 문구로 11일을 숨긴 적이 있다).
    if [ "${MARKET}" = "US" ]; then
        .venv/bin/python tools/collect_us_prices.py \
            --sessions "${SESSIONS}" ${US_TOP:+--top "${US_TOP}"}
        echo "  미장 시세 rc=$?"
    fi

    # 2. 수급. **날짜축(KRX)이다** — 종목축(LS)은 991종목을 한 종목씩 받아
    #    하루에 4시간이 든다. 이쪽은 주체별 한 콜씩, 전 종목이 한 번에 온다.
    if [ "${MARKET}" = "KR" ]; then
        .venv/bin/python tools/backfill.py --market KR --table flows --sessions "${SESSIONS}"
        echo "  수급 rc=$?"
    fi

    # 2-1. DART 공시. **여기 없어서 유니버스 필터가 눈을 감고 있었다** —
    #      지수·fx 와 같은 사고다. selector/filters.py 의 distressed() 가
    #      관리종목·불성실공시·거래정지·회생절차를 이 표(documents)에서만 읽는데,
    #      수집이 수동 백필뿐이라 마지막 백필 이후 지정된 종목은 영영 안 보였고
    #      필터는 그 종목을 "정상" 으로 통과시켰다 (2026-08-15 발견).
    #
    #      **--sessions 는 달력일이다** — 이 표의 축이 거래일이 아니다. 공시는
    #      휴장일에도 접수되고, 거래일로 자르면 그 공시를 영영 못 받는다.
    #      7일을 주는 이유는 주말·연휴가 창 안에 통째로 들어와야 하기
    #      때문이다(SESSIONS=3 이면 금요일 것이 월요일에 이미 창 밖이다).
    #
    #      **--years 로 부르면 안 된다.** 공시가 없는 날은 남길 배치가 없어
    #      매니페스트에 안 남고 영원히 "남은" 채로 있는다 — 1년 창이면 그
    #      244일(주말·연휴)을 매일 다시 물어보게 된다.
    #
    #      로그의 "미공표 대기" 는 정상이다. DART 는 접수 **시각**을 안 줘서
    #      관측시각을 그날 18:00 KST 로 잡는데(backfill.dart_publication_hour_kst),
    #      이 실행은 15:55 이다. 당일치는 대기로 남고 22:40 실행이 받는다.
    if [ "${MARKET}" = "KR" ]; then
        .venv/bin/python tools/backfill.py \
            --market KR --table documents-dart --sessions "${DART_DAYS:-7}"
        echo "  공시 rc=$?"
    fi

    # 2-2. 미장 상장주식수·시가총액. **국장에는 없는 단계다** — 국장은 KRX
    #      일별매매가 시세와 같은 콜에서 LIST_SHRS·MKTCAP 을 주지만, 미장은
    #      시세 소스(LS g3204)도 종목마스터(g3101)도 주식수를 주지 않는다.
    #      그래서 SEC EDGAR 에서 따로 받아 곱한다.
    #
    #      두 단계의 주기가 다르다.
    #      - shares-sec : **주 1회로 충분하다.** 상장주식수는 분기 공시라
    #        매일 바뀌지 않는다. 다만 회사마다 결산월이 달라 새 공시는 매주
    #        들어오므로 분기 1회로는 최대 석 달 묵는다. 실행 id 에 ISO 주차가
    #        박혀 있어 **매일 불러도 그 주 첫 실행만 실제로 돈다** — 별도
    #        크론을 두지 않는 이유다 (us_shares.refresh_stamp).
    #      - market-cap : 매일. 시세가 매일 바뀌므로 시가총액도 매일 새로
    #        만든다. 이미 넣은 세션은 매니페스트가 건너뛴다.
    if [ "${MARKET}" = "US" ]; then
        #      **의존 순서가 있다: 시세 → 명단 → 상장주식수 → 시가총액.**
        #      미장 명단은 시세에서 유도하고(us-universe), 상장주식수는 "시세가
        #      있는 종목만" 대상으로 삼으며(backfill.py), 시가총액은 주식수×종가다.
        #      순서를 어기면 각 단계가 조용히 0행으로 끝난다.
        #
        #      명단이 빠져 있었다(2026-08-18 발견). 시세만 매일 새로 받고
        #      명단은 백필 때만 갱신돼서, **새로 상장된 종목이 영영 안 들어왔다.**
        #      실측: 시세 6,647종목인데 명단은 2026-08-12 에 멈춰 있었다.
        .venv/bin/python tools/collect_us_prices.py --sessions "${SESSIONS}"
        echo "  미장 일봉 rc=$?"
        #      **`--sessions` 를 준다.** 안 주면 5년(약 1,250세션)을 통째로
        #      훑는다 — 매일 도는 자리에 둘 물건이 아니다. 짧은 창에서는
        #      **상폐 판정을 건너뛴다**(근거가 창 밖이라 오인한다). 상폐는
        #      아래 주 1회 전체 창이 맡는다.
        .venv/bin/python tools/backfill.py \
            --market US --table universe --sessions "${SESSIONS}"
        echo "  미장 명단 rc=$?"
        .venv/bin/python tools/backfill.py --market US --table shares-sec
        echo "  미장 상장주식수 rc=$?"
        #      **--sessions 를 반드시 준다.** 없으면 5년 전 구간을 다시 훑는데,
        #      prices 는 관측지연을 선언하지 않아 창을 좁혀도 파티션을 전부
        #      연다 — 그 비용이 매일 붙는다. 백필은 인자 없이 따로 돌린다.
        .venv/bin/python tools/backfill.py \
            --market US --table market-cap --sessions "${SESSIONS}"
        echo "  미장 시가총액 rc=$?"
        .venv/bin/python tools/collect_indices_us.py
        echo "  미장 지수(Yahoo) rc=$?"
    fi

    # 2-3. 기업행위 조정계수. **공시 단계 뒤에 와야 한다** — 후보를 그 표
    #      (documents)에서 고르기 때문이다. 순서를 바꾸면 오늘 난 권리락을
    #      내일까지 못 본다.
    #
    #      창고에는 원주가가 든다. 액면분할·무상증자·감자·주식병합이 보정되지
    #      않으면 실제 손실이 아닌 가격 급변이 수익률이 되고, 모멘텀 창이
    #      250일이면 사건 하나가 그 뒤 250세션을 오염시킨다. 5년 국장에서
    #      유니버스의 4분의 1이 닿는다 (collectors/corporate_actions.py).
    #
    #      **급락 감시로는 못 잡는다.** 5% 무상증자는 가격이 5% 내려갈 뿐이다.
    #      그래서 감시가 아니라 공시로 후보를 잡고 LS 로 배율을 확정한다.
    #
    #      보통 0~3종목이라 30초면 끝난다. 사건이 없는 날은 쓸 것도 없다.
    #      스캔 파일은 매일 덮어쓴다 — 남겨 두면 --daily 가 어제 결과를 보고
    #      오늘 사건을 건너뛴다.
    if [ "${MARKET}" = "KR" ]; then
        SCAN="logs/adjfactor-daily-KR.json"
        rm -f "${SCAN}"
        .venv/bin/python tools/scan_corporate_actions.py \
            --daily --market KR --out "${SCAN}"
        echo "  기업행위 스캔 rc=$?"
        .venv/bin/python tools/backfill_adj_factor.py --scan "${SCAN}" --market KR --daily
        echo "  기업행위 적재 rc=$?"
    fi

    # 3. 지수. **여기 없어서 조용히 낡아 있었다** — fx 와 같은 사고다.
    #    시세는 08-14 까지 들어와 있는데 지수는 08-11 에서 멈춰 있었다
    #    (2026-08-15 발견). 화면의 수익률 캘린더·지수 대비 패널이 이걸 읽는다.
    #
    #    두 패널을 다 부른다. 경로가 달라 응답이 겹치지 않는다.
    #    - indices-krx   : KRX 통합지수 40개 (KRX 300·KRX 100 …). regime 의 입력
    #    - indices-board : 시장 대표지수 (코스피·코스닥). "지수 대비" 패널
    #    **가격지수 · 배당 미반영 — 우리에게 유리하다.** 총수익지수는 유료
    #    라이선스라 지금 키로는 못 받는다.
    if [ "${MARKET}" = "KR" ]; then
        for PANEL in indices-krx indices-board; do
            .venv/bin/python tools/backfill.py \
                --market KR --table "${PANEL}" --sessions "${SESSIONS}"
            echo "  지수(${PANEL}) rc=$?"
        done
        # 오늘 지수는 KRX 가 내일 오후에야 준다 — LS t1511 로 오늘 종가를 먼저 적는다.
        # 없으면 23:05 shadow 의 벤치마크가 매일 null 로 시작한다.
        .venv/bin/python tools/collect_indices_ls.py
        echo "  지수(LS t1511) rc=$?"
    fi

    # 4. 거시지표. 발표 일정과 실측값 — 미장은 21:30 KST 발표라 저녁 실행이
    #    그날 것을 잡는다. 미장 지수(S&P500·나스닥)도 여기서 같이 들어온다
    #    (collect_macro 의 IndexCollector). 역시 가격지수다.
    .venv/bin/python tools/collect_macro.py
    echo "  거시 rc=$?"

    # 5. 환율. **여기 없으면 회계가 멈춘다** — NAV 는 환율 없이 계산을 거부한다
    #    (accounting). 예전에 fx 가 0행이라 회계가 테스트 위에서만 돌던 적이
    #    있고, 1,400행을 백필한 뒤로는 **갱신하는 사람이 없어 7일이 밀려 있었다**
    #    (2026-08-14 발견). 백필 도구를 그대로 쓰되 창을 짧게 준다.
    #    인자 없이 부르면 "30일 전 ~ 어제" 다. 같은 구간 run_id 는 결정론적이라
    #    창고가 중복을 거부하므로 매일 다시 받아도 안전하다.
    .venv/bin/python tools/collect_fx.py
    echo "  환율 rc=$?"
    .venv/bin/python tools/collect_fx_yahoo.py
    echo "  환율(Yahoo) rc=$?"
} >>"${LOG}" 2>&1
