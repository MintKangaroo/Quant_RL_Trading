/* 표 → 카드 (폰) — 전 탭 공통 (2026-10-06).
 *
 * 640px 아래에서 .scroll 은 가로 스와이프를 막는다(사용자 요청 2026-08-28). 그래서 열이 많은
 * 표는 오른쪽이 **잘려서 안 보였다** — 10/6 폰 점검에서 열한 탭 중 여덟 탭이 그랬다.
 * 컨테이너에 `mcard` 를 달면 768px 아래에서 줄마다 카드가 된다(app.css): 첫 칸이 제목 줄,
 * 나머지 칸은 "머리글 값" 으로 그 밑에 흘러 쌓인다. 머리글은 숨겨지므로 칸마다 자기 머리글을
 * `data-l` 로 들고 있어야 한다 — 그걸 여기서 단다. 표는 JS 가 fetch 뒤에 그리고 60초마다
 * 다시 그리므로 본문이 바뀔 때마다 다시 단다(MutationObserver, glossary.js 와 같은 묶음 방식).
 *
 * 긴 글 칸(사유·요약·상세)은 한 줄을 통째로 쓰게 `mc-long` 을 단다 — 안 그러면 짧은 숫자
 * 칸들 사이에 끼어 카드가 들쭉날쭉해진다.
 */
(function () {
  "use strict";
  var LONG = 28;  // 글자 수 — 이보다 긴 칸은 제목 밑 한 줄을 통째로 쓴다(모양 기준이지 매매 임계가 아니다)

  function labels(table) {
    var head = table.tHead && table.tHead.rows[0];
    if (!head) {
      var first = table.rows[0];
      if (!first || first.querySelector("td")) return null;
      head = first;
    }
    var out = [];
    for (var i = 0; i < head.cells.length; i++) {
      var th = head.cells[i];
      var text = th.textContent.replace(/\s+/g, " ").trim();
      for (var k = 0; k < (th.colSpan || 1); k++) out.push(text);
    }
    return out;
  }

  function annotate() {
    var tables = document.querySelectorAll(".mcard table");
    for (var t = 0; t < tables.length; t++) {
      var names = labels(tables[t]);
      if (!names) continue;
      var bodies = tables[t].tBodies;
      for (var b = 0; b < bodies.length; b++) {
        var rows = bodies[b].rows;
        for (var r = 0; r < rows.length; r++) {
          var col = 0;
          for (var c = 0; c < rows[r].cells.length; c++) {
            var td = rows[r].cells[c];
            // 여러 칸을 덮는 칸(빈 상태 문장·소제목 줄)은 머리글이 없다.
            var label = td.colSpan > 1 ? "" : (names[col] || "");
            if (td.getAttribute("data-l") !== label) td.setAttribute("data-l", label);
            var long = td.textContent.trim().length > LONG;
            if (long !== td.classList.contains("mc-long")) td.classList.toggle("mc-long", long);
            col += td.colSpan || 1;
          }
        }
      }
    }
  }

  var timer = null;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () { timer = null; annotate(); }, 120);
  }

  function start() {
    annotate();
    // 속성만 바꾸므로 childList 감시가 자기 자신을 다시 부르지 않는다.
    if (window.MutationObserver) {
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
