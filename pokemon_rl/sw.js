// Pokemon Battle AI — Service Worker
// 전략: 네트워크 우선(게임은 항상 최신), 오프라인 시 캐시 폴백
const CACHE = 'pokebattle-v1';
const ASSETS = ['/icon-192.png', '/icon-512.png', '/apple-touch-icon.png', '/manifest.json'];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS).catch(() => {})));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  // WebSocket, 비-GET, API, BGM은 항상 네트워크
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.pathname.startsWith('/ws') || url.pathname.startsWith('/api') ||
      url.pathname.endsWith('.mp3')) {
    return; // 브라우저 기본 처리
  }
  // 네트워크 우선, 실패 시 캐시
  e.respondWith(
    fetch(req).then((res) => {
      // 정적 자산만 캐시 갱신
      if (ASSETS.some((a) => url.pathname === a)) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req).then((r) => r || caches.match('/')))
  );
});
