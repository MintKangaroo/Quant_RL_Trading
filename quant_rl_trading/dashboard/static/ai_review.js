/* AI 리뷰 탭 — LLM 이 실제로 무엇을 했고 얼마가 들었는지.
 *
 * 공통 규약은 scope.js 에 있다. 여기서는 이 화면 고유의 표현만 만든다.
 * 두 출처를 합친 화면이다 — 내력은 ai_review.html 머리 주석에 있다.
 *
 * **같은 숫자를 두 번 쓰지 않는다.** 합치기 전에는 한 값이 세 군데 있었다:
 *   · 경고 문자열 → #alerts 띠 · 문제점 카드 · 우선순위 액션
 *   · 비용 총액   → 머리 배지 · KPI 타일 · 비용 패널 KPI
 *   · 판정·거부   → KPI 타일 · 거부 성적표 KPI
 * 지금은 각각 한 곳에서만 말한다. 아래 비용 패널은 **총액이 어떻게 나왔는지**
 * (토큰 네 갈래·예산)만, 거부 성적표는 **사후 성적**만 맡는다.
 *
 * ``verdicts`` 가 거의 비어 있는 것은 고장이 아니다. milestones.md M2 가
 * 못 박았다 — 뉴스 40건 → 패턴 4건 → LLM 4건 전부 기각, 0건은 "거부할
 * 사유가 없었다" 는 정답이다. 화면이 그 구분을 말한다.
 */

/* 표에 들어가는 값은 창고에서 온 문자열이다(기사 제목·거부 사유). 그대로
   넣으면 마크업으로 해석된다 — 제목 하나에 "<" 가 있으면 표가 무너진다. */
