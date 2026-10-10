"""Isolated Host loader/render contract smoke; never start a bot or send to QQ.

Run with the Host's Python dependencies and a checked-out official Host tag.
Only the supplied output directory is written. Cold downloads can be slow:
run this development tool in the background and retain its log/results.json.
"""

import argparse
import asyncio
import base64
import builtins
from datetime import datetime, timezone
import functools
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import sys
import time
import types


def run(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    plugin_dir = root / "plugins" / "Watcher 安装 # offline"
    plugin_dir.mkdir(parents=True)
    for name in ("plugin.py", "notice_card.py", "tibo_discovery.py", "_manifest.json"):
        shutil.copyfile(args.plugin_root / name, plugin_dir / name)
    shutil.copytree(args.plugin_root / "templates", plugin_dir / "templates")
    # This fresh tool process isolates configuration and logging boundaries;
    # Host source, validator, loader, authorization, codec and renderer are real.
    sys.path.insert(0, str(args.host_root.resolve()))
    logger = types.ModuleType("src.common.logger")
    logger.PROJECT_ROOT = root
    logger.get_logger = logging.getLogger
    sys.modules[logger.__name__] = logger
    config = types.ModuleType("src.config.config")
    config.config_manager = types.SimpleNamespace(
        get_global_config=lambda: types.SimpleNamespace(
            plugin_runtime=types.SimpleNamespace(render=current)
        )
    )
    sys.modules[config.__name__] = config
    from maibot_sdk import PluginContext
    from src.config.official_configs import PluginRuntimeRenderConfig
    from src.plugin_runtime.host.authorization import AuthorizationManager
    from src.plugin_runtime.host.capability_service import CapabilityService
    from src.plugin_runtime.protocol.codec import MsgPackCodec
    from src.plugin_runtime.protocol.envelope import Envelope, MessageType
    from src.plugin_runtime.runner.plugin_loader import PluginLoader
    from src.services import html_render_service

    # Import this exact module without capabilities/__init__ starting unrelated
    # Core integrations. Its implementation and lazy renderer import are intact.
    spec = importlib.util.spec_from_file_location(
        "_smoke_host_render_capability",
        args.host_root / "src/plugin_runtime/capabilities/render.py",
    )
    render_capability = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_capability)

    current = PluginRuntimeRenderConfig(
        browser_install_root=str(root / "browsers"),
        executable_path=args.browser_executable,
        auto_download_chromium=not bool(args.browser_executable),
    )
    service = html_render_service.HTMLRenderService()
    html_render_service._html_render_service = service
    loader = PluginLoader(host_version=args.host_version)
    loaded = loader.discover_and_load([str(root / "plugins")])
    assert len(loaded) == 1, loader._failed_plugins
    watcher = loaded[0].instance
    notice = sys.modules[loaded[0].module_name + ".notice_card"]
    auth = AuthorizationManager()
    auth.register_plugin(loaded[0].plugin_id, ["render.html2png"])
    capabilities = CapabilityService(auth)
    impl = render_capability.RuntimeRenderCapabilityMixin()._cap_render_html2png
    capabilities.register_capability("render.html2png", impl)
    codec = MsgPackCodec()
    results = {
        "host_version": args.host_version,
        "loaded": [m.plugin_id for m in loaded],
        "failed_plugins": loader._failed_plugins,
        "initial_browser_cache": False,
        "cold_download_requested": not bool(args.browser_executable),
        "rpc_request_bytes": [],
        "cases": [],
    }

    async def rpc(method, plugin_id, payload, **kwargs):
        assert method == "cap.call" and payload["capability"] == "render.html2png"
        request = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method=method,
            plugin_id=plugin_id,
            payload=payload,
        )
        encoded = codec.encode_envelope(request)
        assert len(encoded) < 16 * 1024 * 1024
        results["rpc_request_bytes"].append(len(encoded))
        response = await capabilities.handle_capability_request(
            codec.decode_envelope(encoded)
        )
        response = codec.decode_envelope(codec.encode_envelope(response))
        if response.error:
            raise RuntimeError(str(response.error))
        return response.payload["result"]

    ctx = PluginContext(loaded[0].plugin_id, rpc_call=rpc)
    watcher._ctx = ctx
    watcher._plugin_config_instance = types.SimpleNamespace(
        watcher=types.SimpleNamespace(display_mode="image")
    )
    sent = []

    async def text(group, message):
        assert group == "offline-only"
        sent.append({"kind": "text", "message": message})
        return True

    async def image(group, data):
        assert group == "offline-only"
        sent.append({"kind": "image", "png_bytes": len(base64.b64decode(data))})
        return True

    watcher._send_group_text = text
    watcher._send_group_image = image
    message = "通知文字\nhttps://x.com/thsottiaux/status/123"

    async def smoke(name, expected):
        start = len(sent)
        success = await watcher._send_group_notice(
            "offline-only", message, notice.NoticeCard("Tibo 动态", "English", "中文")
        )
        delta = sent[start:]
        assert success and len(delta) == 1 and delta[0]["kind"] == expected
        if expected == "text":
            assert delta[0]["message"] == message
        results["cases"].append({"name": name, "sent": delta})

    async def main():
        nonlocal current
        card = notice.NoticeCard(
            "Codex 额度重置提醒",
            "Reset all propagated. Enjoy.",
            "重置已全部生效，尽情使用吧。",
            tweet=True,
        )
        html = notice.build_card_html(card, datetime(2026, 10, 4, tzinfo=timezone.utc))
        began = time.monotonic()
        rendered = await ctx.render.html2png(
            html,
            selector="#capture",
            viewport={"width": 1240, "height": 900},
            allow_network=False,
            render_timeout_ms=15000,
        )
        png = base64.b64decode(rendered["image_base64"])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        (root / "host-render.png").write_bytes(png)
        results["render"] = {k: v for k, v in rendered.items() if k != "image_base64"}
        results["render"].update(
            html_bytes=len(html.encode()),
            png_bytes=len(png),
            total_ms=round((time.monotonic() - began) * 1000, 2),
        )
        await smoke("image", "image")
        watcher._plugin_config_instance.watcher.display_mode = "text"
        await smoke("text", "text")
        watcher._plugin_config_instance.watcher.display_mode = "image"
        current.enabled = False
        await smoke("render-disabled", "text")
        current.enabled = True
        auth.clear()
        await smoke("capability-denied", "text")
        auth.register_plugin(loaded[0].plugin_id, ["render.html2png"])
        capabilities._implementations.clear()
        await smoke("capability-unregistered", "text")
        capabilities.register_capability("render.html2png", impl)

        # Actual Host route and page.set_content origin; no security bypass.
        browser = await service._ensure_browser(current)
        page = await browser.new_page()
        console = []
        page.on("console", lambda msg: console.append(msg.text))
        await page.route(
            "**/*",
            functools.partial(service._handle_network_route, allow_network=False),
        )
        uri = (plugin_dir / "templates/assets/watcher-sans-sc.woff2").as_uri()
        await page.set_content(
            '<style>@font-face{font-family:"file-experiment";src:url("'
            + uri
            + '")}body{font-family:"file-experiment"}</style>字体实验'
        )
        await page.evaluate("document.fonts.ready")
        results["file_font_experiment"] = {
            "uri": uri,
            "status": await page.evaluate("[...document.fonts].map(f=>f.status)"),
            "console": console,
        }
        assert results["file_font_experiment"]["status"] == ["error"]
        await page.close()
        await service.reset_browser(restart_playwright=True)
        current = PluginRuntimeRenderConfig(
            browser_install_root=str(root / "missing-browser"),
            auto_download_chromium=False,
        )
        await smoke("browser-missing", "text")
        await service.reset_browser(restart_playwright=True)
        original = builtins.__import__

        def missing(name, *a, **kw):
            if name.startswith("playwright"):
                raise ImportError("isolated missing Playwright")
            return original(name, *a, **kw)

        builtins.__import__ = missing
        try:
            await smoke("playwright-missing", "text")
        finally:
            builtins.__import__ = original
        current.auto_download_chromium = True

        async def failed_download(cfg):
            raise RuntimeError("isolated Chromium download failure")

        service._install_chromium_browser = failed_download
        await smoke("download-failed", "text")
        await service.reset_browser(restart_playwright=True)
        results["success"] = True

    try:
        asyncio.run(main())
    except BaseException as exc:
        results["error"] = repr(exc)
        raise
    finally:
        (root / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", required=True, type=Path)
    parser.add_argument("--host-version", required=True)
    parser.add_argument(
        "--plugin-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--browser-executable", default="")
    logging.basicConfig(level=logging.INFO)
    run(parser.parse_args())
