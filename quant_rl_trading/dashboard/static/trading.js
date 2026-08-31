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


/* "오늘 수익금" 카드의 아랫줄.
 *
 * **큰 숫자는 종가 기준으로 둔다.** 그것이 회계가 확정한 값이고 벤치마크·
 * MDD·TWR 이 전부 같은 시각으로 서 있다. 장중 값으로 덮으면 기준 시각이
 * 어긋나 그 차이가 통째로 가짜 손익이 된다(accounting.md §2 — 스냅샷은
 * 15:40 하루 한 번).
 *
 * 그래서 장중 손익은 **아랫줄에 참고로** 붙인다. 장이 열려 있고 값이
 * 있을 때만 — 장외에는 마지막 체결가가 곧 종가라 0 이 찍히는데, 그 0 은
 * "오늘 안 움직였다" 가 아니라 "아직 안 열렸다" 다.
 */
function todayPnlNote(k, signed, liveOn) {
  // **`signed` 를 인자로 받는다.** 그 포매터는 renderKpis 안의 지역 상수라
  // 최상위 함수에서는 안 보인다 — 그냥 부르면 런타임에 ReferenceError 로
  // 죽고, 그러면 이 아래 KPI 가 통째로 안 그려진다(실제로 그랬다).
  // 여기서 부호 규칙을 다시 만들지 않는 이유는 그것이 두 곳에 생기면
  // 언젠가 한쪽만 고쳐지기 때문이다.
  const base = `지수 ${dec(k.index_value, 2)}`;
  // **장 마감 후에는 회계 확정값을 덧붙이지 않는다.** 그 값(`today_pnl`)은
  // 아직 어제 종가끼리의 차이라 0 이고, 큰 숫자 옆에 "종가 0" 이 붙으면
  // 사람은 큰 숫자를 의심한다 — 사용자가 본 화면이 정확히 그랬다.
  if (k.live_is_close === true) return base;
  // 장중이면 종가 확정값을 참고로 남긴다. 둘 다 볼 수 있어야 어제와 오늘이
  // 갈린다.
  if (liveOn) return `${base} · 종가 ${signed(k.today_pnl)}`;
  return base;
}

/** 총자산 카드의 아랫줄. **장중 값이 있으면 그것이 몇 종목을 덮는지 적는다.**
 *
 * 절반만 실시간인 수치를 "지금 총자산" 이라고 말하면 안 된다 — 장외거나
 * 일부 종목만 응답이 오는 경우가 실제로 있고, 그때 숫자는 종가와 장중이
 * 섞인 값이다. 덮은 종목 수를 같이 보여주면 그 사실이 화면에 남는다. */
function navFoot(k, liveOn, closed) {
  const base = `원금 ${num(Math.round(k.principal || 0))}`;
  if (k.live_nav === null || k.live_nav === undefined) return base;
  // 마감 후에는 그 값이 **오늘 종가**다. "장외 — 마지막 체결가" 는 값의
  // 출처는 맞게 말하지만 그것이 종가라는 사실을 숨겨서, 읽는 사람이 어제
  // 종가와 헷갈린다.
  if (closed) return `${base} · ${k.live_covered}종목 · 오늘 종가`;
  if (!liveOn) return `${base} · 장외 — 마지막 체결가`;
  // **마지막으로 읽어 온 시각을 적는다.** 안 적으면 값이 안 바뀌었을 때
  // "갱신이 멈췄다" 와 "시세가 안 움직였다" 를 구분할 수 없다 — 사람은
  // 앞쪽으로 읽고 고장이라고 판단한다(2026-08-19 실제로 그랬다).
  //
  // 덮은 종목 수도 같이 적는다. 절반만 실시간인 수치를 "지금 총자산" 이라고
  // 말하면 안 된다 — 일부 종목만 응답이 오는 경우가 실제로 있다.
  return `${base} · ${k.live_covered}종목 · ${stampNow()} 기준`;
}

