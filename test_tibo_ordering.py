"""Real dogfood pair: concurrent preparation, chronological per-group attempts."""

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import plugin as module
from test_tibo_render import PNG, feed, ready, wire_images, tibo_batch
from test_watcher import _drain_inflight, _gstate

G1, G2 = "100000001", "100000002"
FIXTURE = json.loads(
    (Path(__file__).parent / "tests/fixtures/tibo-ordering/feed.json").read_text()
)
OLDER, NEWER = FIXTURE["tweets"]
OLD, NEW = OLDER["id"], NEWER["id"]
NOW = datetime(2026, 10, 7, 19, 25, tzinfo=timezone.utc)


def setup(tmp_path, monkeypatch):
    p = ready(
        tmp_path,
        monkeypatch,
        tibo_full_push=True,
        display_mode="image",
        group_ids=[G1, G2],
    )
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    wire_images(p)

    async def enrich(tweet_id, payload):
        t = (
            payload.hint
            if isinstance(payload, module.TweetCandidate)
            else next(t for t in payload["tweets"] if t["id"] == tweet_id)
        )
        content = module.TweetContent(t["text"], "feed", "full")
        content.quote = t.get("quote")
        return content

    async def translate(*args):
        return "中文翻译"

    p._enrich_content = enrich
    p._llm_translate_content = translate
    return p


async def drain(p):
    await asyncio.wait_for(_drain_inflight(p), timeout=2)


def receipts(p, gid):
    return set(_gstate(p, gid).get("tibo_delivered_ids", []))


