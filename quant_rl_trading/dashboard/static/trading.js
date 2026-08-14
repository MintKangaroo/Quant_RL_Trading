/* 트레이딩 탭 — 지금 포트폴리오가 어떤 상태인가.
 *
 * 상태는 URL 에만 둔다 (scope.js 와 같은 규칙). 선택한 종목도 쿼리스트링
 * ?entity= 에 실린다 — 그래야 "그때 그 화면" 링크가 그대로 재현된다.
 *
 * 갱신은 부분 갱신이다. 종목을 바꿀 때 전체를 다시 그리면 스크롤 위치와
 * 차트 줌이 매번 날아간다.
 */

const SIDE_COLOR = { buy: COLOR.up, sell: COLOR.down };

function currentEntity() {
  return new URLSearchParams(window.location.search).get("entity");
}

function selectEntity(entityId) {
  const query = new URLSearchParams(window.location.search);
  query.set("entity", entityId);
  window.location.search = query.toString();
}

/* -- 상태 바 -------------------------------------------------------------- */

function renderStatus(body) {
  const s = body.data.system;
  const target = document.getElementById("statusbar");
  if (!s) return;
  target.hidden = false;
  const engaged = body.data.risk && body.data.risk.killswitch.engaged;
  target.innerHTML = `
    <span class="badge mode-${s.mode.toLowerCase()}" title="${s.store_root}">${s.mode}</span>
    <span class="badge dim">${s.mode_note}</span>
    <span class="badge dim">엔진 ${s.engine}</span>
    <span class="badge dim">브로커 ${s.broker}</span>
    <span class="badge ${engaged ? "stop" : "dim"}">킬스위치 ${engaged ? "발동" : "정상"}</span>
    <span class="badge dim">신호 ${s.last_signal ? s.last_signal.slice(0, 16).replace("T", " ") : "없음"}</span>`;
}

/* EMERGENCY STOP — 화면에서 매매를 멈춘다.
 *
 * 확인 없이 도는 정지 버튼은 오조작으로 하루를 날린다. 그래서 이유를 받는다 —
 * 서버도 이유 없는 발동을 거부하므로, 여기서 막는 것은 왕복 한 번을 아끼는
 * 것뿐이고 규칙 자체는 서버가 지킨다.
 */
