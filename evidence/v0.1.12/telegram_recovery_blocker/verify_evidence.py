"""Offline integrity and probe-parser verification; no network or production imports."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        raw = (ROOT / item["file"]).read_bytes()
        assert len(raw) == item["bytes"], item["file"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"], item["file"]

    tree = ast.parse((ROOT / "runtime_probe_round2.py").read_text(encoding="utf-8"))
    separators = [
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "split" and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, bytes)
        and node.args[0].value != b"="
    ]
    assert separators == [b"\0", b"\0"]
    raw_environ = b"PATH=/bin\0HTTPS_PROXY=http://example.invalid:8080\0SSL_CERT_FILE=/example.pem\0"
    parsed = dict(x.split(b"=", 1) for x in raw_environ.split(separators[0]) if b"=" in x)
    correct = dict(x.split(b"=", 1) for x in raw_environ.split(b"\0") if b"=" in x)
    assert parsed == correct
    assert b"HTTPS_PROXY" in correct and b"SSL_CERT_FILE" in correct

    for filename, count in [("round1.jsonl", 1), ("round2.jsonl", 2)]:
        rows = [json.loads(line) for line in (ROOT / filename).read_text().splitlines()]
        tg = [r for r in rows if r.get("url", "").startswith("https://t.me/")]
        assert len(tg) == count
        assert all(r.get("error_type") == "TimeoutError" and "status" not in r for r in tg)
        control = next(r for r in rows if r.get("url", "").startswith("https://codex-reset.com/"))
        assert control["status"] == 200
    print(f"PASS: {len(manifest['files'])} file hashes; captured request outcomes consistent")
    print("PASS: original probe uses NUL delimiters; proxy/CA positive-control parsing verified")


if __name__ == "__main__":
    main()