@pytest.mark.parametrize("split_cycle", [False, True])
@pytest.mark.parametrize("slow_stage", ["provider", "llm", "quote", "render"])
def test_faster_newer_preparation_cannot_overtake(
    tmp_path, monkeypatch, split_cycle, slow_stage
):
    p = setup(tmp_path, monkeypatch)
    sends, prepared = [], []

    async def scenario():
        release, new_ready = asyncio.Event(), asyncio.Event()
        enrich, translate, render = (
            p._enrich_content,
            p._llm_translate_content,
            module.render_card,
        )

        async def delayed_enrich(tweet_id, payload):
            if slow_stage == "provider" and tweet_id == OLD:
                await release.wait()
            return await enrich(tweet_id, payload)

        async def delayed_translate(tweet_id, *args):
            if (slow_stage == "llm" and tweet_id == OLD) or (
                slow_stage == "quote" and tweet_id == OLDER["quote"]["id"]
            ):
                await release.wait()
            return await translate(tweet_id, *args)

        async def delayed_render(ctx, card, now):
            tweet_id = card.url.rsplit("/", 1)[-1]
            if slow_stage == "render" and tweet_id == OLD:
                await release.wait()
            result = await render(ctx, card, now)
            card.image_base64 = base64.b64encode(
                base64.b64decode(PNG) + tweet_id.encode()
            ).decode()
            if tweet_id not in prepared:
                prepared.append(tweet_id)
            if tweet_id == NEW:
                new_ready.set()
            assert result
            return card.image_base64

        async def send(data, stream, **kwargs):
            gid = stream.removeprefix("qq-group-")
            tweet_id = base64.b64decode(data)[len(base64.b64decode(PNG)) :].decode()
            sends.append((gid, tweet_id))
            return {"sent": True}

        p._enrich_content = delayed_enrich
        p._llm_translate_content = delayed_translate
        monkeypatch.setattr(module, "render_card", delayed_render)
        p.ctx.send.image = send
        assert datetime.fromisoformat(NEWER["at"]) - datetime.fromisoformat(
            OLDER["at"]
        ) == timedelta(seconds=16)
        assert module.is_tibo_main_post(NEWER)
        if split_cycle:
            await p._process_tibo_posts(tibo_batch(feed(OLDER)), [G1, G2])
            await asyncio.sleep(0)
            await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        else:
            await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        try:
            await asyncio.wait_for(new_ready.wait(), timeout=1)
            assert prepared == [NEW]
            assert sends == []
        finally:
            release.set()
            await drain(p)
        for gid in [G1, G2]:
            assert [tid for g, tid in sends if g == gid] == [OLD, NEW]
            assert receipts(p, gid) == {OLD, NEW}
            entry = _gstate(p, gid)
            assert set(entry["tibo_seen_ids"]) == {OLD, NEW}
            assert set(entry["delivered_notice_keys"]) == {
                f"tibo:{OLD}",
                f"tweet:{OLD}",
                f"tibo:{NEW}",
                f"tweet:{NEW}",
            }
        assert len(p.ctx.render.htmls) == 2  # Shared render, not one per group.
        p._load_state()
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        await drain(p)
        assert len(sends) == 4  # Success-only receipts still suppress replays.

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, "exception", "cancel"])
def test_one_group_blocked_then_failed_does_not_block_other_group(
    tmp_path, monkeypatch, failure
):
    p = setup(tmp_path, monkeypatch)
    attempts = []

    async def scenario():
        release, other_done = asyncio.Event(), asyncio.Event()

        async def send(gid, text, card=None):
            tid = card.url.rsplit("/", 1)[-1]
            attempts.append((gid, tid))
            if (gid, tid) == (G1, OLD):
                await release.wait()
                if failure == "exception":
                    raise RuntimeError("old group send failed")
                if failure == "cancel":
                    raise asyncio.CancelledError
                return False
            if (gid, tid) == (G2, NEW):
                other_done.set()
            return True

        p._send_group_notice = send
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        try:
            await asyncio.wait_for(other_done.wait(), timeout=1)
            assert (G1, NEW) not in attempts
            assert receipts(p, G2) == {OLD, NEW}
            assert receipts(p, G1) == set()
        finally:
            release.set()
            await drain(p)
        assert [tid for g, tid in attempts if g == G1] == [OLD, NEW]
        assert receipts(p, G1) == {NEW}
        assert OLD not in _gstate(p, G1)["tibo_seen_ids"]
        assert f"tweet:{OLD}" not in _gstate(p, G1)["delivered_notice_keys"]

        # Retry only the failed group; do not resend either successful receipt.
        async def retry(gid, text, card=None):
            attempts.append((gid, card.url.rsplit("/", 1)[-1]))
            return True

        p._send_group_notice = retry
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        await drain(p)
        assert attempts[-1] == (G1, OLD)
        assert len(attempts) == 5
        assert all(receipts(p, g) == {OLD, NEW} for g in [G1, G2])

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["exception", "cancel", "cancel-before-start"])
def test_earlier_preparation_failure_releases_all_group_barriers(
    tmp_path, monkeypatch, failure
):
    p = setup(tmp_path, monkeypatch)
    original = p._enrich_content

    async def fail(tweet_id, payload):
        if tweet_id == OLD:
            if failure == "exception":
                raise RuntimeError("provider failed")
            raise asyncio.CancelledError
        return await original(tweet_id, payload)

    p._enrich_content = fail

    async def scenario():
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        if failure == "cancel-before-start":
            p._inflight[f"tibo:{OLD}"]["task"].cancel()
        await drain(p)
        for gid in [G1, G2]:
            assert receipts(p, gid) == {NEW}
            assert OLD not in _gstate(p, gid)["tibo_seen_ids"]
        assert len(p.ctx.render.htmls) == 1

    asyncio.run(scenario())


def test_chronology_uses_parsed_instant_not_iso_string(tmp_path, monkeypatch):
    p = setup(tmp_path, monkeypatch)
    attempts = []

    async def send(gid, text, card=None):
        attempts.append((gid, card.url.rsplit("/", 1)[-1]))
        return True

    p._send_group_notice = send
    # Older sorts after newer lexically, but still represents 19:19:17 UTC.
    older = {**OLDER, "at": "2026-10-08T03:19:17+08:00"}

    async def scenario():
        await p._process_tibo_posts(tibo_batch(feed(NEWER, older)), [G1, G2])
        await drain(p)
        for gid in [G1, G2]:
            assert [tid for g, tid in attempts if g == gid] == [OLD, NEW]

    asyncio.run(scenario())


