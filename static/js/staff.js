/* Staff area motion (loaded by templates/staff/_shell.html). One highlight slides between the buttons of a switch,
   the Overview tiles count up, the button that sent a form pulses until the next page arrives, and a notice's
   time-left bar starts again after a pause, as its timer does (static/js/site.js). Page changes, rows, tiles and
   notices move in staff.css. All of it is extra: without scripting or with reduced motion the pages read the same. */
(function () {
  "use strict";
  var still = !window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* --- switches: one highlight glides to the pressed button ------------------------------------------ */
  function place(group, thumb, glide) {
    var on = group.querySelector('button[aria-pressed="true"]');
    if (!on || !group.clientWidth) return;
    var top = on.offsetTop, left = on.offsetLeft;
    var right = group.clientWidth - left - on.offsetWidth, bottom = group.clientHeight - top - on.offsetHeight;
    thumb.classList.toggle("is-gliding", glide && !still);
    thumb.style.clipPath = "inset(" + top + "px " + right + "px " + bottom + "px " + left + "px round var(--radius-sm))";
    group.classList.add("has-thumb");
  }
  Array.prototype.forEach.call(document.querySelectorAll(".segmented"), function (group) {
    var thumb = document.createElement("span"), glide = false;
    thumb.className = "segmented__thumb";
    thumb.setAttribute("aria-hidden", "true");
    group.insertBefore(thumb, group.firstChild);
    // Capture runs before the button's own handler changes aria-pressed, so only a person's press glides.
    group.addEventListener("click", function () { glide = true; }, true);
    new MutationObserver(function () {
      place(group, thumb, glide);
      glide = false;
    }).observe(group, { subtree: true, attributes: true, attributeFilter: ["aria-pressed"] });
    if (window.ResizeObserver) new ResizeObserver(function () { place(group, thumb, false); }).observe(group); // also fires when a hidden switch appears
    place(group, thumb, false);
  });

  /* --- figures count up and bars grow each time they come onto the screen ----------------------------- */
  // Tiles (.stat__value), totals and figures marked data-count, tab counts and progress bars (the finance
  // report's months). They play whenever most of one comes onto the screen, again after any part of it left
  // (the owner's choice); the Overview graphs do the same in staff-charts.js.
  var NUMBER = /\d[\d,]*(?:\.\d+)?/g; // "1,250.00"; only marked elements are read, so dates and references never count

  // Returns a function that counts every number in the element up from zero to the figure the server wrote.
  function counter(element) {
    var walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT), runs = [];
    while (walker.nextNode()) {
      var node = walker.currentNode, numbers = node.nodeValue.match(NUMBER);
      if (!numbers) continue;
      runs.push({
        node: node,
        pieces: node.nodeValue.split(NUMBER),
        numbers: numbers.map(function (raw) {
          var decimals = (raw.split(".")[1] || "").length;
          return {
            end: parseFloat(raw.replace(/,/g, "")),
            format: new Intl.NumberFormat("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: raw.indexOf(",") >= 0 })
          };
        })
      });
    }
    return function () {
      var start = null;
      cancelAnimationFrame(element.adCount);
      function frame(now) {
        if (start === null) start = now;
        var p = Math.min(1, (now - start) / 1100), e = 1 - Math.pow(1 - p, 4);
        runs.forEach(function (run) {
          run.node.nodeValue = run.pieces.map(function (piece, k) {
            return k < run.numbers.length ? piece + run.numbers[k].format.format(run.numbers[k].end * e) : piece;
          }).join("");
        });
        if (p < 1) element.adCount = requestAnimationFrame(frame);
      }
      element.adCount = requestAnimationFrame(frame);
    };
  }
  // Returns a function that grows the bar again from nothing.
  function grower(bar) {
    return function () {
      bar.classList.add("is-start");
      bar.getBoundingClientRect(); // lay out the empty bar before letting it grow
      requestAnimationFrame(function () { bar.classList.remove("is-start"); });
    };
  }
  if (!still) {
    var players = [];
    Array.prototype.forEach.call(document.querySelectorAll(".stat__value, .stat__hint, [data-count], .tabs .count"), function (element) {
      element.adPlay = counter(element);
      players.push(element);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".app-main .progress"), function (bar) {
      bar.adPlay = grower(bar);
      players.push(bar);
    });
    var watch = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var target = entry.target;
        if (entry.intersectionRatio < 0.6) target.adAway = true;
        else if (target.adAway) {
          target.adAway = false;
          target.adPlay();
        }
      });
    }, { threshold: [0, 0.6] }) : null;
    players.forEach(function (element) {
      element.adAway = true;
      if (watch) watch.observe(element);
      else element.adPlay();
    });
  }

  /* --- the button that sent a form pulses until the next page arrives -------------------------------- */
  function settle() {
    Array.prototype.forEach.call(document.querySelectorAll(".is-sending"), function (button) {
      button.classList.remove("is-sending");
      button.removeAttribute("aria-busy");
    });
  }
  document.addEventListener("submit", function (event) {
    var button = event.submitter;
    if (event.defaultPrevented || !button || !button.classList) return;
    button.classList.add("is-sending");
    button.setAttribute("aria-busy", "true");
    setTimeout(settle, 8000); // a form that downloads a file never leaves the page
  });
  window.addEventListener("pageshow", settle); // coming back with the Back button

  /* --- a notice's time-left bar starts again when the pointer leaves, as its timer does --------------- */
  Array.prototype.forEach.call(document.querySelectorAll('.toast:not([data-sticky="true"])'), function (toast) {
    toast.classList.add("is-timing");
    toast.addEventListener("mouseleave", function () {
      toast.classList.remove("is-timing");
      void toast.offsetWidth; // restart the bar's animation
      toast.classList.add("is-timing");
    });
  });
})();
