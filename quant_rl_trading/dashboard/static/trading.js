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

/* -- KPI 스트립 ---------------------------------------------------------- */

function renderKpis(body) {
  const k = body.data.kpis;
  const risk = body.data.risk;
  const cards = [
    kpi("순자산", num(Math.round(k.nav)) + " 원", `세후 ${num(Math.round(k.nav_after_tax))}`),
    kpi("일간손익", pct(k.daily_return), `지수 ${dec(k.index_value, 2)}`),
    kpi("누적수익률", pct(k.cumulative_return), "TWR 기준"),
    kpi("현재낙폭", pct(k.drawdown), risk.band_message, risk.band !== "free"),
    kpi("익스포저", pct(k.exposure), `현금 ${num(Math.round(k.cash_krw))}`),
    kpi(
      "액션 반영률",
      pct(k.action_reflection, 0),
      `하한 ${pct(k.action_reflection_floor, 0)}`,
      k.action_reflection < k.action_reflection_floor
    ),
    kpi("보유 종목", num(k.positions), `KR ${num(Math.round(k.equity_kr))}`),
    kpi("원달러", dec(k.fx_rate, 1), k.cash_usd ? `USD ${num(Math.round(k.cash_usd))}` : "USD 0"),
    kpi(
      "킬스위치",
      risk.killswitch.engaged ? "발동" : "정상",
      risk.killswitch.engaged ? risk.killswitch.reason : `발동선 ${pct(risk.killswitch.drawdown_trigger, 0)}`,
      risk.killswitch.engaged
    ),
    kpi(
      "주문 거부",
      risk.orders_rejected + " / " + risk.orders_total,
      risk.reject_rate === null ? "주문 없음" : `거부율 ${pct(risk.reject_rate, 1)}`,
      risk.reject_rate !== null && risk.reject_rate > risk.killswitch.order_fail_rate
    ),
  ];
  document.getElementById("kpis").innerHTML = cards.join("");
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
  const head = `<tr><th>종목</th><th class="r">현재가</th><th class="r">등락</th>
                <th class="r">점수</th><th class="r">보유</th></tr>`;
  const cells = rows
    .map(
      (row) => `<tr class="click${row.entity_id === selected ? " on" : ""}" data-entity="${row.entity_id}">
        <td><span class="name">${row.name}</span><span class="code">${row.entity_id}</span></td>
        <td class="r mono">${num(row.price)}</td>
        <td class="r mono ${signClass(row.change)}">${pct(row.change)}</td>
        <td class="r mono">${dec(row.score, 3)}</td>
        <td class="r mono">${row.position ? num(row.position) : "—"}</td>
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

  const limits = [
    ["익스포저", r.exposure, 1 - r.cash_buffer],
    ["종목 상한", Math.max(0, ...body.data.positions.map((p) => p.weight || 0)), r.max_position_weight],
    ["주문 거부율", r.reject_rate || 0, r.killswitch.order_fail_rate],
    ["액션 반영률", k.action_reflection, k.action_reflection_floor],
  ];
  const rows = limits
    .map(([label, value, limit]) => {
      const ratio = limit ? Math.min(100, (value / limit) * 100) : 0;
      const hot = value !== null && limit && value > limit;
      return `<div class="limit">
        <span class="limit-label">${label}</span>
        <span class="limit-track"><span class="limit-fill ${hot ? "hot" : ""}" style="width:${ratio}%"></span></span>
        <span class="limit-value mono">${pct(value)} <em>/ ${pct(limit)}</em></span>
      </div>`;
    })
    .join("");

  document.getElementById("risk").innerHTML = gauge + rows;
}

/* -- 포지션·주문 ---------------------------------------------------------- */

function renderPositions(body) {
  const rows = body.data.positions;
  document.getElementById("positions-count").textContent = `${rows.length}종목`;
  if (!rows.length) {
    document.getElementById("positions").innerHTML = `<p class="empty">보유 종목이 없다.</p>`;
    return;
  }
  const head = `<tr><th>종목</th><th class="r">수량</th><th class="r">평균단가</th>
    <th class="r">현재가</th><th class="r">평가금액</th><th class="r">평가손익</th>
    <th class="r">수익률</th><th class="r">비중</th><th class="r">점수</th></tr>`;
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
  const head = `<tr><th>시각</th><th>종목</th><th>방향</th><th class="r">수량</th>
    <th class="r">지정가</th><th class="r">체결가</th><th class="r">체결수량</th>
    <th class="r">비용</th><th class="r">목표비중</th><th class="r">지연</th><th>상태</th></tr>`;
  document.getElementById("orders").innerHTML =
    `<table>${head}${rows
      .map(
        (row) => `<tr class="click" data-entity="${row.entity_id}">
      <td class="mono">${row.time.slice(0, 16).replace("T", " ")}</td>
      <td><span class="name">${row.name}</span></td>
      <td class="${row.side === "buy" ? "up" : "down"}">${row.side.toUpperCase()}</td>
      <td class="r mono">${num(row.quantity)}</td>
      <td class="r mono">${row.limit_price ? num(Math.round(row.limit_price)) : "시장가"}</td>
      <td class="r mono">${row.fill_price ? num(Math.round(row.fill_price)) : "—"}</td>
      <td class="r mono">${row.fill_quantity ? num(row.fill_quantity) : "—"}</td>
      <td class="r mono">${row.cost === null ? "—" : num(Math.round(row.cost))}</td>
      <td class="r mono">${pct(row.target_weight)}</td>
      <td class="r mono">${row.latency_ms === null ? "—" : ms(row.latency_ms)}</td>
      <td><span class="status ${row.status}">${row.status.toUpperCase()}</span></td>
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

async function renderCandles(entityId) {
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

  chart("chart-candle").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["봉", "MA5", "MA20", "MA60"] },
    grid: [
      { left: 52, right: 52, top: 18, height: "62%" },
      { left: 52, right: 52, top: "76%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: c.sessions, ...AXIS, gridIndex: 0 },
      { type: "category", data: c.sessions, gridIndex: 1, axisLabel: { show: false },
        axisLine: AXIS.axisLine, splitLine: { show: false } },
    ],
    yAxis: [
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

/* -- 진입 ----------------------------------------------------------------- */

async function loadTrading() {
  const entity = currentEntity();
  const body = await fetchJson(`trading${entity ? "?entity=" + encodeURIComponent(entity) : ""}`);
  showScope(body);

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

  renderKpis(body);
  renderAlerts(body);
  renderWatchlist(body);
  renderDecision(body);
  renderRisk(body);
  renderPositions(body);
  renderOrders(body);
  renderEquity(body);
  await renderCandles(body.data.decision.entity_id);
}

runAll([loadTrading]);
