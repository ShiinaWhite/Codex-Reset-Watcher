# Host 1.2.2 compatibility correction

2026-10-10 UTC. Base candidate: `65673ccc38d992c882ea700bac2df29439c60643`.
This follows the independent review of the failed production upgrade. It is a
compatibility correction on dev, not another deployment or a production soak.
Stage 1 business code remains approved separately; production remains on
`fa09454985ee4dde04e01f2acc7b6340166c2abf`, main on
`90174ae7d59ab75d7a6fe6e6c5f826063ac7ccd6`. Stage 2/3 are outside this change.

## Corrected source evidence

Official remote tags and their raw Git blobs were checked independently:

| Host | Immutable commit | SDK in uv.lock |
| --- | --- | --- |
| 1.2.0 | `7ade2a8a967d6ca93b62538f753ed5110b65dc65` | 2.7.1 |
| 1.2.1 | `8b937f2d284c86a4e9148d4e77026c05180ce564` | 2.8.0 |
| 1.2.2 | `b9c00e648c7e493bbb071d5f0349411bee6bc98e` | 2.8.0 |
| 1.2.3 | `762348103fd086471f72ed2997b730f7b865b674` | 2.8.0 |
| 1.2.4 | `21cd74d81d47b6f77ba5ab7116a88916e957d15b` | 2.8.0 |

