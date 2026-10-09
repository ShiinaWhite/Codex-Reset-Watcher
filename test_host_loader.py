"""Regression for MaiBot's package-style PluginLoader import environment."""

from pathlib import Path
import shutil
import subprocess
import sys


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
