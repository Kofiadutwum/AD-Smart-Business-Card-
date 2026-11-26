/* Face ID, fingerprint and device-PIN sign-in (passkeys). Server side: apps/accounts/passkeys.py.
   Sign-in pages show the device's prompt as soon as they open when this device has a passkey;
   settings pages turn it on for the device. */
(function () {
  "use strict";
  // Run once even when a page includes the prompt and the settings panel (each brings this script).
  if (!window.PublicKeyCredential || !navigator.credentials || window.adPasskeyReady) return;
  window.adPasskeyReady = true;
  var KEY = "passkey-id"; // the public id of this device's passkey: tells sign-in pages to show the prompt on open

  function remember(id) {
    try { if (id) localStorage.setItem(KEY, id); else localStorage.removeItem(KEY); } catch (e) {}
  }
  var DISMISSED = "passkey-offer-off"; // "Not now" on the dashboard prompt, for this device

  function remembered(key) {
    try { return localStorage.getItem(key || KEY); } catch (e) { return null; }
  }

  function toB64(buffer) {
    var bytes = new Uint8Array(buffer), text = "";
    for (var i = 0; i < bytes.length; i++) text += String.fromCharCode(bytes[i]);
    return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function fromB64(value) {
    var text = atob(value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4));
    var bytes = new Uint8Array(text.length);
    for (var i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
    return bytes.buffer;
  }

  function post(url, body) {
    var token = document.querySelector("[name=csrfmiddlewaretoken]");
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": token ? token.value : "" },
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (response.ok) return data;
        var error = new Error(data.error || "");
        error.fromServer = true;
        error.forget = data.forget;
        throw error;
      });
    });
  }

  function say(box, text) {
    var status = box.querySelector("[data-passkey-status]");
    if (status) status.textContent = text || "";
  }

  function failed(box, error) {
    if (error && (error.name === "NotAllowedError" || error.name === "AbortError")) return say(box, ""); // prompt closed
    if (error && error.name === "InvalidStateError") return say(box, box.getAttribute("data-exists"));
    say(box, error && error.fromServer && error.message ? error.message : box.getAttribute("data-failed"));
  }

  /* --- signing in ---------------------------------------------------------------------- */
  document.querySelectorAll("[data-passkey-signin]").forEach(function (box) {
    var busy = false;
    function signIn() {
      if (busy) return;
      busy = true;
      say(box, "");
      var mine = remembered();
      post(box.getAttribute("data-start")).then(function (options) {
        options.challenge = fromB64(options.challenge);
        // Go straight to this device's own prompt when it has a passkey; otherwise let the browser offer any.
        options.allowCredentials = mine ? [{ type: "public-key", id: fromB64(mine) }] : [];
        return navigator.credentials.get({ publicKey: options });
      }).then(function (credential) {
        var r = credential.response;
        return post(box.getAttribute("data-finish"), {
          next: box.getAttribute("data-next") || "",
          credential: {
            id: credential.id,
            rawId: toB64(credential.rawId),
            type: credential.type,
            response: {
              clientDataJSON: toB64(r.clientDataJSON),
              authenticatorData: toB64(r.authenticatorData),
              signature: toB64(r.signature),
              userHandle: r.userHandle ? toB64(r.userHandle) : null
            },
            clientExtensionResults: {}
          }
        }).then(function (data) {
          remember(credential.id);
          window.location.assign(data.redirect);
        });
      }).catch(function (error) {
        busy = false;
        if (error && error.forget) remember(null);
        failed(box, error);
      });
    }
    box.hidden = false;
    box.querySelector("[data-passkey-go]").addEventListener("click", signIn);
    // Pop the prompt up on open. Browsers that insist on a tap first refuse quietly, and the button stays.
    if (remembered()) signIn();
  });

  /* --- turning it on for this device ---------------------------------------------------- */
  document.querySelectorAll("[data-passkey-manage]").forEach(function (box) {
    var button = box.querySelector("[data-passkey-add]");
    var note = box.querySelector("[data-passkey-unsupported]");
    var offer = box.hasAttribute("data-passkey-offer"); // the dashboard prompt: devices without a passkey, until "Not now"
    PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(function (ok) {
      if (!ok || (offer && (remembered() || remembered(DISMISSED)))) return;
      box.hidden = false;
      button.hidden = false;
      if (note) note.hidden = true;
    }).catch(function () {});
    var dismiss = box.querySelector("[data-passkey-dismiss]");
    if (dismiss) dismiss.addEventListener("click", function () {
      try { localStorage.setItem(DISMISSED, "1"); } catch (e) {}
      box.hidden = true;
    });

    button.addEventListener("click", function () {
      button.disabled = true;
      say(box, "");
      post(box.getAttribute("data-start")).then(function (options) {
        options.challenge = fromB64(options.challenge);
        options.user.id = fromB64(options.user.id);
        (options.excludeCredentials || []).forEach(function (c) { c.id = fromB64(c.id); });
        return navigator.credentials.create({ publicKey: options });
      }).then(function (credential) {
        var r = credential.response;
        return post(box.getAttribute("data-finish"), {
          credential: {
            id: credential.id,
            rawId: toB64(credential.rawId),
            type: credential.type,
            response: {
              clientDataJSON: toB64(r.clientDataJSON),
              attestationObject: toB64(r.attestationObject),
              transports: r.getTransports ? r.getTransports() : []
            },
            clientExtensionResults: {}
          }
        });
      }).then(function (data) {
        remember(data.credential);
        window.location.reload();
      }).catch(function (error) {
        button.disabled = false;
        failed(box, error);
      });
    });
  });
})();
