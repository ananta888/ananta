"""Fail-closed worker-side loader for policy-approved Memory Packs.

The Hub remains responsible for route and approval decisions.  This component
only materializes one already-approved local pack and owns its unload cleanup.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.dendritic_memory import DendriticMemoryPackManifestV1, require_digest, require_id


class DendriticLoadedModulePort(Protocol):
    def unload(self) -> None: ...


class DendriticMemoryRuntimeLoader:
    def __init__(
        self,
        *,
        artifact_root: str | Path,
        model_catalog: Mapping[str, Mapping[str, Any]],
        load_module: Callable[[DendriticMemoryPackManifestV1, bytes], DendriticLoadedModulePort],
        max_pack_bytes: int,
    ) -> None:
        self._root = Path(artifact_root).resolve(strict=True)
        self._models = {str(key): dict(value) for key, value in model_catalog.items()}
        self._load_module = load_module
        if not 1_048_576 <= max_pack_bytes <= 4_294_967_296:
            raise ValueError("dendritic_runtime_pack_limit_invalid")
        self._max_pack_bytes = max_pack_bytes
        self._active: dict[str, tuple[str, DendriticLoadedModulePort]] = {}

    def activate(
        self,
        *,
        scope_id: str,
        tenant_id: str,
        pack_digest: str,
        manifest: Mapping[str, Any],
        registry_state: str,
    ) -> dict[str, Any]:
        scope = require_id(scope_id, "runtime_scope_id")
        tenant = require_id(tenant_id, "runtime_tenant_id")
        digest = require_digest(pack_digest, "runtime_pack_digest")
        parsed = DendriticMemoryPackManifestV1.from_mapping(manifest)
        if registry_state != "approved_for_experiment":
            raise PermissionError("dendritic_runtime_registry_state_denied")
        if parsed.tenant_id != tenant or parsed.digest != digest or not parsed.executable:
            raise PermissionError("dendritic_runtime_pack_binding_invalid")
        model = self._models.get(parsed.base_model_id)
        if model is None or model.get("snapshot_digest") != parsed.base_model_snapshot_digest:
            raise PermissionError("dendritic_runtime_model_binding_invalid")
        pack_root = (self._root / tenant / digest).resolve()
        if self._root not in pack_root.parents or pack_root.is_symlink() or not pack_root.is_dir():
            raise ValueError("dendritic_runtime_pack_path_invalid")
        expected = {str(item["name"]): dict(item) for item in parsed.files}
        total = 0
        payloads: dict[str, bytes] = {}
        for name, metadata in expected.items():
            candidate = pack_root / name
            if candidate.is_symlink() or candidate.parent.resolve() != pack_root:
                raise ValueError("dendritic_runtime_pack_path_invalid")
            payload = candidate.read_bytes()
            total += len(payload)
            if (
                len(payload) != metadata["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != metadata["sha256"]
            ):
                raise ValueError("dendritic_runtime_pack_digest_mismatch")
            payloads[name] = payload
        if total > self._max_pack_bytes:
            raise ValueError("dendritic_runtime_pack_size_exceeded")

        previous = self._active.pop(scope, None)
        if previous is not None:
            previous[1].unload()
        try:
            loaded = self._load_module(parsed, payloads["weights.safetensors"])
        except Exception:
            # The safe fallback is the unchanged base model, never a partially loaded pack.
            raise
        self._active[scope] = (digest, loaded)
        return {
            "scope_id": scope,
            "pack_digest": digest,
            "active": True,
            "fallback": "unchanged_base_model",
            "human_intervention_required": False,
        }

    def deactivate(self, *, scope_id: str) -> dict[str, Any]:
        scope = require_id(scope_id, "runtime_scope_id")
        current = self._active.pop(scope, None)
        if current is not None:
            current[1].unload()
        return {
            "scope_id": scope,
            "pack_digest": current[0] if current else None,
            "active": False,
            "fallback": "unchanged_base_model",
            "human_intervention_required": False,
        }

    def active(self, *, scope_id: str) -> dict[str, Any]:
        scope = require_id(scope_id, "runtime_scope_id")
        current = self._active.get(scope)
        return {
            "scope_id": scope,
            "pack_digest": current[0] if current else None,
            "active": current is not None,
        }


__all__ = ["DendriticLoadedModulePort", "DendriticMemoryRuntimeLoader"]
