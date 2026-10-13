/* 매매 과정 탭 — 흐름도 + AI 속 들여다보기 (dashboard.md §7-2).
 *
 * 공통 규약(as_of·장부·시장을 API 에 싣기, 차트 테마, ResizeObserver)은 scope.js 에 있다.
 * 화면은 숫자를 만들지 않는다 — /api/pipeline 이 세션 기록·세션이 쓴 선정 함수·그때 설정에서 낸 값을 그린다.
 * 상태(고른 단계·종목)는 URL 쿼리(?stage= · ?entity=)에만 둔다 — 브라우저 저장소 금지.
 */

const PL_UNKNOWN = "모름";
const plState = { flow: null, stage: null, cand: null, inside: null };

function plEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const plNum = (v) => (v === null || v === undefined ? PL_UNKNOWN : Number(v).toLocaleString("ko-KR"));
const plPct = (v, d = 1) => (v === null || v === undefined ? PL_UNKNOWN : `${(v * 100).toFixed(d)}%`);
const plSigned = (v, d = 4) => (v === null || v === undefined ? PL_UNKNOWN : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`);
const plDay = (iso) => (iso ? String(iso).slice(0, 10) : PL_UNKNOWN);
// 시각은 한국 시간 고정(scope.js kstStamp, 2026-10-13 밤) — ISO 를 잘라 쓰면 서버 시간대(UTC)가 그대로 보였다.
const plStamp = (iso) => (iso ? kstStamp(iso) : PL_UNKNOWN);

/* 종류 → 색 토큰. 손익 색(--up/--down)·선택 색(--accent)은 쓰지 않는다 — 범주 팔레트만(DESIGN.md). */
function plKindColor(kind) {
  return { ai: COLOR.series[1], llm: COLOR.series[4], cand: COLOR.series[3] }[kind] || COLOR.muted;
}
const PL_KIND_LABEL = { ai: "AI", rule: "규칙", llm: "LLM" };
const PL_STEP_AI = { ai: "AI 씀", llm: "LLM 확인", rule: "AI 안 씀" };
const PL_CIRCLED = ["", "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"];

/* 최신 방식만 보인다(사용자 2026-10-13 "기각된 거는 보여주지 말고 가장 최신 버전으로만") — 꺼진 단계는 흐름도에서 빼고,
   판정이 끝나 기각된 후보(다음 판정이 없는 것)는 칩을 숨긴다. 기각 기록은 모델 검증 탭 과거 학습 내역·docs/trials-postmortem.md.
   꺼진 단계에 걸린 살아 있는 후보는 같은 큰 단계의 마지막 켜진 노드에 붙인다. */
function plCandAlive(c) {
  const s = String(c.status || "");
  return !(s.startsWith("기각") && !s.includes("다음 판정"));
}
function plVisible(data) {
  const nodes = (data.nodes || []).filter((n) => n.active || n.key === "commentary");
  const shown = new Set(nodes.map((n) => n.key));
  const anchor = {};
  (data.steps || []).forEach((st) => {
    const live = st.nodes.filter((k) => shown.has(k));
    st.nodes.forEach((k) => { anchor[k] = shown.has(k) ? k : live[live.length - 1]; });
  });
  const candidates = (data.candidates || []).filter(plCandAlive)
    .map((c) => ({ ...c, stage: anchor[c.stage] || c.stage, own_stage: c.stage }))
    .filter((c) => shown.has(c.stage));
  return { nodes, candidates };
}
function plStepNo(data, key) {
  const st = (data.steps || []).find((s) => s.nodes.includes(key));
  return st && st.no ? PL_CIRCLED[st.no] || String(st.no) : "";
}

function plSetQuery(key, value) {
  const url = new URL(window.location.href);
  if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
  window.history.replaceState(null, "", url.pathname + url.search);
}

/* ---------------------------------------------------------------- 흐름도 */

async function renderFlow() {
  const body = await fetchJson("pipeline");
  showScope(body);
  const data = body.data;
  plState.flow = data;
  renderSessionBar(data, body);
  const warnings = [];
  if (!data.session) warnings.push("이 시점에 보이는 세션 기록이 없다 — 단계 숫자는 모름이다.");
  if (data.screen_error) warnings.push(`선정 함수를 다시 부르지 못했다 — ${plEsc(data.screen_error)}`);
  showAlerts(warnings);
  renderSteps(data);
  renderFunnel(data);
  drawFlow(data);
  const wanted = new URLSearchParams(window.location.search).get("stage");
  const first = data.nodes.find((n) => n.key === wanted) || data.nodes.find((n) => n.key === "model") || data.nodes[0];
  const candId = new URLSearchParams(window.location.search).get("cand");
  const cand = candId && (data.candidates || []).find((c) => c.id === candId);
  if (cand) showCandidate(cand); else showStage(first.key);
  renderPicks(data);
}

/* 최신 매매 한 번만 보인다 — 날짜 고르기는 뺐다(사용자 2026-10-13). 되감기는 상단 기준 시각(as_of)으로만. */
function renderSessionBar(data, body) {
  const stamp = document.getElementById("pl-stamp");
  stamp.innerHTML = data.session
    ? `최신 매매 — <span class="num">${plDay(data.session.as_of)}</span> 장 마감 자료로 정해 다음 거래일 아침에 주문했다`
      + (data.session.nav ? ` · 자본 <span class="num">${plNum(Math.round(data.session.nav))}원</span>` : "")
    : `이 시점에 보이는 매매 기록이 없다`;
}

/* 깔때기 — 흐름 노드(켜진 것, 해설 제외)마다 나간 종목 수. 로그 축 — 2,868 → 24 를 한 그림에(§8-5). 색 = AI·규칙·LLM. */
function renderFunnel(data) {
  const cap = document.getElementById("pl-funnel-cap");
  const nodes = (data.nodes || []).filter((n) => n.active && n.key !== "commentary" && n.key !== "analysts" && n.n_out !== null && n.n_out !== undefined && n.n_out > 0);
  if (cap) cap.innerHTML = nodes.length ? vizCaption([[plKindColor("ai"), "AI"], [plKindColor("rule"), "규칙"], [plKindColor("llm"), "LLM"]],
    "단계마다 남은 수 · 로그 축 · 주문·체결은 조각·건수") : "";
  hbarChart("pl-funnel", nodes.map((n) => ({
    label: n.title, value: n.n_out, color: plKindColor(n.kind),
    tip: `${plEsc(n.title)}<br>${n.n_in !== null && n.n_in !== undefined ? `${plNum(n.n_in)} → ` : ""}${plNum(n.n_out)}`,
  })), { log: true, phoneTop: 16, format: (v) => plNum(v), labelMax: 22, min: 120 });
}

function renderSteps(data) {
  const host = document.getElementById("pl-steps");
  const steps = data.steps || [];
  if (!steps.length) { host.innerHTML = `<p class="empty">단계 정보가 없다.</p>`; return; }
  const alive = plVisible(data).candidates;
  const rows = steps.map((st) => {
    const counts = st.count_text || "";
    const cands = alive.filter((c) => st.nodes.includes(c.own_stage)).map((c) => plEsc(c.id));
    const first = st.live_nodes[0] || st.nodes[0];
    return `<tr class="pl-step${st.no ? "" : " off-path"}" data-stage="${plEsc(first)}" tabindex="0">
      <td class="num pl-step-no">${st.no ? PL_CIRCLED[st.no] : "·"}</td>
      <td class="pl-step-title">${plEsc(st.title)}</td>
      <td class="pl-step-does">${plEsc(st.does)}</td>
      <td class="pl-step-ai"><span class="pl-kind k-${plEsc(st.ai)}">${plEsc(PL_STEP_AI[st.ai] || st.ai)}</span><span class="pl-step-why">${plEsc(st.why_ai)}</span></td>
      <td class="num pl-step-n">${counts}</td>
      <td class="pl-step-cand">${cands.length ? cands.join(" · ") : ""}</td>
    </tr>`;
  }).join("");
  host.innerHTML = `<div class="table-scroll"><table class="dense pl-steps-table">
    <thead><tr><th></th><th>단계</th><th>하는 일</th><th>AI</th><th class="num">이날 숫자</th><th>시험 중 후보</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
  host.querySelectorAll("tr.pl-step").forEach((tr) => {
    const go = () => { showStage(tr.dataset.stage); document.getElementById("pl-flow-title").scrollIntoView({ behavior: "smooth", block: "start" }); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
}

function plColumns(width) {
  if (width >= 1180) return 6;
  if (width >= 820) return 4;
  if (width >= 560) return 3;
  return 1;
}

/* 노드 배치 — 넓으면 지그재그(행마다 방향을 바꾼다), 좁으면 한 줄 세로. 좌표는 픽셀 그대로 쓰도록
   보이지 않는 모서리 점 둘을 둔다(그래프는 점들의 외접 상자를 차트에 맞춰 늘린다). */
function plLayout(nodes, cands, width) {
  const cols = plColumns(width);
  const chain = nodes.filter((n) => n.key !== "commentary");
  const side = nodes.find((n) => n.key === "commentary");
  const candBy = {};
  cands.forEach((c) => { (candBy[c.stage] = candBy[c.stage] || []).push(c); });
  const maxChips = Math.max(0, ...chain.map((n) => (candBy[n.key] || []).length));
  const pos = {};
  let height;
  if (cols === 1) {
    // 폰 — 한 줄 세로. 후보 칩은 노드 오른쪽 한 칸에 세로로 쌓고, 칩이 많은 단계는 그 행을 늘린다.
    const chipW = 78, nodeW = Math.min(220, width - chipW - 34);
    let y = 30;
    chain.forEach((n) => {
      const chips = candBy[n.key] || [];
      const rowH = Math.max(62, chips.length * 24 + 14);
      pos[n.key] = { x: nodeW / 2 + 2, y: y + 23, w: nodeW, h: 46 };
      chips.forEach((c, j) => {
        pos[`cand:${c.id}`] = { x: width - chipW / 2 - 4, y: y + 10 + j * 24, w: chipW, h: 20 };
      });
      y += rowH;
    });
    if (side) { pos[side.key] = { x: nodeW / 2 + 2, y: y + 23, w: nodeW, h: 46 }; y += 62; }
    height = y + 10;
  } else {
    const cellW = width / cols, nodeW = Math.min(170, cellW - 22), rowH = 74 + maxChips * 24;
    chain.forEach((n, i) => {
      const row = Math.floor(i / cols), k = i % cols;
      const col = row % 2 === 0 ? k : cols - 1 - k;
      pos[n.key] = { x: cellW * col + cellW / 2, y: 36 + row * rowH, w: nodeW, h: 48 };
      // 칩은 노드 왼쪽 절반 아래에 — 행 사이를 잇는 세로 화살표(노드 가운데)와 겹치지 않게.
      const chipW = Math.min(80, nodeW / 2 - 6);
      (candBy[n.key] || []).forEach((c, j) => {
        pos[`cand:${c.id}`] = { x: cellW * col + cellW / 2 - nodeW / 2 + chipW / 2, y: 36 + row * rowH + 42 + j * 24, w: chipW, h: 20 };
      });
    });
    const rows = Math.ceil(chain.length / cols);
    if (side) {
      const last = chain.length;
      const row = Math.floor(last / cols), k = last % cols;
      const col = row % 2 === 0 ? k : cols - 1 - k;
      pos[side.key] = { x: cellW * col + cellW / 2, y: 36 + row * rowH, w: nodeW, h: 48 };
      height = 36 + Math.max(rows, row + 1) * rowH;
    } else {
      height = 36 + rows * rowH;
    }
  }
  return { pos, height, chain, side, candBy };
}

function plNodeLabel(n, no) {
  const pre = no ? `${no} ` : "";
  n = { ...n, title: pre + n.title };
  if (n.key === "exposure" && n.active) return `{t|${n.title}}\n{c|배수 ${n.scale === null || n.scale === undefined ? PL_UNKNOWN : plPct(n.scale, 0)}}`;
  if (n.key === "cadence") return `{t|${n.title}}\n{c|${n.holding_day === true ? "보유일 — 교체 없음" : n.holding_day === false ? "교체일" : PL_UNKNOWN}}`;
  const counts = n.active
    ? (n.n_in !== null && n.n_in !== undefined ? `${plNum(n.n_in)} → ${plNum(n.n_out)}` : (n.n_out !== null && n.n_out !== undefined ? plNum(n.n_out) : ""))
    : "꺼짐";
  return `{t|${n.title}}\n{c|${counts}}`;
}

function drawFlow(data) {
  const host = document.getElementById("pl-flow");
  const width = host.clientWidth || 900;
  const view = plVisible(data);
  const cands = view.candidates;
  const lay = plLayout(view.nodes, cands, width);
  host.style.height = `${Math.round(lay.height)}px`;
  const instance = chart("pl-flow");
  instance.resize();
  const selected = plState.cand ? `cand:${plState.cand}` : plState.stage;
  const items = [];
  view.nodes.forEach((n) => {
    const p = lay.pos[n.key];
    if (!p) return;
    const color = plKindColor(n.kind);
    const on = n.key === selected;
    items.push({
      id: n.key, name: n.title, x: p.x, y: p.y, symbol: "rect", symbolSize: [p.w, p.h], value: n.key,
      itemStyle: { color: COLOR.panel, borderColor: on ? COLOR.accent : color, borderWidth: on ? 2 : 1,
                   borderType: n.key === "commentary" ? "dashed" : "solid", opacity: n.active ? 1 : 0.38 },
      label: { show: true, position: "inside", formatter: plNodeLabel(n, plStepNo(data, n.key)),
               rich: { t: { color: n.active ? COLOR.text : COLOR.dim, fontSize: 11, fontFamily: "Pretendard", lineHeight: 15,
                            width: p.w - 10, overflow: "truncate" },
                       c: { color: n.active ? color : COLOR.dim, fontSize: 11, fontFamily: "IBM Plex Mono", lineHeight: 15 } } },
      tooltip: { formatter: `${plEsc(n.title)} · ${PL_KIND_LABEL[n.kind] || n.kind}${n.active ? "" : " · 꺼짐"}` },
    });
  });
  cands.forEach((c) => {
    const p = lay.pos[`cand:${c.id}`];
    if (!p) return;
    const on = `cand:${c.id}` === selected;
    items.push({
      id: `cand:${c.id}`, name: c.id, x: p.x, y: p.y, symbol: "rect", symbolSize: [p.w, p.h], value: c.id,
      itemStyle: { color: COLOR.panel, borderColor: on ? COLOR.accent : plKindColor("cand"), borderType: "dashed", borderWidth: on ? 2 : 1 },
      label: { show: true, position: "inside", formatter: `후보 ${c.id}`, color: plKindColor("cand"), fontSize: 10,
               fontFamily: "IBM Plex Mono", width: p.w - 6, overflow: "truncate" },
      tooltip: { formatter: `${plEsc(c.title)} · ${plEsc(c.status)}` },
    });
  });
  // 외접 상자 고정용 투명 점 — 좌표가 픽셀 그대로 앉게.
  items.push({ id: "_a", x: 0, y: 0, symbolSize: 0, label: { show: false }, tooltip: { show: false } });
  items.push({ id: "_b", x: width, y: lay.height, symbolSize: 0, label: { show: false }, tooltip: { show: false } });
  const links = [];
  for (let i = 0; i + 1 < lay.chain.length; i += 1) {
    links.push({ source: lay.chain[i].key, target: lay.chain[i + 1].key });
  }
  Object.entries(lay.candBy).forEach(([stage, list]) => {
    if (lay.pos[stage]) list.forEach((c) => links.push({ source: stage, target: `cand:${c.id}`, symbol: ["none", "none"],
      lineStyle: { type: "dashed", color: plKindColor("cand"), opacity: 0.5, curveness: 0 } }));
  });
  instance.setOption({
    ...BASE, grid: undefined, legend: undefined,
    tooltip: { ...BASE.tooltip, trigger: "item" },
    series: [{
      type: "graph", layout: "none", left: 0, top: 0, right: 0, bottom: 0, roam: false, silent: false,
      data: items, links,
      edgeSymbol: ["none", "arrow"], edgeSymbolSize: 8,
      lineStyle: { color: COLOR.dim, width: 1, opacity: 0.8 },
      emphasis: { disabled: true },
    }],
  }, true);
  instance.off("click");
  instance.on("click", (event) => {
    const id = String(event.data && event.data.id || "");
    if (!id || id.startsWith("_")) return;
    if (id.startsWith("cand:")) {
      const cand = cands.find((c) => `cand:${c.id}` === id);
      if (cand) showCandidate(cand);
    } else {
      showStage(id);
    }
  });
}

/* ---------------------------------------------------------------- 옆 패널 */

function plSettingsTable(settings) {
  if (!settings || !settings.length) return "";
  return `<table class="dense pl-kv"><tbody>${settings.map((s) =>
    `<tr><th title="${plEsc(s.key)}">${plEsc(s.label)}</th><td class="num ${s.found ? "" : "dim"}">${plEsc(s.display)}</td></tr>`).join("")}</tbody></table>`;
}

function plDrops(d) {
  if (!d) return "";
  const reasons = d.reasons.map((r) => `<li><span>${plEsc(r.reason)}</span><span class="num">${plNum(r.count)}</span></li>`).join("");
  const ex = d.examples.map((x) =>
    `<tr><td><button type="button" class="linky pl-ent" data-entity="${plEsc(x.entity_id)}">${plEsc(x.name || x.entity_id)}</button>
      <span class="code">${plEsc(x.entity_id)}</span></td><td>${plEsc(x.reason)}</td></tr>`).join("");
  return `<h3>걸러진 종목 <span class="sub">${plNum(d.count)}종목 · 사유별</span></h3>
    <ul class="pl-reasons">${reasons}</ul>
    <h3>예시 <span class="sub">점수가 높았는데 빠진 순 · 누르면 아래에서 본다</span></h3>
    <table class="dense pl-ex"><tbody>${ex}</tbody></table>`;
}

function plStageExtra(n) {
  if (n.key === "analysts" && n.analysts) {
    return `<h3>Analyst <span class="sub">세션 시각에 보이던 최신 신호</span></h3>
      <table class="dense pl-an"><thead><tr><th>이름</th><th class="num">종목</th><th>역할</th></tr></thead><tbody>${
      n.analysts.map((a) => `<tr><td><span class="pl-dot k-${plEsc(a.kind)}"></span>${plEsc(a.name)}${a.label ? `<span class="code">${plEsc(a.label)}</span>` : ""}</td>
        <td class="num">${plNum(a.n)}</td><td>${plEsc(a.role)}${a.weight ? ` <span class="num">${a.weight}</span>` : ""}</td></tr>`).join("")}</tbody></table>`;
  }
  if (n.key === "model" && n.model) {
    const m = n.model.model;
    const rows = [["점수 가중치", (n.model.alpha || []).join(" · ") || PL_UNKNOWN]];
    if (m) {
      rows.push(["모델 파일", m.file], ["버전", m.version], ["학습 자료 끝", m.trained_through], ["쓰기 시작", m.usable_from]);
      if (m.seeds) rows.push(["시드", m.seeds.join(", ")]);
      if (m.parts) rows.push(["구성", m.parts]);
      if (m.protocol_hash) rows.push(["등록 해시", m.protocol_hash]);
    } else if (n.model.kind === "ai") {
      rows.push(["모델 파일", "이 시점에 쓸 수 있는 모델 파일이 없다 — 모름"]);
    }
    const gain = m && m.gain && m.gain.length
      ? `<h3>입력별 비중 <span class="sub">학습 때 gain — 모델이 무엇에 기대나</span></h3><ul class="pl-reasons">${
        m.gain.map((g) => `<li><span>${plEsc(g.feature)}</span><span class="num">${plPct(g.share)}</span></li>`).join("")}</ul>` : "";
    return `<table class="dense pl-kv"><tbody>${rows.map(([k, v]) => `<tr><th>${plEsc(k)}</th><td class="mono">${plEsc(v)}</td></tr>`).join("")}</tbody></table>${gain}`;
  }
  if (n.key === "risk_floor" && n.threshold !== undefined) {
    return `<p class="pl-line">임계(이 값 미만이면 탈락) <span class="num">${plSigned(n.threshold)}</span></p>`;
  }
  if (n.key === "exposure") {
    return `<p class="pl-line">배수 <span class="num">${n.scale === null || n.scale === undefined ? PL_UNKNOWN : plPct(n.scale, 0)}</span>
      · 결정 <span class="mono">${plEsc(n.driver || PL_UNKNOWN)}</span></p>${(n.notes || []).length
      ? `<ul class="pl-notes">${n.notes.map((t) => `<li>${plEsc(t)}</li>`).join("")}</ul>` : ""}`;
  }
  if (n.key === "weights") {
    return `<p class="pl-line">배분 <span class="mono">${plEsc(n.allocator || PL_UNKNOWN)}</span> · 비중 합 <span class="num">${plPct(n.weight_sum)}</span>
      — 노출 배수를 곱하기 전</p>`;
  }
  if (n.key === "execute") {
    return `<p class="pl-line">조각 <span class="num">${plNum(n.n_out)}</span> (매수 ${plNum(n.buy)} · 매도 ${plNum(n.sell)}) · 종목 ${plNum(n.names)}
      ${n.blocked_by ? ` · <span class="warn">막힘: ${plEsc(n.blocked_by)}</span>` : ""}</p>`;
  }
  if (n.key === "fill" && n.fill) {
    const f = n.fill;
    return `<p class="pl-line">체결율(수량) <span class="num">${plPct(f.quantity_rate)}</span> · 체결율(건수) <span class="num">${plPct(f.count_rate)}</span>
      · 일부 체결 ${plNum(f.partial_count)} · 취소 ${plNum(f.unfilled.cancelled)} · 거부 ${plNum(f.unfilled.rejected)} · 대기 ${plNum(f.unfilled.pending)}</p>`;
  }
  return "";
}

function plCandChips(stageKey) {
  const list = plVisible(plState.flow).candidates.filter((c) => c.stage === stageKey);
  if (!list.length) return "";
  return `<h3>후보 <span class="sub">이 단계를 바꾸려고 등록·대기 중</span></h3><div class="pl-chips">${
    list.map((c) => `<button type="button" class="pl-chip" data-cand="${plEsc(c.id)}">${plEsc(c.id)} <span>${plEsc(c.status)}</span></button>`).join("")}</div>`;
}

function showStage(key) {
  const n = plState.flow.nodes.find((x) => x.key === key);
  if (!n) return;
  plState.stage = key;
  plState.cand = null;
  plSetQuery("stage", key);
  plSetQuery("cand", null);
  const kind = PL_KIND_LABEL[n.kind] || n.kind;
  const counts = n.n_in !== null && n.n_in !== undefined
    ? `들어온 <span class="num">${plNum(n.n_in)}</span> → 나간 <span class="num">${plNum(n.n_out)}</span>${
      n.n_out !== null && n.n_out !== undefined ? ` <span class="sub">(−${plNum(n.n_in - n.n_out)})</span>` : ""}`
    : n.n_out !== null && n.n_out !== undefined ? `나간 <span class="num">${plNum(n.n_out)}</span>` : "";
  document.getElementById("pl-side-title").firstChild.textContent = `${n.title} `;
  document.getElementById("pl-side-sub").innerHTML = `<span class="pl-kind k-${plEsc(n.kind)}">${plEsc(kind)}</span>${n.active ? "" : " · 꺼짐"}`;
  document.getElementById("pl-side").innerHTML = `
    ${counts ? `<p class="pl-counts">${counts}</p>` : ""}
    ${n.note ? `<p class="pl-line">${plEsc(n.note)}</p>` : ""}
    ${n.plain ? `<p class="pl-plain">${plEsc(n.plain)}</p>` : ""}
    ${n.detail ? `<details class="fold"><summary>더 읽기</summary><p class="pl-plain">${plEsc(n.detail)}</p></details>` : ""}
    ${plStageExtra(n)}
    ${n.settings && n.settings.length ? `<h3>설정 <span class="sub">그 세션 시각의 값</span></h3>${plSettingsTable(n.settings)}` : ""}
    ${plDrops(n.drops)}
    ${plCandChips(n.key)}`;
  plBindSide();
  drawFlow(plState.flow);
}

function showCandidate(c) {
  plState.cand = c.id;
  plState.stage = null;
  plSetQuery("cand", c.id);
  plSetQuery("stage", null);
  const stage = plState.flow.nodes.find((n) => n.key === c.stage);
  document.getElementById("pl-side-title").firstChild.textContent = `후보 ${c.id} `;
  document.getElementById("pl-side-sub").innerHTML = `<span class="pl-kind k-cand">후보</span> · ${plEsc(stage ? stage.title : c.stage)}`;
  const docState = { fixed: "고정", draft: "초안 — 미고정", unmarked: "머리에 고정 표시 없음", missing: "문서 없음" }[c.doc_state] || PL_UNKNOWN;
  const judge = (c.judge || []).map((j) => `<li><span>${plEsc(j.label)}</span><span class="num">${plEsc(j.date || "날짜 미정")}</span></li>`).join("");
  const results = (c.results || []).length
    ? c.results.map((r) => `<div class="pl-result"><div class="pl-result-head"><span class="code">${plEsc(r.entity_id)}</span>
        <span class="num">${plStamp(r.at)}</span>${r.protocol_hash ? `<span class="code">해시 ${plEsc(r.protocol_hash)}</span>` : ""}</div>
        <p>${plEsc(r.detail)}</p></div>`).join("")
    : `<p class="empty">${c.status.startsWith("판정 전") ? "판정 전 — 결과 없음" : "시행 대장에 이 후보의 기록이 없다"}</p>`;
  const cat = c.catalog ? `<h3>카탈로그 요약 <span class="sub">${plEsc(c.catalog.date)} · ${plEsc(c.catalog.result || "")}</span></h3><p class="pl-plain">${plEsc(c.catalog.why || "")}</p>` : "";
  const s = c.sequential;
  const seq = c.pair
    ? `<h3>shadow 장부 짝 순차 검정 <span class="sub">${plEsc(c.pair)}</span></h3>${s
      ? `<p class="pl-line">${plEsc(s.status)} · 겹친 세션 <span class="num">${plNum(s.n)}</span>${
        s.lower !== undefined && s.lower !== null ? ` · 연 <span class="num">${plPct(s.annual)}</span> [${plPct(s.lower)}, ${plPct(s.upper)}]`
          : " · 신뢰구간은 표본이 차야 나온다"}${
        s.since ? ` · ${plEsc(s.since)} 부터` : ""}</p>` : `<p class="empty">순차 검정 결과 없음</p>`}` : "";
  const sw = c.switch_values
    ? `<h3>바꾸는 설정 <span class="sub">tools/apply_switch.py ${plEsc(c.switch)}</span></h3><table class="dense pl-kv"><tbody>${
      Object.entries(c.switch_values).map(([k, v]) => `<tr><th>${plEsc(k)}</th><td class="num">${plEsc(v)}</td></tr>`).join("")}</tbody></table>
      <p class="pl-line">${c.applied ? "이 시점 설정이 위 값과 같다 — 실전 적용" : c.applied === false ? "이 시점 설정은 아직 위 값이 아니다" : "적용 여부 모름"}</p>` : "";
  document.getElementById("pl-side").innerHTML = `
    <p class="pl-counts"><span class="pl-status">${plEsc(c.status)}</span></p>
    <p class="pl-plain">${plEsc(c.change)}</p>
    <table class="dense pl-kv"><tbody>
      <tr><th>등록 문서</th><td class="mono">${plEsc(c.doc)}</td></tr>
      <tr><th>문서 상태</th><td>${plEsc(docState)}${c.doc_hash ? ` · 해시 <span class="mono">${plEsc(c.doc_hash)}</span>` : ""}</td></tr>
    </tbody></table>
    <h3>판정 일정</h3><ul class="pl-reasons">${judge}</ul>
    <h3>테스트 결과 <span class="sub">시행 대장 원문 — 판정은 화면이 새로 내리지 않는다</span></h3>${results}
    ${cat}${seq}${sw}
    <p class="pl-back"><button type="button" class="linky" data-stage="${plEsc(c.stage)}">← ${plEsc(stage ? stage.title : c.stage)} 단계로</button></p>`;
  plBindSide();
  drawFlow(plState.flow);
}

function plBindSide() {
  const side = document.getElementById("pl-side");
  side.querySelectorAll("[data-cand]").forEach((b) => b.addEventListener("click", () => {
    const c = (plState.flow.candidates || []).find((x) => x.id === b.dataset.cand);
    if (c) showCandidate(c);
  }));
  side.querySelectorAll("[data-stage]").forEach((b) => b.addEventListener("click", () => showStage(b.dataset.stage)));
  side.querySelectorAll(".pl-ent").forEach((b) => b.addEventListener("click", () => {
    loadInside(b.dataset.entity);
    document.getElementById("pl-inside-title").scrollIntoView({ behavior: "auto", block: "start" });
  }));
}

/* ---------------------------------------------------------------- AI 속 */

function renderPicks(data) {
  const box = document.getElementById("pl-picks");
  const list = data.selected || [];
  const current = new URLSearchParams(window.location.search).get("entity");
  box.innerHTML = list.length
    ? `<span class="pl-picks-label">선정 ${list.length}종목</span>` + list.map((s) =>
      `<button type="button" class="pl-pick-btn${s.entity_id === current ? " on" : ""}" data-entity="${plEsc(s.entity_id)}" title="${plEsc(s.entity_id)}">${plEsc(s.name || s.entity_id)}</button>`).join("")
    : `<p class="empty">이 세션 선정 명단이 없다.</p>`;
  box.querySelectorAll("[data-entity]").forEach((b) => b.addEventListener("click", () => loadInside(b.dataset.entity)));
}

document.getElementById("pl-search").addEventListener("submit", (event) => {
  event.preventDefault();
  let code = document.getElementById("pl-code").value.trim().toUpperCase();
  if (!code) return;
  if (!code.includes(":")) code = `${(params().get("market") || "KR").toUpperCase() === "US" ? "US" : "KR"}:${code}`;
  loadInside(code);
});

async function loadInside(entity) {
  if (entity) plSetQuery("entity", entity);
  document.querySelectorAll(".pl-pick-btn").forEach((b) => b.classList.toggle("on", b.dataset.entity === entity));
  await renderInside();
}

async function renderInside() {
  const entity = new URLSearchParams(window.location.search).get("entity");
  const body = await fetchJson(`pipeline/inside${entity ? `?entity=${encodeURIComponent(entity)}` : ""}`);
  const d = body.data;
  plState.inside = d;
  const sub = document.getElementById("pl-inside-sub");
  if (!d.session) {
    sub.textContent = d.reason || PL_UNKNOWN;
    ["pl-contrib-head", "pl-why"].forEach((id) => { document.getElementById(id).innerHTML = `<p class="empty">${plEsc(d.reason || PL_UNKNOWN)}</p>`; });
    return;
  }
  sub.innerHTML = `<strong>${plEsc(d.name || d.entity_id)}</strong> <span class="code">${plEsc(d.entity_id)}</span> · 세션 ${plDay(d.session.as_of)}`;
  drawContrib(d);
  drawTtm(d);
  renderWhy(d);
}

function drawContrib(d) {
  const c = d.contrib || {};
  const head = document.getElementById("pl-contrib-head");
  const foot = document.getElementById("pl-contrib-foot");
  const host = document.getElementById("pl-contrib");
  document.getElementById("pl-contrib-sub").textContent = c.part || "";
  const stored = c.stored ? `저장된 실전 점수(평활 뒤) <span class="num">${plSigned(c.stored.score, 3)}</span>` : "저장된 실전 점수 없음";
  if (!c.available) {
    head.innerHTML = `<p class="empty">${plEsc(c.reason || PL_UNKNOWN)}</p><p class="pl-line">${stored}</p>`;
    host.style.display = "none";
    foot.textContent = "";
    return;
  }
  host.style.display = "";
  head.innerHTML = `<p class="pl-line">모델 원점수 <span class="num">${plSigned(c.raw)}</span> · 순위 <span class="num">${plNum(c.rank)} / ${plNum(c.n)}</span>
    · ${stored}${c.file ? ` · <span class="code">${plEsc(c.file)}</span>` : ""}</p>`;
  const bars = [...(c.bars || [])];
  if (c.rest !== null && c.rest !== undefined) bars.push({ feature: `나머지 ${c.n_features - bars.length}열 합`, contrib: c.rest, value: null });
  bars.reverse();
  host.style.height = `${Math.max(140, bars.length * 22 + 40)}px`;
  const instance = chart("pl-contrib");
  instance.resize();
  const pos = COLOR.series[0], neg = COLOR.series[2];
  instance.setOption({
    ...BASE, legend: undefined,
    grid: { left: 118, right: 54, top: 8, bottom: 22 },
    tooltip: { ...BASE.tooltip, trigger: "item",
               formatter: (p) => `${plEsc(p.name)}<br>기여 ${plSigned(p.value, 5)}${bars[p.dataIndex].value !== null && bars[p.dataIndex].value !== undefined ? `<br>입력 값 ${plSigned(bars[p.dataIndex].value, 3)}` : ""}` },
    xAxis: { type: "value", splitNumber: 3, axisLabel: { color: COLOR.muted, fontSize: 10, fontFamily: "IBM Plex Mono", hideOverlap: true } },
    yAxis: { type: "category", data: bars.map((b) => b.feature), axisLabel: { color: COLOR.text, fontSize: 11, fontFamily: "IBM Plex Mono", width: 110, overflow: "truncate" } },
    series: [{ type: "bar", barMaxWidth: 14, data: bars.map((b) => ({ value: b.contrib, itemStyle: { color: b.contrib >= 0 ? pos : neg } })),
               label: { show: true, position: "right", color: COLOR.muted, fontSize: 10, fontFamily: "IBM Plex Mono",
                        formatter: (p) => plSigned(p.value, 3) } }],
  }, true);
  foot.textContent = [c.part_note, c.value_note, `막대 합 + 기준값(${plSigned(c.bias, 4)}) = 모델 원점수`].filter(Boolean).join(" · ");
}

/* 버전 문자열(ttm-r2-zeroshot-L256-v2) → 쉬운 이름. 모르는 모양이면 원문 그대로. */
function plTtmName(version) {
  const m = /^ttm-(r[\d.]+)-zeroshot-L(\d+)/.exec(String(version || ""));
  return m ? `IBM TTM ${m[1]} · 추가 학습 없이(제로샷) · 최근 ${m[2]}일 종가로 예측` : (version || "");
}
/* 이 세션 설정에서 하한·상한 Analyst 가 tsfm 인 켜진 단계가 있나. */
function plTsfmUsed(flow) {
  return ((flow && flow.nodes) || []).some((n) => n.active && ["extra_floor", "ceiling", "combo_floor", "risk_floor"].includes(n.key)
    && (n.settings || []).some((x) => String(x.value) === "tsfm"));
}

function drawTtm(d) {
  const t = d.ttm || {};
  const host = document.getElementById("pl-ttm");
  const foot = document.getElementById("pl-ttm-foot");
  document.getElementById("pl-ttm-sub").textContent = plTtmName(t.version);
  const used = plTsfmUsed(plState.flow);
  const explain = "선은 수정 주가(액면분할·배당을 맞춘 종가), 점은 TTM 예측 순위다 — 그날 계산 대상 종목 안에서 "
    + "'5일 뒤 수익' 예측의 순위를 −1(가장 떨어질 것으로 예측) ~ +1(가장 오를 것으로 예측)로 적은 값. "
    + (used ? "이 세션 매매에서 나쁜 종목 빼기에 쓰인다." : "지금 매매에는 쓰지 않는다 — 기록용(IX-T 시험 장부만 쓴다).");
  foot.textContent = explain;
  if (!(t.prices || []).length && !(t.points || []).length) {
    host.style.display = "none";
    foot.textContent = "가격도 TTM 점수도 없다 — 모름. " + (t.stored_note || "");
    return;
  }
  host.style.display = "";
  host.style.height = "220px";
  const instance = chart("pl-ttm");
  instance.resize();
  const price = (t.prices || []).map((p) => [p.at, p.close]);
  const score = (t.points || []).map((p) => [p.at, p.score]);
  instance.setOption({
    ...BASE,
    grid: { left: 52, right: 40, top: 24, bottom: 26 },
    legend: { ...BASE.legend, data: ["수정 주가", "TTM 예측 순위"] },
    xAxis: { type: "time" },
    yAxis: [{ type: "value", scale: true, axisLabel: { ...AXIS.axisLabel } },
            { type: "value", min: -1, max: 1, splitLine: { show: false }, axisLabel: { ...AXIS.axisLabel } }],
    series: [
      { name: "수정 주가", type: "line", data: price, showSymbol: false, lineStyle: { width: 1.2, color: COLOR.text }, itemStyle: { color: COLOR.text } },
      { name: "TTM 예측 순위", type: "line", yAxisIndex: 1, data: score, showSymbol: true, symbolSize: 4, lineStyle: { width: 1, color: COLOR.series[1] },
        itemStyle: { color: COLOR.series[1] },
        markLine: { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: COLOR.dim, type: "dashed" }, label: { show: false } } },
    ],
  }, true);
  if (!score.length) foot.textContent = "이 종목은 아직 TTM 예측이 없다(계산 대상 밖이거나 신호 시작 전). " + explain;
}

const PL_VERDICT = {
  buy: "점수 상위 안 — 새로 산다", keep: "점수 상위 안 — 계속 든다", buffer_keep: "상위 밖이지만 완충 안 — 팔지 않는다",
  sell_out_of_buffer: "완충 밖으로 밀렸다 — 판다", sell: "명단에서 빠졌다 — 판다",
  pushed_out: "순위는 상위 안인데 명단 밖 — 완충·상관·섹터 중 무엇인지 기록 없음",
  not_top: "상위 밖 — 사지 않는다", filtered: "앞 단계에서 걸러졌다", unknown: PL_UNKNOWN,
};

function renderWhy(d) {
  const w = d.why || {};
  const f = w.filters || {}, r = w.rule || {}, wt = w.weight || {};
  const riskText = { pass: "통과", cut: "탈락", not_reached: "앞 관문에서 빠짐", no_score: "위험 점수 없음 — 자르지 않음" }[f.risk_status] || PL_UNKNOWN;
  const link = new URL("/trading", window.location.href);
  const q = params();
  ["as_of", "lookback", "market", "ledger"].forEach((k) => { if (q.get(k)) link.searchParams.set(k, q.get(k)); });
  link.searchParams.set("entity", d.entity_id);
  document.getElementById("pl-why").innerHTML = `
    <table class="dense pl-kv"><tbody>
      <tr><th>② 걸러짐</th><td>${f.gate_reason ? plEsc(f.gate_reason) : "관문 통과"} · 위험 ${plEsc(riskText)}
        ${f.risk_score !== null && f.risk_score !== undefined ? `(<span class="num">${plSigned(f.risk_score, 3)}</span> / 임계 <span class="num">${plSigned(f.risk_threshold, 3)}</span>)` : ""}</td></tr>
      <tr><th>③ 선정 규칙</th><td>${plEsc(PL_VERDICT[r.verdict] || r.verdict || PL_UNKNOWN)}${r.rank ? ` · 걸러진 뒤 <span class="num">${plNum(r.rank)}</span>위 / ${plNum(r.n_passed)}` : ""}
        ${r.cadence ? `<br><span class="sub">${plEsc(r.cadence)}</span>` : ""}</td></tr>
      <tr><th>④ 비중</th><td>배분 <span class="num">${wt.allocated === null || wt.allocated === undefined ? PL_UNKNOWN : plPct(wt.allocated, 2)}</span>
        × 노출 <span class="num">${wt.exposure_scale === null || wt.exposure_scale === undefined ? PL_UNKNOWN : plPct(wt.exposure_scale, 0)}</span>
        = 목표 <span class="num">${wt.target === null || wt.target === undefined ? PL_UNKNOWN : plPct(wt.target, 2)}</span></td></tr>
    </tbody></table>
    <p class="pl-back"><a class="control-link" href="${plEsc(link.pathname + link.search)}">트레이딩 탭 '왜 샀나' 패널에서 다섯 단계 전부 보기</a></p>`;
}

/* ---------------------------------------------------------------- ③ 거르기 — 뉴스 매수 금지 */

/* 중요 시황 탭에서 옮겨 왔다(사용자 2026-10-13 밤) — 흐름도 '뉴스 매수 금지' 노드가 막은 명단이다.
 * News·SNS Analyst 는 매수 금지만 할 수 있다(매도 권한 없음). 만료된 차단도 남긴다 — "방금 풀렸다" 와 "여태 걸려 있다" 는 다르다. */
const PL_BLOCK_CATEGORY = {
  dilution: "희석", insider_sell: "내부자매도", delisting: "상장폐지",
  litigation: "소송", trading_halt: "거래정지", accounting: "회계",
};

async function renderBlocks() {
  const target = document.getElementById("pl-blocks");
  if (!target) return;
  const body = await fetchJson("pipeline/blocks");
  const d = body.data;
  const count = document.getElementById("pl-blocks-count");
  if (count) count.textContent = `차단 중 ${d.active_verdicts} · 기록 ${d.verdicts.length}건`;
  // 사유별 건수 — 지금 막고 있는 것(차단 중)과 만료를 갈라 쌓는다. 응답을 세기만 한다(§8-5).
  const byCat = {};
  for (const row of d.verdicts) {
    const key = PL_BLOCK_CATEGORY[row.category] || row.category;
    byCat[key] = byCat[key] || { active: 0, expired: 0 };
    byCat[key][row.active ? "active" : "expired"] += 1;
  }
  hbarChart("pl-blocks-chart", Object.entries(byCat).sort((a, b) => (b[1].active + b[1].expired) - (a[1].active + a[1].expired))
    .map(([k, v]) => ({ label: k, value: v.active + v.expired, color: v.active ? COLOR.bad : COLOR.dim,
      tip: `${plEsc(k)}<br>차단 중 ${v.active} · 만료 ${v.expired}` })), { format: (v) => `${v}건`, min: 70 });
  if (!d.verdicts.length) {
    target.innerHTML = `<p class="empty">창 안에 기록된 매수 금지 판정이 없다.</p>`;
    return;
  }
  target.innerHTML = `<table class="dense"><thead><tr><th>종목</th><th class="mid">Analyst</th><th>사유</th>
    <th class="num">발동 KST</th><th class="num">만료 KST</th><th class="mid">상태</th></tr></thead><tbody>${d.verdicts.map((row) => `<tr>
      <td><span class="name">${plEsc(row.name)}</span> <span class="code">${plEsc(row.entity_id)}</span></td>
      <td class="mid"><span class="tag dim">${plEsc(row.analyst)}</span></td>
      <td>${plEsc(PL_BLOCK_CATEGORY[row.category] || row.category)}
        <div class="sub trunc" title="${plEsc(row.reason)}">${plEsc(row.reason)}</div></td>
      <td class="num">${plStamp(row.valid_from)}</td>
      <td class="num">${plStamp(row.expires_at)}</td>
      <td class="mid"><span class="verdict ${row.active ? "critical" : "info"}">${row.active ? "차단 중" : "만료"}</span></td>
    </tr>`).join("")}</tbody></table>`;
}

/* ---------------------------------------------------------------- 시작 */

let plResizeTimer = null;
if (typeof window !== "undefined" && window.ResizeObserver) {
  // 열 수는 폭으로 정해진다 — 폭이 바뀌면 배치를 다시 잡는다(차트 resize 만으로는 열 수가 안 바뀐다).
  let lastCols = null;
  new ResizeObserver(() => {
    const host = document.getElementById("pl-flow");
    if (!host || !plState.flow) return;
    const cols = plColumns(host.clientWidth);
    if (cols === lastCols) return;
    lastCols = cols;
    clearTimeout(plResizeTimer);
    plResizeTimer = setTimeout(() => drawFlow(plState.flow), 60);
  }).observe(document.getElementById("pl-flow"));
}

runAll([renderFlow, renderBlocks, renderInside]);
