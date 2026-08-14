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
  const head = panel.indices.headline;
  const b = panel.breadth;
  const label = code === "KR" ? "국장" : "미장";
  return [
    kpi(
      `${label} 대표 · ${esc(indexLabel(panel.indices.headline_id))}`,
      head ? dec(head.close, 2) : "—",
      head ? `${arrow(head.change)}${pct(head.change)}` : "창고에 없다",
      false,
      { tone: head ? signClass(head.change) : "" }
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

function renderFxChart(body) {
  const f = body.data.fx;
  const target = document.getElementById("chart-fx");
  if (!f.sessions.length) {
    target.innerHTML = `<p class="empty">fx 가 비어 있다. FRED 수집을 확인할 것.</p>`;
    return;
  }
  chart("chart-fx").setOption({
    ...BASE,
    grid: { ...BASE.grid, top: 12, bottom: 24 },
    xAxis: { type: "category", data: f.sessions, ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [
      { name: "USDKRW", type: "line", data: f.rates, showSymbol: false,
        lineStyle: { color: COLOR.accent, width: 1.6 },
        areaStyle: { color: COLOR.accent, opacity: 0.08 } },
    ],
  });
}

/* -- 지수 ------------------------------------------------------------------ */

/* 대표 지수 한 줄. **가격지수 배지를 단다** — 배당이 빠져 있어 그만큼 우리가
 * 유리하게 보인다. 언제 떼는지는 config(benchmark.total_return)만 안다. */
function renderIndexHead(body, code, suffix) {
  const panel = body.data.markets[code].indices;
  const head = panel.headline;
  const target = document.getElementById(`index-head-${suffix}`);
  const badge = body.data.total_return
    ? ""
    : `<span class="chip warn-chip" title="총수익지수(TR)를 못 구한다 — 배당만큼 우리가 유리하게 보인다">가격지수 · 배당 미반영</span>`;
  if (!head) {
    target.innerHTML = `<p class="empty">대표 지수 <code>${esc(panel.headline_id)}</code> 가
      아직 창고에 없다. 수집이 들어오면 이 자리가 찬다 — 다른 지수로
      바꿔치기하지 않는다.</p>`;
    return;
  }
  target.innerHTML = `<div class="headline">
    <div class="headline-name">${esc(indexLabel(head.entity_id))} ${badge}</div>
    <div class="headline-value mono ${signClass(head.change)}">${dec(head.close, 2)}</div>
    <div class="headline-change mono ${signClass(head.change)}">${signed(head.change)}</div>
  </div>`;
}

function renderIndices(body, code, suffix) {
  const panel = body.data.markets[code].indices;
  const rows = panel.others;
  // 자른 목록이 아니다 — 오늘 변동이 큰 순으로 쌓고 패널 안에서 스크롤한다.
  document.getElementById(`indices-count-${suffix}`).textContent =
    panel.total ? `${panel.total}종 · 변동 큰 순` : "";
  const target = document.getElementById(`indices-${suffix}`);
  if (!rows.length) {
    target.innerHTML = panel.total
      ? `<p class="empty">대표 지수 말고는 이 시장에 지수가 없다.</p>`
      : `<p class="empty">지수가 없다. 백필(bf-indices)이 돌았는지 확인할 것.</p>`;
    return;
  }
  const head = `<thead><tr><th>지수</th><th class="r">종가</th><th class="r">등락률</th></tr></thead>`;
  const rendered = rows
    .map(
      (row) => `<tr>
        <td><span class="name trunc" title="${esc(indexLabel(row.entity_id))}">${esc(indexLabel(row.entity_id))}</span></td>
        <td class="r mono">${dec(row.close, 2)}</td>
        <td class="r mono ${signClass(row.change)}">${signed(row.change)}</td>
      </tr>`
    )
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

/* -- 오늘 움직인 종목 --------------------------------------------------------- */

function moverRows(rows, currency, kind) {
  if (!rows.length) return `<tr><td colspan="3" class="empty">—</td></tr>`;
  return rows
    .map(
      (row) => `<tr>
        <td><span class="name trunc" title="${esc(row.name)} (${esc(row.entity_id)})">${esc(row.name)}</span>
            ${codeCell(row.entity_id, row.name)}</td>
        <td class="r mono">${kind === "actives" ? money(row.value, currency) : num(row.close)}</td>
        <td class="r mono ${signClass(row.change)}">${signed(row.change)}</td>
      </tr>`
    )
    .join("");
}

function renderMovers(body, code, suffix) {
  const panel = body.data.markets[code];
  const m = panel.movers;
  const target = document.getElementById(`movers-${suffix}`);
  if (!m.gainers.length && !m.losers.length && !m.actives.length) {
    target.innerHTML = `<p class="empty">시세가 없다 — 오늘 움직인 종목을 못 고른다.</p>`;
    return;
  }
  const block = (title, rows, kind, unit) => `
    <tr class="section"><td colspan="3">${title}</td></tr>
    <tr class="sub-head"><td>종목</td><td class="r">${unit}</td><td class="r">등락률</td></tr>
    ${moverRows(rows, panel.currency, kind)}`;
  target.innerHTML = `<table>
    <tbody>
      ${block("상승 상위", m.gainers, "gainers", "종가")}
      ${block("하락 상위", m.losers, "losers", "종가")}
      ${block("거래대금 상위", m.actives, "actives", "거래대금")}
    </tbody>
  </table>`;
}

/* -- 시가총액 상위 ------------------------------------------------------------ */

/* 없는 것은 없다고 말한다. **미장 시가총액이 0행인 이유는 조인 버그가
 * 아니라 수집기가 없어서다** — 화면이 그걸 말해야 엉뚱한 데를 파지 않는다. */
function noCapReason(code) {
  return code === "US"
    ? `미장 시가총액은 아직 수집되지 않았다 — market_cap = 주가 × 상장주식수인데,
       미장 상장주식수(shares outstanding)를 받는 수집기가 아직 없다. 국장은 KRX
       Open API 로 받는다. <strong>시세·유니버스는 들어와 있다</strong> — 위의
       시장 폭·움직인 종목이 그것으로 만든 것이다.`
    : `market_stats 에 오늘 시총이 없다. KRX Open API 수집을 확인할 것.`;
}

function renderLeaders(body, code, suffix) {
  const panel = body.data.markets[code];
  const rows = panel.leaders;
  document.getElementById(`leaders-count-${suffix}`).textContent =
    rows.length ? `상위 ${rows.length}종목` : "";
  const target = document.getElementById(`leaders-${suffix}`);
  if (!rows.length) {
    target.innerHTML = `<p class="empty">${noCapReason(code)}</p>`;
    return;
  }
  const head = `<thead><tr><th>종목</th><th class="r">시가총액</th><th class="r">등락률</th></tr></thead>`;
  const rendered = rows
    .map(
      (row) => `<tr>
        <td><span class="name trunc" title="${esc(row.name)}">${esc(row.name)}</span>
            ${codeCell(row.entity_id, row.name)}</td>
        <td class="r mono">${money(row.market_cap, panel.currency)}</td>
        <td class="r mono ${signClass(row.change)}">${signed(row.change)}</td>
      </tr>`
    )
    .join("");
  target.innerHTML = `<table>${head}<tbody>${rendered}</tbody></table>`;
}

/* -- 시가총액 맵 (finviz 식) --------------------------------------------------- */

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
  const base = change >= 0 ? [34, 197, 94] : [239, 68, 68]; // COLOR.up / COLOR.down
  const panel = [20, 27, 36]; // --panel2 근사 — 옅은 등락은 배경에 가깝게
  const mix = panel.map((c, i) => Math.round(c + (base[i] - c) * (0.2 + ratio * 0.8)));
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
    target.innerHTML = `<p class="empty">${noCapReason(code)}</p>`;
    return;
  }
  if (note) note.textContent = `상위 ${t.rows.length}종목 (최대 ${t.top_n})`;

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
          color: "#fff", fontFamily: "IBM Plex Mono", fontSize: 11, overflow: "truncate",
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

function renderColumnNote(body, code, suffix) {
  const b = body.data.markets[code].breadth;
  const target = document.getElementById(`col-note-${suffix}`);
  if (!target) return;
  target.textContent = b.session ? `시세 ${b.session} · ${num(b.traded)}종목` : "시세 없음";
}

async function loadMarket() {
  const body = await fetchJson("market");
  showScope(body);
  renderKpis(body);
  renderFxChart(body);
  for (const [code, suffix] of COLUMNS) {
    renderColumnNote(body, code, suffix);
    renderIndexHead(body, code, suffix);
    renderIndices(body, code, suffix);
    renderBreadth(body, code, suffix);
    renderMovers(body, code, suffix);
    renderLeaders(body, code, suffix);
    renderTreemap(body, code, suffix);
    renderMacro(body, code, suffix);
  }

  const warnings = [];
  if (body.data.fx.rate === null) warnings.push("환율이 비어 있다 — NAV 평가에도 영향을 준다");
  for (const [code] of COLUMNS) {
    const panel = body.data.markets[code];
    const label = code === "KR" ? "국장" : "미장";
    if (!panel.indices.headline) {
      warnings.push(`${label} 대표 지수(${panel.indices.headline_id})가 창고에 없다`);
    }
    if (!panel.breadth.traded) warnings.push(`${label} 시세가 비어 있다`);
    if (!panel.treemap.rows.length) {
      warnings.push(
        code === "US"
          ? "미장 시가총액 미수집 — 상장주식수 소스가 없다"
          : "국장 시가총액이 비어 있다"
      );
    }
    if (!panel.macro.length) warnings.push(`${label} 거시지표가 비어 있다`);
  }
  showAlerts(warnings);
}

runAll([loadMarket]);

if (isLive()) {
  window.setInterval(() => runAll([loadMarket]), REFRESH_MS);
}
