"""Hub-side resolution of admitted CodeCompass graph artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.config import settings
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)

GRAPH_INDEX_FILENAME = "cc_graph_index.json"
GRAPH_VISUAL_METRICS_FILENAME = "cc_graph_index.visual_metrics.json"
GRAPH_BINDING_SCHEMA = "codecompass_graph_artifact_binding.v1"
_MAX_GRAPH_BYTES = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES


class CodeCompassGraphArtifactResolver:
    """Prefer Hub-admitted artifact bindings and retain a legacy fallback.

    Integrity hashes are checked during worker-result admission and again when
    a request resolves the admitted local pair. Metrics are never recomputed.
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
        return self._validated_legacy_path(candidate)

    def resolve_artifacts(self, knowledge_index: Any) -> tuple[Path, Path]:
        """Resolve and re-verify the graph/metrics pair used by read paths."""

        metadata = getattr(knowledge_index, "index_metadata", None)
        if isinstance(metadata, Mapping) and "graph_artifacts" in metadata:
            return self._resolve_admitted_artifacts(metadata["graph_artifacts"])
        if not self._allow_legacy:
            raise ValueError("legacy_graph_artifact_binding_disabled")
        output_dir = str(getattr(knowledge_index, "output_dir", None) or "").strip()
        if not output_dir:
            raise ValueError("graph_output_dir_not_set")
        directory = Path(output_dir)
        return (
            self._validated_legacy_path(directory / GRAPH_INDEX_FILENAME),
            self._validated_legacy_artifact_path(
                directory / GRAPH_VISUAL_METRICS_FILENAME,
                expected_filename=GRAPH_VISUAL_METRICS_FILENAME,
            ),
        )

    def _resolve_admitted_binding(self, raw_binding: Any) -> Path:
        binding = self._validated_binding(raw_binding)
        graph_reference = binding.get("graph_index")
        if not isinstance(graph_reference, Mapping):
            raise ValueError("graph_artifact_binding_invalid")
        return self._resolve_admitted_reference(
            graph_reference,
            artifact_schema="codecompass_graph_index.v1",
            filename=GRAPH_INDEX_FILENAME,
        )

    def _resolve_admitted_artifacts(self, raw_binding: Any) -> tuple[Path, Path]:
        binding = self._validated_binding(raw_binding)
        graph_reference = binding.get("graph_index")
        metrics_reference = binding.get("visual_metrics")
        if not isinstance(graph_reference, Mapping) or not isinstance(
            metrics_reference, Mapping
        ):
            raise ValueError("graph_artifact_binding_invalid")
        graph_path = self._resolve_admitted_reference(
            graph_reference,
            artifact_schema="codecompass_graph_index.v1",
            filename=GRAPH_INDEX_FILENAME,
        )
        metrics_path = self._resolve_admitted_reference(
            metrics_reference,
            artifact_schema="graph_visual_metrics.v1",
            filename=GRAPH_VISUAL_METRICS_FILENAME,
        )
        if graph_path.parent != metrics_path.parent:
            raise ValueError("graph_artifact_binding_invalid")
        return graph_path, metrics_path

    @staticmethod
    def _validated_binding(raw_binding: Any) -> dict[str, Any]:
        if not isinstance(raw_binding, Mapping):
            raise ValueError("graph_artifact_binding_invalid")
        binding = dict(raw_binding)
        revision = str(binding.get("graph_revision") or "")
        if (
            str(binding.get("schema") or "") != GRAPH_BINDING_SCHEMA
            or not revision.startswith("sha256:")
            or len(revision) != 71
            or any(char not in "0123456789abcdef" for char in revision[7:])
        ):
            raise ValueError("graph_artifact_binding_invalid")
        return binding

    def _resolve_admitted_reference(
        self,
        raw_reference: Mapping[str, Any],
        *,
        artifact_schema: str,
        filename: str,
    ) -> Path:
        reference = dict(raw_reference)
        digest = str(reference.get("sha256") or "")
        if (
            str(reference.get("artifact_schema") or "") != artifact_schema
            or str(reference.get("filename") or "") != filename
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("graph_artifact_binding_invalid")
        local_path = self._validated_local_artifact_path(
            Path(str(reference.get("local_path") or "")),
            expected_filename=filename,
        )
        if self._sha256(local_path) != digest:
            raise ValueError("graph_artifact_hash_drift")
        return local_path

    def _validated_local_path(self, local_path: Path) -> Path:
        return self._validated_local_artifact_path(
            local_path,
            expected_filename=GRAPH_INDEX_FILENAME,
        )

    def _validated_local_artifact_path(
        self,
        local_path: Path,
        *,
        expected_filename: str,
    ) -> Path:
        if not local_path.is_absolute() or local_path.name != expected_filename:
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

    def _validated_legacy_path(self, local_path: Path) -> Path:
        """Resolve a legacy path while preserving the historical empty graph.

        A missing legacy graph is a supported degraded read state. Existing
        files still pass the complete materialization, containment and size
        checks used for admitted artifacts.
        """

        return self._validated_legacy_artifact_path(
            local_path,
            expected_filename=GRAPH_INDEX_FILENAME,
        )

    def _validated_legacy_artifact_path(
        self,
        local_path: Path,
        *,
        expected_filename: str,
    ) -> Path:
        if not local_path.is_absolute() or local_path.name != expected_filename:
            raise ValueError("graph_artifact_not_materialized")
        if local_path.is_symlink():
            raise ValueError("graph_artifact_not_materialized")
        if local_path.exists():
            return self._validated_local_artifact_path(
                local_path,
                expected_filename=expected_filename,
            )
        resolved = local_path.resolve(strict=False)
        if self._artifact_root is not None:
            try:
                resolved.relative_to(self._artifact_root)
            except ValueError as exc:
                raise ValueError("graph_artifact_outside_root") from exc
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
    "GRAPH_VISUAL_METRICS_FILENAME",
    "get_codecompass_graph_artifact_resolver",
]
