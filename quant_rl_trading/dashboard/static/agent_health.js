/* Agent Health — 에이전트가 살아 있는지, 그리고 쓸모가 있는지.
 *
 * 공통 규약은 scope.js 에 있다. 여기서는 이 화면 고유의 표현만 만든다.
 */

async function renderSummary() {
  const body = await fetchJson("agent-health/summary");
  const d = body.data;
  showScope(body);

  document.getElementById("kpis").innerHTML = [
    kpi("Analyst", num(d.total), `측정 완료 ${num(d.measured)}`),
    // M2 완료 기준이 2명이라 이 숫자가 화면의 중심이다.
    kpi("IC 통과", num(d.passed), "M2 기준 2명", d.passed < 2),
    kpi("관찰 모드", num(d.observing), "가중치 0 — 매매 미사용"),
    kpi("가중치 합", dec(d.active_weight, 1), "0이면 아무도 매매에 못 쓴다", d.active_weight === 0),
    kpi("기록된 Signal", num(d.signals), "관찰 모드여도 기록은 남긴다", d.signals === 0),
    kpi("거부 건수", num(d.blocks), "News · SNS"),
  ].join("");

  showAlerts(d.warnings);
}

async function renderRoster() {
  const { data, thresholds } = await fetchJson("agent-health/roster");
  const rows = data.map((item) => {
    const state = !item.measured
      ? `<span class="tag dim">미측정</span>`
      : item.passed
        ? `<span class="tag pass">통과</span>`
        : `<span class="tag observe">관찰</span>`;
    const icCell = item.ic === null
      ? "—"
      : `<span class="${item.passed ? 'good' : 'weak'}">${dec(item.ic)}</span>`;
    return `<tr>
      <td><strong>${item.analyst}</strong><div class="kpi-note">${item.note}</div></td>
      <td>${state}</td>
      <td class="num">${icCell}</td>
      <td class="num">${item.ic_threshold === undefined ? dec(thresholds.ic_threshold ?? null, 2) : dec(item.ic_threshold, 2)}</td>
      <td class="num">${num(item.sample_days)}</td>
      <td class="num">${dec(item.weight, 1)}</td>
      <td class="num">${item.version || "—"}</td>
      <td class="num">${item.measured_at ? item.measured_at.slice(0, 16).replace("T", " ") : "—"}</td>
    </tr>`;
  });

  document.getElementById("roster").innerHTML = `<table>
    <thead><tr>
      <th>Analyst</th><th>상태</th><th class="num">IC</th><th class="num">합격선</th>
      <th class="num">표본(일)</th><th class="num">가중치</th><th class="num">버전</th>
      <th class="num">측정 시각</th>
    </tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
}

async function renderIcHistory() {
  const { data, thresholds } = await fetchJson("agent-health/ic-history");
  const instance = chart("chart-ic");

  if (!data.series.length) {
    instance.clear();
    instance.setOption({
      ...BASE,
      title: {
        text: "측정 이력 없음 — tools/measure_ic.py --save 로 기록된다",
        left: "center", top: "middle",
        textStyle: { color: COLOR.dim, fontSize: 12, fontWeight: "normal" },
      },
    }, true);
    return;
  }

  const stamps = [...new Set(data.series.flatMap((s) => s.points.map((p) => p.at)))].sort();
  const line = (series, index) => ({
    name: series.analyst,
    type: "line",
    // 점이 하나면 선이 안 보인다. 심볼을 항상 그린다.
    showSymbol: true,
    symbolSize: 7,
    data: stamps.map((at) => {
      const hit = series.points.find((p) => p.at === at);
      return hit ? hit.ic : null;
    }),
    // 계열 구분이지 손익·상태가 아니다 — 공용 범주 팔레트를 쓴다.
    lineStyle: { width: 1.6, color: COLOR.series[index % COLOR.series.length] },
    itemStyle: { color: COLOR.series[index % COLOR.series.length] },
  });

  instance.setOption({
    ...BASE,
    legend: { ...BASE.legend, data: data.series.map((s) => s.analyst) },
    xAxis: { type: "category", data: stamps.map((at) => at.slice(0, 16).replace("T", " ")), ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [
      ...data.series.map(line),
      {
        // 합격선을 배경에 깐다. 숫자를 눈으로 판정할 수 있어야 한다.
        name: "합격선", type: "line", data: stamps.map(() => thresholds.ic_threshold ?? 0.03),
        showSymbol: false, lineStyle: { width: 1, color: COLOR.warn, type: "dashed" },
      },
    ],
  }, true);
}

async function renderSignals() {
  const { data } = await fetchJson("agent-health/signals");
  const target = document.getElementById("signals");
  if (!data.analysts.length) {
    target.innerHTML = `<div class="empty">기록된 Signal 이 없다.
      관찰 모드여도 점수는 남겨야 나중에 켤 근거가 생긴다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>Analyst</th><th class="num">신호</th><th class="num">종목</th>
      <th class="num">세션</th><th class="num">지연 p90</th><th class="num">최근 as_of</th></tr></thead>
    <tbody>${data.analysts.map((a) => `<tr>
      <td>${a.analyst}<div class="kpi-note">${a.version}</div></td>
      <td class="num">${num(a.signals)}</td>
      <td class="num">${num(a.entities)}</td>
      <td class="num">${num(a.sessions)}</td>
      <td class="num">${ms(a.latency_p90_ms)}</td>
      <td class="num">${a.last_as_of.slice(0, 16).replace("T", " ")}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderVerdicts() {
  const { data } = await fetchJson("agent-health/verdicts");
  const target = document.getElementById("verdicts");
  const card = data.scorecard || {};
  if (!data.blocks) {
    target.innerHTML = `<div class="empty">거부 기록이 없다.
      News · SNS 수집기는 M3 실전 직전에 붙인다 — 과거 데이터를 검증할 수 없어
      IC 에 물리지 않는다. 판정 규칙·거부 상한·만료·성적표 배관은 완성돼 있다.</div>`;
    return;
  }
  target.innerHTML = `
    <div class="kpis" style="margin-bottom:10px">
      ${kpi("총 거부", num(data.blocks), "")}
      ${kpi("현재 유효", num(data.active), "expires_at 이 지나면 자동 해제")}
      ${kpi("채점 완료", num(card.settled), `진행 중 ${num(card.pending)}건은 제외`)}
      ${kpi("적중률", pct(card.hit_rate, 1), "차단 종목이 시장보다 더 빠진 비율",
            card.hit_rate !== null && card.hit_rate < 0.5)}
      ${kpi("평균 초과수익", pct(card.mean_excess, 2), "음수면 손실을 피한 것",
            card.mean_excess !== null && card.mean_excess > 0)}
    </div>
    <table>
      <thead><tr><th>사유</th><th class="num">건수</th></tr></thead>
      <tbody>${data.by_category.map((row) => `<tr>
        <td>${row.category}</td><td class="num">${num(row.count)}</td>
      </tr>`).join("")}</tbody>
    </table>`;
}

runAll([renderSummary, renderRoster, renderIcHistory, renderSignals, renderVerdicts]);
