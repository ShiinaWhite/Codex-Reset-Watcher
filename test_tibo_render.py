"""New feature boundaries; fake host transport, real SDK/config and HTML."""

import asyncio
import base64
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import plugin as module
from notice_card import NoticeCard, build_card_html
from test_watcher import _make_plugin, _gstate, _drain_inflight

NOW = datetime(2026, 10, 5, 4, 33, tzinfo=timezone.utc)
ID = "2106845241357824205"
GID = "100000001"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()


def post(**changes):
    return {
        "id": ID,
        "text": "We are shipping improvements.",
        "is_reply": False,
        "url": f"https://x.com/thsottiaux/status/{ID}",
        "at": NOW.isoformat(),
        **changes,
    }


def feed(*posts, events=None):
    return {
        "profile": {"handle": "thsottiaux"},
        "source_scope": "timeline",
        "stale": False,
        "tweets": list(posts),
        "events": events or [],
    }


def tibo_batch(payload):
    """Existing delivery fixtures enter the normalized boundary with known metadata."""
    from tibo_discovery import (
        DiscoveryBatch,
        TweetCandidate,
        TweetMetadata,
        parsed_time,
    )

    if (
        not isinstance(payload, dict)
        or payload.get("stale") is True
        or payload.get("profile", {}).get("handle") != "thsottiaux"
        or payload.get("source_scope") != "timeline"
    ):
        return DiscoveryBatch()
    rows = []
    for row in payload.get("tweets", []):
        refs = row.get("referenced_tweets") or []
        target = row.get("replying_to")
        target = (
            target.strip().removeprefix("@").casefold()
            if isinstance(target, str)
            else None
        )
        metadata = TweetMetadata(
            True
            if any(r.get("type") == "replied_to" for r in refs)
            else row.get("is_reply"),
            target,
            row.get("in_reply_to_tweet_id"),
            bool(
                row.get("is_retweet")
                or row.get("is_repost")
                or row.get("retweeted_status")
                or row.get("reposted_by")
                or any(r.get("type") == "retweeted" for r in refs)
            ),
            "verified",
        )
        if not str(row.get("id", "")).isascii() or not str(row.get("id", "")).isdigit():
            continue
        rows.append(
            TweetCandidate(
                str(row["id"]),
                parsed_time(row.get("at")),
                ("fixture",),
                row.get("text", ""),
                metadata,
                row,
            )
        )
    return DiscoveryBatch(tuple(rows), "ok")


def ready(tmp_path, monkeypatch, **config):
    p = _make_plugin(tmp_path, **config)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    for gid in p._target_groups():
        p._group_state(gid).update(
            tibo_baseline_done=True,
            tibo_seen_ids=[],
            feed_baseline_done=True,
            notified_keys=[],
        )

    async def no_network(*args):
        return None

    p._get_json = no_network
    return p


def run_posts(p, payload):
    async def scenario():
        await p._process_tibo_posts(tibo_batch(payload), p._target_groups())
        await _drain_inflight(p)

    asyncio.run(scenario())


