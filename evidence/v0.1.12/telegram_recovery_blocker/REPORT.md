# v0.1.12 forensic / blocker evidence — NOT A FIX CANDIDATE

Base: `5122f7227d78f1b907a455d36c187048a3a23d4b` (v0.1.11).
Branch: `review/v0.1.12-fix`. Historical probe window: 2026-09-11 15:15–15:17 UTC.
This commit packages the previous investigation; no network probe was rerun
during packaging. It contains no production change or successful recovery test.

## Current bounded conclusion

**当前生产运行环境中，没有验证出可用的 Telegram public archive runtime path，
因此 message identity / pagination / offline recovery 无法完成验收。**

This means acceptance remains blocked. It does not establish that no working
runtime path can exist. The probe limitations and packaging checks are detailed below.
Independent review must decide BLOCKER CONFIRMED versus CHANGES REQUESTED.

## Confirmed by captured requests

- Probes executed inside the existing production `maibot-core` container using
  `/MaiMBot/.venv/bin/python -B -`, via stdin, without writing remote files.
- Python 3.13.15 / aiohttp 3.13.5 / OpenSSL 3.5.6. The Python/aiohttp/default TLS
  **request construction** is equivalent to baseline plugin `_get_json`:
  `ClientSession(timeout=ClientTimeout(total=12))` followed by `session.get(url)`.
  No explicit proxy, cookies, user authorization or TLS-verification bypass.
  This is not a claim of fully proven equivalence to every live process setting.
- The probe directly reads its own environment through `os.environ`. Default
  CA path and round-2 CA file hash are obtained directly; see raw outputs.
- Round 1 captures deployed plugin version 0.1.11 and source SHA-256
  `0063be5c30e4a2931b917ccca685d39e00b905114fd77c351a2338893b9b671a`.
  The local baseline plugin hash was checked against it during investigation.

| Round | URL | Address selection / total timeout | Captured outcome | Seconds |
|---|---|---|---|---:|
| 1 | https://t.me/s/codexresetalerts | Default / 12s | TimeoutError | 12.460 |
| 1 | https://codex-reset.com/api/push/notification | Default / 12s | HTTP 200 | 0.694 |
| 2 | https://t.me/s/codexresetalerts | AF_INET / 25s | TimeoutError | 25.136 |
| 2 | https://t.me/codexresetalerts/70?embed=1 | Default / 25s | TimeoutError | 25.134 |
| 2 | https://codex-reset.com/api/push/notification | Default / 25s | HTTP 200 | 0.893 |

The successful same-round control requests demonstrate that the container was
not completely without external connectivity. The Telegram requests produced
no captured HTTP status or body. `status` is printed only after `resp.text()`
finishes, so this instrumentation does not prove whether headers arrived before
a body-read timeout. It does not locate the failing DNS/TCP/TLS/HTTP phase.

## Environment inspection and probe review

Round 2's name search returned `runner_processes=[]`. This does not prove that
no plugin runner existed. A separate follow-up inspected all Python processes;
its original output is `process_environment.json`. Four non-probe processes
have the same underlying executable and network namespace as the probe. Their
checked proxy/CA environment variables are absent and CA environments match.
This does not identify which process is the watcher or establish equivalence
of any settings dynamically changed inside a live Python process.

During packaging, escaped tool-output display initially raised a suspicion
that the /proc delimiter was a literal backslash-zero. AST inspection of the
actual saved file disproved that suspicion: the delimiter is NUL (byte 0).
`verify_evidence.py` verifies that value and uses a synthetic NUL-delimited
environment with proxy/CA variables present as a positive control against
false-negative parsing. No delimiter defect is asserted and no original script
was modified. This is an offline parser check, not a synthetic Telegram fixture.

## Unconfirmed and explicitly not claimed

- We do not claim the Telegram archive is absent, lacks pagination, requires a
  user session, is permanently inaccessible, or has confirmed DNS/TLS blocking.
- Reviewer-observed public messages (including permalink
  https://t.me/codexresetalerts/70) are acknowledged, not independently recaptured
  by these production probes.
- Message IDs/permalinks, X URLs/Tweet IDs, timestamps, body extraction,
  pagination and offline cursor recovery are NOT runtime-verified here.
- Two short rounds cannot establish long-term availability or rule out transient
  service/network failures, request-header differences or process-specific setup.
- Requests were concurrent via `asyncio.gather`, each with a separate session;
  no sequential control, DNS/socket trace or aiohttp phase trace was captured.
  There are no additional DNS/socket/address-resolution result files to submit.
- `/banked-reset` timeline entries are not TG delivery receipts. No classifier,
  unknown=>notify, scraper or latest-only workaround is implemented.

## Execution provenance and artifacts

The original local driver invoked these arguments with `subprocess.run`:

```text
ssh -o BatchMode=yes -o ConnectTimeout=10 abiotic-server
  "sudo docker exec -i maibot-core /MaiMBot/.venv/bin/python -B -"
```

The contents of `runtime_probe.py` / `runtime_probe_round2.py` were sent as stdin.
Driver timeouts were 30s / 40s; both SSH calls returned exit 0 and empty stderr.
Captured stdout was written as UTF-8 text to `round1.jsonl` / `round2.jsonl`.
These are original locally saved outputs, not packet captures or original HTTP
wire bytes: `resp.text()` decodes bodies before JSON serialization.

The original on-disk scripts and outputs, and the prior report renamed to
`REPORT.original.md`, are copied byte-for-byte from `.work/tg-final-probe/`.
The follow-up environment script was not originally saved; its tool-call source
is reproduced as `process_environment_probe.reconstructed.py`, clearly marked.
It is not claimed to be an original file and was not rerun during packaging.

`source_manifest.json` hashes every other file in this directory and records
original-copy versus new/reconstructed provenance. `.gitattributes` disables
Git line-ending conversion here to retain original file bytes. No secrets,
credentials, full environment dumps or SSH connection credentials are included.

Offline check, run from repository root:

```text
python evidence/v0.1.12/telegram_recovery_blocker/verify_evidence.py
```

Production code/config/version/manifest/Core/SDK/test expectations: zero diff.
203-test suite: **未运行，因为 production code zero diff**.
No old test result is asserted as this commit's result. No deployment, service
restart, Telegram authorization, QQ message, merge, tag or Release was performed.
