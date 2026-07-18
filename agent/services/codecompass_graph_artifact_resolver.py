"""Hub-side resolution of admitted CodeCompass graph artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

GRAPH_INDEX_FILENAME = "cc_graph_index.json"
GRAPH_BINDING_SCHEMA = "codecompass_graph_artifact_binding.v1"


class CodeCompassGraphArtifactResolver:
    """Prefer Hub-admitted artifact bindings and retain a legacy fallback.

    Integrity hashes are checked once during worker-result admission.  The
    request path therefore only resolves an already admitted local file and
    never recomputes graph metrics or hashes.
    """

    def resolve(self, knowledge_index: Any) -> Path:
        metadata = getattr(knowledge_index, "index_metadata", None)
        if isinstance(metadata, Mapping) and "graph_artifacts" in metadata:
            return self._resolve_admitted_binding(metadata["graph_artifacts"])
        output_dir = str(getattr(knowledge_index, "output_dir", None) or "").strip()
        if not output_dir:
            raise ValueError("graph_output_dir_not_set")
        return Path(output_dir) / GRAPH_INDEX_FILENAME

    @staticmethod
    def _resolve_admitted_binding(raw_binding: Any) -> Path:
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
        local_path = Path(str(reference.get("local_path") or ""))
        if (
            not local_path.is_absolute()
            or local_path.name != GRAPH_INDEX_FILENAME
            or local_path.is_symlink()
            or not local_path.is_file()
        ):
            raise ValueError("graph_artifact_not_materialized")
        return local_path


_resolver = CodeCompassGraphArtifactResolver()


def get_codecompass_graph_artifact_resolver() -> CodeCompassGraphArtifactResolver:
    return _resolver


__all__ = [
    "CodeCompassGraphArtifactResolver",
    "GRAPH_BINDING_SCHEMA",
    "GRAPH_INDEX_FILENAME",
    "get_codecompass_graph_artifact_resolver",
]
