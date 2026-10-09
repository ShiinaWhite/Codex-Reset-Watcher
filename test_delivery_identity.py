"""Production duplicate regressions. Snapshots are current, not historical polls."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import plugin as module
from test_watcher import _make_plugin, _gstate, _drain_inflight, BEIJING


FIXTURES = Path(__file__).parent / "tests/fixtures/historical/docs/forensics/2026-09-30"
GID = "100000001"
GLOBAL_IDS = ["2103637477760311522", "2103911959544610829"]
BANKED_ID = "2105008012525769969"


def _feed(event_id):
    payload = json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))
    return {
        k: [item for item in payload[k] if item["id"] == event_id]
        for k in ("events", "tweets")
    }


def _forecast(event_id, tier="likely"):
    # Reconstructed minimal L4 input from logged receipt identity; not a claim
    # that a complete historical forecast HTTP response was retained.
    return {
        "official_signal": {
            "delivery_destination": "alerts",
            "alert_event_id": f"signal:{event_id}:{tier}",
            "tweet_id": event_id,
            "url": f"https://x.com/thsottiaux/status/{event_id}",
        }
    }


def _ready(tmp_path, monkeypatch, **kwargs):
    plugin = _make_plugin(tmp_path, **kwargs)
    for gid in plugin._target_groups():
        plugin._group_state(gid).update(feed_baseline_done=True, notified_keys=[])
    monkeypatch.setattr(
        module, "_utcnow", lambda: datetime(2026, 9, 27, 0, tzinfo=timezone.utc)
    )
    calls = []

    async def no_network(url, timeout_seconds):
        calls.append(url)
        return None

    plugin._get_json = no_network
    return plugin, calls


@pytest.mark.parametrize("event_id", GLOBAL_IDS)
@pytest.mark.parametrize("first", ["feed", "l4"])
def test_global_cross_lane_orders_survive_reload(
    tmp_path, monkeypatch, event_id, first
):
    plugin, calls = _ready(tmp_path, monkeypatch)
    feed = _feed(event_id)
    # 26th's current pointer has reverted to none; logged L1 delivery proves it
    # once qualified. Use announced for this explicit reconstructed input.
    feed["events"][0]["announcement_state"] = "announced"
    feed["events"][0]["source"] = "archive"
    feed["tweets"][0]["explicit_reset_claim"] = False

    async def run(which):
        if which == "feed":
            await plugin._process_feed_signals(feed, [GID], BEIJING)
        else:
            await plugin._process_upstream_alert(_forecast(event_id), [GID], feed)
        await _drain_inflight(plugin)

    asyncio.run(run(first))
    assert len(plugin.ctx.send.sent_messages) == 1
    first_calls = len(calls)
    plugin._load_state()
    asyncio.run(run("l4" if first == "feed" else "feed"))
    assert len(plugin.ctx.send.sent_messages) == 1
    assert len(calls) == first_calls  # no second Provider/LLM pipeline
    assert f"global-declared:{event_id}" in _gstate(plugin)["notified_keys"]
    assert (
        f"upstream-alert:signal:{event_id}:likely"
        in _gstate(plugin)["upstream_alert_keys"]
    )

    # A different upstream alert ID remains a new revision, even for same Tweet.
    async def revision():
        await plugin._process_upstream_alert(_forecast(event_id, "strong"), [GID], feed)
        await _drain_inflight(plugin)

    asyncio.run(revision())
    assert len(plugin.ctx.send.sent_messages) == 2


@pytest.mark.parametrize("first", ["push", "feed"])
def test_observed_banked_orders_body_translation_and_reload(
    tmp_path, monkeypatch, first
):
    plugin, calls = _ready(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module, "_utcnow", lambda: datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    )
    feed = _feed(BANKED_ID)
    push = json.loads((FIXTURES / "push_notification.json").read_text(encoding="utf-8"))
    translations = []

    async def translate(tweet_id, url, at, content):
        if content is None:
            return None
        translations.append((tweet_id, content.source, content.text))
        return "监测账户收到新的 Banked Reset，Tibo 尚未在 X 宣布。"

    plugin._llm_translate_content = translate

    async def run(which):
        if which == "push":
            await plugin._process_push_banked(push, [GID], feed)
        else:
            await plugin._process_feed_signals(feed, [GID], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(run(first))
    plugin._load_state()
    asyncio.run(run("feed" if first == "push" else "push"))
    assert len(plugin.ctx.send.sent_messages) == 1
    message = plugin.ctx.send.sent_messages[0][1]
    assert "中文翻译：" in message and "上游通知：" in message
    assert "来源：https://codex-reset.com/banked-reset" in message
    assert "Tibo 原文" not in message and "/status/" not in message
    assert calls == [] and len(translations) == 1
    assert translations[0][0:2] == ("", "upstream")
    assert f"banked:{BANKED_ID}:available" in _gstate(plugin)["notified_keys"]
    assert f"push-banked:{BANKED_ID}" in _gstate(plugin)["push_banked_keys"]


@pytest.mark.parametrize("family", ["global", "banked"])
def test_cross_lane_send_await_race_is_serialized(tmp_path, monkeypatch, family):
    plugin, _ = _ready(tmp_path, monkeypatch)
    sent = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(gid, message):
            assert not plugin._state_lock.locked()
            entered.set()
            await release.wait()
            sent.append(message)
            return True

        plugin._send_group_text = send
        identity = "global:event" if family == "global" else "banked:event:available"
        first = asyncio.create_task(
            plugin._deliver_notice(
                GID, "notified_keys", "feed:event", identity, "first"
            )
        )
        await entered.wait()
        second = asyncio.create_task(
            plugin._deliver_notice(
                GID,
                "upstream_alert_keys" if family == "global" else "push_banked_keys",
                "upstream-alert:signal:event:likely"
                if family == "global"
                else "mirror:event",
                identity,
                "second",
                l4_tweet_id="event" if family == "global" else "",
            )
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    assert sent == ["first"]


def test_baseline_and_age_keys_never_become_delivery_proof(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch)
    event_id = GLOBAL_IDS[0]
    _gstate(plugin)["notified_keys"] = [f"global-declared:{event_id}"]
    plugin._save_state()
    plugin._load_state()

    async def run():
        await plugin._process_upstream_alert(
            _forecast(event_id), [GID], _feed(event_id)
        )
        await _drain_inflight(plugin)

    asyncio.run(run())
    assert len(plugin.ctx.send.sent_messages) == 1


def test_legacy_l4_receipt_is_positive_coverage_without_fingerprint(
    tmp_path, monkeypatch
):
    plugin, calls = _ready(tmp_path, monkeypatch)
    event_id = GLOBAL_IDS[1]
    _gstate(plugin)["upstream_alert_tweet_ids"] = [event_id]
    _gstate(plugin)["upstream_alert_keys"] = [
        f"upstream-alert:signal:{event_id}:likely"
    ]
    plugin._save_state()
    plugin._load_state()
    asyncio.run(plugin._process_feed_signals(_feed(event_id), [GID], BEIJING))
    assert plugin.ctx.send.sent_messages == [] and calls == []


def test_banked_distinct_phase_and_unknown_phase_do_not_suppress(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module, "_utcnow", lambda: datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    )
    feed = _feed(BANKED_ID)

    async def run():
        # No matched event/explicit phase: current push remains independent.
        await plugin._process_push_banked(
            {"alert": {"id": BANKED_ID, "kind": "banked"}}, [GID], None
        )
        await _drain_inflight(plugin)
        for phase in ["announced", "arriving", "available"]:
            stage = copy.deepcopy(feed)
            stage["events"][0]["banked_state"] = phase
            await plugin._process_feed_signals(stage, [GID], BEIJING)
            await _drain_inflight(plugin)

    asyncio.run(run())
    assert len(plugin.ctx.send.sent_messages) == 4


def test_failed_group_retry_does_not_claim_cross_lane_coverage(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch, group_id="", group_ids=[GID, "100000002"])
    attempts = []

    async def send(gid, message):
        attempts.append(gid)
        return gid == GID or attempts.count(gid) > 1

    plugin._send_group_text = send

    async def run():
        for gid in [GID, "100000002"]:
            await plugin._deliver_notice(
                gid, "notified_keys", "feed:key", "global:event", "notice"
            )
        for gid in [GID, "100000002"]:
            await plugin._deliver_notice(
                gid,
                "upstream_alert_keys",
                "upstream-alert:signal:event:likely",
                "global:event",
                "notice",
                l4_tweet_id="event",
            )

    asyncio.run(run())
    assert attempts == [GID, "100000002", "100000002"]
    assert "global:event" in plugin._group_state("100000002")["delivered_notice_keys"]


def test_missing_l4_linkage_is_not_guessed_from_alert_id(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch)

    async def run():
        await plugin._deliver_notice(
            GID, "notified_keys", "feed:key", "global:event", "feed"
        )
        await plugin._process_upstream_alert(
            {
                "official_signal": {
                    "delivery_destination": "alerts",
                    "alert_event_id": "signal:event:likely",
                }
            },
            [GID],
            None,
        )
        await _drain_inflight(plugin)

    asyncio.run(run())
    assert len(plugin.ctx.send.sent_messages) == 2


def test_corrupt_new_coverage_is_discarded_conservatively(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch)
    _gstate(plugin)["delivered_notice_keys"] = [123]
    plugin._save_state()
    plugin._load_state()
    assert "delivered_notice_keys" not in _gstate(plugin)


def test_tweet_provenance_rejects_lookalike_hosts():
    for url in [
        "https://example.com/status/123",
        "https://x.com.evil/status/123",
        "https://x.com/user/status/fake",
        "https://codex-reset.com/banked-reset",
        "https://[broken/status/123",
    ]:
        assert module._tweet_source_id(url) == ""
    assert module._tweet_source_id("https://x.com/thsottiaux/status/123?foo=1") == "123"


@pytest.mark.parametrize("alert_id", ["signal:event:strong", "opaque-new-id"])
def test_feed_receipt_cannot_swallow_first_strong_or_unknown_l4_revision(
    tmp_path, monkeypatch, alert_id
):
    plugin, _ = _ready(tmp_path, monkeypatch)

    async def run():
        await plugin._deliver_notice(
            GID, "notified_keys", "feed:key", "global:event", "feed"
        )
        await plugin._process_upstream_alert(
            {
                "official_signal": {
                    "delivery_destination": "alerts",
                    "alert_event_id": alert_id,
                    "tweet_id": "event",
                }
            },
            [GID],
            None,
        )
        await _drain_inflight(plugin)

    asyncio.run(run())
    assert len(plugin.ctx.send.sent_messages) == 2


def test_banked_push_cannot_borrow_later_feed_phase():
    feed = _feed(BANKED_ID)
    alert = {"id": BANKED_ID, "kind": "banked", "at": "2026-09-28T00:00:00Z"}
    assert module._banked_notice_identity(alert, BANKED_ID, feed) == ""
    # Matching time alone does not link a Tweet's mutable phase either.
    alert["at"] = feed["events"][0]["announced_at"]
    feed["events"][0]["source"] = "live"
    assert module._banked_notice_identity(alert, BANKED_ID, feed) == ""
    alert["banked_state"] = "available"
    assert (
        module._banked_notice_identity(alert, BANKED_ID, feed)
        == f"banked:{BANKED_ID}:available"
    )
    alert["banked_state"] = {"future": "shape"}
    assert module._banked_notice_identity(alert, BANKED_ID, feed) == ""


def test_cancel_during_send_does_not_commit_receipt(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch)

    async def run():
        entered = asyncio.Event()

        async def send(gid, text):
            entered.set()
            await asyncio.Future()

        plugin._send_group_text = send
        task = asyncio.create_task(
            plugin._deliver_notice(
                GID, "notified_keys", "key", "global:event", "notice"
            )
        )
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert "global:event" not in _gstate(plugin).get("delivered_notice_keys", [])
    assert "key" not in _gstate(plugin)["notified_keys"]


def test_group_removed_during_send_is_not_recreated(tmp_path, monkeypatch):
    plugin, _ = _ready(tmp_path, monkeypatch)

    async def send(gid, text):
        plugin.config.watcher.group_ids = []
        plugin.config.watcher.group_ids_migrated = True
        plugin._reconcile_groups()
        return True

    plugin._send_group_text = send
    asyncio.run(
        plugin._deliver_notice(GID, "notified_keys", "key", "global:event", "notice")
    )
    assert GID not in plugin._state["groups"]


def test_upstream_observation_has_explicit_translation_provenance(
    tmp_path, monkeypatch
):
    plugin, calls = _ready(tmp_path, monkeypatch)
    prompts = []

    async def generate(prompt, **kwargs):
        prompts.append(prompt)
        return {"success": True, "response": '{"translation_zh":"已存入待兑换机会"}'}

    plugin.ctx.llm.generate = generate
    push = json.loads((FIXTURES / "push_notification.json").read_text(encoding="utf-8"))

    async def run():
        await plugin._process_push_banked(push, [GID], _feed(BANKED_ID))
        await _drain_inflight(plugin)

    asyncio.run(run())
    assert calls == [] and len(prompts) == 1
    user = prompts[0][1]["content"]
    assert "上游监测通知，不是 Tibo 推文" in user
    assert "tweet_id: unknown" in user
    assert push["alert"]["body"] in user
    assert "中文翻译：\n已存入待兑换机会" in plugin.ctx.send.sent_messages[0][1]


def test_alias_optimization_losing_coverage_reenriches_instead_of_sending_empty(
    tmp_path, monkeypatch
):
    plugin, _ = _ready(tmp_path, monkeypatch)
    event_id = GLOBAL_IDS[0]
    _gstate(plugin)["delivered_notice_keys"] = [f"global:{event_id}"]
    original = plugin._deliver_notice
    reset_once = False

    async def lose_coverage(*args, **kwargs):
        nonlocal reset_once
        if not reset_once:
            reset_once = True
            _gstate(plugin).pop("delivered_notice_keys")
        return await original(*args, **kwargs)

    plugin._deliver_notice = lose_coverage

    async def run():
        await plugin._process_upstream_alert(
            _forecast(event_id), [GID], _feed(event_id)
        )
        await _drain_inflight(plugin)

    asyncio.run(run())
    assert len(plugin.ctx.send.sent_messages) == 1
    assert plugin.ctx.send.sent_messages[0][1].startswith(module.GLOBAL_NOTICE_TITLE)


def test_push_body_still_mirrored_as_upstream_when_feed_join_absent(
    tmp_path, monkeypatch
):
    plugin, calls = _ready(tmp_path, monkeypatch)
    push = json.loads((FIXTURES / "push_notification.json").read_text(encoding="utf-8"))

    async def run():
        await plugin._process_push_banked(push, [GID], None)
        await _drain_inflight(plugin)

    asyncio.run(run())
    body = plugin.ctx.send.sent_messages[0][1]
    assert "上游通知：" in body and push["alert"]["body"] in body
    assert "Tibo 原文" not in body and "/status/" not in body and calls == []
