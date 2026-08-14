/* 별도 창의 수익률 캘린더. 한 달씩 보고 ‹ › 로 옮긴다.
 *
 * 트레이딩 탭의 달력과 **같은 렌더러**(calendar.js)를 쓴다. 두 벌로 만들면
 * 한쪽만 고쳐진 채로 남고, 두 화면이 같은 수익률을 다르게 그리게 된다.
 */

let CAL = { days: [], months: [] };
let MONTHS = [];
let cursor = 0;

function monthTotal(month) {
  const hit = (CAL.months || []).find((m) => m.month === month);
  return hit ? hit.return : null;
}

function paint() {
  const label = document.getElementById("cal-label");
  const target = document.getElementById("calendar-full");
  if (!MONTHS.length) {
    if (label) label.textContent = "—";
    renderCalendar(target, { days: [], months: [] });
    return;
  }
  cursor = Math.max(0, Math.min(cursor, MONTHS.length - 1));
  const month = MONTHS[cursor];
  if (label) label.textContent = `${month} · ${pct(monthTotal(month))}`;
  renderCalendar(target, CAL, { months: [month] });

  // 끝에 닿으면 버튼을 끈다. 눌리는데 아무 일도 안 나는 버튼은 고장으로 읽힌다.
  const prev = document.getElementById("cal-prev");
  const next = document.getElementById("cal-next");
  if (prev) prev.disabled = cursor === 0;
  if (next) next.disabled = cursor === MONTHS.length - 1;
}

function renderKpis(payload) {
  const best = payload.best;
  const worst = payload.worst;
  document.getElementById("cal-kpis").innerHTML = [
    kpi("거래일", num(payload.sessions), "이 창의 거래일 수"),
    kpi("누적", pct(payload.cumulative), "일별 TWR 복리합성", false, {
      tone: payload.cumulative >= 0 ? "up" : "down",
    }),
    kpi("최고의 날", best ? pct(best.return) : "—", best ? best.session : "기록 없음", false, {
      tone: "up",
    }),
    kpi("최악의 날", worst ? pct(worst.return) : "—", worst ? worst.session : "기록 없음", false, {
      tone: "down",
    }),
  ].join("");
}

function renderMonths() {
  const rows = (CAL.months || []).slice().reverse();
  const target = document.getElementById("cal-months");
  if (!rows.length) {
    target.innerHTML = `<p class="empty">nav_daily 가 비어 있다.</p>`;
    return;
  }
  target.innerHTML = `<table class="tbl"><tr><th>월</th><th class="r">수익률</th><th>　</th></tr>${rows
    .map((row, index) => {
      const sign = row.return > 0 ? "up" : row.return < 0 ? "down" : "";
      return `<tr data-month="${row.month}">
        <td class="num">${row.month}</td>
        <td class="num r ${sign}">${pct(row.return)}</td>
        <td><button type="button" class="linky" data-goto="${rows.length - 1 - index}">보기</button></td>
      </tr>`;
    })
    .join("")}</table>`;

  target.querySelectorAll("button[data-goto]").forEach((button) => {
    button.addEventListener("click", () => {
      cursor = Number(button.dataset.goto);
      paint();
    });
  });
}

async function loadCalendar() {
  const body = await fetchJson("trading/calendar");
  showScope(body);
  CAL = body.data.calendar;
  MONTHS = calendarMonths(CAL);
  cursor = MONTHS.length - 1; // 최근 달부터 본다
  renderKpis(body.data);
  renderMonths();
  paint();
}

document.getElementById("cal-prev").addEventListener("click", () => {
  cursor -= 1;
  paint();
});
document.getElementById("cal-next").addEventListener("click", () => {
  cursor += 1;
  paint();
});

runAll([loadCalendar]);
