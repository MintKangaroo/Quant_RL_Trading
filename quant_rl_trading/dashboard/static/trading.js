/* 트레이딩 — **지금은 목업이다.**
 *
 * M2 시점에 accounting·positions·orders 가 없어서 레이아웃만 먼저 세운다.
 * 화면이 스스로 그 사실을 띄운다 — 자동매매 화면에서 가짜 손익이 진짜처럼
 * 보이면 사고가 나기 때문이다.
 *
 * M3 에서 `/api/mock/trading` 을 `/api/trading/*` 로 바꾸면 이 파일의 렌더링
 * 코드는 대부분 그대로 쓴다. 그래서 목업의 필드 이름을 실제와 같게 뒀다.
 */

const money = (v) =>
  v === null || v === undefined ? "—" : "₩" + Math.round(v).toLocaleString("ko-KR");
const signed = (v, digits = 2) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;

/** 손익 방향. 상승=빨강(한국식)으로 통일한다 (dashboard.md §10). */
const tone = (v) => (v > 0 ? "good" : v < 0 ? "down" : "");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function renderKpis(d) {
  const s = d.summary;
  document.getElementById("kpis").innerHTML = [
    kpi("총자산", money(s.nav), `현금 ${money(s.cash)}`),
    kpi("금일 손익", money(s.day_pnl), signed(s.day_pnl_pct), s.day_pnl < 0),
    kpi("누적 수익률", signed(s.total_return_pct), `초과 ${signed(s.excess_pct)}`),
    kpi("현재 낙폭", signed(s.drawdown_pct), `최대 ${signed(s.max_drawdown_pct)}`,
        Math.abs(s.drawdown_pct) > s.mdd_bands[0]),
    kpi("익스포저", `${s.exposure_pct.toFixed(0)}%`, `보유 ${s.positions}종목`),
    // 액션 반영률은 M4(RL) 값이다. 없는 것을 0으로 채우지 않는다 —
    // 0%는 "RL이 전부 덮였다"는 뜻이라 완전히 다른 사실이다.
    kpi("액션 반영률", s.action_rate_pct === null ? "—" : `${s.action_rate_pct}%`,
        "M4 · RL 이후"),
    kpi("AI 상태", esc(s.ai_state), esc(s.ai_note), true),
  ].join("");
}

function renderNav(d) {
  const c = d.curves;
  chart("chart-nav").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["NAV", "벤치마크"] },
    xAxis: { type: "category", data: c.sessions, ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS,
             axisLabel: { ...AXIS.axisLabel, formatter: (v) => (v / 1e6).toFixed(1) + "M" } },
    series: [
      { name: "NAV", type: "line", data: c.nav, showSymbol: false,
        lineStyle: { width: 2, color: COLOR.text } },
      { name: "벤치마크", type: "line", data: c.benchmark, showSymbol: false,
        lineStyle: { width: 1, color: COLOR.bench, type: "dashed" } },
    ],
  });
}

function renderUnderwater(d) {
  const c = d.curves;
  const bands = d.summary.mdd_bands;
  // 밴드를 배경으로 깐다. 낙폭이 어느 구간에 있는지가 숫자보다 먼저 보여야 한다.
  const marks = bands.map((band, i) => [
    { yAxis: -band, itemStyle: { color: [COLOR.warn, "#c2410c", COLOR.up][i] } },
    { yAxis: i + 1 < bands.length ? -bands[i + 1] : -60 },
  ]);
  chart("chart-uw").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["포트폴리오", "벤치마크"] },
    xAxis: { type: "category", data: c.sessions, ...AXIS },
    yAxis: { type: "value", max: 0, ...AXIS,
             axisLabel: { ...AXIS.axisLabel, formatter: (v) => v + "%" } },
    series: [
      {
        name: "포트폴리오", type: "line", data: c.underwater, showSymbol: false,
        areaStyle: { color: "rgba(229,72,77,0.15)" },
        lineStyle: { width: 2, color: COLOR.up },
        markArea: { silent: true, itemStyle: { opacity: 0.07 }, data: marks },
      },
      { name: "벤치마크", type: "line", data: c.benchmark_underwater, showSymbol: false,
        lineStyle: { width: 1, color: COLOR.bench, type: "dashed" } },
    ],
  });
}

function renderMdd(d) {
  const s = d.summary;
  const used = Math.abs(s.drawdown_pct);
  const [free, penalty, hard] = s.mdd_bands;
  const state = used < free
    ? [`자유구간 · 페널티 없음 · 여유 ${(free - used).toFixed(1)}%p`, "tone-flat"]
    : used < penalty
      ? [`페널티 구간 · 한계까지 ${(hard - used).toFixed(1)}%p`, "tone-mix"]
      : [`급증 구간 · 신규매수 제한 · 한계까지 ${(hard - used).toFixed(1)}%p`, "tone-off"];
  document.getElementById("mdd").innerHTML = `
    <div class="mdd-value">${used.toFixed(2)}<span class="sub"> / ${hard}%</span></div>
    <div class="bar" style="height:14px"><span style="width:${Math.min(100, used / hard * 100)}%"></span></div>
    <div class="chip ${state[1]}" style="margin-top:8px">${esc(state[0])}</div>`;
}

