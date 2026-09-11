import { base64UrlToUint8Array, pushState, readLabels } from "./push-state.js";
import { t } from "./i18n.js";
import {NOTIFY_FEATURES,RESET_ALERT_INTENT_EVENT,notificationsSupported,readPermission,requestPermission} from "./notification-permission.js";
function setState(root, state, labels) {const button = root.querySelector("[data-push-button]");
const note = root.querySelector("[data-push-note]");
root.dataset.pushState = state;
root.hidden = state === "hidden";
if (!button || !note) return;
const visibleButton = state === "subscribe" || state === "subscribed";
button.hidden = !visibleButton;
button.textContent = state === "subscribed" ? labels.subscribed : labels.subscribe;
button.setAttribute("aria-pressed", state === "subscribed" ? "true" : "false");
note.textContent = visibleButton ? "" : labels[state] || "";
note.hidden = !note.textContent;}
async function currentSubscription(win) {const registration = await win.navigator.serviceWorker.getRegistration("/");
return registration ? registration.pushManager.getSubscription() : null;}
async function post(win, path, body) {const response = await win.fetch(path, {method: "POST",headers: { "content-type": "application/json" },body: JSON.stringify(body)});
if (!response.ok) throw new Error(`${path}_${response.status}`);
return response.json();}
function activePushWorkerVersion(registration) {try {return new URL(registration?.active?.scriptURL).searchParams.get("v");} catch {return null;}}
async function syncPushSubscription(win, doc, registration, subscription) {if (!subscription?.endpoint) return;
const version = activePushWorkerVersion(registration);
return post(win, "/api/push/subscribe", {endpoint: subscription.endpoint,locale: doc.documentElement.lang || "en",...(version === "2"? { alert_scope: "alerts-v1" }: {})});}
export async function initPush(doc = document, win = window) {const root = doc.querySelector("[data-push]");
if (!root) return;
const labels = readLabels(root);
let config;
try {const response = await win.fetch("/api/push/key", { cache: "no-store" });
config = response.ok ? await response.json() : null;} catch {setState(root, "hidden", labels);
return;}
if (!config?.enabled || !config?.public_key) {setState(root, "hidden", labels);
return;}
const state = pushState({supported: "serviceWorker" in win.navigator && "PushManager" in win && notificationsSupported(win),permission: readPermission(win),isIos: /iPad|iPhone|iPod/.test(win.navigator.userAgent),isStandalone: Boolean(win.navigator.standalone) ||
Boolean(win.matchMedia && win.matchMedia("(display-mode: standalone)").matches)});
if (state !== "subscribe") {setState(root, state, labels);
if (state === "hidden" || state === "unsupported" || state === "ios-install" || state === "blocked") return;}
const registration = await win.navigator.serviceWorker.register("/sw.js?v=2", {scope: "/",updateViaCache: "none"});
const existing = await registration.pushManager.getSubscription();
setState(root, existing ? "subscribed" : "subscribe", labels);
const resync = async () => {const subscription = await registration.pushManager.getSubscription();
if (subscription) await syncPushSubscription(win, doc, registration, subscription);};
win.navigator.serviceWorker.addEventListener?.("controllerchange",() => resync().catch(() => {}),{ once: true });
if (existing) {await resync().catch(() => {});}
const button = root.querySelector("[data-push-button]");
if (!button) return;
mountSoftAsk(doc, win, () => button.click());
button.addEventListener("click", async () => {button.disabled = true;
try {const subscription = await currentSubscription(win);
if (subscription) {await post(win, "/api/push/unsubscribe", { endpoint: subscription.endpoint });
await subscription.unsubscribe();
setState(root, "subscribe", labels);
return;}
const permission = await requestPermission(NOTIFY_FEATURES.RESET_PUSH, { win });
if (permission !== "granted") {setState(root, permission === "denied" ? "blocked" : "subscribe", labels);
return;}
const created = await registration.pushManager.subscribe({userVisibleOnly: true,applicationServerKey: base64UrlToUint8Array(config.public_key)});
await syncPushSubscription(win, doc, registration, created);
setState(root, "subscribed", labels);} catch {setState(root, "error", labels);} finally {button.disabled = false;}});}
const SOFT_ASK_KEY = "codexreset.push-invite.v1";
const SOFT_ASK_SESSION_KEY = "codexreset.push-invite.seen.v1";
const SOFT_ASK_SNOOZE_MS = 30 * 24 * 60 * 60 * 1000;
const SOFT_ASK_DWELL_MS = 45_000;
function softAskSnoozed(win, now) {try {const until = Number(win.localStorage?.getItem(SOFT_ASK_KEY));
return Number.isFinite(until) && until > now;} catch {return true;}}
function snoozeSoftAsk(win, now, snoozeMs) {try {win.localStorage?.setItem(SOFT_ASK_KEY, String(now + snoozeMs));} catch {}}
function softAskSeenThisSession(win) {try {return win.sessionStorage?.getItem(SOFT_ASK_SESSION_KEY) === "1";} catch {return true;}}
function rememberSoftAskSession(win) {try {win.sessionStorage?.setItem(SOFT_ASK_SESSION_KEY, "1");} catch {}}
function buildSoftAsk(doc, copy) {const panel = doc.createElement("aside");
panel.className = "push-invite";
panel.id = "push-invite";
panel.setAttribute("role", "region");
panel.setAttribute("aria-label", copy.ariaLabel);
const status = doc.createElement("span");
status.className = "sr-only";
status.setAttribute("role", "status");
status.setAttribute("aria-live", "polite");
const body = doc.createElement("p");
body.className = "push-invite-body";
body.textContent = copy.body;
const actions = doc.createElement("div");
actions.className = "push-invite-actions";
const accept = doc.createElement("button");
accept.type = "button";
accept.className = "btn amber";
accept.textContent = `[ ${copy.accept} ]`;
const dismiss = doc.createElement("button");
dismiss.type = "button";
dismiss.className = "btn dim";
dismiss.textContent = `[ ${copy.dismiss} ]`;
actions.append(accept, dismiss);
panel.append(status, body, actions);
return { panel, status, accept, dismiss };}
export function mountSoftAsk(doc,win,subscribe,{now = () => Date.now(),dwellMs = SOFT_ASK_DWELL_MS,snoozeMs = SOFT_ASK_SNOOZE_MS} = {}) {const root = doc.querySelector("[data-push]");
if (root?.dataset.pushState !== "subscribe") return null;
if (softAskSnoozed(win, now())) return null;
if (softAskSeenThisSession(win)) return null;
const copy = {body: t("pushInvite.body"),accept: t("pushInvite.accept"),dismiss: t("pushInvite.dismiss"),ariaLabel: t("pushInvite.ariaLabel")};
if (!copy.body || !copy.accept) return null;
const { panel, status, accept, dismiss } = buildSoftAsk(doc, copy);
let shown = false;
let dwellTimer = 0;
let dwellStartedAt = null;
let dwellRemaining = dwellMs;
function stopScheduling() {doc.removeEventListener("visibilitychange", onVisibility);
doc.removeEventListener(RESET_ALERT_INTENT_EVENT, show);
win.clearTimeout(dwellTimer);
dwellTimer = 0;
dwellStartedAt = null;}
function close(snooze) {if (snooze) snoozeSoftAsk(win, now(), snoozeMs);
panel.remove();
stopScheduling();}
function show() {if (shown) return;
if (root.dataset.pushState !== "subscribe") return;
shown = true;
rememberSoftAskSession(win);
stopScheduling();
doc.body.appendChild(panel);
win.requestAnimationFrame(() => win.requestAnimationFrame(() => {panel.setAttribute("data-open", "true");
status.textContent = copy.body;}));}
accept.addEventListener("click", () => {close(false);
subscribe();});
dismiss.addEventListener("click", () => close(true));
doc.addEventListener(RESET_ALERT_INTENT_EVENT, show);
function startDwell() {win.clearTimeout(dwellTimer);
dwellStartedAt = now();
dwellTimer = win.setTimeout(show, dwellRemaining);}
function onVisibility() {if (doc.visibilityState === "visible") {startDwell();
return;}
if (dwellStartedAt !== null) {dwellRemaining = Math.max(0, dwellRemaining - Math.max(0, now() - dwellStartedAt));}
win.clearTimeout(dwellTimer);
dwellTimer = 0;
dwellStartedAt = null;}
doc.addEventListener("visibilitychange", onVisibility);
if (doc.visibilityState === "visible") startDwell();
return { show, close, panel, accept, dismiss };}
