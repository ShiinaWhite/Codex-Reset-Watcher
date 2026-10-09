"""Real missing-post corpus and normalized provider/delivery boundaries; no live send."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
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
    path = ROOT / (name + ".json")
    raw = (
        path.read_bytes()
        if path.exists()
        else gzip.decompress(path.with_suffix(".json.gz").read_bytes())
    )
    return json.loads(raw)


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
    return discovery.union_batches(*batches, complementary=True)


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
        raw = (Path(__file__).parent / record["path"]).read_bytes()
        if record.get("encoding") == "gzip":
            raw = gzip.decompress(raw)
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
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


def single_payload(c):
    return {
        "code": 200,
        "tweet": {
            **c.hint,
            "created_timestamp": c.published_at.timestamp(),
            "replying_to": c.metadata.replying_to,
            "replying_to_status": c.metadata.parent_id,
        },
    }


@pytest.mark.parametrize("lane", ["push", "l4"])
@pytest.mark.parametrize(
    "resolution", ["eligible", "exclude", "unresolved", "failure", "cancel"]
)
def test_provisional_ordering_never_eats_old_lane(
    tmp_path, monkeypatch, lane, resolution
):
    from test_tibo_render import forecast

    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    unknown = candidate(replying_to={"status": "456"})
    resolved = (
        candidate()
        if resolution == "eligible"
        else candidate(replying_to={"screen_name": "other", "status": "456"})
    )

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def get(url, budget):
            started.set()
            await release.wait()
            if resolution == "failure":
                raise OSError("offline")
            return None if resolution == "unresolved" else single_payload(resolved)

        async def enrich(*args):
            return module.TweetContent("Real body", "fixture", "full")

        p._get_json = get
        p._enrich_content = enrich
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((unknown,), "partial"), p._target_groups()
        )
        await started.wait()
        tibo_task = p._inflight["tibo:123"]["task"]
        try:
            if lane == "push":
                await p._process_push_banked(
                    {
                        "alert": {
                            "kind": "banked",
                            "id": "123",
                            "banked_state": "available",
                            "url": "https://x.com/thsottiaux/status/123",
                            "at": NOW.isoformat(),
                        }
                    },
                    p._target_groups(),
                )
                key, field = "push-banked:123", "push_banked_keys"
            else:
                await p._process_upstream_alert(forecast("123"), p._target_groups())
                key, field = "upstream-alert:signal:123:likely", "upstream_alert_keys"
            await asyncio.wait_for(p._inflight[key]["task"], 1)
            entry = p._group_state(p._target_groups()[0])
            assert key in entry[field], (
                "provisional attempt must not consume a latest-only alert"
            )
            assert "tweet:123" in entry["delivered_notice_keys"]
            assert len(p.ctx.send.sent_messages) == 1
            if resolution == "cancel":
                tibo_task.cancel()
        finally:
            release.set()
            await _drain_inflight(p)
        assert not p._inflight
        assert (
            len(p.ctx.send.sent_messages) == 1
        )  # Canonical old-lane receipt covers later eligible Tibo.
        assert ("123" in entry["tibo_seen_ids"]) == (resolution == "eligible")

    asyncio.run(scenario())


def test_crosscheck_novel_id_and_conflicts_make_batch_partial(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    entry = p._group_state(p._target_groups()[0])
    entry["tibo_baseline_done"] = False
    c = candidate()
    hints = discovery.DiscoveryBatch(
        (replace(candidate(id="124"), metadata=discovery.TweetMetadata()),), "partial"
    )
    batch = discovery.union_batches(discovery.DiscoveryBatch((c,), "ok"), hints)
    assert batch.status == "partial"
    asyncio.run(p._process_tibo_posts(batch, p._target_groups()))
    assert not entry["tibo_baseline_done"] and not entry["tibo_seen_ids"]
    conflict = replace(c, published_at=NOW - timedelta(seconds=1))
    batch = discovery.union_batches(
        discovery.DiscoveryBatch((c,), "ok"),
        discovery.DiscoveryBatch((conflict,), "ok"),
    )
    assert batch.status == "partial"
    # A known Fx timestamp disagreeing with a Codex-only hint also prevents baseline.
    batch = discovery.union_batches(
        discovery.DiscoveryBatch((c,), "ok"),
        discovery.DiscoveryBatch(
            (replace(conflict, metadata=discovery.TweetMetadata()),), "partial"
        ),
    )
    assert batch.status == "partial"


@pytest.mark.parametrize("excluded_only", [False, True])
def test_complete_empty_eligible_baseline_preserves_first_future_post(
    tmp_path, monkeypatch, excluded_only
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    entry = p._group_state(p._target_groups()[0])
    entry["tibo_baseline_done"] = False
    rows = (
        (candidate(replying_to={"screen_name": "other", "status": "456"}),)
        if excluded_only
        else ()
    )

    async def scenario():
        await p._process_tibo_posts(
            discovery.DiscoveryBatch(rows, "ok"), p._target_groups()
        )
        assert entry["tibo_baseline_done"] and not entry["tibo_seen_ids"]
        p._load_state()
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((candidate(),), "ok"), p._target_groups()
        )
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert p._group_state(p._target_groups()[0])["tibo_delivered_ids"] == ["123"]
    assert len(p.ctx.send.sent_messages) == 1


@pytest.mark.parametrize("conflict_type", ["relation", "time"])
def test_authoritative_single_post_repairs_conflict(conflict_type):
    good = candidate()
    other = (
        candidate(replying_to={"screen_name": "thsottiaux", "status": "456"})
        if conflict_type == "relation"
        else replace(good, published_at=NOW - timedelta(seconds=1))
    )
    # Chosen time is the wrong one, while the authoritative single post is good.
    c = discovery.union_batches(
        discovery.DiscoveryBatch((other,), "ok"),
        discovery.DiscoveryBatch((good,), "ok"),
    ).candidates[0]

    async def get(url, budget):
        return single_payload(good)

    repaired = asyncio.run(discovery.verify_candidate(c, get))
    assert repaired.metadata == good.metadata
    assert repaired.published_at == good.published_at


@pytest.mark.parametrize("direction", ["earlier", "later"])
def test_time_repair_replaces_participant_and_preserves_final_order(
    tmp_path, monkeypatch, direction
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    early, late = NOW - timedelta(minutes=2), NOW
    authoritative = replace(
        candidate(), published_at=early if direction == "earlier" else late
    )
    wrong = replace(
        authoritative, published_at=late if direction == "earlier" else early
    )
    conflicted = discovery.union_batches(
        discovery.DiscoveryBatch((wrong,), "ok"),
        discovery.DiscoveryBatch((authoritative,), "ok"),
    ).candidates[0]
    neighbor = replace(candidate(id="124"), published_at=NOW - timedelta(minutes=1))
    sends = []

    async def scenario():
        started, release, neighbor_ready = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def get(url, budget):
            started.set()
            await release.wait()
            return single_payload(authoritative)

        async def enrich(tid, payload):
            if tid == "124":
                neighbor_ready.set()
            return module.TweetContent("Body", "fixture", "full")

        async def send(gid, message, card=None):
            sends.append("123" if "/status/123" in message else "124")
            return True

        p._get_json, p._enrich_content, p._send_group_notice = get, enrich, send
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((conflicted, neighbor), "partial"),
            p._target_groups(),
        )
        old = p._inflight["tibo:123"]
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(neighbor_ready.wait(), 1)
            assert sends == [], (
                "unresolved time cannot let neighbor overtake a possible earlier post"
            )
        finally:
            release.set()
            await asyncio.wait_for(_drain_inflight(p), 1)
        assert all(e.is_set() for e in old["delivery_done"].values())
        assert not p._inflight
        assert sends == (["123", "124"] if direction == "earlier" else ["124", "123"])

    asyncio.run(scenario())


def test_url_only_codex_hint_requires_authoritative_body(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    real = candidate(text="My comment https://t.co/x")
    hint = replace(
        real,
        text="https://t.co/x",
        metadata=discovery.TweetMetadata(),
        hint={"text": "https://t.co/x"},
        discovery_sources=("codex-reset",),
    )

    async def get(url, budget):
        return single_payload(real)

    p._get_json = get

    async def scenario():
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((hint,), "partial"), p._target_groups()
        )
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert p._group_state(p._target_groups()[0])["tibo_delivered_ids"] == ["123"]
    assert "My comment" in p.ctx.send.sent_messages[0][1]


@pytest.mark.parametrize("exit_mode", ["cancel", "timeout"])
def test_twenty_unknown_verifications_have_shared_bound_and_queue_deadline(
    tmp_path, monkeypatch, exit_mode
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    monkeypatch.setattr(discovery, "VERIFICATION_BUDGET_SECONDS", 0.05)
    rows = tuple(
        candidate(id=str(1000 + n), replying_to={"status": "456"}) for n in range(20)
    )
    active = peak = started_count = 0

    async def scenario():
        full = asyncio.Event()

        async def get(url, budget):
            nonlocal active, peak, started_count
            active += 1
            peak = max(peak, active)
            started_count += 1
            if active == 4:
                full.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        p._get_json = get
        await p._process_tibo_posts(
            discovery.DiscoveryBatch(rows, "partial"), p._target_groups()
        )
        attempts = list(p._inflight.values())
        if exit_mode == "cancel":
            await asyncio.wait_for(full.wait(), 1)
            await p._cancel_inflight()
        else:
            await asyncio.wait_for(_drain_inflight(p), 0.15)
        assert not p._inflight and active == 0
        assert all(e.is_set() for a in attempts for e in a["delivery_done"].values())

    asyncio.run(scenario())
    assert peak == 4
    # A queued deadline can expire just after a slot wakes; it still cannot
    # exceed the shared capacity or extend the round by another full budget.
    assert started_count == 4 if exit_mode == "cancel" else 4 <= started_count <= 20
    assert not p.ctx.send.sent_messages
    assert not p._group_state(p._target_groups()[0])["tibo_seen_ids"]


def test_crosscheck_scope_does_not_treat_old_codex_history_as_recent_gap():
    feed = {
        "stale": False,
        "profile": {"handle": discovery.HANDLE},
        "source_scope": "timeline",
        "tweets": [
            {
                "id": "123",
                "at": (NOW - timedelta(hours=73)).isoformat(),
                "text": "Old history",
            }
        ],
        "radar_context": [],
    }
    primary = discovery.DiscoveryBatch((), "ok")
    scoped = discovery.union_batches(primary, discovery.codex_hints(feed, now=NOW))
    assert scoped.status == "ok" and not scoped.candidates
    feed["tweets"][0]["at"] = (NOW - timedelta(hours=1)).isoformat()
    scoped = discovery.union_batches(primary, discovery.codex_hints(feed, now=NOW))
    assert scoped.status == "partial" and len(scoped.candidates) == 1


def test_multiple_provisional_promotions_do_not_deadlock(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    rows = [
        replace(
            candidate(id=str(n)),
            published_at=NOW - timedelta(minutes=130 - n),
            metadata=discovery.TweetMetadata(),
        )
        for n in [123, 124, 125]
    ]
    sends = []

    async def scenario():
        releases = {c.tweet_id: asyncio.Event() for c in rows}
        enriched = {c.tweet_id: asyncio.Event() for c in rows}

        async def get(url, budget):
            tid = url.rsplit("/", 1)[-1]
            await releases[tid].wait()
            return single_payload(
                replace(
                    next(c for c in rows if c.tweet_id == tid),
                    metadata=discovery.TweetMetadata(
                        False, is_repost=False, status="verified"
                    ),
                )
            )

        async def enrich(tid, payload):
            enriched[tid].set()
            return module.TweetContent("Body", "fixture", "full")

        async def send(gid, message, card=None):
            sends.append(next(tid for tid in releases if "/status/" + tid in message))
            return True

        p._get_json, p._enrich_content, p._send_group_notice = get, enrich, send
        await p._process_tibo_posts(
            discovery.DiscoveryBatch(tuple(rows), "partial"), p._target_groups()
        )
        try:
            for tid in ["125", "124"]:
                releases[tid].set()
                await asyncio.wait_for(enriched[tid].wait(), 1)
                assert not sends
        finally:
            releases["123"].set()
            await asyncio.wait_for(_drain_inflight(p), 1)
        assert sends == ["123", "124", "125"] and not p._inflight

    asyncio.run(scenario())


@pytest.mark.parametrize("final_age_hours", [1, 49])
def test_time_repair_applies_original_age_guard_to_authoritative_time(
    tmp_path, monkeypatch, final_age_hours
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    actual = replace(candidate(), published_at=NOW - timedelta(hours=final_age_hours))
    conflicted = replace(
        actual,
        published_at=NOW - timedelta(hours=50 if final_age_hours == 1 else 1),
        metadata=discovery.TweetMetadata(status="conflict"),
    )

    async def get(url, budget):
        return single_payload(actual)

    p._get_json = get

    async def scenario():
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((conflicted,), "partial"), p._target_groups()
        )
        await _drain_inflight(p)

    asyncio.run(scenario())
    entry = p._group_state(p._target_groups()[0])
    assert "123" in entry["tibo_seen_ids"]
    assert len(p.ctx.send.sent_messages) == (1 if final_age_hours == 1 else 0)


def test_promoted_attempt_cancel_cleans_final_events_and_coverage(
    tmp_path, monkeypatch
):
    p = ready(
        tmp_path, monkeypatch, tibo_full_push=True, group_ids=["100000001", "100000002"]
    )
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    unknown = candidate(replying_to={"status": "456"})

    async def scenario():
        enriching = asyncio.Event()

        async def get(url, budget):
            return single_payload(candidate())

        async def enrich(*args):
            enriching.set()
            await asyncio.Event().wait()

        p._get_json, p._enrich_content = get, enrich
        await p._process_tibo_posts(
            discovery.DiscoveryBatch((unknown,), "partial"), p._target_groups()
        )
        provisional = p._inflight["tibo:123"]
        assert all(not p._tibo_inflight("123", gid) for gid in p._target_groups())
        await asyncio.wait_for(enriching.wait(), 1)
        final = p._inflight["tibo:123"]
        assert final is not provisional
        assert all(e.is_set() for e in provisional["delivery_done"].values())
        assert all(p._tibo_inflight("123", gid) for gid in p._target_groups())
        await p._cancel_inflight()
        assert not p._inflight
        assert all(e.is_set() for e in final["delivery_done"].values())

    asyncio.run(scenario())
    assert all(not p._group_state(gid)["tibo_seen_ids"] for gid in p._target_groups())
    assert not p.ctx.send.sent_messages
