"""Targeted index migration planning for file-type registry changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from .file_type_classifier import FileTypeClassifier
from .file_type_support import FileTypeSupportRegistry


@dataclass(frozen=True, slots=True)
class FileTypeMigrationPlan:
    previous_registry_version: str
    current_registry_version: str
    previous_digest: str
    current_digest: str
    added_format_ids: tuple[str, ...]
    removed_format_ids: tuple[str, ...]
    changed_format_ids: tuple[str, ...]

    @property
    def affected_format_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.added_format_ids)
                | set(self.removed_format_ids)
                | set(self.changed_format_ids)
            )
        )

    @property
    def requires_migration(self) -> bool:
        return bool(self.affected_format_ids)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "codecompass.file-type-migration-plan.v1",
            "previous_registry_version": self.previous_registry_version,
            "current_registry_version": self.current_registry_version,
            "previous_digest": self.previous_digest,
            "current_digest": self.current_digest,
            "added_format_ids": list(self.added_format_ids),
            "removed_format_ids": list(self.removed_format_ids),
            "changed_format_ids": list(self.changed_format_ids),
            "affected_format_ids": list(self.affected_format_ids),
            "requires_migration": self.requires_migration,
            "strategy": "targeted_file_type_invalidation",
        }


def compare_file_type_registries(
    previous: FileTypeSupportRegistry,
    current: FileTypeSupportRegistry,
) -> FileTypeMigrationPlan:
    previous_hashes = file_type_descriptor_hashes(previous)
    current_hashes = file_type_descriptor_hashes(current)
    previous_ids = set(previous_hashes)
    current_ids = set(current_hashes)
    return FileTypeMigrationPlan(
        previous_registry_version=previous.registry_version,
        current_registry_version=current.registry_version,
        previous_digest=previous.digest,
        current_digest=current.digest,
        added_format_ids=tuple(sorted(current_ids - previous_ids)),
        removed_format_ids=tuple(sorted(previous_ids - current_ids)),
        changed_format_ids=tuple(
            sorted(
                format_id
                for format_id in previous_ids & current_ids
                if previous_hashes[format_id] != current_hashes[format_id]
            )
        ),
    )


def affected_paths(
    paths: Iterable[str],
    *,
    previous: FileTypeSupportRegistry,
    current: FileTypeSupportRegistry,
    first_lines: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return only paths whose old/new classification has changed or is affected."""

    plan = compare_file_type_registries(previous, current)
    if not plan.requires_migration:
        return ()
    affected_ids = set(plan.affected_format_ids)
    previous_classifier = FileTypeClassifier(previous)
    current_classifier = FileTypeClassifier(current)
    line_by_path = dict(first_lines or {})
    result: list[str] = []
    for path in sorted(set(str(value).replace("\\", "/") for value in paths)):
        first_line = line_by_path.get(path)
        old = previous_classifier.classify(path, first_line=first_line, is_text=True)
        new = current_classifier.classify(path, first_line=first_line, is_text=True)
        old_id = old.format_id if old else None
        new_id = new.format_id if new else None
        if old_id != new_id or old_id in affected_ids or new_id in affected_ids:
            result.append(path)
    return tuple(result)


def file_type_descriptor_hashes(registry: FileTypeSupportRegistry) -> dict[str, str]:
    """Return stable per-format hashes suitable for persisted cache metadata."""

    raw_by_id = {
        str(item["format_id"]): item
        for item in registry.as_dict().get("formats", [])
    }
    return {
        format_id: hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for format_id, payload in raw_by_id.items()
    }


__all__ = [
    "FileTypeMigrationPlan",
    "affected_paths",
    "compare_file_type_registries",
    "file_type_descriptor_hashes",
]
