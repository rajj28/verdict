(function () {
  "use strict";

  function start() {
    var modalElement = document.getElementById("consequence-dialog");
    if (!modalElement || !window.bootstrap || !window.bootstrap.Modal) {
      return;
    }

    var modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
    var sentence = modalElement.querySelector("[data-consequence-sentence]");
    var loading = modalElement.querySelector("[data-consequence-loading]");
    var error = modalElement.querySelector("[data-consequence-error]");
    var rankContainer = modalElement.querySelector("[data-consequence-rank-container]");
    var awardContainer = modalElement.querySelector("[data-consequence-award-container]");
    var reasonGroup = modalElement.querySelector("[data-consequence-reason-group]");
    var reason = modalElement.querySelector("#consequence-reason");
    var confirmButton = modalElement.querySelector("[data-consequence-confirm]");
    var state = null;

    function request(method, url, payload) {
      var headers = { Accept: "application/json" };
      var token = document.querySelector('meta[name="csrf-token"]');
      if (token && token.content) {
        headers["X-CSRFToken"] = token.content;
      }
      if (payload !== undefined) {
        headers["Content-Type"] = "application/json";
      }
      return window.fetch(url, {
        method: method,
        headers: headers,
        body: payload === undefined ? undefined : JSON.stringify(payload),
        credentials: "same-origin"
      }).then(function (response) {
        if (response.status === 204) {
          return { ok: response.ok, status: response.status, body: {} };
        }
        return response.json().catch(function () {
          return {};
        }).then(function (body) {
          return { ok: response.ok, status: response.status, body: body };
        });
      });
    }

    function errorMessage(result) {
      return result.body && result.body.error && result.body.error.message
        ? result.body.error.message
        : "The request could not be completed.";
    }

    function addCell(row, value, header) {
      var cell = document.createElement(header ? "th" : "td");
      if (header) {
        cell.scope = "col";
      }
      cell.textContent = value;
      row.appendChild(cell);
    }

    function renderTable(title, headers, rows, container, limit) {
      container.replaceChildren();
      if (!rows || !rows.length) {
        return;
      }
      var heading = document.createElement("h3");
      heading.className = "h6 mt-3";
      heading.textContent = title;
      container.appendChild(heading);
      var wrapper = document.createElement("div");
      wrapper.className = "table-responsive";
      var table = document.createElement("table");
      table.className = "data-table";
      var head = document.createElement("thead");
      var headerRow = document.createElement("tr");
      headers.forEach(function (item) {
        addCell(headerRow, item, true);
      });
      head.appendChild(headerRow);
      table.appendChild(head);
      var body = document.createElement("tbody");
      rows.slice(0, limit).forEach(function (items) {
        var row = document.createElement("tr");
        items.forEach(function (item) {
          addCell(row, item, false);
        });
        body.appendChild(row);
      });
      table.appendChild(body);
      wrapper.appendChild(table);
      container.appendChild(wrapper);
      if (rows.length > limit) {
        var more = document.createElement("p");
        more.className = "form-hint";
        more.textContent = "and " + (rows.length - limit) + " more";
        container.appendChild(more);
      }
    }

    function renderConsequences(data) {
      sentence.textContent = data.sentence || "";
      renderTable(
        "Rank changes",
        ["Project", "Before", "After"],
        (data.rank_changes || []).map(function (row) {
          return [row.title, row.before, row.after];
        }),
        rankContainer,
        10
      );
      renderTable(
        "Award changes",
        ["Prize", "Before", "After"],
        (data.award_changes || []).map(function (row) {
          return [
            row.prize_name,
            (row.before || []).map(function (item) { return item.title; }).join(", ") || "No award",
            (row.after || []).map(function (item) { return item.title; }).join(", ") || "No award"
          ];
        }),
        awardContainer,
        100
      );
    }

    function setLoading(isLoading) {
      loading.textContent = isLoading ? "Loading consequences…" : "";
      confirmButton.disabled = isLoading || !state || !state.digest;
    }

    function loadConsequences() {
      state.digest = "";
      setLoading(true);
      error.textContent = state.notice || "";
      sentence.textContent = "";
      rankContainer.replaceChildren();
      awardContainer.replaceChildren();
      var payload = { action: state.action };
      if (state.target) {
        payload.target = state.target;
      }
      return request("POST", modalElement.getAttribute("data-consequences-url"), payload)
        .then(function (result) {
          if (!result.ok) {
            error.textContent = state.notice
              ? state.notice + " " + errorMessage(result)
              : errorMessage(result);
            return;
          }
          state.digest = result.body.basis_digest;
          renderConsequences(result.body);
          error.textContent = state.notice || "";
          confirmButton.disabled = false;
        })
        .catch(function () {
          error.textContent = state.notice
            ? state.notice + " Could not load updated consequences."
            : "Could not load consequences. Check your connection and try again.";
        })
        .then(function () {
          setLoading(false);
          if (state && state.digest) {
            confirmButton.disabled = false;
          }
        });
    }

    function open(trigger, action, target, method, url) {
      state = {
        trigger: trigger,
        action: action,
        target: target,
        method: method,
        url: url,
        digest: "",
        notice: ""
      };
      error.textContent = "";
      reason.value = "";
      reasonGroup.hidden = action !== "disqualify" && action !== "exclude_review";
      confirmButton.textContent = action === "publish"
        ? "Publish results"
        : action === "disqualify" ? "Disqualify project"
          : action === "exclude_review" ? "Exclude review" : "Include review";
      modal.show();
      loadConsequences();
    }

    modalElement.addEventListener("shown.bs.modal", function () {
      var close = modalElement.querySelector(".btn-close");
      if (close) {
        close.focus();
      }
    });
    modalElement.addEventListener("hidden.bs.modal", function () {
      if (state && state.trigger && document.contains(state.trigger)) {
        state.trigger.focus();
      }
      state = null;
    });

    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-consequence-action]");
      if (!button) {
        return;
      }
      event.preventDefault();
      open(
        button,
        button.getAttribute("data-consequence-action"),
        button.getAttribute("data-consequence-target"),
        button.getAttribute("data-execute-method"),
        button.getAttribute("data-execute-url")
      );
    });

    var publishForm = document.querySelector("[data-publish-form]");
    if (publishForm) {
      publishForm.addEventListener("submit", function (event) {
        event.preventDefault();
        open(
          publishForm.querySelector("[data-publish-submit]"),
          "publish",
          null,
          "POST",
          publishForm.getAttribute("data-publish-url")
        );
      });
    }

    confirmButton.addEventListener("click", function () {
      if (!state || !state.digest || confirmButton.disabled) {
        return;
      }
      if (!reasonGroup.hidden && !reason.value.trim()) {
        reason.setCustomValidity("A reason is required.");
        reason.reportValidity();
        reason.addEventListener("input", function clearValidity() {
          reason.setCustomValidity("");
          reason.removeEventListener("input", clearValidity);
        });
        return;
      }
      var payload = { expected_digest: state.digest };
      if (state.action === "publish") {
        var form = document.querySelector("[data-publish-form]");
        payload.note = form.querySelector('[name="note"]').value;
        var acknowledge = form.querySelector('[name="acknowledge_unranked"]');
        if (acknowledge) {
          payload.acknowledge_unranked = acknowledge.checked;
        }
      } else if (state.action === "disqualify" || state.action === "exclude_review") {
        payload.reason = reason.value.trim();
      }
      confirmButton.disabled = true;
      error.textContent = "";
      request(state.method, state.url, payload).then(function (result) {
        if (result.ok) {
          window.location.reload();
          return;
        }
        if (result.status === 409 && result.body && result.body.error
            && result.body.error.code === "stale_preview") {
          state.notice = errorMessage(result);
          loadConsequences();
          return;
        }
        error.textContent = errorMessage(result);
        confirmButton.disabled = false;
      }).catch(function () {
        error.textContent = "Could not complete the action. Check your connection and try again.";
        confirmButton.disabled = false;
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
