(function () {
  "use strict";

  var root = document.querySelector("[data-voting]");
  if (!root) {
    return;
  }

  var apiUrl = root.getAttribute("data-api-url");
  var linkToken = root.getAttribute("data-link-token") || "";
  var emailTicket = root.getAttribute("data-email-ticket") || "";
  var message = root.querySelector("[data-voting-message]");
  var start = root.querySelector("[data-voting-start]");
  var form = root.querySelector("[data-voting-form]");
  var projectList = root.querySelector("[data-voting-projects]");
  var budgetNote = root.querySelector("[data-voting-budget]");
  var ballotId = "";

  function urlWithCredentials(url) {
    var query = [];
    if (linkToken) {
      query.push("v=" + encodeURIComponent(linkToken));
    }
    if (emailTicket) {
      query.push("email_ticket=" + encodeURIComponent(emailTicket));
    }
    return query.length ? url + "?" + query.join("&") : url;
  }

  function showMessage(text, isError) {
    message.textContent = text;
    message.className = isError ? "text-danger mb-3" : "text-muted-verdict mb-3";
  }

  function renderBallot(data) {
    ballotId = data.ballot;
    projectList.replaceChildren();
    // The per-project limit is a single-style rule; a quadratic ballot is bounded by its
    // credit budget only (n votes cost n*n credits), which the server enforces.
    var perProject = data.style === "quadratic"
      ? Math.max(1, Math.floor(Math.sqrt(data.credits)))
      : data.max_votes_per_project;
    data.projects.forEach(function (project) {
      var row = document.createElement("div");
      row.className = "row g-2 align-items-center border-bottom pb-2";
      var label = document.createElement("label");
      label.className = "col";
      label.textContent = project.title;
      var input = document.createElement("input");
      input.className = "form-control";
      input.type = "number";
      input.name = project.public_id;
      input.min = "0";
      input.max = String(perProject);
      input.value = String(project.votes || 0);
      input.setAttribute("aria-label", "Votes for " + project.title);
      label.appendChild(input);
      row.appendChild(label);
      projectList.appendChild(row);
    });
    budgetNote.textContent = data.style === "quadratic"
      ? "Credit cost: the sum of each project's votes squared; budget " + data.credits + "."
      : "Single voting: no more than one vote per project.";
    start.classList.add("d-none");
    form.classList.remove("d-none");
    showMessage("Ballot ready. Project order is fixed for this ballot.", false);
  }

  function send(method, url, payload) {
    var headers = { "Accept": "application/json", "Content-Type": "application/json" };
    var token = window.verdictApi ? window.verdictApi.csrfToken() : "";
    if (token) {
      headers["X-CSRFToken"] = token;
    }
    return window.fetch(url, {
      method: method,
      headers: headers,
      body: payload ? JSON.stringify(payload) : undefined,
      credentials: "same-origin"
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          throw new Error((body.error && body.error.message) || "Voting request failed.");
        }
        return body;
      });
    });
  }

  function credentials() {
    var body = {};
    if (linkToken) {
      body.link_token = linkToken;
    }
    if (emailTicket) {
      body.email_ticket = emailTicket;
    }
    return body;
  }

  start.addEventListener("click", function () {
    start.disabled = true;
    send("POST", urlWithCredentials(apiUrl), credentials())
      .then(renderBallot)
      .catch(function (error) {
        showMessage(error.message, true);
        start.disabled = false;
      });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var items = [];
    Array.prototype.forEach.call(projectList.querySelectorAll("input[name]"), function (input) {
      items.push({ project: input.name, votes: Number(input.value) });
    });
    var payload = credentials();
    payload.items = items;
    send("PUT", urlWithCredentials(apiUrl + "/" + encodeURIComponent(ballotId)), payload)
      .then(function () { showMessage("Your ballot was submitted.", false); })
      .catch(function (error) { showMessage(error.message, true); });
  });

  send("GET", urlWithCredentials(apiUrl))
    .then(renderBallot)
    .catch(function (error) {
      if (error.message !== "No ballot exists for this voter.") {
        showMessage(error.message, true);
      }
    });
})();
