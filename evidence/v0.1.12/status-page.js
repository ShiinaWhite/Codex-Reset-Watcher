import { escapeHtml, safeHttpsUrl } from "./dom.js";
import { relativeTime } from "./time.js";
import {appendTranslationMeta,fetchJsonWithLocale,formatFreshnessCopy,getClientLangTag,getClientLocale,localizedField,t} from "./i18n.js";
const REFRESH_MS = 5 * 60_000;
const VALID_RANGES = new Set(["30d", "90d", "180d"]);
const SURFACE_DEFINITIONS = [{ id: "web", label: "Codex Web", aliases: ["codexweb", "web"] },{ id: "api", label: "Codex API", aliases: ["codexapi", "api"] },{ id: "cli", label: "CLI", aliases: ["codexcli", "cli"] },{ id: "vscode", label: "VS Code extension", aliases: ["vscodeextension", "vscode"] },{id: "desktop",label: "ChatGPT Desktop",aliases: ["codexinchatgptdesktop", "chatgptdesktop", "desktop"]}];
const STATUS_META = {operational: { label: "operational", level: "ok", rank: 0 },under_maintenance: { label: "maintenance", level: "warn", rank: 1 },degraded_performance: { label: "degraded", level: "warn", rank: 2 },partial_outage: { label: "partial outage", level: "bad", rank: 3 },major_outage: { label: "major outage", level: "bad", rank: 4 },unknown: { label: "status n/a", level: "dim", rank: -1 }};
const STATUS_LABEL_KEYS = {operational: "status.statusOperational",under_maintenance: "status.statusMaintenance",degraded_performance: "status.statusDegraded",partial_outage: "status.statusPartialOutage",major_outage: "status.statusMajorOutage",unknown: "status.statusUnavailable"};
const el = (id) => document.getElementById(id);
const stringValue = (value, fallback = "") => (typeof value === "string" ? value : value == null ? fallback : String(value));
function arrayValue(value) {if (Array.isArray(value)) return value;
if (!value || typeof value !== "object") return [];
return Object.entries(value).map(([id, entry]) =>
entry && typeof entry === "object" ? { id, ...entry } : { id, status: entry });}
function compactKey(value) {return String(value ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");}
function timeValue(value) {const milliseconds = Date.parse(value ?? "");
return Number.isFinite(milliseconds) ? milliseconds : null;}
function firstTime(object, keys) {for (const key of keys) {if (timeValue(object?.[key]) !== null) return object[key];}
return null;}
export function normalizeRange(value) {return VALID_RANGES.has(value) ? value : "90d";}
export function resolveTimeZone() {return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";}
export function formatLocalDate(iso, timeZone = resolveTimeZone(), locale = "en-US") {if (timeValue(iso) === null) return "n/a";
try {return new Intl.DateTimeFormat(locale, {timeZone,year: "numeric",month: "short",day: "numeric",hour: "numeric",minute: "2-digit",timeZoneName: "short"}).format(new Date(iso));} catch {return new Intl.DateTimeFormat(locale, {timeZone: "UTC",year: "numeric",month: "short",day: "numeric",hour: "numeric",minute: "2-digit",timeZoneName: "short"}).format(new Date(iso));}}
export function formatDuration(milliseconds) {if (!Number.isFinite(milliseconds) || milliseconds < 0) return "n/a";
const minutes = Math.max(1, Math.round(milliseconds / 60_000));
if (minutes < 60) return `${minutes}m`;
const hours = Math.floor(minutes / 60);
const remainder = minutes % 60;
if (hours < 24) return `${hours}h${remainder ? ` ${remainder}m` : ""}`;
const days = Math.floor(hours / 24);
const remainingHours = hours % 24;
return `${days}d${remainingHours ? ` ${remainingHours}h` : ""}`;}
export function normalizeStatus(value) {const key = compactKey(value);
if (["none", "ok", "available", "operational"].includes(key)) return "operational";
if (["maintenance", "undermaintenance"].includes(key)) return "under_maintenance";
if (["minor", "degraded", "degradedperformance"].includes(key)) return "degraded_performance";
if (["major", "partialoutage"].includes(key)) return "partial_outage";
if (["critical", "outage", "majoroutage"].includes(key)) return "major_outage";
return "unknown";}
function surfaceSource(payload) {return (payload?.current?.surfaces ??
payload?.surfaces ??
payload?.current?.components ??
payload?.components ??
[]);}
export function normalizeSurfaces(payload) {const candidates = arrayValue(surfaceSource(payload));
return SURFACE_DEFINITIONS.map((definition) => {const match = candidates.find((candidate) => {const key = compactKey(candidate?.id ?? candidate?.key ?? candidate?.slug ?? candidate?.name);
return definition.aliases.some((alias) => key === alias || key.includes(alias));});
return {id: definition.id,label: definition.label,status: normalizeStatus(typeof match === "string"? match: match?.status ?? match?.state ?? match?.indicator),updated_at: firstTime(match, ["updated_at", "checked_at", "changed_at"])};});}
function incidentSourceUrl(incident) {const direct = safeHttpsUrl(incident?.source_url ?? incident?.url ?? incident?.shortlink);
if (direct) return direct;
const id = String(incident?.id ?? "");
return /^[A-Za-z0-9_-]+$/.test(id)? `https://status.openai.com/incidents/${encodeURIComponent(id)}`: "";}
export function normalizeIncidents(payload) {return arrayValue(payload?.incidents).map((incident, index) => ({id: String(incident?.id ?? `incident-${index}`),name: String(incident?.name ?? incident?.title ?? t("status.defaultIncident")),localized_name: stringValue(incident?.localized_name),description: stringValue(incident?.description ?? incident?.body),localized_description: stringValue(incident?.localized_description),translations: incident?.translations,translation_status: stringValue(incident?.translation_status),impact: String(incident?.impact ?? "unknown"),status: String(incident?.status ?? "unknown"),started_at: firstTime(incident, ["started_at", "created_at", "began_at"]),resolved_at: firstTime(incident, ["resolved_at", "ended_at"]),updated_at: firstTime(incident, ["updated_at", "resolved_at", "created_at"]),surfaces: arrayValue(incident?.surfaces ?? incident?.affected_surfaces ?? incident?.components).map((surface) => String(surface?.label ?? surface?.name ?? surface?.id ?? surface)).filter(Boolean),source_url: incidentSourceUrl(incident)}));}
function compensationEntries(payload) {const direct = [...arrayValue(payload?.reset_links),...arrayValue(payload?.compensation_links),...arrayValue(payload?.compensations),...arrayValue(payload?.resets)];
for (const incident of arrayValue(payload?.incidents)) {for (const link of [...arrayValue(incident?.reset_links),...arrayValue(incident?.compensations)]) {direct.push({ incident_id: incident.id, ...link });}}
return direct;}
export function normalizeCompensations(payload) {const seen = new Set();
return compensationEntries(payload).map((entry, index) => {const reset = entry?.reset && typeof entry.reset === "object" ? entry.reset : entry;
const announcedAt = firstTime(reset, ["announced_at", "reset_at", "at", "created_at"]);
const incidentId = String(entry?.incident_id ?? reset?.incident_id ?? "");
const sourceUrl = safeHttpsUrl(reset?.source_url ?? reset?.url ?? entry?.source_url ?? entry?.url);
const id = String(reset?.id ?? entry?.id ?? `${incidentId}:${announcedAt ?? index}`);
return {id,incident_id: incidentId,evidence: String(entry?.evidence ?? reset?.evidence ?? "none").toLowerCase(),announced_at: announcedAt,kind: String(reset?.reset_kind ?? reset?.kind ?? "reset"),summary: String(reset?.summary ?? entry?.summary ?? t("status.defaultResetSummary")),localized_summary: stringValue(reset?.localized_summary ?? entry?.localized_summary),translations: reset?.translations ?? entry?.translations,translation_status: stringValue(reset?.translation_status ?? entry?.translation_status),audience: arrayValue(reset?.audience ?? entry?.audience).map((item) => String(item?.label ?? item?.name ?? item)).filter(Boolean),source_url: sourceUrl};}).filter((entry) => {if (seen.has(entry.id)) return false;
seen.add(entry.id);
return true;});}
function rangeCutoff(range, now) {return now - Number.parseInt(normalizeRange(range), 10) * 86_400_000;}
export function filterStatusHistory(payload, range, now = Date.now()) {const cutoff = rangeCutoff(range, now);
const incidents = normalizeIncidents(payload).filter((incident) => {const startedAt = timeValue(incident.started_at);
if (startedAt === null) return false;
return startedAt >= cutoff || (timeValue(incident.resolved_at) === null && startedAt <= now);});
const compensations = normalizeCompensations(payload).filter((reset) => {const announcedAt = timeValue(reset.announced_at);
return announcedAt !== null && announcedAt >= cutoff && announcedAt <= now;});
return {range: normalizeRange(range),surfaces: normalizeSurfaces(payload),incidents,compensations};}
export function selectLatestExplicitCompensation(compensations) {return [...compensations].filter((entry) => entry.evidence === "explicit" && timeValue(entry.announced_at) !== null).sort((left, right) => timeValue(right.announced_at) - timeValue(left.announced_at))[0] ?? null;}
export function currentStatus(payload, surfaces = normalizeSurfaces(payload)) {const knownSurfaces = surfaces.filter((surface) => surface.status !== "unknown");
if (knownSurfaces.length) {return knownSurfaces.reduce((worst, surface) => {const candidate = STATUS_META[surface.status] ?? STATUS_META.unknown;
const current = STATUS_META[worst] ?? STATUS_META.unknown;
return candidate.rank > current.rank ? surface.status : worst;}, "operational");}
const direct = normalizeStatus(payload?.current?.status ??
payload?.current?.indicator ??
payload?.status ??
payload?.indicator);
if (direct !== "unknown") return direct;
return "unknown";}
export function buildTimelineItems(incidents, compensations) {const incidentItems = incidents.map((incident) => ({id: incident.id,track: "incident",at: incident.started_at,title: incident.name,localized_title: incident.localized_name,translations: incident.translations,translation_status: incident.translation_status,status: incident.status,incident_id: incident.id,source_url: incident.source_url})).sort((left, right) => timeValue(right.at) - timeValue(left.at));
const resetItems = compensations.map((reset) => ({id: reset.id,track: "reset",at: reset.announced_at,title: reset.summary,localized_title: reset.localized_summary,translations: reset.translations,translation_status: reset.translation_status,status: reset.evidence,incident_id: reset.incident_id,source_url: reset.source_url})).sort((left, right) => timeValue(right.at) - timeValue(left.at));
return { incidents: incidentItems, resets: resetItems };}
function median(values) {if (!values.length) return null;
const sorted = [...values].sort((left, right) => left - right);
const middle = Math.floor(sorted.length / 2);
return sorted.length % 2? sorted[middle]: (sorted[middle - 1] + sorted[middle]) / 2;}
export function deriveStatusStats(incidents, compensations) {const durations = incidents.map((incident) => {const start = timeValue(incident.started_at);
const end = timeValue(incident.resolved_at);
return start !== null && end !== null && end >= start ? end - start : null;}).filter((duration) => duration !== null);
return {incidents: incidents.length,affected_ms: durations.reduce((total, duration) => total + duration, 0),median_recovery_ms: median(durations),confirmed_resets: compensations.filter((entry) => entry.evidence === "explicit").length};}
export function sourceLinkHtml(url, label, accessibleLabel = label, opensNewTab = "opens in a new tab") {const safeUrl = safeHttpsUrl(url);
if (!safeUrl) return "";
return `<a class="icon-link status-source-link" href="${escapeHtml(safeUrl)}" rel="noopener" target="_blank" aria-label="${escapeHtml(accessibleLabel)} (${escapeHtml(opensNewTab)})">${escapeHtml(label)}</a>`;}
export function formatFreshness(payload, timeZone, now = Date.now(), error = false) {const checkedAt =
firstTime(payload?.current, ["checked_at", "updated_at"]) ??
firstTime(payload, ["checked_at", "updated_at"]);
const age = checkedAt ? relativeTime(checkedAt, now, getClientLangTag()) : "";
return formatFreshnessCopy({age,stale: Boolean(payload?.stale || payload?.current?.stale),error,timeZone});}
function statusMeta(status) {const meta = STATUS_META[status] ?? STATUS_META.unknown;
return { ...meta, label: t(STATUS_LABEL_KEYS[status] ?? STATUS_LABEL_KEYS.unknown) };}
function renderBanner(payload, view, timeZone) {const host = el("status-banner");
if (!host) return;
const status = currentStatus(payload, view.surfaces);
const meta = statusMeta(status);
const activeIncident = view.incidents.find((incident) => incident.status !== "resolved");
const activeIncidentName = localizedField(activeIncident ?? {}, "name", getClientLocale());
const stale = Boolean(payload?.stale || payload?.current?.stale);
const state = status === "major_outage" || status === "partial_outage"? "outage": status === "degraded_performance" || status === "under_maintenance"? "degraded": status;
const renderKey = [status, stale, activeIncident?.id ?? "", timeZone].join("|");
if (host.dataset.renderKey === renderKey) return;
host.dataset.renderKey = renderKey;
host.dataset.state = stale ? "stale" : state;
host.innerHTML = `
    <span class="status-orb" aria-hidden="true"></span>
    <span>
      <b>${escapeHtml(status === "operational" ? t("status.bannerOperational") : t("status.bannerPrefix", { status: meta.label.toUpperCase() }))}</b>
      ${activeIncident ? `<small class="status-incident-name">${escapeHtml(activeIncidentName.value)}</small>` : ""}
      <small id="status-freshness">${escapeHtml(`${t("common.localTime")} · ${timeZone}`)}</small>
    </span>
    ${sourceLinkHtml("https://status.openai.com", t("status.officialSource"), t("status.officialSourceAria"), t("common.opensNewTab"))}`;
if (activeIncident) {appendTranslationMeta(host.querySelector("span:last-of-type") ?? host, activeIncidentName, {sourceUrl: "https://status.openai.com",sourceLabel: t("translation.originalSource")});}}
function renderComponents(surfaces) {const host = el("status-components");
if (!host) return;
host.innerHTML = surfaces.map((surface) => {const meta = statusMeta(surface.status);
return `
        <li class="surface-row" data-surface="${escapeHtml(surface.id)}" data-state="${escapeHtml(surface.status)}">
          <span class="surface-icon"><span class="ui-icon" data-icon="${surface.id === "api" ? "activity" : "server"}" aria-hidden="true"></span></span>
          <span class="surface-name">${escapeHtml(t(`status.surfaceLabels.${surface.id}`))}</span>
          <span class="surface-state"><span class="state-dot" aria-hidden="true"></span>${escapeHtml(meta.label)}</span>
        </li>`;}).join("");}
function renderCompensation(view, timeZone) {const host = el("status-compensation");
if (!host) return;
const compensation = selectLatestExplicitCompensation(view.compensations);
if (!compensation) {host.innerHTML = `
      <div class="empty-state">
        <h2>${escapeHtml(t("status.emptyCompTitle"))}</h2>
        <p>${escapeHtml(t("status.emptyCompBody"))}</p>
      </div>`;
return;}
const incident = view.incidents.find((entry) => entry.id === compensation.incident_id);
const incidentName = localizedField(incident ?? {}, "name", getClientLocale());
const summaryInfo = localizedField(compensation, "summary", getClientLocale());
const resolvedAt = timeValue(incident?.resolved_at);
const announcedAt = timeValue(compensation.announced_at);
const lag =
resolvedAt !== null && announcedAt !== null && announcedAt >= resolvedAt? formatDuration(announcedAt - resolvedAt): null;
const kindKey = String(compensation.kind ?? "reset").toLowerCase().replace(/[^a-z0-9]+/g, "_");
const kindPath = `status.compKinds.${kindKey}`;
const localizedKind = t(kindPath) === kindPath ? compensation.kind : t(kindPath);
const localizedAudience = compensation.audience.map((audience) => {const key = String(audience).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
const path = `status.audienceLabels.${key}`;
return t(path) === path ? audience : t(path);});
const incidentLink = incident? sourceLinkHtml(incident.source_url,t("status.openIncidentLabel"),t("status.openIncidentAria", { title: incidentName.value }),t("common.opensNewTab")): "";
const resetLink = sourceLinkHtml(compensation.source_url,t("status.openResetLabel"),t("status.openResetAria"),t("common.opensNewTab"));
host.innerHTML = `
    <article class="comp-card" data-evidence="explicit">
      <div class="comp-head">
        <span class="evidence-badge"><span class="ui-icon" data-icon="shield" aria-hidden="true"></span> ${escapeHtml(t("status.confirmedLink"))}</span>
        <span class="comp-kind">${escapeHtml(t("status.compKind", { kind: localizedKind }))}</span>
      </div>
      <h2>${escapeHtml(t("status.compHeading"))}</h2>
      <p class="comp-summary">${escapeHtml(summaryInfo.value)}</p>
      <div class="comp-sequence" aria-label="${escapeHtml(t("status.sequenceAria"))}">
        <div>
          <span class="sequence-icon"><span class="ui-icon" data-icon="activity" aria-hidden="true"></span></span>
          <span><b>${escapeHtml(t("status.sequenceIncident"))}</b><time datetime="${escapeHtml(incident?.started_at ?? "")}">${escapeHtml(formatLocalDate(incident?.started_at, timeZone, getClientLangTag()))}</time></span>
          <strong>${incident?.resolved_at ? escapeHtml(formatDuration(timeValue(incident.resolved_at) - timeValue(incident.started_at))) : escapeHtml(t("status.ongoing"))}</strong>
        </div>
        <span class="sequence-line" aria-hidden="true"></span>
        <div>
          <span class="sequence-icon"><span class="ui-icon" data-icon="refresh" aria-hidden="true"></span></span>
          <span><b>${escapeHtml(t("status.sequenceReset"))}</b><time datetime="${escapeHtml(compensation.announced_at)}">${escapeHtml(formatLocalDate(compensation.announced_at, timeZone, getClientLangTag()))}</time></span>
          <strong>${lag ? escapeHtml(t("status.afterRecovery", { lag })) : escapeHtml(t("status.linkedBySource"))}</strong>
        </div>
      </div>
      <p class="comp-audience"><span class="ui-icon" data-icon="users" aria-hidden="true"></span>${escapeHtml(localizedAudience.join(" + ") || t("status.defaultAudience"))}</p>
      <div class="source-actions">${incidentLink}${resetLink}</div>
    </article>`;
const card = host.querySelector(".comp-card");
appendTranslationMeta(card, summaryInfo, {sourceUrl: compensation.source_url,sourceLabel: t("translation.originalOnX")});
appendTranslationMeta(card, incidentName, {sourceUrl: incident?.source_url,sourceLabel: t("translation.originalSource")});}
function statHtml(label, value, hint) {return `
    <div class="stat">
      <span class="stat-label">${escapeHtml(label)}</span>
      <strong class="stat-value">${escapeHtml(value)}</strong>
      <span class="stat-hint">${escapeHtml(hint)}</span>
    </div>`;}
function renderStats(view) {const host = el("status-stats");
if (!host) return;
const stats = deriveStatusStats(view.incidents, view.compensations);
host.innerHTML = [statHtml(t("status.statsIncidents"), stats.incidents, t("status.statsSourceRecords", { range: view.range })),statHtml(t("status.statsAffected"), formatDuration(stats.affected_ms), t("status.statsResolvedTime")),statHtml(t("status.statsMedianRecovery"),stats.median_recovery_ms === null ? t("common.unavailable") : formatDuration(stats.median_recovery_ms),t("status.statsPublicInterval")),statHtml(t("status.statsConfirmedResets"), stats.confirmed_resets, t("status.statsExplicitLinks"))].join("");}
function timelineSource(item, title) {const kind = item.track === "incident" ? t("status.trackIncidents") : t("status.trackResets");
return sourceLinkHtml(item.source_url,t("status.sourceLabel"),t("status.sourceAria", { kind, title }),t("common.opensNewTab"));}
function timelineTrackHtml(label, items, timeZone) {const rows = items.length? items.map((item) => {const title = localizedField(item, "title", getClientLocale());
const stateKey = String(item.status ?? "none").toLowerCase().replace(/[^a-z0-9]+/g, "_");
const statePath = `status.timelineState.${stateKey}`;
const stateLabel = t(statePath) === statePath ? item.status : t(statePath);
return `
            <li class="status-timeline-item" data-incident-id="${escapeHtml(item.incident_id)}">
              <time datetime="${escapeHtml(item.at)}">${escapeHtml(formatLocalDate(item.at, timeZone, getClientLangTag()))}</time>
              <span class="chip" data-kind="${item.track === "reset" ? "signal" : "codex"}">${escapeHtml(stateLabel)}</span>
              <strong>${escapeHtml(title.value)}</strong>
              ${timelineSource(item, title.value)}
            </li>`;}).join(""): `<li class="mini-empty">${escapeHtml(t("status.trackEmpty"))}</li>`;
return `
    <section class="status-track" data-track="${escapeHtml(label.toLowerCase())}" aria-label="${escapeHtml(t("status.trackAria", { label }))}">
      <h3>${escapeHtml(label)}</h3>
      <ol>${rows}</ol>
    </section>`;}
function renderTimeline(view, timeZone) {const host = el("status-timeline");
if (!host) return;
const items = buildTimelineItems(view.incidents, view.compensations);
host.innerHTML = `
    <div class="status-dual-track">
      ${timelineTrackHtml(t("status.trackIncidents"), items.incidents, timeZone)}
      ${timelineTrackHtml(t("status.trackResets"), items.resets, timeZone)}
    </div>`;}
function renderFreshness(payload, timeZone, now, error = false) {const host = el("status-freshness");
if (!host) return;
host.dataset.state = error ? "error" : payload?.stale || payload?.current?.stale ? "stale" : "fresh";
const next = formatFreshness(payload, timeZone, now, error);
if (host.textContent !== next) host.textContent = next;}
function syncRangeControl(range) {const host = el("status-range");
if (!host) return;
if ("value" in host) host.value = range;
for (const button of host.querySelectorAll("[data-range]")) {button.setAttribute("aria-pressed", String(button.dataset.range === range));}}
function renderPayload(payload, range, now, error = false) {const timeZone = resolveTimeZone();
const view = filterStatusHistory(payload, range, now);
renderBanner(payload, view, timeZone);
renderComponents(view.surfaces);
renderCompensation(view, timeZone);
renderStats(view);
renderTimeline(view, timeZone);
renderFreshness(payload, timeZone, now, error);
const zone = el("status-timezone");
if (zone) zone.textContent = `${t("common.localTime")} · ${timeZone}`;}
function renderError(timeZone) {const banner = el("status-banner");
if (banner) {banner.dataset.state = "error";
banner.innerHTML = `
      <p class="signal-kicker">${escapeHtml(t("status.errorKicker"))}</p>
      <h2>${escapeHtml(t("status.errorTitle"))}</h2>
      <p>${escapeHtml(t("status.errorBody"))}</p>`;}
for (const id of ["status-components", "status-compensation", "status-stats", "status-timeline"]) {const host = el(id);
if (host) host.innerHTML = `<p class="mini-empty">${escapeHtml(t("status.errorRetry"))}</p>`;}
renderFreshness({}, timeZone, Date.now(), true);}
function rangeFromUrl() {return normalizeRange(new URLSearchParams(location.search).get("range"));}
function writeRangeToUrl(range) {const url = new URL(location.href);
url.searchParams.set("range", range);
history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);}
export function createStatusPage({ announce, fetchImpl = fetch, now = () => Date.now() } = {}) {let range = rangeFromUrl();
let sequence = 0;
let lastPayload = null;
let lastLoadFailed = false;
const rangeHost = el("status-range");
syncRangeControl(range);
const load = async ({ quiet = false } = {}) => {const request = ++sequence;
try {const payload = await fetchJsonWithLocale(`/api/status-history?range=${range}`, {fetchImpl});
if (request !== sequence) return;
lastPayload = payload;
renderPayload(payload, range, now());
if (!quiet || lastLoadFailed) announce?.(t("status.announceLoaded", { range }));
lastLoadFailed = false;} catch {if (request !== sequence) return;
if (lastPayload) {renderPayload({ ...lastPayload, stale: true }, range, now(), true);} else {renderError(resolveTimeZone());}
if (!quiet || !lastLoadFailed) announce?.(t("status.announceFailed"));
lastLoadFailed = true;}};
const setRange = (value) => {const next = normalizeRange(value);
if (next === range) return;
range = next;
writeRangeToUrl(range);
syncRangeControl(range);
load();};
rangeHost?.addEventListener("click", (event) => {const button = event.target.closest?.("[data-range]");
if (button) setRange(button.dataset.range);});
rangeHost?.addEventListener("change", (event) => {setRange(event.target.value);});
window.addEventListener("popstate", () => {const next = rangeFromUrl();
if (next === range) return;
range = next;
syncRangeControl(range);
load();});
setInterval(() => {if (!document.hidden) load({ quiet: true });}, REFRESH_MS);
load();}