def test_defaults_and_old_config_rebuild():
    from maibot_sdk.config import rebuild_plugin_config_data

    defaults = module.CodexResetWatcherConfig().model_dump()
    old = {
        "plugin": {"config_version": "1.2.0"},
        "watcher": {"group_ids": ["611817038"], "check_interval": 120},
    }
    rebuilt = rebuild_plugin_config_data(defaults, old)
    assert rebuilt["watcher"]["tibo_full_push"] is False
    assert rebuilt["watcher"]["display_mode"] == "text"
    assert rebuilt["watcher"]["group_ids"] == ["611817038"]
    assert rebuilt["watcher"]["check_interval"] == 120
    with pytest.raises(ValidationError):
        module.WatcherConfig(display_mode="broken")


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({}, True),
        ({"text": "Long note.\n" * 200, "is_note_tweet": True}, True),
        ({"text": "My comment https://t.co/quote", "is_quote": True}, True),
        ({"is_reply": True}, False),
        ({"replying_to": "someone"}, False),
        ({"is_reply": None}, False),
        ({"is_retweet": True}, False),
        ({"is_repost": True}, False),
        ({"text": "RT @someone: News"}, False),
        ({"text": "https://t.co/quote", "is_quote": True}, False),
        ({"referenced_tweets": [{"type": "retweeted", "id": "1"}]}, False),
        ({"referenced_tweets": [{"type": "replied_to", "id": "1"}]}, False),
    ],
)
def test_main_post_contract_and_actual_dispatch(
    tmp_path, monkeypatch, changes, expected
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    assert module.is_tibo_main_post(post(**changes)) is expected
    run_posts(p, feed(post(**changes)))
    assert len(p.ctx.send.sent_messages) == int(expected)
    assert (ID in _gstate(p).get("tibo_delivered_ids", [])) is expected


def test_default_has_no_new_lane_or_provider_calls(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch)

    async def forbid(*args):
        raise AssertionError("default must not enrich Tibo posts")

    p._enrich_content = forbid
    run_posts(p, feed(post()))
    assert not p.ctx.send.sent_messages


def test_baseline_never_absorbs_existing_alert(tmp_path, monkeypatch):
    p = _make_plugin(tmp_path, tibo_full_push=True)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)

    async def no_network(*args):
        return None

    p._get_json = no_network

    async def scenario():
        await p._process_tibo_posts(tibo_batch(feed(post())), [GID])
        assert ID in _gstate(p)["tibo_seen_ids"]
        assert not _gstate(p).get("tibo_delivered_ids")
        await p._process_upstream_alert(forecast(), [GID], feed(post()))
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert len(p.ctx.send.sent_messages) == 1
    assert p.ctx.send.sent_messages[0][1].startswith(module.GLOBAL_NOTICE_TITLE)


def forecast(tweet_id=ID):
    return {
        "official_signal": {
            "delivery_destination": "alerts",
            "alert_event_id": "signal:" + tweet_id + ":likely",
            "tweet_id": tweet_id,
            "url": f"https://x.com/thsottiaux/status/{tweet_id}",
        }
    }


@pytest.mark.parametrize("order", ["tibo_first", "old_first", "parallel"])
def test_cross_lane_absorption_orders_reload(tmp_path, monkeypatch, order):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    payload = feed(post())

    async def scenario():
        if order == "old_first":
            await p._process_upstream_alert(forecast(), [GID], payload)
            await _drain_inflight(p)
        await p._process_tibo_posts(tibo_batch(payload), [GID])
        if order == "parallel":
            await p._process_upstream_alert(forecast(), [GID], payload)
        await _drain_inflight(p)
        p._load_state()
        await p._process_upstream_alert(forecast(), [GID], payload)
        await _drain_inflight(p)
        await p._process_tibo_posts(tibo_batch(payload), [GID])
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert len(p.ctx.send.sent_messages) == 1
    assert ID in _gstate(p)["tibo_delivered_ids"]
    assert f"upstream-alert:signal:{ID}:likely" in _gstate(p)["upstream_alert_keys"]
    assert f"tweet:{ID}" in _gstate(p)["delivered_notice_keys"]


