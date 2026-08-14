/* AI 리뷰 탭 — LLM 이 실제로 무엇을 했는지.
 *
 * 공통 규약은 scope.js 에 있다. 여기서는 이 화면 고유의 표현만 만든다.
 *
 * ``verdicts`` 가 거의 비어 있는 것은 고장이 아니다. milestones.md M2 가
 * 못 박았다 — 뉴스 40건 → 패턴 4건 → LLM 4건 전부 기각, 0건은 "거부할
 * 사유가 없었다" 는 정답이다. 화면이 그 구분을 말한다.
 */

const stamp = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");
const krw = (v) => (v === null || v === undefined ? "—" : `₩${Number(v).toLocaleString("ko-KR")}`);
const usd = (v) => (v === null || v === undefined ? "—" : `$${Number(v).toFixed(4)}`);

async function renderKpis() {
  const body = await fetchJson("ai-review/summary");
  const d = body.data;
  showScope(body);

  document.getElementById("kpis").innerHTML = [
    kpi("LLM 호출", num(d.calls), "재계산 건수 — 캐시 적중은 안 잡힌다"),
    kpi("에이전트", num(d.agents), "news-screen · macro-brief"),
    kpi("판정 기록", num(d.verdicts_total), "News · SNS"),
    kpi("거부", num(d.blocked), "0건이면 거부할 사유가 없었다는 뜻일 수 있다"),
    kpi("공시 · 뉴스", num(d.documents), "판정의 입력"),
    kpi("LLM 비용", krw(d.llm_cost_krw), `${usd(d.llm_cost_usd)} · llm_usage ${num(d.llm_usage_calls)}건`),
  ].join("");

  showAlerts(d.warnings);
}

