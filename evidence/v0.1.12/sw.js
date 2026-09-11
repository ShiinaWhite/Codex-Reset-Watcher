// Root-scoped service worker. Notifications only.
//
// Deliberately NO offline caching. This site's entire value is live freshness,
// and a caching worker is how you end up serving a reset time that is hours
// old. The one edge-cache incident on this project already showed a browser QA
// pass a stale footer; a client-side cache would be worse and permanent.
//
// This file must stay at /sw.js. A worker served from the hashed
// /assets/<release>/ path could only control that directory. Versioning is the
// constant below plus registration with { updateViaCache: "none" }.

// Bumping this does NOT bump the registration URL, and they must not be
// conflated: push.js registers "/sw.js?v=2" and sends alert_scope
// "alerts-v1" only while that ?v= reads exactly "2". Move the registration to
// v=3 and every resyncing subscriber silently loses the #alerts scope, i.e.
// forecast and banked pushes, keeping reset-only. Changing this file's bytes
// is already what makes a browser adopt the new worker, so an edit here needs
// no bump at all; if you ever do bump both, widen the check in push.js first.
const SW_VERSION = "2";

const FALLBACK_TITLE = "New Codex reset alert";
const FALLBACK_BODY = "Open Codex Reset for the latest Tibo alert.";
const ALERT_KINDS = new Set(["reset", "forecast", "banked"]);

function fallbackNotification() {
  return {
    title: FALLBACK_TITLE,
    options: {
      body: FALLBACK_BODY,
      tag: "codex-reset-alert",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: "/" }
    }
  };
}

function notificationFromAlert(alert) {
  const id = String((alert && alert.id) || "").replace(/[^0-9A-Za-z:._-]/g, "").slice(0, 120);
  const eventId = String((alert && alert.event_id) || "").replace(/[^0-9A-Za-z:._-]/g, "").slice(0, 160);
  const kind = String((alert && alert.kind) || "").toLowerCase();
  const title = String((alert && alert.title) || "").trim().slice(0, 120);
  const body = String((alert && alert.body) || "").trim().slice(0, 240);
  if (!id || !ALERT_KINDS.has(kind) || !title || !body || alert.url !== "/") {
    return fallbackNotification();
  }
  return {
    title,
    options: {
      body,
      tag: "codex-alert-" + (eventId || kind + "-" + id),
      renotify: true,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: "/" }
    }
  };
}

async function buildNotification() {
  try {
    // The record that was actually pushed, not a re-selection. /api/push/latest
    // answers a different question (the public Reset-or-Watch banner) and a
    // banked push rendered through it announced a stale reset on 2026-09-03.
    const response = await fetch("/api/push/notification", { cache: "no-store" });
    if (!response.ok) throw new Error("push_notification_" + response.status);
    const payload = await response.json();
    return notificationFromAlert(payload && payload.alert);
  } catch (error) {
    return fallbackNotification();
  }
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  // Always ends in exactly one showNotification, including when the fetch
  // fails — see the comment on userVisibleOnly above.
  event.waitUntil(
    buildNotification().then((notification) =>
      self.registration.showNotification(notification.title, notification.options)
    )
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (new URL(client.url).pathname === url && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(url);
    })
  );
});
