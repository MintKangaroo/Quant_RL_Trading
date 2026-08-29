/* 학습 탭 — RL(M4)은 아직 없다.
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
  const [statusBody, runsBody, evalBody] = await Promise.all([
    fetchJson("learning/status"),
    fetchJson("learning/training-runs"),
    fetchJson("learning/evaluations"),
  ]);
  const data = statusBody.data;
  const runs = runsBody.data;
  const evals = evalBody.data;
  renderTrainingLive(runs);

  for (const widget of data.widgets) {
    const target = document.getElementById(M4_TARGETS[widget.key]);
    if (!target) continue;

    // 그릴 수 있게 된 칸이면 그린다. 아니면 왜 비었는지를 말한다.
    if (runs.has_data && RUN_SERIES[widget.key]) {
      drawRunChart(target, widget.key, runs);
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
      target.innerHTML = "<strong>학습은 완주했지만 평가를 아직 안 돌렸다.</strong>"
        + "<br>tools/evaluate_policy.py --save 가 rl_evaluations 를 채운다.";
      continue;
    }
    const why = RUN_SERIES[widget.key]
      ? "학습 기록이 0행이다 — 표는 있다(rl_updates). PPO 를 돌리면 채워진다."
      : "학습을 완주해야 나온다.";
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
    ? `<p class="plain" style="margin:0 0 10px;padding:10px 12px;border-left:3px solid #4C9AFF;background:var(--panel2);font-size:13px;line-height:1.5">${run.plain}</p>`
    : "";
  const rows = [
    ["상태", `<span class="${tone}">${statusText}</span>${lastAt ? ` · 마지막 기록 ${lastAt.toLocaleString("ko-KR", { hour12: false })} (${minutesLabel(run.silent_minutes)} 전)` : ""}`],
    ["진행", `${lastUpdate == null ? "—" : lastUpdate.toLocaleString()} / ${total == null ? "—" : total.toLocaleString()} 업데이트 (${pct.toFixed(1)}%)
      <div style="height:6px;background:var(--line,#333);border-radius:3px;margin-top:4px"><div style="width:${pct.toFixed(1)}%;height:6px;background:#4C9AFF;border-radius:3px"></div></div>`],
    ["페이스", `${run.pace_minutes == null ? "—" : run.pace_minutes.toFixed(1) + "분/업데이트"} · 남은시간 ${minutesLabel(run.eta_minutes ?? null)}`],
    ["보상 추이", `최근 50 평균 ${fmt(recent)} · 그 앞 50 평균 ${fmt(before)}${recent != null && before != null ? ` · 차이 ${(recent - before >= 0 ? "+" : "") + (recent - before).toFixed(5)}` : ""}`],
    ["마지막 지표", `EV ${fmt(last("explained_variance"), 3)} · KL ${fmt(last("approx_kl"), 5)} · grad ${fmt(last("grad_norm"), 2)} · 반영률 ${fmt(last("action_reflection"), 3)} · 현금 ${fmt(last("cash_weight"), 3)}`],
    ["실행", `${run.run_id} · seed ${run.seed ?? "—"} · ${run.curriculum || "—"} · ${run.git_commit ? run.git_commit.slice(0, 7) : "—"}`],
  ];
  target.innerHTML = plain + `<table class="kv">${rows
    .map(([k, v]) => `<tr><th style="white-space:nowrap;text-align:left;padding-right:12px;vertical-align:top">${k}</th><td>${v}</td></tr>`)
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
  target.innerHTML = `<div id="chart-${key}" style="height:220px"></div>
    <div class="dim" style="font-size:11px;margin-top:4px">
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
    itemStyle: { color: ["#4C9AFF", "#F5A623", "#9B7BF7"][i % 3] },
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
    kpi("RL 학습(M4)", s.label ?? (s.active ? "가동" : "미착수"),
        s.run ? `${s.run.run_id}` : s.milestone),
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

  // **애널리스트마다 다른 색.** 예전에는 색 넷을 `% 4` 로 돌려써서 여섯 중
  // 둘씩 같은 색이었다(chart↔regime 빨강, event↔risk 초록) — 범례를 짚어
  // 가며 봐야 어느 선인지 알 수 있었다. 색은 구분하라고 있는 것이다.
  //
  // 손익 색(up/down)은 **쓰지 않는다.** 이 차트에서 초록·빨강은 "올랐다/
  // 내렸다" 가 아니라 그냥 계열 구분인데, 같은 화면의 다른 패널에서는 손익을
  // 뜻해서 한 색이 두 가지를 말하게 된다.
  const SERIES_COLORS = [
    "#4C9AFF", // 파랑
    "#F5A623", // 주황
    "#9B7BF7", // 보라
    "#22C7A9", // 청록
    "#E06C9F", // 분홍
    "#8FA3B8", // 회청
  ];
  const colorOf = (index) => SERIES_COLORS[index % SERIES_COLORS.length];

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
          itemStyle: { color: COLOR.up, opacity: 0.06 },
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
    const cls = data.dsr.dsr >= 0.95 ? "good" : "weak";
    dsrCell = `<span class="${cls}">${pct}%</span>
      <span class="kpi-note">일별 샤프 ${data.dsr.sharpe.toFixed(3)} vs 시행 ${data.cumulative_trials}회 운의 상한 ${data.dsr.expected_max.toFixed(3)} · 표본 ${data.dsr.sample_days}일</span>`;
  } else {
    dsrCell = `<span class="kpi-note">표본 부족 — NAV ${data.nav_sample_days}일 (30일 필요). 시간이 유일한 진짜 신규 데이터다</span>`;
  }

  const openings = (data.holdout_openings || []).length
    ? data.holdout_openings
        .map((o) => `${o.opened_at.slice(0, 10)} · ${o.reason} · ${o.window}`)
        .join("<br>")
    : `<span class="kpi-note">개봉 이력 없음 — 정상 상태다. 금고 시작 ${data.holdout_start}</span>`;

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

runAll([renderKpis, renderM4Placeholders, renderGate, renderIcHistory, renderWalkForward, renderResearchLedger]);


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
