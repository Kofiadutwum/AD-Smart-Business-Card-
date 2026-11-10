/* shop.js — count product opens and "Buy" taps on a shop page and in the marketplace.
   No cookies, no IDs: the server keeps a salted hash and one count per visitor, product and day. */
(function () {
  "use strict";

  function send(url, kind, product) {
    var body = JSON.stringify({ kind: kind, product: product });
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    } else {
      fetch(url, { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "application/json" } });
    }
  }

  window.shopBeacon = send; // the shop page's cart reports its Buy taps the same way

  var opened = {};
  document.addEventListener("click", function (event) {
    var shop = event.target.closest("[data-events-url]");
    if (!shop) return;
    var url = shop.getAttribute("data-events-url");
    var buy = event.target.closest("[data-buy]");
    if (buy) {
      send(url, "buy", buy.getAttribute("data-buy"));
      return;
    }
    // A tap on a product of a shop page counts as opening it, once per page load.
    var item = event.target.closest("[data-product]");
    var id = item && item.getAttribute("data-product");
    if (id && !opened[id]) {
      opened[id] = true;
      send(url, "product", id);
    }
  });

  // Arriving from the slider, the marketplace or a WhatsApp message with ?p=<id>: bring that product into view.
  var picked = document.querySelector(".biz-item--picked");
  // Instant, and after the pictures have loaded: the site's smooth scrolling is cancelled when the layout shifts under it.
  if (picked && picked.scrollIntoView) {
    var bring = function () { picked.scrollIntoView({ block: "center", behavior: "instant" }); };
    if (document.readyState === "complete") bring();
    else window.addEventListener("load", bring);
  }
})();
