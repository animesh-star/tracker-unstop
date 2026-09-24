// Service Worker for Unstop 30-Day Challenge Reminder
// Handles background push notifications

const CACHE_NAME = 'unstop-reminder-v2';
const ASSETS_TO_CACHE = ['/', '/index.html', '/style.css', '/app.js'];

// ─── Install ───────────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS_TO_CACHE))
  );
  self.skipWaiting();
});

// ─── Activate ──────────────────────────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ─── Fetch (Network-first with cache fallback) ─────────────────────────────────
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || event.request.url.includes('/api/')) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response && response.status === 200) {
          const resClone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, resClone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// ─── Push Notification Handler ─────────────────────────────────────────────────
self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: '🔔 Unstop Reminder', body: event.data?.text() || 'Check your challenge!' };
  }

  const title = data.title || '🔔 Unstop Challenge Reminder';
  const options = {
    body: data.body || "Don't forget your daily challenge!",
    icon: data.icon || '/icons/icon-192.png',
    badge: data.badge || '/icons/icon-72.png',
    tag: data.tag || 'unstop-reminder',
    renotify: data.renotify !== false,
    requireInteraction: data.requireInteraction !== false, // Won't auto-dismiss!
    vibrate: [200, 100, 200, 100, 200],
    data: data.data || {},
    actions: [
      { action: 'open', title: '🚀 Open Challenge', icon: '/icons/icon-72.png' },
      { action: 'solve', title: '✅ Mark Solved', icon: '/icons/icon-72.png' },
    ],
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// ─── Notification Click Handler ────────────────────────────────────────────────
self.addEventListener('notificationclick', event => {
  event.notification.close();

  const notifData = event.notification.data || {};
  const challengeLink = notifData.link || 'https://unstop.com/challenges';
  const appUrl = notifData.url || '/';

  if (event.action === 'solve') {
    // Mark as solved via API
    event.waitUntil(
      fetch('/api/solve', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
        .then(() => self.clients.openWindow(appUrl))
    );
  } else if (event.action === 'open') {
    // Open the challenge link
    event.waitUntil(self.clients.openWindow(challengeLink));
  } else {
    // Default: open the app
    event.waitUntil(
      self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
        const existing = clients.find(c => c.url.includes(self.location.origin));
        if (existing) return existing.focus();
        return self.clients.openWindow(appUrl);
      })
    );
  }
});

// ─── Notification Close (dismissed without action) ────────────────────────────
self.addEventListener('notificationclose', event => {
  console.log('[SW] Notification dismissed — reminder will repeat in 5 min if unsolved.');
});
