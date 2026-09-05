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
      `<span class="cal-cell pick" data-session="${hit.session}"
             data-return="${hit.return}"
             style="background:${cellColor(hit.return, scale)}"
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


/* -- 월 브라우저 -------------------------------------------------------------
 *
 * ‹ › 로 한 달씩 옮겨 보는 달력 + 월별 표. **트레이딩 탭과 별도 창이 같은
 * 것을 그린다** — 탭에서 최근 한 달만 색으로 보여주던 축소판은 없앴다.
 * 사람이 [표시하기] 를 눌러서 보던 화면이 원래 보고 싶던 화면이었다면,
 * 한 번 더 누르게 할 이유가 없다.
 *
 * ``mount`` 는 DOM 요소 세 개를 받는다. 어느 화면이든 그 세 자리만 있으면
 * 된다 — 트레이딩 탭은 KPI 자리를 안 주고, 별도 창은 준다.
 */
function mountCalendarBrowser(cal, refs = {}) {
  const months = calendarMonths(cal);
  // 최근 달부터 본다. 사람이 궁금한 것은 거의 언제나 이번 달이다.
  let cursor = months.length - 1;

  const monthTotal = (month) => {
    const hit = (cal.months || []).find((m) => m.month === month);
    return hit ? hit.return : null;
  };

  function paint() {
    if (!months.length) {
      if (refs.label) refs.label.textContent = "—";
      renderCalendar(refs.grid, { days: [], months: [] });
      return;
    }
    cursor = Math.max(0, Math.min(cursor, months.length - 1));
    const month = months[cursor];
    if (refs.label) {
      const total = monthTotal(month);
      refs.label.textContent =
        total === null ? month : `${month} · ${(total * 100).toFixed(2)}%`;
    }
    renderCalendar(refs.grid, cal, { months: [month] });

    // 끝에 닿으면 버튼을 끈다. 눌리는데 아무 일도 안 나는 버튼은 고장으로 읽힌다.
    if (refs.prev) refs.prev.disabled = cursor === 0;
    if (refs.next) refs.next.disabled = cursor === months.length - 1;
  }

  function paintMonths() {
    if (!refs.months) return;
    const rows = (cal.months || []).slice().reverse();
    if (!rows.length) {
      refs.months.innerHTML = `<p class="empty">nav_daily 가 비어 있다.</p>`;
      return;
    }
    refs.months.innerHTML = `<table class="tbl"><tr><th>월</th><th class="r">수익률</th><th>　</th></tr>${rows
      .map((row, index) => {
        const sign = row.return > 0 ? "up" : row.return < 0 ? "down" : "";
        return `<tr data-month="${row.month}">
          <td class="num">${row.month}</td>
          <td class="num r ${sign}">${pct(row.return)}</td>
          <td><button type="button" class="linky" data-goto="${rows.length - 1 - index}">보기</button></td>
        </tr>`;
      })
      .join("")}</table>`;
    refs.months.querySelectorAll("button[data-goto]").forEach((button) => {
      button.addEventListener("click", () => {
        cursor = Number(button.dataset.goto);
        paint();
      });
    });
  }

  // **누를 때마다 다시 매달지 않는다.** loadTrading 이 여러 번 돌면 한 번의
  // 클릭이 여러 달을 건너뛰게 된다.
  const bind = (element, delta) => {
    if (!element || element.dataset.calBound) return;
    element.dataset.calBound = "1";
    element.addEventListener("click", () => {
      cursor += delta;
      paint();
    });
  };
  bind(refs.prev, -1);
  bind(refs.next, +1);

  paintMonths();
  paint();
}


/* -- 하루를 누르면 그날 시장 -------------------------------------------------
 *
 * "내가 그날 몇 % 였나" 만으로는 잘한 건지 알 수 없다. 시장이 -3% 인 날의
 * -1% 와 +2% 인 날의 -1% 는 완전히 다른 사실이다. 그래서 같은 날의 지수를
 * 나란히 놓는다.
 *
 * **작게 둔다.** 이건 달력을 읽다가 곁눈으로 보는 값이지 주인공이 아니다.
 *
 * 지수와 우리 수익률을 **한 칸에 섞지 않는다** — 지수는 가격지수(배당 미반영)
 * 이고 우리는 TWR 이다. 빼서 "초과수익" 을 만들면 그 숫자는 근거가 없다.
 */
function bindDayDetail(root, cal, target) {
  if (!root || !target) return;
  const indices = (cal && cal.indices) || {};

  root.addEventListener("click", (event) => {
    const cell = event.target.closest(".cal-cell.pick");
    if (!cell) return;

    root.querySelectorAll(".cal-cell.on").forEach((el) => el.classList.remove("on"));
    cell.classList.add("on");

    const session = cell.dataset.session;
    const mine = Number(cell.dataset.return);
    const market = indices[session] || {};
    const names = Object.keys(market);

    const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
    const fmt = (v) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;

    // 지수가 없는 날은 **없다고 적는다.** 빈칸으로 두면 "그날 시장이 안
    // 움직였다" 로 읽힌다 — 실제로는 아직 안 받았거나 휴장이다.
    const cells = names.length
      ? names.map((name) =>
          `<span class="day-idx"><i>${name}</i><b class="${cls(market[name])}">${fmt(market[name])}</b></span>`
        ).join("")
      : `<span class="day-idx none">그날 지수가 창고에 없다</span>`;

    // **어느 날의 수익인지 먼저 적는다** — 날짜 없이 "코스피 +1.53%" 만 보이면 오늘로 읽힌다
    // (2026-08-28 실측: 27일 값을 28일 시장과 비교해 "틀렸다" 가 됐다).
    target.innerHTML =
      `<span class="day-idx"><i>${session} 하루</i></span>` +
      `<span class="day-idx mine"><i>내 포트폴리오</i><b class="${cls(mine)}">${fmt(mine)}</b></span>` +
      cells;
    target.hidden = false;
  });
}
