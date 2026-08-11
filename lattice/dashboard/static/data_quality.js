/* Data Quality 화면.
 *
 * 상태는 URL 쿼리스트링에만 둔다. 브라우저 저장소는 금지다 (dashboard.md §6)
 * — 브라우저에 숨은 상태가 있으면 같은 링크를 열어도 서로 다른 화면을 보게
 * 되고, "그때 그 화면" 을 재현할 수 없다.
 * (금지어를 여기 적으면 가드가 잡는다: tests/invariants/test_dashboard_bans.py)
 */

const COLOR = {
  text: "#e6e9ee", muted: "#8a93a0", dim: "#5a626d", border: "#262c35",
  panel: "#161a20", warn: "#f5a524", up: "#e5484d", down: "#3e7bfa", bench: "#6b7280",
};

const AXIS = {
  axisLine: { lineStyle: { color: COLOR.border } },
  axisLabel: { color: COLOR.muted, fontFamily: "IBM Plex Mono", fontSize: 10 },
  splitLine: { lineStyle: { color: COLOR.border, opacity: 0.4 } },
};

const BASE = {
  backgroundColor: "transparent",
  animation: false, // 움직이는 대시보드는 아마추어처럼 보인다
  grid: { left: 52, right: 52, top: 18, bottom: 28 },
  tooltip: { trigger: "axis", backgroundColor: COLOR.panel, borderColor: COLOR.border,
             textStyle: { color: COLOR.text, fontFamily: "IBM Plex Mono", fontSize: 11 } },
  legend: { textStyle: { color: COLOR.muted, fontSize: 11 }, top: 0, right: 0 },
};

const charts = {};

function chart(id) {
  if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "canvas" });
  return charts[id];
}

function params() {
  const search = new URLSearchParams(window.location.search);
  const out = new URLSearchParams();
  for (const key of ["as_of", "lookback"]) {
    const value = search.get(key);
    if (value) out.set(key, value);
  }
  return out;
}

async function fetchJson(path) {
  const query = params().toString();
  const response = await fetch(`/api/data-quality/${path}${query ? "?" + query : ""}`);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

const pct = (v, digits = 2) => (v === null || v === undefined ? "—" : (v * 100).toFixed(digits) + "%");
const num = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("ko-KR"));
const ms = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(0) + " ms");

function kpi(label, value, note, warn) {
  return `<div class="kpi${warn ? " warn" : ""}">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}</div>
    <div class="kpi-note">${note || ""}</div>
  </div>`;
}

async function renderSummary() {
  const body = await fetchJson("summary");
  const d = body.data;
  const t = body.thresholds;

  document.getElementById("as-of-label").textContent =
    `as_of ${body.as_of}${body.live ? " (live)" : ""} · 창 ${body.lookback_days}일`;

  document.getElementById("kpis").innerHTML = [
    kpi("커버리지", pct(d.coverage_ratio, 1),
        `${num(d.covered_sessions)} / ${num(d.expected_sessions)} 거래일`,
        d.coverage_ratio !== null && d.coverage_ratio < t.coverage_warn),
    kpi("종목(누적)", num(d.entities_total), `현재 상장 ${num(d.listed_now)}`),
    kpi("상장폐지", num(d.delisted_total), "0이면 생존편향 의심", d.delisted_total === 0),
    kpi("close 결측", pct(d.missing_close_rate, 3), `volume ${pct(d.missing_volume_rate, 3)}`,
        d.missing_close_rate !== null && d.missing_close_rate > t.missing_warn),
    kpi("수집 지연 p90", ms(d.latency_p90_ms), `p50 ${ms(d.latency_p50_ms)} · 표본 ${num(d.latency_samples)}`,
        d.latency_p90_ms !== null && d.latency_p90_ms > t.latency_p90_warn_ms),
    kpi("수집 실패", num(d.failure_count), "최근 창 기준", d.failure_count > 0),
    kpi("prices 행수", num(d.rows_total), ""),
  ].join("");

  const alerts = document.getElementById("alerts");
  alerts.innerHTML = d.warnings.length
    ? d.warnings.map((text) => `<div class="alert">${text}</div>`).join("")
    : `<div class="alert ok">경고 없음 — 임계치는 store.config 기준</div>`;
}

