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

/** 총자산 카드의 아랫줄. **장중 값이 있으면 그것이 몇 종목을 덮는지 적는다.**
 *
 * 절반만 실시간인 수치를 "지금 총자산" 이라고 말하면 안 된다 — 장외거나
 * 일부 종목만 응답이 오는 경우가 실제로 있고, 그때 숫자는 종가와 장중이
 * 섞인 값이다. 덮은 종목 수를 같이 보여주면 그 사실이 화면에 남는다. */
function navFoot(k) {
  const base = `원금 ${num(Math.round(k.principal || 0))}`;
  if (k.live_nav === null || k.live_nav === undefined) return base;
  return `${base} · 장중 ${k.live_covered}종목 반영`;
}

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
    kpi("총자산", num(Math.round(k.nav)), navFoot(k), false,
        { unit: "KRW", spark: navLine }),
    ...(k.live_nav === null || k.live_nav === undefined
      ? []
      : [kpi("장중 총자산", num(Math.round(k.live_nav)),
             `종가 대비 ${pct(k.live_change)} · 참고값`, false,
             { unit: "KRW", tone: tone(k.live_change) })]),
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

/** 장중 현재가 한 칸. **참고 값이다 — 평가금액·손익은 종가로 계산된다.**
 *
 * 회계는 한국시간 15:40 하루 한 번으로 못 박혀 있고(accounting.md §2), 거기에
 * 장중 값을 섞으면 벤치마크와 기준 시각이 어긋나 그 차이가 통째로 가짜
 * 초과수익이 된다. 그래서 이 칸은 옆 칸들과 **다른 시각의 숫자**이고, 장외면
 * 종가로 때우지 않고 "—" 로 비운다. */
function liveCell(row) {
  if (row.live_price === null || row.live_price === undefined) {
    return `<td class="r mono soft">—</td>`;
  }
  const cls = signClass(row.live_change);
  return `<td class="r mono ${cls}">${num(Math.round(row.live_price))}` +
    `<span class="hint">${pct(row.live_change)}</span></td>`;
}

