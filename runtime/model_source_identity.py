"""Explicit, fail-closed source compatibility for unchanged historical ML artifacts."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def source_hash(path):
    path = Path(path).resolve()
    manifest = json.loads((ROOT / 'ml-source-compatibility.json').read_text())
    relative = path.relative_to(ROOT).as_posix()
    entry = manifest['adapters'].get(relative)
    dependencies = manifest['dependencies']
    if entry and sha(path) == entry['refactored_sha256'] and all(
            sha(ROOT / name) == value for name, value in dependencies.items()):
        return entry['legacy_sha256']
    # Any drift invalidates existing artifacts; never silently bless new math.
    return hashlib.sha256((sha(path) + ''.join(sha(ROOT / name) for name in dependencies)).encode()).hexdigest()
