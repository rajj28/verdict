/*
 * judge-console.js: the rubric side of /judge/{slug}/review/{prj}.
 *
 * Three jobs, all of them presentation over the same two endpoints the buttons
 * use (BUILD-SPEC section 2: the page never writes anywhere but the API):
 *
 *   1. a live weighted score preview, using the engine's own formula
 *      (100 * sum(w * (v - min) / (max - min)) / sum(w));
 *   2. a debounced draft save two seconds after the last change, with a visible
 *      saved / saving / not-saved state, so a judge never loses work silently;
 *   3. the keyboard shortcuts (1-9 score, arrows move, s submits, n/p navigate).
 *
 * The explicit Save draft and Submit buttons stay with api-forms.js: they are
 * data-api-* controls, and this file only keeps the hidden scores field in sync
 * with the radios so the generic serializer can send the nested payload.
 */
(function () {
  "use strict";

  var AUTOSAVE_MS = 2000;

  function shell() {
    return document.querySelector("[data-review]");
  }

  function fields() {
    var node = shell();
    return node ? Array.prototype.slice.call(node.querySelectorAll("[data-criterion]")) : [];
  }

  function commentField() {
    return document.querySelector('[name="comment"]');
  }

  function scoreOf(field) {
    var checked = field.querySelector('input[type="radio"]:checked');
    return checked ? parseInt(checked.value, 10) : null;
  }

  function scores() {
    var values = {};
    fields().forEach(function (field) {
      var value = scoreOf(field);
      if (value !== null) {
        values[field.getAttribute("data-criterion")] = value;
      }
    });
    return values;
  }

  function complete(values) {
    var all = fields();
    return all.length > 0 && Object.keys(values).length === all.length;
  }

  /* The same arithmetic the results engine uses, so the preview cannot drift. */
  function weighted(values) {
    var total = 0;
    var scaled = 0;
    var answered = 0;
    fields().forEach(function (field) {
      var key = field.getAttribute("data-criterion");
      var weight = parseFloat(field.getAttribute("data-weight"));
      var min = parseFloat(field.getAttribute("data-min"));
      var max = parseFloat(field.getAttribute("data-max"));
      var value = values[key];
      if (value === undefined) {
        return;
      }
      answered += 1;
      if (max <= min) {
        return;
      }
      total += weight;
      scaled += weight * ((value - min) / (max - min));
    });
    return { answered: answered, score: total > 0 ? (100 * scaled) / total : null };
  }

  function paintPreview() {
    var node = document.querySelector("[data-score-preview]");
    if (!node) {
      return;
    }
    var values = scores();
    if (complete(values)) {
      node.textContent = weighted(values).score.toFixed(1);
      return;
    }
    var partial = weighted(values);
    node.textContent = partial.answered + " / " + fields().length;
  }

  function setState(text, variant) {
    var node = document.querySelector("[data-save-state]");
    if (!node) {
      return;
    }
    node.textContent = text;
    node.classList.remove("text-danger", "text-success");
    if (variant) {
      node.classList.add(variant);
    }
  }

  function clock() {
    try {
      return new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } catch (error) {
      return new Date().toTimeString().slice(0, 5);
    }
  }

  function syncPayload() {
    var field = shell().querySelector("[data-scores-payload]");
    if (field) {
      field.value = JSON.stringify(scores());
    }
  }

  function showError(message) {
    var target = document.querySelector("[data-form-errors]");
    if (target) {
      target.textContent = message;
    }
    if (window.verdictApi && window.verdictApi.toast) {
      window.verdictApi.toast(message, "danger");
    }
  }

  function draft() {
    return window.fetch(shell().getAttribute("data-draft-url"), {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": (window.verdictApi && window.verdictApi.csrfToken()) || ""
      },
      body: JSON.stringify({ scores: scores(), comment: (commentField() || {}).value || "" })
    })
      .then(function (response) {
        return response.text().then(function (text) {
          var body = {};
          try {
            body = text ? JSON.parse(text) : {};
          } catch (error) {
            body = {};
          }
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (result.ok) {
          setState("Saved · " + clock(), "text-success");
          return;
        }
        var error = (result.body && result.body.error) || {};
        setState("Not saved", "text-danger");
        showError(error.message || "The draft could not be saved.");
      })
      .catch(function () {
        setState("Not saved · no connection", "text-danger");
        showError("Could not reach the server. Your scores are still on this page.");
      });
  }

  var pending = null;

  function schedule() {
    if (pending) {
      window.clearTimeout(pending);
    }
    setState("Saving…");
    pending = window.setTimeout(function () {
      pending = null;
      draft();
    }, AUTOSAVE_MS);
  }

  function paintControls() {
    var values = scores();
    var readonly = shell().getAttribute("data-readonly") === "1";
    var submit = document.querySelector("[data-submit-review]");
    var save = document.querySelector("[data-save-draft]");
    if (submit) {
      submit.disabled = readonly || !complete(values);
      submit.title = complete(values)
        ? ""
        : "Every criterion has to be scored before a review can be submitted.";
    }
    if (save) {
      save.disabled = readonly;
    }
    paintPreview();
  }

  function isTyping(node) {
    if (!node) {
      return false;
    }
    if (node.isContentEditable) {
      return true;
    }
    var tag = node.tagName;
    return tag === "TEXTAREA" || tag === "SELECT" ||
      (tag === "INPUT" && ["text", "email", "url", "search", "password"].indexOf(node.type) !== -1);
  }

  function focusCriterion(field) {
    var radio = field && field.querySelector('input[type="radio"]:not([disabled])');
    if (radio) {
      radio.focus();
    }
  }

  function focusedField() {
    var active = document.activeElement;
    var field = active && active.closest ? active.closest("[data-criterion]") : null;
    return field || fields()[0] || null;
  }

  function step(delta) {
    var all = fields();
    if (!all.length) {
      return;
    }
    var current = focusedField();
    var index = all.indexOf(current);
    if (index === -1) {
      index = 0;
    }
    focusCriterion(all[Math.min(Math.max(index + delta, 0), all.length - 1)]);
  }

  function scoreFocused(digit) {
    var field = focusedField();
    if (!field) {
      return;
    }
    var min = parseInt(field.getAttribute("data-min"), 10);
    var max = parseInt(field.getAttribute("data-max"), 10);
    var value = Math.min(Math.max(digit, min), max);
    var wanted = field.querySelector('input[type="radio"][value="' + value + '"]');
    if (wanted && !wanted.disabled) {
      wanted.checked = true;
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function start() {
    var node = shell();
    if (!node) {
      return;
    }
    var readonly = node.getAttribute("data-readonly") === "1";

    document.addEventListener("change", function (event) {
      if (!node.contains(event.target)) {
        return;
      }
      syncPayload();
      paintControls();
      if (!readonly) {
        schedule();
      }
    });
    document.addEventListener("input", function (event) {
      if (node.contains(event.target) && !readonly && event.target.name === "comment") {
        syncPayload();
        schedule();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) {
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        step(event.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (event.key >= "1" && event.key <= "9") {
        event.preventDefault();
        scoreFocused(parseInt(event.key, 10));
        return;
      }
      var key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        var submit = document.querySelector("[data-submit-review]");
        if (submit && !submit.disabled) {
          submit.click();
        }
        return;
      }
      if (key === "n" || key === "p") {
        var link = document.querySelector(key === "n" ? 'a[rel="next"]' : 'a[rel="prev"]');
        if (link) {
          event.preventDefault();
          window.location.assign(link.getAttribute("href"));
        }
      }
    });

    var toggle = document.querySelector("[data-shortcuts-toggle]");
    var content = document.getElementById("judge-shortcuts");
    if (toggle && content && window.bootstrap && window.bootstrap.Popover) {
      new window.bootstrap.Popover(toggle, {
        content: content,
        html: true,
        trigger: "click",
        placement: "bottom-end",
        container: "body"
      });
    }

    syncPayload();
    paintControls();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.verdictJudge = { scores: scores, paint: paintControls };
})();
