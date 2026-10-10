"""Regression for MaiBot's package-style PluginLoader import environment."""

from pathlib import Path
import copy
from importlib.metadata import version
import json
import logging
import shutil
import subprocess
import sys
import types

import pytest

from test_llm_contract import host_module


def host122_loader_modules(tmp_path, monkeypatch):
    """Use pinned actual validator/loader; isolate only version discovery/logging."""
    for name in (
        "src",
        "src.common",
        "src.plugin_runtime",
        "src.plugin_runtime.runner",
    ):
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    runtime = sys.modules["src.plugin_runtime"]
    runtime.detect_host_application_version = lambda *args, **kwargs: "1.2.2"
    sdk = types.ModuleType("src.plugin_runtime.local_sdk")
    sdk.read_local_sdk_version = lambda *args, **kwargs: version("maibot-plugin-sdk")
    monkeypatch.setitem(sys.modules, sdk.__name__, sdk)
    logger = types.ModuleType("src.common.logger")
    logger.PROJECT_ROOT = tmp_path
    logger.get_logger = logging.getLogger
    monkeypatch.setitem(sys.modules, logger.__name__, logger)
    validator = host_module(
        "1.2.2",
        "src.plugin_runtime.runner.manifest_validator",
        "manifest-1.2.2.py.source",
        monkeypatch,
    )
    loader = host_module(
        "1.2.2",
        "src.plugin_runtime.runner.plugin_loader",
        "loader-1.2.2.py.source",
        monkeypatch,
    )
    return validator, loader


@pytest.mark.parametrize(
    "host,sdk,minimum,accepted",
    [
        ("1.2.2", "2.8.0", "1.2.2", True),
        ("1.2.4", "2.8.0", "1.2.2", True),
        ("1.2.2", "2.8.0", "1.2.4", False),  # Original deployment blocker.
        ("1.2.1", "2.8.0", "1.2.2", False),  # Still below tested Host floor.
        ("1.2.2", "2.7.1", "1.2.2", False),  # SDK floor is unchanged.
    ],
)
def test_real_host122_manifest_gate(
    tmp_path, monkeypatch, host, sdk, minimum, accepted
):
    validator, _ = host122_loader_modules(tmp_path, monkeypatch)
    manifest = json.loads((Path(__file__).parent / "_manifest.json").read_text())
    manifest = copy.deepcopy(manifest)
    manifest["host_application"]["min_version"] = minimum
    gate = validator.ManifestValidator(
        host_version=host, sdk_version=sdk, project_root=tmp_path, log_errors=False
    )
    assert gate.validate(manifest) is accepted
    if accepted:
        assert gate.errors == []
    else:
        assert len(gate.errors) == 1 and "低于最小要求" in gate.errors[0]


def test_real_host122_loader_registers_complete_runtime(tmp_path, monkeypatch):
    _, host = host122_loader_modules(tmp_path, monkeypatch)
    root = Path(__file__).parent
    plugin_dir = tmp_path / "plugins" / "Watcher 安装 # offline"
    plugin_dir.mkdir(parents=True)
    for name in ("plugin.py", "notice_card.py", "tibo_discovery.py", "_manifest.json"):
        shutil.copyfile(root / name, plugin_dir / name)
    shutil.copytree(root / "templates", plugin_dir / "templates")
    loader = host.PluginLoader(host_version="1.2.2")
    prefix = "_maibot_plugin_codex_reset_watcher"
    cached = {
        key: value for key, value in sys.modules.items() if key.startswith(prefix)
    }
    try:
        loaded = loader.discover_and_load([str(plugin_dir.parent)])
        assert len(loaded) == 1 and loader._failed_plugins == {}
        meta = loaded[0]
        assert meta.plugin_id == "codex-reset.watcher"
        module = sys.modules[meta.module_name]
        assert isinstance(meta.instance, module.CodexResetWatcher)
        assert (
            sys.modules[meta.module_name + ".notice_card"].NoticeCard
            is module.NoticeCard
        )
        assert meta.module_name + ".tibo_discovery" in sys.modules
    finally:
        for key in list(sys.modules):
            if key.startswith(prefix):
                del sys.modules[key]
        sys.modules.update(cached)


def test_host_package_loader_imports_sibling_notice_card(tmp_path):
    plugin_dir = tmp_path / "plugins" / "codex-reset.watcher"
    plugin_dir.mkdir(parents=True)
    root = Path(__file__).resolve().parent
    for name in ("plugin.py", "notice_card.py", "tibo_discovery.py"):
        shutil.copyfile(root / name, plugin_dir / name)

    # A fresh isolated interpreter prevents repository paths or cached top-level
    # notice_card imports from hiding the Host's sibling-import failure.
    script = """
import importlib.util
from pathlib import Path
import sys

plugin_dir = Path(sys.argv[1])
sys.path.insert(0, str(plugin_dir.parent))
assert str(plugin_dir) not in sys.path
assert 'notice_card' not in sys.modules
name = '_maibot_plugin_codex_reset_watcher'
spec = importlib.util.spec_from_file_location(
    name, plugin_dir / 'plugin.py',
    submodule_search_locations=[str(plugin_dir)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
sibling = sys.modules[name + '.notice_card']
assert module.__package__ == name
assert Path(sibling.__file__) == plugin_dir / 'notice_card.py'
assert module.NoticeCard is sibling.NoticeCard
assert module.render_card is sibling.render_card
assert isinstance(module.create_plugin(), module.CodexResetWatcher)
assert 'notice_card' not in sys.modules
assert str(plugin_dir) not in sys.path
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(plugin_dir)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