function renderPositions(body) {
  const rows = body.data.positions;
  document.getElementById("positions-count").textContent = `${rows.length}종목`;
  if (!rows.length) {
    document.getElementById("positions").innerHTML = `<p class="empty">보유 종목이 없다.</p>`;
    return;
  }
  const head = `<thead><tr><th>종목</th><th class="r">수량</th><th class="r">평균단가</th>
    <th class="r">종가</th><th class="r">장중<span class="hint">참고</span></th>
    <th class="r">평가금액</th><th class="r">평가손익</th>
    <th class="r">수익률</th><th class="r">비중</th><th class="r">점수</th></tr></thead>`;
  document.getElementById("positions").innerHTML =
    `<table>${head}${rows
      .map(
        (row) => `<tr class="click" data-entity="${row.entity_id}">
      <td><span class="name">${row.name}</span><span class="code">${row.entity_id}</span></td>
      <td class="r mono">${num(row.quantity)}</td>
      <td class="r mono">${num(Math.round(row.avg_price))}</td>
      <td class="r mono">${row.price ? num(Math.round(row.price)) : "—"}</td>
      ${liveCell(row)}
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

/** 실현손익 두 칸. **매도에만 값이 있다** — 매수에 0 을 넣으면 "본전" 으로
 * 읽힌다. 통화가 시장마다 다르므로(원·달러) 기호를 값에 붙여 보여준다. */
function pnlCells(row) {
  if (row.realized_pnl === null || row.realized_pnl === undefined) {
    return `<td class="r mono">—</td><td class="r mono">—</td>`;
  }
  const won = row.currency !== "USD";
  const amount = won
    ? `${num(Math.round(row.realized_pnl))}원`
    : `$${row.realized_pnl.toFixed(2)}`;
  const sign = row.realized_pnl >= 0 ? "up" : "down";
  const rate =
    row.realized_rate === null || row.realized_rate === undefined
      ? "—"
      : pct(row.realized_rate);
  return `<td class="r mono ${sign}">${row.realized_pnl >= 0 ? "+" : ""}${amount}</td>
      <td class="r mono ${sign}">${rate}</td>`;
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
    <th class="r">체결수량</th><th class="r">비용</th><th class="r">실현손익</th>
    <th class="r">수익률</th><th class="r">목표비중</th>
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
      ${pnlCells(row)}
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
/* 벤치마크가 무엇이고 왜 비었는지. **배지를 그리고 나서 차트를 그린다** —
   데이터가 없어 차트가 빈 날에도 이 문장은 남아야 한다. 그게 없으면 빈 점선이
   "벤치마크가 안 빠졌다" 로 읽힌다.

   지수 이름·비중은 서버가 config.benchmark 에서 읽어 실어 준다. 여기에 적어
   두면 설정을 바꿔도 화면만 옛 지수를 말한다 (불변식 10). */
function renderBenchmarkBadge(e) {
  const badge = document.getElementById("benchmark-badge");
  const gap = document.getElementById("benchmark-gap");
  const label = e.benchmark_label;
  if (badge && label) {
    const pct = (w) => (w * 100).toFixed(0);
    const mix = `${pct(label.kr_weight)}% ${label.kr_index} + ${pct(label.us_weight)}% ${label.us_index}`;
    badge.textContent = label.price_return_only ? `${mix} · 가격지수` : mix;
    badge.title = label.price_return_only
      ? "총수익지수(TR)가 아니라 가격지수(PR)다. 배당수익률만큼 우리가 유리하게 보인다."
      : "총수익지수(TR)";
  }
  if (gap) {
    // 결측일은 null 로 남긴다. 그 이유를 여기 적지 않으면 끊긴 점선이
    // 데이터 부재인지 벤치마크가 안 움직인 것인지 구별되지 않는다.
    gap.textContent = e.benchmark_note ? ` 끊긴 구간: ${e.benchmark_note}.` : "";
  }
}

function renderUnderwater(body) {
  const e = body.data.equity;
  renderBenchmarkBadge(e);
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
            band(0, bands.free, token("--band-free"), `자유 ${(bands.free * 100).toFixed(0)}%`),
            band(bands.free, bands.warn, token("--band-warn"), `페널티 ${(bands.warn * 100).toFixed(0)}%`),
            band(bands.warn, bands.hard, token("--band-hard"), `급증 ${(bands.hard * 100).toFixed(0)}%`),
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

/* -- 시간대 세그먼트 (1m/5m/15m/1H/4H/1D) ------------------------------------
 *
 * 창고에 그 구간이 있는 종목만 버튼이 켜진다. 종목마다 다르다 — 분봉 수집이
 * 보유·워치리스트만 받으므로(intraday_collector.py), A 종목엔 5분봉이 있어도
 * B 종목엔 없을 수 있다. 그래서 "한 번 켜면 계속 켜짐" 이 아니라, 매 응답의
 * available_intervals 로 **매번 다시** 켜고 끈다.
 */
let chartInterval = "1D";
let chartEntityId = null;
let chartPositions = [];

function timeframeLabel(interval) {
  if (interval === "1D") return "일봉";
  if (interval === "1H") return "1시간봉";
  if (interval === "4H") return "4시간봉";
  return `${interval}봉`; // "1m"·"5m"·"15m"
}

/* 버튼 disabled 상태를 서버가 방금 알려준 사실로 맞춘다. **여기서 추측하지
 * 않는다** — 있다고 짐작하고 켰다가 눌렀을 때 빈 화면이 뜨면, 그게 고장인지
 * 원래 수집 범위 밖인지 사용자가 구분 못 한다. */
function applyAvailableIntervals(available) {
  const seg = document.getElementById("chart-timeframe");
  if (!seg) return;
  const have = new Set(available || []);
  seg.querySelectorAll("button[data-interval]").forEach((button) => {
    const interval = button.dataset.interval;
    if (interval === "1D") return; // 일봉은 항상 있다 — 건드리지 않는다.
    button.disabled = !have.has(interval);
    if (have.has(interval)) button.title = `${timeframeLabel(interval)}으로 보기`;
  });
}

function bindChartTimeframe() {
  const seg = document.getElementById("chart-timeframe");
  // 한 번만 매단다 — 매 renderCandles 호출마다 다시 매달면 클릭 하나에
  // 리스너가 여러 개 붙어 fetch 가 중복으로 나간다.
  if (!seg || seg.dataset.bound) return;
  seg.dataset.bound = "1";
  seg.querySelectorAll("button[data-interval]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.disabled) return; // 꺼진 버튼은 창고에 그 구간이 없다는 뜻.
      chartInterval = button.dataset.interval;
      seg.querySelectorAll("button[data-interval]").forEach((b) =>
        b.classList.toggle("on", b === button)
      );
      renderCandles(chartEntityId, chartPositions);
    });
  });
}

