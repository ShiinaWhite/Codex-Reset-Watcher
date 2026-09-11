import { formatSinceReset, relativeTime, averageGapDays } from "./time.js";
import { observedAlertPresentation } from "./alert-observation-copy.js";
import { currentBankedStatePresentation, renderBankedState, latestActionableBankedRecord, selectHomeAlert } from "./banked-state.js";
import {latestHomeAlertPresentation,LIVE_REFRESH_MS,startVisiblePolling} from "./home-signal.js";
export {actionableOfficialSignal,currentOfficialSignal,homeSignalPresentation,latestHomeAlertPresentation,nextSuppressedSignalId} from "./home-signal.js";
import {appendTranslationMeta,fetchJsonWithLocale,formatCompactNumber,getClientLangTag,getClientLocale,homeRadarHistogramTitle,localizedField,t} from "./i18n.js";
const REFRESH_MS = 3 * 60_000;
const I18N_REFRESH_MS = 15_000, I18N_WINDOW_MS = REFRESH_MS;
const RESET_KINDS = ["signal", "boost", "unlock"];
const FILTER_KINDS = {signal: new Set([...RESET_KINDS, "candidate", "banked"]),reset: new Set(RESET_KINDS),candidate: new Set(["candidate"]),banked: new Set(["banked"]),limits: new Set(["limits"]),note: new Set(["other", "codex", "context"])};
const EVENT_KIND = { reset: "signal", boost: "boost", unlock: "unlock", credits: "banked" };
const el = (id) => document.getElementById(id);
function appendIconLink(link, text) {link.classList.add("icon-link");
link.textContent = text;}
function renderTweetText(container, text) {container.textContent = "";
const parts = String(text).split(/(https?:\/\/\S+)/g);
for (const part of parts) {if (/^https?:\/\//.test(part)) {const a = document.createElement("a");
a.href = part;
a.textContent = part.replace(/^https?:\/\//, "").slice(0, 42);
a.rel = "noopener nofollow ugc";
a.target = "_blank";
container.append(a);} else if (part) {container.append(document.createTextNode(part));}}}
function formatCount(n) {return formatCompactNumber(n);}
function translatedOr(path, fallback, translate = t) {const value = translate(path);
return value === path ? fallback : value;}
export function homeRadarVerificationPresentation(tweet, translate = t) {if (tweet?.reset_verification_candidate !== true) return null;
const result = tweet.observation_result;
const status = tweet.reset_verification_status;
if (result === "reset_observed" || status === "confirmed") {return {state: "confirmed",kind: "signal",label: `✓ ${translate("juice.verification.confirmed")}`,title: translate("homeRadar.kindWhy.signal")};}
if (result === "unchanged" || status === "rejected") {const label = translate("juice.deltaNone");
return {state: "rejected",kind: "other",label: `≠ ${label}`,title: label};}
if (result === "unverified" || status === "unverified") {const label = translatedOr("homeRadar.verificationLabels.unverified", "UNVERIFIED", translate);
return {state: "unverified",kind: "other",label: `? ${label}`,title: label};}
if (status === "expired") return { kind: "signal" };
return {state: "pending",kind: "candidate",label: `… ${translate("homeRadar.kindLabels.candidate")}`,title: translate("homeRadar.kindWhy.candidate")};}
export function visibleFeedRows(feed, filter = "all", translate = t) {const kinds = FILTER_KINDS[filter];
const contextRows = filter === "all" || kinds?.has("context")? (feed.radar_context ?? []).map((row) => ({ ...row, kind: "context" })): [];
return [...feed.tweets, ...contextRows].map((tweet) => {const verification = homeRadarVerificationPresentation(tweet, translate);
return { tweet, verification, kind: verification?.kind ?? tweet?.kind ?? "other" };}).filter((row) => filter === "all" || !kinds || kinds.has(row.kind)).sort((left, right) => Date.parse(right.tweet.at ?? 0) - Date.parse(left.tweet.at ?? 0));}
export function createHomeRadar({ limit = 20, homeCard = false } = {}) {let feed = null;
let filter = "all";
let latestAlert = null;
let pendingSince = null;
const locale = getClientLocale();
const langTag = getClientLangTag();
function liveRefreshActive(now = Date.now()) {if (homeCard) return true;
if (latestAlert?.kind === "watch" && latestAlert?.state === "active") return true;
const at = Date.parse(latestAlert?.source_at ?? "");
return latestAlert?.kind === "reset" && Number.isFinite(at) && now - at >= 0 && now - at < 15 * 60_000;}
function renderSignal() {const banner = el("signal-banner");
if (!banner) return;
const presentation = latestHomeAlertPresentation(selectHomeAlert(latestAlert, latestActionableBankedRecord(feed?.events, Date.now())));
if (presentation) {const { signal } = presentation;
const observation = presentation.kind === "event" ? observedAlertPresentation(signal, locale) : null;
const summary = observation ? { value: observation.summary } : localizedField(signal, "summary", locale);
const alertLabel = presentation.kind === "banked"? t("home.latestBankedAlert"): t("home.latestAlert");
const expiredLabel = presentation.kind === "preview" && presentation.state === "expired"? t("tracker.weeklyExpired"): "";
banner.dataset.state = "active";
banner.dataset.signalKind = presentation.kind;
banner.closest(".forecast-console")?.setAttribute("data-signal-state", "active");
banner.setAttribute("aria-live", "polite");
banner.textContent = "";
{const avatar = document.createElement("img");
avatar.className = "signal-avatar";
avatar.src = observation?.avatar_src ?? "/tibo-avatar.jpg";
avatar.alt = "";
avatar.width = 28;
avatar.height = 28;
avatar.loading = "lazy";
avatar.decoding = "async";
avatar.addEventListener("error", () => avatar.remove(), { once: true });
banner.append(avatar);}
const strong = document.createElement("strong");
strong.className = "signal-alert-mark";
strong.textContent = presentation.kind === "banked"? "🎟️": presentation.kind === "preview" ? "⚠️" : "✅";
strong.setAttribute("role", "img");
strong.setAttribute("aria-label",observation ? observation.attribution : expiredLabel? `${alertLabel}; ${expiredLabel}`: presentation.kind === "banked"? t("homeRadar.kindWhy.banked"): presentation.kind === "preview"? t("home.confidenceSummaryOfficial"): t("homeRadar.kindWhy.signal"));
const quote = document.createElement("span");
quote.className = `signal-evidence-quote${observation ? " observed-copy" : ""}`;
quote.textContent = summary.value;
const age = formatSinceReset(signal.at, Date.now(), langTag);
const meta = document.createElement("small");
meta.className = "signal-evidence-meta";
meta.textContent = [observation?.attribution ?? alertLabel,expiredLabel,presentation.window_label,age].filter(Boolean).join(" · ");
banner.append(strong, quote, meta);
const sourceUrl = observation?.source_url ?? presentation.source_url;
if (sourceUrl) {const link = document.createElement("a");
link.classList.add("signal-evidence-source");
link.href = sourceUrl;
link.rel = "noopener";
link.target = "_blank";
appendIconLink(link, observation?.source_label ?? t("common.viewOnX"));
banner.append(link);}} else {banner.dataset.state = "quiet";
delete banner.dataset.signalKind;
banner.closest(".forecast-console")?.setAttribute("data-signal-state", "quiet");
banner.textContent = "";
const link = document.createElement("a");
link.href = "https://x.com/thsottiaux";
link.rel = "noopener";
link.target = "_blank";
appendIconLink(link, "@thsottiaux");
banner.append(document.createTextNode(t("homeRadar.watching")), link);}}
function renderBankedSignal() {const host = el("home-banked-signal");
if (!host) return;
const actions = host.closest(".reset-since-actions");
const event = latestActionableBankedRecord(feed?.events, Date.now());
const presentation = currentBankedStatePresentation(event, t);
if (!event || !presentation) {host.hidden = true;
actions?.setAttribute("data-banked-active", "false");
return;}
const at = event.announced_at ?? event.at ?? "";
actions?.setAttribute("data-banked-active", "true");
host.hidden = false;
host.dataset.bankedId = String(event.id ?? "");
host.dataset.bankedState = presentation.state;
const state = el("home-banked-signal-state");
const time = el("home-banked-signal-time");
const age = relativeTime(at, Date.now(), langTag) || t("common.unavailable");
if (state) {renderBankedState(state, presentation);
state.title = presentation.notice ?? "";}
const notice = el("home-banked-notice");
if (notice) notice.textContent = presentation.notice ?? t("homeRadar.kindWhy.banked");
if (time) {time.dateTime = at;
time.textContent = age;}}
function renderFeed() {const list = el("feed-list");
if (!list || !feed) return;
const rows = visibleFeedRows(feed, filter);
list.textContent = "";
if (!rows.length) {const empty = document.createElement("li");
empty.className = "feed-empty";
empty.textContent =
filter === "all"? t("homeRadar.feedEmptyAll"): t("homeRadar.feedEmptyFiltered");
list.append(empty);
return;}
const now = Date.now();
for (const { tweet, verification, kind } of rows.slice(0, Math.max(1, limit))) {const text = localizedField(tweet, "text", locale);
const item = document.createElement("li");
item.className = "feed-item";
item.dataset.kind = kind;
if (verification?.state) item.dataset.verificationState = verification.state;
const meta = document.createElement("div");
meta.className = "feed-meta-row";
const chip = document.createElement("span");
chip.className = "chip";
chip.dataset.kind = kind;
if (verification?.state) chip.dataset.verificationState = verification.state;
chip.textContent = verification?.label ?? (t(`homeRadar.kindLabels.${kind}`) || t("homeRadar.kindLabels.other"));
const why = verification?.title ?? t(`homeRadar.kindWhy.${kind}`);
if (why !== `homeRadar.kindWhy.${kind}`) chip.title = why;
const bankedState = currentBankedStatePresentation(feed.events?.find((event) => String(event.id) === String(tweet.id)) ?? tweet, t, now);
const bankedChips = [];
if (bankedState) {if (kind !== "banked") {const bankedChip = document.createElement("span");
bankedChip.className = "chip";
bankedChip.dataset.kind = "banked";
bankedChip.textContent = t("homeRadar.kindLabels.banked");
bankedChips.push(bankedChip);}
const stateChip = document.createElement("a");
stateChip.className = "chip banked-state";
stateChip.dataset.bankedState = bankedState.state;
stateChip.dataset.accountUrl = list.dataset.accountUrl;
renderBankedState(stateChip, bankedState);
bankedChips.push(stateChip);}
const time = document.createElement("a");
time.className = "feed-time";
time.href = tweet.url;
time.rel = "noopener";
time.target = "_blank";
appendIconLink(time, relativeTime(tweet.at, now, langTag) || "—");
meta.append(chip, ...bankedChips, time);
const body = document.createElement("p");
body.className = "feed-text";
renderTweetText(body, text.value);
item.append(meta, body);
appendTranslationMeta(item, text, {sourceUrl: tweet.url,sourceLabel: t("translation.originalOnX")});
const stats = [["replies", tweet.replies],["reposts", tweet.reposts],["likes", tweet.likes]].filter(([, count]) => typeof count === "number");
if (stats.length) {const statsRow = document.createElement("div");
statsRow.className = "feed-stats";
const statIcons = { replies: "reply", reposts: "repost", likes: "heart" };
for (const [name, count] of stats) {const stat = document.createElement("span");
const icon = document.createElement("span");
stat.className = "feed-stat";
icon.className = "ui-icon";
icon.dataset.icon = statIcons[name];
icon.setAttribute("aria-hidden", "true");
stat.setAttribute("aria-label", `${formatCount(count)} ${name}`);
stat.append(icon, document.createTextNode(formatCount(count)));
statsRow.append(stat);}
item.append(statsRow);}
list.append(item);}}
function renderTimeline() {const timeline = el("timeline");
const stat = el("timeline-stat");
if (!timeline || !feed) return;
timeline.textContent = "";
const events = feed.events ?? [];
if (!events.length) {const empty = document.createElement("li");
empty.className = "feed-empty";
empty.textContent = t("homeRadar.timelineEmpty");
timeline.append(empty);
if (stat) stat.textContent = "";
return;}
for (const event of events.slice(0, 8)) {const summaryInfo = localizedField(event, "summary", locale);
const item = document.createElement("li");
item.className = "event";
item.dataset.type = event.type;
const date = document.createElement("span");
date.className = "event-date";
date.textContent = event.date;
const type = document.createElement("span");
type.className = "event-type";
type.textContent = t(`homeRadar.kindLabels.${EVENT_KIND[event.group] ?? "other"}`);
const bankedState = currentBankedStatePresentation(event, t);
if (bankedState) {if (event.group !== "credits") {type.append(document.createTextNode(` · ${t("homeRadar.kindLabels.banked")}`));}
const state = document.createElement("a");
state.className = "event-banked-state";
state.dataset.bankedState = bankedState.state;
state.dataset.accountUrl = timeline.dataset.accountUrl;
renderBankedState(state, { ...bankedState, label: ` · ${bankedState.label}` });
type.append(state);}
const summary = document.createElement("span");
summary.className = "event-summary";
summary.textContent = summaryInfo.value;
item.append(date, type, summary);
if (event.url) {const link = document.createElement("a");
link.href = event.url;
link.rel = "noopener";
link.target = "_blank";
link.className = "event-link";
link.setAttribute("aria-label", t("homeRadar.timelineSourceAria", { date: event.date }));
item.append(link);}
appendTranslationMeta(item, summaryInfo, {sourceUrl: event.url,sourceLabel: t("translation.originalOnX")});
timeline.append(item);}
if (stat) {const gap = averageGapDays(events.map((e) => e.date));
stat.textContent = gap ? t("homeRadar.timelineGap", { count: gap }) : "";}}
function renderMeta() {const meta = el("feed-meta");
if (!meta || !feed) return;
const synced = feed.fetched_at ? relativeTime(feed.fetched_at, Date.now(), langTag) : "never";
const source = feed.source === "seed" ? t("homeRadar.metaSourceArchival") : "";
meta.textContent = t("homeRadar.metaSynced", {time: synced,stale: feed.stale ? t("homeRadar.metaStale") : "",source});
meta.dataset.stale = String(Boolean(feed.stale));}
function renderHistogram() {const svg = el("hist-svg");
const caption = el("hist-caption");
if (!svg || !feed?.events?.length) return;
const bins = Array(24).fill(0);
for (const event of feed.events) {const t = new Date(event.announced_at ?? event.at ?? `${event.date}T12:00:00Z`);
if (!Number.isNaN(t.getTime())) bins[t.getHours()] += 1;}
const max = Math.max(...bins, 1);
const peak = bins.indexOf(Math.max(...bins));
for (let hour = 0; hour < 24; hour += 1) {const rect = svg.querySelector(`rect[data-hour="${hour}"]`);
if (!rect) continue;
const h = Math.round((bins[hour] / max) * 44);
rect.setAttribute("height", String(Math.max(bins[hour] ? 3 : 1, h)));
rect.setAttribute("y", String(50 - Math.max(bins[hour] ? 3 : 1, h)));
rect.setAttribute("fill", hour === peak && bins[hour] ? "#ffb454" : bins[hour] ? "#41ff8b" : "#1d2a24");
const title = rect.querySelector("title");
if (title) title.textContent = homeRadarHistogramTitle(String(hour).padStart(2, "0"), bins[hour]);}
if (caption) {const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
caption.textContent = t("homeRadar.histogramCaption", {count: feed.events.length,zone,hour: String(peak).padStart(2, "0")});}}
function renderAll() {renderSignal();
renderBankedSignal();
renderFeed();
renderTimeline();
renderMeta();
renderHistogram();}
async function refresh(force = false) {try {const nextFeed = await fetchJsonWithLocale(force ? "/api/feed?_fresh=1" : "/api/feed");
if (Date.parse(nextFeed?.fetched_at ?? "") < Date.parse(feed?.fetched_at ?? "")) return;
feed = nextFeed;
renderAll();} catch {if (feed) {feed.stale = true;
renderBankedSignal();
renderMeta();} else {const meta = el("feed-meta");
if (meta) meta.textContent = t("homeRadar.metaUnavailable");}}}
for (const button of document.querySelectorAll("[data-filter]")) {button.addEventListener("click", () => {filter = button.dataset.filter;
for (const other of document.querySelectorAll("[data-filter]")) {other.setAttribute("aria-pressed", String(other === button));}
renderFeed();});}
const showSignals = () => document.querySelector('[data-filter="signal"]')?.click();
el("signals-jump")?.addEventListener("click", showSignals);
if (document.location?.hash === "#signals") showSignals();
const translationRefreshActive = (now = Date.now()) => {const pending = feed?.translation_status === "pending";
pendingSince = pending ? pendingSince ?? now : null;
return pending && now - pendingSince < I18N_WINDOW_MS;};
startVisiblePolling((force) => refresh(force || translationRefreshActive()),() => translationRefreshActive()? I18N_REFRESH_MS: liveRefreshActive()? LIVE_REFRESH_MS: REFRESH_MS);
function setForecast(forecast) {latestAlert = forecast?.latest_alert ?? null;
renderSignal();}
return { refresh, setForecast };}