@pytest.mark.parametrize(
    "field", ["notified_keys", "upstream_alert_keys", "push_banked_keys"]
)
def test_delivered_tibo_absorbs_linked_revisions_and_confirmation(
    tmp_path, monkeypatch, field
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    run_posts(p, feed(post()))
    asyncio.run(
        p._deliver_notice(
            GID,
            field,
            "revision",
            "different-phase",
            "duplicate",
            source_tweet_id=ID,
            l4_tweet_id=ID,
            confirmation="confirmation",
        )
    )
    assert len(p.ctx.send.sent_messages) == 1
    assert "revision" in _gstate(p)[field]
    asyncio.run(
        p._deliver_notice(GID, field, "unlinked-system", "system:1", "system event")
    )
    assert len(p.ctx.send.sent_messages) == 2


def test_per_group_failure_reload_and_independent_retry(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True, group_ids=[GID, "100000002"])
    original = p._send_group_text

    async def fail_second(gid, message):
        return False if gid == "100000002" else await original(gid, message)

    p._send_group_text = fail_second
    run_posts(p, feed(post()))
    assert ID in _gstate(p)["tibo_delivered_ids"]
    assert not _gstate(p, "100000002").get("tibo_delivered_ids")
    assert not _gstate(p, "100000002")["tibo_seen_ids"]
    p._load_state()
    p._send_group_text = original
    run_posts(p, feed(post()))
    assert len(p.ctx.send.sent_messages) == 2
    assert ID in _gstate(p, "100000002")["tibo_delivered_ids"]


def test_pending_failure_never_becomes_receipt_and_old_lane_recovers(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            entered.set()
            await release.wait()
            return module.TweetContent("text", "feed", "full")

        p._enrich_content = delayed
        await p._process_tibo_posts(tibo_batch(feed(post())), [GID])
        await entered.wait()
        await p._process_upstream_alert(forecast(), [GID], feed(post()))
        await asyncio.sleep(0.02)
        assert not _gstate(p).get("upstream_alert_keys")
        p._send_group_text = fail
        release.set()
        await _drain_inflight(p)
        assert not _gstate(p).get("tibo_delivered_ids")
        p._send_group_text = success
        await p._process_upstream_alert(forecast(), [GID], feed(post()))
        await _drain_inflight(p)

    sent = []

    async def fail(*args):
        return False

    async def success(gid, message):
        sent.append(message)
        return True

    asyncio.run(scenario())
    assert len(sent) == 1
    assert _gstate(p).get("upstream_alert_keys")


@pytest.mark.parametrize(
    "mutation", ["stale", "profile", "scope", "empty", "unknown-time", "old"]
)
def test_bad_or_old_timeline_does_not_suppress_alerts(tmp_path, monkeypatch, mutation):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    payload = feed(post())
    if mutation == "stale":
        payload["stale"] = True
    if mutation == "profile":
        payload["profile"] = {"handle": "someone"}
    if mutation == "scope":
        payload["source_scope"] = "search"
    if mutation == "empty":
        payload["tweets"] = []
    if mutation == "unknown-time":
        payload["tweets"][0]["at"] = ""
    if mutation == "old":
        payload["tweets"][0]["at"] = "2026-09-01T00:00:00Z"
    run_posts(p, payload)
    assert not _gstate(p).get("tibo_delivered_ids")
    asyncio.run(
        p._deliver_notice(
            GID, "upstream_alert_keys", "alert", "", "alert", source_tweet_id=ID
        )
    )
    assert len(p.ctx.send.sent_messages) == 1


class FakeRender:
    def __init__(self, failure=False):
        self.htmls = []
        self.failure = failure

    async def html2png(self, html, **kwargs):
        self.htmls.append((html, kwargs))
        if self.failure:
            raise RuntimeError("renderer unavailable")
        return {"image_base64": PNG}


def wire_images(p, render_failure=False, image_result=True):
    renderer = FakeRender(render_failure)
    p.ctx.render = renderer
    images = []

    async def image(data, stream, **kwargs):
        images.append((stream, data))
        if isinstance(image_result, Exception):
            raise image_result
        return {"sent": image_result}

    p.ctx.send.image = image
    return renderer, images


@pytest.mark.parametrize(
    "failure", ["none", "render", "send-false", "send-raise", "invalid-image"]
)
def test_image_delivery_fallback_and_success_receipts(tmp_path, monkeypatch, failure):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    renderer, images = wire_images(
        p,
        failure == "render",
        RuntimeError("send failure")
        if failure == "send-raise"
        else failure != "send-false",
    )
    if failure == "invalid-image":

        async def invalid(*args, **kwargs):
            return {"image_base64": "bad"}

        renderer.html2png = invalid
    card = NoticeCard("Title", "English", "中文", tweet=True)
    asyncio.run(
        p._deliver_notice(
            GID, "notified_keys", "image:1", "identity", "fallback", card=card
        )
    )
    assert len(p.ctx.send.sent_messages) == (0 if failure == "none" else 1)
    assert len(images) == (0 if failure in {"render", "invalid-image"} else 1)
    assert "image:1" in _gstate(p)["notified_keys"]
    assert "identity" in _gstate(p)["delivered_notice_keys"]
    p._load_state()
    asyncio.run(
        p._deliver_notice(
            GID, "notified_keys", "image:1", "identity", "fallback", card=card
        )
    )
    assert len(p.ctx.send.sent_messages) == (0 if failure == "none" else 1)


def test_image_and_text_both_fail_leave_no_receipt(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    wire_images(p, image_result=False)

    async def fail(*args):
        return False

    p._send_group_text = fail
    asyncio.run(p._deliver_notice(GID, "notified_keys", "key", "identity", "text"))
    assert not _gstate(p)["notified_keys"]
    assert not _gstate(p).get("delivered_notice_keys")


def test_multi_group_single_render_and_fallback_only_failed_group(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, display_mode="image", group_ids=[GID, "100000002"])
    renderer, images = wire_images(p)

    async def image(data, stream, **kwargs):
        images.append((stream, data))
        return {"sent": stream.endswith(GID)}

    p.ctx.send.image = image
    card = NoticeCard("Title", "English", "中文", tweet=True)

    async def scenario():
        for gid in p._target_groups():
            await p._deliver_notice(
                gid, "notified_keys", "key", "identity", "text", card=card
            )

    asyncio.run(scenario())
    assert len(renderer.htmls) == 1
    assert len(images) == 2
    assert p.ctx.send.sent_messages == [("qq-group-100000002", "text")]
    for gid in p._target_groups():
        assert "key" in _gstate(p, gid)["notified_keys"]


@pytest.mark.parametrize(
    "quote", [None, {}, {"id": "1"}, {"text": ""}, {"text": "quoted text"}]
)
def test_quote_block_whole_translation_and_escaping(quote):
    card = NoticeCard(
        "Tibo 动态",
        "<script>bad()</script>\nEnglish second paragraph",
        "中文第一段\n中文第二段",
        tweet=True,
        quote=quote,
        quote_translation="引用完整中文",
    )
    html = build_card_html(card, NOW)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert (
        html.index("中文第一段")
        < html.index("中文第二段")
        < html.index("English second paragraph")
    )
    assert ('<aside class="quote">' in html) == bool(quote and quote.get("text"))
    assert "引用内容获取失败" not in html and "被引用原帖" not in html
    assert "2026-10-05 12:33" in html and "UTC+8" in html
    if quote and quote.get("text"):
        assert html.index("引用完整中文") < html.index("quoted text")
        assert "<strong>Tibo</strong>" in html  # main author only


def test_system_template_never_impersonates_tibo():
    html = build_card_html(
        NoticeCard("Codex Banked Reset 提醒", "System event", "系统通知"), NOW
    )
    assert "系统 / 上游通知" in html
    assert "thsottiaux" not in html and 'class="avatar"' not in html
    assert html.index("系统通知") < html.index("System event")


def test_quote_provider_and_translation_in_actual_tibo_image(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True, display_mode="image")
    renderer, images = wire_images(p)

    async def provider(*args):
        return {
            "tweet": {
                "text": "My own comment",
                "is_note_tweet": False,
                "quote": {
                    "text": "Quoted first\n\nQuoted second",
                    "author": {"name": "Other", "screen_name": "other"},
                },
            }
        }

    p._get_json = provider
    calls = []

    async def translate(tid, url, at, content):
        calls.append(content.text)
        return (
            "引用中文第一段\n\n引用中文第二段"
            if content.quote is None
            else "自己的完整中文"
        )

    p._llm_translate_content = translate
    run_posts(p, feed(post()))
    assert len(images) == 1 and not p.ctx.send.sent_messages
    html = renderer.htmls[0][0]
    assert '<aside class="quote">' in html and "@other" in html
    assert html.index("引用中文第二段") < html.index("Quoted first")
    assert len(calls) == 2


def test_state_additive_and_corrupt_fields_conservative():
    assert module._carry_validated_state(
        {"feed_baseline_done": True, "notified_keys": []}
    ) == {"feed_baseline_done": True, "notified_keys": []}
    carried = module._carry_validated_state(
        {
            "tibo_baseline_done": True,
            "tibo_seen_ids": ["1"],
            "tibo_delivered_ids": ["2"],
            "delivered_notice_keys": ["tweet:2"],
        }
    )
    assert carried["tibo_seen_ids"] == ["1"] and carried["tibo_delivered_ids"] == ["2"]
    bad = module._carry_validated_state(
        {"tibo_baseline_done": True, "tibo_seen_ids": "1", "tibo_delivered_ids": [1]}
    )
    assert not bad


@pytest.mark.parametrize("lane", ["tibo", "global", "banked", "push-banked", "confirm"])
def test_all_active_lanes_image_only(tmp_path, monkeypatch, lane):
    from test_delivery_identity import _feed, _forecast, GLOBAL_IDS, BANKED_ID, FIXTURES
    import json
    from test_watcher import BEIJING

    p = ready(
        tmp_path, monkeypatch, tibo_full_push=lane == "tibo", display_mode="image"
    )
    renderer, images = wire_images(p)
    monkeypatch.setattr(
        module, "_utcnow", lambda: datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    )

    async def scenario():
        if lane == "tibo":
            await p._process_tibo_posts(tibo_batch(feed(post())), [GID])
        elif lane == "global":
            await p._process_upstream_alert(
                _forecast(GLOBAL_IDS[1]), [GID], _feed(GLOBAL_IDS[1])
            )
        elif lane == "banked":
            await p._process_feed_signals(_feed(BANKED_ID), [GID], BEIJING)
        elif lane == "push-banked":
            payload = json.loads((FIXTURES / "push_notification.json").read_text())
            await p._process_push_banked(payload, [GID], _feed(BANKED_ID))
        else:
            from test_watcher import _confirmation_feed

            payload = _confirmation_feed()
            event_id = payload["events"][0]["id"]
            payload["events"] = payload["events"][:1]
            monkeypatch.setattr(
                module, "_utcnow", lambda: datetime(2026, 9, 8, 1, tzinfo=timezone.utc)
            )
            _gstate(p)["upstream_alert_tweet_ids"] = [event_id]
            await p._process_feed_signals(payload, [GID], BEIJING)
        await _drain_inflight(p)

    # Fresh known global fixture outside 48h: mirror ignores feed age guard.
    asyncio.run(scenario())
    assert len(images) == 1
    assert not p.ctx.send.sent_messages
    html = renderer.htmls[0][0]
    if lane in {"banked", "push-banked", "confirm"}:
        assert "系统 / 上游通知" in html
    else:
        assert "@thsottiaux" in html


def test_quote_from_feed_without_provider_and_missing_quote_hidden(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True, display_mode="image")
    renderer, _ = wire_images(p)
    run_posts(p, feed(post(quote={"text": "Available quoted body"})))
    assert "Available quoted body" in renderer.htmls[0][0]
    other = "2106845241357824206"
    run_posts(
        p,
        feed(
            post(id=other, quote={"id": "123", "url": "https://x.com/other/status/123"})
        ),
    )
    assert '<aside class="quote">' not in renderer.htmls[-1][0]
    assert other in _gstate(p)["tibo_delivered_ids"]


def test_cancelled_tibo_inflight_has_no_receipts_and_alert_can_retry(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)

    async def scenario():
        entered = asyncio.Event()

        async def blocked(*args):
            entered.set()
            await asyncio.Event().wait()

        p._enrich_content = blocked
        await p._process_tibo_posts(tibo_batch(feed(post())), [GID])
        await entered.wait()
        await p._cancel_inflight()
        assert not p._inflight
        assert not _gstate(p).get("tibo_delivered_ids")

        async def content(*args):
            return module.TweetContent("text", "feed", "full")

        p._enrich_content = content
        await p._process_upstream_alert(forecast(), [GID], feed(post()))
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert len(p.ctx.send.sent_messages) == 1


def test_removed_group_during_image_send_not_recreated(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    wire_images(p)

    async def send(data, stream, **kwargs):
        p.config.watcher.group_ids = []
        p._reconcile_groups()
        return {"sent": True}

    p.ctx.send.image = send
    asyncio.run(p._deliver_notice(GID, "notified_keys", "key", "identity", "text"))
    assert GID not in p._state["groups"]


def test_full_push_without_timeline_retains_actual_upstream_system_event(
    tmp_path, monkeypatch
):
    import json
    from test_delivery_identity import FIXTURES, BANKED_ID, _feed

    p = ready(tmp_path, monkeypatch, tibo_full_push=True, display_mode="image")
    renderer, images = wire_images(p)
    payload = json.loads((FIXTURES / "push_notification.json").read_text())
    monkeypatch.setattr(
        module, "_utcnow", lambda: datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    )

    async def scenario():
        await p._process_tibo_posts(tibo_batch(None), [GID])
        await p._process_push_banked(payload, [GID], _feed(BANKED_ID))
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert len(images) == 1
    assert "系统 / 上游通知" in renderer.htmls[0][0]
    assert "@thsottiaux" not in renderer.htmls[0][0]
    assert _gstate(p)["push_banked_keys"]


@pytest.mark.parametrize("lane", ["global-feed", "banked-feed", "push-banked"])
def test_actual_existing_pipelines_absorb_tibo_delivery(tmp_path, monkeypatch, lane):
    from test_watcher import _banked_fresh_feed, BEIJING
    from test_delivery_identity import _feed, GLOBAL_IDS

    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    payload = _feed(GLOBAL_IDS[1]) if lane == "global-feed" else _banked_fresh_feed()
    payload.update(profile={"handle": "thsottiaux"}, source_scope="timeline")
    payload["tweets"][0]["at"] = NOW.isoformat()
    payload["events"][0]["announced_at"] = NOW.isoformat()
    tid = payload["tweets"][0]["id"]

    async def scenario():
        await p._process_tibo_posts(tibo_batch(payload), [GID])
        await _drain_inflight(p)
        if lane == "push-banked":
            alert = {
                "route": "banked",
                "alert": {
                    "id": tid,
                    "kind": "banked",
                    "url": f"https://x.com/thsottiaux/status/{tid}",
                    "banked_state": "announced",
                },
            }
            await p._process_push_banked(alert, [GID], payload)
        else:
            await p._process_feed_signals(payload, [GID], BEIJING)
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert len(p.ctx.send.sent_messages) == 1
    assert p.ctx.send.sent_messages[0][1].startswith("Tibo 动态")
    assert tid in _gstate(p)["tibo_delivered_ids"]
    if lane == "push-banked":
        assert f"push-banked:{tid}" in _gstate(p)["push_banked_keys"]
    else:
        assert _gstate(p)["notified_keys"]


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"is_reply": True, "replying_to": "thsottiaux"}, True),
        ({"is_reply": True, "replying_to": "@Thsottiaux"}, True),
        ({"is_reply": True, "replying_to": "  @Thsottiaux  "}, True),
        ({"is_reply": True, "replying_to": "someone"}, False),
        ({"is_reply": True}, False),
        ({"is_reply": True, "in_reply_to_tweet_id": "123"}, False),
        (
            {
                "is_reply": True,
                "replying_to": "thsottiaux",
                "referenced_tweets": [{"type": "replied_to", "id": "123"}],
            },
            True,
        ),
        ({"is_reply": True, "replying_to": "thsottiaux", "is_retweet": True}, False),
        (
            {
                "is_reply": True,
                "replying_to": "thsottiaux",
                "referenced_tweets": [{"type": "retweeted", "id": "123"}],
            },
            False,
        ),
    ],
)
def test_self_reply_regression_and_full_push_dispatch(
    tmp_path, monkeypatch, metadata, expected
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True)
    # Actual counterexample identity/reply metadata supplied in review;
    # body/timestamp remain synthetic to exercise fresh-post dispatch offline.
    tweet_id = "2104467346945675347"
    item = post(id=tweet_id, **metadata)
    assert module.is_tibo_main_post(item) is expected
    run_posts(p, feed(item))
    assert len(p.ctx.send.sent_messages) == int(expected)
    assert (tweet_id in _gstate(p).get("tibo_delivered_ids", [])) is expected