function bindEmergencyStop() {
  const button = document.getElementById("emergency-stop");
  if (!button || button.dataset.bound === "true") return;
  // KPI 줄을 다시 그릴 때마다 불린다. 두 번 묶으면 확인 창이 두 번 뜨고,
  // 두 번째 확인이 첫 번째 요청을 되돌리는 것처럼 보인다.
  button.dataset.bound = "true";
  button.addEventListener("click", async () => {
    const engaged = button.dataset.engaged === "true";
    const action = engaged ? "release" : "engage";
    const label = engaged ? "킬스위치를 해제" : "킬스위치를 발동";
    const reason = window.prompt(
      `${label}합니다. 이유를 적으세요.\n\n` +
        (engaged
          ? "해제하면 다음 세션부터 신규매수가 다시 열립니다."
          : "발동하면 신규매수가 차단됩니다. 매도는 막지 않습니다.")
    );
    if (!reason) return;
    button.disabled = true;
    try {
      const response = await fetch("/api/trading/killswitch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, reason, by: "dashboard" }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      window.location.reload();
    } catch (error) {
      document.getElementById("alerts").innerHTML +=
        `<div class="alert critical">킬스위치 조작 실패: ${error.message}</div>`;
      button.disabled = false;
    }
  });
}

/* -- KPI 스트립 ---------------------------------------------------------- */

const tone = (v) => (v === null || v === undefined || v === 0 ? "" : v > 0 ? "up" : "down");

function renderKpis(body) {
  const k = body.data.kpis;
  const risk = body.data.risk;
  const e = body.data.equity;
  const s = body.data.system;
  // 스파크라인은 에쿼티 곡선에서 온다. 없는 계열을 지어내지 않는다 —
  // 카드에 따라 선이 없는 것이 정상이다.
  const navLine = e && e.nav.length > 1 ? e.nav : null;
  const indexLine = e && e.index.length > 1 ? e.index : null;
  const ddLine = e && e.drawdown.length > 1 ? e.drawdown : null;

  const signed = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + num(Math.round(v)));
  const cards = [
    kpi("총자산", num(Math.round(k.nav)), `원금 ${num(Math.round(k.principal || 0))}`, false,
        { unit: "KRW", spark: navLine }),
    // 수익 4종. LS_KR 화면에서 가장 먼저 읽던 자리라 앞으로 당겼다.
    kpi("오늘 수익금", signed(k.today_pnl), `지수 ${dec(k.index_value, 2)}`, false,
        { unit: "KRW", tone: tone(k.today_pnl) }),
    kpi("오늘 수익률", pct(k.daily_return), "TWR 기준", false, { tone: tone(k.daily_return) }),
    kpi("총 수익금", signed(k.total_pnl), "원금 대비", false,
        { unit: "KRW", tone: tone(k.total_pnl) }),
    kpi("총 수익률", pct(k.cumulative_return), "TWR 누적", false,
        { tone: tone(k.cumulative_return), spark: indexLine }),
    kpi("승률", k.win_rate === null ? "—" : pct(k.win_rate, 0),
        // 무엇을 세는지 적는다. 매도 기준 승률과 다른 숫자다.
        k.win_rate === null ? "표본 없음" : `일간 ${k.win_samples}일 중`),
    kpi("MDD", pct(k.mdd), `현재 ${pct(k.drawdown)} · ${risk.band_message}`,
        risk.band !== "free", { spark: ddLine, tone: "down" }),
    kpi("익스포저", pct(k.exposure), `현금 ${num(Math.round(k.cash_krw))}`),
    kpi("액션 반영률", pct(k.action_reflection, 0), `하한 ${pct(k.action_reflection_floor, 0)}`,
        k.action_reflection < k.action_reflection_floor),
    kpi("AI 상태", body.data.decision && body.data.decision.rl_active ? "RL" : "RULE",
        s ? s.engine : "—"),
    kpi("주문 거부", risk.orders_rejected + " / " + risk.orders_total,
        risk.reject_rate === null ? "주문 없음" : `거부율 ${pct(risk.reject_rate, 1)}`,
        risk.reject_rate !== null && risk.reject_rate > risk.killswitch.order_fail_rate),
    emergencyStopCard(risk.killswitch.engaged),
  ];
  document.getElementById("kpis").innerHTML = cards.join("");
  bindEmergencyStop();
}

/* 정지 버튼은 KPI 줄의 마지막 칸이다 — 숫자와 같은 눈높이에 있어야 한다. */
function emergencyStopCard(engaged) {
  return `<div class="kpi kpi-stop${engaged ? " engaged" : ""}">
    <button type="button" id="emergency-stop" data-engaged="${engaged}"
            title="신규매수 차단 — 매도는 막지 않는다">
      ${engaged ? "킬스위치 해제" : "EMERGENCY STOP"}
      <span class="glyph">${engaged ? "⏻" : "✋"}</span>
    </button>
  </div>`;
}

function renderAlerts(body) {
  document.getElementById("alerts").innerHTML = body.data.alerts
    .map((item) => `<div class="alert ${item.level === "info" ? "ok" : item.level}">${item.text}</div>`)
    .join("");
}

/* -- 워치리스트 ---------------------------------------------------------- */

function renderWatchlist(body) {
  const rows = body.data.watchlist;
  const selected = body.data.decision.entity_id;
  document.getElementById("watchlist-count").textContent = `${rows.length}종목`;
  if (!rows.length) {
    document.getElementById("watchlist").innerHTML =
      `<p class="empty">오늘 기록된 신호가 없다. 0 으로 채우지 않는다.</p>`;
    return;
  }
  const head = `<thead><tr><th>종목명</th><th class="r">현재가</th><th class="r">등락률</th>
                <th class="mid">AI 시그널</th><th class="mid">포지션</th>
                <th class="r">PnL</th></tr></thead>`;
  const cells = rows
    .map(
      (row) => `<tr class="click${row.entity_id === selected ? " on" : ""}" data-entity="${row.entity_id}">
        <td><span class="name">${row.name}</span>
            <span class="code">${row.entity_id} · 점수 ${dec(row.score, 3)}</span></td>
        <td class="r mono">${num(row.price)}</td>
        <td class="r mono ${signClass(row.change)}">${arrow(row.change)}${pct(row.change)}</td>
        <td class="mid"><span class="sig ${row.signal.toLowerCase()}">${row.signal}</span></td>
        <td class="mid mono ${row.position ? "up" : ""}">${row.position ? "LONG" : "FLAT"}
            <span class="code">${row.position ? num(row.position) : ""}</span></td>
        <td class="r mono ${signClass(row.pnl)}">${row.pnl === null ? "—" : num(Math.round(row.pnl))}
            <span class="code">${row.pnl_pct === null ? "" : pct(row.pnl_pct)}</span></td>
      </tr>`
    )
    .join("");
  document.getElementById("watchlist").innerHTML = `<table>${head}${cells}</table>`;
  bindRows("watchlist");
}

