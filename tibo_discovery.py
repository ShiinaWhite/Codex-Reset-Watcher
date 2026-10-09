"""Public Tibo discovery adapters; transport provenance is never delivery identity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import math
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlparse

HANDLE = "thsottiaux"
AUTHOR_ID = "1953337039510003712"
DISCOVERY_BUDGET_SECONDS = 12.0
VERIFICATION_BUDGET_SECONDS = 12.0
MAX_PAGES_PER_TIMELINE = 4
PAGE_SIZE = 20
LOOKBACK_HOURS = 72
GetJSON = Callable[[str, float], Awaitable[Any]]


def tweet_id(value: Any) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]+", value) else ""


def parsed_time(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(timezone.utc) if at.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TweetMetadata:
    is_reply: bool | None = None
    replying_to: str | None = None
    parent_id: str | None = None
    is_repost: bool | None = None
    status: str = "unknown"


@dataclass(frozen=True)
class TweetCandidate:
    tweet_id: str
    published_at: datetime | None
    discovery_sources: tuple[str, ...]
    text: str = ""
    metadata: TweetMetadata = TweetMetadata()
    hint: dict | None = None

    def as_post(self) -> dict:
        return {
            "id": self.tweet_id,
            "at": self.published_at.isoformat() if self.published_at else "",
            "text": self.text,
            "is_reply": self.metadata.is_reply,
            "replying_to": self.metadata.replying_to,
            "in_reply_to_tweet_id": self.metadata.parent_id,
            "is_repost": self.metadata.is_repost,
        }


@dataclass(frozen=True)
class DiscoveryBatch:
    candidates: tuple[TweetCandidate, ...] = ()
    status: str = "unavailable"
    issues: tuple[str, ...] = ()
    request_count: int = 0
    elapsed_ms: int = 0
    fallback_used: bool = False


def fx_candidate(
    row: Any, source: str, *, legacy: bool = False, check_author_id: bool = True
) -> TweetCandidate | None:
    if not isinstance(row, dict):
        raise ValueError("invalid status row")
    if not legacy and row.get("type") != "status":
        raise ValueError("invalid status discriminator")
    author = row.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("screen_name"), str):
        raise ValueError("missing author")
    # Other authors are legitimate parent/context rows, never Tibo candidates.
    if author["screen_name"].casefold() != HANDLE:
        return None
    if check_author_id and author.get("id") != AUTHOR_ID:
        raise ValueError("Tibo author ID mismatch")
    tid = tweet_id(row.get("id"))
    seconds = row.get("created_timestamp")
    if not tid or isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("invalid ID/time")
    try:
        if not math.isfinite(seconds):
            raise ValueError("invalid time")
        at = datetime.fromtimestamp(seconds, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("invalid time") from exc
    text = row.get("text")
    if not isinstance(text, str):
        raise ValueError("missing text")
    relation = row.get("replying_to")
    metadata = TweetMetadata()
    if "replying_to" in row and "reposted_by" in row:
        repost = row["reposted_by"]
        repost_known = repost is None or isinstance(repost, dict)
        metadata = TweetMetadata(
            is_repost=(repost is not None) if repost_known else None
        )
        if (
            relation is None
            and repost_known
            and (
                not legacy
                or ("replying_to_status" in row and row["replying_to_status"] is None)
            )
        ):
            metadata = TweetMetadata(
                False, is_repost=repost is not None, status="verified"
            )
        else:
            target = (
                relation
                if legacy
                else (
                    relation.get("screen_name") if isinstance(relation, dict) else None
                )
            )
            parent = (
                row.get("replying_to_status")
                if legacy
                else (relation.get("status") if isinstance(relation, dict) else None)
            )
            if isinstance(target, str) and re.fullmatch(r"[A-Za-z0-9_]+", target):
                metadata = TweetMetadata(
                    True,
                    target.casefold(),
                    tweet_id(parent) or None,
                    metadata.is_repost,
                )
            if metadata.is_reply is True and tweet_id(parent) and repost_known:
                metadata = TweetMetadata(
                    True, target.casefold(), parent, repost is not None, "verified"
                )
    return TweetCandidate(tid, at, (source,), text, metadata, row)


def codex_hints(feed: Any, *, now: datetime | None = None) -> DiscoveryBatch:
    if not isinstance(feed, dict) or feed.get("stale") is not False:
        return DiscoveryBatch(issues=("codex unavailable/stale",))
    profile = feed.get("profile")
    if (
        not isinstance(profile, dict)
        or profile.get("handle") != HANDLE
        or feed.get("source_scope") != "timeline"
    ):
        return DiscoveryBatch(issues=("codex provenance invalid",))
    candidates = []
    for collection in ("tweets", "radar_context"):
        rows = feed.get(collection)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not tweet_id(row.get("id")):
                continue
            at = parsed_time(row.get("at"))
            # Cross-check the same recent window as the primary, not Codex's
            # unrelated older product history. Unknown times remain untrusted.
            if (
                now is not None
                and at is not None
                and at < now - timedelta(hours=LOOKBACK_HOURS)
            ):
                continue
            candidates.append(
                TweetCandidate(
                    row["id"],
                    at,
                    ("codex-reset",),
                    row.get("text") if isinstance(row.get("text"), str) else "",
                    hint=row,
                )
            )
    # A product projection can never establish a new full timeline baseline.
    return DiscoveryBatch(tuple(candidates), "partial", fallback_used=True)


def union_batches(
    *batches: DiscoveryBatch, complementary: bool = False
) -> DiscoveryBatch:
    # Ordinary and with-replies are complementary surfaces of one provider;
    # an external cross-check adding IDs disproves the primary's completeness.
    primary = batches[0] if batches else DiscoveryBatch()
    primary_ids = {c.tweet_id for c in primary.candidates}
    discrepancies = []
    merged: dict[str, TweetCandidate] = {}
    for index, batch in enumerate(batches):
        for new in batch.candidates:
            if index and not complementary and new.tweet_id not in primary_ids:
                discrepancies.append("cross-check adds " + new.tweet_id)
            old = merged.get(new.tweet_id)
            if old is None:
                merged[new.tweet_id] = new
                continue
            reliable_old = old.metadata.status == "verified"
            reliable_new = new.metadata.status == "verified"
            chosen = new if reliable_new and not reliable_old else old
            conflict = (
                old.metadata.status == "conflict"
                or new.metadata.status == "conflict"
                or (
                    reliable_old
                    and reliable_new
                    and (
                        old.metadata != new.metadata
                        or old.published_at != new.published_at
                    )
                )
            )
            if conflict or (
                old.published_at is not None
                and new.published_at is not None
                and old.published_at != new.published_at
            ):
                discrepancies.append("metadata/time discrepancy " + new.tweet_id)
            merged[new.tweet_id] = replace(
                chosen,
                published_at=chosen.published_at
                or new.published_at
                or old.published_at,
                text=chosen.text or new.text or old.text,
                hint=chosen.hint or new.hint or old.hint,
                metadata=TweetMetadata(status="conflict")
                if conflict
                else chosen.metadata,
                discovery_sources=tuple(
                    sorted(set(old.discovery_sources + new.discovery_sources))
                ),
            )
    rows = sorted(
        merged.values(),
        key=lambda c: (
            c.published_at or datetime.max.replace(tzinfo=timezone.utc),
            c.tweet_id,
        ),
    )
    return replace(
        primary,
        candidates=tuple(rows),
        status="partial"
        if primary.status == "ok" and discrepancies
        else primary.status,
        fallback_used=any("codex-reset" in c.discovery_sources for c in rows),
        issues=tuple(issue for b in batches for issue in b.issues)
        + tuple(sorted(set(discrepancies))),
    )


class FxTiboDiscovery:
    """At most eight GETs, both timelines start together, one total wall-time budget."""

    def __init__(self, get_json: GetJSON, *, budget: float = DISCOVERY_BUDGET_SECONDS):
        self.get_json = get_json
        self.budget = budget

    async def fetch_recent(self, now: datetime) -> DiscoveryBatch:
        started = time.monotonic()
        requests = 0
        cutoff = now - timedelta(hours=LOOKBACK_HOURS)

        async def timeline(replies: bool) -> DiscoveryBatch:
            nonlocal requests
            rows = []
            cursors = set()
            cursor = None
            for _ in range(MAX_PAGES_PER_TIMELINE):
                params = {"count": str(PAGE_SIZE)}
                if replies:
                    params["with_replies"] = "true"
                if cursor:
                    params["cursor"] = cursor
                requests += 1
                try:
                    payload = await self.get_json(
                        "https://api.fxtwitter.com/2/profile/thsottiaux/statuses?"
                        + urlencode(params),
                        self.budget,
                    )
                    if (
                        not isinstance(payload, dict)
                        or payload.get("code") != 200
                        or not isinstance(payload.get("results"), list)
                    ):
                        raise ValueError(
                            "invalid code/schema (cursor errors are not EOF)"
                        )
                    current = []
                    issues = []
                    for row in payload["results"]:
                        try:
                            candidate = fx_candidate(row, "fxtwitter")
                            if candidate:
                                current.append(candidate)
                        except ValueError as exc:
                            issues.append(str(exc))
                    rows.extend(current)
                    cursor_obj = payload.get("cursor")
                    if not isinstance(cursor_obj, dict) or "bottom" not in cursor_obj:
                        raise ValueError("invalid cursor schema")
                    bottom = cursor_obj["bottom"]
                    if bottom is not None and not isinstance(bottom, str):
                        raise ValueError("invalid cursor")
                    if issues:
                        return DiscoveryBatch(tuple(rows), "partial", tuple(issues))
                    # Thread/context rows may be older than their replies: never stop
                    # on the first old row, only after an entire authored page is old.
                    if current and all(c.published_at < cutoff for c in current):
                        return DiscoveryBatch(tuple(rows), "ok")
                    if bottom is None:
                        return DiscoveryBatch(tuple(rows), "ok")
                    if not bottom or bottom in cursors:
                        raise ValueError("empty/repeated cursor")
                    cursors.add(bottom)
                    cursor = bottom
                except (Exception, asyncio.CancelledError) as exc:
                    if (
                        isinstance(exc, asyncio.CancelledError)
                        and not asyncio.current_task().cancelling()
                    ):
                        raise
                    return DiscoveryBatch(
                        tuple(rows),
                        "partial" if rows else "unavailable",
                        (type(exc).__name__ + ": " + str(exc),),
                    )
            return DiscoveryBatch(tuple(rows), "partial", ("page cap reached",))

        tasks = [
            asyncio.create_task(timeline(False)),
            asyncio.create_task(timeline(True)),
        ]
        try:
            async with asyncio.timeout(self.budget):
                batches = await asyncio.gather(*tasks)
        except TimeoutError:
            batches = [task.result() for task in tasks]
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        merged = union_batches(*batches, complementary=True)
        status = (
            "ok"
            if all(b.status == "ok" for b in batches) and merged.status == "ok"
            else ("partial" if merged.candidates else "unavailable")
        )
        return replace(
            merged,
            status=status,
            request_count=requests,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )


async def verify_candidate(
    candidate: TweetCandidate,
    get_json: GetJSON,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> TweetCandidate:
    """Unknown relation gets at most two GETs within one verification budget."""
    async with asyncio.timeout(VERIFICATION_BUDGET_SECONDS):
        # The total budget includes waiting for the Watcher's shared capacity.
        async with semaphore or asyncio.Semaphore(1):
            return await _verify_candidate(candidate, get_json)


async def _verify_candidate(
    candidate: TweetCandidate, get_json: GetJSON
) -> TweetCandidate:
    for source, url in (
        ("fxtwitter", f"https://api.fxtwitter.com/status/{candidate.tweet_id}"),
        (
            "vxtwitter",
            f"https://api.vxtwitter.com/thsottiaux/status/{candidate.tweet_id}",
        ),
    ):
        try:
            payload = await get_json(url, VERIFICATION_BUDGET_SECONDS)
            if source == "fxtwitter":
                if not isinstance(payload, dict) or payload.get("code") != 200:
                    continue
                resolved = fx_candidate(payload.get("tweet"), source, legacy=True)
            else:
                if (
                    not isinstance(payload, dict)
                    or payload.get("user_screen_name") != HANDLE
                ):
                    continue
                url = urlparse(str(payload.get("tweetURL") or ""))
                if (
                    url.scheme != "https"
                    or url.hostname not in {"x.com", "twitter.com"}
                    or url.path != f"/{HANDLE}/status/{candidate.tweet_id}"
                ):
                    continue
                resolved = fx_candidate(
                    {
                        "id": payload.get("tweetID"),
                        "text": payload.get("text"),
                        "created_timestamp": payload.get("date_epoch"),
                        "author": {"screen_name": HANDLE},
                        **(
                            {
                                "replying_to": payload["replyingTo"],
                                "replying_to_status": payload.get("replyingToID"),
                            }
                            if "replyingTo" in payload
                            else {}
                        ),
                        **(
                            {
                                "reposted_by": None
                                if payload["retweetURL"] is None
                                else {}
                            }
                            if "retweetURL" in payload
                            else {}
                        ),
                    },
                    source,
                    legacy=True,
                    check_author_id=False,
                )
                if resolved:
                    resolved = replace(resolved, hint=payload)
            if (
                resolved
                and resolved.tweet_id == candidate.tweet_id
                and resolved.metadata.status == "verified"
            ):
                return replace(
                    resolved,
                    discovery_sources=tuple(
                        sorted(
                            set(
                                candidate.discovery_sources + resolved.discovery_sources
                            )
                        )
                    ),
                )
        except Exception:
            continue
    return candidate