async function renderCosts() {
  const { data } = await fetchJson("ai-review/costs");

  const sinceEl = document.getElementById("cost-since");
  sinceEl.textContent = data.total_calls
    ? `— 이 창 안 첫 기록: ${stamp(data.since)}`
    : "— 이 창엔 기록이 없다";

  const t = data.tokens;
  document.getElementById("cost-kpis").innerHTML = [
    kpi("호출", num(data.total_calls), "llm_usage 행 수(왕복 하나당 1행)"),
    kpi("비용", krw(data.cost_krw), `${usd(data.cost_usd)} · ₩1=$${(1 / data.usd_krw_rate).toFixed(6)} 환산`),
    kpi("input 토큰", num(t ? t.input : null), "정가"),
    kpi("output 토큰", num(t ? t.output : null), "정가"),
    kpi("캐시 쓰기", num(t ? t.cache_write : null), "input의 1.25배 단가"),
    kpi("캐시 읽기", num(t ? t.cache_read : null), "input의 0.1배 단가 — 훨씬 싸다"),
  ].join("");

  const target = document.getElementById("cost-by-agent");
  if (!data.by_agent.length) {
    target.innerHTML = `<div class="empty">이 창 안에 llm_usage 기록이 없다.
      이 표가 생기기 전 호출은 집계되지 않는다 — 0원이 아니라 몰랐다는 뜻이다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>에이전트</th><th>모델</th><th class="num">호출</th>
      <th class="num">항목</th><th class="num">토큰(입/출/캐시w/캐시r)</th>
      <th class="num">비용</th></tr></thead>
    <tbody>${data.by_agent.map((row) => `<tr>
      <td>${row.agent}</td>
      <td class="mono">${row.model}${row.priced ? "" : ' <span class="tag dim">단가 모름</span>'}</td>
      <td class="num">${num(row.calls)}</td>
      <td class="num">${num(row.items)}</td>
      <td class="num mono">${num(row.tokens.input)}/${num(row.tokens.output)}/${num(row.tokens.cache_write)}/${num(row.tokens.cache_read)}</td>
      <td class="num">${row.priced ? krw(row.cost_krw) : "—"}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderAgents() {
  const { data } = await fetchJson("ai-review/calls");
  const target = document.getElementById("agents");
  if (!data.agents.agents.length) {
    target.innerHTML = `<div class="empty">LLM 호출 기록이 없다.
      news_screen · macro_brief 가 아직 안 돌았을 수 있다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>에이전트</th><th class="num">호출</th><th class="num">종목</th>
      <th class="num">최근 계산</th></tr></thead>
    <tbody>${data.agents.agents.map((a) => `<tr>
      <td>${a.agent}<div class="kpi-note">${a.version}</div></td>
      <td class="num">${num(a.calls)}</td>
      <td class="num">${num(a.entities)}</td>
      <td class="num">${stamp(a.last_computed_at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderCalls() {
  const { data } = await fetchJson("ai-review/calls");
  const target = document.getElementById("calls");
  if (!data.recent.length) {
    target.innerHTML = `<div class="empty">호출 기록이 없다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>종목</th><th>에이전트</th><th>요약</th>
      <th class="num">as_of</th><th class="num">계산 시각</th></tr></thead>
    <tbody>${data.recent.map((c) => `<tr>
      <td class="mono">${c.entity_id}</td>
      <td>${c.agent}</td>
      <td>${c.summary}</td>
      <td class="num">${stamp(c.as_of)}</td>
      <td class="num">${stamp(c.computed_at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderVerdictScorecard() {
  const { data } = await fetchJson("ai-review/verdicts");
  const target = document.getElementById("verdict-scorecard");
  const card = data.scorecard || {};
  if (!data.total) {
    target.innerHTML = `<div class="empty">판정 기록이 없다.
      배관은 완성돼 있다 — 판정할 뉴스·SNS 가 아직 없었다는 뜻이다.</div>`;
    return;
  }
  target.innerHTML = `
    <div class="kpis" style="margin-bottom:10px">
      ${kpi("판정", num(data.total), "News · SNS 전체")}
      ${kpi("거부", num(data.blocked), "매수 금지만 가능하다")}
      ${kpi("채점 완료", num(card.settled), `진행 중 ${num(card.pending)}건은 제외`)}
      ${kpi("적중률", pct(card.hit_rate, 1), "차단 종목이 시장보다 더 빠진 비율",
            card.hit_rate !== null && card.hit_rate < 0.5)}
      ${kpi("평균 초과수익", pct(card.mean_excess, 2), "음수면 손실을 피한 것",
            card.mean_excess !== null && card.mean_excess > 0)}
    </div>
    ${data.by_analyst.length ? `<table>
      <thead><tr><th>Analyst</th><th class="num">거부 건수</th></tr></thead>
      <tbody>${data.by_analyst.map((row) => `<tr>
        <td>${row.analyst}</td><td class="num">${num(row.count)}</td>
      </tr>`).join("")}</tbody>
    </table>` : ""}`;
}

async function renderVerdicts() {
  const { data } = await fetchJson("ai-review/verdicts");
  const target = document.getElementById("verdicts");
  if (!data.recent.length) {
    target.innerHTML = `<div class="empty">거부 기록이 없다.
      0건은 고장이 아니라 "거부할 사유가 없었다" 는 정답일 수 있다
      (docs/milestones.md M2).</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>종목</th><th>Analyst</th><th>판정</th><th>사유</th>
      <th class="num">시각</th></tr></thead>
    <tbody>${data.recent.map((v) => `<tr>
      <td class="mono">${v.entity_id}</td>
      <td>${v.analyst}</td>
      <td><span class="tag ${v.decision === "block" ? "observe" : "dim"}">${v.decision}</span></td>
      <td>${v.reason || v.category || "—"}</td>
      <td class="num">${stamp(v.at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderDocuments() {
  const { data } = await fetchJson("ai-review/documents");
  const target = document.getElementById("documents");
  if (!data.recent.length) {
    target.innerHTML = `<div class="empty">수집된 공시·뉴스가 없다.</div>`;
    return;
  }
  target.innerHTML = `<table>
    <thead><tr><th>종목</th><th>유형</th><th>제목</th>
      <th class="num">시각</th></tr></thead>
    <tbody>${data.recent.map((d) => `<tr>
      <td class="mono">${d.entity_id}</td>
      <td>${d.doc_type}</td>
      <td>${d.title}</td>
      <td class="num">${stamp(d.at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

runAll([renderKpis, renderCosts, renderAgents, renderCalls, renderVerdictScorecard, renderVerdicts, renderDocuments]);