The prior assertion that 1.2.4 was the first Host locking SDK 2.8.0 was wrong.
The first is 1.2.1. This does **not** establish plugin support for 1.2.1: the
new tested lower bound is 1.2.2. SDK floor 2.8.0 and both upper bounds stay unchanged.
Primary sources: [1.2.1 lockfile](https://github.com/Mai-with-u/MaiBot/blob/8b937f2d284c86a4e9148d4e77026c05180ce564/uv.lock),
[1.2.2 lockfile](https://github.com/Mai-with-u/MaiBot/blob/b9c00e648c7e493bbb071d5f0349411bee6bc98e/uv.lock).

Actual production reports Host 1.2.2, installed SDK 2.8.0 and Playwright 1.58.0.
Thirteen relevant production files match official 1.2.2 **and** 1.2.4 byte for byte:
capabilities `core/data/registry/render`; Host `capability_service/authorization`;
runner `manifest_validator/plugin_loader`; protocol `codec/envelope`; services
`html_render_service/service_task_resolver`; `llm_service_data_models`.
All 66 installed SDK Python source files match the official 2.8.0 wheel, whose
SHA256 is `b4ef723fb00bac6ce34a14a58d847690072ce690a7df9be44f35defb9cb8b35d`.
This is a targeted runtime fingerprint, not a claim that the whole customized
Host installation equals the official tree.

## Exact production Host, isolated contracts

The existing production container was used for a separate Python process with
a temporary plugin root under `/tmp`, isolated config/logging and state copies.
No Bot startup, Core/NapCat reload or external model/QQ invocation occurred.
The full immutable candidate runtime was verified before copying to that root.

The actual Host validator rejects the original manifest with exactly one error:
Host 1.2.2 below min 1.2.4. Changing only the temporary copy's minimum to 1.2.2
passes. The actual package loader then loads `codex-reset.watcher`, including
`notice_card.py` and `tibo_discovery.py`, with zero failed plugins.

Actual SDK `PluginContext` → cap.call → MsgPack codec/Envelope → Host authorization/
capability service → actual registered implementations passed all six contracts:

- `chat.get_stream_by_group_id`, `chat.open_session`: real serialization and SDK
  normalization; external chat manager replaced with an isolated backend.
- `llm.generate`: actual task resolver and request constructor produce
  `task_name=replyer`, `model_name=None`; distinct concrete-model negative control
  is checked. `timeout_ms` stays in RPC transport kwargs, absent from LLM args.
  Final model call is a recorder.
- `send.text`, `send.image`: `return_details=True`, platform message ID, successful
  normalization, None-return failure and exception failure are checked. Final send
  backend is a recorder, not QQ.
- `render.html2png`: candidate's actual NoticeCard pipeline uses `#capture`,
  1240×900 viewport, scale 2, `allow_network=False`, `render_timeout_ms=15000`.
  Actual managed production Chromium produces a 70,057-byte PNG; image-only success
  and render-disabled text fallback pass. Visual inspection found no tofu in the
  exercised Chinese/English sample, including 镕、喆、囧、龘.

The portable `tools/check_host_render.py` now copies `tibo_discovery.py` alongside
its existing runtime files. Its separate exact-Host warm render and eight
image/text/failure cases also pass; it deliberately tests missing Playwright,
browser, capability, authorization and failed-download fallback in isolation.
No browser installation or system-library change is needed for these warm tests.
These results prove runtime contracts with isolated external backends. They do
not prove real QQ delivery, external LLM success or production polling/soak.

## Loaded Watcher and production state copy

The actual loaded Watcher's `on_load`, one `_check_once()`, delivery-task drain
and `on_unload` ran with copies of production config/state and real read-only
public discovery/enrichment. Only local render/recorder sends were permitted.

Observed Fx discovery: **partial, 3 GETs, 7,606 ms, 20 candidates**; cursor responses
with invalid code/schema remained provider issues, not EOF. Combined discovery:
**partial, fallback_hints=True, 23 candidates**. Both formal groups retained their
completed baselines and every original receipt. Inflight/repair tasks released.
Each group produced seven **simulated** image deliveries, fourteen recorder calls
total, **zero real QQ sends**:

`2108275041276420573`, `2108349826727588000`, `2108646052178092403`,
`2108650026671231468`, `2108773703064657936`, `2108777962053292398`,
`2108840070648397933`.

Only the first two of those are still-window members of the original twelve
missing golden tweets at this observation time. Original golden
`2107912709715132482` is already >48h and absent from this partial union;
`2108084615349170480` is discovered but >48h and follows the existing age guard.
The older eight golden IDs are also expired. The original four fresh IDs in
[STAGE1_IMPLEMENTATION.md](STAGE1_IMPLEMENTATION.md) use its frozen 10-09 clock,
not today's window. All twelve frozen-clock outcomes remain in regression tests.
Original delivered Tweet IDs discovered across providers did not resend.
The source copy bytes remain unchanged; receipt changes exist only in the dry-run
working copy. Production state was never supplied to this isolated plugin.

Production config SHA256:
`1fbb3f8ecdbf413ff4fcef000c6c5789a4f2eb5ee66bd44c72a301fe794d55b9`.
State snapshot SHA256:
`60f9804c9365390a31713b2d4eb635cc9d501a573246cb144ed9717f1b0c9b6a`.
Before/after read-only fingerprints cover live runtime, config/state and both
container IDs/start times. No live runtime, config or state write is part of this change.

## Change and repeatable acceptance

The **only runtime change** is `_manifest.json` Host minimum 1.2.4 → 1.2.2.
`plugin.py`, `notice_card.py`, `tibo_discovery.py`, all templates/assets and LICENSE
remain exact base-candidate bytes. No qualification, discovery, ordering, receipt,
retry, age guard, migration, schema or defaults are changed.

`test_llm_contract.py` adds the pinned Host 1.2.2 routing row;
`test_host_loader.py` executes that version's actual validator/loader and exercises
original rejection, acceptance, SDK/Host lower-bound rejection, and complete
package loading. Version discovery/logging are isolated; SDK and Host logic are
real. Pinned blob hashes live in `tests/fixtures/llm-host-origins.json`.
The LLM harness enables postponed annotations to extract the upstream request
class on Python 3.13 as well as 3.14. No upstream code is committed here.

```bash
CRW_HOST_GIT_ROOT=/path/to/official-MaiBot-clone \
CRW_EXPECT_SDK=2.8.0 python -m pytest -q test_llm_contract.py test_host_loader.py
python tools/check_host_render.py --host-root /path/to/actual-host \
  --host-version 1.2.2 --plugin-root /path/to/candidate \
  --output /path/to/new-isolated-output --browser-executable /path/to/chromium
```

Acceptance on this change:

- Full pytest: **519 passed, 1 skipped**; Stage 1 discovery/ordering/Tibo-render
  combined: **195 passed**; Chromium: **6 passed**.
- Actual SDK 2.8.0/2.8.2/2.10.0 with all four pinned Host routing rows plus loader
  tests: each **14 passed, 1 skipped**; SDK 2.8.1: **15 passed**.
  The skip is exclusively the SDK 2.8.1 old-wrapper negative control.
- Ruff check/format, diff whitespace and exact committed-blob `verify_release` are
  required before the new immutable SHA is handed to review; final manifests/logs
  are stored outside Git alongside the contract evidence.

Local private evidence:
`/home/dev/acceptance-evidence/codex-reset.watcher/20261010-host122-compat/`.
Server private evidence:
`/srv/maibot/deployment-evidence/codex-reset.watcher/20261010T153430Z-host122-compat/`.
Raw production config/state, SDK wheels, upstream code and logs stay outside Git.

## Required gate for any later deployment

Before **any** live replacement, a separately authorized deployment must complete:

1. Immutable candidate and remote dev/main identity; actual live Host/SDK/runtime
   fingerprint and the formal groups/config/state identities.
2. Actual Host manifest validator against the unmodified immutable artifact.
   Any error stops before staging or live writes.
3. Exact-host isolated complete loader and six-capability smoke; distinguish fake
   external backend evidence from live operation.
4. Complete runtime manifest/bytes verification and recoverable runtime/config/
   state backup. Stage the whole runtime outside the watched plugin path. Use a
   controlled replacement that prevents FileWatcher observing a half-updated tree,
   followed by only the authorized single reload.
5. Await real command completion and collect exit/log/hash evidence. A `running`
   result, interruption or absent completion remains **pending**, never passed;
   long waits require a durable background command/log/completion marker and handoff.
6. Preserve original config/state; verify startup and unchanged baselines/receipts,
   then observe natural discovery/delivery and soak. Keep MAIN-READY false until
   the separately reviewed production acceptance has actually completed.

This commit must receive compatibility review before seeking a new production
upgrade authorization. The prior authorization named `65673cc...`; it does not
silently transfer to a new candidate SHA.