async function renderCoverage() {
  const { data } = await fetchJson("coverage");
  chart("chart-coverage").setOption({
    ...BASE,
    xAxis: { type: "category", data: data.points.map((p) => p.session), ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [{
      name: "종목 수", type: "line", showSymbol: false,
      data: data.points.map((p) => p.entities),
      lineStyle: { width: 1.4, color: COLOR.down },
      areaStyle: { color: COLOR.down, opacity: 0.08 },
    }],
  }, true);
}

async function renderMissing() {
  const { data } = await fetchJson("missing");
  chart("chart-missing").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["close", "volume"] },
    xAxis: { type: "category", data: data.points.map((p) => p.session), ...AXIS },
    yAxis: {
      type: "value", ...AXIS,
      axisLabel: { ...AXIS.axisLabel, formatter: (v) => (v * 100).toFixed(2) + "%" },
    },
    series: [
      { name: "close", type: "line", showSymbol: false, data: data.points.map((p) => p.close),
        lineStyle: { width: 1.4, color: COLOR.up } },
      { name: "volume", type: "line", showSymbol: false, data: data.points.map((p) => p.volume),
        lineStyle: { width: 1.4, color: COLOR.bench } },
    ],
  }, true);
}

async function renderUniverse() {
  const { data } = await fetchJson("universe");
  chart("chart-universe").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["상장", "상폐 누적"] },
    xAxis: { type: "category", data: data.points.map((p) => p.session), ...AXIS },
    yAxis: [
      { type: "value", scale: true, ...AXIS },
      { type: "value", scale: true, ...AXIS },
    ],
    series: [
      { name: "상장", type: "line", showSymbol: false, data: data.points.map((p) => p.listed),
        lineStyle: { width: 1.4, color: COLOR.text } },
      { name: "상폐 누적", type: "line", yAxisIndex: 1, showSymbol: false,
        data: data.points.map((p) => p.delisted_cumulative),
        lineStyle: { width: 1.4, color: COLOR.warn, type: "dashed" } },
    ],
  }, true);
}

async function renderLatency() {
  const { data } = await fetchJson("latency");
  const stages = data.stages.map((s) => s.stage);
  chart("chart-latency").setOption({
    ...BASE,
    grid: { ...BASE.grid, left: 72 },
    legend: { ...BASE.legend, data: ["p50", "p90", "p99"] },
    tooltip: { ...BASE.tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", ...AXIS },
    yAxis: { type: "category", data: stages, ...AXIS },
    series: ["p50", "p90", "p99"].map((key, index) => ({
      name: key, type: "bar",
      data: data.stages.map((s) => s[key]),
      itemStyle: { color: [COLOR.bench, COLOR.down, COLOR.warn][index] },
    })),
  }, true);
}

async function renderFailures() {
  const { data } = await fetchJson("failures");
  const target = document.getElementById("failures");
  if (!data.length) {
    target.innerHTML = `<div class="empty">최근 창에 수집 실패 없음.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>관측시각</th><th>소스</th><th>단계</th>
      <th class="num">소요(ms)</th><th>상세</th></tr></thead>
    <tbody>${data.map((row) => `<tr>
      <td class="num">${row.observed_at}</td><td>${row.source}</td><td>${row.stage}</td>
      <td class="num">${row.elapsed_ms.toFixed(0)}</td><td>${row.detail}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function refresh() {
  const jobs = [renderSummary, renderCoverage, renderMissing, renderUniverse,
                renderLatency, renderFailures];
  for (const job of jobs) {
    try {
      await job();
    } catch (error) {
      document.getElementById("alerts").innerHTML +=
        `<div class="alert">${job.name}: ${error.message}</div>`;
    }
  }
}

document.getElementById("scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const query = new URLSearchParams();
  const asOf = document.getElementById("as-of").value;
  const lookback = document.getElementById("lookback").value;
  // datetime-local 은 타임존이 없다. 브라우저의 오프셋을 붙여서 보낸다 —
  // 타임존 없는 as_of 는 API 가 거부한다.
  if (asOf) query.set("as_of", new Date(asOf).toISOString());
  if (lookback) query.set("lookback", lookback);
  window.location.search = query.toString();
});

document.getElementById("live").addEventListener("click", () => {
  window.location.search = "";
});

window.addEventListener("resize", () => {
  Object.values(charts).forEach((instance) => instance.resize());
});

(function init() {
  const search = new URLSearchParams(window.location.search);
  const asOf = search.get("as_of");
  if (asOf) {
    const local = new Date(asOf);
    if (!Number.isNaN(local.valueOf())) {
      const offset = local.getTimezoneOffset() * 60000;
      document.getElementById("as-of").value =
        new Date(local.valueOf() - offset).toISOString().slice(0, 16);
    }
  }
  const lookback = search.get("lookback");
  if (lookback) document.getElementById("lookback").value = lookback;
  refresh();
})();
