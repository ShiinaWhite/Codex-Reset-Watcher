"""Real SDK payload -> pinned official Host routing; no provider or bot access.

Run this file in each actual SDK environment for the release matrix, setting
CRW_HOST_GIT_ROOT to a separate official MaiBot clone containing the pinned tags.
Core and task resolver modules come from verified upstream Git blobs. Only
config, logging and the final external model invocation are isolated. Upstream
Core source and SDK packages are not distributed with the Watcher release tree.
"""

from __future__ import annotations

import asyncio
import ast
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import time
import types

import pytest
from maibot_sdk import PluginContext

import plugin as watcher_module
from plugin import TweetContent
from test_watcher import _llm_plugin


ORIGINS = json.loads(
    (Path(__file__).parent / "tests/fixtures/llm-host-origins.json").read_text()
)


def upstream_source(host, filename):
    root = os.environ.get("CRW_HOST_GIT_ROOT")
    if not root:
        pytest.skip("Set CRW_HOST_GIT_ROOT to run the real official Host matrix")
    release = ORIGINS[host]
    origin = release["sources"][filename]
    raw = subprocess.check_output(
        ["git", "-C", root, "show", f"{release['commit']}:{origin['path']}"]
    )
    expected = origin.get("source_sha256", origin["sha256"])
    assert hashlib.sha256(raw).hexdigest() == expected
    source = raw.decode("utf-8-sig")
    if "extraction" in origin:
        node = next(
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.ClassDef) and n.name == "LLMServiceRequest"
        )
        start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
        source = "".join(source.splitlines(keepends=True)[start : node.end_lineno])
        assert hashlib.sha256(source.encode()).hexdigest() == origin["sha256"]
    return source


def host_module(host, name, filename, monkeypatch, initial=None):
    module = types.ModuleType(name)
    module.__file__ = f"{ORIGINS[host]['commit']}/{filename}"
    module.__dict__.update(initial or {})
    monkeypatch.setitem(sys.modules, name, module)
    # Decode the upstream BOM; preserve all remaining source bytes/logic.
    source = upstream_source(host, filename)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def prepare_host(host, monkeypatch):
    logger = types.ModuleType("src.common.logger")
    logger.get_logger = logging.getLogger
    monkeypatch.setitem(sys.modules, logger.__name__, logger)
    config = types.ModuleType("src.config.config")

    @dataclass
    class TaskConfig:
        model_list: list[str]

    config.global_config = types.SimpleNamespace()
    # Concrete model names differ from task names; a direct 'replyer' model
    # lookup must fail rather than accidentally pass the routing assertion.
    tasks = types.SimpleNamespace(
        replyer=TaskConfig(["actual-reply-model"]),
        utils=TaskConfig(["actual-utils-model"]),
    )
    config.config_manager = types.SimpleNamespace(
        get_model_config=lambda: types.SimpleNamespace(model_task_config=tasks)
    )
    monkeypatch.setitem(sys.modules, config.__name__, config)
    model_configs = types.ModuleType("src.config.model_configs")
    model_configs.TaskConfig = TaskConfig
    monkeypatch.setitem(sys.modules, model_configs.__name__, model_configs)
    resolver = host_module(
        host, "_host_task_resolver", f"tasks-{host}.py.source", monkeypatch
    )
    request = host_module(
        host,
        "_host_llm_request",
        f"request-{host}.py.source",
        monkeypatch,
        {"dataclass": dataclass, "BaseDataModel": object},
    )
    llm_api = types.ModuleType("src.services.llm_service")
    llm_api.get_available_models = resolver.get_available_models
    llm_api.resolve_task_name = resolver.resolve_task_name
    llm_api.LLMServiceRequest = request.LLMServiceRequest
    captured = []

    async def generate(req):
        captured.append(req)
        if req.model_name is not None:
            raise ValueError(f"Concrete model not configured: {req.model_name}")
        assert tasks.replyer.model_list == ["actual-reply-model"]
        return types.SimpleNamespace(
            to_capability_payload=lambda: {
                "success": True,
                "response": '{"translation_zh":"完整中文翻译"}',
            }
        )

    llm_api.generate = generate
    services = types.ModuleType("src.services")
    services.llm_service = llm_api
    monkeypatch.setitem(sys.modules, services.__name__, services)
    monkeypatch.setitem(sys.modules, llm_api.__name__, llm_api)
    core = host_module(host, "_host_core", f"core-{host}.py.source", monkeypatch)
    return core.RuntimeCoreCapabilityMixin(), captured


def test_host_sources_match_recorded_upstream_bytes():
    for host, release in ORIGINS.items():
        for name in release["sources"]:
            upstream_source(host, name)


