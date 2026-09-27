/*
 * Dark/light theme.
 *
 * Loaded synchronously in <head> so the attribute is set before the body paints
 * (no inline <script> is allowed by the CSP, and a flash of the wrong theme is
 * worse than a 1 KB blocking request). The choice is remembered in localStorage;
 * with nothing stored we follow the operating system.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "verdict-theme";
  document.documentElement.classList.add("js");

  function stored() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      return null;
    }
  }

  function preferred() {
    var locked = document.documentElement.getAttribute("data-theme-lock");
    if (locked === "light" || locked === "dark") {
      return locked;
    }
    var saved = stored();
    if (saved) {
      return saved;
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    var pressed = theme === "dark" ? "true" : "false";
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    Array.prototype.forEach.call(toggles, function (toggle) {
      toggle.setAttribute("aria-pressed", pressed);
    });
  }

  function remember(theme) {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch (error) {
      /* private mode: the operating system preference keeps working */
    }
  }

  apply(preferred());

  // The <head> pass runs before the toggle button exists, so label it once the
  // body is there.
  document.addEventListener("DOMContentLoaded", function () {
    apply(document.documentElement.getAttribute("data-bs-theme"));
  });

  // Follow the system until the reader picks a side.
  if (window.matchMedia) {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var onSystemChange = function (event) {
      if (!stored()) {
        apply(event.matches ? "dark" : "light");
      }
    };
    if (query.addEventListener) {
      query.addEventListener("change", onSystemChange);
    } else if (query.addListener) {
      query.addListener(onSystemChange);
    }
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-theme-toggle]");
    if (!toggle) {
      return;
    }
    event.preventDefault();
    var next = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    apply(next);
    remember(next);
  });
})();
