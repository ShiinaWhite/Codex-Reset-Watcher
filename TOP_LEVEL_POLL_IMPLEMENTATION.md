# Top-level Tweet poll presentation

2026-10-11, UTC+8. Base: `2b13246ace5f61445765957c699384540985619b`.
This is an independent presentation fix on dev. No production upgrade/reload,
manual QQ send, state/config write, main merge, Stage 2/3 or release is included.
The user explicitly asked not to send this candidate to the exhausted review Chat.

## Problem and resulting behavior

Tweet `2109152716954222849` (“Pro users, would you rather have”, published
13:22:48 UTC+8) has a primary poll with three choices. Production displayed only
its body because enrichment and cards carried `quote.poll` but no current-Tweet
poll. This omission existed before Stage 1 discovery. The previously implemented
quoted-poll normalizer and renderer remain in use.

`TweetContent.poll` now carries the normalized primary poll independently of
`content.quote["poll"]`. Enrichment takes Fx `poll`, Vx `pollData` and matching,
non-stale feed / discovery hints. Provider metadata must identify the requested
Tweet; mismatched or unidentified provider records cannot attach a primary poll.
The selected body and primary/quoted poll snapshots are independent. A poll is
selected atomically by usable options, complete statistics, known status/total
and deadline; ties retain the first snapshot. Numbers/status from different
providers are never combined. The existing normalizer still owns validity and
explicitly supported arithmetic; viewer selection is never inferred.

`NoticeCard.poll` renders below the current Tweet body, not in a fabricated quote.
Image option translation uses the existing optional translator without changing
statistics; the font selector includes primary labels/translations as well as
quoted content. Quote layout/translation/rendering remain unchanged.

The shared Tweet notice formatter includes primary poll options in text mode and
image failure fallback, followed by known percentages, total, status and deadline.
Original labels require no extra LLM calls. Unknown numeric/status/deadline fields
are omitted. An explicitly present unusable poll displays the existing unavailable
message. The shared formatter is also used by Tweet-backed Reset/Banked notices;
this is display data only, with no qualification or routing changes.

## Request and translation budgets

- Text mode keeps the existing full-body early return: a complete Fx body costs
  **one GET**, whether it contains a poll or not. No poll-specific Vx probe or
  option-translation call is introduced for text/fallback.
- Image mode preserves the existing maximum of **two providers**, each using the
  existing timeout. The existing rich/quote search remains; a known incomplete
  primary poll also prevents premature rich-content return. Matching complete
  feed/discovery hints can satisfy it without another request.
- Image-only primary-poll golden with no quote retains the pre-existing two-GET
  rich search. This patch does not optimize or refactor discovery/enrichment
  transport. Image options reuse the existing per-poll bounded translation budget;
  failure leaves the original labels. Successful image delivery sends no text.

## Evidence and acceptance

The intact public Fx snapshot and observation clock are documented in
[the primary-poll fixture](tests/fixtures/primary-poll/README.md). It was captured
at 2026-10-11T06:40:49.745573Z, not at the earlier QQ send time. Vx/feed and conflict
variants are explicit synthetic schema cases, not additional live captures.

Acceptance on the final code:

- Full pytest: **541 passed, 1 skipped**.
- Primary poll targeted: **20 passed**; discovery: **80 passed**; ordering:
  **29 passed**; actual pinned Host LLM/loader contracts: **14 passed, 1 skipped**.
- Real local Chromium: **8 passed**, including both new primary-poll cases,
  actual percentages/bar geometry, active deadline, no quote, no external network,
  and supplemental Chinese glyph `龘` rendered with the embedded custom font.
- Actual SDK matrix including the primary and quoted poll suites: SDK
  **2.8.0/2.8.2/2.10.0: 79 passed, 1 skipped each**; **2.8.1: 80 passed**.
  All four pinned Host routing versions (1.2.2/1.2.4/1.2.5/1.3.5) execute.
  The sole permitted skip is the SDK-2.8.1-specific old-wrapper negative control
  on other SDKs; no Host routing or loader row is skipped.
- Ruff check/format and diff whitespace pass. Exact committed Git-blob/tree
  `verify_release` is required before push; its final SHA/manifests are private
  acceptance artifacts, not a new release/tag.

The initial local browser launch failed because its process lacked the already
cached NSPR/NSS/audio dependencies. Acceptance uses
`LD_LIBRARY_PATH=/home/dev/.cache/crw-chromium-libs/root/usr/lib/x86_64-linux-gnu`;
no browser download, system install or production-library change occurred.
The official-source tests use
`CRW_HOST_GIT_ROOT=/home/dev/acceptance-evidence/codex-reset.watcher/pre-main-fa0945/official-host-1.2.4`.

Protected-function AST comparison confirms discovery scheduling, eligibility,
reply repair, chronology/barrier, delivery, cross-lane routing, receipt/state,
LLM routing, normalizer and quote merge are unchanged. Both delivery pipelines
that format directly only add the optional poll formatter argument. Discovery,
manifest, templates and font assets remain exact base bytes. Non-poll Tweet,
system and existing quoted-poll golden HTML are byte-for-byte identical to base.

A dry-run uses the saved read-only production state snapshot SHA256
`173a46e4f1a9d7ee706a58b18c0dacc6a1246803058dba68e300d05ea72af4db`, copied into
an isolated temporary fake context. Both formal groups keep completed baselines
and every seen/delivered/canonical/Reset/Banked receipt. The already-delivered
primary poll causes **zero enrichment/render/send calls**, with unchanged working
state and source-copy bytes. No live state is loaded or modified by this harness.
Poll/no-poll pipeline tests additionally prove identical receipts, including
image success, render/send fallback and reloaded no-resend behavior.

Private logs, screenshots, AST checks, matrix and dry-run evidence:
`/home/dev/acceptance-evidence/codex-reset.watcher/20261011-top-level-poll/`.
Production state/logs remain outside Git. These are offline/local acceptance
results; the corrected presentation is **not yet deployed or live-verified**.
