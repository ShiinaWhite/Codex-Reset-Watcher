# Stage 1 implementation and acceptance evidence

2026-10-09, UTC+8. Scope: independent Tibo discovery only. This document supplements
[DECOUPLING_PLAN.md](DECOUPLING_PLAN.md). Dev implementation is ready for review;
production remains on `fa09454985ee4dde04e01f2acc7b6340166c2abf`.

## Identity and scope

Before implementation, remote dev was `4229dec8f864ca3e472919ca8f9acb011d83dbfb`,
main was `90174ae7d59ab75d7a6fe6e6c5f826063ac7ccd6`; both matched the requested
baselines. Production is an installed tree, not a Git checkout: read-only SSH
hashes of plugin.py, notice_card.py and _manifest.json matched fa094549 Git blobs
(the hashes are in the design report). No production code/config/state write,
QQ resend, Core modification, main merge, tag, Release or Marketplace action.
Stage 2/3 are not implemented.

## Implemented boundary

`tibo_discovery.py` owns transport/schema/provenance normalization:

- `TweetCandidate`: Tweet ID, parsed UTC publication time, discovery provenance,
  original text, normalized `TweetMetadata`, optional raw enrichment hint.
- `TweetMetadata`: is_reply, replying_to handle, parent Tweet ID, is_repost,
  verification status (`verified`, `unknown`, `conflict`). Explicit null relations
  establish non-reply/non-repost only when the required fields are present.
- `DiscoveryBatch`: candidates, ok/partial/unavailable, issues, GET count,
  elapsed milliseconds and Codex hint use. Provenance never becomes a receipt key.
- Fx v2 requires `type=status`, author handle `thsottiaux` and pinned author ID
  `1953337039510003712`. Other-author thread context is filtered. Malformed same-
  author rows invalidate completeness; HTTP 200 with bad code/schema is failure.
- Fresh Codex timeline `tweets[]` and `radar_context[]` are untrusted relation
  hints. Missing/stale/provenance-invalid feed contributes no candidates. The
  hint adapter always has partial coverage, even when its arrays are nonempty.
- Tweet ID union removes cross-page/cross-provider duplicates. Verified Fx
  metadata wins over Codex hints. Conflicting verified relations or times require
  re-verification. Candidates are sorted by parsed instants, not raw strings or
  provider order. Parent timestamps do not terminate a page early.

Core `_process_tibo_posts(batch, groups)` consumes normalized objects, with no
feed collection knowledge. It calls the existing `is_tibo_main_post` after
normalization. Reliable reply-to-other, repost and URL-only exclusions are
filtered before creating chronological participants. Relation-unknown candidates
may participate while bounded verification runs; exclusion, unresolved relation,
exception, timeout or cancellation releases the existing per-group barrier and
writes no seen/receipt. Unknown expired candidates also write no seen.

There is no unnecessary new combined enrichment class: the normalized candidate
and verified relation travel alongside existing `TweetContent` (body/source/
completeness/quote/poll) into the existing translation/card/delivery pipeline.
The adapter owns structural relation decisions; existing enrichment owns full
body/quote/poll repair; presentation owns translations and cards; existing state
and delivery code own positive receipts and retry. Qualification/state methods
were compared as ASTs against the dev baseline: only `_check_once`,
`_process_tibo_posts`, `_tibo_pipeline`, `_enrich_content` changed. In particular
Reset/Banked eligibility, full-push eligibility, old-lane coverage, receipt and
ordering methods remain unchanged. Old lanes retain their existing text fallback
and stale quote guard.

## Request budget and observed latency

Ordinary statuses and with-replies statuses start concurrently. `count=20`, at
most **4 pages per timeline, 8 discovery GETs per poll**, within one **12 second
wall-time budget**. The design's example three-page bound becomes four pages to
allow a full older page to establish the 72h lookback. There is no durable cursor:
next poll replays the head and positive Tweet receipts suppress duplicates.
An entire authored page older than 72h or explicit null bottom cursor ends that
surface; a 404 cursor, empty/repeated cursor, invalid schema or page cap gives
partial/unavailable, never EOF. Returned good pages remain usable after failure.
The discovery window is 72h; delivery eligibility still uses the existing 48h guard.

