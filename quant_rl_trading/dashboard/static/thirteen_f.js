/* 13F 탭 — 미국 기관의 분기말 보유.
 *
 * 이 화면의 어려움은 데이터가 아니라 **시제**다. 숫자는 크고 확실해 보이는데
 * 최대 45일 낡았다. 그래서 여기 코드는 값을 그리기 전에 언제 기준인지를
 * 먼저 그린다 — 낡음을 작은 회색 글씨로 두면 아무도 안 읽는다.
 */

let SELECTED = null;

const usdB = (v) =>
  v === null || v === undefined ? "—" : "$" + (Number(v) / 1e9).toFixed(1) + "십억";

/* 변화 배지. **"미측정" 은 회색이다** — 증감 0 과 색이 같으면 "안 변했다" 로
 * 읽힌다. 이 저장소가 반복해서 낸 "안 물어봤다 vs 없다" 결함이다. */
function changeTag(change, pct) {
  const amount =
    pct === null || pct === undefined ? "" : ` ${(pct * 100).toFixed(1)}%`;
  if (change === "신규") return `<span class="tag pass">신규</span>`;
  if (change === "증가") return `<span class="up">▲${amount}</span>`;
  if (change === "감소") return `<span class="down">▼${amount.replace("-", "")}</span>`;
  if (change === "미측정") return `<span class="dim">직전 분기 없음</span>`;
  return `<span class="dim">유지</span>`;
}

/* -- 그림 (ECharts, 시트 테마) ----------------------------------------------
 * dashboard.md 2026-10-13 밤 ③. 색 규칙: 기관의 매수·매도는 **우리 손익이 아니다** — 손익 색(초록·빨강)을 쓰지 않는다.
 * 막대는 무채색, 강조색은 '우리 보유와 겹침'·'신규' 같은 표시에만. 폰(≤640)은 막대 수를 줄인다(가로 스크롤 없음). */
const TF_PHONE = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(max-width: 640px)") : { matches: false };
const tfTop = (rows, desk, phone) => rows.slice(0, TF_PHONE.matches ? phone : desk);
/** 막대 수로 높이 — 막대 하나 22px(폰 20px) + 여백. 높이를 고정하면 막대가 적은 기관에서 굵은 막대가 된다. */
function tfHeight(id, bars, extra = 34) {
  const el = document.getElementById(id);
  if (el && el.style) el.style.height = `${Math.max(140, bars * (TF_PHONE.matches ? 20 : 22) + extra)}px`;
}
/** 긴 영문 발행사 이름을 축 칸에 맞게 자른다(원문은 툴팁). */
function tfName(text, max) {
  const t = String(text || "");
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}
const tfAxisName = () => (TF_PHONE.matches ? 13 : 24);
const tfOursTag = (row) => (row.ours ? " · 보유" : "");

