/* card.js — record clicks on a public card (Section 11). No cookies, no IDs. */
(function () {
  "use strict";
  var card = document.querySelector("[data-card]");
  if (!card) return;
  var url = card.getAttribute("data-events-url");
  var params = new URLSearchParams(window.location.search);
  var src = params.get("src") || params.get("s") || "link";

  card.addEventListener("click", function (event) {
    var link = event.target.closest("[data-track]");
    if (!link || link.hasAttribute("data-server-tracked")) return;
    var body = JSON.stringify({
      kind: link.getAttribute("data-track"),
      detail: link.getAttribute("data-detail") || "",
      src: src,
    });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    } else {
      fetch(url, { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "application/json" } });
    }
  });
})();
