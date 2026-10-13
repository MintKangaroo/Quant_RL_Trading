/* 시스템 탭 — 지금 이 시스템이 제대로 돌고 있는가.
 *
 * 공통 규약(타임머신 컨트롤, as_of 부착, KPI·경고 헬퍼)은 scope.js 에 있다.
 * 여기서는 이 화면 고유의 표현만 만든다.
 */

/* 표에 들어가는 값은 창고·로그에서 온 문자열이다. 그대로 넣으면 마크업으로
   해석될 수 있다. */
function esc(value) {
  return String(value === null || value === undefined ? "—" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function stamp(iso) {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

async function renderSummary() {
  const body = await fetchJson("system/summary");
  const d = body.data;
  showScope(body);

  const jobHealth = d.job_failed_count > 0
    ? `실패 ${num(d.job_failed_count)}건`
    : d.job_silent_count > 0
      ? `미확인 ${num(d.job_silent_count)}건`
      : "전부 정상";

  document.getElementById("kpis").innerHTML = [
    kpi("킬스위치", d.killswitch_engaged ? "발동" : "정상",
        d.killswitch_engaged ? esc(d.killswitch_reason) : "안전장치 대기",
        d.killswitch_engaged),
    kpi("설정 판번호", `v${num(d.config_version)}`, "store.config"),
    kpi("크론 작업", jobHealth, `등록 ${num(d.job_count)}개 (crontab -l 기준)`,
        d.job_failed_count > 0 || d.job_silent_count > 0),
    kpi("지연 테이블", `${num(d.table_stale_count)} / ${num(d.table_count)}`,
        "최근 파티션 기준", d.table_stale_count > 0),
    kpi("LLM 캐시 적재", num(d.cache_recent_entries), "최근 창 · agent_cache"),
    d.llm_usage_cost_usd !== null
      ? kpi("LLM 실측 지출", `$${dec(d.llm_usage_cost_usd, 2)}`,
          `최근 창 · 월 예산 $${num(d.llm_monthly_budget_usd)}`,
          d.llm_usage_cost_usd > d.llm_monthly_budget_usd)
      : kpi("LLM 실측 지출", "단가 모름",
          d.llm_usage_unpriced_models.length
            ? `단가표 없음: ${d.llm_usage_unpriced_models.join(", ")}`
            : "최근 창에 호출 기록 없음"),
    // 임계치를 안 둔다 — store.config 에 CPU/메모리/디스크 경고선이 아직
    // 없다. 없는 임계치로 색을 칠하면 코드에 숫자를 박는 것과 같다(불변식 10).
    kpi("메모리 사용률", pct(d.mem_used_pct !== null ? d.mem_used_pct / 100 : null, 1), "이 기계 전체"),
    kpi("디스크 사용률", pct(d.disk_used_pct !== null ? d.disk_used_pct / 100 : null, 1), "창고 파티션"),
  ].join("");

  showAlerts(d.warnings);
}

function jobRunChip(run) {
  if (run.ok === null) return '<span class="chip tone-mix">미확인</span>';
  return run.ok ? '<span class="chip tone-on">성공</span>' : '<span class="chip tone-off">실패</span>';
}

async function renderJobs() {
  const { data } = await fetchJson("system/jobs");
  const target = document.getElementById("jobs");
  if (!data.length) {
    target.innerHTML = `<div class="empty">등록된 작업이 없다.</div>`;
    return;
  }
  drawJobsStrip(data);
  target.innerHTML = `<table>
    <thead><tr><th>작업</th><th>일정</th><th>최근 실행</th><th>결과</th>
      <th>마지막 성공</th></tr></thead>
    <tbody>${data.map((job) => {
      const last = job.runs[0];
      return `<tr>
        <td><strong>${esc(job.label)}</strong></td>
        <td class="sub">${esc(job.schedule)}</td>
        <td class="num sub">${last ? stamp(last.started_at) : "기록 없음"}</td>
        <td>${last ? jobRunChip(last) : "—"}</td>
        <td class="num sub">${stamp(job.last_success_at)}</td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

/* 지연 배지. **거래일 기준이다** — 서버가 그렇게 센다(services/system.py
 * `_trading_days_since`). 달력일로 세던 시절에는 연휴만 지나면 전 테이블이
 * 빨간불이었고, 매일 뜨는 빨간불은 곧 아무도 안 보는 빨간불이 된다.
 *
 * `alarm: false` 인 테이블은 **사건 테이블**이다(입출금 등). 며칠째 안 들어온
 * 것이 고장이 아니라 정상이라, 마지막 날짜는 보여주되 경보색을 안 쓴다.
 */
function tableStaleChip(row) {
  if (row.latest_valid_from === null) return '<span class="chip tone-flat">미가동</span>';
  if (row.stale_days === null) return "";
  if (row.alarm === false) {
    return `<span class="chip tone-flat" title="입출금처럼 사건이 있을 때만 생기는 표라 지연 경보를 걸지 않는다">${row.stale_days}거래일 전</span>`;
  }
  if (row.stale_days > 3) {
    return `<span class="chip tone-off">${row.stale_days}거래일 지연</span>`;
  }
  return '<span class="chip tone-on">최신</span>';
}

async function renderTables() {
  const { data } = await fetchJson("system/tables");
  drawTablesBar(data);
  document.getElementById("tables").innerHTML = `<table>
    <thead><tr><th>테이블</th><th class="num">최근 창 행수</th>
      <th>최신 valid_from</th><th>상태</th></tr></thead>
    <tbody>${data.map((row) => `<tr>
      <td class="mono">${esc(row.table)}</td>
      <td class="num">${num(row.rows_recent)}</td>
      <td class="num sub">${stamp(row.latest_valid_from)}</td>
      <td>${tableStaleChip(row)}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderLatency() {
  const { data } = await fetchJson("system/latency");
  const target = document.getElementById("latency");
  if (!data.stages.length) {
    target.innerHTML = `<div class="empty">최근 창에 수집 지연 기록이 없다.</div>`;
    return;
  }
  drawLatencyChart(data.stages);
  target.innerHTML = `<table>
    <thead><tr><th>단계</th><th class="num">p50</th><th class="num">p90</th>
      <th class="num">p99</th><th class="num">성공률</th><th class="num">표본</th></tr></thead>
    <tbody>${data.stages.map((s) => `<tr>
      <td>${esc(s.stage)}</td>
      <td class="num">${ms(s.p50)}</td><td class="num">${ms(s.p90)}</td><td class="num">${ms(s.p99)}</td>
      <td class="num">${pct(s.ok_rate, 1)}</td><td class="num">${num(s.samples)}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderSafety() {
  const { data } = await fetchJson("system/safety");
  document.getElementById("safety").innerHTML = `
    <div class="facts">
      <div class="fact"><span>킬스위치</span>
        <strong class="${data.killswitch_engaged ? 'bad' : 'good'}">
          ${data.killswitch_engaged ? "발동" : "정상"}</strong></div>
      <div class="fact"><span>설정 판번호</span><strong>v${num(data.config_version)}</strong></div>
      <div class="fact"><span>LLM 월 예산</span><strong>$${num(data.llm_monthly_budget_usd)}</strong></div>
      ${data.killswitch_reason ? `<div class="fact fact-wide"><span>${data.killswitch_engaged ? "발동 사유" : "마지막 해제 기록"}</span>
        <p>${esc(data.killswitch_reason)}</p></div>` : ""}
    </div>`;
}

async function renderCache() {
  const { data } = await fetchJson("system/cache");
  const target = document.getElementById("cache");
  if (!data.rows.length) {
    target.innerHTML = `<div class="empty">최근 창에 캐시 적재 기록이 없다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>에이전트</th><th class="num">버전</th>
      <th class="num">적재 건수</th><th class="num">최신 계산</th></tr></thead>
    <tbody>${data.rows.map((row) => `<tr>
      <td>${esc(row.agent)}</td>
      <td class="num sub">${esc(row.agent_version)}</td>
      <td class="num">${num(row.entries)}</td>
      <td class="num sub">${stamp(row.last_computed_at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

function llmCostCell(row) {
  return row.cost_usd !== null ? `$${dec(row.cost_usd, 4)}` : `<span class="sub">단가 모름</span>`;
}

async function renderLlmUsage() {
  const { data } = await fetchJson("system/llm-usage");
  const target = document.getElementById("llm-usage");
  if (!data.rows.length) {
    target.innerHTML = `<div class="empty">최근 창에 LLM 호출 기록이 없다.</div>`;
    return;
  }
  const note = document.getElementById("llm-usage-note");
  if (note) {
    note.textContent = data.unpriced_models.length
      ? `단가표에 없는 모델: ${data.unpriced_models.join(", ")} — 그 모델의 비용은 합계에서 빠졌다.`
      : "";
  }
  drawLlmBar(data.rows);
  target.innerHTML = `<table>
    <thead><tr><th>에이전트</th><th>모델</th><th class="num">호출</th>
      <th class="num">입력 토큰</th><th class="num">출력 토큰</th>
      <th class="num">비용</th><th class="num">최근 호출</th></tr></thead>
    <tbody>${data.rows.map((row) => `<tr>
      <td>${esc(row.agent)}</td>
      <td class="sub">${esc(row.model)}</td>
      <td class="num">${num(row.calls)}</td>
      <td class="num">${num(row.input_tokens)}</td>
      <td class="num">${num(row.output_tokens)}</td>
      <td class="num">${llmCostCell(row)}</td>
      <td class="num sub">${stamp(row.last_call_at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

/* 서버 리소스 · 프로세스 — LS_KR system.html 의 카드형 화면 구성을 그대로
 * 옮겼다(색만 우리 팔레트). 창고를 안 거치고 /proc 만 읽으므로 as_of 와
 * 무관하다 (services/system.py 모듈 docstring). */

let lastResources = null;
let lastProcesses = null;

async function renderResources() {
  const { data } = await fetchJson("system/resources");
  lastResources = data;
  const c = data.cpu, m = data.memory, d = data.disk;

  if (c) {
    document.getElementById("cpu-cores").textContent = `${num(c.cores)} cores`;
    document.getElementById("cpu-load").textContent = dec(c.load1, 2);
    document.getElementById("cpu-detail").textContent =
      `load 1/5/15 = ${dec(c.load1, 2)} / ${dec(c.load5, 2)} / ${dec(c.load15, 2)} · 코어당 ${dec(c.load1_pct, 1)}%`;
    document.getElementById("cpu-bar").style.width = `${c.load1_pct ?? 0}%`;
  } else {
    document.getElementById("cpu-detail").textContent = "측정 불가(리눅스 /proc 없음)";
  }

  if (m) {
    document.getElementById("mem-used").textContent = dec(m.used_gb, 1);
    document.getElementById("mem-total").textContent = dec(m.total_gb, 1);
    document.getElementById("mem-detail").textContent =
      `사용 ${dec(m.used_pct, 1)}% · 여유 ${dec(m.avail_gb, 1)}GB`;
    document.getElementById("mem-bar").style.width = `${m.used_pct ?? 0}%`;
  } else {
    document.getElementById("mem-detail").textContent = "측정 불가";
  }

  if (d) {
    document.getElementById("disk-used").textContent = dec(d.used_gb, 0);
    document.getElementById("disk-total").textContent = dec(d.total_gb, 0);
    document.getElementById("disk-detail").textContent =
      `사용 ${dec(d.used_pct, 1)}% · 여유 ${dec(d.free_gb, 0)}GB`;
    document.getElementById("disk-bar").style.width = `${d.used_pct ?? 0}%`;
  } else {
    document.getElementById("disk-detail").textContent = "측정 불가";
  }

}

async function renderProcesses() {
  const { data } = await fetchJson("system/processes");
  lastProcesses = data;
  document.getElementById("proc-rss").textContent = data.total_rss_mb ?? "—";
  const target = document.getElementById("proc-rows");
  target.innerHTML = data.processes.length
    ? data.processes.map((p) => `<tr>
        <td class="cmd" title="${esc(p.command)}">${num(p.pid)} · ${esc(p.command)}</td>
        <td class="num">${dec(p.cpu_pct, 1)}</td>
        <td class="num">${num(p.rss_mb)}</td>
        <td class="num">${dec(p.uptime_h, 1)}</td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="empty">이 레포 아래에서 도는 프로세스가 없다.</td></tr>`;

}


/* 파이프라인 이벤트 — 관측→Signal→Selector→Allocator→주문→체결 판단 로그(창고 events). 중요 시황 탭에서 옮겼다
 * (2026-10-13 밤) — 배관이 도는지의 증거라 운영 갈래다. 창고 사실이라 as_of 로 되감긴다. 시각은 한국 시간. */
async function renderEvents() {
  const target = document.getElementById("events");
  if (!target) return;
  const { data } = await fetchJson("system/events");
  const rows = data.events || [];
  const count = document.getElementById("events-count");
  if (count) count.textContent = `${rows.length}건`;
  if (!rows.length) {
    target.innerHTML = `<p class="empty">창 안에 파이프라인 이벤트가 없다 — 세션이 events 를 남기면 관측→체결 판단이 여기 쌓인다.</p>`;
    return;
  }
  drawEventsStrip(rows);
  target.innerHTML = `<table class="stamp-first dense"><thead><tr><th>시각 KST</th><th>실행</th><th class="mid">단계</th>
    <th>주체</th></tr></thead><tbody>${rows.map((row) => `<tr>
      <td class="num">${kstStamp(row.ts_sim)}</td>
      <td class="code">${esc(row.run_id)}</td>
      <td class="mid"><span class="tag dim">${esc(row.stage)}</span></td>
      <td>${esc(row.actor)}</td></tr>`).join("")}</tbody></table>`;
}

/* -- 그림 (dashboard.md §8-5) — 표 위에 핵심 하나. 값은 API 그대로, 상태색은 ok/bad(손익색 아님). -- */
const kstDay = (iso) => { const d = kstDate(iso); return d ? d.slice(5) : "—"; };

/* 시각 축은 한국 시간 — ECharts 시간 축은 보는 기기 시간대로 찍으므로, KST 로 옮긴 값을 UTC 로 읽어 적는다. */
const SYS_KST_MS = 9 * 3600 * 1000;
const sysKstMs = (iso) => Date.parse(iso) + SYS_KST_MS;
const sysAxisLabel = (v) => { const d = new Date(v); const p2 = (n) => String(n).padStart(2, "0");
  return `${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`; };
const SYS_RUN = {
  ok: ["성공", () => COLOR.ok], fail: ["실패", () => COLOR.bad],
  deferred: ["미룸(rc=6)", () => COLOR.warn], unknown: ["미확인(rc 못 찾음)", () => COLOR.dim],
};
const runStatus = (run) => run.status || (run.ok === false ? "fail" : run.ok === null ? "unknown" : "ok");

/** 크론 작업 — 작업 × 시각 타임라인(KST). 점 하나 = 한 번 실행, 색 = 결과(성공·실패·미룸·미확인). */
function drawJobsStrip(jobs) {
  const el = document.getElementById("sys-jobs-strip");
  const cap = document.getElementById("sys-jobs-cap");
  if (!el) return;
  const pts = [];
  jobs.forEach((job) => (job.runs || []).forEach((run) => pts.push({ job, run })));
  if (!pts.length) { el.style.display = "none"; if (cap) cap.innerHTML = ""; return; }
  el.style.display = "";
  const phone = vizPhone();
  el.style.height = `${jobs.length * (phone ? 26 : 30) + 34}px`;
  if (cap) cap.innerHTML = vizCaption(Object.values(SYS_RUN).map(([label, color]) => [color(), label]), "점 = 실행 한 번 · 시각 KST");
  chart("sys-jobs-strip").setOption({
    ...BASE, legend: { show: false },
    tooltip: { ...BASE.tooltip, trigger: "item", formatter: (p) => { const { job, run } = pts[p.dataIndex];
      return `${esc(job.label)}<br>${kstStamp(run.started_at, true)}${run.market ? ` · ${esc(run.market)}` : ""}<br>${SYS_RUN[runStatus(run)][0]}${run.steps && run.steps.length ? ` · rc ${run.steps.join(",")}` : ""}`; } },
    grid: { left: 8, right: 14, top: 6, bottom: 20, containLabel: true },
    xAxis: { type: "time", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: sysAxisLabel, hideOverlap: true } },
    yAxis: { type: "category", data: jobs.map((j) => vizTrunc(j.label, phone ? 10 : 22)), inverse: true, ...AXIS, axisTick: { show: false },
             splitLine: { show: true, lineStyle: { color: COLOR.border, opacity: 0.5 } } },
    series: [{ type: "scatter", symbolSize: phone ? 7 : 9,
      data: pts.map(({ job, run }) => ({ value: [sysKstMs(run.started_at), vizTrunc(job.label, phone ? 10 : 22)],
        itemStyle: { color: SYS_RUN[runStatus(run)][1]() } })) }],
  }, true);
}

/** 데이터 신선도 — 기준일 띠(services/freshness)의 항목별 늦은 세션 수. 0 은 '최신' 이라 적고, 색 = 상태. */
const SYS_FRESH = { ok: () => COLOR.ok, stale: () => COLOR.bad, pending: () => COLOR.warn };
async function renderFreshChart() {
  const target = document.getElementById("sys-fresh-bar");
  if (!target) return;
  const body = await fetchJson("system/freshness");
  const items = (body.data && body.data.items) || [];
  const cap = document.getElementById("sys-fresh-cap");
  if (cap) cap.innerHTML = vizCaption([[COLOR.ok, "최신"], [COLOR.warn, "수집 대기"], [COLOR.bad, "지연"], [COLOR.dim, "모름"]], "막대 = 늦은 세션 수");
  const label = (it) => (it.status === "ok" ? "최신" : it.status === "pending" ? "대기" : it.status === "stale" ? `${it.lag_sessions}세션 늦음` : "모름");
  hbarChart("sys-fresh-bar", items.map((it) => ({
    label: it.label, value: Math.max(it.lag_sessions || 0, 0.15),   // 0 은 막대가 안 보여 얇은 자리만 — 값 글자는 '최신'
    color: (SYS_FRESH[it.status] || (() => COLOR.dim))(), text: label(it),
    tip: `${esc(it.label)}<br>${label(it)} · 기대 ${it.expected ? it.expected.slice(0, 10) : "—"} · 관측 ${it.observed ? it.observed.slice(0, 10) : "—"}`,
  })), { labelOf: (r) => r.text, min: 90, labelMax: 18, right: 90 });   // 막대 끝 글자는 값이 아니라 상태 말
}

/** 메모리·스왑 추이 — 램 가드 로그 매시 상태 줄(MB). 사건(작업 내림 등)은 세로 점선. 경고선은 긋지 않는다 — 기준이 config 에 없다. */
async function renderMemory() {
  const el = document.getElementById("sys-mem-chart");
  if (!el) return;
  const { data } = await fetchJson("system/memory");
  const cap = document.getElementById("sys-mem-cap");
  const pts = data.points || [];
  if (!pts.length) { el.innerHTML = `<p class="empty">${esc(data.note || "램 가드 로그가 비어 있다.")}</p>`; if (cap) cap.innerHTML = ""; return; }
  const events = data.events || [];
  if (cap) cap.innerHTML = vizCaption([[COLOR.series[0], "가용(GB)"], [COLOR.warn, "스왑 사용(GB)"], [COLOR.bad, "램 가드 사건"]],
    `시각 KST · 사건 ${events.length}건`);
  chart("sys-mem-chart").setOption({
    ...BASE, legend: { show: false },
    tooltip: { ...BASE.tooltip, trigger: "axis",
      formatter: (ps) => `${kstStamp(pts[ps[0].dataIndex].at, true)}<br>${ps.map((p) => `${p.seriesName} ${dec(p.value[1], 1)}GB`).join(" · ")}` },
    grid: { left: 36, right: 10, top: 10, bottom: 22 },
    xAxis: { type: "time", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: sysAxisLabel, hideOverlap: true } },
    yAxis: { type: "value", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v) => `${v}G` } },
    series: [
      { name: "가용", type: "line", showSymbol: false, data: pts.map((p) => [sysKstMs(p.at), p.available_mb / 1024]),
        lineStyle: { width: 1.4, color: COLOR.series[0] }, areaStyle: { color: COLOR.series[0], opacity: 0.08 },
        markLine: events.length ? { silent: false, symbol: "none", lineStyle: { color: COLOR.bad, type: "dotted", opacity: 0.7 },
          label: { show: false }, data: events.map((e) => ({ xAxis: sysKstMs(e.at), name: e.text })),
          tooltip: { formatter: (p) => esc(p.name) } } : undefined },
      { name: "스왑", type: "line", showSymbol: false, data: pts.map((p) => [sysKstMs(p.at), p.swap_mb / 1024]),
        lineStyle: { width: 1.2, color: COLOR.warn, type: "dashed" } },
    ],
  }, true);
}

/** 연구 작업 — 로그별 마지막 기록 시각(점)과 지금 도는 스크립트(굵은 점). 되감기지 않는 '지금'. */
function drawResearch(d) {
  const el = document.getElementById("sys-research-chart");
  const cap = document.getElementById("sys-research-cap");
  if (!el) return;
  const logs = (d.logs || []).filter((l) => l.modified);
  if (!logs.length) {
    el.style.display = "none";
    if (cap) cap.innerHTML = `<p class="empty">최근 연구 로그가 없다 · 지금 도는 연구 스크립트 ${(d.running || []).length}개.</p>`;
    return;
  }
  el.style.display = "";
  const phone = vizPhone();
  el.style.height = `${Math.max(110, logs.length * (phone ? 18 : 20) + 30)}px`;
  if (cap) cap.innerHTML = vizCaption([[COLOR.muted, "로그 마지막 기록"]], `지금 도는 연구 스크립트 ${(d.running || []).length}개`);
  // 로그 수정 시각은 서버가 그 기계 시각(KST, 시간대 없음)으로 준다 — 그대로 KST 로 읽는다.
  const at = (l) => Date.parse(String(l.modified).slice(0, 19) + "Z");
  chart("sys-research-chart").setOption({
    ...BASE, legend: { show: false },
    tooltip: { ...BASE.tooltip, trigger: "item", formatter: (p) => { const l = logs[p.dataIndex]; return `${esc(l.log)}<br>${esc(String(l.modified).replace("T", " ").slice(0, 16))} KST<br>${esc(String(l.last || "").slice(0, 120))}`; } },
    grid: { left: 8, right: 14, top: 6, bottom: 20, containLabel: true },
    xAxis: { type: "time", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: sysAxisLabel, hideOverlap: true } },
    yAxis: { type: "category", data: logs.map((l) => vizTrunc(String(l.log).replace(/\.log$/, ""), phone ? 14 : 24)), inverse: true, ...AXIS, axisTick: { show: false } },
    series: [{ type: "scatter", symbolSize: 8, itemStyle: { color: COLOR.muted },
      data: logs.map((l) => [at(l), vizTrunc(String(l.log).replace(/\.log$/, ""), phone ? 14 : 24)]) }],
  }, true);
}

/** 창고 테이블 — 최근 창 행수(로그 축) 가로 막대, 색 = 최신성(표의 배지 tableStaleChip 과 같은 판정 — 기준이 둘이면 안 된다).
 *  모든 표가 '1거래일 전' 인 평일엔 지연 막대가 다 같아 아무 말도 안 한다 — 크기와 최신성을 한 그림에. 0행(미가동)은 막대 없이 캡션에. */
function drawTablesBar(rows) {
  const state = (r) => (r.alarm === false ? "event" : tableStaleChip(r).includes("tone-off") ? "stale" : "ok");
  const live = rows.filter((r) => r.rows_recent > 0).sort((a, b) => b.rows_recent - a.rows_recent);
  const idle = rows.length - live.length;
  const stale = live.filter((r) => state(r) === "stale").length;
  const cap = document.getElementById("sys-tables-cap");
  if (cap) cap.innerHTML = vizCaption([[COLOR.bad, "지연 경보"], [COLOR.muted, "최신"], [COLOR.faint, "사건 표(경보 없음)"]],
    `막대 = 최근 창 행수(로그) · 지연 ${stale} · 창 안 0행 ${idle}개는 아래 표`);
  hbarChart("sys-tables-bar", live.map((r) => ({
    label: r.table, value: r.rows_recent,
    color: state(r) === "stale" ? COLOR.bad : state(r) === "event" ? COLOR.faint : COLOR.muted,
    tip: `${esc(r.table)}<br>최근 창 ${num(r.rows_recent)}행 · ${stamp(r.latest_valid_from)}${r.stale_days != null ? ` · ${r.stale_days}거래일 전` : ""}`,
  })), { top: 16, phoneTop: 10, log: true, format: (v) => num(v), min: 90 });
}

/** 수집 지연 — 단계별 p50·p90·p99(ms) 묶음 가로 막대, 로그 축(단계마다 자릿수가 다르다). */
function drawLatencyChart(stages) {
  const el = document.getElementById("sys-latency-chart");
  if (!el) return;
  const rows = stages.slice().reverse();
  const phone = vizPhone();
  el.style.height = `${Math.max(120, rows.length * (phone ? 30 : 34) + 24)}px`;
  const cap = document.getElementById("sys-latency-cap");
  if (cap) cap.innerHTML = vizCaption([[COLOR.muted, "p50"], [COLOR.series[1], "p90"], [COLOR.warn, "p99"]], "ms · 로그 축");
  const bar = (key, color) => ({ name: key, type: "bar", barMaxWidth: 7, barGap: "15%",
    data: rows.map((r) => (r[key] && r[key] > 0 ? r[key] : null)), itemStyle: { color } });
  chart("sys-latency-chart").setOption({
    ...BASE, legend: { show: false },
    tooltip: { ...BASE.tooltip, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `${esc(r.stage)}<br>p50 ${ms(r.p50)} · p90 ${ms(r.p90)} · p99 ${ms(r.p99)}<br>성공률 ${pct(r.ok_rate, 1)} · 표본 ${num(r.samples)}`; } },
    grid: { left: 8, right: 16, top: 4, bottom: 20, containLabel: true },
    xAxis: { type: "log", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v) => (v >= 1000 ? `${v / 1000}s` : `${v}ms`) } },
    yAxis: { type: "category", data: rows.map((r) => vizTrunc(r.stage, phone ? 14 : 22)), ...AXIS, axisTick: { show: false } },
    series: [bar("p50", COLOR.muted), bar("p90", COLOR.series[1]), bar("p99", COLOR.warn)],
  }, true);
}

/** LLM 실제 호출 — 에이전트·모델별 비용(달러) 가로 막대. 단가 모르는 줄은 막대 없이 뺀다(0 달러가 아니다). */
function drawLlmBar(rows) {
  const priced = rows.filter((r) => r.cost_usd !== null).sort((a, b) => b.cost_usd - a.cost_usd);
  hbarChart("sys-llm-bar", priced.map((r) => ({
    label: `${r.agent} · ${r.model}`, value: r.cost_usd,
    tip: `${esc(r.agent)} · ${esc(r.model)}<br>$${dec(r.cost_usd, 4)} · 호출 ${num(r.calls)}`,
  })), { top: 10, phoneTop: 6, format: (v) => `$${dec(v, 2)}`, labelMax: 34, min: 70 });
}

/** 파이프라인 이벤트 — 단계 × 세션 칸 띠. 세션마다 관측→선정→배분→노출→집행이 다 찍혔나를 한눈에. */
const SYS_STAGE_ORDER = ["observe", "signal", "select", "allocate", "exposure", "execute", "fill"];
function drawEventsStrip(rows) {
  const runs = [...new Set(rows.map((r) => r.run_id))].sort();
  const stages = [...new Set(rows.map((r) => r.stage))]
    .sort((a, b) => (SYS_STAGE_ORDER.indexOf(a) + 1 || 99) - (SYS_STAGE_ORDER.indexOf(b) + 1 || 99));
  // "session-KR-2026-10-12" → "10-12 KR" — 칸 아래 글자가 짧아야 폰에서 겹치지 않는다.
  const label = (run) => { const m = /^session-([A-Z]+)-\d{4}-(\d{2}-\d{2})$/.exec(String(run)); return m ? `${m[2]} ${m[1]}` : String(run); };
  const cap = document.getElementById("sys-events-cap");
  if (cap) cap.innerHTML = vizCaption([[COLOR.ok, "기록 있음"]], `빈칸 = 그 세션에 그 단계 기록 없음 · 최근 ${rows.length}건 기준`);
  stripChart("sys-events-strip", stages, runs.map(label), rows.map((r) => ({
    row: r.stage, col: label(r.run_id), color: COLOR.ok,
    tip: `${esc(r.run_id)}<br>${esc(r.stage)} · ${esc(r.actor)}<br>${kstStamp(r.ts_sim, true)}`,
  })), { labelMax: 12 });
}

/* 연구 작업 — 지금 도는 연구 스크립트(프로세스)와 최근 로그. 모델 검증 탭의 접힌 RL 진단 안에 있던 것을 옮겼다
 * (2026-10-13 밤). 같은 API(`/api/learning/research-jobs`). 이 기계의 지금 상태라 as_of 로 되감기지 않는다. */
async function renderResearchJobs() {
  const target = document.getElementById("research-jobs");
  if (!target) return;
  let body;
  try { body = await fetchJson("learning/research-jobs"); } catch (e) { target.innerHTML = `<p class="empty">연구 작업 목록을 못 읽었다.</p>`; return; }
  const d = body.data || {};
  drawResearch(d);
  const running = d.running || []; const logs = d.logs || [];
  const runRows = running.length
    ? running.map((p) => `<tr><td class="mono">${esc(p.pid)}</td><td>${esc(p.script)}</td><td class="num">${esc(p.cpu_pct)}%</td><td class="num">${esc(p.rss_mb)} MB</td><td class="num">${esc(p.uptime_h)} h</td></tr>`).join("")
    : `<tr><td colspan="5" class="empty">지금 도는 연구 스크립트 없음</td></tr>`;
  const logRows = logs.map((l) => `<tr><td class="mono">${esc(l.log)}</td><td class="mono">${esc(String(l.modified).replace("T", " ").slice(5, 16))}</td><td>${esc(l.last)}</td></tr>`).join("");
  target.innerHTML = `
    <table class="dense"><thead><tr><th>PID</th><th>스크립트</th><th class="num">CPU</th><th class="num">RSS</th><th class="num">가동</th></tr></thead><tbody>${runRows}</tbody></table>
    <h3 class="sys-sub-h">최근 로그</h3>
    <table class="dense"><thead><tr><th>로그</th><th>수정</th><th>마지막 줄</th></tr></thead><tbody>${logRows || '<tr><td colspan="3" class="empty">없음</td></tr>'}</tbody></table>`;
}

function renderClock() {
  document.getElementById("sys-updated").textContent =
    "갱신 " + new Date().toLocaleTimeString("ko-KR");
}

runAll([
  renderSummary, renderResources, renderProcesses, renderJobs, renderTables,
  renderLatency, renderSafety, renderCache, renderLlmUsage, renderResearchJobs, renderEvents, renderFreshChart, renderMemory, renderClock,
]);
setInterval(() => { renderResources(); renderProcesses(); renderClock(); }, 8000);
