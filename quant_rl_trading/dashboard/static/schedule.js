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
    cells.push(
      `<div class="sched-cell${items.length ? " pick" : ""}${dow === 0 || dow === 6 ? " wk" : ""}" data-date="${key}">
        <b class="d">${d}<small>${SCHED_WEEKDAYS[dow]}</small></b>${shown}${more}
      </div>`,
    );
  }
  target.innerHTML =
    `<div class="sched-week">${SCHED_WEEKDAYS.map((w) => `<span>${w}</span>`).join("")}</div>` +
    `<div class="sched-grid">${cells.join("")}</div>`;
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
