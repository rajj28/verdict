/*
 * results.js: the live half of /manage/{slug}/results.
 *
 * Two small jobs, neither of them a write:
 *
 *   1. keep the reader on the tab they were looking at. The Explain tab is a
 *      plain GET form (the breakdown is computed server-side), so it carries a
 *      fragment and this file re-activates that tab on load;
 *   2. hold the Publish button until the acknowledge-unranked box is ticked when
 *      the event has unranked projects. That is a courtesy, not the control:
 *      results.services.publish refuses the same request with 409
 *      unranked_projects when the box is missing.
 */
(function () {
  "use strict";

  function tabFor(fragment) {
    if (!fragment || fragment.charAt(0) !== "#") {
      return null;
    }
    var pane = document.querySelector(fragment);
    if (!pane || !pane.classList.contains("tab-pane")) {
      return null;
    }
    return document.querySelector('[data-bs-toggle="tab"][data-bs-target="' + fragment + '"]');
  }

  function showTab(hash) {
    var button = tabFor(hash);
    if (!button || !window.bootstrap || !window.bootstrap.Tab) {
      return;
    }
    window.bootstrap.Tab.getOrCreateInstance(button).show();
  }

  function followHash() {
    showTab(window.location.hash);
    window.addEventListener("hashchange", function () {
      showTab(window.location.hash);
    });
  }

  function gatePublish() {
    var form = document.querySelector("[data-publish-form]");
    if (!form) {
      return;
    }
    var box = form.querySelector("[data-publish-ack] input");
    var submit = form.querySelector("[data-publish-submit]");
    if (!box || !submit) {
      return;
    }
    function sync() {
      submit.disabled = !box.checked;
    }
    box.addEventListener("change", sync);
    sync();
  }

  function start() {
    followHash();
    gatePublish();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
