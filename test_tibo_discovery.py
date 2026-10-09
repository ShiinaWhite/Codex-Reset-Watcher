"""Real missing-post corpus and normalized provider/delivery boundaries; no live send."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import plugin as module
import tibo_discovery as discovery
from test_tibo_render import ready
from test_watcher import _drain_inflight

ROOT = Path(__file__).parent / "tests/fixtures/tibo-discovery"
NOW = datetime(2026, 10, 9, 13, 6, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((ROOT / (name + ".json")).read_text())


def corpus_batch():
    batches = []
    for name in [
        "host_fx_v2_replies",
        "fx_replies_page2_retry",
        "fx_replies_page3",
        "host_fx_v2_posts",
        "fx_posts_page2",
    ]:
        rows = [
            discovery.fx_candidate(t, "fxtwitter") for t in fixture(name)["results"]
        ]
        batches.append(discovery.DiscoveryBatch(tuple(t for t in rows if t), "ok"))
    return discovery.union_batches(*batches)


def candidate(**changes):
    row = {
        "type": "status",
        "id": "123",
        "text": "Own comment",
        "created_timestamp": NOW.timestamp(),
        "author": {"screen_name": "thsottiaux", "id": discovery.AUTHOR_ID},
        "replying_to": None,
        "reposted_by": None,
    }
    row.update(changes)
    return discovery.fx_candidate(row, "fxtwitter")


def test_captured_origins_and_twelve_missing_golden():
    for record in fixture("ORIGINS"):
        assert (
            hashlib.sha256(
                (Path(__file__).parent / record["path"]).read_bytes()
            ).hexdigest()
            == record["sha256"]
        )
    batch = corpus_batch()
    by_id = {c.tweet_id: c for c in batch.candidates}
    golden = fixture("golden")
    assert len(golden) == 12
    assert sum(g["within_48h"] for g in golden) == 4
    for g in golden:
        c = by_id[g["tweet_id"]]
        assert c.published_at == discovery.parsed_time(g["published_at"])
        assert module.is_tibo_main_post(c.as_post())
        assert (NOW - c.published_at <= timedelta(hours=48)) == g["within_48h"]
    observed = [
        c for c in batch.candidates if c.published_at >= NOW - timedelta(hours=72)
    ]
    assert len(observed) == 53
    assert sum(module.is_tibo_main_post(c.as_post()) for c in observed) == 20
    assert list(batch.candidates) == sorted(
        batch.candidates, key=lambda c: (c.published_at, c.tweet_id)
    )
    assert len(by_id) == len(batch.candidates)


@pytest.mark.parametrize(
    "name,eligible",
    [
        ("host_fx_golden", True),
        ("fx_2108275041276420573", True),
        ("fx_2108349826727588000", True),
        ("fx_2107912709715132482", True),
        ("fx_2107599996225007892", True),
        ("fx_2108428560822424062", False),
    ],
)
def test_real_single_post_relations_quote_note_poll(name, eligible):
    row = fixture(name)["tweet"]
    c = discovery.fx_candidate(row, "fxtwitter", legacy=True)
    assert c.metadata.status == "verified"
    assert module.is_tibo_main_post(c.as_post()) is eligible
    if name == "fx_2108275041276420573":
        assert row["is_note_tweet"] and c.hint["quote"]["id"]
    if name == "fx_2107599996225007892":
        assert c.hint["poll"] and c.metadata.replying_to == discovery.HANDLE


@pytest.mark.parametrize(
    "mutation",
    [
        {"author": {"screen_name": "thsottiaux", "id": "other"}},
        {"author": None},
        {"id": "１２３"},
        {"created_timestamp": float("nan")},
        {"created_timestamp": True},
        {"text": None},
        {"type": "thread"},
    ],
)
def test_bad_row_identity_and_schema_rejected(mutation):
    with pytest.raises(ValueError):
        candidate(**mutation)


def test_other_author_context_filtered_and_missing_relation_stays_unknown():
    assert candidate(author={"screen_name": "other", "id": "other"}) is None
    c = candidate(replying_to={"status": "456"})
    assert c.metadata.status == "unknown"
    assert not module.is_tibo_main_post(c.as_post())


def test_codex_is_hint_only_and_duplicate_provider_union():
    hints = discovery.codex_hints(fixture("host_feed"))
    assert hints.status == "partial"
    assert all(c.metadata.status == "unknown" for c in hints.candidates)
    primary = corpus_batch()
    merged = discovery.union_batches(primary, hints)
    by_id = {c.tweet_id: c for c in merged.candidates}
    tid = "2108084615349170480"
    assert by_id[tid].metadata.status == "verified"
    assert by_id[tid].discovery_sources == ("codex-reset", "fxtwitter")
    assert len([c for c in merged.candidates if c.tweet_id == tid]) == 1
    for bad in [
        None,
        {**fixture("host_feed"), "stale": True},
        {**fixture("host_feed"), "source_scope": "search"},
    ]:
        assert not discovery.codex_hints(bad).candidates


def test_reliable_metadata_conflict_requires_verification():
    c = candidate()
    other = candidate(replying_to={"screen_name": "other", "status": "456"})
    batch = discovery.union_batches(
        discovery.DiscoveryBatch((c,), "ok"), discovery.DiscoveryBatch((other,), "ok")
    )
    assert batch.candidates[0].metadata.status == "conflict"
    assert (
        discovery.union_batches(batch, discovery.DiscoveryBatch((c,), "ok"))
        .candidates[0]
        .metadata.status
        == "conflict"
    )


def test_both_primary_timelines_start_together_pagination_and_duplicate():
    calls = []
    first_started = set()
    release = asyncio.Event()

    async def get(url, budget):
        params = parse_qs(urlparse(url).query)
        replies = "with_replies" in params
        calls.append(params)
        assert budget == 12
        if "cursor" not in params:
            first_started.add(replies)
            if len(first_started) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), 1)
            row = candidate().hint
            return {"code": 200, "results": [row], "cursor": {"bottom": "next"}}
        return {"code": 200, "results": [candidate().hint], "cursor": {"bottom": None}}

    batch = asyncio.run(discovery.FxTiboDiscovery(get).fetch_recent(NOW))
    assert batch.status == "ok" and batch.request_count == 4
    assert len(batch.candidates) == 1
    assert len(calls) == 4


@pytest.mark.parametrize(
    "failure",
    [
        None,
        {"code": 404, "results": [], "cursor": {"bottom": None}},
        {"code": 200},
        {"code": 500, "results": []},
    ],
)
def test_page_failure_is_partial_not_eof(failure):
    async def get(url, budget):
        if "cursor=" in url:
            return failure
        return {
            "code": 200,
            "results": [candidate().hint],
            "cursor": {"bottom": "next"},
        }

    batch = asyncio.run(discovery.FxTiboDiscovery(get).fetch_recent(NOW))
    assert batch.status == "partial" and batch.candidates and batch.issues


def test_page_cap_duplicate_cursor_and_wall_time_budget(monkeypatch):
    async def get(url, budget):
        params = parse_qs(urlparse(url).query)
        cur = params.get("cursor", ["0"])[0]
        return {
            "code": 200,
            "results": [candidate().hint],
            "cursor": {"bottom": str(int(cur) + 1)},
        }

    batch = asyncio.run(discovery.FxTiboDiscovery(get).fetch_recent(NOW))
    assert batch.status == "partial" and batch.request_count == 8

    async def stuck(url, budget):
        await asyncio.Event().wait()

    batch = asyncio.run(discovery.FxTiboDiscovery(stuck, budget=0.02).fetch_recent(NOW))
    assert batch.status == "unavailable" and batch.request_count == 2
    assert batch.elapsed_ms < 500

    async def repeated(url, budget):
        return {
            "code": 200,
            "results": [candidate().hint],
            "cursor": {"bottom": "same"},
        }

    batch = asyncio.run(discovery.FxTiboDiscovery(repeated).fetch_recent(NOW))
    assert batch.status == "partial" and batch.request_count == 4


def test_cancel_discovery_cancels_both_transports():
    async def scenario():
        started = asyncio.Event()
        count = 0
        cancelled = []

        async def get(url, budget):
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(url)

        task = asyncio.create_task(discovery.FxTiboDiscovery(get).fetch_recent(NOW))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(cancelled) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad",
    [None, {"code": 200}, {"code": 400, "results": [], "cursor": {"bottom": None}}],
)
def test_outage_fallback_does_not_create_baseline(tmp_path, monkeypatch, bad):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    entry = p._group_state(p._target_groups()[0])
    entry["tibo_baseline_done"] = False

    async def get(url, budget):
        return bad

    p._get_json = get

    async def scenario():
        primary = await discovery.FxTiboDiscovery(get).fetch_recent(NOW)
        batch = discovery.union_batches(
            primary, discovery.codex_hints(fixture("host_feed"))
        )
        assert batch.fallback_used
        await p._process_tibo_posts(batch, p._target_groups())
        assert not p._inflight

    asyncio.run(scenario())
    assert not entry["tibo_baseline_done"] and not entry["tibo_seen_ids"]


def test_reliable_exclusions_before_attempt_registration(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    excluded = [
        candidate(replying_to={"screen_name": "other", "status": "456"}),
        candidate(reposted_by={"screen_name": "thsottiaux"}),
        candidate(text="https://t.co/share"),
    ]

    async def scenario():
        await p._process_tibo_posts(
            discovery.DiscoveryBatch(tuple(excluded), "ok"), p._target_groups()
        )
        assert not p._inflight

    asyncio.run(scenario())
    assert not p.ctx.send.sent_messages


@pytest.mark.parametrize(
    "resolution", ["exclude", "unknown", "timeout", "failure", "eligible"]
)
def test_unknown_bounded_verification_releases_ordering_without_seen(
    tmp_path, monkeypatch, resolution
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    monkeypatch.setattr(discovery, "VERIFICATION_BUDGET_SECONDS", 0.02)
    older = replace(
        candidate(replying_to={"status": "456"}),
        published_at=NOW - timedelta(minutes=1),
    )
    newer = candidate(id="124")

    async def get(url, budget):
        if not url.endswith("/123"):
            return None
        if resolution == "timeout":
            await asyncio.Event().wait()
        if resolution == "failure":
            raise OSError("offline")
        if resolution == "unknown":
            return None
        row = dict(
            older.hint,
            created_timestamp=older.published_at.timestamp(),
            replying_to_status="456",
            replying_to="other" if resolution == "exclude" else "thsottiaux",
        )
        return {"code": 200, "tweet": row}

    p._get_json = get

    async def scenario():
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((newer, older), "ok"), p._target_groups()
        )
        await asyncio.wait_for(_drain_inflight(p), 1)
        assert not p._inflight

    asyncio.run(scenario())
    entry = p._group_state(p._target_groups()[0])
    assert "124" in entry["tibo_delivered_ids"]
    assert ("123" in entry["tibo_seen_ids"]) == (resolution == "eligible")
    assert len(p.ctx.send.sent_messages) == (2 if resolution == "eligible" else 1)


def test_unknown_cancellation_and_old_unknown_never_write_seen(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    started = asyncio.Event()

    async def get(url, budget):
        started.set()
        await asyncio.Event().wait()

    p._get_json = get
    c = candidate(replying_to={"status": "456"})

    async def scenario():
        await p._process_tibo_posts(
            discovery.DiscoveryBatch(
                (replace(c, published_at=NOW - timedelta(hours=49)),), "ok"
            ),
            p._target_groups(),
        )
        assert not p._inflight
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((c,), "ok"), p._target_groups()
        )
        await started.wait()
        await p._cancel_inflight()
        assert not p._inflight

    asyncio.run(scenario())
    assert not p._group_state(p._target_groups()[0])["tibo_seen_ids"]


def test_outage_hint_verification_delivers_existing_group_only(tmp_path, monkeypatch):
    p = ready(
        tmp_path,
        monkeypatch,
        tibo_full_push=True,
        group_ids=["100000001", "100000002"],
    )
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    new_group = p._group_state("100000002")
    new_group["tibo_baseline_done"] = False
    row = fixture("host_fx_golden")["tweet"]
    tid = row["id"]
    feed = {
        "stale": False,
        "profile": {"handle": discovery.HANDLE},
        "source_scope": "timeline",
        "tweets": [],
        "radar_context": [
            {
                "id": tid,
                "at": discovery.parsed_time("2026-10-08T06:38:33+00:00").isoformat(),
                "text": row["text"],
            }
        ],
    }

    async def get(url, budget):
        return {"code": 200, "tweet": row} if url.endswith("/" + tid) else None

    p._get_json = get

    async def scenario():
        primary = await discovery.FxTiboDiscovery(get).fetch_recent(NOW)
        assert primary.status == "unavailable"
        await p._process_tibo_posts(
            discovery.union_batches(primary, discovery.codex_hints(feed)),
            p._target_groups(),
        )
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert p._group_state("100000001")["tibo_delivered_ids"] == [tid]
    assert not new_group["tibo_baseline_done"]
    assert not new_group["tibo_seen_ids"]
    assert len(p.ctx.send.sent_messages) == 1


def test_discovery_wait_does_not_delay_latest_only_sampling(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)

    async def scenario():
        timelines = set()
        both_started, sampled, release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def get(url, budget):
            if "/2/profile/" in url:
                timelines.add("with_replies=true" in url)
                if len(timelines) == 2:
                    both_started.set()
                await release.wait()
                return {"code": 200, "results": [], "cursor": {"bottom": None}}
            if "/api/push/notification" in url:
                sampled.set()
            return None

        p._get_json = get
        check = asyncio.create_task(p._check_once())
        try:
            await asyncio.wait_for(both_started.wait(), 1)
            assert sampled.is_set() and not check.done()
        finally:
            release.set()
            await check

    asyncio.run(scenario())
