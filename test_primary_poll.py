"""Current Tweet poll golden; transport, receipt and quoted-poll boundaries."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import plugin as module
from notice_card import build_card_html
from test_tibo_render import feed, post, ready, run_posts, wire_images
from test_watcher import _gstate

FIXTURE = Path(__file__).parent / "tests/fixtures/primary-poll/fxtwitter.json"
NOW = datetime(2026, 10, 11, 6, 40, 49, tzinfo=timezone.utc)
ID = "2109152716954222849"


def payload():
    return json.loads(FIXTURE.read_text())


def primary_poll():
    return module._normalize_poll(payload()["tweet"]["poll"])


def watcher(tmp_path, monkeypatch, **kwargs):
    p = ready(tmp_path, monkeypatch, **kwargs)
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    return p


def wire_provider(p, fx, vx=None):
    calls = []

    async def get(url, timeout):
        calls.append(url)
        return fx if "fxtwitter" in url else vx

    p._get_json = get
    return calls


def vx_payload():
    # Verified public Vx option schema, synthetic current-Tweet snapshot.
    return {
        "tweetID": ID,
        "text": "Short",
        "pollData": {
            "options": [
                {"name": "Older A", "votes": 10, "percent": 25},
                {"name": "Older B", "votes": 30, "percent": 75},
            ]
        },
    }


def test_real_primary_poll_provider_enrichment_card_html(tmp_path, monkeypatch):
    p = watcher(tmp_path, monkeypatch, display_mode="image")
    raw = payload()
    parsed = module._content_from_provider_payload(raw, "fxtwitter")
    assert parsed.poll == primary_poll() and parsed.quote is None
    wire_provider(p, raw)

    async def scenario():
        content = await p._enrich_content(ID)
        assert content.poll == parsed.poll and content.quote is None
        card = await p._notice_card(
            "Tibo 动态",
            raw["tweet"]["url"],
            content,
            "Pro 用户们，你们更希望拥有",
            "2026-10-11T05:22:48Z",
            True,
        )
        assert card.poll == content.poll and card.quote is None
        return build_card_html(card, NOW)

    html = asyncio.run(scenario())
    assert (
        '<aside class="quote">' not in html and html.count('class="poll-option"') == 3
    )
    for expected in [
        "33.8%",
        "37.3%",
        "28.8%",
        "60,812 票",
        "进行中",
        "2026-10-12 01:22",
        "2026-10-11 13:22",
    ]:
        assert expected in html
    assert html.index('class="poll"') < html.index("</section>")
    assert "✓" not in html


@pytest.mark.parametrize("secondary", ["vx", "feed", "discovery"])
def test_incomplete_primary_poll_repairs_atomically_without_replacing_body(
    tmp_path, monkeypatch, secondary
):
    p = watcher(tmp_path, monkeypatch, display_mode="image")
    fx = payload()
    fx["tweet"]["poll"] = {"total_votes": 999, "closed": True}
    # A complete quote must not hide an incomplete primary poll.
    fx["tweet"]["quote"] = {
        "id": "123",
        "text": "Quoted text",
        "poll": payload()["tweet"]["poll"],
    }
    vx = vx_payload()
    hint = post(id=ID, at="2026-10-11T05:22:48Z")
    hint["poll"] = vx["pollData"]
    if secondary == "discovery":
        from tibo_discovery import fx_candidate

        hint = deepcopy(payload()["tweet"])
        hint.update(type="status", replying_to=None)
        hint["poll"] = vx["pollData"]
        source = fx_candidate(hint, "fxtwitter")
    else:
        source = feed(hint) if secondary == "feed" else None
    calls = wire_provider(p, fx, vx if secondary == "vx" else None)
    result = asyncio.run(p._enrich_content(ID, source))
    expected = module._normalize_poll(vx["pollData"])
    assert result.source == "fxtwitter" and result.text == fx["tweet"]["text"]
    assert result.poll == expected  # 40 votes, unknown status/deadline: no Fx values.
    assert result.quote["poll"] == primary_poll()
    assert len(calls) == (2 if secondary == "vx" else 1)

    card = asyncio.run(
        p._notice_card("Tibo 动态", "", result, "", NOW.isoformat(), True)
    )
    html = build_card_html(card, NOW)
    primary, quoted = html.split('<aside class="quote">')
    assert "Older A" in primary and "5h limits, 2X week usage" not in primary
    assert "5h limits, 2X week usage" in quoted and "Older A" not in quoted


@pytest.mark.parametrize("source", ["vx", "feed"])
def test_complete_primary_snapshot_is_not_mixed_with_secondary(
    tmp_path, monkeypatch, source
):
    p = watcher(tmp_path, monkeypatch, display_mode="image")
    hint = post(id=ID, poll=vx_payload()["pollData"])
    wire_provider(p, payload(), vx_payload() if source == "vx" else None)
    result = asyncio.run(
        p._enrich_content(ID, feed(hint) if source == "feed" else None)
    )
    assert result.poll == primary_poll()


def test_primary_poll_survives_better_body_and_all_provider_failure(
    tmp_path, monkeypatch
):
    p = watcher(tmp_path, monkeypatch, display_mode="image")
    fx = payload()
    fx["tweet"]["is_note_tweet"] = True  # First body is an incomplete note excerpt.
    vx = {"tweetID": ID, "text": "Full note paragraph. " * 40}
    wire_provider(p, fx, vx)
    result = asyncio.run(p._enrich_content(ID))
    assert result.source == "vxtwitter" and result.text == vx["text"].strip()
    assert result.poll == primary_poll()
    wire_provider(p, None)
    hint = post(id=ID, text=payload()["tweet"]["text"], poll=payload()["tweet"]["poll"])
    result = asyncio.run(p._enrich_content(ID, feed(hint)))
    assert result.source == "feed" and result.poll == primary_poll()


@pytest.mark.parametrize(
    "stale, hint_id, provider_id", [(True, ID, "other"), (False, "other", "other")]
)
def test_unrelated_or_stale_snapshots_are_not_attached(
    tmp_path, monkeypatch, stale, hint_id, provider_id
):
    p = watcher(tmp_path, monkeypatch)
    fx = payload()
    fx["tweet"]["id"] = provider_id
    hint = feed(post(id=hint_id, poll=payload()["tweet"]["poll"]))
    hint["stale"] = stale
    wire_provider(p, fx)
    assert asyncio.run(p._enrich_content(ID, hint)).poll is None


@pytest.mark.parametrize("include_poll", [False, True])
def test_text_poll_never_adds_provider_or_llm_requests(
    tmp_path, monkeypatch, include_poll
):
    p = watcher(tmp_path, monkeypatch)
    raw = payload()
    if not include_poll:
        raw["tweet"].pop("poll")
    p.config.llm.enabled = True

    async def forbid(*args):
        pytest.fail("Text poll must not translate its options")

    p._llm_translate_content = forbid
    calls = wire_provider(p, raw, vx_payload())
    content = asyncio.run(p._enrich_content(ID))
    assert len(calls) == 1
    assert content.poll == (primary_poll() if include_poll else None)
    text = module.build_full_notice(
        "Tibo 动态",
        raw["tweet"]["url"],
        content.text,
        content.completeness,
        poll=content.poll,
    )
    assert ("5h limits, 2X week usage" in text) is include_poll
    assert (
        asyncio.run(p._notice_card("Title", "", content, "", NOW.isoformat(), True))
        is None
    )


@pytest.mark.parametrize(
    "translation", ["success", "none", "exception", "missing-emoji"]
)
def test_primary_option_translation_preserves_snapshot(
    tmp_path, monkeypatch, translation
):
    p = watcher(tmp_path, monkeypatch, display_mode="image")
    p.config.llm.enabled = True
    content = module._content_from_provider_payload(payload(), "fxtwitter")
    content.poll["options"][0]["label"] = "👌 good day"
    original = deepcopy(content.poll)

    async def translate(tid, url, at, option):
        if translation == "exception":
            raise RuntimeError("LLM unavailable")
        return {
            "success": "👌（美好的一天）",
            "none": None,
            "missing-emoji": "美好的一天",
        }[translation]

    p._llm_translate_content = translate
    card = asyncio.run(p._notice_card("Title", "", content, "", NOW.isoformat(), True))
    assert content.poll == original
    untranslated = deepcopy(card.poll)
    for option in untranslated["options"]:
        option.pop("translation", None)
    assert untranslated == original
    assert bool(card.poll["options"][0].get("translation")) is (
        translation == "success"
    )


@pytest.mark.parametrize(
    "mode, failure",
    [("text", "none"), ("image", "none"), ("image", "render"), ("image", "send")],
)
def test_actual_pipeline_text_or_image_failure_keeps_poll_and_receipts(
    tmp_path, monkeypatch, mode, failure
):
    states = []
    for include in [True, False]:
        p = watcher(
            tmp_path / str(include), monkeypatch, display_mode=mode, tibo_full_push=True
        )
        renderer, images = wire_images(
            p, render_failure=failure == "render", image_result=failure != "send"
        )
        raw = payload()
        if not include:
            raw["tweet"].pop("poll")
        calls = wire_provider(p, raw)
        data = feed(post(id=ID, text=raw["tweet"]["text"], at="2026-10-11T05:22:48Z"))
        run_posts(p, data)
        states.append(deepcopy(_gstate(p)))
        assert len(calls) == (1 if mode == "text" else 2)
        assert len(p.ctx.send.sent_messages) == (
            0 if mode == "image" and failure == "none" else 1
        )
        if p.ctx.send.sent_messages:
            message = p.ctx.send.sent_messages[0][1]
            assert ("5h limits, 2X week usage" in message) is include
            assert ("60,812 票" in message) is include
        if mode == "image":
            assert len(renderer.htmls) == 1
            assert ('class="poll-option"' in renderer.htmls[0][0]) is include
            assert len(images) == (0 if failure == "render" else 1)
        p._load_state()
        run_posts(p, data)
        assert _gstate(p) == states[-1] and len(calls) == (1 if mode == "text" else 2)
    assert states[0] == states[1] and states[0]["tibo_delivered_ids"] == [ID]
    assert "poll" not in json.dumps(states[0])


def test_text_omits_unknown_statistics_and_keeps_explicit_unavailability(monkeypatch):
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    normalized = module._normalize_poll({"options": [{"label": "A"}, {"label": "B"}]})
    assert module._poll_text(normalized) == "投票：\n• A\n• B"
    assert module._poll_text(module._normalize_poll({})) == "投票内容暂不可用"
    text = module._poll_text(primary_poll())
    assert (
        "33.8%" in text and "60,812 票 · 进行中 · 截止 2026-10-12 01:22 UTC+8" in text
    )
    later = datetime(2026, 10, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utcnow", lambda: later)
    assert "已结束" in module._poll_text(primary_poll())