function bindRows(id) {
  document.getElementById(id).querySelectorAll("tr.click").forEach((row) => {
    row.addEventListener("click", () => selectEntity(row.dataset.entity));
  });
}

const signClass = (v) => (v === null || v === undefined ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
/* 부호를 색만으로 말하지 않는다. 색각 이상에서 초록·빨강은 같은 회색이다. */
const arrow = (v) => (v === null || v === undefined || v === 0 ? "" : v > 0 ? "▲ " : "▼ ");

/* -- 결정 패널 ------------------------------------------------------------ */

function renderDecision(body) {
  const d = body.data.decision;
  document.getElementById("decision-engine").textContent = d.engine;
  document.getElementById("decision-note").textContent = d.engine_note;

  if (!d.entity_id) {
    document.getElementById("decision").innerHTML =
      `<p class="empty">신호가 없어 분해할 결정이 없다.</p>`;
    return;
  }

  const max = Math.max(1e-9, ...d.contributions.map((c) => Math.abs(c.share * c.score)));
  const bars = d.contributions.length
    ? d.contributions
        .map((c) => {
          const value = c.share * c.score;
          const width = Math.min(100, (Math.abs(value) / max) * 100);
          return `<div class="bar-row">
            <span class="bar-label">${c.analyst}</span>
            <span class="bar-track"><span class="bar-fill ${value >= 0 ? "up" : "down"}"
                  style="width:${width}%"></span></span>
            <span class="bar-value mono">${dec(value, 3)}</span>
          </div>`;
        })
        .join("")
    : `<p class="empty">이 종목에 기여한 Analyst 가 없다 (가중치 0 은 빠진다).</p>`;

  const p = d.position;
  const row = (body.data.watchlist || []).find((item) => item.entity_id === d.entity_id);
  const action = row ? row.signal : "HOLD";
  // 신뢰도는 기여 가중 평균이다. Analyst 각자의 confidence 는 롤링 IC 에서
  // 오고(agents.md §1), 합성 점수에 실린 몫만큼만 이 화면의 신뢰도가 된다.
  const confidence = d.contributions.length
    ? d.contributions.reduce((sum, c) => sum + Math.abs(c.share) * c.confidence, 0) /
      Math.max(1e-9, d.contributions.reduce((sum, c) => sum + Math.abs(c.share), 0))
    : null;

  const facts = [
    ["종목", d.entity_id],
    ["합성 점수", dec(d.score, 4)],
    ["목표 비중", pct(d.target_weight)],
    ["실현 비중", pct(d.realized_weight)],
    ["보유 수량", p.quantity ? num(p.quantity) : "—"],
    ["평균 단가", p.avg_price ? num(Math.round(p.avg_price)) : "—"],
    ["현재가", p.price ? num(Math.round(p.price)) : "—"],
    ["평가손익", p.pnl === null ? "—" : num(Math.round(p.pnl))],
  ];

  document.getElementById("decision").innerHTML = `
    <div class="decision-head">
      <div class="decision-action">
        <div class="k">현재 Action</div>
        <div class="v ${action.toLowerCase()}">${action}</div>
      </div>
      ${donut(confidence)}
    </div>
    <h3>Q-Values <span class="sub">행동 확률</span></h3>
    <p class="pending">— 미측정 <span class="why">· Q값은 정책(RL)이 내는 값이다. 지금 비중은
      규칙이 정하므로 잴 대상이 없다 (M4)</span></p>
    <div class="facts">${facts
      .map(([label, value]) => `<div class="fact"><span>${label}</span><b class="mono">${value}</b></div>`)
      .join("")}</div>
    <h3>Analyst 기여도 <span class="sub">score × confidence × weight</span></h3>
    ${bars}
    <p class="note">
      목표와 실현이 벌어지는 것은 라운딩·상한이 한 일이다. 그 차이를 되먹이지
      않으면 Allocator 는 자기가 하지 않은 행동으로 보상받는다 (불변식 7).
    </p>`;
}

/* 신뢰도 고리. 못 잰 값은 고리를 비우고 "—" 를 적는다 — 0% 로 그리면
 * "쟀는데 신뢰가 없다" 로 읽히고, 그건 다른 사실이다. */
function donut(value) {
  const r = 26;
  const circumference = 2 * Math.PI * r;
  const filled = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  return `<div class="donut" title="기여 가중 평균 신뢰도">
    <svg width="62" height="62" viewBox="0 0 62 62">
      <circle class="ring-bg" cx="31" cy="31" r="${r}" fill="none" stroke-width="5"/>
      <circle class="ring" cx="31" cy="31" r="${r}" fill="none" stroke-width="5"
              stroke-dasharray="${(filled * circumference).toFixed(1)} ${circumference.toFixed(1)}"/>
    </svg>
    <span class="mid">${value === null || value === undefined ? "—" : pct(value, 0)}</span>
  </div>`;
}

/* -- 리스크 --------------------------------------------------------------- */

function renderRisk(body) {
  const r = body.data.risk;
  const k = body.data.kpis;
  const bands = r.bands;
  const width = (value) => Math.min(100, (value / bands.hard) * 100);

  const gauge = `
    <div class="gauge">
      <div class="gauge-track">
        <span class="gauge-band free" style="width:${width(bands.free)}%"></span>
        <span class="gauge-band warn" style="width:${width(bands.warn) - width(bands.free)}%"></span>
        <span class="gauge-band hard" style="width:${100 - width(bands.warn)}%"></span>
        <span class="gauge-needle" style="left:${width(r.drawdown)}%"></span>
      </div>
      <div class="gauge-legend">
        <span>${pct(r.drawdown)} / ${pct(bands.hard, 0)}</span>
        <span class="${r.band}">${r.band_message}</span>
      </div>
    </div>`;

  // [이름][값][막대][판정]. 판정은 한도 대비로 정한다 — 화면이 임계치를
  // 다시 정하지 않는다 (임계치는 store.config, 불변식 10).
  const limits = [
    ["금일손익", r.daily_return, null, "sign"],
    ["현재낙폭", r.drawdown, r.bands.hard, "band"],
    ["최대낙폭 밴드", r.bands.warn, r.bands.hard, "band"],
    ["익스포저", r.exposure, 1 - r.cash_buffer, "limit"],
    ["종목 상한", Math.max(0, ...body.data.positions.map((p) => p.weight || 0)), r.max_position_weight, "limit"],
    ["보유 종목", k.positions / 10, 1, "limit"],
    ["주문 거부율", r.reject_rate || 0, r.killswitch.order_fail_rate, "limit"],
    ["액션 반영률", k.action_reflection, k.action_reflection_floor, "floor"],
  ];

  const rows = limits
    .map(([label, value, limit, kind]) => {
      const magnitude = Math.abs(value === null || value === undefined ? 0 : value);
      const ratio = limit ? Math.min(100, (magnitude / Math.abs(limit)) * 100) : 0;
      let level = "info";
      if (kind === "sign") level = value < 0 ? "warning" : "info";
      else if (kind === "floor") level = value < limit ? "critical" : "info";
      else if (magnitude > Math.abs(limit)) level = "critical";
      else if (ratio > 70) level = "warning";
      const fill = level === "critical" ? "hot" : level === "warning" ? "warn" : "";
      const text =
        label === "보유 종목"
          ? `${num(k.positions)} / 10`
          : `${pct(value)}${limit === null ? "" : ` / ${pct(limit, 0)}`}`;
      return `<div class="risk-row">
        <span class="name">${label}</span>
        <span class="val ${kind === "sign" ? signClass(value) : ""}">${text}</span>
        <span class="track"><span class="fill ${fill}" style="width:${ratio}%"></span></span>
        <span class="verdict ${level}">${level.toUpperCase()}</span>
      </div>`;
    })
    .join("");

  const engaged = r.killswitch.engaged;
  const system = `<div class="sys-line">
    <span>전체 시스템 상태</span>
    <span class="verdict ${engaged ? "critical" : "info"}">
      ${engaged ? "킬스위치 발동" : "OPERATIONAL"}</span>
  </div>`;

  document.getElementById("risk").innerHTML = gauge + rows + system;
}

/* -- 포지션·주문 ---------------------------------------------------------- */

function renderPositions(body) {
  const rows = body.data.positions;
  document.getElementById("positions-count").textContent = `${rows.length}종목`;
  if (!rows.length) {
    document.getElementById("positions").innerHTML = `<p class="empty">보유 종목이 없다.</p>`;
    return;
  }
  const head = `<thead><tr><th>종목</th><th class="r">수량</th><th class="r">평균단가</th>
    <th class="r">현재가</th><th class="r">평가금액</th><th class="r">평가손익</th>
    <th class="r">수익률</th><th class="r">비중</th><th class="r">점수</th></tr></thead>`;
  document.getElementById("positions").innerHTML =
    `<table>${head}${rows
      .map(
        (row) => `<tr class="click" data-entity="${row.entity_id}">
      <td><span class="name">${row.name}</span><span class="code">${row.entity_id}</span></td>
      <td class="r mono">${num(row.quantity)}</td>
      <td class="r mono">${num(Math.round(row.avg_price))}</td>
      <td class="r mono">${row.price ? num(Math.round(row.price)) : "—"}</td>
      <td class="r mono">${row.value ? num(Math.round(row.value)) : "—"}</td>
      <td class="r mono ${signClass(row.pnl)}">${row.pnl === null ? "—" : num(Math.round(row.pnl))}</td>
      <td class="r mono ${signClass(row.pnl_pct)}">${pct(row.pnl_pct)}</td>
      <td class="r mono">${pct(row.weight)}</td>
      <td class="r mono">${dec(row.score, 3)}</td>
    </tr>`
      )
      .join("")}</table>`;
  bindRows("positions");
}

function renderOrders(body) {
  const rows = body.data.orders;
  if (!rows.length) {
    document.getElementById("orders").innerHTML =
      `<p class="empty">기록된 주문이 없다. Session 이 돌면 여기 쌓인다.</p>`;
    return;
  }
  const head = `<thead><tr><th>시각</th><th>종목</th><th class="mid">방향</th>
    <th class="r">지정가</th><th class="r">체결가</th><th class="r">수량</th>
    <th class="r">체결수량</th><th class="r">비용</th><th class="r">목표비중</th>
    <th class="r">지연</th><th class="mid">상태</th></tr></thead>`;
  document.getElementById("orders").innerHTML =
    `<table class="ledger">${head}${rows
      .map(
        (row) => `<tr class="click" data-entity="${row.entity_id}">
      <td class="mono">${row.time.slice(0, 16).replace("T", " ")}</td>
      <td><span class="name">${row.name}</span><span class="code">${row.entity_id}</span></td>
      <td class="mid side ${row.side}">${row.side.toUpperCase()}</td>
      <td class="r mono">${row.limit_price ? num(Math.round(row.limit_price)) : "시장가"}</td>
      <td class="r mono">${row.fill_price ? num(Math.round(row.fill_price)) : "—"}</td>
      <td class="r mono">${num(row.quantity)}</td>
      <td class="r mono">${row.fill_quantity ? num(row.fill_quantity) : "—"}</td>
      <td class="r mono">${row.cost === null ? "—" : num(Math.round(row.cost))}</td>
      <td class="r mono">${pct(row.target_weight)}</td>
      <td class="r mono">${row.latency_ms === null ? "—" : ms(row.latency_ms)}</td>
      <td class="mid"><span class="status ${row.status}">${row.status.toUpperCase()}</span></td>
    </tr>`
      )
      .join("")}</table>`;
  bindRows("orders");
}

/* -- 차트 ----------------------------------------------------------------- */

function renderEquity(body) {
  const e = body.data.equity;
  if (!e.sessions.length) {
    document.getElementById("chart-equity").innerHTML =
      `<p class="empty">nav_daily 가 비어 있다. 회계 스냅샷이 아직 없다.</p>`;
    return;
  }
  chart("chart-equity").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["누적지수", "낙폭"] },
    xAxis: { type: "category", data: e.sessions, ...AXIS },
    yAxis: [
      { type: "value", scale: true, ...AXIS },
      { type: "value", max: 0, axisLabel: { ...AXIS.axisLabel, formatter: (v) => (v * 100).toFixed(0) + "%" },
        splitLine: { show: false }, axisLine: AXIS.axisLine },
    ],
    series: [
      { name: "누적지수", type: "line", data: e.index, showSymbol: false,
        lineStyle: { color: COLOR.text, width: 1.6 } },
      { name: "낙폭", type: "line", yAxisIndex: 1, data: e.drawdown, showSymbol: false,
        lineStyle: { color: COLOR.warn, width: 1 }, areaStyle: { color: COLOR.warn, opacity: 0.12 } },
    ],
  });
}

