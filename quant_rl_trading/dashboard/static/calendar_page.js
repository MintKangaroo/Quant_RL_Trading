/* 별도 창의 수익률 캘린더. 한 달씩 보고 ‹ › 로 옮긴다.
 *
 * 그리는 일은 전부 `calendar.js` 의 `mountCalendarBrowser` 가 한다 —
 * **트레이딩 탭과 같은 렌더러다.** 두 벌로 만들면 한쪽만 고쳐진 채로 남고,
 * 두 화면이 같은 수익률을 다르게 그리게 된다.
 *
 * 이 창에만 있는 것은 KPI 줄뿐이다(거래일·누적·최고·최악). 탭에서는 그 숫자를
 * 이미 위쪽 KPI 줄이 말하고 있어서 두 번 적을 이유가 없다.
 */

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

async function loadCalendar() {
  const body = await fetchJson("trading/calendar");
  showScope(body);
  renderKpis(body.data);
  bindDayDetail(
    document.getElementById("calendar-full"),
    body.data.calendar,
    document.getElementById("day-detail"),
  );
  mountCalendarBrowser(body.data.calendar, {
    label: document.getElementById("cal-label"),
    grid: document.getElementById("calendar-full"),
    months: document.getElementById("cal-months"),
    prev: document.getElementById("cal-prev"),
    next: document.getElementById("cal-next"),
  });
}

runAll([loadCalendar]);