function drawFilersChart(filers) {
  const id = "tf-filers-chart";
  if (!document.getElementById(id)) return;
  const rows = [...filers].sort((a, b) => b.total_usd - a.total_usd).reverse();   // 가로 막대는 아래부터 그린다
  tfHeight(id, rows.length);
  const c = chart(id);
  c.setOption({
    ...BASE,
    tooltip: { ...BASE.tooltip, trigger: "item",
      formatter: (p) => { const f = rows[p.dataIndex]; return `${f.filer_name}<br>${usdB(f.total_usd)} · ${num(f.holdings)}종목<br>${f.report_date} 기준 · ${f.lag_days}일 뒤 공개`; } },
    grid: { left: 8, right: 64, top: 6, bottom: 6, containLabel: true },
    xAxis: { type: "value", ...AXIS, axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: { type: "category", data: rows.map((f) => tfName(f.filer_name, tfAxisName())), ...AXIS, axisTick: { show: false } },
    series: [{
      type: "bar", barMaxWidth: 14,
      data: rows.map((f) => ({ value: f.total_usd / 1e9,
        itemStyle: { color: f.filer_cik === SELECTED ? COLOR.accent : COLOR.muted } })),
      label: { show: true, position: "right", color: COLOR.muted, fontFamily: "IBM Plex Mono", fontSize: 10,
               formatter: (p) => `$${p.value.toFixed(1)}B` },
    }],
  }, true);
  if (c.off) c.off("click");
  c.on("click", (p) => {
    const f = rows[p.dataIndex];
    if (!f) return;
    SELECTED = f.filer_cik;
    loadFilers();
    loadHoldings(SELECTED);
  });
}

/** 선택 기관 상위 보유 — 왼쪽 비중, 오른쪽 직전 분기 대비 주식 수 변화(%). 종목 축 하나를 같이 쓴다. */
function drawHoldingsChart(data) {
  const id = "tf-holdings-chart";
  if (!document.getElementById(id)) return;
  const rows = tfTop(data.rows || [], 15, 10).slice().reverse();
  tfHeight(id, rows.length, 40);
  const names = rows.map((r) => tfName(r.issuer, TF_PHONE.matches ? 11 : 20) + tfOursTag(r));
  const changeValue = (r) => (r.change === "신규" ? null : r.change_pct === null || r.change_pct === undefined ? null : r.change_pct * 100);
  const cap = document.getElementById("tf-holdings-cap");
  if (cap) {
    cap.innerHTML = `<span class="tf-key"><i class="tf-sw" style="background:${COLOR.muted}"></i>비중(왼쪽)</span>
      <span class="tf-key"><i class="tf-sw" style="background:${COLOR.dim}"></i>직전 분기(${data.previous_date || "없음"}) 대비 주식 수(오른쪽)</span>
      <span class="tf-key"><i class="tf-sw" style="background:${COLOR.accent}"></i>신규 편입 · 우리 미장 보유와 겹침</span>
      ${data.ours_known === false ? `<span>우리 보유: 모름(장부 없음)</span>` : ""}`;
  }
  const c = chart(id);
  c.setOption({
    ...BASE,
    tooltip: { ...BASE.tooltip, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `${r.issuer}${r.ours ? " (우리 보유)" : ""}<br>비중 ${pct(r.weight, 1)} · ${usdB(r.value_usd)}<br>직전 분기 대비 ${r.change}${r.change_pct === null || r.change_pct === undefined ? "" : ` ${(r.change_pct * 100).toFixed(1)}%`}`; } },
    grid: [
      { left: TF_PHONE.matches ? 96 : 150, width: TF_PHONE.matches ? "30%" : "36%", top: 6, bottom: 22 },
      { right: 34, width: TF_PHONE.matches ? "26%" : "30%", top: 6, bottom: 22 },
    ],
    xAxis: [
      { gridIndex: 0, type: "value", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v) => `${v}%` }, splitNumber: 2 },
      { gridIndex: 1, type: "value", ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v) => `${v}%` }, splitNumber: 2 },
    ],
    yAxis: [
      { gridIndex: 0, type: "category", data: names, ...AXIS, axisTick: { show: false },
        axisLabel: { ...AXIS.axisLabel, color: COLOR.muted, fontFamily: "Pretendard Variable, IBM Plex Mono", fontSize: 10,
          formatter: (v) => v, rich: {} } },
      { gridIndex: 1, type: "category", data: names, ...AXIS, axisLabel: { show: false }, axisTick: { show: false } },
    ],
    series: [
      { type: "bar", xAxisIndex: 0, yAxisIndex: 0, barMaxWidth: 12,
        data: rows.map((r) => ({ value: r.weight * 100, itemStyle: { color: r.ours ? COLOR.accent : COLOR.muted } })) },
      { type: "bar", xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 12,
        data: rows.map((r) => (r.change === "신규"
          ? { value: 0, itemStyle: { color: COLOR.accent }, label: { show: true, position: "right", formatter: "신규", color: COLOR.accent, fontSize: 10 } }
          : { value: changeValue(r), itemStyle: { color: COLOR.dim } })),
        markLine: { silent: true, symbol: "none", data: [{ xAxis: 0 }], lineStyle: { color: COLOR.border }, label: { show: false } } },
    ],
  }, true);
}

/** 겹쳐 든 종목 — 최신 분기 기관 수 vs 각 기관의 직전 분기 기관 수. 우리 미장 보유와 겹치면 강조색. */
function drawConsensusChart(rows, meta) {
  const id = "tf-consensus-chart";
  if (!document.getElementById(id)) return;
  const shown = tfTop(rows, 20, 10).slice().reverse();
  tfHeight(id, shown.length, 40);
  const cap = document.getElementById("tf-consensus-cap");
  const ours = rows.filter((r) => r.ours).length;
  if (cap) {
    cap.innerHTML = `<span class="tf-key"><i class="tf-sw" style="background:${COLOR.muted}"></i>최신 분기 기관 수</span>
      <span class="tf-key"><i class="tf-sw" style="background:${COLOR.faint || COLOR.border}"></i>각 기관의 직전 분기</span>
      <span class="tf-key"><i class="tf-sw" style="background:${COLOR.accent}"></i>우리 미장 보유와 겹침</span>
      <span>${meta.ours_known ? `겹침 ${ours}종목 / 우리 보유 ${meta.ours_count}종목` : "우리 보유: 모름(장부 없음)"}</span>`;
  }
  const c = chart(id);
  c.setOption({
    ...BASE,
    tooltip: { ...BASE.tooltip, trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => { const r = shown[ps[0].dataIndex]; return `${r.issuer}${r.ticker ? ` (${r.ticker})` : ""}${r.ours ? " · 우리 보유" : ""}<br>기관 ${r.filers} · 직전 ${r.filers_prev === null || r.filers_prev === undefined ? "—" : r.filers_prev}<br>${r.names.join(", ")}`; } },
    grid: { left: 8, right: 24, top: 6, bottom: 22, containLabel: true },
    xAxis: { type: "value", minInterval: 1, ...AXIS },
    yAxis: { type: "category", data: shown.map((r) => tfName(r.issuer, TF_PHONE.matches ? 13 : 22) + tfOursTag(r)), ...AXIS, axisTick: { show: false } },
    series: [
      { name: "직전 분기", type: "bar", barMaxWidth: 7, barGap: "10%",
        data: shown.map((r) => ({ value: r.filers_prev, itemStyle: { color: COLOR.faint || COLOR.border } })) },
      { name: "최신 분기", type: "bar", barMaxWidth: 7,
        data: shown.map((r) => ({ value: r.filers, itemStyle: { color: r.ours ? COLOR.accent : COLOR.muted } })) },
    ],
  }, true);
}

