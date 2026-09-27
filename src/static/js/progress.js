/*
 * progress.js: the live half of /manage/{slug}/progress.
 *
 * The dashboard is server-rendered so it is useful with scripting off; this file
 * only re-reads the progress API every 15 seconds (BUILD-SPEC section 11) and
 * patches the numbers that are already on the page. If the data has moved on in a
 * way the page cannot patch — a judge or a project it has never seen — it reloads
 * instead of showing a page that is quietly wrong.
 *
 * Reads only: no write ever travels through this file.
 */
(function () {
  "use strict";

  var DEFAULT_INTERVAL = 15000;
  var STATUS_BADGES = {
    "not started": ["Not started", "status-not-started"],
    "in progress": ["In progress", "status-in-progress"],
    "done": ["Done", "status-done"],
    "no assignments": ["No assignments", "status-closed"]
  };

  function root() {
    return document.querySelector("[data-progress-api]");
  }

  function endpoint() {
    var node = root();
    return node ? node.getAttribute("data-progress-api") : "";
  }

  function interval() {
    var node = root();
    var value = node ? parseInt(node.getAttribute("data-progress-interval"), 10) : NaN;
    return isNaN(value) || value < 1000 ? DEFAULT_INTERVAL : value;
  }

  function setText(selector, value) {
    var node = document.querySelector(selector);
    if (node) {
      node.textContent = String(value);
    }
  }

  function badge(status) {
    return STATUS_BADGES[status] || [status, "status-closed"];
  }

  /* The same one-decimal shape the server rendered, so a patched tile does not
     change shape halfway through a session. */
  function percent(submitted, target) {
    return target > 0 ? Math.round((1000 * submitted) / target) / 10 : 100;
  }

  /* --- patching -------------------------------------------------------- */

  function patchJudge(row) {
    var node = document.querySelector('[data-progress-judge="' + row.judge + '"]');
    if (!node) {
      return false;
    }
    ["assigned", "submitted", "drafts", "remaining"].forEach(function (field) {
      var cell = node.querySelector('[data-field="' + field + '"]');
      if (cell) {
        cell.textContent = String(row[field]);
      }
    });
    var status = node.querySelector("[data-progress-judge-status]");
    if (status) {
      var label = badge(row.status);
      status.textContent = label[0];
      status.className = "status-badge " + label[1];
    }
    return true;
  }

  function patchProject(row) {
    var node = document.querySelector('[data-progress-project="' + row.project + '"]');
    if (!node) {
      return false;
    }
    ["submitted", "target"].forEach(function (field) {
      var cell = node.querySelector('[data-field="' + field + '"]');
      if (cell) {
        cell.textContent = String(row[field]);
      }
    });
    var bar = node.querySelector("[data-progress-bar]");
    if (bar) {
      bar.setAttribute("aria-valuemax", String(row.target));
      bar.setAttribute("aria-valuenow", String(row.submitted));
      var fill = bar.querySelector(".progress-bar");
      if (fill) {
        fill.style.width = percent(row.submitted, row.target) + "%";
      }
    }
    return true;
  }

  function patchTotals(data) {
    var submitted = 0;
    var target = 0;
    var underCovered = 0;
    var projects = 0;
    data.projects.forEach(function (row) {
      submitted += row.submitted;
      target += row.target;
      projects += 1;
      if (row.under_covered) {
        underCovered += 1;
      }
    });
    var done = 0;
    data.judges.forEach(function (row) {
      if (row.status === "done") {
        done += 1;
      }
    });
    setText("[data-progress-submitted]", submitted);
    setText("[data-progress-target]", target);
    setText("[data-progress-total]", percent(submitted, target) + "%");
    setText("[data-progress-judges]", data.judges.length);
    setText("[data-progress-judges-done]", done);
    setText("[data-progress-projects]", projects);
    setText("[data-progress-under]", underCovered);
    setText("[data-progress-stamp]", "updated " + submitted + " of " + target + " reviews");
  }

  function apply(data) {
    var known = data.judges.every(patchJudge) && data.projects.every(patchProject);
    if (!known) {
      // A judge or a project appeared that this page does not have a row for.
      window.location.reload();
      return;
    }
    patchTotals(data);
  }

  /* --- polling --------------------------------------------------------- */

  function refresh() {
    var url = endpoint();
    if (!url || !window.fetch) {
      return;
    }
    window.fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (data && data.judges && data.projects) {
          apply(data);
        }
      })
      .catch(function () {
        // A dashboard that is briefly stale beats an error banner; the next tick
        // in 15 seconds tries again.
      });
  }

  function start() {
    if (!root() || !window.fetch) {
      return;
    }
    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-progress-refresh]")) {
        refresh();
      }
    });
    window.setInterval(refresh, interval());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
