/* 마켓 — 지금 시장이 어떤 상태인가.
 *
 * 공통 규약은 scope.js 에 있다. 폴링은 dashboard.md §8 대로 장중 5초 /
 * 장외 1분인데, 이 화면은 시황이지 체결 화면이 아니라서 1분으로 고정한다 —
 * 초 단위로 지수를 흔들면 화면이 시세창처럼 보이고, 이 화면의 질문은
 * "지금 얼마인가" 지 "지금 이 순간" 이 아니다.
 */

const REFRESH_MS = 60_000;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function isLive() {
  return !new URLSearchParams(window.location.search).get("as_of");
}

const MARKET_NAME = { KR: "한국", US: "미국" };
const market = (code) => MARKET_NAME[code] || code;

const signClass = (v) => (v === null || v === undefined ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
/* 부호를 색만으로 말하지 않는다. 색각 이상에서 초록·빨강은 같은 회색이다. */
const arrow = (v) => (v === null || v === undefined || v === 0 ? "" : v > 0 ? "▲ " : "▼ ");

function stamp(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.valueOf())) return "—";
  return at.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

/* 지수 entity_id 에서 사람이 읽는 이름만 뗀다. "KR:IDX:KRX TMI" → "KRX TMI". */
function indexLabel(entityId) {
  const idx = entityId.indexOf("IDX:");
  return idx === -1 ? entityId : entityId.slice(idx + 4);
}

function indexCard(row) {
  return `<div class="kpi">
    <div class="kpi-label">${esc(indexLabel(row.entity_id))}
      <span class="chip chip-${row.market.toLowerCase()}">${esc(market(row.market))}</span>
    </div>
    <div class="kpi-value ${signClass(row.change)}">${dec(row.close, 2)}</div>
    <div class="kpi-note ${signClass(row.change)}">${arrow(row.change)}${pct(row.change)}</div>
  </div>`;
}

function renderKpis(body) {
  const d = body.data;
  const cards = d.indices.highlights.map(indexCard);
  cards.push(
    kpi(
      "원달러",
      d.fx.rate === null ? "—" : num(Math.round(d.fx.rate)),
      d.fx.change === null ? "환율 없음" : `${arrow(d.fx.change)}${pct(d.fx.change)}`,
      false,
      { tone: signClass(d.fx.change), spark: d.fx.rates }
    )
  );
  document.getElementById("kpis").innerHTML = cards.length
    ? cards.join("")
    : `<p class="empty">지수·환율이 창고에 없다.</p>`;
}

function renderIndices(body) {
  const rows = body.data.indices.table;
  document.getElementById("indices-count").textContent = `${rows.length}종`;
  if (!rows.length) {
    document.getElementById("indices").innerHTML =
      `<p class="empty">지수가 없다. 백필(bf-indices)이 돌았는지 확인할 것.</p>`;
    return;
  }
  const head = `<thead><tr><th>지수</th><th class="r">종가</th><th class="r">등락률</th></tr></thead>`;
  const body_ = rows
    .map(
      (row) => `<tr>
        <td><span class="name">${esc(indexLabel(row.entity_id))}</span>
            <span class="chip chip-${row.market.toLowerCase()}">${esc(market(row.market))}</span></td>
        <td class="r mono">${dec(row.close, 2)}</td>
        <td class="r mono ${signClass(row.change)}">${arrow(row.change)}${pct(row.change)}</td>
      </tr>`
    )
    .join("");
  document.getElementById("indices").innerHTML = `<table>${head}<tbody>${body_}</tbody></table>`;
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
    xAxis: { type: "category", data: f.sessions, ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [
      { name: "USDKRW", type: "line", data: f.rates, showSymbol: false,
        lineStyle: { color: COLOR.accent, width: 1.6 },
        areaStyle: { color: COLOR.accent, opacity: 0.08 } },
    ],
  });
}

function leaderTable(rows, marketCode) {
  if (!rows.length) {
    return marketCode === "US"
      ? `<p class="empty">미장 시가총액은 아직 수집되지 않았다 — market_cap = 주가 ×
          상장주식수인데, 미장 상장주식수(shares outstanding) 를 받는 수집기가 아직
          없다. 국장은 KRX Open API 로 채운다.</p>`
      : `<p class="empty">market_stats 에 오늘 시총이 없다.</p>`;
  }
  const head = `<thead><tr><th>종목</th><th class="r">시가총액</th><th class="r">등락률</th></tr></thead>`;
  const body_ = rows
    .map(
      (row) => `<tr>
        <td><span class="name">${esc(row.name)}</span><span class="code">${esc(row.entity_id)}</span></td>
        <td class="r mono">${num(Math.round(row.market_cap / 1e8))}억</td>
        <td class="r mono ${signClass(row.change)}">${row.change === null ? "—" : arrow(row.change) + pct(row.change)}</td>
      </tr>`
    )
    .join("");
  return `<table>${head}<tbody>${body_}</tbody></table>`;
}

