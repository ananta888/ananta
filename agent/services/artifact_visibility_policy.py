"""Visibility policy for artifacts exposed through generic client surfaces.

Knowledge-index payload and Worker-output artifacts are capability-bound
transport records.  They remain available through their dedicated internal
routes, but must never appear in generic artifact browsers or context builders.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SYSTEM_MANAGED_KNOWLEDGE_INDEX_ARTIFACT_KINDS = frozenset(
    {
        "knowledge_index_job_payload",
        "knowledge_index_worker_output",
    }
)


def artifact_metadata(artifact: object) -> Mapping[str, Any] | None:
    """Return normalized metadata, or ``None`` for a malformed record."""

    if isinstance(artifact, Mapping):
        raw_metadata = artifact.get("artifact_metadata")
    else:
        raw_metadata = getattr(artifact, "artifact_metadata", None)
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, Mapping):
        return None
    return raw_metadata


def is_artifact_visible_on_generic_surfaces(artifact: object | None) -> bool:
    """Return whether an artifact may cross a generic read boundary.

    Missing and malformed records fail closed.  Dedicated capability routes do
    not call this policy and therefore retain access to their system artifacts.
    """

    if artifact is None:
        return False
    metadata = artifact_metadata(artifact)
    if metadata is None:
        return False
    artifact_kind = str(metadata.get("system_artifact_kind") or "").strip()
    return artifact_kind not in SYSTEM_MANAGED_KNOWLEDGE_INDEX_ARTIFACT_KINDS


def repository_artifact_reference_candidates(artifact_ref: object) -> tuple[str, ...]:
    """Return repository IDs that a generic artifact reference may denote."""

    normalized = str(artifact_ref or "").strip()
    if not normalized:
        return ()
    candidates = [normalized]
    if normalized.startswith("artifact:"):
        unprefixed = normalized.removeprefix("artifact:").strip()
        if unprefixed and unprefixed != normalized:
            candidates.append(unprefixed)
    return tuple(candidates)