/** 가장 최근 분기말(YYYY-MM-DD). 기관마다 신고가 달라 첫 줄이 최신이라는 보장이 없다. */
function latestReport(filers) {
  return filers.map((f) => f.report_date).filter(Boolean).sort().slice(-1)[0] || "—";
}

/** 다음 13F 가 언제 이 화면에 들어오나 — **법정 규칙(분기말 + 45일 제출 마감)** 에서 계산한다.
 *  수집은 마감 달 20일(크론 `collect_13f.py`, 2·5·8·11월 20일 20:30)이라 그 날짜를 '갱신' 으로 보여 준다.
 *  사용자 질문(2026-09-29 "최신 기준일이 6/30 인데?") — 늦은 게 아니라 원래 45일 늦게 공개되는 자료다. */
function nextUpdateKpi(latest) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(latest || "");
  if (!m) return kpi("다음 갱신", "—", "기준일을 모른다");
  // 다음 분기말: 기준일 달 + 3 의 말일.
  const nextEnd = new Date(Date.UTC(+m[1], +m[2] - 1 + 3 + 1, 0));
  const deadline = new Date(nextEnd.getTime() + 45 * 86400000);
  const refresh = new Date(Date.UTC(deadline.getUTCFullYear(), deadline.getUTCMonth(), 20));
  if (refresh < deadline) refresh.setUTCMonth(refresh.getUTCMonth() + 1);
  const d = (x) => `${x.getUTCMonth() + 1}/${x.getUTCDate()}`;
  return kpi("다음 갱신", `${refresh.getUTCFullYear()}-${String(refresh.getUTCMonth() + 1).padStart(2, "0")}-20 경`,
             `${d(nextEnd)} 기준분 · 기관 제출 마감 ${d(deadline)}(분기말+45일) 뒤 자동 수집`);
}

async function loadFilers() {
  // fetchJson 은 봉투째 준다 — as_of 가 응답마다 실려 오기 때문이다(불변식 9).
  const { filers } = (await fetchJson("thirteen-f/filers")).data;
  if (!filers.length) {
    document.getElementById("filers").innerHTML =
      `<div class="empty">13F 를 아직 한 건도 안 받았다 —
       <code>tools/collect_13f.py</code> 로 들어온다.</div>`;
    return;
  }

  drawFilersChart(filers);
  const worst = Math.max(...filers.map((f) => f.lag_days));
  document.getElementById("kpis").innerHTML = [
    kpi("따라보는 기관", num(filers.length), "SEC 13F-HR 신고 기준"),
    kpi("합산 신고 규모", usdB(filers.reduce((a, f) => a + f.total_usd, 0)),
        "각 기관의 최신 분기"),
    // **낡음을 KPI 로 올린다.** 화면에서 제일 중요한 사실이다.
    kpi("가장 낡은 신고", `${worst}일`, "분기말 → 공개까지", worst >= 45),
    kpi("최신 기준일", latestReport(filers), "지금이 아니다 — 분기말 기준"),
    nextUpdateKpi(latestReport(filers)),
  ].join("");

  document.getElementById("filers").innerHTML = `
  <div class="scroll"><table class="dense">
    <thead><tr>
      <th>기관</th><th>기준일</th><th class="num">지연</th>
      <th class="num">종목</th><th class="num">규모</th><th class="num">분기</th>
    </tr></thead>
    <tbody>${filers.map((f) => `
      <tr class="clicky${f.filer_cik === SELECTED ? " on" : ""}"
          data-cik="${f.filer_cik}">
        <td>${f.filer_name}</td>
        <td class="num">${f.report_date}</td>
        <td class="num ${f.lag_days >= 45 ? "warn" : ""}">${f.lag_days}일</td>
        <td class="num">${num(f.holdings)}</td>
        <td class="num">${usdB(f.total_usd)}</td>
        <td class="num ${f.quarters < 2 ? "dim" : ""}">${f.quarters}</td>
      </tr>`).join("")}</tbody>
  </table></div>
  <p class="note">줄을 누르면 그 기관의 보유가 아래에 뜬다.
     <strong>분기가 1이면 변화를 못 잰다</strong> — 직전 분기가 있어야 증감이 나온다.</p>`;

  for (const row of document.querySelectorAll("#filers tr.clicky")) {
    row.addEventListener("click", () => {
      SELECTED = row.dataset.cik;
      loadFilers();
      loadHoldings(SELECTED);
    });
  }
}

