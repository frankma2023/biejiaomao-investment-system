const CACHE = 'hana-v2';
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
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // API 请求：网络优先（不缓存）
  if (e.request.url.indexOf('/api/') >= 0) {
    e.respondWith(fetch(e.request));
    return;
  }
  // HTML 文件：网络优先（先问服务器，更新缓存）
  if (e.request.mode === 'navigate' || e.request.headers.get('Accept').includes('text/html')) {
    e.respondWith(
      fetch(e.request).then(r => {
        var clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request))
    );
  } else {
    // 静态资源：缓存优先
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request))
    );
  }
});
