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
    kpi("LLM 월 예산", `$${num(d.llm_monthly_budget_usd)}`, "참고값 — 실제 지출 미집계"),
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

function tableStaleChip(row) {
  if (row.latest_valid_from === null) return '<span class="chip tone-flat">미가동</span>';
  if (row.stale_days === null) return "";
  if (row.stale_days > 3) return `<span class="chip tone-off">${row.stale_days}일 지연</span>`;
  return '<span class="chip tone-on">최신</span>';
}

async function renderTables() {
  const { data } = await fetchJson("system/tables");
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
        <strong class="${data.killswitch_engaged ? 'down' : 'up'}">
          ${data.killswitch_engaged ? "발동" : "정상"}</strong></div>
      <div class="fact"><span>사유</span><strong>${esc(data.killswitch_reason || "—")}</strong></div>
      <div class="fact"><span>설정 판번호</span><strong>v${num(data.config_version)}</strong></div>
      <div class="fact"><span>LLM 월 예산</span><strong>$${num(data.llm_monthly_budget_usd)}</strong></div>
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

/* 서버 리소스 · 프로세스 — LS_KR system 탭에서 가져왔다. 창고를 안 거치고
 * /proc 만 읽으므로 as_of 와 무관하다 (services/system.py 모듈 docstring). */

async function renderResources() {
  const { data } = await fetchJson("system/resources");
  const c = data.cpu, m = data.memory, disk = data.disk;
  document.getElementById("resources").innerHTML = [
    c
      ? kpi("CPU", dec(c.load1, 2), `load1/5/15 · 코어 ${num(c.cores)}개 · 코어당 ${dec(c.load1_pct, 1)}%`)
      : kpi("CPU", "—", "측정 불가(리눅스 /proc 없음)"),
    m
      ? kpi("메모리", `${dec(m.used_gb, 1)}`, `/ ${dec(m.total_gb, 1)}GB · 여유 ${dec(m.avail_gb, 1)}GB`,
            false, { unit: "GB" })
      : kpi("메모리", "—", "측정 불가"),
    disk
      ? kpi("디스크", `${dec(disk.used_gb, 0)}`, `/ ${dec(disk.total_gb, 0)}GB · 여유 ${dec(disk.free_gb, 0)}GB`,
            false, { unit: "GB" })
      : kpi("디스크", "—", "측정 불가"),
  ].join("");
}

async function renderProcesses() {
  const { data } = await fetchJson("system/processes");
  document.getElementById("resources-total-rss").textContent =
    data.total_rss_mb !== null ? `워커 RSS 합 ${num(data.total_rss_mb)}MB` : "";
  const target = document.getElementById("processes");
  if (!data.processes.length) {
    target.innerHTML = `<div class="empty">이 레포 아래에서 도는 프로세스가 없다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>PID · 명령</th><th class="num">CPU%</th>
      <th class="num">RSS(MB)</th><th class="num">가동(h)</th></tr></thead>
    <tbody>${data.processes.map((p) => `<tr>
      <td><span class="lead"><span class="sub">${num(p.pid)}</span>
        <span class="trunc">${esc(p.command)}</span></span></td>
      <td class="num">${dec(p.cpu_pct, 1)}</td>
      <td class="num">${num(p.rss_mb)}</td>
      <td class="num">${dec(p.uptime_h, 1)}</td>
    </tr>`).join("")}</tbody></table>`;
}

runAll([
  renderSummary, renderResources, renderProcesses, renderJobs, renderTables,
  renderLatency, renderSafety, renderCache,
]);
