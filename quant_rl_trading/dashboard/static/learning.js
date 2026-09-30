/* 모델 검증 탭 — Ranker 관측을 먼저, 과거 RL 진단은 펼쳤을 때 읽는다.
 *
 * 공통 규약은 scope.js 에 있다. 이 화면 고유의 표현만 만든다.
 *
 * dashboard.md §5 가 요구하는 위젯(explained_variance·커리큘럼·학습
 * 곡선·approx KL·IR·시드 분산·Optuna trial) 중 셋은 `rl_updates` 표가
 * 생기면서 그릴 수 있게 됐다(2026-08-19). 나머지는 학습을 실제로 완주해야
 * 나온다. **0 이나 가짜 곡선을 그리지 않는다** — 학습 기록이 0행이면
 * "아직 없다" 를 글자로 말하지 곡선을 0 으로 눕히지 않는다.
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

//: rl_updates 로 그릴 수 있는 위젯 → 그 표의 컬럼.
const RUN_SERIES = {
  explained_variance: ["explained_variance"],
  episode_reward: ["episode_reward", "cash_weight"],
  optimizer_diag: ["approx_kl", "entropy", "grad_norm"],
};

const SERIES_LABEL = {
  explained_variance: "explained_variance",
  episode_reward: "에피소드 보상",
  cash_weight: "현금 비중",
  approx_kl: "approx KL",
  entropy: "entropy",
  grad_norm: "gradient norm",
};

async function renderM4Placeholders() {
  const [statusBody, runsBody, evalBody, curBody] = await Promise.all([
    fetchJson("learning/status"),
    fetchJson("learning/training-runs"),
    fetchJson("learning/evaluations"),
    fetchJson("learning/curriculum"),
  ]);
  const data = statusBody.data;
  const runs = runsBody.data;
  const evals = evalBody.data;
  const curriculum = curBody.data;
  renderTrainingLive(runs);

  for (const widget of data.widgets) {
    const target = document.getElementById(M4_TARGETS[widget.key]);
    if (!target) continue;

    // 그릴 수 있게 된 칸이면 그린다. 아니면 왜 비었는지를 말한다.
    if (runs.has_data && RUN_SERIES[widget.key]) {
      drawRunChart(target, widget.key, runs);
      continue;
    }
    if (widget.key === "curriculum" && curriculum) {
      renderCurriculum(target, curriculum);
      continue;
    }
    if (widget.key === "ir_vs_baseline" && evals.has_data) {
      renderEvaluation(target, evals);
      continue;
    }
    if (widget.key === "seed_variance" && evals.has_data) {
      renderEvaluationSpread(target, evals);
      continue;
    }
    if (runs.has_data && !RUN_SERIES[widget.key] && evals && !evals.has_data) {
      target.innerHTML = "<strong>저장된 평가 기록이 없다.</strong>"
        + "<br>재개 조건과 사전등록을 확인한 뒤 평가한다.";
      continue;
    }
    const why = RUN_SERIES[widget.key]
      ? "저장된 학습 기록이 없다."
      : "저장된 검증 기록이 없다.";
    target.innerHTML = `<strong>${why}</strong>${
      widget.detail ? `<br>${widget.detail}` : ""
    }`;
  }
}

const STATUS_LABEL = {
  running: ["진행 중", "ok"],
  completed: ["완주", "ok"],
  stopped: ["멈춤 — 마지막 기록 뒤 조용하다", "bad"],
};

function minutesLabel(m) {
  if (m == null) return "—";
  if (m < 90) return `${Math.round(m)}분`;
  if (m < 60 * 36) return `${(m / 60).toFixed(1)}시간`;
  return `${(m / 60 / 24).toFixed(1)}일`;
}

function tailMean(arr, n, offset = 0) {
  const end = arr.length - offset;
  const slice = arr.slice(Math.max(0, end - n), end).filter((v) => v != null);
  if (!slice.length) return null;
  return slice.reduce((a, b) => a + b, 0) / slice.length;
}

/** 지금 돌고 있는 학습 — 창고의 마지막 기록이 말하는 것만 보인다. */
function renderTrainingLive(runs) {
  const target = document.getElementById("training-live");
  if (!target) return;
  if (!runs.has_data || !runs.runs.length) {
    target.innerHTML = "<strong>학습 기록이 0행이다 — 돌고 있는 학습이 없다.</strong>";
    return;
  }
  const run = runs.runs[0];
  // 서버가 진행 정보를 안 주는 옛 페이로드(재시작 전)여도 죽지 않는다 —
  // 있는 것만 보이고 없는 것은 "—" 다.
  const lastUpdate = run.last_update ?? (run.updates?.length ? run.updates[run.updates.length - 1] : null);
  const total = run.total_updates ?? null;
  const [statusText, tone] = STATUS_LABEL[run.status] || [run.status ?? "서버 재시작 전 — 상태 미상", ""];
  const pct = total && lastUpdate != null ? (100 * lastUpdate) / total : 0;
  const reward = run.series.episode_reward || [];
  const recent = tailMean(reward, 50);
  const before = tailMean(reward, 50, 50);
  const lastAt = run.last_observed_at ? new Date(run.last_observed_at) : null;
  const s = run.series;
  const last = (k) => (s[k] && s[k].length ? s[k][s[k].length - 1] : null);
  const fmt = (v, d = 4) => (v == null ? "—" : Number(v).toFixed(d));

  const plain = run.plain
    ? `<p class="plain note-accent">${run.plain}</p>`
    : "";
  const rows = [
    ["상태", `<span class="${tone}">${statusText}</span>${lastAt ? ` · 마지막 기록 ${lastAt.toLocaleString("ko-KR", { hour12: false })} (${minutesLabel(run.silent_minutes)} 전)` : ""}`],
    ["진행", `${lastUpdate == null ? "—" : lastUpdate.toLocaleString()} / ${total == null ? "—" : total.toLocaleString()} 업데이트 (${pct.toFixed(1)}%)
      <div class="meter"><i style="width:${pct.toFixed(1)}%"></i></div>`],
    ["페이스", `${run.pace_minutes == null ? "—" : run.pace_minutes.toFixed(1) + "분/업데이트"} · 남은시간 ${minutesLabel(run.eta_minutes ?? null)}`],
    ["보상 추이", `최근 50 평균 ${fmt(recent)} · 그 앞 50 평균 ${fmt(before)}${recent != null && before != null ? ` · 차이 ${(recent - before >= 0 ? "+" : "") + (recent - before).toFixed(5)}` : ""}`],
    ["마지막 지표", `EV ${fmt(last("explained_variance"), 3)} · KL ${fmt(last("approx_kl"), 5)} · grad ${fmt(last("grad_norm"), 2)} · 반영률 ${fmt(last("action_reflection"), 3)} · 현금 ${fmt(last("cash_weight"), 3)}`],
    ["실행", `${run.run_id} · seed ${run.seed ?? "—"} · ${run.curriculum || "—"} · ${run.git_commit ? run.git_commit.slice(0, 7) : "—"}`],
  ];
  target.innerHTML = plain + `<table class="kv">${rows
    .map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`)
    .join("")}</table>`;
}

/** 가장 최근 run 의 지표 곡선. 여러 run 을 겹치면 어느 것이 최신인지 안 보인다. */
function drawRunChart(target, key, runs) {
  const run = runs.runs[0];
  const keys = RUN_SERIES[key].filter((k) => run.series[k]);
  if (!keys.length) {
    target.innerHTML = "<strong>이 지표는 아직 기록되지 않았다.</strong>";
    return;
  }
  // 차트가 들어갈 자리를 만든다 — 자리표시자 문구가 남아 있으면 겹친다.
  target.innerHTML = `<div id="chart-${key}" class="chart"></div>
    <div class="dim subnote">
      ${run.run_id} · seed ${run.seed ?? "—"} ${run.git_commit ? `· ${run.git_commit.slice(0, 7)}` : ""}
    </div>`;

  const guards = runs.guards || {};
  const series = keys.map((k, i) => ({
    name: SERIES_LABEL[k] || k,
    type: "line",
    smooth: true,
    showSymbol: false,
    data: run.series[k],
    lineStyle: { width: 2 },
    itemStyle: { color: COLOR.series[i % COLOR.series.length] },
    // 경고선은 문서(§10)에서 온 값이다. 화면이 따로 정하지 않는다.
    markLine: guards[k]?.floor !== undefined
      ? {
          silent: true,
          symbol: "none",
          label: { formatter: guards[k].label, fontSize: 10 },
          data: [{ yAxis: guards[k].floor }],
        }
      : undefined,
  }));

  chart(`chart-${key}`).setOption({
    ...BASE,
    legend: { show: keys.length > 1, bottom: 0, textStyle: { fontSize: 10 } },
    grid: { left: 44, right: 12, top: 12, bottom: keys.length > 1 ? 30 : 12 },
    xAxis: { type: "category", data: run.updates, name: "update" },
    yAxis: { type: "value", scale: true },
    series,
  }, true);
}

async function renderGate() {
  const body = await fetchModelGate();
  const data = body.data;
  showScope(body);

  const rows = data.roster.map((item) => {
    const state = !item.measured
      ? `<span class="tag dim">미측정</span>`
      : item.passed && item.weight > 0
        ? `<span class="tag pass">매매에 쓰임</span>`
        : `<span class="tag observe">관찰</span>`;
    const icCell = item.ic === null
      ? "—"
      : `<span class="${item.passed ? "good" : "weak"}">${dec(item.ic)}</span>`;
    return `<tr>
      <td><strong>${item.analyst}</strong> <span class="sub">${item.market || ''}</span><div class="kpi-note">${item.note}</div></td>
      <td>${state}</td>
      <td class="num">${icCell}</td>
      <td class="num">${dec(item.weight, 1)}</td>
      <td class="num ${item.applied !== undefined && Math.abs(item.applied - item.weight) > 0.005 ? "warn" : ""}">${item.applied === undefined ? "—" : dec(item.applied, 2)}</td>
      <td class="num">${item.measured_at ? item.measured_at.slice(0, 16).replace("T", " ") : "—"}</td>
    </tr>`;
  });

  // 랭커 ModelOps 경보 — 없으면 아무것도 안 그린다(빈 목록은 "정상" 이 아니라 "경보 없음" 이다).
  const alerts = (data.alerts || []).map((a) => `<div class="alert warn">${a.text}</div>`).join("");
  document.getElementById("gate").innerHTML = `${alerts}<table>
    <thead><tr>
      <th>애널리스트</th><th>상태</th><th class="num">적중도</th>
      <th class="num">가중치</th><th class="num">적용<span class="hint">5세션 혼합</span></th><th class="num">측정 시각</th>
    </tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
}

