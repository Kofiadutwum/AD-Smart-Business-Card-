/* site.js — small, dependency-free behaviour shared by every page. */
(function () {
  "use strict";

  var root = document.documentElement;

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) {
      return null;
    }
  }

  /* --- theme: system → light → dark (Section 18) --------------------------- */
  var toggles = document.querySelectorAll("[data-theme-toggle]");
  function applyTheme(mode) {
    if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
    else root.removeAttribute("data-theme");
    toggles.forEach(function (btn) {
      btn.setAttribute("data-mode", mode);
      var label = { system: "Theme: match device", light: "Theme: light", dark: "Theme: dark" }[mode];
      btn.setAttribute("aria-label", label + ". Change theme");
      btn.title = label;
    });
  }
  var mode = store("theme") || "system";
  applyTheme(mode);
  toggles.forEach(function (btn) {
    btn.addEventListener("click", function () {
      mode = { system: "light", light: "dark", dark: "system" }[mode] || "system";
      store("theme", mode === "system" ? null : mode);
      applyTheme(mode);
    });
  });

  /* --- header over the hero ------------------------------------------------ */
  var header = document.querySelector("[data-header]");
  if (header && header.classList.contains("site-header--hero")) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 24);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* --- mobile menu ---------------------------------------------------------- */
  var menuBtn = document.querySelector("[data-menu-toggle]");
  var menu = document.getElementById("mobile-menu");
  if (menuBtn && menu) {
    var setMenu = function (open) {
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
      menu.classList.toggle("is-open", open);
      if (header) header.classList.toggle("menu-open", open);
      menu.inert = !open;
    };
    setMenu(false);
    menuBtn.addEventListener("click", function () {
      setMenu(menuBtn.getAttribute("aria-expanded") !== "true");
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.classList.contains("is-open")) {
        setMenu(false);
        menuBtn.focus();
      }
    });
    menu.addEventListener("click", function (event) {
      if (event.target.closest("a")) setMenu(false);
    });
  }

  /* --- toasts: auto-dismiss after 5s, paused while hovered or focused ------ */
  document.querySelectorAll(".toast").forEach(function (toast) {
    var timer;
    var close = function () {
      toast.classList.add("is-leaving");
      setTimeout(function () {
        toast.remove();
      }, 260);
    };
    var arm = function () {
      if (toast.dataset.sticky === "true") return;
      timer = setTimeout(close, 5000);
    };
    toast.addEventListener("mouseenter", function () { clearTimeout(timer); });
    toast.addEventListener("mouseleave", arm);
    toast.addEventListener("focusin", function () { clearTimeout(timer); });
    var btn = toast.querySelector(".toast__close");
    if (btn) btn.addEventListener("click", close);
    arm();
  });

  /* --- copy to clipboard ------------------------------------------------------ */
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-copy]");
    if (!btn) return;
    var value = btn.getAttribute("data-copy");
    var target = value && value.charAt(0) === "#" ? document.querySelector(value) : null;
    var text = target ? target.value || target.textContent : value;
    var done = function () {
      var label = btn.querySelector("[data-copy-label]");
      if (!label) return;
      var original = label.textContent;
      label.textContent = "Copied";
      btn.setAttribute("aria-live", "polite");
      setTimeout(function () {
        label.textContent = original;
      }, 1800);
    };
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(done, function () {
        if (target && target.select) target.select();
      });
    } else if (target && target.select) {
      target.select();
      document.execCommand("copy");
      done();
    }
  });

  /* --- native share (SHR-13) --------------------------------------------------- */
  document.querySelectorAll("[data-share]").forEach(function (btn) {
    if (!navigator.share) {
      btn.hidden = true;
      return;
    }
    btn.addEventListener("click", function () {
      navigator.share({ title: btn.dataset.shareTitle || document.title, url: btn.dataset.share }).catch(function () {});
    });
  });

  /* --- lightbox for gallery pictures --------------------------------------------- */
  var lightbox = document.getElementById("lightbox");
  if (lightbox && lightbox.showModal) {
    var lbImg = lightbox.querySelector("img");
    var lbTitle = lightbox.querySelector("[data-lightbox-title]");
    var opener = null;
    var openLightbox = function (src, alt, title, from) {
      opener = from;
      lbImg.src = src;
      lbImg.alt = alt || "";
      lbTitle.textContent = title || "";
      lightbox.showModal();
    };
    // The gallery carousel opens its front picture through this as well.
    window.openLightbox = openLightbox;
    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-lightbox]");
      if (!trigger) return;
      event.preventDefault();
      openLightbox(
        trigger.getAttribute("data-lightbox"),
        trigger.getAttribute("data-lightbox-alt"),
        trigger.getAttribute("data-lightbox-title"),
        trigger
      );
    });
    lightbox.addEventListener("click", function (event) {
      if (event.target === lightbox || event.target.closest("[data-lightbox-close]")) lightbox.close();
    });
    lightbox.addEventListener("close", function () {
      if (opener) opener.focus();
    });
  }

  /* --- confirmation before destructive actions ------------------------------------- */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    // Loading state: disable the submit button so a payment or order is never sent twice.
    var submit = event.submitter || form.querySelector('[type="submit"]');
    if (submit && !form.hasAttribute("data-no-lock")) {
      setTimeout(function () {
        submit.setAttribute("aria-disabled", "true");
        submit.classList.add("is-loading");
        submit.disabled = true;
      }, 0);
    }
  });

  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("[type=submit].is-loading").forEach(function (btn) {
      btn.disabled = false;
      btn.classList.remove("is-loading");
      btn.removeAttribute("aria-disabled");
    });
  });

  /* --- NFC price calculator (mirrors apps/nfc/services.cards_price) ----------------- */
  function cardsPrice(quantity, tiers) {
    if (quantity < 1) return 0;
    for (var i = 0; i < tiers.length; i++) {
      var t = tiers[i];
      if (quantity >= t.min && (t.max === null || quantity <= t.max)) {
        if (t.mode === "incremental" && i > 0) {
          var base = t.min - 1;
          return cardsPrice(base, tiers.slice(0, i)) + t.unit * (quantity - base);
        }
        return t.unit * quantity;
      }
    }
    return null;
  }
  window.ADSmart = window.ADSmart || {};
  window.ADSmart.cardsPrice = cardsPrice;

  function ghs(minor) {
    return "GHS " + (minor / 100).toLocaleString("en-GH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  window.ADSmart.ghs = ghs;

  /* The same as the {% money %} tag: whole dollars without cents, otherwise two places. */
  function usd(minor, rate) {
    if (!rate) return "";
    var value = Math.round(minor / rate) / 100;
    var whole = value === Math.round(value);
    return "$" + value.toLocaleString("en-US", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
  }
  window.ADSmart.usd = usd;

  /* Fill a .money element: dollars large, cedis beneath; cedis alone without a rate. */
  function setMoney(el, minor, rate) {
    if (!el) return;
    var big = el.querySelector(".money__usd");
    var small = el.querySelector(".money__ghs");
    if (minor === null || minor === undefined) {
      big.textContent = "—";
      if (small) small.textContent = "";
      return;
    }
    big.textContent = rate ? usd(minor, rate) : ghs(minor);
    if (small) {
      small.textContent = rate ? ghs(minor) : "";
      small.hidden = !rate;
    }
  }
  window.ADSmart.setMoney = setMoney;

  document.querySelectorAll("[data-nfc-calc]").forEach(function (calc) {
    var tiers = JSON.parse(calc.querySelector('script[type="application/json"]').textContent);
    var rate = parseFloat(calc.getAttribute("data-rate") || "0");
    var input = calc.querySelector("input[type=number]");
    var total = calc.querySelector("[data-total]");
    var totalGhs = calc.querySelector("[data-total-ghs]");
    var each = calc.querySelector("[data-each]");
    var hint = calc.querySelector("[data-hint]");
    var update = function () {
      var q = Math.max(1, Math.min(500, parseInt(input.value || "1", 10) || 1));
      if (String(q) !== input.value) input.value = q;
      var price = cardsPrice(q, tiers);
      if (price === null) return;
      var per = Math.round(price / q);
      // Dollars large with the cedi total beneath; cedis alone when there is no rate.
      total.textContent = rate ? usd(price, rate) : ghs(price);
      if (totalGhs) totalGhs.textContent = rate ? ghs(price) : "";
      // The CSS shrinks a long total by its length so it stays beside the stepper.
      total.style.setProperty("--chars", String(total.textContent.length));
      // Only worth saying once there is more than one card.
      each.textContent = q > 1 ? (rate ? usd(per, rate) + " each (" + ghs(per) + ")" : ghs(per) + " each") : "";
      var next = cardsPrice(q + 1, tiers);
      if (hint) hint.hidden = !(next !== null && next <= price);
      calc.dispatchEvent(new CustomEvent("nfc:price", { detail: { quantity: q, price: price } }));
    };
    calc.querySelectorAll("[data-step]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        input.value = (parseInt(input.value || "1", 10) || 1) + parseInt(btn.getAttribute("data-step"), 10);
        update();
      });
    });
    input.addEventListener("input", update);
    update();
  });

  /* --- team plan price (mirrors Plan.price_for) --------------------------------------- */
  // The base price covers the first ``included`` people; each band adds its price for the
  // people inside it, and the bands stack, so a bigger team never pays less.
  function teamPrice(seats, team) {
    var n = Math.max(seats, team.included);
    var total = team.base;
    team.bands.forEach(function (band) {
      var top = band[1] === null ? n : Math.min(n, band[1]);
      if (top >= band[0]) total += (top - band[0] + 1) * band[2];
    });
    return total;
  }
  window.ADSmart.teamPrice = teamPrice;

  document.querySelectorAll("[data-team-calc]").forEach(function (box) {
    var team = JSON.parse(box.querySelector('script[type="application/json"]').textContent);
    var rate = parseFloat(box.getAttribute("data-rate") || "0");
    var input = box.querySelector("input[type=number]");
    var total = box.querySelector(".team-calc__total .money");
    var each = box.querySelector("[data-team-each]");
    var max = parseInt(input.getAttribute("max") || "1000", 10);
    // On a plan card, the "Choose" button carries the team size to checkout.
    var card = box.closest(".plan");
    var link = card ? card.querySelector("a[href*='plan=']") : null;
    var update = function (byUser) {
      var n = Math.max(team.included, Math.min(max, parseInt(input.value || "0", 10) || team.included));
      if (String(n) !== input.value && document.activeElement !== input) input.value = n;
      var price = teamPrice(n, team);
      setMoney(total, price, rate);
      var per = Math.round(price / n);
      each.textContent = (rate ? usd(per, rate) + " (" + ghs(per) + ")" : ghs(per)) + " per person a year";
      if (link) {
        var url = new URL(link.href, window.location.href);
        url.searchParams.set("seats", String(n));
        link.href = url.pathname + url.search;
      }
      box.dispatchEvent(new CustomEvent("team:price", { detail: { seats: n, price: price, byUser: !!byUser } }));
    };
    box.querySelectorAll("[data-step]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        input.value = (parseInt(input.value || "0", 10) || team.included) + parseInt(btn.getAttribute("data-step"), 10);
        update(true);
      });
    });
    input.addEventListener("input", function () { update(true); });
    // When the box loses focus, snap what was typed to the allowed range.
    input.addEventListener("change", function () {
      input.value = String(Math.max(team.included, Math.min(max, parseInt(input.value || "0", 10) || team.included)));
      update(true);
    });
    update(false);
  });

  /* --- swipe rows on phones: one dot per card, the one in view highlighted ---------------- */
  document.querySelectorAll("[data-slider]").forEach(function (list) {
    var dots = list.nextElementSibling;
    if (!dots || !dots.hasAttribute("data-slider-dots")) return;
    var items = Array.prototype.slice.call(list.children);
    items.forEach(function () {
      dots.appendChild(document.createElement("span"));
    });
    var update = function () {
      var stride = items.length > 1 ? items[1].offsetLeft - items[0].offsetLeft : 1;
      var index = stride > 0 ? Math.round(list.scrollLeft / stride) : 0;
      Array.prototype.forEach.call(dots.children, function (dot, i) {
        dot.classList.toggle("is-active", i === index);
      });
    };
    list.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  });

  /* --- cookie notice (PRV-05) ----------------------------------------------------------- */
  var notice = document.getElementById("cookie-notice");
  if (notice && !store("cookie-ok")) {
    notice.hidden = false;
    notice.querySelector("button").addEventListener("click", function () {
      store("cookie-ok", "1");
      notice.hidden = true;
    });
  }
})();
