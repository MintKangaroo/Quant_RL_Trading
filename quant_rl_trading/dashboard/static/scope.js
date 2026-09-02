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

/* 캔버스·SVG 차트는 CSS 변수를 직접 못 읽는다(echarts style 값, rgba() 보간
   등). 그렇다고 여기 hex 를 다시 적으면 app.css 의 :root 를 고쳤을 때 이
   파일만 옛 색으로 남는다 — 그래서 리터럴을 두지 않고 :root 에서 매번
   읽는다. */
function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const COLOR = {
  text: token("--text"), muted: token("--muted"), dim: token("--dim"), border: token("--border"),
  panel: token("--panel"), panel2: token("--panel2"),
  warn: token("--warn"), up: token("--up"), down: token("--down"),
  bench: token("--bench"), accent: token("--accent"),
  ok: token("--ok"), bad: token("--bad"),
  // 범주 팔레트 — 종목·시리즈 **구분**용, 손익 의미 없음 (:root --s1~--s6).
  // learning IC 차트와 포지션 도넛이 같은 순서로 쓴다. 하드코딩하면 화면마다 갈린다.
  series: ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"].map(token),
};

const AXIS = {
  axisLine: { lineStyle: { color: COLOR.border } },
  axisLabel: { color: COLOR.muted, fontFamily: "IBM Plex Mono", fontSize: 10 },
  splitLine: { lineStyle: { color: COLOR.border, opacity: 0.4 } },
};

const BASE = {
  backgroundColor: "transparent",
  animation: false, // 움직이는 대시보드는 아마추어처럼 보인다 (reduced-motion 배려이기도 하다)
  // 스크린리더가 차트 내용을 읽을 수 있게 한다 — ECharts 가 시리즈 요약을
  // aria-label 로 만들어 붙인다. 장중 수시 확인은 휴대폰이고(PRODUCT.md),
  // 접근성 트리는 iOS VoiceOver 가 그대로 쓴다.
  aria: { enabled: true },
  grid: { left: 52, right: 52, top: 18, bottom: 28 },
  // confine — 툴팁이 화면 밖으로 나가지 않는다. 좁은 휴대폰에서 오른쪽 끝
  // 점을 탭하면 툴팁이 잘려 안 보이던 것. triggerOn 에 click 을 더해 터치
  // (탭)에서도 툴팁이 뜬다 — 모바일엔 mousemove 가 없다.
  tooltip: { trigger: "axis", triggerOn: "mousemove|click", confine: true,
             backgroundColor: COLOR.panel, borderColor: COLOR.border,
             textStyle: { color: COLOR.text, fontFamily: "IBM Plex Mono", fontSize: 11 } },
  legend: { textStyle: { color: COLOR.muted, fontSize: 11 }, top: 0, right: 0 },
};

const charts = {};

/* 차트 테마 — 축·격자·글자를 토큰으로. 각 화면이 AXIS 를 안 펴면 ECharts 기본
   (#E0E6F1 흰 격자·#6E7079 축)이 그대로 나와 검은 시트 위에서 혼자 밝았다
   (2026-09-02 학습 탭 실측). init 에 테마를 물리면 옵션이 빠진 차트도 같은
   선을 쓴다 — 옵션에 적힌 값이 있으면 그쪽이 이긴다. */
const SHEET_THEME = {
  backgroundColor: "transparent",
  textStyle: { color: COLOR.muted, fontFamily: "IBM Plex Mono" },
  categoryAxis: { axisLine: { lineStyle: { color: COLOR.border } }, axisTick: { lineStyle: { color: COLOR.border } },
                  axisLabel: { color: COLOR.muted, fontFamily: "IBM Plex Mono", fontSize: 10 },
                  splitLine: { lineStyle: { color: COLOR.border, opacity: 0.6 } } },
  valueAxis:    { axisLine: { lineStyle: { color: COLOR.border } }, axisTick: { lineStyle: { color: COLOR.border } },
                  axisLabel: { color: COLOR.muted, fontFamily: "IBM Plex Mono", fontSize: 10 },
                  splitLine: { lineStyle: { color: COLOR.border, opacity: 0.6 } } },
  timeAxis:     { axisLine: { lineStyle: { color: COLOR.border } }, axisLabel: { color: COLOR.muted, fontFamily: "IBM Plex Mono", fontSize: 10 },
                  splitLine: { lineStyle: { color: COLOR.border, opacity: 0.6 } } },
  legend: { textStyle: { color: COLOR.muted } },
  tooltip: { backgroundColor: COLOR.panel2, borderColor: COLOR.border, textStyle: { color: COLOR.text } },
};
// 등록은 **첫 차트를 만들 때** 한다. 이 파일은 echarts.min.js(defer) 보다 먼저
// 실행될 수 있어 파일 상단에서 부르면 echarts 가 아직 없다(2026-09-02 실측: 등록이
// 조용히 건너뛰어져 학습 탭 격자가 흰색 기본값이었다). 렌더 테스트의 스텁에는
// registerTheme 이 없으므로 있을 때만 부른다.
let sheetThemeReady = false;
function ensureSheetTheme() {
  if (sheetThemeReady) return;
  if (typeof echarts !== "undefined" && typeof echarts.registerTheme === "function") echarts.registerTheme("sheet", SHEET_THEME);
  sheetThemeReady = true;
}