async function renderCandles(entityId, positions) {
  if (!entityId) return;
  chartEntityId = entityId;
  chartPositions = positions;
  bindChartTimeframe();

  const body = await fetchJson(
    `trading/chart?entity=${encodeURIComponent(entityId)}&interval=${encodeURIComponent(chartInterval)}`
  );
  const c = body.data;
  applyAvailableIntervals(c.available_intervals);

  const label = timeframeLabel(chartInterval);
  const intraday = chartInterval !== "1D";
  document.getElementById("chart-title").textContent = c.entity_id;
  document.getElementById("chart-sub").textContent = `${c.sessions.length}봉 · ${label}`;

  const note = document.getElementById("chart-note");
  if (note) {
    // 분봉은 수집 범위가 좁다(보유·워치리스트의 최근 며칠) — 그 사실을
    // 캡션에서도 말해야 "왜 이렇게 짧지" 가 고장으로 안 읽힌다.
    note.innerHTML = intraday
      ? `<strong>${label}이다.</strong> 보유·워치리스트 종목의 최근 며칠만
         받는다 — 전 종목·전 구간이 아니다. ▲▼ 체결 흔적은 일봉에서만 그린다.`
      : `<strong>일봉이다.</strong> 창고에 있는 것이 일봉이고, 없는 봉을 그리면
         화면이 창고보다 많이 아는 것처럼 보인다. ▲▼ 는 우리 체결이다.`;
  }

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
        fillerColor: token("--chart-ma20-fill"),
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
      // 분봉의 session 문자열은 날짜만이 아니라 시각까지 든 ISO 다
      // (candles() 는 "YYYY-MM-DD", intraday_candles() 는 전체 타임스탬프 —
      // dashboard/services/trading.py 참고). 그대로 축에 찍으면 라벨이
      // 너무 길어 서로 겹친다. "HH:MM" 만 자른다 — 어느 날짜인지는 툴팁
      // (원문 그대로)이 말한다.
      {
        type: "category", data: c.sessions, gridIndex: 0,
        ...AXIS,
        axisLabel: intraday
          ? { ...AXIS.axisLabel, formatter: (value) => value.slice(11, 16) }
          : AXIS.axisLabel,
      },
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
        lineStyle: { width: 1, color: COLOR.warn } },
      // MA20 은 토큰에 없는 색이라 trading.css 에 --chart-ma20 로 따로 선언했다
      // (다른 이동평균과 헷갈리지 않게 하려면 warn/up/down 재사용이 아니라
      // 전용 색이 있어야 한다).
      { name: "MA20", type: "line", data: c.ma.ma20, showSymbol: false,
        lineStyle: { width: 1, color: token("--chart-ma20") } },
      { name: "MA60", type: "line", data: c.ma.ma60, showSymbol: false,
        lineStyle: { width: 1, color: COLOR.muted } },
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
/* 모바일에서는 새 창(window.open) 이 대부분 새 탭으로 강제 전환된다 — 크기
 * 지정이 안 먹고, 탭을 나가면 이 화면의 스크롤 위치가 날아간다. 그래서
 * 좁은 화면에서는 같은 /calendar 페이지를 iframe 으로 띄운 전체화면
 * 오버레이로 대신한다. **기능은 그대로다** — 같은 렌더러(calendar.js)를
 * 그대로 쓰는 같은 페이지고, as_of/창도 그대로 물려준다.
 */