Unknown relation verification adds at most **2 single-post GETs per candidate**,
Fx then Vx, within **12 seconds total**. Fx verifies handle/author ID/ID/time.
Vx does not expose author ID reliably; as the existing secondary single-post
provider it requires matching handle, canonical HTTPS Tweet URL, Tweet ID/time
and explicit reply/repost fields. It is not promoted to timeline discovery.
Subsequent full-body enrichment reuses the old budget (up to two 10s provider
calls per eligible post); this is separate from discovery and relation repair.
All calls are skipped when full-push is disabled. Existing feed/forecast/
latest-only push GETs still start concurrently with discovery. No extra blocking
retry/backoff/cache was introduced in Stage 1.

The new adapter was executed in memory through read-only SSH from the production
network, using public GETs and urllib transport. It was not installed or loaded
into the live Watcher. Two consecutive observations:

| UTC start | status | GETs started | discovery latency | unique candidates | missing golden covered |
|---|---|---:|---:|---:|---:|
| 14:09:23.542 | partial | 4 | 12,004 ms | 41 | 8/12, including all 4 fresh |
| 14:09:35.546 | partial | 6 | 12,002 ms | 61 | 12/12, including all 4 fresh |

First observation: ordinary head returned HTTP 404; two with-replies pages
completed, third was canceled at the total deadline. Second: ordinary head
succeeded but its cursor returned HTTP 404; with-replies returned three good
pages before another page was canceled. Completed GET latency was 1,858–5,428 ms.
Request counts include in-flight canceled GETs; response records include completed
requests only. urllib thread cleanup is probe-specific; production uses existing
cancellable aiohttp `_get_json`. These observations establish useful partial
coverage and the enforced adapter deadline, not a success SLA or an exhaustive X
coverage guarantee. No latency samples are represented as a complete-provider
success when they were partial.

## Failure and baseline semantics

Partial primary results are unioned with Codex hints in every poll (cross-check
when primary succeeds, fallback when it fails). Existing baseline_done groups
process verified fresh candidates and bounded relation repair normally. Fully
unavailable primary + available hints can still deliver a reliably verified post
for an existing group. If no source yields candidates, the poll produces no
Tibo state transition. Next poll retries head discovery and unknown relations.

**Partial/unavailable never establishes or rebuilds a Tibo baseline.** New groups
wait for a complete primary batch with eligible/potentially eligible candidates;
verified candidates establish the baseline, unresolved candidates are not marked
seen. Existing baseline flags are retained regardless of provider status. Neither
provider switching nor partial recovery clears seen/delivered/receipt history.
No provider-specific dedup identity or state migration exists. A Tweet discovered
by several providers remains one candidate and one `tweet:<id>` coverage identity.

## Golden outcomes

Raw public HTTP response bytes captured 2026-10-09 are in
`tests/fixtures/tibo-discovery/`, with exact URL/UTC capture time/SHA256 in
`ORIGINS.json`. Production state, config, logs and receipt snapshots are excluded.
The observation clock is frozen at **2026-10-09 13:06 UTC** for reproducible age
classification; actual future operation must recalculate age and read live receipts.

| Tweet ID | Publication UTC | Relation | Core result at frozen clock |
|---|---|---|---|
| 2107504197012988007 | 10-06 16:12:10 | self-reply | eligible; >48h, age guard, no send |
| 2107548725359104441 | 10-06 19:09:07 | quote/comment | eligible; >48h, age guard, no send |
| 2107573405553938664 | 10-06 20:47:11 | quote/comment | eligible; >48h, age guard, no send |
| 2107574912349303197 | 10-06 20:53:10 | quote/comment | eligible; >48h, age guard, no send |
| 2107576143285219799 | 10-06 20:58:04 | self-reply | eligible; >48h, age guard, no send |
| 2107597780352950624 | 10-06 22:24:02 | original | eligible; >48h, age guard, no send |
| 2107599996225007892 | 10-06 22:32:51 | self-reply/poll | eligible; >48h, age guard, no send |
| 2107676900894417277 | 10-07 03:38:26 | self-reply | eligible; >48h, age guard, no send |
| 2107912709715132482 | 10-07 19:15:27 | quote/comment | unseen <=48h, pending for both groups |
| 2108084615349170480 | 10-08 06:38:33 | quote/comment | unseen <=48h, pending for both groups |
| 2108275041276420573 | 10-08 19:15:14 | note + quote/comment | unseen <=48h, pending for both groups |
| 2108349826727588000 | 10-09 00:12:24 | original | unseen <=48h, pending for both groups |

