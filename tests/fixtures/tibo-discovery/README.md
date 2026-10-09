# Captured public Tibo discovery fixtures

These are public HTTP JSON response bodies (large bodies are gzip-compressed without changing their decompressed bytes) from the 2026-10-09
read-only source investigation. ORIGINS.json records URLs, UTC capture instants
and original response byte SHA256s; test_tibo_discovery.py checks every digest. Cursor values are
historical fixtures, not reusable operational cursors. golden.json lists the
12 eligible missing posts at a frozen 2026-10-09T13:06:00Z observation clock;
within_48h is a historical test fact, not a current delivery instruction.

Ordinary + with-replies pages intentionally contain duplicate IDs, other-author
thread context, replies, self-replies, quotes, notes and polls. Synthetic failure,
unknown-relation and URL-only/repost variants are built inline in tests. These
files contain no production state, config, group receipt or QQ message history.
