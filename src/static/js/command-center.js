/*
 * command-center.js: the judging command center, refreshed every 30 s.
 *
 * The server renders the first payload (so the page is useful with JS off and
 * never shows an empty table); this file then re-reads the same read-only
 * endpoint and repaints the stat tiles, the forecast table with its timeline
 * bar per judge, and the rebalance proposal. Writes are not here: the rebalance
 * buttons are data-api-* controls handled by api-forms.js.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-command-center]");
  if (!root) {
    return;
  }

  var url = root.getAttribute("data-api-url") || "";
  var seconds = parseInt(root.getAttribute("data-refresh-seconds") || "30", 10);
  var state = { payload: readInitial() };

  function readInitial() {
    var raw = root.getAttribute("data-payload");
    if (!raw) {
      return null;
    }
    try {
      return JSON.parse(raw);
    } catch (error) {
      return null;
    }
  }

  /* --- helpers --------------------------------------------------------- */

  function slot(name) {
    return root.querySelector('[data-cc="' + name + '"]');
  }

  function text(name, value) {
    var node = slot(name);
    if (node) {
      node.textContent = value;
    }
  }

  function cell(row, value, className) {
    var td = document.createElement("td");
    td.textContent = value;
    if (className) {
      td.className = className;
    }
    row.appendChild(td);
    return td;
  }

  function timeCell(row, iso, fallback) {
    var td = document.createElement("td");
    if (!iso) {
      td.textContent = fallback || "unknown";
    } else {
      var node = document.createElement("time");
      node.setAttribute("datetime", iso);
      td.appendChild(node);
    }
    row.appendChild(td);
    return td;
  }

  function pace(judge) {
    if (judge.pace_minutes === null || judge.pace_minutes === undefined) {
      return "no pace yet";
    }
    return judge.pace_minutes + " min/review";
  }

  function statusBadge(judge) {
    var span = document.createElement("span");
    span.className = "status-badge status-" + (judge.status || "").replace(/ /g, "-");
    span.textContent = judge.status || "";
    if (judge.at_risk) {
      span.setAttribute("title", (judge.reasons || []).join(", "));
    }
    return span;
  }

  function bar(judge) {
    var total = judge.assigned || 0;
    var done = judge.submitted || 0;
    var wrapper = document.createElement("div");
    wrapper.className = "progress";
    wrapper.setAttribute("role", "img");
    wrapper.setAttribute("aria-label", done + " of " + total + " reviews submitted");
    wrapper.style.height = "0.5rem";

    var finished = document.createElement("div");
    finished.className = "progress-bar";
    finished.style.width = (total ? (100 * done) / total : 100) + "%";
    wrapper.appendChild(finished);

    if (total > done) {
      var left = document.createElement("div");
      left.className = "progress-bar " + (judge.at_risk ? "bg-danger" : "bg-secondary");
      left.style.width = (100 * (total - done)) / total + "%";
      wrapper.appendChild(left);
    }

    var cellNode = document.createElement("td");
    cellNode.appendChild(wrapper);
    var small = document.createElement("div");
    small.className = "stat-sub";
    small.textContent = done + " / " + total;
    cellNode.appendChild(small);
    return cellNode;
  }

  /* --- sections -------------------------------------------------------- */

  function renderStats(payload) {
    var forecast = payload.forecast || {};
    var proposal = payload.proposal || {};
    var judges = forecast.judges || [];
    var atRisk = forecast.at_risk_count || 0;
    var left = judges.reduce(function (total, judge) {
      return total + (judge.remaining || 0);
    }, 0);

    text("projected-finish", forecast.projected_finish ? "" : "unknown");
    if (forecast.projected_finish) {
      var finish = document.createElement("time");
      finish.setAttribute("datetime", forecast.projected_finish);
      finish.setAttribute("data-countdown", "");
      finish.setAttribute("data-countdown-label", "finishes");
      var node = slot("projected-finish");
      node.textContent = "";
      node.appendChild(finish);
    }
    var close = forecast.judging_close_at;
    if (forecast.projected_finish && close) {
      // Parse both sides: ISO strings only compare as text when they share an
      // offset, and the server may render either.
      var finishesAt = Date.parse(forecast.projected_finish);
      var closesAt = Date.parse(close);
      text("finish-note", finishesAt <= closesAt
        ? "inside the judging window"
        : "after the window shuts");
    } else if (close) {
      text("finish-note", "judging closes " + close.slice(0, 16).replace("T", " "));
    } else {
      text("finish-note", "no judging close set");
    }

    text("at-risk-count", String(atRisk));
    text("at-risk-note", atRisk === 1 ? "judge needs help" : "judges need help");
    text("reviews-left", String(left));
    text("reviews-note", "across " + judges.length + " judges");
    text("move-count", String((proposal.moves || []).length));
    text("move-note", "max load " + (proposal.max_load === undefined ? "?" : proposal.max_load));
  }

  function renderJudges(payload) {
    var body = slot("judges");
    if (!body) {
      return;
    }
    body.textContent = "";
    var judges = (payload.forecast || {}).judges || [];
    if (!judges.length) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 10;
      td.className = "text-muted";
      td.textContent = "No judges are assigned to this event yet.";
      empty.appendChild(td);
      body.appendChild(empty);
      return;
    }
    judges.forEach(function (judge) {
      var row = document.createElement("tr");
      if (judge.at_risk) {
        row.classList.add("table-warning");
      }
      cell(row, judge.name || judge.judge);
      cell(row, judge.tracks || "");
      cell(row, judge.assigned, "num");
      cell(row, judge.submitted, "num");
      cell(row, judge.drafts, "num");
      cell(row, judge.remaining, "num");
      cell(row, pace(judge), "num");
      timeCell(row, judge.projected_finish, "no pace yet");
      row.appendChild(bar(judge));
      var status = document.createElement("td");
      status.appendChild(statusBadge(judge));
      row.appendChild(status);
      body.appendChild(row);
    });
  }

  function renderProposal(payload) {
    var body = slot("moves");
    var skipped = slot("skipped");
    if (skipped) {
      skipped.textContent = "";
    }
    if (!body) {
      return;
    }
    body.textContent = "";
    var moves = (payload.proposal || {}).moves || [];
    if (!moves.length) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 4;
      td.className = "text-muted";
      td.textContent = "No assignment can be moved: nothing is both at risk and untouched.";
      empty.appendChild(td);
      body.appendChild(empty);
    }
    moves.forEach(function (move) {
      var row = document.createElement("tr");
      cell(row, move.project_title || move.project);
      cell(row, move.track_name || move.track);
      cell(row, move.from_judge_name);
      cell(row, move.to_judge_name);
      body.appendChild(row);
    });
    ((payload.proposal || {}).skipped || []).forEach(function (item) {
      if (!skipped) {
        return;
      }
      var entry = document.createElement("li");
      entry.className = "list-group-item small";
      entry.textContent = (item.project_title || item.project) + ": " + item.reason;
      skipped.appendChild(entry);
    });
  }

  function render(payload) {
    if (!payload) {
      return;
    }
    state.payload = payload;
    renderStats(payload);
    renderJudges(payload);
    renderProposal(payload);
    var apply = root.querySelector("[data-cc-apply]");
    if (apply) {
      apply.disabled = !((payload.proposal || {}).moves || []).length;
    }
    if (window.verdictTime) {
      window.verdictTime.paint();
    }
  }

  function refresh() {
    if (!url || !window.fetch) {
      return Promise.resolve({});
    }
    return window
      .fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (response) {
        return response.ok ? response.json() : {};
      })
      .then(function (payload) {
        render(payload);
        return payload;
      })
      .catch(function () {
        // A poll that fails leaves the last known forecast on screen; the next
        // tick tries again, so the page never blanks on a flaky network.
        return {};
      });
  }

  function start() {
    render(state.payload);
    var button = document.querySelector("[data-command-center-refresh]");
    if (button) {
      button.addEventListener("click", function () {
        button.disabled = true;
        refresh().then(function () {
          button.disabled = false;
        });
      });
    }
    var apply = root.querySelector("[data-cc-apply]");
    if (apply) {
      apply.disabled = !((state.payload.proposal || {}).moves || []).length;
    }
    window.setInterval(refresh, Math.max(seconds, 5) * 1000);  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.verdictCommandCenter = { refresh: refresh, render: render };
})();
