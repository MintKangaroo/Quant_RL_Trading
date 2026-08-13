/* 뉴스 · 일정 — 지금 무슨 일이 벌어지고 있고, 다음에 무엇이 오는가.
 *
 * 공통 규약은 scope.js 에 있다. 여기서는 이 화면 고유의 표현만 만든다.
 *
 * 이 화면은 숫자가 아니라 글을 보여주므로 두 가지를 특히 조심한다.
 *  1) 제목·기관명은 바깥에서 온 문자열이다. 반드시 이스케이프한다
 *  2) "실시간" 은 as_of 를 안 준 상태를 뜻한다. 과거를 조회 중이면
 *     자동 갱신을 멈춘다 — 타임머신을 켜 둔 채 화면이 현재로 흘러가면
 *     사람이 보고 있는 시점이 조용히 바뀐다
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

/** 발표까지 남은 시간. 음수면 지났다는 뜻이다. */
function countdown(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const past = seconds < 0;
  let left = Math.abs(seconds);
  const days = Math.floor(left / 86400);
  left -= days * 86400;
  const hours = Math.floor(left / 3600);
  const mins = Math.floor((left - hours * 3600) / 60);
  const parts = days ? `${days}일 ${hours}시간` : hours ? `${hours}시간 ${mins}분` : `${mins}분`;
  return past ? `${parts} 전` : `${parts} 후`;
}

/** 시장 코드 → 사람이 읽는 이름. 화면에 KR/US 를 그대로 두면 국장·미장을
 *  같이 보는 화면에서 매번 머릿속으로 번역하게 된다. */
const MARKET_NAME = { KR: "한국", US: "미국" };
const market = (code) => MARKET_NAME[code] || code;

function stamp(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.valueOf())) return "—";
  return at.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

/** 실측값과 직전값의 변화.
 *
 * **색을 쓰지 않는다.** 설계 규칙이 "색은 손익과 경고에만, 나머지는 무채색"
 * 이고(dashboard.md §10), 거시지표의 전월대비 변화는 손익이 아니다.
 *
 * 그리고 부호에 좋고 나쁨을 입힐 수도 없다 — 실업률 상승과 GDP 상승은 같은
 * 부호지만 반대 뜻이다. 방향은 부호로만 보여주고 해석은 사람에게 맡긴다.
 */
function change(actual, previous) {
  if (actual === null || previous === null || actual === undefined || previous === undefined) {
    return "—";
  }
  const diff = actual - previous;
  return `${diff > 0 ? "+" : ""}${diff.toFixed(2)}`;
}

function releaseRows(items, { withActual }) {
  if (!items.length) {
    return `<p class="note">${withActual ? "발표된 지표가 없다." : "예정된 발표가 없다."}</p>`;
  }
  const head = withActual
    ? "<thead><tr><th>지표</th><th>발표</th><th class='num'>실측</th><th class='num'>직전</th><th class='num'>변화</th></tr></thead>"
    : "<thead><tr><th>지표</th><th>예정</th><th>남은 시간</th></tr></thead>";

  const now = Date.now();
  const body = items.map((item) => {
    // **한 줄로 유지한다.** 두 줄로 쓰면 행 높이가 지표마다 달라지고,
    // 오른쪽 숫자 열이 그 들쭉날쭉함을 따라가 표 전체가 어긋나 보인다.
    // 발표 원문 이름은 길어서 title 로 넘기고 화면에서는 줄임표로 자른다.
    const label = `<span class="lead" title="${esc(item.release_name)}">`
      + `<strong>${esc(item.indicator)}</strong>`
      + `<span class="chip chip-${esc(item.market.toLowerCase())}">`
      + `${esc(market(item.market))}</span>`
      + `<span class="sub trunc">${esc(item.release_name)}</span></span>`;
    if (withActual) {
      return `<tr>
        <td>${label}</td>
        <td class="num">${stamp(item.scheduled_at)}</td>
        <td class="num">${item.actual === null ? "—" : num(item.actual)}</td>
        <td class="num">${item.previous === null ? "—" : num(item.previous)}</td>
        <td class="num">${change(item.actual, item.previous)}</td>
      </tr>`;
    }
    const secs = Math.round((new Date(item.scheduled_at).valueOf() - now) / 1000);
    return `<tr>
      <td>${label}</td>
      <td class="num">${stamp(item.scheduled_at)}</td>
      <td class="num">${countdown(secs)}</td>
    </tr>`;
  }).join("");

  return `<table>${head}<tbody>${body}</tbody></table>`;
}

