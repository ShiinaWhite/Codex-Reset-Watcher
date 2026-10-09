"""Presentation only: escaped HTML templates and MaiBot's local browser renderer."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo


TEMPLATES = Path(__file__).with_name("templates")


@dataclass
class NoticeCard:
    title: str
    text: str
    translation: str = ""
    url: str = ""
    published_at: datetime | None = None
    tweet: bool = False
    quote: dict | None = None
    quote_translation: str = ""
    # One render per pipeline shared by groups; sending failures remain per-group.
    render_attempted: bool = False
    image_base64: str | None = None


def _body(text: str, translation: str) -> str:
    blocks = []
    for lang, value in (("zh", translation), ("en", text)):
        if value.strip():
            blocks.append(
                f'<div class="body {lang}" lang="{lang}">{escape(value)}</div>'
            )
    return "".join(blocks)


def _avatar() -> str:
    data = base64.b64encode((TEMPLATES / "assets/tibo.jpg").read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


def _card_font_css(card: NoticeCard) -> str:
    """Embed deterministic core and only the supplemental glyphs this card uses.

    Coverage is partitioned at build time, never generated from notification
    fixtures. The union retains the original font; no runtime font tooling or
    filesystem URLs are needed in the Host process.
    """
    text = [card.title, card.text, card.translation, card.quote_translation]
    quote = card.quote if card.tweet and isinstance(card.quote, dict) else {}
    text.append(str(quote.get("text") or ""))
    author = quote.get("author") or {}
    if isinstance(author, dict):
        text.extend(str(author.get(k) or "") for k in ("name", "screen_name", "handle"))
    poll = quote.get("poll") or {}
    if isinstance(poll, dict):
        for option in poll.get("options") or []:
            text.extend(str(option.get(k) or "") for k in ("label", "translation"))
    needed = set("".join(text))
    assets = TEMPLATES / "assets"
    index = json.loads((assets / "font-index.json").read_text(encoding="utf-8"))
    faces = []
    for i, entry in enumerate(index):
        if i and not needed.intersection(entry["codepoints"]):
            continue
        data = base64.b64encode((assets / entry["file"]).read_bytes()).decode()
        # Supplemental blocks contain only non-core Han. Restrict selection to
        # their block, retaining the original core and Latin face precedence.
        points = [ord(c) for c in entry["codepoints"]]
        coverage = f"unicode-range: U+{min(points):X}-{max(points):X};" if i else ""
        faces.append(
            '@font-face { font-family: "Watcher Sans SC"; '
            f'src: url("data:font/woff2;base64,{data}") format("woff2"); '
            "font-style: normal; font-weight: 400 600; font-display: block; "
            + coverage
            + " }"
        )
    return "\n".join(faces)


def _poll_option_label(label: str, translation: str) -> str:
    """Remove caption wrappers for display only; preserve casing and emoji."""
    wrapped = re.fullmatch(r"([^\w(（]*)(?:\(([^()]*)\)|（([^（）]*)）)", label)
    if wrapped:
        prefix = wrapped[1].strip()
        label = (prefix + " " if prefix else "") + (
            wrapped[2] or wrapped[3] or ""
        ).strip()
    symbols = {c for c in label if unicodedata.category(c) == "So"}

    def caption(value: str) -> str:
        value = "".join(
            c
            for c in value
            if c not in symbols
            and c not in "\ufe0e\ufe0f\u200d"
            and not "\U0001f3fb" <= c <= "\U0001f3ff"
        ).strip()
        if (value.startswith("（") and value.endswith("）")) or (
            value.startswith("(") and value.endswith(")")
        ):
            value = value[1:-1].strip()
        return value

    translated = caption(translation)
    suffix = (
        f'<span class="poll-translation" lang="zh">（{escape(translated)}）</span>'
        if translated and translated.casefold() != caption(label).casefold()
        else ""
    )
    return f'<span lang="en">{escape(label)}</span>' + suffix


def _poll_html(poll: dict, now: datetime) -> str:
    """Render normalized enrichment data only; never infer a viewer selection."""
    options = poll.get("options") or []
    if len(options) < 2:
        return '<div class="poll poll-unavailable">投票内容暂不可用</div>'
    rows = []
    for option in options:
        label = str(option["label"])
        translated = str(option.get("translation") or "")
        labels = _poll_option_label(label, translated)
        percentage = option.get("percentage")
        amount = f"{percentage:.1f}%" if percentage is not None else "—"
        width = f"{percentage:g}" if percentage is not None else "0"
        rows.append(
            '<div class="poll-option">'
            f'<div class="poll-label">{labels}</div><div class="poll-result">'
            f'<div class="poll-track"><div class="poll-bar" style="width:{width}%"></div></div>'
            f'<span class="poll-percentage">{amount}</span></div></div>'
        )
    details = []
    total = poll.get("total_votes")
    if total is not None:
        details.append(f"{total:,} 票")
    ends_at = None
    try:
        value = poll.get("ends_at")
        if value:
            ends_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not ends_at.tzinfo:
                ends_at = None
    except (TypeError, ValueError):
        pass
    closed = poll.get("closed")
    if ends_at and ends_at <= now:
        closed = True
    if closed is True:
        details.append("已结束")
    elif closed is False or (ends_at and ends_at > now):
        details.append("进行中")
        if ends_at:
            minutes = max(1, math.ceil((ends_at - now).total_seconds() / 60))
            details.append(
                f"剩余 {math.ceil(minutes / 60)} 小时"
                if minutes >= 60
                else f"剩余 {minutes} 分钟"
            )
    else:
        details.append("状态未知")
    if ends_at:
        details.append(
            "截止 "
            + ends_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
            + " UTC+8"
        )
    return (
        '<section class="poll">'
        + "".join(rows)
        + f'<div class="poll-footer">{escape(" · ".join(details))}</div></section>'
    )


def build_card_html(card: NoticeCard, now: datetime) -> str:
    """No external assets, scripts or quote placeholders; all source text escaped."""
    moment = (card.published_at or now).astimezone(ZoneInfo("Asia/Shanghai"))
    quote_html = ""
    quote = card.quote
    if (
        card.tweet
        and isinstance(quote, dict)
        and (
            str(quote.get("text") or "").strip() or isinstance(quote.get("poll"), dict)
        )
    ):
        author = quote.get("author") if isinstance(quote.get("author"), dict) else {}
        name = str(author.get("name") or "")
        handle = str(author.get("screen_name") or author.get("handle") or "")
        # Never label an unknown quoted author as Tibo.
        avatar = (
            f'<img class="avatar quote-avatar" src="{_avatar()}">'
            if handle == "thsottiaux"
            else ""
        )
        quote_time = ""
        try:
            at = quote.get("at")
            moment_quoted = (
                datetime.fromisoformat(at.replace("Z", "+00:00"))
                if isinstance(at, str)
                else datetime.fromtimestamp(quote["created_timestamp"], ZoneInfo("UTC"))
            )
            if moment_quoted.tzinfo:
                quote_time = moment_quoted.astimezone(
                    ZoneInfo("Asia/Shanghai")
                ).strftime("%m-%d %H:%M")
        except (ValueError, TypeError, KeyError, OverflowError):
            pass
        poll = quote.get("poll") if isinstance(quote.get("poll"), dict) else None
        text = str(quote.get("text") or "")
        translation = card.quote_translation
        body = _body(text, translation)
        # Only short poll headings are inline; ordinary/long quote bodies retain
        # whole-translation blocks. Unknown poll placeholders stay hidden below.
        if (
            poll is not None
            and text.strip()
            and len(text.strip()) + len(translation.strip()) <= 40
            and "\n" not in text + translation
            and "\r" not in text + translation
        ):
            translated = translation.strip()
            suffix = (
                f'<span class="body zh" lang="zh">（{escape(translated)}）</span>'
                if translated and translated.casefold() != text.strip().casefold()
                else ""
            )
            body = (
                '<div class="body quote-title">'
                f'<span class="body en" lang="en">{escape(text)}</span>{suffix}</div>'
            )
        if (
            poll is not None
            and len(poll.get("options") or []) < 2
            and str(quote.get("text") or "").strip().casefold() in {"vote", "投票"}
        ):
            body = ""
        quote_html = (
            '<aside class="quote">'
            f'<div class="quote-head">{avatar}<div><strong>{escape(name)}</strong>'
            f'<span class="handle">{escape("@" + handle if handle else "")}</span></div>'
            f'<span class="quote-label">Quote<span class="handle">{quote_time}</span></span></div>'
            + body
            + (_poll_html(poll, now) if poll is not None else "")
            + "</aside>"
        )
    values = dict(
        css=(TEMPLATES / "card.css")
        .read_text(encoding="utf-8")
        .replace(
            "__SC_FONTS__",
            _card_font_css(card),
        )
        .replace(
            "__LATIN_FONT__",
            "data:font/woff2;base64,"
            + base64.b64encode(
                (TEMPLATES / "assets/watcher-sans-latin.woff2").read_bytes()
            ).decode(),
        ),
        title=escape(card.title),
        body=_body(card.text, card.translation),
        quote=quote_html,
        layout="with-quote" if quote_html else "",
        avatar=_avatar() if card.tweet else "",
        time=moment.strftime("%Y-%m-%d %H:%M"),
    )
    template = "tweet.html" if card.tweet else "system.html"
    return Template((TEMPLATES / template).read_text(encoding="utf-8")).substitute(
        values
    )


async def render_card(ctx, card: NoticeCard, now: datetime) -> str | None:
    if not card.render_attempted:
        card.render_attempted = True
        # Exceptions propagate to the presentation gate, which sends text.
        result = await asyncio.wait_for(
            ctx.render.html2png(
                build_card_html(card, now),
                selector="#capture",
                viewport={"width": 1240, "height": 900},
                device_scale_factor=2,
                allow_network=False,
                render_timeout_ms=15000,
            ),
            timeout=20,
        )
        if isinstance(result, dict):
            data = result.get("image_base64")
            if isinstance(data, str) and data.strip():
                decoded = base64.b64decode(data, validate=True)
                if decoded.startswith(b"\x89PNG\r\n\x1a\n"):
                    card.image_base64 = data
    return card.image_base64
