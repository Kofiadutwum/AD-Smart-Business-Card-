/* app.js — dashboard and staff shells: drawer navigation and the card
   editor's live preview (Section 25: the preview updates as you type). */
(function () {
  "use strict";

  /* --- drawer on small screens --------------------------------------------- */
  var side = document.getElementById("app-side");
  var scrim = document.querySelector(".app-scrim");
  var opener = document.querySelector("[data-drawer-open]");
  function setDrawer(open) {
    if (!side) return;
    side.classList.toggle("is-open", open);
    if (scrim) scrim.classList.toggle("is-open", open);
    if (opener) opener.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var first = side.querySelector("a, button");
      if (first) first.focus();
    }
  }
  if (opener) opener.addEventListener("click", function () { setDrawer(true); });
  if (scrim) scrim.addEventListener("click", function () { setDrawer(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && side && side.classList.contains("is-open")) {
      setDrawer(false);
      if (opener) opener.focus();
    }
  });

  /* --- live preview --------------------------------------------------------- */
  var form = document.querySelector("[data-card-editor]");
  var card = document.querySelector("[data-preview] .pcard");
  if (!form || !card) return;

  function hexToRgb(hex) {
    var h = hex.replace("#", "");
    return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); });
  }
  function luminance(hex) {
    var c = hexToRgb(hex).map(function (v) {
      v = v / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function contrast(a, b) {
    var la = luminance(a), lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  function mix(hex, target, t) {
    var a = hexToRgb(hex), b = hexToRgb(target);
    return "#" + a.map(function (v, i) {
      var x = Math.round(v + (b[i] - v) * t);
      return ("0" + x.toString(16)).slice(-2);
    }).join("");
  }
  function readable(colour, bg) {
    if (contrast(colour, bg) >= 4.5) return colour;
    var target = luminance(bg) > 0.5 ? "#000000" : "#ffffff";
    for (var s = 1; s <= 20; s++) {
      var c = mix(colour, target, s * 0.05);
      if (contrast(c, bg) >= 4.5) return c;
    }
    return target;
  }
  function onColour(bg) {
    return contrast("#ffffff", bg) >= contrast("#15171d", bg) ? "#ffffff" : "#15171d";
  }

  function val(name) {
    var el = form.elements[name];
    if (!el) return "";
    if (el.length !== undefined && el.tagName !== "SELECT" && !el.type) {
      for (var i = 0; i < el.length; i++) if (el[i].checked) return el[i].value;
      return "";
    }
    return el.value || "";
  }

  function setText(selector, text) {
    var node = card.querySelector(selector);
    if (node) node.textContent = text;
  }

  function initials(name) {
    var parts = name.trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function paint() {
    var name = val("full_name") || "Your name";
    setText(".pcard__name", name);
    var avatarSpan = card.querySelector(".pcard__avatar span");
    if (avatarSpan) avatarSpan.textContent = initials(name);

    var role = card.querySelector(".pcard__role");
    var title = val("job_title"), org = val("business_name");
    if (!role && (title || org)) {
      role = document.createElement("p");
      role.className = "pcard__role";
      card.querySelector(".pcard__head").appendChild(role);
    }
    if (role) {
      role.textContent = "";
      role.hidden = !(title || org);
      if (title) role.appendChild(document.createTextNode(title));
      if (title && org) role.appendChild(document.createTextNode(" · "));
      if (org) {
        var s = document.createElement("span");
        s.className = "pcard__org";
        s.textContent = org;
        role.appendChild(s);
      }
    }

    var bio = card.querySelector(".pcard__bio p");
    var bioText = val("bio");
    if (!bio && bioText) {
      var section = document.createElement("section");
      section.className = "pcard__bio";
      bio = document.createElement("p");
      section.appendChild(bio);
      var after = card.querySelector(".pcard__details");
      if (after) after.after(section);
    }
    if (bio) {
      bio.textContent = bioText;
      bio.parentNode.hidden = !bioText;
    }

    var preset = val("accent_preset");
    var accent = preset === "custom" ? val("accent_custom") : preset;
    if (/^#[0-9a-f]{6}$/i.test(accent || "")) {
      var bandInput = form.elements.card_colour;
      var bandCustom = bandInput && !bandInput.disabled && bandInput.dataset.custom === "1";
      var band = bandCustom && /^#[0-9a-f]{6}$/i.test(bandInput.value) ? bandInput.value : accent;
      card.style.setProperty("--pc-accent", accent);
      card.style.setProperty("--pc-on-accent", onColour(accent));
      card.style.setProperty("--pc-accent-text", readable(accent, "#ffffff"));
      card.style.setProperty("--pc-accent-text-dark", readable(accent, "#15161a"));
      card.style.setProperty("--pc-band", band);
    }

    ["classic", "banner", "minimal"].forEach(function (t) { card.classList.remove("pcard--" + t); });
    card.classList.add("pcard--" + (val("template") || "classic"));
    ["pill", "rounded", "square"].forEach(function (t) { card.classList.remove("pcard--btn-" + t); });
    card.classList.add("pcard--btn-" + (val("button_style") || "pill"));
    var social = card.querySelector(".pcard__social");
    if (social) social.className = "pcard__social pcard__social--" + (val("icon_layout") || "list");
  }

  if (form.elements.card_colour) {
    form.elements.card_colour.addEventListener("input", function () {
      form.elements.card_colour.dataset.custom = "1";
    });
  }
  form.addEventListener("input", paint);
  form.addEventListener("change", paint);

  var avatarInput = form.elements.avatar_upload;
  if (avatarInput) {
    avatarInput.addEventListener("change", function () {
      var file = avatarInput.files && avatarInput.files[0];
      if (!file || !/^image\//.test(file.type)) return;
      var reader = new FileReader();
      reader.onload = function () {
        var holder = card.querySelector(".pcard__avatar");
        holder.innerHTML = "";
        var img = document.createElement("img");
        img.src = reader.result;
        img.alt = "";
        holder.appendChild(img);
      };
      reader.readAsDataURL(file);
    });
  }

  /* "Use my current location" for the map pin. */
  var locate = document.querySelector("[data-locate]");
  if (locate && navigator.geolocation) {
    locate.addEventListener("click", function () {
      var status = document.querySelector("[data-locate-status]");
      status.textContent = "Finding you…";
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          form.elements.latitude.value = pos.coords.latitude.toFixed(6);
          form.elements.longitude.value = pos.coords.longitude.toFixed(6);
          status.textContent = "Location added. Save to keep it.";
        },
        function () {
          status.textContent = "We could not get your location. Check your browser’s permission.";
        },
        { enableHighAccuracy: true, timeout: 10000 }
      );
    });
    var clear = document.querySelector("[data-locate-clear]");
    if (clear) {
      clear.addEventListener("click", function () {
        form.elements.latitude.value = "";
        form.elements.longitude.value = "";
        document.querySelector("[data-locate-status]").textContent = "Map pin removed. Save to keep it.";
      });
    }
  } else if (locate) {
    locate.hidden = true;
  }

  paint();
})();
