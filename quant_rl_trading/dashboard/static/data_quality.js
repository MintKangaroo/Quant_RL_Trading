/* Data Quality 화면.
 *
 * 공통 규약(타임머신 컨트롤, as_of 부착, 차트 기본값)은 scope.js 에 있다.
 * 여기서는 이 화면 고유의 표현만 만든다.
 */

async function renderSummary() {
  const body = await fetchJson("data-quality/summary");
  const d = body.data;
  const t = body.thresholds;

  showScope(body);

  document.getElementById("kpis").innerHTML = [
    kpi("커버리지", pct(d.coverage_ratio, 1),
        `${num(d.covered_sessions)} / ${num(d.expected_sessions)} 거래일`,
        d.coverage_ratio !== null && d.coverage_ratio < t.coverage_warn),
    kpi("종목(누적)", num(d.entities_total), `현재 상장 ${num(d.listed_now)}`),
    kpi("상장폐지", num(d.delisted_total), "0이면 생존편향 의심", d.delisted_total === 0),
    kpi("close 결측", pct(d.missing_close_rate, 3), `volume ${pct(d.missing_volume_rate, 3)}`,
        d.missing_close_rate !== null && d.missing_close_rate > t.missing_warn),
    kpi("수집 지연 p90", ms(d.latency_p90_ms), `p50 ${ms(d.latency_p50_ms)} · 표본 ${num(d.latency_samples)}`,
        d.latency_p90_ms !== null && d.latency_p90_ms > t.latency_p90_warn_ms),
    kpi("수집 실패", num(d.failure_count), "최근 창 기준", d.failure_count > 0),
    kpi("prices 행수", num(d.rows_total), ""),
  ].join("");

  showAlerts(d.warnings);
}

async function renderCoverage() {
  const { data } = await fetchJson("data-quality/coverage");
  chart("chart-coverage").setOption({
    ...BASE,
    xAxis: { type: "category", data: data.points.map((p) => p.session), ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [{
      name: "종목 수", type: "line", showSymbol: false,
      data: data.points.map((p) => p.entities),
      lineStyle: { width: 1.4, color: COLOR.accent },
      areaStyle: { color: COLOR.accent, opacity: 0.08 },
    }],
  }, true);
}

async function renderMissing() {
  const { data } = await fetchJson("data-quality/missing");
  chart("chart-missing").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["close", "volume"] },
    xAxis: { type: "category", data: data.points.map((p) => p.session), ...AXIS },
    yAxis: {
      type: "value", ...AXIS,
      axisLabel: { ...AXIS.axisLabel, formatter: (v) => (v * 100).toFixed(2) + "%" },
    },
    series: [
      { name: "close", type: "line", showSymbol: false, data: data.points.map((p) => p.close),
        lineStyle: { width: 1.4, color: COLOR.up } },
      { name: "volume", type: "line", showSymbol: false, data: data.points.map((p) => p.volume),
        lineStyle: { width: 1.4, color: COLOR.bench } },
    ],
  }, true);
}

async function renderUniverse() {
  const { data } = await fetchJson("data-quality/universe");
  chart("chart-universe").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["상장", "상폐 누적"] },
    xAxis: { type: "category", data: data.points.map((p) => p.session), ...AXIS },
    yAxis: [
      { type: "value", scale: true, ...AXIS },
      { type: "value", scale: true, ...AXIS },
    ],
    series: [
      { name: "상장", type: "line", showSymbol: false, data: data.points.map((p) => p.listed),
        lineStyle: { width: 1.4, color: COLOR.text } },
      { name: "상폐 누적", type: "line", yAxisIndex: 1, showSymbol: false,
        data: data.points.map((p) => p.delisted_cumulative),
        lineStyle: { width: 1.4, color: COLOR.warn, type: "dashed" } },
    ],
  }, true);
}

