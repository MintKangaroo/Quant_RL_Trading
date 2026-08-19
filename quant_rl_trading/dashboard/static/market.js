/* 마켓 — 지금 시장이 어떤 상태인가.
 *
 * **왼쪽 국장 · 오른쪽 미장.** 두 칸은 같은 렌더러를 시장 코드만 바꿔 두 번
 * 돌린다 — 시장마다 따로 그리면 어느 한쪽에만 패널이 늘고, 그러면 좌우 밀도가
 * 갈라진다. 여기서 시장 이름으로 분기하는 곳은 **없는 데이터의 이유를 적는
 * 문구 하나뿐**이다(미장 시가총액).
 *
 * 공통 규약은 scope.js 에 있다. 폴링은 dashboard.md §8 대로 장중 5초 /
 * 장외 1분인데, 이 화면은 시황이지 체결 화면이 아니라서 1분으로 고정한다 —
 * 초 단위로 지수를 흔들면 화면이 시세창처럼 보이고, 이 화면의 질문은
 * "지금 얼마인가" 지 "지금 이 순간" 이 아니다.
 */

const REFRESH_MS = 60_000;

/* (응답 키, DOM 접미사). 칸을 늘릴 일은 없지만, 늘린다면 여기 한 줄이다. */
const COLUMNS = [
  ["KR", "kr"],
  ["US", "us"],
];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function isLive() {
  return !new URLSearchParams(window.location.search).get("as_of");
}

