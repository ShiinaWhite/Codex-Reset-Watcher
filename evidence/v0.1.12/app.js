import { initBackground } from "./bg.js";
import { retireExpiredPromos } from "./dom.js";
import { initClientI18n } from "./i18n.js";
import { initPalette } from "./palette.js";
import { initStatusPill } from "./status.js";
function makeAnnouncer() {const region = document.getElementById("live");
let timer = 0;
return (message) => {if (!region) return;
region.textContent = "";
clearTimeout(timer);
timer = setTimeout(() => {region.textContent = message;}, 40);};}
function runIntro() {const root = document.documentElement;
const target = document.getElementById("typed-cmd");
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const seen = sessionStorage.getItem("codexreset.intro");
if (!target || reduced || seen) {root.dataset.intro = "done";
if (target) target.textContent = "codex /status";
return;}
sessionStorage.setItem("codexreset.intro", "1");
root.dataset.intro = "typing";
const text = "codex /status";
let index = 0;
target.textContent = "";
const type = () => {if (index <= text.length) {target.textContent = text.slice(0, index);
index += 1;
setTimeout(type, 34 + Math.random() * 40);} else {root.dataset.intro = "done";}};
setTimeout(type, 250);}
async function initPushControl() {if (!document.querySelector("[data-push]")) return;
try {const { initPush } = await import("./push.js");
await initPush(document, window);} catch {}}
async function initAlerts() {if (!document.getElementById("alerts-open")) return;
try {const { initAlertsHub } = await import("./alerts-hub.js");
initAlertsHub(document);} catch {}}
async function initPage(page, announce) {if (page === "home") {const [{ createTracker }, { createHome }, { createHomeRadar }, { initLegalModal }] = await Promise.all([import("./tracker.js"),import("./home.js"),import("./home-radar.js"),import("./modal.js")]);
const tracker = createTracker({ announce });
const radar = createHomeRadar({ homeCard: true });
createHome({announce,onForecast: (forecast) => {tracker?.setAnchor(forecast.last_reset_at);
radar?.setForecast(forecast);}});
initLegalModal();
runIntro();
return tracker;}
if (page === "timeline") {const { createTimelinePage } = await import("./timeline.js");
createTimelinePage({ announce });
return null;}
if (page === "radar") {const [{ createHomeRadar }, { createRadar }, { createRadarAggregate }] = await Promise.all([import("./home-radar.js"),import("./radar.js"),import("./juice.js")]);
const homeRadar = createHomeRadar();
createRadar({ announce, onForecast: homeRadar.setForecast });
createRadarAggregate();
return null;}
if (page === "juice") {const { createJuicePage } = await import("./juice.js");
createJuicePage({ announce });
return null;}
if (page === "usage") {const [{ createUsagePage }, { createResetPoll }] = await Promise.all([import("./usage-page.js"),import("./reset-poll.js")]);
createUsagePage({ announce });
createResetPoll({ announce });
return null;}
if (page === "worthit") {const { createWorthItPage } = await import("./worthit-page.js");
createWorthItPage({ announce });
return null;}
if (page === "banked") {const [{ createBankedPage }, { createBankedEvidence }] = await Promise.all([import("./banked-page.js"),import("./banked-evidence.js")]);
createBankedPage({ announce });
createBankedEvidence();
return null;}
if (page === "status") {const { createStatusPage } = await import("./status-page.js");
createStatusPage({ announce });
return null;}
if (page === "tibo") {const [{ createTiboPage }, { createHomeRadar }] = await Promise.all([import("./tibo.js"),import("./home-radar.js")]);
createTiboPage({ announce });
createHomeRadar({ limit: 10 });}
return null;}
async function init() {await initClientI18n();
const announce = makeAnnouncer();
const page = document.body.dataset.page || "home";
const background = initBackground();
initStatusPill();
void initAlerts();
const tracker = await initPage(page, announce);
initPalette({ tracker, announce });
initPushControl();
if (background) {const signalHost = document.getElementById("signal-banner");
if (signalHost) {const sync = () => background.setSignal(signalHost.dataset.state === "active");
new MutationObserver(sync).observe(signalHost, { attributes: true, attributeFilter: ["data-state"] });
sync();}}}
if (document.readyState === "loading") {document.addEventListener("DOMContentLoaded", () => {retireExpiredPromos();
void init();});} else {retireExpiredPromos();
void init();}