/* 언더워터 — 보상 함수를 그대로 그린 그림이다 (dashboard-kickoff D-3).
   밴드 12/22/30% 는 store.config 에서 온 값이 risk.bands 로 실려 온다.
   여기서 숫자를 적어 두면 설정을 바꿔도 화면만 옛 임계치를 말한다. */
function renderUnderwater(body) {
  const e = body.data.equity;
  const target = document.getElementById("chart-underwater");
  if (!target) return;
  if (!e.sessions.length) {
    target.innerHTML = `<p class="empty">nav_daily 가 비어 있다. 회계 스냅샷이 아직 없다.</p>`;
    return;
  }
  const bands = body.data.risk.bands;

  // 아래로 갈수록 깊다. 축을 데이터에만 맞추면 낙폭이 얕은 날 밴드가
  // 화면 밖으로 나가 "한계가 없는 것처럼" 보인다 — 항상 hard 까지 잡아둔다.
  const deepest = Math.min(
    0,
    ...e.drawdown,
    ...e.benchmark_drawdown.filter((v) => v !== null),
    -bands.hard,
  );
  const band = (from, to, color, label) => [
    { yAxis: -from, itemStyle: { color }, label: { show: true, position: "insideEndTop",
      color: COLOR.dim, fontSize: 9, fontFamily: "IBM Plex Mono", formatter: label } },
    { yAxis: -to },
  ];

  chart("chart-underwater").setOption({
    ...BASE,
    grid: { ...BASE.grid, left: 44, right: 16 },
    legend: { ...BASE.legend, data: ["낙폭", "벤치마크 낙폭"] },
    xAxis: { type: "category", data: e.sessions, ...AXIS },
    yAxis: {
      type: "value", max: 0, min: Math.min(deepest * 1.08, -bands.hard * 1.08),
      axisLabel: { ...AXIS.axisLabel, formatter: (v) => (v * 100).toFixed(0) + "%" },
      splitLine: { show: false }, axisLine: AXIS.axisLine,
    },
    series: [
      {
        name: "낙폭", type: "line", data: e.drawdown, showSymbol: false,
        lineStyle: { color: COLOR.text, width: 1.4 },
        areaStyle: { color: COLOR.accent, opacity: 0.10 },
        markArea: {
          silent: true,
          data: [
            band(0, bands.free, "rgba(34,197,94,0.07)", `자유 ${(bands.free * 100).toFixed(0)}%`),
            band(bands.free, bands.warn, "rgba(245,165,36,0.10)", `페널티 ${(bands.warn * 100).toFixed(0)}%`),
            band(bands.warn, bands.hard, "rgba(239,68,68,0.13)", `급증 ${(bands.hard * 100).toFixed(0)}%`),
          ],
        },
      },
      {
        // 벤치마크는 점선이다. 실선 두 개면 어느 쪽이 우리 것인지 매번 범례를 봐야 한다.
        name: "벤치마크 낙폭", type: "line", data: e.benchmark_drawdown, showSymbol: false,
        connectNulls: false,
        lineStyle: { color: COLOR.bench, width: 1, type: "dashed" },
      },
    ],
  });
}

