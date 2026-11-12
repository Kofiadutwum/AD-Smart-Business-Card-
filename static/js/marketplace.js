/* marketplace.js — the category sidebar of /shops slides in and out, and each shop's rows of products
   get their left and right arrows. Without this script the categories simply sit above the shops and
   the rows still scroll (the page works either way). */
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

  // Each row of products scrolls on its own. An arrow shows only while there is more to see in that direction;
  // one tap moves a screenful. Without this script the row still scrolls by touch, trackpad or keyboard.
  var calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  layout.querySelectorAll("[data-row]").forEach(function (row) {
    var track = row.querySelector("[data-row-track]");
    var prev = row.querySelector("[data-row-prev]");
    var next = row.querySelector("[data-row-next]");
    function update() {
      var rest = track.scrollWidth - track.clientWidth - track.scrollLeft;
      prev.hidden = track.scrollLeft < 2;
      next.hidden = rest < 2;
    }
    function go(direction) {
      track.scrollBy({ left: direction * track.clientWidth, behavior: calm.matches ? "auto" : "smooth" });
    }
    prev.addEventListener("click", function () { go(-1); });
    next.addEventListener("click", function () { go(1); });
    track.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  });
})();
