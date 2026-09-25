/* ==========================================================================
   Squeeze carousel — vanilla port of the supplied React component
   "carousel-squeeze.tsx" (SqueezeCarousel).

   Kept from the original:
     - four columns and a tail of slats; SHARES / STRETCHED / SQUEEZED
     - the row is a strip that slides; a card leaving the front narrows to a
       slat and carries on out of the left edge
     - hover widens the column under the pointer; neighbours give a little
     - clicking a column or slat steps to it; arrows and ←/→ keys step by one
     - copy and action button cross-fade under the panels
     - autoplay (off by default) pauses on hover, on focus and under reduced
       motion; reduced motion also makes every movement instant
     - ARIA: tablist of tabs, one tabpanel, aria-live polite

   Changed, deliberately:
     - stepping back mirrors stepping forward (the incoming card grows from
       a slat while the strip eases home) instead of racing a timer against
       an animation frame
     - after a keyboard step, focus follows the newly opened tab
   ========================================================================== */

(function () {
  "use strict";

  var SHARES = [-0.06, 0.61, 0.3, 0.15];
  /** The hovered column takes more room. */
  var STRETCHED = [0, 0.71, 0.4, 0.25];
  /** Its neighbours give a little up to pay for it. */
  var SQUEEZED = [-0.12, 0.59, 0.28, 0.13];

  var ARROW_BACK = "M9.6 2.6 5.1 7.1h9.1v1.8H5.1l4.5 4.5-1.2 1.2-6-6L1.8 8l.6-.6 6-6 1.2 1.2Z";
  var ARROW_NEXT = "M6.4 2.6l4.5 4.5H1.8v1.8h9.1l-4.5 4.5 1.2 1.2 6-6 .6-.6-.6-.6-6-6-1.2 1.2Z";

  var instance = 0;

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function bool(value, fallback) {
    if (value === undefined) return fallback;
    return value === "true" || value === "1";
  }

  function Squeeze(root) {
    var inner = root.querySelector(".sq__inner");
    var dataNode = root.querySelector("script[type=\"application/json\"]");
    if (!inner || !dataNode) return;

    var slides = JSON.parse(dataNode.textContent || "[]");
    var count = slides.length;
    if (!count) return;

    var opts = inner.dataset;
    var duration = parseInt(opts.duration || "1000", 10);
    var hoverGrow = bool(opts.hoverGrow, true);
    var autoplay = bool(opts.autoplay, false);
    var interval = parseInt(opts.interval || "6000", 10);
    var defaultIndex = clamp(parseInt(opts.defaultIndex || "0", 10), 0, count - 1);

    var id = "sq" + ++instance;
    var wrap = function (i) {
      return ((i % count) + count) % count;
    };
    // Four columns plus a tail of slats. Fewer slides, shorter tail.
    var slats = clamp(count - 4, 1, 3);
    var visible = 4 + slats;
    inner.style.setProperty("--sq-slats", String(slats));

    var motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    var reduced = motion.matches;
    var ms = function () {
      return reduced ? 0 : duration;
    };

    var strip = inner.querySelector(".sq__strip");
    var copies = Array.prototype.slice.call(inner.querySelectorAll(".sq__copy"));
    var panel = inner.querySelector(".sq__panel");
    panel.id = id + "-panel";

    /* --- state --------------------------------------------------------- */
    var seed = 0;
    var cards = [];
    for (var p = 0; p < visible; p++) cards.push({ key: seed++, slide: wrap(defaultIndex + p) });
    // Which column each card sits in: its place in the strip plus this.
    var column = 0;
    // How far the strip is slid, counted in slats.
    var slid = 0;
    var still = false;
    var hover = -1;
    var paused = false;
    var timers = [];
    var autoTimer = null;
    var lastOpen = -1;
    var nodes = new Map();
    // A back step waiting for its next frame: {raf, column, slid}.
    var pending = null;

    function open() {
      var card = cards[-column];
      return card ? card.slide : defaultIndex;
    }

    /* --- geometry -------------------------------------------------------- */
    function shareOf(col) {
      var hovering = hoverGrow && hover >= 0 && hover <= 3 && !reduced;
      if (!hovering) return SHARES[col];
      return hover === col ? STRETCHED[col] : SQUEEZED[col];
    }

    function widthOf(col) {
      if (col < 0 || col > 3) return "var(--sq-slat)";
      if (col === 0) return "calc(var(--sq-hero) + var(--sq-room) * " + shareOf(0) + ")";
      return "calc(var(--sq-room) * " + shareOf(col) + ")";
    }

    /* --- one card ---------------------------------------------------------- */
    function makeCard(card) {
      var slide = slides[card.slide];
      var el = document.createElement("button");
      el.type = "button";
      el.className = "sq__card";
      el.setAttribute("role", "tab");
      el.id = id + "-tab-" + card.key;
      el.setAttribute("aria-controls", panel.id);
      el.setAttribute("aria-label", slide.title);

      if (slide.image) {
        var img = document.createElement("img");
        img.className = "sq__img";
        img.src = slide.image;
        img.alt = slide.imageAlt || "";
        img.draggable = false;
        img.decoding = "async";
        if (slide.focus) img.style.objectPosition = "center " + slide.focus;
        el.appendChild(img);
      } else {
        var bg = document.createElement("span");
        bg.className = "sq__bg";
        bg.setAttribute("aria-hidden", "true");
        bg.style.background = slide.background || "var(--brand-gradient)";
        el.appendChild(bg);
      }

      if (slide.overlay) {
        var overlay = document.createElement("span");
        overlay.className = "sq__overlay";
        overlay.setAttribute("aria-hidden", "true");
        var mark = document.createElement("span");
        mark.className = "sq__mark";
        mark.textContent = slide.overlay;
        overlay.appendChild(mark);
        el.appendChild(overlay);
      }

      el.addEventListener("mousemove", function () {
        if (!hoverGrow) return;
        var col = colOf(card.key);
        if (col !== hover) {
          hover = col;
          render();
        }
      });
      el.addEventListener("click", function () {
        var col = colOf(card.key);
        if (col > 0) step(col);
      });
      return el;
    }

    function colOf(key) {
      for (var i = 0; i < cards.length; i++) if (cards[i].key === key) return i + column;
      return -99;
    }

    /* --- paint ----------------------------------------------------------------- */
    function render() {
      inner.style.setProperty("--sq-ms", ms() + "ms");
      var keep = new Set(cards.map(function (c) { return c.key; }));
      nodes.forEach(function (el, key) {
        if (!keep.has(key)) {
          el.remove();
          nodes.delete(key);
        }
      });

      cards.forEach(function (card, place) {
        var el = nodes.get(card.key);
        if (!el) {
          el = makeCard(card);
          nodes.set(card.key, el);
        }
        var at = strip.children[place];
        if (at !== el) strip.insertBefore(el, at || null);

        var col = place + column;
        var front = col === 0;
        var width = widthOf(col);
        el.style.width = width;
        el.style.marginLeft = place === 0 ? "0px" : col < 4 ? "var(--sq-gap)" : "var(--sq-slat-gap)";
        el.style.borderRadius = "min(var(--sq-radius), calc(" + width + " / 2))";
        el.style.transitionDuration = still ? "0s" : "var(--sq-ms)";
        el.setAttribute("aria-selected", front ? "true" : "false");
        el.tabIndex = front ? 0 : -1;
        var overlay = el.querySelector(".sq__overlay");
        if (overlay) overlay.style.opacity = front ? "1" : "0";
      });

      strip.style.transform = "translateX(calc(" + slid + " * (var(--sq-slat) + var(--sq-gap))))";
      strip.style.transition = still ? "none" : "transform var(--sq-ms) var(--sq-ease)";

      var shown = open();
      if (shown !== lastOpen) {
        copies.forEach(function (copy, index) {
          var on = index === shown;
          copy.classList.toggle("is-shown", on);
          copy.setAttribute("aria-hidden", on ? "false" : "true");
          var action = copy.querySelector(".sq__action");
          if (action) action.tabIndex = on ? 0 : -1;
        });
        lastOpen = shown;
        root.dispatchEvent(new CustomEvent("squeeze:change", { detail: { index: shown } }));
        scheduleAutoplay();
      }
    }

    function flush() {
      // Commit the current styles before the next change, so the browser
      // treats what follows as a transition from here.
      void strip.offsetWidth;
    }

    /* --- movement --------------------------------------------------------------- */
    // Once a movement has finished, cut the strip back to the cards on show
    // and put the numbers back to zero — the same picture, so nothing moves.
    function settle() {
      var at = -column;
      cards = cards.slice(at, at + visible);
      while (cards.length < visible) {
        cards.push({ key: seed++, slide: wrap(cards[cards.length - 1].slide + 1) });
      }
      column = 0;
      slid = 0;
      still = true;
      render();
      flush();
      requestAnimationFrame(function () {
        still = false;
        render();
      });
    }

    function step(by) {
      if (count < 2 || by === 0) return;
      timers.forEach(clearTimeout);
      timers = [];
      if (pending) {
        // A back step had not started moving yet; land it before going on.
        cancelAnimationFrame(pending.raf);
        still = false;
        column = pending.column;
        slid = pending.slid;
        pending = null;
      }
      if (by > 0) {
        // The incoming slats join the tail at full size before anything
        // moves, so the end of the row is never a slat short.
        var last = cards[cards.length - 1].slide;
        for (var k = 0; k < by; k++) cards.push({ key: seed++, slide: wrap(last + 1 + k) });
        column -= by;
        slid -= by;
        render();
      } else {
        // Going back, grow the strip at the front without anything moving
        // on screen, then let the columns and the strip ease home.
        var n = -by;
        var first = cards[0].slide;
        var fresh = [];
        for (var j = 0; j < n; j++) fresh.push({ key: seed++, slide: wrap(first - (n - j)) });
        cards = fresh.concat(cards);
        var homeColumn = column;
        var homeSlid = slid;
        column -= n;
        slid -= n;
        still = true;
        render();
        flush();
        pending = {
          column: homeColumn,
          slid: homeSlid,
          raf: requestAnimationFrame(function () {
            pending = null;
            still = false;
            column = homeColumn;
            slid = homeSlid;
            render();
          }),
        };
      }
      timers.push(setTimeout(settle, ms() + 40));
    }

    function go(to) {
      var here = open();
      if (to === here) return;
      var ahead = wrap(to - here);
      step(ahead <= count / 2 ? ahead : ahead - count);
    }

    /* --- autoplay ---------------------------------------------------------------- */
    function scheduleAutoplay() {
      clearTimeout(autoTimer);
      if (!autoplay || paused || reduced || count < 2) return;
      autoTimer = setTimeout(function () {
        step(1);
      }, interval);
    }

    /* --- wiring ------------------------------------------------------------------ */
    strip.setAttribute("aria-label", opts.label || "Featured");

    strip.addEventListener("keydown", function (event) {
      var by = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!by) return;
      event.preventDefault();
      step(by);
      requestAnimationFrame(function () {
        var front = strip.querySelector('[aria-selected="true"]');
        if (front) front.focus({ preventScroll: true });
      });
    });

    var prev = inner.querySelector("[data-sq-prev]");
    var next = inner.querySelector("[data-sq-next]");
    if (count < 2) {
      if (prev) prev.parentNode.hidden = true;
    } else {
      if (prev) prev.addEventListener("click", function () { step(-1); });
      if (next) next.addEventListener("click", function () { step(1); });
    }

    inner.addEventListener("mouseenter", function () {
      paused = true;
      scheduleAutoplay();
    });
    inner.addEventListener("mouseleave", function () {
      paused = false;
      hover = -1;
      render();
      scheduleAutoplay();
    });
    inner.addEventListener("focusin", function () {
      paused = true;
      scheduleAutoplay();
    });
    inner.addEventListener("focusout", function () {
      paused = false;
      scheduleAutoplay();
    });
    document.addEventListener("visibilitychange", function () {
      paused = document.hidden;
      scheduleAutoplay();
    });
    motion.addEventListener("change", function () {
      reduced = motion.matches;
      render();
    });

    root.classList.add("is-ready");
    render();
    root.squeeze = { step: step, go: go, open: open };
  }

  function init() {
    document.querySelectorAll("[data-squeeze-root]").forEach(Squeeze);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.SqueezeCarousel = { init: init, ARROW_BACK: ARROW_BACK, ARROW_NEXT: ARROW_NEXT };
})();