def exercise_watcher(tmp_path, rpc):
    context = PluginContext("codex-reset.watcher", rpc_call=rpc)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Watcher must bypass SDK LLM convenience wrapper")

    context.llm.generate = forbidden
    watcher = _llm_plugin(tmp_path)
    watcher._ctx = context
    text = asyncio.run(
        watcher._llm_translate_content(
            "123",
            "https://x.com/thsottiaux/status/123",
            "unknown",
            TweetContent("Reset propagated. Enjoy.", "full", "fxtwitter"),
        )
    )
    assert text == "完整中文翻译"
    return watcher


def test_real_sdk_capability_wire_and_rpc_budget(tmp_path):
    """Always runs with the installed SDK, without a Core checkout dependency."""
    wire = []

    async def rpc(method, plugin_id, payload, timeout_ms=None):
        assert method == "cap.call" and payload["capability"] == "llm.generate"
        assert plugin_id == "codex-reset.watcher"
        wire.append({"args": payload["args"], "timeout_ms": timeout_ms})
        return {"success": True, "response": '{"translation_zh":"完整中文翻译"}'}

    exercise_watcher(tmp_path, rpc)
    assert len(wire) == 1
    assert set(wire[0]["args"]) == {"prompt", "model", "temperature", "max_tokens"}
    assert wire[0]["args"]["model"] == "replyer"
    assert 590000 <= wire[0]["timeout_ms"] <= 600000


def test_real_sdk_repair_uses_remaining_rpc_budget(tmp_path, monkeypatch):
    wire = []
    elapsed = [0]
    monkeypatch.setattr(
        watcher_module,
        "time",
        types.SimpleNamespace(monotonic=lambda: time.monotonic() + elapsed[0]),
    )

    async def rpc(method, plugin_id, payload, timeout_ms=None):
        assert method == "cap.call" and payload["capability"] == "llm.generate"
        assert set(payload["args"]) == {"prompt", "model", "temperature", "max_tokens"}
        assert payload["args"]["model"] == "replyer"
        wire.append(timeout_ms)
        if len(wire) == 1:
            elapsed[0] = 120  # First attempt has consumed part of the total budget.
            return {"success": True, "response": "invalid JSON"}
        return {"success": True, "response": '{"translation_zh":"完整中文翻译"}'}

    exercise_watcher(tmp_path, rpc)
    assert len(wire) == 2
    assert 590000 <= wire[0] <= 600000
    assert 470000 <= wire[1] <= 480000
    assert 119000 <= wire[0] - wire[1] <= 121000


@pytest.mark.parametrize("host", ["1.2.2", "1.2.4", "1.2.5", "1.3.5"])
def test_real_sdk_host_replyer_task_and_rpc_budget(tmp_path, monkeypatch, host):
    expected_sdk = os.environ.get("CRW_EXPECT_SDK")
    if expected_sdk:
        assert version("maibot-plugin-sdk") == expected_sdk
    core, requests = prepare_host(host, monkeypatch)
    wire = []

    async def rpc(method, plugin_id, payload, timeout_ms=None):
        assert method == "cap.call" and payload["capability"] == "llm.generate"
        wire.append({"args": payload["args"], "timeout_ms": timeout_ms})
        return await core._cap_llm_generate(plugin_id, "llm.generate", payload["args"])

    watcher = exercise_watcher(tmp_path, rpc)
    assert len(wire) == len(requests) == 1
    assert set(wire[0]["args"]) == {"prompt", "model", "temperature", "max_tokens"}
    assert wire[0]["args"]["model"] == "replyer"
    assert 590000 <= wire[0]["timeout_ms"] <= 600000
    assert requests[0].task_name == "replyer"
    assert requests[0].model_name is None
    assert requests[0].temperature == watcher.config.llm.temperature
    assert requests[0].max_tokens == watcher.config.llm.max_tokens


def test_281_convenience_wrapper_reproduces_wrong_concrete_model(monkeypatch):
    if version("maibot-plugin-sdk") != "2.8.1":
        pytest.skip("Negative control requires the actual SDK 2.8.1 package")
    core, requests = prepare_host("1.2.5", monkeypatch)
    wire = []

    async def rpc(method, plugin_id, payload, **kwargs):
        wire.append(payload["args"])
        return await core._cap_llm_generate(plugin_id, "llm.generate", payload["args"])

    ctx = PluginContext("codex-reset.watcher", rpc_call=rpc)
    result = asyncio.run(ctx.llm.generate("Hello", model="replyer"))
    assert wire[0]["task_name"] == "utils"
    assert requests[0].task_name == "utils" and requests[0].model_name == "replyer"
    assert result["success"] is False and "replyer" in result["error"]
