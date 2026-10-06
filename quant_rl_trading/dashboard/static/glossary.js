/* 용어 풀이 — 전 탭 공통 (2026-09-29).
 *
 * 사용자는 전문가가 아니다. IC·MDD·TWR·반영률 같은 말이 KPI 라벨·표 머리글·패널 제목에
 * 풀이 없이 서 있었다. 화면마다 문장을 덧붙이면 시트가 설명서가 되므로(DESIGN.md: 설명 문단은
 * [설명] 뒤로), 말 자체에 점선 밑줄을 긋고 풀이는 마우스를 올리면(title) · 손가락으로 누르면
 * (화면 아래 한 줄) 보이게 한다.
 *
 * **풀이는 사실만 적는다.** 숫자 기준값(IC 합격선 0.03, 반영률 30% 등)은 store.config 에서
 * 오는 값이라 여기 적지 않는다 — 적으면 설정이 바뀌어도 풀이는 옛 값을 말한다(불변식 10).
 *
 * 화면은 JS 가 나중에 그리므로(fetch 뒤 innerHTML) 한 번 훑고 끝나지 않는다 — 본문이 바뀌면
 * 잠깐 기다렸다 다시 훑는다(MutationObserver, 250ms 묶음).
 */
(function () {
  "use strict";

  // [말, 풀이]. 영문 약어는 단어 경계로, 한글은 포함 여부로 찾는다.
  var TERMS = [
    ["IC", "적중도 — 점수 순위와 며칠 뒤 수익 순위가 얼마나 맞았나(0 = 아무 관계 없음). 합격선을 넘어야 매매에 쓴다."],
    ["AI 신호", "목표 비중 대비 지금 보유 — BUY 덜 들고 있어 더 사는 중 · HOLD 목표만큼 보유 · TRIM 목표보다 많아 줄이는 중 · SELL 목표에서 빠져 파는 중. 목표는 직전 재조정이 정했다."],
    ["MDD", "최대 낙폭 — 고점에서 가장 크게 떨어진 비율."],
    ["TWR", "시간가중수익률 — 입금·출금 영향을 뺀 수익률. 돈을 넣은 날이 수익으로 잡히지 않는다."],
    ["반영률", "액션 반영률 — AI(RL)가 낸 결정 중 실제로 집행된 비율. 낮으면 AI 가 아니라 안전 규칙이 매매를 정하고 있다는 뜻이다."],
    ["체결율", "주문 체결율 — 증권사에 실제로 나간 주문 수량 중 체결된 비율(건수 기준은 한 주라도 체결된 조각의 비율). 예약만 된 주문·안전장치가 막은 주문·휴장일 거부는 빼고, 나갔다가 거절된 주문은 넣는다. 오늘 전송이 없으면 — 로 적는다."],
    ["익스포저","주식에 들어가 있는 돈의 비율. 나머지는 현금이다."],
    ["낙폭", "고점 대비 지금 얼마나 내려와 있나."],
    ["킬스위치", "비상 정지 장치 — 켜지면 신규 매수를 막는다. 매도는 막지 않는다."],
    ["대사", "우리 장부와 증권사 계좌를 맞춰 보는 것 — 수량·현금이 같은지 대조한다."],
    ["shadow", "돈이 오가지 않는 시뮬레이션 장부 — 실제 주문 없이 같은 규칙으로 매매를 기록한다."],
    ["RSI", "상대강도지수(14일) — 최근 오른 폭과 내린 폭의 비율. 흔히 70 이상은 과열, 30 이하는 침체로 읽는다."],
    ["13F", "운용자산 1억 달러 이상 미국 기관이 분기말 보유 종목을 45일 안에 내는 공시. 과거 자료다."],
    ["p50", "중앙값 — 절반은 이보다 빠르다."],
    ["p90", "90번째 백분위 — 열 번 중 아홉 번은 이보다 빠르다."],
    ["p99", "99번째 백분위 — 백 번 중 한 번 나오는 느린 경우."],
    ["Analyst", "종목마다 점수를 내는 분석 에이전트(차트·공시·재무·수급 등). 적중도(IC)가 합격선을 넘어야 가중치를 받는다."],
    ["가중치", "Analyst 점수를 합칠 때 곱하는 비중. 0 이면 관찰만 하고 매매에는 쓰지 않는다."],
    ["결측", "값이 빠진 것. 결측률 = 받아야 할 값 중 빠진 비율."],
    ["커버리지", "받아야 할 거래일 중 실제로 자료를 받은 날의 비율."],
    ["랭커", "여러 재료를 한꺼번에 보고 종목 순위를 매기는 모델(GBM). 지금 매매에 쓰는 점수다."],
    ["홀드아웃", "판정용으로 아무도 안 본 채 남겨 둔 기간 — 결과를 보고 규칙을 고치는 과적합을 막는다."],
    ["NAV", "순자산 — 현금 + 보유 주식 평가액."],
    ["LLM", "대형 언어 모델(Claude) — 뉴스·공시 판정과 해설에 쓴다. 보상·점수 계산에는 들어가지 않는다."],
    ["GBM", "그래디언트 부스팅 — 결정나무를 여러 개 쌓아 예측하는 기계학습 모델."],
  ];
  var SELECTOR = [
    ".kpi-label", "th", ".panel h2", ".panel h3", ".risk-row .name", ".control-state > span",
    ".decision-action .k", ".air-kpi .k", ".air-h", ".sys-card h3", ".perf-label", ".cost-item > span",
    ".ls-zone-head h2", ".index-head", ".desk-heading h2",
  ].join(",");

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  var MATCHERS = TERMS.map(function (t) {
    var ascii = /^[A-Za-z0-9_]+$/.test(t[0]);
    var re = ascii ? new RegExp("(^|[^A-Za-z0-9_])" + escapeRe(t[0]) + "(?![A-Za-z0-9_])") : null;
    return { term: t[0], text: t[1], test: ascii ? function (s) { return re.test(s); } : function (s) { return s.indexOf(t[0]) !== -1; } };
  });

  function explain(text) {
    var hits = [];
    for (var i = 0; i < MATCHERS.length; i++) {
      var m = MATCHERS[i];
      if (m.test(text)) {
        // '낙폭' 은 'MDD' 풀이와 겹친다 — 둘 다 걸리면 긴 쪽 하나만.
        if (m.term === "낙폭" && hits.some(function (h) { return h.term === "MDD"; })) continue;
        hits.push(m);
      }
    }
    return hits;
  }

  function scan() {
    var nodes = document.querySelectorAll(SELECTOR);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      // 머리글 안의 부제(.sub)·버튼까지 읽으면 '… · TWR 기준' 같은 부제가 제목에 풀이를 단다.
      // 요소 자신의 글자만 본다(자식 요소 글자 제외).
      var own = "";
      for (var c = el.firstChild; c; c = c.nextSibling) if (c.nodeType === 3) own += c.nodeValue;
      if (!own.trim()) own = el.children.length ? "" : el.textContent;
      if (el.dataset.termFor === own) continue;
      el.dataset.termFor = own;
      var hits = explain(own);
      if (!hits.length) {
        if (el.classList.contains("term")) { el.classList.remove("term"); el.removeAttribute("data-term-tip"); }
        continue;
      }
      var tip = hits.map(function (h) { return h.term + ": " + h.text; }).join("\n");
      el.classList.add("term");
      el.setAttribute("data-term-tip", tip);
      if (!el.getAttribute("title")) el.setAttribute("title", tip);
    }
  }

  var pending = null;
  function schedule() {
    if (pending) return;
    pending = setTimeout(function () { pending = null; scan(); }, 250);
  }

  // 손가락: 누르면 화면 아래에 풀이가 4초 뜬다. hover 가 있는 기기는 title 이 한다.
  var tipEl = null, tipTimer = null;
  function showTip(text) {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.className = "term-tip";
      tipEl.setAttribute("role", "status");
      tipEl.addEventListener("click", function () { tipEl.hidden = true; });
      document.body.appendChild(tipEl);
    }
    tipEl.innerHTML = "";
    text.split("\n").forEach(function (line, i) {
      var idx = line.indexOf(": ");
      var row = document.createElement("div");
      var b = document.createElement("b");
      b.textContent = line.slice(0, idx);
      row.appendChild(b);
      row.appendChild(document.createTextNode(line.slice(idx + 2)));
      if (i) row.style.marginTop = "6px";
      tipEl.appendChild(row);
    });
    tipEl.hidden = false;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(function () { tipEl.hidden = true; }, 4500);
  }
  var touch = window.matchMedia && window.matchMedia("(hover: none)").matches;
  if (touch) {
    document.addEventListener("click", function (e) {
      var el = e.target.closest && e.target.closest(".term");
      if (el && el.dataset.termTip) showTip(el.dataset.termTip);
    });
  }

  function start() {
    scan();
    if (window.MutationObserver) {
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