The captured 72h corpus has 53 distinct Tibo IDs and 20 eligible posts. Additional
real fixtures include reply-to-other `2108428560822424062` (excluded before
attempt registration), and quote/poll `2107578625419866469`. URL-only/repost,
unknown/conflicting metadata, cross-provider/page duplicates, malformed author,
code/schema, page failure, repeated cursor, cap, outage fallback, bounded timeout
and cancellation are covered through mutated captured-schema fixtures. Existing
quote/poll/long-body and self-reply tests retain their assertions. This is the
observed corpus; authenticated X timeline completeness remains unverified as
already documented in the approved plan.

## Production state copy dry-run

Read-only snapshot at 14:04:26 UTC from the real production state path; exact
SHA256 `0c04dcb493b5e220e549b8c42773a3f3d44620f7c846c4ed3e10dd0a2203e2a5`.
The copy and simulation outputs live outside Git under:
`/home/dev/acceptance-evidence/codex-reset.watcher/20261009-stage1/` (mode 0700).
Simulation ran the actual new `_process_tibo_posts`/pipeline/delivery/receipt code
against local copies, frozen clock above and captured union batch. Network was
stubbed; send was a recorder returning failure, never a QQ transport.

- Both formal groups retained baseline_done=true and every delivered entry.
- Each group attempted exactly the four fresh missing IDs above, oldest first.
  Simulated failure left them pending, with no false seen/delivered receipt.
- The eight expired golden posts were not sent. The larger paginated corpus also
  included 14 previously unseen older eligible posts: 22 total local age-guard
  additions, not 22 recovery sends. Unknown expired relations add no seen entry.
- Reload + second poll retried only the same four missing posts. Already delivered
  IDs discovered from another provider did not resend.
- A second local-copy scenario removed only the Tibo lane's seen/delivered/alias
  for already delivered `2108040921044639779`, retaining `tweet:<id>` coverage:
  canonical receipt prevented send and restored the Tibo lane receipt normally.
- **Real send calls: 0.** Original copied bytes unchanged. Real production state
  was never passed to the plugin nor written by these simulations.

## Validation and review handoff

Local validation uses the actual project venv and the existing pinned official
Host clone outside this repository; no Core edit. Chromium uses its existing
managed executable plus the previously extracted local runtime libraries via
`LD_LIBRARY_PATH=/home/dev/.cache/crw-chromium-libs/root/usr/lib/x86_64-linux-gnu`.
The initial missing-library failure was environmental and was resolved before
acceptance; no browser tests were skipped to hide it.

- Full pytest: **468 passed, 1 skipped** (SDK 2.8.1-only negative control).
- Discovery targeted: **36 passed**; ordering targeted: **29 passed**;
  discovery + ordering + Tibo/render targeted: **151 passed**.
- Real Chromium: **6 passed**, including quote, note/long, poll and active poll.
- Actual SDK LLM contract matrix, official Host 1.2.4/1.2.5/1.3.5:
  SDK 2.8.0/2.8.2/2.10.0 each **6 passed, 1 skipped**;
  SDK 2.8.1 **7 passed**, including the negative control.
- Ruff check, format check, diff whitespace check; exact committed Git-blob
  `verify_release` manifest is required before push. Final immutable SHA and
  manifest live in the external evidence directory / review handoff.

Review the Stage 1 commit before any production change. The minimum complete
production miss repair is this independent discovery + metadata boundary; it is
not the later Reset/Banked adapter refactor. Stage 2 remains a separate review
scope; Stage 3 remains future work. No deployment or historical resend is part of
this acceptance. The four fresh golden posts have individual 48h deadlines in
DECOUPLING_PLAN.md; completing dev does not claim production recovery.
