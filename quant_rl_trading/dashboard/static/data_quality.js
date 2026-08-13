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
      lineStyle: { width: 1.4, color: COLOR.down },
      areaStyle: { color: COLOR.down, opacity: 0.08 },
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
      itemStyle: { color: [COLOR.bench, COLOR.down, COLOR.warn][index] },
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

runAll([renderSummary, renderCoverage, renderMissing, renderUniverse,
        renderLatency, renderFailures]);