def test_waiting_tibo_does_not_hold_delivery_lock_for_other_lanes(
    tmp_path, monkeypatch
):
    p = setup(tmp_path, monkeypatch)
    original = p._enrich_content

    async def scenario():
        release, newer_ready = asyncio.Event(), asyncio.Event()

        async def enrich(tid, payload):
            if tid == OLD:
                await release.wait()
            else:
                newer_ready.set()
            return await original(tid, payload)

        p._enrich_content = enrich
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        try:
            await asyncio.wait_for(newer_ready.wait(), 1)
            await asyncio.wait_for(
                p._deliver_notice(G1, "push_banked_keys", "real-key", "", "System"),
                timeout=1,
            )
            assert "real-key" in _gstate(p, G1)["push_banked_keys"]
            assert not receipts(p, G1)
        finally:
            release.set()
            await drain(p)

    asyncio.run(scenario())


def test_parent_cancellation_during_send_releases_newer(tmp_path, monkeypatch):
    p = setup(tmp_path, monkeypatch)
    attempts = []

    async def scenario():
        sending = asyncio.Event()

        async def send(gid, text, card=None):
            tid = card.url.rsplit("/", 1)[-1]
            attempts.append((gid, tid))
            if tid == OLD:
                sending.set()
                await asyncio.Event().wait()
            return True

        p._send_group_notice = send
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        await asyncio.wait_for(sending.wait(), 1)
        p._inflight[f"tibo:{OLD}"]["task"].cancel()
        await drain(p)
        for gid in [G1, G2]:
            assert [tid for g, tid in attempts if g == gid] == [OLD, NEW]
            assert receipts(p, gid) == {NEW}

    asyncio.run(scenario())


