import { safeXUrl } from "./dom.js";
import { relativeTime } from "./time.js";
const BANKED_STATES = new Set(["announced", "arriving", "available", "unknown"]);
export const HOME_BANKED_STATES = new Set(["announced", "arriving", "available", "unknown"]);
export const CARD_TEXT_MAX = 600;
export const HOME_QUOTE_MAX = 180;
export function clampText(text, max) {const value = String(text ?? "").trim();
if (value.length <= max) return value;
const head = value.slice(0, max);
const cut = head.search(/\s\S*$/);
return `${(cut > max / 2 ? head.slice(0, cut) : head).trimEnd()}…`;}
export function latestActionableBankedRecord(records, now = Date.now()) {if (!Array.isArray(records) || !Number.isFinite(now)) return null;
let latest = null;
let latestAt = -Infinity;
for (const record of records) {if (!HOME_BANKED_STATES.has(record?.banked_state)) continue;
const at = Date.parse(record?.announced_at ?? record?.at ?? "");
if (!Number.isFinite(at) || at > now || at <= latestAt) continue;
latest = record;
latestAt = at;}
return latest;}
export function selectBankedUpdates(events, limit = 5, now = Date.now()) {const out = [];
for (const event of events ?? []) {const state = BANKED_STATES.has(event?.banked_state) ? event.banked_state : null;
const banked = state || event?.group === "credits" || event?.kind === "banked" || event?.reset_kind === "banked";
const at = event?.announced_at ?? event?.at;
const summary = clampText(event?.text ?? event?.summary ?? "", CARD_TEXT_MAX);
const localizedText = clampText(event?.localized_text ?? "", CARD_TEXT_MAX);
const url = safeXUrl(event?.url);
if (!banked || !event?.id || !Number.isFinite(Date.parse(at)) || Date.parse(at) > now || !summary || !url) continue;
out.push({...event,announced_at: at,banked_state: state ?? "unknown",summary,...(localizedText ? { localized_summary: localizedText } : {}),url});}
return out.sort((left, right) => Date.parse(right.announced_at) - Date.parse(left.announced_at)).slice(0, limit);}
export function bankedShareIntentUrl(template, announcedAt, now = Date.now()) {const at = Date.parse(announcedAt ?? "");
if (typeof template !== "string" || !template.trim() || !Number.isFinite(at) || !Number.isFinite(now) || at > now) return "";
const days = Math.floor((now - at) / 86_400_000);
const text = template.replace("{days}", String(days)).replace("{plural}", days === 1 ? "" : "s");
return `https://x.com/intent/post?text=${encodeURIComponent(text)}`;}
export function bankedStatePresentation(record, translate = (path) => path) {const explicit = BANKED_STATES.has(record?.banked_state) ? record.banked_state : null;
const bankedRecord = record?.kind === "banked" || record?.group === "credits" || record?.reset_kind === "banked";
if (!explicit && !bankedRecord) return null;
const state = explicit ?? "unknown";
return {state,label: translate(`bankedStates.${state}`)};}
export function currentBankedStatePresentation(record, translate = (path) => path, now = Date.now()) {const presentation = bankedStatePresentation(record, translate);
if (!presentation) return presentation;
if (presentation.state === "available") return { ...presentation, accountAction: true };
if (presentation.state === "announced" || presentation.state === "arriving") {const at = Date.parse(record?.announced_at ?? record?.at ?? "");
const windowEnd = Date.parse(record?.official_window?.end_at ?? "");
const staleAt = Math.max(at + 86_400_000, Number.isFinite(windowEnd) ? windowEnd : -Infinity);
if (!Number.isFinite(at) || !Number.isFinite(now) || now < staleAt) return presentation;}
return {...presentation,accountAction: true,label: translate("bankedStatus.checkAccount"),notice: translate("bankedStatus.note")};}
export function renderBankedState(node, presentation) {node.textContent = presentation.label;
node.setAttribute("target", "_blank");
node.setAttribute("rel", "noopener nofollow");
if (presentation.accountAction && node.dataset.accountUrl) node.setAttribute("href", node.dataset.accountUrl);
else node.removeAttribute("href");}
export function syncBankedLatestRelative(doc = document, now = Date.now()) {const time = doc.getElementById("banked-latest-time");
const output = doc.getElementById("banked-latest-relative");
const text = time?.dateTime ? relativeTime(time.dateTime, now, doc.documentElement.lang) : "";
if (output && text) output.textContent = text;
return text;}
const TWEET_ID_RE = /^\d{5,25}$/;
export function bankedHomeAlertProjection(record) {const id = String(record?.id ?? "");
const at = Date.parse(record?.announced_at ?? record?.at ?? "");
const summary = clampText(record?.text ?? record?.summary ?? "", HOME_QUOTE_MAX);
const localizedSummary = clampText(record?.localized_text ?? record?.localized_summary ?? "", HOME_QUOTE_MAX);
const state = record?.banked_state;
if (!TWEET_ID_RE.test(id) || !Number.isFinite(at) || !summary || !HOME_BANKED_STATES.has(state)) return null;
return {id,kind: "banked",state,source_at: new Date(at).toISOString(),summary,...(localizedSummary ? { localized_summary: localizedSummary } : {}),...(typeof record?.translation_status === "string" ? { translation_status: record.translation_status } : {}),url: String(record?.url ?? `https://x.com/thsottiaux/status/${id}`),score: null,window: null,corrected: false};}
export function selectHomeAlert(latestAlert, bankedRecord) {const banked = bankedHomeAlertProjection(bankedRecord);
if (!banked) return latestAlert ?? null;
if (!latestAlert) return banked;
const alertAt = Date.parse(latestAlert?.source_at ?? "");
return Number.isFinite(alertAt) && alertAt >= Date.parse(banked.source_at) ? latestAlert : banked;}
