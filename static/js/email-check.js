/* Checks an email address while it is typed: spelling, popular-domain typos, a mail server, and whether it is taken.
   The words come from the server (/auth/check-email), so they are in the customer's language. */
(function () {
  document.querySelectorAll("input[data-email-check]").forEach(function (input) {
    var note = document.createElement("p");
    note.className = "field-help";
    note.setAttribute("aria-live", "polite");
    input.insertAdjacentElement("afterend", note);
    var timer = null;
    var latest = 0;

    function show(data) {
      note.textContent = data.message || "";
      note.className = data.status === "error" ? "field-error" : data.status === "ok" ? "field-help field-help--ok" : "field-help";
      input.setAttribute("aria-invalid", data.status === "error" ? "true" : "false");
      if (data.suggestion) {
        note.appendChild(document.createTextNode(" "));
        var use = document.createElement("button");
        use.type = "button";
        use.textContent = data.use;
        use.addEventListener("click", function () {
          input.value = data.suggestion;
          input.focus();
          check();
        });
        note.appendChild(use);
      }
    }

    function check() {
      var value = input.value.trim();
      var ticket = ++latest;
      fetch(input.getAttribute("data-email-check") + "?email=" + encodeURIComponent(value), { credentials: "same-origin" })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) { if (data && ticket === latest) show(data); })
        .catch(function () {});
    }

    input.addEventListener("input", function () {
      clearTimeout(timer);
      latest++;
      note.textContent = "";
      note.className = "field-help";
      input.removeAttribute("aria-invalid");
      // Wait until it looks like name@domain.tld, then until the typing pauses.
      if (/^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$/.test(input.value.trim())) timer = setTimeout(check, 450);
    });
  });
})();