const signClass = (v) => (v === null || v === undefined ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
/* 부호를 색만으로 말하지 않는다. 색각 이상에서 초록·빨강은 같은 회색이다. */
const arrow = (v) => (v === null || v === undefined || v === 0 ? "" : v > 0 ? "▲ " : "▼ ");
const signed = (v) => (v === null || v === undefined ? "—" : arrow(v) + pct(v));

/* 금액. **원과 달러를 같은 단위로 찍지 않는다** — 통화를 응답이 들고 온다.
 * 조 단위부터는 억으로 적으면 자릿수가 읽히지 않아 단위를 올린다. */
function money(value, currency) {
  if (value === null || value === undefined) return "—";
  const unit = currency === "USD" ? "달러" : "원";
  if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(1)}조${unit}`;
  return `${num(Math.round(value / 1e8))}억${unit}`;
}

function stamp(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.valueOf())) return "—";
  return at.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

/* 지수 entity_id 에서 사람이 읽는 이름만 뗀다. "KR:IDX:KRX TMI" → "KRX TMI". */
function indexLabel(entityId) {
  const idx = String(entityId).indexOf("IDX:");
  return idx === -1 ? String(entityId) : String(entityId).slice(idx + 4);
}

/* 종목 코드에서 시장 접두어를 뗀다. "US:AAPL" → "AAPL". */
function tickerLabel(entityId) {
  const parts = String(entityId).split(":");
  return parts.length > 1 ? parts.slice(1).join(":") : String(entityId);
}

/* 이름 옆 코드. **이름이 곧 코드인 시장에서는 안 적는다** — 미장 유니버스의
 * name 은 티커 그대로라(AAPL/AAPL) 같은 글자를 두 번 쓰게 된다. */
function codeCell(entityId, name) {
  const code = tickerLabel(entityId);
  return code === name ? "" : `<span class="code">${esc(code)}</span>`;
}

/* -- 공통 줄: 두 시장의 대표 지수 + 환율 ------------------------------------- */

/* 줄의 순서가 곧 화면의 순서다 — **국장 왼쪽 · 환율 가운데 · 미장 오른쪽.**
 * 환율을 끝에 두면 두 시장 사이의 다리라는 것이 안 읽힌다. */
function marketKpis(body, code) {
  const panel = body.data.markets[code];
  // 그 칸의 첫 자리는 **한 곳**(instrument_panels)에서만 읽는다. 화면이 두
  // 군데서 따로 고르면 언젠가 서로 다른 것을 대표라 부른다.
  const panels = panel.instrument_panels;
  const head = panels.panels.find((row) => row.role === "primary");
  const spec = head || panels.missing.find((row) => row.role === "primary") || {};
  const b = panel.breadth;
  const label = code === "KR" ? "국장" : "미장";
  // **장중 값이 있으면 그것을 크게 둔다.** 없으면 종가다.
  //
  // 실측 2026-08-19 12:31: 화면이 코스피 6,869.83(-1.55%)을 보여주고 있었는데
  // 그건 **전일 종가**였고 그때 실시간은 6,488.37(-5.55%) 였다. 종가를 오늘
  // 값처럼 보여주는 것이 이 화면에서 가장 비싼 거짓이다.
  const liveOn = head && head.live_price !== null && head.live_price !== undefined;
  const level = liveOn ? head.live_price : (head ? head.close : null);
  const move = liveOn ? head.live_change : (head ? head.change : null);
  return [
    kpi(
      `${label} 대표 · ${esc(spec.label || "—")}`,
      head ? dec(level, 2) : "—",
      head
        ? `${arrow(move)}${pct(move)}${liveOn ? " · 장중" : " · 종가"}`
        : "창고에 없다",
      false,
      { tone: head ? signClass(move) : "", spark: head ? head.closes : null }
    ),
    // 지수 하나로는 "지수는 올랐는데 종목의 70%는 내렸다" 를 볼 수 없다.
    kpi(
      `${label} 상승·하락`,
      b.traded ? `${num(b.advancers)} : ${num(b.decliners)}` : "—",
      b.traded ? `거래대금 ${money(b.value, b.currency)}` : "시세 없음",
      false
    ),
  ];
}

function renderKpis(body) {
  const f = body.data.fx;
  const cards = [
    ...marketKpis(body, "KR"),
    kpi(
      "원달러",
      f.rate === null ? "—" : num(Math.round(f.rate)),
      f.change === null ? "환율 없음" : `${arrow(f.change)}${pct(f.change)}`,
      false,
      { tone: signClass(f.change), spark: f.rates }
    ),
    ...marketKpis(body, "US").reverse(),
  ];
  document.getElementById("kpis").innerHTML = cards.join("");
}

/* -- 지수 · ETF 개별 패널 ------------------------------------------------------ */

/* **가격지수 배지는 지수 패널에만.** 미장 패널은 ETF 라 배당이 아니라
 * 분배금·보수·괴리가 문제이고, 그건 아래 etfCaveats() 가 적는다. */
function prBadge(body, panel) {
  if (panel.kind !== "index" || body.data.total_return) return "";
  return `<span class="chip warn-chip" title="총수익지수(TR)를 못 구한다 — 배당만큼 우리가 유리하게 보인다">가격지수 · 배당 미반영</span>`;
}

/* 이 패널이 무엇인지. **벤치마크와 화면 대표는 다른 것이라 배지도 다르다.**
 * SPY 는 미장 칸의 첫 자리지만 우리가 견줘 평가받는 대상이 아니다 — 벤치마크는
 * 여전히 config.benchmark 가 정한 지수다. 한 배지로 뭉치면 언젠가 화면 사정으로
 * 벤치마크가 갈아치워진다. */
function roleBadge(panel) {
  if (panel.benchmark) {
    return `<span class="chip bench-chip" title="config.benchmark 가 정한 지수 — 회계·백테스트가 우리를 견주는 그것이다">벤치마크</span>`;
  }
  if (panel.role === "primary") {
    return `<span class="chip" title="이 칸의 첫 자리일 뿐이다 — 벤치마크가 아니다(벤치마크는 config.benchmark 가 정한 지수)">화면 대표</span>`;
  }
  return "";
}

/* 지수인지 ETF 인지. **ETF 는 제목이 티커이고, 좇는 지수는 여기 부제로만
 * 적는다** — 제목이 지수 이름이면 그게 대용치 바꿔치기다. */
function kindBadge(panel) {
  if (panel.kind !== "etf") {
    return `<span class="chip">지수</span>`;
  }
  return `<span class="chip etf-chip" title="ETF 는 지수가 아니다 — 분배금·보수·시장가 괴리만큼 어긋난다">ETF${
    panel.tracks ? ` · ${esc(panel.tracks)} 추종` : ""
  }</span>`;
}

/* **이 교체가 공짜가 아닌 지점.** 칸마다 한 번만 적는다 — 패널마다 붙이면
 * 같은 문단이 넷이 되어 아무도 안 읽는다. */
function etfCaveats() {
  return `<p class="note tiny etf-note">
    <strong>ETF 는 지수가 아니다.</strong> 미장 지수는 창고에 <strong>종가만</strong>
    있어(FRED) 봉을 못 그리므로 이 칸은 ETF 를 그린다. 셋이 달라진다 —
    ① <strong>분배금</strong>: 분배락에 가격이 떨어진다(가격지수도 배당을 빼지만
    방식이 다르다). ② <strong>추적오차·보수</strong>: SPY 는 연 0.0945% 를 떼고
    지수를 정확히 못 따라간다. ③ <strong>시장가격 ≠ NAV</strong>:
    프리미엄/디스카운트가 붙는다.
    <br><strong>벤치마크는 바뀌지 않았다</strong> — 회계·백테스트는 여전히
    <code>config.benchmark</code> 의 지수로 잰다. 이 교체는 화면 한정이다.
  </p>`;
}

/* 없으면 없다고 말한다 — 빈 차트는 조인 버그처럼 보인다. */
function panelMissingReason(panel, lookback) {
  const what = panel.kind === "etf" ? "시세" : "종가";
  return `<p class="empty"><code>${esc(panel.entity_id)}</code> 의 ${what}가
    창(${num(lookback)}일) 안에 없다 — 아직 수집되지 않았다. 수집이 들어오면
    이 자리가 저절로 찬다. <strong>다른 종목으로 바꿔치기하지 않는다.</strong></p>`;
}

/* 캔들. **넷이 다 있을 때만 봉이다** — 없는 시가·고가·저가를 종가로 채우면
 * 모든 봉이 십자가가 되는데, 그건 "그날 변동이 없었다" 는 다른 사실이다.
 * 못 그리면 선으로 긋고 화면이 이유를 적는다.
 *
 * 상승 초록 · 하락 빨강. ECharts 의 color 가 양봉, color0 이 음봉이다 —
 * 뒤집으면 이 대시보드의 다른 화면과 색이 갈린다(§10). */
function panelChart(elId, panel) {
  const tone = panel.total === null || panel.total === undefined
    ? COLOR.muted
    : panel.total > 0 ? COLOR.up : panel.total < 0 ? COLOR.down : COLOR.muted;
  const axis = {
    xAxis: { type: "category", data: panel.sessions, boundaryGap: panel.has_ohlc, ...AXIS },
    // scale:true 가 필수다. 0 부터 그리면 봉이 위쪽 몇 %에 눌린다.
    yAxis: { type: "value", scale: true, ...AXIS },
    grid: { left: 46, right: 8, top: 8, bottom: 18 },
  };
  const series = panel.has_ohlc
    ? [{
        type: "candlestick",
        data: panel.ohlc,
        itemStyle: {
          color: COLOR.up, color0: COLOR.down,
          borderColor: COLOR.up, borderColor0: COLOR.down, borderWidth: 1,
        },
      }]
    : [{
        type: "line", data: panel.closes, showSymbol: false,
        lineStyle: { color: tone, width: 1.6 },
        areaStyle: { color: tone, opacity: 0.08 },
      }];

  chart(elId).setOption({
    ...BASE, ...axis,
    legend: { show: false },
    tooltip: {
      ...BASE.tooltip,
      formatter: (rows) => {
        if (!rows || !rows.length) return "";
        const row = rows[0];
        if (!panel.has_ohlc) {
          return `${esc(row.axisValue)}<br>${esc(panel.label)} <b>${dec(row.value, 2)}</b>`;
        }
        // 캔들의 value 는 [index, 시가, 종가, 저가, 고가] 다.
        const v = row.value;
        return `${esc(row.axisValue)} <b>${esc(panel.label)}</b><br>`
          + `시 ${dec(v[1], 2)} · 고 ${dec(v[4], 2)}<br>`
          + `저 ${dec(v[3], 2)} · 종 ${dec(v[2], 2)}`;
      },
    },
    series,
  });
}

/* 시장 머리글. **가로 한 줄에서는 위치가 시장을 말해주지 않는다** — 좌우로
 * 갈랐을 때는 왼쪽/오른쪽이 그 일을 했는데, 여섯을 한 줄에 펴면 코스닥과 SPY
 * 사이의 경계가 사라진다. 그래서 묶음마다 머리글을 얹고 사이에 선을 둔다. */
function marketGroupHead(code, data) {
  const label = code === "KR" ? "국장" : "미장";
  const what = data.kind === "etf" ? "ETF" : "지수";
  return `<div class="index-group-head">
    <span class="chip chip-${code.toLowerCase()}">${code}</span>
    <span class="index-group-name">${label} · ${what}</span>
  </div>`;
}

/* 패널 카드 하나. ``spec`` 은 값이 있든 없든 오는 명세이고, ``panel`` 은
 * 값이 있을 때만이다 — 없는 것도 자리를 지켜야 "수집이 안 된 것" 과 "애초에
 * 안 그리는 것" 이 화면에서 갈린다. */
function panelCard(body, spec, panel, elId, lookback) {
  const head = `<div class="index-panel-head">
    <span class="index-panel-name">${esc(spec.label)}</span>
    ${roleBadge(spec)}${kindBadge(spec)}${prBadge(body, spec)}
    <span class="index-panel-nums">
      <span class="mono index-panel-close">${panel ? dec(panel.close, 2) : "—"}</span>
      <span class="mono ${panel ? signClass(panel.change) : ""}">${panel ? signed(panel.change) : "—"}</span>
    </span>
  </div>`;
  const inner = panel
    ? `<div class="chart mini" id="${elId}"></div>
       <div class="index-panel-foot sub tiny">
         ${esc(panel.first_session)} ~ ${esc(panel.session)} ·
         창 등락 <span class="mono ${signClass(panel.total)}">${signed(panel.total)}</span>
         ${panel.has_ohlc ? "" : ` · <span class="warn-text">종가만 — 봉을 못 그린다</span>`}
       </div>`
    : panelMissingReason(spec, lookback);
  return `<div class="index-panel">${head}${inner}</div>`;
}

/* 여섯을 **가로 한 줄**로. 순서는 코스피·코스닥 → SPY·QQQ·DIA·SOXX 이고,
 * 그건 COLUMNS(=MARKETS 순서)와 서비스가 든 순서를 그대로 따른 것이다. */
function renderIndexStrip(body) {
  const target = document.getElementById("index-strip");
  const note = document.getElementById("index-strip-note");
  const groups = [];
  const jobs = [];
  let missingCount = 0;

  for (const [code, suffix] of COLUMNS) {
    const data = body.data.markets[code].instrument_panels;
    const cards = [
      ...data.panels.map((panel) => ({ panel, spec: panel })),
      ...data.missing.map((spec) => ({ panel: null, spec })),
    ];
    missingCount += data.missing.length;
    const rendered = cards
      .map(({ panel, spec }, i) => {
        const elId = `chart-index-${suffix}-${i}`;
        if (panel) jobs.push([elId, panel]);
        return panelCard(body, spec, panel, elId, data.lookback);
      })
      .join("");
    groups.push(`<div class="index-group" id="index-group-${suffix}">
      ${marketGroupHead(code, data)}
      <div class="index-group-panels">${rendered || `<p class="empty">그릴 패널이 정해져 있지 않다.</p>`}</div>
      ${data.kind === "etf" ? etfCaveats() : ""}
    </div>`);
  }

  target.innerHTML = groups.join("");
  if (note) {
    note.textContent = missingCount
      ? `${num(jobs.length)}종 · 미수집 ${num(missingCount)}종`
      : `${num(jobs.length)}종`;
  }
  // innerHTML 을 먼저 넣어야 컨테이너가 생긴다 — 순서를 바꾸면 ECharts 가
  // 크기 0 인 요소에 붙어 아무것도 안 그린다.
  for (const [elId, panel] of jobs) panelChart(elId, panel);
}

/* -- 나머지 지수 목록 --------------------------------------------------------- */


function renderIndices(body, code, suffix) {
  const panel = body.data.markets[code].indices;
  const rows = panel.others;
  // 자른 목록이 아니다 — 오늘 변동이 큰 순으로 쌓고 패널 안에서 스크롤한다.
  // 위에서 개별 패널로 세운 지수는 여기서 빠지는데, 빠진 개수를 적어야
  // "총 N종" 과 눈에 보이는 줄 수가 안 맞는 것이 버그처럼 안 보인다.
  const moved = (panel.excluded || []).length;
  document.getElementById(`indices-count-${suffix}`).textContent = panel.total
    ? `${panel.total}종${moved ? ` · ${moved}종은 위 패널` : ""} · 변동 큰 순`
    : "";
  const target = document.getElementById(`indices-${suffix}`);
  if (!rows.length) {
    target.innerHTML = panel.total
      ? `<p class="empty">위 패널의 지수 말고는 이 시장에 지수가 없다.</p>`
      : `<p class="empty">지수가 없다. 백필(bf-indices)이 돌았는지 확인할 것.</p>`;
    return;
  }
  const head = `<thead><tr><th>지수</th><th class="r">종가</th><th class="r">등락률</th></tr></thead>`;
  // 변동성 지수는 가격지수가 아니다 — 서비스가 뒤로 묶어 주고, 화면은 그
  // 경계에 머리글을 하나 끼워 넣는다. 등락에 손익 색을 쓰지 않는다:
  // **VIX 가 오른 것은 수익이 아니라 공포다.**
  let seenVol = false;
  const rendered = rows
    .map((row) => {
      const vol = row.kind === "volatility";
      const divider = vol && !seenVol
        ? `<tr class="section"><td colspan="3">변동성 지수 — 가격지수가 아니다 · 수익률로 읽지 말 것</td></tr>`
        : "";
      if (vol) seenVol = true;
      return `${divider}<tr${vol ? ' class="vol"' : ""}>
        <td><span class="name trunc" title="${esc(indexLabel(row.entity_id))}">${esc(indexLabel(row.entity_id))}</span></td>
        <td class="r mono">${dec(row.close, 2)}</td>
        <td class="r mono ${vol ? "" : signClass(row.change)}">${signed(row.change)}</td>
      </tr>`;
    })
    .join("");
  target.innerHTML = `<table>${head}<tbody>${rendered}</tbody></table>`;
}

/* -- 시장 폭 ---------------------------------------------------------------- */

/* 지수 하나로는 "지수는 올랐는데 종목의 70%는 내렸다" 를 볼 수 없다.
 * 등락을 못 잰 종목은 보합 칸에 넣지 않는다 — 따로 센다. */
function renderBreadth(body, code, suffix) {
  const b = body.data.markets[code].breadth;
  document.getElementById(`breadth-session-${suffix}`).textContent = b.session || "";
  const target = document.getElementById(`breadth-${suffix}`);
  if (!b.traded) {
    target.innerHTML = `<p class="empty">이 시장의 시세가 창고에 없다 — 오늘 등락을
      잴 수 있는 종목이 없다.</p>`;
    return;
  }
  const measured = b.advancers + b.decliners + b.unchanged;
  const share = (n) => (measured ? (n / measured) * 100 : 0);
  target.innerHTML = `
    <div class="breadth">
      <div class="breadth-cell"><span class="k">상승</span>
        <span class="v mono up">${num(b.advancers)}</span></div>
      <div class="breadth-cell"><span class="k">하락</span>
        <span class="v mono down">${num(b.decliners)}</span></div>
      <div class="breadth-cell"><span class="k">보합</span>
        <span class="v mono">${num(b.unchanged)}</span></div>
      <div class="breadth-cell"><span class="k">거래 종목</span>
        <span class="v mono">${num(b.traded)}</span></div>
      <div class="breadth-cell"><span class="k">거래대금</span>
        <span class="v mono">${money(b.value, b.currency)}</span></div>
    </div>
    <div class="breadth-bar" title="상승 ${num(b.advancers)} · 하락 ${num(b.decliners)}">
      <span class="up" style="width:${share(b.advancers).toFixed(1)}%"></span>
      <span class="flat" style="width:${share(b.unchanged).toFixed(1)}%"></span>
      <span class="down" style="width:${share(b.decliners).toFixed(1)}%"></span>
    </div>
    ${b.unmeasured
      ? `<p class="sub tiny">등락 미측정 ${num(b.unmeasured)}종목 — 직전 종가가 없다(신규·거래정지 복귀). 보합에 넣지 않는다.</p>`
      : ""}`;
}

/* -- 순위표 3종 --------------------------------------------------------------- */

/* 표마다 기준값의 단위가 다르다. 거래대금은 금액, 시총도 금액, 상승률은 %.
 * 같은 열에 섞어 찍으면 숫자가 무슨 뜻인지 매번 생각하게 된다. */
const RANK_BY_CHANGE = new Set(["gainers", "losers"]);

function rankMetric(table, row, currency) {
  if (RANK_BY_CHANGE.has(table.key)) return signed(row.change);
  return money(row.metric, currency);
}

function rankUnit(table) {
  if (RANK_BY_CHANGE.has(table.key)) return "등락률";
  return table.key === "value" ? "거래대금" : "시가총액";
}

/* 하한을 화면이 적는다. **안 보이면 사용자는 이게 전체 순위인 줄 안다.**
 * 값은 config.reporting 에서 온 것이고 메일 브리핑과 같은 것이다. */
function floorNote(data) {
  const f = data.floor;
  if (!f) return "";
  const cut = num(data.universe - data.eligible);
  return `<p class="sub tiny rank-floor">
    하한 — 거래대금 ${money(f.min_turnover, f.currency)} 이상 ·
    주가 ${f.currency === "USD" ? "$" + dec(f.min_price, 2) : num(f.min_price) + "원"} 이상.
    <strong>상승률·하락률</strong>은 <strong>같은</strong> 모집단
    — 거래대금 상위 ${num(f.pool)}종목 — 안에서 고른다. 하한이 없으면 시황이
    아니라 동전주 목록이 되고, 한쪽에만 걸면 두 표가 서로 다른 세계를 본다.
    ${data.universe ? `${num(data.eligible)}종목 통과 · ${cut}종목 제외` : ""}
  </p>`;
}

/** 시가총액 순위가 빈 이유. **"없다" 와 "못 만든다" 는 다르다.**
 *
 * 시총은 상장주식수 × 종가라, 주식수가 없으면 종목이 통째로 빠진다. 그걸
 * 그냥 "종목이 없다" 로 적으면 수집이 멈춘 것을 시장이 조용한 것으로 읽게
 * 된다 — 이 저장소가 반복해서 겪은 결함 계열이다.
 *
 * 이 함수가 없어서 마켓 탭이 통째로 안 떴다(2026-08-18, ReferenceError).
 * 이름이 어긋나 있었다 — 정의는 `noCapReasonText`, 트리맵의 호출은
 * `noCapReason`. **그 줄에서 죽으면 아래가 통째로 안 돈다**: 국장 트리맵이
 * 비는 날에 미장 칸 전부(지수 목록·시장폭·순위·트리맵·거시)가 같이 사라졌다.
 */
function noCapReasonText(code) {
  return code === "KR"
    ? "시가총액을 만들 수 없다 — KRX 상장주식수가 그 세션에 없다."
    : "시가총액을 만들 수 없다 — SEC 상장주식수가 그 세션에 없다 " +
      "(ADR·ETF 는 주식수가 없어 원래 빠진다).";
}

/* 트리맵이 빈 이유. **"창고에 없다" 와 "창 밖이다" 는 다른 사실이다** —
 * 시총 수집은 시세보다 며칠 밀리므로, 창(CAP_RECENT_DAYS) 안에 한 세션도
 * 없으면 수집이 멈춘 것이다. 그 창을 화면이 적어야 어디를 파야 할지 안다. */
function treemapEmptyText(code, t) {
  const window = t.lookback ? ` 최근 ${num(t.lookback)}일 안에 시총 세션이 하나도 없다 —
    수집이 멈춘 것인지 먼저 볼 것.` : "";
  return `${noCapReasonText(code)}${window}`;
}

/* -- 장중 값 ------------------------------------------------------------------ */

/* 종가 아래에 붙는 참고 한 줄. **종가를 덮지 않는다** — 순위와 시총은 확정된
 * 종가로 섰으므로 그 숫자가 먼저 보여야 하고, 실시간은 그 옆에 앉는다
 * (services/market.py `attach_live` 와 같은 규약).
 *
 * 값이 없으면 **아무것도 그리지 않는다.** 빈 자리가 "지금은 장외" 를 뜻한다 —
 * 종가로 때우면 화면이 실시간인 척하게 되고, 그건 조용히 틀리는 거짓이다.
 */
function liveCell(row) {
  if (row.live_price === null || row.live_price === undefined) return "";
  return `<div class="live-line" title="장중 체결가 (t8407)">
    <span class="live-dot"></span>${num(row.live_price)}
    <span class="${signClass(row.live_change)}">${signed(row.live_change)}</span>
  </div>`;
}

/* 그 시장이 장중 값을 몇 개나 받았는지. **0 건과 "안 물어본다" 는 다른
 * 사실이다** — 미장은 appkey 가 따로고 호가가 응답에 없어 조회 자체가 안
 * 나간다. 조용히 비면 둘이 같아 보인다.
 */
function liveBadge(panel) {
  const live = panel.live || {};
  if (!live.supported) {
    return `<span class="badge dim" title="${esc(live.reason || "")}">실시간 없음</span>`;
  }
  if (!live.filled) {
    return `<span class="badge dim" title="장외이거나 조회가 비었다">장외</span>`;
  }
  return `<span class="badge live" title="t8407 장중 체결가 · ${num(live.filled)}종목">
    <span class="live-dot"></span>실시간 ${num(live.filled)}
  </span>`;
}

/* 순위표 열 폭. **colgroup 으로 고정한다** — 안 주면 종목명이 긴 행이 숫자
 * 칸을 밀어내고, 실시간 줄까지 들어간 가격 칸에서 숫자가 잘린다(아이폰
 * 실측 2026-08-19: `1,662,00` 처럼 보였다). 좁은 화면에서 시총 열을 접는
 * 것도 이 class 를 잡고 한다(market.css).
 */
function rankCols() {
  return `<colgroup><col class="name"><col class="metric"><col class="price"><col class="cap"></colgroup>`;
}

function renderRankings(body, code, suffix) {
  const panel = body.data.markets[code];
  const data = panel.rankings;
  const target = document.getElementById(`rankings-${suffix}`);
  const note = document.getElementById(`rankings-note-${suffix}`);

  if (!data.floor) {
    const label = code === "KR" ? "국장" : "미장";
    if (note) note.textContent = "";
    target.innerHTML = `<p class="empty">${label} 순위를 매길 수 없다 —
      ${esc(data.reason || "하한을 읽을 수 없다")}</p>`;
    return;
  }
  if (note) {
    note.innerHTML = `${num(data.eligible)}종목 · 상위 ${num(data.rows)} ${liveBadge(panel)}`;
  }

  // 넷을 한 표에 쌓으면 패널 안에서 세로로만 길어져 넷째 표까지 내려가는
  // 사람이 없다. **2×2 로 눕히고 좁으면 한 줄씩 쌓는다**(market.css).
  const block = (table) => {
    // **표마다 세션이 다를 수 있다.** 시세와 시총은 다른 수집기가 넣는다 —
    // 실측으로 국장 시세 08-14 · 시총 08-11 이었다. 나란히 놓으면 같은 날로
    // 읽히므로 표마다 자기 날짜를 적는다.
    const head = `<tr class="section"><td colspan="4">${esc(table.label)}
      <span class="sub">${table.session ? esc(table.session) : "세션 없음"}</span></td></tr>
      <tr class="sub-head"><td>종목</td><td class="r">${rankUnit(table)}</td>
      <td class="r">가격</td><td class="r cap">시총</td></tr>`;
    if (!table.rows.length) {
      return `${head}<tr><td colspan="4" class="empty">${
        table.key === "market_cap"
          ? esc(noCapReasonText(code))
          : "하한을 넘은 종목이 없다"
      }</td></tr>`;
    }
    const rows = table.rows
      .map(
        (row) => `<tr>
          <td><span class="name trunc" title="${esc(row.name)} (${esc(row.entity_id)})">${esc(row.name)}</span>
              ${codeCell(row.entity_id, row.name)}</td>
          <td class="r mono ${table.key === "gainers" ? signClass(row.change) : ""}">${rankMetric(table, row, panel.currency)}</td>
          <td class="r mono">${num(row.close)}
              <span class="sub ${signClass(row.change)}">${signed(row.change)}</span>
              ${liveCell(row)}</td>
          <td class="r mono cap">${row.market_cap === null ? "—" : money(row.market_cap, panel.currency)}</td>
        </tr>`
      )
      .join("");
    return head + rows;
  };

  target.innerHTML =
    `<div class="rank-grid">${data.tables
      .map((table) => `<table class="rank-table">${rankCols()}<tbody>${block(table)}</tbody></table>`)
      .join("")}</div>` + floorNote(data);
}

/* -- 시가총액 맵 (finviz 식) --------------------------------------------------- */

/* 16진 문자열 → RGB 세 자리. treemap 은 색을 섞어야 해서(옅은 등락일수록
 * 배경에 가깝게) COLOR 의 hex 를 그대로 못 쓴다 — 숫자 세 개로 풀어야 mix 가
 * 된다. 리터럴 hex 를 여기 새로 적지 않고 COLOR(scope.js, app.css 와 값이
 * 같다)에서만 읽는다. */
function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/* 등락률 → 색. **상승 초록 · 하락 빨강** — 이 대시보드의 고정 색 규칙이다
 * (scope.js COLOR.up/down). 한국식(빨강↑)을 쓰면 다른 탭과 색이 갈린다.
 * 등락을 못 잰 종목은 회색(COLOR.dim)이다 — 0% 로 칠하면 "보합" 이라는
 * 다른 사실이 된다. */
function treemapColor(change) {
  if (change === null || change === undefined) return COLOR.dim;
  // ±5%에서 색이 꽉 찬다. finviz 도 비슷한 폭으로 채도를 준다 — 상폐 임박
  // 종목의 ±30% 한 건이 나머지 전부를 흐리게 만들면 맵을 못 읽는다.
  const cap = 0.05;
  const ratio = Math.min(1, Math.abs(change) / cap);
  const base = hexToRgb(change >= 0 ? COLOR.up : COLOR.down);
  // 옅은 등락은 배경에 가깝게 섞여야 "안 읽는다" 가 아니라 "약하다" 로
  // 보인다. **배경값을 여기 복제하지 않는다** — 숫자로 박아 두면 --panel2 를
  // 바꿨을 때 트리맵만 옛 배경을 쓰고, 그 어긋남은 눈에 잘 안 띈다.
  const panel2 = hexToRgb(COLOR.panel2);
  const mix = panel2.map((c, i) => Math.round(c + (base[i] - c) * (0.2 + ratio * 0.8)));
  return `rgb(${mix.join(",")})`;
}

function renderTreemap(body, code, suffix) {
  const panel = body.data.markets[code];
  const t = panel.treemap;
  const note = document.getElementById(`treemap-note-${suffix}`);
  const elId = `chart-treemap-${suffix}`;
  const target = document.getElementById(elId);
  if (!t.rows.length) {
    if (note) note.textContent = "";
    target.innerHTML = `<p class="empty">${esc(treemapEmptyText(code, t))}</p>`;
    return;
  }
  // **시총 세션은 시세 세션과 다를 수 있다**(수집기가 다르다). 날짜를 안
  // 적으면 며칠 지난 시총이 오늘 것으로 읽힌다 — 순위표가 표마다 자기
  // 날짜를 적는 것과 같은 이유다.
  if (note) {
    note.textContent = `상위 ${t.rows.length}종목 (최대 ${t.top_n})`
      + (t.session ? ` · 시총 ${t.session}` : "");
  }

  const data = t.rows.map((row) => ({
    name: row.name,
    value: row.market_cap,
    change: row.change,
    entityId: row.entity_id,
    itemStyle: { color: treemapColor(row.change) },
  }));

  chart(elId).setOption({
    ...BASE,
    tooltip: {
      ...BASE.tooltip,
      formatter: (info) => {
        const d = info.data;
        if (!d) return "";
        const changeText = d.change === null || d.change === undefined
          ? "등락 미측정(거래 없음)"
          : `${arrow(d.change)}${pct(d.change)}`;
        return `<b>${esc(d.name)}</b> <span class="sub">${esc(d.entityId)}</span><br>`
          + `시총 ${money(d.value, panel.currency)}<br>${changeText}`;
      },
    },
    series: [
      {
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        label: {
          color: COLOR.text, fontFamily: "IBM Plex Mono", fontSize: 11, overflow: "truncate",
        },
        itemStyle: { borderColor: COLOR.panel, gapWidth: 1 },
        levels: [
          { itemStyle: { borderColor: COLOR.border, borderWidth: 1, gapWidth: 1 } },
        ],
        data,
      },
    ],
  });
}

/* -- 거시지표 ---------------------------------------------------------------- */

function renderMacro(body, code, suffix) {
  const rows = body.data.markets[code].macro;
  const target = document.getElementById(`macro-${suffix}`);
  if (!rows.length) {
    target.innerHTML = `<p class="empty">이 시장에서 발표된 거시지표가 없다.
      tools/collect_macro.py 를 확인할 것.</p>`;
    return;
  }
  const head = `<thead><tr><th>지표</th><th class="num">발표</th>
    <th class="num">실측</th><th class="num">직전</th></tr></thead>`;
  // 색을 쓰지 않는다 — 전월대비 변화는 손익이 아니고, 부호에 좋고 나쁨을
  // 입힐 수 없다(dashboard.md §8-2). 방향은 부호로만 보여준다.
  const rendered = rows
    .map((item) => {
      const diff = item.actual !== null && item.previous !== null ? item.actual - item.previous : null;
      const diffText = diff === null ? "" : ` (${diff > 0 ? "+" : ""}${diff.toFixed(2)})`;
      return `<tr>
        <td><span class="lead">
          <strong>${esc(item.indicator)}</strong>
          <span class="sub trunc" title="${esc(item.release_name)}">${esc(item.release_name)}</span>
        </span></td>
        <td class="num">${stamp(item.scheduled_at)}</td>
        <td class="num">${item.actual === null ? "—" : num(item.actual)} ${esc(item.unit)}</td>
        <td class="num">${item.previous === null ? "—" : num(item.previous)}${diffText}</td>
      </tr>`;
    })
    .join("");
  target.innerHTML = `<table>${head}<tbody>${rendered}</tbody></table>`;
}

/* -- 한 판 ------------------------------------------------------------------- */

/* 그 칸의 데이터가 **언제 것인가**. 화면에 숫자가 있다는 것과 그 숫자가
 * 오늘 것이라는 것은 다른 사실인데, 날짜를 안 적으면 둘이 같아 보인다 —
 * 실측으로 시세 08-14 · 시총 08-11 인 채로 순위표가 멀쩡해 보인 적이 있다.
 *
 * 셋을 나눠 적는다. 셋의 출처가 다르기 때문이다:
 *   시세  일일 수집(collect_daily.sh)이 넣는 종가 세션
 *   시총  같은 스크립트의 다른 단계 — 자주 어긋난다
 *   실시간 t8407 장중 체결가. 국장만 있고, 장외에는 아예 없다
 */
function renderColumnNote(body, code, suffix) {
  const panel = body.data.markets[code];
  const b = panel.breadth;
  const cap = (panel.treemap || {}).session;
  const live = panel.live || {};
  const target = document.getElementById(`col-note-${suffix}`);
  if (!target) return;

  if (!b.session) {
    target.textContent = "시세 없음";
    return;
  }
  const parts = [`시세 ${esc(b.session)} · ${num(b.traded)}종목`];
  // 시총 날짜는 **다를 때만** 적는다. 같으면 같은 날짜를 두 번 읽게 되고,
  // 그러면 정작 어긋난 날 눈에 안 들어온다.
  if (cap && cap !== b.session) {
    parts.push(`<span class="warn">시총 ${esc(cap)}</span>`);
  }
  if (live.supported && live.filled) {
    parts.push(`<span class="live-dot">실시간 ${num(live.filled)}종목</span>`);
  }
  target.innerHTML = parts.join(" · ");
}

async function loadMarket() {
  const body = await fetchJson("market");
  showScope(body);
  renderKpis(body);
  renderIndexStrip(body);
  for (const [code, suffix] of COLUMNS) {
    renderColumnNote(body, code, suffix);
    renderIndices(body, code, suffix);
    renderBreadth(body, code, suffix);
    renderRankings(body, code, suffix);
    renderTreemap(body, code, suffix);
    renderMacro(body, code, suffix);
  }

  const warnings = [];
  if (body.data.fx.rate === null) warnings.push("환율이 비어 있다 — NAV 평가에도 영향을 준다");
  for (const [code] of COLUMNS) {
    const panel = body.data.markets[code];
    const label = code === "KR" ? "국장" : "미장";
    // 첫 자리가 빈 것과 곁 패널이 빈 것은 무게가 다르다 — 따로 짚는다.
    for (const gone of panel.instrument_panels.missing) {
      warnings.push(
        gone.role === "primary"
          ? `${label} 대표(${gone.entity_id})가 창(${num(panel.instrument_panels.lookback)}일) 안에 없다`
          : `${label} ${gone.label} 패널이 비었다 — 아직 수집되지 않았다`
      );
    }
    if (!panel.breadth.traded) warnings.push(`${label} 시세가 비어 있다`);
    if (!panel.treemap.rows.length) {
      warnings.push(`${label} 시가총액이 비어 있다 — 상장주식수 수집을 볼 것`);
    } else if (
      // 시총이 시세보다 **늦은** 것은 조용히 넘어가면 안 된다. 화면은 멀쩡해
      // 보이고 숫자도 있는데, 순위가 며칠 전 시총으로 매겨진다.
      panel.treemap.session &&
      panel.breadth.session &&
      panel.treemap.session < panel.breadth.session
    ) {
      warnings.push(
        `${label} 시총이 시세보다 늦다 — 시세 ${panel.breadth.session} · 시총 ${panel.treemap.session}`
      );
    }
    if (!panel.rankings.floor) {
      warnings.push(`${label} 순위 하한 설정(config.reporting)이 창고에 없다`);
    }
    if (!panel.macro.length) warnings.push(`${label} 거시지표가 비어 있다`);
  }
  showAlerts(warnings);
}

runAll([loadMarket]);

if (isLive()) {
  window.setInterval(() => runAll([loadMarket]), REFRESH_MS);
}
