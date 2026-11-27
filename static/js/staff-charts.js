/* The Overview graphs (staff home). Data: the "staff-charts" JSON from apps/staff/views/overview.py.
   Lines draw in, bars spring up and totals count up when a graph comes into view (the figures count again each
   time it comes back onto the screen); switching between Ghana and
   outside Ghana moves the figures in place. Hover, tap or the arrow keys read a point; a click (a second tap on a
   phone, or Enter) opens the list behind it; dragging across a graph zooms into that stretch, "Show all" goes back.
   With reduced motion everything appears still. The span and region switches keep their state in the address bar. */
(function () {
  "use strict";
  var boxes = Array.prototype.slice.call(document.querySelectorAll("[data-chart]"));
  if (!boxes.length) return;

  var SVG = "http://www.w3.org/2000/svg";
  var WHEN = { "7d": "in the last 7 days", "30d": "in the last 30 days", "12m": "in the last 12 months" };
  var WHERE = { all: "", ghana: ", Ghana", abroad: ", outside Ghana" };
  var HINT = { mouse: "Click to open the list", touch: "Tap again to open the list", pen: "Tap again to open the list", key: "Press Enter to open the list" };
  var FAILED = "This graph could not load. Reload the page to try again.";
  var still = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: true };
  var whole = new Intl.NumberFormat("en-GB");
  var cedis = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var params = new URLSearchParams(window.location.search);
  var region = ["ghana", "abroad"].indexOf(params.get("region")) >= 0 ? params.get("region") : "all";
  var charts = null;
  try {
    charts = JSON.parse(document.getElementById("staff-charts").textContent);
  } catch (e) {
    charts = null;
  }

  function remember(key, value, fallback) {
    if (value === fallback) params.delete(key);
    else params.set(key, value);
    var query = params.toString();
    history.replaceState(null, "", window.location.pathname + (query ? "?" + query : "") + window.location.hash);
  }

  function trim(n) {
    return String(Math.round(n * 10) / 10);
  }
  function figure(chart, value, short) {
    if (value === null || value === undefined) return "";
    if (!chart.money) return whole.format(value);
    var cedi = value / 100;
    if (short) {
      if (cedi >= 1e6) return "GHS " + trim(cedi / 1e6) + "m";
      if (cedi >= 1e3) return "GHS " + trim(cedi / 1e3) + "k";
      return "GHS " + whole.format(Math.round(cedi));
    }
    return "GHS " + cedis.format(cedi);
  }
  function when(chart, label, long) {
    if (chart.categories) return label;
    var p = label.split("-").map(Number);
    var day = new Date(Date.UTC(p[0], p[1] - 1, p[2] || 1));
    var options = p.length === 2
      ? (long ? { month: "long", year: "numeric" } : { month: "short" })
      : (long ? { weekday: "short", day: "numeric", month: "short" } : { day: "numeric", month: "short" });
    options.timeZone = "UTC";
    return day.toLocaleDateString("en-GB", options);
  }
  // Four ticks of a round step: 1, 1.5, 2, 2.5, 3, 4, 5, 6 or 8 times a power of ten, never a fraction of a count.
  function step(max) {
    var raw = Math.max(max, 4) / 4;
    var size = Math.pow(10, Math.floor(Math.log10(raw)));
    var steps = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] * size >= raw && (steps[i] * size) % 1 === 0) return steps[i] * size;
    }
    return 10 * size;
  }
  // The list behind a day ("2026-11-03") or a month ("2026-11"): the graph's link with its dates and region filled in.
  function listUrl(chart, label) {
    var p = label.split("-"), from = label, to = label;
    if (p.length === 2) {
      from = label + "-01";
      to = label + "-" + ("0" + new Date(Date.UTC(+p[0], +p[1], 0)).getUTCDate()).slice(-2);
    }
    return chart.link.replace("{from}", from).replace("{to}", to).replace("{region}", region === "all" ? "" : region);
  }

  /* --- motion ---------------------------------------------------------------------------------------- */
  function cubicOut(p) { return 1 - Math.pow(1 - p, 3); }
  function backOut(p) { var c = 1.70158; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); } // overshoots, then settles
  // Runs frame(progress) each animation frame for ms; a new tween on the same node replaces the running one.
  function tween(node, ms, frame) {
    cancelAnimationFrame(node.adTween);
    var start = null;
    function tick(now) {
      if (start === null) start = now;
      var p = Math.min(1, (now - start) / ms);
      frame(p);
      if (p < 1) node.adTween = requestAnimationFrame(tick);
    }
    node.adTween = requestAnimationFrame(tick);
  }
  function countUp(node, chart, from, to, animate) {
    if (!animate || from === to) {
      cancelAnimationFrame(node.adTween);
      node.textContent = figure(chart, to);
      return;
    }
    tween(node, 900, function (p) { node.textContent = figure(chart, Math.round(from + (to - from) * cubicOut(p))); });
  }

  function el(name, attributes, parent) {
    var node = document.createElementNS(SVG, name);
    for (var key in attributes) node.setAttribute(key, attributes[key]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function text(parent, value, x, y, anchor, css) {
    el("text", { x: x, y: y, "text-anchor": anchor, "class": css }, parent).textContent = value;
  }
  function note(plot, message, error) {
    var p = document.createElement("p");
    p.className = "chart__note" + (error ? " chart__note--error" : "");
    p.textContent = message;
    plot.appendChild(p);
  }
  function largest(chart, values) {
    var top = 0;
    chart.series.forEach(function (series) {
      values[series.key].forEach(function (v) { top = Math.max(top, v || 0); });
    });
    return top;
  }

  /* --- a graph over time: bars (one series) or lines (one to three series) ------------------------- */
  // hooks.zoom(first, last) zooms to a dragged stretch; hooks.open(label) opens the list behind a point.
  function drawTime(plot, chart, data, hooks) {
    var width = Math.max(280, Math.round(plot.clientWidth));
    var height = Math.round(Math.min(260, Math.max(180, width * 0.36)));
    var keys = chart.series.map(function (s) { return s.key; });
    var bars = chart.kind === "bar", multi = keys.length > 1, n = data.labels.length;
    var named = multi && width >= 480; // on a phone the legend above names the lines; end labels would squeeze the plot
    var pad = { t: 10, r: named ? 100 : 8, b: 26, l: chart.money ? 58 : 34 };
    var w = width - pad.l - pad.r, h = height - pad.t - pad.b, base = pad.t + h, band = w / n;
    var max = 1;
    function x(i) { return bars ? pad.l + band * (i + 0.5) : pad.l + (n > 1 ? (w * i) / (n - 1) : w / 2); }
    function y(v) { return base - (h * Math.max(0, v || 0)) / max; }

    var svg = el("svg", {
      viewBox: "0 0 " + width + " " + height, width: width, height: height, "class": "chart__svg" + (hooks.open ? " is-linked" : ""),
      role: "img", tabindex: "0",
      "aria-label": chart.title + ". Arrow keys read each point" + (hooks.open ? ", Enter opens its list" : "") + "; the table below lists every figure."
    });
    var ticks = el("g", { "class": "chart__ticks" }, svg), tickNodes = [];
    for (var t = 0; t <= 4; t++) {
      var gy = base - (h * t) / 4;
      el("line", { x1: pad.l, x2: pad.l + w, y1: gy, y2: gy, "class": t ? "chart__grid" : "chart__base" }, svg);
      tickNodes.push(el("text", { x: pad.l - 8, y: gy + 4, "text-anchor": "end", "class": "chart__tick" }, ticks));
    }
    function axis(values) { // labels the grid for these figures and returns the top of the scale
      var tick = step(largest(chart, values));
      tickNodes.forEach(function (node, k) { node.textContent = figure(chart, tick * k, true); });
      return tick * 4;
    }
    var stride = Math.ceil(n / Math.max(1, Math.floor(w / 64)));
    for (var i = n - 1; i >= 0; i -= stride) {
      var anchor = bars ? "middle" : x(i) > pad.l + w - 24 ? "end" : x(i) < pad.l + 24 ? "start" : "middle";
      text(svg, when(chart, data.labels[i]), x(i), height - 6, anchor, "chart__tick");
    }

    var marks = el("g", {}, svg), lines = [], barNodes = [];
    keys.forEach(function (key, s) {
      if (!bars) {
        lines.push(el("path", { pathLength: "1", "class": "chart__line series-" + (s + 1) }, marks));
        return;
      }
      for (var k = 0; k < n; k++) {
        barNodes.push(el("path", { "class": "chart__bar series-" + (s + 1), style: "transition-delay:" + Math.min(k * 28, 520) + "ms,0ms" }, marks));
      }
    });
    var ends = named ? keys.map(function (key, s) { // a name at the end of each line
      var node = el("text", { x: pad.l + w + 8, "text-anchor": "start", "class": "chart__end" }, svg);
      node.textContent = chart.series[s].label;
      return { key: key, node: node };
    }) : [];

    // Places every mark for these figures on the current scale; the morph calls it each frame.
    function paint(values) {
      lines.forEach(function (line, s) {
        line.setAttribute("d", values[keys[s]].map(function (v, k) { return (k ? "L" : "M") + x(k).toFixed(1) + "," + y(v).toFixed(1); }).join(""));
      });
      var bw = Math.max(2, Math.min(band - 2, band * 0.62, 48));
      barNodes.forEach(function (node, k) {
        var x0 = x(k) - bw / 2, y0 = y(values[keys[0]][k]), r = Math.min(4, bw / 2, base - y0);
        // rounded at the data end, square on the baseline; a zero is a flat bar ready to grow
        node.setAttribute("d", "M" + x0 + "," + base + "V" + (y0 + r) + "Q" + x0 + "," + y0 + " " + (x0 + r) + "," + y0 +
          "H" + (x0 + bw - r) + "Q" + (x0 + bw) + "," + y0 + " " + (x0 + bw) + "," + (y0 + r) + "V" + base + "Z");
      });
      if (ends.length) { // nudged apart so none overlap
        var spots = ends.map(function (end) { return { node: end.node, y: y(values[end.key][n - 1]) }; })
          .sort(function (a, b) { return a.y - b.y; });
        for (var j = 1; j < spots.length; j++) if (spots[j].y - spots[j - 1].y < 14) spots[j].y = spots[j - 1].y + 14;
        var over = Math.max(0, spots[spots.length - 1].y - base);
        spots.forEach(function (spot) { spot.node.setAttribute("y", spot.y - over + 4); });
      }
    }

    var cross = bars ? null : el("line", { y1: pad.t, y2: base, "class": "chart__cross" }, svg);
    var dots = bars ? [] : keys.map(function (key, s) { return el("circle", { r: 4, "class": "chart__dot series-" + (s + 1) }, svg); });
    var brush = el("rect", { y: pad.t, height: h, "class": "chart__brush" }, svg);
    el("rect", { x: pad.l, y: pad.t, width: w, height: h, "class": "chart__hit" }, svg);
    plot.appendChild(svg);
    var tip = document.createElement("div");
    tip.className = "chart__tip";
    tip.setAttribute("aria-live", "polite");
    plot.appendChild(tip);

    max = axis(data.values);
    paint(data.values);

    var current = -1, how = "mouse", pinned = -1, drag = null;
    function show(i) {
      current = i;
      svg.classList.add("is-pointing");
      if (cross) {
        cross.setAttribute("x1", x(i));
        cross.setAttribute("x2", x(i));
      }
      dots.forEach(function (dot, s) {
        dot.setAttribute("cx", x(i));
        dot.setAttribute("cy", y(data.values[keys[s]][i]));
      });
      barNodes.forEach(function (node, k) { node.classList.toggle("is-active", k === i); });
      tip.textContent = "";
      var head = document.createElement("p");
      head.textContent = when(chart, data.labels[i], true);
      tip.appendChild(head);
      chart.series.forEach(function (series, s) {
        var row = document.createElement("div");
        row.innerHTML = '<span class="chart__key series-' + (s + 1) + '"></span><span></span><span></span>';
        row.children[1].textContent = series.label;
        row.children[2].textContent = figure(chart, data.values[series.key][i]);
        tip.appendChild(row);
      });
      if (hooks.open) {
        var hint = document.createElement("p");
        hint.className = "chart__hint";
        hint.textContent = HINT[how] || HINT.mouse;
        tip.appendChild(hint);
      }
      tip.classList.add("is-on");
      var left = x(i) + 12;
      if (left + tip.offsetWidth > width) left = x(i) - tip.offsetWidth - 12;
      tip.style.transform = "translate(" + Math.max(0, left) + "px," + pad.t + "px)";
    }
    function hide() {
      current = -1;
      pinned = -1;
      svg.classList.remove("is-pointing");
      tip.classList.remove("is-on");
    }
    function at(event) {
      var box = svg.getBoundingClientRect(), px = (event.clientX - box.left) * (width / box.width) - pad.l;
      var k = bars ? Math.floor(px / band) : Math.round((px / w) * (n - 1));
      return Math.max(0, Math.min(n - 1, k));
    }
    function brushTo(a, b) {
      var lo = Math.min(a, b), hi = Math.max(a, b);
      var left = bars ? pad.l + band * lo : x(lo), right = bars ? pad.l + band * (hi + 1) : x(hi);
      brush.setAttribute("x", left);
      brush.setAttribute("width", Math.max(1, right - left));
      svg.classList.add("is-brushing");
    }
    function endDrag() {
      drag = null;
      svg.classList.remove("is-brushing");
    }
    svg.addEventListener("pointerdown", function (event) {
      if (event.button > 0) return;
      how = event.pointerType || "mouse";
      drag = { from: at(event), x: event.clientX, id: event.pointerId, moved: false };
      show(drag.from);
    });
    svg.addEventListener("pointermove", function (event) {
      how = event.pointerType || "mouse";
      var k = at(event);
      if (drag && hooks.zoom && n > 2 && (drag.moved || Math.abs(event.clientX - drag.x) > 8)) {
        if (!drag.moved) {
          drag.moved = true;
          try { svg.setPointerCapture(drag.id); } catch (e) { /* the pointer is already gone */ }
        }
        brushTo(drag.from, k);
      }
      show(k);
    });
    svg.addEventListener("pointerup", function (event) {
      if (!drag) return;
      var d = drag, k = at(event);
      endDrag();
      if (d.moved) {
        if (k !== d.from) hooks.zoom(Math.min(d.from, k), Math.max(d.from, k));
        return;
      }
      if (!hooks.open) return;
      if (how === "mouse" || pinned === k) {
        hooks.open(data.labels[k]);
      } else { // a first tap reads the point; tapping it again opens its list
        pinned = k;
        show(k);
      }
    });
    svg.addEventListener("pointercancel", endDrag);
    svg.addEventListener("pointerleave", function (event) { if (event.pointerType === "mouse" && !drag) hide(); });
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", function (event) {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        how = "key";
        var move = event.key === "ArrowRight" ? 1 : -1;
        show(current < 0 ? n - 1 : Math.max(0, Math.min(n - 1, current + move)));
      } else if (event.key === "Enter" && current >= 0 && hooks.open) {
        event.preventDefault();
        hooks.open(data.labels[current]);
      } else if (event.key === "Escape") {
        hide();
      }
    });

    // The same days or months with other figures: every mark travels from where it was, with a little bounce.
    function morph(next) {
      hide();
      var from = data.values, fromMax = max, toMax = axis(next.values);
      data = next;
      ticks.classList.remove("is-new");
      ticks.getBoundingClientRect(); // restart the labels' fade
      ticks.classList.add("is-new");
      tween(svg, 750, function (p) {
        var e = backOut(p), now = {};
        max = fromMax + (toMax - fromMax) * cubicOut(p); // the scale eases without bouncing so no mark flies off
        keys.forEach(function (key) {
          now[key] = next.values[key].map(function (v, k) { var a = from[key][k] || 0; return a + ((v || 0) - a) * e; });
        });
        paint(p < 1 ? now : next.values);
      });
    }
    return { node: svg, size: n, morph: morph };
  }

  /* --- where things stand: horizontal bars per status, grouped by series ----------------------------- */
  function drawCategories(plot, chart, data) {
    var list = document.createElement("div");
    list.className = "hbars";
    var rows = [];
    chart.series.forEach(function (series, s) {
      var group = document.createElement("p");
      group.className = "hbars__group";
      group.textContent = series.label;
      list.appendChild(group);
      data.labels.forEach(function (label, i) {
        if (data.values[series.key][i] === null) return;
        var href = chart.links && chart.links[i];
        var row = document.createElement(href ? "a" : "div");
        row.className = "hbar";
        if (href) row.href = href;
        row.innerHTML = '<span></span><span class="hbar__track"><span class="hbar__fill series-' + (s + 1) + '"></span></span><span class="hbar__value"></span>';
        row.children[0].textContent = label;
        var fill = row.children[1].firstChild;
        fill.style.transitionDelay = i * 60 + "ms";
        rows.push({ key: series.key, i: i, fill: fill, value: row.children[2], shown: 0 });
        list.appendChild(row);
      });
    });
    function set(values, animate) {
      var top = largest(chart, values);
      rows.forEach(function (row) {
        var v = values[row.key][row.i] || 0;
        row.fill.style.setProperty("--v", top ? v / top : 0);
        countUp(row.value, chart, row.shown, v, animate);
        row.shown = v;
      });
    }
    set(data.values, false);
    plot.appendChild(list);
    return {
      node: list, size: data.labels.length,
      morph: function (next) { set(next.values, true); },
      recount: function () { rows.forEach(function (row) { countUp(row.value, chart, 0, row.shown, true); }); }
    };
  }

  function fillTable(box, chart, data, span) {
    var holder = box.querySelector("[data-chart-table]");
    var wrap = document.createElement("div"), table = document.createElement("table");
    wrap.className = "table-wrap";
    table.className = "table";
    function cell(row, tag, value, css) {
      var node = document.createElement(tag);
      node.textContent = value;
      if (css) node.className = css;
      if (tag === "th") node.scope = "col";
      row.appendChild(node);
    }
    var head = table.createTHead().insertRow();
    cell(head, "th", chart.categories ? "Status" : span === "12m" ? "Month" : "Day");
    chart.series.forEach(function (series) { cell(head, "th", series.label, "num"); });
    var body = table.createTBody();
    data.labels.forEach(function (label, i) {
      var row = body.insertRow();
      cell(row, "td", when(chart, label, true));
      chart.series.forEach(function (series) { cell(row, "td", figure(chart, data.values[series.key][i]), "num"); });
    });
    wrap.appendChild(table);
    holder.textContent = "";
    holder.appendChild(wrap);
  }

  function fail(box) {
    var plot = box.querySelector("[data-chart-plot]");
    plot.textContent = "";
    note(plot, FAILED, true); // the table the server wrote stays, so the figures still read
  }

  /* --- wiring ----------------------------------------------------------------------------------------- */
  var seen = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      seen.unobserve(entry.target);
      var go = entry.target.adStart;
      entry.target.adStart = null;
      if (go) go();
    });
  }, { threshold: 0.3 }) : null;
  // Each time a graph comes back onto the screen, even after only part of it scrolled away, it plays in again.
  var again = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var box = entry.target;
      if (entry.intersectionRatio < 0.5) box.adAway = true;
      else if (box.adAway) {
        box.adAway = false;
        if (box.adAgain) box.adAgain();
      }
    });
  }, { threshold: [0, 0.5] }) : null;

  var views = [];
  boxes.forEach(function (box) {
    var chart = charts && charts.filter(function (c) { return c.id === box.getAttribute("data-chart"); })[0];
    if (!chart || !chart.data || !chart.data.all) return fail(box);
    var plot = box.querySelector("[data-chart-plot]");
    var total = box.querySelector("[data-chart-total]");
    var whenNode = box.querySelector("[data-chart-when]");
    var details = box.querySelector("[data-chart-details]");
    var switcher = box.querySelector("[data-span-switch]");
    var span = chart.data.all[params.get(chart.id)] ? params.get(chart.id) : chart.span;
    var width = 0, view = null, zoom = null, shown = 0;

    var reset = document.createElement("button");
    reset.type = "button";
    reset.className = "btn btn--sm chart__reset";
    reset.textContent = "Show all";
    reset.hidden = true;
    switcher.parentNode.insertBefore(reset, switcher);

    var hooks = {
      zoom: chart.categories ? null : function (first, last) {
        var offset = zoom ? zoom[0] : 0;
        zoom = [offset + first, offset + last];
        run("enter");
      },
      open: chart.link ? function (label) { window.location.href = listUrl(chart, label); } : null
    };
    function slice(data) {
      if (!zoom) return data;
      var values = {};
      for (var key in data.values) values[key] = data.values[key].slice(zoom[0], zoom[1] + 1);
      return { labels: data.labels.slice(zoom[0], zoom[1] + 1), values: values };
    }

    // Draws the graph. "enter" returns the function that plays it in, "morph" moves the figures of the graph
    // already showing, "still" just shows it.
    function render(mode) {
      var waiting = !!box.adStart; // drawn but not yet seen, so there is nothing on screen to morph
      box.adStart = null;
      if (still.matches) mode = "still";
      else if (waiting && mode === "morph") mode = "enter";
      var data = slice(chart.data[region][span]);
      switcher.querySelectorAll("button").forEach(function (b) {
        b.setAttribute("aria-pressed", String(b.getAttribute("data-span") === span));
      });
      var last = data.labels.length - 1;
      whenNode.textContent = (zoom ? "from " + when(chart, data.labels[0], true) + " to " + when(chart, data.labels[last], true) : WHEN[span]) + WHERE[region];
      reset.hidden = !zoom;
      var sum = data.values[chart.series[0].key].reduce(function (a, v) { return a + (v || 0); }, 0);
      var any = chart.series.some(function (s) { return data.values[s.key].some(function (v) { return v; }); });
      details.hidden = !any;
      if (any) fillTable(box, chart, data, span);
      if (mode === "morph" && any && view && view.size === data.labels.length) {
        view.morph(data);
        countUp(total, chart, shown, sum, true);
        shown = sum;
        return null;
      }
      plot.textContent = "";
      view = null;
      if (!any) {
        countUp(total, chart, shown, 0, mode !== "still");
        shown = 0;
        note(plot, chart.empty);
        return null;
      }
      view = chart.categories ? drawCategories(plot, chart, data) : drawTime(plot, chart, data, hooks);
      width = plot.clientWidth;
      if (mode === "still") {
        countUp(total, chart, sum, sum, false);
        shown = sum;
        return null;
      }
      var from = shown, node = view.node; // the total counts on from the figure it showed before
      countUp(total, chart, sum, sum, false);
      node.classList.add("is-start");
      return function () {
        node.getBoundingClientRect(); // lay out the start state before letting it move
        requestAnimationFrame(function () { node.classList.remove("is-start"); });
        countUp(total, chart, from, sum, true);
        shown = sum;
      };
    }
    // Plays a graph in now if any of it is on screen, otherwise when it scrolls into view.
    function run(mode) {
      var go = render(mode);
      if (!go) return;
      var spot = box.getBoundingClientRect();
      if (seen && (spot.bottom < 0 || spot.top > window.innerHeight)) {
        box.adStart = go;
        seen.observe(box);
      } else {
        go();
      }
    }

    switcher.hidden = false;
    switcher.querySelectorAll("button").forEach(function (button) {
      button.addEventListener("click", function () {
        span = button.getAttribute("data-span");
        zoom = null;
        remember(chart.id, span, chart.span);
        run("enter");
      });
    });
    reset.addEventListener("click", function () {
      zoom = null;
      run("enter");
      var pressed = switcher.querySelector('[aria-pressed="true"]');
      if (pressed) pressed.focus(); // the button just pressed has gone
    });
    try {
      run("enter");
    } catch (e) {
      return fail(box);
    }
    if (window.ResizeObserver) {
      var pending = 0;
      new ResizeObserver(function () {
        if (Math.abs(plot.clientWidth - width) < 2) return;
        cancelAnimationFrame(pending);
        pending = requestAnimationFrame(function () {
          if (box.adStart) box.adStart = render("enter"); // still waiting to be seen: keep its entrance
          else render("still");
        });
      }).observe(plot);
    }
    if (again) {
      box.adAgain = function () {
        if (box.adStart || still.matches || !view) return; // its first entrance plays on its own
        var node = view.node;
        node.classList.add("is-start");
        node.getBoundingClientRect(); // lay out the start state before letting it move
        requestAnimationFrame(function () { node.classList.remove("is-start"); });
        countUp(total, chart, 0, shown, true);
        if (view.recount) view.recount();
      };
      again.observe(box);
    }
    views.push(run);
  });

  var regions = document.querySelector("[data-region-switch]");
  if (regions && views.length) {
    regions.hidden = false;
    regions.querySelectorAll("button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-region") === region));
      button.addEventListener("click", function () {
        region = button.getAttribute("data-region");
        remember("region", region, "all");
        regions.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b === button)); });
        views.forEach(function (run) { run("morph"); });
      });
    });
  }
})();
