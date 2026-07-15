const CACHE = 'hana-v3';
const URLS = [
  '/mobile/',
  '/cockpit/',
  '/market-scan/',
  '/pattern-scan/',
  '/mw-signals/',
  '/manifest.json',
  '/images/favicon.svg',
  '/shared/css/hanako-glass.css',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(URLS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // API 请求：网络优先，失败不缓存
  if (e.request.url.indexOf('/api/') >= 0) {
    e.respondWith(fetch(e.request).catch(() => new Response('', {status: 502})));
    return;
  }
  // HTML：网络优先，失败走缓存
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request).then(r => r || fetch(e.request)))
    );
    return;
  }
  // 其他资源：缓存优先，失败走网络
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request).catch(() => new Response('', {status: 502})))
  );
});
