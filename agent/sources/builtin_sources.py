from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_builtin_source_descriptors() -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    for path in (
        ROOT / "sources" / "keycloak" / "source_descriptor.json",
        ROOT / "sources" / "wikipedia" / "source_descriptor.json",
    ):
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            descriptors.append(payload)
    return descriptors

