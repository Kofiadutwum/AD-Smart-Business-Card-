/* marketplace.js — the category sidebar of /shops slides in and out. Without this script the
   categories simply sit above the shops (the page works either way). */
(function () {
  "use strict";
  var layout = document.querySelector("[data-shops]");
  if (!layout) return;
  var toggle = layout.querySelector("[data-cat-toggle]");
  var scrim = layout.querySelector("[data-cat-scrim]");
  var wide = window.matchMedia("(min-width: 60rem)");
  var KEY = "shopsCats";

  function remembered() {
    try { return window.sessionStorage.getItem(KEY); } catch (e) { return null; }
  }
  function remember(value) {
    try { window.sessionStorage.setItem(KEY, value); } catch (e) {}
  }
  function show(open) {
    layout.setAttribute("data-cats", open ? "open" : "closed");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function isOpen() {
    return layout.getAttribute("data-cats") === "open";
  }

  // Open on a desktop (or as the visitor left it), closed on a phone, where it covers the page.
  show(wide.matches ? remembered() !== "closed" : false);

  toggle.addEventListener("click", function () {
    var open = !isOpen();
    show(open);
    if (wide.matches) remember(open ? "open" : "closed");
  });
  scrim.addEventListener("click", function () { show(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !wide.matches && isOpen()) {
      show(false);
      toggle.focus();
    }
  });
  wide.addEventListener("change", function () {
    show(wide.matches ? remembered() !== "closed" : false);
  });
})();
