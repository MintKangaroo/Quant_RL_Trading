/* 중요 시황 — 지금 시장에서 우리와 관련해 무슨 일이 벌어졌나.
 *
 * 공통 규약은 scope.js 에 있다. 뉴스·일정 탭과 같은 이유로 60초 폴링이고,
 * as_of 가 있으면 갱신을 멈춘다 — 타임머신을 켠 채 화면이 현재로 흘러가면
 * 사람이 보고 있는 시점이 조용히 바뀐다.
 */

const REFRESH_MS = 60_000;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function isLive() {
  return !new URLSearchParams(window.location.search).get("as_of");
}

function stamp(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.valueOf())) return "—";
  return at.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

const DOC_TYPE_NAME = {
  distress: "상장폐지사유", dilution: "유상증자·감자", split: "액면분할",
  contract: "계약", dividend: "배당", buyback: "자사주", news: "뉴스",
};

const CATEGORY_NAME = {
  dilution: "희석", insider_sell: "내부자매도", delisting: "상장폐지",
  litigation: "소송", trading_halt: "거래정지", accounting: "회계",
};

function renderKpis(body) {
  const d = body.data;
  document.getElementById("kpis").innerHTML = [
    kpi("매수 차단 중", num(d.active_verdicts), `전체 기록 ${num(d.verdicts.length)}건`,
        d.active_verdicts > 0),
    kpi("중요 공시·뉴스", num(d.documents.length), "창 안 기준"),
    kpi("파이프라인 이벤트", num(d.events.length), "관측→체결 판단 로그"),
  ].join("");
}

function verdictRows(rows) {
  if (!rows.length) return `<p class="empty">기록된 판정이 없다.</p>`;
  const head = `<thead><tr><th>종목</th><th class="mid">Analyst</th><th>사유</th>
    <th class="num">발동</th><th class="num">만료</th><th class="mid">상태</th></tr></thead>`;
  const body_ = rows
    .map(
      (row) => `<tr>
        <td><span class="name">${esc(row.name)}</span><span class="code">${esc(row.entity_id)}</span></td>
        <td class="mid"><span class="tag dim">${esc(row.analyst)}</span></td>
        <td>${esc(CATEGORY_NAME[row.category] || row.category)}
            <div class="sub trunc" title="${esc(row.reason)}">${esc(row.reason)}</div></td>
        <td class="num">${stamp(row.valid_from)}</td>
        <td class="num">${stamp(row.expires_at)}</td>
        <td class="mid"><span class="verdict ${row.active ? "critical" : "info"}">
          ${row.active ? "차단 중" : "만료"}</span></td>
      </tr>`
    )
    .join("");
  return `<table>${head}<tbody>${body_}</tbody></table>`;
}

function documentRows(rows) {
  if (!rows.length) {
    return `<p class="empty">중요 공시·뉴스가 없다. tools/collect_disclosures.py 를 확인할 것 —
      비어 있는 것과 중요한 일이 없는 것은 다르다.</p>`;
  }
  const head = `<thead><tr><th class="num">발행</th><th>제목</th></tr></thead>`;
  const body_ = rows
    .map((item) => {
      const tags = item.entities
        .map((e, i) => `<span class="tag dim" title="${esc(item.entity_names[i])}">${esc(e)}</span>`)
        .join(" ");
      const title = item.url
        ? `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>`
        : esc(item.title);
      return `<tr>
        <td class="num">${stamp(item.published_at)}</td>
        <td><span class="chip dim">${esc(DOC_TYPE_NAME[item.doc_type] || item.doc_type)}</span>
            ${title}<div class="sub">${esc(item.filer)} ${tags}</div></td>
      </tr>`;
    })
    .join("");
  return `<table class="news">${head}<tbody>${body_}</tbody></table>`;
}

function eventRows(rows) {
  if (!rows.length) {
    return `<p class="empty">이 창고에 파이프라인 이벤트가 없다. 백테스트·라이브 run 이
      이 창고에 events 를 남기면 관측→체결까지의 판단이 여기 쌓인다.</p>`;
  }
  const head = `<thead><tr><th class="num">시각</th><th>run</th><th class="mid">단계</th>
    <th>주체</th></tr></thead>`;
  const body_ = rows
    .map(
      (row) => `<tr>
        <td class="num mono">${stamp(row.ts_sim)}</td>
        <td class="code">${esc(row.run_id)}</td>
        <td class="mid"><span class="tag dim">${esc(row.stage)}</span></td>
        <td>${esc(row.actor)}</td>
      </tr>`
    )
    .join("");
  return `<table>${head}<tbody>${body_}</tbody></table>`;
}

async function loadHeadlines() {
  const body = await fetchJson("headlines");
  showScope(body);
  const d = body.data;

  document.getElementById("verdicts-count").textContent = `${d.verdicts.length}건`;
  document.getElementById("documents-count").textContent = `${d.documents.length}건`;
  document.getElementById("events-count").textContent = `${d.events.length}건`;

  renderKpis(body);
  document.getElementById("verdicts").innerHTML = verdictRows(d.verdicts);
  document.getElementById("documents").innerHTML = documentRows(d.documents);
  document.getElementById("events").innerHTML = eventRows(d.events);

  const warnings = [];
  if (d.active_verdicts > 0) warnings.push(`${d.active_verdicts}개 종목이 매수 차단 중이다`);
  showAlerts(warnings);
}

runAll([loadHeadlines, () => loadSchedule(schedMonth)]);

if (isLive()) {
  window.setInterval(() => runAll([loadHeadlines]), REFRESH_MS);
}