function renderLeaders(body) {
  const l = body.data.leaders;
  document.getElementById("leaders-kr-count").textContent = `${l.KR.length}종목`;
  document.getElementById("leaders-us-count").textContent = `${l.US.length}종목`;
  document.getElementById("leaders-kr").innerHTML = leaderTable(l.KR, "KR");
  document.getElementById("leaders-us").innerHTML = leaderTable(l.US, "US");
}

function renderMacro(body) {
  const rows = body.data.macro;
  const target = document.getElementById("macro");
  if (!rows.length) {
    target.innerHTML = `<p class="empty">발표된 거시지표가 없다. tools/collect_macro.py 를 확인할 것.</p>`;
    return;
  }
  const head = `<thead><tr><th>지표</th><th class="num">발표</th>
    <th class="num">실측</th><th class="num">직전</th></tr></thead>`;
  // 색을 쓰지 않는다 — 전월대비 변화는 손익이 아니고, 부호에 좋고 나쁨을
  // 입힐 수 없다(dashboard.md §8-2). 방향은 부호로만 보여준다.
  const body_ = rows
    .map((item) => {
      const diff = item.actual !== null && item.previous !== null ? item.actual - item.previous : null;
      const diffText = diff === null ? "" : ` (${diff > 0 ? "+" : ""}${diff.toFixed(2)})`;
      return `<tr>
        <td><span class="lead">
          <strong>${esc(item.indicator)}</strong>
          <span class="chip chip-${item.market.toLowerCase()}">${esc(market(item.market))}</span>
          <span class="sub trunc" title="${esc(item.release_name)}">${esc(item.release_name)}</span>
        </span></td>
        <td class="num">${stamp(item.scheduled_at)}</td>
        <td class="num">${item.actual === null ? "—" : num(item.actual)} ${esc(item.unit)}</td>
        <td class="num">${item.previous === null ? "—" : num(item.previous)}${diffText}</td>
      </tr>`;
    })
    .join("");
  target.innerHTML = `<table>${head}<tbody>${body_}</tbody></table>`;
}

/* -- 시가총액 트리맵 (finviz map) ------------------------------------------ */

/* 등락률 → 색. **상승 초록 · 하락 빨강** — 이 대시보드의 고정 색 규칙이다
 * (scope.js COLOR.up/down). 한국식(빨강↑)을 쓰면 다른 탭과 색이 갈리고,
 * 한 화면 안에서 색 규칙이 갈리는 것이 제일 위험하다(dashboard.md §10).
 *
 * 등락을 못 잰 종목은 회색(COLOR.dim)이다 — 0% 로 칠하면 "보합" 이라는
 * 다른 사실이 된다.
 */
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

/* 국장·미장을 완전히 다른 맵 둘로 그린다(같은 맵 안 그룹이 아니다) — 원·달러
 * 시총 스케일이 섞이면 두 시장을 나란히 비교하기 어렵다. */
function renderTreemapFor(elId, rows, isUs) {
  const target = document.getElementById(elId);
  if (!rows.length) {
    target.innerHTML = isUs
      ? `<p class="empty">미장 시가총액은 아직 수집되지 않았다 — 상장주식수(shares
          outstanding) 소스가 없어 market_cap 자체가 안 만들어진다. 국장은 KRX
          Open API 로 상장주식수를 받지만 미장은 아직 그런 수집기가 없다.</p>`
      : `<p class="empty">market_stats 에 오늘 시총이 없다.</p>`;
    return;
  }
  const data = rows.map((row) => ({
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
          + `시총 ${num(Math.round(d.value / 1e8))}억<br>${changeText}`;
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

function renderTreemap(body) {
  const t = body.data.treemap;
  const note = document.getElementById("treemap-note");
  if (note) note.textContent = `시장별 상위 ${t.top_n}종목`;
  document.getElementById("treemap-kr-count").textContent = t.KR.length ? `${t.KR.length}종목` : "";
  document.getElementById("treemap-us-count").textContent = t.US.length ? `${t.US.length}종목` : "";
  renderTreemapFor("chart-treemap-kr", t.KR, false);
  renderTreemapFor("chart-treemap-us", t.US, true);
}

async function loadMarket() {
  const body = await fetchJson("market");
  showScope(body);
  renderKpis(body);
  renderIndices(body);
  renderFxChart(body);
  renderLeaders(body);
  renderMacro(body);
  renderTreemap(body);

  const warnings = [];
  if (!body.data.indices.table.length) warnings.push("지수가 비어 있다");
  if (body.data.fx.rate === null) warnings.push("환율이 비어 있다 — NAV 평가에도 영향을 준다");
  if (!body.data.macro.length) warnings.push("거시지표가 비어 있다");
  if (!body.data.treemap.US.length) warnings.push("미장 시가총액 미수집 — 상장주식수 소스가 없다");
  showAlerts(warnings);
}

runAll([loadMarket]);

if (isLive()) {
  window.setInterval(() => runAll([loadMarket]), REFRESH_MS);
}
