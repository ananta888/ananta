"""Pin the existing Python Playwright's matching Node client for a private peer."""

import hashlib
import importlib.util
import json
from pathlib import Path

from scripts.hub_browser_test_evidence import canonical_digest


def peer_driver_snapshot(package=None):
    if package is None:
        package = Path(importlib.util.find_spec("playwright").origin).parent / "driver/package"
    root = Path(package).resolve(strict=True)
    metadata = root / "package.json"
    if not metadata.is_file() or metadata.stat().st_size > 8192:
        raise ValueError("meet_test_peer_driver_invalid")
    value = json.loads(metadata.read_bytes())
    if value.get("name") != "playwright-core" or value.get("version") != "1.58.0":
        raise ValueError("meet_test_peer_driver_invalid")
    rows, total = [], 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("meet_test_peer_driver_linked")
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if size > 8 * 1024**2 or total > 32 * 1024**2 or len(rows) >= 4096:
            raise ValueError("meet_test_peer_driver_budget")
        rows.append([path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return {"path": str(root), "version": "1.58.0", "digest": canonical_digest(rows)}