function chart(id) {
  if (!charts[id]) {
    ensureSheetTheme();
    charts[id] = echarts.init(document.getElementById(id), "sheet", { renderer: "canvas" });
  }
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
  // 각주는 두 줄에서 잘린다(sheet.css). 잘려도 마우스를 올리면 전문이 뜬다 —
  // 잘린 글을 못 읽게 두는 것과 화면을 각주로 채우는 것 사이의 절충이다.
  const plain = String(note || "").replace(/<[^>]+>/g, "").replace(/"/g, "&quot;");
  return `<div class="kpi${warn ? " warn" : ""}${tone}">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}${unit}</div>
    <div class="kpi-note" title="${plain}">${note || ""}</div>
    ${line}
  </div>`;
}

/* 시점 표시. **지금을 보고 있을 때는 아무 말도 하지 않는다.**

   전에는 라이브에도 `as_of 2026-08-18T07:21:40.413669+00:00 (live) · 창 90일`
   을 늘 찍었다. 모바일에서 두 줄을 먹었고, 매번 같은 말이라 아무도 안 읽는다.
   읽히지 않는 경고는 경고가 아니다.

   되감았을 때만, 그때는 **눈에 띄게** 말한다 — 화면이 과거를 보여주는데 그
   사실을 모르면 지금이라고 착각한 채로 판단하게 된다(불변식 9). 그래서
   조건을 뒤집었지 지운 것이 아니다. */
function showScope(body) {
  const label = document.getElementById("as-of-label");
  if (body.live) {
    label.hidden = true;
    return;
  }
  label.hidden = false;
  label.classList.add("rewound");
  const stamp = String(body.as_of).replace("T", " ").slice(0, 16);
  label.textContent = `${stamp} 시점을 보고 있다 · 창 ${body.lookback_days}일`;
}

/** 데이터 기준일 띠. **날짜 없는 숫자는 없다** — 어느 세션 값인지, 늦었으면 며칠인지. */
async function renderFreshness() {
  const target = document.getElementById("freshness");
  if (!target) return;
  const body = await fetchJson("system/freshness");
  const items = (body.data && body.data.items) || [];
  if (!items.length) return;
  const md = (iso) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : "—");
  target.innerHTML = items.map((it) => {
    // 상태는 글리프가 아니라 앞의 점(CSS ::before)이 말한다 — ✓·⚠ 같은 문자
    // 기호는 글꼴마다 다르게 그려져 화면이 들쭉날쭉해진다.
    if (it.status === "ok") return `<span class="fresh ok">${it.label} ${md(it.observed)}</span>`;
    if (it.status === "stale") return `<span class="fresh stale">${it.label} ${md(it.observed)} · ${it.lag_sessions}세션 지연 (기대 ${md(it.expected)})</span>`;
    return `<span class="fresh unknown">${it.label} 없음</span>`;
  }).join("");
  target.hidden = false;
}
if (typeof document !== "undefined" && document.getElementById("freshness")) {
  renderFreshness().catch(() => {});
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

window.addEventListener("resize", () => {
  Object.values(charts).forEach((instance) => instance.resize());
});

/* 타임머신 폼은 헤더에서 걷어냈다(2026-08-18). **되감기 자체는 살아 있다** —
   ``?as_of=...&lookback=...`` 를 URL 에 붙이면 params() 가 그대로 읽어
   모든 /api 호출에 실어 보낸다.

   폼을 지우면서 그 폼을 잡던 핸들러도 같이 지웠다. 남겨 두면
   ``getElementById("scope-form")`` 이 null 을 주고 거기서 이 파일 전체가
   죽는다 — scope.js 는 모든 탭이 먼저 읽는 파일이라 화면 아홉 개가 한꺼번에
   빈다. 오늘 마켓 탭이 정의 없는 함수 하나로 통째로 죽은 것과 같은 종류다.

   되감은 상태는 as-of 배지가 계속 말해 준다(fillScope 의 `(live)` 표시). */
