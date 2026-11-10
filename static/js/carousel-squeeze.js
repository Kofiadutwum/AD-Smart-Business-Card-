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
     - a sideways swipe on a touch screen steps by one, and clicking the open
       card shows the full picture; on phones the arrows and the picture's
       own "View design" button are hidden (carousel.css)
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
    // Drift: the row glides on by itself, one design per `interval`, with no
    // rests between. A hover then only pauses it, so hover-grow is off.
    var drift = bool(opts.drift, false);
    var hoverGrow = bool(opts.hoverGrow, true) && !drift;
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
    // Drift state. `moveMs` is how long the move in hand takes (null = the
    // usual `duration`); `held` keeps the caption on the card that was in
    // front until the glide is half way; `driftToken` retires old glides.
    var holds = { hover: false, focus: false, hidden: false, away: "IntersectionObserver" in window };
    var moveMs = null;
    var held = null;
    var driftToken = 0;
    var drifting = false;
    var rest = null;

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
        else if (col === 0 && slide.image && window.openLightbox) {
          window.openLightbox(slide.image, slide.imageAlt, slide.title, el);
        }
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
      // Movement has its own length and easing; the caption fade keeps --sq-ms.
      inner.style.setProperty("--sq-move", (moveMs === null ? ms() : moveMs) + "ms");
      inner.style.setProperty("--sq-move-ease", moveMs === null ? "var(--sq-ease)" : "linear");
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
        el.style.transitionDuration = still ? "0s" : "var(--sq-move)";
        el.setAttribute("aria-selected", front ? "true" : "false");
        el.tabIndex = front ? 0 : -1;
        var overlay = el.querySelector(".sq__overlay");
        if (overlay) overlay.style.opacity = front ? "1" : "0";
      });

      strip.style.transform = "translateX(calc(" + slid + " * (var(--sq-slat) + var(--sq-gap))))";
      strip.style.transition = still ? "none" : "transform var(--sq-move) var(--sq-move-ease)";

      var shown = held === null ? open() : held;
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
    function settle(done) {
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
        if (done) done();
      });
    }

    // `auto` marks a step the drift took itself; anything else is a person
    // taking over, which ends the glide in hand and lets the row rest.
    function step(by, auto) {
      if (count < 2 || by === 0) return;
      if (!auto) {
        driftToken++;
        drifting = false;
        held = null;
        moveMs = null;
        clearTimeout(rest);
      }
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
      // A drift step is finished by its own glide (see driftStep), so that a
      // pause holds it; any other step is timed.
      if (!auto) {
        timers.push(
          setTimeout(function () {
            settle(function () {
              scheduleDrift(3000);
            });
          }, ms() + 40)
        );
      }
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

    /* --- drift ------------------------------------------------------------------- */
    function stopped() {
      return holds.hover || holds.focus || holds.hidden || holds.away;
    }

    function moving() {
      return strip.getAnimations ? strip.getAnimations({ subtree: true }) : [];
    }

    function scheduleDrift(wait) {
      clearTimeout(rest);
      if (drift && !reduced && count > 1 && !stopped() && !drifting) rest = setTimeout(driftStep, wait);
    }

    // One design per `interval`, back to back and at an even pace, so the row
    // never stops. The glide is the carousel's own move, only slower.
    function driftStep() {
      if (!drift || reduced || count < 2 || stopped() || drifting) return;
      drifting = true;
      var token = ++driftToken;
      held = open();
      moveMs = interval;
      step(1, true);
      var slide = moving().filter(function (a) {
        return a.transitionProperty === "transform";
      })[0];

      function release() {
        if (token !== driftToken || held === null) return;
        held = null;
        render();
      }
      function next() {
        if (token !== driftToken) return;
        moveMs = null;
        settle(function () {
          if (token !== driftToken) return;
          drifting = false;
          scheduleDrift(0);
        });
      }

      if (!slide) {
        timers.push(setTimeout(release, interval / 2));
        timers.push(setTimeout(next, interval + 40));
        return;
      }
      // The caption changes half way, when the new card is the bigger one.
      // Reading the glide's own clock means a pause holds the caption too.
      (function watch() {
        if (token !== driftToken || held === null) return;
        if (Number(slide.currentTime) >= interval / 2) release();
        else requestAnimationFrame(watch);
      })();
      slide.finished.then(next, function () {});
    }

    // Hovering, focus, a hidden tab or the row being off screen each hold the
    // glide where it is; letting go carries on from the same spot.
    function hold(name, on) {
      holds[name] = on;
      if (!drift) return;
      if (stopped()) {
        clearTimeout(rest);
        if (drifting) moving().forEach(function (a) { a.pause(); });
      } else {
        moving().forEach(function (a) {
          if (a.playState === "paused") a.play();
        });
        scheduleDrift(300);
      }
    }

    /* --- wiring ------------------------------------------------------------------ */
    strip.setAttribute("aria-label", opts.label || (window.ADSmart && window.ADSmart.t ? window.ADSmart.t("featured", "Featured") : "Featured"));

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

    // Touch screens: a sideways swipe steps one design either way. Upward and
    // downward drags are left to the page (touch-action: pan-y in the CSS).
    var viewport = inner.querySelector(".sq__viewport");
    var touch = null;
    var swiped = false;
    viewport.addEventListener("pointerdown", function (event) {
      swiped = false;
      touch = event.pointerType === "mouse" ? null : { x: event.clientX, y: event.clientY };
    });
    viewport.addEventListener("pointerup", function (event) {
      if (!touch) return;
      var dx = event.clientX - touch.x;
      var dy = event.clientY - touch.y;
      touch = null;
      if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy)) return;
      swiped = true;
      step(dx < 0 ? 1 : -1);
    });
    viewport.addEventListener("pointercancel", function () {
      touch = null;
    });
    // A swipe that ends on a card must not also open it or step to it.
    viewport.addEventListener(
      "click",
      function (event) {
        if (!swiped) return;
        swiped = false;
        event.preventDefault();
        event.stopPropagation();
      },
      true
    );

    inner.addEventListener("mouseenter", function () {
      paused = true;
      scheduleAutoplay();
      hold("hover", true);
    });
    inner.addEventListener("mouseleave", function () {
      paused = false;
      hover = -1;
      render();
      scheduleAutoplay();
      hold("hover", false);
    });
    inner.addEventListener("focusin", function (event) {
      paused = true;
      scheduleAutoplay();
      // Only keyboard focus holds the drift. A mouse click leaves focus on the
      // card or arrow, and that must not stop the row for good.
      if (event.target.matches(":focus-visible")) hold("focus", true);
    });
    inner.addEventListener("focusout", function () {
      paused = false;
      scheduleAutoplay();
      hold("focus", false);
    });
    document.addEventListener("visibilitychange", function () {
      paused = document.hidden;
      scheduleAutoplay();
      hold("hidden", document.hidden);
    });
    motion.addEventListener("change", function () {
      reduced = motion.matches;
      if (reduced && drifting) {
        // Motion is now unwelcome: end the glide where the row stands.
        driftToken++;
        drifting = false;
        held = null;
        moveMs = null;
        settle();
      }
      render();
      scheduleDrift(300);
    });
    if (drift) {
      // A caption that changes every few seconds should not be read out each time.
      panel.setAttribute("aria-live", "off");
      // Start only once the row is on screen, and stop when it leaves.
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(
          function (entries) {
            hold("away", !entries[entries.length - 1].isIntersecting);
          },
          { threshold: 0.25 }
        ).observe(inner);
      }
    }

    root.classList.add("is-ready");
    render();
    scheduleDrift(300);
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
