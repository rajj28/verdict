/*
 * time.js: every timestamp on the page in the reader's own timezone.
 *
 * The server renders ISO 8601 UTC into <time datetime="..."> (that is the
 * canonical value, and what the countdown counts down to). This file adds the
 * local rendering, and for elements marked data-countdown a live relative
 * phrase such as "closes in 3 h 12 min".
 */
(function () {
  "use strict";

  var REFRESH_MS = 30000;

  function parse(value) {
    if (!value) {
      return null;
    }
    var stamp = Date.parse(value);
    return isNaN(stamp) ? null : new Date(stamp);
  }

  function localTime(date) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short"
      }).format(date);
    } catch (error) {
      return date.toLocaleString();
    }
  }

  function duration(ms) {
    var seconds = Math.round(Math.abs(ms) / 1000);
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    if (days >= 1) {
      return days + (days === 1 ? " day" : " days");
    }
    if (hours >= 1) {
      return hours + " h " + minutes + " min";
    }
    if (minutes >= 1) {
      return minutes + " min";
    }
    return seconds + " s";
  }

  function relative(date, now, label) {
    var delta = date.getTime() - now.getTime();
    if (delta > 0) {
      return { text: label + " in " + duration(delta), state: "countdown-open" };
    }
    return { text: label + " " + duration(delta) + " ago", state: "countdown-closed" };
  }

  function paint() {
    var now = new Date();
    var nodes = document.querySelectorAll("time[datetime]");
    Array.prototype.forEach.call(nodes, function (node) {
      var date = parse(node.getAttribute("datetime"));
      if (!date) {
        return;
      }
      var local = localTime(date);
      node.setAttribute("title", local + " (" + date.toISOString() + ")");
      if (!node.hasAttribute("data-countdown")) {
        if (!node.textContent.trim()) {
          node.textContent = local;
        }
        return;
      }
      var label = node.getAttribute("data-countdown-label") || "closes";
      var result = relative(date, now, label);
      node.textContent = result.text + " · " + local;
      node.classList.remove("countdown-open", "countdown-closed", "countdown-soon");
      node.classList.add(result.state);
      var hours = Math.abs(date.getTime() - now.getTime()) / 3600000;
      if (result.state === "countdown-open" && hours < 24) {
        node.classList.remove("countdown-open");
        node.classList.add("countdown-soon");
      }
    });
  }

  function start() {
    paint();
    window.setInterval(paint, REFRESH_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) {
        paint();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.verdictTime = { paint: paint };
})();