async function renderLatency() {
  const { data } = await fetchJson("data-quality/latency");
  const stages = data.stages.map((s) => s.stage);
  chart("chart-latency").setOption({
    ...BASE,
    grid: { ...BASE.grid, left: 72 },
    legend: { ...BASE.legend, data: ["p50", "p90", "p99"] },
    tooltip: { ...BASE.tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", ...AXIS },
    yAxis: { type: "category", data: stages, ...AXIS },
    series: ["p50", "p90", "p99"].map((key, index) => ({
      name: key, type: "bar",
      data: data.stages.map((s) => s[key]),
      itemStyle: { color: [COLOR.bench, COLOR.bad, COLOR.warn][index] },
    })),
  }, true);
}

async function renderFailures() {
  const { data } = await fetchJson("data-quality/failures");
  const target = document.getElementById("failures");
  if (!data.length) {
    target.innerHTML = `<div class="empty">최근 창에 수집 실패 없음.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>관측시각</th><th>소스</th><th>단계</th>
      <th class="num">소요(ms)</th><th>상세</th></tr></thead>
    <tbody>${data.map((row) => `<tr>
      <td class="num">${row.observed_at}</td><td>${row.source}</td><td>${row.stage}</td>
      <td class="num">${row.elapsed_ms.toFixed(0)}</td><td>${row.detail}</td>
    </tr>`).join("")}</tbody></table>`;
}

/* 진행 중인 작업 — 백필·IC 측정이 어디까지 갔나.
 *
 * 이 패널은 뉴스·일정 탭에 있었다. 수집 진행률은 "지금 무슨 일이 벌어지고
 * 있나" 가 아니라 **"입력을 믿을 수 있나"** 의 답이라 이 화면이 제자리다
 * (dashboard.md §2).
 */

/* 표에 들어가는 값은 창고에서 온 문자열이다. 그대로 넣으면 마크업으로
   해석될 수 있다. */
function esc(value) {
  return String(value === null || value === undefined ? "—" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function bar(done, total, running, finished) {
  if (!total) {
    // 총량을 모른다. 막대를 채우면 진행률을 아는 척하게 된다.
    return `<div class="bar unknown"><span></span></div>`;
  }
  const pct = Math.min(100, (done / total) * 100);
  const cls = finished ? "done" : running ? "on" : "";
  return `<div class="bar ${cls}"><span style="width:${pct.toFixed(1)}%"></span></div>`;
}

function jobsRows(d) {
  const rows = [];
  for (const job of d.jobs) {
    const pct = job.ratio === null ? "—" : `${(job.ratio * 100).toFixed(1)}%`;
    const count = job.total ? `${num(job.done)} / ${num(job.total)}` : `${num(job.done)}건`;
    rows.push(`<tr>
      <td><span class="lead">
        <strong>${esc(job.kind)}</strong>
        ${job.running ? '<span class="chip tone-mix">진행 중</span>'
                      : '<span class="chip tone-flat">멈춤</span>'}
        <span class="sub trunc">${esc(job.plan_id)}</span>
      </span></td>
      <td style="width:180px">${bar(job.done, job.total, job.running, false)}</td>
      <td class="num">${pct}</td>
      <td class="num">${count}</td>
      <td class="num">${num(job.rows)}행</td>
      <td class="num sub">${esc(job.last_unit)}</td>
    </tr>`);
  }
  for (const run of d.ic) {
    const pct = `${((run.done / run.total) * 100).toFixed(0)}%`;
    rows.push(`<tr>
      <td><span class="lead">
        <strong>IC 측정</strong>
        ${run.running ? '<span class="chip tone-mix">진행 중</span>'
          : run.finished ? '<span class="chip tone-flat">완료</span>'
                         : '<span class="chip tone-flat">멈춤</span>'}
        <span class="sub trunc">${esc(run.analyst)}</span>
      </span></td>
      <td style="width:180px">${bar(run.done, run.total, run.running, run.finished)}</td>
      <td class="num">${pct}</td>
      <td class="num">${num(run.done)} / ${num(run.total)}</td>
      <td class="num">—</td><td class="num sub">—</td>
    </tr>`);
  }
  if (!rows.length) return `<p class="note">진행 중이거나 기록된 작업이 없다.</p>`;
  return `<table><thead><tr><th>작업</th><th>진행</th><th class="num">%</th>`
    + `<th class="num">단위</th><th class="num">적재</th><th class="num">마지막</th>`
    + `</tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

async function renderJobs() {
  const body = await fetchJson("data-quality/jobs");
  document.getElementById("jobs").innerHTML = jobsRows(body.data);
}

runAll([renderSummary, renderCoverage, renderMissing, renderUniverse,
        renderLatency, renderJobs, renderFailures]);
