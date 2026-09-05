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

async function loadFilers() {
  // fetchJson 은 봉투째 준다 — as_of 가 응답마다 실려 오기 때문이다(불변식 9).
  const { filers } = (await fetchJson("thirteen-f/filers")).data;
  if (!filers.length) {
    document.getElementById("filers").innerHTML =
      `<div class="empty">13F 를 아직 한 건도 안 받았다 —
       <code>tools/collect_13f.py</code> 로 들어온다.</div>`;
    return;
  }

  const worst = Math.max(...filers.map((f) => f.lag_days));
  document.getElementById("kpis").innerHTML = [
    kpi("따라보는 기관", num(filers.length), "SEC 13F-HR 신고 기준"),
    kpi("합산 신고 규모", usdB(filers.reduce((a, f) => a + f.total_usd, 0)),
        "각 기관의 최신 분기"),
    // **낡음을 KPI 로 올린다.** 화면에서 제일 중요한 사실이다.
    kpi("가장 낡은 신고", `${worst}일`, "분기말 → 공개까지", worst >= 45),
    kpi("최신 기준일", filers[0].report_date, "지금이 아니다"),
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
        <td>${r.issuer}<br><span class="sub">${r.entity_id}</span></td>
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
  const { rows } = (await fetchJson("thirteen-f/consensus")).data;
  const target = document.getElementById("consensus");
  if (!rows.length) {
    target.innerHTML = `<div class="empty">둘 이상이 겹쳐 든 종목이 없다 —
      기관이 하나뿐이면 겹칠 수가 없다.</div>`;
    return;
  }
  target.innerHTML = `
  <div class="scroll"><table class="dense">
    <thead><tr>
      <th>종목</th><th class="num">기관 수</th><th class="num">합산 평가액</th>
      <th class="num">최대 비중</th><th>기관</th><th>기준일</th>
    </tr></thead>
    <tbody>${rows.map((r) => `
      <tr>
        <td>${r.issuer}<br><span class="sub">${r.entity_id}</span></td>
        <td class="num">${r.filers}</td>
        <td class="num">${usdB(r.total_usd)}</td>
        <td class="num">${pct(r.max_weight, 1)}</td>
        <td class="sub">${r.names.join(" · ")}</td>
        <td class="sub num">${r.dates.join(" / ")}</td>
      </tr>`).join("")}</tbody>
  </table></div>`;
}

runAll([loadFilers, () => loadHoldings(SELECTED), loadConsensus]);
