/* AD Smart service worker. Served from /sw.js so it covers the whole site.

   - Keeps the offline page and the logo from the moment it installs.
   - A page is always fetched from the network. Only when there is no connection
     does it show the offline page, so a page with someone's details on it is
     never kept on the device or shown out of date.
   - Static files (they carry a content hash in their name, so they never change)
     are kept after the first visit and open straight away next time.
   - Anything that is not a plain GET, and anything on another site, is left alone. */

var CACHE = "{{ cache_name }}";
var KEEP = {{ keep_json|safe }};
var OFFLINE = "/offline";
var DEBUG = {{ debug|yesno:"true,false" }};

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE)
      .then(function (cache) {
        return cache.addAll(KEEP);
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (names) {
        return Promise.all(
          names
            .filter(function (name) {
              return name.indexOf("adsmart-") === 0 && name !== CACHE;
            })
            .map(function (name) {
              return caches.delete(name);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE);
      })
    );
    return;
  }

  if (!DEBUG && url.pathname.indexOf("/static/") === 0) {
    event.respondWith(
      caches.match(request).then(function (hit) {
        if (hit) return hit;
        return fetch(request).then(function (response) {
          if (response.ok) {
            var copy = response.clone();
            caches.open(CACHE).then(function (cache) {
              cache.put(request, copy);
            });
          }
          return response;
        });
      })
    );
  }
});