async function renderCandles(entityId, positions) {
  if (!entityId) return;
  const body = await fetchJson(`trading/chart?entity=${encodeURIComponent(entityId)}`);
  const c = body.data;
  document.getElementById("chart-title").textContent = c.entity_id;
  document.getElementById("chart-sub").textContent = `${c.sessions.length}세션 · 일봉`;
  if (!c.sessions.length) {
    document.getElementById("chart-candle").innerHTML =
      `<p class="empty">이 종목의 시세가 창 안에 없다.</p>`;
    return;
  }
  const marks = (c.trades || []).map((t) => ({
    name: t.side,
    coord: [t.session, t.price],
    symbol: t.side === "buy" ? "triangle" : "pin",
    symbolSize: 10,
    itemStyle: { color: SIDE_COLOR[t.side] },
    label: { show: false },
  }));

  // 평균단가 선. 보유 중일 때만 그린다 — 없는 선을 그리면 "샀다" 로 읽힌다.
  const held = (positions || []).find((row) => row.entity_id === entityId);
  const guides = held
    ? [{ yAxis: held.avg_price, name: "평균단가",
         lineStyle: { color: COLOR.warn, type: "dashed", width: 1 },
         label: { color: COLOR.warn, formatter: "평균단가", position: "insideEndTop" } }]
    : [];

  // 처음에는 최근 120세션만 보여준다. 5년을 한 화면에 밀어 넣으면 봉이
  // 선이 되어 아무것도 안 보인다. 나머지는 스크롤로 간다.
  const span = c.sessions.length;
  const startPct = span > 120 ? Math.max(0, (1 - 120 / span) * 100) : 0;

  chart("chart-candle").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["봉", "MA5", "MA20", "MA60"] },
    // 높이를 비율로 주면 봉 영역이 패널 높이에 따라 접힌다 — 실제로 봉이
    // 세로로 눌려 보였다. 거래량(56px)과 손잡이(24px)는 고정 크기이므로
    // 픽셀로 잡고, 남는 세로는 전부 봉이 가져간다.
    grid: [
      { left: 56, right: 62, top: 26, bottom: 108 },
      { left: 56, right: 62, height: 56, bottom: 46 },
    ],
    // 좌우 스크롤·확대축소. 두 grid 를 함께 움직여야 봉과 거래량이 어긋나지
    // 않는다. inside 는 휠·드래그, slider 는 아래 손잡이다.
    dataZoom: [
      {
        type: "inside", xAxisIndex: [0, 1], start: startPct, end: 100,
        zoomOnMouseWheel: true, moveOnMouseMove: true,
        // filterMode "filter" 라야 보이는 구간만 남아 Y축이 그 구간으로
        // 다시 잡힌다. "none" 이면 5년 최고가에 눌려 최근 봉이 납작해진다.
        filterMode: "filter",
      },
      {
        type: "slider", xAxisIndex: [0, 1], start: startPct, end: 100,
        filterMode: "filter",
        bottom: 6, height: 18,
        backgroundColor: "transparent",
        borderColor: COLOR.border,
        fillerColor: "rgba(62,123,250,0.12)",
        handleStyle: { color: COLOR.muted, borderColor: COLOR.border },
        moveHandleStyle: { color: COLOR.border },
        textStyle: { color: COLOR.dim, fontSize: 10, fontFamily: "IBM Plex Mono" },
        dataBackground: {
          lineStyle: { color: COLOR.dim, opacity: 0.5 },
          areaStyle: { color: COLOR.dim, opacity: 0.15 },
        },
      },
    ],
    xAxis: [
      { type: "category", data: c.sessions, ...AXIS, gridIndex: 0 },
      { type: "category", data: c.sessions, gridIndex: 1, axisLabel: { show: false },
        axisLine: AXIS.axisLine, splitLine: { show: false } },
    ],
    yAxis: [
      // scale + 확대 구간 기준 재계산. 없으면 5년 최고가에 눌려 최근 봉이
      // 납작해진다.
      { type: "value", scale: true, ...AXIS, gridIndex: 0 },
      { type: "value", gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false },
        axisLine: AXIS.axisLine },
    ],
    series: [
      {
        name: "봉", type: "candlestick", data: c.ohlc,
        itemStyle: { color: COLOR.up, color0: COLOR.down,
                     borderColor: COLOR.up, borderColor0: COLOR.down },
        markPoint: { data: marks },
        markLine: { symbol: "none", data: guides, silent: true },
      },
      { name: "MA5", type: "line", data: c.ma.ma5, showSymbol: false,
        lineStyle: { width: 1, color: "#f5a524" } },
      { name: "MA20", type: "line", data: c.ma.ma20, showSymbol: false,
        lineStyle: { width: 1, color: "#3e7bfa" } },
      { name: "MA60", type: "line", data: c.ma.ma60, showSymbol: false,
        lineStyle: { width: 1, color: "#8a93a0" } },
      { name: "거래량", type: "bar", data: c.volume, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: COLOR.border } },
    ],
  });
}

