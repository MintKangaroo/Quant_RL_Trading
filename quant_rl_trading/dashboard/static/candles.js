/* 봉 — **트레이딩 탭과 마켓 탭이 같은 코드로 그린다.**
 *
 * 왜 한 벌인가. 이 저장소에서 두 화면이 같은 것을 각자 그리기 시작하면
 * 반드시 한쪽만 고쳐진 채로 남는다 — 수익률 캘린더가 그랬고(calendar.js 로
 * 합쳤다), 봉도 같은 길을 갈 참이었다. 트레이딩 탭에는 이미 1m/5m/15m/1H/4H/1D
 * 전환이 있었고 마켓 탭 패널은 선 하나였다. 마켓 탭에 봉을 붙이면서 두 번째
 * 캔들 렌더러를 짜는 대신, 있던 것을 여기로 끌어내 둘이 나눠 쓴다.
 *
 * 나눠 쓰는 것은 셋이다.
 *   ① 구간의 이름과 순서 — 버튼 이름이 두 화면에서 갈리지 않게.
 *   ② 버튼을 언제 끄나 — "없는 봉을 그리지 마라" 의 반대쪽.
 *   ③ 봉을 어떻게 그리나 — 색·OHLC 순서·분봉 x축 라벨·툴팁.
 *
 * 화면마다 다른 것은 **크기와 곁가지**뿐이다(``compact``): 마켓 패널은
 * 108px 짜리 미니라 거래량·이동평균·확대손잡이를 얹을 자리가 없고, 트레이딩
 * 탭의 큰 차트는 그 셋이 본론이다. 그 차이만 분기하고 나머지는 한 길이다.
 */

/* 짧은 것 → 긴 것. 서버의 ``market.PANEL_INTERVALS`` 와 **같은 순서**다. */
const CANDLE_TIMEFRAMES = ["1m", "5m", "15m", "1H", "4H", "1D", "1W"];

/* 분봉인가. 일봉·주봉과 x축 라벨이 다르다(날짜 vs 시각). */
function isIntradayInterval(interval) {
  return interval !== "1D" && interval !== "1W";
}

function timeframeLabel(interval) {
  if (interval === "1D") return "일봉";
  if (interval === "1W") return "주봉";
  if (interval === "1H") return "1시간봉";
  if (interval === "4H") return "4시간봉";
  return `${interval}봉`; // "1m"·"5m"·"15m"
}

/* 꺼진 버튼의 title. **왜 꺼졌는지 화면이 말해야 한다** — 이유가 없으면
 * 사용자는 고장으로 읽고, 실제 원인(수집 대상이 아니다)에서 멀어진다. */
function timeframeOffReason(interval) {
  if (!isIntradayInterval(interval)) return `${timeframeLabel(interval)}을 그릴 시세가 없다`;
  return `이 종목의 ${timeframeLabel(interval)}이 창고에 없다 — 분봉 수집은 보유·워치리스트·shadow 보유 종목만 받는다`;
}

/* 버튼 상태를 **서버가 방금 알려준 사실**로 맞춘다. 여기서 추측하지 않는다 —
 * 있다고 짐작하고 켰다가 눌렀을 때 빈 화면이 뜨면, 그게 고장인지 원래 수집
 * 범위 밖인지 사용자가 구분 못 한다.
 *
 * ``seg`` 은 button[data-interval] 들을 담은 요소다. 없으면 조용히 넘어간다. */
function syncTimeframeSeg(seg, available, current) {
  if (!seg || !seg.querySelectorAll) return;
  const have = new Set(available || []);
  seg.querySelectorAll("button[data-interval]").forEach((button) => {
    const interval = button.dataset.interval;
    const on = have.has(interval);
    button.disabled = !on;
    button.title = on ? `${timeframeLabel(interval)}으로 보기` : timeframeOffReason(interval);
    if (button.classList) button.classList.toggle("on", interval === current);
  });
}

