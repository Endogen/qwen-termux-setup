#!/data/data/com.termux/files/usr/bin/python
"""Verify a completed GGUF against the pinned Hugging Face LFS SHA-256."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
info = json.loads((root / "model-info.json").read_text())
path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / info["filename"]
if path.stat().st_size != info["size_bytes"]:
    sys.exit(f"FAIL: incorrect size for {path}")
sha = hashlib.sha256()
with path.open("rb") as model:
    for chunk in iter(lambda: model.read(8 * 1024 * 1024), b""):
        sha.update(chunk)
actual = sha.hexdigest()
if actual != info["sha256"]:
    sys.exit(f"FAIL: SHA-256 mismatch: {actual}")
print(f"PASS: {path}\nSHA-256: {actual}")
