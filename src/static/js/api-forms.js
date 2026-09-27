/*
 * api-forms.js: the only write path in the UI (BUILD-SPEC section 2).
 *
 * Any <form> or <button> carrying data-api-method and data-api-url posts to the
 * JSON API with the CSRF token from <meta name="csrf-token">, paints field and
 * non-field errors back into the markup, shows a toast, and then does whatever
 * data-success asks for. There is no inline <script> and no on* handler
 * anywhere, so the CSP stays strict.
 *
 *   <form data-api-method="POST" data-api-url="/api/v1/..." data-success="reload">
 *   <button data-api-method="DELETE" data-api-url="/api/v1/..." data-confirm="Sure?">
 *
 * data-success: "reload" | "redirect:/path" | "redirect-field:<json key>" | (absent: toast only)
 * data-type:    "int" | "float" | "bool" | "list" | "json" input coercion
 */
(function () {
  "use strict";

  var TOKEN = "vd_";

  function meta(name) {
    var tag = document.querySelector('meta[name="' + name + '"]');
    return tag ? tag.getAttribute("content") : "";
  }

  function cookie(name) {
    var parts = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < parts.length; i += 1) {
      var pair = parts[i].trim();
      if (pair.indexOf(name + "=") === 0) {
        return decodeURIComponent(pair.slice(name.length + 1));
      }
    }
    return "";
  }

  function csrfToken() {
    return meta("csrf-token") || cookie("csrftoken") || "";
  }

  /* --- toasts ---------------------------------------------------------- */

  function toastHost() {
    var host = document.querySelector(".toast-container");
    if (!host) {
      host = document.createElement("div");
      host.className = "toast-container position-fixed bottom-0 end-0 p-3";
      document.body.appendChild(host);
    }
    return host;
  }

  function toast(message, variant) {
    var host = toastHost();
    var element = document.createElement("div");
    element.className = "toast align-items-center border-0 text-bg-" + (variant || "dark");
    element.setAttribute("role", "status");
    element.setAttribute("aria-live", "polite");

    var body = document.createElement("div");
    body.className = "d-flex";
    var text = document.createElement("div");
    text.className = "me-2";
    text.textContent = message;
    body.appendChild(text);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close btn-close-white me-2 m-auto";
    close.setAttribute("data-bs-dismiss", "toast");
    close.setAttribute("aria-label", "Dismiss");
    body.appendChild(close);

    element.appendChild(body);
    host.appendChild(element);

    if (window.bootstrap && window.bootstrap.Toast) {
      var instance = window.bootstrap.Toast.getOrCreateInstance(element, { delay: 6000 });
      element.addEventListener("hidden.bs.toast", function () {
        element.remove();
      });
      instance.show();
      return;
    }
    element.classList.add("show");
    element.style.opacity = "1";
    window.setTimeout(function () {
      element.remove();
    }, 6000);
  }

  /* --- value coercion -------------------------------------------------- */

  function coerce(field) {
    var type = field.getAttribute("data-type");
    if (field.type === "checkbox") {
      return field.checked;
    }
    if (type === "bool") {
      return field.checked || ["true", "1", "on", "yes"].indexOf(field.value.trim()) !== -1;
    }
    if (field.multiple) {
      return Array.prototype.filter
        .call(field.options, function (option) {
          return option.selected;
        })
        .map(function (option) {
          return option.value;
        });
    }
    if (!type) {
      return field.value;
    }
    if (type === "int") {
      return field.value.trim() === "" ? null : parseInt(field.value, 10);
    }
    if (type === "float") {
      return field.value.trim() === "" ? null : parseFloat(field.value);
    }
    if (type === "list") {
      return field.value
        .split(",")
        .map(function (part) {
          return part.trim();
        })
        .filter(function (part) {
          return part.length > 0;
        });
    }
    if (type === "json") {
      if (field.value.trim() === "") {
        return null;
      }
      try {
        return JSON.parse(field.value);
      } catch (error) {
        return field.value;
      }
    }
    return field.value;
  }

  function serialize(form) {
    var payload = {};
    var fields = form.querySelectorAll("input[name], select[name], textarea[name]");
    Array.prototype.forEach.call(fields, function (field) {
      if (field.type === "file" || field.type === "submit" || field.type === "button") {
        return;
      }
      if (field.type === "radio" && !field.checked) {
        return;
      }
      payload[field.name] = coerce(field);
    });
    return payload;
  }

  function hasFileInput(form) {
    return Boolean(form.querySelector('input[type="file"]'));
  }

  /* --- error painting -------------------------------------------------- */

  function clearErrors(form) {
    if (!form) {
      return;
    }
    Array.prototype.forEach.call(form.querySelectorAll(".is-invalid"), function (field) {
      field.classList.remove("is-invalid");
    });
    Array.prototype.forEach.call(form.querySelectorAll(".invalid-feedback.d-block"), function (node) {
      node.classList.remove("d-block");
    });
    var summary = form.querySelector("[data-form-errors]");
    if (summary) {
      summary.textContent = "";
    }
  }

  function controlFor(form, name) {
    return form ? form.querySelector('[name="' + CSS.escape(name) + '"]') : null;
  }

  function messageList(value) {
    if (Array.isArray(value)) {
      return value.map(function (item) {
        return typeof item === "string" ? item : JSON.stringify(item);
      });
    }
    return [String(value)];
  }

  function paintErrors(form, error) {
    var fields = (error && error.fields) || {};
    var summary = form ? form.querySelector("[data-form-errors]") : null;
    var unmatched = [];

    Object.keys(fields).forEach(function (name) {
      var messages = messageList(fields[name]);
      var control = controlFor(form, name);
      if (!control) {
        unmatched.push(name + ": " + messages.join(" "));
        return;
      }
      control.classList.add("is-invalid");
      var feedback = null;
      var wrapper = control.closest(".input-group");
      var scope = wrapper || control.parentNode;
      if (scope) {
        feedback = scope.querySelector(".invalid-feedback");
      }
      if (!feedback) {
        feedback = document.createElement("div");
        feedback.className = "invalid-feedback";
        (wrapper || control).parentNode.insertBefore(feedback, (wrapper || control).nextSibling);
      }
      feedback.textContent = messages.join(" ");
      feedback.classList.add("d-block");
    });

    var text = ((error && error.message) || "Something went wrong.").trim();
    if (unmatched.length) {
      text = text ? text + " " + unmatched.join(" ") : unmatched.join(" ");
    }
    if (summary) {
      summary.textContent = text;
    } else if (text) {
      toast(text, "danger");
    }
  }

  /* --- success handling ------------------------------------------------ */

  function safeRedirect(target) {
    // Only same-origin paths: a server-supplied redirect must not become an
    // open redirect, and a token or path fragment is not a URL we invented.
    if (typeof target !== "string" || target === "") {
      return null;
    }
    if (target.charAt(0) === "/" && target.charAt(1) !== "/") {
      return target;
    }
    return null;
  }

  function applySuccess(success, body) {
    if (!success) {
      return false;
    }
    if (success === "reload") {
      window.location.reload();
      return true;
    }
    if (success.indexOf("redirect:") === 0) {
      var target = safeRedirect(success.slice("redirect:".length));
      if (target) {
        window.location.assign(target);
        return true;
      }
      return false;
    }
    if (success.indexOf("redirect-field:") === 0) {
      var key = success.slice("redirect-field:".length);
      var value = body && typeof body === "object" ? body[key] : null;
      var fieldTarget = safeRedirect(value);
      if (fieldTarget) {
        window.location.assign(fieldTarget);
        return true;
      }
      return false;
    }
    return false;
  }

  /* --- response payloads ----------------------------------------------- */

  function reveal(element, body) {
    // A one-off value the server returns once, such as a new token or a reset
    // link: data-response-field says which key, data-response-target where to
    // print it, data-response-reveal which wrapper to un-hide.
    var field = element.getAttribute("data-response-field");
    if (!field) {
      return;
    }
    var value = body && typeof body === "object" ? body[field] : null;
    if (value === null || value === undefined) {
      return;
    }
    var target = element.getAttribute("data-response-target");
    var node = target ? document.querySelector(target) : null;
    if (!node) {
      return;
    }
    node.textContent = value;
    node.classList.remove("d-none");
    node.hidden = false;
    var wrapper = element.getAttribute("data-response-reveal");
    if (wrapper) {
      var box = document.querySelector(wrapper);
      if (box) {
        box.classList.remove("d-none");
        box.hidden = false;
      }
    }
  }

  /* --- requests -------------------------------------------------------- */

  function readBody(response) {
    return response.text().then(function (text) {
      if (!text) {
        return {};
      }
      try {
        return JSON.parse(text);
      } catch (error) {
        return { raw: text };
      }
    });
  }

  function send(method, url, payload, form, success, element) {
    var headers = { Accept: "application/json" };
    var token = csrfToken();
    if (token) {
      headers["X-CSRFToken"] = token;
    }
    var body;
    if (form && hasFileInput(form)) {
      body = new FormData(form);
    } else if (method === "GET" || method === "HEAD" || method === "DELETE") {
      body = undefined;
      if (payload && Object.keys(payload).length) {
        var query = new URLSearchParams();
        Object.keys(payload).forEach(function (key) {
          query.append(key, payload[key]);
        });
        url = url + (url.indexOf("?") === -1 ? "?" : "&") + query.toString();
      }
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(payload || {});
    }

    var pending = window.fetch(url, {
      method: method,
      headers: headers,
      body: body,
      credentials: "same-origin"
    });

    return pending
      .then(function (response) {
        return readBody(response).then(function (parsed) {
          if (response.ok) {
            clearErrors(form);
            if (element) {
              reveal(element, parsed);
            }
            toast("Done.", "success");
            if (!applySuccess(success, parsed)) {
              if (form && form.tagName === "FORM" && !success) {
                // No redirect asked for: clear the inputs but leave anything
                // reveal() just wrote on the page.
                form.reset();
              }
            }
            return parsed;
          }
          var error = parsed && parsed.error ? parsed.error : { message: "Request failed." };
          paintErrors(form, error);
          toast(error.message || "Request failed.", "danger");
          return parsed;
        });
      })
      .catch(function () {
        var message = "Could not reach the server. Check your connection and try again.";
        paintErrors(form, { message: message });
        toast(message, "danger");
        return {};
      });
  }

  /* --- confirm modal --------------------------------------------------- */

  function confirmMessage(message) {
    // Never window.confirm: a styled modal is what BUILD-SPEC section 12 asks
    // for, and it keeps the markup in one place instead of on every button.
    return new Promise(function (resolve) {
      if (!window.bootstrap || !window.bootstrap.Modal) {
        resolve(false);
        return;
      }
      var stale = document.getElementById("verdict-confirm");
      if (stale) {
        stale.remove();
      }
      var element = document.createElement("div");
      element.className = "modal fade";
      element.id = "verdict-confirm";
      element.tabIndex = -1;
      element.setAttribute("role", "dialog");
      element.setAttribute("aria-modal", "true");
      element.innerHTML =
        '<div class="modal-dialog modal-dialog-centered">' +
        '<div class="modal-content">' +
        '<div class="modal-header"><h2 class="modal-title fs-5">Please confirm</h2>' +
        '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>' +
        '<div class="modal-body"><p class="mb-0" data-confirm-text></p></div>' +
        '<div class="modal-footer">' +
        '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>' +
        '<button type="button" class="btn btn-primary" data-confirm-accept>Confirm</button>' +
        "</div></div></div>";
      element.querySelector("[data-confirm-text]").textContent = message;
      var modal = new window.bootstrap.Modal(element);
      var accepted = false;
      element.querySelector("[data-confirm-accept]").addEventListener("click", function () {
        accepted = true;
        modal.hide();
      });
      element.addEventListener("hidden.bs.modal", function () {
        element.remove();
        resolve(accepted);
      });
      document.body.appendChild(element);
      modal.show();
    });
  }

  /* --- wiring ---------------------------------------------------------- */

  function requestFor(element) {
    var form = element.tagName === "FORM" ? element : element.form;
    var method = (element.getAttribute("data-api-method") || "POST").toUpperCase();
    var url = element.getAttribute("data-api-url") || "";
    var success = element.getAttribute("data-success") || "";

    function run() {
      var payload;
      if (form) {
        payload = serialize(form);
      } else {
        payload = {};
      }
      var extra = element.getAttribute("data-body");
      if (extra) {
        try {
          var parsed = JSON.parse(extra);
          Object.keys(parsed).forEach(function (key) {
            payload[key] = parsed[key];
          });
        } catch (error) {
          toast("This control has an invalid data-body attribute.", "danger");
          return;
        }
      }
      var formSelector = element.getAttribute("data-form");
      if (formSelector && !form) {
        var other = document.querySelector(formSelector);
        if (other) {
          var otherPayload = serialize(other);
          Object.keys(otherPayload).forEach(function (key) {
            payload[key] = otherPayload[key];
          });
        }
      }
      clearErrors(form);
      send(method, url, payload, form, success, element);
    }

    var confirmText = element.getAttribute("data-confirm");
    if (confirmText) {
      confirmMessage(confirmText).then(function (accepted) {
        if (accepted) {
          run();
        }
      });
      return;
    }
    run();
  }

  function copyText(value) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value);
    }
    var scratch = document.createElement("textarea");
    scratch.value = value;
    scratch.setAttribute("readonly", "readonly");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    try {
      document.execCommand("copy");
    } finally {
      document.body.removeChild(scratch);
    }
    return Promise.resolve();
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute("data-api-method")) {
      return;
    }
    event.preventDefault();
    requestFor(form);
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-api-method]");
    if (!button || button.tagName === "FORM") {
      return;
    }
    event.preventDefault();
    if (button.disabled) {
      return;
    }
    button.disabled = true;
    window.setTimeout(function () {
      button.disabled = false;
    }, 1500);
    requestFor(button);
  });

  document.addEventListener("click", function (event) {
    var copy = event.target.closest("[data-copy]");
    if (!copy) {
      return;
    }
    event.preventDefault();
    var value = "";
    if (copy.getAttribute("data-copy")) {
      var source = document.querySelector(copy.getAttribute("data-copy"));
      value = source ? source.value || source.textContent : "";
    } else {
      value = copy.value || copy.textContent;
    }
    if (!value) {
      return;
    }
    copyText(value.trim()).then(function () {
      toast("Copied to the clipboard.", "success");
    });
  });

  window.verdictApi = { send: send, toast: toast, csrfToken: csrfToken, TOKEN: TOKEN };
})();
