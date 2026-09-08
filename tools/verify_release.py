"""Verify raw tracked bytes against an exact Git commit; emit a SHA-256 manifest."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def verify(root, revision):
    root = Path(root).resolve()
    commit = git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    assert git(root, "rev-parse", "HEAD").decode().strip() == commit, "HEAD differs"
    assert not git(root, "status", "--porcelain", "--untracked-files=all"), "Dirty checkout"
    records = git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    expected, actual = [], []
    for record in filter(None, records):
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode("utf-8")
        assert kind == "blob" and mode in ("100644", "100755"), f"Unsupported entry: {path}"
        blob = git(root, "cat-file", "blob", oid)
        local = (root / path).read_bytes()
        assert local == blob, f"Raw bytes differ: {path}"
        expected.append([path, mode, hashlib.sha256(blob).hexdigest()])
        actual.append([path, mode, hashlib.sha256(local).hexdigest()])
        if Path(path).name.startswith("test_") and path.endswith(".py"):
            module = ast.parse(local, filename=path)
            names = [n.name for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            assert len(names) == len(set(names)), f"Duplicate definitions: {path}"
    def digest(rows):
        data = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(data).hexdigest()
    return {
        "root": str(root), "commit": commit,
        "git_tree": git(root, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
        "tracked_count": len(actual), "local_sha256": digest(actual),
        "commit_blobs_sha256": digest(expected), "files": sorted(actual),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision", help="Exact commit SHA")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.revision), ensure_ascii=False, indent=2))
