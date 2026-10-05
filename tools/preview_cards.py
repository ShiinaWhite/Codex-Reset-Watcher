"""Offline visual review; requires development-only Playwright + Chromium/fonts.

Usage: python tools/preview_cards.py --output docs/preview
Uses the same HTML builder and screenshot options as the host capability.
Never connects to QQ or reads runtime configuration/state.
"""

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_card import NoticeCard, build_card_html  # noqa: E402


async def preview(output):
    from playwright.async_api import async_playwright

    output.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 10, 4, 20, 33, tzinfo=timezone.utc)
    main = "Over the next 28 days, each day we’ll either ship one thing that is a clear improvement and relevant for most codex/work users or ship a full reset. Let the improvements begin."
    chinese = "在接下来的 28 天里，我们每天要么发布一项对大多数 Codex / Work 用户切实相关、明显改进的东西，要么就进行一次彻底重置。让改进开始吧。"
    quoted = "All right, we’re locking in. Only things being worked on are simplifications, more efficiency for more usage, groundbreaking features or new models.\n\nSometimes you have to invest ahead of the curve, but feedback is clear that you all want things to get simpler. On it."
    quoted_zh = "好，我们锁定方向了。目前正在推进的只有这些：简化、为更多用量提升效率、突破性功能或新模型。\n\n有时候你必须超前投入，但反馈很明确：大家都希望事情变得更简单。这就去办。"
    cards = {
        "tweet-quote": NoticeCard(
            "Tibo 动态",
            main,
            chinese,
            "https://x.com/thsottiaux/status/2106845241357824205",
            now,
            True,
            {
                "text": quoted,
                "author": {"name": "Tibo", "screen_name": "thsottiaux"},
                "at": "2026-10-04T04:59:00Z",
            },
            quoted_zh,
        ),
        "tweet": NoticeCard(
            "Codex 额度重置提醒",
            "Reset all propagated. Enjoy.",
            "重置已全部生效，尽情使用吧。",
            "https://x.com/thsottiaux/status/2106131810921136451",
            now,
            True,
        ),
        "system": NoticeCard(
            "Codex Banked Reset 提醒",
            "Monitoring accounts received a new Banked Reset. Tibo has not announced it on X.",
            "监测账户收到新的 Banked Reset，Tibo 尚未在 X 宣布。",
            "https://codex-reset.com/banked-reset",
            now,
        ),
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(
                viewport={"width": 1240, "height": 900}, device_scale_factor=2
            )
            await page.route("**/*", lambda route: route.abort())
            for name, card in cards.items():
                html = build_card_html(card, now)
                await page.set_content(html)
                await page.evaluate("document.fonts.ready")
                await page.locator("#capture").screenshot(
                    path=str(output / f"{name}.png")
                )
                print(output / f"{name}.png")
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/preview"))
    asyncio.run(preview(parser.parse_args().output))