function openCalendarOverlay(url) {
  let overlay = document.getElementById("calendar-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "calendar-overlay";
    overlay.className = "calendar-overlay";
    overlay.hidden = true;
    overlay.innerHTML = `
      <div class="calendar-overlay-bar">
        <span>수익률 캘린더</span>
        <button type="button" id="calendar-overlay-close">닫기 ✕</button>
      </div>
      <iframe id="calendar-overlay-frame" title="수익률 캘린더"></iframe>`;
    document.body.appendChild(overlay);
    overlay.querySelector("#calendar-overlay-close").addEventListener("click", () => {
      overlay.hidden = true;
      // src 를 비워 iframe 을 죽인다 — 안 그러면 닫아도 백그라운드에서 계속 로드된 채로 남는다.
      overlay.querySelector("#calendar-overlay-frame").src = "about:blank";
    });
  }
  overlay.querySelector("#calendar-overlay-frame").src = url;
  overlay.hidden = false;
}

function bindCalendarPopout() {
  const button = document.getElementById("calendar-popout");
  // 지금은 loadTrading 이 한 번만 돌지만, 나중에 자동 갱신이 붙으면 누를 때마다
  // 창이 여러 개 뜬다. 한 번만 매다는 비용이 그 고장을 찾는 비용보다 싸다.
  if (!button || button.dataset.bound) return;
  button.dataset.bound = "1";
  button.addEventListener("click", () => {
    const query = params().toString();
    const url = `/calendar${query ? "?" + query : ""}`;
    if (window.matchMedia("(max-width: 768px)").matches) {
      openCalendarOverlay(url);
      return;
    }
    window.open(url, "quant-rl-calendar", "width=980,height=860,scrollbars=yes");
  });
}

/* -- 지수 대비 -------------------------------------------------------------- */

/* 캘린더 오른쪽 공백에 앉는다. **여기서 대용치를 쓰지 않는다** — 코스피가
 * 창고에 없으면 "없다" 고 말한다. KRX 300 을 대신 보여주고 이름만 코스피라고
 * 붙이면 그게 더 큰 거짓말이다 (regime Analyst 는 이유가 있어 대용치를
 * 쓰지만 이 화면은 그 이유를 공유하지 않는다).
 */
/* 그 달의 **누적 초과수익 곡선**. 일별 막대가 아니다.
 *
 * 이 전략은 저베타라(실측 베타 0.131 · 상승일 포착률 14%) 상승장에서 일별
 * 초과가 음수인 날이 줄줄이 나오는 것이 **설계대로 굴러가는 모습**이다.
 * 막대로 그리면 화면이 매일 "졌다" 고 스무 번 외친다 — 2026-07 실측으로
 * 일별 부호가 22일 중 9~13번 뒤집혔다. 그리고 그 빨강은 손실이 아닌데
 * 같은 패널의 캘린더 빨강은 **진짜 손실**이라, 한 패널에서 같은 색이 두
 * 가지를 뜻하게 된다.
 *
 * 누적은 그 달의 결론이고, 곡선이면 **언제부터 벌어졌나**도 보인다.
 *
 * ECharts 를 안 쓰는 이유는 밀도다 — 이 패널은 달력 오른쪽의 좁은 칸이고
 * 네 지수가 세로로 쌓인다. 축과 눈금이 들어갈 자리가 없고, 정확한 값은
 * 오른쪽 숫자와 호버가 든다. */
const BENCH_CURVE_H = 34;

