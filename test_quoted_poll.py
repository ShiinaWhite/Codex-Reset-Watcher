"""Public 2107578625419866469 / quote 2107576143285219799 schema regressions."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import plugin as module
from notice_card import NoticeCard, build_card_html
from test_tibo_render import ready, feed, post, run_posts, wire_images
from test_watcher import _gstate

FIXTURES = Path(__file__).parent / "tests/fixtures/quoted-poll"
NOW = datetime(2026, 10, 7, 5, tzinfo=timezone.utc)
ID = "2107578625419866469"
QID = "2107576143285219799"


def payload(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def quoted(name="fxtwitter"):
    data = payload(name)
    return module._quote_from_tweet(data["tweet"] if name == "fxtwitter" else data)


def card(quote=None):
    return NoticeCard(
        "Tibo 动态",
        "Four updates or a reset. Or both. How was day 2.",
        "四次更新，或者一次重置。或者两者都要。第二天过得怎么样？",
        tweet=True,
        quote=quote if quote is not None else quoted(),
        quote_translation="投票",
    )


def test_real_fx_schema_and_snapshot_no_viewer_selection(monkeypatch):
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    q = quoted()
    assert q["id"] == QID and q["text"] == "Vote"
    assert q["author"]["screen_name"] == "thsottiaux"
    poll = q["poll"]
    assert [o["label"] for o in poll["options"]] == [
        "👌(good day)",
        "🫨 (needs a reset)",
    ]
    assert [o["votes"] for o in poll["options"]] == [17902, 56663]
    assert [o["percentage"] for o in poll["options"]] == [24, 76]
    assert poll["total_votes"] == 74565 and poll["closed"] is True
    assert poll["ends_at"] == "2026-10-07T00:58:03+00:00"
    html = build_card_html(card(q), NOW)
    for text in [
        "24.0%",
        "76.0%",
        "74,565 票",
        "已结束",
        "10-07 04:58",
        "2026-10-07 08:58",
        "@thsottiaux",
    ]:
        assert text in html
    assert "width:24%" in html and "width:76%" in html
    assert "✓" not in html and "checked" not in html


def test_real_vx_schema_is_older_snapshot_with_unknown_end():
    q = quoted("vxtwitter")
    assert q["id"] == QID and q["created_timestamp"] == 1791320284
    assert q["author"] == {"name": "Tibo", "screen_name": "thsottiaux"}
    poll = q["poll"]
    assert [o["percentage"] for o in poll["options"]] == [24.65, 75.35]
    assert [o["votes"] for o in poll["options"]] == [989, 3023]
    assert poll["total_votes"] == 4012  # Explicitly derived from all option counts.
    assert poll["closed"] is None and poll["ends_at"] is None
    html = build_card_html(card(q), NOW)
    assert "4,012 票" in html and "状态未知" in html and "24.6%" in html
    assert "✓" not in html
    merged = module._merge_quote(q, quoted())
    assert merged["poll"] == quoted()["poll"]  # Atomic Fx snapshot, not mixed values.


@pytest.mark.parametrize("closed", [True, False])
def test_active_and_closed_poll(closed, monkeypatch):
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    raw = deepcopy(payload("fxtwitter")["tweet"])
    raw["quote"]["poll"]["ends_at"] = "2026-10-07T08:00:00Z"
    raw["quote"]["poll"].pop("time_left_en")
    raw["quote"]["poll"]["closed"] = closed
    html = build_card_html(card(module._quote_from_tweet(raw)), NOW)
    assert ("已结束" if closed else "进行中") in html
    assert ("剩余 3 小时" in html) is (not closed)


def test_placeholder_only_when_poll_explicitly_known():
    known = module._quote_from_tweet({"quote": {"text": "Vote", "poll": {}}})
    html = build_card_html(card(known), NOW)
    assert "投票内容暂不可用" in html
    assert '<div class="body en" lang="en">Vote</div>' not in html
    assert 'class="poll-option"' not in html and 'class="poll-percentage"' not in html
    unknown = module._quote_from_tweet({"quote": {"text": "Vote"}})
    html = build_card_html(card(unknown), NOW)
    assert "投票内容暂不可用" not in html and ">Vote</div>" in html
    assert 'class="poll"' not in html


def test_poll_only_quote_without_text_and_missing_quote_stays_hidden():
    q = quoted()
    q.pop("text")
    q = module._quote_from_tweet({"quote": q})
    assert 'class="poll"' in build_card_html(card(q), NOW)
    html = build_card_html(card({"id": QID}), NOW)
    assert '<aside class="quote">' not in html


def test_poll_html_escapes_labels_and_translation():
    q = quoted()
    q["poll"]["options"][0].update(
        label='<img onerror="evil()">', translation="<script>bad</script>"
    )
    html = build_card_html(card(q), NOW)
    assert "&lt;script&gt;bad" in html and "&lt;img" in html
    assert "<script>bad" not in html and "<img onerror" not in html


@pytest.mark.parametrize("secondary", ["provider", "feed"])
def test_poll_enriched_independently_of_best_primary(tmp_path, monkeypatch, secondary):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    a = payload("fxtwitter")
    a["tweet"]["quote"].pop("poll")
    b = payload("vxtwitter")
    b["text"] = "Short"
    calls = []

    async def provider(url, timeout):
        calls.append(url)
        return a if "fxtwitter" in url else (b if secondary == "provider" else None)

    p._get_json = provider
    item = payload("feed-target")
    if secondary == "feed":
        item["quote"] = payload("fxtwitter")["tweet"]["quote"]
    result = asyncio.run(p._enrich_content(ID, feed(item)))
    assert result.text == a["tweet"]["text"] and result.source == "fxtwitter"
    assert result.quote["id"] == QID and module._poll_complete(result.quote)
    assert result.quote["poll"]["total_votes"] == (
        4012 if secondary == "provider" else 74565
    )
    assert len(calls) == (2 if secondary == "provider" else 1)


def test_poll_retained_across_primary_switch_and_provider_failure(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    a = payload("fxtwitter")
    a["tweet"].update(text="Excerpt", is_note_tweet=True)

    async def provider(url, timeout):
        return a if "fxtwitter" in url else {"text": "Better primary " * 40}

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID))
    assert result.source == "vxtwitter" and result.quote["poll"] == quoted()["poll"]

    async def failure(url, timeout):
        if "fxtwitter" in url:
            return a
        raise RuntimeError("Provider failure")

    p._get_json = failure
    assert asyncio.run(p._enrich_content(ID)).quote["poll"] == quoted()["poll"]


def test_quote_id_conflict_does_not_mix_poll():
    a = {"id": "other", "text": "Other quote"}
    assert "poll" not in module._merge_quote(a, quoted())


def test_text_mode_has_no_extra_poll_provider_latency(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch)
    calls = []
    a = payload("fxtwitter")
    a["tweet"]["quote"].pop("poll")

    async def provider(url, timeout):
        calls.append(url)
        return a

    p._get_json = provider
    result = asyncio.run(p._enrich_content(ID))
    assert result.text == a["tweet"]["text"] and len(calls) == 1


@pytest.mark.parametrize(
    "translation", ["success", "none", "exception", "missing-emoji"]
)
def test_option_translation_is_optional_and_never_rewrites_statistics(
    tmp_path, monkeypatch, translation
):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    p.config.llm.enabled = True
    inputs = []

    async def translate(tid, url, at, content):
        inputs.append(content.text)
        if content.text == "Vote":
            return "投票"
        if translation == "exception":
            raise RuntimeError("LLM down")
        if translation == "none":
            return None
        if translation == "missing-emoji":
            return "美好的一天"
        return "👌（美好的一天）" if "good day" in content.text else "🫨（需要重置）"

    p._llm_translate_content = translate
    c = module.TweetContent("Main", "fxtwitter", "full")
    c.quote = quoted()
    rendered = asyncio.run(
        p._notice_card("Tibo 动态", "", c, "主帖", NOW.isoformat(), True)
    )
    html = build_card_html(rendered, NOW)
    assert "👌 good day" in html and "🫨 needs a reset" in html
    assert "24.0%" in html and "76.0%" in html and "74,565 票" in html
    assert ("美好的一天" in html) is (translation == "success")
    assert "Vote" in inputs and all(
        "74565" not in text and "ends_at" not in text for text in inputs
    )
    assert "translation" not in c.quote["poll"]["options"][0]


def test_chinese_and_emoji_labels_do_not_duplicate_translation(tmp_path, monkeypatch):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    p.config.llm.enabled = True
    poll = quoted()["poll"]
    poll["options"][0]["label"] = "已经很好👌"
    poll["options"][1]["label"] = "🫨"

    async def forbid(*args):
        raise AssertionError("No translation needed")

    p._llm_translate_content = forbid
    assert asyncio.run(p._translate_poll_options(poll, "fxtwitter")) == poll


def test_poll_does_not_change_alert_identity_receipts_or_dedup(tmp_path, monkeypatch):
    states = []
    for include in [True, False]:
        p = ready(
            tmp_path / str(include),
            monkeypatch,
            display_mode="image",
            tibo_full_push=True,
        )
        wire_images(p)
        raw = payload("fxtwitter")
        if not include:
            raw["tweet"]["quote"].pop("poll")

        async def provider(url, timeout):
            return raw if "fxtwitter" in url else None

        p._get_json = provider
        item = post(id=ID)
        run_posts(p, feed(item))
        states.append(deepcopy(_gstate(p)))
        run_posts(p, feed(item))
        assert _gstate(p) == states[-1]
    assert states[0] == states[1]
    assert states[0]["tibo_delivered_ids"] == [ID]
    assert f"tweet:{ID}" in states[0]["delivered_notice_keys"]
    assert "poll" not in json.dumps(states[0])


def test_invalid_stats_are_not_fabricated_and_naive_deadline_is_unknown():
    q = module._normalize_poll(
        {
            "ends_at": "2026-10-07T08:00:00",
            "choices": [
                {"label": "A", "percentage": float("nan"), "count": -1},
                {"label": "B", "percentage": 200, "count": False},
            ],
        }
    )
    assert q["ends_at"] is None and q["closed"] is None and q["total_votes"] is None
    assert all(o["percentage"] is None and o["votes"] is None for o in q["options"])


def test_known_incomplete_poll_still_seeks_provider_when_feed_has_only_text(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, display_mode="image")
    a = payload("fxtwitter")
    a["tweet"]["quote"]["poll"] = {}

    async def provider(url, timeout):
        return a if "fxtwitter" in url else payload("vxtwitter")

    p._get_json = provider
    item = payload("feed-target")
    item["quote"] = {"id": QID, "text": "Vote"}
    result = asyncio.run(p._enrich_content(ID, feed(item)))
    assert result.quote["poll"]["total_votes"] == 4012


def test_invalid_option_does_not_produce_partial_poll_or_derived_total():
    poll = module._normalize_poll(
        {
            "options": [
                {"label": "A", "votes": 5},
                {"label": "B", "votes": 5},
                {"votes": 2},
            ]
        }
    )
    assert poll["options"] == [] and poll["total_votes"] is None


def test_all_providers_fail_but_real_schema_feed_fallback_keeps_poll(
    tmp_path, monkeypatch
):
    p = ready(tmp_path, monkeypatch, display_mode="image")

    async def unavailable(*args):
        return None

    p._get_json = unavailable
    item = payload("feed-target")
    item["quote"] = payload("fxtwitter")["tweet"]["quote"]
    result = asyncio.run(p._enrich_content(ID, feed(item)))
    assert result.source == "feed" and result.quote["poll"] == quoted()["poll"]


@pytest.mark.parametrize(
    "label, translation, expected",
    [
        ("🤌 good day", "🤌（美好的一天）", "🤌 good day（美好的一天）"),
        ("👌(Good Day)", "👌（美好的一天）", "👌 Good Day（美好的一天）"),
        ("🫨 Needs a Reset", "🫨 (需要重置)", "🫨 Needs a Reset（需要重置）"),
        ("👌🏽 good day", "👌🏽（很好）", "👌🏽 good day（很好）"),
        ("👩‍💻 Good Day", "👩‍💻（美好的一天）", "👩‍💻 Good Day（美好的一天）"),
        ("Good Day", "", "Good Day"),
        ("👌 (good day)", "", "👌 good day"),
        ("(Good Day)", "很好", "Good Day（很好）"),
        ("👌（Good Day）", "很好", "👌 Good Day（很好）"),
        ("Good (day)", "很好", "Good (day)（很好）"),
        ("👌 (good) (day)", "很好", "👌 (good) (day)（很好）"),
        ("Good Day", "（）", "Good Day"),
        ("👌 Good Day", "👌", "👌 Good Day"),
        ("已经很好👌", "已经很好👌", "已经很好👌"),
    ],
)
def test_option_single_caption_preserves_original_case_and_one_emoji(
    label, translation, expected
):
    from html import unescape
    import re
    from notice_card import _poll_option_label

    html = _poll_option_label(label, translation)
    visible = unescape(re.sub(r"<[^>]*>", "", html))
    assert visible == expected
    assert "（（））" not in visible


def test_percentage_has_one_decimal_but_bar_preserves_snapshot_precision():
    q = quoted()
    q["poll"]["options"][0]["percentage"] = 24.64
    q["poll"]["options"][1]["percentage"] = 75.36
    html = build_card_html(card(q), NOW)
    assert ">24.6%</span>" in html and ">75.4%</span>" in html
    assert "width:24.64%" in html and "width:75.36%" in html
    assert 'class="poll-option-head"' not in html
    assert html.index('class="poll-track"') < html.index('class="poll-percentage"')


@pytest.mark.parametrize(
    "text, translation",
    [("Vote", "投票"), ("Day 2", "第二天"), ("Vote", ""), ("Vote", "Vote")],
)
def test_short_poll_heading_inline(text, translation):
    q = quoted()
    q["text"] = text
    before = deepcopy(q)
    rendered = card(q)
    rendered.quote_translation = translation
    html = build_card_html(rendered, NOW)
    expected = f'<span class="body en" lang="en">{text}</span>'
    if translation and translation != text:
        expected += f'<span class="body zh" lang="zh">（{translation}）</span>'
    assert f'<div class="body quote-title">{expected}</div>' in html
    assert q == before  # Display cleanup never modifies provider snapshot data.
    assert 'class="poll-footer"' in html


@pytest.mark.parametrize(
    "text, translation, has_poll",
    [
        ("A long poll question " * 4, "完整问题译文", True),
        ("Vote\nNow", "现在\n投票", True),
        ("Vote", "投票", False),
    ],
)
def test_other_quote_bodies_keep_translation_blocks(text, translation, has_poll):
    from html import escape

    q = quoted()
    q["text"] = text
    if not has_poll:
        q.pop("poll")
    rendered = card(q)
    rendered.quote_translation = translation
    html = build_card_html(rendered, NOW)
    assert 'class="body quote-title"' not in html
    assert f'<div class="body zh" lang="zh">{escape(translation)}</div>' in html
    assert f'<div class="body en" lang="en">{escape(text)}</div>' in html


def test_short_poll_heading_escapes_original_and_translation():
    q = quoted()
    q["text"] = "<Vote>"
    rendered = card(q)
    rendered.quote_translation = "<投票>"
    html = build_card_html(rendered, NOW)
    assert '&lt;Vote&gt;</span><span class="body zh" lang="zh">（&lt;投票&gt;）' in html