/* 봉 하나. **넷이 다 있을 때만 캔들이다** — 없는 시가·고가·저가를 종가로
 * 채우면 모든 봉이 십자가가 되는데, 그건 "그날 변동이 없었다" 는 다른
 * 사실이다(미장 지수는 FRED 라 종가만 있다). 못 그리면 선으로 긋고 화면이
 * 이유를 적는다.
 *
 * 상승 초록 · 하락 빨강. ECharts 의 color 가 양봉, color0 이 음봉이다 —
 * 뒤집으면 이 대시보드의 다른 화면과 색이 갈린다(dashboard.md §10). */
function candleSeries(c, opts) {
  const options = opts || {};
  const hasOhlc = c.has_ohlc !== false && (c.ohlc || []).length > 0;
  if (!hasOhlc) {
    const tone = options.tone || COLOR.muted;
    return {
      name: "봉", type: "line", data: c.closes || [], showSymbol: false,
      lineStyle: { color: tone, width: 1.6 },
      areaStyle: { color: tone, opacity: 0.08 },
    };
  }
  return {
    // ECharts 캔들 데이터 순서는 [시가, 종가, 저가, 고가] 다. 서버가 그
    // 순서로 만들어 보낸다(services 의 ohlc 주석) — 여기서 다시 섞지 않는다.
    name: "봉", type: "candlestick", data: c.ohlc,
    itemStyle: {
      color: COLOR.up, color0: COLOR.down,
      borderColor: COLOR.up, borderColor0: COLOR.down,
      ...(options.compact ? { borderWidth: 1 } : {}),
    },
    ...(options.marks ? { markPoint: { data: options.marks } } : {}),
    ...(options.guides ? { markLine: { symbol: "none", data: options.guides, silent: true } } : {}),
  };
}

/* 분봉의 session 문자열은 날짜만이 아니라 시각까지 든 ISO 다(서버의
 * intraday_candles). 그대로 축에 찍으면 라벨이 너무 길어 서로 겹친다 —
 * "HH:MM" 만 자른다. 어느 날짜인지는 툴팁(원문 그대로)이 말한다. */
function candleAxisLabel(interval) {
  return isIntradayInterval(interval)
    ? { ...AXIS.axisLabel, formatter: (value) => String(value).slice(11, 16) }
    : AXIS.axisLabel;
}

/* 미니 패널의 툴팁. 큰 차트는 legend 와 축이 값을 말해 주지만 미니는
 * 아무것도 안 적혀 있어서, 여기서만 OHLC 를 글자로 편다. */
function candleTooltipFormatter(c, label) {
  return (rows) => {
    if (!rows || !rows.length) return "";
    const row = rows[0];
    const head = `${String(row.axisValue)}`;
    if (c.has_ohlc === false || !(c.ohlc || []).length) {
      return `${head}<br>${label} <b>${dec(row.value, 2)}</b>`;
    }
    // 캔들의 value 는 [index, 시가, 종가, 저가, 고가] 다.
    const v = row.value;
    return `${head} <b>${label}</b><br>`
      + `시 ${dec(v[1], 2)} · 고 ${dec(v[4], 2)}<br>`
      + `저 ${dec(v[3], 2)} · 종 ${dec(v[2], 2)}`;
  };
}

/* 봉 차트 한 판. 두 화면이 이 함수 하나를 부른다.
 *
 * opts:
 *   interval  — 무슨 봉인가. x축 라벨 규칙이 여기서 갈린다.
 *   compact   — 미니 패널(마켓 탭). 거래량·이동평균·확대손잡이를 빼고 축만 남긴다.
 *   tone      — 캔들을 못 그릴 때 선의 색(미니 전용).
 *   label     — 툴팁에 적는 이름(미니 전용).
 *   marks     — 체결 흔적 markPoint (일봉 전용, 트레이딩 탭).
 *   guides    — 평균단가 markLine (트레이딩 탭).
 */
