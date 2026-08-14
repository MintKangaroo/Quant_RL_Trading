/* 화면 공통 — 타임머신 컨트롤과 API 호출 규약.
 *
 * 상태는 URL 쿼리스트링에만 둔다. 브라우저 저장소는 금지다 (dashboard.md §6)
 * — 브라우저에 숨은 상태가 있으면 같은 링크를 열어도 서로 다른 화면을 보게
 * 되고, "그때 그 화면" 을 재현할 수 없다.
 * (금지어를 여기 적으면 가드가 잡는다: tests/invariants/test_dashboard_bans.py)
 *
 * as_of 를 붙이는 곳을 화면마다 만들지 않는다. 한 곳에서만 만들어야 빠뜨린
 * 화면이 생기지 않는다.
 */

/* app.css 의 :root 와 같은 값이다. 차트는 CSS 변수를 못 읽으므로 여기 한 벌
   더 있다 — 한쪽만 고치면 봉과 표의 색이 갈라진다. */
const COLOR = {
  text: "#e6e9ee", muted: "#8a93a0", dim: "#5a626d", border: "#1e2733",
  panel: "#0d1219", warn: "#f5a524", up: "#22c55e", down: "#ef4444",
  bench: "#6b7280", accent: "#38bdf8",
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
  // path 가 이미 쿼리를 들고 있을 수 있다(?entity=). 구분자를 잘못 붙이면
  // as_of 가 통째로 값에 섞여 들어가고, 그러면 화면이 조용히 라이브를 본다.
  const query = params().toString();
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`/api/${path}${query ? separator + query : ""}`);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

const pct = (v, digits = 2) => (v === null || v === undefined ? "—" : (v * 100).toFixed(digits) + "%");
const num = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("ko-KR"));
const ms = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(0) + " ms");
const dec = (v, digits = 4) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));

/* 스파크라인. 축도 눈금도 없는 **모양**이다 — 정확한 값은 바로 옆에 있다.
 *
 * 점이 둘 미만이면 아무것도 그리지 않는다. 한 점을 선으로 그리면 평평한
 * 추세로 보이고, 그건 "변화가 없었다" 는 거짓말이다.
 */
function spark(values, color) {
  const points = (values || []).filter((v) => v !== null && v !== undefined && Number.isFinite(v));
  if (points.length < 2) return "";
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const path = points
    .map((v, i) => `${(i / (points.length - 1)) * 100},${28 - ((v - lo) / span) * 26}`)
    .join(" ");
  return `<span class="kpi-spark"><svg viewBox="0 0 100 30" preserveAspectRatio="none">
    <polyline points="${path}" fill="none" stroke="${color || COLOR.muted}"
              stroke-width="1.4" vector-effect="non-scaling-stroke"/>
  </svg></span>`;
}

/* KPI 카드. ``extra`` 로 스파크라인·단위·손익 색을 준다.
 *
 * tone 은 "up" | "down" 만 받는다. 손익이 아닌 값(익스포저·보유 종목 수)에
 * 색을 주면 화면 전체가 색으로 덮여 정작 손익이 안 보인다 (dashboard.md §10).
 */
function kpi(label, value, note, warn, extra = {}) {
  const tone = extra.tone ? ` ${extra.tone}` : "";
  const unit = extra.unit ? `<span class="unit">${extra.unit}</span>` : "";
  const line = extra.spark
    ? spark(extra.spark, extra.tone === "down" ? COLOR.down : extra.tone === "up" ? COLOR.up : COLOR.muted)
    : "";
  return `<div class="kpi${warn ? " warn" : ""}${tone}">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}${unit}</div>
    <div class="kpi-note">${note || ""}</div>
    ${line}
  </div>`;
}

function showScope(body) {
  document.getElementById("as-of-label").textContent =
    `as_of ${body.as_of}${body.live ? " (live)" : ""} · 창 ${body.lookback_days}일`;
}

function showAlerts(warnings) {
  const target = document.getElementById("alerts");
  target.innerHTML = warnings.length
    ? warnings.map((text) => `<div class="alert">${text}</div>`).join("")
    : `<div class="alert ok">경고 없음 — 임계치는 store.config 기준</div>`;
}

async function runAll(jobs) {
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

(function fillScopeForm() {
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
})();
