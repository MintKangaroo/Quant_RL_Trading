/* 뉴스·일정 탭 — 월별 일정 달력 (지표 발표 · 실적 발표).
 *
 * 그리는 규칙:
 * - 칸 하나에 최대 3건, 나머지는 "+n". 하루를 누르면 아래에 전부 펼친다.
 * - 국장 실적은 **예상**이다 — 라벨에 (예상) 이 붙고 점선 테두리.
 * - 시각은 서버가 서울 기준으로 준다. 여기서 시간대를 다시 만지지 않는다.
 * - 좁은 화면(휴대폰)에서는 격자 대신 날짜별 목록으로 흐른다(CSS).
 */
const SCHED_WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
let schedMonth = null;

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
  const now = new Date();
  const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
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
      `<div class="sched-cell${items.length ? " pick" : ""}${dow === 0 || dow === 6 ? " wk" : ""}${past ? " past" : ""}${key === todayKey ? " today" : ""}" data-date="${key}">
        <b class="d">${d}<small>${SCHED_WEEKDAYS[dow]}</small></b>${shown}${more}
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

function bindScheduleDay(root, target, data) {
  root.onclick = (event) => {
    const cell = event.target.closest(".sched-cell.pick");
    if (!cell) return;
    root.querySelectorAll(".sched-cell.on").forEach((el) => el.classList.remove("on"));
    cell.classList.add("on");
    const items = data.days[cell.dataset.date] || [];
    target.innerHTML = `<b>${cell.dataset.date}</b>` + items.map((i) => schedItem(i, { full: true })).join("");
    target.hidden = false;
  };
}

async function loadSchedule(month) {
  const query = month ? `headlines/schedule?month=${month}` : "headlines/schedule";
  const body = await fetchJson(query);
  const data = body.data;
  schedMonth = data.month;
  const grid = document.getElementById("schedule");
  const day = document.getElementById("sched-day");
  if (!grid) return;
  day.hidden = true;
  renderSchedule(grid, data);
  bindScheduleDay(grid, day, data);
  const prev = document.getElementById("sched-prev");
  const next = document.getElementById("sched-next");
  if (prev) prev.onclick = () => loadSchedule(data.prev);
  if (next) next.onclick = () => loadSchedule(data.next);
}
