/* 장부별 지수 대비 IR — 트레이딩 탭과 학습 탭이 같은 표를 그린다 (dashboard.md §4 "지수 대비 IR").
 *
 * 숫자는 화면이 계산하지 않는다. `/api/trading/alpha-ir` 이 `accounting/relative.py`(종료 판정 도구·실자금 관문 1 과
 * 같은 함수)로 낸 값을 그대로 찍는다. 표본이 모자란 칸은 API 가 null 로 보내고, 여기서는 '표본 부족 n/N' 을 적는다.
 *
 * 자기 runAll 을 부르지 않는다 — 한 화면에 runAll 이 둘이면 자동 갱신 목록을 서로 덮는다(scope.js scheduleAutoRefresh).
 * 이 표는 하루 한 번 바뀌므로 갱신할 이유도 없다. 이름은 전부 `air` 로 시작한다 — 탭 스크립트와 전역을 나눠 쓴다.
 */

const airSign = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
const airTone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const airPct = (v) => (v === null || v === undefined ? "—" : `${airSign(v)}${(Math.abs(v) * 100).toFixed(2)}%`);
const airRatio = (v) => (v === null || v === undefined ? "—" : `${airSign(v)}${Math.abs(v).toFixed(2)}`);
const airEsc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/* 한 창의 한 값. 표본 부족이면 숫자 대신 그 사실을 — 화면이 잡음을 숫자로 보여 주지 않는다. */
function airCell(win, field, format, toned) {
  if (!win) return `<td class="num dim">—</td>`;
  if (!win.sufficient) {
    return `<td class="num dim" title="세션 ${win.sessions} < ${win.need} — 이 창의 비율 추정은 잡음이다">표본 부족 ${win.sessions}/${win.need}</td>`;
  }
  const value = win[field];
  return `<td class="num ${toned ? airTone(value) : ""}">${format(value)}</td>`;
}

function airSessions(win) {
  if (!win) return `<td class="num dim">—</td>`;
  const missing = win.missing || [];
  const gap = missing.length
    ? ` <span class="air-gap" title="장부에 없는 거래일 — 건너뛴 구간을 한 걸음으로 잇는다(판정 도구와 같다): ${missing.join(", ")}">결손 ${missing.length}</span>`
    : "";
  return `<td class="num">${win.sessions}${gap}</td>`;
}

function airRow(book, columns, data) {
  const head = `<td class="air-name" title="${airEsc(book.ledger)}">${airEsc(book.name)}</td>`;
  if (book.status !== "ok") {
    return `<tr class="air-off">${head}<td colspan="${columns.length + 6}" class="dim">${airEsc(book.reason)}</td></tr>`;
  }
  const all = book.windows.all;
  const rolling = columns.filter((c) => c.key !== "all" && c.key !== "reset");
  const reset = book.windows.reset;
  const resetCell = reset
    ? airCell(reset, "ir", airRatio, true)
    : `<td class="num dim" title="측정 리셋(${airEsc(data.reset_date)}) 뒤 창 — 실자금 관문 측정 창">${airEsc(data.reset_date.slice(5).replace("-", "/"))} 부터</td>`;
  // 전체 창부터 표본이 모자라면 IR·β·α·추적오차 칸을 하나로 접는다 — 같은 '표본 부족' 을 일곱 번 적으면 읽히지 않는다.
  const ratios = all && all.sufficient
    ? `${rolling.map((c) => airCell(book.windows[c.key], "ir", airRatio, true)).join("")}
    ${airCell(all, "ir", airRatio, true)}
    ${airCell(all, "beta", (v) => (v === null || v === undefined ? "—" : v.toFixed(2)), false)}
    ${airCell(all, "alpha", airPct, true)}
    ${airCell(all, "tracking_error", (v) => (v === null || v === undefined ? "—" : (v * 100).toFixed(2) + "%"), false)}`
    : `<td colspan="${rolling.length + 4}" class="dim air-short" title="이 창의 비율 추정은 잡음이다 — 누적 초과만 싣는다">표본 부족 ${all ? all.sessions : 0}/${all ? all.need : data.min_sessions} · IR·β·α·추적오차는 ${all ? all.need : data.min_sessions}세션부터</td>`;
  return `<tr>${head}
    ${airSessions(all)}
    <td class="num ${airTone(all && all.excess)}" title="우리 ${airPct(all && all.ours_total)} · KODEX200 총수익 ${airPct(all && all.etf_total)}">${airPct(all && all.excess)}</td>
    ${ratios}
    ${resetCell}
    <td class="air-spark-cell"><div class="air-spark" id="air-spark-${airEsc(book.key)}"></div></td>
  </tr>`;
}

