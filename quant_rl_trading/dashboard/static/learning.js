/* 학습 탭 — RL(M4)은 아직 없다.
 *
 * 공통 규약은 scope.js 에 있다. 이 화면 고유의 표현만 만든다.
 *
 * dashboard.md §5 가 요구하는 위젯(explained_variance·커리큘럼·학습
 * 곡선·approx KL·IR·시드 분산·Optuna trial)은 지금 잴 수 없다 — allocator/
 * 도, 그 산출물을 담을 테이블도 창고에 없다. 그 자리는 learning/status 가
 * 돌려주는 문구로 채운다. **0 이나 가짜 곡선을 그리지 않는다.**
 *
 * 지금 실제로 있는 것은 Analyst IC 게이트다. 계산은 Agent Health 화면과
 * 같은 서비스(services/learning.py 가 agent_health 를 그대로 부른다)를
 * 쓰므로 두 화면의 숫자가 갈라지지 않는다.
 */

//: learning/status 의 widget key → 템플릿의 빈 자리 id.
const M4_TARGETS = {
  explained_variance: "empty-explained-variance",
  curriculum: "empty-curriculum",
  episode_reward: "empty-episode-reward",
  optimizer_diag: "empty-optimizer-diag",
  ir_vs_baseline: "empty-ir-vs-baseline",
  seed_variance: "empty-seed-variance",
};

async function renderM4Placeholders() {
  const { data } = await fetchJson("learning/status");

  for (const widget of data.widgets) {
    const target = document.getElementById(M4_TARGETS[widget.key]);
    if (!target) continue;
    target.innerHTML = `<strong>M4 에서 채워진다 · 지금은 학습이 없다.</strong>
      ${widget.detail ? `<br>${widget.detail}` : ""}`;
  }
}

async function renderGate() {
  const body = await fetchJson("learning/gate");
  const data = body.data;
  showScope(body);

  const rows = data.roster.map((item) => {
    const state = !item.measured
      ? `<span class="tag dim">미측정</span>`
      : item.passed
        ? `<span class="tag pass">매매에 쓰임</span>`
        : `<span class="tag observe">관찰</span>`;
    const icCell = item.ic === null
      ? "—"
      : `<span class="${item.passed ? "good" : "weak"}">${dec(item.ic)}</span>`;
    return `<tr>
      <td><strong>${item.analyst}</strong><div class="kpi-note">${item.note}</div></td>
      <td>${state}</td>
      <td class="num">${icCell}</td>
      <td class="num">${dec(item.weight, 1)}</td>
      <td class="num">${item.measured_at ? item.measured_at.slice(0, 16).replace("T", " ") : "—"}</td>
    </tr>`;
  });

  document.getElementById("gate").innerHTML = `<table>
    <thead><tr>
      <th>애널리스트</th><th>상태</th><th class="num">적중도</th>
      <th class="num">가중치</th><th class="num">측정 시각</th>
    </tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
}

async function renderKpis() {
  // gate·status 를 여기서 따로 부른다. runAll 은 각 job 을 독립적으로
  // 실패시키므로(agent_health.js 와 같은 관례), renderGate 의 side effect에
  // 기대지 않는다 — 그쪽이 실패해도 KPI 줄은 뜬다.
  const [gateBody, statusBody] = await Promise.all([
    fetchJson("learning/gate"),
    fetchJson("learning/status"),
  ]);
  const g = gateBody.data;
  const s = statusBody.data;

  document.getElementById("kpis").innerHTML = [
    kpi("RL 학습(M4)", s.active ? "가동" : "미착수", s.milestone),
    kpi("매매에 쓰이는 애널리스트", num(g.active_count), `잰 것 ${num(g.measured_count)}/${num(g.total)}`,
      g.active_count === 0),
    kpi("가중치 합", dec(g.active_weight, 1), "0 이면 아무도 매매에 못 쓴다", g.active_weight === 0),
    kpi("적중도를 잰 애널리스트", num(g.measured_count), `전체 ${num(g.total)}명`),
  ].join("");

  const warnings = [];
  if (g.active_count === 0) warnings.push("합격선을 넘은 애널리스트가 없다 — 아무도 매매에 못 쓴다");
  showAlerts(warnings);
}

async function renderIcHistory() {
  const { data } = await fetchJson("learning/ic-history");
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
    lineStyle: { width: 1.6, color: [COLOR.down, COLOR.up, COLOR.text, COLOR.warn][index % 4] },
    itemStyle: { color: [COLOR.down, COLOR.up, COLOR.text, COLOR.warn][index % 4] },
  });

  instance.setOption({
    ...BASE,
    legend: { ...BASE.legend, data: data.series.map((s) => s.analyst) },
    xAxis: { type: "category", data: stamps.map((at) => at.slice(0, 16).replace("T", " ")), ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [
      ...data.series.map(line),
      {
        // 합격선을 배경에 깐다. store.config 에서 읽은 값이지 코드에 적은
        // 숫자가 아니다 (services/learning.py 가 매 요청 다시 읽는다).
        name: "합격선(0.03)", type: "line", data: stamps.map(() => data.threshold),
        showSymbol: false, lineStyle: { width: 1, color: COLOR.warn, type: "dashed" },
      },
    ],
  }, true);
}

async function renderWalkForward() {
  const { data } = await fetchJson("learning/walk-forward");

  document.getElementById("wf-source").textContent = `${data.measured_at} · ${data.source}`;

  const rows = data.rows.map((row) => {
    const wfState = row.wf_passed
      ? `<span class="tag pass">통과</span>`
      : `<span class="tag observe">관찰</span>`;
    const liveCell = !row.live_measured
      ? `<span class="tag dim">미측정</span>`
      : `<span class="${row.live_passed ? "good" : "weak"}">${dec(row.live_ic)}</span>`;
    const deltaCell = row.delta_ic === null
      ? "—"
      : `<span class="${row.delta_ic >= 0 ? "good" : "weak"}">${row.delta_ic >= 0 ? "+" : ""}${dec(row.delta_ic)}</span>`;
    return `<tr>
      <td><strong>${row.analyst}</strong></td>
      <td>${wfState}</td>
      <td class="num">${dec(row.wf_ic)}</td>
      <td class="num">${liveCell}</td>
      <td class="num">${deltaCell}</td>
    </tr>`;
  });

  document.getElementById("walk-forward").innerHTML = `<table>
    <thead><tr>
      <th>애널리스트</th><th>과거 검증 판정</th><th class="num">과거 검증</th>
      <th class="num">지금 실측</th><th class="num">차이(지금−과거)</th>
    </tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
}

runAll([renderKpis, renderM4Placeholders, renderGate, renderIcHistory, renderWalkForward]);