@pytest.mark.parametrize("mode", ["text", "image"])
@pytest.mark.parametrize("secondary_note", [None, False])
def test_quote_enrichment_keeps_best_primary_and_limits_text_latency(
    tmp_path, monkeypatch, mode, secondary_note
):
    p = ready(tmp_path, monkeypatch, display_mode=mode)
    primary = ("Provider A complete primary. " * 20).strip()
    quote = {
        "text": "Quote body from provider B",
        "url": "https://x.com/other/status/123",
    }
    calls = []

    async def provider(url, timeout):
        calls.append(url)
        if "fxtwitter" in url:
            return {"tweet": {"text": primary, "is_note_tweet": True}}
        return {
            "text": "Short excerpt",
            "is_note_tweet": secondary_note,
            "quote": quote,
        }

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID, feed(post())))
    assert result.text == primary and result.source == "fxtwitter"
    assert result.completeness == "full"
    assert result.quote == (quote if mode == "image" else None)
    assert len(calls) == (2 if mode == "image" else 1)


@pytest.mark.parametrize("mode", ["text", "image"])
def test_quote_enrichment_survives_better_primary_without_quote(
    tmp_path, monkeypatch, mode
):
    p = ready(tmp_path, monkeypatch, display_mode=mode)
    primary = ("Provider B complete primary. " * 20).strip()
    quote = {"text": "Quote obtained earlier from provider A"}

    async def provider(url, timeout):
        if "fxtwitter" in url:
            return {"tweet": {"text": "Excerpt", "is_note_tweet": True, "quote": quote}}
        return {"text": primary}

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID, feed(post())))
    assert result.text == primary and result.source == "vxtwitter"
    assert result.quote == quote


