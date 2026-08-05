// Intentionally does not cache anything - this is a live trading dashboard,
// so serving a stale price/position/balance from cache would be actively
// wrong. This exists only so Chrome/Android will treat the site as an
// installable PWA; iOS "Add to Home Screen" doesn't require it at all.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
