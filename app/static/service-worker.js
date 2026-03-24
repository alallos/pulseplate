const CACHE_NAME = 'pulseplate-shell-v6';
// Keep this list focused on the "app shell" so we don't accidentally cache stale HTML/JS forever.
const URLS_TO_CACHE = [
  '/',
  '/manifest.webmanifest',
  '/favicon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(URLS_TO_CACHE))
  );
  // Activate the new worker as soon as it's installed.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  // Start controlling open clients immediately.
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // Never cache API traffic; always hit the network for live status data.
  const livePrefixes = [
    '/webhooks/',
    '/biometrics/',
    '/plans',
    '/preferences',
    '/generate-meal-plan',
    '/health/',
  ];
  if (livePrefixes.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Keep page navigations fresh (prevents stale JS/HTML after deploys).
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/'))
    );
    return;
  }

  // App-shell assets can be cache-first.
  event.respondWith(
    caches.match(event.request).then((response) => response || fetch(event.request))
  );
});

