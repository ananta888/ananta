"""Deterministic activation policy for canonical file-type descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .file_type_support import FileTypeDescriptor, FileTypeSupportRegistry


class FileTypeRolloutPolicyError(ValueError):
    """Raised when rollout configuration references invalid registry values."""


@dataclass(frozen=True, slots=True)
class FileTypeRolloutPolicy:
    """Pure allow/deny projection; it grants no path or parser permission."""

    priorities: frozenset[str]
    enabled_format_ids: frozenset[str]
    disabled_format_ids: frozenset[str]

    @classmethod
    def build(
        cls,
        registry: FileTypeSupportRegistry,
        *,
        priorities: Iterable[str],
        enabled_format_ids: Iterable[str] = (),
        disabled_format_ids: Iterable[str] = (),
    ) -> "FileTypeRolloutPolicy":
        normalized_priorities = frozenset(
            str(value).strip().upper() for value in priorities if str(value).strip()
        )
        known_priorities = {descriptor.priority.upper() for descriptor in registry.descriptors}
        unknown_priorities = sorted(normalized_priorities - known_priorities)
        if not normalized_priorities or unknown_priorities:
            detail = ",".join(unknown_priorities) or "empty"
            raise FileTypeRolloutPolicyError(f"unknown_file_type_priority:{detail}")

        enabled = frozenset(
            str(value).strip().lower() for value in enabled_format_ids if str(value).strip()
        )
        disabled = frozenset(
            str(value).strip().lower() for value in disabled_format_ids if str(value).strip()
        )
        known_formats = {descriptor.format_id for descriptor in registry.descriptors}
        unknown_formats = sorted((enabled | disabled) - known_formats)
        if unknown_formats:
            raise FileTypeRolloutPolicyError(
                f"unknown_file_type_format:{','.join(unknown_formats)}"
            )
        overlap = sorted(enabled & disabled)
        if overlap:
            raise FileTypeRolloutPolicyError(
                f"conflicting_file_type_format:{','.join(overlap)}"
            )
        return cls(
            priorities=normalized_priorities,
            enabled_format_ids=enabled,
            disabled_format_ids=disabled,
        )

    def allows(self, descriptor: FileTypeDescriptor) -> bool:
        if not descriptor.enabled or descriptor.format_id in self.disabled_format_ids:
            return False
        if self.enabled_format_ids and descriptor.format_id not in self.enabled_format_ids:
            return False
        return descriptor.priority.upper() in self.priorities

    def as_dict(self) -> dict[str, object]:
        return {
            "priorities": sorted(self.priorities),
            "enabled_format_ids": sorted(self.enabled_format_ids),
            "disabled_format_ids": sorted(self.disabled_format_ids),
        }


__all__ = ["FileTypeRolloutPolicy", "FileTypeRolloutPolicyError"]