function candleOption(c, opts) {
  const o = opts || {};
  const interval = o.interval || "1D";
  const axisLabel = candleAxisLabel(interval);
  const bars = candleSeries(c, o);

  if (o.compact) {
    return {
      ...BASE,
      legend: { show: false },
      grid: { left: 46, right: 8, top: 8, bottom: 18 },
      xAxis: {
        type: "category", data: c.sessions,
        // 캔들은 눈금 사이에 서고 선은 눈금 위에 선다. 섞으면 봉이 축
        // 바깥으로 반쯤 밀린다.
        boundaryGap: bars.type === "candlestick",
        ...AXIS, axisLabel,
      },
      // scale:true 가 필수다. 0 부터 그리면 봉이 위쪽 몇 %에 눌린다.
      yAxis: { type: "value", scale: true, ...AXIS },
      tooltip: { ...BASE.tooltip, formatter: candleTooltipFormatter(c, o.label || "") },
      series: [bars],
    };
  }

  // 처음에는 최근 120봉만 보여준다. 5년을 한 화면에 밀어 넣으면 봉이 선이
  // 되어 아무것도 안 보인다. 나머지는 스크롤로 간다.
  const span = (c.sessions || []).length;
  const startPct = span > 120 ? Math.max(0, (1 - 120 / span) * 100) : 0;
  return {
    ...BASE,
    legend: { ...BASE.legend, data: ["봉", "MA5", "MA20", "MA60"] },
    // 높이를 비율로 주면 봉 영역이 패널 높이에 따라 접힌다 — 실제로 봉이
    // 세로로 눌려 보였다. 거래량(56px)과 손잡이(24px)는 고정 크기이므로
    // 픽셀로 잡고, 남는 세로는 전부 봉이 가져간다.
    grid: [
      { left: 56, right: 62, top: 26, bottom: 108 },
      { left: 56, right: 62, height: 56, bottom: 46 },
    ],
    // 좌우 스크롤·확대축소. 두 grid 를 함께 움직여야 봉과 거래량이 어긋나지
    // 않는다. inside 는 휠·드래그, slider 는 아래 손잡이다.
    dataZoom: [
      {
        type: "inside", xAxisIndex: [0, 1], start: startPct, end: 100,
        zoomOnMouseWheel: true, moveOnMouseMove: true,
        // filterMode "filter" 라야 보이는 구간만 남아 Y축이 그 구간으로
        // 다시 잡힌다. "none" 이면 5년 최고가에 눌려 최근 봉이 납작해진다.
        filterMode: "filter",
      },
      {
        type: "slider", xAxisIndex: [0, 1], start: startPct, end: 100,
        filterMode: "filter",
        bottom: 6, height: 18,
        backgroundColor: "transparent",
        borderColor: COLOR.border,
        fillerColor: token("--chart-ma20-fill"),
        handleStyle: { color: COLOR.muted, borderColor: COLOR.border },
        moveHandleStyle: { color: COLOR.border },
        textStyle: { color: COLOR.dim, fontSize: 10, fontFamily: "IBM Plex Mono" },
        dataBackground: {
          lineStyle: { color: COLOR.dim, opacity: 0.5 },
          areaStyle: { color: COLOR.dim, opacity: 0.15 },
        },
      },
    ],
    xAxis: [
      { type: "category", data: c.sessions, gridIndex: 0, ...AXIS, axisLabel },
      { type: "category", data: c.sessions, gridIndex: 1, axisLabel: { show: false },
        axisLine: AXIS.axisLine, splitLine: { show: false } },
    ],
    yAxis: [
      // scale + 확대 구간 기준 재계산. 없으면 5년 최고가에 눌려 최근 봉이
      // 납작해진다.
      { type: "value", scale: true, ...AXIS, gridIndex: 0 },
      { type: "value", gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false },
        axisLine: AXIS.axisLine },
    ],
    series: [
      bars,
      { name: "MA5", type: "line", data: c.ma.ma5, showSymbol: false,
        lineStyle: { width: 1, color: COLOR.warn } },
      // MA20 은 토큰에 없는 색이라 trading.css 에 --chart-ma20 로 따로 선언했다
      // (다른 이동평균과 헷갈리지 않게 하려면 warn/up/down 재사용이 아니라
      // 전용 색이 있어야 한다).
      { name: "MA20", type: "line", data: c.ma.ma20, showSymbol: false,
        lineStyle: { width: 1, color: token("--chart-ma20") } },
      { name: "MA60", type: "line", data: c.ma.ma60, showSymbol: false,
        lineStyle: { width: 1, color: COLOR.muted } },
      { name: "거래량", type: "bar", data: c.volume, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: COLOR.border } },
    ],
  };
}
