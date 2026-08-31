"""Read-only allowlisted model catalog for the local reference backend."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class ReadOnlyDendriticModelCatalog:
    def __init__(self, entries: Mapping[str, Mapping[str, Any]]) -> None:
        self._entries = {str(key): dict(value) for key, value in entries.items()}

    def resolve(self, *, model_id: str, snapshot_digest: str) -> Mapping[str, Any]:
        entry = self._entries.get(model_id)
        if entry is None:
            raise PermissionError("dendritic_model_not_allowlisted")
        if set(entry) != {"weights_path", "snapshot_digest", "factory", "allowed_target_prefixes"}:
            raise ValueError("dendritic_model_catalog_entry_invalid")
        candidate = Path(str(entry["weights_path"]))
        if candidate.is_symlink():
            raise PermissionError("dendritic_model_catalog_path_denied")
        path = candidate.resolve(strict=True)
        if path.suffix != ".safetensors":
            raise PermissionError("dendritic_model_catalog_path_denied")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != snapshot_digest or actual != entry["snapshot_digest"]:
            raise PermissionError("dendritic_model_snapshot_mismatch")
        factory = entry["factory"]
        if not isinstance(factory, Callable):
            raise ValueError("dendritic_model_catalog_factory_invalid")
        prefixes = tuple(entry["allowed_target_prefixes"])
        if not prefixes or any(not isinstance(value, str) or not value for value in prefixes):
            raise ValueError("dendritic_model_catalog_target_prefixes_invalid")
        return {
            "model": factory(path),
            "snapshot_digest": actual,
            "allowed_target_prefixes": prefixes,
            "source": "read_only_local_catalog",
            "network_download_performed": False,
            "remote_code_executed": False,
        }


__all__ = ["ReadOnlyDendriticModelCatalog"]