async function renderKpis() {
  // gate·status 를 여기서 따로 부른다. runAll 은 각 job 을 독립적으로
  // 실패시키므로(agent_health.js 와 같은 관례), renderGate 의 side effect에
  // 기대지 않는다 — 그쪽이 실패해도 KPI 줄은 뜬다.
  const gateBody = await fetchModelGate();
  const g = gateBody.data;
  showScope(gateBody);
  const market = (params().get("market") || "KR").toUpperCase();
  const rankers = g.roster.filter((row) => row.analyst === "ranker" && row.market === market);
  const ranker = rankers.length === 1 ? rankers[0] : null;
  const measured = ranker?.measured && Number.isFinite(ranker.ic);
  const ic = measured ? dec(ranker.ic, 3) : "미측정";

  document.getElementById("kpis").innerHTML = [
    kpi("Ranker IC", ic, `${market} · 관측값`, measured && !ranker.passed),
    kpi("매매에 쓰이는 애널리스트", num(g.active_count), `잰 것 ${num(g.measured_count)}/${num(g.total)}`,
      g.active_count === 0),
    kpi("가중치 합", dec(g.active_weight, 1), "0 이면 아무도 매매에 못 쓴다", g.active_weight === 0),
    kpi("적중도를 잰 애널리스트", num(g.measured_count), `전체 ${num(g.total)}명`),
  ].join("");

  const safe = (value) => String(value ?? "미측정").replace(/[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  document.getElementById("champion-evidence").innerHTML = `<dl class="model-evidence">
    <div><dt>Ranker IC · ${safe(market)}</dt><dd>${ic}</dd></div>
    <div><dt>합성 가중치</dt><dd>${ranker ? dec(ranker.weight, 2) : '미측정'}</dd></div>
    <div><dt>측정 세션 수</dt><dd>${ranker?.sample_days != null ? num(ranker.sample_days) : '미측정'}</dd></div></dl>
    <p class="cost-detail">버전 ${safe(ranker?.version)} · 측정 ${safe(ranker?.measured_at)}<br>재학습 시각 · feature drift: 미측정</p>`;
  document.getElementById("model-comparison").innerHTML = `<table><thead><tr>
    <th>구분</th><th>현재 증거</th><th class="num">IC</th><th class="num">Marginal IC</th>
    <th class="num">순IR</th><th class="num">회전율</th><th class="num">총비용</th><th class="num">Seed 분산</th>
    </tr></thead><tbody><tr><td>Champion 기준</td><td>${ranker?.weight > 0 ? 'Ranker 합성 참여' : '관측 확인 필요'}</td>
    <td class="num">${ic}</td>${'<td class="num">미측정</td>'.repeat(5)}</tr>
    <tr><td>Challenger</td><td>승격 근거 없음</td>${'<td class="num">미측정</td>'.repeat(6)}</tr></tbody></table>`;

  const warnings = [];
  if (g.active_count === 0) warnings.push("합격선을 넘은 애널리스트가 없다 — 아무도 매매에 못 쓴다");
  showAlerts(warnings);
}

async function fetchModelGate() {
  try { return await fetchJson("learning/gate"); } catch (error) {
    for (const id of ["kpis", "champion-evidence", "model-comparison", "gate"]) {
      document.getElementById(id).innerHTML = '<p class="empty">모델 상태 조회 실패 · 미측정</p>';
    }
    throw error;
  }
}

async function renderIcHistory() {
  let data;
  try { ({ data } = await fetchJson("learning/ic-history")); } catch (error) {
    charts["chart-ic"]?.clear();
    throw error;
  }
  const instance = chart("chart-ic");

  if (!data.series.length) {
    instance.clear();
    instance.setOption({
      ...BASE,
      title: {
        text: "해당 시장의 저장된 IC 이력 없음",
        left: "center", top: "middle",
        textStyle: { color: COLOR.dim, fontSize: 12, fontWeight: "normal" },
      },
    }, true);
    return;
  }

  const stamps = [...new Set(data.series.flatMap((s) => s.points.map((p) => p.at)))].sort();

  // **애널리스트마다 다른 색.** 예전에는 색 넷을 `% 4` 로 돌려써서 여섯 중
  // 둘씩 같은 색이었다(chart↔regime 빨강, event↔risk 초록) — 범례를 짚어
  // 가며 봐야 어느 선인지 알 수 있었다. 색은 구분하라고 있는 것이다.
  //
  // 손익 색(up/down)은 **쓰지 않는다.** 이 차트에서 초록·빨강은 "올랐다/
  // 내렸다" 가 아니라 그냥 계열 구분인데, 같은 화면의 다른 패널에서는 손익을
  // 뜻해서 한 색이 두 가지를 말하게 된다.
  // 팔레트는 scope.js COLOR.series (:root --s1~--s6) — 여기 다시 적으면
  // 토큰을 고친 날 이 파일만 옛 색으로 남는다.
  const colorOf = (index) => COLOR.series[index % COLOR.series.length];

  // **최신값을 범례에 넣는다.** 선 끝에 라벨을 달았더니 값이 가까운 계열끼리
  // 겹쳐서 `0.074`·`0.072` 가 한 덩어리로 뭉갰다(2026-08-19 아이폰 실측).
  // ECharts 는 endLabel 겹침을 피해 주지 않는다 — 자리를 옮기는 대신 값을
  // 겹칠 수 없는 곳으로 옮긴다.
  //
  // 통과(✓)·관찰(·)도 같이 붙인다. 점선 위아래를 눈으로 재지 않아도 지금
  // 무엇이 매매에 쓰이는지 범례만 보면 안다.
  const label = (series) => {
    const last = series.points.length
      ? series.points[series.points.length - 1].ic : null;
    if (last === null || last === undefined) return series.analyst;
    const mark = last >= data.threshold ? "✓" : "·";
    return `${series.analyst} ${last.toFixed(3)} ${mark}`;
  };

  const line = (series, index) => ({
    name: label(series),
    type: "line",
    // 점이 하나면 선이 안 보인다. 심볼을 항상 그린다.
    showSymbol: true,
    symbolSize: 6,
    // 측정이 드문드문이라 점 사이가 비는 날이 많다. 이어 그리지 않으면
    // 선이 조각나 어느 계열인지 못 쫓아간다.
    connectNulls: true,
    data: stamps.map((at) => {
      const hit = series.points.find((p) => p.at === at);
      return hit ? hit.ic : null;
    }),
    lineStyle: { width: 1.8, color: colorOf(index) },
    itemStyle: { color: colorOf(index) },
  });

  instance.setOption({
    ...BASE,
    // 범례를 **아래로** 내린다. 위에 두면 계열 여섯이 두 줄을 먹어 차트가
    // 그만큼 납작해진다(모바일에서 특히).
    legend: {
      ...BASE.legend,
      data: data.series.map(label),
      top: "auto", bottom: 0, itemGap: 8, itemWidth: 12,
      // 이름+값이라 항목이 길다. 줄바꿈을 허용하고 그만큼 아래를 비워 둔다 —
      // 안 그러면 범례가 x축 라벨 위에 얹힌다.
      type: "scroll", width: "96%",
      textStyle: { ...(BASE.legend && BASE.legend.textStyle), fontSize: 10 },
    },
    // 오른쪽 여백을 줄였다(끝 라벨을 없앴으므로). 아래는 범례 두 줄 + x축.
    grid: { left: 46, right: 16, top: 14, bottom: 74 },
    xAxis: { type: "category", data: stamps.map((at) => at.slice(0, 16).replace("T", " ")), ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS },
    series: [
      ...data.series.map(line),
      {
        // 합격선을 배경에 깐다. store.config 에서 읽은 값이지 코드에 적은
        // 숫자가 아니다 (services/learning.py 가 매 요청 다시 읽는다).
        name: `합격선(${data.threshold})`, type: "line",
        data: stamps.map(() => data.threshold),
        showSymbol: false, lineStyle: { width: 1, color: COLOR.warn, type: "dashed" },
        // **합격선 위를 옅게 칠한다.** 선 하나보다 면이 먼저 읽힌다 — 어느
        // 계열이 "쓰이는 쪽" 에 있는지가 한눈에 들어온다.
        markArea: {
          silent: true,
          itemStyle: { color: COLOR.ok, opacity: 0.06 },
          data: [[{ yAxis: data.threshold }, { yAxis: "max" }]],
        },
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

async function renderResearchLedger() {
  const { data } = await fetchJson("learning/research-ledger");
  const target = document.getElementById("research-ledger");
  if (!target) return;

  const fam = Object.entries(data.families || {})
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => `${name} ${n}`)
    .join(" · ");
  const budgetPct = data.quarter_budget
    ? Math.round((data.quarter_used / data.quarter_budget) * 100)
    : 0;
  const budgetClass = budgetPct >= 100 ? "weak" : "";

  // DSR — 표본이 모자라면 숫자를 지어내지 않는다 (불변식 3 의 화면판).
  let dsrCell;
  if (data.dsr) {
    const pct = (data.dsr.dsr * 100).toFixed(1);
    dsrCell = `<span>${pct}% · 관측값</span>
      <span class="kpi-note">일별 샤프 ${data.dsr.sharpe.toFixed(3)} vs 시행 ${data.cumulative_trials}회 운의 상한 ${data.dsr.expected_max.toFixed(3)} · 표본 ${data.dsr.sample_days}일</span>`;
  } else {
    dsrCell = `<span class="kpi-note">표본 부족 — NAV ${data.nav_sample_days}일 (30일 필요). 시간이 유일한 진짜 신규 데이터다</span>`;
  }

  const openings = (data.holdout_openings || []).length
    ? data.holdout_openings
        .map((o) => `${o.opened_at.slice(0, 10)} · ${o.reason} · ${o.window}`)
        .join("<br>")
    : `<span class="kpi-note">저장된 개봉 이력 없음 · 금고 시작 ${data.holdout_start}</span>`;

  target.innerHTML = `<table>
    <tbody>
      <tr><th>누적 시행</th>
        <td class="num"><strong>${data.cumulative_trials}</strong></td>
        <td><span class="kpi-note">${fam}</span></td></tr>
      <tr><th>분기 예산</th>
        <td class="num ${budgetClass}">${data.quarter_used} / ${data.quarter_budget}</td>
        <td><span class="kpi-note">${budgetPct}% 소진 — 다 쓰면 다음 분기까지 탐색을 멈춘다</span></td></tr>
      <tr><th>Deflated Sharpe</th>
        <td colspan="2">${dsrCell}</td></tr>
      <tr><th>홀드아웃 금고</th>
        <td colspan="2">${openings}</td></tr>
      <tr><th>승격 성적표</th>
        <td colspan="2"><span class="kpi-note">승격 파이프라인 미가동 — 제안·승격이 생기면 비율과 사후 성과가 여기 쌓인다</span></td></tr>
    </tbody></table>`;
}

/* 마지막 모델 회차(BE·BF·BG·D1·C0·C1) — **학습 진행만.** 시행마다 카드 하나.
 *
 * 카드가 답하는 세 질문: 무엇을 배우나(한 줄) · 얼마나 남았나(막대 + 예상 끝 시각) · 잘 되고 있나
 * (상태 배지 + 이유 한 줄). 상태·시각 말은 서비스가 as_of 로 계산해 보낸다 — 화면은 벽시계를 읽지
 * 않고(되감기면 그때의 상태가 나온다), 임계치(멈춤 배수·추세 창)도 들지 않는다(config).
 *
 * 판정 창의 수익·IC 는 이 칸에 오지 않는다(서비스가 애초에 안 담는다). 사전등록이 "학습 중에는
 * 판정 창을 보지 않는다" 로 정했고, 화면이 그것을 비추면 규칙이 깨진다. 그래프의 검증 선은
 * **학습창 안쪽 검증**이고, 서비스가 사람이 읽는 방향(높을수록 좋음)으로 되돌려 `score` 로 준다.
 * 판정이 끝난 시행은 시행 대장(research_trials)의 줄을 그대로 옮긴다.
 */
function elapsedLabel(seconds) {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)}초`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}분`;
  if (seconds < 3600 * 36) return `${(seconds / 3600).toFixed(1)}시간`;
  return `${(seconds / 86400).toFixed(1)}일`;
}

function frEsc(value) {
  return String(value ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

//: 상태 → (배지 글자, CSS 꼬리). 색은 CSS 토큰이 정한다.
const FR_STATUS = {
  ok: ["정상", "ok"],
  stalled: ["느림·멈춤 의심", "stall"],
  overfit: ["과적합 의심", "overfit"],
  unknown: ["판단 보류", "unknown"],
  done: ["끝남", "done"],
  queued: ["대기", "queued"],
};

function frChartId(trial, part) {
  return `chart-fr-${String(trial).replace(/[^A-Za-z0-9_-]/g, "_")}-${part}`;
}

function frHasLine(t, which) {
  return t.curves.some((c) => (c[which] || []).some((v) => v != null));
}

function frBadge(status) {
  const [label, tail] = FR_STATUS[status] || [status, "unknown"];
  return `<span class="fr-badge st-${tail}"><i aria-hidden="true"></i>${label}</span>`;
}

function frCard(t) {
  const unit = t.axis === "fold" ? "폴드" : "블록";
  const pct = t.progress == null ? null : Math.min(t.progress, 1) * 100;
  const seeds = t.n_seeds ? `시드 ${t.seeds.length}/${t.n_seeds}` : `시드 ${t.seeds.length}`;
  const units = t.units_total ? `${unit} ${t.units_done}/${t.units_total}` : `${unit} ${t.units_done}`;
  const barClass = t.status === "done" ? "done" : pct == null ? "unknown" : "on";
  // 남은 양을 모르면 그 칸을 **숨긴다** — "모른다" 를 한 칸 차지하게 두지 않는다.
  const facts = [
    t.eta_label ? ["예상 끝", t.eta_label, `약 ${elapsedLabel(t.eta_seconds)} 남음`] : null,
    ["경과", elapsedLabel(t.elapsed_wall_s), t.status === "done" ? "시작부터 끝까지" : "시작부터 지금까지"],
    ["마지막 기록", t.last_label || "—", t.mean_unit_s ? `${unit} 하나에 보통 ${elapsedLabel(t.mean_unit_s)}` : ""],
    t.early_share == null ? null
      : ["일찍 멈춘 비율", `${Math.round(t.early_share * 100)}%`, "과적합을 막으려 학습을 일찍 멈춘 비율"],
  ].filter(Boolean);
  const hasScore = frHasLine(t, "score");
  const hasTrain = frHasLine(t, "train");
  const charts = hasScore || hasTrain
    ? `${hasScore ? `<div class="fr-chart" id="${frChartId(t.trial, "score")}"></div>` : ""}
       ${hasTrain ? `<div class="fr-chart fr-chart-sub" id="${frChartId(t.trial, "train")}"></div>` : ""}`
    : `<p class="kpi-note">그래프 없음 — 이 시행은 손실을 적지 않는다${t.kind === "control" ? "(비교 기준 GBM 은 일찍 멈추기를 쓰지 않는다)" : ""}.</p>`;
  return `<article class="fr-card st-${(FR_STATUS[t.status] || [])[1] || "unknown"}">
    <div class="fr-info">
    <header class="fr-head">
      <strong class="fr-name">${frEsc(t.trial)}</strong>
      ${frBadge(t.status)}
      ${t.markets.length ? `<span class="fr-market">${frEsc(t.markets.join("+"))}</span>` : ""}
    </header>
    <p class="fr-about">${frEsc(t.about || (t.kind === "control" ? "비교 기준(대조군)" : ""))}</p>
    <p class="fr-reason">${frEsc(t.status_reason)}</p>
    <div class="fr-progress">
      <span class="fr-pct">${pct == null ? "—" : `${pct.toFixed(0)}%`}</span>
      <div class="bar ${barClass}" role="progressbar" aria-valuemin="0" aria-valuemax="100"
        ${pct == null ? "" : `aria-valuenow="${pct.toFixed(0)}"`}><span style="width:${pct == null ? 0 : pct.toFixed(1)}%"></span></div>
      <span class="fr-units">${seeds} · ${units}</span>
    </div>
    <dl class="fr-facts">${facts.map(([k, v, s]) => `<div><dt>${k}</dt><dd>${frEsc(v)}</dd>${s ? `<small>${frEsc(s)}</small>` : ""}</div>`).join("")}</dl>
    ${t.last_note ? `<p class="fr-note">${frEsc(t.last_note)}</p>` : ""}
    </div>
    <div class="fr-plots">${charts}</div>
  </article>`;
}

function frDrawCharts(t) {
  const unit = t.axis === "fold" ? "폴드" : "블록";
  // 시드마다 한 선. 한 줄로 이으면 시드 사이의 계단이 학습 곡선처럼 보인다.
  const lines = (which, faded) => t.curves
    .filter((c) => (c[which] || []).some((v) => v != null))
    .map((c, i) => {
      const color = COLOR.series[i % COLOR.series.length];
      return {
        type: "line", name: `시드 ${c.seed ?? "—"}${c.market ? ` · ${c.market}` : ""}`, showSymbol: c.x.length < 3, connectNulls: false,
        data: c.x.map((x, k) => [x, c[which][k]]),
        itemStyle: { color }, lineStyle: { width: faded ? 1 : 1.6, color, opacity: faded ? 0.5 : 1 },
      };
    });
  // x 축 이름(블록·폴드)은 축 끝에 세우면 오른쪽 여백에 눌려 세로로 찍혔다 — 눈금 값 뒤에 붙인다.
  const axes = (name, top, ticks) => ({
    grid: { left: 64, right: 14, top, bottom: 22 },  // 64: 0.00029 같은 긴 눈금이 잘리지 않게
    xAxis: { type: "value", minInterval: 1, axisLabel: { formatter: (v) => `${v}` } },
    yAxis: { type: "value", scale: true, name, nameLocation: "end", nameGap: 8, splitNumber: ticks,
             nameTextStyle: { color: COLOR.muted, fontSize: 11, align: "left" },
             // 자릿수는 값 크기에 맞춘다 — BG 우위는 0.0002 수준이라 고정 소수 둘째 자리면 눈금이 전부 0.00 이었다.
             axisLabel: { formatter: (v) => { const a = Math.abs(Number(v)); return a === 0 ? "0" : a >= 0.1 ? Number(v).toFixed(ticks <= 2 ? 3 : 2) : Number(v).toPrecision(2); } } },
  });
  const scoreId = frChartId(t.trial, "score");
  if (document.getElementById(scoreId)) {
    chart(scoreId).setOption({
      ...BASE, ...axes(`${t.score_label || "내부 검증"} · 가로축 ${unit}`, 30, 5),
      legend: { ...BASE.legend, show: t.curves.length > 1, top: 0, right: 0, itemWidth: 12, itemHeight: 6 },
      tooltip: { ...BASE.tooltip, trigger: "axis" },
      series: lines("score", false),
    }, true);
  }
  const trainId = frChartId(t.trial, "train");
  if (document.getElementById(trainId)) {
    chart(trainId).setOption({
      ...BASE, ...axes("학습 손실 (낮을수록 좋음 · 보조)", 22, 2),
      legend: { show: false },
      tooltip: { ...BASE.tooltip, trigger: "axis" },
      series: lines("train", true),
    }, true);
  }
}

function frQueue(queued) {
  if (!queued.length) return "";
  const items = queued.map((q, i) => `<li><span class="fr-order">${i + 1}</span>
    <strong>${frEsc(q.trial)}</strong> ${q.started
      ? `<span class="fr-badge st-ok"><i></i>시작됨 — 첫 진행 기록 전</span>`
      : frBadge("queued")}
    <span class="fr-about">${frEsc(q.about)}${q.started ? " · 학습 프로그램이 돌고 있다. 진행 기록은 한 구간(블록·폴드)이 끝날 때 적힌다" : ""}</span></li>`).join("");
  return `<section class="fr-queue"><h3>대기 <span class="sub">등록 순서 · 앞 시행이 끝나면 다음 것이 돈다</span></h3>
    <ol>${items}</ol></section>`;
}

function frControlsLine(controls) {
  // 끝난 비교 기준은 카드 대신 한 줄. 모델 카드가 볼 자리를 차지하지 않게 한다.
  if (!controls.length) return "";
  const names = controls.map((t) => frEsc(t.trial)).join("·");
  const times = controls.map((t) => frEsc(t.last_label)).join(" · ");
  return `<p class="fr-controls">${frBadge("done")} 비교 기준 ${names} 준비 완료 — ${times}
    <span class="kpi-note">모델이 이겨야 하는 기준선이다(지금 쓰는 GBM · 새 재료를 넣은 GBM)</span></p>`;
}

function renderFinalRoundVerdicts(target, data) {
  if (!data.verdicts.length) {
    target.innerHTML = `<p class="kpi-note">판정 기록 없음 — 학습이 끝나면 시행 대장에 한 줄이 적힌다.
      진행률이 100% 라도 판정은 별도 실행이다.</p>`;
    return;
  }
  const rows = data.verdicts.map((v) => `<tr>
    <td><strong>${frEsc(v.entity_id)}</strong><div class="kpi-note">${frEsc(v.family)} · ${frEsc(v.protocol_hash || "해시 미고정")}</div></td>
    <td>${String(v.at).replace("T", " ").slice(0, 16)}</td>
    <td>${frEsc(v.detail)}</td></tr>`).join("");
  target.innerHTML = `<h3>판정 기록 <span class="sub">시행 대장에 적힌 줄 그대로</span></h3>
    <table class="dense"><thead><tr><th>시행</th><th>시각</th><th>판정</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function renderFinalRound() {
  const target = document.getElementById("final-round-progress");
  const verdicts = document.getElementById("final-round-verdicts");
  if (!target) return;
  const body = await fetchJson("learning/final-round");
  const data = body.data;
  showScope(body);
  const queued = data.queued || [];
  const trials = data.trials || [];
  // 지금 도는 것 → 대기 → 끝난 것. 끝난 비교 기준은 한 줄로 접는다.
  const active = trials.filter((t) => t.status !== "done");
  const doneModels = trials.filter((t) => t.status === "done" && t.kind !== "control");
  const doneControls = trials.filter((t) => t.status === "done" && t.kind === "control");
  const parts = [];
  if (!data.has_data) {
    // **0행과 "돌렸는데 진행이 없다" 는 다른 사실이다.** 0 으로 그리지 않는다.
    parts.push(`<p class="empty">진행 기록이 0행이다 — 이 시점에 돌고 있는 회차 학습이 없다.</p>`);
  } else if (!active.length) {
    parts.push(`<p class="kpi-note">지금 도는 학습이 없다.</p>`);
  }
  if (active.length) parts.push(`<div class="fr-cards">${active.map(frCard).join("")}</div>`);
  parts.push(frQueue(queued));
  // 끝난 시행은 카드로 두지 않는다 — 결과·실패 이유는 ③ 과거 학습 내역(시행 카탈로그)으로 간다(사용자 2026-09-30).
  if (doneModels.length) {
    const names = doneModels.map((t) => frEsc(t.trial)).join(" · ");
    parts.push(`<p class="kpi-note fr-done-moved">${frBadge("done")} 끝난 시행 ${doneModels.length}개(${names}) —
      결과와 이유는 아래 <a href="#zone-history">과거 학습 내역</a>에 있다.</p>`);
  }
  parts.push(frControlsLine(doneControls));
  target.innerHTML = parts.join("");
  for (const t of active) frDrawCharts(t);
  if (verdicts) renderFinalRoundVerdicts(verdicts, data);
}

/* ② 지금 매매에 쓰이는 모델 — 흐름 단계 · 랭커 상태 · 병행 트랙.
 *
 * 설명 문구와 채택 기록은 서비스 상수(services/model_story.PIPELINE_STAGES) 한 곳에서 온다. 화면은
 * 문구를 들지 않는다. 설정 값도 서비스가 store.config(as_of) 로 읽어 보낸다 — 여기서 숫자를 적지 않는다.
 */
function lsDocLink(base, doc, label) {
  // 문서는 저장소 경로로만 보인다 — 화면에 바깥 출처를 두지 않는다(tests/invariants/test_dashboard_bans).
  if (!doc) return "";
  return `<span class="ls-doclink" title="${frEsc(doc)}">${label ? `${frEsc(label)} ` : ""}<span class="mono">${frEsc(doc)}</span></span>`;
}

function lsStage(stage, index, base) {
  const settings = stage.settings.length
    ? `<dl class="ls-set">${stage.settings.map((x) => `<div class="${x.found ? "" : "ls-missing"}">
        <dt>${frEsc(x.label)}${x.market ? ` <span class="ls-mk">${frEsc(x.market)}</span>` : ""}</dt>
        <dd title="${frEsc(x.key)}">${frEsc(x.display)}</dd></div>`).join("")}</dl>`
    : "";
  const adopted = stage.adopted.length
    ? `<ul class="ls-adopted">${stage.adopted.map((a) => `<li><span class="ls-when">${frEsc(a.date)}</span>
        <strong>${frEsc(a.trial)}</strong> <span class="ls-adopt-note">${frEsc(a.note)}</span>
        ${lsDocLink(base, a.doc)}</li>`).join("")}</ul>`
    : "";
  return `<li class="ls-stage" id="stage-${frEsc(stage.key)}">
    <span class="ls-step">${index + 1}</span>
    <div class="ls-stage-main">
      <h4>${frEsc(stage.title)}</h4>
      <p class="ls-plain">${frEsc(stage.plain)}</p>
      ${adopted}
      ${stage.detail ? `<details class="ls-more"><summary>왜 이렇게 하나</summary><p>${frEsc(stage.detail)}</p></details>` : ""}
    </div>
    <div class="ls-stage-set">${settings}</div>
  </li>`;
}

function lsRanker(r, base) {
  const threshold = r.threshold;
  const rows = (r.markets || []).map((m) => {
    const pass = threshold != null && m.ic != null ? m.ic >= threshold : null;
    const hist = (m.history || []).map((h) => h.ic);
    return `<tr>
      <td><strong>${frEsc(m.market)}</strong></td>
      <td class="num"><span class="${pass === false ? "weak" : pass ? "good" : ""}">${m.ic == null ? "—" : m.ic.toFixed(3)}</span>
        ${m.ic_t == null ? "" : `<span class="sub">t ${m.ic_t.toFixed(1)}</span>`}</td>
      <td class="ls-spark-cell">${spark(hist, COLOR.muted)}</td>
      <td class="num">${m.weight == null ? "—" : m.weight.toFixed(2)}</td>
      <td class="num">${frEsc(String(m.measured_at || "").slice(0, 10)) || "—"}</td></tr>`;
  }).join("");
  const table = rows
    ? `<table class="dense ls-ranker-table"><colgroup><col class="c-ls-mk"><col class="c-ls-ic"><col class="c-ls-trend"><col class="c-ls-w"><col class="c-ls-at"></colgroup>
        <thead><tr><th>시장</th><th class="num">적중도(IC)</th><th>추이</th><th class="num">가중치</th><th class="num">측정일</th></tr></thead>
        <tbody>${rows}</tbody></table>
       <p class="ls-hint">적중도(IC) = 랭커 점수 순위와 5일 뒤 수익 순위가 얼마나 맞았나(0 = 아무 관계 없음). 합격선
         ${threshold == null ? "설정 없음" : threshold} 을 넘어야 매매에 쓰인다. 매주 토요일 자동으로 잰다.</p>`
    : `<p class="empty">저장된 랭커 적중도 측정이 없다 — 모름.</p>`;
  const model = r.model;
  const modelBlock = model
    ? `<dl class="ls-set ls-model">
        <div><dt>지금 쓰는 모델 파일</dt><dd>${frEsc(model.file)}</dd></div>
        <div><dt>학습 자료 끝</dt><dd>${frEsc(model.trained_through)}</dd></div>
        <div><dt>매매에 쓰기 시작</dt><dd>${frEsc(model.usable_from)}</dd></div>
        <div><dt>학습 행 수</dt><dd>${num(model.rows)}</dd></div>
      </dl>
      <p class="ls-hint">홀드아웃 금고(2026-07-01 이후)를 아무도 안 본 기간으로 지키려고 그 뒤 자료로는 다시 학습하지 않는다 — 11월 금고 개봉 뒤 재학습.</p>
      ${model.gain.length ? `<div class="ls-gain" aria-label="입력별 기여">${model.gain.map((g) => `<div class="ls-gain-row">
          <span>${frEsc(g.feature)}</span><span class="ls-gain-track"><i style="width:${(g.share * 100).toFixed(1)}%"></i></span>
          <span class="num">${(g.share * 100).toFixed(0)}%</span></div>`).join("")}
        <p class="ls-hint">모델이 어느 입력에 기대는지(나눔 기여, gain). risk = 위험 점수 · event = 공시·이벤트 · is_us = 미장 여부.</p></div>` : ""}`
    : `<p class="empty">모델 파일을 찾지 못했다 — 모름.</p>`;
  return `${table}${modelBlock}`;
}

function lsTracks(tracks, base) {
  if (!tracks.length) return `<p class="empty">이 시점에 돌던 병행 장부가 없다.</p>`;
  return `<ul class="ls-tracks">${tracks.map((t) => `<li>
      <div class="ls-track-head"><strong>${frEsc(t.name)}</strong><span class="ls-when">${frEsc(t.started)}~</span></div>
      <p>${frEsc(t.compares)}</p>${lsTrackReturns(t.returns)}
      <p class="ls-hint"><span class="mono">${frEsc(t.ledger)}</span> ${lsDocLink(base, t.doc)}</p></li>`).join("")}</ul>`;
}

/* 병행 장부 수익 한 줄 — 회계가 적은 TWR 지수끼리(같은 창). 서버가 준 숫자만 옮긴다. */
function lsTrackReturns(r) {
  if (!r) return "";
  const pct = (v) => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`);
  const gap = r.book !== null && r.compare !== null && r.book !== undefined && r.compare !== undefined
    ? ` · 차 ${r.book - r.compare >= 0 ? "+" : ""}${((r.book - r.compare) * 100).toFixed(2)}%p` : "";
  const other = r.compare_name ? ` · ${frEsc(r.compare_name)} <span class="mono">${pct(r.compare)}</span>` : "";
  return `<p class="ls-hint">${frEsc(r.since)}부터 ${r.sessions}세션 · 이 장부 <span class="mono">${pct(r.book)}</span>${other}${gap}</p>`;
}

async function renderLiveModels() {
  const flow = document.getElementById("live-flow");
  if (!flow) return;
  const body = await fetchJson("learning/live-models");
  const data = body.data;
  const base = "";
  // 흐름 띠: 단계 이름만 한 줄. 누르면 그 단계 설명으로 간다.
  const strip = `<ol class="ls-strip" aria-label="매매 흐름 요약">${data.stages.map((st, i) =>
    `<li><a href="#stage-${frEsc(st.key)}"><span class="ls-step">${i + 1}</span>${frEsc(st.title)}</a></li>`).join("")}</ol>`;
  flow.innerHTML = `${strip}<ol class="ls-stages">${data.stages.map((st, i) => lsStage(st, i, base)).join("")}</ol>`;
  document.getElementById("live-ranker").innerHTML = lsRanker(data.ranker || {}, base);
  document.getElementById("live-tracks").innerHTML = lsTracks(data.tracks || [], base);
}

/* ③ 과거 학습 내역 — 카탈로그(사람이 옮겨 적은 요약) + 시행 대장. 판정은 원문 그대로다.
 * 분류 필터 상태는 URL 쿼리(?cat=)에 둔다 — 브라우저 저장소를 쓰지 않는다(금지 사항). 펼친 줄은
 * 이 페이지가 떠 있는 동안만 기억한다(자동 갱신 때 접히지 않게). */
const LS_RESULT = { 채택: "adopt", 기각: "reject", 보류: "hold", 진행중: "live" };
const lsOpen = new Set();
let lsHistory = null;
const LS_PAGE = 20;
let lsShowAll = false;
let lsShowLedger = false;

function lsCategory() {
  const value = new URLSearchParams(window.location.search).get("cat");
  return value || "전체";
}

function lsSetCategory(value) {
  const url = new URL(window.location.href);
  if (value === "전체") url.searchParams.delete("cat"); else url.searchParams.set("cat", value);
  window.history.replaceState(null, "", url);
  lsShowAll = false;
  lsDrawHistory();
}

function lsResultBadge(result, source) {
  if (!result) return `<span class="ls-badge ls-r-ledger">${source === "ledger_only" ? "대장 기록" : "결과 없음"}</span>`;
  return `<span class="ls-badge ls-r-${LS_RESULT[result] || "other"}">${frEsc(result)}</span>`;
}

function lsTrialRow(t, base) {
  const key = `${t.source}:${t.id}:${t.date}`;
  const whatText = t.what || (t.source === "ledger_only" ? "대장에만 있음 — 카탈로그 요약 없음" : "");
  const ledger = (t.ledger || []).map((l) => `<li><span class="mono">${frEsc(l.entity_id)}</span>
      <span class="ls-when">${frEsc(String(l.at).slice(0, 10))}</span><div>${frEsc(l.detail)}</div></li>`).join("");
  return `<details class="ls-trial" data-key="${frEsc(key)}" ${lsOpen.has(key) ? "open" : ""}>
    <summary>
      <span class="ls-when">${frEsc(t.date)}</span>
      <span class="ls-tid" title="${frEsc(t.id)}">${frEsc(t.id)}</span>
      <span class="ls-cat">${frEsc(t.category)}</span>
      <span class="ls-what"><strong>${frEsc(t.title || "")}</strong>${t.title && whatText ? " — " : ""}${frEsc(whatText)}</span>
      ${lsResultBadge(t.result, t.source)}
    </summary>
    <div class="ls-trial-body">
      ${t.why ? `<p><span class="ls-k">${t.result === "채택" ? "채택 이유" : t.result === "기각" ? "안 된 이유" : "내용"}</span>${frEsc(t.why)}</p>` : ""}
      ${ledger ? `<p class="ls-k">시행 대장에 적힌 줄</p><ul class="ls-ledger">${ledger}</ul>` : ""}
      ${t.doc ? `<p class="ls-doc">근거 ${lsDocLink(base, t.doc)}</p>` : ""}
    </div>
  </details>`;
}

function lsDrawHistory() {
  const data = lsHistory;
  const list = document.getElementById("history-list");
  if (!data || !list) return;
  const current = lsCategory();
  const cats = ["전체", ...data.categories, ...(data.summary.by_category["기타"] ? ["기타"] : [])];
  document.getElementById("history-filter").innerHTML = cats.map((c) => {
    const n = c === "전체" ? data.summary.total : (data.summary.by_category[c] || 0);
    return `<button type="button" class="ls-chip" data-cat="${frEsc(c)}" aria-pressed="${c === current}">${frEsc(c)} <span class="num">${n}</span></button>`;
  }).join("");
  for (const button of document.getElementById("history-filter").querySelectorAll("button")) {
    button.addEventListener("click", () => lsSetCategory(button.dataset.cat));
  }
  const inCat = data.trials.filter((t) => current === "전체" || t.category === current);
  // 목록 다듬기(2026-09-29 점검): 83줄이 한 번에 펼쳐져 탭이 7,000px 였고, 그중 '대장에만 있음 —
  // 카탈로그 요약 없음' 줄(제목도 설명도 없는 시행 대장 원문)이 사이사이 끼어 읽기를 끊었다.
  // 그 줄은 기본으로 접고, 목록은 최근 LS_PAGE 줄만 보인다. 둘 다 누르면 펼친다 — 지우지 않는다.
  // 상태는 이 페이지가 떠 있는 동안만 기억한다(브라우저 저장소 금지).
  const ledgerOnly = inCat.filter((t) => t.source === "ledger_only" && !t.what);
  const visible = lsShowLedger ? inCat : inCat.filter((t) => !ledgerOnly.includes(t));
  const rows = lsShowAll ? visible : visible.slice(0, LS_PAGE);
  const more = [];
  if (visible.length > rows.length) {
    more.push(`<button type="button" class="ls-chip" data-more="all">나머지 ${visible.length - rows.length}건 더 보기</button>`);
  }
  if (ledgerOnly.length) {
    more.push(`<button type="button" class="ls-chip" data-more="ledger" aria-pressed="${lsShowLedger}">${
      lsShowLedger ? "요약 없는 대장 줄 숨기기" : `요약 없는 대장 줄 ${ledgerOnly.length}건 보기`}</button>`);
  }
  list.innerHTML = (rows.length
    ? rows.map((t) => lsTrialRow(t, "")).join("")
    : `<p class="empty">이 분류에 ${ledgerOnly.length ? "요약이 있는 " : ""}기록이 없다.</p>`)
    + (more.length ? `<div class="ls-more-row">${more.join("")}</div>` : "");
  for (const button of list.querySelectorAll(".ls-more-row button")) {
    button.addEventListener("click", () => {
      if (button.dataset.more === "all") lsShowAll = true; else lsShowLedger = !lsShowLedger;
      lsDrawHistory();
    });
  }
  for (const node of list.querySelectorAll("details.ls-trial")) {
    node.addEventListener("toggle", () => {
      if (node.open) lsOpen.add(node.dataset.key); else lsOpen.delete(node.dataset.key);
    });
  }
}

async function renderTrialHistory() {
  const summary = document.getElementById("history-summary");
  if (!summary) return;
  const body = await fetchJson("learning/trial-history");
  const data = body.data;
  lsHistory = data;
  const r = data.summary.by_result;
  summary.innerHTML = `<p class="ls-count"><strong>총 ${data.summary.total}건</strong>
    · <span class="ls-r-adopt">채택 ${r["채택"] || 0}</span> · 기각 ${r["기각"] || 0} · 보류 ${r["보류"] || 0} · 진행중 ${r["진행중"] || 0}
    <span class="sub">요약 ${data.summary.catalog}건 + 대장에만 있는 줄 ${data.summary.ledger_only}건</span></p>
    ${data.problems.length ? `<p class="kpi-note">카탈로그에서 읽지 못한 줄 ${data.problems.length}개 — ${frEsc(data.problems.slice(0, 3).join(" · "))}</p>` : ""}`;
  const lessons = data.lessons || [];
  document.getElementById("history-lessons").innerHTML = lessons.length
    ? `<div class="ls-lessons"><h3>배운 것 세 줄</h3><ol>${lessons.map((l) =>
        `<li>${frEsc(l.text)} ${lsDocLink("", l.source)}</li>`).join("")}</ol></div>`
    : "";
  lsDrawHistory();
}

runAll([renderFinalRound, renderLiveModels, renderTrialHistory, renderKpis, renderGate, renderIcHistory, renderResearchLedger, renderOpenDiagnostics]);

const diagnostics = document.getElementById("rl-diagnostics");
async function renderOpenDiagnostics() {
  if (!document.getElementById("rl-diagnostics")?.open) return;
  await renderM4Placeholders();
  await renderResearchJobs();
}
if (diagnostics) diagnostics.addEventListener("toggle", async () => {
  try { await renderOpenDiagnostics(); } catch (_) {
    document.getElementById("training-live").textContent = "진단 조회 실패 · 재조회하려면 접었다 펼친다.";
  }
});


const VERDICT_LABEL = {
  generalizes: ["두 구간 다 균등가중을 이긴다 — 일반화의 증거", "ok"],
  overfit: ["학습 구간에서만 이긴다 — 과적합", "bad"],
  untrained: ["학습 구간에서도 못 이긴다 — 학습이 안 됐다", "bad"],
};

function evalPct(v, digits = 2) {
  return v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function evalSigned(v, digits = 5) {
  return v == null || Number.isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;
}

/* '기본 전략보다 나은가' — 최신 평가 배치. **보상 기준·균등가중 대조군**이다.
 * 스코어 비례(M3 룰)와의 비교는 아직 재지 않았으므로 그렇다고 적는다. */
function renderEvaluation(target, evals) {
  const latest = evals.latest;
  const [verdictText, cls] = VERDICT_LABEL[latest.verdict] || [latest.verdict, ""];
  const rows = [["train", "학습 구간(본 것)"], ["oos", "OOS(안 본 것)"]]
    .filter(([key]) => latest.table[key])
    .map(([key, label]) => {
      const w = latest.table[key];
      const p = w.policy || {};
      const e = w.equal || {};
      return `<tr><td>${label}</td>
        <td class="num">${evalSigned(p.reward_mean)}</td>
        <td class="num">${evalSigned(e.reward_mean)}</td>
        <td class="num ${w.gap > 0 ? "ok" : "bad"}">${evalSigned(w.gap)}</td>
        <td class="num mobile-hide">${evalPct(p.cash_weight, 1)} / ${evalPct(e.cash_weight, 1)}</td>
        <td class="num mobile-hide">${evalSigned(p.cost)} / ${evalSigned(e.cost)}</td>
      </tr>`;
    })
    .join("");
  target.classList.remove("empty");
  target.innerHTML = `
    <p class="plain ${cls}"><strong>${verdictText}.</strong></p>
    <table class="dense">
      <thead><tr><th>구간</th><th class="num">정책</th><th class="num">균등가중</th>
        <th class="num">차이</th><th class="num mobile-hide">현금 (정책/균등)</th>
        <th class="num mobile-hide">비용/일</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="muted">보상 평균/일 = 초과수익 − 낙폭벌점 − 비용. ${latest.run_id} · 업데이트 ${latest.update ?? "—"}
      · 에피소드 ${latest.episode_days}일 × env ${latest.envs} · 평가 ${new Date(latest.evaluated_at).toLocaleString("ko-KR")}.
      대조군은 균등가중뿐 — 스코어 비례(M3 룰)와의 비교는 아직 안 쟀다.</p>`;
}

/* '운이었나 실력이었나' — 학습 시드 수와, 같은 run 을 자를 바꿔 다시 잰 편차.
 * 시드가 하나면 시드 분산은 **없다**고 말한다. 평가 표본 편차는 다른 사실이다. */
function renderEvaluationSpread(target, evals) {
  const seeds = evals.train_seeds || [];
  const history = evals.history || [];
  const oos = history.map((h) => h.gap_oos).filter((v) => v != null);
  const flips = history.length
    ? new Set(history.map((h) => h.verdict)).size - 1
    : 0;
  const spread = oos.length > 1 ? Math.max(...oos) - Math.min(...oos) : null;
  const seedLine = seeds.length > 1
    ? `학습 시드 ${seeds.length}개(${seeds.join(", ")}) — 시드 간 비교 가능.`
    : `학습 시드 <strong>${seeds.length}개</strong>(${seeds.join(", ") || "—"}) — 시드 간 분산은 <strong>잴 수 없다</strong>. 3시드가 §13 의 요구다.`;
  const rows = history.slice(-6).reverse().map((h) => `<tr>
      <td>${new Date(h.evaluated_at).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
      <td class="num">${h.envs}</td>
      <td class="num">${evalSigned(h.gap_train)}</td>
      <td class="num ${h.gap_oos > 0 ? "ok" : "bad"}">${evalSigned(h.gap_oos)}</td>
      <td>${(VERDICT_LABEL[h.verdict] || [h.verdict])[0].split(" — ")[1] || h.verdict}</td>
    </tr>`).join("");
  target.classList.remove("empty");
  target.innerHTML = `
    <p class="plain">${seedLine}</p>
    <p class="plain">같은 정책을 평가 표본만 바꿔 ${history.length}번 쟀다 —
      OOS 우위 편차 ${spread == null ? "—" : evalSigned(spread)} · 판정이 뒤집힌 횟수 <strong>${flips}</strong>.</p>
    <table class="dense">
      <thead><tr><th>평가</th><th class="num">env</th><th class="num">학습 우위</th><th class="num">OOS 우위</th><th>판정</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}


const STAGE_LABEL = {
  passed: ["통과", "ok"],
  failed: ["불합격", "bad"],
  running: ["학습 중", ""],
  unevaluated: ["평가 전", ""],
  pending: ["미착수", "dim"],
};

/* '훈련 단계와 사전 점검' — C0~C5. 상태는 게이트 로그·rl_updates·rl_evaluations 에서만 온다. */
function renderCurriculum(target, data) {
  const rows = data.stages.map((s) => {
    const [text, cls] = STAGE_LABEL[s.status] || [s.status, ""];
    const isCurrent = s.stage === data.current;
    return `<tr class="${isCurrent ? "current" : ""}">
      <td><strong>${s.stage}</strong>${isCurrent ? " ◀" : ""}</td>
      <td>${s.label}<span class="code">${s.criterion}</span></td>
      <td><span class="badge ${cls}">${text}</span></td>
      <td class="mobile-hide">${s.note}</td>
    </tr>`;
  }).join("");
  const gate = data.gate;
  const gateLine = gate.checked
    ? `카나리 게이트 ${gate.passed ? "통과" : "실패"} — ${gate.detail} (${gate.at ? gate.at.slice(0, 10) : ""})`
    : "카나리 게이트를 돌린 기록이 없다";
  target.classList.remove("empty");
  target.innerHTML = `
    <p class="plain">${gateLine}. 지금 단계 <strong>${data.current || "—"}</strong> — 깨진 단계를 고치기 전에 다음으로 넘어가지 않는다.</p>
    <table class="dense">
      <thead><tr><th>단계</th><th>설정 · 통과 기준</th><th>상태</th><th class="mobile-hide">근거</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}


/* 연구 작업 — 프로세스 + 로그. 실패해도 나머지 화면을 막지 않는다. */
async function renderResearchJobs() {
  const target = document.getElementById("research-jobs");
  if (!target) return;
  let body;
  try { body = await fetchJson("learning/research-jobs"); } catch (e) { target.innerHTML = `<p class="empty">연구 작업 목록을 못 읽었다.</p>`; return; }
  const d = body.data || {};
  const running = d.running || []; const logs = d.logs || [];
  const runRows = running.length
    ? running.map((p) => `<tr><td class="mono">${p.pid}</td><td>${p.script}</td><td class="num">${p.cpu_pct}%</td><td class="num">${p.rss_mb} MB</td><td class="num">${p.uptime_h} h</td></tr>`).join("")
    : `<tr><td colspan="5" class="empty">지금 도는 연구 스크립트 없음</td></tr>`;
  const logRows = logs.map((l) => `<tr><td class="mono">${l.log}</td><td class="mono">${String(l.modified).replace("T", " ").slice(5, 16)}</td><td>${l.last}</td></tr>`).join("");
  target.innerHTML = `
    <table class="dense"><thead><tr><th>PID</th><th>스크립트</th><th class="num">CPU</th><th class="num">RSS</th><th class="num">가동</th></tr></thead><tbody>${runRows}</tbody></table>
    <h3>최근 로그</h3>
    <table class="dense"><thead><tr><th>로그</th><th>수정</th><th>마지막 줄</th></tr></thead><tbody>${logRows || '<tr><td colspan="3" class="empty">없음</td></tr>'}</tbody></table>`;
}
