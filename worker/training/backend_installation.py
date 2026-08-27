"""Read optional trainer identity without importing its isolated environment."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

_INVENTORY_PATH = Path("/usr/share/ananta-sbom/training-backend-identity.json")


def installed_package_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError as original:
        try:
            raw: Any = json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise original from exc
        version = raw.get("version") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or set(raw) != {"package", "version"}
            or raw.get("package") != package_name
            or not isinstance(version, str)
        ):
            raise original
        return version


__all__ = ["installed_package_version"]