/* -- 수익률 캘린더 --------------------------------------------------------- */

/* 그리는 일은 calendar.js 가 한다 — 별도 창(/calendar)과 **같은 렌더러**다.
 * 두 벌로 만들면 한쪽만 고쳐진 채로 남고, 두 화면이 같은 수익률을 다르게 그린다.
 *
 * 탭에서는 최근 달만, 숫자 없이 색으로만 보여준다. 패널 하나에 여러 달을
 * 숫자까지 넣으면 칸이 읽을 수 없게 작아진다. 전체는 새 창에서 본다.
 */
function renderCalendarPanel(body) {
  const cal = body.data.calendar || { days: [], months: [] };
  const months = calendarMonths(cal);
  renderCalendar(document.getElementById("calendar"), cal, {
    months: months.slice(-1),
    compact: true,
  });
}

/* 새 창은 지금 보고 있는 as_of·창을 그대로 물려받는다. 안 넘기면 달력만
 * 조용히 지금을 보게 되고, 두 화면이 다른 시점을 말한다. */
function bindCalendarPopout() {
  const button = document.getElementById("calendar-popout");
  // 지금은 loadTrading 이 한 번만 돌지만, 나중에 자동 갱신이 붙으면 누를 때마다
  // 창이 여러 개 뜬다. 한 번만 매다는 비용이 그 고장을 찾는 비용보다 싸다.
  if (!button || button.dataset.bound) return;
  button.dataset.bound = "1";
  button.addEventListener("click", () => {
    const query = params().toString();
    window.open(`/calendar${query ? "?" + query : ""}`, "quant-rl-calendar",
                "width=980,height=860,scrollbars=yes");
  });
}