function newsRows(items) {
  if (!items.length) {
    return `<p class="note">뉴스가 없다. 수집기(<code>tools/collect_news.py</code>)를
      돌렸는지 확인할 것 — 비어 있는 것과 악재가 없는 것은 다르다.</p>`;
  }
  const rows = items.map((item) => {
    const tickers = item.entities
      .map((e) => `<span class="tag dim">${esc(e)}</span>`)
      .join(" ");
    const title = item.url
      ? `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>`
      : esc(item.title);
    return `<tr>
      <td class="num">${stamp(item.published_at)}</td>
      <td>${title}<div class="sub">${esc(item.filer)} ${tickers}</div></td>
    </tr>`;
  }).join("");
  // 머리글도 값과 같은 정렬이어야 한다. 값은 오른쪽인데 머리글만 왼쪽이면
  // 넓은 열에서 둘이 멀찍이 떨어져 서로 다른 열처럼 보인다.
  // thead 로 감싸야 스크롤 중에도 머리글이 붙어 있는다 (app.css .scroll).
  return `<table class="news"><thead><tr><th class="num">발행</th><th>제목</th></tr></thead>`
    + `<tbody>${rows}</tbody></table>`;
}

/** 시장 함의 배지. 지표의 좋고 나쁨이 아니라 **주식시장에 어떻게 읽히는가** 다 —
 *  고용 부진이 금리 기대를 낮춰 호재로 읽히면 risk_on 이다. */
const TONE = {
  risk_on: ["위험선호", "tone-on"], risk_off: ["위험회피", "tone-off"],
  mixed: ["엇갈림", "tone-mix"], neutral: ["중립", "tone-flat"],
};

function explainRows(items) {
  const done = items.filter((i) => i.brief);
  if (!done.length) {
    return `<p class="note">해설이 없다. ANTHROPIC_API_KEY 를 확인할 것 —
      숫자는 그대로 남지만 해석은 붙지 않는다.</p>`;
  }
  const rows = done.map((item) => {
    const [label, cls] = TONE[item.brief.tone] || TONE.neutral;
    const r = item.reaction;
    const reaction = r && r.change_pct !== null
      ? `${esc(r.index)} ${r.change_pct > 0 ? "+" : ""}${r.change_pct.toFixed(2)}%`
      : "반응 미측정";
    const sp = item.surprise;
    const surprise = sp.diff === null ? "—"
      : `${sp.diff > 0 ? "+" : ""}${sp.diff}${sp.pct === null ? "" : ` (${sp.pct}%)`}`;
    return `<tr>
      <td><span class="lead">
        <strong>${esc(item.indicator)}</strong>
        <span class="chip chip-${esc(item.market.toLowerCase())}">${esc(market(item.market))}</span>
        <span class="chip ${cls}">${esc(label)}</span>
      </span></td>
      <td class="num">${surprise}</td>
      <td class="num">${esc(reaction)}</td>
      <td>${esc(item.brief.headline)}<div class="sub">${esc(item.brief.reading)}</div></td>
    </tr>`;
  }).join("");
  return `<table><thead><tr><th>지표</th><th class="num">직전 대비</th>`
    + `<th class="num">시장 반응</th><th>해석</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function renderExplain() {
  const body = await fetchJson("briefing/explain");
  document.getElementById("explain").innerHTML = explainRows(body.data.releases);
}

async function render() {
  const body = await fetchJson("briefing/summary");
  const d = body.data;
  showScope(body);

  document.getElementById("next").innerHTML = d.next
    ? `<div class="kpis">${[
        kpi("지표", esc(d.next.indicator), esc(d.next.release_name)),
        kpi("발표 시각", stamp(d.next.scheduled_at), esc(market(d.next.market))),
        kpi("남은 시간", countdown(d.next.in_seconds), "as_of 기준", d.next.in_seconds < 3600),
      ].join("")}</div>`
    : `<p class="note">예정된 발표가 없다.</p>`;

  document.getElementById("released").innerHTML = releaseRows(d.released, { withActual: true });
  document.getElementById("upcoming").innerHTML = releaseRows(d.upcoming, { withActual: false });
  document.getElementById("news").innerHTML = newsRows(d.news);

  const warnings = [];
  if (!d.news.length) warnings.push("뉴스가 비어 있다 — 수집기를 확인할 것");
  if (!d.released.length && !d.upcoming.length) {
    warnings.push("거시지표가 비어 있다 — tools/collect_macro.py 를 확인할 것");
  }
  if (!d.released.concat(d.upcoming).some((item) => item.market === "KR")) {
    warnings.push("국장 지표가 없다 — ECOS(한국은행) 미연결");
  }
  showAlerts(warnings);
}

runAll([render, renderExplain]);

// 라이브일 때만 갱신한다. 과거를 조회 중이면 화면이 현재로 흘러가면 안 된다.
if (isLive()) {
  window.setInterval(() => runAll([render, renderExplain]), REFRESH_MS);
}
