# Telegram public archive: final narrow production-runtime probe

Date: 2026-09-11, 15:15–15:17 UTC. Result: BLOCKED in the current production network.

This does not claim that the public archive is absent, requires a user account,
or lacks pagination. The reviewer has independently observed the public sent
messages. This probe answers whether the existing production HTTP path can
currently retrieve the archive and validate recovery.

## Runtime equivalence

- Read-only `ssh abiotic-server` → `sudo docker exec -i maibot-core /MaiMBot/.venv/bin/python -B -`.
- Existing production container; no deployment, restart, installation or remote file writes.
- Production plugin manifest: 0.1.11.
- Production and local baseline plugin.py SHA-256 match:
  `0063be5c30e4a2931b917ccca685d39e00b905114fd77c351a2338893b9b671a`.
- Python 3.13.15; aiohttp 3.13.5; OpenSSL 3.5.6.
- Default CA file `/usr/lib/ssl/cert.pem`; directory `/usr/lib/ssl/certs`.
- CA file SHA-256: `714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5`.
- Same default `aiohttp.ClientSession(timeout=ClientTimeout(total=12))` and
  `session.get(url)` pattern as the plugin. `trust_env=False`, TLS verification enabled.
- All four observed non-probe Python processes use the same network namespace
  as the probe and the same underlying Python executable. None has upper/lowercase
  HTTP/HTTPS/ALL proxy variables or SSL_CERT_FILE/SSL_CERT_DIR overrides.
- Initial process-name search for `process_runner` matched none; a follow-up
  inventory checked all running Python processes instead. It did not dump full
  command lines, credentials or environment values.
- The probe is a separate process in the production container, not a function
  injected into the live plugin process. No QQ sends or state mutations occurred.

## Actual requests

| Round | URL | Stack | Result | Seconds |
|---|---|---|---|---:|
| 1 | https://t.me/s/codexresetalerts | Production default aiohttp, 12s | TimeoutError | 12.460 |
| 1 | https://codex-reset.com/api/push/notification | Same default, control | HTTP 200 | 0.694 |
| 2 | https://t.me/s/codexresetalerts | aiohttp IPv4-only diagnostic, 25s | TimeoutError | 25.136 |
| 2 | https://t.me/codexresetalerts/70?embed=1 | Default address selection, 25s | TimeoutError | 25.134 |
| 2 | https://codex-reset.com/api/push/notification | Same session pattern, control | HTTP 200 | 0.893 |

No Telegram response body or HTTP status was obtained. The observed failure is
a request timeout, not a proven certificate validation failure. The exact
DNS/TCP/TLS/network-policy cause was not localized by this narrow probe.

## Acceptance questions

1. No account/session was used. Public availability is acknowledged, but this
   production path did not retrieve any Telegram response.
2. Message ID, permalink, X/Tibo URL, Tweet ID, timestamp and message body:
   NOT RUNTIME-VERIFIED here because no archive body was returned.
3. History pagination and resuming from message N to recover N+1 after later
   Reset/Watch messages: NOT VERIFIED. No guessed cursor parameter was treated
   as a contract, and no synthetic archive response was substituted.
4. Actual sent TG messages would be delivery evidence. `/banked-reset` entries
   are not delivery receipts, including 2090964822422949999. No timeline fallback
   or Tweet/title/NLP classifier was implemented.

## Decision

Stop implementation under the user's explicit final-blocker condition.
Current production access prerequisite fails, and therefore identity/pagination
acceptance cannot complete. Do not ship latest-only recovery or unknown=>notify.

The actionable unblock is a permitted, functioning production egress path to
the Telegram public archive. Once that exists, the next step is to fetch raw
archive pages and follow real pagination links, prove recovery of actual older
message IDs, then evaluate a cursor/pending/per-group-receipt implementation.
No production network/proxy/CA changes were authorized or made in this probe.

## Artifacts and Git

- `runtime_probe.py`, `runtime_probe_round2.py`: scripts sent via stdin.
- `round1.jsonl`, `round2.jsonl`: exact captured stdout, including control bodies.
- `process_environment.json`: redacted process metadata (presence booleans only).
- Branch remains `review/v0.1.12-fix`, HEAD remains the required v0.1.11 baseline
  `5122f7227d78f1b907a455d36c187048a3a23d4b`; no tracked changes.
- These local diagnostic files are under the repository's existing ignored
  `.work/` directory. No implementation commit or candidate push was created.
- No full regression run: no implementation changed; runtime probes are not
  presented as production regression tests or a successful recovery test.
