/* 수익률 캘린더 — 진짜 달력 격자. 트레이딩 탭과 별도 창이 **같은 함수**를 쓴다.
 *
 * 격자는 요일에 맞춘다. 그런데 **휴장일 칸을 "0%" 로 그리면 안 된다** —
 * 그날은 0% 였던 게 아니라 장이 없었다. 그래서 거래일이 아닌 칸은 색도 숫자도
 * 주지 않고 비워 둔다. 달력 모양은 지키면서 "쟀는데 0" 이라는 거짓말은 안 한다.
 *
 * 색은 손익이라 이 화면의 규칙을 따른다(상승 초록 · 하락 빨강). 진하기는
 * 그 창에서의 상대 크기다 — 절대 기준을 두면 변동이 작은 달이 통째로 회색이 된다.
 */

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** "2026-08" → 그 달의 일수 */
function daysInMonth(month) {
  const [year, mon] = month.split("-").map(Number);
  return new Date(year, mon, 0).getDate();
}

/** "2026-08" 1일의 요일 (0=일) */
function firstWeekday(month) {
  const [year, mon] = month.split("-").map(Number);
  return new Date(year, mon - 1, 1).getDay();
}

/** 창 전체에서의 최대 변동폭. 달마다 다시 재면 달끼리 진하기를 비교할 수 없다. */
function scaleOf(days) {
  return Math.max(...days.map((d) => Math.abs(d.return)), 1e-9);
}

function cellColor(value, scale) {
  if (value === null || value === undefined) return "transparent";
  const strength = Math.min(1, Math.abs(value) / scale);
  // app.css 가 상태 배지·게이지에 쓰는 것과 같은 패턴이다(color-mix). 진하기는
  // 값마다 다른 연속량이라 :root 에 고정 변수로 못 둔다 — 대신 섞을 비율만
  // 여기서 정하고, 실제 색 계산은 CSS 에 맡긴다. JS 는 rgb 숫자를 만들지 않는다.
  const token = value > 0 ? "--up" : value < 0 ? "--down" : "--dim";
  const mix = (14 + strength * 70).toFixed(0);
  return `color-mix(in srgb, var(${token}) ${mix}%, transparent)`;
}

/** 달 하나를 요일에 맞춘 격자로. `days` 는 그 달의 거래일만 들어온다. */
function monthGrid(month, days, scale, { compact = false } = {}) {
  const byDay = new Map(days.map((d) => [Number(d.session.slice(8, 10)), d]));
  const lead = firstWeekday(month);
  const total = daysInMonth(month);

  const cells = [];
  for (let i = 0; i < lead; i += 1) cells.push(`<span class="cal-cell none"></span>`);
  for (let day = 1; day <= total; day += 1) {
    const hit = byDay.get(day);
    if (!hit) {
      // 장이 없던 날. 숫자도 색도 주지 않는다 — 0% 로 읽히면 안 된다.
      cells.push(`<span class="cal-cell none"><i class="d">${day}</i></span>`);
      continue;
    }
    const body = compact
      ? ""
      : `<b class="r">${(hit.return * 100).toFixed(2)}%</b>`;
    cells.push(
      `<span class="cal-cell" style="background:${cellColor(hit.return, scale)}"
             title="${hit.session} · ${(hit.return * 100).toFixed(2)}% · NAV ${Math.round(hit.nav).toLocaleString()}">
         <i class="d">${day}</i>${body}
       </span>`,
    );
  }
  return `<div class="cal-week">${WEEKDAYS.map((w) => `<span>${w}</span>`).join("")}</div>
          <div class="cal-grid${compact ? " compact" : ""}">${cells.join("")}</div>`;
}

/**
 * 달력을 그린다.
 *   target  — 넣을 요소
 *   cal     — {days:[{session,return,nav}], months:[{month,return}]}
 *   options — {months: 그릴 달 목록(없으면 전부), compact: 숫자 없이 색만}
 */
function renderCalendar(target, cal, options = {}) {
  if (!target) return;
  const days = (cal && cal.days) || [];
  if (!days.length) {
    target.innerHTML = `<p class="empty">nav_daily 가 비어 있다. 회계 스냅샷이 아직 없다.</p>`;
    return;
  }
  const scale = scaleOf(days);
  const monthReturn = new Map(((cal && cal.months) || []).map((m) => [m.month, m.return]));

  const grouped = new Map();
  for (const day of days) {
    const key = day.session.slice(0, 7);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(day);
  }

  const wanted = options.months || [...grouped.keys()];
  target.innerHTML = wanted
    .map((month) => {
      const rows = grouped.get(month) || [];
      const total = monthReturn.get(month);
      const sign = total > 0 ? "up" : total < 0 ? "down" : "";
      const label = total === undefined ? "—" : `${(total * 100).toFixed(2)}%`;
      return `<div class="cal-month">
        <div class="cal-head">
          <span>${month}</span>
          <b class="num ${sign}">${label}</b>
        </div>
        ${monthGrid(month, rows, scale, { compact: options.compact })}
      </div>`;
    })
    .join("");
}

/** 창고에 값이 있는 달 목록. 오래된 순. */
function calendarMonths(cal) {
  return [...new Set(((cal && cal.days) || []).map((d) => d.session.slice(0, 7)))].sort();
}