function benchCurve(row) {
  const days = row.daily || [];
  const values = days.map((d) => d.cumulative_excess);
  if (values.length < 2) {
    return `<p class="sub tiny">곡선을 그리려면 이틀은 있어야 한다 (지금 ${num(values.length)}일).</p>`;
  }
  // 0 을 반드시 범위에 넣는다 — 0선이 화면 밖으로 나가면 "앞섰나 뒤졌나" 를
  // 곡선 모양만으로는 못 읽는다.
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1e-9;
  const px = (i) => (i / (values.length - 1)) * 100;
  const py = (v) => BENCH_CURVE_H - 2 - ((v - lo) / span) * (BENCH_CURVE_H - 4);

  const last = values[values.length - 1];
  // **색은 마지막 값 하나로만.** 구간마다 바꾸면 막대와 같은 문제가 돌아온다.
  const tone = last > 0 ? "up" : last < 0 ? "down" : "flat";
  const line = values.map((v, i) => `${px(i)},${py(v)}`).join(" ");
  const zero = py(0);

  // 양쪽이 다 관측되지 않은 날. 그날은 한쪽 누적이 안 움직였다 — 즉 "그날
  // 그쪽 수익률이 0" 이라고 가정한 셈이라, 지우지 않고 점으로 드러낸다.
  const gaps = days
    .map((d, i) => (d.paired ? "" : `<circle cx="${px(i)}" cy="${py(values[i])}" r="1.6" class="gap"/>`))
    .join("");

  // 호버 자리. polyline 에는 점마다 title 을 못 달아서 투명한 칸을 겹친다.
  const width = 100 / values.length;
  const hits = days
    .map(
      (d, i) => `<rect x="${Math.max(0, px(i) - width / 2)}" y="0" width="${width}" height="${BENCH_CURVE_H}"
        fill="transparent"><title>${d.session}
우리 ${d.ours === null || d.ours === undefined ? "—" : pct(d.ours)}
지수 ${d.index === null || d.index === undefined ? "—" : pct(d.index)}
누적 초과 ${pct(d.cumulative_excess)}${d.paired ? "" : "\n(한쪽 데이터 없음)"}</title></rect>`
    )
    .join("");

  return `<svg class="bench-curve ${tone}" viewBox="0 0 100 ${BENCH_CURVE_H}" preserveAspectRatio="none">
    <line class="zero" x1="0" y1="${zero}" x2="100" y2="${zero}"/>
    <polyline points="${line}" fill="none" vector-effect="non-scaling-stroke"/>
    ${gaps}${hits}
  </svg>`;
}

function renderBenchmarkCompare(body) {
  const target = document.getElementById("bench-compare");
  if (!target) return;
  const bc = body.data.benchmark_compare;
  if (!bc || !bc.benchmarks || !bc.benchmarks.length) {
    target.innerHTML = `<p class="empty">비교할 지수가 없다.</p>`;
    return;
  }

  const rows = bc.benchmarks
    .map((b) => {
      if (!b.available) {
        return `<div class="bench-row unavailable">
          <span class="label">${b.label}</span>
          <span>없음 — ${b.reason}</span>
        </div>`;
      }
      const excess = b.cumulative_excess;
      const missing = (b.daily || []).filter((d) => !d.paired).length;
      return `<div class="bench-row">
        <div class="head">
          <span class="label">${b.label} <span class="sub">${bc.month}</span></span>
          <span class="num ${signClass(excess)}">${arrow(excess)}${pct(excess)}</span>
        </div>
        <div class="head">
          <span class="label">우리 ${pct(b.cumulative_ours)} · 지수 ${pct(b.cumulative_index)}</span>
        </div>
        ${benchCurve(b)}
        ${missing
          ? `<p class="sub tiny bench-gap-note">양쪽이 다 관측된 날이 아닌 ${num(missing)}일이 있다 —
             그날은 한쪽이 안 움직인 것으로 잰다(점으로 표시).</p>`
          : ""}
      </div>`;
    })
    .join("");
  target.innerHTML = rows;
}

/* -- Positions 파이 --------------------------------------------------------- */