def test_cancelling_newer_waiter_does_not_cancel_older(tmp_path, monkeypatch):
    p = setup(tmp_path, monkeypatch)
    original = p._enrich_content

    async def scenario():
        release, rendered = asyncio.Event(), asyncio.Event()
        render = module.render_card

        async def enrich(tid, payload):
            if tid == OLD:
                await release.wait()
            return await original(tid, payload)

        async def capture(ctx, card, now):
            result = await render(ctx, card, now)
            if card.url.endswith(NEW):
                rendered.set()
            return result

        p._enrich_content = enrich
        monkeypatch.setattr(module, "render_card", capture)
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        await asyncio.wait_for(rendered.wait(), 1)
        task = p._inflight[f"tibo:{NEW}"]["task"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not p._inflight[f"tibo:{OLD}"]["task"].cancelling()
        release.set()
        await drain(p)
        assert all(receipts(p, gid) == {OLD} for gid in [G1, G2])

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["text", "image"])
def test_text_and_render_failure_fallback_keep_order_and_links(
    tmp_path, monkeypatch, mode
):
    p = setup(tmp_path, monkeypatch)
    data = p.config.model_dump()
    data["watcher"]["display_mode"] = mode
    p.set_plugin_config(data)
    renderer, images = wire_images(p, render_failure=True)

    async def scenario():
        await p._process_tibo_posts(tibo_batch(feed(NEWER, OLDER)), [G1, G2])
        await drain(p)
        for gid in [G1, G2]:
            texts = [
                text
                for stream, text in p.ctx.send.sent_messages
                if stream.endswith(gid)
            ]
            assert len(texts) == 2
            assert OLDER["url"] in texts[0] and NEWER["url"] in texts[1]
            assert receipts(p, gid) == {OLD, NEW}
        assert not images
        assert len(renderer.htmls) == (2 if mode == "image" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize("at", [None, "", "not-a-time"])
def test_unknown_time_is_not_registered_as_barrier(tmp_path, monkeypatch, at):
    p = setup(tmp_path, monkeypatch)

    async def scenario():
        await p._process_tibo_posts(
            tibo_batch(feed({**OLDER, "at": at}, NEWER)), [G1, G2]
        )
        await drain(p)
        for gid in [G1, G2]:
            assert receipts(p, gid) == {NEW}
            assert OLD not in _gstate(p, gid)["tibo_seen_ids"]

    asyncio.run(scenario())


@pytest.mark.parametrize("lane", ["route", "push-banked"])
@pytest.mark.parametrize("g1_success", [False, True])
def test_finished_group_can_route_while_other_tibo_group_is_sending(
    tmp_path, monkeypatch, lane, g1_success
):
    p = setup(tmp_path, monkeypatch)
    attempts = []
    key = f"{module.PUSH_BANKED_KEY_PREFIX}{OLD}"
    identity = f"banked:{OLD}:announced"

    async def scenario():
        release, g2_sending = asyncio.Event(), asyncio.Event()
        first_g1 = True

        async def image(data, stream, **kwargs):
            nonlocal first_g1
            gid = stream.removeprefix("qq-group-")
            attempts.append(gid)
            if gid == G2:
                g2_sending.set()
                await release.wait()
            if gid == G1 and first_g1:
                first_g1 = False
                return {"sent": g1_success}
            return {"sent": True}

        async def failed_text(*args, **kwargs):
            return {"sent": False}  # Failed image fallback must not create coverage.

        p.ctx.send.image = image
        p.ctx.send.text = failed_text
        payload = feed(OLDER)
        await p._process_tibo_posts(tibo_batch(payload), [G1, G2])
        attempt = p._inflight[f"tibo:{OLD}"]
        try:
            await asyncio.wait_for(g2_sending.wait(), 1)
            await asyncio.wait_for(attempt["delivery_done"][G1].wait(), 1)
            assert not attempt["task"].done()
            assert attempt["delivery_done"][G1].is_set()
            assert not attempt["delivery_done"][G2].is_set()
            assert p._tibo_inflight(OLD, G1) is False
            assert p._tibo_inflight(OLD, G2) is True
            assert receipts(p, G1) == ({OLD} if g1_success else set())
            assert not receipts(p, G2)
            assert key not in _gstate(p, G1).get("push_banked_keys", [])
            assert key not in _gstate(p, G2).get("push_banked_keys", [])

            if lane == "route":
                remaining = await asyncio.wait_for(
                    p._route_tibo_groups([G1], "push_banked_keys", key, identity, OLD),
                    1,
                )
                assert remaining == ([] if g1_success else [G1])
                for gid in remaining:
                    assert await p._deliver_notice(
                        gid,
                        "push_banked_keys",
                        key,
                        identity,
                        "Banked notice",
                        source_tweet_id=OLD,
                    )
            else:
                await p._process_push_banked(
                    {
                        "alert": {
                            "id": OLD,
                            "kind": "banked",
                            "banked_state": "announced",
                            "url": OLDER["url"],
                        }
                    },
                    [G1],
                    payload,
                )
                await asyncio.wait_for(p._inflight[key]["task"], 1)

            # Assert takeover/coverage now, before releasing the other group.
            assert not release.is_set() and not attempt["task"].done()
            assert attempts.count(G1) == (1 if g1_success else 2)
            assert attempts.count(G2) == 1
            assert key in _gstate(p, G1).get("push_banked_keys", [])
            assert identity in _gstate(p, G1)["delivered_notice_keys"]
            assert f"tweet:{OLD}" in _gstate(p, G1)["delivered_notice_keys"]
            assert key not in _gstate(p, G2).get("push_banked_keys", [])
            assert not _gstate(p, G2).get("delivered_notice_keys")
            assert receipts(p, G1) == ({OLD} if g1_success else set())
            assert p._tibo_inflight(OLD, G2) is True
        finally:
            release.set()
            await drain(p)

        assert receipts(p, G2) == {OLD}
        assert p._tibo_inflight(OLD, G2) is False
        # After its own success, G2 claims coverage without another QQ send.
        assert (
            await p._route_tibo_groups([G2], "push_banked_keys", key, identity, OLD)
            == []
        )
        assert key in _gstate(p, G2).get("push_banked_keys", [])
        assert attempts.count(G2) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", [False, True])
def test_tibo_inflight_legacy_and_missing_group_completion(
    tmp_path, monkeypatch, legacy
):
    p = setup(tmp_path, monkeypatch)
    assert p._tibo_inflight(OLD, G1) is False
    p._inflight[f"tibo:{OLD}"] = {"groups": [G1]}
    if not legacy:
        p._inflight[f"tibo:{OLD}"]["delivery_done"] = {}
    assert p._tibo_inflight(OLD, G1) is legacy
    assert p._tibo_inflight(OLD, G2) is False
