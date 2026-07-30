"""Hub-side resolution of admitted CodeCompass graph artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.config import settings

GRAPH_INDEX_FILENAME = "cc_graph_index.json"
GRAPH_BINDING_SCHEMA = "codecompass_graph_artifact_binding.v1"
_MAX_GRAPH_BYTES = 32 * 1024 * 1024


class CodeCompassGraphArtifactResolver:
    """Prefer Hub-admitted artifact bindings and retain a legacy fallback.

    Integrity hashes are checked once during worker-result admission.  The
    request path therefore only resolves an already admitted local file and
    never recomputes graph metrics or hashes.
    """

    def __init__(
        self,
        *,
        artifact_root: str | Path | None = None,
        allow_legacy: bool = True,
    ) -> None:
        self._artifact_root = (
            Path(artifact_root).resolve()
            if artifact_root is not None
            else None
        )
        self._allow_legacy = bool(allow_legacy)

    def resolve(self, knowledge_index: Any) -> Path:
        metadata = getattr(knowledge_index, "index_metadata", None)
        if isinstance(metadata, Mapping) and "graph_artifacts" in metadata:
            return self._resolve_admitted_binding(metadata["graph_artifacts"])
        if not self._allow_legacy:
            raise ValueError("legacy_graph_artifact_binding_disabled")
        output_dir = str(getattr(knowledge_index, "output_dir", None) or "").strip()
        if not output_dir:
            raise ValueError("graph_output_dir_not_set")
        candidate = Path(output_dir) / GRAPH_INDEX_FILENAME
        return self._validated_local_path(candidate)

    def _resolve_admitted_binding(self, raw_binding: Any) -> Path:
        if not isinstance(raw_binding, Mapping):
            raise ValueError("graph_artifact_binding_invalid")
        binding = dict(raw_binding)
        graph_reference = binding.get("graph_index")
        if (
            str(binding.get("schema") or "") != GRAPH_BINDING_SCHEMA
            or not isinstance(graph_reference, Mapping)
        ):
            raise ValueError("graph_artifact_binding_invalid")
        reference = dict(graph_reference)
        revision = str(binding.get("graph_revision") or "")
        digest = str(reference.get("sha256") or "")
        if (
            str(reference.get("artifact_schema") or "") != "codecompass_graph_index.v1"
            or str(reference.get("filename") or "") != GRAPH_INDEX_FILENAME
            or not revision.startswith("sha256:")
            or len(revision) != 71
            or any(char not in "0123456789abcdef" for char in revision[7:])
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("graph_artifact_binding_invalid")
        local_path = self._validated_local_path(
            Path(str(reference.get("local_path") or ""))
        )
        if self._sha256(local_path) != digest:
            raise ValueError("graph_artifact_hash_drift")
        return local_path

    def _validated_local_path(self, local_path: Path) -> Path:
        if not local_path.is_absolute() or local_path.name != GRAPH_INDEX_FILENAME:
            raise ValueError("graph_artifact_not_materialized")
        if local_path.is_symlink() or not local_path.is_file():
            raise ValueError("graph_artifact_not_materialized")
        try:
            resolved = local_path.resolve(strict=True)
        except OSError as exc:
            raise ValueError("graph_artifact_not_materialized") from exc
        if self._artifact_root is not None:
            try:
                resolved.relative_to(self._artifact_root)
            except ValueError as exc:
                raise ValueError("graph_artifact_outside_root") from exc
        if resolved.stat().st_size > _MAX_GRAPH_BYTES:
            raise ValueError("graph_artifact_too_large")
        return resolved

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


_resolver = CodeCompassGraphArtifactResolver(
    artifact_root=Path(settings.data_dir) / "knowledge_indices",
    allow_legacy=bool(
        getattr(
            settings,
            "source_control_legacy_codecompass_artifacts_enabled",
            True,
        )
    ),
)


def get_codecompass_graph_artifact_resolver() -> CodeCompassGraphArtifactResolver:
    return _resolver


__all__ = [
    "CodeCompassGraphArtifactResolver",
    "GRAPH_BINDING_SCHEMA",
    "GRAPH_INDEX_FILENAME",
    "get_codecompass_graph_artifact_resolver",
]