/* 추이 — 전체 창 누적 초과. 0선을 긋고 색은 마지막 값 하나로(§4 '지수 대비' 와 같은 규칙). ECharts 만 쓴다. */
function airSpark(book) {
  const points = book.curve || [];
  if (points.length < 2) return;
  const last = points[points.length - 1][1];
  const color = last > 0 ? COLOR.up : last < 0 ? COLOR.down : COLOR.muted;
  chart(`air-spark-${book.key}`).setOption({
    backgroundColor: "transparent",
    animation: false,
    aria: { enabled: true },
    grid: { left: 0, right: 0, top: 2, bottom: 2 },
    tooltip: {
      trigger: "axis", triggerOn: "mousemove|click", confine: true,
      backgroundColor: COLOR.panel, borderColor: COLOR.border,
      textStyle: { color: COLOR.text, fontFamily: "IBM Plex Mono", fontSize: 11 },
      formatter: (items) => `${items[0].axisValue} · 누적 초과 ${airPct(items[0].value)}`,
    },
    xAxis: { type: "category", show: false, boundaryGap: false, data: points.map((p) => p[0]) },
    yAxis: { type: "value", show: false, min: (v) => Math.min(v.min, 0), max: (v) => Math.max(v.max, 0) },
    series: [{
      type: "line", showSymbol: false, data: points.map((p) => p[1]),
      lineStyle: { width: 1.4, color }, itemStyle: { color },
      markLine: { silent: true, symbol: "none", label: { show: false },
                  lineStyle: { color: COLOR.dim, type: "dashed", width: 1 }, data: [{ yAxis: 0 }] },
    }],
  });
}

function renderAlphaIr(target, stamp, data) {
  if (!data.available) {
    target.innerHTML = `<p class="empty">${airEsc(data.note || "장부별 IR 을 잴 수 없다.")}</p>`;
    if (stamp) stamp.textContent = "";
    return;
  }
  const columns = data.columns || [];
  const rolling = columns.filter((c) => c.key !== "all" && c.key !== "reset");
  const yieldPct = (data.annual_yield * 100).toFixed(2);
  if (stamp) stamp.textContent = `기준 세션 ${data.through}`;
  target.innerHTML = `
    <p class="air-def">KODEX200 총수익(가격 + 분배금 연 ${yieldPct}% 가정) 대비 <strong>비용 차감 후 일간 초과</strong>의 정보비율, 연환산 √${data.trading_days_per_year}.
      종료 판정·실자금 관문 1 과 같은 수식이다. 세션 ${data.min_sessions} 미만 창은 숫자 대신 '표본 부족'. β·α·추적오차는 전체 창.</p>
    <table class="air-table">
      <colgroup><col class="c-air-name"></colgroup>
      <thead><tr>
        <th>장부</th><th class="num">세션</th><th class="num" title="전체 창 — 우리 누적 − KODEX200 총수익 누적">누적 초과</th>
        ${rolling.map((c) => `<th class="num" title="장부 세션 ${c.sessions}개 — 결손일을 넘어 잇는다">IR ${c.sessions}</th>`).join("")}
        <th class="num">IR 전체</th><th class="num" title="cov(우리, ETF 가격) / var(ETF 가격) — 표본 공분산·표본 분산">β</th>
        <th class="num" title="우리 누적 − β × KODEX200 총수익 누적. 참고 — 관문 아님">베타 보정 α</th>
        <th class="num" title="일간 초과의 표준편차 × √${data.trading_days_per_year}">추적오차</th>
        <th class="num" title="측정 리셋(${airEsc(data.reset_date)}) 뒤 창의 IR — 실자금 관문 측정 창">리셋 뒤 IR</th>
        <th>누적 초과 추이</th>
      </tr></thead>
      <tbody>${(data.books || []).map((b) => airRow(b, columns, data)).join("")}</tbody>
    </table>`;
  for (const book of data.books || []) if (book.status === "ok") airSpark(book);
}

async function loadAlphaIr() {
  const target = document.getElementById("alpha-ir");
  if (!target) return;
  const stamp = document.getElementById("alpha-ir-stamp");
  try {
    const body = await fetchJson("trading/alpha-ir");
    renderAlphaIr(target, stamp, body.data);
  } catch (error) {
    target.innerHTML = `<p class="empty">장부별 IR 을 못 읽었다 — ${airEsc(error.message)}</p>`;
  }
}

loadAlphaIr();
