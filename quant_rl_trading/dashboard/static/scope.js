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

function kpi(label, value, note, warn) {
  return `<div class="kpi${warn ? " warn" : ""}">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}</div>
    <div class="kpi-note">${note || ""}</div>
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