/* 보유 종목 비중 + 현금. **현금을 빼면 익스포저가 100% 로 보인다** — 위
 * Risk Monitor 의 "익스포저" 행과 이 파이가 같은 사실을 말해야 한다.
 * 종목이 많으면 상위 N + 기타로 접되, 접었다는 사실 자체를 라벨에 남긴다.
 */
const POSITIONS_PIE_TOP_N = 6;

function renderPositionsPie(body) {
  const target = document.getElementById("chart-positions-pie");
  if (!target) return;
  const rows = body.data.positions || [];
  const k = body.data.kpis;
  if (!rows.length && !(k && k.cash_krw)) {
    target.innerHTML = `<p class="empty">보유도 현금도 없다.</p>`;
    return;
  }

  const nav = k.nav;
  const cashValue = (k.cash_krw || 0) + (k.cash_usd || 0) * (k.fx_rate || 0);
  const sorted = [...rows].sort((a, b) => (b.value || 0) - (a.value || 0));
  const top = sorted.slice(0, POSITIONS_PIE_TOP_N);
  const rest = sorted.slice(POSITIONS_PIE_TOP_N);
  const restValue = rest.reduce((sum, row) => sum + (row.value || 0), 0);

  // 색은 이 화면의 규칙을 그대로 쓴다 — 손익 부호(초록↑·빨강↓). 파이는
  // 구성이 아니라 "그 조각이 지금 벌고 있나" 를 같이 말한다. 현금은
  // 손익이 없으니 중립색이다.
  const sliceColor = (row) =>
    row.pnl_pct === null || row.pnl_pct === undefined
      ? COLOR.muted
      : row.pnl_pct > 0
      ? COLOR.up
      : row.pnl_pct < 0
      ? COLOR.down
      : COLOR.muted;

  const data = [
    ...top.map((row) => ({
      name: `${row.name} (${row.entity_id})`,
      value: row.value || 0,
      itemStyle: { color: sliceColor(row) },
    })),
    ...(rest.length
      ? [
          {
            // 접었다는 사실을 라벨에 남긴다 — 숫자만 보면 종목 하나로 보인다.
            name: `기타 ${rest.length}종목`,
            value: restValue,
            itemStyle: { color: COLOR.border },
          },
        ]
      : []),
    { name: "현금", value: cashValue, itemStyle: { color: COLOR.bench } },
  ].filter((slice) => slice.value > 0);

  chart("chart-positions-pie").setOption({
    backgroundColor: "transparent",
    animation: false,
    tooltip: {
      trigger: "item",
      backgroundColor: COLOR.panel,
      borderColor: COLOR.border,
      textStyle: { color: COLOR.text, fontFamily: "IBM Plex Mono", fontSize: 11 },
      formatter: (p) => `${p.name}<br/>${num(Math.round(p.value))} KRW · ${p.percent.toFixed(1)}%`,
    },
    legend: {
      type: "scroll",
      orient: "vertical",
      right: 4,
      top: "middle",
      textStyle: { color: COLOR.muted, fontSize: 10 },
      itemWidth: 10,
      itemHeight: 10,
    },
    series: [
      {
        type: "pie",
        radius: ["38%", "70%"],
        center: ["36%", "50%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: COLOR.panel, borderWidth: 1 },
        label: { show: false },
        labelLine: { show: false },
        data,
      },
    ],
  });
  target.title = nav ? `NAV ${num(Math.round(nav))} 기준` : "";
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
    ["watchlist", "decision", "risk", "positions", "orders", "bench-compare"].forEach((id) => {
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
  renderPositionsPie(body);
  renderOrders(body);
  renderEquity(body);
  renderUnderwater(body);
  renderCalendarPanel(body);
  renderBenchmarkCompare(body);
  bindCalendarPopout();

  // 정지 버튼은 KPI 줄이 그린다 (emergencyStopCard). 여기서 다시 만지지 않는다.
  await renderCandles(body.data.decision.entity_id, body.data.positions);
}

runAll([loadTrading]);
