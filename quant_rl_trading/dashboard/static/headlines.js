/* 중요 시황 — 오늘·이달 일정과 중요 공시.
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
  // 'YYYY-MM-DD HH:mm' 한국 시간 고정(scope.js kstStamp, 2026-10-13 밤) — 예전엔 보는 기기 시각이었다.
  // 머리글이 'KST' 를 든다.
  return kstStamp(iso);
}

const DOC_TYPE_NAME = {
  distress: "상장폐지사유", dilution: "유상증자·감자", split: "액면분할",
  contract: "계약", dividend: "배당", buyback: "자사주", news: "뉴스",
};

/* 매수 차단(News·SNS 판정)은 매매 과정 탭 ③ 거르기 옆으로, 파이프라인 이벤트는 시스템 탭으로 옮겼다
 * (사용자 2026-10-13 밤 "그 탭과 상관없다") — 이 탭은 일정과 중요 공시만. */

/* KPI — 오늘 일정 수는 일정 달력(schedule.js)이 받은 응답에서 센다. 오늘 = as_of 의 서울 날짜(서버 today). */
let headlinesDocs = null;
let headlinesToday = null;   // {day, count} — 달을 넘겨 본 뒤에도 오늘 수는 오늘 달 응답에서 센 값 그대로
function renderKpis() {
  const target = document.getElementById("kpis");
  if (!target) return;
  const sched = typeof schedLastData !== "undefined" ? schedLastData : null;
  const today = sched ? schedToday(sched) : null;
  if (sched && today && (!sched.month || today.startsWith(sched.month))) {
    headlinesToday = { day: today, count: (sched.days[today] || []).length };
  }
  const todayItems = headlinesToday ? { length: headlinesToday.count } : null;
  const monthCount = sched && sched.counts ? sched.counts.macro + sched.counts.earnings : null;
  target.innerHTML = [
    kpi("오늘 일정", todayItems === null ? "—" : num(todayItems.length),
        headlinesToday ? `${headlinesToday.day} (KST) · 지표·실적 발표` : "일정을 못 읽었다"),
    kpi("이 화면 일정", monthCount === null ? "—" : num(monthCount),
        sched ? (sched.month ? `${sched.month} 한 달` : `${sched.from}~${sched.to}`) : "—"),
    kpi("중요 공시·뉴스", headlinesDocs === null ? "—" : num(headlinesDocs), "창 안 기준"),
  ].join("");
}

function documentRows(rows) {
  if (!rows.length) {
    return `<p class="empty">중요 공시·뉴스가 없다. tools/collect_disclosures.py 를 확인할 것 —
      비어 있는 것과 중요한 일이 없는 것은 다르다.</p>`;
  }
  const head = `<thead><tr><th class="num">발행 KST</th><th>제목</th></tr></thead>`;
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

async function loadHeadlines() {
  const body = await fetchJson("headlines");
  showScope(body);
  const d = body.data;
  headlinesDocs = d.documents.length;
  document.getElementById("documents-count").textContent = `${d.documents.length}건`;
  document.getElementById("documents").innerHTML = documentRows(d.documents);
  // 공시 유형별 건수 — 아래 표에 실린 것(최근 상위)만 센다(§8-5, 세기만).
  const byType = {};
  for (const doc of d.documents) byType[doc.doc_type] = (byType[doc.doc_type] || 0) + 1;
  hbarChart("hl-doc-chart", Object.entries(byType).sort((a, b) => b[1] - a[1])
    .map(([type, n]) => ({ label: DOC_TYPE_NAME[type] || type, value: n })), { format: (v) => `${v}건`, min: 70 });
  showAlerts([]);
}

runAll([loadHeadlines, () => loadSchedule(schedMonth), renderKpis]);

if (isLive()) {
  window.setInterval(() => runAll([loadHeadlines, renderKpis]), REFRESH_MS);
}
