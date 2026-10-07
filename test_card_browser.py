"""Optional real Chromium screenshots; no QQ/API/production access.

Install Playwright and Chromium/fonts, then explicitly run this file.
"""

import asyncio
import base64
from datetime import datetime, timedelta, timezone

import pytest

from notice_card import NoticeCard, render_card

pytest.importorskip("playwright.async_api")


@pytest.mark.parametrize(
    "kind", ["tweet", "quote", "system", "long", "poll", "active-poll"]
)
def test_real_local_screenshot_and_layout(tmp_path, kind):
    from playwright.async_api import async_playwright
    from types import SimpleNamespace

    now = datetime(2026, 10, 4, 20, 33, tzinfo=timezone.utc)
    card = NoticeCard(
        "Tibo 动态" if kind != "system" else "Codex Banked Reset 提醒",
        "English first paragraph: we’ll ship improvements.\n\nEnglish second paragraph.",
        "中文第一段：额度重置、银行监测、自己续帖。\n\n中文第二段：镕喆囧龘。",
        url="https://codex-reset.com/banked-reset"
        if kind == "system"
        else "https://x.com/thsottiaux/status/123",
        tweet=kind != "system",
    )
    if kind == "quote":
        card.quote = {
            "text": "Quoted English",
            "author": {"name": "Other", "screen_name": "other"},
            "url": "https://x.com/other/status/456",
        }
        card.quote_translation = "引用中文"
    if kind == "long":
        card.text = "Long note paragraph.\n" * 160
        card.translation = "这是长文中的一段，完整呈现。\n" * 160

    if kind in {"poll", "active-poll"}:
        import json
        from pathlib import Path

        values = json.loads(
            (Path(__file__).parent / "tests/fixtures/quoted-poll/card.json").read_text()
        )
        values["published_at"] = datetime.fromisoformat(values["published_at"])
        card = NoticeCard(**values)
        now = datetime(2026, 10, 7, 5, tzinfo=timezone.utc)
        if kind == "active-poll":
            card.quote["poll"].update(
                closed=False, ends_at=(now + timedelta(hours=3)).isoformat()
            )

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page(
                    viewport={"width": 1240, "height": 900}, device_scale_factor=2
                )
                network = []

                async def block(route):
                    network.append(route.request.url)
                    await route.abort()

                await page.route("**/*", block)

                class BrowserRender:
                    async def html2png(self, html, **kwargs):
                        assert kwargs["allow_network"] is False
                        await page.set_content(html)
                        await page.evaluate("document.fonts.ready")
                        data = await page.locator(kwargs["selector"]).screenshot()
                        (tmp_path / f"{kind}.png").write_bytes(data)
                        return {"image_base64": base64.b64encode(data).decode()}

                image = await render_card(
                    SimpleNamespace(render=BrowserRender()), card, now
                )
                assert image and base64.b64decode(image).startswith(b"\x89PNG")
                assert await page.locator(".source").count() == 0
                visible = await page.locator("#capture").inner_text()
                assert card.url not in visible
                if card.quote:
                    assert card.quote["url"] not in visible
                assert await page.locator(".quote").count() == int(
                    kind in {"quote", "poll", "active-poll"}
                )
                assert await page.locator(".timezone").inner_text() == "UTC+8"
                expected_time = (
                    "2026-10-07 05:07"
                    if kind in {"poll", "active-poll"}
                    else "2026-10-05 04:33"
                )
                assert await page.locator("time").inner_text() == expected_time
                if kind in {"poll", "active-poll"}:
                    assert await page.locator(".poll-option").count() == 2
                    assert "74,565 票" in visible and "✓" not in visible
                    assert ("进行中" if kind == "active-poll" else "已结束") in visible
                    title = page.locator(".quote-title")
                    assert await title.inner_text() == "Vote（投票）"
                    title_en = await title.locator(".en").bounding_box()
                    title_zh = await title.locator(".zh").bounding_box()
                    assert abs(title_en["y"] - title_zh["y"]) < 1
                    captions = [
                        "👌 good day（美好的一天）",
                        "🫨 needs a reset（需要重置）",
                    ]
                    for i, caption in enumerate(captions):
                        label = page.locator(".poll-label").nth(i)
                        assert await label.inner_text() == caption
                        original = await label.locator('[lang="en"]').bounding_box()
                        translation = await label.locator('[lang="zh"]').bounding_box()
                        assert abs(original["y"] - translation["y"]) < 1
                        result = (
                            await page.locator(".poll-result").nth(i).bounding_box()
                        )
                        assert result["y"] >= original["y"] + original["height"]
                        assert (
                            await page.locator(".poll-percentage").nth(i).inner_text()
                            == f"{[24, 76][i]:.1f}%"
                        )
                    for i, percentage in enumerate([24, 76]):
                        track = await page.locator(".poll-track").nth(i).bounding_box()
                        bar = await page.locator(".poll-bar").nth(i).bounding_box()
                        assert (
                            abs(bar["width"] / track["width"] * 100 - percentage) < 0.1
                        )
                    assert (
                        await page.locator(".content.with-quote").evaluate(
                            "e => getComputedStyle(e).display"
                        )
                        == "grid"
                    )
                # Verify actual glyph usage, not merely a declared CSS family:
                # Chinese/Latin must render with the same embedded font offline.
                session = await page.context.new_cdp_session(page)
                await session.send("DOM.enable")
                await session.send("CSS.enable")
                root = (await session.send("DOM.getDocument"))["root"]["nodeId"]
                selectors = [".notice-title", ".body.zh", ".body.en", "time"]
                if kind in {"poll", "active-poll"}:
                    selectors.extend([".quote-title .zh", ".quote-title .en"])
                elif card.quote:
                    selectors.extend([".quote .body.zh", ".quote .body.en"])
                for selector in selectors:
                    node = await session.send(
                        "DOM.querySelector", {"nodeId": root, "selector": selector}
                    )
                    fonts = (
                        await session.send(
                            "CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]}
                        )
                    )["fonts"]
                    assert fonts and all(
                        f["isCustomFont"] and f["familyName"] == "Watcher Sans SC"
                        for f in fonts
                    ), (selector, fonts)
                    if selector.endswith(".en") or selector == "time":
                        assert all(
                            f["postScriptName"] == "WatcherSansSC-Latin" for f in fonts
                        ), (selector, fonts)
                    weight = await page.locator(selector).first.evaluate(
                        "e => getComputedStyle(e).fontWeight"
                    )
                    assert weight == ("600" if selector == ".notice-title" else "400")
                await session.detach()
                # Whole translations form contiguous blocks in every body.
                primary = page.locator(".primary" if card.tweet else ".system-body")
                zh = await primary.locator(".zh").bounding_box()
                en = await primary.locator(".en").bounding_box()
                assert zh["y"] + zh["height"] <= en["y"]
                assert await page.locator("#capture").evaluate(
                    "(e) => e.scrollWidth === e.clientWidth"
                )
                assert await page.locator(".avatar").evaluate_all(
                    "(xs) => xs.every(x => x.complete && x.naturalWidth > 0)"
                )
                assert not network
                if kind == "long":
                    assert (await page.locator("#capture").bounding_box())[
                        "height"
                    ] > 900
            finally:
                await browser.close()

    asyncio.run(scenario())