/* -- 진입 ----------------------------------------------------------------- */

async function loadTrading() {
  const entity = currentEntity();
  const body = await fetchJson(`trading${entity ? "?entity=" + encodeURIComponent(entity) : ""}`);
  showScope(body);
  renderStatus(body);

  // 회계가 평가를 거부한 경우(예: 환율 미수집). **가짜 숫자를 그리지 않는다** —
  // 화면이 죽은 것과 데이터가 빠진 것은 다른 사건이고, 그 구분이 복구를 가른다.
  if (body.data.unavailable) {
    renderAlerts(body);
    document.getElementById("kpis").innerHTML =
      kpi("평가 불가", "—", body.data.unavailable, true);
    ["watchlist", "decision", "risk", "positions", "orders"].forEach((id) => {
      document.getElementById(id).innerHTML =
        `<p class="empty">회계가 평가를 거부했다. 위 사유를 먼저 해결한다.</p>`;
    });
    return;
  }

  // "LIVE" 는 as_of 를 안 준 상태다. 타임머신을 켠 채 점이 켜져 있으면
  // 사람이 보고 있는 시점이 조용히 흘러가는 것처럼 읽힌다 (dashboard.md §8-2).
  const dot = document.getElementById("decision-live");
  if (dot) {
    dot.classList.toggle("paused", !body.live);
    dot.textContent = body.live ? "LIVE" : "AS_OF 고정";
  }
  const equitySub = document.getElementById("equity-sub");
  if (equitySub) equitySub.textContent = `${body.data.equity.sessions.length}세션`;

  renderKpis(body);
  renderAlerts(body);
  renderWatchlist(body);
  renderDecision(body);
  renderRisk(body);
  renderPositions(body);
  renderOrders(body);
  renderEquity(body);
  renderUnderwater(body);
  renderCalendarPanel(body);
  bindCalendarPopout();

  // 정지 버튼은 KPI 줄이 그린다 (emergencyStopCard). 여기서 다시 만지지 않는다.
  await renderCandles(body.data.decision.entity_id, body.data.positions);
}

runAll([loadTrading]);
