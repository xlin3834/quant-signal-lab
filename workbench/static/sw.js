const SHELL = "signal-lab-shell-v2";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(["/", "/static/css/app.css", "/static/js/app.js", "/static/js/chart.js"])));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/") || event.request.method !== "GET") return;
  event.respondWith((async () => {
    const cache = await caches.open(SHELL);
    try {
      const fresh = await fetch(event.request);
      cache.put(event.request, fresh.clone());
      return fresh;
    } catch (error) {
      const cached = await cache.match(event.request);
      return cached || new Response("页面资源暂不可用。行情与提醒不会在离线时继续。", {status: 503});
    }
  })());
});
