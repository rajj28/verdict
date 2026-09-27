/*
 * submission-editor.js: the required-field checklist on /events/{slug}/submission.
 *
 * Each row in the checklist is [data-check="<field>"] and the control it watches
 * carries data-check-source="<field>". This file only marks what is filled in as
 * the writer types; the submit endpoint re-checks the same list server side, so a
 * green row here is a convenience, never the control (BUILD-SEC section 2).
 */
(function () {
  "use strict";

  function value(node) {
    if (!node) {
      return "";
    }
    if (node.type === "checkbox") {
      return node.checked ? "yes" : "";
    }
    return (node.value || "").trim();
  }

  function paintRow(row) {
    var name = row.getAttribute("data-check");
    var badge = row.querySelector("[data-check-badge]");
    if (!badge) {
      return;
    }
    var source = document.querySelector('[data-check-source="' + CSS.escape(name) + '"]');
    var done = value(source).length > 0;
    badge.textContent = done ? "Done" : "Missing";
    badge.classList.remove("status-open", "status-closed");
    badge.classList.add(done ? "status-open" : "status-closed");
  }

  function paint() {
    var rows = document.querySelectorAll("[data-check]");
    Array.prototype.forEach.call(rows, paintRow);
  }

  function start() {
    paint();
    document.addEventListener("input", paint);
    document.addEventListener("change", paint);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.verdictSubmission = { paint: paint };
})();
