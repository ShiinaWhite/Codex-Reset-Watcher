"""Verify raw tracked bytes against an exact Git commit; emit a SHA-256 manifest."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


MiB = 1024 * 1024
SIZE_LIMITS = {"tracked": 8 * MiB, "runtime": 7 * MiB, "fonts": 6 * MiB}


def check_tree_hygiene(sizes):
    """Gate the actual clone tree, including development files installed with it."""
    totals = dict.fromkeys(SIZE_LIMITS, 0)
    for name, size in sizes.items():
        path = Path(name)
        assert path.parts[0] not in {"live", "evidence", "private", "test_env"}, name
        assert not (len(path.parts) == 1 and path.name.startswith("probe")), name
        assert "before-typography" not in path.parts, name
        assert not name.startswith("docs/forensics/"), name
        assert path.name not in {"config.toml", "reset_state.json", ".env"}, name
        assert path.suffix not in {".pyc", ".log", ".jsonl", ".ttf"}, name
        totals["tracked"] += size
        if name in {
            "plugin.py",
            "notice_card.py",
            "tibo_discovery.py",
            "_manifest.json",
            "LICENSE",
        } or name.startswith("templates/"):
            totals["runtime"] += size
        if path.suffix == ".woff2":
            assert size <= 2 * MiB, f"Oversized font asset: {name}"
            totals["fonts"] += size
    for category, limit in SIZE_LIMITS.items():
        assert totals[category] <= limit, (
            f"{category} tree exceeds {limit}: {totals[category]}"
        )
    return totals


def check_html_payload(root):
    """Measure common and all-supported-character requests with the real builder."""
    from datetime import datetime, timezone

    spec = importlib.util.spec_from_file_location(
        "_release_notice_card", root / "notice_card.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    now = datetime(2026, 10, 4, 20, 33, tzinfo=timezone.utc)
    common = module.NoticeCard(
        "Codex 额度重置提醒",
        "Reset all propagated. Enjoy.",
        "重置已全部生效，尽情使用吧。",
        tweet=True,
    )
    index = json.loads(
        (root / "templates/assets/font-index.json").read_text(encoding="utf-8")
    )
    stress = module.NoticeCard(
        "Tibo 动态", "".join(entry["codepoints"] for entry in index), tweet=True
    )
    measured = {
        name: len(module.build_card_html(card, now).encode("utf-8"))
        for name, card in [("common", common), ("all_supported_glyphs", stress)]
    }
    assert measured["common"] < 3 * MiB, "Common HTML payload regression"
    assert measured["all_supported_glyphs"] < 9 * MiB, "Worst font payload regression"
    return {
        "html_utf8_bytes": measured,
        "ipc_frame_limit_bytes": 16 * MiB,
        "ipc_headroom_before_envelope_bytes": {
            k: 16 * MiB - n for k, n in measured.items()
        },
    }


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def verify(root, revision):
    root = Path(root).resolve()
    commit = (
        git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    )
    assert git(root, "rev-parse", "HEAD").decode().strip() == commit, "HEAD differs"
    assert not git(root, "status", "--porcelain", "--untracked-files=all"), (
        "Dirty checkout"
    )
    records = git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    expected, actual, sizes = [], [], {}
    for record in filter(None, records):
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode("utf-8")
        assert kind == "blob" and mode in ("100644", "100755"), (
            f"Unsupported entry: {path}"
        )
        blob = git(root, "cat-file", "blob", oid)
        local = (root / path).read_bytes()
        assert local == blob, f"Raw bytes differ: {path}"
        sizes[path] = len(blob)
        expected.append([path, mode, hashlib.sha256(blob).hexdigest()])
        actual.append([path, mode, hashlib.sha256(local).hexdigest()])
        if Path(path).name.startswith("test_") and path.endswith(".py"):
            module = ast.parse(local, filename=path)
            names = [
                n.name
                for n in module.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            assert len(names) == len(set(names)), f"Duplicate definitions: {path}"

    def digest(rows):
        data = json.dumps(
            sorted(rows), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    return {
        "root": str(root),
        "commit": commit,
        "git_tree": git(root, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
        "tracked_count": len(actual),
        "local_sha256": digest(actual),
        "commit_blobs_sha256": digest(expected),
        "files": sorted(actual),
        "tree_bytes": check_tree_hygiene(sizes),
        "size_limits_bytes": SIZE_LIMITS,
        "render_payload": check_html_payload(root),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision", help="Exact commit SHA")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.revision), ensure_ascii=False, indent=2))