function renderAllocation(d) {
  chart("chart-alloc").setOption({
    ...BASE,
    grid: undefined,
    tooltip: { ...BASE.tooltip, trigger: "item",
               formatter: (p) => `${p.name}<br>₩${Math.round(p.value).toLocaleString("ko-KR")} (${p.percent}%)` },
    legend: { ...BASE.legend, orient: "vertical", left: 0, top: "middle" },
    series: [{
      type: "pie", radius: ["48%", "72%"], center: ["66%", "50%"],
      data: d.allocation.map((a) => ({ name: a.label, value: a.value })),
      label: { color: COLOR.muted, fontSize: 11, formatter: "{d}%" },
      itemStyle: { borderColor: COLOR.panel, borderWidth: 2 },
    }],
    color: [COLOR.up, COLOR.down, COLOR.warn, COLOR.bench, COLOR.dim],
  });
}

function renderPositions(d) {
  const rows = d.positions.map((p) => `<tr>
    <td><span class="lead"><strong>${esc(p.name)}</strong>
      <span class="sub trunc">${esc(p.entity_id)}</span></span></td>
    <td class="num">${p.quantity.toLocaleString("ko-KR")}</td>
    <td class="num">${money(p.avg_price)}</td>
    <td class="num">${money(p.last_price)}</td>
    <td class="num">${money(p.value)}</td>
    <td class="num">${p.weight_actual_pct.toFixed(2)}%</td>
    <td class="num ${tone(p.pnl_pct)}">${signed(p.pnl_pct)}</td>
    <td class="num">${p.held_days}일</td>
    <td class="sub">${esc(p.contributors)}</td></tr>`).join("");
  document.getElementById("positions").innerHTML =
    `<table><thead><tr><th>종목</th><th class="num">수량</th><th class="num">평균단가</th>
     <th class="num">현재가</th><th class="num">평가액</th><th class="num">비중</th>
     <th class="num">손익</th><th class="num">보유</th><th>기여 Analyst</th>
     </tr></thead><tbody>${rows}</tbody></table>`;
}

const ORDER_TONE = { FILLED: "tone-flat", PARTIAL: "tone-mix", REJECTED: "tone-off" };

function renderOrders(d) {
  const rows = d.orders.map((o) => `<tr>
    <td class="num">${new Date(o.at).toLocaleTimeString("ko-KR")}</td>
    <td>${esc(o.name)}</td>
    <td class="num ${o.side === "BUY" ? "good" : "down"}">${esc(o.side)}</td>
    <td class="num">${o.quantity}</td>
    <td class="num">${money(o.price)}</td>
    <td><span class="chip ${ORDER_TONE[o.status] || "tone-flat"}">${esc(o.status)}</span></td>
    </tr>`).join("");
  document.getElementById("orders").innerHTML =
    `<table><thead><tr><th class="num">시각</th><th>종목</th><th class="num">방향</th>
     <th class="num">수량</th><th class="num">가격</th><th>상태</th>
     </tr></thead><tbody>${rows}</tbody></table>`;
}

function renderRisk(d) {
  const r = d.risk;
  const line = (label, value, limit, ratio) => `<tr>
    <td>${label}</td><td class="num">${value}</td>
    <td class="num sub">한도 ${limit}</td>
    <td style="width:120px"><div class="bar ${ratio > 0.8 ? "on" : ""}">
      <span style="width:${Math.min(100, ratio * 100).toFixed(0)}%"></span></div></td></tr>`;
  document.getElementById("risk").innerHTML = `<table><tbody>
    ${line("일일 손실", signed(r.daily_loss_pct), signed(r.max_daily_loss_pct),
           Math.abs(r.daily_loss_pct / r.max_daily_loss_pct))}
    ${line("익스포저", r.exposure_pct + "%", r.max_exposure_pct + "%",
           r.exposure_pct / r.max_exposure_pct)}
    ${line("보유 종목", r.open_positions, r.max_positions, r.open_positions / r.max_positions)}
    ${line("주문 거부", r.order_rejects, "—", 0)}
    ${line("API 오류", r.api_errors, "—", 0)}
    </tbody></table>
    <div class="chip tone-off" style="margin-top:10px">
      KILL SWITCH · ${esc(r.kill_switch)} — Executor 는 M3 다</div>`;
}

async function render() {
  const body = await fetchJson("mock/trading");
  const d = body.data;
  showScope(body);

  // 목업 배너를 제일 먼저 띄운다. 나머지가 못 그려져도 이건 보여야 한다.
  document.getElementById("alerts").innerHTML =
    `<div class="alert">⚠️ ${esc(d.banner)}</div>`;

  renderKpis(d);
  renderNav(d);
  renderUnderwater(d);
  renderMdd(d);
  renderAllocation(d);
  renderPositions(d);
  renderOrders(d);
  renderRisk(d);
}

runAll([render]);