/** 지금 시각 hh:mm:ss. 갱신이 도는지를 사람이 눈으로 확인하는 유일한 수단이다. */
function stampNow() {
  return new Date().toLocaleTimeString("ko-KR", { hour12: false });
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
  // **장중이면 큰 숫자를 장중 값으로 보여준다** (사용자 결정 2026-08-19).
  //
  // 예전에는 종가 카드와 장중 카드를 나란히 뒀는데, 화면에 같은 것이 두 개
  // 있으면 어느 쪽을 봐야 하는지가 매번 질문이 된다. 화면은 **지금**을 보는
  // 곳이니 지금 값을 크게 둔다.
  //
  // **회계 지표는 여전히 종가다** — 아래 MDD·총수익률·승률. 장중 값을 섞으면
  // 하루 안의 출렁임이 낙폭으로 잡혀 킬스위치(30%)가 오발동하고, 벤치마크와
  // 기준 시각이 어긋나 그 차이가 통째로 가짜 초과수익이 된다. 그래서 그
  // 카드들에는 **[종가] 배지**를 붙여 어느 시각 기준인지 화면이 말한다.
  const liveOn = k.live_session_open !== false
    && k.live_nav !== null && k.live_nav !== undefined;
  // **장이 끝나면 마지막 체결가가 오늘 종가다.** 그런데 일봉 수집은 장이
  // 끝난 뒤에 도니까, 그 사이 창고 기준 값(`nav`·`today_pnl`)은 아직 어제를
  // 가리킨다. 그때 실시간 값을 버리면 화면이 "오늘 수익률 0.00%" 를 보여준다
  // — 안 움직인 것이 아니라 아직 모르는 것인데(2026-08-19 실측 -1.13%).
  //
  // 그래서 장중이든 마감 후든 실시간 값이 있으면 쓴다. 다른 것은 **이름**
  // 뿐이다: 장중은 참고값, 마감 후는 종가.
  const closed = k.live_is_close === true;
  const useLive = liveOn || closed;
  const navValue = useLive ? k.live_nav : k.nav;
  const todayPnl = useLive && k.live_today_pnl !== null && k.live_today_pnl !== undefined
    ? k.live_today_pnl : k.today_pnl;
  const todayReturn = useLive && k.live_change !== null && k.live_change !== undefined
    ? k.live_change : k.daily_return;
  const closeBadge = "종가 기준";
  // **총 수익금은 총자산과 같은 NAV 로 센다.** 서버의 total_pnl 은 창고의
  // 마지막 종가 NAV(어제) 기준이라, 총자산이 오늘 실시간이면 두 카드가 서로
  // 다른 날을 가리킨다(2026-08-27 실측: 총자산 506.8M · 총 수익금 +9.9M 은
  // 어제 510.2M 기준). 원금이 있으면 화면의 NAV 로 다시 센다.
  const totalPnl = useLive && k.principal ? navValue - k.principal : k.total_pnl;
  // 원금 대비 단순 수익률. TWR 과 **다른 숫자가 맞다** — 입금 뒤의 수익은
  // 큰 돈에, 입금 전의 손실은 작은 돈에 붙어서 금액은 +, TWR 은 − 일 수 있다.
  const simpleReturn = k.principal && totalPnl !== null && totalPnl !== undefined
    ? totalPnl / k.principal : null;
  // 오늘 수익 두 칸의 부제. 셋을 구분한다 — 장중 / 마감(종가) / 장 열기 전.
  const todayFoot = liveOn ? `${stampNow()} 기준`
    : closed ? "종가 기준 · 일봉 수집 전" : "TWR 기준";

  const cards = [
    kpi("총자산", num(Math.round(navValue)), navFoot(k, liveOn, closed), false,
        { unit: "KRW", spark: navLine, tone: useLive ? tone(k.live_change) : "" }),
    // 수익 4종. LS_KR 화면에서 가장 먼저 읽던 자리라 앞으로 당겼다.
    kpi("오늘 수익금", signed(todayPnl), todayPnlNote(k, signed, useLive), false,
        { unit: "KRW", tone: tone(todayPnl) }),
    kpi("오늘 수익률", pct(todayReturn), todayFoot, false, { tone: tone(todayReturn) }),
    kpi("총 수익금", signed(totalPnl),
        `원금 ${k.principal ? num(Math.round(k.principal)) : "—"} 대비 · ${useLive ? (liveOn ? "장중 참고" : closeBadge) : closeBadge}`
        + (simpleReturn === null ? "" : ` · ${pct(simpleReturn)}`), false,
        { unit: "KRW", tone: tone(totalPnl) }),
    kpi("총 수익률", pct(k.cumulative_return),
        `TWR 누적 · ${closeBadge} · 입금 시점 영향 없음 — 원금 대비 %와 다른 것이 맞다`, false,
        { tone: tone(k.cumulative_return), spark: indexLine }),
    kpi("승률", k.win_rate === null ? "—" : pct(k.win_rate, 0),
        // 무엇을 세는지 적는다. 매도 기준 승률과 다른 숫자다.
        k.win_rate === null ? "표본 없음" : `일간 ${k.win_samples}일 중 · ${closeBadge}`),
    // MDD 는 **장중을 포함해 보여주되**(사용자 결정 2026-08-19), 킬스위치는
    // 여전히 종가로 판정한다. 그 사실을 부제가 말한다 — 안 적으면 화면의
    // 빨간 숫자를 보고 "왜 킬스위치가 안 걸렸지" 를 묻게 된다.
    kpi("MDD", pct(liveOn && k.live_mdd !== null ? k.live_mdd : k.mdd),
        liveOn && k.live_drawdown !== null && k.live_drawdown !== undefined
          ? `현재 ${pct(k.live_drawdown)} · ${risk.band_message} · 킬스위치는 ${closeBadge}`
          : `현재 ${pct(k.drawdown)} · ${risk.band_message} · ${closeBadge}`,
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
  // **열마다 class 를 단다.** 좁은 화면에서 PnL 열을 접고 그 값을 포지션 칸
  // 아래로 내리는데(trading.css), 그러려면 어느 열인지 CSS 가 알아야 한다.
  // 6열은 390px 화면에 안 들어간다 — 넣으면 오른쪽이 잘려 `-6,81` 처럼 보인다.
  // **열 폭을 colgroup 으로 준다.** `table-layout: fixed` 만 켜고 폭을 안 주면
  // 열이 균등 분배되어 종목명 칸이 좁아지고, 그 안의 이름이 옆 칸 숫자와
  // 겹친다(2026-08-19 아이폰 실측).
  const cols = `<colgroup><col class="c-name"><col class="c-price"><col class="c-chg">
                <col class="c-sig"><col class="c-pos"><col class="c-pnl"></colgroup>`;
  const head = `${cols}<thead><tr><th>종목명</th><th class="r">현재가</th><th class="r">등락률</th>
                <th class="mid">AI 시그널</th><th class="mid">포지션</th>
                <th class="r c-pnl">PnL</th></tr></thead>`;
  const cells = rows
    .map(
      (row) => `<tr class="click${row.entity_id === selected ? " on" : ""}" data-entity="${row.entity_id}">
        <td><span class="name">${row.name}</span>
            <span class="code">${row.entity_id} · 점수 ${dec(row.score, 3)}</span></td>
        <td class="r mono">${num(row.price)}</td>
        <td class="r mono ${signClass(row.change)}">${arrow(row.change)}${pct(row.change)}</td>
        <td class="mid"><span class="sig ${row.signal.toLowerCase()}">${row.signal}</span></td>
        <td class="mid mono ${row.position ? "up" : ""}">${row.position ? "LONG" : "FLAT"}
            <span class="code">${row.position ? num(row.position) : ""}</span>
            <span class="code pnl-inline ${signClass(row.pnl)}">${
              row.pnl === null ? "" :
                `${num(Math.round(row.pnl))}${row.pnl_pct === null ? "" : " " + pct(row.pnl_pct)}`
            }</span></td>
        <td class="r mono c-pnl ${signClass(row.pnl)}">${row.pnl === null ? "—" : num(Math.round(row.pnl))}
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
  const head = `<thead><tr><th>종목</th><th class="r">수량</th><th class="r mobile-hide">평균단가</th>
    <th class="r">종가</th><th class="r mobile-hide">장중<span class="hint">참고</span></th>
    <th class="r mobile-hide">평가금액</th><th class="r">평가손익</th>
    <th class="r">수익률</th><th class="r mobile-hide">비중</th><th class="r mobile-hide">점수</th></tr></thead>`;
  document.getElementById("positions").innerHTML =
    `<table>${head}${rows
      .map(
        (row) => `<tr class="click" data-entity="${row.entity_id}">
      <td><span class="name">${row.name}</span><span class="code">${row.entity_id}</span></td>
      <td class="r mono">${num(row.quantity)}</td>
      <td class="r mono mobile-hide">${num(Math.round(row.avg_price))}</td>
      <td class="r mono">${row.price ? num(Math.round(row.price)) : "—"}</td>
      ${liveCell(row).replace("<td ", '<td data-mobile="hide" ').replace('class="', 'class="mobile-hide ')}
      <td class="r mono mobile-hide">${row.value ? num(Math.round(row.value)) : "—"}</td>
      <td class="r mono ${signClass(row.pnl)}">${row.pnl === null ? "—" : num(Math.round(row.pnl))}</td>
      <td class="r mono ${signClass(row.pnl_pct)}">${pct(row.pnl_pct)}</td>
      <td class="r mono mobile-hide">${pct(row.weight)}</td>
      <td class="r mono mobile-hide">${dec(row.score, 3)}</td>
    </tr>`
      )
      .join("")}</table>`;
  bindRows("positions");
}

/** 실현손익 두 칸. **매도에만 값이 있다** — 매수에 0 을 넣으면 "본전" 으로
 * 읽힌다. 통화가 시장마다 다르므로(원·달러) 기호를 값에 붙여 보여준다. */
function pnlCells(row) {
  if (row.realized_pnl === null || row.realized_pnl === undefined) {
    // **왜 비었는지를 말한다.** 빈칸 하나로 세 가지가 같아 보인다:
    //   매수라 손익이 없다 · 매도인데 아직 체결이 안 됐다 · 계산이 실패했다
    // 가운데가 특히 헷갈린다 — shadow 는 D+1 체결이라 오늘 낸 매도가 내일
    // 세션에서 채워진다. 실측 2026-08-19: 매도 주문 14건에 매도 체결 0건이라
    // 화면이 통째로 빈칸이었고, "매도했는데 왜 수익률이 없냐" 는 물음이 나왔다.
    if (String(row.side).toLowerCase() === "sell") {
      const why = row.fill_quantity ? "손익 계산 불가" : "체결 대기";
      return `<td class="r sub" colspan="2" title="shadow 는 D+1 체결이다 — 다음 세션에서 채워진다">${why}</td>`;
    }
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
    <th class="r mobile-hide">지정가</th><th class="r">체결가</th><th class="r">수량</th>
    <th class="r mobile-hide">체결수량</th><th class="r mobile-hide">비용</th><th class="r">실현손익</th>
    <th class="r">수익률</th><th class="r mobile-hide">목표비중</th>
    <th class="r mobile-hide">지연</th><th class="mid">상태</th></tr></thead>`;
  document.getElementById("orders").innerHTML =
    `<table class="ledger">${head}${rows
      .map(
        (row) => `<tr class="click" data-entity="${row.entity_id}">
      <td class="mono">${row.time.slice(0, 16).replace("T", " ")}</td>
      <td><span class="name">${row.name}</span><span class="code">${row.entity_id}</span></td>
      <td class="mid side ${row.side}">${row.side.toUpperCase()}</td>
      <td class="r mono mobile-hide">${row.limit_price ? num(Math.round(row.limit_price)) : "시장가"}</td>
      <td class="r mono">${row.fill_price ? num(Math.round(row.fill_price)) : "—"}</td>
      <td class="r mono">${num(row.quantity)}</td>
      <td class="r mono mobile-hide">${row.fill_quantity ? num(row.fill_quantity) : "—"}</td>
      <td class="r mono mobile-hide">${row.cost === null ? "—" : num(Math.round(row.cost))}</td>
      ${pnlCells(row)}
      <td class="r mono mobile-hide">${pct(row.target_weight)}</td>
      <td class="r mono mobile-hide">${row.latency_ms === null ? "—" : ms(row.latency_ms)}</td>
      <td class="mid"><span class="status ${row.status}">${row.status.toUpperCase()}</span></td>
    </tr>`
      )
      .join("")}</table>`;
  bindRows("orders");
}

/* -- 오늘의 성과 --------------------------------------------------------- */

/** 부호를 붙인 원화. 값이 없으면 "—" 다 — 0 으로 채우면 "본전" 으로 읽힌다. */
/** 증권사 계좌(t0424) 대 우리 장부 — **나란히 놓고 차이를 보인다.** 조회가 안 되면
 *  패널을 숨긴다(0 을 지어내지 않는다). 합계 다섯 줄 + 종목별 수량 대조. */
async function renderAccount(tradingBody) {
  const panel = document.getElementById("account-panel");
  const target = document.getElementById("account-kpis");
  if (!panel || !target) return;
  const body = await fetchJson("trading/account");
  const a = (body && body.data) || {};
  if (!a.available) { panel.style.display = "none"; return; }
  const d = tradingBody.data || {};
  const k = d.kpis || {};
  const muted = "color:var(--muted)";
  const won = (v) => (v == null ? "—" : num(Math.round(v)));
  const sgn = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + num(Math.round(v)));
  const diffCell = (v, base) => {
    if (v == null) return "<td class=\"r\">—</td>";
    const big = Math.abs(v) > 0.005 * Math.max(1, Math.abs(base || 1));
    return `<td class="r ${big ? "down" : ""}" style="${big ? "" : muted}">${sgn(v)}</td>`;
  };
  const ledgerNav = k.live_nav ?? k.nav;
  const ledgerEquity = k.live_equity ?? k.equity;
  // 장부 평가손익도 계좌와 **같은 정의**(현재가 − 평균 매입가)로 센다. positions.pnl 은
  // 전일 종가 대비 당일 손익이라 다른 숫자다 — 그걸 나란히 두면 차이가 가짜로 커진다.
  const ledgerUnreal = (d.positions || []).reduce(
    (s, p) => s + ((p.live_price ?? p.price) - (p.avg_price || 0)) * (p.quantity || 0), 0);
  const settled = a.net_asset != null && a.equity != null ? a.net_asset - a.equity : null;
  const perf = d.performance || {};
  const ledgerRealized = perf.realized_pnl ?? 0;
  const rows = [
    ["총자산", a.net_asset, ledgerNav, true],
    ["현금(정산 후)", settled, k.cash_krw, true],
    ["주식 평가금액", a.equity, ledgerEquity, true],
    ["평가손익", a.unrealized, ledgerUnreal, true],
    // 당일 실현손익은 차이를 경고로 안 칠한다 — 계좌 쪽엔 장부 밖 청산(선행 잔고)이 들어간다.
    ["당일 실현손익", a.realized_today, ledgerRealized, false],
  ];
  let html = `<table><thead><tr><th>항목</th><th class="r">증권사 계좌</th><th class="r">우리 장부</th><th class="r">차이</th></tr></thead><tbody>`;
  for (const [label, acct, ledger, judge] of rows) {
    const delta = acct == null || ledger == null ? null : acct - ledger;
    const cell = judge ? diffCell(delta, a.net_asset)
      : `<td class="r mono" style="${muted}">${delta == null ? "—" : sgn(delta)}</td>`;
    html += `<tr><td style="white-space:nowrap">${label}</td>
      <td class="r mono ${label === "당일 실현손익" || label === "평가손익" ? tone(acct) : ""}">${label.includes("손익") ? sgn(acct) : won(acct)}</td>
      <td class="r mono ${label === "당일 실현손익" || label === "평가손익" ? tone(ledger) : ""}">${label.includes("손익") ? sgn(ledger) : won(ledger)}</td>${cell}</tr>`;
  }
  const cntOk = a.positions === k.positions;
  html += `<tr><td>보유 종목 수</td><td class="r">${a.positions ?? "—"}</td><td class="r">${k.positions ?? "—"}</td>
    <td class="r ${cntOk ? "" : "down"}" style="${cntOk ? muted : ""}">${a.positions == null || k.positions == null ? "—" : a.positions - k.positions}</td></tr></tbody></table>`;

  // 종목별 수량 대조 — 합집합. 한쪽에만 있으면 그게 곧 어긋난 곳이다.
  const acctBy = new Map((a.holdings || []).map((h) => [h.entity_id, h]));
  const ledgerBy = new Map((d.positions || []).map((p) => [p.entity_id, p]));
  const keys = [...new Set([...acctBy.keys(), ...ledgerBy.keys()])].sort();
  let mismatch = 0;
  let tbl = `<table style="margin-top:10px"><thead><tr><th>종목</th><th class="r">계좌 수량</th><th class="r">장부 수량</th><th class="r mobile-hide">계좌 평가손익</th><th></th></tr></thead><tbody>`;
  for (const key of keys) {
    const h = acctBy.get(key); const p = ledgerBy.get(key);
    const qa = h ? h.quantity : null; const ql = p ? p.quantity : null;
    const ok = qa != null && ql != null && Math.round(qa) === Math.round(ql);
    if (!ok) mismatch += 1;
    tbl += `<tr><td>${(h && h.name) || (p && p.name) || key} <span style="font-size:11px;${muted}">${key}</span></td>
      <td class="r">${qa == null ? "—" : num(qa)}</td><td class="r">${ql == null ? "—" : num(ql)}</td>
      <td class="r mobile-hide ${h ? tone(h.unrealized) : ""}">${h ? sgn(h.unrealized) : "—"}</td>
      <td class="${ok ? "up" : "down"}">${ok ? "✓" : "✗ 불일치"}</td></tr>`;
  }
  tbl += "</tbody></table>";
  const verdict = mismatch === 0
    ? `<p class="up" style="margin:10px 0 4px;font-weight:600">종목·수량 ${keys.length}건 전부 일치 ✓</p>`
    : `<p class="down" style="margin:10px 0 4px;font-weight:600">종목·수량 불일치 ${mismatch}건 — 대사(reconcile) 로그를 볼 것</p>`;
  const legend = `<p class="note" style="margin-top:6px">
    총자산 = 계좌 추정순자산 vs 장부 NAV · 현금(정산 후) = 계좌 순자산 − 평가금액 (당일 예수금 ${won(a.cash)}, 차이는 미결제 매도대금) ·
    평가손익 = 현재가 − 평균 매입가 · 당일 실현손익의 계좌 쪽엔 장부 밖 청산(선행 잔고 6종목, 8/28)이 들어 있어 차이를 경고로 치지 않는다 ·
    계좌 값은 t0424(정규장 종가 기준)라 LS 앱의 시간외 현재가 기준 숫자와 조금 다르다 · 차이가 0.5% 를 넘으면 빨갛게 표시.</p>`;
  target.innerHTML = html + legend + verdict + tbl;
  const sub = document.getElementById("account-sub");
  if (sub) sub.textContent = `t0424 조회 · 모드 ${a.mode} · ${stampNow()} 기준`;
  panel.style.display = "";
}

function wonSigned(v) {
  if (v === null || v === undefined) return "—";
  return (v > 0 ? "+" : "") + num(Math.round(v)) + "원";
}

/** 성과 한 칸. `tone` 이 빈 문자열이면 색을 안 칠한다 —
 *  **방향이 없는 값에 색을 칠하면 있는 것처럼 보인다.** */
function perfCell(label, value, note, toneClass) {
  return `<div class="perf-cell">
    <div class="perf-label">${label}</div>
    <div class="perf-value ${toneClass || ""}">${value}</div>
    <div class="perf-note">${note || ""}</div>
  </div>`;
}

function renderPerformance(body) {
  const p = body.data.performance;
  const summary = document.getElementById("perf-summary");
  const fills = document.getElementById("perf-fills");
  const stamp = document.getElementById("perf-stamp");
  if (!summary) return;

  // 회계 스냅샷이 없으면 **숫자를 지어내지 않는다.** 0 으로 채운 표는
  // "손실 0" 으로 읽힌다.
  if (!p || !p.session) {
    stamp.textContent = "";
    summary.innerHTML = "";
    fills.innerHTML = `<p class="empty">${(p && p.note) || "회계 스냅샷이 아직 없다 — 성과를 잴 수 없다."}</p>`;
    return;
  }

  // 어느 창고의 숫자인지 항상 적는다. 모의 운용 성과를 실전으로 읽는 것이
  // 이 패널에서 가능한 가장 비싼 오해다.
  // **"종가" 라고 안 적는다.** 화면은 요청 시각에 장부를 다시 접으므로,
  // 06:00 에 열면 이 값은 오늘 종가가 아니라 지금까지 알 수 있는 전부다.
  // 메일 쪽은 창고의 확정 스냅샷만 읽으므로 거기서만 "종가" 라고 적는다.
  stamp.textContent = `${p.session} 기준 · ${p.mode}`;

  const flowNote = p.inflow
    ? `그중 입출금 ${wonSigned(p.inflow)}`
    : "입출금 없음";
  const changeNote = p.previous_nav === null
    ? p.note || "비교할 어제가 없다"
    : `${num(Math.round(p.previous_nav))} → ${num(Math.round(p.nav))} · ${flowNote}`;

  summary.innerHTML = [
    perfCell("자산 증감", wonSigned(p.nav_change), changeNote, tone(p.nav_change)),
    perfCell("당일 실현손익", wonSigned(p.realized_pnl ?? 0), `매도 ${p.sell_count ?? 0}건의 (매도가 − 평균매입가)`, tone(p.realized_pnl)),
  ].join("");

  if (!p.fill_count) {
    // **"매매 0건" 이 아니라 "매매가 없었다" 다.** 앞은 수치이고 뒤는 사실이다.
    fills.innerHTML = `<p class="empty">${p.session} 에 체결된 매매가 없다.</p>`;
    return;
  }

  // **체결 조각은 안 보인다** (사용자 요청 2026-08-28 — 종목당 슬라이스 4개가 줄줄이
  // 나와 읽을 수 없었다). 종목별로 접어 한 줄씩, 방향·수량·평균가·금액만 적는다.
  // 조각 단위가 필요하면 아래 "주문" 표가 그 자리다.
  const byStock = new Map();
  for (const f of p.fills) {
    const key = `${f.entity_id}|${f.side}`;
    const cur = byStock.get(key) || { name: f.name, entity_id: f.entity_id, side: f.side,
      currency: f.currency, quantity: 0, amount: 0, realized: 0, hasRealized: false };
    cur.quantity += f.quantity; cur.amount += f.amount;
    if (f.realized_pnl !== null && f.realized_pnl !== undefined) { cur.realized += f.realized_pnl; cur.hasRealized = true; }
    byStock.set(key, cur);
  }
  const groups = [...byStock.values()].sort((a, b) => b.amount - a.amount);
  const head = `<thead><tr><th>종목</th><th class="mid">방향</th>
    <th class="r">수량</th><th class="r mobile-hide">평균가</th><th class="r">금액</th><th class="r">실현손익</th></tr></thead>`;
  const rows = groups.map((g) => {
    const won = g.currency !== "USD";
    const money = (v) => (won ? num(Math.round(v)) : "$" + Number(v).toFixed(2));
    const realized = g.hasRealized
      ? `<td class="r mono ${g.realized >= 0 ? "up" : "down"}">${g.realized >= 0 ? "+" : ""}${money(g.realized)}</td>`
      : `<td class="r sub">매수 — 아직 실현 없음</td>`;
    return `<tr class="click" data-entity="${g.entity_id}">
      <td><span class="name">${g.name}</span><span class="code">${g.entity_id}</span></td>
      <td class="mid side ${g.side}">${g.side.toUpperCase()}</td>
      <td class="r mono">${num(g.quantity)}</td>
      <td class="r mono mobile-hide">${money(g.quantity ? g.amount / g.quantity : 0)}</td>
      <td class="r mono">${money(g.amount)}</td>
      ${realized}
    </tr>`;
  }).join("");
  // 보유 표(POSITIONS)와 같은 종목이 그대로 겹친다(첫날엔 완전히 같다) — 요약 한 줄만
  // 펼쳐 두고 종목별 체결은 접는다 (사용자 요청 2026-08-28).
  const buyAmt = groups.filter((g) => g.side === "buy").reduce((s, g) => s + g.amount, 0);
  const sellAmt = groups.filter((g) => g.side === "sell").reduce((s, g) => s + g.amount, 0);
  const won = (v) => num(Math.round(v));
  const line = `오늘 체결 ${p.fill_count}건 — 매수 ${p.buy_count}건 ${won(buyAmt)}원 · 매도 ${p.sell_count}건 ${won(sellAmt)}원`
    + (p.fills_omitted ? ` (큰 것부터 ${p.fills.length}건만, 외 ${p.fills_omitted}건 생략)` : "");
  fills.innerHTML = `<details class="fold"><summary>${line} · 종목별 ${groups.length}줄 보기</summary>
    <table class="ledger">${head}${rows}</table></details>`;
  bindRows("perf-fills");
}

/* -- 차트 ----------------------------------------------------------------- */

function renderEquity(body) {
  const e = body.data.equity;
  const target = document.getElementById("chart-equity");
  if (!target) return;
  if (!e.sessions.length) {
    target.innerHTML = `<p class="empty">nav_daily 가 비어 있다. 회계 스냅샷이 아직 없다.</p>`;
    return;
  }
  // **총자산(원) 하나만, 한 축으로.** 누적지수+낙폭 두 축은 읽기 어렵다는 지적(2026-08-28).
  // 낙폭은 아래 언더워터 차트가 따로 그린다. 날짜는 M/D, 점을 찍어 하루가 보이게.
  const nav = e.nav || [];
  const days = e.sessions.map((s) => `${Number(String(s).slice(5, 7))}/${Number(String(s).slice(8, 10))}`);
  const eok = (v) => (v == null ? "—" : v >= 1e8 ? (v / 1e8).toFixed(2) + "억" : num(Math.round(v / 1e4)) + "만");
  const first = nav.find((v) => v != null);
  const bench = (e.benchmark || []).map((b) => (b == null || first == null ? null : first * b / (e.benchmark.find((x) => x != null) || 1)));
  chart("chart-equity").setOption({
    ...BASE,
    legend: { ...BASE.legend, data: ["총자산", "벤치마크(같은 출발점)"] },
    tooltip: { trigger: "axis", formatter: (items) => {
      const i = items[0].dataIndex;
      const prev = i > 0 ? nav[i - 1] : null;
      const d = prev != null && nav[i] != null ? nav[i] - prev : null;
      return `${e.sessions[i]}<br>총자산 <b>${num(Math.round(nav[i]))}원</b>`
        + (d != null ? `<br>전일 대비 ${d >= 0 ? "+" : ""}${num(Math.round(d))}원` : "")
        + (bench[i] != null ? `<br>벤치마크 ${num(Math.round(bench[i]))}원` : "");
    } },
    grid: { left: 56, right: 14, top: 28, bottom: 28 },
    xAxis: { type: "category", data: days, ...AXIS },
    yAxis: { type: "value", scale: true, ...AXIS,
      axisLabel: { ...AXIS.axisLabel, formatter: (v) => eok(v) } },
    series: [
      { name: "총자산", type: "line", data: nav, showSymbol: true, symbolSize: 6, smooth: false,
        lineStyle: { color: COLOR.up, width: 2.2 }, itemStyle: { color: COLOR.up },
        areaStyle: { color: COLOR.up, opacity: 0.08 } },
      { name: "벤치마크(같은 출발점)", type: "line", data: bench, showSymbol: false,
        lineStyle: { color: COLOR.muted, width: 1.2, type: "dashed" }, connectNulls: false },
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

/* 이름·버튼 상태·봉 그리기는 **candles.js** 가 한다 — 마켓 탭 패널과 같은
 * 코드다. 여기서 두 번째 벌을 두면 한쪽만 고쳐진 채로 남는다.
 *
 * 일봉은 창고에 항상 있으므로 available 에 없어도 켜 둔다. 서버는
 * 분봉 목록만 실어 보낸다(``/api/trading/chart`` 의 available_intervals). */
function applyAvailableIntervals(available) {
  syncTimeframeSeg(
    document.getElementById("chart-timeframe"),
    [...(available || []), "1D"],
    chartInterval
  );
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

  // 그리는 일은 **candles.js** 가 한다 — 마켓 탭 패널과 같은 함수다.
  // 여기 남는 것은 이 화면에만 있는 것들뿐이다: 체결 흔적과 평균단가 선.
  chart("chart-candle").setOption(
    candleOption(c, { interval: chartInterval, marks, guides })
  );
}

/* -- 수익률 캘린더 --------------------------------------------------------- */

/* 그리는 일은 calendar.js 가 한다 — 별도 창(/calendar)과 **같은 렌더러**다.
 * 두 벌로 만들면 한쪽만 고쳐진 채로 남고, 두 화면이 같은 수익률을 다르게 그린다.
 *
 * 예전에는 여기서 최근 한 달만 숫자 없이 색으로 보여주고, [표시하기] 버튼이
 * 새 창을 띄웠다. **버튼을 눌러서 보던 화면이 원래 보고 싶던 화면이었으므로**
 * 한 번 더 누르게 하지 않고 여기서 바로 그린다 — ‹ › 로 달을 옮기고 아래
 * 월별 표에서 [보기] 로 건너뛴다.
 */
function renderCalendarPanel(body) {
  const cal = body.data.calendar || { days: [], months: [] };
  mountCalendarBrowser(cal, {
    label: document.getElementById("cal-label"),
    grid: document.getElementById("calendar"),
    months: document.getElementById("cal-months"),
    prev: document.getElementById("cal-prev"),
    next: document.getElementById("cal-next"),
  });
  bindDayDetail(
    document.getElementById("calendar"), cal, document.getElementById("day-detail")
  );
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

async function loadReview() {
  const panel = document.getElementById("panel-review");
  if (!panel) return;
  const body = await fetchJson("trading/review");
  const review = body.data && body.data.review;
  if (!review || !review.headline) {
    panel.hidden = true;
    return;
  }
  const tone = { good: "up", bad: "down", mixed: "", quiet: "dim" }[review.tone] || "";
  document.getElementById("review-stamp").textContent =
    `${new Date(review.session_at).toLocaleDateString("ko-KR")} 장 · ${review.model} · ${review.status === "cached" ? "캐시" : review.status}`;
  const headline = document.getElementById("review-headline");
  headline.textContent = review.headline;
  headline.className = `review-headline ${tone}`;
  document.getElementById("review-body").textContent = review.body || "";
  panel.hidden = false;
}

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
    ["watchlist", "decision", "risk", "positions", "orders", "perf-fills"].forEach((id) => {
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
  renderAccount(body).catch(() => {});  // 외부 조회 — 느리고 실패할 수 있다, KPI 를 막지 않는다
  renderAlerts(body);
  renderWatchlist(body);
  renderDecision(body);
  renderRisk(body);
  renderPositions(body);
  renderPositionsPie(body);
  renderOrders(body);
  renderPerformance(body);
  renderEquity(body);
  // renderUnderwater 는 뺐다 (사용자 요청 2026-08-31 — 낙폭이 0 이라 안 보였다).
  // 함수는 /calendar 재사용 위해 남겨 둔다.
  renderCalendarPanel(body);

  // 정지 버튼은 KPI 줄이 그린다 (emergencyStopCard). 여기서 다시 만지지 않는다.
  await renderCandles(body.data.decision.entity_id, body.data.positions);

  // 장중이면 다음 갱신을 예약한다. **그리기가 끝난 뒤**여야 한다 — 앞에 두면
  // 느린 세션에서 갱신이 겹쳐 쌓인다.
  scheduleLiveRefresh(body);
}

/* -- 장중 자동 갱신 ---------------------------------------------------------- */

/* **장이 열려 있을 때만** 주기적으로 다시 읽는다.
 *
 * 안 하면 실시간 시세를 붙여 놓고도 화면이 처음 연 순간에 얼어붙는다 —
 * 값은 맞는데 안 움직이니 고장으로 읽힌다(2026-08-19 09:09 실측).
 *
 * 세 가지를 지킨다:
 *   - **타임머신을 켰으면 갱신하지 않는다.** 되감은 화면이 조용히 흘러가면
 *     사람이 보고 있는 시점이 바뀌어 버린다 (dashboard.md §8-2)
 *   - **장외에는 돌지 않는다.** 마지막 체결가는 안 변하는데 매번 창고를 연다
 *   - **탭이 숨겨져 있으면 쉰다.** 배경 탭 수십 개가 30초마다 깨우면
 *     이 기계에서 그 비용이 실제로 아프다
 */
const LIVE_REFRESH_MS = 30000;
let liveTimer = null;
//: 마지막으로 실제로 읽어 온 시각. 화면이 돌아왔을 때 "얼마나 굶었나" 를
//: 재는 데 쓴다. 타이머만 믿으면 안 되는 이유는 아래에 있다.
let lastLoadedAt = 0;
let visibilityBound = false;

function liveEnabled(body) {
  if (!body || !body.live) return false;           // 타임머신은 흐르지 않는다
  const k = (body.data && body.data.kpis) || {};
  return k.live_session_open !== false;            // 장외에는 안 돈다
}

function scheduleLiveRefresh(body) {
  lastLoadedAt = Date.now();
  if (liveTimer) {
    clearTimeout(liveTimer);
    liveTimer = null;
  }
  bindVisibilityRefresh(body);
  if (!liveEnabled(body)) return;

  liveTimer = setTimeout(async () => {
    if (document.hidden) return;   // 숨은 동안은 쉰다. 복귀는 아래가 맡는다
    try {
      await loadTrading();
    } catch (error) {
      // 한 번 실패했다고 갱신을 멈추지 않는다 — 다음 주기에 다시 해 본다.
      console.warn("자동 갱신 실패:", error);
      scheduleLiveRefresh(body);
    }
  }, LIVE_REFRESH_MS);
}

/* **화면이 돌아오면 즉시 다시 읽는다.**
 *
 * iOS Safari 는 화면이 꺼지거나 다른 앱으로 가면 백그라운드 타이머를 죽이고,
 * 돌아와도 **자동으로 재개하지 않는다.** setTimeout 하나만 믿으면 그 순간부터
 * 화면이 영영 얼어붙는다 — 실측 2026-08-19: 아이폰에서 11:24 값이 12:08 까지
 * 43분간 그대로였다(서버는 멀쩡히 새 값을 주고 있었다).
 *
 * 그래서 `visibilitychange` 로 복귀를 잡는다. 한 번만 매단다 — loadTrading 이
 * 돌 때마다 매달면 핸들러가 쌓여서 복귀 한 번에 여러 번 읽는다.
 */
function bindVisibilityRefresh(body) {
  if (visibilityBound) return;
  visibilityBound = true;
  document.addEventListener("visibilitychange", async () => {
    if (document.hidden) return;
    if (!liveEnabled(body)) return;
    // 방금 읽었으면 다시 읽지 않는다 — 탭을 빠르게 오갈 때 매번 창고를 연다.
    if (Date.now() - lastLoadedAt < LIVE_REFRESH_MS) {
      scheduleLiveRefresh(body);   // 예약만 되살린다
      return;
    }
    try {
      await loadTrading();
    } catch (error) {
      console.warn("복귀 갱신 실패:", error);
    }
  });
}

runAll([loadTrading, loadReview]);
