"""Release installation must include only intentional, bounded tracked content."""

import hashlib
import json
from pathlib import Path

import pytest

from tools.verify_release import MiB, check_html_payload, check_tree_hygiene


@pytest.mark.parametrize(
    "name",
    [
        "live/feed.json",
        "evidence/raw.json",
        "probe_new.json",
        "docs/preview/before-typography/test.png",
        "docs/forensics/raw.json",
        "private/key",
        "config.toml",
        "reset_state.json",
        "raw.log",
        "raw.jsonl",
        "templates/assets/full.ttf",
    ],
)
def test_forensic_or_runtime_state_artifacts_are_rejected(name):
    with pytest.raises(AssertionError):
        check_tree_hygiene({name: 1})


def test_preserved_regression_snapshots_are_allowed_and_byte_exact():
    root = Path(__file__).parent
    origins = json.loads((root / "tests/fixtures/historical/ORIGINS.json").read_text())
    sizes = {}
    for record in origins["fixtures"]:
        path = root / record["path"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        sizes[record["path"]] = len(data)
    assert check_tree_hygiene(sizes)["tracked"] == sum(sizes.values())


def test_budgets_reject_tree_and_monolithic_font_regressions():
    for sizes in [
        {"README.md": 9 * MiB},
        {"plugin.py": 8 * MiB},
        {"templates/assets/font.woff2": 3 * MiB},
        {f"templates/assets/font-{i}.woff2": 2 * MiB for i in range(4)},
    ]:
        with pytest.raises(AssertionError):
            check_tree_hygiene(sizes)


def test_common_and_full_coverage_payload_keep_ipc_headroom():
    result = check_html_payload(Path(__file__).parent)
    assert result["html_utf8_bytes"]["common"] < 3 * MiB
    assert min(result["ipc_headroom_before_envelope_bytes"].values()) > 7 * MiB