def test_quote_enrichment_survives_feed_primary_selection(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    quote = {"text": "Provider quote retained across feed selection"}

    async def provider(url, timeout):
        return (
            {"tweet": {"text": "Excerpt", "quote": quote}}
            if "fxtwitter" in url
            else None
        )

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID, feed(post(text="Longer feed primary"))))
    assert result.source == "feed" and result.text == "Longer feed primary"
    assert result.quote == quote


@pytest.mark.parametrize("second", ["quote-only", "unavailable", "invalid-quote"])
def test_quote_only_or_failed_provider_keeps_full_primary(
    tmp_path, monkeypatch, second
):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    quote = {"text": "Quote with no second-provider primary body"}

    async def provider(url, timeout):
        if "fxtwitter" in url:
            return {"tweet": {"text": "Complete primary", "is_note_tweet": False}}
        if second == "unavailable":
            raise RuntimeError("Quote provider unavailable")
        return {"quote": quote if second == "quote-only" else {"id": "123"}}

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID, feed(post())))
    assert result.text == "Complete primary" and result.source == "fxtwitter"
    assert result.quote == (quote if second == "quote-only" else None)


def test_feed_quote_avoids_extra_provider_request_after_full_primary(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    quote = {"text": "Available feed quote"}
    calls = []

    async def provider(url, timeout):
        calls.append(url)
        return {"tweet": {"text": "Full primary", "is_note_tweet": False}}

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID, feed(post(quote=quote))))
    assert result.text == "Full primary" and result.quote == quote
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["tweet", "quote", "system"])
def test_image_html_has_no_independent_source_urls(kind):
    main_url = f"https://x.com/thsottiaux/status/{ID}"
    quoted_url = "https://x.com/other/status/123"
    source_url = "https://codex-reset.com/banked-reset"
    card = NoticeCard(
        "Title",
        "English body",
        "中文正文",
        url=source_url if kind == "system" else main_url,
        tweet=kind != "system",
    )
    if kind == "quote":
        card.quote = {"text": "Quoted body", "url": quoted_url}
        card.quote_translation = "引用中文"
    html = build_card_html(card, NOW)
    assert ".source" not in html and 'class="source"' not in html
    assert main_url not in html and quoted_url not in html and source_url not in html
    assert "English body" in html and "中文正文" in html
    assert card.url == (source_url if kind == "system" else main_url)
    if kind == "quote":
        assert '<aside class="quote">' in html and "Quoted body" in html
        assert card.quote["url"] == quoted_url


