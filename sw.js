const CACHE_NAME = "ivrit-trainer-v2";
const SHELL_URL = "./index.html";
const ASSETS = [SHELL_URL, "./manifest.json", "./icon-192.png", "./icon-512.png"];

// Вытеснение кэша по чекпоинтам растущих текстов (см. ROADMAP, "кнопка
// назад", остаток пункта про вес) — раньше cache.put() копил аудио
// КАЖДОГО пройденного чекпоинта навечно (127 МБ на все 66, если пройти
// курс целиком). Тот же приём, что уже в sw.js читалки (MAX_BOOKS_CACHED):
// держим только текущий + один предыдущий чекпоинт, оболочка/JS/иконки в
// этот учёт не попадают (checkpointIdFromUrl вернёт null) и остаются в
// кэше всегда, как раньше. Совпадает и для growing_texts/checkpoint_NN_
// audio.json (метаданные), и для growing_texts/audio_gtNN/*.mp3 (сами
// клипы) — оба содержат тот же двузначный номер чекпоинта.
const MAX_CHECKPOINTS_CACHED = 2;
const META_KEY = new URL("__sw-meta__", self.location).toString();
const CHECKPOINT_URL_RE = /growing_texts\/(?:checkpoint_(\d+)_audio\.json|audio_gt(\d+)\/)/;

function checkpointIdFromUrl(url) {
  const m = url.match(CHECKPOINT_URL_RE);
  return m ? (m[1] || m[2]) : null;
}

async function getRecentCheckpoints(cache) {
  const res = await cache.match(META_KEY);
  return res ? res.json() : [];
}

async function touchCheckpointAndEvict(cache, cpId) {
  let recent = (await getRecentCheckpoints(cache)).filter((c) => c !== cpId);
  recent.unshift(cpId);
  const evict = recent.slice(MAX_CHECKPOINTS_CACHED);
  recent = recent.slice(0, MAX_CHECKPOINTS_CACHED);
  await cache.put(META_KEY, new Response(JSON.stringify(recent)));
  if (!evict.length) return;
  for (const req of await cache.keys()) {
    if (evict.includes(checkpointIdFromUrl(req.url))) await cache.delete(req);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first so a real connection always gets the latest build;
// falls back to the cached shell when offline.
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(async (cache) => {
          await cache.put(event.request, copy);
          const cpId = checkpointIdFromUrl(event.request.url);
          if (cpId) await touchCheckpointAndEvict(cache, cpId);
        });
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => {
          if (cached) return cached;
          // Оболочку сайта отдаём ТОЛЬКО для навигации (открыл сайт
          // офлайн) — раньше на сбой сети отдавалась она же для ЛЮБОГО
          // несостоявшегося запроса (аудио растущих текстов, теория
          // курса «Корни»), и вместо понятной сетевой ошибки в код
          // приходил HTML (см. ROADMAP, "кнопка назад").
          if (event.request.mode === "navigate" || event.request.destination === "document") {
            return caches.match(SHELL_URL);
          }
          return new Response(null, { status: 504, statusText: "Offline" });
        })
      )
  );
});
