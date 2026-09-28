(function () {
  "use strict";
  var panel = document.querySelector("[data-tour-panel]");
  var csrf = document.querySelector("meta[name=csrf-token]")?.getAttribute("content") || "";
  var starter = document.querySelector("[data-tour-start]");
  if (starter) starter.addEventListener("click", function () {
    starter.disabled = true;
    fetch("/api/v1/tour/start", {method: "POST", headers: {"X-CSRFToken": csrf}})
      .then(function (response) { return response.json(); })
      .then(function (data) { if (data.url) location.href = data.url; else starter.disabled = false; })
      .catch(function () { starter.disabled = false; });
  });
  if (!panel) return;
  var island = document.getElementById("tour-steps");
  var steps = island ? JSON.parse(island.textContent) : [];
  var state = 0;
  try { state = parseInt(localStorage.getItem("verdict-tour-step") || "0", 10) || 0; } catch (_) {}
  var current = document.body.dataset.tourStep ? parseInt(document.body.dataset.tourStep, 10) : state;
  if (current < 0 || current >= steps.length) current = 0;
  function save() {
    try { localStorage.setItem("verdict-tour-step", String(current)); } catch (_) {}
    fetch("/api/v1/tour/step", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify({step: current})}).catch(function () {});
  }
  function render() {
    var step = steps[current];
    if (!step) return;
    panel.querySelector("[data-tour-count]").textContent = "Step " + (current + 1) + " of " + steps.length;
    panel.querySelector("[data-tour-title]").textContent = step.title;
    panel.querySelector("[data-tour-text]").textContent = step.text;
    panel.querySelector("[data-tour-hint]").textContent = step.hint || "";
    panel.querySelector("[data-tour-back]").disabled = current === 0;
    panel.querySelector("[data-tour-next]").textContent = current === steps.length - 1 ? "Done" : "Next";
    var next = steps[current + 1];
    var switcher = panel.querySelector("[data-tour-switch]");
    if (next && next.role) {
      switcher.hidden = false;
      panel.querySelector("[data-tour-role]").textContent = "Switch to " + next.role;
    } else switcher.hidden = true;
    document.querySelectorAll("[data-tour-highlight]").forEach(function (el) { el.removeAttribute("data-tour-highlight"); });
    var target = document.querySelector(step.selector);
    if (target) {
      target.setAttribute("data-tour-highlight", "");
      target.scrollIntoView({behavior: "smooth", block: "center"});
    }
    save();
  }
  function go(delta) {
    current = Math.max(0, Math.min(steps.length - 1, current + delta));
    var step = steps[current];
    if (step && step.url && location.pathname !== step.url) location.href = step.url;
    else render();
  }
  panel.querySelector("[data-tour-back]").addEventListener("click", function () { go(-1); });
  panel.querySelector("[data-tour-next]").addEventListener("click", function () { go(1); });
  panel.querySelector("[data-tour-exit]").addEventListener("click", function () { panel.remove(); });
  panel.querySelector("[data-tour-reset]").addEventListener("click", function () {
    fetch("/api/v1/tour/reset", {method: "POST", headers: {"X-CSRFToken": csrf}}).then(function (response) {
      if (response.ok) response.json().then(function (data) { location.href = data.url; });
    });
  });
  panel.querySelector("[data-tour-role]").addEventListener("click", function () {
    var next = steps[current + 1];
    if (!next || !next.role) return;
    fetch("/api/v1/tour/role", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify({role: next.role})})
      .then(function (response) { return response.json(); }).then(function (data) {
        if (data.redirect) { current += 1; location.href = data.redirect; }
      });
  });
  render();
}());
