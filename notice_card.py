"""Presentation only: escaped HTML templates and MaiBot's local browser renderer."""

from __future__ import annotations

import asyncio
import base64
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
        if value:
            blocks.append(
                f'<div class="body {lang}" lang="{lang}">{escape(value)}</div>'
            )
    return "".join(blocks)


def _avatar() -> str:
    data = base64.b64encode((TEMPLATES / "assets/tibo.jpg").read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


def build_card_html(card: NoticeCard, now: datetime) -> str:
    """No external assets, scripts or quote placeholders; all source text escaped."""
    moment = (card.published_at or now).astimezone(ZoneInfo("Asia/Shanghai"))
    quote_html = ""
    quote = card.quote
    if card.tweet and isinstance(quote, dict) and str(quote.get("text") or "").strip():
        author = quote.get("author") if isinstance(quote.get("author"), dict) else {}
        name = str(author.get("name") or "")
        handle = str(author.get("screen_name") or author.get("handle") or "")
        # Never label an unknown quoted author as Tibo.
        avatar = (
            f'<img class="avatar quote-avatar" src="{_avatar()}">'
            if handle == "thsottiaux"
            else ""
        )
        quote_url = str(quote.get("url") or "")
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
        quote_html = (
            '<aside class="quote">'
            f'<div class="quote-head">{avatar}<div><strong>{escape(name)}</strong>'
            f'<span class="handle">{escape("@" + handle if handle else "")}</span></div>'
            f'<span class="quote-label">Quote<span class="handle">{quote_time}</span></span></div>'
            + _body(str(quote["text"]), card.quote_translation)
            + (f'<div class="source">{escape(quote_url)}</div>' if quote_url else "")
            + "</aside>"
        )
    values = dict(
        css=(TEMPLATES / "card.css")
        .read_text(encoding="utf-8")
        .replace(
            "__LATIN_FONT__",
            "data:font/ttf;base64,"
            + base64.b64encode(
                (TEMPLATES / "assets/noto-sans.ttf").read_bytes()
            ).decode(),
        ),
        title=escape(card.title),
        body=_body(card.text, card.translation),
        quote=quote_html,
        layout="with-quote" if quote_html else "",
        avatar=_avatar() if card.tweet else "",
        time=moment.strftime("%Y-%m-%d %H:%M"),
        source=f'<div class="source">{escape(card.url)}</div>' if card.url else "",
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