async function loadHoldings(cik) {
  const query = cik ? `thirteen-f/holdings?cik=${encodeURIComponent(cik)}` : "thirteen-f/holdings";
  const data = (await fetchJson(query)).data;
  SELECTED = data.filer_cik || SELECTED;

  const target = document.getElementById("holdings");
  if (!data.rows || !data.rows.length) {
    target.innerHTML = `<div class="empty">${data.note || "보유가 없다."}</div>`;
    return;
  }

  document.getElementById("holdings-title").innerHTML =
    `${data.filer_name} <span class="sub">${data.report_date} 기준 ·
     ${data.lag_days}일 뒤 공개 · ${num(data.holdings)}종목 ·
     원본 ${num(data.folded_total)}줄</span>`;

  drawHoldingsChart(data);
  const closed = data.closed && data.closed.length
    ? `<p class="note">직전 분기(${data.previous_date}) 대비 <strong>청산</strong>:
       ${data.closed.map((c) => `${c.issuer} (${usdB(c.value_usd)})`).join(" · ")}</p>`
    : "";

  target.innerHTML = `
  ${data.note ? `<div class="empty">${data.note}</div>` : ""}
  <div class="scroll"><table class="dense">
    <thead><tr>
      <th>종목</th><th class="num">비중</th><th class="num">평가액</th>
      <th class="num">주식수</th><th class="num">접힘</th>
      <th>직전 분기 대비</th>
    </tr></thead>
    <tbody>${data.rows.map((r) => `
      <tr>
        <td>${r.issuer}${r.ours ? `<span class="tf-ours">보유</span>` : ""}<br><span class="sub">${r.ticker || r.entity_id}</span></td>
        <td class="num">${pct(r.weight, 1)}</td>
        <td class="num">${usdB(r.value_usd)}</td>
        <td class="num">${num(Math.round(r.shares))}</td>
        <td class="num ${r.folded_rows > 1 ? "" : "dim"}">${r.folded_rows}</td>
        <td>${changeTag(r.change, r.change_pct)}</td>
      </tr>`).join("")}</tbody>
  </table></div>
  ${data.rest ? `<p class="note">그 외 ${num(data.rest)}종목은 접었다.</p>` : ""}
  ${closed}`;
}

async function loadConsensus() {
  const data = (await fetchJson("thirteen-f/consensus")).data;
  const rows = data.rows;
  const target = document.getElementById("consensus");
  if (rows.length) drawConsensusChart(rows, data);
  if (!rows.length) {
    target.innerHTML = `<div class="empty">둘 이상이 겹쳐 든 종목이 없다 —
      기관이 하나뿐이면 겹칠 수가 없다.</div>`;
    return;
  }
  target.innerHTML = `
  <div class="scroll"><table class="dense">
    <thead><tr>
      <th>종목</th><th class="num">기관 수</th><th class="num">직전</th><th class="num">합산 평가액</th>
      <th class="num">최대 비중</th><th>기관</th><th>기준일</th>
    </tr></thead>
    <tbody>${rows.map((r) => `
      <tr>
        <td>${r.issuer}${r.ours ? `<span class="tf-ours">보유</span>` : ""}<br><span class="sub">${r.ticker || r.entity_id}</span></td>
        <td class="num">${r.filers}</td>
        <td class="num ${r.filers_prev === null || r.filers_prev === undefined ? "dim" : ""}">${r.filers_prev === null || r.filers_prev === undefined ? "—" : r.filers_prev}</td>
        <td class="num">${usdB(r.total_usd)}</td>
        <td class="num">${pct(r.max_weight, 1)}</td>
        <td class="sub">${r.names.join(" · ")}</td>
        <td class="sub num">${r.dates.join(" / ")}</td>
      </tr>`).join("")}</tbody>
  </table></div>`;
}

runAll([loadFilers, () => loadHoldings(SELECTED), loadConsensus]);
