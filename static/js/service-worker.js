"use strict";

const CACHE = "bokydojo-shell-v4";
const SHELL = [
  "/offline/",
  "/static/css/tailwind.css",
  "/static/css/dojo.css",
  "/static/js/pwa.js",
  "/static/js/roster.js",
  "/static/js/attendance-sync.js",
  "/static/js/attendance-queue.js",
  "/static/js/kiosk.js",
  "/static/js/offline.js",
  "/static/manifest.webmanifest",
  "/static/icons/bokydojo-192.png",
  "/static/icons/bokydojo-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  // Static assets: stale-while-revalidate.
  //
  // ⚠ Only /static/ is ever written to the cache. Authenticated HTML must not
  // be, which is why this branch returns before the navigate handler below.
  //
  // Previously this was cache-first with no revalidation at all
  // (`cached || fetch(...)`), which pinned an installed PWA to whichever CSS
  // and JS it happened to see first. Django serves static files under stable,
  // unhashed names, so nothing else busts them either: a fix to roster.js —
  // the offline attendance path — could never reach an instructor's phone
  // unless someone remembered to bump CACHE, and nothing enforced that.
  //
  // Answering from cache keeps a cold offline start fast; the background
  // refresh means the *next* load picks up a redeploy.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const cached = await cache.match(event.request);
        const network = fetch(event.request).then((response) => {
          if (response && response.ok) cache.put(event.request, response.clone());
          return response;
        });
        if (!cached) return network;
        // Keep the worker alive for the refresh, but never fail the response
        // for it — offline is the expected case, not an error.
        event.waitUntil(network.catch(() => {}));
        return cached;
      }),
    );
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/offline/")));
  }
});