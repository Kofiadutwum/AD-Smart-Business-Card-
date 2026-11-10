/* storefront.js — the shop page: search, category chips, the big product view and the order cart.
   Nothing here is needed to read the page or to tap Buy: without JavaScript the grid and Buy still work.
   The cart lives only in this phone's localStorage. The shop hears of it when the shopper presses Send in WhatsApp. */
(function () {
  "use strict";

  var shop = document.querySelector(".shop");
  var grid = shop && shop.querySelector("[data-grid]");
  if (!grid) return;

  var items = Array.prototype.slice.call(grid.querySelectorAll(".biz-item"));

  // ---- search and category chips ------------------------------------------------

  function norm(text) {
    return String(text || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  function words(text) {
    return norm(text).match(/[\p{L}\p{N}_]+/gu) || [];
  }

  // Optimal string alignment: a swapped pair of letters ("castel") counts as one slip, like a typo should.
  function slips(a, b) {
    var prev2 = null, prev = [], row, i, j, cost;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      row = [i];
      for (j = 1; j <= b.length; j++) {
        cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
        row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost);
        if (prev2 && i > 1 && j > 1 && a.charAt(i - 1) === b.charAt(j - 2) && a.charAt(i - 2) === b.charAt(j - 1)) {
          row[j] = Math.min(row[j], prev2[j - 2] + 1);
        }
      }
      prev2 = prev;
      prev = row;
    }
    return prev[b.length];
  }

  // shortcut: the same rule as the /shops search (a word of 4+ letters may be 25% off), but edit distance, not difflib.
  function hit(word, list) {
    var i;
    for (i = 0; i < list.length; i++) if (list[i].indexOf(word) !== -1) return true;
    if (word.length < 4) return false;
    for (i = 0; i < list.length; i++) {
      if (1 - slips(word, list[i]) / Math.max(word.length, list[i].length) >= 0.75) return true;
    }
    return false;
  }

  var index = items.map(function (li) { return words(li.getAttribute("data-search")); });
  var tools = shop.querySelector("[data-tools]");
  var input = shop.querySelector("[data-search]");
  var empty = shop.querySelector("[data-empty]");
  var query = [];
  var category = "";
  if (tools) tools.hidden = false;

  function filter() {
    var shown = 0;
    items.forEach(function (li, n) {
      var ok = (!category || li.getAttribute("data-cat") === category) &&
        query.every(function (word) { return hit(word, index[n]); });
      li.hidden = !ok;
      if (ok) shown++;
    });
    if (empty) empty.hidden = shown > 0;
  }

  if (input) {
    input.addEventListener("input", function () {
      query = words(input.value).slice(0, 8);
      filter();
    });
  }
  shop.addEventListener("click", function (event) {
    var chip = event.target.closest(".shop-chip");
    if (!chip) return;
    category = chip.getAttribute("data-cat");
    Array.prototype.forEach.call(chip.parentNode.children, function (other) {
      other.setAttribute("aria-pressed", other === chip ? "true" : "false");
    });
    filter();
  });

  // ---- the order cart -----------------------------------------------------------------

  var bar = document.querySelector("[data-cart]");
  var base = shop.getAttribute("data-cart-url");
  var cartKey = "shopcart:" + shop.getAttribute("data-cart-key");
  var cart = {};
  var send = bar && bar.querySelector("[data-cart-send]");

  function saveCart() {
    try { localStorage.setItem(cartKey, JSON.stringify(cart)); } catch (e) { /* private mode: the cart just lasts this visit */ }
  }

  function message() {
    var lines = [shop.getAttribute("data-cart-intro")];
    items.forEach(function (li) {
      var count = cart[li.getAttribute("data-product")];
      var price = li.getAttribute("data-price");
      if (count) lines.push(count + " × " + li.getAttribute("data-title") + (price ? " (" + price + ")" : ""));
    });
    lines.push(shop.getAttribute("data-page-url"));
    return lines.join("\n");
  }

  function button(className, act, label, aria) {
    var b = document.createElement("button");
    b.type = "button";
    if (className) b.className = className;
    b.setAttribute("data-act", act);
    b.textContent = label;
    if (aria) b.setAttribute("aria-label", aria);
    return b;
  }

  function control(slot) {
    var id = slot.getAttribute("data-id");
    var count = cart[id] || 0;
    var t = bar.dataset;
    slot.textContent = "";
    if (!count) {
      slot.appendChild(button("biz-add", "add", t.tAdd, ""));
      return;
    }
    var step = document.createElement("span");
    step.className = "biz-step";
    step.appendChild(button("", "less", "−", t.tLess));
    var out = document.createElement("output");
    out.textContent = count;
    step.appendChild(out);
    step.appendChild(button("", "more", "+", t.tMore));
    slot.appendChild(step);
  }

  function render() {
    var total = 0;
    Object.keys(cart).forEach(function (id) { total += cart[id]; });
    Array.prototype.forEach.call(shop.querySelectorAll("[data-cart-slot]"), control);
    bar.hidden = total === 0;
    document.body.classList.toggle("has-cart", total > 0);
    bar.querySelector("[data-cart-count]").textContent = total;
    // Only a chat link that takes a typed message can carry the order; the server has checked that it does.
    send.href = base.replace(/([?&]text=)[^&]*/, function (found, start) { return start + encodeURIComponent(message()); });
  }

  function setCount(id, count) {
    count = Math.max(0, Math.min(99, count));
    if (count) cart[id] = count; else delete cart[id];
    saveCart();
    render();
  }

  if (bar && base && send) {
    try {
      var saved = JSON.parse(localStorage.getItem(cartKey) || "{}");
      items.forEach(function (li) {
        var id = li.getAttribute("data-product");
        var count = parseInt(saved[id], 10);
        if (count > 0) cart[id] = Math.min(count, 99);
      });
    } catch (e) { cart = {}; }
    items.forEach(function (li) {
      var slot = li.querySelector("[data-cart-slot]");
      if (slot) slot.setAttribute("data-id", li.getAttribute("data-product"));
    });

    shop.addEventListener("click", function (event) {
      var act = event.target.closest("[data-act]");
      var slot = act && act.closest("[data-cart-slot]");
      if (!slot) return;
      var id = slot.getAttribute("data-id");
      var count = cart[id] || 0;
      setCount(id, act.getAttribute("data-act") === "less" ? count - 1 : count + 1);
    });
    bar.querySelector("[data-cart-clear]").addEventListener("click", function () {
      cart = {};
      saveCart();
      render();
    });
    send.addEventListener("click", function () {
      var url = shop.getAttribute("data-events-url");
      if (url && window.shopBeacon) Object.keys(cart).forEach(function (id) { window.shopBeacon(url, "buy", id); });
    });
    render();
  }

  // ---- the big product view ---------------------------------------------------------------

  var dialog = shop.querySelector("[data-dialog]");
  if (!dialog || typeof dialog.showModal !== "function") return; // an old browser keeps the plain grid

  var pictures = [];
  var shown = 0;
  var prev = dialog.querySelector("[data-d-prev]");
  var next = dialog.querySelector("[data-d-next]");
  var count = dialog.querySelector("[data-d-count]");

  function showPicture(n) {
    shown = (n + pictures.length) % pictures.length;
    dialog.querySelector("[data-d-img]").src = pictures[shown];
    var several = pictures.length > 1;
    prev.hidden = next.hidden = count.hidden = !several;
    count.textContent = (shown + 1) + " / " + pictures.length;
  }

  prev.addEventListener("click", function () { showPicture(shown - 1); });
  next.addEventListener("click", function () { showPicture(shown + 1); });
  dialog.addEventListener("keydown", function (event) {
    if (pictures.length < 2 || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    showPicture(shown + (event.key === "ArrowRight" ? 1 : -1));
  });
  var swipe = null;
  var pic = dialog.querySelector("[data-d-pic]");
  pic.addEventListener("pointerdown", function (event) {
    swipe = event.pointerType === "mouse" ? null : event.clientX;
  });
  pic.addEventListener("pointerup", function (event) {
    if (swipe === null || pictures.length < 2) return;
    var dx = event.clientX - swipe;
    swipe = null;
    if (Math.abs(dx) > 40) showPicture(shown + (dx < 0 ? 1 : -1));
  });

  function openItem(li) {
    var picture = li.querySelector("img");
    var id = li.getAttribute("data-product");
    var price = li.getAttribute("data-price");
    var text = li.querySelector(".biz-text");
    var body = dialog.querySelector("[data-d-text]");
    var actions = dialog.querySelector("[data-d-actions]");
    var buy = li.querySelector(".biz-buy");
    var slot = li.querySelector("[data-cart-slot]");

    var img = dialog.querySelector("[data-d-img]");
    img.alt = picture.alt;
    var more = [];
    try { more = JSON.parse(li.getAttribute("data-more") || "[]"); } catch (e) { /* one picture only */ }
    pictures = [picture.getAttribute("src")].concat(more);
    more.forEach(function (url) { new Image().src = url; }); // fetch the others now, so an arrow tap shows at once
    showPicture(0);
    dialog.querySelector("[data-d-title]").textContent = li.getAttribute("data-title");
    var priceEl = dialog.querySelector("[data-d-price]");
    priceEl.textContent = price;
    priceEl.hidden = !price;
    body.textContent = "";
    if (text) Array.prototype.forEach.call(text.childNodes, function (node) { body.appendChild(node.cloneNode(true)); });
    actions.textContent = "";
    if (buy) actions.appendChild(buy.cloneNode(true)); // the copy keeps data-buy, so shop.js counts its taps too
    if (slot && bar) {
      var copy = document.createElement("span");
      copy.className = "biz-cart";
      copy.setAttribute("data-cart-slot", "");
      copy.setAttribute("data-id", id);
      actions.appendChild(copy);
      control(copy);
    }
    document.documentElement.classList.add("shop-dialog-open");
    dialog.showModal();
  }

  grid.addEventListener("click", function (event) {
    var link = event.target.closest("[data-open]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    openItem(link.closest(".biz-item"));
  });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
  });
  dialog.addEventListener("close", function () {
    document.documentElement.classList.remove("shop-dialog-open");
  });

  // Arriving with ?p=<id> from the slider, /shops or a WhatsApp message: show that product.
  var picked = grid.querySelector(".biz-item--picked");
  if (picked) openItem(picked);
})();
