/*
 * manage-forms.js: the two organizer forms that need more than a flat payload.
 *
 *   1. The rubric editor saves the whole criteria list in one PUT, but the
 *      organizer edits it as rows. Rows marked data-row are collected into the
 *      form's data-compact hidden field as a JSON array just before the submit,
 *      so the request is exactly what a script would send.
 *   2. The auto-assign preview is a POST that returns a proposal, not a
 *      redirect. It is sent through verdictApi.send (the same sender, CSRF token
 *      and error envelope as every other write) and the proposal is rendered
 *      here, so what the organizer reads is what Apply will create.
 *
 * Both are presentation over the JSON API; the rules stay in the services.
 */
(function () {
  "use strict";

  function valueOf(field) {
    if (field.type === "checkbox") {
      return field.checked;
    }
    if (field.type === "number") {
      return field.value.trim() === "" ? null : Number(field.value);
    }
    return field.value;
  }

  /* --- rubric rows ----------------------------------------------------- */

  function compact(form) {
    var field = form.getAttribute("data-compact");
    if (!field) {
      return;
    }
    var target = form.querySelector('[name="' + CSS.escape(field) + '"]');
    if (!target) {
      return;
    }
    var rows = [];
    Array.prototype.forEach.call(form.querySelectorAll("[data-row]"), function (row) {
      var entry = {};
      var empty = true;
      Array.prototype.forEach.call(
        row.querySelectorAll("[data-key]"),
        function (input) {
          var value = valueOf(input);
          entry[input.getAttribute("data-key")] = value;
          if (value !== null && value !== "") {
            empty = false;
          }
        }
      );
      if (!empty) {
        rows.push(entry);
      }
    });
    target.value = JSON.stringify(rows);
  }

  function addRow(button) {
    var container = document.querySelector(button.getAttribute("data-row-add"));
    var template = document.querySelector(button.getAttribute("data-row-template"));
    if (!container || !template || !template.content) {
      return;
    }
    var row = template.content.cloneNode(true);
    var index = container.children.length + 1;
    // Labels in a cloned row have no id, so give them one and point the input
    // at it: a label without a for= is a label for nothing.
    Array.prototype.forEach.call(row.querySelectorAll("label"), function (label) {
      var input = label.parentNode.querySelector("input");
      if (!input) {
        return;
      }
      var id = "row-" + index + "-" + (label.textContent || "").toLowerCase();
      label.setAttribute("for", id);
      input.setAttribute("id", id);
    });
    container.appendChild(row);
    var first = container.lastElementChild.querySelector("input");
    if (first) {
      first.focus();
    }
  }

  /* --- auto-assign preview --------------------------------------------- */

  function optionLabels(selectId) {
    var labels = {};
    var select = document.getElementById(selectId);
    if (!select) {
      return labels;
    }
    Array.prototype.forEach.call(select.options, function (option) {
      labels[option.value] = option.textContent.replace(/\s+/g, " ").trim();
    });
    return labels;
  }

  function cell(row, text, className) {
    var td = document.createElement("td");
    td.textContent = text;
    if (className) {
      td.className = className;
    }
    row.appendChild(td);
    return td;
  }

  function table(headers, rows, emptyMessage) {
    var element = document.createElement("div");
    element.className = "table-responsive";
    var t = document.createElement("table");
    t.className = "data-table";
    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    headers.forEach(function (label) {
      var th = document.createElement("th");
      th.setAttribute("scope", "col");
      th.textContent = label;
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    t.appendChild(head);
    var body = document.createElement("tbody");
    if (!rows.length) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = headers.length;
      td.className = "form-hint";
      td.textContent = emptyMessage;
      empty.appendChild(td);
      body.appendChild(empty);
    }
    rows.forEach(function (values) {
      var tr = document.createElement("tr");
      values.forEach(function (value, index) {
        cell(tr, value, index ? "num" : "");
      });
      body.appendChild(tr);
    });
    t.appendChild(body);
    element.appendChild(t);
    return element;
  }

  function renderPreview(target, data, labels) {
    while (target.firstChild) {
      target.removeChild(target.firstChild);
    }
    var proposed = (data && data.proposed) || [];
    var unfilled = (data && data.unfilled) || [];
    var heading = document.createElement("h3");
    heading.className = "h5";
    heading.textContent = "Preview: " + proposed.length + " assignment"
      + (proposed.length === 1 ? "" : "s") + " would be created";
    target.appendChild(heading);
    target.appendChild(table(
      ["Judge", "Project"],
      proposed.map(function (row) {
        return [labels.judges[row.judge] || row.judge, labels.projects[row.project] || row.project];
      }),
      "Nothing to add: every eligible pair is already assigned."
    ));
    if (unfilled.length) {
      var warn = document.createElement("h3");
      warn.className = "h5 mt-3";
      warn.textContent = "Needs that cannot be filled";
      target.appendChild(warn);
      target.appendChild(table(
        ["Project", "Have", "Need", "Why"],
        unfilled.map(function (row) {
          return [
            labels.projects[row.project] || row.project,
            String(row.assigned),
            String(row.target),
            row.reason || "no eligible judge left"
          ];
        }),
        "Every project reached its target."
      ));
    }
  }

  function wirePreview() {
    var button = document.getElementById("auto-preview-button");
    var form = document.getElementById("auto-assign-form");
    if (!button || !form || !window.verdictApi) {
      return;
    }
    var target = document.querySelector(form.getAttribute("data-preview-target"));
    var url = form.getAttribute("data-auto-preview");
    if (!target || !url) {
      return;
    }
    var labels = { judges: optionLabels("id_batch_judges"), projects: optionLabels("id_batch_projects") };
    button.addEventListener("click", function () {
      var payload = {};
      ["target", "max_load"].forEach(function (name) {
        var field = form.querySelector('[name="' + name + '"]');
        if (field && field.value.trim() !== "") {
          payload[name] = Number(field.value);
        }
      });
      payload.dry_run = true;
      button.disabled = true;
      window.verdictApi.send("POST", url, payload, form, "", null).then(function (body) {
        button.disabled = false;
        if (body && body.error) {
          target.textContent = body.error.message || "The preview could not be built.";
          return;
        }
        renderPreview(target, body, labels);
      });
    });
  }

  function start() {
    // Bound to the form itself, not to document: api-forms.js also listens for
    // submit on document, and a document-level listener here would run after it
    // has already serialized the payload.
    Array.prototype.forEach.call(
      document.querySelectorAll("form[data-compact]"),
      function (form) {
        form.addEventListener("submit", function () {
          compact(form);
        });
      }
    );
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-row-add]");
      if (button) {
        event.preventDefault();
        addRow(button);
      }
    });
    wirePreview();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
