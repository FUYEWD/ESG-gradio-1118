/* Service Worker：App Shell 快取 + 離線頁
 * - 頁面導覽：網路優先，失敗時顯示 /offline.html
 * - 靜態資源（/assets /static /pwa）：stale-while-revalidate
 * - Gradio 動態 API / 事件串流 / 檔案下載：一律不攔截，直接走網路
 * 修改快取內容時請更新 VERSION。
 */
const VERSION = "v1";
const CACHE = `twse-pwa-${VERSION}`;
const PRECACHE = [
  "/offline.html",
  "/pwa/icons/icon-192.png",
  "/pwa/icons/icon-512.png",
];
const STATIC_PREFIX = ["/assets/", "/static/", "/pwa/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 頁面導覽：網路優先，離線時回傳離線頁
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline.html")));
    return;
  }

  // 靜態資源：先回快取、背景更新
  if (STATIC_PREFIX.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const network = fetch(req)
          .then((res) => {
            if (res && res.ok) cache.put(req, res.clone());
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
  }
  // 其餘（/gradio_api、/config、/queue…）不處理，交給瀏覽器
});
