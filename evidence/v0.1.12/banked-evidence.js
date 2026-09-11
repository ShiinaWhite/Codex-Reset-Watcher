import { bankedShareIntentUrl, bankedStatePresentation, currentBankedStatePresentation, renderBankedState, selectBankedUpdates, syncBankedLatestRelative } from "./banked-state.js";
import { fetchJsonWithLocale, getClientLocale, localizedField, t } from "./i18n.js";
import { relativeTime } from "./time.js";
const el = (id) => document.getElementById(id);
export { selectBankedUpdates };
function node(tag, cls = "", text = "") {const item = document.createElement(tag);
if (cls) item.className = cls;
if (text) item.textContent = text;
return item;}
function timeText(event) {return new Date(event.announced_at).toLocaleString(document.documentElement.lang, {month: "short",day: "numeric",hour: "2-digit",minute: "2-digit",timeZoneName: "short"});}
function localizedSummary(event) {return localizedField(event, "summary", getClientLocale()).value;}
function renderUpdate(event, copy) {const state = bankedStatePresentation(event, t);
const item = node("li", "banked-update");
item.dataset.bankedUpdateId = event.id;
item.dataset.bankedUpdateState = state.state;
const head = node("div", "banked-update-head");
head.append(node("strong", "banked-primary-label", "BANKED"), node("span", "banked-weak-label", state.label));
const time = node("time", "banked-update-time", timeText(event));
time.dateTime = event.announced_at;
head.append(time);
const text = node("p", "banked-update-text", localizedSummary(event));
const link = node("a", "icon-link banked-update-link", copy.viewSource);
link.href = event.url;
link.rel = "noopener";
link.target = "_blank";
item.append(head, text, link);
return item;}
function renderLatest(event, copy) {const state = currentBankedStatePresentation(event, t);
const latest = el("banked-latest-signal");
latest.dataset.bankedLatestId = event.id;
latest.dataset.bankedLatestState = state.state;
renderBankedState(el("banked-latest-state"), state);
el("banked-latest-notice").textContent = state.notice ?? "";
el("banked-latest-notice").hidden = !state.notice;
el("banked-latest-text").textContent = localizedSummary(event);
const time = el("banked-latest-time");
time.dateTime = event.announced_at;
time.textContent = timeText(event);
el("banked-latest-relative").textContent = relativeTime(event.announced_at, Date.now(), document.documentElement.lang);
const link = el("banked-latest-source");
link.href = event.url;
link.textContent = copy.copyViewSource;
el("banked-latest-share").href = bankedShareIntentUrl(copy.copyShareText, event.announced_at);}
export async function createBankedEvidence() {const root = el("banked-recent");
if (!root) return;
syncBankedLatestRelative();
const copy = root.dataset;
try {const payload = await fetchJsonWithLocale("/api/timeline");
const events = selectBankedUpdates(payload?.events);
if (!events.length) throw new Error("no banked updates");
renderLatest(events[0], copy);
el("banked-recent-list").replaceChildren(...events.map((event) => renderUpdate(event, copy)));
root.dataset.source = "live";
el("banked-evidence-freshness").textContent = copy.copyLive.replace("{time}", timeText(events[0]));} catch {root.dataset.source = "fallback";
el("banked-latest-signal")?.setAttribute("aria-busy", "false");
el("banked-evidence-freshness").textContent = copy.copyFallback;}}
