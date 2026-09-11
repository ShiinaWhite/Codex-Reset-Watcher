import { formatSinceReset, relativeTime } from "./time.js";
import { safeHttpsUrl, safeXUrl } from "./dom.js";
import { createHomeCountdown } from "./home-countdown.js";
import {actionableOfficialSignal,registerCodexResetWebMcpTools,startVisiblePolling} from "./home-signal.js";
import {appendTranslationMeta,fetchJsonWithLocale,getClientLangTag,getClientLocale,homeConfidenceSummary,localizedField,t} from "./i18n.js";
const el = (id) => document.getElementById(id);
const locale = () => getClientLocale();
const langTag = () => getClientLangTag();
const HOME_FORECAST_REFRESH_MS = 60_000;
const HOME_RHYTHM_REFRESH_MS = 5 * 60_000;
function fmtDateTime(iso, timeZone = null) {const date = new Date(iso);
if (Number.isNaN(date.getTime())) return t("common.unavailable");
return new Intl.DateTimeFormat(langTag(), {month: "short",day: "numeric",hour: "numeric",minute: "2-digit",...(timeZone ? { timeZone, timeZoneName: "short" } : {})}).format(date);}
function timeZoneOffset(timeZone) {try {const part = new Intl.DateTimeFormat("en-US", {timeZone,timeZoneName: "shortOffset"}).formatToParts().find(({ type }) => type === "timeZoneName");
return part?.value ?? timeZone;} catch {return timeZone;}}
function setIconLabel(node, text) {const labelText = node?.querySelector("[data-label-text]");
if (labelText) {labelText.textContent = text;
return;}
const control = node?.querySelector("a, button");
if (node?.firstElementChild) {node.replaceChildren(node.firstElementChild, document.createTextNode(text), ...(control ? [control] : []));}}
const HOT_PERCENT = 50;
function setIconLink(link, text) {link.className = "icon-link";
link.textContent = text;}
function setForecastChance(horizon, value) {const progress = el(`forecast-${horizon}-bar`);
const figure = el(`forecast-${horizon}`);
const available = typeof value === "number";
figure.textContent = available ? `${value}%` : t("common.unavailable");
progress.value = available ? value : 0;
progress.textContent = available ? `${value}%` : t("common.notAvailable");
const hot = available && value > HOT_PERCENT;
for (const node of [figure, progress]) {if (hot) node.setAttribute("data-hot", "true");
else node.removeAttribute("data-hot");}}
function confidenceLabel(value) {const normalized = String(value ?? "").toLowerCase();
const key = normalized ? `home.confidence${normalized[0].toUpperCase()}${normalized.slice(1)}` : "";
const label = key ? t(key) : "";
return label && label !== key ? label : t("common.unavailable");}
function renderEvidence(items) {const list = el("forecast-evidence");
if (!list) return;
list.textContent = "";
for (const item of items ?? []) {const label = localizedField(item, "label", locale());
const detail = localizedField(item, "detail", locale());
const row = document.createElement("li");
const strong = document.createElement("strong");
strong.textContent = `${label.value}: `;
row.append(strong, document.createTextNode(detail.value));
if (item.href) {row.append(document.createTextNode(" "));
const link = document.createElement("a");
link.href = item.href;
if (!item.href.startsWith("/")) {link.rel = "noopener";
link.target = "_blank";}
setIconLink(link, item.href.startsWith("/") ? t("common.details") : t("common.sourceLinkLabel"));
row.append(link);}
appendTranslationMeta(row, detail, {sourceUrl: item.href && !item.href.startsWith("/") ? item.href : "",sourceLabel: t("translation.originalSource")});
list.append(row);}}
function renderRecent(feed) {const list = el("home-recent-list");
if (!list) return;
list.textContent = "";
const rows = (feed?.tweets ?? []).filter((tweet) => ["signal", "boost", "unlock", "banked", "limits"].includes(tweet.kind)).slice(0, 3);
if (!rows.length) {const item = document.createElement("li");
item.className = "mini-empty";
item.textContent = t("home.miniEmpty");
list.append(item);
return;}
for (const tweet of rows) {const text = localizedField(tweet, "text", locale());
const item = document.createElement("li");
item.className = "mini-post";
const head = document.createElement("div");
head.className = "mini-head";
head.textContent = `${t(`homeRadar.kindLabels.${tweet.kind}`)} · ${relativeTime(tweet.at, Date.now(), langTag())}`;
const body = document.createElement("p");
body.textContent = text.value;
const link = document.createElement("a");
link.href = tweet.url;
link.rel = "noopener";
link.target = "_blank";
setIconLink(link, t("common.viewOnX"));
item.append(head, body, link);
appendTranslationMeta(item, text, {sourceUrl: tweet.url,sourceLabel: t("translation.originalOnX")});
list.append(item);}}
function renderForecast(forecast, browserTimeZone) {const officialSignal = forecast.official_signal;
const commitmentSignal = actionableOfficialSignal(officialSignal, Date.now());
const teasePercent = forecast.signal_score?.band === "tease" ? forecast.signal_score.value : null;
const teaseLead = !officialSignal && typeof teasePercent === "number" && teasePercent >= 50;
const teaseSignal = commitmentSignal ? null : forecast.tease_signal ?? null;
const resetLanded = Date.parse(forecast.last_reset_at ?? "") >
Date.parse(officialSignal?.at ?? "");
const promiseLead = Boolean(officialSignal) && !commitmentSignal;
const officialSummary = localizedField(officialSignal ?? {}, "summary", locale());
el("forecast-mode").textContent = commitmentSignal? t("home.forecastModeOfficial"): officialSignal? t("home.hintStatus"): t("home.forecastModeModel");
const verdict = el("forecast-verdict");
if (verdict) {verdict.dataset.state = officialSignal || teaseLead ? "active" : "quiet";
verdict.textContent = commitmentSignal? t("home.verdictOfficial"): officialSignal? t("home.hintStatus"): teaseLead? t("home.teasedAlert"): t("home.verdictQuiet");}
const chanceLabel = el("forecast-chance-label");
if (chanceLabel) {setIconLabel(chanceLabel, commitmentSignal || promiseLead? t("home.chanceLabelCommitment"): teaseLead || teaseSignal ? t("home.teasedAlert") : t("home.chanceLabelModel"));
chanceLabel.removeAttribute("aria-label");
if (commitmentSignal || promiseLead || teaseSignal) {chanceLabel.removeAttribute("aria-description");} else {chanceLabel.setAttribute("aria-description", t("home.ariaChanceModel"));}}
const windowLabel = el("forecast-window-label");
setIconLabel(windowLabel, officialSignal ? t("home.windowOfficial") : t("home.windowHistorical"));
windowLabel.setAttribute("aria-label", officialSignal ? t("home.ariaWindowOfficial") : t("home.ariaWindowHistorical"));
const commitmentPercent = teaseLead? teasePercent: forecast.probabilities.commitment_floor_percent;
const leadsWithCommitment = !resetLanded &&
(commitmentSignal || promiseLead || teaseLead) &&
typeof commitmentPercent === "number";
const chanceGroup = el("forecast-chance-group");
if (chanceGroup) chanceGroup.dataset.lead = leadsWithCommitment ? "commitment" : "cadence";
for (const horizon of [el("forecast-horizon-24h"), el("forecast-horizon-48h")]) {if (!horizon) continue;
horizon.textContent = leadsWithCommitment? horizon.dataset.cadenceLabel ?? "": horizon.dataset.defaultLabel ?? "";}
const commitmentChance = el("forecast-commitment-chance");
if (commitmentChance) {commitmentChance.hidden = !leadsWithCommitment;
if (leadsWithCommitment) {commitmentChance.textContent = `${commitmentPercent}%`;}}
const commitmentChanceNote = el("forecast-commitment-chance-note");
if (commitmentChanceNote && leadsWithCommitment) {const noteWindow = teaseLead ? forecast.teased_window?.window : officialSignal?.window;
const noteLabel = noteWindow?.localized_label ?? noteWindow?.label ?? "";
commitmentChanceNote.textContent = noteLabel? t("home.commitmentChanceNote", { window: noteLabel }): t("home.commitmentChanceNoteBare");}
setForecastChance("24h", forecast.probabilities.rounded_24h);
setForecastChance("48h", forecast.probabilities.rounded_48h);
const waitCopy = forecast.wait_copy?.[locale()] ?? null;
const waitToken = forecast.wait_token?.[locale()] ?? null;
const modelTokenCopy = forecast.model_token?.[locale()] ?? null;
if (chanceGroup) chanceGroup.title = waitCopy ?? "";
for (const h of ["24", "48"]) {const aria = forecast.range_aria?.[locale()]?.[`h${h}`];
if (aria) el(`forecast-${h}h-bar`)?.setAttribute("aria-label", aria);}
el("forecast-window").textContent = officialSignal?.window? officialSignal.window.localized_label ?? officialSignal.window.label: forecast.time_window? forecast.time_window.localized_label ?? forecast.time_window.label: t("home.waitingData");
const timeZoneName = forecast.time_window?.timezone || browserTimeZone || "UTC";
const timeZoneNote = el("forecast-timezone-note");
if (timeZoneNote) {const officialWindow = officialSignal?.window ?? null;
const officialZone = officialWindow?.time_zone;
timeZoneNote.textContent = officialWindow? officialZone? t("home.timeZoneOfficialTibo", {time: fmtDateTime(officialWindow.end_at, officialZone)}): t("home.timeZoneOfficial", { time: fmtDateTime(officialWindow.end_at) }): t("home.timeZoneLocal", { zone: timeZoneOffset(timeZoneName) });}
setIconLabel(el("forecast-confidence-label"), commitmentSignal ? t("home.confidenceLabelOfficial") : t("home.confidenceLabelModel"));
const modelConfidence = forecast.confidence;
const confidence = el("forecast-confidence");
confidence.dataset.state = commitmentSignal ? "official" : modelConfidence;
confidence.textContent = commitmentSignal? t("home.confidenceSummaryOfficial"): confidenceLabel(modelConfidence);
const confidenceOutput = el("forecast-confidence-summary");
if (confidenceOutput) {confidenceOutput.textContent = commitmentSignal? t("home.confidenceSummaryOfficial"): (modelTokenCopy ?? waitToken ?? homeConfidenceSummary(forecast));}
const confidenceNote = el("forecast-confidence-note");
if (confidenceNote) confidenceNote.textContent = commitmentSignal ? t("home.confidenceSummaryOfficial") : [homeConfidenceSummary(forecast), waitCopy].filter(Boolean).join(" ");
const hintNote = el("forecast-hint-note");
if (hintNote) {const hintCopy = commitmentSignal ? null : forecast.context_copy?.[locale()] ?? forecast.hint_copy?.[locale()] ?? null;
hintNote.hidden = !hintCopy;
hintNote.textContent = hintCopy ?? "";}
const teaseCard = el("forecast-tease-card");
if (teaseCard) {teaseCard.hidden = !teaseSignal;
if (teaseSignal) {const quote = el("forecast-tease-quote");
if (quote) quote.textContent = teaseSignal.post.quote;
const note = el("forecast-tease-note");
const noteCopy = forecast.tease_card_note?.[locale()] ?? null;
if (note && noteCopy) note.textContent = noteCopy;
const time = el("forecast-tease-time");
if (time) {time.dateTime = teaseSignal.post.at;
time.title = teaseSignal.post.at_label;
time.textContent = forecast.tease_post_age?.[locale()] ?? teaseSignal.post.at_label;}
const link = el("forecast-tease-link");
const href = safeHttpsUrl(teaseSignal.post.url);
if (link && href) link.href = href;}}
el("forecast-last-reset").textContent = forecast.last_reset_at ? fmtDateTime(forecast.last_reset_at) : t("home.noVerifiedReset");
el("forecast-updated").textContent = fmtDateTime(forecast.updated_at);
const official = el("forecast-official");
if (official) {if (officialSignal) {official.hidden = false;
official.innerHTML = "";
const strong = document.createElement("strong");
strong.textContent = t("home.officialSignalPrefix");
const text = document.createElement("span");
text.textContent = officialSummary.value;
official.append(strong, text);
const sourceUrl = safeXUrl(officialSignal.url);
if (sourceUrl) {const link = document.createElement("a");
link.href = sourceUrl;
link.rel = "noopener";
link.target = "_blank";
setIconLink(link, t("common.viewOnX"));
official.append(document.createTextNode(" "), link);}
appendTranslationMeta(official, officialSummary, {sourceUrl,sourceLabel: t("translation.originalOnX")});} else {official.hidden = false;
official.textContent = t("home.noOfficialSignal");}}
renderEvidence(forecast.evidence);}
function initHeroSince() {const host = el("reset-since");
if (!host) return () => {};
const render = () => {const iso = host.dataset.resetAt;
const relative = el("reset-since-relative");
if (iso && relative) relative.textContent = formatSinceReset(iso, Date.now(), langTag());};
const localize = () => {const abs = el("reset-since-abs");
const iso = host.dataset.resetAt;
if (!abs || !iso || !Number.isFinite(Date.parse(iso))) return;
abs.dateTime = iso;
abs.textContent = new Intl.DateTimeFormat(langTag(), {month: "short",day: "numeric",hour: "numeric",minute: "2-digit",timeZoneName: "short"}).format(new Date(iso));};
localize();
render();
setInterval(() => {if (!document.hidden) render();}, 30_000);
document.addEventListener("visibilitychange", () => {if (!document.hidden) render();});
return (lastResetAt) => {if (!lastResetAt || !Number.isFinite(Date.parse(lastResetAt))) return;
host.dataset.resetAt = lastResetAt;
localize();
render();};}
function renderHomeRhythm(payload) {if (!payload?.home_rhythm?.chart) return;
const chart = el("live-home-rhythm-chart");
if (chart) chart.outerHTML = payload.home_rhythm.chart;
const trend = el("live-home-rhythm-trend");
if (trend && payload.home_rhythm.trend) trend.textContent = payload.home_rhythm.trend;}
export function createHome({ announce, onForecast } = {}) {void registerCodexResetWebMcpTools().catch(() => {});
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const reanchorHeroSince = initHeroSince();
const homeCountdown = createHomeCountdown();
let lastForecastFingerprint = "";
let lastForecast = null;
const presentForecast = () => {if (!lastForecast) return null;
renderForecast(lastForecast, zone);
homeCountdown.update(lastForecast);
onForecast?.(lastForecast);
const signal = lastForecast.official_signal;
const fingerprint = `${lastForecast.mode}:${signal?.tweet_id ?? "none"}:${signal?.window?.end_at ?? "none"}:${lastForecast.latest_alert?.id ?? "none"}:${lastForecast.latest_alert?.state ?? "none"}:${lastForecast.last_reset_at ?? "none"}`;
if (fingerprint !== lastForecastFingerprint) {lastForecastFingerprint = fingerprint;
announce?.(actionableOfficialSignal(signal, Date.now())? t("home.announceOfficial", {window: signal.window?.localized_label ?? signal.window?.label ?? t("common.unavailable")}): t("home.announceLoaded", {p24: lastForecast.probabilities.rounded_24h ?? t("common.notAvailable"),confidence: confidenceLabel(lastForecast.confidence)}));}
return lastForecast;};
const refreshForecast = (force = false) => {const retained = presentForecast();
const fresh = force ? "&_fresh=1" : "";
return fetchJsonWithLocale(`/api/forecast?tz=${encodeURIComponent(zone)}${fresh}`).then((forecast) => {lastForecast = forecast;
presentForecast();
const nextResetAt = forecast.last_reset_at;
const currentResetAt = Date.parse(el("reset-since")?.dataset.resetAt ?? "");
const forecastResetAt = Date.parse(nextResetAt ?? "");
if (!Number.isFinite(currentResetAt) || (Number.isFinite(forecastResetAt) && forecastResetAt >= currentResetAt)) {reanchorHeroSince(nextResetAt);}}).catch(() => {if (!retained) {el("forecast-confidence").textContent = t("common.unavailable");
el("forecast-confidence-summary").textContent = t("home.forecastError");
announce?.(t("home.announceError"));}});};
if (el("home-recent-list")) {fetchJsonWithLocale("/api/feed").then(renderRecent).catch(() => {el("home-recent-list").innerHTML = `<li class="mini-empty">${t("home.recentError")}</li>`;});}
const refreshRhythm = (force = false) => fetchJsonWithLocale(force ? "/api/timeline?_fresh=1" : "/api/timeline").then(renderHomeRhythm).catch(() => {});
startVisiblePolling(refreshForecast, () => HOME_FORECAST_REFRESH_MS);
startVisiblePolling(refreshRhythm, () => HOME_RHYTHM_REFRESH_MS);}
