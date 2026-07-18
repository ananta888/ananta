"""Plan targeted rag-helper cache invalidation for file-type policy changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent.services.rag_helper_file_type_policy import RagHelperFileTypePolicy


@dataclass(frozen=True, slots=True)
class RagHelperCacheMigrationPlan:
    affected_format_ids: tuple[str, ...]
    affected_paths: tuple[str, ...]
    full_invalidation_fallback: bool

    @property
    def requires_invalidation(self) -> bool:
        return bool(self.affected_paths)

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": "targeted_file_type_invalidation",
            "affected_format_ids": list(self.affected_format_ids),
            "affected_paths": list(self.affected_paths),
            "affected_path_count": len(self.affected_paths),
            "full_invalidation_fallback": self.full_invalidation_fallback,
            "requires_invalidation": self.requires_invalidation,
        }


def plan_rag_helper_cache_migration(
    *,
    previous_contract: Mapping[str, Any] | None,
    current_contract: Mapping[str, Any],
    previous_manifest: Mapping[str, Any] | None,
    repository_path: Path,
    policy: RagHelperFileTypePolicy,
) -> RagHelperCacheMigrationPlan:
    """Select old cached paths affected by registry or activation changes."""

    previous = dict(previous_contract or {})
    current = dict(current_contract)
    manifest_files = _manifest_files(previous_manifest)
    if not previous:
        return RagHelperCacheMigrationPlan(
            affected_format_ids=(),
            affected_paths=tuple(sorted(manifest_files)),
            full_invalidation_fallback=bool(manifest_files),
        )

    previous_hashes = _string_mapping(previous.get("descriptor_hashes"))
    current_hashes = _string_mapping(current.get("descriptor_hashes"))
    hashes_available = bool(previous_hashes) and bool(current_hashes)
    affected_formats = {
        format_id
        for format_id in set(previous_hashes) | set(current_hashes)
        if previous_hashes.get(format_id) != current_hashes.get(format_id)
    }
    previous_enabled = _string_set(previous.get("effective_format_ids"))
    current_enabled = _string_set(current.get("effective_format_ids"))
    affected_formats.update(previous_enabled ^ current_enabled)

    if not hashes_available:
        return RagHelperCacheMigrationPlan(
            affected_format_ids=tuple(sorted(affected_formats)),
            affected_paths=tuple(sorted(manifest_files)),
            full_invalidation_fallback=bool(manifest_files),
        )

    affected_paths: list[str] = []
    for relative_path, previous_format_id in sorted(manifest_files.items()):
        source_path = repository_path / relative_path
        classification = (
            policy.classify_file(source_path, relative_path=relative_path)
            if source_path.exists()
            else None
        )
        current_format_id = (
            classification.format_id
            if classification is not None
            and classification.format_id in current_enabled
            else None
        )
        if (
            previous_format_id != current_format_id
            or previous_format_id in affected_formats
            or current_format_id in affected_formats
        ):
            affected_paths.append(relative_path)

    return RagHelperCacheMigrationPlan(
        affected_format_ids=tuple(sorted(affected_formats)),
        affected_paths=tuple(affected_paths),
        full_invalidation_fallback=False,
    )


def _manifest_files(manifest: Mapping[str, Any] | None) -> dict[str, str | None]:
    raw_files = (manifest or {}).get("files")
    if not isinstance(raw_files, list):
        return {}
    result: dict[str, str | None] = {}
    for raw in raw_files[:100_000]:
        if not isinstance(raw, Mapping):
            continue
        path = str(raw.get("file") or "").replace("\\", "/")[:4096]
        if not path or path.startswith("/") or ".." in Path(path).parts:
            continue
        format_id = str(raw.get("detected_type") or "").strip().lower() or None
        result[path] = format_id
    return result


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key).strip().lower(): str(item).strip().lower()
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _string_set(value: object) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


__all__ = ["RagHelperCacheMigrationPlan", "plan_rag_helper_cache_migration"]
