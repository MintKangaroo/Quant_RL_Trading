/* 뉴스·일정 탭 — 월별 일정 달력 (지표 발표 · 실적 발표).
 *
 * 그리는 규칙:
 * - 칸 하나에 최대 3건, 나머지는 "+n". 하루를 누르면 아래에 전부 펼친다.
 * - 국장 실적은 **예상**이다 — 라벨에 (예상) 이 붙고 점선 테두리.
 * - 시각은 서버가 서울 기준으로 준다. 여기서 시간대를 다시 만지지 않는다.
 * - 좁은 화면(휴대폰)에서는 격자 대신 날짜별 목록으로 흐른다(CSS).
 * - **오늘 = as_of 의 서울 날짜**(서버가 `today` 로 준다 — 보는 기기의 날짜가 아니다). 되감으면 그날이 오늘이다.
 *   오늘 칸을 강조하고, 페이지를 열면 아래 상세에 오늘 일정이 바로 펼쳐진다(2026-10-13 밤 사용자 요청).
 */
const SCHED_WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
let schedMonth = null;
/** 마지막으로 그린 일정 응답 — 중요 시황 KPI(오늘 일정 수)가 같은 응답을 센다(다시 부르지 않는다). */
let schedLastData = null;

function schedEsc(v) {
  return String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function schedItem(item, { full = false } = {}) {
  const cls = `sched-item ${item.kind}${item.estimated ? " est" : ""}${item.importance >= 3 ? " hi" : ""}`;
  const time = item.time ? `<i>${item.time}</i>` : `<i>—</i>`;
  const market = `<em>${item.market}</em>`;
  const detail = full && item.detail ? `<small>${schedEsc(item.detail)}</small>` : "";
  return `<div class="${cls}" title="${schedEsc(item.detail || "")}">${time}${market}<span>${schedEsc(item.label)}</span>${detail}</div>`;
}

function renderSchedule(target, data) {
  const [year, mon] = data.month.split("-").map(Number);
  const todayKey = schedToday(data);
  let pastWithItems = 0;
  const first = new Date(year, mon - 1, 1);
  const total = new Date(year, mon, 0).getDate();
  const cells = [];
  for (let i = 0; i < first.getDay(); i += 1) cells.push(`<div class="sched-cell none"></div>`);
  for (let d = 1; d <= total; d += 1) {
    const key = `${data.month}-${String(d).padStart(2, "0")}`;
    const items = data.days[key] || [];
    const shown = items.slice(0, 3).map((i) => schedItem(i)).join("");
    const more = items.length > 3 ? `<div class="sched-more">+${items.length - 3}</div>` : "";
    const dow = new Date(year, mon - 1, d).getDay();
    const past = key < todayKey;
    cells.push(
      `<div class="sched-cell${items.length || key === todayKey ? " pick" : ""}${dow === 0 || dow === 6 ? " wk" : ""}${past ? " past" : ""}${key === todayKey ? " today" : ""}" data-date="${key}">
        <b class="d">${d}<small>${SCHED_WEEKDAYS[dow]}</small>${key === todayKey ? `<em class="sched-today-tag">오늘</em>` : ""}</b>${shown}${more}
      </div>`,
    );
  }
  for (const key of Object.keys(data.days)) if (key < todayKey && key.startsWith(data.month)) pastWithItems += 1;
  // 휴대폰은 격자가 아니라 날짜별 목록이다(CSS). 지난 날짜는 접어 두고 오늘부터 보인다 —
  // 8/30 에 8/2 일정부터 스크롤하게 두면 "오늘 뭐 있지" 를 못 본다(2026-08-30 실측).
  const fold = pastWithItems
    ? `<button type="button" class="linky sched-fold" data-fold="past">지난 ${pastWithItems}일 보기</button>`
    : "";
  target.innerHTML =
    fold +
    `<div class="sched-week">${SCHED_WEEKDAYS.map((w) => `<span>${w}</span>`).join("")}</div>` +
    `<div class="sched-grid">${cells.join("")}</div>`;
  const button = target.querySelector(".sched-fold");
  if (button) {
    button.onclick = () => {
      target.classList.toggle("show-past");
      button.textContent = target.classList.contains("show-past") ? "지난 날짜 접기" : `지난 ${pastWithItems}일 보기`;
    };
  }
  const label = document.getElementById("sched-label");
  if (label) {
    label.textContent = `${data.month} · 지표 ${data.counts.macro} · 실적 ${data.counts.earnings}` +
      (data.counts.estimated ? ` (예상 ${data.counts.estimated})` : "");
  }
}

/** as_of 의 서울 날짜. 서버가 준 값이 우선 — 없으면(옛 응답) as_of 를 서울 날짜로 바꾼다. 기기 날짜는 쓰지 않는다. */
function schedToday(data) {
  if (data && data.today) return data.today;
  return (typeof kstDate === "function" && data && data.as_of) ? kstDate(data.as_of) : "";
}

/** 하루 상세 — 그 날 일정 전부. 오늘이면 '오늘' 을 붙이고, 비었으면 비었다고 적는다. */
function schedDayDetail(target, data, key) {
  const items = data.days[key] || [];
  const today = key === schedToday(data);
  const head = `<b>${key}${today ? ` <em class="sched-today-tag">오늘</em>` : ""}</b>`;
  target.innerHTML = head + (items.length
    ? items.map((i) => schedItem(i, { full: true })).join("")
    : `<p class="empty">${today ? "오늘" : "이날"} 등록된 일정이 없다.</p>`);
  target.hidden = false;
}

function bindScheduleDay(root, target, data) {
  root.onclick = (event) => {
    const cell = event.target.closest(".sched-cell.pick");
    if (!cell) return;
    root.querySelectorAll(".sched-cell.on").forEach((el) => el.classList.remove("on"));
    cell.classList.add("on");
    schedDayDetail(target, data, cell.dataset.date);
  };
  // 페이지를 열면 오늘이 펼쳐져 있다 — 보고 있는 달에 오늘이 있을 때만(다른 달로 넘기면 접는다).
  // 폰 목록은 오늘 칸이 이미 맨 위에 전부 펼쳐져 있어 아래에 한 번 더 적지 않는다.
  const today = schedToday(data);
  const cell = today && root.querySelector ? root.querySelector(`.sched-cell[data-date="${today}"]`) : null;
  if (cell && !SCHED_MOBILE.matches) {
    cell.classList.add("on");
    schedDayDetail(target, data, today);
  }
}

/** 날짜별 일정 수 — 지표·실적 쌓은 막대, 오늘(as_of 서울 날짜) 강조(§8-5). 응답에 있는 날만 센다 — 계산하지 않는다. */
function schedDrawChart(data) {
  const el = document.getElementById("hl-sched-chart");
  const cap = document.getElementById("hl-sched-cap");
  if (!el || typeof chart !== "function") return;
  const keys = Object.keys(data.days || {}).sort();
  if (!keys.length) { el.style.display = "none"; if (cap) cap.innerHTML = ""; return; }
  el.style.display = "";
  const today = schedToday(data);
  const count = (key, kind) => (data.days[key] || []).filter((i) => i.kind === kind).length;
  if (cap && typeof vizCaption === "function") {
    cap.innerHTML = vizCaption([[COLOR.accent, "지표 발표"], [COLOR.muted, "실적 발표"]], `날짜별 건수 · 오늘 ${today ? today.slice(5) : "—"} 굵게`);
  }
  chart("hl-sched-chart").setOption({
    ...BASE, legend: { show: false },
    tooltip: { ...BASE.tooltip, trigger: "axis", formatter: (ps) => { const k = keys[ps[0].dataIndex]; return `${k}${k === today ? " (오늘)" : ""}<br>지표 ${count(k, "macro")} · 실적 ${count(k, "earnings")}`; } },
    grid: { left: 30, right: 6, top: 6, bottom: 20 },
    xAxis: { type: "category", data: keys.map((k) => k.slice(5)), ...AXIS, axisTick: { show: false },
             axisLabel: { ...AXIS.axisLabel, formatter: (v) => (today && v === today.slice(5) ? `{t|${v}}` : v),
                          rich: { t: { color: COLOR.accent, fontWeight: 600, fontFamily: "IBM Plex Mono", fontSize: 10 } } } },
    yAxis: { type: "value", ...AXIS, minInterval: 1 },
    series: [
      { type: "bar", stack: "d", barMaxWidth: 14, data: keys.map((k) => ({ value: count(k, "macro"), itemStyle: { color: COLOR.accent, opacity: k === today ? 1 : 0.55 } })) },
      { type: "bar", stack: "d", barMaxWidth: 14, data: keys.map((k) => ({ value: count(k, "earnings"), itemStyle: { color: COLOR.muted, opacity: k === today ? 1 : 0.55 } })) },
    ],
  }, true);
}

const SCHED_MOBILE = window.matchMedia ? window.matchMedia("(max-width: 640px)") : { matches: false };

/* 휴대폰 — 오늘부터 30일 목록. 달을 넘겨 이어 붙인다(월말에 "이달" 은 비어 보인다). */
function renderUpcoming(target, data) {
  const today = schedToday(data);
  // 오늘은 일정이 없어도 맨 위에 선다 — '오늘 뭐 있지' 의 답이 '없다' 인 것도 답이다.
  const days = Object.keys(data.days);
  if (today && !days.includes(today)) days.unshift(today);
  days.sort();
  if (!days.length) {
    target.innerHTML = `<p class="empty">${data.from}~${data.to} 사이 등록된 일정이 없다.</p>`;
  } else {
    target.innerHTML = `<div class="sched-list">${days.map((key) => {
      const d = new Date(`${key}T00:00:00`);
      const label = `${d.getMonth() + 1}/${d.getDate()} <small>${SCHED_WEEKDAYS[d.getDay()]}</small>`;
      const items = data.days[key] || [];
      const isToday = key === today;
      return `<div class="sched-cell pick${isToday ? " today" : ""}" data-date="${key}"><b class="d">${label}${isToday ? `<em class="sched-today-tag">오늘</em>` : ""}</b>${items.length ? items.map((i) => schedItem(i)).join("") : `<span class="sched-none">등록된 일정 없음</span>`}</div>`;
    }).join("")}</div>`;
  }
  const label = document.getElementById("sched-label");
  if (label) label.textContent = `${data.from.slice(5)}~${data.to.slice(5)} · 지표 ${data.counts.macro} · 실적 ${data.counts.earnings}`;
  for (const id of ["sched-prev", "sched-next"]) {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  }
}

async function loadSchedule(month) {
  if (SCHED_MOBILE.matches) {
    const body = await fetchJson("headlines/schedule?days=30");
    const grid = document.getElementById("schedule");
    const day = document.getElementById("sched-day");
    if (!grid) return;
    day.hidden = true;
    schedLastData = body.data;
    renderUpcoming(grid, body.data);
    schedDrawChart(body.data);
    bindScheduleDay(grid, day, body.data);
    return;
  }
  const query = month ? `headlines/schedule?month=${month}` : "headlines/schedule";
  const body = await fetchJson(query);
  const data = body.data;
  schedMonth = data.month;
  const grid = document.getElementById("schedule");
  const day = document.getElementById("sched-day");
  if (!grid) return;
  day.hidden = true;
  schedLastData = data;
  renderSchedule(grid, data);
  schedDrawChart(data);
  bindScheduleDay(grid, day, data);
  const prev = document.getElementById("sched-prev");
  const next = document.getElementById("sched-next");
  if (prev) prev.onclick = () => loadSchedule(data.prev);
  if (next) next.onclick = () => loadSchedule(data.next);
}