function esc(value) {
  return String(value === null || value === undefined ? "—" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const stamp = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");
const krw = (v) => (v === null || v === undefined ? "—" : `₩${Number(v).toLocaleString("ko-KR")}`);
/* 청구 통화는 달러다. 원화는 llm.usd_krw_rate 로 환산한 참고치일 뿐이라
   예산 비교·정렬은 달러로 한다 (config 주석도 같은 말을 한다). */
const usd = (v, digits = 2) => (v === null || v === undefined ? "—" : `$${Number(v).toFixed(digits)}`);

/* LS_KR 의 air-kpi 타일. 공용 kpi()(scope.js)는 .kpi 클래스로 다른 톤을
 * 쓰므로, 이 탭에서 이식한 카드는 직접 그린다 — 값은 전부 실측치다.
 *
 * tone 은 회색(mut)만 쓴다. 이 화면엔 손익이 없다 — 비용은 손실이 아니라
 * 지출이다. 손익이 아닌 값에 초록·빨강을 주면 화면이 색으로 덮인다
 * (dashboard.md §10). 예산 초과만 예외로 경고색을 받는다. */
function airKpi(label, value, note, tone) {
  const cls = tone ? ` ${tone}` : "";
  return `<div class="air-kpi"><div class="k">${label}</div>
    <div class="v${cls}">${value}</div>
    <div class="mut" style="font-size:10.5px;margin-top:2px">${note || ""}</div></div>`;
}

/* 시장별 활동 — KR·US 를 합계 하나로 뭉개지 않는다(dashboard.md §8-2).
 * ``markets`` 는 summary 가 세 표(agent_cache·verdicts·documents)를 시장으로
 * 나눠 한 줄씩 합친 것 — 비용은 없다(llm_usage 엔 종목축이 없어 못 나눈다). */
function renderMarketsTable(markets) {
  const target = document.getElementById("air-markets");
  if (!markets || !markets.length) {
    target.innerHTML = `<div class="empty">이 창엔 시장을 붙일 활동이 없다
      — 호출 · 판정 · 공시가 모두 0건이다.</div>`;
    return;
  }
  // 시장 이름은 창고의 entity_id 접두어 그대로다. "코스피/나스닥" 처럼 고쳐
  // 부르면 창고에 없는 이름이 화면에 생긴다.
  target.innerHTML = `<table>
    <thead><tr><th>시장</th><th class="num">LLM 호출</th><th class="num">판정</th>
      <th class="num">거부</th><th class="num">공시·뉴스</th></tr></thead>
    <tbody>${markets.map((m) => `<tr>
      <td class="mono">${esc(m.market)}</td>
      <td class="num">${num(m.calls)}</td>
      <td class="num">${num(m.verdicts)}</td>
      <td class="num${m.blocked ? "" : " mut"}">${num(m.blocked)}</td>
      <td class="num">${num(m.documents)}</td>
    </tr>`).join("")}</tbody></table>`;
}

async function renderKpis() {
  const body = await fetchJson("ai-review/summary");
  const d = body.data;
  showScope(body);

  document.getElementById("air-kpis").innerHTML = [
    airKpi("LLM 호출", num(d.calls), "재계산 건수 — 캐시 적중은 안 잡힌다"),
    airKpi("에이전트", num(d.agents), "news-screen · macro-brief"),
    airKpi("판정 기록", num(d.verdicts_total), "News · SNS"),
    airKpi("거부", num(d.blocked), "0건이면 거부할 사유가 없었다는 뜻일 수 있다",
           d.blocked ? "" : "mut"),
    airKpi("공시 · 뉴스", num(d.documents), "판정의 입력"),
    airKpi("LLM 비용", usd(d.llm_cost_usd),
           `${krw(d.llm_cost_krw)} 환산 · llm_usage ${num(d.llm_usage_calls)}건`,
           d.llm_cost_usd ? "" : "mut"),
  ].join("");

  document.getElementById("air-cost-badge").innerHTML =
    `이 창 누적 <b>${usd(d.llm_cost_usd)}</b> (${num(d.llm_usage_calls)}건)`;

  // 예산은 **달** 단위, 이 화면의 창(lookback)은 아니다 — 창이 이달 전체를
  // 못 덮으면 이달 누적이 실제보다 적게 나온다(services/ai_review.py
  // month_covered). 그 상태로 "예산 안" 이라 말하면 화면이 안심시킨다.
  // 예산 초과일 때만 색을 쓴다. 예산 안이라고 초록을 칠하면 "이익" 처럼
  // 보인다 — 지출은 손익이 아니다 (dashboard.md §10).
  const budgetOver = d.llm_month_covered && d.llm_month_to_date_usd > d.llm_budget_usd;
  document.getElementById("air-budget-badge").innerHTML = d.llm_month_covered
    ? `이달 <b${budgetOver ? ' class="warn"' : ""}>${usd(d.llm_month_to_date_usd)}</b> / ${usd(d.llm_budget_usd)} 예산`
    : `이달 누적 — 이 창이 이달 전체를 안 덮어 미확인 (예산 ${usd(d.llm_budget_usd)} · llm.monthly_budget_usd)`;

  renderMarketsTable(d.markets);

  // 한 줄 리뷰 자리 — LS_KR 은 Claude 의 headline(주관적 채점)을 건다.
  // 우리에겐 그 채점이 없으니, 이 창에서 실제로 있었던 일을 한 줄로 요약한다.
  const oneliner = d.calls
    ? `이 창(${body.lookback_days}일) · LLM 호출 ${num(d.calls)}건 · News·SNS 거부 ${num(d.blocked)}건 · 비용 ${usd(d.llm_cost_usd)}`
    : `이 창(${body.lookback_days}일)엔 LLM 호출 기록이 없다 — news-screen · macro-brief 가 아직 안 돌았을 수 있다.`;
  document.getElementById("air-oneliner").innerHTML =
    `<span style="font-size:13px;font-weight:600;color:var(--text)">${esc(oneliner)}</span>`;

  // 경고는 여기 한 곳에서만 그린다. LS_KR 은 문제점 카드와 우선순위 액션을
  // 따로 두지만, 그건 Claude 가 problems[]·action_items[] 를 **다르게** 채워
  // 주기 때문이다. 우리 warnings 는 한 벌이라 두 카드에 나눠 담으면 같은
  // 문장이 #alerts 띠까지 세 번 나온다 — 세 번 보인다고 더 급해 보이지 않는다.
  // (경고 문구 자체가 이미 조치를 담고 있다: "…llm.pricing 에 추가할 것")
  showAlerts(d.warnings);
}

async function renderCosts() {
  const { data } = await fetchJson("ai-review/costs");

  const sinceEl = document.getElementById("cost-since");
  sinceEl.textContent = data.total_calls
    ? `— 이 창 안 첫 기록: ${stamp(data.since)}`
    : "— 이 창엔 기록이 없다";

  // 총액·호출 수는 위 KPI 스트립이 이미 말했다. 여기서는 **그 총액이 어떻게
  // 나왔는지**(토큰 네 갈래)와 달 단위 예산만 본다.
  const t = data.tokens;
  document.getElementById("cost-kpis").innerHTML = [
    kpi("input 토큰", num(t ? t.input : null), "정가"),
    kpi("output 토큰", num(t ? t.output : null), "정가"),
    kpi("캐시 쓰기", num(t ? t.cache_write : null), "input의 1.25배 단가"),
    kpi("캐시 읽기", num(t ? t.cache_read : null), "input의 0.1배 단가 — 훨씬 싸다"),
    data.month_covered
      ? kpi("이달 누적", usd(data.month_to_date_usd),
            `월 예산 ${usd(data.budget_usd)} · ${stamp(data.month_start)} 부터`,
            data.month_to_date_usd > data.budget_usd)
      : kpi("이달 누적", "못 잼",
            `창이 이달 1일(${stamp(data.month_start)})에 못 미친다 · 예산 ${usd(data.budget_usd)}`),
    kpi("환율", `₩${num(data.usd_krw_rate)}`, "청구는 달러 — 원화는 참고치다"),
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
      <td>${esc(row.agent)}</td>
      <td class="mono">${esc(row.model)}${row.priced ? "" : ' <span class="tag dim">단가 모름</span>'}</td>
      <td class="num">${num(row.calls)}</td>
      <td class="num">${num(row.items)}</td>
      <td class="num mono">${num(row.tokens.input)}/${num(row.tokens.output)}/${num(row.tokens.cache_write)}/${num(row.tokens.cache_read)}</td>
      <td class="num">${row.priced
        ? `${usd(row.cost_usd)}<div class="kpi-note">${krw(row.cost_krw)}</div>`
        : "—"}</td>
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
      <td>${esc(a.agent)}<div class="kpi-note">${esc(a.version)}</div></td>
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
      <td class="mono">${esc(c.entity_id)}</td>
      <td>${esc(c.agent)}</td>
      <td>${esc(c.summary)}</td>
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
  // 판정·거부 총계는 위 KPI 스트립에 있다. 여기서는 **사후 성적**만 본다 —
  // 차단이 실제로 손실을 피했는지는 이 표에서만 알 수 있다.
  target.innerHTML = `
    <div class="kpis" style="margin-bottom:10px">
      ${kpi("채점 완료", num(card.settled), `진행 중 ${num(card.pending)}건은 제외`)}
      ${kpi("적중률", pct(card.hit_rate, 1), "차단 종목이 시장보다 더 빠진 비율",
            card.hit_rate !== null && card.hit_rate < 0.5)}
      ${kpi("평균 초과수익", pct(card.mean_excess, 2), "음수면 손실을 피한 것",
            card.mean_excess !== null && card.mean_excess > 0)}
    </div>
    ${data.by_analyst.length ? `<table>
      <thead><tr><th>Analyst</th><th class="num">거부 건수</th></tr></thead>
      <tbody>${data.by_analyst.map((row) => `<tr>
        <td>${esc(row.analyst)}</td><td class="num">${num(row.count)}</td>
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
      <td class="mono">${esc(v.entity_id)}</td>
      <td>${esc(v.analyst)}</td>
      <td><span class="tag ${v.decision === "block" ? "observe" : "dim"}">${esc(v.decision)}</span></td>
      <td>${esc(v.reason || v.category || "—")}</td>
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
      <td class="mono">${esc(d.entity_id)}</td>
      <td>${esc(d.doc_type)}</td>
      <td>${esc(d.title)}</td>
      <td class="num">${stamp(d.at)}</td>
    </tr>`).join("")}</tbody></table>`;
}

runAll([renderKpis, renderCosts, renderAgents, renderCalls, renderVerdictScorecard, renderVerdicts, renderDocuments]);