@pytest.mark.parametrize("kind", ["tweet", "quote", "system"])
def test_image_body_preserves_urls_and_following_chinese_text(kind):
    from html import escape

    text = "详情见 https://example.com/test，然后继续 & <原文>"
    translation = "译文见 https://example.com/translation，然后继续 & <译文>"
    card = NoticeCard("Title", text, translation, tweet=kind != "system")
    if kind == "quote":
        card.quote = {"text": text}
        card.quote_translation = translation
    html = build_card_html(card, NOW)
    count = 2 if kind == "quote" else 1
    assert html.count(f'<div class="body en" lang="en">{escape(text)}</div>') == count
    assert (
        html.count(f'<div class="body zh" lang="zh">{escape(translation)}</div>')
        == count
    )
    assert html.index(escape(translation)) < html.index(escape(text))


@pytest.mark.parametrize("mode", ["text", "image"])
def test_original_links_survive_text_and_image_failure_fallback(
    tmp_path, monkeypatch, mode
):
    p = ready(tmp_path, monkeypatch, tibo_full_push=True, display_mode=mode)
    if mode == "image":
        wire_images(p, render_failure=True)
    run_posts(p, feed(post()))
    assert len(p.ctx.send.sent_messages) == 1
    assert (
        f"原帖：https://x.com/thsottiaux/status/{ID}" in p.ctx.send.sent_messages[0][1]
    )
    assert ID in _gstate(p)["tibo_delivered_ids"]


@pytest.mark.parametrize("mode", ["text", "image"])
def test_system_source_link_survives_text_and_image_send_failure(
    tmp_path, monkeypatch, mode
):
    import json
    from test_delivery_identity import FIXTURES, BANKED_ID, _feed

    p = ready(tmp_path, monkeypatch, display_mode=mode)
    if mode == "image":
        wire_images(p, image_result=False)
    payload = json.loads((FIXTURES / "push_notification.json").read_text())

    async def scenario():
        await p._process_push_banked(payload, [GID], _feed(BANKED_ID))
        await _drain_inflight(p)

    asyncio.run(scenario())
    assert len(p.ctx.send.sent_messages) == 1
    assert (
        "来源：https://codex-reset.com/banked-reset" in p.ctx.send.sent_messages[0][1]
    )
    assert _gstate(p)["push_banked_keys"] == [f"push-banked:{BANKED_ID}"]
